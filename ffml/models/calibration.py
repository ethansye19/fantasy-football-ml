"""Post-hoc calibration of prediction frames (MODELING.md section 6).

Online calibrators run inside the walk-forward harness as a post-processing
step, and each can be frozen for the holdout and live use. Versions
(``CALIBRATION_VERSION``, written to manifests, calibrated rows and frozen
files): ``v1-pooled-isotonic`` and ``v2-per-position-isotonic`` used
:class:`OnlineConformal` for the quantiles; ``v3-tie-aware-quantile``
(current, D-088) uses :class:`OnlinePITQuantile`. All versions use
:class:`OnlineIsotonic` per position (from v2) for boom and bust.

:class:`OnlinePITQuantile` (v3)
    Recalibration in probability space, aimed at the acceptance rule (D-085):
    the randomized PIT of each earlier row is drawn with the metric's own
    :func:`ffml.eval.metrics.randomized_pit` (knots at the quantiles plus the
    prior-season position minimum and maximum, one seeded generator over the
    partition's rows in season, week, position, entity order), and the
    level-``tau`` quantile becomes the raw quantile function at
    ``G^-1(tau)``, where ``G`` is the empirical CDF of earlier weeks' PITs for
    the position (pooled, then identity, below ``min_count``). Ties at
    discrete scores are therefore treated exactly as the metric treats them.

:class:`OnlineConformal` (v1, v2)
    Per-level quantile offsets (conformalized quantile regression). For a
    level ``tau`` with predicted quantile ``q_tau`` the conformity score of a
    row is ``actual - q_tau`` and the offset is the empirical ``tau``-quantile
    (linear interpolation) of those scores, per ``by`` group (default
    position). Adding it to ``q_tau`` makes the share of actuals at or below
    the calibrated quantile equal ``tau`` on the calibration rows. For each
    predicted week, offsets come only from rows of weeks predicted earlier in
    the same run (an expanding window from the first season of the run, the
    warm-up season), and, when the frame carries ``kickoff_utc`` (the row's
    game) and ``week_cutoff`` (the first kickoff of the row's week), only
    from games that kicked off before the predicted week's first kickoff
    (D-046). Calibration rows are active rows with an actual, because the
    quantiles are conditional on being active. Groups with fewer than
    ``min_count`` calibration rows get offset 0. Quantiles are re-sorted per
    row after the offsets and optionally clipped below at a bound column.
    ``pred_mean`` is left unchanged.

:class:`OnlineIsotonic`
    Isotonic regression of the 0/1 outcome on ``boom_prob`` and ``bust_prob``
    when those columns exist, fitted on earlier weeks only in the same way.
    Calibration version ``v2-per-position-isotonic`` (D-086) fits one map per
    position; a position with fewer than ``min_count`` calibration rows uses
    the map pooled over positions, then the identity, and every row records
    which in ``cal_boom_source`` / ``cal_bust_source``. Version
    ``v1-pooled-isotonic`` (``by=()``) pooled all positions, which left kicker
    boom/bust slopes at 0.14-0.26.

Calibrated copies keep every column and rename ``model`` to
``<model>+cal``. Partitions are ``(model, scoring)``: offsets are never shared
across models or scorings.

Frozen calibration
==================
``OnlineConformal.fit_final(frame)`` fits offsets (and, given an
:class:`OnlineIsotonic`, the isotonic maps) on every row of a backtest frame
for one model, returning a :class:`FrozenCalibration` that applies the same
transformation to the holdout and to live predictions. It is saved as JSON
next to the model artifact (:func:`calibration_path_for`). Offsets and
isotonic maps are keyed by ``scoring`` when the frame has that column; a
scoring that is not in the frozen table gets no offset and identity
probabilities unless ``apply(use_scoring=...)`` maps it to a fitted one.
Frozen isotonic maps follow the online rule: a ``(scoring, position)`` map
when that group had ``min_count`` rows, else the scoring's pooled map, else
identity. ``apply`` writes the ``cal_*_source`` columns and
``calibration_version``.
"""

from __future__ import annotations

import json
import math
from collections.abc import Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl
from sklearn.isotonic import IsotonicRegression

from ffml.eval.metrics import cdf_limits, randomized_pit  # the metric's tie handling (D-088)
from ffml.models.protocol import QUANTILE_LEVELS

CAL_SUFFIX = "+cal"
PARTITION: tuple[str, ...] = ("model", "scoring")
PROB_PAIRS: tuple[tuple[str, str], ...] = (
    ("boom_prob", "boom_actual"),
    ("bust_prob", "bust_actual"),
)
CALIBRATION_FORMAT = "ffml.calibration/3"
#: older frozen files that still load (v1 isotonic maps were pooled, without fallbacks)
READABLE_FORMATS: tuple[str, ...] = ("ffml.calibration/1", "ffml.calibration/2")
#: written to the backtest manifest, the calibrated rows and every frozen file.
#: v1 pooled isotonic, v2 per-position isotonic (D-086), v3 tie-aware quantiles (D-088)
CALIBRATION_VERSION = "v3-tie-aware-quantile"
#: the offset-based quantile calibrator of v2 (OnlineConformal.fit_final writes it)
CALIBRATION_V2 = "v2-per-position-isotonic"


def quantile_column(level: float) -> str:
    """``0.05 -> "pred_q05"`` (the protocol's column naming)."""
    return f"pred_q{int(round(level * 100)):02d}"


def _offset_column(level: float) -> str:
    return f"off_q{int(round(level * 100)):02d}"


def _calibration_rows(frame: pl.DataFrame) -> pl.DataFrame:
    """Active rows with an actual (the population the quantiles are conditional on)."""
    cond = pl.col("actual").is_not_null()
    if "active" in frame.columns:
        cond = cond & pl.col("active").fill_null(False)
    return frame.filter(cond)


def _weeks(frame: pl.DataFrame) -> list[tuple[int, int]]:
    keys = frame.select("season", "week").unique().sort(["season", "week"])
    return [(int(s), int(w)) for s, w in keys.iter_rows()]


def _earlier(frame: pl.DataFrame, season: int, week: int, cutoff: Any) -> pl.DataFrame:
    """Rows of weeks before ``(season, week)`` whose game kicked off before ``cutoff``."""
    before = (pl.col("season") < season) | ((pl.col("season") == season) & (pl.col("week") < week))
    if cutoff is not None and "kickoff_utc" in frame.columns:
        before = before & (pl.col("kickoff_utc") < cutoff)
    return frame.filter(before)


def _week_cutoff(part: pl.DataFrame) -> Any:
    if "week_cutoff" not in part.columns:
        return None
    values = part["week_cutoff"].drop_nulls()
    return values.min() if values.len() else None


def _partitions(frame: pl.DataFrame, partition: Sequence[str]) -> list[pl.DataFrame]:
    cols = [c for c in partition if c in frame.columns]
    if not cols:
        return [frame]
    return [part for _, part in frame.group_by(cols, maintain_order=True)]


def sort_quantiles(frame: pl.DataFrame, columns: Sequence[str]) -> pl.DataFrame:
    """Sort the quantile columns within each row (rows with a null quantile are left unchanged)."""
    if frame.height == 0:
        return frame
    values = frame.select(columns).cast(pl.Float64).to_numpy().copy()
    ok = ~np.isnan(values).any(axis=1)
    values[ok] = np.sort(values[ok], axis=1)
    return frame.with_columns(
        pl.Series(c, values[:, i], dtype=pl.Float64).fill_nan(None) for i, c in enumerate(columns)
    )


def _clip_lower(
    frame: pl.DataFrame, columns: Sequence[str], lower_bound: str | None
) -> pl.DataFrame:
    if lower_bound is None or lower_bound not in frame.columns:
        return frame
    bound = pl.col(lower_bound)
    return frame.with_columns(
        pl.when(bound.is_null() | pl.col(c).is_null())
        .then(pl.col(c))
        .otherwise(pl.max_horizontal(pl.col(c), bound))
        .alias(c)
        for c in columns
    )


# --------------------------------------------------------------------------- #
# Conformal quantile offsets
# --------------------------------------------------------------------------- #


class OnlineConformal:
    """Walk-forward per-level quantile offsets by ``by`` group (see module docstring)."""

    def __init__(
        self,
        levels: Iterable[float] = QUANTILE_LEVELS,
        by: Iterable[str] = ("position",),
        min_count: int = 50,
        suffix: str = CAL_SUFFIX,
    ) -> None:
        self.levels = tuple(float(q) for q in levels)
        self.by = tuple(by)
        self.min_count = int(min_count)
        self.suffix = suffix
        self.columns = tuple(quantile_column(q) for q in self.levels)

    def __repr__(self) -> str:
        return f"OnlineConformal(levels={self.levels}, by={self.by}, min_count={self.min_count})"

    def offsets(self, history: pl.DataFrame) -> pl.DataFrame:
        """Offsets per ``by`` group from calibration history: ``off_qXX`` columns and ``cal_n``."""
        rows = _calibration_rows(history)
        aggs = [
            (pl.col("actual") - pl.col(c))
            .quantile(q, interpolation="linear")
            .alias(_offset_column(q))
            for q, c in zip(self.levels, self.columns, strict=True)
        ]
        aggs.append(pl.len().alias("cal_n"))
        if not self.by:
            return rows.select(aggs)
        return rows.group_by(list(self.by)).agg(aggs)

    def _apply_offsets(
        self, part: pl.DataFrame, offsets: pl.DataFrame | None, lower_bound: str | None
    ) -> pl.DataFrame:
        off_cols = [_offset_column(q) for q in self.levels]
        part = part.drop("cal_n", strict=False)
        if offsets is None or offsets.height == 0:
            out = part.with_columns(
                *[pl.lit(0.0).alias(c) for c in off_cols], pl.lit(0, dtype=pl.UInt32).alias("cal_n")
            )
        else:
            usable = offsets.with_columns(
                pl.when(pl.col("cal_n") >= self.min_count).then(pl.col(c)).otherwise(0.0).alias(c)
                for c in off_cols
            )
            if self.by:
                out = part.join(usable, on=list(self.by), how="left")
            else:
                out = part.join(usable, how="cross")
            out = out.with_columns(
                *[pl.col(c).fill_null(0.0) for c in off_cols], pl.col("cal_n").fill_null(0)
            )
        out = out.with_columns(
            (pl.col(c) + pl.col(o)).alias(c) for c, o in zip(self.columns, off_cols, strict=True)
        ).drop(off_cols)
        out = sort_quantiles(out, self.columns)
        return _clip_lower(out, self.columns, lower_bound)

    def transform(
        self,
        frame: pl.DataFrame,
        *,
        partition: Sequence[str] = PARTITION,
        lower_bound: str | None = None,
        rename: bool = True,
    ) -> pl.DataFrame:
        """Calibrated copy of every row; week ``w`` uses only rows of earlier weeks.

        Adds ``cal_n`` (calibration rows behind the offsets of the row's group).
        """
        missing = [
            c
            for c in ("season", "week", "actual", *self.columns, *self.by)
            if c not in frame.columns
        ]
        if missing:
            raise ValueError(f"frame is missing columns {missing}")
        out = []
        for part in _partitions(frame, partition):
            for season, week in _weeks(part):
                rows = part.filter((pl.col("season") == season) & (pl.col("week") == week))
                history = _earlier(part, season, week, _week_cutoff(rows))
                offsets = self.offsets(history) if history.height else None
                out.append(self._apply_offsets(rows, offsets, lower_bound))
        if not out:
            return frame.clear()
        result = pl.concat(out, how="vertical_relaxed")
        if rename and "model" in result.columns:
            result = result.with_columns((pl.col("model") + self.suffix).alias("model"))
        return result

    def fit_final(
        self,
        frame: pl.DataFrame,
        isotonic: OnlineIsotonic | None = None,
        meta: dict[str, Any] | None = None,
    ) -> FrozenCalibration:
        """Frozen offsets (and isotonic maps) from every row of one model's backtest frame."""
        if "model" in frame.columns and frame["model"].n_unique() > 1:
            raise ValueError("fit_final takes the rows of one model; filter the frame first")
        keys = [c for c in ("scoring", *self.by) if c in frame.columns]
        rows = _calibration_rows(frame)
        aggs = [
            (pl.col("actual") - pl.col(c))
            .quantile(q, interpolation="linear")
            .alias(_offset_column(q))
            for q, c in zip(self.levels, self.columns, strict=True)
        ]
        aggs.append(pl.len().alias("cal_n"))
        table = rows.group_by(keys).agg(aggs).sort(keys) if keys else rows.select(aggs)
        iso = isotonic.fit_final(frame) if isotonic is not None else {}
        info = {
            "calibration_version": CALIBRATION_V2,
            "conformal": repr(self),
            "isotonic": repr(isotonic) if isotonic is not None else None,
            "fitted_at": datetime.now(UTC).isoformat(),
            "rows": rows.height,
            "seasons": sorted({int(s) for s in frame["season"].unique().to_list()})
            if "season" in frame.columns
            else [],
            "model": frame["model"][0] if "model" in frame.columns and frame.height else None,
        }
        info.update(meta or {})
        return FrozenCalibration(
            levels=self.levels,
            by=self.by,
            keys=tuple(keys),
            min_count=self.min_count,
            offsets=table.to_dicts(),
            isotonic=iso,
            meta=info,
        )


# --------------------------------------------------------------------------- #
# Tie-aware quantile recalibration (calibration v3, D-088)
# --------------------------------------------------------------------------- #

PIT_METHOD = "pit_quantile"
OFFSET_METHOD = "conformal_offsets"
#: row order of the randomized PIT draws, the metric's seed scheme (one generator per frame)
PIT_SORT_KEYS: tuple[str, ...] = ("season", "week", "position", "entity_id")


def _knot_matrix(
    frame: pl.DataFrame, columns: Sequence[str], lower: str | None, upper: str | None
) -> np.ndarray:
    """``(n, k + 2)`` knots ``lower, q_1..q_k, upper`` with the metric's convention.

    ``lower = min(lower, q_1)`` and ``upper = max(upper, q_k)``; a missing
    bound drops that tail (the bound becomes ``q_1`` or ``q_k``), exactly as
    :func:`ffml.eval.metrics.cdf_limits`.
    """
    q = frame.select(columns).cast(pl.Float64).to_numpy()
    n = q.shape[0]
    lo = (
        frame[lower].cast(pl.Float64).to_numpy()
        if lower and lower in frame.columns
        else np.full(n, np.nan)
    )
    hi = (
        frame[upper].cast(pl.Float64).to_numpy()
        if upper and upper in frame.columns
        else np.full(n, np.nan)
    )
    lo = np.where(np.isnan(lo), q[:, 0], np.minimum(lo, q[:, 0]))
    hi = np.where(np.isnan(hi), q[:, -1], np.maximum(hi, q[:, -1]))
    return np.column_stack([lo, q, hi])


def quantile_at(knots: np.ndarray, levels: Sequence[float], p: np.ndarray) -> np.ndarray:
    """The raw predictive quantile function at probabilities ``p`` (inverse of the piecewise-linear CDF).

    ``knots`` is ``(n, k + 2)`` from :func:`_knot_matrix` at levels
    ``0, levels..., 1``; ``p`` is ``(n, m)`` or ``(m,)``. Linear between knots
    and out to the bounds; inside a point mass (tied knots) the tied value.
    """
    full = np.array([0.0, *levels, 1.0])
    p = np.clip(np.asarray(p, dtype=float), 0.0, 1.0)
    if p.ndim == 1:
        p = np.broadcast_to(p, (knots.shape[0], p.size))
    j = np.clip(np.searchsorted(full, p, side="right") - 1, 0, full.size - 2)
    t = (p - full[j]) / (full[j + 1] - full[j])
    rows = np.arange(knots.shape[0])[:, None]
    x0, x1 = knots[rows, j], knots[rows, j + 1]
    return x0 + t * (x1 - x0)


def pit_limits(
    frame: pl.DataFrame,
    columns: Sequence[str] = tuple(quantile_column(q) for q in QUANTILE_LEVELS),
    lower: str | None = "pos_min",
    upper: str | None = "pos_max",
    levels: Sequence[float] = QUANTILE_LEVELS,
) -> tuple[np.ndarray, np.ndarray]:
    """``F(y-)`` and ``F(y)`` per row from :func:`ffml.eval.metrics.cdf_limits` (same knots and bounds)."""
    knots = _knot_matrix(frame, columns, lower, upper)
    return cdf_limits(
        frame["actual"].cast(pl.Float64).to_numpy(),
        knots[:, 1:-1],
        knots[:, 0],
        knots[:, -1],
        levels,
    )


class OnlinePITQuantile:
    """Walk-forward recalibration of the quantiles in probability space, per position (v3, D-088).

    The acceptance rule (D-085) measures the randomized PIT: ``U`` uniform on
    ``[F(y-), F(y)]`` of the model's piecewise-linear CDF (knots at the
    quantiles plus the prior-season position minimum and maximum). This
    calibrator computes exactly that quantity with the metric's own functions
    (:func:`ffml.eval.metrics.randomized_pit`; the eligible rows of a
    ``(model, scoring)`` partition are ordered by season, week, position and
    entity and drawn from one generator seeded with ``seed``). For each
    predicted week, ``G`` is the empirical CDF of the randomized PITs of
    earlier weeks (games before the week's first kickoff) for the row's
    position, or for all positions of the partition when the position has
    fewer than ``min_count`` rows, or the identity when that is also short.
    The calibrated level-``tau`` quantile is the raw quantile function at
    ``G^-1(tau)`` (linear between knots and to the bounds), so under
    calibration the randomized PIT becomes uniform, ties included. The levels
    used are recorded per row in ``cal_q_source`` (``position``, ``pooled``,
    ``identity``) and ``cal_n``. ``pred_mean`` is unchanged; quantiles are
    monotone because ``G^-1`` and the quantile function are.

    Atoms (``atoms="nearest"``, the default). An integer score that lands
    exactly on an untied knot has a PIT exactly equal to that knot's level,
    so the earlier PITs pile up at the knot levels, and the rule's open
    interval ``(0.1, 0.9)`` counts such a row as outside on both tails. When
    ``G^-1(tau)`` falls on an atom ``a``, the knot is placed on the atom
    (``p = a``) or just off it (``a - eps`` below the median, ``a + eps``
    above), whichever brings the outside share closest to its target.
    Outside means ``U <= tau`` for lower levels and ``U >= tau`` for upper
    levels. ``atoms="plain"`` is the plain ``G^-1``.
    """

    #: probability step used to place a knot just off an atom
    ATOM_EPS = 1e-6
    #: PITs closer than this count as one atom
    ATOM_TOL = 1e-12

    def __init__(
        self,
        levels: Iterable[float] = QUANTILE_LEVELS,
        by: Iterable[str] = ("position",),
        min_count: int = 50,
        seed: int = 0,
        suffix: str = CAL_SUFFIX,
        atoms: str = "nearest",
    ) -> None:
        if atoms not in ("nearest", "plain"):
            raise ValueError("atoms must be 'nearest' or 'plain'")
        self.levels = tuple(float(q) for q in levels)
        self.by = tuple(by)
        self.min_count = int(min_count)
        self.seed = int(seed)
        self.suffix = suffix
        self.atoms = atoms
        self.columns = tuple(quantile_column(q) for q in self.levels)

    def __repr__(self) -> str:
        return (
            f"OnlinePITQuantile(levels={self.levels}, by={self.by}, min_count={self.min_count}, "
            f"seed={self.seed}, atoms={self.atoms!r})"
        )

    @property
    def group_source(self) -> str:
        return "+".join(self.by) if self.by else "pooled"

    def pits(
        self, part: pl.DataFrame, lower: str | None = "pos_min", upper: str | None = "pos_max"
    ) -> np.ndarray:
        """Randomized PIT per row of ``part`` (NaN outside the evaluation population), metric scheme."""
        n = part.height
        out = np.full(n, np.nan)
        cond = pl.col("actual").is_not_null() & pl.all_horizontal(
            [pl.col(c).is_not_null() for c in self.columns]
        )
        if "active" in part.columns:
            cond = cond & pl.col("active").fill_null(False)
        idx = part.with_row_index("_ri").filter(cond)
        if idx.height == 0:
            return out
        keys = [k for k in PIT_SORT_KEYS if k in idx.columns]
        idx = idx.sort(keys)
        knots = _knot_matrix(idx, self.columns, lower, upper)
        u = randomized_pit(
            idx["actual"].cast(pl.Float64).to_numpy(),
            knots[:, 1:-1],
            knots[:, 0],
            knots[:, -1],
            seed=self.seed,
            levels=self.levels,
        )
        out[idx["_ri"].to_numpy()] = u
        return out

    def level_map(self, u: np.ndarray) -> np.ndarray:
        """``G^-1(tau)`` for every level from the history PITs, with the atom rule (class docstring)."""
        u = np.sort(np.asarray(u, dtype=float))
        p = np.quantile(u, self.levels, method="linear")
        if self.atoms == "plain" or u.size == 0:
            return p
        n = u.size
        for k, tau in enumerate(self.levels):
            a = p[k]
            below = np.searchsorted(u, a - self.ATOM_TOL, side="left") / n  # G(a-)
            at = np.searchsorted(u, a + self.ATOM_TOL, side="right") / n  # G(a)
            if at - below < 1.0 / n + 1e-15:  # no repeated value at a: not an atom
                continue
            # the atom's own rows sit at U = a; tau is the outside share at or below (or above) the knot
            if tau <= 0.5:
                p[k] = a if abs(at - tau) <= abs(below - tau) else a - self.ATOM_EPS
            else:
                p[k] = a if abs(tau - below) <= abs(tau - at) else a + self.ATOM_EPS
        return np.clip(p, 0.0, 1.0)

    def _recalibrate(
        self,
        rows: pl.DataFrame,
        plevels: np.ndarray,
        lower: str | None,
        upper: str | None,
        source: np.ndarray,
        counts: np.ndarray,
    ) -> pl.DataFrame:
        """New quantiles ``Q_raw(plevels)`` per row (``plevels`` is ``(n, k)``)."""
        if rows.height == 0:
            return rows
        knots = _knot_matrix(rows, self.columns, lower, upper)
        ok = ~np.isnan(knots).any(axis=1)
        new = rows.select(self.columns).cast(pl.Float64).to_numpy().copy()
        if ok.any():
            vals = quantile_at(knots[ok], self.levels, plevels[ok])
            new[ok] = np.sort(vals, axis=1)
        return rows.with_columns(
            *[
                pl.Series(c, new[:, i], dtype=pl.Float64).fill_nan(None)
                for i, c in enumerate(self.columns)
            ],
            pl.Series("cal_q_source", source.tolist(), dtype=pl.String),
            pl.Series("cal_n", counts, dtype=pl.Int64),
        )

    def _week_levels(
        self, rows: pl.DataFrame, history: pl.DataFrame
    ) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        """Per-row levels, sources and history counts for one predicted week."""
        n = rows.height
        identity = np.array(self.levels)
        plevels = np.tile(identity, (n, 1))
        source = np.full(n, "identity", dtype=object)
        counts = np.zeros(n, dtype=np.int64)
        hist = history.filter(pl.col("_u").is_not_null() & ~pl.col("_u").is_nan())
        pooled = self.level_map(hist["_u"].to_numpy()) if hist.height >= self.min_count else None
        if pooled is not None:
            plevels[:] = pooled
            source[:] = "pooled"
            counts[:] = hist.height
        if self.by:
            row_keys = rows.select(list(self.by)).rows()
            groups = hist.group_by(list(self.by)).agg(pl.col("_u"))
            for rec in groups.iter_rows(named=True):
                key = tuple(rec[c] for c in self.by)
                values = np.asarray(rec["_u"], dtype=float)
                if values.size < self.min_count:
                    continue
                mask = np.array([k == key for k in row_keys], dtype=bool)
                if not mask.any():
                    continue
                plevels[mask] = self.level_map(values)
                source[mask] = self.group_source
                counts[mask] = values.size
        return plevels, source, counts

    def transform(
        self,
        frame: pl.DataFrame,
        *,
        partition: Sequence[str] = PARTITION,
        lower_bound: str | None = "pos_min",
        upper_bound: str | None = "pos_max",
        rename: bool = True,
    ) -> pl.DataFrame:
        """Calibrated copy of every row; week ``w`` uses only randomized PITs of earlier weeks."""
        missing = [
            c
            for c in ("season", "week", "actual", *self.columns, *self.by)
            if c not in frame.columns
        ]
        if missing:
            raise ValueError(f"frame is missing columns {missing}")
        out = []
        for part in _partitions(frame, partition):
            part = part.drop("cal_n", "cal_q_source", strict=False).with_columns(
                pl.Series("_u", self.pits(part, lower_bound, upper_bound), dtype=pl.Float64)
            )
            for season, week in _weeks(part):
                rows = part.filter((pl.col("season") == season) & (pl.col("week") == week))
                history = _earlier(part, season, week, _week_cutoff(rows))
                plevels, source, counts = self._week_levels(rows, history)
                out.append(
                    self._recalibrate(rows, plevels, lower_bound, upper_bound, source, counts).drop(
                        "_u"
                    )
                )
        if not out:
            return frame.clear()
        result = pl.concat(out, how="vertical_relaxed")
        if rename and "model" in result.columns:
            result = result.with_columns((pl.col("model") + self.suffix).alias("model"))
        return result

    def fit_final(
        self,
        frame: pl.DataFrame,
        isotonic: OnlineIsotonic | None = None,
        meta: dict[str, Any] | None = None,
        *,
        lower_bound: str | None = "pos_min",
        upper_bound: str | None = "pos_max",
        bounds: pl.DataFrame | None = None,
    ) -> FrozenCalibration:
        """Frozen ``G^-1`` levels per ``(scoring, position)`` plus pooled per scoring, from every row.

        ``bounds`` (``scoring, position, pos_min, pos_max``) are stored for
        applying the calibrator to frames that carry no bound columns
        (the holdout and live weeks: extremes of every season before them).
        """
        if "model" in frame.columns and frame["model"].n_unique() > 1:
            raise ValueError("fit_final takes the rows of one model; filter the frame first")
        part_keys = [c for c in ("scoring",) if c in frame.columns]
        maps, pooled = [], []
        parts = frame.group_by(part_keys, maintain_order=True) if part_keys else [((), frame)]
        n_rows = 0
        for pkey, part in parts:
            pgroup = dict(zip(part_keys, [str(v) for v in pkey], strict=True))
            u = self.pits(part, lower_bound, upper_bound)
            part = part.with_columns(pl.Series("_u", u, dtype=pl.Float64)).filter(
                ~pl.col("_u").is_nan()
            )
            n_rows += part.height
            if part.height >= self.min_count:
                pooled.append(
                    {
                        "group": pgroup,
                        "levels": self.level_map(part["_u"].to_numpy()).tolist(),
                        "n": part.height,
                    }
                )
            for key, sub in part.group_by(list(self.by), maintain_order=True) if self.by else []:
                if sub.height < self.min_count:
                    continue
                group = {**pgroup, **dict(zip(self.by, [str(v) for v in key], strict=True))}
                maps.append(
                    {
                        "group": group,
                        "levels": self.level_map(sub["_u"].to_numpy()).tolist(),
                        "n": sub.height,
                    }
                )
        iso = isotonic.fit_final(frame) if isotonic is not None else {}
        info = {
            "calibration_version": CALIBRATION_VERSION,
            "quantile_calibrator": repr(self),
            "isotonic": repr(isotonic) if isotonic is not None else None,
            "fitted_at": datetime.now(UTC).isoformat(),
            "rows": n_rows,
            "seasons": sorted({int(s) for s in frame["season"].unique().to_list()})
            if "season" in frame.columns
            else [],
            "model": frame["model"][0] if "model" in frame.columns and frame.height else None,
        }
        info.update(meta or {})
        stored_bounds = (
            bounds.select(
                *[
                    pl.col(c).cast(pl.String)
                    for c in ("scoring", "position")
                    if c in bounds.columns
                ],
                pl.col("pos_min").cast(pl.Float64),
                pl.col("pos_max").cast(pl.Float64),
            ).to_dicts()
            if bounds is not None
            else []
        )
        return FrozenCalibration(
            levels=self.levels,
            by=self.by,
            keys=tuple([*part_keys, *self.by]),
            min_count=self.min_count,
            offsets=[],
            isotonic=iso,
            meta=info,
            quantile_method=PIT_METHOD,
            quantile_maps={
                "keys": [*part_keys, *self.by],
                "pooled_keys": part_keys,
                "maps": maps,
                "pooled": pooled,
                "seed": self.seed,
            },
            bounds=stored_bounds,
        )


# --------------------------------------------------------------------------- #
# Isotonic probability calibration
# --------------------------------------------------------------------------- #


def _fit_isotonic(p: np.ndarray, y: np.ndarray) -> IsotonicRegression:
    model = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    model.fit(p, y)
    return model


def source_column(prob: str) -> str:
    """``"boom_prob" -> "cal_boom_source"``: which isotonic map calibrated each row."""
    return f"cal_{prob.removesuffix('_prob')}_source"


def _isotonic_entry(
    train: pl.DataFrame, prob: str, outcome: str, group: dict[str, str]
) -> dict[str, Any]:
    iso = _fit_isotonic(
        train[prob].cast(pl.Float64).to_numpy(), train[outcome].cast(pl.Float64).to_numpy()
    )
    return {
        "group": group,
        "x": [float(v) for v in iso.X_thresholds_],
        "y": [float(v) for v in iso.y_thresholds_],
        "n": train.height,
    }


def _interp(p: np.ndarray, entry: dict[str, Any]) -> np.ndarray:
    """Evaluate a stored isotonic map (step-wise linear between thresholds, clipped at the ends)."""
    return np.interp(p, entry["x"], entry["y"])


def _with_probs(
    rows: pl.DataFrame, prob: str, entry: dict[str, Any] | None, source: str
) -> pl.DataFrame:
    p = rows[prob].cast(pl.Float64).to_numpy()
    new = p.copy()
    ok = ~np.isnan(p)
    if entry is not None and ok.any():
        new[ok] = _interp(p[ok], entry)
    return rows.with_columns(
        pl.Series(prob, new, dtype=pl.Float64).fill_nan(None),
        pl.lit(source).alias(source_column(prob)),
    )


class OnlineIsotonic:
    """Walk-forward isotonic recalibration of probability columns, per position by default.

    For each predicted week and probability column, a group (``by``, default
    position) with at least ``min_count`` calibration rows in earlier weeks
    gets its own isotonic map (source ``"position"``, or the ``by`` names
    joined with ``+``). A smaller group uses the map fitted on all positions
    of the partition (source ``"pooled"``) when that has ``min_count`` rows,
    and otherwise keeps its probabilities (source ``"identity"``). The source
    of every row is written to ``cal_<kind>_source`` (``cal_boom_source``,
    ``cal_bust_source``). ``by=()`` gives the pooled calibrator of
    calibration v1.
    """

    def __init__(
        self,
        pairs: Iterable[tuple[str, str]] = PROB_PAIRS,
        by: Iterable[str] = ("position",),
        min_count: int = 200,
    ) -> None:
        self.pairs = tuple((str(p), str(o)) for p, o in pairs)
        self.by = tuple(by)
        self.min_count = int(min_count)

    def __repr__(self) -> str:
        return f"OnlineIsotonic(pairs={self.pairs}, by={self.by}, min_count={self.min_count})"

    @property
    def group_source(self) -> str:
        return "+".join(self.by) if self.by else "pooled"

    def present(self, frame: pl.DataFrame) -> list[tuple[str, str]]:
        return [(p, o) for p, o in self.pairs if p in frame.columns and o in frame.columns]

    def _training(self, history: pl.DataFrame, prob: str, outcome: str) -> pl.DataFrame:
        return _calibration_rows(history).filter(
            pl.col(prob).is_not_null() & pl.col(outcome).is_not_null()
        )

    def _calibrate_rows(
        self, rows: pl.DataFrame, history: pl.DataFrame, prob: str, outcome: str
    ) -> pl.DataFrame:
        train_all = self._training(history, prob, outcome)
        pooled = (
            _isotonic_entry(train_all, prob, outcome, {})
            if train_all.height >= self.min_count
            else None
        )
        if not self.by:
            return _with_probs(rows, prob, pooled, "pooled" if pooled else "identity")
        out = []
        for key, g in rows.group_by(list(self.by), maintain_order=True):
            train = train_all
            for c, v in zip(self.by, key, strict=True):
                train = train.filter(pl.col(c) == v)
            if train.height >= self.min_count:
                group = dict(zip(self.by, [str(v) for v in key], strict=True))
                out.append(
                    _with_probs(
                        g, prob, _isotonic_entry(train, prob, outcome, group), self.group_source
                    )
                )
            elif pooled is not None:
                out.append(_with_probs(g, prob, pooled, "pooled"))
            else:
                out.append(_with_probs(g, prob, None, "identity"))
        return pl.concat(out, how="vertical_relaxed") if out else rows

    def transform(
        self, frame: pl.DataFrame, *, partition: Sequence[str] = PARTITION
    ) -> pl.DataFrame:
        """Recalibrated probability columns; week ``w`` uses only rows of earlier weeks."""
        pairs = self.present(frame)
        if not pairs:
            return frame
        out = []
        for part in _partitions(frame, partition):
            for season, week in _weeks(part):
                rows = part.filter((pl.col("season") == season) & (pl.col("week") == week))
                history = _earlier(part, season, week, _week_cutoff(rows))
                for prob, outcome in pairs:
                    rows = self._calibrate_rows(rows, history, prob, outcome)
                out.append(rows)
        return pl.concat(out, how="diagonal_relaxed") if out else frame

    def fit_final(self, frame: pl.DataFrame) -> dict[str, Any]:
        """Frozen maps per probability column: one per ``(scoring, by)`` group plus pooled per scoring.

        Groups and the pooled fit need ``min_count`` calibration rows, as online.
        """
        maps: dict[str, Any] = {}
        part_keys = [c for c in ("scoring",) if c in frame.columns]
        for prob, outcome in self.present(frame):
            groups, pooled = [], []
            parts = frame.group_by(part_keys, maintain_order=True) if part_keys else [((), frame)]
            for pkey, part in parts:
                pgroup = dict(zip(part_keys, [str(v) for v in pkey], strict=True))
                train_all = self._training(part, prob, outcome)
                if train_all.height >= self.min_count:
                    pooled.append(_isotonic_entry(train_all, prob, outcome, pgroup))
                if not self.by:
                    continue
                for key, sub in train_all.group_by(list(self.by), maintain_order=True):
                    if sub.height < self.min_count:
                        continue
                    group = {**pgroup, **dict(zip(self.by, [str(v) for v in key], strict=True))}
                    groups.append(_isotonic_entry(sub, prob, outcome, group))
            if groups or pooled:
                maps[prob] = {
                    "outcome": outcome,
                    "keys": [*part_keys, *self.by],
                    "pooled_keys": part_keys,
                    "by": list(self.by),
                    "min_count": self.min_count,
                    "maps": groups,
                    "pooled": pooled,
                }
        return maps


# --------------------------------------------------------------------------- #
# Frozen calibration
# --------------------------------------------------------------------------- #


@dataclass
class FrozenCalibration:
    """Offsets and isotonic maps fitted once on a backtest, applied to new frames."""

    levels: tuple[float, ...]
    by: tuple[str, ...]
    keys: tuple[str, ...]
    min_count: int
    offsets: list[dict[str, Any]]
    isotonic: dict[str, Any] = field(default_factory=dict)
    meta: dict[str, Any] = field(default_factory=dict)
    #: ``conformal_offsets`` (v1, v2) or ``pit_quantile`` (v3)
    quantile_method: str = OFFSET_METHOD
    #: v3: ``G^-1`` levels per group plus pooled per scoring
    quantile_maps: dict[str, Any] = field(default_factory=dict)
    #: v3: position bounds for frames without bound columns
    bounds: list[dict[str, Any]] = field(default_factory=list)

    @property
    def columns(self) -> tuple[str, ...]:
        return tuple(quantile_column(q) for q in self.levels)

    def offset_table(self) -> pl.DataFrame:
        schema: dict[str, pl.DataType] = {k: pl.String() for k in self.keys}
        schema.update({_offset_column(q): pl.Float64() for q in self.levels})
        schema["cal_n"] = pl.Int64()
        if not self.offsets:
            return pl.DataFrame(schema=schema)
        return pl.DataFrame(self.offsets).select(list(schema)).cast(schema)

    def apply(
        self,
        frame: pl.DataFrame,
        *,
        lower_bound: str | None = None,
        upper_bound: str | None = None,
        rename: bool = False,
        suffix: str = CAL_SUFFIX,
        use_scoring: str | None = None,
    ) -> pl.DataFrame:
        """Calibrated copy of ``frame``; rows whose group is not in the table keep their quantiles.

        ``use_scoring`` applies the calibration fitted under that scoring to
        every row (for a league scoring without its own backtest). For v3,
        ``lower_bound`` / ``upper_bound`` name the frame's position-bound
        columns; when absent the stored bounds are used.
        """
        if self.quantile_method == PIT_METHOD:
            out = self._apply_pit_levels(frame, lower_bound, upper_bound, use_scoring)
        else:
            out = self._apply_offsets(frame, lower_bound, use_scoring)
        for prob, spec in self.isotonic.items():
            if prob not in out.columns:
                continue
            out = self._apply_isotonic(out, prob, spec, use_scoring=use_scoring)
        out = out.with_columns(
            pl.lit(self.meta.get("calibration_version", CALIBRATION_VERSION)).alias(
                "calibration_version"
            )
        )
        if rename and "model" in out.columns:
            out = out.with_columns((pl.col("model") + suffix).alias("model"))
        return out

    def _apply_pit_levels(
        self,
        frame: pl.DataFrame,
        lower_bound: str | None,
        upper_bound: str | None,
        use_scoring: str | None,
    ) -> pl.DataFrame:
        """v3: ``Q_raw(G^-1(tau))`` with the row's group levels, else pooled, else identity."""
        spec = self.quantile_maps
        keys = list(spec.get("keys", []))
        pooled_keys = list(spec.get("pooled_keys", []))
        base = frame.drop("cal_n", "cal_q_source", strict=False)
        lo_col, hi_col = lower_bound, upper_bound
        if (lo_col is None or lo_col not in base.columns) and self.bounds:
            b = pl.DataFrame(self.bounds)
            on = [c for c in ("scoring", "position") if c in b.columns and c in base.columns]
            if use_scoring is not None and "scoring" in on:
                b = b.filter(pl.col("scoring") == use_scoring).drop("scoring")
                on.remove("scoring")
            base = base.with_columns(pl.col(c).cast(pl.String) for c in on).join(
                b.rename({"pos_min": "_lo", "pos_max": "_hi"}),
                on=on,
                how="left",
                maintain_order="left",
            )
            lo_col, hi_col = "_lo", "_hi"
        n = base.height

        def key_values(cols: list[str]) -> list[tuple]:
            vals = []
            for c in cols:
                if c == "scoring" and use_scoring is not None:
                    vals.append([use_scoring] * n)
                elif c in base.columns:
                    vals.append(base[c].cast(pl.String).to_list())
                else:
                    vals.append([None] * n)
            return list(zip(*vals, strict=True)) if vals else [()] * n

        plevels = np.tile(np.array(self.levels), (n, 1))
        source = np.full(n, "identity", dtype=object)
        counts = np.zeros(n, dtype=np.int64)
        done = np.zeros(n, dtype=bool)
        group_source = "+".join(self.by) or "pooled"
        for entries, cols, label in (
            (spec.get("maps", []), keys, group_source),
            (spec.get("pooled", []), pooled_keys, "pooled"),
        ):
            if not entries:
                continue
            row_keys = key_values(cols)
            for entry in entries:
                ekey = tuple(entry["group"].get(c) for c in cols)
                mask = ~done & np.array([k == ekey for k in row_keys], dtype=bool)
                if not mask.any():
                    continue
                plevels[mask] = entry["levels"]
                source[mask] = label
                counts[mask] = entry["n"]
                done |= mask
        calibrator = OnlinePITQuantile(levels=self.levels, by=self.by, min_count=self.min_count)
        out = calibrator._recalibrate(base, plevels, lo_col, hi_col, source, counts)
        return out.drop("_lo", "_hi", strict=False)

    def _apply_offsets(
        self, frame: pl.DataFrame, lower_bound: str | None, use_scoring: str | None
    ) -> pl.DataFrame:
        """v1/v2: per-level conformal offsets."""
        table = self.offset_table()
        off_cols = [_offset_column(q) for q in self.levels]
        usable = table.with_columns(
            pl.when(pl.col("cal_n") >= self.min_count).then(pl.col(c)).otherwise(0.0).alias(c)
            for c in off_cols
        )
        keys = list(self.keys)
        if use_scoring is not None and "scoring" in keys:
            usable = usable.filter(pl.col("scoring") == use_scoring).drop("scoring")
            keys.remove("scoring")
        missing = [k for k in keys if k not in frame.columns]
        if missing:
            raise ValueError(f"frame lacks calibration keys {missing}")
        base = frame.drop("cal_n", strict=False)
        if keys:
            out = base.with_columns(pl.col(k).cast(pl.String) for k in keys).join(
                usable, on=keys, how="left"
            )
        elif usable.height:
            out = base.join(usable.head(1), how="cross")
        else:
            out = base.with_columns(
                *[pl.lit(0.0).alias(c) for c in off_cols], pl.lit(0, dtype=pl.Int64).alias("cal_n")
            )
        out = out.with_columns(
            *[pl.col(c).fill_null(0.0) for c in off_cols], pl.col("cal_n").fill_null(0)
        )
        out = out.with_columns(
            (pl.col(c) + pl.col(o)).alias(c) for c, o in zip(self.columns, off_cols, strict=True)
        ).drop(off_cols)
        out = sort_quantiles(out, self.columns)
        return _clip_lower(out, self.columns, lower_bound)

    @staticmethod
    def _apply_isotonic(
        frame: pl.DataFrame,
        prob: str,
        spec: dict[str, Any],
        use_scoring: str | None = None,
    ) -> pl.DataFrame:
        """Group map where one exists, else the pooled map of the row's scoring, else identity."""
        keys = list(spec.get("keys", spec.get("by", [])))
        pooled_keys = list(spec.get("pooled_keys", []))
        group_source = "+".join(spec.get("by", [])) or "pooled"
        n = frame.height

        def key_values(cols: list[str]) -> list[tuple[str, ...]]:
            values = []
            for c in cols:
                if c == "scoring" and use_scoring is not None:
                    values.append([use_scoring] * n)
                elif c in frame.columns:
                    values.append(frame[c].cast(pl.String).to_list())
                else:
                    values.append([None] * n)
            return list(zip(*values, strict=True)) if values else [()] * n

        p = frame[prob].cast(pl.Float64).to_numpy().copy()
        result = p.copy()
        source = np.full(n, "identity", dtype=object)
        done = np.zeros(n, dtype=bool)
        ok = ~np.isnan(p)
        for entries, cols, label in (
            (spec.get("maps", []), keys, group_source),
            (spec.get("pooled", []), pooled_keys, "pooled"),
        ):
            if not entries:
                continue
            row_keys = key_values(cols)
            lookup = {tuple(e["group"].get(c) for c in cols): e for e in entries}
            for i_key, entry in lookup.items():
                mask = ~done & np.array([k == i_key for k in row_keys], dtype=bool)
                if not mask.any():
                    continue
                apply = mask & ok
                if apply.any():
                    result[apply] = _interp(p[apply], entry)
                source[mask] = label
                done |= mask
        return frame.with_columns(
            pl.Series(prob, result, dtype=pl.Float64).fill_nan(None),
            pl.Series(source_column(prob), source.tolist(), dtype=pl.String),
        )

    def to_dict(self) -> dict[str, Any]:
        def clean(v: Any) -> Any:
            if isinstance(v, float) and math.isnan(v):
                return None
            return v

        return {
            "format": CALIBRATION_FORMAT,
            "levels": list(self.levels),
            "by": list(self.by),
            "keys": list(self.keys),
            "min_count": self.min_count,
            "offsets": [{k: clean(v) for k, v in row.items()} for row in self.offsets],
            "quantile_method": self.quantile_method,
            "quantile_maps": self.quantile_maps,
            "bounds": [{k: clean(v) for k, v in row.items()} for row in self.bounds],
            "isotonic": self.isotonic,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrozenCalibration:
        if payload.get("format") not in (CALIBRATION_FORMAT, *READABLE_FORMATS):
            raise ValueError(f"not a calibration file (format={payload.get('format')!r})")
        return cls(
            levels=tuple(float(q) for q in payload["levels"]),
            by=tuple(payload["by"]),
            keys=tuple(payload["keys"]),
            min_count=int(payload["min_count"]),
            offsets=list(payload["offsets"]),
            isotonic=dict(payload.get("isotonic", {})),
            meta=dict(payload.get("meta", {})),
            quantile_method=str(payload.get("quantile_method", OFFSET_METHOD)),
            quantile_maps=dict(payload.get("quantile_maps", {})),
            bounds=list(payload.get("bounds", [])),
        )

    def save(self, path: Path | str) -> Path:
        path = Path(path)
        path.parent.mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(path.suffix + ".tmp")
        tmp.write_text(json.dumps(self.to_dict(), indent=2) + "\n", encoding="utf-8")
        tmp.replace(path)
        return path

    @classmethod
    def load(cls, path: Path | str) -> FrozenCalibration:
        return cls.from_dict(json.loads(Path(path).read_text(encoding="utf-8")))


def calibration_path_for(artifact: Path | str) -> Path:
    """Where the frozen calibration of a model artifact lives (next to it)."""
    artifact = Path(artifact)
    if artifact.is_dir() or not artifact.suffix:
        return artifact / "calibration.json"
    return artifact.with_name(f"{artifact.stem}.calibration.json")


def save_next_to(frozen: FrozenCalibration, artifact: Path | str) -> Path:
    """Write ``frozen`` as JSON next to the model artifact and return the path."""
    return frozen.save(calibration_path_for(artifact))


__all__ = [
    "CALIBRATION_V2",
    "CALIBRATION_VERSION",
    "CAL_SUFFIX",
    "PIT_METHOD",
    "FrozenCalibration",
    "OnlineConformal",
    "OnlineIsotonic",
    "OnlinePITQuantile",
    "pit_limits",
    "quantile_at",
    "calibration_path_for",
    "quantile_column",
    "save_next_to",
    "sort_quantiles",
    "source_column",
]
