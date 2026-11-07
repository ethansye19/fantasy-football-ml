"""Weekly runner for the chain_v2 live test (D-098, D-099).

For the week whose first kickoff is next (from ``dim_game`` ``kickoff_utc``),
before that kickoff:

1. refresh the data with the pinned interpreter (``python -m ffml.data.refresh``,
   one step per process; new snapshots only, and existing snapshots are
   fingerprinted before and after and must be unchanged);
2. run ``ffml livetest build-week --season S --week W`` (optionally from a
   separate worktree pinned at the pre-registered commit, with its own venv);
3. commit ``commitment.json`` (plus ``player_summary.parquet`` under 5 MB) to
   the orphan branch ``livetest-2026`` in a separate worktree and push that
   branch only, then record the push result and GitHub's view of it;
4. re-hash the files and compare them with the manifest and the pushed blobs.

Every run writes ``data/livetest/logs/<UTC stamp>_<label>.jsonl``. The runner
is idempotent: a week whose commitment is already pushed is left alone, and a
commitment that was built but not pushed is pushed as is, never rebuilt.

Commands::

    python scripts/livetest_week.py init --season 2026 --weeks 10-18 \
        [--code-worktree P --python P --expected-commit SHA]
    python scripts/livetest_week.py run [--season S --week W] [--rehearsal] [--dry-run]
    python scripts/livetest_week.py watchdog [--season S --week W]
    python scripts/livetest_week.py check            # daily: stale predictions, schedule drift, misses
    python scripts/livetest_week.py plan [--json] [--save] [--export scripts/livetest_schedule_2026.json]
    python scripts/livetest_week.py status [--json]
    python scripts/livetest_week.py verify --season S --week W [--rehearsal]
    python scripts/livetest_week.py run --dry-run-branch livetest-dryrun [--force-fail refresh|build|push] ...
    python scripts/livetest_week.py score-week [--season S --week W] [--skip-refresh]   # Fridays
    python scripts/livetest_week.py seal --season S --week W
    python scripts/livetest_week.py seal-check --season S --week W

Exit codes: 0 done or nothing to do; 1 failed, retry later; 2 build blocked
or pin refused; 3 missed (too late for this week); 4 refused (bad arguments
or unsafe environment); 6 integrity problem (needs a human); 7 an existing
snapshot was modified.

Timing defaults (relative to the official first kickoff K; tentative weeks use
K - 24 h): act from K - 25 h; scheduled start K - 24 h 15 min with retries
every 30 min until a valid commitment is pushed (D-106, D-107); no refresh
starts after K - 3 h (the build then uses the newest snapshots, with an
alert); no refresh or build starts after K - 30 min; no push starts after
K - 5 min; nothing counts for the week after K.

Missed weeks (D-106, D-111): after K, a week without a pushed commitment gets
``<week>/MISSED.json`` on the branch with the reason (``technical_failure``
with the last error, or ``machine_offline`` when no runner log exists in the
window), the log excerpt, the miss number, the allowed count and the test
state. The allowed count is read from the pre-registration
(``missed_weeks.allowed``, 2 under D-111): that many misses are excluded from
scoring without extending the window, one more makes the test INVALID. A
commitment built before K but not pushed in time is pushed under
``<week>/late/`` so projections that exist are never withheld. ``STATUS.md``
on the branch is regenerated at every push.

Sealed hashes (pre-registration ``sealed_interim``): after the Evaluator's
scorer seals a week (``data/livetest/scoring/sealed/<week>.sha256``),
``seal`` publishes those hashes as ``<week>/sealed_hashes.json`` on the branch,
idempotently; ``seal-check`` compares the branch copy with the local sealed
files; ``score-week`` is the Friday job (D-116, A3): the week's data cutoff
from the scorer, nothing before it, a refresh at or after it, the Evaluator's
scorer for the oldest unsealed committed week (above-normal priority,
360-minute timeout, A7), then ``seal``.

Pre-build check (D-116, A1): before every ``build-week`` the runner runs
``<python> -m ffml.eval.livetest_score --prebuild-check --from <last build's code
commit> --to <pinned HEAD>`` in the pinned worktree. Exit 0 builds; exit 5 (or
any failure to run the check) blocks the build with a ``prebuild_blocked``
alert, and the normal 30-minute loop retries. A block fixed before the
deadline is not a miss; otherwise the MISSED marker says "pre-build check
blocked".

Dry runs (D-107): ``--dry-run-branch <name>`` pushes to that branch only
(never ``livetest-2026``), builds in the rehearsal sandbox without deadlines,
and ``--force-fail refresh|build|push`` makes one step fail on purpose to
prove the alert and the retry.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import alert as alerts  # noqa: E402
import livetest_core as core  # noqa: E402

EXIT_OK = 0
EXIT_RETRY = 1
EXIT_BLOCKED = 2
EXIT_MISSED = 3
EXIT_REFUSED = 4
EXIT_INTEGRITY = 6
EXIT_SNAPSHOT = 7

BUILD_OK, BUILD_FAILED, BUILD_BLOCKED, BUILD_REFUSED_KICKOFF, BUILD_ALREADY_COMMITTED = 0, 1, 2, 3, 4
REFRESH_STEPS = ("ingest", "sleeper", "process", "validate", "features")
ALERT_COOLDOWN_MINUTES = 120.0
RESULT_PREFIX = "REFRESH_RESULT "
FORCE_FAIL_STEPS = ("refresh", "prebuild", "build", "push")
SCORER_MODULE = "ffml.eval.livetest_score"
ABOVE_NORMAL_PRIORITY_CLASS = 0x00008000  # Windows; the scorer runs above normal priority (A7)
PREBUILD_BLOCKED = 5  # scorer --prebuild-check exit code for "blocked"
SCORER_NOT_YET = 4  # scorer exit code for "not yet, retry" (no alert)
#: the datasets the scorer pins (ffml.eval.livetest_score.PINNED_DATASETS), as paths under data/processed
PINNED_DATASETS: tuple[str, ...] = ("dim_game", "fact_player_game", "fact_dst_game", "restricted/fact_ecr_weekly")
MAX_SCORE_REFRESHES = 2  # refreshes per week and pin point (R1: a straddling refresh is repeated once)


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


@dataclass
class Config:
    main_repo: Path
    data_dir: Path
    artifacts_dir: Path
    reports_dir: Path
    first_season: int
    branch: str = core.DEFAULT_BRANCH
    remote: str = core.DEFAULT_REMOTE
    commitments_worktree: Path = core.DEFAULT_COMMITMENTS_WORKTREE
    code_worktree: Path | None = None
    python: Path = Path(sys.executable)
    expected_commit: str | None = None
    test_season: int | None = None
    weeks: list[int] = field(default_factory=list)
    season: int | None = None
    week: int | None = None
    rehearsal: bool = False
    stub_build: bool = False
    stub_score: bool = False  # scripts/livetest_stub_score.py instead of the scorer module (testing)
    allow_stub_push: bool = False
    skip_refresh: bool = False
    refresh_steps: tuple[str, ...] = REFRESH_STEPS
    max_refresh_attempts: int = 3
    no_push: bool = False
    dry_run: bool = False
    now_override: datetime | None = None
    live_schedule: bool = True
    timing: core.Timing = field(default_factory=core.Timing)
    trailers: list[str] = field(default_factory=list)
    build_timeout_minutes: float = 240.0
    refresh_step_timeout_minutes: float = 60.0
    retry_every_minutes: float = 0.0
    check_env: bool = True
    github: bool = True
    dry_run_branch: str | None = None  # push to this branch only; implies the rehearsal sandbox
    real_branch: str = core.DEFAULT_BRANCH
    force_fail: str | None = None  # dry-run branch only
    preregistration: Path | None = None  # the pre-registration (JSON, or the fenced block of the .md)
    misses_allowed: int | None = None  # missed_weeks.allowed from the pre-registration (D-111: 2)
    misses_allowed_error: str | None = None
    window: list[tuple[int, int]] | None = None  # the pre-registered window, both seasons (QA-F30)
    prereg_data: dict[str, Any] | None = None  # the loaded pre-registration (path policy, frozen commit X)
    scorer_timeout_minutes: float = 360.0  # A7

    def window_keys(self) -> list[str]:
        """The test window's week keys in order: the pre-registration's window, else the configured season."""
        if self.window:
            return [core.week_key(s, w) for s, w in self.window]
        return [core.week_key(self.test_season, w) for w in self.weeks] if self.test_season is not None else []

    @property
    def sealed_dir(self) -> Path:
        return self.data_dir.joinpath(*core.SEALED_SUBDIR)

    @property
    def livetest_dir(self) -> Path:
        return self.data_dir / "livetest"

    @property
    def logs_dir(self) -> Path:
        return self.livetest_dir / "logs"

    @property
    def config_path(self) -> Path:
        return self.livetest_dir / "config.json"

    @property
    def pinned(self) -> bool:
        return self.code_worktree is not None

    @property
    def code_cwd(self) -> Path:
        return self.code_worktree if self.code_worktree is not None else self.main_repo

    def commitment_dir(self, season: int, week: int) -> Path:
        base = self.livetest_dir / "rehearsal" if self.rehearsal else self.livetest_dir
        return base / core.week_key(season, week)

    def runner_dir(self, season: int, week: int) -> Path:
        base = self.livetest_dir / "runner"
        if self.dry_run_branch:
            base = base / "dryrun" / re.sub(r"[^A-Za-z0-9._-]+", "-", self.dry_run_branch)
        elif self.rehearsal:
            base = base / "rehearsal"
        return base / core.week_key(season, week)

    def child_env(self) -> dict[str, str]:
        env = core.clean_child_env(
            data_dir=self.data_dir, artifacts_dir=self.artifacts_dir, reports_dir=self.reports_dir
        )
        if self.now_override is not None:
            env["FFML_LIVETEST_NOW"] = core.iso(self.now_override) or ""
        return env

    def public(self) -> dict[str, Any]:
        out = asdict(self)
        out["timing"] = asdict(self.timing)
        return {k: (str(v) if isinstance(v, Path) else v) for k, v in out.items()}


@dataclass
class Deps:
    """Injection points for tests; the defaults do the real thing."""

    clock: Callable[[], datetime] = core.utcnow
    run: Callable[..., subprocess.CompletedProcess] = subprocess.run
    git_runner: Callable[..., subprocess.CompletedProcess] = subprocess.run
    alert: Callable[..., Any] = alerts.send_alert
    github_view: Callable[..., dict[str, Any]] = core.github_view
    live_schedule: Callable[[int, datetime], Any] | None = core.fetch_live_schedule
    sleep: Callable[[float], None] = time.sleep


class Abort(Exception):
    """Stop the run with an exit code (already logged and alerted)."""

    def __init__(self, code: int, message: str = "") -> None:
        super().__init__(message)
        self.code = code


# --------------------------------------------------------------------------- #
# Context for one run
# --------------------------------------------------------------------------- #


class Run:
    def __init__(self, cfg: Config, deps: Deps, label: str, echo: bool = True) -> None:
        self.cfg = cfg
        self.deps = deps
        self.log = core.RunLog(cfg.logs_dir, label, now=self.now(), echo=echo)
        self.pin: core.PinResult | None = None
        self.log.event("start", "info", argv=sys.argv, config=cfg.public(), log=str(self.log.path))

    def now(self) -> datetime:
        return self.cfg.now_override or self.deps.clock()

    def alert(self, kind: str, message: str, *, key: str, severity: str = "error", **details: Any) -> None:
        try:
            result = self.deps.alert(
                kind,
                message,
                severity=severity,
                details={**details, "run_log": str(self.log.path), "rehearsal": self.cfg.rehearsal,
                         "dry_run_branch": self.cfg.dry_run_branch},
                dedupe_key=f"livetest:{kind}:{key}"
                + (f":dryrun:{self.cfg.dry_run_branch}" if self.cfg.dry_run_branch
                   else ":rehearsal" if self.cfg.rehearsal else ""),
                cooldown_minutes=ALERT_COOLDOWN_MINUTES,
                now=self.now() if self.cfg.now_override else None,
            )
            channels = getattr(result, "channels", None)
        except Exception as exc:  # an alert failure must never stop the run
            channels = {"error": f"{type(exc).__name__}: {exc}"}
        self.log.event("alert", severity, kind=kind, message=message, channels=channels)

    def abort(self, code: int, step: str, message: str, *, alert_kind: str | None = None,
              key: str = "", severity: str = "error", **details: Any) -> Abort:
        self.log.event(step, "failed" if code else "done", message=message, exit_code=code, **details)
        if alert_kind:
            self.alert(alert_kind, message, key=key or step, severity=severity, **details)
        return Abort(code, message)

    def repo(self) -> core.CommitmentsRepo:
        return core.CommitmentsRepo(
            self.cfg.main_repo, self.cfg.commitments_worktree, self.cfg.branch, self.cfg.remote,
            runner=self.deps.git_runner,
        )


# --------------------------------------------------------------------------- #
# Plan
# --------------------------------------------------------------------------- #


def merge_plans(base: list[core.WeekKickoff], live: list[core.WeekKickoff]) -> tuple[list[core.WeekKickoff], list[dict[str, Any]]]:
    """Combine ``dim_game`` and the live schedule: earliest first kickoff wins."""
    by_key = {(w.season, w.week): w for w in live}
    merged, diffs = [], []
    for w in base:
        other = by_key.get((w.season, w.week))
        if other is None:
            merged.append(w)
            continue
        first = min(w.first_kickoff_utc, other.first_kickoff_utc)
        new = core.WeekKickoff(
            season=w.season,
            week=w.week,
            first_kickoff_utc=first,
            last_kickoff_utc=max(w.last_kickoff_utc, other.last_kickoff_utc),
            games=max(w.games, other.games),
            games_final=max(w.games_final, other.games_final),
            tentative=w.tentative and other.tentative,
        )
        if (w.first_kickoff_utc, w.tentative) != (other.first_kickoff_utc, other.tentative):
            diffs.append({
                "week": w.key,
                "dim_game": core.iso(w.first_kickoff_utc),
                "dim_game_tentative": w.tentative,
                "live_schedule": core.iso(other.first_kickoff_utc),
                "live_tentative": other.tentative,
            })
        merged.append(new)
    return merged, diffs


def load_plan(run: Run, season_hint: int | None = None, live: bool | None = None) -> list[core.WeekKickoff]:
    cfg = run.cfg
    try:
        dim = core.load_dim_game(cfg.data_dir)
    except FileNotFoundError as exc:
        raise run.abort(EXIT_REFUSED, "plan", f"no dim_game snapshot: {exc}", alert_kind="build_failure", key="plan") from None
    plan = core.week_plan(dim)
    run.log.event("plan", "loaded", source="dim_game", snapshot=core.dim_game_snapshot_id(cfg.data_dir), weeks=len(plan))
    use_live = cfg.live_schedule if live is None else live
    if use_live and run.deps.live_schedule is not None:
        season = season_hint
        if season is None:
            upcoming = core.next_week(plan, run.now())
            season = upcoming.season if upcoming else None
        if season is not None:
            try:
                live_plan = core.week_plan(run.deps.live_schedule(season, run.now()), season)
                plan, diffs = merge_plans(plan, live_plan)
                run.log.event("plan", "live schedule checked", season=season, differences=diffs)
            except Exception as exc:  # network trouble: the snapshot still counts
                run.log.event("plan", "live schedule unavailable", message=f"{type(exc).__name__}: {exc}")
    return plan


def select_week(run: Run, plan: list[core.WeekKickoff]) -> core.WeekKickoff | None:
    cfg = run.cfg
    if cfg.season is not None and cfg.week is not None:
        wk = core.find_week(plan, cfg.season, cfg.week)
        if wk is None:
            raise run.abort(EXIT_REFUSED, "select", f"{cfg.season} week {cfg.week} is not in the schedule")
        return wk
    if (cfg.season is None) != (cfg.week is None):
        raise run.abort(EXIT_REFUSED, "select", "give both --season and --week, or neither")
    return core.next_week(plan, run.now())


# --------------------------------------------------------------------------- #
# Branch layout
# --------------------------------------------------------------------------- #


def branch_prefix(cfg: Config, wk: core.WeekKickoff, commitment: core.Commitment | None) -> str | None:
    if cfg.dry_run_branch or not cfg.rehearsal:
        return wk.key  # a dry-run branch mirrors the real layout so the watchdogs find the file
    if commitment is None or commitment.created_at is None:
        return None
    return f"rehearsal/{wk.key}-{core.compact(commitment.created_at)}"


def commit_message(cfg: Config, wk: core.WeekKickoff, c: core.Commitment, run: Run, refresh: dict[str, Any]) -> str:
    title = (
        f"DRY RUN livetest {wk.key} on {cfg.branch}: {'stub ' if c.stub else ''}commitment, not part of the test"
        if cfg.dry_run_branch
        else f"REHEARSAL livetest {wk.key}: stub commitment, not part of the test"
        if cfg.rehearsal and c.stub
        else f"REHEARSAL livetest {wk.key}: commitment, not part of the test"
        if cfg.rehearsal
        else f"livetest {wk.key}: commitment before first kickoff {core.iso(wk.first_kickoff_utc)}"
    )
    lines = [
        title,
        "",
        f"commitment.json sha256: {c.sha256}",
        f"commitment digest: {c.digest}",
        f"season/week: {c.season} week {c.week}",
        f"model_version: {c.data.get('model_version')}",
        f"code_commit: {c.code_commit}",
        f"created_at: {core.iso(c.created_at)}",
        f"first kickoff (schedule): {core.iso(wk.first_kickoff_utc)}"
        + (" (tentative)" if wk.tentative else ""),
        f"label: {c.data.get('label') or c.data.get('approval_status')}",
        f"refresh: {refresh.get('status', 'unknown')}",
        f"committed at (local clock): {core.iso(run.now())}",
        "pushed by: scripts/livetest_week.py",
    ]
    if c.stub:
        lines.append("STUB: produced by scripts/livetest_stub_build.py; contains no model output")
    if cfg.trailers:
        lines.append("")
        lines.extend(cfg.trailers)
    return "\n".join(lines) + "\n"


# --------------------------------------------------------------------------- #
# Per-week state
# --------------------------------------------------------------------------- #


def load_state(cfg: Config, wk: core.WeekKickoff) -> dict[str, Any]:
    return core.read_json(cfg.runner_dir(wk.season, wk.week) / "state.json") or {
        "season": wk.season,
        "week": wk.week,
        "refresh_attempts": 0,
        "refresh_ok_at": None,
        "build_attempts": 0,
    }


def save_state(cfg: Config, wk: core.WeekKickoff, state: dict[str, Any]) -> None:
    core.write_json(cfg.runner_dir(wk.season, wk.week) / "state.json", state)


def local_commitment(run: Run, wk: core.WeekKickoff) -> core.Commitment | None:
    path = run.cfg.commitment_dir(wk.season, wk.week) / core.COMMITMENT_NAME
    if not path.exists():
        return None
    try:
        return core.load_commitment(path)
    except (ValueError, OSError) as exc:
        raise run.abort(
            EXIT_INTEGRITY, "commitment", f"{path} exists but cannot be read: {exc}",
            alert_kind="integrity", key=wk.key, severity="critical",
        ) from None


def timing_kickoff(cfg: Config, wk: core.WeekKickoff) -> datetime | None:
    """The kickoff that ``created_at`` must precede; rehearsals make no timing claim."""
    return None if cfg.rehearsal else wk.first_kickoff_utc


def ffml_verify(run: Run, path: Path) -> dict[str, Any]:
    """Second opinion from the build code itself: ``ffml livetest verify <path>``."""
    cfg = run.cfg
    cmd = [str(cfg.python), "-m", "ffml.cli", "livetest", "verify", str(path)]
    try:
        proc = run.deps.run(cmd, cwd=str(cfg.code_cwd), env=cfg.child_env(), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=600)
    except (OSError, subprocess.TimeoutExpired) as exc:
        return {"returncode": None, "output": f"{type(exc).__name__}: {exc}"}
    out = (proc.stdout or "") + (proc.stderr or "")
    if proc.returncode == 2 and "No such command" in out:
        return {"returncode": None, "output": "unavailable in this code version"}
    return {"returncode": proc.returncode, "output": out.strip()[-1500:]}


def roots(cfg: Config, wk: core.WeekKickoff) -> list[Path]:
    return [cfg.data_dir, cfg.commitment_dir(wk.season, wk.week), cfg.code_cwd, cfg.main_repo]


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def scorer_cmd(cfg: Config, *args: str) -> list[str]:
    """``<python> -m ffml.eval.livetest_score <args>``, or the stub with ``--stub-score``."""
    if cfg.stub_score:
        return [str(cfg.python), str(SCRIPTS_DIR / "livetest_stub_score.py"), *args]
    return [str(cfg.python), "-m", SCORER_MODULE, *args]


def scorer_creationflags() -> int:
    """Above-normal priority for the weekly scorer on Windows; nothing elsewhere (A7)."""
    if sys.platform == "win32":
        return int(getattr(subprocess, "ABOVE_NORMAL_PRIORITY_CLASS", ABOVE_NORMAL_PRIORITY_CLASS))
    return 0


def parse_json_output(text: str | None) -> dict[str, Any] | None:
    """The JSON object a scorer command printed: the whole output, else its last JSON line."""
    text = (text or "").strip()
    if not text:
        return None
    try:
        data = json.loads(text)
        return data if isinstance(data, dict) else {"value": data}
    except ValueError:
        pass
    for line in reversed(text.splitlines()):
        line = line.strip()
        if line.startswith("{"):
            try:
                data = json.loads(line)
                return data if isinstance(data, dict) else None
            except ValueError:
                continue
    return None


def run_scorer(run: Run, args: list[str], *, timeout_minutes: float, priority: bool = False) -> tuple[int, str, str]:
    cfg = run.cfg
    cmd = scorer_cmd(cfg, *args)
    kwargs: dict[str, Any] = {"cwd": str(cfg.code_cwd), "env": cfg.child_env(), "capture_output": True, "text": True,
                              "encoding": "utf-8", "errors": "replace", "timeout": timeout_minutes * 60}
    if priority and scorer_creationflags():
        kwargs["creationflags"] = scorer_creationflags()
    try:
        proc = run.deps.run(cmd, **kwargs)
        return proc.returncode, proc.stdout or "", proc.stderr or ""
    except subprocess.TimeoutExpired as exc:
        return -9, _tail(exc.stdout), f"timed out after {timeout_minutes} min"
    except OSError as exc:
        return -1, "", f"{type(exc).__name__}: {exc}"


def last_build_commit(run: Run, repo: core.CommitmentsRepo, remote: bool | None) -> tuple[str, str]:
    """The code commit of the latest commitment on the branch (``""`` before the first build).

    The latest is the one with the newest ``created_at`` among ``<week>/commitment.json``
    and ``<week>/late/commitment.json``; rehearsal commitments are not builds of the
    test. If the remote cannot be read, the local commitments stand in (logged).
    """
    pattern = re.compile(r"^\d{4}w\d{2}/(late/)?commitment\.json$")
    found: list[tuple[datetime, str, str]] = []
    if remote is not None:
        for rel in repo.remote_paths():
            if not pattern.match(rel):
                continue
            blob = repo.remote_blob(rel)
            try:
                c = core.Commitment(path=Path(rel), raw=blob or b"", data=json.loads(blob or b"{}"), files=[])
            except ValueError:
                continue
            if c.code_commit and c.created_at:
                found.append((c.created_at, c.code_commit, f"{repo.branch}:{rel}"))
        source = "branch"
    else:
        for path in sorted(run.cfg.livetest_dir.glob("[0-9][0-9][0-9][0-9]w[0-9][0-9]/commitment.json")):
            try:
                c = core.load_commitment(path)
            except (ValueError, OSError):
                continue
            if c.code_commit and c.created_at:
                found.append((c.created_at, c.code_commit, str(path)))
        source = "local commitments (remote unreachable)"
    if not found:
        return "", f"no earlier build ({source})"
    created, commit, where = max(found)
    return commit, f"{where} (created {core.iso(created)})"


def code_head(run: Run) -> str:
    """The commit the build will run: the pinned worktree's HEAD (or the main checkout's)."""
    if run.pin is not None:
        return run.pin.commit
    proc = run.deps.run(["git", "-C", str(run.cfg.code_cwd), "rev-parse", "HEAD"], capture_output=True, text=True,
                        encoding="utf-8", errors="replace", timeout=60)
    return (proc.stdout or "").strip()


def prebuild_check(run: Run, wk: core.WeekKickoff, repo: core.CommitmentsRepo, remote: bool | None) -> None:
    """D-116 B.1 with A1: block the build unless every commit since the last build is in the change log."""
    cfg = run.cfg
    key = wk.key
    from_commit, from_source = last_build_commit(run, repo, remote)
    to_commit = code_head(run)
    args = ["--prebuild-check", "--from", from_commit, "--to", to_commit]
    if cfg.force_fail == "prebuild":
        code, result = PREBUILD_BLOCKED, {"forced": True, "uncovered": [], "frozen": [],
                                          "message": "forced pre-build block (--force-fail prebuild)"}
        out = err = ""
    else:
        code, out, err = run_scorer(run, args, timeout_minutes=30)
        result = parse_json_output(out)
    uncovered = list((result or {}).get("uncovered") or (result or {}).get("uncovered_commits") or [])
    frozen = list((result or {}).get("frozen") or (result or {}).get("frozen_commits") or [])
    run.log.event("prebuild", "passed" if code == 0 else "checked", from_commit=from_commit, from_source=from_source,
                  to_commit=to_commit, returncode=code, result=result, stdout_tail=_tail(out, 800),
                  stderr_tail=_tail(err, 800))
    if code == 0:
        return
    if code == PREBUILD_BLOCKED:
        message = (f"{key}: pre-build check blocked the build: {len(uncovered)} commit(s) without a change-log "
                   f"entry {uncovered[:10]}, {len(frozen)} touching a frozen path {frozen[:10]}; fix the change log "
                   "(frozen paths need the owner's ruling, D-111); the runner retries every 30 min")
    else:
        message = (f"{key}: pre-build check could not run (exit {code}); the build is blocked until it passes; "
                   f"{_tail(err, 200)}")
    raise run.abort(EXIT_BLOCKED, "prebuild", message, alert_kind="prebuild_blocked", key=f"{key}:prebuild",
                    severity="critical", uncovered=uncovered, frozen=frozen, from_commit=from_commit,
                    to_commit=to_commit, check=result)


def verify_pin(run: Run) -> None:
    cfg = run.cfg
    if not cfg.pinned:
        run.log.event("pin", "not pinned", message="build runs from the main checkout", python=str(cfg.python))
        return
    if not cfg.expected_commit:
        raise run.abort(EXIT_REFUSED, "pin", "--code-worktree needs --expected-commit")
    try:
        run.pin = core.verify_code_pin(
            cfg.code_worktree, cfg.expected_commit, cfg.python,
            main_repo=cfg.main_repo, check_env=cfg.check_env, runner=run.deps.run,
        )
    except core.PinError as exc:
        raise run.abort(EXIT_BLOCKED, "pin", f"refused: {exc}", alert_kind="build_failure", key="pin") from None
    run.log.event("pin", "verified", **run.pin.to_dict())
    prereg = cfg.prereg_data or {}
    x = core.frozen_commit_from(prereg)
    if x is None:
        run.log.event("pin", "descendant check skipped",
                      message="the pre-registration's commit field is not filled yet (before the freeze)")
        return
    try:
        relation = core.verify_descends_from_frozen(
            cfg.code_worktree, x, run.pin.commit, (prereg.get("allowed_paths") or {}).keys(),
            prereg.get("frozen_paths") or [], runner=run.deps.run)
    except core.PinError as exc:
        raise run.abort(EXIT_BLOCKED, "pin", f"refused: {exc}", alert_kind="build_failure", key="pin") from None
    run.log.event("pin", "descends from the frozen commit", **relation)


def _tail(text: str | bytes | None, n: int = 1500) -> str:
    if text is None:
        return ""
    if isinstance(text, bytes):
        text = text.decode("utf-8", "replace")
    return text[-n:]


def run_refresh(run: Run, wk: core.WeekKickoff, state: dict[str, Any]) -> dict[str, Any]:
    """Run the refresh steps; returns {"status": ok|failed|stale, ...}. Raises Abort on snapshot changes."""
    cfg = run.cfg
    seasons = f"{cfg.first_season}-{wk.season}"
    report = cfg.logs_dir / f"validation_{core.compact(run.now())}.md"
    before = core.fingerprint_snapshots(cfg.data_dir)
    run.log.event("refresh", "started", steps=list(cfg.refresh_steps), seasons=seasons,
                  snapshots_before=len(before), attempt=state["refresh_attempts"] + 1)
    state["refresh_attempts"] += 1
    state["refresh_last_started"] = core.iso(run.now())
    save_state(cfg, wk, state)
    status, results = "ok", []
    for step in cfg.refresh_steps:
        cmd = [str(cfg.python), "-m", "ffml.data.refresh", "--step", step, "--seasons", seasons,
               "--report", str(report)]
        t0 = time.perf_counter()
        try:
            proc = run.deps.run(cmd, cwd=str(cfg.code_cwd), env=cfg.child_env(), capture_output=True, text=True,
                                encoding="utf-8", errors="replace", timeout=cfg.refresh_step_timeout_minutes * 60)
            code, out, err = proc.returncode, proc.stdout, proc.stderr
        except subprocess.TimeoutExpired as exc:
            code, out, err = -9, _tail(exc.stdout), f"timed out after {cfg.refresh_step_timeout_minutes} min"
        except OSError as exc:
            code, out, err = -1, "", f"{type(exc).__name__}: {exc}"
        parsed = None
        for line in (out or "").splitlines():
            if line.startswith(RESULT_PREFIX):
                try:
                    parsed = json.loads(line[len(RESULT_PREFIX):])
                except ValueError:
                    pass
        row = {"refresh_step": step, "returncode": code, "seconds": round(time.perf_counter() - t0, 1), "result": parsed}
        results.append(row)
        run.log.event("refresh", "step ok" if code == 0 else "step failed", **row,
                      stdout_tail=_tail(out, 800), stderr_tail=_tail(err))
        if code != 0:
            status = "validate_failed" if step == "validate" else "failed"
            break
    after = core.fingerprint_snapshots(cfg.data_dir)
    problems = core.compare_fingerprints(before, after)
    if problems:
        raise run.abort(
            EXIT_SNAPSHOT, "refresh", f"existing snapshots changed during the refresh: {problems[:5]}",
            alert_kind="integrity", key=wk.key, severity="critical", problems=problems,
        )
    run.log.event("refresh", "snapshots unchanged", snapshots_before=len(before), snapshots_after=len(after),
                  new=sorted(set(after) - set(before)))
    info: dict[str, Any] = {"status": status, "steps": results, "latest": core.latest_ids(cfg.data_dir)}
    if status == "ok":
        try:
            dim = core.load_dim_game(cfg.data_dir, [wk.season])
            import polars as pl

            pending = dim.filter(
                (pl.col("game_type") == "REG") & (pl.col("week") < wk.week) & (pl.col("status") != "final")
            )
            if pending.height:
                info["status"] = "stale"
                info["not_final"] = pending["game_id"].to_list()
        except Exception as exc:  # pragma: no cover - reported, not fatal
            info["freshness_error"] = str(exc)
    return info


def build_cmd(cfg: Config, wk: core.WeekKickoff) -> list[str]:
    args = ["livetest", "build-week", "--season", str(wk.season), "--week", str(wk.week)]
    if cfg.rehearsal:
        args.append("--rehearsal")
    if cfg.stub_build:
        return [str(cfg.python), str(SCRIPTS_DIR / "livetest_stub_build.py"), *args]
    return [str(cfg.python), "-m", "ffml.cli", *args]


def run_build(run: Run, wk: core.WeekKickoff, deadline: datetime) -> int:
    cfg = run.cfg
    cmd = build_cmd(cfg, wk)
    public_before = core.fingerprint_tree(cfg.data_dir / "predictions")
    timeout = min(cfg.build_timeout_minutes * 60, max(60.0, (deadline - run.now()).total_seconds()))
    run.log.event("build", "started", cmd=cmd, cwd=str(cfg.code_cwd), timeout_seconds=round(timeout))
    t0 = time.perf_counter()
    try:
        proc = run.deps.run(cmd, cwd=str(cfg.code_cwd), env=cfg.child_env(), capture_output=True, text=True,
                            encoding="utf-8", errors="replace", timeout=timeout)
        code, out, err = proc.returncode, proc.stdout, proc.stderr
    except subprocess.TimeoutExpired as exc:
        code, out, err = -9, _tail(exc.stdout), f"timed out after {round(timeout)} s"
    except OSError as exc:
        code, out, err = -1, "", f"{type(exc).__name__}: {exc}"
    run.log.event("build", "finished", returncode=code, seconds=round(time.perf_counter() - t0, 1),
                  stdout_tail=_tail(out), stderr_tail=_tail(err))
    if core.fingerprint_tree(cfg.data_dir / "predictions") != public_before:
        run.alert("integrity", "the build changed the public predictions store (D-099 forbids it)",
                  key=wk.key, severity="critical")
    return code


def remote_state(run: Run, repo: core.CommitmentsRepo, create: bool = False) -> bool | None:
    """Fetch; True/False = remote branch exists or not, None = remote unreachable.

    ``create`` also creates the commitments worktree (needed before committing).
    """
    try:
        if create:
            repo.ensure()
        exists = repo.fetch()
        run.log.event("remote", "fetched", branch=repo.branch, exists=exists, remote_head=repo.remote_head())
        return exists
    except core.GitError as exc:
        run.log.event("remote", "unreachable", message=str(exc))
        return None


def run_logs_in_window(cfg: Config, wk: core.WeekKickoff, t: dict[str, datetime]) -> list[Path]:
    """This week's runner logs stamped between the window opening and the kickoff."""
    if not cfg.logs_dir.exists():
        return []
    wanted = (f"{wk.key}_run", f"{wk.key}_watchdog", "next_run", "next_watchdog")
    out = []
    for path in sorted(cfg.logs_dir.glob("*.jsonl")):
        try:
            stamp = datetime.strptime(path.name[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
        except ValueError:
            continue
        if t["window_open"] <= stamp <= wk.first_kickoff_utc and path.name[17:].startswith(wanted):
            out.append(path)
    return out


def log_events(path: Path) -> list[dict[str, Any]]:
    keep = ("ts", "step", "status", "message", "exit_code", "returncode")
    events: list[dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except OSError:
        return events
    for line in lines:
        try:
            ev = json.loads(line)
        except ValueError:
            continue
        events.append({k: ev[k] for k in keep if k in ev})
    return events


def miss_reason(cfg: Config, wk: core.WeekKickoff, t: dict[str, datetime],
                local: core.Commitment | None) -> tuple[dict[str, str], dict[str, Any]]:
    """Why the week was missed (D-106): a technical failure with its error, or an offline machine."""
    logs = run_logs_in_window(cfg, wk, t)
    window = {"from": core.iso(t["window_open"]), "to": core.iso(wk.first_kickoff_utc)}
    failures = [e for p in logs for e in log_events(p)
                if e.get("status") in ("failed", "step failed", "unexpected") or e.get("step") == "error"]
    last = failures[-1] if failures else None
    record = {"window": window, "files": [p.name for p in logs][-10:],
              "excerpt": log_events(logs[-1])[-12:] if logs else []}
    if local is not None and local.created_at is not None:
        detail = f"a commitment was built at {core.iso(local.created_at)} but was not pushed before the kickoff"
        if last:
            detail += f"; last failure: {last.get('step')}: {last.get('message')}"
        return {"category": "technical_failure", "detail": detail}, record
    if not logs:
        return {
            "category": "machine_offline",
            "detail": f"no runner log between {window['from']} and the kickoff {window['to']}: the machine was "
            "off, asleep or logged off, or the task did not start",
        }, record
    if last and last.get("step") == "prebuild":
        return {"category": "technical_failure", "detail": "pre-build check blocked",
                "check": {k: last.get(k) for k in ("ts", "message", "exit_code")}}, record
    if last:
        detail = f"{last.get('step')}: {last.get('message')}"
    else:
        detail = "the runner ran in the window but built no commitment; see the log excerpt"
    return {"category": "technical_failure", "detail": detail}, record


def require_allowed(run: Run) -> int:
    """The allowed miss count; a real run refuses without a readable pre-registration."""
    cfg = run.cfg
    if cfg.misses_allowed is not None:
        return cfg.misses_allowed
    raise run.abort(EXIT_REFUSED, "preregistration",
                    f"missed_weeks.allowed is not available: {cfg.misses_allowed_error or 'no pre-registration'} "
                    "(pass --preregistration, or init with it)")


def missed_marker(run: Run, wk: core.WeekKickoff, t: dict[str, datetime], local: core.Commitment | None,
                  miss_number: int, earlier: list[str], allowed: int) -> dict[str, Any]:
    reason, logs = miss_reason(run.cfg, wk, t, local)
    invalid = miss_number > allowed
    local_info: dict[str, Any] = {"exists": local is not None}
    if local is not None:
        local_info.update(
            sha256=local.sha256, created_at=core.iso(local.created_at), digest=local.digest,
            built_before_kickoff=bool(local.created_at and local.created_at < wk.first_kickoff_utc),
        )
    return {
        "format": "ffml.livetest.missed/2",
        "season": wk.season,
        "week": wk.week,
        "first_kickoff_utc": core.iso(wk.first_kickoff_utc),
        "recorded_at": core.iso(run.now()),
        "status": "MISSED: no commitment was pushed to this branch before the first kickoff",
        "reason": reason,
        "run_logs": logs,
        "local_commitment": local_info,
        "late_commitment": None,
        "miss_number": miss_number,
        "allowed": allowed,
        "earlier_misses": earlier,
        "test_status": (
            f"INVALID: miss {miss_number} of {allowed} allowed; the test is invalid (D-106, D-111)" if invalid
            else f"VALID: miss {miss_number} of {allowed} allowed; this week is excluded from scoring and the "
                 "window is not extended (D-106, D-111)"
        ),
    }


def window_weeks(cfg: Config, plan: list[core.WeekKickoff]) -> list[tuple[str, core.WeekKickoff | None]]:
    """Every week of the test window in order, with its schedule entry (``None`` while not yet scheduled).

    The window spans both seasons of the pre-registration (2026 w10-18 and
    2027 w1-9), so miss numbers, ``STATUS.md`` and the ``status`` footer count
    across the season boundary and the 2027 re-init (QA-F30).
    """
    by_key = {w.key: w for w in plan}
    return [(key, by_key.get(key)) for key in cfg.window_keys()]


def missed_keys(repo: core.CommitmentsRepo, keys: list[str], exclude: str | None = None) -> list[str]:
    return [k for k in keys if k != exclude and repo.remote_blob(f"{k}/{core.MISSED_NAME}") is not None]


def branch_status_markdown(cfg: Config, repo: core.CommitmentsRepo,
                           weeks: list[tuple[str, core.WeekKickoff | None]], now: datetime, allowed: int,
                           pending: dict[str, dict[str, Any]] | None = None,
                           pushed_now: str | None = None, sealed_now: str | None = None) -> str:
    """``STATUS.md`` for the branch: one row per window week, misses, sealing and the test state (D-106, D-111)."""
    rows, misses = [], []
    for key, w in weeks:
        committed = pushed_now == key or repo.remote_blob(f"{key}/{core.COMMITMENT_NAME}") is not None
        sealed = sealed_now == key or repo.remote_blob(f"{key}/{core.SEALED_NAME}") is not None
        marker = (pending or {}).get(key)
        if marker is None:
            blob = repo.remote_blob(f"{key}/{core.MISSED_NAME}")
            if blob:
                try:
                    marker = json.loads(blob)
                except ValueError:
                    marker = {"reason": {"category": "unreadable"}}
        late = bool((marker or {}).get("late_commitment")) or \
            repo.remote_blob(f"{key}/{core.LATE_DIR}/{core.COMMITMENT_NAME}") is not None
        if marker:
            misses.append(key)
            state = "MISSED"
        elif committed:
            state = "committed"
        elif w is None:
            state = "open (not in the schedule yet)"
        else:
            state = "open" if w.first_kickoff_utc > now else "kicked off, not yet recorded"
        reason = ((marker or {}).get("reason") or {}).get("category", "") if marker else ""
        kickoff = core.iso(w.first_kickoff_utc) if w is not None else "not scheduled yet"
        rows.append(f"| {key} | {kickoff} | {state} | {reason} | {'yes' if late else ''} | "
                    f"{'yes' if sealed else ''} |")
    invalid = len(misses) > allowed
    span = f"{weeks[0][0]} to {weeks[-1][0]} ({len(weeks)} weeks)" if weeks else "no weeks"
    return "\n".join([
        "# Live-test status",
        "",
        f"Generated by scripts/livetest_week.py at {core.iso(now)}. Window {span}.",
        "",
        f"**Misses: {len(misses)}** ({', '.join(misses) if misses else 'none'}); allowed: {allowed}. "
        f"**Test state: {f'INVALID (D-106, D-111: more than {allowed} misses)' if invalid else 'VALID'}.**",
        "",
        f"Up to {allowed} missed weeks are excluded from scoring without extending the window; one more makes",
        "the test invalid (D-106, D-111; the allowed count is the pre-registration's missed_weeks.allowed).",
        "A late commitment was built before kickoff and pushed after it; it is not part of the test.",
        "Sealed means the SHA-256 of the week's sealed metric parts is published here; the parts stay",
        "sealed until the final evaluation.",
        "",
        "| week | first kickoff (UTC) | state | missed: reason | late commitment | sealed |",
        "| --- | --- | --- | --- | --- | --- |",
        *rows,
        "",
    ])


def record_misses(run: Run, plan: list[core.WeekKickoff], repo: core.CommitmentsRepo | None = None) -> list[str]:
    """Push a MISSED marker (with reason, log excerpt and any late commitment) for every test week
    that kicked off without a pushed commitment (D-106)."""
    cfg = run.cfg
    if cfg.rehearsal or cfg.dry_run_branch or cfg.test_season is None or not cfg.weeks or cfg.no_push or cfg.dry_run:
        return []
    now = run.now()
    weeks = window_weeks(cfg, plan)
    keys = [k for k, _ in weeks]
    due = [w for _, w in weeks if w is not None and w.first_kickoff_utc <= now]
    if not due:
        return []
    allowed = require_allowed(run)
    repo = repo or run.repo()
    if remote_state(run, repo, create=True) is None:
        run.log.event("misses", "skipped", message="remote unreachable; cannot tell what was pushed")
        return []
    marked = []
    for wk in due:
        if repo.remote_blob(f"{wk.key}/{core.COMMITMENT_NAME}") is not None:
            continue
        if repo.remote_blob(f"{wk.key}/{core.MISSED_NAME}") is not None:
            continue
        earlier = missed_keys(repo, keys, exclude=wk.key)  # both seasons of the window (QA-F30)
        miss_number = len(earlier) + 1
        t = core.triggers(wk, cfg.timing)
        local = None
        path = cfg.commitment_dir(wk.season, wk.week) / core.COMMITMENT_NAME
        if path.exists():
            try:
                local = core.load_commitment(path)
            except (ValueError, OSError) as exc:
                run.log.event("misses", "local commitment unreadable", week=wk.key, message=str(exc))
        marker = missed_marker(run, wk, t, local, miss_number, earlier, allowed)
        files: dict[str, bytes] = {}
        if local is not None and local.created_at is not None and local.created_at < wk.first_kickoff_utc:
            v = core.verify_commitment(local, roots=roots(cfg, wk), season=wk.season, week=wk.week,
                                       kickoff=wk.first_kickoff_utc)
            late_prefix = f"{wk.key}/{core.LATE_DIR}"
            files[f"{late_prefix}/{core.COMMITMENT_NAME}"] = local.raw
            summary = local.summary_entry()
            found = core.resolve_entry(summary, roots(cfg, wk)) if summary is not None else None
            if found is not None and found.stat().st_size < core.SUMMARY_MAX_BYTES:
                files[f"{late_prefix}/{core.SUMMARY_NAME}"] = found.read_bytes()
            marker["late_commitment"] = {
                "path": f"{late_prefix}/{core.COMMITMENT_NAME}",
                "sha256": local.sha256,
                "created_at": core.iso(local.created_at),
                "verified": v.ok,
                "problems": v.problems[:5],
                "note": "built before the kickoff, pushed after it; not part of the test "
                        "(D-106: projections that exist are never withheld)",
            }
        files[f"{wk.key}/{core.MISSED_NAME}"] = (json.dumps(marker, indent=2) + "\n").encode("utf-8")
        title = f"livetest {wk.key}: MISSED ({marker['reason']['category']}), miss {miss_number} of {allowed} allowed"
        if miss_number > allowed:
            title += "; TEST INVALID (D-106, D-111)"
        message = (
            f"{title}\n\nfirst kickoff: {core.iso(wk.first_kickoff_utc)}\nreason: {marker['reason']['detail']}\n"
            f"late commitment: {(marker.get('late_commitment') or {}).get('sha256')}\n"
            + ("\n" + "\n".join(cfg.trailers) + "\n" if cfg.trailers else "")
        )
        try:
            repo.sync()
            files[core.STATUS_NAME] = branch_status_markdown(
                cfg, repo, weeks, now, allowed, pending={wk.key: marker}).encode("utf-8")
            sha = repo.commit(files, message, readme=core.branch_readme(allowed))
            push = repo.push(clock=run.now, sleep=run.deps.sleep)
        except core.GitError as exc:
            run.log.event("misses", "failed", week=wk.key, message=str(exc))
            continue
        core.write_json(cfg.runner_dir(wk.season, wk.week) / core.MISSED_NAME, {**marker, "commit": sha, "push": push})
        run.log.event("misses", "marked", week=wk.key, commit=sha, pushed=push["ok"], reason=marker["reason"],
                      miss_number=miss_number, test_status=marker["test_status"])
        run.alert("missed_commitment",
                  f"{wk.key}: MISSED ({marker['reason']['category']}: {marker['reason']['detail'][:160]}); "
                  f"miss {miss_number}; {marker['test_status']}",
                  key=f"{wk.key}:marker", severity="critical", test_status=marker["test_status"])
        marked.append(wk.key)
    return marked


def external_record(run: Run, repo: core.CommitmentsRepo, wk: core.WeekKickoff, sha: str,
                    push: dict[str, Any], c: core.Commitment, files: dict[str, bytes]) -> dict[str, Any]:
    cfg = run.cfg
    record: dict[str, Any] = {
        "season": wk.season,
        "week": wk.week,
        "rehearsal": cfg.rehearsal,
        "branch": cfg.branch,
        "remote": cfg.remote,
        "remote_url": repo.remote_url(),
        "commit_sha": sha,
        "commitment_sha256": c.sha256,
        "commitment_digest": c.digest,
        "files_committed": {k: core.sha256_bytes(v) for k, v in files.items()},
        "first_kickoff_utc": core.iso(wk.first_kickoff_utc),
        "push": push,
        "ok": push.get("ok"),
    }
    try:
        record["ls_remote_sha"] = repo.ls_remote()
    except core.GitError as exc:
        record["ls_remote_error"] = str(exc)
    local_finish = core.parse_time(push["attempts"][-1].get("finished")) if push.get("attempts") else None
    gh_remote = core.parse_github_remote(record["remote_url"])
    if cfg.github and gh_remote:
        view = run.deps.github_view(gh_remote[0], gh_remote[1], cfg.branch, sha, sleep=run.deps.sleep)
        record["github"] = view
        activity = (view or {}).get("push_activity") or {}
        stamp = core.parse_time(activity.get("timestamp"))
        if stamp is not None:
            record["external_timestamp"] = core.iso(stamp)
            record["external_timestamp_source"] = "GitHub repository activity API (server time of the push)"
            record["on_time"] = stamp < wk.first_kickoff_utc
            if local_finish is not None:
                skew = (stamp - local_finish).total_seconds()
                record["clock_skew_seconds"] = round(skew, 1)
                if abs(skew) > 120:
                    run.alert("integrity", f"{wk.key}: local clock differs from GitHub by {skew:.0f} s",
                              key=f"{wk.key}:skew", severity="warning")
    else:
        record["github"] = {"skipped": "remote is not on github.com" if not gh_remote else "disabled"}
    if "on_time" not in record and local_finish is not None:
        record["on_time"] = local_finish < wk.first_kickoff_utc
        record["external_timestamp_source"] = "none: local clock only"
    return record


def post_push_verify(run: Run, repo: core.CommitmentsRepo, wk: core.WeekKickoff, c: core.Commitment,
                     files: dict[str, bytes], sha: str | None) -> dict[str, Any]:
    cfg = run.cfg
    fresh = core.load_commitment(c.path)
    v = core.verify_commitment(
        fresh, roots=roots(cfg, wk), season=wk.season, week=wk.week, kickoff=timing_kickoff(cfg, wk),
        expected_commit=run.pin.commit if run.pin else None, rehearsal=cfg.rehearsal,
    )
    second = {"skipped": "stub commitment"} if c.stub else ffml_verify(run, c.path)
    if second.get("returncode") == 1:
        v.fail("ffml livetest verify: " + str(second.get("output", ""))[-300:])
    if fresh.raw != c.raw:
        v.fail("commitment.json changed on disk after it was committed")
    try:
        repo.fetch()
        for rel, data in files.items():
            blob = repo.remote_blob(rel)
            if blob is None:
                v.fail(f"{rel} is not on {cfg.remote}/{cfg.branch}")
            elif core.sha256_bytes(blob) != core.sha256_bytes(data):
                v.fail(f"{rel}: pushed blob sha256 {core.sha256_bytes(blob)} differs from local {core.sha256_bytes(data)}")
        summary = c.summary_entry()
        prefix = branch_prefix(cfg, wk, c)
        pushed_summary = files.get(f"{prefix}/{core.SUMMARY_NAME}")
        if summary is not None and pushed_summary is not None and core.sha256_bytes(pushed_summary) != summary.sha256:
            v.fail("pushed player_summary.parquet does not match the commitment's sha256")
        remote_head = repo.remote_head()
        if sha and remote_head and remote_head != sha and \
                repo.git("merge-base", "--is-ancestor", sha, remote_head, check=False).returncode != 0:
            v.fail(f"remote head {remote_head} does not contain commit {sha}")
    except core.GitError as exc:
        v.fail(f"could not read the remote branch: {exc}")
    result = {"checked_at": core.iso(run.now()), "commit_sha": sha, **v.to_dict(), "ffml_livetest_verify": second}
    core.write_json(cfg.runner_dir(wk.season, wk.week) / "verify.json", result)
    return result


# --------------------------------------------------------------------------- #
# run
# --------------------------------------------------------------------------- #


def attempt(run: Run) -> int:
    cfg = run.cfg
    timing = cfg.timing
    verify_pin(run)
    plan = load_plan(run, season_hint=cfg.season or cfg.test_season)
    record_misses(run, plan)
    wk = select_week(run, plan)
    if wk is None:
        run.log.event("select", "nothing to do", message="no upcoming regular-season kickoff in the schedule")
        return EXIT_OK
    t = core.triggers(wk, timing)
    run.log.event("select", "target", week=wk.to_dict(), triggers={k: core.iso(v) for k, v in t.items()})
    if not cfg.rehearsal:
        if cfg.test_season is None or not cfg.weeks:
            raise run.abort(EXIT_REFUSED, "select", "the test window is not initialised; run `init` first "
                            "(or use --rehearsal)")
        if wk.season != cfg.test_season or wk.week not in cfg.weeks:
            run.log.event("select", "nothing to do", message=f"{wk.key} is outside the test window "
                          f"{cfg.test_season} weeks {cfg.weeks}")
            return EXIT_OK
    now = run.now()
    key = wk.key
    state = load_state(cfg, wk)
    c = local_commitment(run, wk)
    repo = run.repo()

    # ---- already pushed? -------------------------------------------------- #
    remote = None if cfg.dry_run else remote_state(run, repo, create=not cfg.no_push)
    prefix = branch_prefix(cfg, wk, c)
    if remote and prefix:
        blob = repo.remote_blob(f"{prefix}/{core.COMMITMENT_NAME}")
        if blob is not None:
            if c is None and not cfg.rehearsal:
                run.log.event("idempotent", "already pushed", message="pushed from elsewhere; no local copy",
                              remote_sha256=core.sha256_bytes(blob))
                return EXIT_OK
            if c is not None and blob == c.raw:
                v = core.verify_commitment(c, roots=roots(cfg, wk), season=wk.season, week=wk.week,
                                           kickoff=timing_kickoff(cfg, wk), rehearsal=cfg.rehearsal)
                run.log.event("idempotent", "already pushed", commitment_sha256=c.sha256,
                              remote_head=repo.remote_head(), local_files_ok=v.ok, problems=v.problems)
                if not v.ok:
                    run.alert("integrity", f"{key}: pushed commitment no longer matches local files: {v.problems[:3]}",
                              key=f"{key}:rehash", severity="critical")
                    return EXIT_INTEGRITY
                return EXIT_OK
            if cfg.dry_run_branch:
                run.log.event("idempotent", "dry-run branch holds an earlier commitment; it will be replaced",
                              remote_sha256=core.sha256_bytes(blob), local_sha256=c.sha256 if c else None)
            else:
                raise run.abort(EXIT_INTEGRITY, "idempotent", f"{key}: the branch holds a different commitment "
                                "than the local one", alert_kind="integrity", key=key, severity="critical",
                                remote_sha256=core.sha256_bytes(blob), local_sha256=c.sha256 if c else None)

    # ---- time gates (not for rehearsals, which make no timing claim) --------- #
    if cfg.rehearsal:
        run.log.event("deadline", "not applied", message="rehearsal: build-week allows rehearsals after kickoff")
    elif now >= wk.first_kickoff_utc:
        raise run.abort(EXIT_MISSED, "deadline", f"{key} kicked off at {core.iso(wk.first_kickoff_utc)}; "
                        "nothing may be committed for it now", alert_kind="missed_commitment", key=key,
                        severity="critical")
    elif c is None:
        if now < t["window_open"]:
            run.log.event("deadline", "too early", message=f"window opens {core.iso(t['window_open'])}")
            return EXIT_OK
        if now >= t["cutoff"]:
            raise run.abort(EXIT_MISSED, "deadline", f"{key}: no commitment built and the cutoff "
                            f"{core.iso(t['cutoff'])} has passed", alert_kind="missed_commitment", key=key,
                            severity="critical")
    elif now >= t["push_deadline"]:
        raise run.abort(EXIT_MISSED, "deadline", f"{key}: commitment built but the push deadline "
                        f"{core.iso(t['push_deadline'])} has passed", alert_kind="missed_commitment", key=key,
                        severity="critical")

    if cfg.dry_run:
        run.log.event("dry-run", "plan", local_commitment=c is not None,
                      would_refresh=c is None and not cfg.skip_refresh and now < t["refresh_deadline"],
                      would_build=build_cmd(cfg, wk) if c is None else None,
                      would_prebuild_check=scorer_cmd(cfg, "--prebuild-check", "--from", "<last build commit>",
                                                      "--to", "<pinned HEAD>") if c is None else None,
                      would_push_to=f"{cfg.remote} refs/heads/{cfg.branch}", worktree=str(cfg.commitments_worktree))
        return EXIT_OK

    # ---- refresh and build ------------------------------------------------- #
    refresh: dict[str, Any] = {"status": "not needed (commitment already built)"}
    if c is None:
        if cfg.skip_refresh:
            refresh = {"status": "skipped (--skip-refresh)"}
            run.log.event("refresh", "skipped", message="--skip-refresh")
        elif cfg.force_fail == "refresh":
            state["refresh_attempts"] += 1
            save_state(cfg, wk, state)
            raise run.abort(EXIT_RETRY, "refresh", f"{key}: forced refresh failure (--force-fail refresh); "
                            "the next run retries", alert_kind="refresh_failure", key=key)
        elif state.get("refresh_ok_at"):
            refresh = {"status": f"ok earlier at {state['refresh_ok_at']}"}
            run.log.event("refresh", "skipped", message=refresh["status"])
        elif now >= t["refresh_deadline"] or state["refresh_attempts"] >= cfg.max_refresh_attempts:
            why = "refresh deadline passed" if now >= t["refresh_deadline"] else "refresh attempts used up"
            refresh = {"status": f"not refreshed ({why})"}
            run.alert("refresh_failure", f"{key}: building on the newest existing snapshots ({why})",
                      key=f"{key}:stale-build", severity="warning")
        else:
            refresh = run_refresh(run, wk, state)
            if refresh["status"] == "ok":
                state["refresh_ok_at"] = core.iso(run.now())
                save_state(cfg, wk, state)
            else:
                kind = "data_quality" if refresh["status"] in ("validate_failed", "stale") else "refresh_failure"
                retry_left = state["refresh_attempts"] < cfg.max_refresh_attempts and \
                    run.now() + timedelta(minutes=timing.retry_minutes) < t["refresh_deadline"]
                run.alert(kind, f"{key}: refresh {refresh['status']}"
                          + ("; will retry" if retry_left else "; building on the newest snapshots"),
                          key=key, details_steps=refresh.get("steps"), not_final=refresh.get("not_final"))
                if retry_left:
                    return EXIT_RETRY
            plan2 = load_plan(run, season_hint=wk.season, live=False)
            wk2 = core.find_week(plan2, wk.season, wk.week)
            if wk2 and (wk2.first_kickoff_utc, wk2.tentative) != (wk.first_kickoff_utc, wk.tentative):
                run.log.event("plan", "kickoff changed", before=core.iso(wk.first_kickoff_utc),
                              after=core.iso(wk2.first_kickoff_utc), tentative_after=wk2.tentative)
                merged, _ = merge_plans([wk], [wk2])
                wk = merged[0]
                t = core.triggers(wk, timing)
        if not cfg.rehearsal and run.now() >= t["cutoff"]:
            raise run.abort(EXIT_MISSED, "deadline", f"{key}: cutoff {core.iso(t['cutoff'])} passed before the build",
                            alert_kind="missed_commitment", key=key, severity="critical")
        prebuild_check(run, wk, repo, remote)
        if cfg.force_fail == "build":
            raise run.abort(EXIT_RETRY, "build", f"{key}: forced build failure (--force-fail build); the next "
                            "run retries", alert_kind="build_failure", key=key)
        state["build_attempts"] = state.get("build_attempts", 0) + 1
        state["build_last_started"] = core.iso(run.now())
        save_state(cfg, wk, state)
        code = run_build(run, wk, t["push_deadline"])
        if code == BUILD_BLOCKED:
            raise run.abort(EXIT_BLOCKED, "build", f"{key}: build-week blocked (exit 2)", alert_kind="build_failure",
                            key=key)
        if code == BUILD_ALREADY_COMMITTED and local_commitment(run, wk) is not None:
            run.log.event("build", "already committed", message="build-week exit 4; pushing the existing commitment")
            code = BUILD_OK
        if code == BUILD_REFUSED_KICKOFF:
            raise run.abort(EXIT_MISSED, "build", f"{key}: build-week refused: at or after kickoff (exit 3)",
                            alert_kind="missed_commitment", key=key, severity="critical")
        if code != BUILD_OK:
            raise run.abort(EXIT_RETRY, "build", f"{key}: build-week failed (exit {code})", alert_kind="build_failure",
                            key=key)
        c = local_commitment(run, wk)
        if c is None:
            raise run.abort(EXIT_RETRY, "build", f"{key}: build-week exited 0 but wrote no commitment.json",
                            alert_kind="build_failure", key=key)
        run.log.event("build", "commitment written", path=str(c.path), sha256=c.sha256, digest=c.digest)

    # ---- verify before pushing --------------------------------------------- #
    v = core.verify_commitment(
        c, roots=roots(cfg, wk), season=wk.season, week=wk.week, kickoff=timing_kickoff(cfg, wk),
        expected_commit=run.pin.commit if run.pin else None, rehearsal=cfg.rehearsal,
    )
    run.log.event("verify", "pre-push ok" if v.ok else "pre-push failed", **v.to_dict())
    if not v.ok:
        raise run.abort(EXIT_INTEGRITY, "verify", f"{key}: commitment failed verification: {v.problems[:3]}",
                        alert_kind="integrity", key=key, severity="critical")
    if c.stub and not (cfg.rehearsal or cfg.allow_stub_push):
        raise run.abort(EXIT_REFUSED, "verify", f"{key}: refusing to push a stub commitment outside a rehearsal")
    label = str(c.data.get("label") or c.data.get("approval_status") or "")
    if "not approved" not in label.lower():
        run.log.event("verify", "warning", message=f"commitment label is {label!r}, expected 'not approved' (D-099)")

    # ---- commit and push --------------------------------------------------- #
    prefix = branch_prefix(cfg, wk, c)
    files: dict[str, bytes] = {f"{prefix}/{core.COMMITMENT_NAME}": c.raw}
    summary = c.summary_entry()
    if summary is not None:
        found = core.resolve_entry(summary, roots(cfg, wk))
        if found is None:
            run.log.event("commit", "summary not attached", message="player_summary file not found")
        elif found.stat().st_size >= core.SUMMARY_MAX_BYTES:
            run.log.event("commit", "summary not attached", message=f"{found.stat().st_size} bytes (limit 5 MB)")
        else:
            files[f"{prefix}/{core.SUMMARY_NAME}"] = found.read_bytes()
    if cfg.no_push:
        run.log.event("commit", "skipped", message="--no-push", files=sorted(files))
        return EXIT_OK
    if remote is None:
        raise run.abort(EXIT_RETRY, "push", f"{key}: remote unreachable; will retry", alert_kind="push_failure", key=key)
    try:
        sync = repo.sync()
        readme = None
        if not cfg.rehearsal:
            allowed = require_allowed(run)
            files[core.STATUS_NAME] = branch_status_markdown(
                cfg, repo, window_weeks(cfg, plan), run.now(), allowed, pushed_now=wk.key).encode("utf-8")
            readme = core.branch_readme(allowed)
        sha = repo.commit(files, commit_message(cfg, wk, c, run, refresh), allow_overwrite=bool(cfg.dry_run_branch),
                          readme=readme)
        if sha is None:
            sha = repo.head()
            for rel, data in files.items():
                if repo.head_blob(rel) != data:
                    raise core.GitError(f"{rel} is not committed on the local branch")
        run.log.event("commit", "done", sha=sha, sync=sync, files=sorted(files))
    except core.GitError as exc:
        raise run.abort(EXIT_INTEGRITY, "commit", f"{key}: {exc}", alert_kind="push_failure", key=key,
                        severity="critical") from None
    if cfg.force_fail == "push":
        push = {"ok": False, "refspec": f"refs/heads/{cfg.branch}", "attempts": [
            {"attempt": 1, "forced": True, "finished": core.iso(run.now()),
             "stderr": "forced failure (--force-fail push); nothing was pushed"}]}
    else:
        push = repo.push(deadline=t["push_deadline"], clock=run.now, sleep=run.deps.sleep)
    run.log.event("push", "ok" if push["ok"] else "failed", **push)
    record = external_record(run, repo, wk, sha, push, c, files)
    record["recorded_at"] = core.iso(run.now())
    core.write_json(cfg.runner_dir(wk.season, wk.week) / "push_record.json", record)
    run.log.event("external", "recorded", **{k: record.get(k) for k in (
        "commit_sha", "ls_remote_sha", "external_timestamp", "external_timestamp_source", "on_time",
        "clock_skew_seconds")}, github=record.get("github"))
    if not push["ok"]:
        raise run.abort(EXIT_RETRY, "push", f"{key}: push failed: {push['attempts'][-1].get('stderr', '')[:200]}",
                        alert_kind="push_failure", key=key)
    if record.get("ls_remote_sha") not in (None, sha):
        run.log.event("push", "warning", message=f"remote head {record.get('ls_remote_sha')} is not {sha}")
    state.update(pushed_sha=sha, pushed_at=core.iso(run.now()))
    save_state(cfg, wk, state)

    # ---- verify after pushing ---------------------------------------------- #
    result = post_push_verify(run, repo, wk, c, files, sha)
    run.log.event("verify", "post-push ok" if result["ok"] else "post-push failed",
                  problems=result["problems"], warnings=result["warnings"], digest=result["digest"])
    if not result["ok"]:
        run.alert("integrity", f"{key}: post-push verification failed: {result['problems'][:3]}", key=f"{key}:post",
                  severity="critical")
        return EXIT_INTEGRITY
    if record.get("on_time") is False and not cfg.rehearsal:
        run.alert("missed_commitment", f"{key}: push landed at {record.get('external_timestamp')}, after kickoff",
                  key=f"{key}:late", severity="critical")
        return EXIT_MISSED
    return EXIT_OK


def _label_key(cfg: Config) -> str:
    return core.week_key(cfg.season, cfg.week) if cfg.season is not None and cfg.week is not None else "next"


def _mode_prefix(cfg: Config) -> str:
    return "dryrun_" if cfg.dry_run_branch else "rehearsal_" if cfg.rehearsal else ""


def cmd_run(cfg: Config, deps: Deps) -> int:
    label = f"{_mode_prefix(cfg)}{_label_key(cfg)}_run"
    while True:
        run = Run(cfg, deps, label)
        try:
            with core.RunLock(cfg.livetest_dir / ".runner.lock") as lock:
                if lock.taken_over:
                    run.log.event("lock", "taken over", stale=lock.taken_over)
                code = attempt(run)
        except core.LockBusy as exc:
            run.log.event("lock", "busy", message=str(exc))
            code = EXIT_OK
        except Abort as exc:
            code = exc.code
        except Exception as exc:  # unexpected: log, alert, retry later
            run.log.event("error", "unexpected", message=f"{type(exc).__name__}: {exc}")
            run.alert("build_failure", f"live-test runner crashed: {type(exc).__name__}: {exc}", key="crash")
            code = EXIT_RETRY
        run.log.event("summary", "exit", exit_code=code)
        if code not in (EXIT_RETRY, EXIT_BLOCKED) or cfg.retry_every_minutes <= 0 or cfg.now_override:
            return code
        deps.sleep(cfg.retry_every_minutes * 60)


# --------------------------------------------------------------------------- #
# watchdog, check, plan, status, verify, init
# --------------------------------------------------------------------------- #


def cmd_watchdog(cfg: Config, deps: Deps) -> int:
    run = Run(cfg, deps, f"{_mode_prefix(cfg)}{_label_key(cfg)}_watchdog")
    try:
        plan = load_plan(run, season_hint=cfg.season or cfg.test_season, live=False)
        wk = select_week(run, plan)
    except Abort as exc:
        return exc.code
    if wk is None:
        run.log.event("watchdog", "nothing to do")
        return EXIT_OK
    repo = run.repo()
    remote = remote_state(run, repo)
    c = local_commitment(run, wk) if (cfg.commitment_dir(wk.season, wk.week) / core.COMMITMENT_NAME).exists() else None
    prefix = branch_prefix(cfg, wk, c)
    if remote is None:
        run.alert("missed_commitment", f"{wk.key}: watchdog cannot reach {cfg.remote}; check the push by hand "
                  f"before {core.iso(wk.first_kickoff_utc)}", key=f"{wk.key}:watchdog", severity="critical")
        run.log.event("summary", "exit", exit_code=EXIT_RETRY)
        return EXIT_RETRY
    pushed = bool(prefix) and remote and repo.remote_blob(f"{prefix}/{core.COMMITMENT_NAME}") is not None
    if pushed:
        run.log.event("watchdog", "ok", message=f"{wk.key}: commitment is on {cfg.remote}/{cfg.branch}")
        run.log.event("summary", "exit", exit_code=EXIT_OK)
        return EXIT_OK
    state = load_state(cfg, wk)
    detail = "a local commitment exists but is not pushed" if c else "no local commitment"
    run.alert("missed_commitment", f"{wk.key}: no pushed commitment at {core.iso(run.now())}, first kickoff "
              f"{core.iso(wk.first_kickoff_utc)} ({detail}); run the runner by hand (docs/RUNBOOK.md)",
              key=f"{wk.key}:watchdog", severity="critical", state=state)
    run.log.event("summary", "exit", exit_code=EXIT_RETRY)
    return EXIT_RETRY


def plan_rows(cfg: Config, plan: list[core.WeekKickoff], now: datetime) -> list[dict[str, Any]]:
    season = cfg.test_season
    if season is None:
        upcoming = core.next_week(plan, now)
        season = upcoming.season if upcoming else max((w.season for w in plan), default=None)
    rows = []
    for w in plan:
        if w.season != season or (cfg.weeks and w.week not in cfg.weeks):
            continue
        t = core.triggers(w, cfg.timing)
        rows.append({
            **w.to_dict(),
            **{k: core.iso(v) for k, v in t.items()},
            "status": "past" if w.first_kickoff_utc <= now else "upcoming",
        })
    return rows


def export_schedule(cfg: Config, rows: list[dict[str, Any]], now: datetime, path: Path) -> dict[str, Any]:
    """The tracked schedule file the GitHub watchdog reads (scripts/livetest_schedule_2026.json)."""
    season = rows[0]["season"] if rows else cfg.test_season
    payload = {
        "format": "ffml.livetest.schedule/1",
        "generated_at": core.iso(now),
        "generated_by": "scripts/livetest_week.py plan --export",
        "source": {"dim_game_snapshot": core.dim_game_snapshot_id(cfg.data_dir), "live_schedule_checked": cfg.live_schedule},
        "season": season,
        "test_weeks": [r["week"] for r in rows],
        "note": "Official first kickoff per week (UTC). Re-export and commit when the daily check reports "
                "schedule drift (week 18 times are tentative until the NFL sets them).",
        "weeks": [
            {k: r[k] for k in ("key", "season", "week", "first_kickoff_utc", "effective_kickoff_utc", "tentative", "games")}
            for r in rows
        ],
    }
    existing = core.read_json(path) or {}
    same = {k: existing.get(k) for k in ("season", "test_weeks", "weeks")} == \
        {k: payload[k] for k in ("season", "test_weeks", "weeks")}
    if same:
        payload = existing
        payload["unchanged"] = True
    else:
        core.write_json(path, payload)
    return payload


def cmd_plan(cfg: Config, deps: Deps, as_json: bool, save: bool, export: Path | None = None) -> int:
    run = Run(cfg, deps, "plan", echo=False)
    try:
        plan = load_plan(run, season_hint=cfg.test_season)
    except Abort as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    rows = plan_rows(cfg, plan, run.now())
    if save:
        core.write_json(cfg.livetest_dir / "schedule_plan.json", {"saved_at": core.iso(run.now()), "weeks": rows})
    if export is not None:
        exported = export_schedule(cfg, rows, run.now(), export)
        state = "unchanged" if exported.get("unchanged") else "written"
        print(f"schedule {state}: {export} ({len(rows)} weeks)", file=sys.stderr)
    if as_json:
        print(json.dumps(rows, indent=2))
    else:
        print(f"{'week':<8} {'first kickoff UTC':<22} {'tent':<5} {'run start UTC':<22} {'cutoff UTC':<22} "
              f"{'watchdog UTC':<22} status")
        for r in rows:
            print(f"{r['key']:<8} {r['first_kickoff_utc']:<22} {str(r['tentative'])[0]:<5} {r['run_start']:<22} "
                  f"{r['cutoff']:<22} {r['watchdog']:<22} {r['status']}")
    return EXIT_OK


def cmd_check(cfg: Config, deps: Deps) -> int:
    run = Run(cfg, deps, "daily_check")
    code = EXIT_OK
    try:
        findings = alerts.check_stale(run.now())
    except Exception as exc:
        findings = [{"store": "?", "problem": f"stale check failed: {type(exc).__name__}: {exc}"}]
    if findings:
        run.alert("stale_predictions", "; ".join(f"{f['store']}: {f['problem']}" for f in findings),
                  key="stale", severity="warning", findings=findings)
        code = EXIT_RETRY
    run.log.event("check", "stale predictions", findings=findings)
    saved = core.read_json(cfg.livetest_dir / "schedule_plan.json")
    try:
        plan = load_plan(run, season_hint=cfg.test_season)
    except Abort as exc:
        return exc.code
    if saved:
        current = {r["key"]: r for r in plan_rows(cfg, plan, run.now())}
        drift = []
        for old in saved.get("weeks", []):
            new = current.get(old["key"])
            if new and new["status"] == "upcoming" and (new["run_start"], new["watchdog"]) != (old["run_start"], old["watchdog"]):
                drift.append({"week": old["key"], "registered_run_start": old["run_start"], "now_run_start": new["run_start"],
                              "registered_watchdog": old["watchdog"], "now_watchdog": new["watchdog"]})
        run.log.event("check", "schedule drift", drift=drift)
        if drift:
            run.alert("schedule_drift", f"kickoff times changed for {[d['week'] for d in drift]}; re-run "
                      "scripts/livetest_schedule.ps1 -Register", key="drift", severity="warning", drift=drift)
            code = EXIT_RETRY
    try:
        with core.RunLock(cfg.livetest_dir / ".runner.lock"):
            marked = record_misses(run, plan)
    except core.LockBusy as exc:
        marked = []
        run.log.event("misses", "skipped", message=str(exc))
    except Abort as exc:  # e.g. no readable pre-registration: refuse, never guess the allowed count
        marked = []
        code = exc.code
    run.log.event("summary", "exit", exit_code=code, misses_marked=marked)
    return code


def week_status(cfg: Config, run: Run, repo: core.CommitmentsRepo | None, remote: bool | None,
                wk: core.WeekKickoff) -> dict[str, Any]:
    c_path = cfg.commitment_dir(wk.season, wk.week) / core.COMMITMENT_NAME
    c = core.load_commitment(c_path) if c_path.exists() else None
    record = core.read_json(cfg.runner_dir(wk.season, wk.week) / "push_record.json") or {}
    prefix = branch_prefix(cfg, wk, c)
    pushed = missed = late = sealed = None
    missed_reason = test_state = None
    if remote is not None and repo is not None:
        blob = repo.remote_blob(f"{prefix}/{core.COMMITMENT_NAME}") if prefix else None
        pushed = blob is not None and (c is None or blob == c.raw)
        sealed = repo.remote_blob(f"{wk.key}/{core.SEALED_NAME}") is not None
        missed_blob = repo.remote_blob(f"{wk.key}/{core.MISSED_NAME}")
        missed = missed_blob is not None
        if missed_blob:
            try:
                marker = json.loads(missed_blob)
                reason = marker.get("reason")
                missed_reason = reason.get("category") if isinstance(reason, dict) else str(reason)
                test_state = marker.get("test_status")
            except ValueError:
                missed_reason = "unreadable"
        late = repo.remote_blob(f"{wk.key}/{core.LATE_DIR}/{core.COMMITMENT_NAME}") is not None
    return {
        "week": wk.key,
        "first_kickoff_utc": core.iso(wk.first_kickoff_utc),
        "local_commitment": c.sha256[:12] if c else None,
        "created_at": core.iso(c.created_at) if c else None,
        "pushed": pushed,
        "github_push_timestamp": record.get("external_timestamp"),
        "on_time": record.get("on_time"),
        "missed_marker": missed,
        "missed_reason": missed_reason,
        "late_commitment": late,
        "sealed": sealed,
        "test_status": test_state,
        "commit_sha": record.get("commit_sha"),
    }


def cmd_status(cfg: Config, deps: Deps, as_json: bool) -> int:
    run = Run(cfg, deps, "status", echo=False)
    try:
        plan = load_plan(run, season_hint=cfg.test_season, live=False)
    except Abort as exc:
        return exc.code
    repo = run.repo()
    remote = remote_state(run, repo)
    season = cfg.season or cfg.test_season
    if season is None:
        upcoming = core.next_week(plan, run.now())
        season = upcoming.season if upcoming else max((w.season for w in plan), default=None)
    window = cfg.window_keys()
    if window and cfg.season is None:  # the whole pre-registered window, across the season boundary (QA-F30)
        keys = set(window)
        rows = [week_status(cfg, run, repo, remote, w) for w in plan if w.key in keys]
    else:
        rows = [week_status(cfg, run, repo, remote, w) for w in plan
                if w.season == season and (not cfg.weeks or w.week in cfg.weeks)]
    misses = missed_keys(repo, window) if (window and remote is not None) else \
        [r["week"] for r in rows if r["missed_marker"]]
    allowed = cfg.misses_allowed
    if remote is None:
        test_state = "unknown (remote unreachable)"
    elif allowed is None:
        test_state = f"unknown (no pre-registration: {cfg.misses_allowed_error})"
    elif len(misses) > allowed:
        test_state = f"INVALID (D-106, D-111: more than {allowed} misses)"
    else:
        test_state = "VALID"
    if as_json:
        print(json.dumps({"remote_reachable": remote is not None, "branch": cfg.branch, "weeks": rows,
                          "misses": misses, "allowed": allowed, "test_state": test_state,
                          "preregistration": str(cfg.preregistration) if cfg.preregistration else None}, indent=2))
    else:
        print(f"remote {cfg.remote}/{cfg.branch} reachable: {remote is not None}")
        print(f"{'week':<8} {'first kickoff UTC':<22} {'local':<13} {'pushed':<7} {'GitHub push time':<22} "
              f"{'on time':<8} {'missed':<7} {'reason':<18} {'late':<5} sealed")
        for r in rows:
            print(f"{r['week']:<8} {r['first_kickoff_utc']:<22} {str(r['local_commitment']):<13} {str(r['pushed']):<7} "
                  f"{str(r['github_push_timestamp']):<22} {str(r['on_time']):<8} {str(r['missed_marker']):<7} "
                  f"{str(r['missed_reason'] or ''):<18} {str(r['late_commitment'] or ''):<5} {r['sealed'] or ''}")
        print(f"misses: {len(misses)} ({', '.join(misses) if misses else 'none'}); "
              f"allowed {allowed if allowed is not None else '?'}; test state: {test_state}")
    return EXIT_OK


# --------------------------------------------------------------------------- #
# seal, seal-check, score-week
# --------------------------------------------------------------------------- #


def read_sealed(cfg: Config, wk: core.WeekKickoff) -> tuple[Path, bytes, dict[str, str]]:
    path = cfg.sealed_dir / f"{wk.key}.sha256"
    raw = path.read_bytes()
    return path, raw, core.parse_sha256_file(raw.decode("utf-8"))


def verify_sealed_parts(cfg: Config, entries: dict[str, str]) -> dict[str, Any]:
    """Re-hash the sealed part files the ``.sha256`` names, when they are present locally."""
    checked, problems = {}, []
    for name, expected in entries.items():
        candidate = Path(name)
        if not candidate.is_absolute():
            candidate = cfg.sealed_dir / name
        if not candidate.exists():
            checked[name] = "not present locally"
            continue
        actual = core.sha256_file(candidate)
        checked[name] = "ok" if actual == expected else "MISMATCH"
        if actual != expected:
            problems.append(f"{name}: local file hashes to {actual}, sealed hash is {expected}")
    return {"checked": checked, "problems": problems}


def sealed_payload(cfg: Config, wk: core.WeekKickoff, path: Path, raw: bytes, entries: dict[str, str],
                   now: datetime) -> dict[str, Any]:
    rel = path.resolve()
    try:
        rel_text = rel.relative_to(cfg.data_dir.resolve()).as_posix()
        rel_text = f"data/{rel_text}"
    except ValueError:
        rel_text = rel.as_posix()
    return {
        "format": "ffml.livetest.sealed/1",
        "season": wk.season,
        "week": wk.week,
        "sealed_file": rel_text,
        "sealed_file_sha256": core.sha256_bytes(raw),
        "entries": entries,
        "raw": raw.decode("utf-8"),
        "published_at": core.iso(now),
        "note": "SHA-256 of the week's sealed metric parts written by the Evaluator's scorer; the parts stay local "
                "and sealed until the final evaluation (pre-registration sealed_interim). Published by "
                "scripts/livetest_week.py seal; immutable once pushed.",
    }


def _same_seal(a: dict[str, Any], b: dict[str, Any]) -> bool:
    return a.get("sealed_file_sha256") == b.get("sealed_file_sha256") and a.get("entries") == b.get("entries")


def scorer_week_record(cfg: Config, wk: core.WeekKickoff) -> dict[str, Any]:
    """The snapshot ids the scorer recorded for the week (``data/livetest/scoring/weeks/<week>.json``)."""
    path = cfg.data_dir / "livetest" / "scoring" / "weeks" / f"{wk.key}.json"
    data = core.read_json(path)
    if data is None:
        return {"weeks_json": None}
    found: dict[str, Any] = {}
    wanted = ("dim_game_snapshot", "processed_snapshots", "outcome_rows_sha256", "data_cutoff_utc")
    stack: list[Any] = [data]
    while stack:
        node = stack.pop()
        if isinstance(node, dict):
            for k, v in node.items():
                if k in wanted and k not in found:
                    found[k] = v
                elif isinstance(v, dict):
                    stack.append(v)
    return {"weeks_json": str(path), "weeks_json_sha256": core.sha256_file(path), **found}


def seal_week(run: Run, wk: core.WeekKickoff, plan: list[core.WeekKickoff],
              data_cutoff: datetime | None = None, exclusion_after: datetime | None = None,
              not_final: list[str] | None = None) -> int:
    """Publish ``<week>/sealed_hashes.json`` from the local sealed file; idempotent; immutable."""
    cfg = run.cfg
    key = wk.key
    if cfg.rehearsal and not cfg.dry_run_branch:
        raise run.abort(EXIT_REFUSED, "seal", "seal is not for rehearsals")
    try:
        path, raw, entries = read_sealed(cfg, wk)
    except FileNotFoundError:
        raise run.abort(EXIT_REFUSED, "seal", f"{key}: no sealed file {cfg.sealed_dir / (key + '.sha256')}; "
                        "the Evaluator's scorer has not sealed this week") from None
    except (ValueError, UnicodeDecodeError) as exc:
        raise run.abort(EXIT_INTEGRITY, "seal", f"{key}: sealed file unreadable: {exc}", alert_kind="integrity",
                        key=f"{key}:seal", severity="critical") from None
    parts = verify_sealed_parts(cfg, entries)
    if parts["problems"]:
        raise run.abort(EXIT_INTEGRITY, "seal", f"{key}: sealed parts do not match their hashes: "
                        f"{parts['problems'][:3]}", alert_kind="integrity", key=f"{key}:seal", severity="critical")
    payload = sealed_payload(cfg, wk, path, raw, entries, run.now())
    run.log.event("seal", "local sealed file read", file=str(path), sha256=payload["sealed_file_sha256"],
                  entries=len(entries), parts=parts["checked"])
    repo = run.repo()
    remote = remote_state(run, repo, create=True)
    if remote is None:
        raise run.abort(EXIT_RETRY, "seal", f"{key}: remote unreachable; will retry", alert_kind="scoring_failure",
                        key=f"{key}:seal")
    rel = f"{key}/{core.SEALED_NAME}"
    blob = repo.remote_blob(rel) if remote else None
    if blob is not None:
        try:
            existing = json.loads(blob)
        except ValueError:
            existing = {}
        if _same_seal(existing, payload):
            run.log.event("seal", "already published", path=rel, sha256=payload["sealed_file_sha256"])
            return EXIT_OK
        raise run.abort(EXIT_INTEGRITY, "seal", f"{key}: the branch already holds different sealed hashes "
                        f"({existing.get('sealed_file_sha256')} vs local {payload['sealed_file_sha256']}); sealed "
                        "hashes are immutable", alert_kind="integrity", key=f"{key}:seal", severity="critical")
    if cfg.no_push or cfg.dry_run:
        run.log.event("seal", "skipped", message="--no-push or --dry-run", path=rel)
        return EXIT_OK
    files = {rel: (json.dumps(payload, indent=2) + "\n").encode("utf-8"),
             # the scorer's --final reads the sealed file verbatim at scoring/<week>.sha256
             f"{core.SEALED_BRANCH_DIR}/{key}.sha256": raw}
    readme = None
    if not cfg.dry_run_branch:
        allowed = require_allowed(run)
        files[core.STATUS_NAME] = branch_status_markdown(
            cfg, repo, window_weeks(cfg, plan), run.now(), allowed, sealed_now=key).encode("utf-8")
        readme = core.branch_readme(allowed)
    message = (f"livetest {key}: sealed hashes of the scored week\n\nsealed file sha256: "
               f"{payload['sealed_file_sha256']}\nentries: {len(entries)}\npublished by: scripts/livetest_week.py seal\n"
               + ("\n" + "\n".join(cfg.trailers) + "\n" if cfg.trailers else ""))
    try:
        repo.sync()
        sha = repo.commit(files, message, readme=readme) or repo.head()
        push = repo.push(clock=run.now, sleep=run.deps.sleep)
    except core.GitError as exc:
        raise run.abort(EXIT_RETRY, "seal", f"{key}: {exc}", alert_kind="scoring_failure", key=f"{key}:seal") from None
    record: dict[str, Any] = {"season": wk.season, "week": wk.week, "branch": cfg.branch, "path": rel,
                              "commit_sha": sha, "sealed_file_sha256": payload["sealed_file_sha256"],
                              "entries": entries, "push": push, "ok": push.get("ok"), "recorded_at": core.iso(run.now()),
                              "data_cutoff_utc": core.iso(data_cutoff) if data_cutoff else None,
                              "exclusion_after_utc": core.iso(exclusion_after) if exclusion_after else None,
                              "not_final_at_scoring": list(not_final or []),
                              "scorer": scorer_week_record(cfg, wk)}
    try:
        record["ls_remote_sha"] = repo.ls_remote()
    except core.GitError as exc:
        record["ls_remote_error"] = str(exc)
    gh_remote = core.parse_github_remote(repo.remote_url())
    if cfg.github and gh_remote and push.get("ok"):
        record["github"] = run.deps.github_view(gh_remote[0], gh_remote[1], cfg.branch, sha, sleep=run.deps.sleep)
        stamp = core.parse_time(((record["github"] or {}).get("push_activity") or {}).get("timestamp"))
        record["external_timestamp"] = core.iso(stamp) if stamp else None
    core.write_json(cfg.runner_dir(wk.season, wk.week) / "seal_record.json", record)
    run.log.event("seal", "pushed" if push.get("ok") else "push failed", commit=sha, path=rel,
                  external_timestamp=record.get("external_timestamp"))
    if not push.get("ok"):
        raise run.abort(EXIT_RETRY, "seal", f"{key}: push failed", alert_kind="scoring_failure", key=f"{key}:seal")
    check = repo.remote_blob(rel) if repo.fetch() else None
    if check is None or not _same_seal(json.loads(check), payload):
        run.alert("integrity", f"{key}: the pushed sealed hashes do not match the local file", key=f"{key}:seal-post",
                  severity="critical")
        return EXIT_INTEGRITY
    return EXIT_OK


def seal_check(run: Run, wk: core.WeekKickoff) -> int:
    """Compare the branch copy of the sealed hashes with the local sealed file and parts."""
    cfg = run.cfg
    key = wk.key
    result: dict[str, Any] = {"week": key, "ok": True, "problems": [], "branch": cfg.branch}
    try:
        path, raw, entries = read_sealed(cfg, wk)
        result["local"] = {"file": str(path), "sha256": core.sha256_bytes(raw), "entries": len(entries)}
        parts = verify_sealed_parts(cfg, entries)
        result["parts"] = parts["checked"]
        result["problems"] += parts["problems"]
    except FileNotFoundError:
        raw, entries = b"", {}
        result["local"] = None
        result["problems"].append("no local sealed file")
    except ValueError as exc:
        raw, entries = b"", {}
        result["local"] = None
        result["problems"].append(f"local sealed file unreadable: {exc}")
    repo = run.repo()
    remote = remote_state(run, repo)
    blob = repo.remote_blob(f"{key}/{core.SEALED_NAME}") if remote else None
    if remote is None:
        result["problems"].append("remote unreachable")
    elif blob is None:
        result["branch_copy"] = None
        result["problems"].append("not sealed on the branch")
    else:
        try:
            published = json.loads(blob)
        except ValueError:
            published = {}
            result["problems"].append("branch copy is not valid JSON")
        result["branch_copy"] = {k: published.get(k) for k in ("sealed_file_sha256", "published_at")}
        if raw and published.get("sealed_file_sha256") != core.sha256_bytes(raw):
            result["problems"].append("the branch copy was made from a different sealed file than the local one")
        if entries and published.get("entries") != entries:
            result["problems"].append("the branch entries differ from the local sealed file")
    result["ok"] = not result["problems"]
    result["checked_at"] = core.iso(run.now())
    core.write_json(cfg.runner_dir(wk.season, wk.week) / "seal_check.json", result)
    print(json.dumps(result, indent=2))
    run.log.event("seal-check", "ok" if result["ok"] else "problems", problems=result["problems"])
    if not result["ok"] and any("differ" in p or "different" in p or "MISMATCH" in p or "unreadable" in p
                                for p in result["problems"]):
        run.alert("integrity", f"{key}: sealed hashes check failed: {result['problems'][:3]}", key=f"{key}:seal-check",
                  severity="critical")
        return EXIT_INTEGRITY
    return EXIT_OK if result["ok"] else EXIT_RETRY


def cmd_seal(cfg: Config, deps: Deps, check_only: bool) -> int:
    name = "seal_check" if check_only else "seal"
    run = Run(cfg, deps, f"{_mode_prefix(cfg)}{core.week_key(cfg.season, cfg.week)}_{name}", echo=not check_only)
    try:
        plan = load_plan(run, season_hint=cfg.season, live=False)
        wk = select_week(run, plan)
        with core.RunLock(cfg.livetest_dir / ".runner.lock"):
            code = seal_check(run, wk) if check_only else seal_week(run, wk, plan)
    except Abort as exc:
        code = exc.code
    except core.LockBusy as exc:
        run.log.event("lock", "busy", message=str(exc))
        code = EXIT_RETRY
    run.log.event("summary", "exit", exit_code=code)
    return code


def week_is_final(cfg: Config, wk: core.WeekKickoff) -> tuple[bool, list[str]]:
    import polars as pl

    dim = core.load_dim_game(cfg.data_dir, [wk.season])
    pending = dim.filter((pl.col("game_type") == "REG") & (pl.col("week") == wk.week) & (pl.col("status") != "final"))
    return pending.height == 0, pending["game_id"].to_list()




def data_cutoff_for(run: Run, wk: core.WeekKickoff) -> tuple[datetime, datetime | None] | None:
    """``(cutoff, exclusion_after)`` from ``--data-cutoff`` (A3, D-121); ``None`` on "not yet" (exit 4)."""
    code, out, err = run_scorer(run, ["--data-cutoff", wk.key], timeout_minutes=10)
    result = parse_json_output(out)
    run.log.event("score", "data cutoff", week=wk.key, returncode=code, result=result, stderr_tail=_tail(err, 400))
    if code == SCORER_NOT_YET:
        return None
    if code != 0:
        raise run.abort(EXIT_RETRY, "score", f"{wk.key}: the scorer's --data-cutoff exited {code}; will retry",
                        alert_kind="scoring_failure", key=f"{wk.key}:cutoff", stderr_tail=_tail(err, 400))
    value = None
    if result:
        for k in ("data_cutoff_utc", "data_cutoff", "cutoff_utc", "cutoff"):
            if result.get(k):
                value = result[k]
                break
    if value is None:
        lines = [ln.strip() for ln in out.splitlines() if ln.strip()]
        value = lines[-1] if lines else None
    try:
        cutoff = core.parse_time(value)
    except (TypeError, ValueError):
        cutoff = None
    if cutoff is None:
        raise run.abort(EXIT_RETRY, "score", f"{wk.key}: no data cutoff in the scorer's output {out[-200:]!r}",
                        alert_kind="scoring_failure", key=f"{wk.key}:cutoff")
    exclusion_after = None
    raw_exclusion = (result or {}).get("exclusion_after_utc")
    if raw_exclusion:
        try:
            exclusion_after = core.parse_time(raw_exclusion)
        except ValueError:
            exclusion_after = None
    run.log.event("score", "cutoff read", week=wk.key, data_cutoff=core.iso(cutoff),
                  exclusion_after=core.iso(exclusion_after) if exclusion_after else None)
    return cutoff, exclusion_after


def _snapshot_time(snapshot_id: str) -> datetime | None:
    try:
        return datetime.strptime(snapshot_id[:16], "%Y%m%dT%H%M%SZ").replace(tzinfo=UTC)
    except ValueError:
        return None


def pinned_coverage(cfg: Config, at: datetime) -> dict[str, str | None]:
    """For each dataset the scorer pins, the first snapshot taken at or after ``at`` (``None``: none yet)."""
    out: dict[str, str | None] = {}
    for name in PINNED_DATASETS:
        root = cfg.data_dir / "processed" / name
        ids = sorted(p.name.split("=", 1)[1] for p in root.glob("snapshot=*") if p.is_dir()) if root.is_dir() else []
        timed = sorted((t, sid) for sid in ids if (t := _snapshot_time(sid)) is not None)
        out[name] = next((sid for t, sid in timed if t >= at), None)
    return out




def weeks_to_score(run: Run, plan: list[core.WeekKickoff], repo: core.CommitmentsRepo) -> list[core.WeekKickoff]:
    """Every test week that kicked off, has a commitment on the branch, is not missed and not sealed, in order."""
    cfg = run.cfg
    now = run.now()
    keys = set(cfg.window_keys())
    out = []
    for w in plan:
        if keys and w.key not in keys:
            continue
        if not keys and (w.season != cfg.test_season or w.week not in cfg.weeks):
            continue
        if w.first_kickoff_utc > now:
            continue
        if repo.remote_blob(f"{w.key}/{core.SEALED_NAME}") is not None:
            continue
        if repo.remote_blob(f"{w.key}/{core.MISSED_NAME}") is not None:
            continue
        if repo.remote_blob(f"{w.key}/{core.COMMITMENT_NAME}") is None:
            continue
        out.append(w)
    return out


def score_one(run: Run, wk: core.WeekKickoff, plan: list[core.WeekKickoff]) -> int:
    """One week: cutoff, refresh until every pinned dataset has a snapshot at the pin point, scorer, seal."""
    cfg = run.cfg
    key = wk.key
    got = data_cutoff_for(run, wk)
    if got is None:
        run.log.event("score", "not yet", week=key, message="the scorer's --data-cutoff exited 4; retry later")
        return EXIT_RETRY
    cutoff, exclusion_after = got
    now = run.now()
    if now < cutoff:
        run.log.event("score", "before the data cutoff", week=key, data_cutoff=core.iso(cutoff),
                      message="nothing to do before the cutoff (A3)")
        return EXIT_OK
    final, pending = week_is_final(cfg, wk)
    past_exclusion = exclusion_after is not None and now >= exclusion_after
    pin_point = exclusion_after if (not final and past_exclusion) else cutoff
    state = load_state(cfg, wk)
    coverage = pinned_coverage(cfg, pin_point)
    lacking = [name for name, sid in coverage.items() if sid is None]
    refreshes = state.setdefault("score_refreshes", {})
    if cfg.skip_refresh:
        run.log.event("score", "refresh skipped", message="--skip-refresh", pin_point=core.iso(pin_point))
    elif not lacking:
        run.log.event("score", "refresh not needed", pin_point=core.iso(pin_point), coverage=coverage,
                      message="every pinned dataset has a snapshot taken at or after the pin point")
    elif refreshes.get(core.iso(pin_point), 0) >= MAX_SCORE_REFRESHES:
        run.log.event("score", "refresh limit reached", pin_point=core.iso(pin_point), lacking=lacking,
                      message="the scorer decides with the snapshots there are")
    else:
        run.log.event("score", "refresh needed", pin_point=core.iso(pin_point), lacking=lacking)
        refreshes[core.iso(pin_point)] = refreshes.get(core.iso(pin_point), 0) + 1
        save_state(cfg, wk, state)
        refresh = run_refresh(run, wk, state)
        if refresh["status"] == "failed":
            raise run.abort(EXIT_RETRY, "score", f"{key}: refresh failed before scoring; will retry",
                            alert_kind="refresh_failure", key=f"{key}:score")
        final, pending = week_is_final(cfg, wk)
    if not final and not past_exclusion:
        run.log.event("score", "week not final yet", week=key, not_final=pending[:8],
                      exclusion_after=core.iso(exclusion_after) if exclusion_after else None,
                      message="retried; after the exclusion point the week is scored without these games (D-121)")
        return EXIT_RETRY
    if not final:
        run.log.event("score", "scoring with games excluded", week=key, not_final=pending,
                      exclusion_after=core.iso(exclusion_after),
                      message="D-121: games still not final 14 days after the cutoff are excluded by the scorer")
    sealed_path = cfg.sealed_dir / f"{key}.sha256"
    if not sealed_path.exists():
        args = ["--week", key, "--weekly-sensitivity"]
        run.log.event("score", "scorer started", cmd=scorer_cmd(cfg, *args), cwd=str(cfg.code_cwd),
                      priority="above normal" if scorer_creationflags() else "normal",
                      timeout_minutes=cfg.scorer_timeout_minutes, data_cutoff=core.iso(cutoff))
        t0 = time.perf_counter()
        code, out, err = run_scorer(run, args, timeout_minutes=cfg.scorer_timeout_minutes, priority=True)
        run.log.event("score", "scorer finished", returncode=code, seconds=round(time.perf_counter() - t0, 1),
                      stdout_tail=_tail(out, 800), stderr_tail=_tail(err))
        if code == SCORER_NOT_YET:
            run.log.event("score", "not yet", week=key, message="the scorer exited 4; the next repetition retries")
            return EXIT_RETRY
        if code != 0:
            raise run.abort(EXIT_RETRY, "score", f"{key}: the scorer exited {code}; will retry",
                            alert_kind="scoring_failure", key=f"{key}:score", stderr_tail=_tail(err, 400))
        if not sealed_path.exists():
            raise run.abort(EXIT_RETRY, "score", f"{key}: the scorer exited 0 but wrote no {sealed_path.name}; "
                            "nothing to seal", alert_kind="scoring_failure", key=f"{key}:score", severity="warning")
    return seal_week(run, wk, plan, data_cutoff=cutoff, exclusion_after=exclusion_after,
                     not_final=pending if not final else [])


def score_week(run: Run) -> int:
    """Friday job (A3, D-121): every due, unsealed week in order; a waiting week never holds a later one."""
    cfg = run.cfg
    if cfg.rehearsal and not cfg.dry_run_branch:
        raise run.abort(EXIT_REFUSED, "score", "score-week is not for rehearsals")
    plan = load_plan(run, season_hint=cfg.season or cfg.test_season, live=False)
    if cfg.test_season is None or not cfg.weeks:
        raise run.abort(EXIT_REFUSED, "score", "the test window is not initialised; run `init` first")
    repo = run.repo()
    if remote_state(run, repo, create=True) is None:
        raise run.abort(EXIT_RETRY, "score", "remote unreachable; will retry", alert_kind="scoring_failure", key="score")
    if cfg.season is not None:
        wk = select_week(run, plan)
        if repo.remote_blob(f"{wk.key}/{core.SEALED_NAME}") is not None:
            run.log.event("score", "already sealed", week=wk.key)
            return EXIT_OK
        weeks = [wk]
    else:
        weeks = weeks_to_score(run, plan, repo)
    if not weeks:
        run.log.event("score", "nothing to do", message="every committed week that kicked off is sealed")
        return EXIT_OK
    results: dict[str, int] = {}
    for wk in weeks:
        try:
            results[wk.key] = score_one(run, wk, plan)
        except Abort as exc:
            results[wk.key] = exc.code
    run.log.event("score", "weeks tried", results=results)
    codes = set(results.values())
    if EXIT_INTEGRITY in codes:
        return EXIT_INTEGRITY
    return EXIT_OK if codes <= {EXIT_OK} else EXIT_RETRY


def cmd_score_week(cfg: Config, deps: Deps) -> int:
    run = Run(cfg, deps, f"{_mode_prefix(cfg)}{_label_key(cfg)}_score")
    try:
        with core.RunLock(cfg.livetest_dir / ".runner.lock"):
            code = score_week(run)
    except Abort as exc:
        code = exc.code
    except core.LockBusy as exc:
        run.log.event("lock", "busy", message=str(exc))
        code = EXIT_RETRY
    except Exception as exc:  # unexpected: log, alert, retry later
        run.log.event("error", "unexpected", message=f"{type(exc).__name__}: {exc}")
        run.alert("scoring_failure", f"score-week crashed: {type(exc).__name__}: {exc}", key="score-crash")
        code = EXIT_RETRY
    run.log.event("summary", "exit", exit_code=code)
    return code


def cmd_verify(cfg: Config, deps: Deps) -> int:
    run = Run(cfg, deps, f"{_mode_prefix(cfg)}{core.week_key(cfg.season, cfg.week)}_verify")
    try:
        plan = load_plan(run, season_hint=cfg.season, live=False)
        wk = select_week(run, plan)
        c = local_commitment(run, wk)
    except Abort as exc:
        return exc.code
    if c is None:
        print(f"no local commitment for {wk.key}")
        return EXIT_RETRY
    repo = run.repo()
    remote_state(run, repo)
    record = core.read_json(cfg.runner_dir(wk.season, wk.week) / "push_record.json") or {}
    prefix = branch_prefix(cfg, wk, c)
    files = {f"{prefix}/{core.COMMITMENT_NAME}": c.raw}
    for rel in (record.get("files_committed") or {}):
        if rel.endswith(core.SUMMARY_NAME):
            blob = repo.head_blob(rel)
            if blob is not None:
                files[rel] = blob
    result = post_push_verify(run, repo, wk, c, files, record.get("commit_sha"))
    result["push_record"] = {k: record.get(k) for k in ("commit_sha", "external_timestamp", "on_time",
                                                        "external_timestamp_source")}
    print(json.dumps(result, indent=2))
    run.log.event("summary", "exit", exit_code=EXIT_OK if result["ok"] else EXIT_INTEGRITY)
    return EXIT_OK if result["ok"] else EXIT_INTEGRITY


def cmd_init(args: argparse.Namespace, cfg: Config) -> int:
    path = cfg.config_path
    old = core.read_json(path)
    if old and not args.force:
        print(f"{path} exists; pass --force to replace it (the old values are kept under 'history')")
        print(json.dumps(old, indent=2))
        return EXIT_REFUSED
    weeks = core.parse_weeks(args.weeks)
    payload: dict[str, Any] = {
        "format": "ffml.livetest.config/1",
        "season": args.init_season,
        "weeks": weeks,
        "branch": cfg.branch,
        "remote": cfg.remote,
        "commitments_worktree": str(cfg.commitments_worktree),
        "code_worktree": str(cfg.code_worktree) if cfg.code_worktree else None,
        "python": str(cfg.python) if cfg.code_worktree else None,
        "expected_commit": cfg.expected_commit,
        "preregistration": str(cfg.preregistration) if cfg.preregistration else None,
        "misses_allowed": cfg.misses_allowed,
        "written_at": core.iso(core.utcnow()),
        "note": args.note or "",
        "history": ([{k: v for k, v in old.items() if k != "history"}] + old.get("history", [])) if old else [],
    }
    core.write_json(path, payload)
    print(json.dumps(payload, indent=2))
    return EXIT_OK


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="livetest_week.py", description="chain_v2 live-test weekly runner",
                                     formatter_class=argparse.RawDescriptionHelpFormatter, epilog=__doc__)
    common = argparse.ArgumentParser(add_help=False)
    common.add_argument("--season", type=int)
    common.add_argument("--week", type=int)
    common.add_argument("--rehearsal", action="store_true", help="write under data/livetest/rehearsal; branch path rehearsal/")
    common.add_argument("--code-worktree", type=Path, help="worktree pinned at the pre-registered commit")
    common.add_argument("--python", type=Path, help="that worktree's venv python")
    common.add_argument("--expected-commit", help="the pre-registered code commit")
    common.add_argument("--branch", help=f"commitments branch (default {core.DEFAULT_BRANCH})")
    common.add_argument("--remote", help="git remote (default origin)")
    common.add_argument("--commitments-worktree", type=Path, help="default ../ffml-livetest-commitments")
    common.add_argument("--main-repo", type=Path, default=core.REPO_ROOT, help=argparse.SUPPRESS)
    common.add_argument("--now", help="clock override for planning, dry runs and stub tests (ISO, UTC)")
    common.add_argument("--no-live-schedule", action="store_true", help="use only the dim_game snapshot")
    common.add_argument("--no-github", action="store_true", help="skip the gh api record")
    common.add_argument("--trailer", action="append", default=[], help="extra commit message trailer line")
    common.add_argument("--no-env-check", action="store_true", help=argparse.SUPPRESS)
    common.add_argument("--stub-score", action="store_true",
                        help="use scripts/livetest_stub_score.py instead of the scorer module (testing)")
    common.add_argument("--preregistration", type=Path, default=None,
                        help="the pre-registration (.json, or the .md with its fenced JSON block); "
                             "missed_weeks.allowed is read from it (default: docs/status/livetest-preregistration-2026.md)")
    common.add_argument("--dry-run-branch", default=None,
                        help="push to this separate branch only, never livetest-2026; builds in the rehearsal "
                             "sandbox with no deadlines (logged-off dry run, D-107)")

    sub = parser.add_subparsers(dest="command", required=True)
    p_run = sub.add_parser("run", parents=[common], help="refresh, build, commit, push, verify")
    p_run.add_argument("--dry-run", action="store_true", help="show what would happen; change nothing")
    p_run.add_argument("--skip-refresh", action="store_true")
    p_run.add_argument("--refresh-steps", default=",".join(REFRESH_STEPS))
    p_run.add_argument("--max-refresh-attempts", type=int, default=3)
    p_run.add_argument("--stub-build", action="store_true", help="use scripts/livetest_stub_build.py (testing)")
    p_run.add_argument("--allow-stub-push", action="store_true", help=argparse.SUPPRESS)
    p_run.add_argument("--no-push", action="store_true", help="build and verify, but do not commit or push")
    p_run.add_argument("--retry-every-minutes", type=float, default=0.0,
                       help="keep retrying in this process until the cutoff (0: one attempt, as scheduled)")
    p_run.add_argument("--build-timeout-minutes", type=float, default=240.0)
    p_run.add_argument("--force-fail", choices=FORCE_FAIL_STEPS, default=None,
                       help="dry-run branch only: make this step fail on purpose to prove the alert and the retry")
    for name in ("watchdog", "check", "verify", "seal", "seal-check"):
        sub.add_parser(name, parents=[common])
    p_score = sub.add_parser("score-week", parents=[common],
                             help="Tuesday job: refresh, the Evaluator's scorer, then seal (oldest unsealed week)")
    p_score.add_argument("--skip-refresh", action="store_true")
    p_score.add_argument("--no-push", action="store_true")
    p_plan = sub.add_parser("plan", parents=[common])
    p_plan.add_argument("--json", action="store_true")
    p_plan.add_argument("--save", action="store_true", help="save as the registered plan for the drift check")
    p_plan.add_argument("--weeks", default=None, help="override the test window, e.g. 10-18 (with --season)")
    p_plan.add_argument("--export", type=Path, default=None,
                        help="write the tracked schedule file for the GitHub watchdog "
                             "(scripts/livetest_schedule_2026.json)")
    p_status = sub.add_parser("status", parents=[common])
    p_status.add_argument("--json", action="store_true")
    p_init = sub.add_parser("init", parents=[common], help="record the test window and the pin")
    p_init.add_argument("--weeks", required=True, help="e.g. 10-18")
    p_init.add_argument("--note", default="")
    p_init.add_argument("--force", action="store_true")
    return parser


def make_config(args: argparse.Namespace) -> Config:
    from ffml.config import get_settings

    settings = get_settings()
    cfg = Config(
        main_repo=Path(args.main_repo).resolve(),
        data_dir=Path(settings.data_dir).resolve(),
        artifacts_dir=Path(settings.artifacts_dir).resolve(),
        reports_dir=Path(settings.reports_dir).resolve(),
        first_season=int(settings.first_season),
    )
    saved = core.read_json(cfg.config_path) or {}
    cfg.test_season = saved.get("season")
    cfg.weeks = list(saved.get("weeks") or [])
    cfg.branch = args.branch or saved.get("branch") or core.DEFAULT_BRANCH
    cfg.remote = args.remote or saved.get("remote") or core.DEFAULT_REMOTE
    wt = args.commitments_worktree or saved.get("commitments_worktree")
    cfg.commitments_worktree = Path(wt).resolve() if wt else core.DEFAULT_COMMITMENTS_WORKTREE
    code_wt = args.code_worktree or saved.get("code_worktree")
    cfg.code_worktree = Path(code_wt).resolve() if code_wt else None
    py = args.python or (saved.get("python") if cfg.code_worktree else None)
    cfg.python = Path(py) if py else Path(sys.executable)
    cfg.expected_commit = args.expected_commit or saved.get("expected_commit")
    cfg.season, cfg.week = args.season, args.week
    cfg.rehearsal = bool(args.rehearsal)
    cfg.live_schedule = not args.no_live_schedule
    cfg.github = not args.no_github
    cfg.trailers = list(args.trailer or [])
    cfg.check_env = not args.no_env_check
    if args.now:
        cfg.now_override = core.parse_time(args.now)
    cfg.real_branch = cfg.branch
    cfg.dry_run_branch = getattr(args, "dry_run_branch", None) or None
    if cfg.dry_run_branch:
        cfg.branch = cfg.dry_run_branch
        cfg.rehearsal = True
        if not args.commitments_worktree:
            safe = re.sub(r"[^A-Za-z0-9._-]+", "-", cfg.dry_run_branch)
            cfg.commitments_worktree = core.DEFAULT_COMMITMENTS_WORKTREE.parent / f"ffml-{safe}-commitments"
    cfg.force_fail = getattr(args, "force_fail", None)
    cfg.stub_score = bool(getattr(args, "stub_score", False))
    prereg = args.preregistration or (None if args.command == "init" else saved.get("preregistration"))
    if prereg:
        cfg.preregistration = Path(prereg)
    elif (cfg.main_repo / core.PREREGISTRATION_DOC).exists():
        cfg.preregistration = cfg.main_repo / core.PREREGISTRATION_DOC
    if cfg.preregistration is not None:
        try:
            prereg_data = core.load_preregistration(cfg.preregistration)
            cfg.prereg_data = prereg_data
            cfg.misses_allowed = core.misses_allowed_from(prereg_data)
            try:
                cfg.window = core.window_from(prereg_data)
            except ValueError:
                cfg.window = None  # fall back to the configured season's weeks
        except (OSError, ValueError) as exc:
            cfg.misses_allowed_error = f"{cfg.preregistration}: {type(exc).__name__}: {exc}"
    else:
        cfg.misses_allowed_error = "no pre-registration file"
    if args.command == "score-week":
        cfg.skip_refresh = args.skip_refresh
        cfg.no_push = args.no_push
    if args.command == "plan" and args.weeks:
        cfg.weeks = core.parse_weeks(args.weeks)
        cfg.test_season = args.season or cfg.test_season
    if args.command == "run":
        cfg.dry_run = args.dry_run
        cfg.skip_refresh = args.skip_refresh
        cfg.refresh_steps = tuple(s.strip() for s in args.refresh_steps.split(",") if s.strip())
        cfg.max_refresh_attempts = args.max_refresh_attempts
        cfg.stub_build = args.stub_build
        cfg.allow_stub_push = args.allow_stub_push
        cfg.no_push = args.no_push
        cfg.retry_every_minutes = args.retry_every_minutes
        cfg.build_timeout_minutes = args.build_timeout_minutes
    return cfg


def main(argv: list[str] | None = None, deps: Deps | None = None) -> int:
    from ffml.config import ensure_utf8

    ensure_utf8()
    args = build_parser().parse_args(argv)
    if os.environ.get("FFML_HOLDOUT_UNLOCK", "").strip().lower() in ("1", "true", "yes", "on"):
        print("refused: FFML_HOLDOUT_UNLOCK is set; the live test never unlocks the holdout", file=sys.stderr)
        return EXIT_REFUSED
    cfg = make_config(args)
    deps = deps or Deps()
    if cfg.now_override is not None and args.command == "run" and not (cfg.stub_build or cfg.dry_run):
        print("refused: --now is only for --dry-run and --stub-build runs", file=sys.stderr)
        return EXIT_REFUSED
    if cfg.dry_run_branch and (
        cfg.dry_run_branch in core.PROTECTED_BRANCHES
        or cfg.dry_run_branch == cfg.real_branch
        or cfg.dry_run_branch.startswith(core.DEFAULT_BRANCH)
    ):
        print(f"refused: --dry-run-branch {cfg.dry_run_branch!r} is the real commitments branch or a protected "
              "branch; use a separate name such as livetest-dryrun", file=sys.stderr)
        return EXIT_REFUSED
    if cfg.force_fail and not cfg.dry_run_branch:
        print("refused: --force-fail is only for --dry-run-branch runs", file=sys.stderr)
        return EXIT_REFUSED
    for name in ("FFML_LIVETEST_NOW",):
        if name in os.environ and not cfg.stub_build:
            os.environ.pop(name, None)
    bad = [s for s in cfg.refresh_steps if s not in REFRESH_STEPS]
    if bad:
        print(f"refused: unknown refresh steps {bad}", file=sys.stderr)
        return EXIT_REFUSED
    if args.command == "init":
        args.init_season = args.season
        if args.init_season is None:
            print("init needs --season", file=sys.stderr)
            return EXIT_REFUSED
        return cmd_init(args, cfg)
    if args.command == "run":
        return cmd_run(cfg, deps)
    if args.command == "watchdog":
        return cmd_watchdog(cfg, deps)
    if args.command == "check":
        return cmd_check(cfg, deps)
    if args.command == "plan":
        return cmd_plan(cfg, deps, args.json, args.save, args.export)
    if args.command == "status":
        return cmd_status(cfg, deps, args.json)
    if args.command == "verify":
        if cfg.season is None or cfg.week is None:
            print("verify needs --season and --week", file=sys.stderr)
            return EXIT_REFUSED
        return cmd_verify(cfg, deps)
    if args.command in ("seal", "seal-check"):
        if cfg.season is None or cfg.week is None:
            print(f"{args.command} needs --season and --week", file=sys.stderr)
            return EXIT_REFUSED
        return cmd_seal(cfg, deps, check_only=args.command == "seal-check")
    if args.command == "score-week":
        return cmd_score_week(cfg, deps)
    return EXIT_REFUSED  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
