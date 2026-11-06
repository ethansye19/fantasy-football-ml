"""End-to-end rehearsal of the live-test scorer's verdict path on synthetic data (QA, D-115).

Run it explicitly; it takes about 75 minutes on the 8-core development laptop
(three worlds at a time, ``FFML_REHEARSAL_WORKERS``)::

    FFML_REHEARSAL=1 pytest -m slow tests/rehearsal -q

``FFML_REHEARSAL_5000=1`` adds the real 5,000-draw final through the CLI default
(about 1.5 hours more of one core, run beside the rest); without it the
report-only rule is shown with the forced-disagreement hook only. A run with
the 5,000-draw job and four workers was stopped by Claude Code for low system
memory on 2026-11-06; prefer the default.

Every world is built from scratch in a temporary directory: a clone of the
repository pinned at the candidate frozen commit X (``FFML_REHEARSAL_COMMIT``,
default 4c35fea), a temporary bare repository as ``origin`` holding the fake
``livetest-2026`` branch, and synthetic stores under the clone's ignored
``data/`` and ``artifacts/``. The scorer always runs from the clone (asserted in
every subprocess); nothing touches the real data, artifacts, branch, registry or
access log, and the holdout unlock flag is never set. ``FFML_REHEARSAL_EVIDENCE``
names a file for the JSON evidence (reports/livetest_final_rehearsal.md is
written from it).

Scenarios (the owner's list): PASS; FAIL with L17 (QB intervals too narrow) and
separately L07 (one accuracy line); 0, 1, 2 and 3 missed weeks; sealed results
(weekly outputs, tampered part and record, missing branch record); a missing
change-log entry; change-log-only commits, an edited entry, a merge and a
frozen-path commit; the report-only 5,000-draw check.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from pathlib import Path

import _rehearsal_scenarios as S
import _rehearsal_world as W
import pytest

pytestmark = pytest.mark.slow

APPROVED, NOT_APPROVED = "Verdict: APPROVED", "Verdict: NOT APPROVED"
ALL_LINES = [f"L{i:02d}" for i in range(1, 23)]


def _candidate_known() -> bool:
    out = subprocess.run(["git", "rev-parse", "--verify", "--quiet", f"{W.FROZEN_CANDIDATE}^{{commit}}"],
                         cwd=W.MAIN_REPO, capture_output=True, text=True)
    return out.returncode == 0


if os.environ.get("FFML_REHEARSAL") != "1":
    pytest.skip("the rehearsal runs only with FFML_REHEARSAL=1 (about an hour)", allow_module_level=True)
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
        pytest.skip(f"{name} not run (FFML_REHEARSAL_ONLY)")
    ev = results[name]
    assert "error" not in ev, f"{name} failed: {ev.get('error')}\n{ev.get('traceback', '')[-4000:]}"
    return ev


def single_verdict(fin):
    """Exactly one Verdict line, the same on stdout and in final.md; returns it."""
    assert fin["proc"]["returncode"] == 0, fin["proc"]["stderr"][-3000:]
    assert len(fin["verdict_stdout"]) == 1, fin["proc"]["stdout"]
    assert fin["final_md"].count("Verdict:") == 1
    assert fin["verdict_md"] == fin["verdict_stdout"]
    return fin["verdict_stdout"][0]


def check(checks, name):
    rows = [c for c in checks if (c["name"] if isinstance(c, dict) else c[0]) == name]
    assert rows, f"no check {name!r}"
    return rows[0]


def common_world_asserts(ev):
    """The clone, not the main checkout, ran; the frozen code equals X; every week verified clean."""
    probe = ev["probe"]
    clone = Path(ev["root"]) / "clone"
    assert Path(probe["ffml_file"]).resolve().is_relative_to(clone.resolve())
    assert Path(probe["project_root"]).resolve() == clone.resolve()
    assert Path(probe["data_dir"]).resolve().is_relative_to(clone.resolve())
    assert not probe["holdout_unlock"]
    assert ev["diff_x_head_frozen_code"] == ""
    for step in ev["weekly"]:
        assert step["exit"] == 0, step
        if step["op"] == "push":
            assert step["post_push_verify_ok"] is True and step["ffml_livetest_verify_rc"] == 0, step
            assert step["push_record"]["on_time"] is True
        if step["op"] == "score-hook":
            assert step["failed"] == [], step
    scored = [k for k, v in ev["sealed"]["weeks"].items() if v["local_sha256_file"]]
    for wk in scored:
        info = ev["sealed"]["weeks"][wk]
        # the runner publishes the scorer's sealed file verbatim where the final reads it
        assert info["branch_record"] is not None and info["branch_record"].strip() == info["local_sha256_file"].strip()
        assert info["report"] and info["missed_marker"] is None
    return scored


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
            "final evaluation commit", "final evaluation change log", "window complete",
            "missed weeks (miss rule)"} <= names
    assert sum(1 for n in names if n.endswith(" sealed hashes")) == 18
    assert sum(1 for n in names if n.endswith(" commitment")) == 18
    # deterministic: the same world gives the same verdict and the same binding lines
    rep = ev["final_pass_repeat"]
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
        assert info["part_sha256"] == b["sealed"]["weeks"][wk]["part_sha256"], wk
    sa = [s.get("commitment_sha256") for s in a["weekly"] if s["op"] == "push"]
    sb = [s.get("commitment_sha256") for s in b["weekly"] if s["op"] == "push"]
    assert sa == sb and len(sa) == 18


# --------------------------------------------------------------------------- #
# 3. Missed weeks
# --------------------------------------------------------------------------- #


def _excluded(fin):
    m = re.search(r"Weeks scored: (.*?)(?:; excluded \(miss rule\): (.*?))?\.\n", fin["final_md"])
    assert m, "no 'Weeks scored' line"
    return m.group(1).split(", "), (m.group(2) or "").split(", ") if m.group(2) else []


def test_zero_misses(results):
    fin = world(results, "A_pass")["final_pass"]
    scored, excluded = _excluded(fin)
    assert len(scored) == 18 and excluded == []
    assert "0 missed week(s) [], 2 allowed" in check(fin["checks"], "missed weeks (miss rule)")[2]


@pytest.mark.parametrize("name,missed", [("D1_one_miss", ["2026w12"]), ("D2_two_misses", ["2026w12", "2027w09"])])
def test_one_or_two_misses_are_excluded_without_extension(results, name, missed):
    ev = world(results, name)
    scored = common_world_asserts(ev)
    assert sorted(scored) == sorted(W.week_key(s, w) for s, w in W.WINDOW if W.week_key(s, w) not in missed)
    for wk in missed:
        marker = json.loads(ev["sealed"]["weeks"][wk]["missed_marker"])
        assert marker["reason"]["category"] == "machine_offline" and marker["allowed"] == 2
    fin = ev["final"]
    assert single_verdict(fin) == APPROVED  # the verdict over the remaining weeks
    got_scored, excluded = _excluded(fin)
    assert excluded == missed and len(got_scored) == 18 - len(missed)
    assert "2027w10" not in fin["final_md"]  # the window is not extended
    detail = check(fin["checks"], "missed weeks (miss rule)")[2]
    assert f"{len(missed)} missed week(s)" in detail and "excluded, the window is not extended" in detail
    assert check(fin["checks"], "window complete")[1] == "PASS"


def test_final_refused_until_last_week_final_even_when_it_is_the_miss(results):
    ev = world(results, "D2_two_misses")
    res = ev["last_week_not_final"]
    assert res["proc"]["returncode"] == 3
    assert "the final is refused until 2027w09 is final in dim_game" in res["proc"]["stderr"]
    assert res["verdict_stdout"] == [] and "final_md" not in res


def test_three_misses_are_invalid(results):
    ev = world(results, "D3_three_misses")
    common_world_asserts(ev)
    v = single_verdict(ev["final"])
    assert v.startswith("Verdict: INVALID (") and "missed weeks (miss rule): 3 missed week(s)" in v
    assert "more than allowed: INVALID" in v
    assert ev["final"]["lines"] == {}  # nothing computed
    assert "Not computed: an integrity check failed." in ev["final"]["final_md"]


# --------------------------------------------------------------------------- #
# 4. Sealed results
# --------------------------------------------------------------------------- #


def test_weekly_outputs_hold_no_metrics_and_only_sealed_files(results):
    ev = world(results, "A_pass")
    assert all(v == [] for v in ev["metric_leaks"].values()), {k: v for k, v in ev["metric_leaks"].items() if v}
    assert len([k for k in ev["metric_leaks"] if k.startswith("reports/livetest/")]) == 18
    assert all(not s.get("message_leaks") for s in ev["weekly"] if s["op"] == "score-hook")
    # one weekly run of the real CLI writes the sealed trio and the verification report, nothing else
    d1 = world(results, "D1_one_miss")
    for step in (s for s in d1["weekly"] if s["op"] == "score-cli"):
        wk = W.week_key(step["season"], step["week"])
        assert step["data_written"] == sorted(f"livetest/scoring/sealed/{wk}.{x}" for x in ("json", "parquet", "sha256"))
        assert step["reports_written"] == [f"livetest/{wk}_verification.md"]
        assert step["artifacts_written"] in ([], ["holdout_access.log"])
        assert step["settings_reports"] == []
        assert W.metric_leaks(step["stdout"]) == [], step["stdout"]
        assert "Verdict" not in step["stdout"] + step["stderr"]
    # the weekly hook seals the same part bytes as the CLI
    for wk, eq in d1["hook_cli_equivalence"].items():
        assert eq["cli_part_sha256"] == eq["hook_part_sha256"], wk


def test_final_refuses_tampered_sealed_files_and_a_missing_branch_record(results):
    sc = world(results, "A_pass")["sealed_checks"]
    for key, needle in (("tampered_part", "2026w14 sealed hashes: 2026w14.parquet sha256 differs from the branch record"),
                        ("tampered_record", "2026w11 sealed hashes: 2026w11.json sha256 differs from the branch record"),
                        ("missing_branch_record", "2027w09 sealed hashes: scoring/2027w09.sha256 is not on the branch")):
        r = sc[key]
        assert r["proc"]["returncode"] == 3, (key, r["proc"])
        assert "refused: sealed files do not match the branch record" in r["proc"]["stderr"], key
        assert needle in r["proc"]["stderr"], (key, r["proc"]["stderr"][-2000:])
        assert r["verdict_stdout"] == [] and "final_md" not in r, key
    assert "scoring/2027w09.sha256" in sc["missing_branch_record_ref"]["files_removed"].split()
    # every intact seal verifies in both reading modes
    assert all(c["ok"] for m in ("worktree", "git_only") for c in sc["seals_intact"][m])


def test_worktree_mode_trusts_uncommitted_record_copy(results):
    """FINDING (reported, not fixed): the default worktree mode reads the record from the worktree's files."""
    r = world(results, "A_pass")["sealed_checks"]["repro_worktree_trust"]
    assert "scoring/2026w10.sha256" in r["worktree_status"]  # an uncommitted edit
    assert all(c["ok"] for c in r["worktree_mode"])          # accepted: the tampered part matches the edited copy
    assert all(c["ok"] for c in r["sealed_part_check"])       # and the record's own part hash was rewritten
    assert not all(c["ok"] for c in r["no_worktree_mode"])    # git-only mode refuses it
    assert r["restored_status"] == ""


# --------------------------------------------------------------------------- #
# 5 and 6. Change log and commit relation
# --------------------------------------------------------------------------- #


def test_change_log_only_commits_need_no_entry(results):
    ev = world(results, "A_pass")
    fin = ev["final_pass"]
    assert single_verdict(fin) == APPROVED
    c = ev["commits"]
    log = ev["git_scenarios"]["valid_base"]["log"].splitlines()
    assert len(log) == 4  # F (fill), L1 (log F), A (scripts/, logged), L2 (log A)
    assert check(fin["checks"], "final evaluation change log")[1] == "PASS"
    assert "every commit in X..HEAD is logged" in check(fin["checks"], "final evaluation change log")[2]
    assert c["L2_log_A"].startswith(check(fin["checks"], "final evaluation commit")[2].split("HEAD ")[1][:12])


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
    ev = world(results, "A_pass")
    r = ev["git_scenarios"][name]
    v = single_verdict(r)
    assert v.startswith("Verdict: INVALID ("), v
    failed = [c for c in r["checks"] if c[1] != "PASS"]
    assert [c[0] for c in failed] == [failing], failed
    assert r["commits"][needle][:12] in failed[0][2], failed
    assert r["lines"] == {}


def test_final_from_the_pinned_worktree_at_x_is_invalid(results):
    """The final must run where the commit field is filled (a descendant of X), not in the worktree pinned at X."""
    r = world(results, "A_pass")["repro_pinned_dirty"]["final_at_x"]
    v = single_verdict(r)
    assert v.startswith("Verdict: INVALID (frozen commit: pre-registered commit '<frozen commit>'"), v


def test_frozen_path_scenario_names_the_path(results):
    r = world(results, "A_pass")["git_scenarios"]["frozen_path"]
    assert "ffml/models/x.py" in single_verdict(r)


def test_delete_entry_uncovers_the_logged_commit_again(results):
    ev = world(results, "A_pass")
    r = ev["git_scenarios"]["delete_entry"]
    detail = check(r["checks"], "final evaluation change log")[2]
    assert ev["commits"]["A_logged_change"][:12] in detail


def test_merge_scenario_exempts_the_side_append_but_not_the_merge(results):
    r = world(results, "A_pass")["git_scenarios"]["merge"]
    detail = check(r["checks"], "final evaluation change log")[2]
    assert r["commits"]["merge"][:12] in detail and r["commits"]["side_append"][:12] not in detail


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
    assert "## Report-only sensitivity check: 5,000 draws (D-111)" in fin["final_md"]


# --------------------------------------------------------------------------- #
# Reproduction of a cross-component finding (the runner's pinned worktree)
# --------------------------------------------------------------------------- #


def test_weekly_scorer_dirties_the_pinned_worktree(results):
    """FINDING (reported, not fixed): weekly scoring from the pinned worktree leaves an untracked report there."""
    r = world(results, "A_pass")["repro_pinned_dirty"]
    assert r["returncode"] == 0, r.get("stderr_tail")
    assert r["status_before"] == "" and r["pin_before"]["ok"] is True
    assert "?? reports/livetest/2026w10_verification.md" in r["status_after"]
    assert r["pin_after"]["ok"] is False and "is not clean" in r["pin_after"]["error"]


def test_missed_marker_for_a_committed_sealed_week_is_not_cross_checked(results):
    """FINDING (reported, not fixed): a hand-pushed MISSED.json turns a committed, sealed week into an excluded miss."""
    r = world(results, "A_pass")["repro_marker_for_scored_week"]
    assert {"2026w13/commitment.json", "2026w13/MISSED.json", "scoring/2026w13.sha256"} <= set(r["branch_has"])
    by = {c["name"]: c for c in r["integrity_with_records"]}
    assert by["no week both scored and missed"]["ok"]  # vacuous: misses are only looked up for unscored weeks
    assert "18 scored, 0 missed" in by["window complete"]["detail"]
    fin = r["final"]
    v = single_verdict(fin)
    assert v == APPROVED  # computed, with the committed and sealed week excluded
    scored, excluded = _excluded(fin)
    assert excluded == ["2026w13"] and len(scored) == 17


def test_weekly_sealed_records_hold_the_metrics_and_both_draw_counts(results):
    """The metrics exist only inside the sealed JSON (cumulative 500-draw lines and the sensitivity lines)."""
    ev = world(results, "A_pass")
    sealed = Path(ev["root"]) / "clone" / "data" / "livetest" / "scoring" / "sealed"
    weeks = []
    for s, w in W.WINDOW:
        rec = json.loads((sealed / f"{W.week_key(s, w)}.json").read_text(encoding="utf-8"))
        m = rec["sealed_metrics"]
        assert len(m["week_lines"]) == 22 and len(m["cumulative_lines_500"]) == 22
        assert m["cumulative_lines_sensitivity"]["n_boot"] == 5  # the hook's draw count (CLI: 5,000)
        weeks.append(len(m["cumulative_weeks"]))
        assert all(c["ok"] for c in rec["checks"]), [c for c in rec["checks"] if not c["ok"]]
    assert weeks == list(range(1, 19))  # cumulative over the weeks sealed so far
