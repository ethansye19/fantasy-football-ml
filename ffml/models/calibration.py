"""Post-hoc calibration of prediction frames (MODELING.md section 6).

Two online calibrators run inside the walk-forward harness as a
post-processing step, and each can be frozen for the holdout and live use.

:class:`OnlineConformal`
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
    when those columns exist, fitted on earlier weeks only in the same way
    (pooled over positions by default, at least ``min_count`` rows).

Calibrated copies keep every column and rename ``model`` to
``<model>+cal``. Partitions are ``(model, scoring)``: offsets are never shared
across models or scorings.

Frozen calibration
==================
``OnlineConformal.fit_final(frame)`` fits offsets (and, given an
:class:`OnlineIsotonic`, the isotonic maps) on every row of a backtest frame
for one model, returning a :class:`FrozenCalibration` that applies the same
transformation to the holdout and to live predictions. It is saved as JSON
next to the model artifact (:func:`calibration_path_for`). Offsets are keyed by
``scoring`` when the frame has that column; a scoring that is not in the
frozen table gets no offset (``apply`` reports the unmatched rows).
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

from ffml.models.protocol import QUANTILE_LEVELS

CAL_SUFFIX = "+cal"
PARTITION: tuple[str, ...] = ("model", "scoring")
PROB_PAIRS: tuple[tuple[str, str], ...] = (
    ("boom_prob", "boom_actual"),
    ("bust_prob", "bust_actual"),
)
CALIBRATION_FORMAT = "ffml.calibration/1"


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
# Isotonic probability calibration
# --------------------------------------------------------------------------- #


def _fit_isotonic(p: np.ndarray, y: np.ndarray) -> IsotonicRegression:
    model = IsotonicRegression(y_min=0.0, y_max=1.0, increasing=True, out_of_bounds="clip")
    model.fit(p, y)
    return model


class OnlineIsotonic:
    """Walk-forward isotonic recalibration of probability columns (see module docstring)."""

    def __init__(
        self,
        pairs: Iterable[tuple[str, str]] = PROB_PAIRS,
        by: Iterable[str] = (),
        min_count: int = 200,
    ) -> None:
        self.pairs = tuple((str(p), str(o)) for p, o in pairs)
        self.by = tuple(by)
        self.min_count = int(min_count)

    def __repr__(self) -> str:
        return f"OnlineIsotonic(pairs={self.pairs}, by={self.by}, min_count={self.min_count})"

    def present(self, frame: pl.DataFrame) -> list[tuple[str, str]]:
        return [(p, o) for p, o in self.pairs if p in frame.columns and o in frame.columns]

    def _training(self, history: pl.DataFrame, prob: str, outcome: str) -> pl.DataFrame:
        return _calibration_rows(history).filter(
            pl.col(prob).is_not_null() & pl.col(outcome).is_not_null()
        )

    def _calibrate_rows(
        self, rows: pl.DataFrame, history: pl.DataFrame, prob: str, outcome: str
    ) -> pl.DataFrame:
        groups = (
            [part for _, part in rows.group_by(list(self.by), maintain_order=True)]
            if self.by
            else [rows]
        )
        out = []
        for g in groups:
            hist = history
            for c in self.by:
                hist = hist.filter(pl.col(c) == g[c][0])
            train = self._training(hist, prob, outcome)
            if train.height < self.min_count:
                out.append(g)
                continue
            iso = _fit_isotonic(
                train[prob].cast(pl.Float64).to_numpy(), train[outcome].cast(pl.Float64).to_numpy()
            )
            p = g[prob].cast(pl.Float64).to_numpy()
            ok = ~np.isnan(p)
            new = p.copy()
            if ok.any():
                new[ok] = iso.predict(p[ok])
            out.append(g.with_columns(pl.Series(prob, new, dtype=pl.Float64).fill_nan(None)))
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
        return pl.concat(out, how="vertical_relaxed") if out else frame

    def fit_final(self, frame: pl.DataFrame) -> dict[str, Any]:
        """Isotonic maps per probability column (and ``by`` group) from every row of ``frame``."""
        maps: dict[str, Any] = {}
        for prob, outcome in self.present(frame):
            groups = (
                [(k, part) for k, part in frame.group_by(list(self.by), maintain_order=True)]
                if self.by
                else [((), frame)]
            )
            entries = []
            for key, part in groups:
                train = self._training(part, prob, outcome)
                if train.height < self.min_count:
                    continue
                iso = _fit_isotonic(
                    train[prob].cast(pl.Float64).to_numpy(),
                    train[outcome].cast(pl.Float64).to_numpy(),
                )
                entries.append(
                    {
                        "group": dict(zip(self.by, [str(v) for v in key], strict=True)),
                        "x": [float(v) for v in iso.X_thresholds_],
                        "y": [float(v) for v in iso.y_thresholds_],
                        "n": train.height,
                    }
                )
            if entries:
                maps[prob] = {"outcome": outcome, "by": list(self.by), "maps": entries}
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
        rename: bool = False,
        suffix: str = CAL_SUFFIX,
        use_scoring: str | None = None,
    ) -> pl.DataFrame:
        """Calibrated copy of ``frame``; rows whose group is not in the table keep their quantiles.

        ``use_scoring`` applies the offsets fitted under that scoring to every
        row (for a league scoring without its own backtest).
        """
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
        out = _clip_lower(out, self.columns, lower_bound)
        for prob, spec in self.isotonic.items():
            if prob not in out.columns:
                continue
            out = self._apply_isotonic(out, prob, spec)
        if rename and "model" in out.columns:
            out = out.with_columns((pl.col("model") + suffix).alias("model"))
        return out

    @staticmethod
    def _apply_isotonic(frame: pl.DataFrame, prob: str, spec: dict[str, Any]) -> pl.DataFrame:
        by = list(spec.get("by", []))
        p = frame[prob].cast(pl.Float64).to_numpy().copy()
        result = p.copy()
        for entry in spec["maps"]:
            mask = ~np.isnan(p)
            for c in by:
                mask &= (frame[c].cast(pl.String) == entry["group"][c]).to_numpy()
            if mask.any():
                result[mask] = np.interp(p[mask], entry["x"], entry["y"])
        return frame.with_columns(pl.Series(prob, result, dtype=pl.Float64).fill_nan(None))

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
            "isotonic": self.isotonic,
            "meta": self.meta,
        }

    @classmethod
    def from_dict(cls, payload: dict[str, Any]) -> FrozenCalibration:
        if payload.get("format") != CALIBRATION_FORMAT:
            raise ValueError(f"not a calibration file (format={payload.get('format')!r})")
        return cls(
            levels=tuple(float(q) for q in payload["levels"]),
            by=tuple(payload["by"]),
            keys=tuple(payload["keys"]),
            min_count=int(payload["min_count"]),
            offsets=list(payload["offsets"]),
            isotonic=dict(payload.get("isotonic", {})),
            meta=dict(payload.get("meta", {})),
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
    "CAL_SUFFIX",
    "FrozenCalibration",
    "OnlineConformal",
    "OnlineIsotonic",
    "calibration_path_for",
    "quantile_column",
    "save_next_to",
    "sort_quantiles",
]
