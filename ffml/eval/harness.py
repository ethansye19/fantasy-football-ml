"""Walk-forward backtest harness (MODELING.md sections 2, 6 and 7; protocol in ffml/models/protocol.py).

:func:`run_walk_forward` drives any set of :class:`~ffml.models.protocol.WeeklyModel`
objects through the same schedule, so the structural chain, the transparent
baselines and the direct challenger are evaluated identically.

Mechanics
=========
For each season in order and each requested week that has regular-season
games, the model is refit when due and then predicts that week:

- ``model.fit((season, week))`` trains on rows strictly before the cutoff. A
  fit is due at the first scheduled week of every season and then every
  ``refit_every`` scheduled weeks within the season (the reported backtest
  uses 1, a refit before every week).
- ``model.predict(season, week, scoring_rules)`` returns the protocol frame;
  every frame is checked with :func:`~ffml.models.protocol.validate_predictions`,
  plus season, week, scoring and model labels, and cast to the contract types.

Each (model, season, week) frame is written as a part file
``<run>/parts/<model key>/<season>_wNN.parquet`` (temp file, then rename).
Rerunning the same ``run_id`` skips parts already written; when the first
missing week is not a refit point, the model is refit at the refit point that
week belongs to, so a resumed run follows the same fit schedule. Per-model fit
and predict seconds accumulate in ``parts/<key>/_timing.json`` across sessions.

Outcomes joined to every row
============================
From :func:`ffml.scoring.actuals.actual_statlines` scored with each
:class:`~ffml.scoring.rules.ScoringRules`:

``actual``
    points under the row's scoring (0 for a rostered player who did not play;
    null when the entity has no stat-line row that week);
``active``
    the entity played (``played``);
``boom_actual`` / ``bust_actual``
    1.0 or 0.0 from the entity's actual positional rank among all entities
    with a played row that season-week (same thresholds and tie rules as
    :mod:`ffml.sim.summarize`: boom uses the ``min`` rank, bust the ``max``
    rank); null for rows that did not play;
``pred_cdf_at_actual``
    the predictive CDF at the actual (PIT), by linear interpolation through
    the knots ``(lo, 0), (q05, 0.05), ..., (q95, 0.95), (hi, 1)`` where ``lo``
    and ``hi`` are the position's minimum and maximum observed score under the
    row's scoring in seasons before the row's season (``lo = min(lo, q05)``,
    ``hi = max(hi, q95)``). The CDF is 0 below ``lo`` and 1 above ``hi``.
    Where knots coincide (a point mass, such as several quantiles at zero)
    the mid-point of the jump is used;
``kickoff_utc`` / ``week_cutoff``
    the row's game kickoff and the first kickoff of its week (D-046), used by
    the calibrators to keep later games out of the calibration window;
``pos_min`` / ``pos_max``
    the PIT bounds of the row (position extremes under its scoring in seasons
    before its season), also used by the v3 quantile calibrator.

Calibration
===========
After the walk-forward, :class:`~ffml.models.calibration.OnlinePITQuantile`
(calibration v3, seed 0, aimed at the tie-aware coverage rule of D-085) and
:class:`~ffml.models.calibration.OnlineIsotonic` (``boom_prob`` and
``bust_prob`` when present) produce a calibrated copy of every model's rows
named ``<model>+cal``, each week using only earlier weeks of the run. The
first run season warms the calibration up; ``report_seasons`` (recorded in the
manifest) are the seasons whose metrics are reported. Calibrated quantiles
are clipped below at the position minimum used for the PIT. The isotonic maps
are per position with a pooled fallback (``cal_boom_source`` /
``cal_bust_source`` record which), and ``calibration_version`` is written to
the calibrated rows and the manifest. Every combine recomputes the
calibrated rows from the raw part files, so rerunning a finished run id
recalibrates it without refitting any model.

Output
======
``data/backtests/<run_id>/predictions.parquet`` holds raw and calibrated rows
of every model and scoring; ``_manifest.json`` records models (with wall time,
fit and predict seconds), seasons, weeks, report seasons, refit cadence,
scorings with hashes, git sha, feature version, input snapshot ids, row
counts and the file hash. :func:`load_backtest` returns the rows for one
scoring and the report seasons, which :func:`ffml.eval.metrics.evaluate`
accepts as they are (that function needs one row per model, entity and
season-week, so it is called once per scoring).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import time
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from datetime import UTC, datetime
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from ffml.config import PROJECT_ROOT, get_settings
from ffml.data.store import SnapshotStore
from ffml.holdout import assert_seasons_allowed
from ffml.models.calibration import (
    CAL_SUFFIX,
    CALIBRATION_VERSION,
    PROB_PAIRS,
    FrozenCalibration,
    OnlineConformal,
    OnlineIsotonic,
    OnlinePITQuantile,
    source_column,
)
from ffml.models.protocol import (
    OPTIONAL_COLUMNS,
    PREDICTION_COLUMNS,
    QUANTILE_LEVELS,
    validate_predictions,
)
from ffml.scoring.actuals import actual_statlines
from ffml.scoring.rules import ScoringRules
from ffml.sim.summarize import BOOM_BUST_THRESHOLDS

log = logging.getLogger(__name__)

QUANTILE_COLUMNS: tuple[str, ...] = tuple(
    f"pred_q{int(round(q * 100)):02d}" for q in QUANTILE_LEVELS
)
OUTCOME_COLUMNS: tuple[str, ...] = (
    "actual",
    "active",
    "boom_actual",
    "bust_actual",
    "pred_cdf_at_actual",
    "kickoff_utc",
    "week_cutoff",
    "pos_min",
    "pos_max",
)
STRING_COLUMNS: tuple[str, ...] = ("entity_id", "position", "team", "game_id", "scoring", "model")
FLOAT_OPTIONAL: tuple[str, ...] = (
    "boom_prob",
    "bust_prob",
    "active_prob",
    "pred_mean_uncond",
    "pred_q50_uncond",
)
MANIFEST = "_manifest.json"
PREDICTIONS = "predictions.parquet"
SESSIONS = "_sessions.jsonl"
TIMING = "_timing.json"
PARAMS = "_params.json"


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def backtests_root() -> Path:
    return get_settings().data_dir / "backtests"


def new_run_id(prefix: str = "run") -> str:
    return f"{prefix}_{datetime.now(UTC).strftime('%Y%m%dT%H%M%SZ')}"


def model_key(model: Any) -> str:
    """Unique key of a model within a run (``model.key`` when present, else ``model.name``)."""
    return str(getattr(model, "key", None) or model.name)


def _slug(text: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]+", "_", text)


def resolve_scorings(
    scorings: Iterable[str] | Mapping[str, ScoringRules],
) -> dict[str, ScoringRules]:
    """``("ppr", "half")`` (presets) or ``{"name": ScoringRules}`` to a name -> rules mapping."""
    if isinstance(scorings, Mapping):
        return {str(k): v for k, v in scorings.items()}
    return {str(name): ScoringRules.from_preset(str(name)) for name in scorings}


def _write_parquet_atomic(frame: pl.DataFrame, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.write_parquet(tmp)
    os.replace(tmp, path)


def _write_json_atomic(payload: Mapping[str, Any], path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def part_path(run_dir: Path, key: str, season: int, week: int) -> Path:
    return run_dir / "parts" / _slug(key) / f"{season}_w{week:02d}.parquet"


# --------------------------------------------------------------------------- #
# Schedule
# --------------------------------------------------------------------------- #


def week_cutoffs(actuals: pl.DataFrame) -> dict[tuple[int, int], Any]:
    """First kickoff per regular-season ``(season, week)``."""
    frame = actuals.group_by(["season", "week"]).agg(pl.col("kickoff_utc").min().alias("cutoff"))
    return {(int(s), int(w)): c for s, w, c in frame.iter_rows()}


def build_schedule(
    seasons: Sequence[int], weeks: Iterable[int], cutoffs: Mapping[tuple[int, int], Any]
) -> list[tuple[int, int]]:
    """Requested ``(season, week)`` pairs that have regular-season games, in order."""
    wanted = sorted({int(w) for w in weeks})
    return [(s, w) for s in sorted(seasons) for w in wanted if (s, w) in cutoffs]


def refit_points(schedule: Sequence[tuple[int, int]], refit_every: int) -> list[int]:
    """For each schedule index, the index of the week whose cutoff the model is fitted at."""
    if refit_every < 1:
        raise ValueError("refit_every must be at least 1")
    out: list[int] = []
    season_start = 0
    for i, (season, _) in enumerate(schedule):
        if i == 0 or season != schedule[i - 1][0]:
            season_start = i
        offset = (i - season_start) % refit_every
        out.append(i - offset)
    return out


# --------------------------------------------------------------------------- #
# Frame checks
# --------------------------------------------------------------------------- #


def _empty_prediction_frame() -> pl.DataFrame:
    schema: dict[str, pl.DataType] = {c: pl.String() for c in STRING_COLUMNS}
    schema.update({"season": pl.Int32(), "week": pl.Int32()})
    schema.update({c: pl.Float64() for c in ("pred_mean", *QUANTILE_COLUMNS)})
    return pl.DataFrame(schema=schema).select(PREDICTION_COLUMNS)


def check_frame(
    frame: pl.DataFrame,
    model: Any,
    season: int,
    week: int,
    scorings: Mapping[str, ScoringRules],
) -> pl.DataFrame:
    """Validate one predicted week against the contract and the request; cast to contract types."""
    if frame is None or frame.height == 0:
        return _empty_prediction_frame()
    validate_predictions(frame)
    name = str(model.name)
    problems = []
    if set(frame["season"].cast(pl.Int64).unique().to_list()) != {season}:
        problems.append("season")
    if set(frame["week"].cast(pl.Int64).unique().to_list()) != {week}:
        problems.append("week")
    bad_scoring = set(frame["scoring"].unique().to_list()) - set(scorings)
    if bad_scoring:
        problems.append(f"unrequested scorings {sorted(bad_scoring)}")
    if set(frame["model"].unique().to_list()) != {name}:
        problems.append(f"model label (expected {name!r})")
    nulls = [c for c in ("entity_id", "position", "scoring", "model") if frame[c].null_count()]
    if nulls:
        problems.append(f"null keys {nulls}")
    if problems:
        raise ValueError(
            f"{model_key(model)} {season} week {week}: bad prediction frame: {problems}"
        )
    optional = [c for c in OPTIONAL_COLUMNS if c in frame.columns]
    casts = [pl.col(c).cast(pl.String) for c in STRING_COLUMNS]
    casts += [pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)]
    casts += [pl.col(c).cast(pl.Float64) for c in ("pred_mean", *QUANTILE_COLUMNS)]
    casts += [pl.col(c).cast(pl.Float64) for c in optional if c in FLOAT_OPTIONAL]
    casts += [pl.col(c).cast(pl.String) for c in optional if c == "drivers"]
    return frame.with_columns(casts).select([*PREDICTION_COLUMNS, *optional])


# --------------------------------------------------------------------------- #
# Outcomes and PIT
# --------------------------------------------------------------------------- #


def outcome_table(
    actuals: pl.DataFrame,
    seasons: Sequence[int],
    scorings: Mapping[str, ScoringRules],
    thresholds: Mapping[str, tuple[int, int]] = BOOM_BUST_THRESHOLDS,
) -> pl.DataFrame:
    """Per entity, season-week and scoring: actual, active, boom/bust outcome, position bounds.

    ``pos_min`` / ``pos_max`` are the position's minimum and maximum played
    score under the scoring in seasons before the row's season.
    """
    rows = actuals.sort("played", descending=True).unique(
        ["entity_id", "season", "week"], keep="first", maintain_order=True
    )
    thr = pl.DataFrame(
        {
            "position": list(thresholds),
            "boom_top": [int(v[0]) for v in thresholds.values()],
            "bust_outside": [int(v[1]) for v in thresholds.values()],
        },
        schema={"position": pl.String, "boom_top": pl.Int64, "bust_outside": pl.Int64},
    )
    frames = []
    for name, rules in scorings.items():
        scored = rows.select(
            "entity_id",
            "position",
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "kickoff_utc",
            pl.col("played").fill_null(False).alias("active"),
            rules.score(rows).alias("actual"),
        ).with_columns(pl.lit(name).alias("scoring"))
        bounds = []
        for season in sorted(set(seasons)):
            past = scored.filter(pl.col("active") & (pl.col("season") < season))
            if past.height == 0:
                continue
            bounds.append(
                past.group_by("position")
                .agg(
                    pl.col("actual").min().alias("pos_min"), pl.col("actual").max().alias("pos_max")
                )
                .with_columns(pl.lit(season, dtype=pl.Int32).alias("season"))
            )
        target = scored.filter(pl.col("season").is_in(list(seasons)))
        played = (
            target.filter(pl.col("active"))
            .with_columns(
                pl.col("actual")
                .rank(method="min", descending=True)
                .over(["season", "week", "position"])
                .alias("_rank_min"),
                pl.col("actual")
                .rank(method="max", descending=True)
                .over(["season", "week", "position"])
                .alias("_rank_max"),
            )
            .join(thr, on="position", how="left")
        )
        played = played.select(
            "entity_id",
            "season",
            "week",
            pl.when(pl.col("boom_top").is_null())
            .then(None)
            .otherwise((pl.col("_rank_min") <= pl.col("boom_top")).cast(pl.Float64))
            .alias("boom_actual"),
            pl.when(pl.col("bust_outside").is_null())
            .then(None)
            .otherwise((pl.col("_rank_max") > pl.col("bust_outside")).cast(pl.Float64))
            .alias("bust_actual"),
        )
        target = target.join(played, on=["entity_id", "season", "week"], how="left")
        if bounds:
            target = target.join(
                pl.concat(bounds, how="vertical_relaxed"), on=["season", "position"], how="left"
            )
        else:
            target = target.with_columns(
                pl.lit(None, dtype=pl.Float64).alias("pos_min"),
                pl.lit(None, dtype=pl.Float64).alias("pos_max"),
            )
        frames.append(target.drop("position"))
    return pl.concat(frames, how="vertical_relaxed")


def position_bounds(
    actuals: pl.DataFrame, seasons: Sequence[int], scorings: Mapping[str, ScoringRules]
) -> pl.DataFrame:
    """``season, scoring, position, pos_min, pos_max`` from played rows of seasons before each season.

    The same bounds :func:`outcome_table` joins to every row (and the PIT and
    the v3 calibrator use); for a season without rows yet (the holdout or a
    live season) they are the extremes of every season in ``actuals``
    before it.
    """
    played = actuals.filter(pl.col("played").fill_null(False))
    out = []
    for name, rules in scorings.items():
        scored = played.select(
            "position", pl.col("season").cast(pl.Int32), rules.score(played).alias("actual")
        )
        for season in sorted({int(s) for s in seasons}):
            past = scored.filter(pl.col("season") < season)
            if past.height == 0:
                continue
            out.append(
                past.group_by("position")
                .agg(
                    pl.col("actual").min().alias("pos_min"), pl.col("actual").max().alias("pos_max")
                )
                .with_columns(
                    pl.lit(season, dtype=pl.Int32).alias("season"), pl.lit(name).alias("scoring")
                )
                .select("season", "scoring", "position", "pos_min", "pos_max")
            )
    if not out:
        return pl.DataFrame(
            schema={
                "season": pl.Int32,
                "scoring": pl.String,
                "position": pl.String,
                "pos_min": pl.Float64,
                "pos_max": pl.Float64,
            }
        )
    return pl.concat(out, how="vertical_relaxed").sort(["season", "scoring", "position"])


def fill_bounds(joined: pl.DataFrame, bounds: pl.DataFrame) -> pl.DataFrame:
    """Fill ``pos_min`` / ``pos_max`` where the row has no outcome row, from the position table.

    Rows with an outcome keep the bounds joined through :func:`outcome_table`
    (unchanged, so the raw PIT is unchanged); rows without one (no stat line
    that week) get the bounds of their predicted position, which the v3
    calibrator needs to recalibrate their quantiles.
    """
    b = bounds.select(
        pl.col("season").cast(pl.Int32),
        pl.col("scoring").cast(pl.String),
        pl.col("position").cast(pl.String),
        pl.col("pos_min").alias("_bmin"),
        pl.col("pos_max").alias("_bmax"),
    )
    out = joined.join(b, on=["season", "scoring", "position"], how="left", maintain_order="left")
    return out.with_columns(
        pl.coalesce("pos_min", "_bmin").alias("pos_min"),
        pl.coalesce("pos_max", "_bmax").alias("pos_max"),
    ).drop("_bmin", "_bmax")


def pit_values(
    actual: np.ndarray, quantiles: np.ndarray, lower: np.ndarray, upper: np.ndarray
) -> np.ndarray:
    """Mid-point PIT through the knots ``(lower, 0), (q, level)..., (upper, 1)`` (see module docstring).

    ``quantiles`` is ``(n, 7)`` for the protocol levels; NaN bounds drop that tail.
    """
    y = np.asarray(actual, dtype=float)
    q = np.asarray(quantiles, dtype=float)
    lo = np.asarray(lower, dtype=float)
    hi = np.asarray(upper, dtype=float)
    lo = np.where(np.isnan(lo), q[:, 0], np.minimum(lo, q[:, 0]))
    hi = np.where(np.isnan(hi), q[:, -1], np.maximum(hi, q[:, -1]))
    knots = np.column_stack([lo, q, hi])
    levels = np.array([0.0, *QUANTILE_LEVELS, 1.0])
    k_last = knots.shape[1] - 1
    out = np.full(y.shape, np.nan)
    ok = ~np.isnan(y) & ~np.isnan(knots).any(axis=1)
    if not ok.any():
        return out
    yy = y[ok]
    x = knots[ok]
    rows = np.arange(x.shape[0])
    le = (x <= yy[:, None]).sum(axis=1)
    lt = (x < yy[:, None]).sum(axis=1)

    def interp(left: np.ndarray) -> np.ndarray:
        left = np.clip(left, 0, k_last - 1)
        x0, x1 = x[rows, left], x[rows, left + 1]
        p0, p1 = levels[left], levels[left + 1]
        width = np.where(x1 > x0, x1 - x0, 1.0)
        return p0 + (p1 - p0) * np.clip((yy - x0) / width, 0.0, 1.0)

    right = np.where(le == 0, 0.0, np.where(le > k_last, 1.0, interp(le - 1)))
    left = np.where(lt == 0, 0.0, np.where(lt > k_last, 1.0, interp(lt - 1)))
    out[ok] = 0.5 * (left + right)
    return out


def with_pit(frame: pl.DataFrame) -> pl.DataFrame:
    """Add ``pred_cdf_at_actual`` from the quantiles, ``actual`` and ``pos_min``/``pos_max``."""
    if frame.height == 0:
        return frame.with_columns(pl.lit(None, dtype=pl.Float64).alias("pred_cdf_at_actual"))
    q = frame.select(QUANTILE_COLUMNS).cast(pl.Float64).to_numpy()
    values = pit_values(
        frame["actual"].cast(pl.Float64).to_numpy(),
        q,
        frame["pos_min"].cast(pl.Float64).to_numpy(),
        frame["pos_max"].cast(pl.Float64).to_numpy(),
    )
    return frame.with_columns(
        pl.Series("pred_cdf_at_actual", values, dtype=pl.Float64).fill_nan(None)
    )


def join_outcomes(
    raw: pl.DataFrame, outcomes: pl.DataFrame, cutoffs: Mapping[tuple[int, int], Any]
) -> pl.DataFrame:
    """Raw prediction rows plus outcomes, position bounds and the week cutoff."""
    cut = pl.DataFrame(
        {
            "season": [k[0] for k in cutoffs],
            "week": [k[1] for k in cutoffs],
            "week_cutoff": list(cutoffs.values()),
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32},
    )
    out = raw.join(outcomes, on=["entity_id", "season", "week", "scoring"], how="left").join(
        cut, on=["season", "week"], how="left"
    )
    return out.with_columns(pl.col("active").fill_null(False))


# --------------------------------------------------------------------------- #
# Walk-forward
# --------------------------------------------------------------------------- #


def _read_timing(path: Path) -> dict[str, Any]:
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return {"fit_seconds": 0.0, "predict_seconds": 0.0, "n_fits": 0, "parts": 0, "rows": 0}


def _run_model(
    model: Any,
    run_dir: Path,
    schedule: Sequence[tuple[int, int]],
    fit_index: Sequence[int],
    rules: Mapping[str, ScoringRules],
) -> dict[str, Any]:
    key = model_key(model)
    mdir = run_dir / "parts" / _slug(key)
    timing_path = mdir / TIMING
    timing = _read_timing(timing_path)
    pending = [i for i, (s, w) in enumerate(schedule) if not part_path(run_dir, key, s, w).exists()]
    if not pending:
        log.info("%s: all %d weeks already written, skipping", key, len(schedule))
        return timing
    log.info("%s: %d of %d weeks to predict", key, len(pending), len(schedule))
    fitted: int | None = None
    started = time.perf_counter()
    for n_done, i in enumerate(pending, start=1):
        season, week = schedule[i]
        fit_s = 0.0
        if fitted != fit_index[i]:
            cutoff = schedule[fit_index[i]]
            t0 = time.perf_counter()
            model.fit(cutoff)
            fit_s = time.perf_counter() - t0
            fitted = fit_index[i]
            timing["n_fits"] += 1
        t0 = time.perf_counter()
        frame = model.predict(season, week, rules)
        predict_s = time.perf_counter() - t0
        frame = check_frame(frame, model, season, week, rules)
        _write_parquet_atomic(frame, part_path(run_dir, key, season, week))
        timing["fit_seconds"] += fit_s
        timing["predict_seconds"] += predict_s
        timing["parts"] += 1
        timing["rows"] += frame.height
        _write_json_atomic(timing, timing_path)
        elapsed = time.perf_counter() - started
        eta = elapsed / n_done * (len(pending) - n_done)
        log.info(
            "%s %d w%02d: fit %.1fs predict %.1fs rows %d [%d/%d, eta %.0fs]",
            key,
            season,
            week,
            fit_s,
            predict_s,
            frame.height,
            n_done,
            len(pending),
            eta,
        )
    _store_params(model, mdir / PARAMS)
    return timing


def _store_params(model: Any, path: Path) -> None:
    """Persist ``model.params`` (merged with earlier sessions) so the manifest can report it."""
    params = getattr(model, "params", None)
    if not isinstance(params, Mapping):
        return
    merged: dict[str, Any] = json.loads(path.read_text(encoding="utf-8")) if path.exists() else {}
    for k, v in params.items():
        if isinstance(v, Mapping) and isinstance(merged.get(k), Mapping):
            merged[k] = {**merged[k], **v}
        elif v not in (None, {}, []) or k not in merged:
            merged[k] = v
    _write_json_atomic(merged, path)


def _model_params(model: Any, run_dir: Path) -> Any:
    path = run_dir / "parts" / _slug(model_key(model)) / PARAMS
    if path.exists():
        return json.loads(path.read_text(encoding="utf-8"))
    return getattr(model, "params", None)


def _history(seasons: Sequence[int]) -> list[int]:
    """Every season a run reads: the first data season through the last requested one."""
    return list(range(min(get_settings().first_season, min(seasons)), max(seasons) + 1))


def _load_actuals(
    seasons: Sequence[int], purpose: str, store: SnapshotStore | None
) -> pl.DataFrame:
    settings = get_settings()
    history = list(range(min(settings.first_season, min(seasons)), max(seasons) + 1))
    return actual_statlines(history, purpose, store=store)


def _snapshot_ids(store: SnapshotStore | None) -> dict[str, Any]:
    settings = get_settings()
    out: dict[str, Any] = {"processed": {}, "features": {}}
    try:
        proc = store or SnapshotStore(settings.processed_dir)
        for ds in ("fact_player_game", "fact_dst_game", "dim_game", "dim_player"):
            out["processed"][ds] = proc.latest_id(ds)
        feat = SnapshotStore(settings.features_dir)
        for ds in ("player_week", "team_week", "dst_week"):
            out["features"][ds] = feat.latest_id(ds)
    except (OSError, ValueError) as exc:  # pragma: no cover - informational only
        out["error"] = str(exc)
    return out


def _feature_version(seasons: Sequence[int], purpose: str) -> str | None:
    try:
        from ffml.features.access import load_features

        frame = load_features("player_week", [min(seasons)], purpose, columns_=["feature_version"])
        values = sorted({str(v) for v in frame["feature_version"].unique().to_list()})
        return ",".join(values) if values else None
    except Exception:  # noqa: BLE001 - informational only; tests run without features
        return None


def combine_parts(run_dir: Path, keys: Sequence[str]) -> pl.DataFrame:
    """Every written part of ``keys``, with a ``_key`` column (the model key; never written out)."""
    frames = []
    for key in keys:
        mdir = run_dir / "parts" / _slug(key)
        for path in sorted(mdir.glob("*_w*.parquet")):
            part = pl.read_parquet(path)
            if part.height:
                frames.append(part.with_columns(pl.lit(key).alias("_key")))
    if not frames:
        return _empty_prediction_frame().with_columns(pl.lit(None, dtype=pl.String).alias("_key"))
    return pl.concat(frames, how="diagonal_relaxed")


#: seed of the calibrator's randomized PIT draws (D-088: seed 0 is the frozen choice)
CALIBRATION_SEED = 0


def default_quantile_calibrator(seed: int = CALIBRATION_SEED) -> OnlinePITQuantile:
    return OnlinePITQuantile(seed=seed)


def calibrated_rows(
    joined: pl.DataFrame,
    quantiles: OnlinePITQuantile | OnlineConformal | None = None,
    isotonic: OnlineIsotonic | None = None,
    *,
    version: str = CALIBRATION_VERSION,
) -> pl.DataFrame:
    """``<model>+cal`` rows from raw rows with outcomes (each week uses earlier weeks only).

    The default is calibration v3: :class:`OnlinePITQuantile` with seed 0 for
    the quantiles (bounds ``pos_min`` / ``pos_max``) and per-position
    :class:`OnlineIsotonic` for boom and bust.
    """
    quantiles = quantiles or default_quantile_calibrator()
    isotonic = isotonic or OnlineIsotonic()
    if isinstance(quantiles, OnlinePITQuantile):
        cal = quantiles.transform(joined, lower_bound="pos_min", upper_bound="pos_max")
    else:
        cal = quantiles.transform(joined, lower_bound="pos_min")
    cal = isotonic.transform(cal)
    cal = cal.with_columns(pl.lit(version).alias("calibration_version"))
    return with_pit(cal)


# --------------------------------------------------------------------------- #
# FIXED calibration: frozen calibrators (D-095)
# --------------------------------------------------------------------------- #

FROZEN_FORMAT = "ffml.frozen_calibrators/1"
FROZEN_MANIFEST = "FROZEN.json"
CALIBRATION_MODES: tuple[str, ...] = ("online", "frozen", "none")


def frozen_manifest_path() -> Path:
    """``artifacts/calibration/FROZEN.json``: the pre-registered calibrators of the holdout."""
    return get_settings().artifacts_dir / "calibration" / FROZEN_MANIFEST


def _rel(path: Path | str) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return p.resolve().as_posix()


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def load_frozen_calibrators(
    manifest: Path | str | None, keys: Sequence[str]
) -> tuple[dict[str, tuple[FrozenCalibration, str]], dict[str, Any]]:
    """Frozen calibrator and SHA-256 per model key, after integrity checks; raises on any gap.

    Every key must have an entry, every file must exist and hash to the
    SHA-256 recorded in the manifest. Returns the calibrators and the
    manifest record for the run manifest (its own hash and each calibrator's).
    """
    path = Path(manifest) if manifest is not None else frozen_manifest_path()
    if not path.is_file():
        raise FileNotFoundError(f"frozen calibrator manifest {path} does not exist")
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != FROZEN_FORMAT:
        raise ValueError(
            f"{path} is not a frozen calibrator manifest (format={payload.get('format')!r})"
        )
    entries = payload.get("calibrators", {})
    missing = [k for k in keys if k not in entries]
    if missing:
        raise ValueError(
            f"no frozen calibrator for models {missing} in {path}; refusing to run uncalibrated"
        )
    out: dict[str, tuple[FrozenCalibration, str]] = {}
    record: dict[str, Any] = {"path": _rel(path), "sha256": _sha256(path), "calibrators": {}}
    for key in keys:
        entry = entries[key]
        file = _resolve(entry["path"])
        if not file.is_file():
            raise FileNotFoundError(f"frozen calibrator for {key} missing: {file}")
        sha = _sha256(file)
        if sha != entry["sha256"]:
            raise ValueError(
                f"frozen calibrator for {key} changed: {sha} != recorded {entry['sha256']}"
            )
        frozen = FrozenCalibration.load(file)
        out[key] = (frozen, sha)
        record["calibrators"][key] = {
            "path": entry["path"],
            "sha256": sha,
            "label": f"frozen:{sha[:12]}",
            "source_version": frozen.meta.get("calibration_version"),
        }
    return out, record


def frozen_rows(
    joined: pl.DataFrame, calibrators: Mapping[str, tuple[FrozenCalibration, str]]
) -> pl.DataFrame:
    """``<model>+cal`` rows: each model key's frozen calibrator applied to its raw rows, unchanged.

    No online fitting of any kind; ``calibration_version`` is
    ``frozen:<first 12 hex of the calibrator's SHA-256>``.
    """
    out = []
    for key, part in joined.partition_by("_key", as_dict=True, maintain_order=True).items():
        key = key[0] if isinstance(key, tuple) else key
        if key not in calibrators:
            raise ValueError(f"no frozen calibrator for model key {key!r}")
        frozen, sha = calibrators[key]
        cal = frozen.apply(part, lower_bound="pos_min", upper_bound="pos_max", rename=True)
        out.append(cal.with_columns(pl.lit(f"frozen:{sha[:12]}").alias("calibration_version")))
    if not out:
        return joined.clear()
    return with_pit(pl.concat(out, how="diagonal_relaxed"))


def freeze_calibrators(
    run: str | Path,
    keys: Sequence[str],
    out_dir: Path | str,
    seasons: Sequence[int] = (2022, 2023, 2024),
    *,
    seed: int = CALIBRATION_SEED,
    existing: Mapping[str, Path | str] | None = None,
    actuals: pl.DataFrame | None = None,
    purpose: str = "backtest",
    scorings: Iterable[str] | Mapping[str, ScoringRules] | None = None,
) -> Path:
    """Fit and save the FIXED calibrator of every model key of a backtest run; write ``FROZEN.json``.

    Each key's raw rows (its model name and scorings, read from its part
    files) of ``seasons`` are fitted with the v3 quantile method
    (:class:`OnlinePITQuantile`, ``seed``) and per-position isotonic boom/bust,
    and saved as ``<out_dir>/<key>/calibration.json`` with the position bounds
    for the season after ``seasons`` (played-score extremes of every season
    up to ``max(seasons)``). ``existing`` maps keys to calibrator files that
    are listed as they are (the chain candidate's file). Returns the manifest path.
    """
    run_dir = resolve_run_dir(run)
    out_dir = Path(out_dir)
    seasons = sorted({int(s) for s in seasons})
    existing = dict(existing or {})
    frame = pl.read_parquet(run_dir / PREDICTIONS).filter(pl.col("season").is_in(seasons))
    if "pos_min" not in frame.columns:
        raise ValueError(f"{run_dir} predictions lack pos_min/pos_max; recombine the run first")
    run_manifest = read_manifest(run_dir)
    scorings = resolve_scorings(
        scorings if scorings is not None else tuple(run_manifest["scorings"])
    )
    if actuals is None:
        settings = get_settings()
        actuals = actual_statlines(range(settings.first_season, max(seasons) + 1), purpose)
    next_season = max(seasons) + 1
    bounds = position_bounds(
        actuals.filter(pl.col("season") <= max(seasons)), [next_season], scorings
    )
    calibrators: dict[str, Any] = {}
    for key in keys:
        if key in existing:
            file = Path(existing[key])
            frozen = FrozenCalibration.load(file)
            calibrators[key] = {
                "path": _rel(file),
                "sha256": _sha256(file),
                "model": frozen.meta.get("model"),
                "calibration_version": frozen.meta.get("calibration_version"),
                "fit_rows": frozen.meta.get("rows"),
                "existing": True,
            }
            continue
        parts = sorted((run_dir / "parts" / _slug(key)).glob("*_w*.parquet"))
        sample = next((pl.read_parquet(p) for p in parts if pl.read_parquet(p).height), None)
        if sample is None:
            raise ValueError(f"run {run_dir} has no rows for model key {key!r}")
        name = sample["model"][0]
        key_scorings = sorted(
            set().union(
                *(set(pl.read_parquet(p, columns=["scoring"])["scoring"].unique()) for p in parts)
            )
        )
        rows = frame.filter((pl.col("model") == name) & pl.col("scoring").is_in(key_scorings))
        cal = OnlinePITQuantile(seed=seed)
        frozen = cal.fit_final(
            rows,
            isotonic=OnlineIsotonic(),
            meta={
                "backtest_run_id": run_manifest["run_id"],
                "predictions_sha256": run_manifest["files"][PREDICTIONS],
                "model_key": key,
                "fit_seasons": seasons,
                "seed": seed,
                "mode": "fixed",
                "bounds_note": f"stored bounds: played-score extremes through {max(seasons)}",
            },
            bounds=bounds.filter(pl.col("scoring").is_in(key_scorings)).drop("season"),
        )
        file = out_dir / _slug(key) / "calibration.json"
        frozen.save(file)
        calibrators[key] = {
            "path": _rel(file),
            "sha256": _sha256(file),
            "model": name,
            "scorings": key_scorings,
            "calibration_version": frozen.meta["calibration_version"],
            "fit_rows": frozen.meta["rows"],
            "existing": False,
        }
    payload = {
        "format": FROZEN_FORMAT,
        "mode": "fixed",
        "created_at": datetime.now(UTC).isoformat(),
        "git": git_sha(),
        "fitted_on": {
            "run_id": run_manifest["run_id"],
            "predictions_sha256": run_manifest["files"][PREDICTIONS],
            "seasons": seasons,
            "seed": seed,
            "quantiles": repr(OnlinePITQuantile(seed=seed)),
            "isotonic": repr(OnlineIsotonic()),
        },
        "calibrators": calibrators,
    }
    path = out_dir / FROZEN_MANIFEST
    _write_json_atomic(payload, path)
    return path


#: calibration bookkeeping columns (null on raw rows)
CAL_COLUMNS: tuple[str, ...] = (
    "cal_n",
    "cal_q_source",
    *(source_column(p) for p, _ in PROB_PAIRS),
    "calibration_version",
)


def _finish_frame(frame: pl.DataFrame) -> pl.DataFrame:
    optional = [c for c in OPTIONAL_COLUMNS if c in frame.columns]
    cols = [*PREDICTION_COLUMNS, *optional, *OUTCOME_COLUMNS, *CAL_COLUMNS]
    for c in cols:
        if c not in frame.columns:
            frame = frame.with_columns(pl.lit(None).alias(c))
    return (
        frame.select(cols)
        .with_columns(
            pl.col("cal_n").cast(pl.Int64),
            *[pl.col(c).cast(pl.String) for c in CAL_COLUMNS if c != "cal_n"],
        )
        .sort(["model", "scoring", "season", "week", "position", "entity_id"])
    )


def run_walk_forward(
    models: Sequence[Any],
    seasons: Iterable[int],
    weeks: Iterable[int] = range(1, 19),
    scorings: Iterable[str] | Mapping[str, ScoringRules] = ("ppr", "half", "std"),
    refit_every: int = 1,
    run_id: str | None = None,
    purpose: str = "backtest",
    *,
    report_seasons: Iterable[int] | None = None,
    config_name: str | None = None,
    calibrate: bool = True,
    combine: bool = True,
    root: Path | str | None = None,
    store: SnapshotStore | None = None,
    actuals: pl.DataFrame | None = None,
    calibration: str | None = None,
    frozen_manifest: Path | str | None = None,
) -> Path:
    """Run the walk-forward backtest and return the run directory (see module docstring).

    ``actuals`` (a stat-line frame covering every history season) replaces the
    store read, for tests. ``combine=False`` only writes part files (parallel
    workers); a later call with ``combine=True`` skips the written parts and
    writes ``predictions.parquet`` and the manifest.

    ``calibration`` is ``online`` (walk-forward v3, the default when
    ``calibrate``), ``none``, or ``frozen`` (FIXED, D-095): every model key's
    calibrator from ``frozen_manifest`` (default
    ``artifacts/calibration/FROZEN.json``) is applied unchanged to its raw rows
    of every week, with no online fitting and no warm-up. A key without a
    frozen calibrator, a missing file or a changed hash stops the run before
    any model is fitted.
    """
    wall_start = time.perf_counter()
    started_at = datetime.now(UTC).isoformat()
    git_at_start = git_sha()
    seasons = sorted({int(s) for s in seasons})
    if not seasons:
        raise ValueError("seasons is empty")
    # the guard covers every season read, history included (2026 reads 2025 as history)
    assert_seasons_allowed(_history(seasons), purpose)
    if refit_every < 1:
        raise ValueError("refit_every must be at least 1")
    mode = calibration or ("online" if calibrate else "none")
    if mode not in CALIBRATION_MODES:
        raise ValueError(f"calibration must be one of {CALIBRATION_MODES}, got {mode!r}")
    rules = resolve_scorings(scorings)
    if not rules:
        raise ValueError("scorings is empty")
    keys = [model_key(m) for m in models]
    if len(set(keys)) != len(keys):
        raise ValueError(f"model keys must be unique within a run: {keys}")
    frozen_calibrators: dict[str, tuple[FrozenCalibration, str]] = {}
    frozen_record: dict[str, Any] | None = None
    if mode == "frozen":
        frozen_calibrators, frozen_record = load_frozen_calibrators(frozen_manifest, keys)
    report = (
        sorted({int(s) for s in report_seasons})
        if report_seasons is not None
        else (seasons[1:] if len(seasons) > 1 and mode == "online" else list(seasons))
    )
    if not set(report) <= set(seasons):
        raise ValueError(f"report_seasons {report} are not all in seasons {seasons}")
    run_id = run_id or new_run_id(config_name or "run")
    run_dir = Path(root) / run_id if root is not None else backtests_root() / run_id
    run_dir.mkdir(parents=True, exist_ok=True)

    if actuals is None:
        actuals = _load_actuals(seasons, purpose, store)
    cutoffs = week_cutoffs(actuals.filter(pl.col("season").is_in(seasons)))
    schedule = build_schedule(seasons, weeks, cutoffs)
    if not schedule:
        raise ValueError("no scheduled regular-season weeks for the requested seasons and weeks")
    fit_index = refit_points(schedule, refit_every)
    log.info(
        "backtest %s: %d models x %d weeks (%s to %s), scorings %s, refit_every %d",
        run_id,
        len(models),
        len(schedule),
        schedule[0],
        schedule[-1],
        list(rules),
        refit_every,
    )

    timings: dict[str, dict[str, Any]] = {}
    for model in models:
        t0 = time.perf_counter()
        timings[model_key(model)] = _run_model(model, run_dir, schedule, fit_index, rules)
        log.info("%s done in %.1fs this session", model_key(model), time.perf_counter() - t0)

    session = {
        "started_at": started_at,
        "finished_at": datetime.now(UTC).isoformat(),
        "git": git_at_start,
        "git_at_end": git_sha(),
        "pid": os.getpid(),
        "models": keys,
        "combine": combine,
        "seconds": round(time.perf_counter() - wall_start, 3),
    }
    with (run_dir / SESSIONS).open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(session) + "\n")
    if not combine:
        return run_dir

    t_comb = time.perf_counter()
    raw = combine_parts(run_dir, keys)
    outcomes = outcome_table(actuals, seasons, rules)
    joined = fill_bounds(
        join_outcomes(raw, outcomes, cutoffs), position_bounds(actuals, seasons, rules)
    )
    joined = with_pit(joined)
    frames = [joined]
    if mode == "online" and joined.height:
        frames.append(calibrated_rows(joined))
    elif mode == "frozen" and joined.height:
        frames.append(frozen_rows(joined, frozen_calibrators))
    final = _finish_frame(pl.concat(frames, how="diagonal_relaxed"))
    pred_path = run_dir / PREDICTIONS
    _write_parquet_atomic(final, pred_path)
    combine_seconds = time.perf_counter() - t_comb

    sessions = [
        json.loads(line)
        for line in (run_dir / SESSIONS).read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]
    by_model = final.group_by("model").len().sort("model")
    manifest = {
        "run_id": run_id,
        "config": config_name,
        "created_at": sessions[0]["started_at"] if sessions else started_at,
        "written_at": datetime.now(UTC).isoformat(),
        "git": git_sha(),
        "git_sessions": sorted(
            {s["git"] for s in sessions} | {s.get("git_at_end", s["git"]) for s in sessions}
        ),
        "purpose": purpose,
        "seasons": seasons,
        "weeks": sorted({w for _, w in schedule}),
        "schedule_weeks": len(schedule),
        "report_seasons": report,
        "warmup_seasons": [s for s in seasons if s not in report],
        "refit_every": refit_every,
        "scorings": {name: r.scoring_hash for name, r in rules.items()},
        "feature_version": _feature_version(seasons, purpose),
        "input_snapshots": _snapshot_ids(store),
        "calibration_version": (
            CALIBRATION_VERSION if mode == "online" else "frozen" if mode == "frozen" else None
        ),
        "calibration": {
            "mode": mode,
            "applied": mode != "none",
            "frozen_manifest": frozen_record,
            "version": CALIBRATION_VERSION,
            "suffix": CAL_SUFFIX,
            "quantiles": repr(default_quantile_calibrator()),
            "seed": CALIBRATION_SEED,
            "pit_bounds": "pos_min / pos_max: the position's played-score extremes in seasons before the row's",
            "isotonic": repr(OnlineIsotonic()),
            "window": "expanding over earlier weeks of the run, games before the week's first kickoff",
        },
        "models": [
            {
                "key": model_key(m),
                "name": str(m.name),
                "class": f"{type(m).__module__}.{type(m).__qualname__}",
                "repr": repr(m),
                "params": _model_params(m, run_dir),
                "wall_time_seconds": round(
                    timings[model_key(m)]["fit_seconds"] + timings[model_key(m)]["predict_seconds"],
                    3,
                ),
                **{
                    k: (round(v, 3) if isinstance(v, float) else v)
                    for k, v in timings[model_key(m)].items()
                },
            }
            for m in models
        ],
        "rows": {
            "total": final.height,
            "raw": final.filter(~pl.col("model").str.ends_with(CAL_SUFFIX)).height,
            "calibrated": final.filter(pl.col("model").str.ends_with(CAL_SUFFIX)).height,
            "by_model": dict(by_model.iter_rows()),
        },
        "combine_seconds": round(combine_seconds, 3),
        "sessions": sessions,
        "files": {PREDICTIONS: _sha256(pred_path)},
    }
    _write_json_atomic(manifest, run_dir / MANIFEST)
    log.info(
        "backtest %s written: %s (%d rows, combine %.1fs)",
        run_id,
        pred_path,
        final.height,
        combine_seconds,
    )
    return run_dir


# --------------------------------------------------------------------------- #
# Reading results
# --------------------------------------------------------------------------- #


def resolve_run_dir(run: str | Path) -> Path:
    path = Path(run)
    if path.is_dir():
        return path
    return backtests_root() / str(run)


def read_manifest(run: str | Path) -> dict[str, Any]:
    return json.loads((resolve_run_dir(run) / MANIFEST).read_text(encoding="utf-8"))


def load_backtest(
    run: str | Path,
    scoring: str = "ppr",
    *,
    report_only: bool = True,
    models: Sequence[str] | None = None,
) -> pl.DataFrame:
    """Rows of one scoring (and the manifest's report seasons) ready for :func:`ffml.eval.metrics.evaluate`."""
    run_dir = resolve_run_dir(run)
    frame = pl.read_parquet(run_dir / PREDICTIONS).filter(pl.col("scoring") == scoring)
    if report_only:
        report = read_manifest(run_dir)["report_seasons"]
        frame = frame.filter(pl.col("season").is_in(report))
    if models is not None:
        frame = frame.filter(pl.col("model").is_in(list(models)))
    return frame


# --------------------------------------------------------------------------- #
# Configured runs (``ffml backtest --config <name>``)
# --------------------------------------------------------------------------- #


def _worker_logging(log_path: Path) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    logging.basicConfig(
        level=logging.INFO,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
        handlers=[logging.FileHandler(log_path, encoding="utf-8"), logging.StreamHandler()],
        force=True,
    )


def _run_config_part(
    config_name: str,
    key: str,
    run_id: str,
    threads: int | None,
    frozen_manifest: str | None = None,
) -> dict[str, Any]:
    """Worker: build the configured models, run the one with ``key``, write its parts only."""
    from ffml.eval.backtest_configs import get_config

    cfg = get_config(config_name)
    _worker_logging(backtests_root() / run_id / "logs" / f"{_slug(key)}.log")
    model = next(m for m in cfg.build_models() if model_key(m) == key)
    if threads is not None and hasattr(model, "num_threads"):
        model.num_threads = threads
    t0 = time.perf_counter()
    run_walk_forward(
        [model],
        cfg.seasons,
        cfg.weeks,
        cfg.scorings,
        cfg.refit_every,
        run_id,
        cfg.purpose,
        report_seasons=cfg.report_seasons,
        config_name=cfg.name,
        combine=False,
        calibration=cfg.calibration,
        frozen_manifest=frozen_manifest,
    )
    return {"key": key, "seconds": time.perf_counter() - t0}


def run_backtest(
    config_name: str = "default",
    run_id: str | None = None,
    n_jobs: int | None = None,
    frozen_manifest: str | Path | None = None,
) -> Path:
    """Run a configured backtest (``ffml/eval/backtest_configs.py``) and return the run directory.

    The run id defaults to ``$FFML_BACKTEST_RUN_ID`` or ``<config>_<UTC date>``,
    so rerunning a stopped backtest on the same day resumes it. With
    ``n_jobs > 1`` each model runs in its own worker process (part files only;
    logs in ``<run>/logs``) and the parent then combines every part.

    Before any model is built, the holdout guard checks every season the run
    reads, and a frozen-calibration config checks that each model key has an
    intact frozen calibrator (``frozen_manifest``, else the config's, else
    ``artifacts/calibration/FROZEN.json``).
    """
    from ffml.eval.backtest_configs import get_config

    cfg = get_config(config_name)
    assert_seasons_allowed(_history(cfg.seasons), cfg.purpose)
    run_id = (
        run_id
        or os.environ.get("FFML_BACKTEST_RUN_ID")
        or (f"{cfg.name}_{datetime.now(UTC).strftime('%Y%m%d')}")
    )
    manifest = frozen_manifest or cfg.frozen_manifest
    manifest = str(manifest) if manifest is not None else None
    n_jobs = cfg.n_jobs if n_jobs is None else int(n_jobs)
    models = cfg.build_models()
    if cfg.calibration == "frozen":
        load_frozen_calibrators(manifest, [model_key(m) for m in models])
    t0 = time.perf_counter()
    if n_jobs > 1 and len(models) > 1:
        threads = max(1, (os.cpu_count() or 2) // n_jobs)
        keys = [model_key(m) for m in models]
        # heaviest first so the long jobs start immediately
        keys.sort(
            key=lambda k: (
                0 if getattr(next(m for m in models if model_key(m) == k), "heavy", False) else 1
            )
        )
        log.info(
            "backtest %s: %d models over %d workers (%d threads each)",
            run_id,
            len(keys),
            n_jobs,
            threads,
        )
        with ProcessPoolExecutor(max_workers=n_jobs, mp_context=get_context("spawn")) as pool:
            futures = [
                pool.submit(_run_config_part, cfg.name, k, run_id, threads, manifest) for k in keys
            ]
            for fut in futures:
                info = fut.result()
                log.info("worker finished %s in %.1fs", info["key"], info["seconds"])
    run_dir = run_walk_forward(
        models,
        cfg.seasons,
        cfg.weeks,
        cfg.scorings,
        cfg.refit_every,
        run_id,
        cfg.purpose,
        report_seasons=cfg.report_seasons,
        config_name=cfg.name,
        calibration=cfg.calibration,
        frozen_manifest=manifest,
    )
    log.info("backtest %s finished in %.1fs: %s", run_id, time.perf_counter() - t0, run_dir)
    return run_dir


__all__ = [
    "OUTCOME_COLUMNS",
    "build_schedule",
    "freeze_calibrators",
    "frozen_manifest_path",
    "frozen_rows",
    "load_frozen_calibrators",
    "calibrated_rows",
    "check_frame",
    "join_outcomes",
    "load_backtest",
    "model_key",
    "outcome_table",
    "pit_values",
    "position_bounds",
    "read_manifest",
    "refit_points",
    "resolve_scorings",
    "run_backtest",
    "run_walk_forward",
    "with_pit",
]
