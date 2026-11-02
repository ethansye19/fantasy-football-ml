"""Transparent baselines under the WeeklyModel protocol (MODELING.md section 3, D-053).

Every baseline scores actual stat lines (:func:`ffml.scoring.actuals.actual_statlines`)
with the league rules in question, so it works under any scoring, and returns
the prediction frame of :mod:`ffml.models.protocol`: one row per entity,
season-week and scoring with ``entity_id, position, team, game_id, season,
week, scoring, model, pred_mean`` and the seven quantiles ``pred_q05 ...
pred_q95``. Predictions are conditional on the entity being active, so no
``active_prob`` is returned.

Models (``name`` in reports)
============================

``rolling_avg_4`` / ``rolling_avg_3``
    mean of the entity's last 4 (3) games, :class:`RollingAverageBaseline`.
``rolling_ew_035``
    exponentially weighted mean over every past game with ``alpha = 0.35``
    (most recent game weighted most, ``adjust=True``).
``rolling_median_8``
    median (linear interpolation) of the last 8 games, :class:`RollingMedianBaseline`;
    the strongest simple predictor under MAE.
``prior_season``
    per-game average over the previous season, :class:`PriorSeasonBaseline`.

Information set (D-046)
=======================
A row in season-week ``(s, w)`` uses only games whose kickoff precedes the
first kickoff of that season-week (an as-of join with strict inequality). The
row's own game is never seen, Thursday and Sunday players share one
information set, and a game rescheduled past the next week's first kickoff is
not used for that week. Histories run across season boundaries. Only games
the entity played count. Position-level statistics (rookie fallbacks,
residual quantiles, the position minimum) use seasons strictly before ``s``.

Point estimate and quantiles (D-053 ruling)
===========================================
``pred_mean = pred_q50 =`` the baseline's own estimate. When the entity has
no usable history for the estimate (no past game for the rolling baselines,
no game in the previous season for ``prior_season``), the estimate is the
position-and-draft-round average from prior seasons: the per-game average,
over seasons before the row's season, of players at that position and draft
round (``0`` for undrafted; DST rows use round 0) who had no game in their
previous season. Cells with fewer than :data:`MIN_FALLBACK_GAMES` games fall
back to the position-level average of that population, and when it is empty
(the season after the first data season) to the position's average over all
games in prior seasons.

The other quantiles are ``estimate + r_tau`` where ``r_tau`` is the
empirical ``tau``-quantile (linear interpolation) of the entity's residuals
over its last :data:`RESIDUAL_WINDOW` played games. A residual is a game's
points minus that game's own baseline estimate (the estimate the baseline
would have issued for that week, fallback included). With fewer than
:data:`RESIDUAL_MIN_GAMES` residuals the position-level residual quantiles
of all games in prior seasons are used. Then:

- lower quantiles (``tau < 0.5``) are capped at the estimate and upper
  quantiles floored at it, so the frame is monotone with ``q50 = estimate``
  (a run of positive residuals cannot push ``q25`` above the median);
- every quantile is clipped below at the position's minimum observed score
  in prior seasons (or the estimate, if that is lower).

``predict_from_actuals(..., diagnostics=True)`` adds ``n_hist`` (eligible past
games), ``mean_source`` (``history`` or ``fallback``), ``quantile_source``
(``entity`` or ``position``) and ``n_resid`` (entity residuals available).

Protocol use
============
``fit(cutoff)`` only records the cutoff (baselines have no fitted
parameters). ``predict(season, week, scorings)`` loads outcomes from the first
data season through ``season`` (holdout guard applied to that whole range),
computes the whole season once per scoring, caches it, and returns the rows of
``week``: every entity with a stat-line row in that week's regular-season
games, played or not. :meth:`_Baseline.predict_seasons` is the batch form.
"""

from __future__ import annotations

from collections.abc import Iterable, Mapping

import polars as pl

from ffml.config import get_settings
from ffml.data.store import SnapshotStore
from ffml.holdout import assert_seasons_allowed
from ffml.models.protocol import PREDICTION_COLUMNS, QUANTILE_LEVELS
from ffml.scoring.actuals import actual_statlines
from ffml.scoring.rules import ScoringRules

RESIDUAL_WINDOW = 8
RESIDUAL_MIN_GAMES = 4
MEDIAN_WINDOW = 8
MIN_FALLBACK_GAMES = 20

QUANTILE_COLUMNS: tuple[str, ...] = tuple(f"pred_q{int(round(q * 100)):02d}" for q in QUANTILE_LEVELS)
#: (level, residual column suffix) for every quantile other than the median
TAIL_LEVELS: tuple[tuple[float, str], ...] = tuple(
    (q, f"{int(round(q * 100)):02d}") for q in QUANTILE_LEVELS if q != 0.5
)
DIAGNOSTIC_COLUMNS: tuple[str, ...] = ("n_hist", "mean_source", "quantile_source", "n_resid")


# --------------------------------------------------------------------------- #
# Shared preparation
# --------------------------------------------------------------------------- #


def week_cutoffs(games: pl.DataFrame) -> pl.DataFrame:
    """First kickoff per ``(season, week)`` from any frame with those columns and ``kickoff_utc``."""
    return games.group_by(["season", "week"]).agg(pl.col("kickoff_utc").min().alias("cutoff")).select(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "cutoff"
    )


def scored_games(actuals: pl.DataFrame, scoring: ScoringRules) -> pl.DataFrame:
    """Played games with ``points`` under ``scoring``, sorted by entity then kickoff."""
    played = actuals.filter(pl.col("played"))
    return played.select(
        "entity_id",
        "position",
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        "kickoff_utc",
        scoring.score(played).alias("points"),
    ).sort(["entity_id", "kickoff_utc"])


def _attach_draft_round(frame: pl.DataFrame, players: pl.DataFrame | None) -> pl.DataFrame:
    if "draft_round" in frame.columns:
        return frame
    if players is None or "draft_round" not in players.columns:
        return frame.with_columns(pl.lit(0, dtype=pl.Int32).alias("draft_round"))
    rounds = players.select(
        pl.col("player_id").alias("entity_id"),
        pl.col("draft_round").cast(pl.Int32),
    ).unique("entity_id")
    return frame.join(rounds, on="entity_id", how="left").with_columns(
        pl.col("draft_round").fill_null(0)
    )


def season_averages(games: pl.DataFrame) -> pl.DataFrame:
    """Per ``(entity_id, season)``: games played, mean points, and the previous season's mean."""
    by_season = games.group_by(["entity_id", "season"]).agg(
        pl.col("position").last(),
        pl.len().alias("games"),
        pl.col("points").mean().alias("season_mean"),
        pl.col("points").sum().alias("season_points"),
    )
    prior = by_season.select(
        "entity_id",
        (pl.col("season") + 1).alias("season"),
        pl.col("season_mean").alias("prior_mean"),
        pl.col("games").alias("prior_games"),
    )
    return by_season.join(prior, on=["entity_id", "season"], how="left")


def fallback_means(
    games: pl.DataFrame, players: pl.DataFrame | None, seasons: Iterable[int]
) -> pl.DataFrame:
    """Position-and-draft-round per-game average of players without a prior season.

    One row per ``(season, position, draft_round)`` for each target season,
    computed from seasons strictly before it, plus a position-level row
    (``draft_round = -1``) used when a cell has too few games. The first
    season present in ``games`` is excluded: its players' previous season is
    unknown, not absent.
    """
    schema = {
        "position": pl.String,
        "draft_round": pl.Int32,
        "fallback_mean": pl.Float64,
        "fallback_games": pl.UInt32,
        "season": pl.Int32,
    }
    if games.height == 0:
        return pl.DataFrame(schema=schema)
    first = games["season"].min()
    per_season = _attach_draft_round(season_averages(games), players)
    no_prior = per_season.filter(pl.col("prior_mean").is_null() & (pl.col("season") > first))
    out = []
    for season in sorted(set(seasons)):
        past = no_prior.filter(pl.col("season") < season)
        if past.height == 0:
            continue
        cells = past.group_by(["position", "draft_round"]).agg(
            (pl.col("season_points").sum() / pl.col("games").sum()).alias("fallback_mean"),
            pl.col("games").sum().alias("fallback_games"),
        )
        pos = past.group_by("position").agg(
            (pl.col("season_points").sum() / pl.col("games").sum()).alias("fallback_mean"),
            pl.col("games").sum().alias("fallback_games"),
        )
        pos = pos.with_columns(pl.lit(-1, dtype=pl.Int32).alias("draft_round"))
        out.append(
            pl.concat([cells, pos.select(cells.columns)], how="vertical_relaxed").with_columns(
                pl.lit(season, dtype=pl.Int32).alias("season")
            )
        )
    if not out:
        return pl.DataFrame(schema=schema)
    return pl.concat(out, how="vertical_relaxed").cast(schema)


def position_quantiles(games: pl.DataFrame, seasons: Iterable[int]) -> pl.DataFrame:
    """Per ``(season, position)``: mean, minimum and quantiles of all games at the position in earlier seasons.

    Quantile columns are ``pos_q05 ... pos_q95`` (linear interpolation).
    """
    out = []
    for season in sorted(set(seasons)):
        past = games.filter(pl.col("season") < season)
        if past.height == 0:
            continue
        out.append(
            past.group_by("position")
            .agg(
                pl.col("points").mean().alias("pos_mean"),
                pl.col("points").min().alias("pos_min"),
                *[
                    pl.col("points").quantile(q, interpolation="linear").alias(f"pos_{c[5:]}")
                    for q, c in zip(QUANTILE_LEVELS, QUANTILE_COLUMNS, strict=True)
                ],
            )
            .with_columns(pl.lit(season, dtype=pl.Int32).alias("season"))
        )
    schema = {"position": pl.String, "pos_mean": pl.Float64, "pos_min": pl.Float64}
    schema.update({f"pos_{c[5:]}": pl.Float64 for c in QUANTILE_COLUMNS})
    schema["season"] = pl.Int32
    if not out:
        return pl.DataFrame(schema=schema)
    return pl.concat(out, how="vertical_relaxed").cast(schema)


def position_residual_quantiles(residuals: pl.DataFrame, seasons: Iterable[int]) -> pl.DataFrame:
    """Per ``(season, position)``: residual quantiles ``pos_r05 ... pos_r95`` over games in earlier seasons."""
    schema = {"position": pl.String, **{f"pos_r{s}": pl.Float64 for _, s in TAIL_LEVELS}}
    schema["season"] = pl.Int32
    valid = residuals.filter(pl.col("resid").is_not_null())
    out = []
    for season in sorted(set(seasons)):
        past = valid.filter(pl.col("season") < season)
        if past.height == 0:
            continue
        out.append(
            past.group_by("position")
            .agg(
                pl.col("resid").quantile(q, interpolation="linear").alias(f"pos_r{s}")
                for q, s in TAIL_LEVELS
            )
            .with_columns(pl.lit(season, dtype=pl.Int32).alias("season"))
        )
    if not out:
        return pl.DataFrame(schema=schema)
    return pl.concat(out, how="vertical_relaxed").cast(schema)


def _targets(
    actuals: pl.DataFrame,
    seasons: list[int] | None,
    cutoffs: pl.DataFrame,
    played_only: bool = False,
) -> pl.DataFrame:
    """Target rows (one per entity and season-week) with the week's cutoff attached."""
    rows = actuals
    if played_only:
        rows = rows.filter(pl.col("played"))
    if seasons is not None:
        rows = rows.filter(pl.col("season").is_in(seasons))
    cols = ["entity_id", "position", "team", "game_id"]
    rows = rows.select(
        *[c for c in cols if c in rows.columns],
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
    )
    for c in cols:
        if c not in rows.columns:
            rows = rows.with_columns(pl.lit(None, dtype=pl.String).alias(c))
    rows = rows.unique(["entity_id", "season", "week"], keep="first", maintain_order=True)
    out = rows.join(cutoffs, on=["season", "week"], how="left")
    if out["cutoff"].null_count():
        raise ValueError("some target season-weeks have no kickoff cutoff")
    return out


def _as_of(targets: pl.DataFrame, history: pl.DataFrame) -> pl.DataFrame:
    """Attach the latest history row whose kickoff is strictly before the target's cutoff."""
    return targets.sort("cutoff").join_asof(
        history.sort("hist_kickoff"),
        left_on="cutoff",
        right_on="hist_kickoff",
        by="entity_id",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )


def _residual_history(residuals: pl.DataFrame) -> pl.DataFrame:
    """Per entity and played game: residual quantiles over its last games up to and including it."""
    valid = residuals.filter(pl.col("resid").is_not_null()).sort(["entity_id", "kickoff_utc"])
    by = "entity_id"
    feats = valid.with_columns(
        pl.int_range(1, pl.len() + 1).over(by).clip(upper_bound=RESIDUAL_WINDOW).alias("n_resid"),
        *[
            pl.col("resid")
            .rolling_quantile(
                quantile=q,
                interpolation="linear",
                window_size=RESIDUAL_WINDOW,
                min_samples=RESIDUAL_MIN_GAMES,
            )
            .over(by)
            .alias(f"ent_r{s}")
            for q, s in TAIL_LEVELS
        ],
    )
    return feats.select(
        "entity_id",
        pl.col("kickoff_utc").alias("hist_kickoff"),
        "n_resid",
        *[f"ent_r{s}" for _, s in TAIL_LEVELS],
    )


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #


class _Baseline:
    """Common logic; subclasses implement :meth:`_history_estimate`."""

    name: str = "baseline"

    def __init__(self, purpose: str = "backtest", store: SnapshotStore | None = None) -> None:
        self.purpose = purpose
        self._store = store
        self._cutoff: tuple[int, int] | None = None
        self._data: dict[str, object] | None = None
        self._cache: dict[tuple[int, str, str], pl.DataFrame] = {}

    # -- estimates -------------------------------------------------------- #

    def _history_estimate(self, games: pl.DataFrame, targets: pl.DataFrame) -> pl.DataFrame:
        """``targets`` plus ``hist_est`` (null when there is no usable history) and ``n_hist``."""
        raise NotImplementedError

    @staticmethod
    def _n_hist(games: pl.DataFrame, targets: pl.DataFrame) -> pl.DataFrame:
        hist = games.select(
            "entity_id",
            pl.col("kickoff_utc").alias("hist_kickoff"),
            pl.int_range(1, pl.len() + 1).over("entity_id").alias("n_hist"),
        )
        out = _as_of(targets, hist).drop("hist_kickoff")
        return out.with_columns(pl.col("n_hist").fill_null(0))

    def _estimates(
        self,
        games: pl.DataFrame,
        targets: pl.DataFrame,
        players: pl.DataFrame | None,
        pos_q: pl.DataFrame,
        fallback: pl.DataFrame,
    ) -> pl.DataFrame:
        """``targets`` plus ``est`` (history, else fallback) and diagnostics."""
        frame = self._history_estimate(games, targets)
        cell = fallback.filter(pl.col("fallback_games") >= MIN_FALLBACK_GAMES).drop(
            "fallback_games"
        )
        level = fallback.filter(pl.col("draft_round") == -1).select(
            "season", "position", pl.col("fallback_mean").alias("fallback_pos_mean")
        )
        frame = _attach_draft_round(frame, players)
        frame = (
            frame.join(pos_q.select("season", "position", "pos_mean"), on=["season", "position"], how="left")
            .join(cell, on=["season", "position", "draft_round"], how="left")
            .join(level, on=["season", "position"], how="left")
        )
        has_hist = pl.col("hist_est").is_not_null()
        return frame.with_columns(
            pl.when(has_hist)
            .then(pl.col("hist_est"))
            .otherwise(pl.coalesce("fallback_mean", "fallback_pos_mean", "pos_mean"))
            .alias("est"),
            pl.when(has_hist).then(pl.lit("history")).otherwise(pl.lit("fallback")).alias("mean_source"),
        ).drop("pos_mean", "fallback_mean", "fallback_pos_mean", "draft_round")

    # -- batch prediction -------------------------------------------------- #

    def predict_from_actuals(
        self,
        actuals: pl.DataFrame,
        seasons: Iterable[int],
        scoring: ScoringRules,
        *,
        players: pl.DataFrame | None = None,
        cutoffs: pl.DataFrame | None = None,
        diagnostics: bool = False,
        scoring_name: str | None = None,
    ) -> pl.DataFrame:
        """Predictions for every stat-line row of ``seasons`` (played or not) under ``scoring``.

        ``actuals`` must hold every earlier season used as history.
        """
        wanted = sorted({int(s) for s in seasons})
        cutoffs = week_cutoffs(actuals) if cutoffs is None else cutoffs
        cutoffs = cutoffs.select(
            pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "cutoff"
        )
        games = scored_games(actuals, scoring)
        all_seasons = sorted(set(games["season"].unique().to_list()) | set(wanted))
        pos_q = position_quantiles(games, all_seasons)
        fallback = fallback_means(games, players, all_seasons)

        # residuals: every played game against the estimate issued for its own week
        played_targets = _targets(actuals, None, cutoffs, played_only=True)
        played_est = self._estimates(games, played_targets, players, pos_q, fallback)
        residuals = played_est.join(
            games.select("entity_id", "season", "week", "kickoff_utc", "points"),
            on=["entity_id", "season", "week"],
            how="inner",
        ).with_columns((pl.col("points") - pl.col("est")).alias("resid"))
        resid_hist = _residual_history(residuals)
        pos_r = position_residual_quantiles(residuals, wanted)

        targets = _targets(actuals, wanted, cutoffs)
        frame = self._estimates(games, targets, players, pos_q, fallback)
        frame = _as_of(frame, resid_hist).drop("hist_kickoff")
        frame = frame.with_columns(pl.col("n_resid").fill_null(0))
        frame = frame.join(pos_r, on=["season", "position"], how="left").join(
            pos_q.select("season", "position", "pos_min"), on=["season", "position"], how="left"
        )
        use_entity = pl.col("n_resid") >= RESIDUAL_MIN_GAMES
        est = pl.col("est")
        # min_horizontal skips nulls, so guard the case without a position minimum
        floor = (
            pl.when(pl.col("pos_min").is_null())
            .then(None)
            .otherwise(pl.min_horizontal(pl.col("pos_min"), est))
        )
        qexprs = []
        for q, s in TAIL_LEVELS:
            raw = est + pl.when(use_entity).then(pl.col(f"ent_r{s}")).otherwise(pl.col(f"pos_r{s}"))
            bounded = pl.min_horizontal(raw, est) if q < 0.5 else pl.max_horizontal(raw, est)
            clipped = pl.when(floor.is_not_null()).then(pl.max_horizontal(bounded, floor)).otherwise(bounded)
            qexprs.append(pl.when(raw.is_not_null()).then(clipped).otherwise(None).alias(f"pred_q{s}"))
        label = scoring_name or scoring.name or scoring.scoring_hash
        out = frame.with_columns(
            est.alias("pred_mean"),
            est.alias("pred_q50"),
            *qexprs,
            pl.lit(label).alias("scoring"),
            pl.lit(self.name).alias("model"),
            pl.when(use_entity).then(pl.lit("entity")).otherwise(pl.lit("position")).alias(
                "quantile_source"
            ),
        )
        cols = list(PREDICTION_COLUMNS) + (list(DIAGNOSTIC_COLUMNS) if diagnostics else [])
        out = out.select(cols).with_columns(
            pl.col(c).cast(pl.Float64) for c in ("pred_mean", *QUANTILE_COLUMNS)
        )
        return out.sort(["season", "week", "position", "entity_id"])

    def predict_seasons(
        self,
        seasons: Iterable[int],
        scoring: ScoringRules,
        purpose: str | None = None,
        *,
        store: SnapshotStore | None = None,
        diagnostics: bool = False,
    ) -> pl.DataFrame:
        """Predictions for every row of ``seasons`` under ``scoring`` (batch form).

        Reads every regular-season outcome from the first data season through
        ``max(seasons)``, so the holdout guard is applied to that whole range
        (predicting 2026 reads 2025 lags and needs the evaluator's release).
        """
        wanted = sorted({int(s) for s in seasons})
        if not wanted:
            raise ValueError("seasons is empty")
        data = self._load(wanted[-1], purpose or self.purpose, store)
        return self.predict_from_actuals(
            data["actuals"],
            wanted,
            scoring,
            players=data["players"],
            cutoffs=data["cutoffs"],
            diagnostics=diagnostics,
        )

    def _load(
        self, season: int, purpose: str, store: SnapshotStore | None = None
    ) -> dict[str, object]:
        settings = get_settings()
        history = list(range(min(settings.first_season, season), season + 1))
        assert_seasons_allowed(history, purpose)
        store = store or self._store or SnapshotStore(settings.processed_dir)
        actuals = actual_statlines(history, purpose, store=store)
        players = store.read("dim_player", columns=["player_id", "draft_round"])
        games = store.read("dim_game", seasons=history, columns=["season", "week", "kickoff_utc"])
        return {
            "actuals": actuals,
            "players": players,
            "cutoffs": week_cutoffs(games),
            "max_season": season,
        }

    # -- WeeklyModel protocol ---------------------------------------------- #

    def fit(self, cutoff: tuple[int, int]) -> _Baseline:
        """Record the cutoff; baselines have no fitted parameters."""
        self._cutoff = (int(cutoff[0]), int(cutoff[1]))
        return self

    def predict(
        self, season: int, week: int, scorings: Mapping[str, ScoringRules]
    ) -> pl.DataFrame:
        """Rows for every entity with a regular-season stat-line row in ``(season, week)``."""
        season, week = int(season), int(week)
        if self._cutoff is not None and (season, week) < self._cutoff:
            raise ValueError(f"cannot predict {(season, week)} before the fit cutoff {self._cutoff}")
        if self._data is None or int(self._data["max_season"]) < season:
            self._data = self._load(season, self.purpose)
            self._cache.clear()
        frames = []
        for label, rules in scorings.items():
            key = (season, label, rules.scoring_hash)
            if key not in self._cache:
                self._cache[key] = self.predict_from_actuals(
                    self._data["actuals"],
                    [season],
                    rules,
                    players=self._data["players"],
                    cutoffs=self._data["cutoffs"],
                    scoring_name=label,
                )
            frames.append(self._cache[key].filter(pl.col("week") == week))
        if not frames:
            return pl.DataFrame(schema={c: pl.String for c in PREDICTION_COLUMNS})
        return pl.concat(frames, how="vertical_relaxed")


class RollingAverageBaseline(_Baseline):
    """Baseline 1: mean of the last ``window`` games, or an EW mean when ``ew_alpha`` is set."""

    def __init__(
        self,
        window: int = 4,
        ew_alpha: float | None = None,
        *,
        name: str | None = None,
        purpose: str = "backtest",
        store: SnapshotStore | None = None,
    ) -> None:
        if ew_alpha is None and window < 1:
            raise ValueError("window must be at least 1")
        if ew_alpha is not None and not 0.0 < ew_alpha <= 1.0:
            raise ValueError("ew_alpha must be in (0, 1]")
        super().__init__(purpose=purpose, store=store)
        self.window = window
        self.ew_alpha = ew_alpha
        default = (
            f"rolling_ew_{int(round(ew_alpha * 100)):03d}"
            if ew_alpha is not None
            else f"rolling_avg_{window}"
        )
        self.name = name or default

    def __repr__(self) -> str:
        arg = f"ew_alpha={self.ew_alpha}" if self.ew_alpha is not None else f"window={self.window}"
        return f"RollingAverageBaseline({arg})"

    def _mean_expr(self) -> pl.Expr:
        if self.ew_alpha is not None:
            return pl.col("points").ewm_mean(alpha=self.ew_alpha, adjust=True)
        return pl.col("points").rolling_mean(window_size=self.window, min_samples=1)

    def _history_estimate(self, games: pl.DataFrame, targets: pl.DataFrame) -> pl.DataFrame:
        hist = games.select(
            "entity_id",
            pl.col("kickoff_utc").alias("hist_kickoff"),
            self._mean_expr().over("entity_id").alias("hist_est"),
            pl.int_range(1, pl.len() + 1).over("entity_id").alias("n_hist"),
        )
        out = _as_of(targets, hist).drop("hist_kickoff")
        return out.with_columns(pl.col("n_hist").fill_null(0))


class RollingMedianBaseline(_Baseline):
    """Median of the last ``window`` games (default 8), the strongest simple MAE baseline."""

    def __init__(
        self,
        window: int = MEDIAN_WINDOW,
        *,
        name: str | None = None,
        purpose: str = "backtest",
        store: SnapshotStore | None = None,
    ) -> None:
        if window < 1:
            raise ValueError("window must be at least 1")
        super().__init__(purpose=purpose, store=store)
        self.window = window
        self.name = name or f"rolling_median_{window}"

    def __repr__(self) -> str:
        return f"RollingMedianBaseline(window={self.window})"

    def _history_estimate(self, games: pl.DataFrame, targets: pl.DataFrame) -> pl.DataFrame:
        hist = games.select(
            "entity_id",
            pl.col("kickoff_utc").alias("hist_kickoff"),
            pl.col("points")
            .rolling_quantile(
                quantile=0.5, interpolation="linear", window_size=self.window, min_samples=1
            )
            .over("entity_id")
            .alias("hist_est"),
            pl.int_range(1, pl.len() + 1).over("entity_id").alias("n_hist"),
        )
        out = _as_of(targets, hist).drop("hist_kickoff")
        return out.with_columns(pl.col("n_hist").fill_null(0))


class PriorSeasonBaseline(_Baseline):
    """Baseline 2: the previous season's per-game average with a rookie fallback."""

    def __init__(
        self,
        *,
        name: str | None = None,
        purpose: str = "backtest",
        store: SnapshotStore | None = None,
    ) -> None:
        super().__init__(purpose=purpose, store=store)
        self.name = name or "prior_season"

    def __repr__(self) -> str:
        return "PriorSeasonBaseline()"

    def _history_estimate(self, games: pl.DataFrame, targets: pl.DataFrame) -> pl.DataFrame:
        prior = season_averages(games).select(
            "entity_id", "season", pl.col("prior_mean").alias("hist_est")
        )
        out = self._n_hist(games, targets)
        return out.join(prior, on=["entity_id", "season"], how="left")


def default_baselines(purpose: str = "backtest") -> list[_Baseline]:
    """Fresh instances of the five transparent baselines (D-053)."""
    return [
        RollingAverageBaseline(window=4, purpose=purpose),
        RollingAverageBaseline(window=3, purpose=purpose),
        RollingAverageBaseline(ew_alpha=0.35, purpose=purpose),
        PriorSeasonBaseline(purpose=purpose),
        RollingMedianBaseline(window=8, purpose=purpose),
    ]


#: Module-level instances kept for wave-one callers; use :func:`default_baselines`
#: for fresh instances (each instance caches loaded outcomes).
DEFAULT_BASELINES: tuple[_Baseline, ...] = tuple(default_baselines())


__all__ = [
    "DEFAULT_BASELINES",
    "DIAGNOSTIC_COLUMNS",
    "PREDICTION_COLUMNS",
    "QUANTILE_COLUMNS",
    "PriorSeasonBaseline",
    "RollingAverageBaseline",
    "RollingMedianBaseline",
    "default_baselines",
    "fallback_means",
    "position_quantiles",
    "position_residual_quantiles",
    "scored_games",
    "season_averages",
    "week_cutoffs",
]
