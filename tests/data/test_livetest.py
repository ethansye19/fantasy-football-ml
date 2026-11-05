"""Tests for the live-test runner (scripts/livetest_week.py, scripts/livetest_core.py).

Git runs against a temporary bare repository; the build is the contract stub
(scripts/livetest_stub_build.py); refresh steps are faked; nothing touches the
network, the real data directory or the real remote.
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import sys
from datetime import UTC, datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

import polars as pl
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import livetest_core as core  # noqa: E402
import livetest_week as runner  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "livetest_dim_game_2026.csv"
ET = ZoneInfo("America/New_York")
K10 = datetime(2026, 11, 13, 1, 15, tzinfo=UTC)  # week 10 first kickoff (Thursday 20:15 ET)

pytestmark = pytest.mark.skipif(shutil.which("git") is None, reason="git is required")


def load_fixture() -> pl.DataFrame:
    return pl.read_csv(FIXTURE).with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("kickoff_utc").str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
    )


def iso(ts: datetime) -> str:
    return ts.strftime("%Y-%m-%dT%H:%M:%SZ")


def git(*args: str, cwd: Path) -> str:
    proc = subprocess.run(["git", *args], cwd=cwd, capture_output=True, text=True)
    assert proc.returncode == 0, proc.stderr
    return proc.stdout.strip()


# --------------------------------------------------------------------------- #
# Week plan and kickoff computation
# --------------------------------------------------------------------------- #


def test_plan_from_schedule_fixture_week12_wednesday_and_week18_saturday():
    plan = core.week_plan(load_fixture(), 2026)
    assert [w.week for w in plan] == list(range(8, 19))
    w12 = core.find_week(plan, 2026, 12)
    assert w12.first_kickoff_utc == datetime(2026, 11, 26, 1, 0, tzinfo=UTC)
    assert w12.first_kickoff_utc.astimezone(ET).strftime("%A %H:%M") == "Wednesday 20:00"
    assert not w12.tentative
    w18 = core.find_week(plan, 2026, 18)
    assert w18.first_kickoff_utc == datetime(2027, 1, 9, 21, 30, tzinfo=UTC)
    assert w18.first_kickoff_utc.astimezone(ET).strftime("%A") == "Saturday"
    assert not w18.tentative
    w10 = core.find_week(plan, 2026, 10)
    assert w10.first_kickoff_utc == K10 and w10.games == 14


def test_triggers_are_relative_to_the_first_kickoff():
    plan = core.week_plan(load_fixture(), 2026)
    t12 = core.triggers(core.find_week(plan, 2026, 12))
    assert t12["window_open"] == datetime(2026, 11, 25, 0, 0, tzinfo=UTC)  # K - 25 h
    assert t12["run_start"] == datetime(2026, 11, 25, 0, 45, tzinfo=UTC)  # K - 24 h 15 min (D-106: at least 24 h)
    assert t12["kickoff"] - t12["run_start"] >= timedelta(hours=24)
    assert t12["watchdog"] == datetime(2026, 11, 26, 0, 0, tzinfo=UTC)
    assert t12["cutoff"] == datetime(2026, 11, 26, 0, 30, tzinfo=UTC)
    assert t12["push_deadline"] == datetime(2026, 11, 26, 0, 55, tzinfo=UTC)
    t18 = core.triggers(core.find_week(plan, 2026, 18))
    assert t18["run_start"] == datetime(2027, 1, 8, 21, 15, tzinfo=UTC)  # Friday evening for a Saturday kickoff


def test_next_week_is_strictly_after_now():
    plan = core.week_plan(load_fixture(), 2026)
    assert core.next_week(plan, datetime(2026, 11, 12, 16, 0, tzinfo=UTC)).week == 10
    assert core.next_week(plan, K10).week == 11  # at kickoff week 10 is no longer next
    assert core.next_week(plan, datetime(2026, 11, 21, tzinfo=UTC)).week == 12
    assert core.next_week(plan, datetime(2027, 1, 9, 12, 0, tzinfo=UTC)).week == 18
    assert core.next_week(plan, datetime(2027, 1, 9, 22, 0, tzinfo=UTC)) is None


def test_tentative_week_is_scheduled_a_day_early_and_live_schedule_wins():
    df = load_fixture().with_columns(
        pl.when(pl.col("week") == 18)
        .then(pl.lit(datetime(2027, 1, 10, 18, 0, tzinfo=UTC)))
        .otherwise(pl.col("kickoff_utc"))
        .alias("kickoff_utc")
    )
    tentative = core.find_week(core.week_plan(df, 2026), 2026, 18)
    assert tentative.tentative
    assert tentative.effective_kickoff_utc == datetime(2027, 1, 9, 18, 0, tzinfo=UTC)
    assert core.triggers(tentative)["run_start"] == datetime(2027, 1, 8, 17, 45, tzinfo=UTC)
    live = core.week_plan(load_fixture(), 2026)
    merged, diffs = runner.merge_plans(core.week_plan(df, 2026), live)
    w18 = core.find_week(merged, 2026, 18)
    assert not w18.tentative and w18.first_kickoff_utc == datetime(2027, 1, 9, 21, 30, tzinfo=UTC)
    assert [d["week"] for d in diffs] == ["2026w18"]


# --------------------------------------------------------------------------- #
# Runner end to end against a temporary bare remote
# --------------------------------------------------------------------------- #


class Env:
    def __init__(self, root: Path) -> None:
        self.root = root
        self.data = root / "data"
        self.remote = root / "remote.git"
        self.main = root / "main"
        self.worktree = root / "commitments"
        self.calls = root / "stub_calls.txt"
        self.alert_log = root / "alerts" / "alerts.jsonl"

    def argv(self, command: str, now: datetime, *extra: str, week: int | None = 10,
             worktree: Path | None = None) -> list[str]:
        out = [command, "--main-repo", str(self.main), "--commitments-worktree", str(worktree or self.worktree),
               "--now", iso(now), "--no-live-schedule", "--no-github"]
        if week is not None:
            out += ["--season", "2026", "--week", str(week)]
        if command == "run":
            out += ["--stub-build", "--allow-stub-push", "--skip-refresh"]
        return out + list(extra)

    def main_(self, argv: list[str], deps: runner.Deps | None = None) -> int:
        return runner.main(argv, deps=deps or runner.Deps(live_schedule=None))

    def stub_calls(self) -> int:
        return len(self.calls.read_text().splitlines()) if self.calls.exists() else 0

    def remote_blob(self, rel: str, branch: str = "livetest-2026") -> bytes | None:
        proc = subprocess.run(["git", "cat-file", "blob", f"{branch}:{rel}"], cwd=self.remote, capture_output=True)
        return proc.stdout if proc.returncode == 0 else None

    def remote_refs(self) -> list[str]:
        return git("for-each-ref", "--format=%(refname)", cwd=self.remote).splitlines()

    def alerts(self) -> list[dict]:
        if not self.alert_log.exists():
            return []
        return [json.loads(line) for line in self.alert_log.read_text(encoding="utf-8").splitlines()]

    def commitment_path(self, week: int = 10, rehearsal: bool = False) -> Path:
        base = self.data / "livetest" / ("rehearsal" if rehearsal else "")
        return base / f"2026w{week:02d}" / "commitment.json"

    def logs(self) -> str:
        return "\n".join(p.read_text(encoding="utf-8") for p in sorted((self.data / "livetest" / "logs").glob("*.jsonl")))


@pytest.fixture
def env(tmp_path, monkeypatch, tmp_settings):
    from ffml.data.store import SnapshotStore

    e = Env(tmp_path)
    for name, value in {
        "GIT_AUTHOR_NAME": "test", "GIT_AUTHOR_EMAIL": "test@example.com",
        "GIT_COMMITTER_NAME": "test", "GIT_COMMITTER_EMAIL": "test@example.com",
        "FFML_REPORTS_DIR": str(tmp_path / "reports"), "FFML_ALERT_LOG": str(e.alert_log),
        "FFML_ALERT_TOAST": "0", "FFML_STUB_CALLS": str(e.calls),
    }.items():
        monkeypatch.setenv(name, value)
    for name in ("FFML_ALERT_WEBHOOK", "ALERT_WEBHOOK_URL", "FFML_HOLDOUT_UNLOCK", "FFML_STUB_EXIT"):
        monkeypatch.delenv(name, raising=False)
    SnapshotStore(e.data / "processed").write_snapshot("dim_game", {2026: load_fixture()})
    git("init", "--quiet", "--bare", str(e.remote), cwd=tmp_path)
    e.main.mkdir()
    git("init", "--quiet", "-b", "main", cwd=e.main)
    (e.main / "README.md").write_text("main repo\n")
    git("add", "README.md", cwd=e.main)
    git("commit", "--quiet", "-m", "main: unreviewed work that must never be pushed", cwd=e.main)
    git("remote", "add", "origin", str(e.remote), cwd=e.main)
    assert e.main_(["init", "--season", "2026", "--weeks", "10-18", "--main-repo", str(e.main),
                    "--commitments-worktree", str(e.worktree)]) == 0
    return e


def test_run_pushes_commitment_to_orphan_branch_only(env):
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == runner.EXIT_OK
    assert env.stub_calls() == 1
    local = env.commitment_path().read_bytes()
    assert env.remote_blob("2026w10/commitment.json") == local
    summary = env.remote_blob("2026w10/player_summary.parquet")
    c = core.load_commitment(env.commitment_path())
    assert core.sha256_bytes(summary) == c.summary_entry().sha256
    assert env.remote_blob(".gitattributes") == b"* -text\n"
    assert env.remote_refs() == ["refs/heads/livetest-2026"]  # main was never pushed
    main_sha = git("rev-parse", "main", cwd=env.main)
    assert git("rev-list", "--count", "livetest-2026", cwd=env.remote) == "1"
    assert main_sha not in git("rev-list", "--all", cwd=env.remote)
    message = git("log", "-1", "--format=%B", "livetest-2026", cwd=env.remote)
    assert message.startswith("livetest 2026w10: commitment before first kickoff 2026-11-13T01:15:00Z")
    assert c.sha256 in message
    runner_dir = env.data / "livetest" / "runner" / "2026w10"
    record = json.loads((runner_dir / "push_record.json").read_text())
    assert record["ok"] and record["ls_remote_sha"] == record["commit_sha"]
    assert record["on_time"] is True and record["external_timestamp_source"] == "none: local clock only"
    verify = json.loads((runner_dir / "verify.json").read_text())
    assert verify["ok"], verify["problems"]
    assert verify["digest"]["match"] is True


def test_second_run_is_a_no_op(env):
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == 0
    head = git("rev-parse", "livetest-2026", cwd=env.remote)
    assert env.main_(env.argv("run", K10 - timedelta(hours=8, minutes=30))) == 0
    assert env.stub_calls() == 1
    assert git("rev-parse", "livetest-2026", cwd=env.remote) == head
    assert '"status": "already pushed"' in env.logs()


def test_built_but_unpushed_commitment_is_pushed_not_rebuilt(env):
    git("remote", "set-url", "origin", str(env.root / "missing.git"), cwd=env.main)
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == runner.EXIT_RETRY
    assert env.stub_calls() == 1 and env.commitment_path().exists()
    assert [a["kind"] for a in env.alerts()] == ["push_failure"]
    git("remote", "set-url", "origin", str(env.remote), cwd=env.main)
    assert env.main_(env.argv("run", K10 - timedelta(hours=8, minutes=30))) == runner.EXIT_OK
    assert env.stub_calls() == 1
    assert env.remote_blob("2026w10/commitment.json") == env.commitment_path().read_bytes()


def test_different_commitment_on_branch_is_an_integrity_error(env):
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == 0
    path = env.commitment_path()
    data = json.loads(path.read_text())
    data["created_at"] = "2026-11-12T16:16:00+00:00"
    path.write_text(json.dumps(data))
    assert env.main_(env.argv("run", K10 - timedelta(hours=8))) == runner.EXIT_INTEGRITY
    assert env.alerts()[-1]["kind"] == "integrity"


def test_too_early_does_nothing_and_the_window_opens_a_day_ahead(env):
    assert env.main_(env.argv("run", K10 - timedelta(hours=26))) == runner.EXIT_OK
    assert env.stub_calls() == 0 and env.remote_refs() == []
    assert env.main_(env.argv("run", K10 - timedelta(hours=24, minutes=15))) == runner.EXIT_OK  # scheduled start
    assert env.stub_calls() == 1 and env.remote_blob("2026w10/commitment.json") is not None
    status = env.remote_blob("STATUS.md").decode()
    assert "| 2026w10 |" in status and "committed" in status and "Misses: 0" in status


def test_no_build_after_the_cutoff(env):
    assert env.main_(env.argv("run", K10 - timedelta(minutes=29))) == runner.EXIT_MISSED
    assert env.stub_calls() == 0
    assert env.alerts()[-1]["kind"] == "missed_commitment"


def test_nothing_after_kickoff(env):
    assert env.main_(env.argv("run", K10)) == runner.EXIT_MISSED
    assert env.stub_calls() == 0 and env.remote_blob("2026w10/commitment.json") is None


def test_built_commitment_pushed_until_the_push_deadline(env, monkeypatch):
    monkeypatch.setenv("FFML_LIVETEST_NOW", iso(K10 - timedelta(hours=1)))
    stub = [sys.executable, str(SCRIPTS / "livetest_stub_build.py"), "livetest", "build-week", "--season", "2026",
            "--week", "10"]
    assert subprocess.run(stub, env=dict(os.environ)).returncode == 0
    assert env.main_(env.argv("run", K10 - timedelta(minutes=3))) == runner.EXIT_MISSED  # past K - 5 min
    assert env.remote_blob("2026w10/commitment.json") is None
    assert env.main_(env.argv("run", K10 - timedelta(minutes=10))) == runner.EXIT_OK  # before K - 5 min
    assert env.remote_blob("2026w10/commitment.json") is not None
    assert env.stub_calls() == 1


def test_missed_week_gets_a_marker_with_machine_offline_reason(env):
    assert env.main_(env.argv("run", K10 + timedelta(hours=1), week=None)) == runner.EXIT_OK  # week 11 too early
    marker = json.loads(env.remote_blob("2026w10/MISSED.json"))
    assert marker["status"].startswith("MISSED") and marker["first_kickoff_utc"] == "2026-11-13T01:15:00Z"
    assert marker["reason"]["category"] == "machine_offline"  # no runner log in the 25 h before kickoff
    assert marker["run_logs"]["files"] == [] and marker["run_logs"]["window"]["from"] == "2026-11-12T00:15:00Z"
    assert marker["miss_number"] == 1 and marker["test_status"].startswith("VALID")
    assert marker["late_commitment"] is None
    assert env.remote_blob("2026w10/commitment.json") is None
    status = env.remote_blob("STATUS.md").decode()
    assert "Misses: 1" in status and "Test state: VALID" in status and "| 2026w10 |" in status
    assert "machine_offline" in status
    assert any(a["kind"] == "missed_commitment" for a in env.alerts())
    head = git("rev-parse", "livetest-2026", cwd=env.remote)
    assert env.main_(env.argv("check", K10 + timedelta(hours=2), week=None)) in (0, 1)
    assert git("rev-parse", "livetest-2026", cwd=env.remote) == head  # marked once


def test_missed_week_records_the_technical_failure_and_its_log_excerpt(env, monkeypatch):
    monkeypatch.setenv("FFML_STUB_EXIT", "1")
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == runner.EXIT_RETRY  # a failed run in the window
    monkeypatch.delenv("FFML_STUB_EXIT")
    assert env.main_(env.argv("run", K10 + timedelta(hours=1), week=None)) == runner.EXIT_OK
    marker = json.loads(env.remote_blob("2026w10/MISSED.json"))
    assert marker["reason"]["category"] == "technical_failure"
    assert "build-week failed (exit 1)" in marker["reason"]["detail"]
    assert len(marker["run_logs"]["files"]) == 1 and marker["run_logs"]["files"][0].endswith("_2026w10_run.jsonl")
    assert any(e.get("step") == "build" and e.get("status") == "failed" for e in marker["run_logs"]["excerpt"])
    alert = [a for a in env.alerts() if a["kind"] == "missed_commitment"][-1]
    assert "technical_failure" in alert["message"] and "VALID" in alert["message"]


def test_built_but_unpushed_commitment_is_pushed_late_and_never_withheld(env, monkeypatch):
    monkeypatch.setenv("FFML_LIVETEST_NOW", iso(K10 - timedelta(hours=1)))
    stub = [sys.executable, str(SCRIPTS / "livetest_stub_build.py"), "livetest", "build-week", "--season", "2026",
            "--week", "10"]
    assert subprocess.run(stub, env=dict(os.environ)).returncode == 0
    monkeypatch.delenv("FFML_LIVETEST_NOW")
    assert env.main_(env.argv("run", K10 + timedelta(hours=1), week=None)) == runner.EXIT_OK
    marker = json.loads(env.remote_blob("2026w10/MISSED.json"))
    assert marker["reason"]["category"] == "technical_failure"
    assert "built at 2026-11-13T00:15:00Z but was not pushed" in marker["reason"]["detail"]
    assert marker["local_commitment"]["built_before_kickoff"] is True
    late = marker["late_commitment"]
    assert late["path"] == "2026w10/late/commitment.json" and late["verified"] is True
    assert env.remote_blob("2026w10/late/commitment.json") == env.commitment_path().read_bytes()
    assert env.remote_blob("2026w10/late/player_summary.parquet") is not None
    assert env.remote_blob("2026w10/commitment.json") is None  # not a pre-kickoff commitment
    assert "| yes |" in env.remote_blob("STATUS.md").decode()


def test_second_miss_makes_the_test_invalid(env):
    K11 = datetime(2026, 11, 20, 1, 15, tzinfo=UTC)
    assert env.main_(env.argv("run", K10 + timedelta(hours=1), week=None)) == runner.EXIT_OK
    assert env.main_(env.argv("run", K11 + timedelta(hours=1), week=None)) == runner.EXIT_OK
    first = json.loads(env.remote_blob("2026w10/MISSED.json"))
    second = json.loads(env.remote_blob("2026w11/MISSED.json"))
    assert (first["miss_number"], second["miss_number"]) == (1, 2)
    assert second["earlier_misses"] == ["2026w10"] and second["test_status"].startswith("INVALID")
    status = env.remote_blob("STATUS.md").decode()
    assert "Misses: 2" in status and "INVALID" in status
    assert "INVALID" in git("log", "-1", "--format=%s", "livetest-2026", cwd=env.remote)
    assert "INVALID" in [a for a in env.alerts() if a["kind"] == "missed_commitment"][-1]["message"]


def test_status_shows_misses_and_the_test_state(env, capsys):
    assert env.main_(env.argv("run", K10 + timedelta(hours=1), week=None)) == runner.EXIT_OK
    capsys.readouterr()
    assert env.main_(env.argv("status", K10 + timedelta(hours=2), week=None)) == runner.EXIT_OK
    out = capsys.readouterr().out
    assert "2026w10" in out and "machine_offline" in out
    assert "misses: 1 (2026w10); allowed 1; test state: VALID" in out
    assert env.main_(env.argv("status", K10 + timedelta(hours=2), "--json", week=None)) == runner.EXIT_OK
    data = json.loads(capsys.readouterr().out)
    assert data["misses"] == ["2026w10"] and data["test_state"] == "VALID"
    assert data["weeks"][0]["missed_reason"] == "machine_offline"


def test_plan_export_writes_the_tracked_schedule(env, tmp_path):
    out = tmp_path / "schedule.json"
    assert env.main_(env.argv("plan", K10 - timedelta(days=5), "--export", str(out), week=None)) == runner.EXIT_OK
    data = json.loads(out.read_text(encoding="utf-8"))
    assert data["format"] == "ffml.livetest.schedule/1"
    assert data["season"] == 2026 and data["test_weeks"] == list(range(10, 19))
    w12 = next(w for w in data["weeks"] if w["week"] == 12)
    assert w12["key"] == "2026w12" and w12["first_kickoff_utc"] == "2026-11-26T01:00:00Z"
    assert w12["tentative"] is False and w12["games"] == 16


def test_dry_run_branch_pushes_elsewhere_and_force_fail_proves_alert_and_retry(env):
    wt = env.root / "dryrun-commitments"
    early = K10 - timedelta(days=20)
    dry = ["--dry-run-branch", "livetest-dryrun"]
    # refusals: the real branch, and --force-fail outside a dry run
    assert env.main_(env.argv("run", early, "--dry-run-branch", "livetest-2026", worktree=wt)) == runner.EXIT_REFUSED
    assert env.main_(env.argv("run", early, "--force-fail", "push")) == runner.EXIT_REFUSED
    assert env.remote_refs() == [] and env.stub_calls() == 0
    # forced push failure: built and committed locally, nothing pushed, alert raised
    assert env.main_(env.argv("run", early, *dry, "--force-fail", "push", worktree=wt)) == runner.EXIT_RETRY
    assert env.alerts()[-1]["kind"] == "push_failure" and env.remote_refs() == [] and env.stub_calls() == 1
    # the retry without the flag pushes the same commitment as is (never rebuilt)
    assert env.main_(env.argv("run", early, *dry, worktree=wt)) == runner.EXIT_OK
    assert env.stub_calls() == 1 and env.remote_refs() == ["refs/heads/livetest-dryrun"]
    local = env.commitment_path(rehearsal=True).read_bytes()
    assert env.remote_blob("2026w10/commitment.json", branch="livetest-dryrun") == local
    assert env.remote_blob("2026w10/commitment.json") is None
    assert git("log", "-1", "--format=%s", "livetest-dryrun", cwd=env.remote).startswith(
        "DRY RUN livetest 2026w10 on livetest-dryrun: stub commitment")
    assert env.main_(env.argv("watchdog", early, *dry, worktree=wt)) == runner.EXIT_OK
    # forced build failure on another week: alert, nothing built, the watchdog on that branch complains
    assert env.main_(env.argv("run", early, *dry, "--force-fail", "build", week=11, worktree=wt)) == runner.EXIT_RETRY
    assert env.alerts()[-1]["kind"] == "build_failure" and env.stub_calls() == 1
    assert env.main_(env.argv("watchdog", early, *dry, week=11, worktree=wt)) == runner.EXIT_RETRY
    # a later dry run of the same week replaces the earlier commitment (dry-run branches are not immutable)
    env.commitment_path(rehearsal=True).unlink()
    assert env.main_(env.argv("run", early + timedelta(days=1), *dry, worktree=wt)) == runner.EXIT_OK
    assert env.stub_calls() == 2
    assert env.remote_blob("2026w10/commitment.json", branch="livetest-dryrun") != local
    assert env.remote_refs() == ["refs/heads/livetest-dryrun"]  # still nothing on livetest-2026


def test_watchdog_alerts_when_nothing_is_pushed(env):
    assert env.main_(env.argv("watchdog", K10 - timedelta(hours=1))) == runner.EXIT_RETRY
    assert env.alerts()[-1]["kind"] == "missed_commitment"
    assert env.main_(env.argv("run", K10 - timedelta(hours=2))) == 0
    assert env.main_(env.argv("watchdog", K10 - timedelta(hours=1))) == runner.EXIT_OK


@pytest.mark.parametrize(("stub_exit", "expected", "kind"), [
    ("2", runner.EXIT_BLOCKED, "build_failure"),
    ("1", runner.EXIT_RETRY, "build_failure"),
    ("3", runner.EXIT_MISSED, "missed_commitment"),
])
def test_build_exit_codes(env, monkeypatch, stub_exit, expected, kind):
    monkeypatch.setenv("FFML_STUB_EXIT", stub_exit)
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == expected
    assert env.alerts()[-1]["kind"] == kind
    assert env.remote_refs() == []


def test_stub_commitment_is_never_pushed_as_a_real_week(env):
    argv = [a for a in env.argv("run", K10 - timedelta(hours=9)) if a != "--allow-stub-push"]
    assert env.main_(argv) == runner.EXIT_REFUSED
    assert env.remote_refs() == []


def test_rehearsal_goes_under_rehearsal_and_is_labeled(env):
    argv = env.argv("run", K10 - timedelta(days=8), "--rehearsal")
    assert env.main_(argv) == runner.EXIT_OK
    assert env.commitment_path(rehearsal=True).exists() and not env.commitment_path().exists()
    paths = git("ls-tree", "-r", "--name-only", "livetest-2026", cwd=env.remote).splitlines()
    assert any(p.startswith("rehearsal/2026w10-") and p.endswith("/commitment.json") for p in paths)
    assert git("log", "-1", "--format=%s", "livetest-2026", cwd=env.remote).startswith("REHEARSAL livetest 2026w10")


def test_refresh_failure_retries_then_builds_on_existing_snapshots(env):
    def fake_run(cmd, **kwargs):
        if "ffml.data.refresh" in cmd:
            return subprocess.CompletedProcess(cmd, 1, 'REFRESH_RESULT {"ok": false}\n', "network down")
        return subprocess.run(cmd, **kwargs)

    deps = runner.Deps(live_schedule=None, run=fake_run)
    argv = [a for a in env.argv("run", K10 - timedelta(hours=9)) if a != "--skip-refresh"]
    assert env.main_(argv, deps) == runner.EXIT_RETRY
    assert env.stub_calls() == 0
    assert env.alerts()[-1]["kind"] == "refresh_failure"
    state = json.loads((env.data / "livetest" / "runner" / "2026w10" / "state.json").read_text())
    assert state["refresh_attempts"] == 1
    argv = [a for a in env.argv("run", K10 - timedelta(hours=2, minutes=50)) if a != "--skip-refresh"]
    assert env.main_(argv, deps) == runner.EXIT_OK  # past K - 3 h: build on the newest snapshots
    assert env.stub_calls() == 1
    assert "building on the newest existing snapshots" in env.alerts()[-1]["message"]


def test_refresh_that_modifies_a_snapshot_stops_the_run(env):
    victim = next((env.data / "processed" / "dim_game").glob("snapshot=*/season=2026/part-0.parquet"))

    def bad_refresh(cmd, **kwargs):
        if "ffml.data.refresh" in cmd:
            victim.write_bytes(victim.read_bytes() + b"x")
            return subprocess.CompletedProcess(cmd, 0, "REFRESH_RESULT {}\n", "")
        return subprocess.run(cmd, **kwargs)

    argv = [a for a in env.argv("run", K10 - timedelta(hours=9)) if a != "--skip-refresh"]
    assert env.main_(argv, runner.Deps(live_schedule=None, run=bad_refresh)) == runner.EXIT_SNAPSHOT
    assert env.stub_calls() == 0 and env.alerts()[-1]["kind"] == "integrity"


def test_post_push_verify_detects_a_changed_file(env):
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == 0
    c = core.load_commitment(env.commitment_path())
    target = Path(c.summary_entry().path)
    target.write_bytes(target.read_bytes() + b"tampered")
    assert env.main_(env.argv("verify", K10 - timedelta(hours=8))) == runner.EXIT_INTEGRITY


def test_lock_busy_and_safety_refusals(env, monkeypatch):
    lock = env.data / "livetest" / ".runner.lock"
    lock.parent.mkdir(parents=True, exist_ok=True)
    lock.write_text(json.dumps({"pid": 1, "started": iso(datetime.now(UTC))}))
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == runner.EXIT_OK
    assert env.stub_calls() == 0 and '"status": "busy"' in env.logs()
    lock.unlink()
    real = [a for a in env.argv("run", K10 - timedelta(hours=9)) if a not in ("--stub-build", "--allow-stub-push")]
    assert env.main_(real) == runner.EXIT_REFUSED  # --now only for stub or dry runs
    monkeypatch.setenv("FFML_HOLDOUT_UNLOCK", "1")
    assert env.main_(env.argv("run", K10 - timedelta(hours=9))) == runner.EXIT_REFUSED
    assert env.stub_calls() == 0


def test_dry_run_changes_nothing(env):
    assert env.main_(env.argv("run", K10 - timedelta(hours=9), "--dry-run")) == 0
    assert env.stub_calls() == 0 and env.remote_refs() == [] and not env.worktree.exists()


# --------------------------------------------------------------------------- #
# Worktree pin verification
# --------------------------------------------------------------------------- #


@pytest.fixture
def code_repo(tmp_path, monkeypatch):
    for name, value in {"GIT_AUTHOR_NAME": "t", "GIT_AUTHOR_EMAIL": "t@e", "GIT_COMMITTER_NAME": "t",
                        "GIT_COMMITTER_EMAIL": "t@e"}.items():
        monkeypatch.setenv(name, value)
    repo = tmp_path / "code"
    (repo / "ffml").mkdir(parents=True)
    (repo / "ffml" / "__init__.py").write_text("VERSION = 'a'\n")
    (repo / ".gitignore").write_text(".venv/\n__pycache__/\n")
    git("init", "--quiet", "-b", "main", cwd=repo)
    git("add", ".", cwd=repo)
    git("commit", "--quiet", "-m", "a", cwd=repo)
    a = git("rev-parse", "HEAD", cwd=repo)
    (repo / "ffml" / "__init__.py").write_text("VERSION = 'b'\n")
    git("commit", "--quiet", "-am", "b", cwd=repo)
    b = git("rev-parse", "HEAD", cwd=repo)
    wt = tmp_path / "pinned"
    git("worktree", "add", "--quiet", "--detach", str(wt), a, cwd=repo)
    return repo, wt, a, b


def pin(wt, commit, repo, **kw):
    return core.verify_code_pin(wt, commit, sys.executable, main_repo=repo, check_env=False, **kw)


def test_pin_accepts_clean_worktree_at_expected_commit(code_repo):
    repo, wt, a, _ = code_repo
    (wt / ".venv").mkdir()
    (wt / ".venv" / "ignored.txt").write_text("ignored files are fine")
    with pytest.raises(core.PinError, match="outside the worktree"):
        pin(wt, a, repo)  # the test interpreter's editable ffml is the real repo, not the worktree
    result = pin(wt, a[:12], repo, require_editable=False)
    assert result.commit == a and Path(result.ffml_file).resolve().is_relative_to(wt.resolve())


def test_pin_refuses_wrong_commit_dirty_tree_main_checkout_and_foreign_ffml(code_repo):
    repo, wt, a, b = code_repo
    with pytest.raises(core.PinError, match="HEAD is"):
        pin(wt, b, repo, require_editable=False)
    with pytest.raises(core.PinError, match="not a commit sha"):
        pin(wt, "main", repo, require_editable=False)
    with pytest.raises(core.PinError, match="separate checkout"):
        pin(repo, b, repo, require_editable=False)
    (wt / "notes.txt").write_text("untracked")
    with pytest.raises(core.PinError, match="not clean"):
        pin(wt, a, repo, require_editable=False)
    (wt / "notes.txt").unlink()
    shutil.rmtree(wt / "ffml")
    git("-c", "core.autocrlf=false", "update-index", "--assume-unchanged", "ffml/__init__.py", cwd=wt)
    with pytest.raises(core.PinError, match="outside the worktree"):
        pin(wt, a, repo, require_editable=False)


def test_requirements_comparison():
    lock = core.parse_requirements("polars==2.1.0\nPyYAML==6.0.3\n# c\n-e .\n")
    assert core.compare_requirements(lock, core.parse_requirements("polars==2.1.0\npyyaml==6.0.3\npip==24.0\n")) == []
    problems = core.compare_requirements(lock, core.parse_requirements("polars==2.0.0\nextra_pkg==1\n"))
    assert "polars: lock 2.1.0, installed 2.0.0" in problems
    assert any("pyyaml" in p and "not installed" in p for p in problems)
    assert any("extra-pkg" in p for p in problems)


# --------------------------------------------------------------------------- #
# Manifest re-hash verification
# --------------------------------------------------------------------------- #


def make_commitment(tmp_path: Path, **overrides) -> Path:
    out = tmp_path / "out"
    out.mkdir(exist_ok=True)
    (out / "player_summary.parquet").write_bytes(b"summary-bytes")
    (out / "model.bin").write_bytes(b"model-bytes")
    body = {
        "format": "ffml.livetest.commitment/1", "season": 2026, "week": 10, "rehearsal": False,
        "label": "not approved", "created_at": "2026-11-12T16:20:00+00:00",
        "first_kickoff": "2026-11-13T01:15:00+00:00", "code": {"commit": "a" * 40, "clean": True},
        "outputs": [
            {"path": (out / n).as_posix(), "role": "predictions", "sha256": core.sha256_file(out / n),
             "bytes": (out / n).stat().st_size}
            for n in ("player_summary.parquet", "model.bin")
        ],
        "calibration_history": {"sources": [{"path": (out / "model.bin").as_posix(),
                                             "sha256": core.sha256_file(out / "model.bin")}]},
    }
    body.update(overrides)
    body["digest"] = {"algorithm": "sha256", "value": core.canonical_digest(body)}
    path = tmp_path / "commitment.json"
    path.write_text(json.dumps(body, indent=2), encoding="utf-8")
    return path


def test_manifest_rehash_passes_and_catches_changes(tmp_path):
    path = make_commitment(tmp_path)
    c = core.load_commitment(path)
    assert len(c.files) == 3 and c.summary_entry().path.endswith("player_summary.parquet")
    kw = {"roots": [tmp_path], "season": 2026, "week": 10, "kickoff": K10, "expected_commit": "a" * 12}
    ok = core.verify_commitment(c, **kw)
    assert ok.ok, ok.problems
    assert ok.digest["match"]
    assert not core.verify_commitment(c, **{**kw, "week": 11}).ok
    assert not core.verify_commitment(c, **{**kw, "expected_commit": "b" * 40}).ok
    assert not core.verify_commitment(c, **{**kw, "kickoff": datetime(2026, 11, 12, 16, 0, tzinfo=UTC)}).ok
    (tmp_path / "out" / "model.bin").write_bytes(b"changed")
    bad = core.verify_commitment(c, **kw)
    assert not bad.ok and any("does not match the manifest" in p for p in bad.problems)


def test_digest_and_dirty_code_are_failures(tmp_path):
    path = make_commitment(tmp_path, code={"commit": "a" * 40, "clean": False})
    c = core.load_commitment(path)
    v = core.verify_commitment(c, roots=[tmp_path], expected_commit="a" * 40)
    assert not v.ok and any("not clean" in p for p in v.problems)
    data = json.loads(path.read_text())
    data["week"] = 11  # body changed after the digest was computed
    path.write_text(json.dumps(data))
    v = core.verify_commitment(core.load_commitment(path), roots=[tmp_path])
    assert not v.ok and any("digest" in p for p in v.problems)


def test_snapshot_fingerprints(tmp_path):
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(tmp_path / "processed")
    frame = pl.DataFrame({"a": [1, 2]})
    first = store.write_snapshot("t", {2026: frame})
    before = core.fingerprint_snapshots(tmp_path)
    store.write_snapshot("t", {2026: frame})
    after = core.fingerprint_snapshots(tmp_path)
    assert core.compare_fingerprints(before, after) == [] and len(after) == len(before) + 1
    part = store.partition_path("t", first, 2026)
    part.write_bytes(part.read_bytes() + b"!")
    assert core.compare_fingerprints(before, core.fingerprint_snapshots(tmp_path))
    shutil.rmtree(store.snapshot_dir("t", first))
    assert any("removed" in p for p in core.compare_fingerprints(before, core.fingerprint_snapshots(tmp_path)))


def test_branch_name_guard():
    for bad in ("main", "master", "refs/heads/x", "HEAD"):
        with pytest.raises(core.GitError):
            core.CommitmentsRepo(Path("."), Path("x"), branch=bad)


def test_github_remote_parsing():
    assert core.parse_github_remote("https://github.com/o/r.git") == ("o", "r")
    assert core.parse_github_remote("git@github.com:o/r.git") == ("o", "r")
    assert core.parse_github_remote("C:/tmp/remote.git") is None


def test_real_build_week_commitment_format_round_trip(tmp_path):
    """A commitment written by ffml.sim.livetest parses and verifies here, digest included."""
    livetest = pytest.importorskip("ffml.sim.livetest")
    out = tmp_path / "out"
    out.mkdir()
    for name in ("player_summary.parquet", "params.json", "part.parquet"):
        (out / name).write_bytes(name.encode() * 3)
    payload = {
        "format": livetest.COMMITMENT_FORMAT, "season": 2026, "week": 10, "rehearsal": False,
        "label": "not approved", "approval_status": "not approved",
        "created_at": "2026-11-12T16:20:00.123456+00:00", "first_kickoff": K10.isoformat(),
        "code": {"commit": "c" * 40, "clean": True},
        "outputs": livetest.file_records([out / "player_summary.parquet", out / "params.json"], "predictions"),
        "calibration_history": {"sources": [{"season": 2026, "week": 9, "path": livetest.rel(out / "part.parquet"),
                                             "sha256": livetest.sha256_file(out / "part.parquet")}]},
    }
    path = tmp_path / "commitment.json"
    digest = livetest.write_commitment(path, payload)
    c = core.load_commitment(path)
    assert c.digest == digest and c.code_commit == "c" * 40 and c.first_kickoff == K10
    assert len(c.files) == 3 and c.summary_entry() is not None
    v = core.verify_commitment(c, roots=[tmp_path], season=2026, week=10, kickoff=K10, expected_commit="c" * 40)
    assert v.ok, v.problems
    assert v.digest["match"] and livetest.verify_commitment(path) == []
