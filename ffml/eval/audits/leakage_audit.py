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
   column unless it is on the allowlist (empty). Each correlation uses only
   rows where both columns are non-null and needs at least 200 of them (pairs
   below that are skipped and counted). A target that only some positions
   carry (kicking targets are zero for non-kickers) is compared within each
   carrying position, on a sample of that position, so between-position
   differences cannot masquerade as a copy. The top 5 pairs by absolute
   correlation are reported next to the largest correlation with each target
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
#: minimum rows where both the feature and the target are non-null
MIN_OVERLAP = 200
#: a position carries a target when this share of its rows is non-null and non-zero
SPECIFIC_SHARE = 0.01
TOP_PAIRS = 5
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


def _exact_copy(x: np.ndarray, y: np.ndarray, n: np.ndarray, vy: np.ndarray, min_overlap: int) -> np.ndarray:
    """Columns of ``x`` equal to ``y`` on every overlapping row (enough rows, non-constant target)."""
    mask = ~np.isnan(x) & ~np.isnan(y)[:, None]
    tol = 1e-5 * (1.0 + np.abs(y))[:, None]
    differs = mask & (np.abs(np.where(mask, x, 0.0) - np.where(mask, y[:, None], 0.0)) > tol)
    return (n >= min_overlap) & (vy > 1e-12) & ~differs.any(axis=0)


def _overlap_stats(x: np.ndarray, y: np.ndarray) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    """Correlation, overlap count and target variance over rows where both are non-null."""
    mask = ~np.isnan(x) & ~np.isnan(y)[:, None]
    n = mask.sum(axis=0).astype(float)
    y0 = np.where(mask, y[:, None], 0.0)
    with np.errstate(invalid="ignore", divide="ignore"):
        my = y0.sum(axis=0) / n
        vy = (y0 * y0).sum(axis=0) / n - my * my
    corr, _ = _pairwise_corr(x, y)
    return corr, n, np.nan_to_num(vy, nan=0.0)


def target_positions(
    frame: pl.DataFrame, targets: Sequence[str], share: float = SPECIFIC_SHARE
) -> dict[str, list[str] | None]:
    """Positions that carry each target, or ``None`` when the target is not position-specific.

    A position carries a target when at least ``share`` of its rows have a
    non-null, non-zero value. A target carried by only some of the positions
    present is position-specific (kicking targets, for example, are zero for
    everyone but kickers) and is compared within each carrying position.
    Without a ``position`` column every target is pooled.
    """
    if "position" not in frame.columns or not targets:
        return dict.fromkeys(targets)
    shares = frame.group_by("position").agg(
        [
            (pl.col(t).is_not_null() & (pl.col(t).cast(pl.Float64) != 0)).mean().alias(t)
            for t in targets
        ]
    )
    present = sorted(str(p) for p in shares["position"].to_list() if p is not None)
    out: dict[str, list[str] | None] = {}
    for t in targets:
        carried = sorted(
            str(p) for p, s in zip(shares["position"], shares[t], strict=True)
            if p is not None and s is not None and s >= share
        )
        out[t] = carried if carried and carried != present else None
    return out


def probe_target_copies(
    frame: pl.DataFrame,
    sample: int = SAMPLE_ROWS,
    seed: int = 0,
    allowlist: Iterable[str] = COPY_ALLOWLIST,
    limit: float = CORR_LIMIT,
    min_overlap: int = MIN_OVERLAP,
    top: int = TOP_PAIRS,
) -> dict[str, object]:
    """Find non-target columns that copy or nearly copy a ``y_`` column.

    Each correlation uses only rows where both columns are non-null and needs
    at least ``min_overlap`` such rows; pairs below that are skipped and
    counted. Pooled targets use a seeded sample of ``sample`` rows;
    position-specific targets (:func:`target_positions`) use a seeded sample
    of up to ``sample`` rows of each carrying position. Returns ``targets,
    features, rows, scopes, position_specific, pairs_computed,
    skipped_low_overlap, undefined, flagged`` (tuples ``feature, target,
    scope, corr, n, exact``), ``top_pairs`` (the ``top`` largest |corr| as
    ``feature, target, scope, corr, n``), ``max_abs_corr``, ``max_pair`` and
    ``shuffled_max_abs_corr`` (same rules with each target shuffled).
    """
    allow = set(allowlist)
    targets = [c for c in frame.columns if c.startswith("y_")]
    numeric = {c for c, t in frame.schema.items() if t.is_numeric() or t == pl.Boolean}
    features = [
        c for c in frame.columns
        if c not in META_COLUMNS and not c.startswith("y_") and c in numeric
    ]
    out: dict[str, object] = {
        "targets": len(targets), "features": len(features), "rows": 0, "scopes": {},
        "position_specific": {}, "pairs_computed": 0, "skipped_low_overlap": 0, "undefined": 0,
        "flagged": [], "top_pairs": [], "max_abs_corr": float("nan"), "max_pair": None,
        "shuffled_max_abs_corr": float("nan"),
    }
    if not targets or not features or frame.is_empty():
        return out
    carriers = target_positions(frame, targets)
    out["position_specific"] = {t: p for t, p in carriers.items() if p is not None}
    cache: dict[str, tuple[np.ndarray, pl.DataFrame]] = {}

    def scope_rows(scope: str) -> tuple[np.ndarray, pl.DataFrame]:
        if scope not in cache:
            part = frame if scope == "ALL" else frame.filter(pl.col("position") == scope)
            rows = part.sample(n=min(sample, part.height), seed=seed, shuffle=True)
            x = rows.select([pl.col(c).cast(pl.Float64) for c in features]).to_numpy()
            cache[scope] = (x, rows)
        return cache[scope]

    rng = np.random.default_rng(seed)
    flagged: list[tuple[str, str, str, float, int, bool]] = []
    candidates: list[tuple[float, str, str, str, float, int]] = []
    computed = skipped = undefined = 0
    shuffled_best = -1.0
    feature_mask = np.array([f not in allow for f in features])
    for target in targets:
        scopes = carriers[target] or ["ALL"]
        for scope in scopes:
            x, rows = scope_rows(scope)
            y = rows[target].cast(pl.Float64).to_numpy()
            corr, n, vy = _overlap_stats(x, y)
            low = n < min_overlap
            skipped += int(low.sum())
            corr[low] = np.nan
            undefined += int((~low & np.isnan(corr)).sum())
            computed += int((~np.isnan(corr)).sum())
            exact = _exact_copy(x, y, n, vy, min_overlap)
            absc = np.abs(corr)
            finite = np.flatnonzero(~np.isnan(absc))
            for j in finite[np.argsort(-absc[finite])][:top]:
                candidates.append((float(absc[j]), features[j], target, scope, float(corr[j]), int(n[j])))
            shuffled, ns, _ = _overlap_stats(x, rng.permutation(y))
            shuffled[ns < min_overlap] = np.nan
            if np.any(~np.isnan(shuffled)):
                shuffled_best = max(shuffled_best, float(np.nanmax(np.abs(shuffled))))
            hits = feature_mask & (exact | (~np.isnan(absc) & (absc > limit)))
            for j in np.flatnonzero(hits):
                flagged.append((features[j], target, scope, float(corr[j]), int(n[j]), bool(exact[j])))
    candidates.sort(key=lambda c: -c[0])
    top_pairs = [(f, t, s, c, n) for _, f, t, s, c, n in candidates[:top]]
    out.update(
        rows=cache["ALL"][1].height if "ALL" in cache else 0,
        scopes={s: rows.height for s, (_, rows) in cache.items()},
        pairs_computed=computed,
        skipped_low_overlap=skipped,
        undefined=undefined,
        flagged=flagged,
        top_pairs=top_pairs,
        max_abs_corr=abs(top_pairs[0][3]) if top_pairs else float("nan"),
        max_pair=(top_pairs[0][0], top_pairs[0][1]) if top_pairs else None,
        shuffled_max_abs_corr=shuffled_best if shuffled_best >= 0 else float("nan"),
    )
    return out


def shuffle_probe(report: AuditReport, frame: pl.DataFrame, seed: int, allowlist: Iterable[str]) -> None:
    res = probe_target_copies(frame, seed=seed, allowlist=allowlist)
    if not res["targets"]:
        report.add("shuffle probe", "SKIP", f"{DATASET} has no y_ target columns")
        return
    flagged = res["flagged"]
    top_pairs = res["top_pairs"]
    specific = res["position_specific"]
    scopes = res["scopes"]
    assert isinstance(flagged, list) and isinstance(top_pairs, list)
    assert isinstance(specific, dict) and isinstance(scopes, dict)
    sample_desc = ", ".join(f"{s} {n}" for s, n in sorted(scopes.items()))
    detail = (
        f"{res['features']} feature columns x {res['targets']} targets "
        f"({len(specific)} position-specific, compared within position); sampled rows: {sample_desc}; "
        f"{res['pairs_computed']} pairs computed on rows where both are non-null, "
        f"{res['skipped_low_overlap']} skipped for overlap below {MIN_OVERLAP}, "
        f"{res['undefined']} undefined (constant); max |corr| {res['max_abs_corr']:.3f}, "
        f"shuffled-target reference {res['shuffled_max_abs_corr']:.3f}"
    )
    evidence = [
        f"top {i}: {f} vs {t} [{s}]: corr {c:.3f} (n={n})"
        for i, (f, t, s, c, n) in enumerate(top_pairs, start=1)
    ]
    by_carriers: dict[tuple[str, ...], list[str]] = {}
    for t, p in sorted(specific.items()):
        by_carriers.setdefault(tuple(p), []).append(t)
    evidence += [
        f"compared within {'/'.join(p)}: {len(ts)} targets ({', '.join(ts)})"
        for p, ts in sorted(by_carriers.items())
    ]
    if flagged:
        evidence = [
            f"FLAG {f} vs {t} [{s}]: corr {c:.4f} (n={n}){' (exact copy)' if e else ''}"
            for f, t, s, c, n, e in flagged
        ] + evidence
        report.add("shuffle probe", "FAIL", f"{len(flagged)} near-copies of targets; {detail}", evidence)
    elif not res["pairs_computed"]:
        report.add("shuffle probe", "WARN", f"inconclusive, no pair had enough overlap; {detail}", evidence)
    else:
        report.add("shuffle probe", "PASS", f"no feature copies a target; {detail}", evidence)


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
    "MIN_OVERLAP",
    "VALIDATION_SEASONS",
    "audit_leakage",
    "check_timestamps",
    "probe_target_copies",
    "summary_line",
    "target_positions",
    "week_first_kickoff",
]
