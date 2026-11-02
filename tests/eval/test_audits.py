"""Tests for the evaluator audits on synthetic logs, a fake repository tree and tiny frames."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl
import pytest

from ffml.config import PROJECT_ROOT, get_settings, reset_settings_cache
from ffml.data.store import SnapshotStore
from ffml.eval.audits import holdout_audit as ha
from ffml.eval.audits import leakage_audit as la
from ffml.eval.audits.report import AuditReport, combined_status, write_report
from ffml.holdout import HoldoutLockedError, assert_seasons_allowed

FLAG = ha.UNLOCK_ENV
ATTR = ha.UNLOCK_ATTR
HOLDOUT = get_settings().holdout_season


def write(root: Path, rel: str, text: str) -> Path:
    path = root / rel
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(text, encoding="utf-8")
    return path


def log_line(ts: datetime, purpose="holdout_eval", git="abc1234", user="tester", seasons=None) -> str:
    seasons = seasons or [HOLDOUT]
    return f"{ts.isoformat()}\tpurpose={purpose}\tseasons={seasons}\tgit={git}\tuser={user}\n"


def item(report: AuditReport, name: str):
    matches = [i for i in report.items if i.name == name]
    assert matches, f"no item {name!r} in {[i.name for i in report.items]}"
    return matches[0]


def run(root: Path, **kw) -> AuditReport:
    return ha.audit_holdout(root, artifacts_dir=root / "artifacts", environ={}, **kw)


# --------------------------------------------------------------------------- #
# Report container
# --------------------------------------------------------------------------- #


def test_report_status_markdown_and_writer(tmp_path):
    r = AuditReport(title="t")
    assert r.status == "SKIP"
    r.add("a", "SKIP", "x")
    assert r.status == "SKIP"
    r.add("b", "WARN", "careful | pipes", ["e1", "e2"])
    assert r.status == "PASS"
    r.add("c", "FAIL", "broken")
    assert r.status == "FAIL" and [i.name for i in r.findings()] == ["b", "c"]
    md = r.to_markdown()
    assert "Overall: **FAIL**" in md and "careful \\| pipes" in md and "- e1" in md
    with pytest.raises(ValueError):
        r.add("d", "MAYBE", "bad status")  # type: ignore[arg-type]
    path = r.write_report("audit_test", reports_dir=tmp_path)
    assert path == (tmp_path / "audit_test.md").resolve() and path.read_text(encoding="utf-8") == md
    combined = write_report("both.md", [r, AuditReport(title="u")], reports_dir=tmp_path, title="All")
    assert "- t: **FAIL**" in combined.read_text(encoding="utf-8")
    ok = AuditReport(title="ok")
    ok.add("x", "PASS", "fine")
    skipped = AuditReport(title="later")
    skipped.add("y", "SKIP", "inputs missing")
    assert combined_status([ok]) == "PASS"
    assert combined_status([ok, skipped]) == "INCOMPLETE"
    assert combined_status([ok, skipped, r]) == "FAIL"
    text = write_report("mixed", [ok, skipped], reports_dir=tmp_path).read_text(encoding="utf-8")
    assert "Overall: **INCOMPLETE**" in text
    for bad in ("../escape", "sub/dir", ""):
        with pytest.raises(ValueError):
            r.write_report(bad, reports_dir=tmp_path)


# --------------------------------------------------------------------------- #
# Holdout audit
# --------------------------------------------------------------------------- #


def test_clean_fake_repo_passes(tmp_path):
    write(tmp_path, "ffml/models/ok.py", "def f(settings):\n    return settings.holdout_season\n")
    report = run(tmp_path)
    assert report.status == "PASS"
    assert "absent" in item(report, "holdout access log").detail


def test_access_log_parsing_and_sessions(tmp_path):
    t0 = datetime(2026, 12, 1, 12, 0, tzinfo=UTC)
    lines = [log_line(t0), log_line(t0 + timedelta(minutes=5)), log_line(t0 + timedelta(minutes=9))]
    write(tmp_path, "artifacts/holdout_access.log", "".join(lines))
    entries, bad = ha.parse_access_log(tmp_path / "artifacts" / "holdout_access.log")
    assert len(entries) == 3 and not bad and entries[0].purpose == "holdout_eval"
    assert len(ha.access_sessions(entries)) == 1
    # before the release run, any access fails
    early = item(run(tmp_path), "holdout access log")
    assert early.status == "FAIL" and len(early.evidence) == 3 and "git=abc1234" in early.evidence[0]
    # in the release run, one holdout_eval session passes
    assert item(run(tmp_path, release_run_allowed=True), "holdout access log").status == "PASS"


@pytest.mark.parametrize(
    "extra",
    [
        log_line(datetime(2026, 12, 2, 12, 0, tzinfo=UTC)),  # second session a day later
        log_line(datetime(2026, 12, 1, 12, 10, tzinfo=UTC), git="def5678"),  # another commit
        log_line(datetime(2026, 12, 1, 12, 10, tzinfo=UTC), purpose="train"),  # wrong purpose
        "not a log line\n",  # malformed
    ],
)
def test_release_run_rejects_second_session_wrong_purpose_or_garbage(tmp_path, extra):
    first = log_line(datetime(2026, 12, 1, 12, 0, tzinfo=UTC))
    write(tmp_path, "artifacts/holdout_access.log", first + extra)
    assert item(run(tmp_path, release_run_allowed=True), "holdout access log").status == "FAIL"


def test_log_written_by_the_real_guard_is_parsed(tmp_settings, monkeypatch):
    monkeypatch.setenv(FLAG, "true")
    reset_settings_cache()
    assert_seasons_allowed([HOLDOUT], purpose="holdout_eval")
    log_path = get_settings().artifacts_dir / ha.LOG_NAME
    entries, bad = ha.parse_access_log(log_path)
    assert len(entries) == 1 and not bad
    assert entries[0].purpose == "holdout_eval" and str(HOLDOUT) in entries[0].seasons
    monkeypatch.delenv(FLAG)
    reset_settings_cache()


def test_literal_holdout_season_scan(tmp_path):
    write(tmp_path, "ffml/features/bad.py", f"HOLDOUT = {HOLDOUT}\nPATH = 'season={HOLDOUT}'\n")
    write(tmp_path, "ffml/sim/doc.py", f'"""Mentions {HOLDOUT} in a docstring."""\nX = 1  # and {HOLDOUT} in a comment\n')
    write(tmp_path, "ffml/models/fine.py", f"YEARS = ({HOLDOUT - 1}, {HOLDOUT + 1}, {HOLDOUT}0)\n")
    write(tmp_path, "ffml/models/test_helpers.py", f"Y = {HOLDOUT}\n")  # test files are exempt
    write(tmp_path, "ffml/data/other.py", f"Y = {HOLDOUT}\n")  # outside the scanned dirs
    found = ha.scan_literals(tmp_path / "ffml/features/bad.py", HOLDOUT)
    assert found == [(1, "code"), (2, "string")]
    assert ha.scan_literals(tmp_path / "ffml/sim/doc.py", HOLDOUT) == [(1, "docstring"), (2, "comment")]
    assert ha.scan_literals(tmp_path / "ffml/models/fine.py", HOLDOUT) == []
    write(tmp_path, "ffml/eval/multi.py", f'"""Title.\n\nThe holdout is {HOLDOUT}.\n"""\n')
    assert ha.scan_literals(tmp_path / "ffml/eval/multi.py", HOLDOUT) == [(3, "docstring")]
    lit = item(run(tmp_path), "literal holdout season")
    assert lit.status == "FAIL"
    assert any(e.startswith("ffml/features/bad.py:1") for e in lit.evidence)
    assert not any("test_helpers" in e or "ffml/data" in e for e in lit.evidence)
    (tmp_path / "ffml/features/bad.py").unlink()
    assert item(run(tmp_path), "literal holdout season").status == "WARN"


def test_unlock_assignment_scan(tmp_path):
    bad = (
        "import os\n"
        f"os.environ['{FLAG}'] = 'true'\n"
        f"settings.{ATTR} = True\n"
        f"s = Settings({ATTR}=True)\n"
        f"env = {{'{FLAG}': '1'}}\n"
        f"os.environ.setdefault('{FLAG}', '1')\n"
        f"setattr(settings, '{ATTR}', True)\n"
        f"if settings.{ATTR}:\n    pass\n"  # reading is fine
    )
    write(tmp_path, "ffml/sim/sneaky.py", bad)
    hits = ha.scan_unlock_assignments(tmp_path / "ffml/sim/sneaky.py")
    assert [line for line, _ in hits] == [2, 3, 4, 5, 6, 7]
    # the guard, its setting and tests may set it
    write(tmp_path, "ffml/holdout.py", f"{ATTR} = True\n")
    write(tmp_path, "ffml/config.py", f"class S:\n    {ATTR}: bool = False\n")
    write(tmp_path, "tests/test_x.py", f"def test(monkeypatch):\n    monkeypatch.setenv('{FLAG}', 'true')\n")
    write(tmp_path, "scripts/run.sh", f"export {FLAG}=true\npython -m x\n")
    write(tmp_path, ".github/workflows/ci.yml", f"env:\n  {FLAG}: 'true'\n")
    found = item(run(tmp_path), "unlock assignments")
    assert found.status == "FAIL"
    files = {e.split(":")[0] for e in found.evidence}
    assert files == {"ffml/sim/sneaky.py", "scripts/run.sh", ".github/workflows/ci.yml"}


def test_env_file_and_process_environment(tmp_path):
    assert item(run(tmp_path), ".env unlock").status == "PASS"
    write(tmp_path, ".env", f"FFML_LOG_LEVEL=DEBUG\nexport {FLAG}=\"True\"\n")
    assert item(run(tmp_path), ".env unlock").status == "FAIL"
    write(tmp_path, ".env", f"{FLAG}=false\n")
    assert item(run(tmp_path), ".env unlock").status == "WARN"
    write(tmp_path, ".env", "FFML_LOG_LEVEL=DEBUG\n")
    assert item(run(tmp_path), ".env unlock").status == "PASS"
    report = ha.audit_holdout(tmp_path, artifacts_dir=tmp_path / "artifacts", environ={FLAG: "1"})
    assert item(report, "process environment").status == "FAIL"


def test_real_repository_eval_code_is_clean():
    report = ha.audit_holdout(PROJECT_ROOT, environ={})
    names = [i.name for i in report.items]
    assert names[:4] == ["holdout access log", "literal holdout season", "unlock assignments", ".env unlock"]
    own = [e for i in report.items for e in i.evidence if e.startswith("ffml/eval/")]
    assert own == []


# --------------------------------------------------------------------------- #
# Leakage audit
# --------------------------------------------------------------------------- #


def test_leakage_suite_skip_pass_fail(tmp_path):
    report = AuditReport(title="x")
    la.run_leakage_suite(report, tmp_path)
    assert report.items[-1].status == "SKIP"
    write(tmp_path, la.LEAKAGE_TEST, "def test_ok():\n    assert True\n")
    la.run_leakage_suite(report, tmp_path)
    assert report.items[-1].status == "PASS" and "1 passed" in report.items[-1].detail
    write(tmp_path, la.LEAKAGE_TEST, "def test_bad():\n    assert False\n")
    la.run_leakage_suite(report, tmp_path)
    assert report.items[-1].status == "FAIL" and "1 failed" in report.items[-1].detail


def test_summary_line():
    out = "....\n=== 4 passed, 1 skipped in 0.12s ===\n"
    assert la.summary_line(out) == "4 passed, 1 skipped in 0.12s"


def features_frame(n_weeks=3, players=12, seed=0) -> pl.DataFrame:
    """Tiny player_week frame obeying D-046: cutoff = first kickoff of the week, asof = cutoff - 1h."""
    rng = np.random.default_rng(seed)
    rows = []
    for season in (2022, 2023):
        for week in range(1, n_weeks + 1):
            thursday = datetime(season, 9, 8, 0, 15, tzinfo=UTC) + timedelta(days=7 * (week - 1))
            for p in range(players):
                kickoff = thursday + (timedelta(days=3, hours=17) if p % 4 else timedelta(0))
                y = float(rng.gamma(3.0, 4.0))
                rows.append({
                    "player_id": f"P{p:03d}",
                    "season": season,
                    "week": week,
                    "cutoff_ts": thursday,
                    "asof_ts": thursday - timedelta(hours=1),
                    "kickoff_utc": kickoff,
                    "opp__targets__l3": float(rng.normal(5, 2)),
                    "eff__ypt__ew": float(rng.normal(8, 1)),
                    "attr__age": float(rng.uniform(21, 34)),
                    "y_targets": y,
                    "y_rec_yds": y * 8 + float(rng.normal(0, 10)),
                })
    return pl.DataFrame(rows)


def test_check_timestamps_counts_violations():
    frame = features_frame()
    counts = la.check_timestamps(frame)
    assert counts["rows"] == frame.height
    assert all(v == 0 for k, v in counts.items() if k != "rows")
    bad = frame.with_row_index().with_columns(
        pl.when(pl.col("index") == 0).then(pl.col("kickoff_utc") + timedelta(hours=2))
        .otherwise(pl.col("asof_ts")).alias("asof_ts"),
        pl.when(pl.col("index") == 1).then(pl.col("cutoff_ts") + timedelta(minutes=30))
        .otherwise(pl.col("cutoff_ts")).alias("cutoff_ts"),
    ).drop("index")
    counts = la.check_timestamps(bad)
    assert counts["asof_after_kickoff"] == 1
    assert counts["asof_after_cutoff"] == 1
    assert counts["cutoff_not_week_first_kickoff"] == 1


def test_probe_flags_target_copies():
    frame = features_frame(n_weeks=6, players=20)
    clean = la.probe_target_copies(frame)
    assert clean["flagged"] == [] and clean["features"] == 3 and clean["targets"] == 2
    assert clean["max_abs_corr"] < 0.5
    leaky = frame.with_columns(
        pl.col("y_targets").cast(pl.Float32).alias("opp__targets__l1"),
        (pl.col("y_rec_yds") * 2 + 1).alias("team__yds__l1"),
    )
    res = la.probe_target_copies(leaky)
    pairs = {(f, t, e) for f, t, _, e in res["flagged"]}
    assert ("opp__targets__l1", "y_targets", True) in pairs
    assert ("team__yds__l1", "y_rec_yds", False) in pairs
    assert res["shuffled_max_abs_corr"] < 0.5
    allowed = la.probe_target_copies(leaky, allowlist={"opp__targets__l1", "team__yds__l1"})
    assert allowed["flagged"] == []


@pytest.fixture
def feature_store(tmp_settings, monkeypatch):
    monkeypatch.delenv(FLAG, raising=False)
    reset_settings_cache()
    settings = get_settings()
    frame = features_frame()
    SnapshotStore(settings.features_dir).write_snapshot(
        "player_week", {s: frame.filter(pl.col("season") == s) for s in (2022, 2023)}
    )
    yield settings
    reset_settings_cache()


def test_audit_leakage_end_to_end(feature_store, tmp_path):
    report = la.audit_leakage(root=tmp_path)
    assert item(report, "leakage suite").status == "SKIP"
    ts = item(report, "timestamp audit")
    assert ts.status == "PASS" and "player_week itself" in ts.detail
    assert item(report, "shuffle probe").status == "PASS"
    assert report.status == "PASS"


def test_audit_leakage_skips_without_features_and_guards_holdout(feature_store, tmp_path):
    empty = la.audit_leakage(features_root=tmp_path / "nothing", root=tmp_path)
    assert empty.status == "SKIP"
    assert item(empty, "timestamp audit").status == "SKIP"
    with pytest.raises(HoldoutLockedError):
        la.audit_leakage(root=tmp_path, seasons=(2024, HOLDOUT))
