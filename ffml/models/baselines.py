"""Transparent baselines 1 and 2 (MODELING.md section 3).

Both baselines score actual stat lines (:func:`ffml.scoring.actuals.actual_statlines`)
with the league rules in question, so they work under any scoring, and emit
the prediction frame contract of ``ffml/eval/metrics.py``: one row per
``(entity_id, season, week)`` with ``played`` true and the columns
``entity_id, position, season, week, pred_mean, pred_q10, pred_q50, pred_q90,
model``.

Information set (D-046)
=======================
A row in season-week ``(s, w)`` uses only games whose kickoff precedes the
first kickoff of that season-week. The row's own game is therefore never
seen, Thursday and Sunday players share one information set, and a game
rescheduled past the next week's first kickoff is not used for that week.
Histories run across season boundaries (the last 4 games in week 1 come from
the previous season). Only games the entity played count.

Baseline 1, :class:`RollingAverageBaseline`
    ``pred_mean`` is the mean of the last ``window`` games (default 4, variant
    3) or, with ``ew_alpha`` set, an exponentially weighted mean over every
    past game (``alpha = 0.35`` variant, most recent game weighted most).

Baseline 2, :class:`PriorSeasonBaseline`
    ``pred_mean`` is the per-game average over the previous season.

Fallbacks (both baselines)
    Quantiles ``pred_q10/q50/q90`` are empirical quantiles (linear
    interpolation) of the entity's last 8 eligible games when at least 4
    exist, otherwise the quantiles of every game played at the position in
    seasons before the row's season. When the entity has no usable history
    for the mean (no past game for baseline 1, no game in the previous season
    for baseline 2), ``pred_mean`` is the position-and-draft-round average
    from prior seasons: the per-game average, over seasons before the row's
    season, of players at that position and draft round (``0`` for
    undrafted; DST rows use round 0) who had no game in their previous
    season. Cells with fewer than :data:`MIN_FALLBACK_GAMES` games fall back
    to the position-level average of the same population, and when that
    population is empty (the season after the first data season, whose
    players' previous season is unknown) to the position's average over all
    games in prior seasons. Rows in the first season of the data get null
    fallbacks and quantiles.

Quantile modes
    ``quantile_mode="empirical"`` (default, as specified) uses the reference
    quantiles as they are, so every baseline variant shares
    ``pred_q10/q50/q90`` and differs only in ``pred_mean``.
    ``quantile_mode="shifted"`` keeps the reference shape but re-centres it on
    the baseline's mean (``pred_mean + Q - mean(reference)``), so the median
    differs by model; model labels get a ``_shifted`` suffix.

``predict(..., diagnostics=True)`` adds ``n_hist`` (eligible past games),
``mean_source`` (``history`` or ``fallback``) and ``quantile_source``
(``entity`` or ``position``).
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

from ffml.config import get_settings
from ffml.data.store import SnapshotStore
from ffml.holdout import assert_seasons_allowed
from ffml.scoring.actuals import actual_statlines
from ffml.scoring.rules import ScoringRules

QUANTILE_WINDOW = 8
QUANTILE_MIN_GAMES = 4
QUANTILE_MODES: tuple[str, ...] = ("empirical", "shifted")
MIN_FALLBACK_GAMES = 20
QUANTILES: tuple[tuple[str, float], ...] = (("q10", 0.1), ("q50", 0.5), ("q90", 0.9))
PREDICTION_COLUMNS: tuple[str, ...] = (
    "entity_id",
    "position",
    "season",
    "week",
    "pred_mean",
    "pred_q10",
    "pred_q50",
    "pred_q90",
    "model",
)
DIAGNOSTIC_COLUMNS: tuple[str, ...] = ("n_hist", "mean_source", "quantile_source")


# --------------------------------------------------------------------------- #
# Shared preparation
# --------------------------------------------------------------------------- #


def week_cutoffs(games: pl.DataFrame) -> pl.DataFrame:
    """First kickoff per ``(season, week)`` from any frame with those columns and ``kickoff_utc``."""
    return games.group_by(["season", "week"]).agg(pl.col("kickoff_utc").min().alias("cutoff"))


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
    """Per ``(season, position)``: mean and quantiles of all games at the position in earlier seasons."""
    out = []
    for season in sorted(set(seasons)):
        past = games.filter(pl.col("season") < season)
        if past.height == 0:
            continue
        out.append(
            past.group_by("position")
            .agg(
                pl.col("points").mean().alias("pos_mean"),
                *[
                    pl.col("points").quantile(q, interpolation="linear").alias(f"pos_{name}")
                    for name, q in QUANTILES
                ],
            )
            .with_columns(pl.lit(season, dtype=pl.Int32).alias("season"))
        )
    schema = {"position": pl.String, "pos_mean": pl.Float64}
    schema.update({f"pos_{n}": pl.Float64 for n, _ in QUANTILES})
    schema["season"] = pl.Int32
    if not out:
        return pl.DataFrame(schema=schema)
    return pl.concat(out, how="vertical_relaxed").cast(schema)


def _targets(actuals: pl.DataFrame, seasons: list[int], cutoffs: pl.DataFrame) -> pl.DataFrame:
    rows = actuals.filter(pl.col("played") & pl.col("season").is_in(seasons)).select(
        "entity_id",
        "position",
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
    )
    rows = rows.unique(["entity_id", "season", "week"], keep="first", maintain_order=True)
    cut = cutoffs.select(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32), "cutoff")
    out = rows.join(cut, on=["season", "week"], how="left")
    if out["cutoff"].null_count():
        raise ValueError("some target season-weeks have no kickoff cutoff")
    return out


def _history_features(games: pl.DataFrame, mean_expr: pl.Expr) -> pl.DataFrame:
    """Per played game: the entity's stats over its games up to and including this one."""
    by = "entity_id"
    feats = games.with_columns(
        mean_expr.over(by).alias("hist_mean"),
        pl.int_range(1, pl.len() + 1).over(by).alias("n_hist"),
        pl.col("points")
        .rolling_mean(window_size=QUANTILE_WINDOW, min_samples=QUANTILE_MIN_GAMES)
        .over(by)
        .alias("ent_mean8"),
        *[
            pl.col("points")
            .rolling_quantile(
                quantile=q,
                interpolation="linear",
                window_size=QUANTILE_WINDOW,
                min_samples=QUANTILE_MIN_GAMES,
            )
            .over(by)
            .alias(f"ent_{name}")
            for name, q in QUANTILES
        ],
    )
    return feats.select(
        "entity_id",
        pl.col("kickoff_utc").alias("hist_kickoff"),
        "hist_mean",
        "n_hist",
        "ent_mean8",
        *[f"ent_{n}" for n, _ in QUANTILES],
    ).sort("hist_kickoff")


def _as_of(targets: pl.DataFrame, history: pl.DataFrame) -> pl.DataFrame:
    """Attach the latest history row whose kickoff is strictly before the target's cutoff."""
    joined = targets.sort("cutoff").join_asof(
        history,
        left_on="cutoff",
        right_on="hist_kickoff",
        by="entity_id",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    return joined.with_columns(pl.col("n_hist").fill_null(0))


def _finish(
    frame: pl.DataFrame,
    model: str,
    games: pl.DataFrame,
    players: pl.DataFrame | None,
    seasons: list[int],
    diagnostics: bool,
    quantile_mode: str = "empirical",
) -> pl.DataFrame:
    """Apply the quantile and mean fallbacks and select the output columns."""
    pos_q = position_quantiles(games, seasons)
    fb = fallback_means(games, players, seasons)
    cell = fb.filter(pl.col("fallback_games") >= MIN_FALLBACK_GAMES).drop("fallback_games")
    level = fb.filter(pl.col("draft_round") == -1).select(
        "season", "position", pl.col("fallback_mean").alias("fallback_pos_mean")
    )
    frame = _attach_draft_round(frame, players)
    frame = (
        frame.join(pos_q, on=["season", "position"], how="left")
        .join(cell, on=["season", "position", "draft_round"], how="left")
        .join(level, on=["season", "position"], how="left")
    )
    use_entity_q = pl.col("ent_q10").is_not_null()
    has_mean = pl.col("base_mean").is_not_null()
    frame = frame.with_columns(
        pl.when(has_mean)
        .then(pl.col("base_mean"))
        .otherwise(pl.coalesce("fallback_mean", "fallback_pos_mean", "pos_mean"))
        .alias("pred_mean"),
        pl.when(use_entity_q)
        .then(pl.col("ent_mean8"))
        .otherwise(pl.col("pos_mean"))
        .alias("ref_mean"),
    )
    if quantile_mode == "empirical":
        quantile_exprs = [
            pl.when(use_entity_q)
            .then(pl.col(f"ent_{n}"))
            .otherwise(pl.col(f"pos_{n}"))
            .alias(f"pred_{n}")
            for n, _ in QUANTILES
        ]
    elif quantile_mode == "shifted":
        quantile_exprs = [
            (
                pl.col("pred_mean")
                + pl.when(use_entity_q).then(pl.col(f"ent_{n}")).otherwise(pl.col(f"pos_{n}"))
                - pl.col("ref_mean")
            ).alias(f"pred_{n}")
            for n, _ in QUANTILES
        ]
    else:
        raise ValueError(f"quantile_mode must be 'empirical' or 'shifted', got {quantile_mode!r}")
    out = frame.with_columns(
        *quantile_exprs,
        pl.lit(model).alias("model"),
        pl.when(has_mean)
        .then(pl.lit("history"))
        .otherwise(pl.lit("fallback"))
        .alias("mean_source"),
        pl.when(use_entity_q)
        .then(pl.lit("entity"))
        .otherwise(pl.lit("position"))
        .alias("quantile_source"),
    )
    cols = list(PREDICTION_COLUMNS) + (list(DIAGNOSTIC_COLUMNS) if diagnostics else [])
    return out.select(cols).sort(["season", "week", "position", "entity_id"])


# --------------------------------------------------------------------------- #
# Baselines
# --------------------------------------------------------------------------- #


class _Baseline:
    """Common loading logic; subclasses implement :meth:`predict_from_actuals`."""

    name: str = "baseline"
    quantile_mode: str = "empirical"

    def _set_quantile_mode(self, quantile_mode: str) -> None:
        if quantile_mode not in QUANTILE_MODES:
            raise ValueError(
                f"quantile_mode must be one of {QUANTILE_MODES}, got {quantile_mode!r}"
            )
        self.quantile_mode = quantile_mode

    def _label(self, base: str) -> str:
        return base if self.quantile_mode == "empirical" else f"{base}_shifted"

    def predict(
        self,
        seasons: Iterable[int],
        scoring: ScoringRules,
        purpose: str = "backtest",
        *,
        store: SnapshotStore | None = None,
        diagnostics: bool = False,
    ) -> pl.DataFrame:
        """Predictions for every played row of ``seasons`` under ``scoring``.

        Reads every regular-season outcome from the first data season through
        ``max(seasons)``, so the holdout guard is applied to that whole range
        (predicting 2026 reads 2025 lags and needs the evaluator's unlock).
        """
        wanted = sorted({int(s) for s in seasons})
        if not wanted:
            raise ValueError("seasons is empty")
        settings = get_settings()
        history = list(range(min(settings.first_season, wanted[0]), wanted[-1] + 1))
        assert_seasons_allowed(history, purpose)
        store = store or SnapshotStore(settings.processed_dir)
        actuals = actual_statlines(history, purpose, store=store)
        players = store.read("dim_player", columns=["player_id", "draft_round"])
        games = store.read("dim_game", seasons=history, columns=["season", "week", "kickoff_utc"])
        return self.predict_from_actuals(
            actuals,
            wanted,
            scoring,
            players=players,
            cutoffs=week_cutoffs(games),
            diagnostics=diagnostics,
        )

    def predict_from_actuals(
        self,
        actuals: pl.DataFrame,
        seasons: Iterable[int],
        scoring: ScoringRules,
        *,
        players: pl.DataFrame | None = None,
        cutoffs: pl.DataFrame | None = None,
        diagnostics: bool = False,
    ) -> pl.DataFrame:
        raise NotImplementedError


class RollingAverageBaseline(_Baseline):
    """Baseline 1: mean of the last ``window`` games, or an EW mean when ``ew_alpha`` is set."""

    def __init__(
        self, window: int = 4, ew_alpha: float | None = None, quantile_mode: str = "empirical"
    ) -> None:
        if ew_alpha is None and window < 1:
            raise ValueError("window must be at least 1")
        if ew_alpha is not None and not 0.0 < ew_alpha <= 1.0:
            raise ValueError("ew_alpha must be in (0, 1]")
        self.window = window
        self.ew_alpha = ew_alpha
        self._set_quantile_mode(quantile_mode)
        base = f"rolling_ew{ew_alpha:g}" if ew_alpha is not None else f"rolling_l{window}"
        self.name = self._label(base)

    def __repr__(self) -> str:
        arg = f"ew_alpha={self.ew_alpha}" if self.ew_alpha is not None else f"window={self.window}"
        return f"RollingAverageBaseline({arg}, quantile_mode={self.quantile_mode!r})"

    def _mean_expr(self) -> pl.Expr:
        if self.ew_alpha is not None:
            return pl.col("points").ewm_mean(alpha=self.ew_alpha, adjust=True)
        return pl.col("points").rolling_mean(window_size=self.window, min_samples=1)

    def predict_from_actuals(
        self,
        actuals: pl.DataFrame,
        seasons: Iterable[int],
        scoring: ScoringRules,
        *,
        players: pl.DataFrame | None = None,
        cutoffs: pl.DataFrame | None = None,
        diagnostics: bool = False,
    ) -> pl.DataFrame:
        wanted = sorted({int(s) for s in seasons})
        cutoffs = week_cutoffs(actuals) if cutoffs is None else cutoffs
        games = scored_games(actuals, scoring)
        history = _history_features(games, self._mean_expr())
        frame = _as_of(_targets(actuals, wanted, cutoffs), history)
        frame = frame.with_columns(pl.col("hist_mean").alias("base_mean"))
        return _finish(frame, self.name, games, players, wanted, diagnostics, self.quantile_mode)


class PriorSeasonBaseline(_Baseline):
    """Baseline 2: the previous season's per-game average with a rookie fallback."""

    def __init__(self, quantile_mode: str = "empirical") -> None:
        self._set_quantile_mode(quantile_mode)
        self.name = self._label("prior_season")

    def __repr__(self) -> str:
        return f"PriorSeasonBaseline(quantile_mode={self.quantile_mode!r})"

    def predict_from_actuals(
        self,
        actuals: pl.DataFrame,
        seasons: Iterable[int],
        scoring: ScoringRules,
        *,
        players: pl.DataFrame | None = None,
        cutoffs: pl.DataFrame | None = None,
        diagnostics: bool = False,
    ) -> pl.DataFrame:
        wanted = sorted({int(s) for s in seasons})
        cutoffs = week_cutoffs(actuals) if cutoffs is None else cutoffs
        games = scored_games(actuals, scoring)
        history = _history_features(games, pl.col("points"))
        frame = _as_of(_targets(actuals, wanted, cutoffs), history)
        prior = season_averages(games).select("entity_id", "season", "prior_mean")
        frame = frame.join(prior, on=["entity_id", "season"], how="left").with_columns(
            pl.col("prior_mean").alias("base_mean")
        )
        return _finish(frame, self.name, games, players, wanted, diagnostics, self.quantile_mode)


DEFAULT_BASELINES: tuple[_Baseline, ...] = (
    RollingAverageBaseline(window=4),
    RollingAverageBaseline(window=3),
    RollingAverageBaseline(ew_alpha=0.35),
    PriorSeasonBaseline(),
)


__all__ = [
    "DEFAULT_BASELINES",
    "PREDICTION_COLUMNS",
    "PriorSeasonBaseline",
    "RollingAverageBaseline",
    "fallback_means",
    "position_quantiles",
    "scored_games",
    "season_averages",
    "week_cutoffs",
]
