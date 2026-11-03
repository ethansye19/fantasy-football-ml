"""Baseline 3: public consensus (FantasyPros weekly ECR) for private evaluation only.

DECISIONS D-007 (owner decision 2026-11-01): the DynastyProcess archive of
FantasyPros weekly expert consensus rankings has no stated licence and
FantasyPros content is proprietary, so it is used for private evaluation
only. The raw archive and every derived table stay under ``data/``
(gitignored) and are never loaded by the API or the web app; committed
reports and the model card show aggregate comparison metrics only, never
ranking rows. This module is the only code outside the processing step that
reads ``data/processed/restricted/fact_ecr_weekly``. Everything it derives
(selected ranks, the rank-to-points curve, the prediction frame) is held in
memory; :func:`write_restricted_frame` refuses any path inside the repository
outside ``data/``.

Selection rules (MODELING.md section 3, D-036, D-040)
-----------------------------------------------------

* Pages: ``qb``, ``ppr-rb``, ``ppr-wr``, ``ppr-te``, ``k``, ``dst`` (weekly
  pages exist only as PPR for RB, WR, TE and scoring-free for QB, K, DST, so
  the comparison runs under PPR). IDP pages are ignored.
* ``rank_pred`` is the positional ECR rank: the ordinal rank of ``ecr``
  within one scrape of one page (ties by ``sd``, then ``fantasypros_id``),
  computed over the whole page so that a Thursday player keeps his slot in
  the expert ordering even though he drops out of the comparison.
* Per entity and season-week the latest scrape with ``valid_for_game`` true
  is used (null counts as false), so Thursday players are excluded.
* Joins to actuals go through ``player_id``; DST joins through ``team``.
  Rows without a ``player_id`` (or DST rows without a team) are dropped and
  counted, and so are rows whose page position differs from the player's
  position in ``fact_player_game`` (cross-listed players).

Rank-to-points curve
--------------------

For an evaluated season ``s`` the curve is fitted on consensus rows of
seasons strictly before ``s`` joined to their actual PPR points (active
players only). Per page position and integer rank ``r`` the curve pools the
actuals of ranks within ``r +/- max(1, round(0.15 r))`` (widened until at
least 30 rows), takes the median (``pred_q50``), mean (``pred_mean``) and 10th
and 90th percentiles (``pred_q10``, ``pred_q90``), and makes each
non-increasing in rank with isotonic regression. Ranks past the deepest
fitted rank take the deepest value. A position without prior data gets null
predictions (counted in the diagnostics).

Actual points (evaluator-local reference scoring)
-------------------------------------------------

To stay independent of ``ffml/scoring`` this module computes its own
reference points, labelled as such: standard PPR for players (identical to
nflverse ``fantasy_points_ppr`` for QB/RB/WR/TE), plus Sleeper default
kicking for K and Sleeper default DST scoring. They are used only for the
consensus curve and the consensus frame's ``actual`` column.

Every function that reads outcomes calls
:func:`ffml.holdout.assert_seasons_allowed` first, including for the prior
seasons used to fit the curve.
"""

from __future__ import annotations

import logging
from collections.abc import Iterable, Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path

import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression

from ffml.config import PROJECT_ROOT, get_settings
from ffml.data.store import SnapshotStore
from ffml.holdout import assert_seasons_allowed

log = logging.getLogger(__name__)

RESTRICTED_DATASET = "fact_ecr_weekly"
MODEL_NAME = "consensus_ecr"
PAGE_POSITION: dict[str, str] = {
    "qb": "QB",
    "ppr-rb": "RB",
    "ppr-wr": "WR",
    "ppr-te": "TE",
    "k": "K",
    "dst": "DST",
}
EVAL_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K", "DST")
COVERAGE_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")
SUPPORTED_SCORING: tuple[str, ...] = ("ppr",)

# Evaluator-local reference scoring (see module docstring); not ffml/scoring.
PPR_WEIGHTS: dict[str, float] = {
    "receptions": 1.0,
    "rec_yds": 0.1,
    "rec_td": 6.0,
    "rush_yds": 0.1,
    "rush_td": 6.0,
    "pass_yds": 0.04,
    "pass_td": 4.0,
    "interceptions": -2.0,
    "fumbles_lost": -2.0,
    "two_pt_conversions": 2.0,
    "return_tds": 6.0,
}
KICKING_WEIGHTS: dict[str, float] = {
    "fg_made_0_39": 3.0,
    "fg_made_40_49": 4.0,
    "fg_made_50p": 5.0,
    "fg_missed": -1.0,
    "pat_made": 1.0,
}
PAT_MISSED_WEIGHT = -1.0
DST_WEIGHTS: dict[str, float] = {
    "sacks": 1.0,
    "interceptions": 2.0,
    "fumble_recoveries": 2.0,
    "def_td": 6.0,
    "return_td": 6.0,
    "safeties": 2.0,
    "blocked_kicks": 2.0,
}
#: (upper bound inclusive, points) tiers for points allowed (Sleeper defaults)
DST_POINTS_ALLOWED: tuple[tuple[float, float], ...] = (
    (0, 10.0),
    (6, 7.0),
    (13, 4.0),
    (20, 1.0),
    (27, 0.0),
    (34, -1.0),
    (float("inf"), -4.0),
)

ACTUAL_COLUMNS: tuple[str, ...] = (
    "entity_id", "position", "season", "week", "team", "game_id", "actual", "active",
)
PREDICTION_SCHEMA: dict[str, pl.DataType] = {
    "entity_id": pl.String(),
    "position": pl.String(),
    "season": pl.Int32(),
    "week": pl.Int32(),
    "pred_mean": pl.Float64(),
    "pred_q10": pl.Float64(),
    "pred_q50": pl.Float64(),
    "pred_q90": pl.Float64(),
    "actual": pl.Float64(),
    "active": pl.Boolean(),
    "model": pl.String(),
    "team": pl.String(),
    "game_id": pl.String(),
    "rank_pred": pl.Float64(),
}
PREDICTION_COLUMNS: tuple[str, ...] = tuple(PREDICTION_SCHEMA)


# --------------------------------------------------------------------------- #
# Reference scoring and actuals
# --------------------------------------------------------------------------- #


def _weighted_sum(weights: Mapping[str, float]) -> pl.Expr:
    return pl.sum_horizontal(
        [pl.col(c).cast(pl.Float64).fill_null(0.0) * w for c, w in weights.items()]
    )


def ppr_points_expr() -> pl.Expr:
    """Standard PPR points from fact_player_game stat columns (reference scoring)."""
    return _weighted_sum(PPR_WEIGHTS)


def kicking_points_expr() -> pl.Expr:
    """Sleeper default kicking points (3/3/3/4/5 by distance, PAT 1, misses -1)."""
    pat_missed = (pl.col("pat_att").cast(pl.Float64).fill_null(0.0)
                  - pl.col("pat_made").cast(pl.Float64).fill_null(0.0))
    return _weighted_sum(KICKING_WEIGHTS) + PAT_MISSED_WEIGHT * pat_missed


def dst_points_expr() -> pl.Expr:
    """Sleeper default DST points from fact_dst_game columns (reference scoring)."""
    allowed = pl.col("points_allowed").cast(pl.Float64)
    tier: pl.Expr | None = None
    for bound, points in reversed(DST_POINTS_ALLOWED):
        tier = pl.lit(points) if tier is None else pl.when(allowed <= bound).then(points).otherwise(tier)
    assert tier is not None
    return _weighted_sum(DST_WEIGHTS) + pl.when(allowed.is_null()).then(0.0).otherwise(tier)


def actual_points(player_game: pl.DataFrame, dst_game: pl.DataFrame | None = None) -> pl.DataFrame:
    """Regular-season actual reference points in the prediction-frame key shape.

    Players: one row per fact_player_game row (``active = played``; a roster
    row that did not play scores zero). Kickers add kicking points. DST: one
    row per fact_dst_game row keyed by team, always active.
    """
    pg = player_game.filter(pl.col("game_type") == "REG") if "game_type" in player_game.columns else player_game
    points = ppr_points_expr()
    players = pg.select(
        pl.col("player_id").cast(pl.String).alias("entity_id"),
        pl.col("position").cast(pl.String),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("team").cast(pl.String),
        pl.col("game_id").cast(pl.String),
        pl.when(pl.col("position") == "K")
        .then(points + kicking_points_expr())
        .otherwise(points)
        .alias("actual"),
        pl.col("played").cast(pl.Boolean).fill_null(False).alias("active"),
    )
    frames = [players]
    if dst_game is not None and not dst_game.is_empty():
        dg = dst_game.filter(pl.col("game_type") == "REG") if "game_type" in dst_game.columns else dst_game
        frames.append(
            dg.select(
                pl.col("team").cast(pl.String).alias("entity_id"),
                pl.lit("DST").alias("position"),
                pl.col("season").cast(pl.Int32),
                pl.col("week").cast(pl.Int32),
                pl.col("team").cast(pl.String),
                pl.col("game_id").cast(pl.String),
                dst_points_expr().alias("actual"),
                pl.lit(True).alias("active"),
            )
        )
    return pl.concat(frames, how="vertical_relaxed").select(ACTUAL_COLUMNS)


# --------------------------------------------------------------------------- #
# Consensus selection
# --------------------------------------------------------------------------- #


def page_ranks(ecr: pl.DataFrame) -> pl.DataFrame:
    """Keep evaluated pages and add ``page_position`` and ``page_rank`` (1 = best).

    The rank is ordinal within ``(season, week, scrape_date, fp_page)`` by
    ``ecr``, then ``sd``, then ``fantasypros_id``.
    """
    scrape = ["season", "week", "scrape_date", "fp_page"]
    f = ecr.filter(pl.col("fp_page").is_in(list(PAGE_POSITION)))
    if "game_type" in f.columns:
        f = f.filter(pl.col("game_type").fill_null("REG") == "REG")
    f = f.sort([*scrape, "ecr", "sd", "fantasypros_id"], nulls_last=True)
    return f.with_columns(
        pl.col("fp_page").replace_strict(PAGE_POSITION, return_dtype=pl.String).alias("page_position"),
        pl.int_range(1, pl.len() + 1).over(scrape).cast(pl.Int32).alias("page_rank"),
    )


def latest_valid(ranked: pl.DataFrame) -> pl.DataFrame:
    """Per entity, page and season-week keep the latest scrape with ``valid_for_game`` true."""
    valid = ranked.filter(pl.col("valid_for_game").fill_null(False))
    key = ["season", "week", "fp_page", "fantasypros_id"]
    return valid.sort([*key, "scrape_date"]).group_by(key, maintain_order=True).last()


@dataclass
class Diagnostics:
    """Counts of rows kept and dropped per season (aggregates only)."""

    counts: dict[int, dict[str, int]] = field(default_factory=dict)

    def add(self, frame: pl.DataFrame, name: str) -> None:
        if frame.is_empty():
            return
        for season, n in frame.group_by("season").len().iter_rows():
            self.counts.setdefault(int(season), {}).setdefault(name, 0)
            self.counts[int(season)][name] += int(n)

    def add_counts(self, counts: pl.DataFrame, name: str) -> None:
        """Add a ``season, n`` frame of counts under ``name``."""
        for season, n in counts.select("season", "n").iter_rows():
            if n:
                self.counts.setdefault(int(season), {}).setdefault(name, 0)
                self.counts[int(season)][name] += int(n)

    def set(self, season: int, name: str, value: int) -> None:
        self.counts.setdefault(int(season), {})[name] = int(value)

    def to_frame(self) -> pl.DataFrame:
        if not self.counts:
            return pl.DataFrame({"season": []}, schema={"season": pl.Int32})
        names = sorted({k for v in self.counts.values() for k in v})
        rows = [{"season": s, **{n: self.counts[s].get(n, 0) for n in names}} for s in sorted(self.counts)]
        return pl.DataFrame(rows)


def join_actuals(selected: pl.DataFrame, actuals: pl.DataFrame, diag: Diagnostics | None = None) -> pl.DataFrame:
    """Join selected consensus rows to actuals (player_id for players, team for DST).

    Returns one row per ``(entity_id, season, week)`` with ``rank_pred``,
    ``position`` (the page position), ``actual``, ``active``, ``team`` and
    ``game_id``. Dropped rows are counted in ``diag``.
    """
    diag = diag if diag is not None else Diagnostics()
    is_dst = pl.col("page_position") == "DST"
    no_id = selected.filter((~is_dst & pl.col("player_id").is_null()) | (is_dst & pl.col("team").is_null()))
    diag.add(no_id, "dropped_no_id")
    keyed = selected.filter(
        (~is_dst & pl.col("player_id").is_not_null()) | (is_dst & pl.col("team").is_not_null())
    ).select(
        pl.when(is_dst).then(pl.col("team")).otherwise(pl.col("player_id")).cast(pl.String).alias("entity_id"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("page_position"),
        pl.col("page_rank").cast(pl.Float64).alias("rank_pred"),
        pl.col("team").cast(pl.String).alias("ecr_team"),
    )
    act = actuals.select(
        "entity_id", "season", "week",
        pl.col("position").alias("actual_position"), "team", "game_id", "actual", "active",
    )
    joined = keyed.join(act, on=["entity_id", "season", "week"], how="left")
    mismatch = joined.filter(
        pl.col("actual_position").is_not_null() & (pl.col("actual_position") != pl.col("page_position"))
    )
    diag.add(mismatch, "dropped_position_mismatch")
    kept = joined.filter(
        pl.col("actual_position").is_null() | (pl.col("actual_position") == pl.col("page_position"))
    )
    # an entity without a game row can sit on two pages; keep its best page rank
    kept = kept.sort(["entity_id", "season", "week", "rank_pred"])
    deduped = kept.group_by(["entity_id", "season", "week"], maintain_order=True).first()
    if kept.height != deduped.height:
        before = kept.group_by("season").len()
        after = deduped.group_by("season").len().rename({"len": "after"})
        diag.add_counts(
            before.join(after, on="season").select("season", (pl.col("len") - pl.col("after")).alias("n")),
            "dropped_duplicate_pages",
        )
    return deduped.select(
        "entity_id",
        pl.col("page_position").alias("position"),
        "season",
        "week",
        "rank_pred",
        pl.coalesce("team", "ecr_team").alias("team"),
        "game_id",
        "actual",
        pl.col("active").fill_null(False),
    )


def select_consensus(ecr: pl.DataFrame, actuals: pl.DataFrame, diag: Diagnostics | None = None) -> pl.DataFrame:
    """Page ranks, latest valid scrape per entity, then the join to actuals."""
    diag = diag if diag is not None else Diagnostics()
    diag.add(ecr, "rows_in")
    diag.add(ecr.filter(~pl.col("fp_page").is_in(list(PAGE_POSITION))), "dropped_other_pages")
    ranked = page_ranks(ecr)
    diag.add(ranked.filter(~pl.col("valid_for_game").fill_null(False)), "dropped_not_valid_for_game")
    selected = latest_valid(ranked)
    diag.add(ranked.filter(pl.col("valid_for_game").fill_null(False)), "valid_rows")
    diag.add(selected, "selected_rows")
    return join_actuals(selected, actuals, diag)


# --------------------------------------------------------------------------- #
# Rank-to-points curve
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PositionCurve:
    """Smoothed quantiles of actual points by integer rank (index 0 is rank 1)."""

    q10: np.ndarray
    q50: np.ndarray
    q90: np.ndarray
    mean: np.ndarray
    n_train: int

    @property
    def max_rank(self) -> int:
        return int(self.q50.size)

    def predict(self, ranks: np.ndarray) -> dict[str, np.ndarray]:
        idx = np.clip(np.rint(ranks).astype(np.int64), 1, self.max_rank) - 1
        return {
            "pred_q10": self.q10[idx],
            "pred_q50": self.q50[idx],
            "pred_q90": self.q90[idx],
            "pred_mean": self.mean[idx],
        }


@dataclass(frozen=True)
class RankCurve:
    """Rank-to-points curves per position, fitted on ``seasons`` (held in memory only)."""

    positions: dict[str, PositionCurve]
    seasons: tuple[int, ...]


def _isotonic_decreasing(values: np.ndarray, weights: np.ndarray) -> np.ndarray:
    if values.size < 2:
        return values.copy()
    x = np.arange(1, values.size + 1, dtype=float)
    return IsotonicRegression(increasing=False).fit_transform(x, values, sample_weight=weights)


def fit_position_curve(
    ranks: np.ndarray, actual: np.ndarray, bandwidth: float = 0.15, min_count: int = 30
) -> PositionCurve | None:
    """Fit one position's curve from training ``(rank, actual)`` pairs (see module docstring)."""
    ok = np.isfinite(ranks) & np.isfinite(actual) & (ranks >= 1)
    ranks, actual = np.rint(ranks[ok]).astype(np.int64), actual[ok].astype(float)
    if ranks.size == 0:
        return None
    order = np.argsort(ranks, kind="stable")
    rs, act = ranks[order], actual[order]
    max_rank = int(rs[-1])
    need = min(min_count, rs.size)
    stats = np.empty((max_rank, 4))
    counts = np.empty(max_rank)
    for r in range(1, max_rank + 1):
        h = max(1, int(round(bandwidth * r)))
        while True:
            i0 = np.searchsorted(rs, r - h, side="left")
            i1 = np.searchsorted(rs, r + h, side="right")
            if i1 - i0 >= need or h > max_rank:
                break
            h += 1
        vals = act[i0:i1]
        stats[r - 1, :3] = np.quantile(vals, [0.1, 0.5, 0.9])
        stats[r - 1, 3] = vals.mean()
        counts[r - 1] = vals.size
    q10, q50, q90, mean = (_isotonic_decreasing(stats[:, k], counts) for k in range(4))
    q10 = np.minimum(q10, q50)
    q90 = np.maximum(q90, q50)
    return PositionCurve(q10=q10, q50=q50, q90=q90, mean=mean, n_train=int(rs.size))


def fit_rank_curve(
    train: pl.DataFrame, bandwidth: float = 0.15, min_count: int = 30
) -> RankCurve:
    """Fit curves per position from rows with ``position, season, rank_pred, actual, active``.

    Only active rows with a non-null actual are used.
    """
    rows = train.filter(pl.col("active").fill_null(False) & pl.col("actual").is_not_null())
    curves: dict[str, PositionCurve] = {}
    for (pos,), part in rows.partition_by(["position"], as_dict=True).items():
        curve = fit_position_curve(
            part["rank_pred"].cast(pl.Float64).to_numpy(),
            part["actual"].cast(pl.Float64).to_numpy(),
            bandwidth,
            min_count,
        )
        if curve is not None:
            curves[str(pos)] = curve
    seasons = tuple(sorted(int(s) for s in rows["season"].unique().to_list())) if rows.height else ()
    return RankCurve(positions=curves, seasons=seasons)


def apply_rank_curve(curve: RankCurve, frame: pl.DataFrame) -> pl.DataFrame:
    """Add ``pred_q10, pred_q50, pred_q90, pred_mean`` from ``rank_pred`` per position."""
    parts = []
    for (pos,), part in frame.partition_by(["position"], as_dict=True, maintain_order=True).items():
        pc = curve.positions.get(str(pos))
        if pc is None:
            preds = {k: pl.lit(None, dtype=pl.Float64) for k in ("pred_q10", "pred_q50", "pred_q90", "pred_mean")}
            parts.append(part.with_columns([v.alias(k) for k, v in preds.items()]))
            continue
        out = pc.predict(part["rank_pred"].cast(pl.Float64).to_numpy())
        parts.append(part.with_columns([pl.Series(k, v, dtype=pl.Float64) for k, v in out.items()]))
    if not parts:
        return frame.with_columns(
            [pl.lit(None, dtype=pl.Float64).alias(k) for k in ("pred_q10", "pred_q50", "pred_q90", "pred_mean")]
        )
    return pl.concat(parts, how="vertical_relaxed")


# --------------------------------------------------------------------------- #
# Prediction frame
# --------------------------------------------------------------------------- #


@dataclass
class ConsensusResult:
    """Consensus prediction frame plus aggregate diagnostics (no ranking rows)."""

    predictions: pl.DataFrame
    diagnostics: pl.DataFrame
    curve_seasons: dict[int, tuple[int, ...]]


def build_consensus(
    ecr: pl.DataFrame,
    actuals: pl.DataFrame,
    seasons: Iterable[int],
    bandwidth: float = 0.15,
    min_count: int = 30,
) -> ConsensusResult:
    """Pure builder: consensus predictions for ``seasons`` from in-memory frames.

    ``ecr`` must contain the evaluated seasons and any prior seasons that
    should feed the curve; ``actuals`` is :func:`actual_points` output for the
    same seasons. The curve for season ``s`` sees only rows with season < s.
    """
    seasons = sorted({int(s) for s in seasons})
    diag = Diagnostics()
    joined = select_consensus(ecr.filter(pl.col("season") <= max(seasons)), actuals, diag)
    outputs = []
    curve_seasons: dict[int, tuple[int, ...]] = {}
    for season in seasons:
        prior = joined.filter(pl.col("season") < season)
        curve = fit_rank_curve(prior, bandwidth, min_count)
        curve_seasons[season] = curve.seasons
        current = joined.filter(pl.col("season") == season)
        if current.is_empty():
            continue
        pred = apply_rank_curve(curve, current)
        diag.set(season, "curve_train_rows", sum(pc.n_train for pc in curve.positions.values()))
        diag.set(season, "rows_without_curve", pred["pred_q50"].null_count())
        outputs.append(pred)
    if outputs:
        frame = pl.concat(outputs, how="vertical_relaxed").with_columns(pl.lit(MODEL_NAME).alias("model"))
        frame = frame.select(PREDICTION_COLUMNS).sort(["season", "week", "position", "rank_pred"])
    else:
        frame = pl.DataFrame(schema=PREDICTION_SCHEMA)
    for season in seasons:
        diag.set(season, "rows_out", frame.filter(pl.col("season") == season).height)
    return ConsensusResult(frame, diag.to_frame(), curve_seasons)


def _restricted_store() -> SnapshotStore:
    return SnapshotStore(get_settings().processed_dir / "restricted")


def _ecr_seasons_through(store: SnapshotStore, last: int) -> list[int]:
    keys = store.partition_keys(RESTRICTED_DATASET)
    return sorted(int(k) for k in keys if isinstance(k, int) and k <= last)


def _read_actuals(seasons: Sequence[int], purpose: str) -> pl.DataFrame:
    """Reference actual points for ``seasons``; calls the holdout guard before reading."""
    assert_seasons_allowed(seasons, purpose)
    store = SnapshotStore(get_settings().processed_dir)
    pg = store.read("fact_player_game", seasons=list(seasons))
    try:
        dst = store.read("fact_dst_game", seasons=list(seasons))
    except FileNotFoundError:
        dst = None
    return actual_points(pg, dst)


#: why a player-week has or lacks a valid consensus row (D-081); see :func:`exclusion_reasons`
REASON_VALID = "valid"
REASON_EARLY = "early_game"
REASON_NO_MATCH = "no_ecr_match"
REASON_NO_SCRAPE = "no_scrape_week"
EXCLUSION_REASONS: tuple[str, ...] = (REASON_VALID, REASON_EARLY, REASON_NO_MATCH, REASON_NO_SCRAPE)


def exclusion_reasons(ecr: pl.DataFrame, keys: pl.DataFrame) -> pl.DataFrame:
    """Classify each key (``entity_id, position, season, week``) by its consensus status (D-081).

    ``reason`` is, checked in this order:

    * ``valid``: the entity is ranked on its own position's page that week in
      at least one scrape that is ``valid_for_game`` (scraped before the date
      of its own game);
    * ``early_game``: it is ranked on that page that week, but no scrape is
      valid for its game, because the game was played on or before the
      scrape date (Thursday and other early games);
    * ``no_ecr_match``: the page was scraped that week, but the entity is not
      on it (not ranked, no id in the archive, or listed only on another
      position's page);
    * ``no_scrape_week``: the position's page has no scrape for that season-week.

    A ``valid`` key can still lack a consensus projection (no prior-season
    curve, or dropped as a duplicate), which the caller counts separately.
    Pure function on in-memory frames; the result stays in memory (D-007).
    """
    ranked = page_ranks(ecr)
    is_dst = pl.col("page_position") == "DST"
    entities = ranked.select(
        pl.when(is_dst).then(pl.col("team")).otherwise(pl.col("player_id")).cast(pl.String).alias("entity_id"),
        pl.col("page_position").alias("position"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("valid_for_game").fill_null(False).alias("_valid"),
    ).filter(pl.col("entity_id").is_not_null())
    on = ["entity_id", "position", "season", "week"]
    per_key = entities.group_by(on).agg(pl.col("_valid").any().alias("_any_valid"))
    weeks = (
        ranked.select(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32),
                      pl.col("page_position").alias("position"))
        .unique()
        .with_columns(pl.lit(True).alias("_scraped"))
    )
    base = keys.select(
        pl.col("entity_id").cast(pl.String), pl.col("position").cast(pl.String),
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32),
    ).unique()
    out = base.join(per_key, on=on, how="left").join(weeks, on=["season", "week", "position"], how="left")
    reason = (
        pl.when(pl.col("_any_valid").eq(True))
        .then(pl.lit(REASON_VALID))
        .when(pl.col("_any_valid").eq(False))
        .then(pl.lit(REASON_EARLY))
        .when(pl.col("_scraped").fill_null(False))
        .then(pl.lit(REASON_NO_MATCH))
        .otherwise(pl.lit(REASON_NO_SCRAPE))
    )
    return out.with_columns(reason.alias("reason")).select(*on, "reason")


def consensus_exclusions(keys: pl.DataFrame, seasons: Sequence[int], purpose: str = "backtest") -> pl.DataFrame:
    """:func:`exclusion_reasons` for ``keys`` from the restricted archive (guard first; held in memory)."""
    seasons = sorted({int(s) for s in seasons})
    assert_seasons_allowed(seasons, purpose)
    ecr = _restricted_store().read(RESTRICTED_DATASET, seasons=seasons)
    return exclusion_reasons(ecr, keys)


def reference_actuals(seasons: Sequence[int], purpose: str = "backtest") -> pl.DataFrame:
    """Evaluator reference PPR points for ``seasons`` (D-052 cross-check against ``ffml/scoring``).

    One row per key (``entity_id, season, week``; a played row wins over a
    roster row) with :data:`ACTUAL_COLUMNS` plus ``kicking_points``: the
    Sleeper-default kicking points of the row's own kicking stats for every
    position (the reference ``actual`` adds them for kickers only), so a
    cross-check can classify emergency-kicker differences. The holdout guard
    is called before reading.
    """
    seasons = sorted({int(s) for s in seasons})
    assert_seasons_allowed(seasons, purpose)
    store = SnapshotStore(get_settings().processed_dir)
    pg = store.read("fact_player_game", seasons=list(seasons))
    try:
        dst = store.read("fact_dst_game", seasons=list(seasons))
    except FileNotFoundError:
        dst = None
    actuals = actual_points(pg, dst)
    reg = pg.filter(pl.col("game_type") == "REG") if "game_type" in pg.columns else pg
    kicking = reg.select(
        pl.col("player_id").cast(pl.String).alias("entity_id"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("played").cast(pl.Boolean).fill_null(False).alias("_played"),
        kicking_points_expr().alias("kicking_points"),
    )
    keys = ["entity_id", "season", "week"]
    kicking = kicking.sort("_played", descending=True).unique(keys, keep="first").drop("_played")
    out = actuals.sort("active", descending=True).unique(keys, keep="first", maintain_order=True)
    return (
        out.join(kicking, on=keys, how="left")
        .with_columns(pl.col("kicking_points").fill_null(0.0))
        .sort([*keys])
    )


def consensus_with_diagnostics(
    seasons: Iterable[int], scoring: str = "ppr", purpose: str = "backtest"
) -> ConsensusResult:
    """Read the restricted archive and build :class:`ConsensusResult` for ``seasons``."""
    if scoring not in SUPPORTED_SCORING:
        raise ValueError(f"consensus pages exist only for scoring in {SUPPORTED_SCORING}, got {scoring!r}")
    seasons = sorted({int(s) for s in seasons})
    if not seasons:
        raise ValueError("seasons is empty")
    store = _restricted_store()
    # prior seasons feed the curve, so they pass the guard too (inside _read_actuals,
    # one guard call per build so an unlocked run writes one log line)
    read_seasons = sorted(set(_ecr_seasons_through(store, max(seasons))) | set(seasons))
    actuals = _read_actuals(read_seasons, purpose)
    ecr = store.read(RESTRICTED_DATASET, seasons=read_seasons)
    result = build_consensus(ecr, actuals, seasons)
    log.info("consensus baseline built for %s: %d rows", seasons, result.predictions.height)
    return result


def consensus_predictions(
    seasons: Iterable[int], scoring: str = "ppr", purpose: str = "backtest"
) -> pl.DataFrame:
    """Consensus baseline prediction frame (``model = "consensus_ecr"``) for ``seasons``.

    In the :mod:`ffml.eval.metrics` contract for QB, RB, WR, TE, K and DST,
    plus ``team``, ``game_id`` and ``rank_pred``. Held in memory; never
    write it into the repository outside ``data/`` (D-007).
    """
    return consensus_with_diagnostics(seasons, scoring, purpose).predictions


# --------------------------------------------------------------------------- #
# Coverage (aggregates only)
# --------------------------------------------------------------------------- #


def coverage_from_frames(
    ecr: pl.DataFrame, actuals: pl.DataFrame, positions: Sequence[str] = COVERAGE_POSITIONS
) -> pl.DataFrame:
    """Share of played rows per season and position that have a valid consensus rank.

    Returns one row per season and position plus ``position = "ALL"`` (the
    pooled ``positions``) with ``played_rows, covered_rows, coverage,
    weeks_with_scrape, played_rows_in_scrape_weeks, coverage_in_scrape_weeks``.
    """
    selected = select_consensus(ecr, actuals)
    played = actuals.filter(pl.col("active") & pl.col("position").is_in(list(positions)))
    covered_keys = selected.filter(pl.col("active")).select("entity_id", "season", "week", "position")
    flagged = played.join(
        covered_keys.with_columns(pl.lit(True).alias("covered")),
        on=["entity_id", "season", "week", "position"],
        how="left",
    ).with_columns(pl.col("covered").fill_null(False))
    scrape_weeks = page_ranks(ecr).select(
        "season", "week", pl.col("page_position").alias("position")
    ).unique()
    flagged = flagged.join(
        scrape_weeks.with_columns(pl.lit(True).alias("scrape_week")),
        on=["season", "week", "position"],
        how="left",
    ).with_columns(pl.col("scrape_week").fill_null(False))

    def summarise(frame: pl.DataFrame, by: list[str]) -> pl.DataFrame:
        return frame.group_by(by).agg(
            pl.len().alias("played_rows"),
            pl.col("covered").sum().alias("covered_rows"),
            pl.col("week").filter(pl.col("scrape_week")).n_unique().alias("weeks_with_scrape"),
            pl.col("scrape_week").sum().alias("played_rows_in_scrape_weeks"),
            (pl.col("covered") & pl.col("scrape_week")).sum().alias("_covered_in"),
        )

    per_pos = summarise(flagged, ["season", "position"])
    pooled = summarise(flagged, ["season"]).with_columns(pl.lit("ALL").alias("position"))
    out = pl.concat([per_pos, pooled.select(per_pos.columns)], how="vertical_relaxed")
    return out.with_columns(
        (pl.col("covered_rows") / pl.col("played_rows")).alias("coverage"),
        (pl.col("_covered_in") / pl.col("played_rows_in_scrape_weeks")).alias("coverage_in_scrape_weeks"),
    ).drop("_covered_in").sort(["season", "position"])


def consensus_coverage(
    seasons: Iterable[int], purpose: str = "backtest", positions: Sequence[str] = COVERAGE_POSITIONS
) -> pl.DataFrame:
    """Coverage aggregates per season (see :func:`coverage_from_frames`)."""
    seasons = sorted({int(s) for s in seasons})
    actuals = _read_actuals(seasons, purpose)  # guard first
    ecr = _restricted_store().read(RESTRICTED_DATASET, seasons=seasons)
    return coverage_from_frames(ecr, actuals, positions)


# --------------------------------------------------------------------------- #
# Output guard
# --------------------------------------------------------------------------- #


def check_output_path(path: Path | str, root: Path = PROJECT_ROOT) -> Path:
    """Return the resolved path, or raise ``ValueError`` when it is inside ``root`` but outside ``root/data``."""
    resolved = Path(path).resolve()
    root = Path(root).resolve()
    data_dir = root / "data"
    if resolved.is_relative_to(root) and not resolved.is_relative_to(data_dir):
        raise ValueError(
            f"refusing to write restricted consensus data to {resolved}: inside the repository "
            f"only {data_dir} is allowed (DECISIONS D-007)"
        )
    return resolved


def write_restricted_frame(frame: pl.DataFrame, path: Path | str, root: Path = PROJECT_ROOT) -> Path:
    """Write a restricted frame as Parquet after :func:`check_output_path`."""
    target = check_output_path(path, root)
    target.parent.mkdir(parents=True, exist_ok=True)
    frame.write_parquet(target)
    return target


__all__ = [
    "COVERAGE_POSITIONS",
    "EXCLUSION_REASONS",
    "REASON_EARLY",
    "REASON_NO_MATCH",
    "REASON_NO_SCRAPE",
    "REASON_VALID",
    "consensus_exclusions",
    "exclusion_reasons",
    "MODEL_NAME",
    "PAGE_POSITION",
    "ConsensusResult",
    "Diagnostics",
    "PositionCurve",
    "RankCurve",
    "actual_points",
    "apply_rank_curve",
    "build_consensus",
    "check_output_path",
    "consensus_coverage",
    "consensus_predictions",
    "consensus_with_diagnostics",
    "coverage_from_frames",
    "dst_points_expr",
    "fit_position_curve",
    "fit_rank_curve",
    "join_actuals",
    "kicking_points_expr",
    "latest_valid",
    "page_ranks",
    "ppr_points_expr",
    "reference_actuals",
    "select_consensus",
    "write_restricted_frame",
]
