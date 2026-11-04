"""Holdout release report (EV-3.4; D-049, D-076, D-091 to D-096).

``build_holdout_report(holdout_run_id, forward_run_id=None, prereg_path=...)``
applies exactly the 22 pre-registered approval lines to the holdout run
(calibrated forms, frozen calibration) and writes
``reports/evaluator_release_<candidate>.md``. The same lines are applied to
the forward run (2026 weeks 1-7) as a separate check that never changes the
holdout verdict (D-094).

Pre-registration (D-091)
========================

A JSON object, either a ``.json`` file or the first fenced ``json`` block of a
markdown file (``docs/status/holdout-preregistration.md``)::

    {
      "candidate": "<registry version>",        "focus_model": "chain_v1",
      "commit": "<git sha of the frozen code>", "holdout_season": <season>,
      "forward": {"season": <season>, "weeks": [1, ..., 7]},
      "frozen": {"path": "<FROZEN.json>", "sha256": "<hex>"},
      "calibrators": {"<model>": {"path": "<calibration.json>", "sha256": "<hex>"}, ...},
      "calibration_mode": "frozen",
      "snapshots": {"processed": {...}, "features": {...}},
      "forward_snapshots": {...},                (optional; default: "snapshots")
      "n_boot": 500, "seed": 0, "metric_seed": 0,
      "lines_sha256": "<lines_spec_sha256()>"
    }

``lines_sha256`` pins the 22 lines defined here (:func:`prereg_lines`); a
pre-registration that does not pin them, or pins a different set, makes the
session invalid.

Order of work
=============

1. Integrity checks, before any holdout metric is read: the line set, the
   commit rule for every run session, every access-log session and the
   report generator's HEAD (each at the pre-registered commit X, or at a
   descendant Y whose ``git diff --name-only X Y`` is exactly
   ``docs/status/holdout-preregistration.md``; X, Y and the diff are recorded),
   the run's seasons, FROZEN.json and every calibrator file against their
   hashes, the manifest's calibration mode and any calibrator hashes it
   records, the input snapshot ids, and the holdout access log (exactly one
   ``holdout_eval`` session at the pre-registered commit, D-049). Any failure
   gives ``Verdict: INVALID (<reason>)`` and nothing is computed (D-093).
2. Every read of holdout-season data goes through the guard with purpose
   ``holdout_eval``; afterwards the access log is checked again (still one
   session).
3. Metrics with the functions of :mod:`ffml.eval.report_backtest`: 500
   week-clustered paired bootstrap draws (seed 0), the D-077 common pair
   set, tie-aware coverage with metric seed 0 (D-085). PIT bounds are the
   run's own ``pos_min`` / ``pos_max`` (the frozen calibrator's bounds), so
   no further outcome data is read for them.
4. ``Verdict: APPROVED`` only if all 22 lines pass, else
   ``Verdict: NOT APPROVED`` with every failing line in plain words. A dry
   run is labelled DRY RUN, written under ``data/reports/`` and its verdict
   line can never satisfy the registry's exact-line check (D-076).
"""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import polars as pl

from ffml.config import PROJECT_ROOT, ensure_utf8, get_settings
from ffml.eval import metrics as M
from ffml.eval import report_backtest as RB
from ffml.eval.audits.holdout_audit import access_sessions, parse_access_log
from ffml.eval.harness import load_backtest, read_manifest, resolve_run_dir

HOLDOUT_PURPOSE = "holdout_eval"
BEATS_METRICS: tuple[str, ...] = ("mae", "pinball_q10", "pinball_q50", "pinball_q90")
LINE_BASELINES: tuple[str, ...] = ("rolling_avg_4", "prior_season", "rolling_median_8")
COVERAGE_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K", "DST")
VERDICT_APPROVED = "Verdict: APPROVED"
VERDICT_NOT_APPROVED = "Verdict: NOT APPROVED"
#: statistics pinned by the line spec (D-078, D-085); the pre-registration must use exactly these
SPEC_N_BOOT = 500
SPEC_SEED = 0
SPEC_METRIC_SEED = 0


# --------------------------------------------------------------------------- #
# The 22 pre-registered lines
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class PreregLine:
    """One approval line: ``beats_baseline``, ``startsit_consensus`` or ``coverage``."""

    id: str
    kind: str
    position: str
    comparator: str | None = None

    def spec(self) -> dict[str, Any]:
        out: dict[str, Any] = {"id": self.id, "kind": self.kind, "position": self.position}
        if self.kind == "beats_baseline":
            out.update(focus=f"<focus>{RB.CAL}", comparator=f"{self.comparator}{RB.CAL}",
                       metrics=list(BEATS_METRICS), rule="every metric's 95% interval of focus - comparator < 0")
        elif self.kind == "startsit_consensus":
            out.update(focus=f"<focus>{RB.CAL}", comparator=RB.CONSENSUS, metric="start_sit_common",
                       rule="not worse: the 95% interval of focus - consensus is not entirely below 0")
        else:
            out.update(model=f"<focus>{RB.CAL}", metric="coverage_tie_aware", band=list(RB.COVERAGE_BAND),
                       rule="tie-aware coverage (metric seed 0) within the band, closed")
        return out

    def describe(self, focus: str) -> str:
        f = f"`{focus}{RB.CAL}`"
        if self.kind == "beats_baseline":
            return (f"{f} beats `{self.comparator}{RB.CAL}` at {self.position} on MAE and pinball q10, q50 and q90 "
                    "(every 95% interval entirely below 0)")
        if self.kind == "startsit_consensus":
            return f"{f} start/sit at {self.position} is not worse than the FantasyPros consensus (common pair set)"
        lo, hi = RB.COVERAGE_BAND
        return f"{f} tie-aware coverage at {self.position} within [{lo:.2f}, {hi:.2f}]"


def prereg_lines() -> tuple[PreregLine, ...]:
    """The 22 lines in their fixed order: 12 beats-baseline, 4 start/sit, 6 coverage."""
    lines: list[PreregLine] = []
    for comp in LINE_BASELINES:
        for pos in RB.ACCEPT_POSITIONS:
            lines.append(PreregLine(f"L{len(lines) + 1:02d}", "beats_baseline", pos, comp))
    for pos in RB.ACCEPT_POSITIONS:
        lines.append(PreregLine(f"L{len(lines) + 1:02d}", "startsit_consensus", pos, RB.CONSENSUS))
    for pos in COVERAGE_POSITIONS:
        lines.append(PreregLine(f"L{len(lines) + 1:02d}", "coverage", pos))
    return tuple(lines)


def lines_spec() -> dict[str, Any]:
    """Canonical description of the 22 lines and their statistics (what ``lines_sha256`` pins)."""
    return {
        "lines": [line.spec() for line in prereg_lines()],
        "bootstrap": {"n_boot": SPEC_N_BOOT, "seed": SPEC_SEED, "cluster": "season-week",
                      "interval": "95% percentile, paired"},
        "pair_set": "D-077 common pair set (mean raw pred_q50 of the primary-configuration models)",
        "coverage": {"definition": "tie-aware randomized PIT in (0.1, 0.9)", "metric_seed": SPEC_METRIC_SEED},
        "scoring": "ppr",
    }


def lines_spec_sha256() -> str:
    return hashlib.sha256(json.dumps(lines_spec(), sort_keys=True).encode("utf-8")).hexdigest()


# --------------------------------------------------------------------------- #
# Pre-registration and integrity checks
# --------------------------------------------------------------------------- #


@dataclass
class Prereg:
    path: Path
    sha256: str
    data: dict[str, Any]

    @property
    def candidate(self) -> str:
        return str(self.data.get("candidate", "unknown"))

    @property
    def focus(self) -> str:
        return str(self.data.get("focus_model", "chain_v1"))


def load_prereg(path: str | Path) -> Prereg:
    """Read a pre-registration (``.json`` or the first fenced ``json`` block of a ``.md`` file)."""
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix == ".md":
        m = re.search(r"```json\s*\n(.*?)\n```", text, flags=re.S)
        if m is None:
            raise ValueError(f"{p} has no fenced json block with the pre-registration")
        data = json.loads(m.group(1))
    else:
        data = json.loads(text)
    return Prereg(p, hashlib.sha256(p.read_bytes()).hexdigest(), data)


@dataclass(frozen=True)
class Check:
    name: str
    ok: bool
    detail: str


def _resolve(rel: str, root: Path) -> Path:
    p = Path(rel)
    return p if p.is_absolute() else root / p


#: the only path a descendant of the frozen commit may change (the pre-registration itself)
PREREG_DOC = "docs/status/holdout-preregistration.md"


def _run_git(root: Path, *args: str) -> subprocess.CompletedProcess:
    return subprocess.run(["git", *args], cwd=root, capture_output=True, text=True, timeout=60, check=False)


def _full_sha(sha: str, root: Path) -> str | None:
    if not sha or not re.fullmatch(r"[0-9a-fA-F]{7,40}", str(sha)):
        return None
    out = _run_git(root, "rev-parse", "--verify", "--quiet", f"{sha}^{{commit}}")
    return out.stdout.strip() if out.returncode == 0 and out.stdout.strip() else None


@dataclass(frozen=True)
class CommitRelation:
    """How commit ``y`` relates to the frozen commit ``x`` (D-091)."""

    ok: bool
    x: str
    y: str
    files: tuple[str, ...]
    detail: str


def commit_relation(x: str | None, y: str | None, root: Path, allowed: str = PREREG_DOC) -> CommitRelation:
    """Valid when ``y`` is ``x``, or a descendant whose diff from ``x`` is exactly ``allowed``.

    ``git diff --name-only X Y`` must list exactly the pre-registration file
    and nothing else; any other difference, a commit that is not a descendant
    of ``x``, or a sha git does not know makes the relation invalid.
    """
    fx, fy = _full_sha(str(x or ""), root), _full_sha(str(y or ""), root)
    if fx is None or fy is None:
        missing = [s for s, f in ((x, fx), (y, fy)) if f is None]
        return CommitRelation(False, str(x), str(y), (), f"unknown commit {missing}")
    if fx == fy:
        return CommitRelation(True, fx, fy, (), f"at X {fx[:12]}")
    anc = _run_git(root, "merge-base", "--is-ancestor", fx, fy)
    if anc.returncode != 0:
        return CommitRelation(False, fx, fy, (), f"{fy[:12]} is not a descendant of X {fx[:12]}")
    diff = _run_git(root, "diff", "--name-only", fx, fy)
    files = tuple(sorted(f for f in diff.stdout.splitlines() if f.strip()))
    if diff.returncode == 0 and files == (allowed,):
        return CommitRelation(True, fx, fy, files, f"Y {fy[:12]} descends from X {fx[:12]}; diff {list(files)}")
    return CommitRelation(False, fx, fy, files,
                          f"Y {fy[:12]} descends from X {fx[:12]} but the diff is {list(files)}, not exactly [{allowed!r}]")


def _manifest_hashes(node: Any, out: dict[str, str] | None = None, key: str = "") -> dict[str, str]:
    """``{path or key: sha256}`` for every dict with a ``sha256`` under the manifest's calibration record."""
    out = {} if out is None else out
    if isinstance(node, Mapping):
        if "sha256" in node and isinstance(node["sha256"], str):
            out[str(node.get("path") or key)] = node["sha256"]
        for k, v in node.items():
            _manifest_hashes(v, out, str(k))
    elif isinstance(node, list):
        for v in node:
            _manifest_hashes(v, out, key)
    return out


def integrity_checks(
    manifest: Mapping[str, Any], prereg: Prereg, *, root: Path, season: int, weeks: Sequence[int] | None = None,
    snapshots: Mapping[str, Any] | None = None, access_log: Path | None = None,
) -> list[Check]:
    """Every check made before a metric is read (module docstring, step 1)."""
    data = prereg.data
    checks: list[Check] = []
    pinned = data.get("lines_sha256")
    checks.append(Check("approval lines", pinned == lines_spec_sha256(),
                        "the pre-registration pins the 22 lines of this code" if pinned == lines_spec_sha256()
                        else f"pre-registered lines_sha256 {pinned!r} differs from this code's {lines_spec_sha256()}"))
    stats = {"n_boot": SPEC_N_BOOT, "seed": SPEC_SEED, "metric_seed": SPEC_METRIC_SEED}
    got_stats = {k: data.get(k) for k in stats}
    checks.append(Check("bootstrap and seeds", got_stats == stats,
                        f"pre-registered {got_stats}, the pinned spec uses {stats}"))
    commit = data.get("commit")
    gits = sorted({str(g) for g in [manifest.get("git"), *manifest.get("git_sessions", [])] if g})
    rels = [commit_relation(commit, g, root) for g in gits]
    checks.append(Check("commit", bool(commit) and bool(rels) and all(r.ok for r in rels),
                        f"pre-registered X {commit}; run sessions: "
                        + "; ".join(f"{g}: {r.detail}" for g, r in zip(gits, rels, strict=True))))
    head = _run_git(root, "rev-parse", "HEAD").stdout.strip()
    rel = commit_relation(commit, head, root)
    dirty = _run_git(root, "status", "--porcelain", "--untracked-files=no").stdout.strip()
    checks.append(Check("report generation commit", rel.ok,
                        f"generator HEAD {head[:12] or '?'}: {rel.detail}"
                        + ("; tracked files modified in the working tree" if dirty else "; working tree clean")))
    seasons = [int(s) for s in manifest.get("report_seasons", [])]
    ok_season = seasons == [int(season)]
    detail = f"run reports seasons {seasons}, expected [{season}]"
    if weeks is not None:
        run_weeks = sorted(int(w) for w in manifest.get("weeks", []))
        ok_season = ok_season and run_weeks == sorted(int(w) for w in weeks)
        detail += f"; weeks {run_weeks}, expected {sorted(int(w) for w in weeks)}"
    checks.append(Check("seasons and weeks", ok_season, detail))
    frozen = data.get("frozen") or {}
    files = [("FROZEN.json", frozen)] + [(f"calibrator {m}", c) for m, c in (data.get("calibrators") or {}).items()]
    if not frozen:
        checks.append(Check("FROZEN.json", False, "the pre-registration names no FROZEN.json"))
    if not data.get("calibrators"):
        checks.append(Check("calibrators", False, "the pre-registration names no calibrator"))
    for name, rec in files:
        if not rec:
            continue
        path = _resolve(str(rec.get("path", "")), root)
        if not path.is_file():
            checks.append(Check(name, False, f"{rec.get('path')} missing"))
            continue
        got = hashlib.sha256(path.read_bytes()).hexdigest()
        checks.append(Check(name, got == rec.get("sha256"),
                            f"{rec.get('path')}: sha256 {got}" + ("" if got == rec.get("sha256")
                                                                  else f" (pre-registered {rec.get('sha256')})")))
    cal = manifest.get("calibration", {}) or {}
    mode, want = cal.get("mode"), data.get("calibration_mode", "frozen")
    checks.append(Check("calibration mode", mode == want, f"run calibration mode {mode!r}, pre-registered {want!r}"))
    # every calibrator (and FROZEN.json) the run recorded must be pre-registered with the same hash
    recorded = _manifest_hashes(cal)
    expected = {str(c.get("path")): c.get("sha256") for c in (data.get("calibrators") or {}).values()}
    if frozen:
        expected[str(frozen.get("path"))] = frozen.get("sha256")
    problems = [f"{p} not pre-registered" if p not in expected else f"{p} sha256 {h} (pre-registered {expected[p]})"
                for p, h in recorded.items() if expected.get(p) != h]
    if want == "frozen" and not recorded:
        problems.append("the run manifest records no frozen calibrator")
    checks.append(Check("calibrators used by the run", not problems,
                        f"{len(recorded)} recorded, all pre-registered with matching hashes" if not problems
                        else "; ".join(problems)))
    want_snaps = snapshots if snapshots is not None else data.get("snapshots")
    run_snaps = manifest.get("input_snapshots")
    checks.append(Check("snapshot ids", bool(want_snaps) and run_snaps == want_snaps,
                        "run input snapshots equal the pre-registration" if run_snaps == want_snaps
                        else f"run {run_snaps} against pre-registered {want_snaps}"))
    if access_log is not None:
        checks.append(access_log_check(access_log, commit, root))
    return checks


def access_log_check(path: Path, commit: str | None, root: Path = PROJECT_ROOT) -> Check:
    """Exactly one ``holdout_eval`` session at the pre-registered commit or its doc-only descendant (D-049)."""
    entries, bad = parse_access_log(path)
    if bad:
        return Check("holdout access log", False, f"{len(bad)} malformed lines in {path.name}")
    sessions = access_sessions(entries)
    if len(sessions) != 1:
        return Check("holdout access log", False, f"{len(sessions)} access sessions in {path.name}; exactly one is allowed")
    s = sessions[0]
    purposes = sorted({e.purpose for e in s})
    if purposes != [HOLDOUT_PURPOSE]:
        return Check("holdout access log", False, f"session purposes {purposes}, expected [{HOLDOUT_PURPOSE!r}]")
    gits = sorted({e.git for e in s})
    rels = [commit_relation(commit, g, root) for g in gits]
    if not all(r.ok for r in rels):
        return Check("holdout access log", False, "session commits: "
                     + "; ".join(f"{g}: {r.detail}" for g, r in zip(gits, rels, strict=True)))
    return Check("holdout access log", True,
                 f"one {HOLDOUT_PURPOSE} session, {len(s)} lines, {s[0].timestamp.isoformat()} to "
                 f"{s[-1].timestamp.isoformat()}; " + "; ".join(f"{g}: {r.detail}" for g, r in zip(gits, rels, strict=True)))


# --------------------------------------------------------------------------- #
# Evaluating the lines
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class LineResult:
    line: PreregLine
    passed: bool
    detail: str


def _interval_text(d: RB.Delta, digits: int) -> str:
    return (f"difference {RB._num(d.delta, digits, True)}, 95% interval {RB._num(d.lo, digits, True)} to "
            f"{RB._num(d.hi, digits, True)}")


def evaluate_lines(main: RB.BootResult, cons: RB.BootResult | None, focus: str,
                   lines: Sequence[PreregLine] | None = None) -> list[LineResult]:
    """Apply the lines to bootstrap results (calibrated forms); a missing statistic fails its line."""
    f = focus + RB.CAL
    out: list[LineResult] = []
    for line in lines or prereg_lines():
        pos = line.position
        if line.kind == "beats_baseline":
            other = f"{line.comparator}{RB.CAL}"
            if not (main.has(pos, f) and main.has(pos, other)):
                out.append(LineResult(line, False, f"no rows for `{f}` or `{other}` at {pos}"))
                continue
            failures = []
            for metric in BEATS_METRICS:
                d = main.delta(pos, f, other, metric)
                if not (not math.isnan(d.hi) and d.hi < 0):
                    failures.append(f"{RB.METRIC_PHRASE[metric]} ({_interval_text(d, 2)})")
            detail = ("all four intervals entirely below 0" if not failures else
                      f"`{f}` does not beat `{other}` at {pos} on " + "; ".join(failures)
                      + " (interval not entirely below 0)")
            out.append(LineResult(line, not failures, detail))
        elif line.kind == "startsit_consensus":
            if cons is None or not (cons.has(pos, f) and cons.has(pos, RB.CONSENSUS)):
                out.append(LineResult(line, False, f"no consensus comparison at {pos}"))
                continue
            d = cons.delta(pos, f, RB.CONSENSUS, "start_sit_common")
            ok = not math.isnan(d.lo) and not (d.hi < 0)
            detail = (f"not worse ({_interval_text(d, 3)})" if ok else
                      f"`{f}` start/sit at {pos} is worse than the FantasyPros consensus ({_interval_text(d, 3)})"
                      if not math.isnan(d.lo) else f"no interval for start/sit at {pos}")
            out.append(LineResult(line, ok, detail))
        else:
            v = main.value(pos, f, "coverage_tie_aware")
            lo, hi = RB.COVERAGE_BAND
            ok = not math.isnan(v) and lo <= v <= hi
            detail = (f"tie-aware coverage {RB._num(v)}" + ("" if ok else
                      f": `{f}` at {pos} is outside [{lo:.2f}, {hi:.2f}]"))
            out.append(LineResult(line, ok, detail))
    return out


def verdict_text(checks: Sequence[Check], results: Sequence[LineResult] | None, dry_run: bool = False) -> str:
    """The verdict line: INVALID on any failed check, APPROVED only when all 22 lines pass."""
    failed = [c for c in checks if not c.ok]
    if failed:
        text = "Verdict: INVALID (" + "; ".join(f"{c.name}: {c.detail}" for c in failed) + ")"
    elif results is None or len(results) != len(prereg_lines()):
        text = "Verdict: INVALID (the approval lines were not all evaluated)"
    elif all(r.passed for r in results):
        text = VERDICT_APPROVED
    else:
        text = VERDICT_NOT_APPROVED
    if dry_run:
        # a dry run's verdict can never satisfy the registry's exact "Verdict: APPROVED" line (D-076)
        return text.replace("Verdict:", "Dry-run verdict (not a release decision):", 1)
    return text


def forward_text(checks: Sequence[Check], results: Sequence[LineResult] | None) -> str:
    if any(not c.ok for c in checks) or results is None:
        return "Forward check: FAIL"
    return "Forward check: PASS" if all(r.passed for r in results) else "Forward check: FAIL"


# --------------------------------------------------------------------------- #
# Computing one run
# --------------------------------------------------------------------------- #


@dataclass
class RunResult:
    results: list[LineResult] = field(default_factory=list)
    main: RB.BootResult | None = None
    cons: RB.BootResult | None = None
    cells: list[RB.VerdictCell] = field(default_factory=list)
    detail: pl.DataFrame = field(default_factory=pl.DataFrame)
    calib_stats: pl.DataFrame = field(default_factory=pl.DataFrame)
    counts: pl.DataFrame = field(default_factory=pl.DataFrame)
    pop_points: dict[str, Any] = field(default_factory=dict)
    split: list[RB.SplitDelta] = field(default_factory=list)
    best: str = ""
    models: RB.ModelSet | None = None
    rows: int = 0


def bounds_from_run(frame: pl.DataFrame) -> pl.DataFrame | None:
    """Prior-season position bounds as the run recorded them (``pos_min`` / ``pos_max``)."""
    if not {"pos_min", "pos_max"} <= set(frame.columns):
        return None
    return (frame.filter(pl.col("pos_min").is_not_null() & pl.col("pos_max").is_not_null())
            .group_by("season", "position")
            .agg(pl.col("pos_min").min(), pl.col("pos_max").max()))


def _cons_frame(seasons: Sequence[int], purpose: str) -> pl.DataFrame:  # pragma: no cover - restricted data
    from ffml.eval.ecr_baseline import consensus_predictions

    return consensus_predictions(seasons, "ppr", purpose=purpose)


def _cons_exclusions(keys: pl.DataFrame, seasons: Sequence[int], purpose: str) -> pl.DataFrame:  # pragma: no cover
    from ffml.eval.ecr_baseline import consensus_exclusions

    return consensus_exclusions(keys, seasons, purpose=purpose)


def _guard(seasons: Sequence[int], purpose: str) -> None:  # pragma: no cover - holdout guard
    from ffml.holdout import assert_seasons_allowed

    assert_seasons_allowed(seasons, purpose)


def compute_run(run_dir: Path, focus: str, *, purpose: str, n_boot: int, seed: int, metric_seed: int,
                n_jobs: int, include_consensus: bool = True,
                positions: Sequence[str] = RB.POSITIONS) -> RunResult:
    """Every metric of one frozen run (holdout or forward) with the report_backtest functions."""
    manifest = read_manifest(run_dir)
    seasons = [int(s) for s in manifest["report_seasons"]]
    _guard(seasons, purpose)  # logged when the season is the holdout (D-049)
    ppr = load_backtest(run_dir, "ppr")
    present = set(ppr["model"].unique().to_list())
    models = RB.resolve_models(present, focus)
    raw = [m for m in models.raw if m in present]
    ref = RB.reference_models(models, present)
    aligned, _, _ = RB.headline_frames(ppr, raw, present, ref)
    bounds = bounds_from_run(ppr)
    aligned = {m: RB.with_randomized_pit(f, bounds, metric_seed) for m, f in aligned.items()}
    main = RB.run_bootstrap(aligned, positions, n_boot=n_boot, seed=seed, n_jobs=n_jobs)
    out = RunResult(main=main, models=models, rows=ppr.height)
    pooled = {}
    for b in models.baselines:
        if b in aligned:
            pop = M.population(M.validate_frame(RB._slim(aligned[b])))
            pooled[b] = float((pop["pred_q50"] - pop["actual"]).abs().mean())
    out.best = min(pooled, key=pooled.get) if pooled else models.focus
    if include_consensus:
        consensus = _cons_frame(seasons, purpose)
        cons_frames = RB.consensus_population(consensus, aligned, raw, ref)
        cons_frames[RB.CONSENSUS] = RB.with_randomized_pit(cons_frames[RB.CONSENSUS], bounds, metric_seed)
        labels = [m for m in (models.focus, models.focus + RB.CAL, RB.CONSENSUS) if m in cons_frames]
        out.cons = RB.run_bootstrap({m: cons_frames[m] for m in labels}, positions, lineup=False, n_boot=n_boot,
                                    seed=seed, n_jobs=n_jobs)
        active = RB.population_keys(aligned[models.focus])
        matched = RB.population_keys(cons_frames[models.focus])
        unmatched = active.join(matched, on=list(M.KEY_COLUMNS), how="anti")
        status = RB.consensus_status(active, matched, _cons_exclusions(unmatched, seasons, purpose))
        out.counts = RB.status_counts(status, positions)
        split_labels = [m for m in (models.focus + RB.CAL, out.best + RB.CAL) if m in aligned]
        if len(split_labels) == 2:
            pops = RB.split_populations({m: aligned[m] for m in split_labels}, status)
            out.pop_points = {p: RB.points_by_position(fr, positions) for p, fr in pops.items()}
            out.split = RB.split_mae_deltas(pops, split_labels[0], split_labels[1], positions, n_boot=n_boot,
                                            seed=seed)
    metrics = tuple("coverage_gap_tie_aware" if m == "coverage_gap" else m for m in RB.VERDICT_METRICS)
    out.cells = RB.verdict_cells(main, out.cons, models, positions, metrics)
    out.results = evaluate_lines(main, out.cons, models.focus)
    out.detail = RB.coverage_detail(aligned, list(aligned), bounds, positions, seed=metric_seed)
    focus_cal = models.focus + RB.CAL
    if focus_cal in aligned:
        stats, _ = RB.boom_bust_calibration(aligned[focus_cal], aligned[focus_cal], positions, n_boot=n_boot,
                                            seed=seed)
        out.calib_stats = stats.filter(pl.col("version") == "after") if not stats.is_empty() else stats
    return out


# --------------------------------------------------------------------------- #
# Rendering
# --------------------------------------------------------------------------- #


def _checks_table(checks: Sequence[Check]) -> str:
    return RB._md_table(["check", "result", "detail"],
                        [[c.name, "PASS" if c.ok else "**FAIL**", c.detail] for c in checks])


def _lines_table(results: Sequence[LineResult], focus: str) -> str:
    return RB._md_table(["line", "statement", "result", "detail"],
                        [[r.line.id, r.line.describe(focus), "PASS" if r.passed else "**FAIL**", r.detail]
                         for r in results])


def _failures(results: Sequence[LineResult]) -> list[str]:
    return [f"- {r.line.id}: {r.detail}." for r in results if not r.passed]


def _grid(cells: Sequence[RB.VerdictCell], positions: Sequence[str]) -> str:
    out = []
    comparators = list(dict.fromkeys(c.comparator for c in cells))
    metrics = list(dict.fromkeys(c.metric for c in cells))
    for form in ("calibrated", "raw"):
        sub = [c for c in cells if c.form == form]
        if not sub:
            continue
        rows = []
        for pos in positions:
            for metric in metrics:
                row = {c.comparator: c for c in sub if c.position == pos and c.metric == metric}
                if row:
                    rows.append([pos, RB.METRIC_LABEL.get(metric, metric),
                                 *[f"{row[c].status} ({row[c].delta.cell(3)})" if c in row else "" for c in comparators]])
        out.append(f"\n#### Verdict grid, {form} (focus minus comparator, D-078)\n")
        out.append(RB._md_table(["position", "metric", *[f"vs {c}" for c in comparators]], rows))
    return "\n".join(out)


def _calib_after(stats: pl.DataFrame) -> str:
    if stats.is_empty():
        return "Not available."
    rows = []
    for r in stats.iter_rows(named=True):
        flag = "**low confidence**" if r["low_confidence"] else "within the rule"
        d089 = " (D-089: stays flagged)" if r["position"] in ("K", "DST") else ""
        rows.append([r["position"], r["kind"], RB._num(int(r["n"])), RB._num(r["brier"]), RB._num(r["intercept"]),
                     f"{RB._num(r['slope'])} [{RB._num(r['slope_lo'])}, {RB._num(r['slope_hi'])}]", flag + d089])
    return RB._md_table(["position", "kind", "n", "Brier", "intercept", "slope [95% CI]", "D-086 rule"], rows)


def _run_sections(title: str, res: RunResult, checks: Sequence[Check], positions: Sequence[str]) -> list[str]:
    lines = [f"\n## {title}\n", "### Integrity checks (before any metric was read)\n", _checks_table(checks)]
    if not res.results:
        lines.append("\nNo metric was computed.")
        return lines
    focus = res.models.focus if res.models else "chain_v1"
    lines.append(f"\n### The 22 pre-registered lines ({res.rows:,} PPR rows read)\n")
    lines.append(_lines_table(res.results, focus))
    fails = _failures(res.results)
    lines.append("\nFailing lines in plain words:\n")
    lines.extend(fails or ["- none: all 22 lines pass."])
    lines.append("\n### Information outside the 22 lines (not part of the verdict)\n")
    lines.append(_grid(res.cells, positions))
    lines.append("\n#### Closed coverage and tails\n")
    lines.append(RB._coverage_section(res.detail, res.main, res.models, positions, RB.RULE_TIE_AWARE)
                 if res.main is not None else "(none)")
    lines.append("\n#### Boom and bust calibration (frozen calibrator, D-086 rule, D-089 flags)\n")
    lines.append(_calib_after(res.calib_stats))
    lines.append("\n#### Thursday split (D-081)\n")
    lines.append(RB._thursday_section(res.counts, res.pop_points, res.split, res.models, res.best, positions)
                 if res.split else "Not available.")
    return lines


def build_holdout_report(
    holdout_run_id: str | Path,
    forward_run_id: str | Path | None = None,
    prereg_path: str | Path = "docs/status/holdout-preregistration.md",
    *,
    dry_run: bool = False,
    access_log: str | Path | None = None,
    reports_dir: Path | None = None,
    data_dir: Path | None = None,
    n_jobs: int = 4,
    include_consensus: bool = True,
    positions: Sequence[str] = RB.POSITIONS,
    root: Path = PROJECT_ROOT,
) -> Path:
    """Write the release report (or a DRY RUN report under ``data/reports/``); returns its path."""
    ensure_utf8()
    if access_log is not None and not dry_run:
        raise ValueError("the access log may only be overridden in a dry run")
    prereg = load_prereg(_resolve(str(prereg_path), root))
    data = prereg.data
    # the integrity check requires these to equal the pinned spec, so the computation uses the spec's values
    n_boot, seed, metric_seed = SPEC_N_BOOT, SPEC_SEED, SPEC_METRIC_SEED
    log_path = Path(access_log) if access_log is not None else get_settings().artifacts_dir / "holdout_access.log"
    hold_dir = resolve_run_dir(holdout_run_id)
    hold_manifest = read_manifest(hold_dir)
    season = int(data.get("holdout_season", -1))
    hold_checks = integrity_checks(hold_manifest, prereg, root=root, season=season, access_log=log_path)
    hold = RunResult()
    if all(c.ok for c in hold_checks):
        hold = compute_run(hold_dir, prereg.focus, purpose=HOLDOUT_PURPOSE, n_boot=n_boot, seed=seed,
                           metric_seed=metric_seed, n_jobs=n_jobs, include_consensus=include_consensus,
                           positions=positions)
        post = access_log_check(log_path, data.get("commit"), root)
        hold_checks = [*hold_checks, Check("holdout access log after the reads", post.ok, post.detail)]
    verdict = verdict_text(hold_checks, hold.results if hold.results else None, dry_run)
    fwd_checks: list[Check] = []
    fwd = RunResult()
    fwd_line = None
    if forward_run_id is not None:
        fwd_dir = resolve_run_dir(forward_run_id)
        fwd_manifest = read_manifest(fwd_dir)
        fspec = data.get("forward") or {}
        fwd_checks = integrity_checks(fwd_manifest, prereg, root=root, season=int(fspec.get("season", -1)),
                                      weeks=fspec.get("weeks"), snapshots=data.get("forward_snapshots"))
        if not all(c.ok for c in hold_checks):
            fwd_checks.append(Check("holdout session", False, "the holdout session is invalid; not run"))
        if all(c.ok for c in fwd_checks):
            fwd = compute_run(fwd_dir, prereg.focus, purpose=HOLDOUT_PURPOSE, n_boot=n_boot, seed=seed,
                              metric_seed=metric_seed, n_jobs=n_jobs, include_consensus=include_consensus,
                              positions=positions)
            # the forward reads (its consensus curve uses the holdout season) must stay in the one session;
            # a failure here fails the forward check only, never the verdict above (D-094)
            post = access_log_check(log_path, data.get("commit"), root)
            fwd_checks.append(Check("holdout access log after the forward reads", post.ok, post.detail))
        fwd_line = forward_text(fwd_checks, fwd.results if fwd.results else None)

    head = RB._git("rev-parse", "--short", "HEAD")
    title = f"Holdout release report: `{prereg.candidate}`" + (" (DRY RUN)" if dry_run else "")
    lines = [f"# {'DRY RUN: ' if dry_run else ''}{title}\n"]
    if dry_run:
        lines.append("> **DRY RUN.** A mechanics test on a non-holdout run with a test pre-registration. It is not a "
                     "release decision, is not committed, and its verdict line cannot be used by the registry.\n")
    lines.append(verdict)
    if fwd_line is not None:
        lines.append("")
        lines.append(fwd_line)
    lines.append(
        "\nThe forward check never changes the verdict above (D-094). Under the owner's rule a forward FAIL means "
        "the orchestrator stops before publishing projections. A NOT APPROVED verdict is final: nothing is tuned "
        "on the holdout and the session is not rerun for a better result (D-092, D-093).\n"
    )
    lines.append("## Pre-registration\n")
    lines.append(RB._md_table(["item", "value"], [
        ["file", f"`{prereg.path.relative_to(root) if prereg.path.is_relative_to(root) else prereg.path}`, "
                 f"sha256 `{prereg.sha256}`"],
        ["candidate", f"`{prereg.candidate}` (focus `{prereg.focus}{RB.CAL}`)"],
        ["commit", f"`{data.get('commit')}`; report generated at `{head}`"],
        ["holdout season", str(season)],
        ["forward", json.dumps(data.get("forward"))],
        ["calibration mode", str(data.get("calibration_mode", "frozen"))],
        ["bootstrap", f"{n_boot} week-clustered paired draws, seed {seed}; metric seed {metric_seed}"],
        ["approval lines", f"22 lines, spec sha256 `{lines_spec_sha256()}`"],
    ]))
    lines.extend(_run_sections(f"Holdout season {season}", hold, hold_checks, positions))
    if forward_run_id is not None:
        lines.extend(_run_sections("Forward check (D-094)", fwd, fwd_checks, positions))
    text = "\n".join(lines).rstrip() + "\n"
    if dry_run:
        out_dir = Path(data_dir) if data_dir is not None else root / "data" / "reports"
        out = out_dir / f"evaluator_release_{prereg.candidate}_DRYRUN.md"
    else:
        out_dir = Path(reports_dir) if reports_dir is not None else root / "reports"
        out = out_dir / f"evaluator_release_{prereg.candidate}.md"
    out_dir.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return out


def main(argv: list[str] | None = None) -> int:
    ensure_utf8()
    parser = argparse.ArgumentParser(prog="python -m ffml.eval.report_holdout", description=__doc__.split("\n")[0])
    parser.add_argument("--holdout-run-id", required=True)
    parser.add_argument("--forward-run-id", default=None)
    parser.add_argument("--prereg", default="docs/status/holdout-preregistration.md")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--access-log", default=None, help="override the access log (dry runs only)")
    parser.add_argument("--n-jobs", type=int, default=4)
    args = parser.parse_args(argv)
    if args.access_log and not args.dry_run:
        parser.error("--access-log may only be overridden in a dry run")
    path = build_holdout_report(args.holdout_run_id, args.forward_run_id, args.prereg, dry_run=args.dry_run,
                                access_log=args.access_log, n_jobs=args.n_jobs)
    print(f"wrote {path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())


__all__ = [
    "BEATS_METRICS",
    "Check",
    "LineResult",
    "PreregLine",
    "Prereg",
    "RunResult",
    "PREREG_DOC",
    "CommitRelation",
    "access_log_check",
    "build_holdout_report",
    "commit_relation",
    "evaluate_lines",
    "forward_text",
    "integrity_checks",
    "lines_spec",
    "lines_spec_sha256",
    "load_prereg",
    "prereg_lines",
    "verdict_text",
]
