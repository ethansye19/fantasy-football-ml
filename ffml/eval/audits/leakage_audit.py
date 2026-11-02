"""Leakage audit (EV-3.2): rerun the leakage suite, timestamp audit, target-copy probe.

1. **Leakage suite.** Runs ``pytest tests/features/test_leakage.py -q`` in a
   subprocess when the file exists and records the summary line (SKIP when
   the Feature Engineer has not written it yet).
2. **Timestamp audit.** When a feature dataset exists under ``data/features``,
   reads ``player_week`` for the validation seasons only (the holdout guard is
   called first) and checks per row that ``cutoff_ts <= kickoff_utc``,
   ``asof_ts <= kickoff_utc``, ``asof_ts <= cutoff_ts`` and that
   ``cutoff_ts`` equals the first kickoff of that season-week (D-046, taken
   from ``dim_game`` when available, else from the frame itself).
3. **Shuffle probe.** On a seeded sample of 2,000 rows, no non-target column
   may be an exact copy of, or correlate above 0.999 with, any ``y_`` target
   column unless it is on the allowlist (empty). The maximum absolute
   correlation is reported next to the same statistic with each target
   shuffled, as a noise reference.

Inputs that do not exist yet produce SKIP items rather than errors.
"""

from __future__ import annotations

import os
import re
import subprocess
import sys
from collections.abc import Iterable, Sequence
from pathlib import Path

import numpy as np
import polars as pl

from ffml.config import PROJECT_ROOT, get_settings
from ffml.data.store import SnapshotStore
from ffml.eval.audits.report import AuditReport
from ffml.holdout import assert_seasons_allowed

LEAKAGE_TEST = "tests/features/test_leakage.py"
VALIDATION_SEASONS: tuple[int, ...] = (2022, 2023, 2024)
DATASET = "player_week"
TIME_COLUMNS: tuple[str, ...] = ("cutoff_ts", "asof_ts", "kickoff_utc")
#: columns that identify a row and are not features
META_COLUMNS: frozenset[str] = frozenset(
    {"season", "week", "cutoff_ts", "asof_ts", "kickoff_utc", "feature_version", "snapshot_id"}
)
#: feature columns allowed to track a target closely (kept empty on purpose)
COPY_ALLOWLIST: frozenset[str] = frozenset()
CORR_LIMIT = 0.999
SAMPLE_ROWS = 2000
MIN_PAIRS = 30
_SUMMARY_RE = re.compile(r"\b\d+ (passed|failed|error|errors|skipped|deselected)\b|no tests ran")


# --------------------------------------------------------------------------- #
# 1. Leakage suite
# --------------------------------------------------------------------------- #


def summary_line(output: str) -> str:
    """The pytest summary line (``N passed in Xs``) from captured output."""
    lines = [ln.strip().strip("=").strip() for ln in output.splitlines() if ln.strip()]
    for line in reversed(lines):
        if _SUMMARY_RE.search(line):
            return line
    return lines[-1] if lines else "(no output)"


def run_leakage_suite(report: AuditReport, root: Path, timeout: int = 1800) -> None:
    test_file = root / LEAKAGE_TEST
    if not test_file.exists():
        report.add("leakage suite", "SKIP", f"{LEAKAGE_TEST} not present yet (Feature Engineer, FE-3.2)")
        return
    env = {**os.environ, "PYTHONUTF8": "1"}
    try:
        proc = subprocess.run(
            [sys.executable, "-m", "pytest", LEAKAGE_TEST, "-q", "-p", "no:cacheprovider"],
            cwd=root,
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=timeout,
            env=env,
            check=False,
        )
    except subprocess.TimeoutExpired:
        report.add("leakage suite", "FAIL", f"pytest {LEAKAGE_TEST} timed out after {timeout}s")
        return
    line = summary_line(proc.stdout + "\n" + proc.stderr)
    if proc.returncode == 0:
        report.add("leakage suite", "PASS", f"pytest {LEAKAGE_TEST} -q: {line}")
    elif proc.returncode == 5:
        report.add("leakage suite", "FAIL", f"pytest {LEAKAGE_TEST} collected no tests: {line}")
    else:
        report.add("leakage suite", "FAIL", f"pytest {LEAKAGE_TEST} -q exit {proc.returncode}: {line}")


# --------------------------------------------------------------------------- #
# 2. Timestamp audit
# --------------------------------------------------------------------------- #


def _utc(name: str, dtype: pl.DataType) -> pl.Expr:
    col = pl.col(name)
    if isinstance(dtype, pl.Datetime):
        col = col.dt.replace_time_zone("UTC") if dtype.time_zone is None else col.dt.convert_time_zone("UTC")
        return col.dt.cast_time_unit("us").alias(name)
    return col.cast(pl.Datetime("us", "UTC")).alias(name)


def week_first_kickoff(dim_game: pl.DataFrame) -> pl.DataFrame:
    """``season, week, week_first_kickoff`` from a dim_game frame."""
    return dim_game.group_by("season", "week").agg(
        _utc("kickoff_utc", dim_game.schema["kickoff_utc"]).min().alias("week_first_kickoff")
    ).with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))


def check_timestamps(frame: pl.DataFrame, first_kickoff: pl.DataFrame | None = None) -> dict[str, int]:
    """Count rows violating each timestamp rule (zero everywhere means PASS).

    ``first_kickoff`` (``season, week, week_first_kickoff``) defaults to the
    minimum ``kickoff_utc`` per season-week of ``frame``.
    """
    f = frame.with_columns([_utc(c, frame.schema[c]) for c in TIME_COLUMNS]).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    if first_kickoff is None:
        first_kickoff = f.group_by("season", "week").agg(
            pl.col("kickoff_utc").min().alias("week_first_kickoff")
        )
    f = f.join(first_kickoff, on=["season", "week"], how="left")
    checks = f.select(
        pl.len().alias("rows"),
        pl.col("cutoff_ts").is_null().sum().alias("null_cutoff_ts"),
        pl.col("asof_ts").is_null().sum().alias("null_asof_ts"),
        pl.col("kickoff_utc").is_null().sum().alias("null_kickoff_utc"),
        (pl.col("cutoff_ts") > pl.col("kickoff_utc")).sum().alias("cutoff_after_kickoff"),
        (pl.col("asof_ts") > pl.col("kickoff_utc")).sum().alias("asof_after_kickoff"),
        (pl.col("asof_ts") > pl.col("cutoff_ts")).sum().alias("asof_after_cutoff"),
        pl.col("week_first_kickoff").is_null().sum().alias("week_without_schedule"),
        (pl.col("cutoff_ts") != pl.col("week_first_kickoff")).sum().alias("cutoff_not_week_first_kickoff"),
        (pl.col("asof_ts") != pl.col("cutoff_ts") - pl.duration(hours=1)).sum().alias("asof_not_cutoff_minus_1h"),
    )
    return {k: int(v or 0) for k, v in checks.row(0, named=True).items()}


_INFO_KEYS = {"rows", "asof_not_cutoff_minus_1h"}


def _read_features(features_root: Path, dataset: str, seasons: Sequence[int]) -> pl.DataFrame | None:
    store = SnapshotStore(features_root)
    if store.latest_id(dataset) is None:
        return None
    assert_seasons_allowed(seasons, "leakage_audit")
    try:
        return store.read(dataset, seasons=list(seasons))
    except FileNotFoundError:
        return None


def _first_kickoff_from_dim_game(seasons: Sequence[int]) -> pl.DataFrame | None:
    store = SnapshotStore(get_settings().processed_dir)
    if store.latest_id("dim_game") is None:
        return None
    try:
        games = store.read("dim_game", seasons=list(seasons), columns=["season", "week", "kickoff_utc"])
    except FileNotFoundError:
        return None
    return week_first_kickoff(games)


def timestamp_audit(report: AuditReport, frame: pl.DataFrame, first_kickoff: pl.DataFrame | None, source: str) -> None:
    missing = [c for c in TIME_COLUMNS if c not in frame.columns]
    if missing:
        report.add("timestamp audit", "FAIL", f"{DATASET} lacks timestamp columns {missing}")
        return
    counts = check_timestamps(frame, first_kickoff)
    bad = {k: v for k, v in counts.items() if k not in _INFO_KEYS and v}
    evidence = [f"{k}: {v}" for k, v in counts.items()]
    detail = f"{counts['rows']} rows checked against week first kickoff from {source}"
    if bad:
        report.add("timestamp audit", "FAIL", f"{detail}; violations {bad}", evidence)
    else:
        report.add("timestamp audit", "PASS", f"{detail}; no violations", evidence)
    if counts["asof_not_cutoff_minus_1h"]:
        report.add(
            "as-of offset", "WARN",
            f"{counts['asof_not_cutoff_minus_1h']} rows where asof_ts is not cutoff_ts - 1h (D-046 historical rule)",
        )


# --------------------------------------------------------------------------- #
# 3. Shuffle probe
# --------------------------------------------------------------------------- #


def _pairwise_corr(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray]:
    """Pairwise-complete Pearson correlation of each column of ``x`` with ``y`` and pair counts."""
    mask = ~np.isnan(x) & ~np.isnan(y)[:, None]
    n = mask.sum(axis=0).astype(float)
    x0 = np.where(mask, x, 0.0)
    y0 = np.where(mask, y[:, None], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        mx = x0.sum(axis=0) / n
        my = y0.sum(axis=0) / n
        cov = (x0 * y0).sum(axis=0) / n - mx * my
        vx = (x0 * x0).sum(axis=0) / n - mx * mx
        vy = (y0 * y0).sum(axis=0) / n - my * my
        corr = cov / np.sqrt(vx * vy)
    corr[(vx <= 1e-12) | (vy <= 1e-12)] = np.nan
    return corr, n


def _exact_copy(x: np.ndarray, y: np.ndarray) -> np.ndarray:
    mask = ~np.isnan(x) & ~np.isnan(y)[:, None]
    tol = 1e-5 * (1.0 + np.abs(y))[:, None]
    differs = mask & (np.abs(np.where(mask, x, 0.0) - np.where(mask, y[:, None], 0.0)) > tol)
    return (mask.sum(axis=0) >= MIN_PAIRS) & ~differs.any(axis=0)


def probe_target_copies(
    frame: pl.DataFrame,
    sample: int = SAMPLE_ROWS,
    seed: int = 0,
    allowlist: Iterable[str] = COPY_ALLOWLIST,
    limit: float = CORR_LIMIT,
) -> dict[str, object]:
    """Find non-target columns that copy or nearly copy a ``y_`` column on a sample.

    Returns ``targets, features, rows, flagged (list of (feature, target, corr,
    exact)), max_abs_corr, max_pair, shuffled_max_abs_corr``.
    """
    allow = set(allowlist)
    targets = [c for c in frame.columns if c.startswith("y_")]
    numeric = {c for c, t in frame.schema.items() if t.is_numeric() or t == pl.Boolean}
    features = [
        c for c in frame.columns
        if c not in META_COLUMNS and not c.startswith("y_") and c in numeric
    ]
    rows = frame.sample(n=min(sample, frame.height), seed=seed, shuffle=True) if frame.height else frame
    out: dict[str, object] = {
        "targets": len(targets), "features": len(features), "rows": rows.height,
        "flagged": [], "max_abs_corr": float("nan"), "max_pair": None, "shuffled_max_abs_corr": float("nan"),
    }
    if not targets or not features or rows.height < MIN_PAIRS:
        return out
    x = rows.select([pl.col(c).cast(pl.Float64) for c in features]).to_numpy()
    rng = np.random.default_rng(seed)
    flagged: list[tuple[str, str, float, bool]] = []
    best, best_pair, shuffled_best = -1.0, None, -1.0
    for target in targets:
        y = rows[target].cast(pl.Float64).to_numpy()
        if np.sum(~np.isnan(y)) < MIN_PAIRS:
            continue
        corr, n = _pairwise_corr(x, y)
        corr[n < MIN_PAIRS] = np.nan
        exact = _exact_copy(x, y)
        absc = np.abs(corr)
        if np.any(~np.isnan(absc)):
            i = int(np.nanargmax(absc))
            if absc[i] > best:
                best, best_pair = float(absc[i]), (features[i], target)
        shuffled, ns = _pairwise_corr(x, rng.permutation(y))
        shuffled[ns < MIN_PAIRS] = np.nan
        if np.any(~np.isnan(shuffled)):
            shuffled_best = max(shuffled_best, float(np.nanmax(np.abs(shuffled))))
        for j, feat in enumerate(features):
            if feat in allow:
                continue
            if exact[j] or (not np.isnan(absc[j]) and absc[j] > limit):
                flagged.append((feat, target, float(corr[j]) if not np.isnan(corr[j]) else float("nan"), bool(exact[j])))
    out["flagged"] = flagged
    out["max_abs_corr"] = best if best >= 0 else float("nan")
    out["max_pair"] = best_pair
    out["shuffled_max_abs_corr"] = shuffled_best if shuffled_best >= 0 else float("nan")
    return out


def shuffle_probe(report: AuditReport, frame: pl.DataFrame, seed: int, allowlist: Iterable[str]) -> None:
    res = probe_target_copies(frame, seed=seed, allowlist=allowlist)
    if not res["targets"]:
        report.add("shuffle probe", "SKIP", f"{DATASET} has no y_ target columns")
        return
    flagged = res["flagged"]
    assert isinstance(flagged, list)
    detail = (
        f"{res['features']} feature columns x {res['targets']} targets on {res['rows']} sampled rows; "
        f"max |corr| {res['max_abs_corr']:.3f} {res['max_pair']}, "
        f"shuffled-target reference {res['shuffled_max_abs_corr']:.3f}"
    )
    if flagged:
        evidence = [
            f"{f} vs {t}: corr {c:.4f}{' (exact copy)' if e else ''}" for f, t, c, e in flagged
        ]
        report.add("shuffle probe", "FAIL", f"{len(flagged)} near-copies of targets; {detail}", evidence)
    else:
        report.add("shuffle probe", "PASS", f"no feature copies a target; {detail}")


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def _display_path(path: Path, root: Path) -> str:
    try:
        return Path(path).resolve().relative_to(Path(root).resolve()).as_posix()
    except ValueError:
        return Path(path).as_posix()


def audit_leakage(
    features_root: Path | None = None,
    *,
    root: Path = PROJECT_ROOT,
    seasons: Sequence[int] = VALIDATION_SEASONS,
    run_suite: bool = True,
    seed: int = 0,
    allowlist: Iterable[str] = COPY_ALLOWLIST,
    first_kickoff: pl.DataFrame | None = None,
) -> AuditReport:
    """Run the leakage audit (see module docstring); missing inputs become SKIP items."""
    root = Path(root)
    report = AuditReport(title="Leakage audit")
    if run_suite:
        run_leakage_suite(report, root)
    else:
        report.add("leakage suite", "SKIP", "not run (run_suite=False)")
    features_root = Path(features_root) if features_root is not None else get_settings().features_dir
    frame = _read_features(features_root, DATASET, seasons)
    if frame is None:
        shown = _display_path(features_root, root)
        reason = f"no {DATASET} feature dataset under {shown} yet (Feature Engineer, FE-3.1)"
        report.add("timestamp audit", "SKIP", reason)
        report.add("shuffle probe", "SKIP", reason)
        return report
    if frame.is_empty():
        report.add("timestamp audit", "SKIP", f"{DATASET} has no rows for seasons {list(seasons)}")
        report.add("shuffle probe", "SKIP", f"{DATASET} has no rows for seasons {list(seasons)}")
        return report
    source = "the caller"
    if first_kickoff is None:
        first_kickoff = _first_kickoff_from_dim_game(seasons)
        source = "dim_game" if first_kickoff is not None else f"{DATASET} itself (dim_game unavailable)"
    timestamp_audit(report, frame, first_kickoff, source)
    shuffle_probe(report, frame, seed, allowlist)
    return report


__all__ = [
    "COPY_ALLOWLIST",
    "LEAKAGE_TEST",
    "VALIDATION_SEASONS",
    "audit_leakage",
    "check_timestamps",
    "probe_target_copies",
    "summary_line",
    "week_first_kickoff",
]
