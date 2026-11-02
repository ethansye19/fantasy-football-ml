"""Evaluation metrics for weekly projection frames (docs/MODELING.md section 7).

Owned by the Independent Evaluator. This module never imports model, feature,
simulation or scoring code: every producer (models, baselines, the consensus
loader in :mod:`ffml.eval.ecr_baseline`) hands over a plain prediction frame.

Prediction frame contract
=========================

A Polars ``DataFrame`` with one row per ``(model, entity_id, season, week)``.

Required columns

================  =======  ==========================================================
column            type     meaning
================  =======  ==========================================================
``entity_id``     str      ``player_id`` (gsis) for QB/RB/WR/TE/K, team code for DST
``position``      str      QB, RB, WR, TE, K, DST
``season``        int
``week``          int      regular-season week
``pred_mean``     float    projected mean points under the evaluated scoring
``pred_q10``      float    projected 10th percentile (floor)
``pred_q50``      float    projected median; the point projection used for MAE,
                           ranks, start/sit and lineups
``pred_q90``      float    projected 90th percentile (ceiling)
``actual``        float    realised points under the same scoring; null when the
                           entity has no game row that week
``active``        bool     the entity played that week
``model``         str      model label
================  =======  ==========================================================

Optional columns

``boom_prob``, ``bust_prob`` (probabilities), ``boom_actual``, ``bust_actual``
(0/1 outcomes, D-017), ``pred_cdf_at_actual`` (predictive CDF at the realised
value, the PIT value), ``team``, ``game_id``, ``rank_pred`` (positional rank
by the model, 1 = best).

Ordering rule
=============

Positional ranks, the replacement-level filter, the startable sets, Spearman
scores, start/sit preferences and lineup choices all use one ordering within
a position-season-week: ``pred_q50`` descending, ties broken by ``rank_pred``
ascending when that column is present, then by ``entity_id``. Two rows are a
true tie (no preference) only when both ``pred_q50`` and ``rank_pred`` are
equal. The ``rank_pred`` tie-break matters for rank-derived projections such
as the consensus baseline, whose rank-to-points curve is flat in places.

Evaluation population
=====================

Unless stated otherwise a metric uses rows with ``active`` true, non-null
``actual`` and non-null ``pred_q50`` (MODELING.md section 7: "players who were
active that week with a projection"). ``evaluate(..., include_inactive=True)``
produces the secondary table that scores inactive players as zero to measure
the availability layer.

Ranks for the replacement-level filter and the startable sets are computed
among all rows with a projection in a position-season-week (inactive players
included, because the projection list is what a user sees before kickoff);
the population filter is applied after ranking. Lineup pools are every row
with a projection; a chosen entity that did not play scores zero.

Bootstrap
=========

Confidence intervals are percentile intervals from a cluster bootstrap over
season-weeks (``bootstrap_ci``). Resampled clusters are tagged with a
replicate id so that a week drawn twice forms two groups in the group-wise
metrics (Spearman, start/sit, lineups) instead of merging into one. Model
comparisons (``compare``) use paired draws: every model sees the same
resampled weeks, so the interval of a difference reflects only the
difference.
"""

from __future__ import annotations

import math
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass, field

import numpy as np
import polars as pl
from scipy.optimize import linear_sum_assignment
from scipy.special import expit, logit

REQUIRED_COLUMNS: tuple[str, ...] = (
    "entity_id",
    "position",
    "season",
    "week",
    "pred_mean",
    "pred_q10",
    "pred_q50",
    "pred_q90",
    "actual",
    "active",
    "model",
)
OPTIONAL_COLUMNS: tuple[str, ...] = (
    "boom_prob",
    "bust_prob",
    "boom_actual",
    "bust_actual",
    "pred_cdf_at_actual",
    "team",
    "game_id",
    "rank_pred",
)
KEY_COLUMNS: tuple[str, ...] = ("entity_id", "position", "season", "week")
_CASTS: dict[str, pl.DataType] = {
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
}
_OPTIONAL_CASTS: dict[str, pl.DataType] = {
    "boom_prob": pl.Float64(),
    "bust_prob": pl.Float64(),
    "boom_actual": pl.Float64(),
    "bust_actual": pl.Float64(),
    "pred_cdf_at_actual": pl.Float64(),
    "team": pl.String(),
    "game_id": pl.String(),
    "rank_pred": pl.Float64(),
}

#: weekly positional replacement level (rank by the ordering rule) for Spearman
REPLACEMENT_LEVEL: dict[str, int] = {"QB": 12, "RB": 24, "WR": 36, "TE": 12, "K": 12, "DST": 12}
#: startable set per position for start/sit pairs (MODELING.md section 7); K and DST excluded
STARTABLE: dict[str, int] = {"QB": 12, "RB": 24, "WR": 36, "TE": 12}
#: roster slot -> eligible positions (Sleeper slot names; DEF and DST are synonyms)
SLOT_ELIGIBILITY: dict[str, tuple[str, ...]] = {
    "QB": ("QB",),
    "RB": ("RB",),
    "WR": ("WR",),
    "TE": ("TE",),
    "K": ("K",),
    "DEF": ("DST",),
    "DST": ("DST",),
    "FLEX": ("RB", "WR", "TE"),
    "WRRB_FLEX": ("RB", "WR"),
    "REC_FLEX": ("WR", "TE"),
    "SUPER_FLEX": ("QB", "RB", "WR", "TE"),
}
#: slots that never start (ignored by the lineup solver)
NON_STARTING_SLOTS: frozenset[str] = frozenset({"BN", "IR", "TAXI"})
#: default roster for lineup efficiency
DEFAULT_SLOTS: tuple[str, ...] = ("QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "FLEX", "K", "DEF")
#: projection-gap bin edges (points) for start/sit accuracy by gap; bins are [lo, hi)
DEFAULT_GAP_EDGES: tuple[float, ...] = (0.0, 1.0, 2.0, 4.0, 6.0, 10.0, math.inf)
#: nominal coverage of the central [q10, q90] interval
NOMINAL_COVERAGE = 0.8

QUANTILE_COLUMNS: dict[float, str] = {0.1: "pred_q10", 0.5: "pred_q50", 0.9: "pred_q90"}
GROUP: tuple[str, ...] = ("position", "season", "week")
CLUSTER: tuple[str, ...] = ("season", "week")

#: direction of each named metric: True higher is better, False lower is better,
#: None no direction (coverage has a target, width trades off against coverage)
HIGHER_IS_BETTER: dict[str, bool | None] = {
    "mae": False,
    "pinball_q10": False,
    "pinball_q50": False,
    "pinball_q90": False,
    "coverage_80": None,
    "coverage_gap": False,
    "interval_width": None,
    "spearman": True,
    "start_sit": True,
    "start_sit_weighted": True,
    "brier_boom": False,
    "brier_bust": False,
    "calib_slope_boom": None,
    "calib_slope_bust": None,
    "pit_max_dev": False,
    "lineup_efficiency": True,
}

_REP = "_rep"  # bootstrap replicate tag (see module docstring)
_SCORE = "_score"  # ordering score within a group: higher is better, equal only for true ties
_BIG = 1e12
_TIE_EPS = 1e-9

MetricFn = Callable[[pl.DataFrame], float]
BundleFn = Callable[[pl.DataFrame], dict[str, float]]


# --------------------------------------------------------------------------- #
# Frame helpers
# --------------------------------------------------------------------------- #


def validate_frame(frame: pl.DataFrame) -> pl.DataFrame:
    """Check the contract and cast columns to the contract types.

    Raises ``ValueError`` on missing columns, null keys or duplicate
    ``(model, entity_id, season, week)`` rows. Float NaN becomes null.
    """
    missing = [c for c in REQUIRED_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"prediction frame is missing columns {missing}")
    casts = [pl.col(c).cast(t) for c, t in _CASTS.items()]
    casts += [pl.col(c).cast(t) for c, t in _OPTIONAL_CASTS.items() if c in frame.columns]
    out = frame.with_columns(casts)
    floats = [c for c, t in out.schema.items() if t == pl.Float64]
    if floats:
        out = out.with_columns([pl.col(c).fill_nan(None) for c in floats])
    null_keys = [c for c in (*KEY_COLUMNS, "model") if out[c].null_count() > 0]
    if null_keys:
        raise ValueError(f"prediction frame has null values in key columns {null_keys}")
    if out.select(["model", "entity_id", "season", "week"]).is_duplicated().any():
        raise ValueError("prediction frame has duplicate (model, entity_id, season, week) rows")
    return out


def population(frame: pl.DataFrame, include_inactive: bool = False) -> pl.DataFrame:
    """Rows that enter the metrics (see module docstring)."""
    if include_inactive:
        frame = frame.with_columns(
            pl.col("actual").fill_null(0.0).alias("actual"), pl.lit(True).alias("active")
        )
    return frame.filter(
        pl.col("active").fill_null(False)
        & pl.col("actual").is_not_null()
        & pl.col("pred_q50").is_not_null()
    )


def _group_cols(frame: pl.DataFrame, group: Sequence[str]) -> list[str]:
    cols = list(group)
    if _REP in frame.columns and _REP not in cols:
        cols.append(_REP)
    return cols


def _mean_or_nan(values: np.ndarray) -> float:
    return float(np.mean(values)) if values.size else math.nan


def _level_expr(levels: Mapping[str, int]) -> pl.Expr:
    return pl.col("position").replace_strict(dict(levels), default=None, return_dtype=pl.Int32)


def rank_within(
    frame: pl.DataFrame, group: Sequence[str] = GROUP, name: str = "pred_rank"
) -> pl.DataFrame:
    """Add the ordinal rank ``name`` (1 = best) and the tie-aware ``_score`` within ``group``.

    Uses the ordering rule of the module docstring. Rows with a null
    ``pred_q50`` get null rank and score. The frame is returned sorted by
    ``group`` and rank.
    """
    cols = _group_cols(frame, group)
    has_rank = "rank_pred" in frame.columns
    sort_by = [*cols, "pred_q50", *(["rank_pred"] if has_rank else []), "entity_id"]
    descending = [False] * len(cols) + [True] + ([False] if has_rank else []) + [False]
    ordered = frame.sort(sort_by, descending=descending, nulls_last=True)
    changed = (pl.col("pred_q50") != pl.col("pred_q50").shift(1).over(cols)).fill_null(True)
    if has_rank:
        changed = changed | pl.col("rank_pred").ne_missing(pl.col("rank_pred").shift(1).over(cols))
    has_pred = pl.col("pred_q50").is_not_null()
    with_change = ordered.with_columns(changed.cast(pl.Int32).alias("_chg"))
    return with_change.with_columns(
        pl.when(has_pred)
        .then(pl.int_range(1, pl.len() + 1).over(cols))
        .otherwise(None)
        .cast(pl.Int32)
        .alias(name),
        pl.when(has_pred)
        .then(-(pl.col("_chg").cum_sum().over(cols)))
        .otherwise(None)
        .cast(pl.Float64)
        .alias(_SCORE),
    ).drop("_chg")


def replacement_filter(
    frame: pl.DataFrame,
    levels: Mapping[str, int] = REPLACEMENT_LEVEL,
    group: Sequence[str] = GROUP,
) -> pl.DataFrame:
    """Keep rows ranked at or above the positional replacement level.

    Ranks follow the ordering rule; positions absent from ``levels`` are kept
    whole. The result carries ``pred_rank`` and ``_score``.
    """
    ranked = rank_within(frame, group)
    level = _level_expr(levels)
    return ranked.filter(level.is_null() | (pl.col("pred_rank") <= level))


def _sorted_groups(frame: pl.DataFrame, cols: Sequence[str]) -> list[tuple[int, int]]:
    """``(start, stop)`` row bounds of each group in a frame sorted by ``cols``."""
    if frame.is_empty():
        return []
    sizes = frame.group_by(list(cols), maintain_order=True).len()["len"].to_numpy()
    stops = np.cumsum(sizes)
    starts = stops - sizes
    return list(zip(starts.tolist(), stops.tolist(), strict=True))


# --------------------------------------------------------------------------- #
# Point and interval metrics
# --------------------------------------------------------------------------- #


def mae(frame: pl.DataFrame) -> float:
    """Mean absolute error of the median projection (``pred_q50``) vs ``actual``."""
    f = population(frame)
    return _mean_or_nan((f["pred_q50"] - f["actual"]).abs().to_numpy())


def pinball_loss(frame: pl.DataFrame, q: float = 0.5) -> float:
    """Pinball (quantile) loss at ``q`` in {0.1, 0.5, 0.9} using the matching column.

    ``loss = max(q * (y - p), (q - 1) * (y - p))``; at ``q = 0.5`` this is half the MAE.
    """
    try:
        col = QUANTILE_COLUMNS[q]
    except KeyError as exc:
        raise ValueError(f"q must be one of {sorted(QUANTILE_COLUMNS)}, got {q}") from exc
    f = population(frame).filter(pl.col(col).is_not_null())
    diff = (f["actual"] - f[col]).to_numpy()
    return _mean_or_nan(np.maximum(q * diff, (q - 1.0) * diff))


def pinball(q: float) -> MetricFn:
    """Metric function for :func:`pinball_loss` at a fixed ``q`` (for bootstrapping)."""

    def fn(frame: pl.DataFrame) -> float:
        return pinball_loss(frame, q)

    fn.__name__ = f"pinball_q{int(round(q * 100)):02d}"
    return fn


def _interval_rows(frame: pl.DataFrame) -> pl.DataFrame:
    return population(frame).filter(pl.col("pred_q10").is_not_null() & pl.col("pred_q90").is_not_null())


def interval_coverage(frame: pl.DataFrame) -> float:
    """Share of actuals inside the closed interval ``[pred_q10, pred_q90]``."""
    f = _interval_rows(frame)
    inside = (f["actual"] >= f["pred_q10"]) & (f["actual"] <= f["pred_q90"])
    return _mean_or_nan(inside.cast(pl.Float64).to_numpy())


def interval_width(frame: pl.DataFrame) -> float:
    """Mean width ``pred_q90 - pred_q10`` over the population."""
    f = _interval_rows(frame)
    return _mean_or_nan((f["pred_q90"] - f["pred_q10"]).to_numpy())


# --------------------------------------------------------------------------- #
# Probability calibration
# --------------------------------------------------------------------------- #


def _prob_outcome(frame: pl.DataFrame, prob_col: str, outcome_col: str) -> tuple[np.ndarray, np.ndarray]:
    for col in (prob_col, outcome_col):
        if col not in frame.columns:
            raise ValueError(f"column {col!r} not in frame")
    f = population(frame).filter(pl.col(prob_col).is_not_null() & pl.col(outcome_col).is_not_null())
    p = f[prob_col].cast(pl.Float64).to_numpy()
    y = f[outcome_col].cast(pl.Float64).to_numpy()
    return p, y


def reliability_table(
    frame: pl.DataFrame, prob_col: str, outcome_col: str, bins: int = 10
) -> pl.DataFrame:
    """Reliability (calibration) table: equal-width probability bins vs outcome rate.

    Columns ``bin, lo, hi, n, mean_prob, outcome_rate, gap`` with
    ``gap = outcome_rate - mean_prob``. Bins are ``[lo, hi)`` except the last,
    which includes 1.0. Empty bins are kept with ``n = 0``.
    """
    p, y = _prob_outcome(frame, prob_col, outcome_col)
    idx = np.minimum((np.clip(p, 0.0, 1.0) * bins).astype(int), bins - 1)
    rows = []
    for b in range(bins):
        m = idx == b
        n = int(m.sum())
        mean_p = float(p[m].mean()) if n else math.nan
        rate = float(y[m].mean()) if n else math.nan
        rows.append(
            {
                "bin": b,
                "lo": b / bins,
                "hi": (b + 1) / bins,
                "n": n,
                "mean_prob": mean_p,
                "outcome_rate": rate,
                "gap": rate - mean_p if n else math.nan,
            }
        )
    return pl.DataFrame(rows)


def brier(frame: pl.DataFrame, prob_col: str, outcome_col: str) -> float:
    """Brier score: mean squared difference between probability and 0/1 outcome."""
    p, y = _prob_outcome(frame, prob_col, outcome_col)
    return _mean_or_nan((p - y) ** 2)


def calibration_slope(frame: pl.DataFrame, prob_col: str, outcome_col: str) -> float:
    """Logistic calibration slope (Cox): fit ``logit P(y=1) = a + b * logit(p)``, return ``b``.

    1.0 means calibrated spread, below 1 over-confident, above 1 under-confident.
    NaN when fewer than 10 rows, a single outcome class, constant
    probabilities, or a non-converging fit (separation).
    """
    p, y = _prob_outcome(frame, prob_col, outcome_col)
    if p.size < 10 or np.unique(y).size < 2:
        return math.nan
    x = logit(np.clip(p, 1e-6, 1 - 1e-6))
    if float(np.ptp(x)) == 0.0:
        return math.nan
    design = np.column_stack([np.ones_like(x), x])
    beta = np.zeros(2)
    for _ in range(100):
        mu = expit(design @ beta)
        weight = mu * (1.0 - mu)
        hessian = design.T @ (design * weight[:, None])
        try:
            step = np.linalg.solve(hessian, design.T @ (y - mu))
        except np.linalg.LinAlgError:
            return math.nan
        beta = beta + step
        if not np.all(np.isfinite(beta)) or np.max(np.abs(beta)) > 1e3:
            return math.nan
        if np.max(np.abs(step)) < 1e-10:
            return float(beta[1])
    return math.nan


def pit_histogram(
    frame: pl.DataFrame, col: str = "pred_cdf_at_actual", bins: int = 10
) -> pl.DataFrame:
    """Histogram of probability integral transform values (uniform when calibrated).

    Columns ``bin, lo, hi, n, share, expected`` with ``expected = 1 / bins``.
    """
    if col not in frame.columns:
        raise ValueError(f"column {col!r} not in frame")
    f = population(frame).filter(pl.col(col).is_not_null())
    u = np.clip(f[col].cast(pl.Float64).to_numpy(), 0.0, 1.0)
    idx = np.minimum((u * bins).astype(int), bins - 1)
    counts = np.bincount(idx, minlength=bins)
    total = int(counts.sum())
    return pl.DataFrame(
        {
            "bin": list(range(bins)),
            "lo": [b / bins for b in range(bins)],
            "hi": [(b + 1) / bins for b in range(bins)],
            "n": counts.tolist(),
            "share": (counts / total).tolist() if total else [math.nan] * bins,
            "expected": [1.0 / bins] * bins,
        }
    )


def pit_max_deviation(frame: pl.DataFrame, col: str = "pred_cdf_at_actual", bins: int = 10) -> float:
    """Largest absolute deviation of a PIT bin share from the uniform share."""
    table = pit_histogram(frame, col, bins)
    if int(table["n"].sum()) == 0:
        return math.nan
    return float((table["share"] - table["expected"]).abs().max())


# --------------------------------------------------------------------------- #
# Rank correlation
# --------------------------------------------------------------------------- #


def spearman_by_group(
    frame: pl.DataFrame,
    group: Sequence[str] = GROUP,
    min_entities: int = 8,
    levels: Mapping[str, int] | None = REPLACEMENT_LEVEL,
) -> pl.DataFrame:
    """Spearman correlation of the projection ordering vs ``actual`` per group.

    Rows are restricted to the positional replacement level (``levels``; pass
    ``None`` to keep every projected row), then to the evaluation population.
    Ranks are averaged on ties (the scipy definition). Groups with fewer than
    ``min_entities`` rows or a constant column are dropped. Columns:
    ``<group...>, n, rho``.
    """
    cols = _group_cols(frame, group)
    if levels is not None:
        base = replacement_filter(frame, levels, group)
    else:
        base = rank_within(frame, group)
    pop = population(base)
    schema = {**{c: pop.schema[c] for c in cols}, "n": pl.UInt32, "rho": pl.Float64}
    if pop.is_empty():
        return pl.DataFrame(schema=schema)
    table = (
        pop.with_columns(
            pl.col(_SCORE).rank("average").over(cols).alias("_rs"),
            pl.col("actual").rank("average").over(cols).alias("_ra"),
        )
        .group_by(cols, maintain_order=True)
        .agg(
            pl.len().alias("n"),
            pl.corr("_rs", "_ra").alias("rho"),
            pl.col("_rs").n_unique().alias("_us"),
            pl.col("_ra").n_unique().alias("_ua"),
        )
        .filter(
            (pl.col("n") >= min_entities)
            & (pl.col("_us") > 1)
            & (pl.col("_ua") > 1)
            & pl.col("rho").is_not_null()
            & pl.col("rho").is_not_nan()
        )
        .select([*cols, pl.col("n").cast(pl.UInt32), "rho"])
    )
    return table


def spearman_within(
    frame: pl.DataFrame,
    group: Sequence[str] = GROUP,
    min_entities: int = 8,
    levels: Mapping[str, int] | None = REPLACEMENT_LEVEL,
) -> float:
    """Mean Spearman correlation over groups with at least ``min_entities`` rows."""
    table = spearman_by_group(frame, group, min_entities, levels)
    return _mean_or_nan(table["rho"].to_numpy())


# --------------------------------------------------------------------------- #
# Start/sit accuracy
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class StartSitResult:
    """Pairwise start/sit accuracy among startable same-position players."""

    accuracy: float
    weighted_accuracy: float
    n_pairs: int
    by_gap: pl.DataFrame

    def as_dict(self) -> dict[str, float | int]:
        return {
            "start_sit": self.accuracy,
            "start_sit_weighted": self.weighted_accuracy,
            "n_pairs": self.n_pairs,
        }


def startable_population(
    frame: pl.DataFrame, startable: Mapping[str, int] = STARTABLE, group: Sequence[str] = GROUP
) -> pl.DataFrame:
    """Startable rows (rank at or above the position's cut) that are in the population.

    Positions absent from ``startable`` are excluded.
    """
    ranked = rank_within(frame, group)
    level = _level_expr(startable)
    return population(ranked.filter(level.is_not_null() & (pl.col("pred_rank") <= level)))


def start_sit_pairs(
    frame: pl.DataFrame,
    startable: Mapping[str, int] = STARTABLE,
    group: Sequence[str] = GROUP,
) -> tuple[np.ndarray, np.ndarray]:
    """``(gap, concordance)`` arrays over all startable same-position pairs.

    A pair is two startable, active players of the same position in the same
    season-week that are not a true tie under the ordering rule. Concordance
    is 1 when the preferred player scored more, 0.5 on an exact tie in actual
    points, 0 otherwise. ``gap`` is ``|pred_q50(a) - pred_q50(b)|``.
    """
    cols = _group_cols(frame, group)
    pop = startable_population(frame, startable, group).sort([*cols, "pred_rank"])
    if pop.height < 2:
        return np.empty(0), np.empty(0)
    score = pop[_SCORE].to_numpy()
    pred = pop["pred_q50"].to_numpy()
    actual = pop["actual"].to_numpy()
    gaps: list[np.ndarray] = []
    conc: list[np.ndarray] = []
    for start, stop in _sorted_groups(pop, cols):
        n = stop - start
        if n < 2:
            continue
        i, j = np.triu_indices(n, 1)
        i, j = i + start, j + start
        ds = score[i] - score[j]
        keep = ds != 0
        i, j, ds = i[keep], j[keep], ds[keep]
        da = actual[i] - actual[j]
        gaps.append(np.abs(pred[i] - pred[j]))
        conc.append(np.where(da == 0, 0.5, (np.sign(ds) == np.sign(da)).astype(float)))
    if not gaps:
        return np.empty(0), np.empty(0)
    return np.concatenate(gaps), np.concatenate(conc)


def _gap_table(gap: np.ndarray, conc: np.ndarray, edges: Sequence[float]) -> pl.DataFrame:
    edges = list(edges)
    if len(edges) < 2:
        raise ValueError("gap_edges needs at least two edges")
    idx = np.digitize(gap, edges[1:-1], right=False) if gap.size else np.empty(0, dtype=int)
    rows = []
    for b in range(len(edges) - 1):
        m = idx == b
        n = int(m.sum())
        rows.append(
            {
                "gap_lo": float(edges[b]),
                "gap_hi": float(edges[b + 1]),
                "n_pairs": n,
                "accuracy": float(conc[m].mean()) if n else math.nan,
            }
        )
    return pl.DataFrame(rows)


def start_sit_accuracy(
    frame: pl.DataFrame,
    startable: Mapping[str, int] = STARTABLE,
    group: Sequence[str] = GROUP,
    gap_edges: Sequence[float] = DEFAULT_GAP_EDGES,
) -> StartSitResult:
    """Share of startable same-position pairs where the preferred player scored more.

    Also returns the version weighted by the absolute projection gap and the
    accuracy binned by projection gap (``gap_edges`` are ``[lo, hi)`` bin
    boundaries).
    """
    gap, conc = start_sit_pairs(frame, startable, group)
    by_gap = _gap_table(gap, conc, gap_edges)
    if gap.size == 0:
        return StartSitResult(math.nan, math.nan, 0, by_gap)
    total_gap = float(gap.sum())
    weighted = float((gap * conc).sum() / total_gap) if total_gap > 0 else math.nan
    return StartSitResult(float(conc.mean()), weighted, int(gap.size), by_gap)


def start_sit(frame: pl.DataFrame) -> float:
    """Metric function: unweighted start/sit accuracy with default settings."""
    return start_sit_accuracy(frame).accuracy


def start_sit_weighted(frame: pl.DataFrame) -> float:
    """Metric function: gap-weighted start/sit accuracy with default settings."""
    return start_sit_accuracy(frame).weighted_accuracy


# --------------------------------------------------------------------------- #
# Lineup efficiency
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LineupResult:
    """Per-week lineup efficiency and its mean."""

    per_week: pl.DataFrame
    mean: float


def _starting_slots(slots: Sequence[str], eligibility: Mapping[str, Sequence[str]]) -> list[str]:
    out = []
    for slot in slots:
        name = str(slot).upper()
        if name in NON_STARTING_SLOTS:
            continue
        if name not in eligibility:
            raise ValueError(f"unknown roster slot {slot!r}; known: {sorted(eligibility)}")
        out.append(name)
    return out


def best_lineup(
    positions: Sequence[str],
    values: Sequence[float],
    slots: Sequence[str] = DEFAULT_SLOTS,
    eligibility: Mapping[str, Sequence[str]] = SLOT_ELIGIBILITY,
) -> list[int]:
    """Indices of the entities that maximise total ``values`` over ``slots``.

    Solved exactly as an assignment problem, so any flex structure (including
    superflex) is handled. Every slot with an eligible entity is filled, even
    when the best value is negative; a slot with no eligible entity stays
    empty. Bench, IR and taxi slots are ignored.
    """
    starting = _starting_slots(slots, eligibility)
    pos = np.asarray(positions, dtype=object)
    val = np.asarray(values, dtype=float)
    if pos.size == 0 or not starting:
        return []
    cost = np.full((pos.size, len(starting)), _BIG)
    for s, slot in enumerate(starting):
        mask = np.isin(pos, list(eligibility[slot]))
        cost[mask, s] = -val[mask]
    rows, cols = linear_sum_assignment(cost)
    return sorted(int(r) for r, c in zip(rows, cols, strict=True) if cost[r, c] < _BIG / 2)


def lineup_efficiency(
    frame: pl.DataFrame,
    slots: Sequence[str] = DEFAULT_SLOTS,
    eligibility: Mapping[str, Sequence[str]] = SLOT_ELIGIBILITY,
    cluster: Sequence[str] = CLUSTER,
    pool: Sequence[str] = (),
) -> LineupResult:
    """Actual points of the lineup chosen by projection over the optimal lineup by actuals.

    The pool for each ``cluster`` (default season-week) plus optional ``pool``
    columns (for example a synthetic fantasy roster id) is every row with a
    projection. The chosen lineup maximises ``pred_q50`` (ties broken by the
    ordering rule); the optimal lineup maximises realised points. A chosen
    entity that did not play scores its ``actual`` with null as zero, which is
    the penalty a real roster pays. ``per_week`` holds ``chosen_points,
    optimal_points, efficiency`` per pool; ``mean`` averages the ratio over
    pools with a positive optimum.
    """
    cols = _group_cols(frame, [*cluster, *pool])
    projected = frame.filter(pl.col("pred_q50").is_not_null())
    schema = {
        **{c: projected.schema[c] for c in cols},
        "chosen_points": pl.Float64,
        "optimal_points": pl.Float64,
        "efficiency": pl.Float64,
    }
    if projected.is_empty():
        return LineupResult(pl.DataFrame(schema=schema), math.nan)
    ranked = rank_within(projected, ["position", *cols]).sort(cols)
    keys = ranked.select(cols)
    positions = ranked["position"].to_numpy()
    choice_value = (ranked["pred_q50"] - _TIE_EPS * ranked["pred_rank"].cast(pl.Float64)).to_numpy()
    actual = ranked["actual"].fill_null(0.0).to_numpy()
    rows = []
    for start, stop in _sorted_groups(ranked, cols):
        pos = positions[start:stop]
        act = actual[start:stop]
        chosen = best_lineup(pos, choice_value[start:stop], slots, eligibility)
        optimal = best_lineup(pos, act, slots, eligibility)
        chosen_pts = float(act[chosen].sum()) if chosen else 0.0
        optimal_pts = float(act[optimal].sum()) if optimal else 0.0
        rows.append(
            {
                **keys.row(start, named=True),
                "chosen_points": chosen_pts,
                "optimal_points": optimal_pts,
                "efficiency": chosen_pts / optimal_pts if optimal_pts > 0 else math.nan,
            }
        )
    per_week = pl.DataFrame(rows, schema=schema)
    effs = per_week["efficiency"].drop_nans().drop_nulls().to_numpy()
    return LineupResult(per_week, _mean_or_nan(effs))


def lineup_efficiency_mean(frame: pl.DataFrame) -> float:
    """Metric function: mean lineup efficiency with the default roster."""
    return lineup_efficiency(frame).mean


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class CI:
    """Point estimate with a percentile bootstrap interval."""

    point: float
    lo: float
    hi: float
    n_boot: int

    def as_tuple(self) -> tuple[float, float, float]:
        return (self.point, self.lo, self.hi)


def _cluster_index(frame: pl.DataFrame, cluster: Sequence[str]) -> dict[tuple, np.ndarray]:
    if frame.is_empty():
        return {}
    grouped = (
        frame.select(list(cluster))
        .with_row_index("_row")
        .group_by(list(cluster), maintain_order=True)
        .agg(pl.col("_row"))
    )
    out: dict[tuple, np.ndarray] = {}
    for row in grouped.iter_rows():
        out[tuple(row[:-1])] = np.asarray(row[-1], dtype=np.int64)
    return out


def _resample(frame: pl.DataFrame, index: Mapping[tuple, np.ndarray], keys: Sequence[tuple], draw: np.ndarray) -> pl.DataFrame | None:
    parts = [index.get(keys[int(k)]) for k in draw]
    sizes = np.array([0 if p is None else p.size for p in parts], dtype=np.int64)
    if sizes.sum() == 0:
        return None
    rows = np.concatenate([p for p in parts if p is not None and p.size])
    rep = np.repeat(np.arange(len(parts), dtype=np.int32), sizes)
    base = frame.drop(_REP) if _REP in frame.columns else frame
    return base[rows].with_columns(pl.Series(_REP, rep, dtype=pl.Int32))


def _paired_bundle_bootstrap(
    frames: Mapping[str, pl.DataFrame],
    bundle: BundleFn,
    n_boot: int,
    cluster: Sequence[str],
    seed: int,
) -> dict[str, dict[str, np.ndarray]]:
    """Bootstrap a metric bundle for several frames with shared cluster draws."""
    index = {name: _cluster_index(f, cluster) for name, f in frames.items()}
    universe: list[tuple] = []
    seen: set[tuple] = set()
    for idx in index.values():
        for key in idx:
            if key not in seen:
                seen.add(key)
                universe.append(key)
    out: dict[str, dict[str, np.ndarray]] = {name: {} for name in frames}
    if not universe or n_boot <= 0:
        return out
    rng = np.random.default_rng(seed)
    draws = rng.integers(0, len(universe), size=(n_boot, len(universe)))
    for b in range(n_boot):
        for name, f in frames.items():
            sample = _resample(f, index[name], universe, draws[b])
            values = bundle(sample) if sample is not None else {}
            for metric, value in values.items():
                arr = out[name].setdefault(metric, np.full(n_boot, math.nan))
                arr[b] = value
    return out


def paired_bootstrap(
    frames: Mapping[str, pl.DataFrame],
    metric_fn: MetricFn,
    n_boot: int = 500,
    cluster: Sequence[str] = CLUSTER,
    seed: int = 0,
) -> dict[str, np.ndarray]:
    """Cluster-bootstrap statistics for several frames using the same cluster draws.

    Clusters are drawn from the union of cluster keys across ``frames``; a
    frame without rows for a drawn cluster contributes nothing for it. Returns
    one array of length ``n_boot`` per frame (NaN where the metric is
    undefined on a resample).
    """
    boots = _paired_bundle_bootstrap(
        frames, lambda f: {"value": float(metric_fn(f))}, n_boot, cluster, seed
    )
    return {name: b.get("value", np.full(max(n_boot, 0), math.nan)) for name, b in boots.items()}


def _percentile(values: np.ndarray, alpha: float) -> tuple[float, float, int]:
    valid = values[~np.isnan(values)]
    if valid.size == 0:
        return math.nan, math.nan, 0
    lo, hi = np.quantile(valid, [alpha / 2, 1 - alpha / 2])
    return float(lo), float(hi), int(valid.size)


def bootstrap_ci(
    metric_fn: MetricFn,
    frame: pl.DataFrame,
    n_boot: int = 500,
    cluster: Sequence[str] = CLUSTER,
    seed: int = 0,
    alpha: float = 0.05,
) -> CI:
    """Percentile cluster-bootstrap interval for ``metric_fn(frame)``.

    Clusters (default season-week) are resampled with replacement ``n_boot``
    times; the interval is the ``alpha/2`` and ``1 - alpha/2`` quantiles of
    the resampled statistics (NaN resamples are dropped; ``n_boot`` in the
    result counts the valid ones).
    """
    point = float(metric_fn(frame))
    if n_boot <= 0:
        return CI(point, math.nan, math.nan, 0)
    boots = paired_bootstrap({"_": frame}, metric_fn, n_boot, cluster, seed)["_"]
    lo, hi, n = _percentile(boots, alpha)
    return CI(point, lo, hi, n)


def bootstrap_bundle(
    bundle: BundleFn,
    frame: pl.DataFrame,
    n_boot: int = 500,
    cluster: Sequence[str] = CLUSTER,
    seed: int = 0,
    alpha: float = 0.05,
) -> dict[str, CI]:
    """Like :func:`bootstrap_ci` for a function returning several named metrics.

    Each replicate is resampled once and every metric is computed on it.
    """
    point = bundle(frame)
    if n_boot <= 0:
        return {k: CI(float(v), math.nan, math.nan, 0) for k, v in point.items()}
    boots = _paired_bundle_bootstrap({"_": frame}, bundle, n_boot, cluster, seed)["_"]
    out = {}
    for name, value in point.items():
        lo, hi, n = _percentile(boots.get(name, np.full(n_boot, math.nan)), alpha)
        out[name] = CI(float(value), lo, hi, n)
    return out


# --------------------------------------------------------------------------- #
# Metric bundles
# --------------------------------------------------------------------------- #


def metric_bundle(
    columns: Sequence[str],
    levels: Mapping[str, int] = REPLACEMENT_LEVEL,
    startable: Mapping[str, int] = STARTABLE,
    nominal: float = NOMINAL_COVERAGE,
) -> BundleFn:
    """The per-position scalar metrics as one function (calibration only when columns exist)."""
    cols = set(columns)
    has_boom = {"boom_prob", "boom_actual"} <= cols
    has_bust = {"bust_prob", "bust_actual"} <= cols
    has_pit = "pred_cdf_at_actual" in cols

    def bundle(f: pl.DataFrame) -> dict[str, float]:
        coverage = interval_coverage(f)
        gap, conc = start_sit_pairs(f, startable)
        total_gap = float(gap.sum()) if gap.size else 0.0
        out = {
            "mae": mae(f),
            "pinball_q10": pinball_loss(f, 0.1),
            "pinball_q50": pinball_loss(f, 0.5),
            "pinball_q90": pinball_loss(f, 0.9),
            "coverage_80": coverage,
            "coverage_gap": abs(coverage - nominal) if not math.isnan(coverage) else math.nan,
            "interval_width": interval_width(f),
            "spearman": spearman_within(f, levels=levels),
            "start_sit": float(conc.mean()) if conc.size else math.nan,
            "start_sit_weighted": float((gap * conc).sum() / total_gap) if total_gap > 0 else math.nan,
        }
        if has_boom:
            out["brier_boom"] = brier(f, "boom_prob", "boom_actual")
            out["calib_slope_boom"] = calibration_slope(f, "boom_prob", "boom_actual")
        if has_bust:
            out["brier_bust"] = brier(f, "bust_prob", "bust_actual")
            out["calib_slope_bust"] = calibration_slope(f, "bust_prob", "bust_actual")
        if has_pit:
            out["pit_max_dev"] = pit_max_deviation(f)
        return out

    return bundle


def lineup_bundle(
    slots: Sequence[str] = DEFAULT_SLOTS,
    eligibility: Mapping[str, Sequence[str]] = SLOT_ELIGIBILITY,
    cluster: Sequence[str] = CLUSTER,
) -> BundleFn:
    """Lineup efficiency as a bundle (it spans positions, so it is evaluated separately)."""

    def bundle(f: pl.DataFrame) -> dict[str, float]:
        return {"lineup_efficiency": lineup_efficiency(f, slots, eligibility, cluster).mean}

    return bundle


def metric_functions(
    levels: Mapping[str, int] = REPLACEMENT_LEVEL, startable: Mapping[str, int] = STARTABLE
) -> dict[str, MetricFn]:
    """Named scalar metric functions (for ad-hoc :func:`bootstrap_ci` calls)."""
    return {
        "mae": mae,
        "pinball_q10": pinball(0.1),
        "pinball_q50": pinball(0.5),
        "pinball_q90": pinball(0.9),
        "coverage_80": interval_coverage,
        "interval_width": interval_width,
        "spearman": lambda f: spearman_within(f, levels=levels),
        "start_sit": lambda f: start_sit_accuracy(f, startable).accuracy,
        "start_sit_weighted": lambda f: start_sit_accuracy(f, startable).weighted_accuracy,
        "lineup_efficiency": lineup_efficiency_mean,
    }


# --------------------------------------------------------------------------- #
# Evaluate and compare
# --------------------------------------------------------------------------- #


@dataclass
class EvalResult:
    """Tidy evaluation tables.

    ``summary``: one row per model, ``by`` group and metric with ``value,
    ci_lo, ci_hi, n`` (``lineup_efficiency`` rows have ``position = "ALL"``
    because lineups span positions). ``per_week``: unbootstrapped metrics per
    model, group, season and week. ``start_sit_by_gap``, ``lineup`` (per-week
    lineup points), ``reliability`` and ``pit`` are the supporting tables.
    """

    summary: pl.DataFrame
    per_week: pl.DataFrame | None
    start_sit_by_gap: pl.DataFrame
    lineup: pl.DataFrame
    reliability: pl.DataFrame | None
    pit: pl.DataFrame | None
    by: tuple[str, ...]
    models: tuple[str, ...] = field(default_factory=tuple)
    n_boot: int = 0


def _groups(frame: pl.DataFrame, by: Sequence[str]) -> list[tuple[dict[str, object], pl.DataFrame]]:
    if not by:
        return [({}, frame)]
    out = []
    for key, part in frame.partition_by(list(by), as_dict=True, maintain_order=True).items():
        out.append((dict(zip(by, key, strict=True)), part))
    return out


def _lineup_by(by: Sequence[str]) -> tuple[str, ...]:
    return tuple(c for c in by if c != "position")


def _with_keys(frame: pl.DataFrame, key: Mapping[str, object]) -> pl.DataFrame:
    return frame.with_columns([pl.lit(v).alias(k) for k, v in key.items()])


def _summary_frame(rows: list[dict[str, object]], by: Sequence[str]) -> pl.DataFrame:
    if not rows:
        return pl.DataFrame()
    frame = pl.DataFrame(rows, infer_schema_length=None)
    lead = [c for c in ("model", *by, "metric") if c in frame.columns]
    return frame.select([*lead, *[c for c in frame.columns if c not in lead]])


def evaluate(
    frame: pl.DataFrame,
    by: Sequence[str] = ("position",),
    per_week: bool = True,
    n_boot: int = 500,
    seed: int = 0,
    cluster: Sequence[str] = CLUSTER,
    include_inactive: bool = False,
    levels: Mapping[str, int] = REPLACEMENT_LEVEL,
    startable: Mapping[str, int] = STARTABLE,
    slots: Sequence[str] = DEFAULT_SLOTS,
    eligibility: Mapping[str, Sequence[str]] = SLOT_ELIGIBILITY,
    alpha: float = 0.05,
) -> EvalResult:
    """Evaluate one or more models (``model`` column) by ``by`` groups.

    Every metric gets a point estimate and a cluster-bootstrap interval
    (``n_boot = 0`` skips intervals). Lineup efficiency is computed per model
    over the ``by`` columns other than ``position``.
    """
    frame = validate_frame(frame)
    if include_inactive:
        frame = frame.with_columns(
            pl.col("actual").fill_null(0.0).alias("actual"), pl.lit(True).alias("active")
        )
    by = tuple(by)
    bundle = metric_bundle(frame.columns, levels, startable)
    lbundle = lineup_bundle(slots, eligibility, cluster)
    summary_rows: list[dict[str, object]] = []
    week_rows: list[dict[str, object]] = []
    gap_frames: list[pl.DataFrame] = []
    lineup_frames: list[pl.DataFrame] = []
    rel_frames: list[pl.DataFrame] = []
    pit_frames: list[pl.DataFrame] = []
    models = tuple(frame["model"].unique(maintain_order=True).to_list())

    for model in models:
        mf = frame.filter(pl.col("model") == model)
        for key, part in _groups(mf, by):
            n = population(part).height
            for name, ci in bootstrap_bundle(bundle, part, n_boot, cluster, seed, alpha).items():
                summary_rows.append(
                    {"model": model, **key, "metric": name, "value": ci.point,
                     "ci_lo": ci.lo, "ci_hi": ci.hi, "n": n}
                )
            ss = start_sit_accuracy(part, startable)
            gap_frames.append(_with_keys(ss.by_gap, {"model": model, **key}))
            for kind in ("boom", "bust"):
                if {f"{kind}_prob", f"{kind}_actual"} <= set(part.columns):
                    table = reliability_table(part, f"{kind}_prob", f"{kind}_actual")
                    rel_frames.append(_with_keys(table, {"model": model, "kind": kind, **key}))
            if "pred_cdf_at_actual" in part.columns:
                pit_frames.append(_with_keys(pit_histogram(part), {"model": model, **key}))
            if per_week:
                for wkey, wpart in _groups(part, ("season", "week")):
                    wn = population(wpart).height
                    for name, value in bundle(wpart).items():
                        week_rows.append(
                            {"model": model, **key, **wkey, "metric": name, "value": value, "n": wn}
                        )
        lby = _lineup_by(by)
        for key, part in _groups(mf, lby):
            lkey = {c: ("ALL" if c == "position" else key.get(c)) for c in by}
            ci = bootstrap_bundle(lbundle, part, n_boot, cluster, seed, alpha)["lineup_efficiency"]
            summary_rows.append(
                {"model": model, **lkey, "metric": "lineup_efficiency", "value": ci.point,
                 "ci_lo": ci.lo, "ci_hi": ci.hi, "n": population(part).height}
            )
            le = lineup_efficiency(part, slots, eligibility, cluster)
            lineup_frames.append(_with_keys(le.per_week, {"model": model, **key}))

    return EvalResult(
        summary=_summary_frame(summary_rows, by),
        per_week=_summary_frame(week_rows, (*by, "season", "week")) if per_week and week_rows else None,
        start_sit_by_gap=pl.concat(gap_frames, how="diagonal_relaxed") if gap_frames else pl.DataFrame(),
        lineup=pl.concat(lineup_frames, how="diagonal_relaxed") if lineup_frames else pl.DataFrame(),
        reliability=pl.concat(rel_frames, how="diagonal_relaxed") if rel_frames else None,
        pit=pl.concat(pit_frames, how="diagonal_relaxed") if pit_frames else None,
        by=by,
        models=models,
        n_boot=max(n_boot, 0),
    )


def align_frames(models: Mapping[str, pl.DataFrame]) -> tuple[dict[str, pl.DataFrame], dict[str, int]]:
    """Restrict every frame to the ``(entity_id, position, season, week)`` keys all share.

    Returns the aligned frames and the number of rows dropped per model. Used
    by :func:`compare` so that paired differences are computed on one
    population (for example, the consensus baseline lacks Thursday players).
    """
    keys: pl.DataFrame | None = None
    for f in models.values():
        k = f.select(KEY_COLUMNS).unique()
        keys = k if keys is None else keys.join(k, on=list(KEY_COLUMNS), how="inner")
    assert keys is not None
    aligned = {name: f.join(keys, on=list(KEY_COLUMNS), how="semi") for name, f in models.items()}
    dropped = {name: models[name].height - aligned[name].height for name in models}
    return aligned, dropped


def compare(
    models: Mapping[str, pl.DataFrame],
    by: Sequence[str] = ("position",),
    n_boot: int = 500,
    seed: int = 0,
    cluster: Sequence[str] = CLUSTER,
    baseline: str | None = None,
    align: bool = True,
    levels: Mapping[str, int] = REPLACEMENT_LEVEL,
    startable: Mapping[str, int] = STARTABLE,
    slots: Sequence[str] = DEFAULT_SLOTS,
    eligibility: Mapping[str, Sequence[str]] = SLOT_ELIGIBILITY,
    alpha: float = 0.05,
) -> pl.DataFrame:
    """Compare models with paired cluster bootstraps.

    ``models`` maps a label to a prediction frame (the ``model`` column is
    overwritten with the label). With ``align`` (default) all frames are first
    restricted to their common keys (:func:`align_frames`). Returns a tidy
    frame with ``model, <by...>, metric, value, ci_lo, ci_hi, delta,
    delta_lo, delta_hi, higher_is_better, better, n`` where ``delta`` is
    ``value - value(baseline)`` with a paired interval and ``better`` is true
    when that interval excludes zero in the metric's good direction (null for
    the baseline and for metrics without a direction). ``baseline`` defaults
    to the first label. ``better`` is the acceptance test of MODELING.md
    section 8.
    """
    if not models:
        raise ValueError("models is empty")
    labels = list(models)
    base_label = baseline or labels[0]
    if base_label not in models:
        raise ValueError(f"baseline {base_label!r} not in models")
    frames = {
        name: validate_frame(f.with_columns(pl.lit(name).alias("model"))) for name, f in models.items()
    }
    if align:
        frames, _ = align_frames(frames)
    by = tuple(by)
    all_cols = set().union(*(set(f.columns) for f in frames.values()))
    bundle = metric_bundle(sorted(all_cols), levels, startable)
    lbundle = lineup_bundle(slots, eligibility, cluster)

    def group_keys(cols: Sequence[str]) -> list[dict[str, object]]:
        seen: dict[tuple, dict[str, object]] = {}
        for f in frames.values():
            for key, _ in _groups(f, cols):
                seen.setdefault(tuple(key.values()), key)
        return list(seen.values())

    def subset(f: pl.DataFrame, key: Mapping[str, object]) -> pl.DataFrame:
        for col, val in key.items():
            f = f.filter(pl.col(col) == val)
        return f

    rows: list[dict[str, object]] = []

    def add_rows(fn: BundleFn, key: Mapping[str, object], label_key: Mapping[str, object]) -> None:
        group_frames = {name: subset(f, key) for name, f in frames.items()}
        points = {name: fn(sub) for name, sub in group_frames.items()}
        boots = _paired_bundle_bootstrap(group_frames, fn, n_boot, cluster, seed) if n_boot > 0 else {}
        metrics = list(points[base_label])
        for metric in metrics:
            direction = HIGHER_IS_BETTER.get(metric)
            for name in labels:
                value = float(points[name].get(metric, math.nan))
                row: dict[str, object] = {"model": name, **label_key, "metric": metric, "value": value}
                own = boots.get(name, {}).get(metric)
                lo, hi, _ = _percentile(own, alpha) if own is not None else (math.nan, math.nan, 0)
                row["ci_lo"], row["ci_hi"] = lo, hi
                if name == base_label:
                    row["delta"] = row["delta_lo"] = row["delta_hi"] = math.nan
                    better = None
                else:
                    row["delta"] = value - float(points[base_label].get(metric, math.nan))
                    base_boot = boots.get(base_label, {}).get(metric)
                    if own is not None and base_boot is not None:
                        dlo, dhi, _ = _percentile(own - base_boot, alpha)
                    else:
                        dlo, dhi = math.nan, math.nan
                    row["delta_lo"], row["delta_hi"] = dlo, dhi
                    if direction is None or math.isnan(dlo):
                        better = None
                    else:
                        better = bool(dlo > 0) if direction else bool(dhi < 0)
                row["higher_is_better"] = direction
                row["better"] = better
                row["n"] = population(group_frames[name]).height
                rows.append(row)

    for key in group_keys(by):
        add_rows(bundle, key, key)
    for key in group_keys(_lineup_by(by)):
        label_key = {c: ("ALL" if c == "position" else key.get(c)) for c in by}
        add_rows(lbundle, key, label_key)
    return _summary_frame(rows, by)


# --------------------------------------------------------------------------- #
# Markdown
# --------------------------------------------------------------------------- #


def _fmt(value: object, digits: int = 3) -> str:
    if value is None:
        return ""
    if isinstance(value, float):
        if math.isnan(value):
            return "nan"
        if math.isinf(value):
            return "inf" if value > 0 else "-inf"
        return f"{value:.{digits}f}"
    return str(value)


def frame_to_markdown(frame: pl.DataFrame, digits: int = 3) -> str:
    """Render a small frame as a GitHub-flavoured markdown table."""
    if frame.is_empty():
        return "(empty)"
    cols = frame.columns
    lines = ["| " + " | ".join(cols) + " |", "| " + " | ".join("---" for _ in cols) + " |"]
    for row in frame.iter_rows():
        lines.append("| " + " | ".join(_fmt(v, digits) for v in row) + " |")
    return "\n".join(lines)


def _ci_cell(value: float, lo: float, hi: float, digits: int) -> str:
    if value is None or (isinstance(value, float) and math.isnan(value)):
        return "nan"
    if lo is None or hi is None or math.isnan(lo) or math.isnan(hi):
        return _fmt(float(value), digits)
    return f"{_fmt(float(value), digits)} [{_fmt(float(lo), digits)}, {_fmt(float(hi), digits)}]"


def _pivot_cells(frame: pl.DataFrame, index: Sequence[str], cell: str) -> pl.DataFrame:
    return frame.pivot(on="model", index=list(index), values=cell, aggregate_function="first")


def to_markdown(result: EvalResult | pl.DataFrame, digits: int = 3, per_week: bool = False) -> str:
    """Markdown for an :class:`EvalResult`, a :func:`compare` table, or any small frame.

    Summaries are pivoted to one column per model showing ``value [lo, hi]``;
    compare tables add a ``delta [lo, hi]`` column per non-baseline model.
    The output holds aggregates only.
    """
    if isinstance(result, pl.DataFrame):
        if {"model", "metric", "value", "ci_lo", "ci_hi", "delta"} <= set(result.columns):
            index = [c for c in result.columns if c not in {
                "model", "value", "ci_lo", "ci_hi", "delta", "delta_lo", "delta_hi",
                "higher_is_better", "better", "n"}]
            cells = result.with_columns(
                pl.struct(["value", "ci_lo", "ci_hi"]).map_elements(
                    lambda s: _ci_cell(s["value"], s["ci_lo"], s["ci_hi"], digits),
                    return_dtype=pl.String,
                ).alias("cell"),
                pl.struct(["delta", "delta_lo", "delta_hi", "better"]).map_elements(
                    lambda s: (
                        ""
                        if s["delta"] is None or math.isnan(s["delta"])
                        else _ci_cell(s["delta"], s["delta_lo"], s["delta_hi"], digits)
                        + (" *" if s["better"] else "")
                    ),
                    return_dtype=pl.String,
                ).alias("dcell"),
            )
            wide = _pivot_cells(cells, index, "cell")
            deltas = _pivot_cells(cells.filter(pl.col("dcell") != ""), index, "dcell")
            if not deltas.is_empty():
                deltas = deltas.rename({c: f"delta {c}" for c in deltas.columns if c not in index})
                wide = wide.join(deltas, on=index, how="left", nulls_equal=True)
            header = (
                "Point estimate [95% paired cluster-bootstrap interval]; delta is model minus "
                "baseline, * marks an interval that excludes zero in the metric's good direction.\n\n"
            )
            return header + frame_to_markdown(wide, digits)
        return frame_to_markdown(result, digits)
    if result.summary.is_empty():
        return "(no results)"
    parts: list[str] = []
    s = result.summary.with_columns(
        pl.struct(["value", "ci_lo", "ci_hi"]).map_elements(
            lambda r: _ci_cell(r["value"], r["ci_lo"], r["ci_hi"], digits), return_dtype=pl.String
        ).alias("cell")
    )
    wide = _pivot_cells(s, [*result.by, "metric"], "cell")
    parts.append(
        f"## Summary (point estimate [95% cluster-bootstrap interval, {result.n_boot} draws])\n"
    )
    parts.append(frame_to_markdown(wide, digits))
    if not result.start_sit_by_gap.is_empty():
        parts.append("\n## Start/sit accuracy by projection gap\n")
        parts.append(frame_to_markdown(result.start_sit_by_gap, digits))
    if not result.lineup.is_empty():
        group = ["model", *_lineup_by(result.by)]
        le = result.lineup.group_by(group, maintain_order=True).agg(
            pl.col("efficiency").drop_nans().mean().alias("mean_efficiency"),
            pl.len().alias("weeks"),
        )
        parts.append("\n## Lineup efficiency\n")
        parts.append(frame_to_markdown(le, digits))
    if result.reliability is not None and not result.reliability.is_empty():
        parts.append("\n## Reliability (boom / bust)\n")
        parts.append(frame_to_markdown(result.reliability, digits))
    if result.pit is not None and not result.pit.is_empty():
        parts.append("\n## PIT histogram\n")
        parts.append(frame_to_markdown(result.pit, digits))
    if per_week and result.per_week is not None:
        parts.append("\n## Per week\n")
        parts.append(frame_to_markdown(result.per_week, digits))
    return "\n".join(parts)


__all__ = [
    "CI",
    "CLUSTER",
    "DEFAULT_GAP_EDGES",
    "DEFAULT_SLOTS",
    "GROUP",
    "HIGHER_IS_BETTER",
    "KEY_COLUMNS",
    "NOMINAL_COVERAGE",
    "OPTIONAL_COLUMNS",
    "REPLACEMENT_LEVEL",
    "REQUIRED_COLUMNS",
    "SLOT_ELIGIBILITY",
    "STARTABLE",
    "EvalResult",
    "LineupResult",
    "StartSitResult",
    "align_frames",
    "best_lineup",
    "bootstrap_bundle",
    "bootstrap_ci",
    "brier",
    "calibration_slope",
    "compare",
    "evaluate",
    "frame_to_markdown",
    "interval_coverage",
    "interval_width",
    "lineup_bundle",
    "lineup_efficiency",
    "lineup_efficiency_mean",
    "mae",
    "metric_bundle",
    "metric_functions",
    "paired_bootstrap",
    "pinball",
    "pinball_loss",
    "pit_histogram",
    "pit_max_deviation",
    "population",
    "rank_within",
    "reliability_table",
    "replacement_filter",
    "spearman_by_group",
    "spearman_within",
    "start_sit",
    "start_sit_accuracy",
    "start_sit_pairs",
    "start_sit_weighted",
    "startable_population",
    "to_markdown",
    "validate_frame",
]
