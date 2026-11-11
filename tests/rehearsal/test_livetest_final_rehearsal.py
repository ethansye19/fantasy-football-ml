"""End-to-end rehearsal of the live-test scorer's verdict path on synthetic data (QA, D-115, D-116 C).

Run it explicitly (about two and a half hours with three worlds at a time)::

    FFML_REHEARSAL=1 pytest -m slow tests/rehearsal -q

``FFML_REHEARSAL_COMMIT`` names the frozen commit X (default 071c3a4, D-125),
``FFML_REHEARSAL_BASE`` the commit each clone starts from (Z, a descendant of X; default X),
``FFML_REHEARSAL_WORKERS`` the number of worlds run at a time (default 3),
``FFML_REHEARSAL_ONLY`` a comma-separated subset of worlds, and
``FFML_REHEARSAL_EVIDENCE`` a file for the JSON evidence behind
reports/livetest_final_rehearsal.md. ``FFML_REHEARSAL_5000=1`` adds the real
5,000-draw final through the CLI default (about 1.5 hours more of one core).

Every world is built from scratch in a temporary directory: a clone of the
repository pinned at X, a temporary bare repository as ``origin`` holding the
fake ``livetest-2026`` branch, and synthetic stores on a simulated calendar under
the clone's ignored ``data/`` and ``artifacts/``. The scorer always runs from the
clone (asserted in every subprocess); nothing touches the real data, artifacts,
branch, registry or access log, and the holdout unlock flag is never set.

Scenarios: the first rehearsal's (PASS; FAIL on L17 and on L07; 0 to 3 missed
weeks; sealed results; the change log and the commit relation; the report-only
5,000-draw rule) plus D-116 C's (a blocked build, a stat correction after the
cutoff, a failed final fetch, flagged markers, the 2027 boundary of the miss
count) and rechecks of QA-F27 to F30; D-121 (world G: a game never final, a
game final before the 14 days, the runner's score-week). Every result is also
appended to ``FFML_REHEARSAL_EVIDENCE``.partial.jsonl as soon as it exists.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from datetime import timedelta
from pathlib import Path

import _rehearsal_scenarios as S
import _rehearsal_world as W
import pytest

pytestmark = pytest.mark.slow

APPROVED, NOT_APPROVED = "Verdict: APPROVED", "Verdict: NOT APPROVED"
#: the candidate's bootstrap: line frames only from 2872d13 (D-118), the full computation at bd3bf21
EXPECT_SUBSET = os.environ.get("FFML_REHEARSAL_EXPECT_SUBSET",
                               "0" if W.FROZEN_CANDIDATE.startswith("bd3bf21") else "1") == "1"
ALL_LINES = [f"L{i:02d}" for i in range(1, 23)]
RUNNER_EXIT_BLOCKED = 2  # scripts/livetest_week.py EXIT_BLOCKED


def _candidate_known() -> bool:
    for rev in (W.FROZEN_CANDIDATE, W.REHEARSAL_BASE):
        out = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{rev}^{{commit}}"],
                             cwd=W.MAIN_REPO, capture_output=True, text=True)
        if out.returncode != 0:
            return False
    return True


LAST = W.week_key(*W.LAST_WEEK)
AFTER_LAST = W.week_key(W.LAST_WEEK[0], W.LAST_WEEK[1] + 1)
FIRST = W.week_key(*W.FIRST_WEEK)


if os.environ.get("FFML_REHEARSAL") != "1":
    pytest.skip("the rehearsal runs only with FFML_REHEARSAL=1 (about two and a half hours)", allow_module_level=True)
if not _candidate_known():
    pytest.skip(f"candidate commit {W.FROZEN_CANDIDATE} is not in this checkout", allow_module_level=True)


@pytest.fixture(scope="module")
def results(tmp_path_factory):
    root = tmp_path_factory.mktemp("rehearsal")
    only = set(filter(None, os.environ.get("FFML_REHEARSAL_ONLY", "").split(","))) or None
    res = S.run_all(root, workers=int(os.environ.get("FFML_REHEARSAL_WORKERS", "3")), only=only)
    out = os.environ.get("FFML_REHEARSAL_EVIDENCE")
    if out:
        Path(out).parent.mkdir(parents=True, exist_ok=True)
        Path(out).write_text(S.evidence_json({"generated": S.utcnow(), "quick": S.QUICK, "root": str(root),
                                               "candidate": W.FROZEN_CANDIDATE, "worlds": res}), encoding="utf-8")
    return res


def world(results, name):
    if name not in results:
        pytest.skip(f"{name} not run (FFML_REHEARSAL_ONLY or FFML_REHEARSAL_5000)")
    ev = results[name]
    assert "error" not in ev, f"{name} failed: {ev.get('error')}\n{ev.get('traceback', '')[-4000:]}"
    return ev


def single_verdict(fin):
    """Exactly one Verdict line, the same on stdout and in the final report; returns it."""
    assert fin["proc"]["returncode"] == 0, fin["proc"]["stderr"][-3000:]
    assert len(fin["verdict_stdout"]) == 1, fin["proc"]["stdout"]
    assert fin["final_md"].count("Verdict:") == 1
    assert fin["verdict_md"] == fin["verdict_stdout"]
    return fin["verdict_stdout"][0]


def refused(fin, code=3):
    """No verdict, no report, nothing in the sealed directory; returns the refusal text."""
    assert fin["proc"]["returncode"] == code, (fin["proc"]["returncode"], fin["proc"]["stderr"][-2000:])
    assert fin["verdict_stdout"] == [] and "final_md" not in fin
    assert fin["sealed_dir_changes"] == []
    return fin["proc"]["stderr"]


def check(checks, name):
    rows = [c for c in checks if (c["name"] if isinstance(c, dict) else c[0]) == name]
    assert rows, f"no check {name!r}"
    return rows[0]


def common_world_asserts(ev):
    """The clone, not the main checkout, ran; the frozen code equals X; every scored week verified clean."""
    probe = ev["probe"]
    clone = Path(ev["root"]) / "clone"
    assert Path(probe["ffml_file"]).resolve().is_relative_to(clone.resolve())
    assert Path(probe["project_root"]).resolve() == clone.resolve()
    assert Path(probe["data_dir"]).resolve().is_relative_to(clone.resolve())
    assert not probe["holdout_unlock"] and probe["bootstrap_line_frames_only"] is EXPECT_SUBSET
    assert probe["scorer_cutoffs_2026"] == probe["world_cutoffs_2026"]
    assert ev["diff_x_head_frozen_code"] == "", ev["diff_x_head"]
    assert ev["window"] == ev["prereg_window"] and len(ev["window"]) == 18
    for step in ev["weekly"]:
        if step.get("required", True):
            assert step["exit"] == 0, {k: v for k, v in step.items() if k not in ("snapshots",)}
        if step["op"] == "push" and step["exit"] == 0:
            assert step["post_push_verify_ok"] is True and step["ffml_livetest_verify_rc"] == 0, step
            assert step["push_record"]["on_time"] is True
            calls = step.get("scorer_calls") or []
            assert any(c["args"][:1] == ["--prebuild-check"] and c["returncode"] == 0 for c in calls), step
        if step["op"] == "score-hook" and step["exit"] == 0:
            assert step["failed"] == [], step
    scored = [k for k, v in ev["sealed"]["weeks"].items() if v["local_sha256_file"]]
    for wk in scored:
        info = ev["sealed"]["weeks"][wk]
        # the runner publishes the scorer's sealed file verbatim where the final reads it
        assert info["branch_record"] is not None and info["branch_record"].strip() == info["local_sha256_file"].strip()
        assert info["report"] and info["status_file"] and info["missed_marker"] is None
        assert info["bootstrap_frames"] == ("lines" if EXPECT_SUBSET else "full")
        assert info["data_cutoff"] and info["outcome_rows_sha256"] and info["data_snapshots"]
        # the sealed file lists the part, the week's consensus rows and the record (up to three files)
        names = sorted(line.split()[-1] for line in info["local_sha256_file"].splitlines() if line.strip())
        assert names == sorted([f"{wk}.json", f"{wk}.parquet", f"{wk}_consensus.parquet"]), names
    return scored


def _excluded(fin):
    m = re.search(r"Weeks scored: (.*?)(?:; excluded \(miss rule\): (.*?))?\.\n", fin["final_md"])
    assert m, "no 'Weeks scored' line"
    return m.group(1).split(", "), (m.group(2) or "").split(", ") if m.group(2) else []


# --------------------------------------------------------------------------- #
# 1. PASS
# --------------------------------------------------------------------------- #


def test_pass_world_is_approved(results):
    ev = world(results, "A_pass")
    scored = common_world_asserts(ev)
    assert len(scored) == 18
    fin = ev["final_pass"]
    assert single_verdict(fin) == APPROVED
    assert fin["lines"] == {lid: "pass" for lid in ALL_LINES}
    assert all(c[1] == "PASS" for c in fin["checks"]), [c for c in fin["checks"] if c[1] != "PASS"]
    names = {c[0] for c in fin["checks"]}
    assert {"approval lines", "rules hash", "bootstrap and seeds", "frozen commit", "allowed paths",
            "final evaluation commit", "final evaluation change log", "window complete", "missed weeks (miss rule)",
            "branch record", "MISSED markers consistent with the branch"} <= names
    for kind in ("sealed hashes", "commitment", "branch commitment", "change log", "sealed part"):
        assert sum(1 for n in names if n.endswith(" " + kind) and re.match(r"^\d{4}w\d\d ", n)) == 18, kind
    rep = ev["final_pass_repeat"]  # the consensus archive and the fact tables were moved away for this run
    assert rep["stores_moved_away"] == ["restricted", "fact_player_game", "fact_dst_game"]
    assert single_verdict(rep) == APPROVED
    assert rep["final_lines"]["binding"] == fin["final_lines"]["binding"]


# --------------------------------------------------------------------------- #
# 2. FAIL: one line made to fail
# --------------------------------------------------------------------------- #


def test_qb_intervals_too_narrow_fail_l17_only(results):
    ev = world(results, "B_qb_narrow")
    common_world_asserts(ev)
    fin = ev["final"]
    assert single_verdict(fin) == NOT_APPROVED
    assert all(c[1] == "PASS" for c in fin["checks"])
    assert {k for k, v in fin["lines"].items() if v != "pass"} == {"L17"}
    l17 = next(ln for ln in fin["final_lines"]["binding"]["lines"] if ln["id"] == "L17")
    assert "outside [0.75, 0.85]" in l17["detail"]


def test_one_accuracy_line_fails_l07_only(results):
    ev = world(results, "C_accuracy_tie")
    common_world_asserts(ev)
    fin = ev["final"]
    assert single_verdict(fin) == NOT_APPROVED
    assert {k for k, v in fin["lines"].items() if v != "pass"} == {"L07"}
    l07 = next(ln for ln in fin["final_lines"]["binding"]["lines"] if ln["id"] == "L07")
    assert "does not beat `prior_season+cal` at WR" in l07["detail"]


def test_rebuilt_world_gives_identical_results(results):
    a, b = world(results, "B_qb_narrow"), world(results, "B2_qb_narrow_repeat")
    assert single_verdict(a["final"]) == single_verdict(b["final"])
    assert a["final"]["final_lines"]["binding"] == b["final"]["final_lines"]["binding"]
    for wk, info in a["sealed"]["weeks"].items():
        other = b["sealed"]["weeks"][wk]
        assert (info["part_sha256"], info["outcome_rows_sha256"], info["data_snapshots"]) == \
            (other["part_sha256"], other["outcome_rows_sha256"], other["data_snapshots"]), wk
    sa = [s.get("commitment_sha256") for s in a["weekly"] if s["op"] == "push"]
    sb = [s.get("commitment_sha256") for s in b["weekly"] if s["op"] == "push"]
    assert sa == sb and len(sa) == 18


# --------------------------------------------------------------------------- #
# 3. Missed weeks, and the miss count across the season boundary
# --------------------------------------------------------------------------- #


def test_zero_misses(results):
    fin = world(results, "A_pass")["final_pass"]
    scored, excluded = _excluded(fin)
    assert len(scored) == 18 and excluded == []
    assert "0 missed week(s) [], 2 allowed" in check(fin["checks"], "missed weeks (miss rule)")[2]


@pytest.mark.parametrize("name,missed", [("D1_one_miss", ["2026w12"]), ("D2_two_misses", ["2026w12", LAST])])
def test_one_or_two_misses_are_excluded_without_extension(results, name, missed):
    ev = world(results, name)
    scored = common_world_asserts(ev)
    assert sorted(scored) == sorted(W.week_key(s, w) for s, w in W.WINDOW if W.week_key(s, w) not in missed)
    for i, wk in enumerate(missed, start=1):
        marker = json.loads(ev["sealed"]["weeks"][wk]["missed_marker"])
        assert marker["reason"]["category"] == "machine_offline" and marker["allowed"] == 2
        assert marker["miss_number"] == i and marker["test_status"].startswith("VALID")
    fin = ev["final"]
    assert single_verdict(fin) == APPROVED
    got_scored, excluded = _excluded(fin)
    assert excluded == missed and len(got_scored) == 18 - len(missed)
    assert AFTER_LAST not in fin["final_md"]
    detail = check(fin["checks"], "missed weeks (miss rule)")[2]
    assert f"{len(missed)} missed week(s)" in detail and "excluded, the window is not extended" in detail
    assert check(fin["checks"], "window complete")[1] == "PASS"


def test_final_refused_until_last_week_final_even_when_it_is_the_miss(results):
    res = world(results, "D2_two_misses")["last_week_not_final"]
    assert f"the final is refused until {LAST} is final in dim_game" in refused(res)


def test_three_misses_across_seasons_are_invalid_in_scorer_and_runner(results):
    ev = world(results, "D3_three_misses")
    common_world_asserts(ev)
    v = single_verdict(ev["final"])
    assert v.startswith("Verdict: INVALID (") and "missed weeks (miss rule): 3 missed week(s)" in v
    assert "more than allowed: INVALID" in v and ev["final"]["lines"] == {}
    assert "Not computed: an integrity check failed." in ev["final"]["final_md"]
    # QA-F30: the runner counts across the season boundary
    m3 = json.loads(ev["sealed"]["weeks"]["2027w03"]["missed_marker"])
    assert m3["miss_number"] == 3 and m3["test_status"].startswith("INVALID: miss 3 of 2")
    assert set(m3["earlier_misses"]) == {"2026w12", "2026w16"}
    assert "Test state: INVALID" in ev["status_md"]


# --------------------------------------------------------------------------- #
# 4. Sealed results, the final fetch and flagged markers
# --------------------------------------------------------------------------- #


def test_weekly_outputs_hold_no_metrics_and_only_sealed_files(results):
    ev = world(results, "A_pass")
    assert all(v == [] for v in ev["metric_leaks"].values()), {k: v for k, v in ev["metric_leaks"].items() if v}
    assert len([k for k in ev["metric_leaks"] if k.startswith("reports/livetest/")]) == 18
    assert len([k for k in ev["metric_leaks"] if k.startswith("data/livetest/scoring/weeks/")]) == 18
    d1 = world(results, "D1_one_miss")
    for step in (s for s in d1["weekly"] if s["op"] == "score-cli"):
        wk = W.week_key(step["season"], step["week"])
        assert step["data_written"] == sorted([f"livetest/scoring/sealed/{wk}.json", f"livetest/scoring/sealed/{wk}.parquet",
                                               f"livetest/scoring/sealed/{wk}.sha256",
                                               f"livetest/scoring/sealed/{wk}_consensus.parquet",
                                               f"livetest/scoring/weeks/{wk}.json"])
        assert step["reports_written"] == [f"livetest/{wk}_verification.md"]
        assert step["clone_reports_written"] == []  # QA-F27: nothing in the checkout
        assert step["artifacts_written"] in ([], ["holdout_access.log"])
        assert W.metric_leaks(step["stdout"]) == [], step["stdout"]
        assert "Verdict" not in step["stdout"] + step["stderr"]
    for wk, eq in d1["hook_cli_equivalence"].items():
        assert eq["cli_part_sha256"] == eq["hook_part_sha256"], wk
        assert eq["cli_consensus_sha256"] == eq["hook_consensus_sha256"], wk
        assert eq["cli_outcome_rows_sha256"] == eq["hook_outcome_rows_sha256"], wk


def test_weekly_sealed_records_hold_the_metrics_and_both_draw_counts(results):
    ev = world(results, "A_pass")
    sealed = Path(ev["root"]) / "clone" / "data" / "livetest" / "scoring" / "sealed"
    weeks = []
    for s, w in W.WINDOW:
        rec = json.loads((sealed / f"{W.week_key(s, w)}.json").read_text(encoding="utf-8"))
        m = rec["sealed_metrics"]
        assert len(m["week_lines"]) == 22 and len(m["cumulative_lines_500"]) == 22
        assert m["cumulative_lines_sensitivity"]["n_boot"] == 5  # the hook's draw count (the runner's CLI: 5,000)
        weeks.append(len(m["cumulative_weeks"]))
        assert all(c["ok"] for c in rec["checks"]), [c for c in rec["checks"] if not c["ok"]]
    assert weeks == list(range(1, 19))


def test_final_refuses_tampered_sealed_files_and_a_missing_branch_record(results):
    sc = world(results, "A_pass")["sealed_checks"]
    for key, needle in (("tampered_part", "2026w14 sealed hashes: 2026w14.parquet sha256 differs from the branch record"),
                        ("tampered_record", "2026w11 sealed hashes: 2026w11.json sha256 differs from the branch record"),
                        ("missing_branch_record", f"{LAST} sealed hashes: scoring/{LAST}.sha256 is not on the branch")):
        err = refused(sc[key])
        assert "refused: sealed files do not match the branch record" in err and needle in err, (key, err[-2000:])
    assert f"scoring/{LAST}.sha256" in sc["missing_branch_record_ref"]["files_removed"].split()


def test_failed_final_fetch_records_nothing_and_a_retry_gives_the_verdict(results):
    ev = world(results, "A_pass")
    r = ev["sealed_checks"]["fetch_failed"]
    err = refused(r, code=75)
    assert "retry later: the final is refused: git fetch origin livetest-2026 failed" in err
    assert r["final_md_exists"] is False
    assert single_verdict(ev["final_pass"]) == APPROVED  # the retry, once the fetch works


def test_worktree_copy_is_not_trusted_any_more(results):
    """QA-F29 recheck: the final reads the fetched ref, so the worktree's uncommitted copy cannot vouch for files."""
    r = world(results, "A_pass")["sealed_checks"]["worktree_tamper"]
    assert f"scoring/{FIRST}.sha256" in r["worktree_status"]
    assert all(c["ok"] for c in r["seals_by_mode"]["worktree"])        # the worktree copy alone would accept it
    assert not all(c["ok"] for c in r["seals_by_mode"]["git_only"])
    assert f"{FIRST} sealed hashes" in refused(r) and r["restored_status"] == ""


def test_stray_marker_is_flagged_with_week_and_time_and_changes_nothing(results):
    ev = world(results, "A_pass")
    r = ev["stray_marker"]
    fin = r["final"]
    assert single_verdict(fin) == APPROVED
    assert fin["final_lines"]["binding"] == ev["final_pass"]["final_lines"]["binding"]
    assert fin["lines"] == {lid: "pass" for lid in ALL_LINES}
    scored, excluded = _excluded(fin)
    assert len(scored) == 18 and excluded == []
    rows = fin["flagged"]
    assert len(rows) == 1 and rows[0][0] == "2026w13" and rows[0][1].startswith(r["marker_commit"][:12])
    assert rows[0][2].startswith("2027-11-26T09:05:00") and rows[0][3] == r["marker_created_at"]
    assert "2026w13" in check(fin["checks"], "MISSED markers consistent with the branch")[2]
    assert fin["final_lines"]["flagged_markers"][0]["week"] == "2026w13"


def test_marker_attack_is_refused(results):
    """QA-F28 recheck: a committed, sealed week with its local files removed refuses the final."""
    r = world(results, "A_pass")["stray_marker"]["attack_local_files_removed"]
    err = refused(r)
    assert "weeks with a commitment or sealed record on the branch but no local sealed record: ['2026w13']" in err


# --------------------------------------------------------------------------- #
# 5 and 6. Change log and commit relation
# --------------------------------------------------------------------------- #


def test_change_log_only_commits_need_no_entry(results):
    ev = world(results, "A_pass")
    fin = ev["final_pass"]
    log = ev["git_scenarios"]["valid_base"]["log"].splitlines()
    assert len(log) == 4  # F (fill), L1 (log F), A (scripts/, logged), L2 (log A)
    assert "every commit in X..HEAD is logged" in check(fin["checks"], "final evaluation change log")[2]
    assert ev["commits"]["L2_log_A"].startswith(check(fin["checks"], "final evaluation commit")[2].split("HEAD ")[1][:12])
    # A2: every build (at L1) had each commit since X in the change log as it stood at the build commit
    for c in fin["checks"]:
        if c[0].endswith(" change log") and re.match(r"^\d{4}w\d\d ", c[0]):
            assert c[1] == "PASS" and "was in the change log at the build commit" in c[2], c


@pytest.mark.parametrize("name,failing,needle", [
    ("fill_unlogged", "final evaluation change log", "fill"),
    ("missing_entry", "final evaluation change log", "scripts_x"),
    ("edit_entry", "final evaluation change log", "edit"),
    ("delete_entry", "final evaluation change log", "delete"),
    ("fill_placeholder", "final evaluation change log", "fill_placeholder"),
    ("log_plus_other_path", "final evaluation change log", "log_plus_doc"),
    ("merge", "final evaluation change log", "merge"),
    ("frozen_path", "final evaluation commit", "log_frozen"),
])
def test_post_freeze_commit_scenarios_are_invalid(results, name, failing, needle):
    r = world(results, "A_pass")["git_scenarios"][name]
    v = single_verdict(r)
    assert v.startswith("Verdict: INVALID ("), v
    failed = [c for c in r["checks"] if c[1] != "PASS"]
    assert [c[0] for c in failed] == [failing], failed
    assert r["commits"][needle][:12] in failed[0][2], failed
    assert r["lines"] == {}


def test_delete_and_merge_details(results):
    ev = world(results, "A_pass")
    assert ev["commits"]["A_logged_change"][:12] in check(ev["git_scenarios"]["delete_entry"]["checks"],
                                                          "final evaluation change log")[2]
    m = ev["git_scenarios"]["merge"]
    detail = check(m["checks"], "final evaluation change log")[2]
    assert m["commits"]["merge"][:12] in detail and m["commits"]["side_append"][:12] not in detail
    assert "ffml/models/x.py" in single_verdict(ev["git_scenarios"]["frozen_path"])


# --------------------------------------------------------------------------- #
# D-116 C 1. Blocked builds (the runner's pre-build check, the real --prebuild-check CLI)
# --------------------------------------------------------------------------- #


def _steps(ev, tag):
    return [s for s in ev["weekly"] if s.get("tag") == tag]


def test_unlogged_change_blocks_the_build_until_logged_and_is_not_a_miss(results):
    ev = world(results, "P_prebuild")
    common_world_asserts(ev)
    blocked = _steps(ev, "blocked_then_fixed")[0]
    assert [a["exit"] for a in blocked["attempts"]] == [RUNNER_EXIT_BLOCKED, RUNNER_EXIT_BLOCKED]
    for a in blocked["attempts"]:
        assert [x["kind"] for x in a["alerts"]] == ["prebuild_blocked"] and a["built"] == []
        assert ev["labels"]["U"][:12] in json.dumps(a["alerts"])
    calls = [c for c in blocked["scorer_calls"] if c["args"][:1] == ["--prebuild-check"]]
    assert len(calls) == 2 and all(c["returncode"] == 5 for c in calls)  # the real CLI: exit 5 = blocked
    out = json.loads(calls[0]["stdout"].strip().splitlines()[-1])
    assert {"ok", "uncovered", "frozen", "detail", "from", "to", "x", "commits"} <= set(out)
    assert out["ok"] is False and out["uncovered"] == [ev["labels"]["U"]] and out["x"] == ev["x"]
    after = _steps(ev, "after_fix")[0]
    assert after["exit"] == 0 and len(after["attempts"]) == 1 and after["attempts"][0]["built"]
    info = ev["sealed"]["weeks"]["2026w11"]
    assert info["missed_marker"] is None and info["local_sha256_file"]
    assert info["code_commit"] == ev["labels"]["log:U"]  # built at the commit that logged U


def test_change_left_unlogged_past_the_deadline_is_a_miss(results):
    ev = world(results, "P_prebuild")
    blocked = _steps(ev, "blocked_past_deadline")[0]
    assert [a["exit"] for a in blocked["attempts"]] == [RUNNER_EXIT_BLOCKED] * 3
    marker = json.loads(ev["sealed"]["weeks"]["2026w13"]["missed_marker"])
    assert marker["reason"]["category"] == "technical_failure" and marker["reason"]["detail"] == "pre-build check blocked"
    assert marker["miss_number"] == 1


def test_frozen_path_change_blocks_the_build(results):
    ev = world(results, "P_prebuild")
    blocked = _steps(ev, "blocked_frozen")[0]
    assert blocked["exit"] == RUNNER_EXIT_BLOCKED
    alert = blocked["attempts"][0]["alerts"][0]
    assert alert["kind"] == "prebuild_blocked" and "touching a frozen path" in alert["message"]
    assert "0 commit(s) without a change-log entry" in alert["message"]  # the change was logged; the path decides
    after = _steps(ev, "after_reset")[0]
    assert after["exit"] == 0 and ev["sealed"]["weeks"]["2026w16"]["local_sha256_file"]


def test_prebuild_world_final_scores_the_fixed_weeks_and_excludes_the_miss(results):
    ev = world(results, "P_prebuild")
    fin = ev["final"]
    assert single_verdict(fin) == APPROVED
    scored, excluded = _excluded(fin)
    assert excluded == ["2026w13"] and "2026w11" in scored and "2026w16" in scored
    assert all(c[1] == "PASS" for c in fin["checks"])
    assert check(fin["checks"], "2026w11 change log")[1] == "PASS"


# --------------------------------------------------------------------------- #
# D-116 C 2. A stat correction after the data cutoff
# --------------------------------------------------------------------------- #


def test_week_cli_exits_4_before_the_cutoff_and_before_a_snapshot_at_or_after_it(results):
    ev = world(results, "E_stat_correction")
    before, early = _steps(ev, "before_cutoff")[0], _steps(ev, "no_snapshot_yet")[0]
    assert before["exit"] == 4 and "retry later: the data cutoff for 2027w01 is 2027-09-17T12:00:00+00:00" in before["stderr"]
    assert early["exit"] == 4 and "no snapshot at or after the data cutoff" in early["stderr"]
    assert before["data_written"] == [] and early["data_written"] == []


def test_data_cutoff_cli(results):
    ev = world(results, "E_stat_correction")
    unscheduled, scheduled = _steps(ev, "data_cutoff_unscheduled")[0], _steps(ev, "data_cutoff_scheduled")[0]
    assert unscheduled["exit"] == 4 and "has no regular-season game in dim_game" in unscheduled["stderr"]
    assert scheduled["exit"] == 0
    assert json.loads(scheduled["stdout"].strip().splitlines()[-1])["data_cutoff_utc"] == "2027-01-15T12:00:00+00:00"


def test_weekly_scoring_pins_the_first_snapshot_at_or_after_the_cutoff(results):
    ev = world(results, "E_stat_correction")
    common_world_asserts(ev)
    ids = ev["snapshot_ids"]["processed/fact_player_game"]
    for s, w in W.WINDOW:
        info = ev["sealed"]["weeks"][W.week_key(s, w)]
        cutoff = W.data_cutoff(s, w).strftime("%Y%m%dT%H%M%SZ")
        first = min(i for i in ids if i >= cutoff)
        assert set(info["data_snapshots"].values()) == {first}, (s, w, info["data_snapshots"])
        assert any(i < cutoff for i in ids)  # an earlier (preliminary) snapshot existed and was not used


def test_corrections_never_reach_the_sealed_weeks_or_the_final(results):
    a, e = world(results, "A_pass"), world(results, "E_stat_correction")
    for wk, info in a["sealed"]["weeks"].items():
        assert e["sealed"]["weeks"][wk]["outcome_rows_sha256"] == info["outcome_rows_sha256"], wk
        assert e["sealed"]["weeks"][wk]["part_sha256"] == info["part_sha256"], wk
    assert single_verdict(e["final"]) == single_verdict(a["final_pass"]) == APPROVED
    assert e["final"]["final_lines"]["binding"] == a["final_pass"]["final_lines"]["binding"]
    # the corrected data exist: scoring 2027w01 against LATEST would give other outcome rows
    assert e["unpinned_2027w01"]["outcome_rows_sha256"] != e["sealed"]["weeks"]["2027w01"]["outcome_rows_sha256"]


def test_rescore_reuses_the_recorded_pins(results):
    r = world(results, "E_stat_correction")["rescore"]
    assert r["proc"]["returncode"] == 0, r["proc"]["stderr"][-2000:]
    assert r["pins_after"] == r["pins_before"] and r["outcome_after"] == r["outcome_before"]
    assert r["part_after"] == r["part_before"]


# --------------------------------------------------------------------------- #
# 7. The report-only 5,000-draw check
# --------------------------------------------------------------------------- #


def test_sensitivity_check_never_changes_the_verdict(results):
    a = world(results, "A_pass")
    forced = a["final_forced_all_fail"]
    assert forced["verdict"] == APPROVED and forced["lines"] == {lid: "pass" for lid in ALL_LINES}
    assert len(forced["differing"]) == 22
    assert "Lines whose PASS/FAIL would differ" in forced["final_md"] and forced["final_md"].count("Verdict:") == 1
    b = world(results, "B_qb_narrow")
    assert b["final_forced"]["verdict"] == NOT_APPROVED
    assert {k for k, v in b["final_forced"]["lines"].items() if v != "pass"} == {"L17"}
    assert [d["id"] for d in b["final_forced"]["differing"]] == ["L17"]


@pytest.mark.skipif(S.QUICK, reason="the real 5,000-draw run needs FFML_REHEARSAL_5000=1")
def test_real_5000_draw_run_reports_beside_the_binding_verdict(results):
    a, s = world(results, "A_pass"), world(results, "A5000_sensitivity")
    fin = s["final_default"]
    assert single_verdict(fin) == APPROVED == single_verdict(a["final_pass"])
    assert fin["final_lines"]["sensitivity"]["n_boot"] == 5000
    assert fin["final_lines"]["binding"] == a["final_pass"]["final_lines"]["binding"]


# --------------------------------------------------------------------------- #
# Rechecks of QA-F27 and the procedure note on where the final runs
# --------------------------------------------------------------------------- #


def test_weekly_scoring_leaves_the_pinned_worktree_clean(results):
    """QA-F27 recheck: the report goes to FFML_REPORTS_DIR, so the runner's pin check still passes."""
    r = world(results, "A_pass")["repro_pinned"]
    assert r["returncode"] == 0, r.get("stderr_tail")
    assert r["status_before"] == "" and r["pin_before"]["ok"] is True
    assert r["status_after"] == "" and r["pin_after"]["ok"] is True
    assert not Path(r["default_reports_dir"]).resolve().is_relative_to(Path(r["project_root"]).resolve())


def test_final_from_the_pinned_worktree_at_x_is_invalid(results):
    r = world(results, "A_pass")["repro_pinned"]["final_at_x"]
    v = single_verdict(r)
    assert v.startswith("Verdict: INVALID (frozen commit: pre-registered commit '<frozen commit>'"), v


# --------------------------------------------------------------------------- #
# D-121. A game still not final 14 days after the cutoff; a game final before then
# --------------------------------------------------------------------------- #

GAME_NEVER = W.game_id(*S.UNFINAL_NEVER)
GAME_LATE = W.game_id(*S.UNFINAL_LATE)
D121_REASON = "not final 14 days after the week's data cutoff (D-121)"


def _sealed_record(ev, wk):
    path = Path(ev["root"]) / "clone" / "data" / "livetest" / "scoring" / "sealed" / f"{wk}.json"
    return json.loads(path.read_text(encoding="utf-8"))


def _tried(step):
    rows = [e for e in step["score_events"] if e["status"] == "weeks tried"]
    assert len(rows) == 1, step["score_events"]
    return rows[0]["results"]


def test_week_with_a_game_not_final_waits_then_is_scored_without_it(results):
    ev = world(results, "G_unfinal_game")
    common_world_asserts(ev)
    friday, cli = _steps(ev, "never_wait_friday")[0], _steps(ev, "never_wait_cli")[0]
    assert friday["exit"] == 4 and "is not final in the pinned dim_game" in friday["message"], friday
    assert cli["exit"] == 4 and "is not final in the pinned dim_game" in cli["stderr"], cli
    assert cli["data_written"] == [] and cli["reports_written"] == []
    cutoff = W.data_cutoff(2026, 14)
    point = cutoff + timedelta(days=14)
    rec = _sealed_record(ev, "2026w14")
    assert rec["data_cutoff"] == cutoff.isoformat()
    assert rec["exclusion_after_utc"] == point.isoformat() and rec["pin_from_utc"] == point.isoformat()
    away, home = GAME_NEVER.split("_")[2:]
    assert rec["excluded_games"] == [{"game_id": GAME_NEVER, "teams": sorted([away, home]),
                                      "status_in_pinned_dim_game": "scheduled", "reason": D121_REASON}]
    # the first snapshot at or after the exclusion point: the 2026w16 Friday refresh (that cutoff plus 30 minutes)
    assert point == W.data_cutoff(2026, 16)
    first = (point + timedelta(minutes=30)).strftime("%Y%m%dT%H%M%SZ")
    assert set(rec["data_snapshots"].values()) == {first}, rec["data_snapshots"]
    assert all(c["ok"] for c in rec["checks"]), [c for c in rec["checks"] if not c["ok"]]
    week_final = next(c for c in rec["checks"] if c["name"] == "week final")
    assert GAME_NEVER in week_final["detail"] and "excluded" in week_final["detail"]


def test_later_weeks_are_not_held_behind_the_waiting_week(results):
    ev = world(results, "G_unfinal_game")
    waiting, after = _steps(ev, "runner_while_waiting")[0], _steps(ev, "runner_after_14_days")[0]
    tried = _tried(waiting)
    assert tried["2026w14"] != 0 and tried["2026w15"] == 0, tried
    assert "2026w15" in waiting["sealed_on_branch"] and "2026w14" not in waiting["sealed_on_branch"]
    assert any(e["status"] == "week not final yet" and e.get("week") == "2026w14" and e.get("not_final") == [GAME_NEVER]
               for e in waiting["score_events"]), waiting["score_events"]
    assert [c["args"] for c in waiting["scorer_calls"] if c["args"][:1] == ["--week"]] == [["--week", "2026w15"]]
    tried = _tried(after)
    assert after["exit"] == 0 and tried["2026w14"] == 0 and tried["2026w16"] == 0, tried
    assert {"2026w14", "2026w15", "2026w16"} <= set(after["sealed_on_branch"])
    assert any(e["status"] == "scoring with games excluded" and e.get("week") == "2026w14"
               and e.get("not_final") == [GAME_NEVER] for e in after["score_events"]), after["score_events"]
    calls = [c for c in after["scorer_calls"] if c["args"][:1] == ["--week"]]
    assert sorted(c["args"][1] for c in calls) == ["2026w14", "2026w16"] and all(c["returncode"] == 0 for c in calls)
    for wk in ("2026w15", "2026w16"):
        rec = _sealed_record(ev, wk)
        assert rec["excluded_games"] == [] and rec["pin_from_utc"] == rec["data_cutoff"], wk


def test_game_final_before_the_14_days_is_scored_from_the_first_final_snapshot(results):
    ev = world(results, "G_unfinal_game")
    wait, scored = _steps(ev, "late_wait_friday")[0], _steps(ev, "late_scored")[0]
    assert wait["exit"] == 4 and "is not final in the pinned dim_game" in wait["message"], wait
    # the game became final 4 days after the cutoff; the next refresh is the 2027w04 pre-build one
    refresh = W.attempt_time(2027, 4) - timedelta(hours=1)
    assert W.data_cutoff(2027, 2) + timedelta(days=4) < refresh < W.data_cutoff(2027, 2) + timedelta(days=14)
    assert scored["exit"] == 0 and scored["excluded"] == [] and scored["pin_from"] == refresh.isoformat(), scored
    rec = _sealed_record(ev, "2027w02")
    assert rec["excluded_games"] == [] and rec["pin_from_utc"] == refresh.isoformat()
    assert set(rec["data_snapshots"].values()) == {refresh.strftime("%Y%m%dT%H%M%SZ")}
    assert all(c["ok"] for c in rec["checks"]), [c for c in rec["checks"] if not c["ok"]]


def test_unfinal_world_verdict_equals_the_all_final_world(results):
    a, g = world(results, "A_pass"), world(results, "G_unfinal_game")
    for wk, info in g["sealed"]["weeks"].items():
        same = info["outcome_rows_sha256"] == a["sealed"]["weeks"][wk]["outcome_rows_sha256"]
        assert same is (wk != "2026w14"), wk  # only the excluded game's rows differ
    fin = g["final"]
    assert single_verdict(fin) == single_verdict(a["final_pass"]) == APPROVED
    assert all(c[1] == "PASS" for c in fin["checks"]), [c for c in fin["checks"] if c[1] != "PASS"]
    c = check(fin["checks"], "2026w14 games excluded (D-121)")
    assert c[1] == "PASS" and GAME_NEVER in c[2]
    assert not [x for x in fin["checks"] if x[0].endswith("games excluded (D-121)") and not x[0].startswith("2026w14")]
    scored, excluded = _excluded(fin)
    assert len(scored) == 18 and excluded == []
    assert "## Games excluded (D-121)" in fin["final_md"] and GAME_NEVER in fin["final_md"]
    assert [x["game_id"] for x in fin["final_lines"]["excluded_games"]] == [GAME_NEVER]
    assert all(v == [] for v in g["metric_leaks"].values()), {k: v for k, v in g["metric_leaks"].items() if v}


def test_comparators_gap_probe_left_the_world_as_it_was(results):
    gap = world(results, "G_unfinal_game")["comparators_gap"]
    assert gap["restored"] is True


@pytest.mark.xfail(strict=True, reason="QA-F31: ensure_comparators ignores the D-121 exclusion; when the comparators "
                   "run must be extended, the week with the excluded game and every later week of its season are refused")
def test_comparators_extension_honours_the_excluded_game(results):
    gap = world(results, "G_unfinal_game")["comparators_gap"]
    for case in ("unfinal_week", "later_week"):
        assert "comparators cannot be extended" not in gap[case]["stderr"], gap[case]
