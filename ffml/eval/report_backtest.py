"""Walk-forward validation report on 2022-2024 (EV-3.3; MODELING.md sections 2, 3, 6, 7, 8, 9, 11).

``build_backtest_report(run_id, out_name, ablation_run_id=None)`` reads one
backtest run (``data/backtests/<run_id>/predictions.parquet`` through
:func:`ffml.eval.harness.load_backtest`) and writes ``reports/<out_name>.md``
plus per-week and delta CSVs under ``data/reports/`` (gitignored). Everything
is recomputed from the backtest file and fixed seeds, so the same file and
code give the same report. Command line::

    python -m ffml.eval.report_backtest --run-id default_20261102 --out backtest_2026-11-03

Conventions
===========

* **Focus model.** The structural chain in its primary configuration
  (``chain_v1``); ``chain_v1_final_report`` is the MODELING.md section 9
  sensitivity. When the run has no chain yet, the challenger stands in so the
  pipeline runs end to end, and the report says so at the top.
* **Populations.** Headline: rows every headline model predicts (D-050
  alignment), then active rows with an actual and a projection. Consensus
  (D-081): PPR only; every model is restricted to exactly the player-weeks
  with a valid consensus row and projection, so populations match row for
  row (checked); consensus rows are scored against the harness actuals
  (D-079). Players whose game is on or before the scrape date drop out of
  that comparison (D-036), so the Thursday effect is shown separately: the
  focus model and the best baseline on the matched, early-game and all-active
  populations, with the count of every other exclusion reason. Secondary: every rostered row
  including inactive ones (actual 0), with ``pred_*_uncond`` for models that
  have an availability layer.
* **Pair set (D-074).** ``ref_rank`` comes from the mean ``pred_q50`` over the
  raw primary-configuration headline models (plus the consensus in the
  consensus population); the final-report variant is left out because it
  uses same-week injury reports. Start/sit and Spearman are scored on that
  set; each model's own top-N is secondary.
* **Deltas.** Always ``focus - other``, so a negative MAE or pinball delta
  and a positive Spearman or start/sit delta favour the focus model. The
  interval is the 2.5-97.5 percentile of paired week-clustered bootstrap
  differences (every model sees the same resampled season-weeks). The focus
  model *beats* the other only when the whole interval lies on the good side
  of zero (MODELING.md section 8); *worse* when it lies on the bad side.
  Coverage has a target, not a direction, so its delta row uses
  ``coverage_gap = |coverage - 0.80|``.
* **Calibrated forms.** ``<model>+cal`` rows (online conformal, 2021 warm-up,
  D-072) are compared with each other; the shipped chain is the calibrated one.

Only aggregate metrics are written to the markdown report (D-007).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import subprocess
import sys
import time
from collections.abc import Iterable, Mapping, Sequence
from concurrent.futures import ProcessPoolExecutor
from dataclasses import dataclass, field
from multiprocessing import get_context
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from ffml.config import PROJECT_ROOT, ensure_utf8, get_settings
from ffml.eval import metrics as M
from ffml.eval.harness import PREDICTIONS, load_backtest, read_manifest, resolve_run_dir

CAL = "+cal"
FOCUS_CANDIDATES: tuple[str, ...] = ("chain_v1", "chain_v1-primary", "chain_v1_primary")
FINAL_REPORT_CANDIDATES: tuple[str, ...] = ("chain_v1_final_report", "chain_v1-final_report")
CHALLENGER = "direct_lgbm"
BASELINES: tuple[str, ...] = (
    "rolling_avg_4", "rolling_avg_3", "rolling_ew_035", "prior_season", "rolling_median_8",
)
#: baselines named in the MODELING.md section 8 rule (1 and 2) plus the D-053 median baseline
VERDICT_BASELINES: tuple[str, ...] = ("rolling_avg_4", "prior_season", "rolling_median_8")
CONSENSUS = "consensus_ecr"
POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K", "DST")
ACCEPT_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")
COVERAGE_BAND: tuple[float, float] = (0.75, 0.85)
LINEUP_SLOTS: tuple[str, ...] = M.DEFAULT_SLOTS
SCORINGS: tuple[str, ...] = ("ppr", "half", "std")
VERDICT_METRICS: tuple[str, ...] = (
    "mae", "pinball_q10", "pinball_q50", "pinball_q90", "coverage_gap", "spearman_common", "start_sit_common",
)
DELTA_METRICS: tuple[str, ...] = (
    "mae", "mae_mean", "pinball_q10", "pinball_q50", "pinball_q90", "coverage_gap",
    "spearman_common", "start_sit_common",
)
METRIC_LABEL: dict[str, str] = {
    "mae": "MAE",
    "mae_mean": "MAE (mean)",
    "pinball_q10": "pinball q10",
    "pinball_q50": "pinball q50",
    "pinball_q90": "pinball q90",
    "coverage_80": "80% coverage",
    "coverage_gap": "coverage gap",
    "interval_width": "80% width",
    "spearman_common": "Spearman",
    "spearman": "Spearman (own top-N)",
    "start_sit_common": "start/sit",
    "start_sit": "start/sit (own top-N)",
    "lineup_efficiency": "lineup efficiency",
}
STATUS_BETTER, STATUS_WORSE, STATUS_NONE, STATUS_NA = "beats", "worse", "no difference", "n/a"
KEEP_COLUMNS: tuple[str, ...] = (
    "entity_id", "position", "season", "week", "model", "team", "game_id",
    "pred_mean", "pred_q05", "pred_q10", "pred_q25", "pred_q50", "pred_q75", "pred_q90", "pred_q95",
    "actual", "active",
    "boom_prob", "bust_prob", "boom_actual", "bust_actual", "pred_cdf_at_actual", "rank_pred",
    "active_prob", "pred_mean_uncond", "pred_q50_uncond", M.REF_RANK,
)
SORT_KEYS: tuple[str, ...] = ("season", "week", "position", "entity_id")


# --------------------------------------------------------------------------- #
# Deltas and the CI rule
# --------------------------------------------------------------------------- #


def ci_status(lo: float, hi: float, higher_is_better: bool | None) -> str:
    """CI rule of MODELING.md section 8 for ``delta = focus - other``.

    ``beats`` when the whole interval is on the good side of zero, ``worse``
    when it is on the bad side, ``no difference`` when it contains zero, and
    ``n/a`` without an interval or a direction.
    """
    if higher_is_better is None or lo is None or hi is None or math.isnan(lo) or math.isnan(hi):
        return STATUS_NA
    if higher_is_better:
        return STATUS_BETTER if lo > 0 else STATUS_WORSE if hi < 0 else STATUS_NONE
    return STATUS_BETTER if hi < 0 else STATUS_WORSE if lo > 0 else STATUS_NONE


@dataclass(frozen=True)
class Delta:
    """``focus - other`` for one metric with its paired interval and CI-rule status."""

    metric: str
    focus: str
    other: str
    focus_value: float
    other_value: float
    delta: float
    lo: float
    hi: float
    status: str

    def cell(self, digits: int = 3) -> str:
        if self.delta is None or math.isnan(self.delta):
            return "n/a"
        tag = {STATUS_BETTER: " B", STATUS_WORSE: " W"}.get(self.status, "")
        if math.isnan(self.lo) or math.isnan(self.hi):
            return f"{_num(self.delta, digits, sign=True)}"
        return (
            f"{_num(self.delta, digits, sign=True)} "
            f"[{_num(self.lo, digits, sign=True)}, {_num(self.hi, digits, sign=True)}]{tag}"
        )


@dataclass
class BootResult:
    """Point values, bootstrap replicates and population sizes per group (position or ``ALL``)."""

    points: dict[str, dict[str, dict[str, float]]] = field(default_factory=dict)
    boots: dict[str, dict[str, dict[str, np.ndarray]]] = field(default_factory=dict)
    n: dict[str, dict[str, int]] = field(default_factory=dict)
    n_boot: int = 0
    alpha: float = 0.05

    def groups(self) -> list[str]:
        return list(self.points)

    def has(self, group: str, label: str) -> bool:
        return label in self.points.get(group, {})

    def value(self, group: str, label: str, metric: str) -> float:
        return float(self.points.get(group, {}).get(label, {}).get(metric, math.nan))

    def delta(self, group: str, focus: str, other: str, metric: str) -> Delta:
        """``focus - other`` with the paired percentile interval and the CI-rule status."""
        a = self.value(group, focus, metric)
        b = self.value(group, other, metric)
        d = a - b
        ba = self.boots.get(group, {}).get(focus, {}).get(metric)
        bb = self.boots.get(group, {}).get(other, {}).get(metric)
        if ba is None or bb is None or self.n_boot <= 0:
            lo = hi = math.nan
        else:
            lo, hi = M.percentile_interval(ba - bb, self.alpha)
        status = ci_status(lo, hi, M.HIGHER_IS_BETTER.get(metric))
        return Delta(metric, focus, other, a, b, d, lo, hi, status)


# --------------------------------------------------------------------------- #
# Bootstrap (parallel over positions and chunks of models; draws stay paired)
# --------------------------------------------------------------------------- #


def _slim(frame: pl.DataFrame) -> pl.DataFrame:
    return frame.select([c for c in KEEP_COLUMNS if c in frame.columns])


def _boot_task(task: tuple[str, str, dict[str, pl.DataFrame], int, int]) -> tuple[str, Any, Any, Any, Any]:
    group, kind, frames, n_boot, seed = task
    columns = sorted(set().union(*(set(f.columns) for f in frames.values())))
    bundle = M.lineup_bundle(LINEUP_SLOTS) if kind == "lineup" else M.metric_bundle(columns)
    points = {name: bundle(f) for name, f in frames.items()}
    sizes = {name: M.population(f).height for name, f in frames.items()}
    boots = M.paired_bundle_bootstrap(frames, bundle, n_boot, seed=seed) if n_boot > 0 else {}
    universe = M.cluster_universe(list(frames.values()))
    return group, points, boots, sizes, universe


def prepare_frames(frames: Mapping[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """Validate, slim and sort frames (sorted frames give every chunk the same cluster universe)."""
    out = {}
    for name, f in frames.items():
        slim = _slim(f.with_columns(pl.lit(name).alias("model")))
        out[name] = M.validate_frame(slim).sort(list(SORT_KEYS))
    # pad to the union of columns (null where a model lacks one) so one bundle fits every frame
    schema: dict[str, pl.DataType] = {}
    for f in out.values():
        for col, dtype in f.schema.items():
            if col not in schema or schema[col] == pl.Null:
                schema[col] = dtype
    for name, f in out.items():
        missing = [c for c in schema if c not in f.columns]
        if missing:
            out[name] = f.with_columns([pl.lit(None, dtype=schema[c]).alias(c) for c in missing])
    return out


def run_bootstrap(
    frames: Mapping[str, pl.DataFrame],
    positions: Sequence[str] = POSITIONS,
    *,
    lineup: bool = True,
    n_boot: int = 500,
    seed: int = 0,
    n_jobs: int = 1,
    chunk: int = 4,
    alpha: float = 0.05,
) -> BootResult:
    """Point metrics and paired bootstrap replicates per position (and ``ALL`` for lineups).

    Frames are split into chunks of ``chunk`` models per task; all chunks of
    a group share the cluster universe and the seed, so their replicates pair
    (checked). ``n_jobs > 1`` runs tasks in spawned worker processes.
    """
    prepared = prepare_frames(frames)
    labels = list(prepared)
    chunks = [labels[i : i + chunk] for i in range(0, len(labels), max(chunk, 1))]
    tasks: list[tuple[str, str, dict[str, pl.DataFrame], int, int]] = []
    if lineup:
        for ch in chunks:
            tasks.append(("ALL", "lineup", {k: prepared[k] for k in ch}, n_boot, seed))
    for pos in positions:
        subs = {k: prepared[k].filter(pl.col("position") == pos) for k in labels}
        if all(s.is_empty() for s in subs.values()):
            continue
        for ch in chunks:
            tasks.append((pos, "position", {k: subs[k] for k in ch}, n_boot, seed))
    if n_jobs > 1 and len(tasks) > 1:
        with ProcessPoolExecutor(max_workers=n_jobs, mp_context=get_context("spawn")) as pool:
            results = list(pool.map(_boot_task, tasks))
    else:
        results = [_boot_task(t) for t in tasks]
    out = BootResult(n_boot=max(n_boot, 0), alpha=alpha)
    universes: dict[str, list[tuple]] = {}
    for group, points, boots, sizes, universe in results:
        if group in universes and universes[group] != universe:
            raise RuntimeError(f"bootstrap chunks of group {group} have different cluster universes")
        universes[group] = universe
        out.points.setdefault(group, {}).update(points)
        out.boots.setdefault(group, {}).update(boots)
        out.n.setdefault(group, {}).update(sizes)
    ordered = [g for g in ("ALL", *positions) if g in out.points]
    out.points = {g: out.points[g] for g in ordered}
    return out


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@dataclass
class ModelSet:
    """Labels of one report: focus, sensitivity, challenger, baselines, and their calibrated forms."""

    focus: str
    final_report: str | None
    challenger: str | None
    baselines: list[str]
    draft_note: str | None = None

    @property
    def raw(self) -> list[str]:
        out = [self.focus]
        for label in (self.final_report, self.challenger, *self.baselines):
            if label and label not in out:
                out.append(label)
        return out

    def comparators(self) -> list[str]:
        """Labels the focus model is compared with (everything but itself)."""
        return [m for m in self.raw if m != self.focus]

    def verdict_comparators(self, with_consensus: bool) -> list[str]:
        out = [b for b in VERDICT_BASELINES if b in self.baselines]
        if with_consensus:
            out.append(CONSENSUS)
        if self.challenger and self.challenger != self.focus:
            out.append(self.challenger)
        return out


def resolve_models(present: Iterable[str], focus: str | None = None) -> ModelSet:
    """Pick the focus model (the primary chain, else the challenger as a draft stand-in)."""
    present = set(present)
    draft_note = None
    if focus is not None:
        if focus not in present:
            raise ValueError(f"focus model {focus!r} not in the run; present: {sorted(present)}")
        chosen = focus
    else:
        chosen = next((m for m in FOCUS_CANDIDATES if m in present), None)
        if chosen is None:
            if CHALLENGER not in present:
                raise ValueError(f"run has neither a chain nor {CHALLENGER}; present: {sorted(present)}")
            chosen = CHALLENGER
            draft_note = (
                "DRAFT: the structural chain is not in this run yet. The direct challenger "
                f"(`{CHALLENGER}`) stands in as the focus model so that every section, interval and "
                "the verdict format run end to end on real rows. Nothing in this draft is a "
                "statement about the chain."
            )
    # the final-report variant is the chain's sensitivity; a stand-in focus has none
    final = next((m for m in FINAL_REPORT_CANDIDATES if m in present), None) if chosen in FOCUS_CANDIDATES else None
    challenger = CHALLENGER if CHALLENGER in present else None
    baselines = [b for b in BASELINES if b in present]
    return ModelSet(chosen, final, challenger, baselines, draft_note)


def labels_with_cal(models: Sequence[str], present: Iterable[str]) -> list[str]:
    present = set(present)
    return [m for m in models if m in present] + [m + CAL for m in models if m + CAL in present]


def headline_frames(
    frame: pl.DataFrame, raw: Sequence[str], present: Iterable[str], reference: Sequence[str] | None = None
) -> tuple[dict[str, pl.DataFrame], dict[str, int], dict[str, int]]:
    """Aligned frames (raw and calibrated) with the D-074 ``ref_rank``.

    The reference is the mean ``pred_q50`` of ``reference`` (default: the raw
    models). Returns the frames, rows per model before alignment, and rows
    dropped by the alignment.
    """
    labels = labels_with_cal(raw, present)
    frames = {m: frame.filter(pl.col("model") == m) for m in labels}
    before = {m: f.height for m, f in frames.items()}
    aligned, dropped = M.align_frames(frames)
    ref = [m for m in (reference if reference is not None else raw) if m in aligned]
    aligned = M.with_reference_rank(aligned, reference=ref)
    return aligned, before, dropped


def reference_models(models: ModelSet, present: Iterable[str]) -> list[str]:
    """Raw primary-configuration models that form the D-074 reference.

    The final-report variant uses same-week injury reports (MODELING.md
    section 9), so it is left out: the headline pair set must not depend on
    information the primary configuration does not have.
    """
    present = set(present)
    return [m for m in models.raw if m in present and m != models.final_report]


def _sha256(path: Path) -> str:
    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def _git(*args: str) -> str:
    try:
        out = subprocess.run(
            ["git", *args], cwd=PROJECT_ROOT, capture_output=True, text=True, check=True, timeout=30
        )
        return out.stdout.strip()
    except (OSError, subprocess.SubprocessError):
        return "unknown"


# --------------------------------------------------------------------------- #
# D-052 scoring cross-check
# --------------------------------------------------------------------------- #


@dataclass
class CrossCheck:
    rows: int
    matched: int
    by_position: pl.DataFrame
    explained: dict[str, int]
    active_mismatch: int
    missing_reference: int

    @property
    def rate(self) -> float:
        return self.matched / self.rows if self.rows else math.nan


def scoring_cross_check(harness: pl.DataFrame, reference: pl.DataFrame, tol: float = 1e-6) -> CrossCheck:
    """Compare harness PPR actuals with the evaluator's reference points (D-052).

    ``harness`` holds ``entity_id, position, season, week, actual, active``
    (one row per key); ``reference`` is
    :func:`ffml.eval.ecr_baseline.reference_actuals`. Differences equal to
    the row's own kicking points for a non-kicker are classified as
    emergency-kicker rows (``ffml/scoring`` scores kicking stats for every
    position; the reference adds them for kickers only).
    """
    keys = ["entity_id", "season", "week"]
    ref = reference.select(
        *keys,
        pl.col("actual").alias("ref_actual"),
        pl.col("active").alias("ref_active"),
        pl.col("kicking_points").fill_null(0.0),
    )
    j = harness.select(*keys, "position", "actual", "active").join(ref, on=keys, how="left")
    j = j.with_columns((pl.col("actual") - pl.col("ref_actual")).abs().alias("_diff"))
    j = j.with_columns(
        (pl.col("_diff") <= tol).fill_null(False).alias("_match"),
        (
            (pl.col("position") != "K")
            & ((pl.col("actual") - pl.col("ref_actual") - pl.col("kicking_points")).abs() <= tol)
            & (pl.col("kicking_points").abs() > tol)
        ).fill_null(False).alias("_kick"),
    )
    by_pos = (
        j.group_by("position")
        .agg(
            pl.len().alias("rows"),
            pl.col("_match").sum().alias("matched"),
            (~pl.col("_match") & pl.col("_kick")).sum().alias("emergency_kicker"),
            (~pl.col("_match") & ~pl.col("_kick") & pl.col("ref_actual").is_not_null()).sum().alias("unexplained"),
            pl.col("ref_actual").is_null().sum().alias("no_reference_row"),
            pl.col("_diff").max().alias("max_abs_diff"),
        )
        .with_columns((pl.col("matched") / pl.col("rows")).alias("match_rate"))
        .sort(pl.col("position").replace_strict({p: i for i, p in enumerate(POSITIONS)}, default=99))
    )
    explained = {
        "emergency_kicker": int((~j["_match"] & j["_kick"]).sum()),
        "unexplained": int((~j["_match"] & ~j["_kick"] & j["ref_actual"].is_not_null()).sum()),
    }
    active_mismatch = int(
        j.filter(pl.col("ref_active").is_not_null() & (pl.col("active") != pl.col("ref_active"))).height
    )
    return CrossCheck(
        rows=j.height,
        matched=int(j["_match"].sum()),
        by_position=by_pos,
        explained=explained,
        active_mismatch=active_mismatch,
        missing_reference=int(j["ref_actual"].null_count()),
    )


# --------------------------------------------------------------------------- #
# Consensus population
# --------------------------------------------------------------------------- #


def consensus_population(
    consensus: pl.DataFrame,
    aligned: Mapping[str, pl.DataFrame],
    raw: Sequence[str],
    reference: Sequence[str] | None = None,
) -> dict[str, pl.DataFrame]:
    """Matched population (D-081): every frame restricted to the same player-weeks as the consensus.

    The consensus frame's own reference actuals are replaced by the harness
    outcomes of the focus frame (the first raw label) so every model is
    scored against the same numbers (D-079). Frames are then restricted to
    the keys they all share and, among those, to keys where every frame has
    a ``pred_q50``, ``pred_q10`` and ``pred_q90`` (a consensus row without a
    rank-to-points curve has none), so the evaluation populations match row
    for row (:func:`population_keys_identical`). The joint ``ref_rank``
    includes the consensus (D-077).
    """
    focus = raw[0]
    outcome_cols = [c for c in ("actual", "active", "boom_actual", "bust_actual") if c in aligned[focus].columns]
    outcomes = aligned[focus].select("entity_id", "season", "week", *outcome_cols)
    cons = consensus.drop([c for c in outcome_cols if c in consensus.columns]).join(
        outcomes, on=["entity_id", "season", "week"], how="inner"
    )
    for col, dtype in (("boom_prob", pl.Float64), ("bust_prob", pl.Float64), ("pred_cdf_at_actual", pl.Float64)):
        if col not in cons.columns:
            cons = cons.with_columns(pl.lit(None, dtype=dtype).alias(col))
    frames = {name: f.drop([c for c in (M.REF_RANK, "ref_q50") if c in f.columns]) for name, f in aligned.items()}
    frames[CONSENSUS] = cons.with_columns(pl.lit(CONSENSUS).alias("model"))
    joint = matched_frames(frames)
    ref = [m for m in (reference if reference is not None else raw) if m in joint] + [CONSENSUS]
    return M.with_reference_rank(joint, reference=ref)


_PRED_REQUIRED: tuple[str, ...] = ("pred_q50", "pred_q10", "pred_q90")


def matched_frames(frames: Mapping[str, pl.DataFrame]) -> dict[str, pl.DataFrame]:
    """Restrict every frame to the keys all share where every frame has q10, q50 and q90 (D-081)."""
    joint, _ = M.align_frames(frames)
    keys: pl.DataFrame | None = None
    for f in joint.values():
        k = f.filter(pl.all_horizontal([pl.col(c).is_not_null() & ~pl.col(c).cast(pl.Float64).is_nan()
                                        for c in _PRED_REQUIRED if c in f.columns])).select(M.KEY_COLUMNS)
        keys = k if keys is None else keys.join(k, on=list(M.KEY_COLUMNS), how="inner")
    assert keys is not None
    return {name: f.join(keys, on=list(M.KEY_COLUMNS), how="semi") for name, f in joint.items()}


def population_keys(frame: pl.DataFrame) -> pl.DataFrame:
    """Sorted key columns of the evaluation population (active, with an actual and a projection)."""
    return M.population(M.validate_frame(_slim(frame))).select(M.KEY_COLUMNS).sort(list(M.KEY_COLUMNS))


def population_keys_identical(frames: Mapping[str, pl.DataFrame]) -> bool:
    """True when every frame's evaluation population has exactly the same row keys."""
    keys = [population_keys(f) for f in frames.values()]
    return all(k.equals(keys[0]) for k in keys[1:]) if keys else True


# --------------------------------------------------------------------------- #
# Thursday effect (D-081): matched, early-game and all player-weeks
# --------------------------------------------------------------------------- #

STATUS_MATCHED = "matched"
STATUS_EARLY = "early_game"
STATUS_NO_MATCH = "no_ecr_match"
STATUS_NO_SCRAPE = "no_scrape_week"
STATUS_NO_PROJECTION = "valid_no_projection"
STATUS_ORDER: tuple[str, ...] = (STATUS_MATCHED, STATUS_EARLY, STATUS_NO_MATCH, STATUS_NO_SCRAPE, STATUS_NO_PROJECTION)
STATUS_LABEL: dict[str, str] = {
    STATUS_MATCHED: "(a) matched consensus population",
    STATUS_EARLY: "(b) game on or before the scrape date",
    STATUS_NO_MATCH: "no ECR match (not ranked, no id, or another page)",
    STATUS_NO_SCRAPE: "no scrape that week",
    STATUS_NO_PROJECTION: "valid ECR row without a consensus projection",
}
POP_MATCHED, POP_EARLY, POP_ALL = "matched", "early_game", "all"
POP_LABEL: dict[str, str] = {
    POP_MATCHED: "(a) matched",
    POP_EARLY: "(b) early game",
    POP_ALL: "(c) all active",
}


def consensus_status(active_keys: pl.DataFrame, matched_keys: pl.DataFrame, reasons: pl.DataFrame) -> pl.DataFrame:
    """One status per active headline key: matched, or why it has no matched consensus row.

    ``active_keys`` and ``matched_keys`` hold :data:`ffml.eval.metrics.KEY_COLUMNS`
    (the evaluation populations of the headline and the matched consensus
    tables); ``reasons`` is :func:`ffml.eval.ecr_baseline.exclusion_reasons`
    output. A key whose ECR row is valid but which is not matched lacks a
    consensus projection (no prior-season curve, or a dropped duplicate).
    """
    keys = list(M.KEY_COLUMNS)
    flagged = active_keys.select(keys).unique().join(
        matched_keys.select(keys).unique().with_columns(pl.lit(True).alias("_m")), on=keys, how="left"
    ).join(reasons.select(*keys, "reason"), on=keys, how="left")
    status = (
        pl.when(pl.col("_m").fill_null(False)).then(pl.lit(STATUS_MATCHED))
        .when(pl.col("reason") == "valid").then(pl.lit(STATUS_NO_PROJECTION))
        .when(pl.col("reason").is_not_null()).then(pl.col("reason"))
        .otherwise(pl.lit(STATUS_NO_SCRAPE))
    )
    return flagged.with_columns(status.alias("status")).select(*keys, "status")


def status_counts(status: pl.DataFrame, positions: Sequence[str] = POSITIONS) -> pl.DataFrame:
    """Rows per position (and ``ALL``) and status, plus the total of active rows."""
    rows = []
    for pos in [*positions, "ALL"]:
        sub = status if pos == "ALL" else status.filter(pl.col("position") == pos)
        if sub.is_empty():
            continue
        counts = dict(sub.group_by("status").len().iter_rows())
        rows.append({"position": pos, **{s: int(counts.get(s, 0)) for s in STATUS_ORDER}, "all": sub.height})
    schema = {"position": pl.String, **dict.fromkeys(STATUS_ORDER, pl.Int64), "all": pl.Int64}
    return pl.DataFrame(rows, schema=schema)


def split_populations(
    frames: Mapping[str, pl.DataFrame], status: pl.DataFrame
) -> dict[str, dict[str, pl.DataFrame]]:
    """``{population: {label: frame}}`` for the matched, early-game and all-active populations.

    The frames keep the headline ``ref_rank``, so start/sit and Spearman in
    every population use the same model-agnostic startable set (D-077).
    """
    keys = list(M.KEY_COLUMNS)
    matched = status.filter(pl.col("status") == STATUS_MATCHED).select(keys)
    early = status.filter(pl.col("status") == STATUS_EARLY).select(keys)
    out: dict[str, dict[str, pl.DataFrame]] = {POP_MATCHED: {}, POP_EARLY: {}, POP_ALL: {}}
    for name, f in frames.items():
        cast = {c: f.schema[c] for c in keys}
        out[POP_MATCHED][name] = f.join(matched.cast(cast), on=keys, how="semi")
        out[POP_EARLY][name] = f.join(early.cast(cast), on=keys, how="semi")
        out[POP_ALL][name] = f
    return out


def _mae_bundle(frame: pl.DataFrame) -> dict[str, float]:
    return {"mae": M.mae(frame)}


@dataclass(frozen=True)
class SplitDelta:
    """``focus - best`` MAE per population and the early-minus-matched difference, with intervals."""

    group: str
    deltas: dict[str, tuple[float, float, float]]  # population -> (delta, lo, hi)
    did: tuple[float, float, float]  # (early - matched) of the deltas
    mae: dict[str, tuple[float, float]]  # population -> (focus MAE, best MAE)


def split_mae_deltas(
    pops: Mapping[str, Mapping[str, pl.DataFrame]], focus: str, best: str, positions: Sequence[str] = POSITIONS,
    n_boot: int = 500, seed: int = 0, alpha: float = 0.05,
) -> list[SplitDelta]:
    """Paired week-clustered intervals for ``focus - best`` MAE in each population (and pooled ``ALL``).

    All six frames of a group are bootstrapped in one call, so the
    early-minus-matched difference of the deltas is paired too.
    """
    out = []
    for group in [*positions, "ALL"]:
        frames = {}
        for pop in (POP_MATCHED, POP_EARLY, POP_ALL):
            for label in (focus, best):
                f = pops[pop][label]
                if group != "ALL":
                    f = f.filter(pl.col("position") == group)
                frames[f"{pop}|{label}"] = M.validate_frame(_slim(f)).sort(list(SORT_KEYS))
        if all(M.population(f).is_empty() for f in frames.values()):
            continue
        points = {k: M.mae(f) for k, f in frames.items()}
        boots = M.paired_bundle_bootstrap(frames, _mae_bundle, n_boot, seed=seed) if n_boot > 0 else {}
        deltas = {}
        arrays: dict[str, np.ndarray | None] = {}
        for pop in (POP_MATCHED, POP_EARLY, POP_ALL):
            d = points[f"{pop}|{focus}"] - points[f"{pop}|{best}"]
            arr = boots[f"{pop}|{focus}"]["mae"] - boots[f"{pop}|{best}"]["mae"] if boots else None
            lo, hi = M.percentile_interval(arr, alpha) if arr is not None else (math.nan, math.nan)
            deltas[pop] = (d, lo, hi)
            arrays[pop] = arr
        did_point = deltas[POP_EARLY][0] - deltas[POP_MATCHED][0]
        if arrays[POP_EARLY] is not None and arrays[POP_MATCHED] is not None:
            dlo, dhi = M.percentile_interval(arrays[POP_EARLY] - arrays[POP_MATCHED], alpha)
        else:
            dlo = dhi = math.nan
        mae = {pop: (points[f"{pop}|{focus}"], points[f"{pop}|{best}"]) for pop in (POP_MATCHED, POP_EARLY, POP_ALL)}
        out.append(SplitDelta(group, deltas, (did_point, dlo, dhi), mae))
    return out


def thursday_sentence(pooled: SplitDelta, subject: str, best: str) -> str:
    """One or two plain sentences: does the focus model do better or worse on early-game player-weeks?"""
    f_m, _ = pooled.mae[POP_MATCHED]
    f_e, _ = pooled.mae[POP_EARLY]
    d_m = pooled.deltas[POP_MATCHED]
    d_e = pooled.deltas[POP_EARLY]
    did, lo, hi = pooled.did
    if math.isnan(lo) or math.isnan(hi):
        verdict = "no interval is available, so no direction can be stated"
    elif hi < 0:
        verdict = "it does relatively better on early-game player-weeks than on the matched population"
    elif lo > 0:
        verdict = "it does relatively worse on early-game player-weeks than on the matched population"
    else:
        verdict = ("it does neither clearly better nor worse on early-game player-weeks than on the matched "
                   "population (the interval includes zero)")
    return (
        f"{subject}'s MAE (all positions pooled, calibrated) is {_num(f_e, 2)} on early-game player-weeks and "
        f"{_num(f_m, 2)} on the matched population; its MAE minus that of `{best}` is {_num(d_e[0], 2, True)} "
        f"(95% interval {_num(d_e[1], 2, True)} to {_num(d_e[2], 2, True)}) on early-game player-weeks and "
        f"{_num(d_m[0], 2, True)} ({_num(d_m[1], 2, True)} to {_num(d_m[2], 2, True)}) on the matched population. "
        f"The difference between the two is {_num(did, 2, True)} (95% interval {_num(lo, 2, True)} to "
        f"{_num(hi, 2, True)}): {verdict}."
    )


# --------------------------------------------------------------------------- #
# Interval coverage detail: tails, ties, tie-aware diagnostic
# --------------------------------------------------------------------------- #

#: a tie share at q10 or q90 at or above this is called material in the coverage paragraph
MATERIAL_TIE_SHARE = 0.05


def position_bounds(reference: pl.DataFrame, seasons: Sequence[int]) -> pl.DataFrame:
    """``season, position, pos_min, pos_max``: played-row extremes in seasons before each season (D-071 knots)."""
    played = reference.filter(pl.col("active"))
    rows = []
    for season in sorted({int(s) for s in seasons}):
        past = played.filter(pl.col("season") < season)
        if past.is_empty():
            continue
        rows.append(past.group_by("position").agg(
            pl.col("actual").min().alias("pos_min"), pl.col("actual").max().alias("pos_max")
        ).with_columns(pl.lit(season, dtype=pl.Int32).alias("season")))
    if not rows:
        return pl.DataFrame(schema={"position": pl.String, "pos_min": pl.Float64, "pos_max": pl.Float64,
                                    "season": pl.Int32})
    return pl.concat(rows, how="vertical_relaxed")


def tie_aware_coverage(frame: pl.DataFrame, bounds: pl.DataFrame | None, seed: int = 0) -> float:
    """Share of the population with a randomized PIT in (0.1, 0.9) (diagnostic, not the acceptance rule)."""
    f = M.population(M.validate_frame(_slim(frame))).sort(list(SORT_KEYS))
    if any(c not in f.columns for c in M.KNOT_COLUMNS) or f.is_empty():
        return math.nan
    f = f.filter(pl.all_horizontal([pl.col(c).is_not_null() for c in M.KNOT_COLUMNS]))
    if f.is_empty():
        return math.nan
    if bounds is not None and not bounds.is_empty():
        f = f.join(bounds.with_columns(pl.col("season").cast(pl.Int32)), on=["season", "position"], how="left")
    else:
        f = f.with_columns(pl.lit(None, dtype=pl.Float64).alias("pos_min"),
                           pl.lit(None, dtype=pl.Float64).alias("pos_max"))
    u = M.randomized_pit(
        f["actual"].to_numpy(), f.select(M.KNOT_COLUMNS).cast(pl.Float64).to_numpy(),
        f["pos_min"].cast(pl.Float64).to_numpy(), f["pos_max"].cast(pl.Float64).to_numpy(), seed=seed,
    )
    return float(((u > 0.1) & (u < 0.9)).mean())


def coverage_detail(
    frames: Mapping[str, pl.DataFrame], labels: Sequence[str], bounds: pl.DataFrame | None,
    positions: Sequence[str] = POSITIONS, seed: int = 0,
) -> pl.DataFrame:
    """Per model and position: closed coverage, the exclusive tail shares, and the tie-aware diagnostic."""
    rows = []
    for name in labels:
        if name not in frames:
            continue
        for pos in positions:
            sub = frames[name].filter(pl.col("position") == pos)
            if sub.is_empty():
                continue
            v = M.validate_frame(_slim(sub))
            tails = M.tail_decomposition(v)
            rows.append({
                "model": name, "position": pos, "n": tails["n"],
                "coverage_closed": M.interval_coverage(v),
                "below_q10": tails["below_q10"], "tied_q10": tails["tied_q10"], "inside": tails["inside"],
                "tied_q90": tails["tied_q90"], "above_q90": tails["above_q90"],
                "coverage_tie_aware": tie_aware_coverage(sub, bounds, seed),
            })
    return pl.DataFrame(rows) if rows else pl.DataFrame()


def coverage_paragraph(detail: pl.DataFrame, acc_coverage: Sequence[tuple[str, float, float, bool]],
                       models: ModelSet, band: tuple[float, float] = COVERAGE_BAND) -> str:
    """Neutral description of the tie issue and the options (the owner decides; no recommendation)."""
    focus_cal = models.focus + CAL
    failing = [pos for pos, _, cal, ok in acc_coverage if not ok and not math.isnan(cal)]
    if detail.is_empty() or not failing:
        return ""
    sub = detail.filter(pl.col("model") == focus_cal)
    tied = []
    for pos in failing:
        r = sub.filter(pl.col("position") == pos)
        if r.is_empty():
            continue
        r0 = r.row(0, named=True)
        if max(r0["tied_q10"], r0["tied_q90"]) >= MATERIAL_TIE_SHARE:
            tied.append(
                f"{pos} {_num(r0['coverage_closed'])} closed, of which {r0['tied_q10']:.1%} tie exactly at q10 "
                f"and {r0['tied_q90']:.1%} at q90 ({r0['below_q10']:.1%} below q10, {r0['above_q90']:.1%} above "
                f"q90); the tie-aware diagnostic gives {_num(r0['coverage_tie_aware'])}"
            )
    subject = subject_name(models)
    if not tied:
        return (
            f"{subject} fails the coverage band at {', '.join(failing)}, and ties at the interval ends are not "
            f"material there (below {MATERIAL_TIE_SHARE:.0%} of rows)."
        )
    return (
        "**Interval coverage and point masses.** The acceptance rule counts an actual inside the closed interval "
        f"[q10, q90] and asks for {band[0]:.0%}-{band[1]:.0%} per position after calibration. "
        f"{subject}'s simulated distribution puts a point mass at 0 points for many players, so its q10 is "
        "exactly 0 for them, and every scoreless active game then counts as covered. "
        f"Calibrated ({focus_cal}): " + "; ".join(tied) + ". The online conformal calibrator counts an actual "
        "equal to q10 as at or below q10, so these ties register as no under-coverage there and produce no "
        "offset. The tie-aware coverage (randomized PIT in (0.1, 0.9)) is a labelled diagnostic and is not used "
        "for acceptance. The options are: (1) keep the rule as written, in which case "
        f"{subject[0].lower()}{subject[1:]} fails coverage acceptance at {', '.join(failing)}; (2) adopt a "
        "tie-aware coverage definition before the holdout session; (3) change the calibrator's tie convention, "
        "which would raise q10 above 0 for those players and, if 0 is the correct 10th percentile for them, "
        "worsen q10 pinball loss (that variant was not run). The owner decides; this report does not recommend "
        "one."
    )


# --------------------------------------------------------------------------- #
# Smaller tables
# --------------------------------------------------------------------------- #


def pair_counts(frames: Mapping[str, pl.DataFrame], positions: Sequence[str] = ACCEPT_POSITIONS) -> dict[str, dict[str, int]]:
    """Common-set (D-074) pair counts per position and model; equal across models by construction."""
    out: dict[str, dict[str, int]] = {}
    for pos in positions:
        out[pos] = {}
        for name, f in frames.items():
            sub = f.filter(pl.col("position") == pos)
            gap, _ = M.start_sit_pairs(M.validate_frame(_slim(sub)), common=True)
            out[pos][name] = int(gap.size)
    return out


def by_week_table(frames: Mapping[str, pl.DataFrame], labels: Sequence[str]) -> pl.DataFrame:
    """MAE and 80% coverage by week of season, pooled over seasons and positions."""
    rows = []
    for name in labels:
        if name not in frames:
            continue
        pop = M.population(M.validate_frame(_slim(frames[name])))
        rows.append(
            pop.group_by("week")
            .agg(
                pl.len().alias("n"),
                (pl.col("pred_q50") - pl.col("actual")).abs().mean().alias("mae"),
                ((pl.col("actual") >= pl.col("pred_q10")) & (pl.col("actual") <= pl.col("pred_q90")))
                .cast(pl.Float64)
                .mean()
                .alias("coverage"),
            )
            .with_columns(pl.lit(name).alias("model"))
        )
    if not rows:
        return pl.DataFrame()
    return pl.concat(rows, how="vertical_relaxed").sort(["model", "week"])


def secondary_table(frames: Mapping[str, pl.DataFrame], raw: Sequence[str]) -> pl.DataFrame:
    """All rostered rows, inactive included as 0 points; unconditional columns where available."""
    rows = []
    for name in raw:
        if name not in frames:
            continue
        f = frames[name].filter(pl.col("actual").is_not_null())
        has_uncond = "pred_q50_uncond" in f.columns and f["pred_q50_uncond"].null_count() < f.height
        q50 = pl.col("pred_q50_uncond") if has_uncond else pl.col("pred_q50")
        mean = pl.col("pred_mean_uncond") if has_uncond else pl.col("pred_mean")
        has_avail = "active_prob" in f.columns and f["active_prob"].null_count() < f.height
        p = pl.col("active_prob").clip(1e-6, 1 - 1e-6)
        y = pl.col("active").cast(pl.Float64)
        aggs = [
            pl.len().alias("rows"),
            (~pl.col("active")).sum().alias("inactive"),
            (q50 - pl.col("actual")).abs().mean().alias("mae"),
            (mean - pl.col("actual")).abs().mean().alias("mae_mean"),
        ]
        if has_avail:
            aggs += [
                ((pl.col("active_prob") - y) ** 2).mean().alias("brier_active"),
                (-(y * p.log() + (1 - y) * (1 - p).log())).mean().alias("logloss_active"),
                pl.col("active_prob").mean().alias("mean_active_prob"),
                y.mean().alias("active_rate"),
            ]
        part = (
            f.filter(q50.is_not_null())
            .group_by("position")
            .agg(aggs)
            .with_columns(
                pl.lit(name).alias("model"), pl.lit("uncond" if has_uncond else "conditional").alias("columns")
            )
        )
        rows.append(part)
    return pl.concat(rows, how="diagonal_relaxed") if rows else pl.DataFrame()


def points_by_position(frames: Mapping[str, pl.DataFrame], positions: Sequence[str] = POSITIONS) -> dict[str, dict[str, dict[str, float]]]:
    """Point metrics (no bootstrap) per position and model."""
    out: dict[str, dict[str, dict[str, float]]] = {}
    prepared = prepare_frames(frames)
    for pos in positions:
        for name, f in prepared.items():
            sub = f.filter(pl.col("position") == pos)
            if sub.is_empty():
                continue
            values = M.metric_bundle(sub.columns)(sub)
            values["_n"] = M.population(sub).height  # population size, not a metric
            out.setdefault(pos, {})[name] = values
    return out


# --------------------------------------------------------------------------- #
# Verdict
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class VerdictCell:
    form: str  # "calibrated" or "raw"
    position: str
    metric: str
    comparator: str
    delta: Delta

    @property
    def status(self) -> str:
        return self.delta.status


def verdict_cells(
    main: BootResult,
    consensus: BootResult | None,
    models: ModelSet,
    positions: Sequence[str] = POSITIONS,
    metrics: Sequence[str] = VERDICT_METRICS,
) -> list[VerdictCell]:
    """One cell per form, position, metric and comparator (the complete verdict grid)."""
    cells: list[VerdictCell] = []
    comparators = models.verdict_comparators(consensus is not None)
    for form, suffix in (("calibrated", CAL), ("raw", "")):
        focus = models.focus + suffix
        for pos in positions:
            for metric in metrics:
                if metric.startswith("start_sit") and pos not in M.STARTABLE:
                    continue
                for comp in comparators:
                    if comp == CONSENSUS:
                        if consensus is None or not consensus.has(pos, focus) or not consensus.has(pos, CONSENSUS):
                            continue
                        d = consensus.delta(pos, focus, CONSENSUS, metric)
                    else:
                        other = comp + suffix
                        if not main.has(pos, focus) or not main.has(pos, other):
                            continue
                        d = main.delta(pos, focus, other, metric)
                    cells.append(VerdictCell(form, pos, metric, comp, d))
    return cells


@dataclass
class Acceptance:
    """Outcome of the MODELING.md section 8 rules on the validation seasons."""

    lines: list[str]
    coverage: list[tuple[str, float, float, bool]]  # position, raw, calibrated, within band


def acceptance(
    cells: Sequence[VerdictCell],
    main: BootResult,
    models: ModelSet,
    positions: Sequence[str] = POSITIONS,
    band: tuple[float, float] = COVERAGE_BAND,
) -> Acceptance:
    lines: list[str] = []
    calibrated = [c for c in cells if c.form == "calibrated"]
    point_metrics = ("mae", "pinball_q10", "pinball_q50", "pinball_q90")
    for comp in [b for b in VERDICT_BASELINES if b in models.baselines]:
        for pos in ACCEPT_POSITIONS:
            sub = [c for c in calibrated if c.comparator == comp and c.position == pos and c.metric in point_metrics]
            if not sub:
                continue
            ok = all(c.status == STATUS_BETTER for c in sub)
            failed = [METRIC_LABEL[c.metric] for c in sub if c.status != STATUS_BETTER]
            lines.append(
                f"- MAE and pinball vs `{comp}`, {pos}: **{'PASS' if ok else 'FAIL'}**"
                + ("" if ok else f" (not beaten on {', '.join(failed)})")
            )
    for pos in ACCEPT_POSITIONS:
        sub = [c for c in calibrated if c.comparator == CONSENSUS and c.position == pos and c.metric == "start_sit_common"]
        if not sub:
            continue
        d = sub[0].delta
        ok = d.status != STATUS_WORSE
        lines.append(
            f"- Start/sit not worse than the consensus beyond the interval, {pos}: **{'PASS' if ok else 'FAIL'}** "
            f"(delta {d.cell()})"
        )
    coverage = []
    focus_cal = models.focus + CAL
    for pos in positions:
        raw_cov = main.value(pos, models.focus, "coverage_80")
        cal_cov = main.value(pos, focus_cal, "coverage_80")
        within = (not math.isnan(cal_cov)) and band[0] <= cal_cov <= band[1]
        if not (math.isnan(raw_cov) and math.isnan(cal_cov)):
            coverage.append((pos, raw_cov, cal_cov, within))
    return Acceptance(lines, coverage)


COMPARATOR_NAME: dict[str, str] = {
    CONSENSUS: "the FantasyPros consensus",
    "rolling_avg_4": "the 4-game rolling average (`rolling_avg_4`)",
    "rolling_avg_3": "the 3-game rolling average (`rolling_avg_3`)",
    "rolling_ew_035": "the exponentially weighted average (`rolling_ew_035`)",
    "prior_season": "the prior-season average (`prior_season`)",
    "rolling_median_8": "the 8-game rolling median (`rolling_median_8`)",
    CHALLENGER: "the direct challenger (`direct_lgbm`)",
}
METRIC_PHRASE: dict[str, str] = {
    "mae": "MAE",
    "pinball_q10": "pinball loss at q10",
    "pinball_q50": "pinball loss at q50",
    "pinball_q90": "pinball loss at q90",
    "coverage_gap": "80% coverage gap (distance from 0.80)",
    "spearman_common": "Spearman rank correlation",
    "start_sit_common": "start/sit accuracy",
}
#: metrics measured in points get 2 decimals in sentences, rates and correlations 3
_POINT_METRICS = frozenset({"mae", "mae_mean", "pinball_q10", "pinball_q50", "pinball_q90"})


def subject_name(models: ModelSet) -> str:
    """How sentences name the focus model."""
    if models.focus in FOCUS_CANDIDATES:
        return "The chain"
    return f"The stand-in model `{models.focus}`"


def cell_sentence(c: VerdictCell, subject: str) -> str:
    """One plain sentence for one verdict cell (D-082): beats, does not beat, or does not beat and is worse."""
    comp = COMPARATOR_NAME.get(c.comparator, f"`{c.comparator}`")
    metric = f"{c.position} {METRIC_PHRASE.get(c.metric, c.metric)}"
    digits = 2 if c.metric in _POINT_METRICS else 3
    d = c.delta
    numbers = (
        f"difference {_num(d.delta, digits, True)}, 95% interval {_num(d.lo, digits, True)} to "
        f"{_num(d.hi, digits, True)}"
    )
    form = "calibrated forms" if c.form == "calibrated" else "raw forms"
    if c.comparator == CONSENSUS:
        form = "calibrated" if c.form == "calibrated" else "raw"
    if c.status == STATUS_BETTER:
        return f"{subject} beats {comp} on {metric} ({numbers}; {form})."
    if c.status == STATUS_WORSE:
        return f"{subject} does not beat {comp} on {metric}: it is worse ({numbers}; {form})."
    if c.status == STATUS_NONE:
        return f"{subject} does not beat {comp} on {metric} ({numbers}; {form})."
    return f"{subject} cannot be compared with {comp} on {metric}: no interval ({numbers}; {form})."


def verdict_sentences(cells: Sequence[VerdictCell], subject: str) -> list[str]:
    """One sentence per cell the focus model does not beat, every form and comparator (complete)."""
    return [f"- {cell_sentence(c, subject)}" for c in cells if c.status != STATUS_BETTER]


def consensus_sentences(cells: Sequence[VerdictCell], subject: str) -> list[str]:
    """Every position and metric against the consensus (D-082).

    One sentence per calibrated cell (the headline form, D-078), beaten or
    not, then one per raw cell whose result differs from the calibrated one,
    so every consensus cell of both forms is accounted for.
    """
    cons = [c for c in cells if c.comparator == CONSENSUS]
    calibrated = {(c.position, c.metric): c for c in cons if c.form == "calibrated"}
    out = [f"- {cell_sentence(c, subject)}" for c in cons if c.form == "calibrated"]
    differing = [
        c for c in cons
        if c.form == "raw" and (c.position, c.metric) in calibrated
        and calibrated[(c.position, c.metric)].status != c.status
    ]
    raw_only = [c for c in cons if c.form == "raw" and (c.position, c.metric) not in calibrated]
    extra = differing + raw_only
    if extra:
        out.append("\nRaw forms, where the result differs from the calibrated one:\n")
        out.extend(f"- {cell_sentence(c, subject)}" for c in extra)
    elif any(c.form == "raw" for c in cons):
        out.append("\nIn the raw forms every one of these cells has the same result as in the calibrated forms.")
    return out


def render_verdict(
    cells: Sequence[VerdictCell],
    acc: Acceptance,
    models: ModelSet,
    positions: Sequence[str] = POSITIONS,
    band: tuple[float, float] = COVERAGE_BAND,
    coverage_text: str = "",
) -> str:
    """Markdown for the verdict section: grids, acceptance rules, coverage and the complete list."""
    focus = models.focus
    lines: list[str] = []
    comparators = list(dict.fromkeys(c.comparator for c in cells))
    lines.append(
        f"Focus model `{focus}` (calibrated form `{focus}{CAL}` against calibrated comparators; the "
        "consensus has no calibrated form). Cells read `beats` / `worse` / `no difference` by the CI "
        "rule on `focus - comparator` (B and W tags in the delta tables). These are validation-season "
        "results; the binding test is the single holdout-season run (EV-3.4)."
    )
    for form in ("calibrated", "raw"):
        sub = [c for c in cells if c.form == form]
        if not sub:
            continue
        lines.append(f"\n### Verdict grid, {form}\n")
        header = ["position", "metric", *[f"vs {c}" for c in comparators]]
        rows = []
        for pos in positions:
            for metric in VERDICT_METRICS:
                row_cells = {c.comparator: c for c in sub if c.position == pos and c.metric == metric}
                if not row_cells:
                    continue
                rows.append(
                    [pos, METRIC_LABEL[metric], *[row_cells[c].status if c in row_cells else "" for c in comparators]]
                )
        lines.append(_md_table(header, rows))
    lines.append("\n### Acceptance rules (MODELING.md section 8, calibrated forms)\n")
    lines.extend(acc.lines or ["- no comparator available"])
    lines.append(
        f"\n### Coverage acceptance ({band[0]:.0%}-{band[1]:.0%} per position after calibration, closed interval "
        "[q10, q90], rule as written)\n"
    )

    def within(v: float) -> bool:
        return (not math.isnan(v)) and band[0] <= v <= band[1]

    cov_rows = [
        [pos, _num(raw), "PASS" if within(raw) else "FAIL", _num(cal), "PASS" if ok else "FAIL"]
        for pos, raw, cal, ok in acc.coverage
    ]
    lines.append(_md_table(["position", "raw coverage", "raw result", "calibrated coverage",
                            "calibrated result (acceptance)"], cov_rows))
    subject = subject_name(models)
    lines.append("")
    for pos, raw, cal, ok in acc.coverage:
        lines.append(
            f"- {pos}: **{'PASS' if ok else 'FAIL'}**. {subject}'s calibrated 80% interval covers {_num(cal)} of "
            f"active player-weeks, {'inside' if ok else 'outside'} {band[0]:.2f}-{band[1]:.2f} (raw {_num(raw)}, "
            f"{'inside' if within(raw) else 'outside'} the band)."
        )
    n_pass = sum(1 for *_, ok in acc.coverage if ok)
    lines.append(
        f"\nCoverage after calibration is within the band for {n_pass} of {len(acc.coverage)} positions"
        + (
            "."
            if n_pass == len(acc.coverage)
            else "; outside it: " + ", ".join(f"{p} ({_num(c)})" for p, _, c, ok in acc.coverage if not ok) + "."
        )
    )
    if coverage_text:
        lines.append("\n" + coverage_text)
    subject = subject_name(models)
    if any(c.comparator == CONSENSUS for c in cells):
        lines.append(
            "\n### Against the FantasyPros consensus: every position and metric (D-081, D-082)\n\n"
            "On identical player-weeks (section 8). The rule (D-078) is fixed in advance and is not changed "
            "after seeing results.\n"
        )
        lines.extend(consensus_sentences(cells, subject))
    others = [c for c in cells if c.comparator != CONSENSUS]
    lines.append("\n### Against the other comparators: every cell not beaten (complete list)\n")
    sentences = verdict_sentences(others, subject)
    lines.extend(sentences or ["- none: every cell of both grids is `beats`."])
    return "\n".join(lines)


# --------------------------------------------------------------------------- #
# Markdown helpers
# --------------------------------------------------------------------------- #


def _num(value: Any, digits: int = 3, sign: bool = False) -> str:
    if value is None:
        return ""
    if isinstance(value, (int, np.integer)) and not isinstance(value, bool):
        return f"{int(value):,}"
    try:
        v = float(value)
    except (TypeError, ValueError):
        return str(value)
    if math.isnan(v):
        return "n/a"
    if sign:
        return f"{v:+.{digits}f}"
    return f"{v:.{digits}f}"


def _md_table(header: Sequence[str], rows: Sequence[Sequence[Any]]) -> str:
    if not rows:
        return "(no rows)"
    out = ["| " + " | ".join(header) + " |", "| " + " | ".join("---" for _ in header) + " |"]
    for row in rows:
        out.append("| " + " | ".join(str(v) for v in row) + " |")
    return "\n".join(out)


def _frame_md(frame: pl.DataFrame, digits: int = 3) -> str:
    if frame.is_empty():
        return "(no rows)"
    rows = [[_num(v, digits) if not isinstance(v, str) else v for v in r] for r in frame.iter_rows()]
    return _md_table(frame.columns, rows)


# --------------------------------------------------------------------------- #
# Sections
# --------------------------------------------------------------------------- #


def _headline_section(main: BootResult, labels: Sequence[str], pairs: Mapping[str, Mapping[str, int]],
                      positions: Sequence[str]) -> str:
    out = []
    header = ["model", "n", "MAE", "MAE mean", "pin q10", "pin q50", "pin q90", "cov80", "width",
              "Spearman", "start/sit", "start/sit own", "Spearman own"]
    for pos in positions:
        if pos not in main.points:
            continue
        rows = []
        for m in labels:
            if not main.has(pos, m):
                continue
            v = main.points[pos][m]
            rows.append([
                f"`{m}`", _num(main.n[pos][m]), _num(v.get("mae")), _num(v.get("mae_mean")),
                _num(v.get("pinball_q10")), _num(v.get("pinball_q50")), _num(v.get("pinball_q90")),
                _num(v.get("coverage_80")), _num(v.get("interval_width"), 2),
                _num(v.get("spearman_common")), _num(v.get("start_sit_common")),
                _num(v.get("start_sit")), _num(v.get("spearman")),
            ])
        note = ""
        if pos in pairs:
            counts = set(pairs[pos].values())
            note = (
                f"\n\nCommon pair set (D-074): {_num(next(iter(counts)))} pairs, identical for every model."
                if len(counts) == 1
                else f"\n\nCommon pair set counts differ across models ({sorted(counts)}): FINDING, check alignment."
            )
        out.append(f"\n#### {pos}\n\n" + _md_table(header, rows) + note)
    return "\n".join(out)


def _delta_section(main: BootResult, models: ModelSet, positions: Sequence[str],
                   metrics: Sequence[str] = DELTA_METRICS) -> tuple[str, list[dict[str, Any]]]:
    out = []
    records: list[dict[str, Any]] = []
    header = ["comparator", "form", *[METRIC_LABEL[m] for m in metrics]]
    for pos in positions:
        if pos not in main.points:
            continue
        rows = []
        for comp in models.comparators():
            for form, suffix in (("raw", ""), ("calibrated", CAL)):
                f, o = models.focus + suffix, comp + suffix
                if not (main.has(pos, f) and main.has(pos, o)):
                    continue
                cells = []
                for metric in metrics:
                    if metric.startswith("start_sit") and pos not in M.STARTABLE:
                        cells.append("n/a")
                        continue
                    d = main.delta(pos, f, o, metric)
                    cells.append(d.cell())
                    records.append({"population": "headline", "position": pos, "form": form, "focus": f,
                                    "other": o, "metric": metric, "focus_value": d.focus_value,
                                    "other_value": d.other_value, "delta": d.delta, "lo": d.lo, "hi": d.hi,
                                    "status": d.status})
                rows.append([f"`{comp}`", form, *cells])
        out.append(f"\n#### {pos}\n\n" + _md_table(header, rows))
    return "\n".join(out), records


def _lineup_section(main: BootResult, models: ModelSet) -> tuple[str, list[dict[str, Any]]]:
    if "ALL" not in main.points:
        return "(no lineup results)", []
    rows = []
    records = []
    for m in [*models.raw, *[x + CAL for x in models.raw]]:
        if not main.has("ALL", m):
            continue
        val = main.value("ALL", m, "lineup_efficiency")
        focus = models.focus + (CAL if m.endswith(CAL) else "")
        if m == focus:
            cell = "(focus)"
        else:
            d = main.delta("ALL", focus, m, "lineup_efficiency")
            cell = d.cell()
            records.append({"population": "headline", "position": "ALL", "form": "calibrated" if m.endswith(CAL) else "raw",
                            "focus": focus, "other": m, "metric": "lineup_efficiency", "focus_value": d.focus_value,
                            "other_value": d.other_value, "delta": d.delta, "lo": d.lo, "hi": d.hi, "status": d.status})
        rows.append([f"`{m}`", _num(val), cell])
    return _md_table(["model", "lineup efficiency", "focus - model [95% CI]"], rows), records


def _by_week_section(table: pl.DataFrame, focus: str, best: str) -> str:
    if table.is_empty():
        return "(no rows)"
    def col(model: str, metric: str) -> dict[int, float]:
        sub = table.filter(pl.col("model") == model)
        return dict(zip(sub["week"].to_list(), sub[metric].to_list(), strict=True))
    n = col(focus, "n")
    f_mae, b_mae = col(focus, "mae"), col(best, "mae")
    f_cov, f_cov_cal = col(focus, "coverage"), col(focus + CAL, "coverage")
    b_cov_cal = col(best + CAL, "coverage")
    rows = []
    for w in sorted(n):
        rows.append([w, _num(n[w]), _num(f_mae.get(w)), _num(b_mae.get(w)), _num(f_mae.get(w, math.nan) - b_mae.get(w, math.nan), sign=True),
                     _num(f_cov.get(w)), _num(f_cov_cal.get(w)), _num(b_cov_cal.get(w))])
    header = ["week", "n", f"MAE `{focus}`", f"MAE `{best}`", "difference", f"cov `{focus}`",
              f"cov `{focus}{CAL}`", f"cov `{best}{CAL}`"]
    return _md_table(header, rows)


def _calibration_section(frames: Mapping[str, pl.DataFrame], main: BootResult, models: ModelSet,
                         positions: Sequence[str]) -> str:
    focus, focus_cal = models.focus, models.focus + CAL
    out = []
    has_probs = focus in frames and "boom_prob" in frames[focus].columns and (
        frames[focus]["boom_prob"].null_count() < frames[focus].height
    )
    if has_probs:
        header = ["position", "Brier boom raw", "Brier boom cal", "slope boom raw", "slope boom cal",
                  "Brier bust raw", "Brier bust cal", "slope bust raw", "slope bust cal"]
        rows = []
        for pos in positions:
            if not main.has(pos, focus):
                continue
            rows.append([pos] + [
                _num(main.value(pos, m, metric))
                for metric in ("brier_boom", "calib_slope_boom", "brier_bust", "calib_slope_bust")
                for m in (focus, focus_cal)
            ])
        out.append("Brier scores and logistic calibration slopes (1.0 is calibrated spread) of `boom_prob` "
                   "and `bust_prob` (D-017, D-067: conditional on active) for the focus model.\n")
        out.append(_md_table(header, rows))
        for kind in ("boom", "bust"):
            tables = {}
            for m in (focus, focus_cal):
                if m in frames:
                    tables[m] = M.reliability_table(M.validate_frame(_slim(frames[m])), f"{kind}_prob", f"{kind}_actual")
            if not tables:
                continue
            rows = []
            raw_t = tables.get(focus)
            cal_t = tables.get(focus_cal)
            for b in range(10):
                r = raw_t.row(b, named=True) if raw_t is not None else {}
                c = cal_t.row(b, named=True) if cal_t is not None else {}
                rows.append([f"{b / 10:.1f}-{(b + 1) / 10:.1f}", _num(r.get("n")), _num(r.get("mean_prob")),
                             _num(r.get("outcome_rate")), _num(c.get("n")), _num(c.get("mean_prob")),
                             _num(c.get("outcome_rate"))])
            out.append(f"\nReliability of `{kind}_prob`, all positions pooled:\n")
            out.append(_md_table(["bin", "n raw", "mean prob raw", "rate raw", "n cal", "mean prob cal", "rate cal"], rows))
    else:
        out.append(f"`{focus}` has no `boom_prob` / `bust_prob`; boom and bust reliability is reported for the chain only.\n")
    # PIT for every model with PIT values
    labels = [m for m in [*models.raw, *[x + CAL for x in models.raw]] if m in frames]
    header = ["model", *positions]
    rows = []
    for m in labels:
        rows.append([f"`{m}`", *[_num(main.value(p, m, "pit_max_dev")) for p in positions]])
    out.append("\nPIT deviation: the largest absolute gap between a PIT decile's share and 0.10 "
               "(0 is a uniform PIT; harness mid-point PIT through q05..q95 and position bounds, D-071).\n")
    out.append(_md_table(header, rows))
    for m in (focus, focus_cal):
        if m not in frames:
            continue
        hist = M.pit_histogram(M.validate_frame(_slim(frames[m])))
        out.append(f"\nPIT histogram `{m}`, all positions pooled: "
                   + ", ".join(f"{r['lo']:.1f}: {r['share']:.3f}" for r in hist.iter_rows(named=True)) + ".")
    return "\n".join(out)


def _coverage_section(detail: pl.DataFrame, main: BootResult, models: ModelSet, positions: Sequence[str]) -> str:
    out = []
    if detail.is_empty():
        return "(no rows)"
    out.append(
        "Exclusive shares of active rows relative to [q10, q90] (ties within 1e-9 points): below q10, tied at q10, "
        "strictly inside, tied at q90 (and not at q10), above q90. Closed coverage, the acceptance definition, is "
        "tied q10 + inside + tied q90. The last column is a **diagnostic, not used for acceptance**: the share of "
        "rows whose randomized PIT lies in (0.1, 0.9), with the PIT drawn uniformly in [F(y-), F(y)] from the "
        "quantile knots q05..q95 and the prior-season position bounds (seed 0), so a point mass is spread over "
        "its probability instead of counting as inside.\n"
    )
    order = {p: i for i, p in enumerate(POSITIONS)}
    t = detail.with_columns(pl.col("position").replace_strict(order, default=99).alias("_o")).sort(["_o", "model"])
    rows = []
    for r in t.iter_rows(named=True):
        rows.append([r["position"], f"`{r['model']}`", _num(int(r["n"])), _num(r["below_q10"]), _num(r["tied_q10"]),
                     _num(r["inside"]), _num(r["tied_q90"]), _num(r["above_q90"]), _num(r["coverage_closed"]),
                     _num(r["coverage_tie_aware"])])
    out.append(_md_table(["position", "model", "n", "below q10", "tied q10", "inside", "tied q90", "above q90",
                          "closed coverage", "tie-aware (diagnostic)"], rows))
    focus_cal = models.focus + CAL
    comps = [m + CAL for m in models.comparators()]
    header = ["position", "comparator", f"pinball q10 `{focus_cal}`", "comparator", "difference [95% CI]",
              f"pinball q90 `{focus_cal}`", "comparator", "difference [95% CI]"]
    prow = []
    for pos in positions:
        for comp in comps:
            if not (main.has(pos, focus_cal) and main.has(pos, comp)):
                continue
            d10 = main.delta(pos, focus_cal, comp, "pinball_q10")
            d90 = main.delta(pos, focus_cal, comp, "pinball_q90")
            prow.append([pos, f"`{comp}`", _num(d10.focus_value), _num(d10.other_value), d10.cell(),
                         _num(d90.focus_value), _num(d90.other_value), d90.cell()])
    out.append(
        "\nPinball loss at q10 and q90 (the proper scoring rule for each quantile), calibrated forms; difference is "
        f"`{focus_cal} - comparator`, negative favours the focus model, B/W by the D-078 rule.\n"
    )
    out.append(_md_table(header, prow))
    return "\n".join(out)


def _secondary_section(table: pl.DataFrame) -> str:
    if table.is_empty():
        return "(no rows)"
    cols = ["model", "columns", "position", "rows", "inactive", "mae", "mae_mean"]
    extra = [c for c in ("brier_active", "logloss_active", "mean_active_prob", "active_rate") if c in table.columns]
    order = {p: i for i, p in enumerate(POSITIONS)}
    t = table.with_columns(pl.col("position").replace_strict(order, default=99).alias("_o")).sort(["model", "_o"])
    return _frame_md(t.select([*cols, *extra]))


def _scoring_section(points: Mapping[str, Mapping[str, Mapping[str, float]]], raw: Sequence[str],
                     positions: Sequence[str]) -> str:
    out = []
    for title, metric, suffix in (("MAE (raw)", "mae", ""), ("80% coverage after calibration", "coverage_80", CAL)):
        rows = []
        for pos in positions:
            if pos not in points:
                continue
            rows.append([pos, *[_num(points[pos].get(m + suffix, {}).get(metric)) for m in raw]])
        out.append(f"{title}:\n\n" + _md_table(["position", *[f"`{m}`" for m in raw]], rows))
    return "\n\n".join(out)


def matched_table_labels(models: ModelSet, best: str, present: Iterable[str]) -> list[str]:
    """Models shown in the matched consensus table (D-081): focus and challenger raw and calibrated,
    the section 8 baselines and the best baseline calibrated, and the consensus."""
    present = set(present)
    wanted = [models.focus, models.focus + CAL]
    if models.challenger and models.challenger != models.focus:
        wanted += [models.challenger, models.challenger + CAL]
    for b in [*VERDICT_BASELINES, best]:
        if b and b != models.focus:
            wanted.append(b + CAL)
    wanted.append(CONSENSUS)
    return [m for m in dict.fromkeys(wanted) if m in present]


def _consensus_section(cons: BootResult, cons_points: Mapping[str, Mapping[str, Mapping[str, float]]],
                       models: ModelSet, positions: Sequence[str], labels: Sequence[str],
                       identical: bool) -> tuple[str, list[dict[str, Any]]]:
    out = []
    records: list[dict[str, Any]] = []
    metrics = ("mae", "pinball_q10", "pinball_q50", "pinball_q90", "coverage_80", "spearman_common",
               "start_sit_common")
    header = ["position", "model", "n", *[METRIC_LABEL[m] for m in metrics]]
    rows = []
    for pos in positions:
        for m in labels:
            if m not in cons_points.get(pos, {}):
                continue
            v = cons_points[pos][m]
            n = v.get("_n")
            rows.append([pos, f"`{m}`", _num(int(n)) if n is not None else "",
                         *[_num(v.get(x)) for x in metrics]])
    out.append(
        "Row keys identical for every model in this table: "
        + ("**yes** (same player-weeks, same actuals)." if identical
           else "**NO: FINDING**, the populations differ; the comparison below is not valid.")
        + "\n"
    )
    out.append(_md_table(header, rows))
    delta_metrics = [*[m for m in metrics if m != "coverage_80"], "coverage_80", "coverage_gap"]
    dheader = ["position", "focus form", *[METRIC_LABEL[m] for m in delta_metrics]]
    drows = []
    for pos in positions:
        for suffix, form in (("", "raw"), (CAL, "calibrated")):
            f = models.focus + suffix
            if not (cons.has(pos, f) and cons.has(pos, CONSENSUS)):
                continue
            cells = []
            for metric in delta_metrics:
                if metric.startswith("start_sit") and pos not in M.STARTABLE:
                    cells.append("n/a")
                    continue
                d = cons.delta(pos, f, CONSENSUS, metric)
                cells.append(d.cell())
                records.append({"population": "consensus", "position": pos, "form": form, "focus": f,
                                "other": CONSENSUS, "metric": metric, "focus_value": d.focus_value,
                                "other_value": d.other_value, "delta": d.delta, "lo": d.lo, "hi": d.hi,
                                "status": d.status})
            drows.append([pos, form, *cells])
    out.append(
        f"\nDifferences `{models.focus} - consensus_ecr` on these identical player-weeks [95% paired "
        "week-clustered interval, D-078]; B: the focus model beats the consensus, W: it is worse. The 80% "
        "coverage difference has no direction (no tag); its verdict uses the coverage gap.\n"
    )
    out.append(_md_table(dheader, drows))
    return "\n".join(out), records


def _thursday_section(counts: pl.DataFrame, pop_points: Mapping[str, Mapping[str, Mapping[str, Mapping[str, float]]]],
                      split: Sequence[SplitDelta], models: ModelSet, best: str,
                      positions: Sequence[str]) -> str:
    focus_cal, best_cal = models.focus + CAL, best + CAL
    out = []
    header = ["position", *[STATUS_LABEL[s] for s in STATUS_ORDER], "(c) all active", "sum check"]
    rows = []
    for r in counts.iter_rows(named=True):
        total = sum(int(r[s]) for s in STATUS_ORDER)
        rows.append([r["position"], *[_num(int(r[s])) for s in STATUS_ORDER], _num(int(r["all"])),
                     "ok" if total == r["all"] else f"MISMATCH ({total})"])
    out.append("Active player-weeks by consensus status (PPR, 2022-2024). Columns (a) and (b) are the populations "
               "compared below; the other exclusion reasons are counted separately and are not part of (b).\n")
    out.append(_md_table(header, rows))
    metrics = ("mae", "pinball_q10", "pinball_q50", "pinball_q90", "coverage_80", "spearman_common",
               "start_sit_common")
    mheader = ["position", "population", "model", "n", *[METRIC_LABEL[m] for m in metrics]]
    mrows = []
    for pos in positions:
        for pop in (POP_MATCHED, POP_EARLY, POP_ALL):
            for label in (focus_cal, best_cal):
                v = pop_points.get(pop, {}).get(pos, {}).get(label)
                if v is None:
                    continue
                mrows.append([pos, POP_LABEL[pop], f"`{label}`", _num(int(v.get("_n", 0))),
                              *[_num(v.get(m)) for m in metrics]])
    out.append(
        f"\nCalibrated forms of `{models.focus}` and the best baseline `{best}` on the three populations. "
        "Spearman and start/sit use the headline startable set (D-077) restricted to each population; with only "
        "a few early-game players per position-week, they are often undefined there (n/a).\n"
    )
    out.append(_md_table(mheader, mrows))
    dheader = ["position", "(a) matched", "(b) early game", "(c) all active", "(b) - (a)"]
    drows = []
    for s in split:
        def cell(t: tuple[float, float, float]) -> str:
            d, lo, hi = t
            return f"{_num(d, 3, True)} [{_num(lo, 3, True)}, {_num(hi, 3, True)}]"
        drows.append([s.group, *[cell(s.deltas[p]) for p in (POP_MATCHED, POP_EARLY, POP_ALL)], cell(s.did)])
    out.append(f"\nMAE of `{focus_cal}` minus MAE of `{best_cal}` [95% paired week-clustered interval]; the last "
               "column is the early-game difference minus the matched difference (negative: the focus model's "
               "advantage is larger on early-game player-weeks).\n")
    out.append(_md_table(dheader, drows))
    pooled = next((s for s in split if s.group == "ALL"), None)
    if pooled is not None:
        out.append("\n" + thursday_sentence(pooled, subject_name(models), best_cal))
    return "\n".join(out)


def _ablation_section(boot: BootResult | None, focus: str, variants: Sequence[str], positions: Sequence[str],
                      title: str) -> tuple[str, list[dict[str, Any]]]:
    if boot is None or not variants:
        return "(none)", []
    metrics = ("mae", "pinball_q10", "pinball_q50", "pinball_q90", "coverage_gap", "spearman_common",
               "start_sit_common")
    header = ["position", "variant", *[METRIC_LABEL[m] for m in metrics]]
    rows = []
    records: list[dict[str, Any]] = []
    for pos in positions:
        for v in variants:
            if not (boot.has(pos, focus) and boot.has(pos, v)):
                continue
            cells = []
            for metric in metrics:
                if metric.startswith("start_sit") and pos not in M.STARTABLE:
                    cells.append("n/a")
                    continue
                d = boot.delta(pos, focus, v, metric)
                cells.append(d.cell())
                records.append({"population": title, "position": pos, "form": "raw" if not v.endswith(CAL) else "calibrated",
                                "focus": focus, "other": v, "metric": metric, "focus_value": d.focus_value,
                                "other_value": d.other_value, "delta": d.delta, "lo": d.lo, "hi": d.hi,
                                "status": d.status})
            rows.append([pos, f"`{v}`", *cells])
    return _md_table(header, rows), records


def audit_summary(reports_dir: Path, audit_name: str | None = None) -> tuple[str | None, list[str]]:
    """The overall block of an evaluator audit report (latest ``evaluator_audit_*`` by name by default)."""
    if audit_name is None:
        candidates = sorted(reports_dir.glob("evaluator_audit_*.md"))
        if not candidates:
            return None, []
        path = candidates[-1]
    else:
        path = reports_dir / f"{audit_name}.md"
        if not path.exists():
            return None, []
    lines = path.read_text(encoding="utf-8").splitlines()
    block: list[str] = []
    for line in lines[1:]:
        if line.startswith("## "):
            break
        if line.startswith("Overall:"):
            block.append(line)
    sections: list[str] = []
    current: str | None = None
    for line in lines:
        if line.startswith("## "):
            current = line[3:].strip()
            continue
        if current is None:
            continue
        if line.startswith("Overall:"):
            sections.append(f"- {current}: {line[len('Overall:'):].strip()}")
        elif line.startswith("| ") and ("| FAIL |" in line or "| WARN |" in line):
            cells = [c.strip() for c in line.strip().strip("|").split("|")]
            if len(cells) >= 3:
                sections.append(f"  - {cells[1]}: {cells[0]}: {cells[2]}")
    return path.stem, block + sections


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def _cons_frame(seasons: Sequence[int]) -> pl.DataFrame:  # pragma: no cover - reads restricted data
    from ffml.eval.ecr_baseline import consensus_predictions

    return consensus_predictions(seasons, "ppr", purpose="backtest")


def _cons_exclusions(keys: pl.DataFrame, seasons: Sequence[int]) -> pl.DataFrame:  # pragma: no cover
    """Consensus status reasons for ``keys`` (reads restricted data; result stays in memory, D-007)."""
    from ffml.eval.ecr_baseline import consensus_exclusions

    return consensus_exclusions(keys, seasons, purpose="backtest")


def _bounds(seasons: Sequence[int]) -> pl.DataFrame:  # pragma: no cover - reads processed data
    """Prior-season position bounds from the evaluator's reference PPR points (all seasons before each)."""
    from ffml.eval.ecr_baseline import reference_actuals

    history = list(range(int(get_settings().first_season), max(int(s) for s in seasons)))
    return position_bounds(reference_actuals(history, purpose="backtest"), seasons)


def _reference(seasons: Sequence[int]) -> pl.DataFrame:  # pragma: no cover - reads processed data
    from ffml.eval.ecr_baseline import reference_actuals

    return reference_actuals(seasons, purpose="backtest")


def build_backtest_report(
    run_id: str | Path,
    out_name: str,
    ablation_run_id: str | Path | None = None,
    *,
    n_boot: int = 500,
    seed: int = 0,
    n_jobs: int = 4,
    focus: str | None = None,
    include_consensus: bool = True,
    cross_check: bool = True,
    audit_name: str | None = None,
    reports_dir: Path | None = None,
    data_dir: Path | None = None,
    positions: Sequence[str] = POSITIONS,
) -> Path:
    """Write ``reports/<out_name>.md`` and ``data/reports/<out_name>_*.csv`` from one backtest run.

    ``ablation_run_id`` names a second run whose chain variants (for example
    ``no_vegas``, ``no_ngs``, ``depth_same_week``) are compared with the focus
    model of the main run. ``include_consensus`` and ``cross_check`` read
    restricted and processed data; tests switch them off.
    """
    ensure_utf8()
    started = time.time()
    reports_dir = Path(reports_dir) if reports_dir is not None else PROJECT_ROOT / "reports"
    data_dir = Path(data_dir) if data_dir is not None else PROJECT_ROOT / "data" / "reports"
    run_dir = resolve_run_dir(run_id)
    manifest = read_manifest(run_dir)
    report_seasons = [int(s) for s in manifest["report_seasons"]]
    ppr = load_backtest(run_dir, "ppr")
    present = set(ppr["model"].unique().to_list())
    models = resolve_models(present, focus)
    raw = [m for m in models.raw if m in present]
    ref_models = reference_models(models, present)
    aligned, before, dropped = headline_frames(ppr, raw, present, ref_models)
    labels = list(aligned)

    main = run_bootstrap(aligned, positions, n_boot=n_boot, seed=seed, n_jobs=n_jobs)
    pairs = pair_counts(aligned)

    # best baseline by pooled raw MAE on the headline population
    pooled_mae = {}
    for b in models.baselines:
        if b in aligned:
            pop = M.population(M.validate_frame(_slim(aligned[b])))
            pooled_mae[b] = float((pop["pred_q50"] - pop["actual"]).abs().mean())
    best = min(pooled_mae, key=pooled_mae.get) if pooled_mae else models.focus

    # consensus on identical player-weeks, and the Thursday effect (PPR only, D-081)
    cons_boot: BootResult | None = None
    cons_points: dict[str, dict[str, dict[str, float]]] = {}
    cons_rows = 0
    cons_frames: dict[str, pl.DataFrame] = {}
    table_labels: list[str] = []
    identical = False
    counts = pl.DataFrame()
    pop_points: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    split: list[SplitDelta] = []
    if include_consensus:
        consensus = _cons_frame(report_seasons)
        cons_rows = consensus.height
        cons_frames = consensus_population(consensus, aligned, raw, ref_models)
        table_labels = matched_table_labels(models, best, cons_frames)
        identical = population_keys_identical({m: cons_frames[m] for m in table_labels})
        boot_labels = [models.focus, models.focus + CAL, CONSENSUS]
        cons_boot = run_bootstrap({m: cons_frames[m] for m in boot_labels if m in cons_frames}, positions,
                                  lineup=False, n_boot=n_boot, seed=seed, n_jobs=n_jobs)
        cons_points = points_by_position({m: cons_frames[m] for m in table_labels}, positions)
        active_keys = population_keys(aligned[models.focus])
        matched_keys = population_keys(cons_frames[models.focus])
        unmatched = active_keys.join(matched_keys, on=list(M.KEY_COLUMNS), how="anti")
        status = consensus_status(active_keys, matched_keys, _cons_exclusions(unmatched, report_seasons))
        counts = status_counts(status, positions)
        split_labels = [m for m in (models.focus + CAL, best + CAL) if m in aligned]
        if len(split_labels) == 2:
            pops = split_populations({m: aligned[m] for m in split_labels}, status)
            pop_points = {p: points_by_position(fr, positions) for p, fr in pops.items()}
            split = split_mae_deltas(pops, models.focus + CAL, best + CAL, positions, n_boot=n_boot, seed=seed)

    # scoring cross-check (D-052)
    xcheck: CrossCheck | None = None
    if cross_check:
        harness_keys = (
            ppr.select("entity_id", "position", "season", "week", "actual", "active")
            .unique(["entity_id", "season", "week"], keep="first")
        )
        xcheck = scoring_cross_check(harness_keys, _reference(report_seasons))

    # other scorings
    other_points: dict[str, dict[str, dict[str, dict[str, float]]]] = {}
    for scoring in SCORINGS[1:]:
        frame = load_backtest(run_dir, scoring)
        if frame.is_empty():
            continue
        sp = set(frame["model"].unique().to_list())
        f_al, _, _ = headline_frames(frame, [m for m in raw if m in sp], sp, [m for m in ref_models if m in sp])
        other_points[scoring] = points_by_position(f_al, positions)

    # ablations and the final-report sensitivity
    sens_boot = None
    sens_variants: list[str] = []
    if models.final_report:
        sens_variants = [models.final_report, models.final_report + CAL]
    abl_boot = None
    abl_variants: list[str] = []
    abl_manifest: dict[str, Any] | None = None
    if ablation_run_id is not None:
        abl_dir = resolve_run_dir(ablation_run_id)
        abl_manifest = read_manifest(abl_dir)
        abl = load_backtest(abl_dir, "ppr")
        abl_present = set(abl["model"].unique().to_list())
        abl_variants = sorted(m for m in abl_present if m not in labels)
        frames = {m: aligned[m] for m in (models.focus, models.focus + CAL) if m in aligned}
        frames.update({m: abl.filter(pl.col("model") == m) for m in abl_variants})
        a_al, _ = M.align_frames(frames)
        a_al = M.with_reference_rank(a_al, reference=[m for m in a_al if not m.endswith(CAL)])
        abl_boot = run_bootstrap(a_al, positions, lineup=False, n_boot=n_boot, seed=seed, n_jobs=n_jobs)
        abl_variants = [m for m in abl_variants if not m.endswith(CAL)] + [m for m in abl_variants if m.endswith(CAL)]
    if sens_variants:
        sens_boot = main

    # by week
    week_table = by_week_table(aligned, [models.focus, models.focus + CAL, best, best + CAL])
    secondary = secondary_table(aligned, raw)

    # per-week CSV (all models, headline population; consensus population appended)
    concat = pl.concat([prepare_frames({m: aligned[m]})[m] for m in labels], how="diagonal_relaxed")
    ev = M.evaluate(concat, by=("position",), per_week=True, n_boot=0)
    per_week = ev.per_week.with_columns(pl.lit("headline").alias("population")) if ev.per_week is not None else None
    if include_consensus and cons_frames:
        cconcat = pl.concat([prepare_frames({m: cons_frames[m]})[m] for m in cons_frames], how="diagonal_relaxed")
        cev = M.evaluate(cconcat, by=("position",), per_week=True, n_boot=0)
        if cev.per_week is not None:
            cw = cev.per_week.with_columns(pl.lit("consensus").alias("population"))
            per_week = cw if per_week is None else pl.concat([per_week, cw], how="diagonal_relaxed")

    cells = verdict_cells(main, cons_boot, models, positions)
    acc = acceptance(cells, main, models, positions)

    # interval coverage detail (PPR): tails, ties, tie-aware diagnostic
    bounds = _bounds(report_seasons) if cross_check else None
    detail_labels = [models.focus, *[m + CAL for m in models.raw]]
    detail = coverage_detail(aligned, detail_labels, bounds, positions, seed=seed)
    coverage_text = coverage_paragraph(detail, acc.coverage, models)

    # ---------------------------------------------------------------- render
    file_sha = _sha256(run_dir / PREDICTIONS)
    manifest_sha = manifest.get("files", {}).get(PREDICTIONS)
    head = _git("rev-parse", "--short", "HEAD")
    eval_dirty = bool(_git("status", "--porcelain", "--", "ffml/eval").strip())
    lines: list[str] = []
    title_date = out_name.removeprefix("backtest_")
    lines.append(f"# Walk-forward validation report {title_date}\n")
    lines.append("Independent Evaluator, EV-3.3 (MODELING.md sections 2, 3, 6-9, 11). Aggregate metrics only "
                 "(D-007). Generated by `ffml/eval/report_backtest.py` "
                 f"(`build_backtest_report({str(run_id)!r}, {out_name!r}"
                 + (f", ablation_run_id={str(ablation_run_id)!r}" if ablation_run_id else "")
                 + (f", focus={focus!r}" if focus else "")
                 + f")`, {n_boot} bootstrap draws, seed {seed}).\n")
    lines.append(
        f"> **Holdout session pending.** The {get_settings().holdout_season} holdout season has not been "
        "evaluated. It will be evaluated once, in a single evaluator session (EV-3.4, D-049), after the owner "
        "has reviewed this report (D-084). Every number below comes from the 2022-2024 validation seasons.\n"
    )
    if models.draft_note:
        lines.append(f"> **{models.draft_note}**\n")

    lines.append("## 1. Setup\n")
    manifest_models = {m.get("key"): m for m in manifest.get("models", [])}
    lines.append(_md_table(["item", "value"], [
        ["run id", f"`{manifest.get('run_id')}` (config `{manifest.get('config')}`)"],
        ["predictions file sha256", f"`{file_sha[:16]}...`" + (" (matches the manifest)" if manifest_sha == file_sha
                                                               else f" (manifest records `{str(manifest_sha)[:16]}...`)")],
        ["git sha of the run", f"`{manifest.get('git')}` (sessions: {', '.join(f'`{g}`' for g in manifest.get('git_sessions', []))})"],
        ["git sha of this report", f"`{head}`" + (" (with uncommitted changes under ffml/eval)" if eval_dirty
                                                 else " (ffml/eval committed)")],
        ["feature version", f"`{manifest.get('feature_version')}`"],
        ["run seasons", ", ".join(str(s) for s in manifest.get("seasons", []))],
        ["reported seasons", ", ".join(str(s) for s in report_seasons)
         + f" (warm-up for calibration: {', '.join(str(s) for s in manifest.get('warmup_seasons', []))})"],
        ["weeks", f"{min(manifest.get('weeks', [0]))}-{max(manifest.get('weeks', [0]))}, {manifest.get('schedule_weeks')} scheduled weeks in the run"],
        ["refit cadence", f"every {manifest.get('refit_every')} week(s), walk-forward (fit on rows before each week)"],
        ["scorings", ", ".join(f"{k} `{v}`" for k, v in manifest.get("scorings", {}).items())],
        ["calibration", f"{manifest.get('calibration', {}).get('conformal', '')}; window: {manifest.get('calibration', {}).get('window', '')}"],
        ["input snapshots", "; ".join(f"{k}: {v}" for grp in manifest.get("input_snapshots", {}).values() for k, v in grp.items())],
        ["models in the run", ", ".join(f"`{k}`" for k in manifest_models)],
        ["focus model", f"`{models.focus}`" + (f"; sensitivity `{models.final_report}`" if models.final_report else "")],
    ]))
    lines.append("\nPopulations (PPR, reported seasons):\n")
    pop_rows = []
    for m in labels:
        pop_rows.append([f"`{m}`", _num(before[m]), _num(dropped[m]), _num(aligned[m].height),
                         _num(M.population(M.validate_frame(_slim(aligned[m]))).height)])
    lines.append(_md_table(["model", "rows", "dropped by alignment", "aligned rows", "headline population (active)"], pop_rows))
    lines.append(
        "\n- Headline population: rows every headline model predicts (D-050), then active players with an "
        "actual and a projection (MODELING.md section 7). Predictions are conditional on active (protocol).\n"
        "- Secondary population (section 6 below): every rostered row including inactive players scored as 0, "
        "using `pred_q50_uncond` / `pred_mean_uncond` for models with `active_prob`.\n"
        + (f"- Consensus population (PPR only): rows with a valid consensus rank ({_num(cons_rows)} consensus rows "
           f"in {', '.join(str(s) for s in report_seasons)}) on the headline keys; players whose game precedes the "
           "Friday scrape (Thursday games) are excluded (D-036), so this population is smaller than the headline one.\n"
           if include_consensus else "- Consensus population: not built in this run of the report.\n")
    )

    lines.append("## 2. Scoring cross-check (D-052)\n")
    if xcheck is None:
        lines.append("Not run.\n")
    else:
        lines.append(
            f"The evaluator's reference PPR points (`ffml/eval/ecr_baseline.py`, computed from `fact_player_game` "
            f"and `fact_dst_game` without `ffml/scoring`) agree with the harness `actual` (`ffml/scoring`, PPR preset) "
            f"on **{_num(xcheck.matched)} of {_num(xcheck.rows)} rows ({xcheck.rate:.4%})** of the union of model "
            f"universes, 2022-2024. Active flags differ on {xcheck.active_mismatch} rows; "
            f"{xcheck.missing_reference} harness rows have no reference row.\n"
        )
        lines.append(_frame_md(xcheck.by_position, 4))
        if xcheck.explained["emergency_kicker"]:
            lines.append(
                f"\n{xcheck.explained['emergency_kicker']} difference(s) equal the row's own kicking points for a "
                "non-kicker (a position player who kicked, for example an emergency kicker). `ffml/scoring` scores "
                "kicking stats for every non-DST row, as Sleeper does; the evaluator reference adds kicking points "
                "only for kickers. The harness value is the correct one for league scoring; the reference is a "
                "documented simplification."
            )
        if xcheck.explained["unexplained"]:
            lines.append(f"\n**{xcheck.explained['unexplained']} difference(s) are unexplained: FINDING.**")
        if not xcheck.explained["unexplained"] and xcheck.rows:
            lines.append("\nNo unexplained differences.")

    lines.append("\n## 3. Headline metrics, PPR, by position\n")
    lines.append(
        "Point estimates on the headline population, 2022-2024 pooled. MAE uses `pred_q50`; MAE mean uses "
        "`pred_mean` (D-053); pinball at q10/q50/q90; cov80 is the share inside [q10, q90] and width its mean "
        "width. Spearman and start/sit are on the D-074 common set (`ref_rank` from the mean `pred_q50` of the "
        "raw primary-configuration models, final-report variant excluded; Spearman above replacement QB 12, RB 24, WR 36, TE 12, K 12, DST 12; start/sit pairs among "
        "the top QB 12, RB 24, WR 36, TE 12, both active); `own` columns use each model's own top-N (D-050)."
    )
    lines.append(_headline_section(main, labels, pairs, positions))
    lines.append("\n### Differences: focus model minus each comparator [95% week-clustered paired bootstrap]\n")
    lines.append(
        f"`delta = {models.focus} - comparator` (calibrated rows against calibrated rows). Negative is better for "
        "MAE, pinball and coverage gap (|coverage - 0.80|); positive is better for Spearman and start/sit. "
        "`B`: the interval excludes zero in the focus model's favour (it beats the comparator); `W`: excludes zero "
        "against it."
    )
    delta_md, delta_records = _delta_section(main, models, positions)
    lines.append(delta_md)

    lines.append("\n## 4. By week of season (PPR, 2022-2024 pooled, all positions)\n")
    lines.append(f"Best baseline by pooled MAE: `{best}` "
                 + ", ".join(f"`{k}` {_num(v)}" for k, v in sorted(pooled_mae.items(), key=lambda kv: kv[1])) + ".\n")
    lines.append(_by_week_section(week_table, models.focus, best))

    lines.append("\n## 5. Calibration (PPR)\n")
    lines.append(_calibration_section(aligned, main, models, positions))
    lines.append("\n### 5.1 Interval coverage detail: tails, ties and a tie-aware diagnostic\n")
    lines.append(_coverage_section(detail, main, models, positions))
    if coverage_text:
        lines.append("\n" + coverage_text)

    lines.append("\n## 6. Secondary population: inactive players included (availability layer)\n")
    lines.append(
        "All rostered rows with an actual (inactive rows score 0). Models with an availability layer use their "
        "unconditional columns (`uncond`); the others keep their conditional projection, so this table shows "
        "what the availability layer is worth. `brier_active` and `logloss_active` score `active_prob` against "
        "`active`.\n"
    )
    lines.append(_secondary_section(secondary))

    lines.append("\n## 7. Lineup efficiency (PPR)\n")
    lines.append(
        "Slots QB, RB, RB, WR, WR, TE, FLEX, FLEX, K, DEF (the test league). D-050 pool: every projected entity "
        "of the season-week on the headline keys; the lineup chosen by `pred_q50` scores its actual points (0 "
        "for an inactive pick) and is divided by the best possible lineup from the same pool; mean over weeks.\n"
    )
    lineup_md, lineup_records = _lineup_section(main, models)
    lines.append(lineup_md)

    lines.append("\n## 8. Against the FantasyPros consensus on identical player-weeks (D-081; PPR, private)\n")
    cons_records: list[dict[str, Any]] = []
    if cons_boot is not None:
        lines.append(
            "FantasyPros weekly ECR from the DynastyProcess archive, latest valid scrape per player-week "
            "(`valid_for_game`, D-036), points from a rank-to-points curve fitted on prior seasons only (D-052), "
            "scored against the harness actuals (D-079). Every model in the table is restricted to exactly the "
            "player-weeks that have a valid consensus row with a projection, so the populations match row for "
            "row; players whose game was on or before the scrape date are therefore absent (section 8.1). "
            "Spearman and start/sit use a startable set from the mean projection of the raw primary-configuration "
            "models and the consensus (D-077). Aggregates only (D-007).\n"
        )
        cons_md, cons_records = _consensus_section(cons_boot, cons_points, models, positions, table_labels, identical)
        lines.append(cons_md)
        lines.append("\n### 8.1 The Thursday effect: matched, early-game and all player-weeks (D-081)\n")
        if split:
            lines.append(_thursday_section(counts, pop_points, split, models, best, positions))
        else:
            lines.append("Not available: the focus model or the best baseline has no calibrated rows.")
    else:
        lines.append("Not built in this run of the report.")

    lines.append("\n## 9. Half-PPR and standard (condensed)\n")
    for scoring, pts in other_points.items():
        lines.append(f"\n### {scoring}\n")
        lines.append(_scoring_section(pts, [m for m in raw if any(m in pts.get(p, {}) for p in pts)], positions))

    lines.append("\n## 10. Ablations and sensitivities\n")
    abl_records: list[dict[str, Any]] = []
    if sens_variants and sens_boot is not None:
        lines.append(
            f"Injury-information sensitivity (MODELING.md section 9): `{models.final_report}` adds same-week "
            f"report features; delta is `{models.focus} - variant` (calibrated against calibrated).\n"
        )
        rows_md, rec = _ablation_section(sens_boot, models.focus, [models.final_report], positions, "sensitivity")
        lines.append(rows_md)
        rows_md, rec2 = _ablation_section(sens_boot, models.focus + CAL, [models.final_report + CAL], positions,
                                          "sensitivity")
        lines.append("\nCalibrated:\n\n" + rows_md)
        abl_records += rec + rec2
    else:
        lines.append("No final-report variant in the run.\n")
    if abl_boot is not None and abl_manifest is not None:
        lines.append(
            f"\nAblation run `{abl_manifest.get('run_id')}` (git `{abl_manifest.get('git')}`, feature version "
            f"`{abl_manifest.get('feature_version')}`): delta is `{models.focus} - variant` on the keys both "
            "predict; a negative MAE delta means the removed input helps.\n"
        )
        raw_v = [v for v in abl_variants if not v.endswith(CAL)]
        cal_v = [v for v in abl_variants if v.endswith(CAL)]
        rows_md, rec = _ablation_section(abl_boot, models.focus, raw_v, positions, "ablation")
        lines.append(rows_md)
        if cal_v:
            rows_md, rec2 = _ablation_section(abl_boot, models.focus + CAL, cal_v, positions, "ablation")
            lines.append("\nCalibrated:\n\n" + rows_md)
            rec += rec2
        abl_records += rec
    else:
        lines.append(
            "\nNo ablation run supplied (`ablation_run_id=None`). Planned variants: no Vegas lines, no NGS, "
            "same-week depth charts (D-059 sensitivity)."
        )

    lines.append("\n## 11. Leakage and holdout audit (EV-3.2)\n")
    audit_stem, audit_lines = audit_summary(reports_dir, audit_name)
    if audit_stem is None:
        lines.append("No audit report found.")
    else:
        lines.append(f"From `reports/{audit_stem}.md` (`python -m ffml.eval.audits --write {audit_stem}`):\n")
        lines.extend(audit_lines)

    lines.append("\n## 12. Verdict (MODELING.md section 8)\n")
    if models.draft_note:
        lines.append(f"> Format check only: the focus model is `{models.focus}`, not the chain.\n")
    lines.append(render_verdict(cells, acc, models, positions, coverage_text=coverage_text))

    lines.append("\n## 13. Files\n")
    lines.append(
        f"- `data/reports/{out_name}_per_week.csv`: every metric per model, position, season and week "
        "(headline and consensus populations; gitignored).\n"
        f"- `data/reports/{out_name}_deltas.csv`: every delta with its interval and CI-rule status (gitignored)."
    )
    elapsed = time.time() - started
    text = "\n".join(lines).rstrip() + "\n"

    reports_dir.mkdir(parents=True, exist_ok=True)
    out = reports_dir / f"{out_name}.md"
    out.write_text(text, encoding="utf-8", newline="\n")  # LF on every platform, as committed
    data_dir.mkdir(parents=True, exist_ok=True)
    if per_week is not None:
        per_week.write_csv(data_dir / f"{out_name}_per_week.csv")
    if not detail.is_empty():
        detail.write_csv(data_dir / f"{out_name}_coverage.csv")
    records = [*delta_records, *lineup_records, *cons_records, *abl_records]
    records += [{"population": "verdict", "position": c.position, "form": c.form, "focus": c.delta.focus,
                 "other": c.delta.other, "metric": c.metric, "focus_value": c.delta.focus_value,
                 "other_value": c.delta.other_value, "delta": c.delta.delta, "lo": c.delta.lo, "hi": c.delta.hi,
                 "status": c.status} for c in cells]
    if records:
        pl.DataFrame(records, infer_schema_length=None).write_csv(data_dir / f"{out_name}_deltas.csv")
    (data_dir / f"{out_name}_meta.json").write_text(
        json.dumps({"run_id": str(run_id), "out_name": out_name, "n_boot": n_boot, "seed": seed,
                    "seconds": round(elapsed, 1), "predictions_sha256": file_sha, "git": head,
                    "focus": models.focus}, indent=2),
        encoding="utf-8",
    )
    return out


def main(argv: list[str] | None = None) -> int:
    ensure_utf8()
    parser = argparse.ArgumentParser(prog="python -m ffml.eval.report_backtest", description=__doc__.split("\n")[0])
    parser.add_argument("--run-id", required=True)
    parser.add_argument("--out", required=True, help="report name: writes reports/<out>.md")
    parser.add_argument("--ablation-run-id", default=None)
    parser.add_argument("--n-boot", type=int, default=500)
    parser.add_argument("--n-jobs", type=int, default=4)
    parser.add_argument("--seed", type=int, default=0)
    parser.add_argument("--focus", default=None)
    parser.add_argument("--audit", default=None, help="audit report name under reports/ (default: latest)")
    parser.add_argument("--no-consensus", action="store_true")
    args = parser.parse_args(argv)
    path = build_backtest_report(
        args.run_id, args.out, args.ablation_run_id, n_boot=args.n_boot, seed=args.seed, n_jobs=args.n_jobs,
        focus=args.focus, include_consensus=not args.no_consensus, audit_name=args.audit,
    )
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "ACCEPT_POSITIONS",
    "BASELINES",
    "CONSENSUS",
    "COVERAGE_BAND",
    "VERDICT_BASELINES",
    "VERDICT_METRICS",
    "Acceptance",
    "BootResult",
    "CrossCheck",
    "Delta",
    "ModelSet",
    "VerdictCell",
    "SplitDelta",
    "acceptance",
    "audit_summary",
    "build_backtest_report",
    "cell_sentence",
    "ci_status",
    "consensus_population",
    "consensus_sentences",
    "consensus_status",
    "coverage_detail",
    "coverage_paragraph",
    "headline_frames",
    "matched_frames",
    "matched_table_labels",
    "pair_counts",
    "population_keys",
    "population_keys_identical",
    "position_bounds",
    "render_verdict",
    "resolve_models",
    "run_bootstrap",
    "scoring_cross_check",
    "secondary_table",
    "split_mae_deltas",
    "split_populations",
    "status_counts",
    "thursday_sentence",
    "tie_aware_coverage",
    "verdict_cells",
    "verdict_sentences",
]
