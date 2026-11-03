"""Release-candidate input audit (EV-3.2): restricted reads, candidate feature lists, backtest inputs.

1. **Restricted data reads** (D-007, D-051). Code outside ``ffml/data`` and
   ``ffml/eval`` (models, features, simulation, scoring, the CLI, ``api``,
   ``web``, ``scripts``) must not reference the restricted consensus
   archive: Python string literals (docstrings excluded) or imports naming
   ``fact_ecr_weekly``, ``ff_rankings``, ``ecr_baseline`` or a ``restricted``
   path, and the same tokens in non-Python source files. Any hit fails.
2. **Candidate feature inputs** (D-047, D-065). Every chain artifact under
   ``artifacts/models/chain/*/features.json`` lists its LightGBM inputs. No
   input may be a target (``y_``). A primary-configuration artifact may not
   use same-week injury inputs (``avail__``); a final-report artifact may,
   and the audit reports how many it uses. The artifact's feature version
   must equal the version the backtest runs used.
3. **Backtest inputs.** For each run under ``data/backtests/<run>/``: the
   manifest purpose is ``backtest``, ``predictions.parquet`` holds no row of
   the holdout season (only the ``season`` column is read, so no holdout data
   is touched), and the feature snapshot ids in the manifest equal the
   ``LATEST`` snapshots under ``data/features`` that the leakage audit
   examined (WARN otherwise: the audit then did not see that run's inputs).
4. **Model registry** (D-076). Before the holdout release no entry may be
   approved or live; every candidate's artifact directory and frozen
   calibrator exist, and the calibrator's SHA-256 matches the recorded one
   (each hash is listed in the evidence).
"""

from __future__ import annotations

import ast
import json
from collections.abc import Iterable, Sequence
from pathlib import Path

import polars as pl

from ffml.config import PROJECT_ROOT, get_settings
from ffml.data.store import SnapshotStore
from ffml.eval.audits.holdout_audit import _docstring_ids, _iter_files, _rel
from ffml.eval.audits.report import AuditReport

#: tokens that identify the restricted consensus archive or its reader
RESTRICTED_TOKENS: tuple[str, ...] = ("fact_ecr_weekly", "ff_rankings", "ecr_baseline", "processed/restricted")
#: directories that may read restricted data (the processing step and the evaluator)
ALLOWED_DIRS: tuple[str, ...] = ("ffml/data", "ffml/eval")
SCAN_DIRS: tuple[str, ...] = ("ffml", "api", "web", "scripts")
TEXT_SUFFIXES: frozenset[str] = frozenset({".ts", ".tsx", ".js", ".jsx", ".mjs", ".sql", ".sh", ".ps1", ".yml", ".yaml"})
TARGET_PREFIX = "y_"
INJURY_PREFIX = "avail__"
FEATURE_DATASETS: tuple[str, ...] = ("player_week", "team_week", "dst_week")


def _is_allowed(rel: str) -> bool:
    return any(rel == d or rel.startswith(d + "/") for d in ALLOWED_DIRS)


def _string_hits(value: str) -> bool:
    low = value.lower()
    if any(tok in low for tok in RESTRICTED_TOKENS):
        return True
    return low == "restricted" or low.startswith("restricted/") or "/restricted/" in low


def scan_restricted_python(path: Path) -> list[tuple[int, str]]:
    """``(line, token)`` for restricted references in string literals (not docstrings) and imports."""
    source = path.read_text(encoding="utf-8")
    try:
        tree = ast.parse(source)
    except SyntaxError as exc:
        return [(exc.lineno or 0, "unparseable")]
    docstrings = _docstring_ids(tree)
    hits: list[tuple[int, str]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Constant) and isinstance(node.value, str) and id(node) not in docstrings:
            if _string_hits(node.value):
                hits.append((node.lineno, node.value[:60]))
        elif isinstance(node, ast.Import):
            for alias in node.names:
                if _string_hits(alias.name):
                    hits.append((node.lineno, f"import {alias.name}"))
        elif isinstance(node, ast.ImportFrom):
            names = [node.module or "", *[a.name for a in node.names]]
            if any(_string_hits(n) for n in names):
                hits.append((node.lineno, f"from {node.module} import ..."))
    return sorted(set(hits))


def scan_restricted_text(path: Path) -> list[tuple[int, str]]:
    hits = []
    for i, line in enumerate(path.read_text(encoding="utf-8", errors="replace").splitlines(), start=1):
        low = line.lower()
        for tok in (*RESTRICTED_TOKENS, "restricted/"):
            if tok in low:
                hits.append((i, tok))
                break
    return hits


def check_restricted_reads(report: AuditReport, root: Path) -> None:
    files = [p for p in _iter_files(root, SCAN_DIRS) if not _is_allowed(_rel(p, root))]
    hits: list[str] = []
    scanned = 0
    for path in files:
        rel = _rel(path, root)
        if path.suffix == ".py":
            found = scan_restricted_python(path)
        elif path.suffix in TEXT_SUFFIXES:
            found = scan_restricted_text(path)
        else:
            continue
        scanned += 1
        hits += [f"{rel}:{line}: {tok}" for line, tok in found]
    scope = f"{scanned} files under {', '.join(SCAN_DIRS)} outside {', '.join(ALLOWED_DIRS)}"
    if hits:
        report.add("restricted data reads", "FAIL", f"{len(hits)} references to restricted data ({scope}); D-007, D-051", hits)
    else:
        report.add("restricted data reads", "PASS", f"no reference to the restricted archive ({scope})")


def _artifact_config(path: Path, payload: dict) -> str:
    name = path.parent.name.lower()
    if "final_report" in name or "final-report" in name:
        return "final_report"
    return "primary"


def check_candidate_features(report: AuditReport, artifacts_dir: Path, backtest_versions: Iterable[str]) -> None:
    paths = sorted((artifacts_dir / "models" / "chain").glob("*/features.json"))
    if not paths:
        report.add("candidate feature inputs", "SKIP", "no chain artifact with features.json yet")
        return
    versions = sorted({v for v in backtest_versions if v})
    fails: list[str] = []
    evidence: list[str] = []
    for path in paths:
        payload = json.loads(path.read_text(encoding="utf-8"))
        inputs = payload.get("lgbm_inputs", {})
        columns = sorted({c for cols in inputs.values() for c in cols})
        config = _artifact_config(path, payload)
        targets = [c for c in columns if c.startswith(TARGET_PREFIX)]
        injury = [c for c in columns if c.startswith(INJURY_PREFIX)]
        name = path.parent.name
        version = payload.get("feature_version")
        evidence.append(
            f"{name}: {config}, {len(inputs)} LightGBM stages, {len(columns)} distinct inputs, "
            f"{len(injury)} {INJURY_PREFIX} inputs, feature version {version}"
        )
        if targets:
            fails.append(f"{name}: target columns used as inputs: {targets[:10]}")
        if injury and config == "primary":
            fails.append(f"{name}: primary configuration uses same-week injury inputs: {injury[:10]}")
        if versions and version not in versions:
            fails.append(f"{name}: feature version {version} differs from the backtest runs' {versions}")
    if fails:
        report.add("candidate feature inputs", "FAIL", f"{len(fails)} problems in {len(paths)} artifacts", fails + evidence)
    else:
        report.add(
            "candidate feature inputs", "PASS",
            f"{len(paths)} chain artifacts: no target inputs, no same-week injury inputs in primary artifacts",
            evidence,
        )


def _latest_feature_ids(features_root: Path, datasets: Sequence[str]) -> dict[str, str | None]:
    store = SnapshotStore(features_root)
    return {d: store.latest_id(d) for d in datasets}


def check_backtest_runs(
    report: AuditReport, backtests_dir: Path, features_root: Path, holdout_season: int
) -> list[str]:
    """Check every run's manifest and predictions; returns the feature versions the runs used."""
    runs = sorted(p for p in backtests_dir.glob("*/_manifest.json"))
    if not runs:
        report.add("backtest inputs", "SKIP", f"no backtest run under {backtests_dir}")
        return []
    latest = _latest_feature_ids(features_root, FEATURE_DATASETS)
    fails: list[str] = []
    warns: list[str] = []
    evidence: list[str] = []
    versions: list[str] = []
    for manifest_path in runs:
        run = manifest_path.parent
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        versions.append(str(manifest.get("feature_version")))
        purpose = manifest.get("purpose")
        if purpose != "backtest":
            fails.append(f"{run.name}: manifest purpose {purpose!r}, expected 'backtest'")
        pred = run / "predictions.parquet"
        seasons: list[int] = []
        if pred.exists():
            seasons = sorted(pl.scan_parquet(pred).select("season").unique().collect()["season"].to_list())
            if holdout_season in seasons:
                fails.append(f"{run.name}: predictions contain holdout season {holdout_season}")
        recorded = manifest.get("input_snapshots", {}).get("features", {})
        differ = {d: (recorded.get(d), latest.get(d)) for d in FEATURE_DATASETS
                  if d in recorded and recorded.get(d) != latest.get(d)}
        if differ:
            warns.append(f"{run.name}: feature snapshots differ from LATEST (run, latest): {differ}")
        if not recorded:
            warns.append(f"{run.name}: manifest records no feature snapshots")
        state = "not recorded" if not recorded else "match LATEST" if not differ else "differ from LATEST"
        evidence.append(
            f"{run.name}: purpose {purpose}, seasons {seasons}, feature version {manifest.get('feature_version')}, "
            f"feature snapshots {state}"
        )
    if fails:
        report.add("backtest inputs", "FAIL", f"{len(fails)} problems in {len(runs)} runs", fails + warns + evidence)
    elif warns:
        report.add("backtest inputs", "WARN", f"{len(warns)} runs used feature snapshots the leakage audit did not read",
                   warns + evidence)
    else:
        report.add(
            "backtest inputs", "PASS",
            f"{len(runs)} runs: purpose backtest, no holdout-season rows, feature snapshots equal the audited LATEST",
            evidence,
        )
    return versions


def _file_sha256(path: Path) -> str:
    import hashlib

    h = hashlib.sha256()
    with path.open("rb") as fh:
        for block in iter(lambda: fh.read(1 << 20), b""):
            h.update(block)
    return h.hexdigest()


def check_registry(report: AuditReport, artifacts_dir: Path, root: Path) -> None:
    """Registry before the holdout: candidate artifacts and frozen calibrators exist, hashes match, none approved.

    Reads ``<artifacts>/models/REGISTRY.json`` as plain JSON. FAIL when an
    entry is ``approved`` or ``live`` (approval needs the holdout release
    note, D-076), when a candidate's artifact directory or frozen calibrator
    is missing, or when the calibrator's SHA-256 differs from the recorded
    one. The evidence records every calibrator hash.
    """
    path = artifacts_dir / "models" / "REGISTRY.json"
    if not path.exists():
        report.add("model registry", "SKIP", "no models/REGISTRY.json yet")
        return
    payload = json.loads(path.read_text(encoding="utf-8"))
    entries = payload.get("models", [])
    fails: list[str] = []
    evidence: list[str] = []

    def resolve(rel: str) -> Path:
        p = Path(rel)
        if p.is_absolute():
            return p
        # registry paths are written relative to the project root ("artifacts/...")
        return root / p if (root / p).exists() else artifacts_dir.parent / p

    for entry in entries:
        version, status = entry.get("version"), entry.get("status")
        if status in ("approved", "live"):
            fails.append(f"{version}: status {status} before the holdout release (D-076)")
        artifact = entry.get("path")
        if not artifact or not resolve(artifact).exists():
            fails.append(f"{version}: artifact directory {artifact!r} missing")
        cal = entry.get("calibration") or {}
        if status == "candidate":
            if not cal.get("path"):
                fails.append(f"{version}: no frozen calibrator recorded")
            elif not resolve(cal["path"]).exists():
                fails.append(f"{version}: frozen calibrator {cal['path']} missing")
            else:
                computed = _file_sha256(resolve(cal["path"]))
                if computed != cal.get("sha256"):
                    fails.append(f"{version}: calibrator sha256 {computed} differs from recorded {cal.get('sha256')}")
        evidence.append(
            f"{version}: status {status}, artifact {artifact}, calibrator {cal.get('path')} "
            f"({cal.get('calibration_version')}), sha256 {cal.get('sha256')}"
        )
    statuses = sorted({str(e.get("status")) for e in entries})
    if fails:
        report.add("model registry", "FAIL", f"{len(fails)} problems in {len(entries)} entries", fails + evidence)
    else:
        report.add("model registry", "PASS",
                   f"{len(entries)} entries (statuses {statuses}): artifacts and frozen calibrators present, "
                   "calibrator hashes match, none approved", evidence)


def audit_inputs(
    root: Path = PROJECT_ROOT,
    *,
    artifacts_dir: Path | None = None,
    backtests_dir: Path | None = None,
    features_root: Path | None = None,
    holdout_season: int | None = None,
) -> AuditReport:
    """Run the input audit (see module docstring); absent inputs become SKIP items."""
    root = Path(root)
    settings = get_settings()
    is_repo = root.resolve() == PROJECT_ROOT.resolve()
    artifacts_dir = Path(artifacts_dir) if artifacts_dir is not None else (
        settings.artifacts_dir if is_repo else root / "artifacts")
    backtests_dir = Path(backtests_dir) if backtests_dir is not None else root / "data" / "backtests"
    features_root = Path(features_root) if features_root is not None else (
        settings.features_dir if is_repo else root / "data" / "features")
    season = int(holdout_season if holdout_season is not None else settings.holdout_season)
    report = AuditReport(title="Release-candidate inputs audit")
    check_restricted_reads(report, root)
    versions = check_backtest_runs(report, backtests_dir, features_root, season)
    check_candidate_features(report, artifacts_dir, versions)
    check_registry(report, artifacts_dir, root)
    return report


__all__ = [
    "ALLOWED_DIRS",
    "RESTRICTED_TOKENS",
    "audit_inputs",
    "check_backtest_runs",
    "check_candidate_features",
    "check_registry",
    "check_restricted_reads",
    "scan_restricted_python",
    "scan_restricted_text",
]
