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
    python scripts/livetest_week.py plan [--json] [--save]
    python scripts/livetest_week.py status [--json]
    python scripts/livetest_week.py verify --season S --week W [--rehearsal]

Exit codes: 0 done or nothing to do; 1 failed, retry later; 2 build blocked
or pin refused; 3 missed (too late for this week); 4 refused (bad arguments
or unsafe environment); 6 integrity problem (needs a human); 7 an existing
snapshot was modified.

Timing defaults (relative to the first kickoff K; tentative weeks use K - 24 h):
act from K - 12 h; scheduled start K - 9 h with retries every 30 min; no
refresh starts after K - 3 h (the build then uses the newest snapshots, with
an alert); no refresh or build starts after K - 30 min; no push starts after
K - 5 min; nothing is pushed for the week after K (a MISSED marker is pushed
instead once the week has kicked off).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import time
from collections.abc import Callable
from dataclasses import asdict, dataclass, field
from datetime import datetime, timedelta
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
        return (base / "rehearsal" if self.rehearsal else base) / core.week_key(season, week)

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
                details={**details, "run_log": str(self.log.path), "rehearsal": self.cfg.rehearsal},
                dedupe_key=f"livetest:{kind}:{key}{':rehearsal' if self.cfg.rehearsal else ''}",
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
    if not cfg.rehearsal:
        return wk.key
    if commitment is None or commitment.created_at is None:
        return None
    return f"rehearsal/{wk.key}-{core.compact(commitment.created_at)}"


def commit_message(cfg: Config, wk: core.WeekKickoff, c: core.Commitment, run: Run, refresh: dict[str, Any]) -> str:
    title = (
        f"REHEARSAL livetest {wk.key}: stub commitment, not part of the test"
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


def missed_marker(run: Run, wk: core.WeekKickoff, reason: str) -> dict[str, Any]:
    cfg = run.cfg
    path = cfg.commitment_dir(wk.season, wk.week) / core.COMMITMENT_NAME
    local: dict[str, Any] = {"exists": path.exists()}
    if path.exists():
        try:
            c = core.load_commitment(path)
            local.update(sha256=c.sha256, created_at=core.iso(c.created_at), digest=c.digest)
        except (ValueError, OSError) as exc:
            local["error"] = str(exc)
    logs = sorted(p.name for p in cfg.logs_dir.glob(f"*{wk.key}*.jsonl"))[-10:] if cfg.logs_dir.exists() else []
    return {
        "format": "ffml.livetest.missed/1",
        "season": wk.season,
        "week": wk.week,
        "first_kickoff_utc": core.iso(wk.first_kickoff_utc),
        "recorded_at": core.iso(run.now()),
        "status": "MISSED: no commitment was pushed to this branch before the first kickoff",
        "reason": reason,
        "local_commitment": local,
        "runner_logs": logs,
        "note": "How a missed week counts is set by the pre-registered test design.",
    }


def record_misses(run: Run, plan: list[core.WeekKickoff], repo: core.CommitmentsRepo | None = None) -> list[str]:
    """Push a MISSED marker for every test week that kicked off without a pushed commitment."""
    cfg = run.cfg
    if cfg.rehearsal or cfg.test_season is None or not cfg.weeks or cfg.no_push or cfg.dry_run:
        return []
    now = run.now()
    due = [w for w in plan if w.season == cfg.test_season and w.week in cfg.weeks and w.first_kickoff_utc <= now]
    if not due:
        return []
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
        reason = "no commitment on the branch at the first kickoff"
        local = core.read_json(cfg.runner_dir(wk.season, wk.week) / "push_record.json")
        if local:
            reason += f"; local push record says {local.get('ok')}"
        marker = missed_marker(run, wk, reason)
        data = (json.dumps(marker, indent=2) + "\n").encode("utf-8")
        try:
            repo.sync()
            sha = repo.commit({f"{wk.key}/{core.MISSED_NAME}": data},
                              f"livetest {wk.key}: MISSED, no commitment pushed before first kickoff "
                              f"{core.iso(wk.first_kickoff_utc)}\n\n{reason}\n"
                              + ("\n" + "\n".join(cfg.trailers) + "\n" if cfg.trailers else ""))
            push = repo.push(clock=run.now, sleep=run.deps.sleep)
        except core.GitError as exc:
            run.log.event("misses", "failed", week=wk.key, message=str(exc))
            continue
        core.write_json(cfg.runner_dir(wk.season, wk.week) / core.MISSED_NAME, {**marker, "commit": sha, "push": push})
        run.log.event("misses", "marked", week=wk.key, commit=sha, pushed=push["ok"])
        run.alert("missed_commitment", f"{wk.key}: kicked off at {core.iso(wk.first_kickoff_utc)} with no pushed "
                  "commitment; MISSED marker recorded", key=f"{wk.key}:marker", severity="critical")
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
            raise run.abort(EXIT_INTEGRITY, "idempotent", f"{key}: the branch holds a different commitment than "
                            "the local one", alert_kind="integrity", key=key, severity="critical",
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
                      would_push_to=f"{cfg.remote} refs/heads/{cfg.branch}", worktree=str(cfg.commitments_worktree))
        return EXIT_OK

    # ---- refresh and build ------------------------------------------------- #
    refresh: dict[str, Any] = {"status": "not needed (commitment already built)"}
    if c is None:
        if cfg.skip_refresh:
            refresh = {"status": "skipped (--skip-refresh)"}
            run.log.event("refresh", "skipped", message="--skip-refresh")
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
        sha = repo.commit(files, commit_message(cfg, wk, c, run, refresh))
        if sha is None:
            sha = repo.head()
            for rel, data in files.items():
                if repo.head_blob(rel) != data:
                    raise core.GitError(f"{rel} is not committed on the local branch")
        run.log.event("commit", "done", sha=sha, sync=sync, files=sorted(files))
    except core.GitError as exc:
        raise run.abort(EXIT_INTEGRITY, "commit", f"{key}: {exc}", alert_kind="push_failure", key=key,
                        severity="critical") from None
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


def cmd_run(cfg: Config, deps: Deps) -> int:
    label = f"{'rehearsal_' if cfg.rehearsal else ''}{_label_key(cfg)}_run"
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
    run = Run(cfg, deps, f"{_label_key(cfg)}_watchdog")
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


def cmd_plan(cfg: Config, deps: Deps, as_json: bool, save: bool) -> int:
    run = Run(cfg, deps, "plan", echo=False)
    try:
        plan = load_plan(run, season_hint=cfg.test_season)
    except Abort as exc:
        print(str(exc), file=sys.stderr)
        return exc.code
    rows = plan_rows(cfg, plan, run.now())
    if save:
        core.write_json(cfg.livetest_dir / "schedule_plan.json", {"saved_at": core.iso(run.now()), "weeks": rows})
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
    run.log.event("summary", "exit", exit_code=code, misses_marked=marked)
    return code


def week_status(cfg: Config, run: Run, repo: core.CommitmentsRepo | None, remote: bool | None,
                wk: core.WeekKickoff) -> dict[str, Any]:
    c_path = cfg.commitment_dir(wk.season, wk.week) / core.COMMITMENT_NAME
    c = core.load_commitment(c_path) if c_path.exists() else None
    record = core.read_json(cfg.runner_dir(wk.season, wk.week) / "push_record.json") or {}
    prefix = branch_prefix(cfg, wk, c)
    pushed = missed = None
    if remote is not None and repo is not None:
        blob = repo.remote_blob(f"{prefix}/{core.COMMITMENT_NAME}") if prefix else None
        pushed = blob is not None and (c is None or blob == c.raw)
        missed = repo.remote_blob(f"{wk.key}/{core.MISSED_NAME}") is not None
    return {
        "week": wk.key,
        "first_kickoff_utc": core.iso(wk.first_kickoff_utc),
        "local_commitment": c.sha256[:12] if c else None,
        "created_at": core.iso(c.created_at) if c else None,
        "pushed": pushed,
        "github_push_timestamp": record.get("external_timestamp"),
        "on_time": record.get("on_time"),
        "missed_marker": missed,
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
    rows = [week_status(cfg, run, repo, remote, w) for w in plan
            if w.season == season and (not cfg.weeks or w.week in cfg.weeks)]
    if as_json:
        print(json.dumps({"remote_reachable": remote is not None, "weeks": rows}, indent=2))
    else:
        print(f"remote {cfg.remote}/{cfg.branch} reachable: {remote is not None}")
        print(f"{'week':<8} {'first kickoff UTC':<22} {'local':<13} {'pushed':<7} {'GitHub push time':<22} "
              f"{'on time':<8} missed")
        for r in rows:
            print(f"{r['week']:<8} {r['first_kickoff_utc']:<22} {str(r['local_commitment']):<13} {str(r['pushed']):<7} "
                  f"{str(r['github_push_timestamp']):<22} {str(r['on_time']):<8} {r['missed_marker']}")
    return EXIT_OK


def cmd_verify(cfg: Config, deps: Deps) -> int:
    run = Run(cfg, deps, f"{'rehearsal_' if cfg.rehearsal else ''}{core.week_key(cfg.season, cfg.week)}_verify")
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
    for name in ("watchdog", "check", "verify"):
        sub.add_parser(name, parents=[common])
    p_plan = sub.add_parser("plan", parents=[common])
    p_plan.add_argument("--json", action="store_true")
    p_plan.add_argument("--save", action="store_true", help="save as the registered plan for the drift check")
    p_plan.add_argument("--weeks", default=None, help="override the test window, e.g. 10-18 (with --season)")
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
        return cmd_plan(cfg, deps, args.json, args.save)
    if args.command == "status":
        return cmd_status(cfg, deps, args.json)
    if args.command == "verify":
        if cfg.season is None or cfg.week is None:
            print("verify needs --season and --week", file=sys.stderr)
            return EXIT_REFUSED
        return cmd_verify(cfg, deps)
    return EXIT_REFUSED  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
