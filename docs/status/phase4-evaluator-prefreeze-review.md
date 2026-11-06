# Phase 4 / Independent Evaluator / pre-freeze review of the change-log fix, the QA rehearsal and QA's defects (EV-4.6, D-115)
Date: 2026-11-06
Scope: an independent review, at the owner's instruction (D-115), of (1) the
orchestrator's change-log exemption in the scorer (4c35fea), (2) QA's
end-to-end rehearsal of the final (1f2baf9,
`reports/livetest_final_rehearsal.md`, `tests/rehearsal/`), and (3) QA's
defects QA-F27, F28, F29, the latent `allowed` read and the cost risk, with
the orchestrator's proposed fixes. The fixes are prepared on branch
`prefreeze-fixes` (from 1f2baf9, in the worktree `../ffml-prefreeze-fixes`),
not merged and not pushed; the owner approves before anything reaches main.

## Summary

| item | verdict |
| --- | --- |
| 1. Change-log exemption (4c35fea) | **Agree.** Correct and narrow. One refinement, about a gap that predates the fix: "logged before first use" is not machine-checked (section 1) |
| 2. QA rehearsal (1f2baf9) | **Agree that it exercises the verdict path the final will use**, with four gaps: the comparators' real extension path never ran, the reference model set was reduced, the Tuesday job with the 5,000-draw step never ran whole, and the real 5,000-draw final never completed (section 2) |
| QA-F27 | **Agree** with the proposal; implemented on the branch |
| QA-F28 | **Refine.** The branch, not the local files, decides which weeks were committed. A committed week is never excluded. A marker for a committed week is listed as a finding that does not change the verdict. A committed week without local sealed files refuses the final, so it cannot be dropped by deleting files. Implemented |
| QA-F29 | **Agree, with one addition.** The final fetches and reads only the remote ref, refuses if the fetch fails, and checks that the tracking ref equals the remote head. It also re-reads each scored week's commitment and summary from that ref and compares them with what was scored, because weekly scoring may have read the worktree. Implemented |
| latent `allowed` | **Agree.** Read exactly; a missing, non-integer, boolean or negative value fails the integrity check. Implemented |
| cost risk | **Confirmed, and larger than estimated** (section 4). At the 18th week the Tuesday job's cumulative 5,000-draw step takes about 114 minutes, and the whole job about 130 minutes, against the runner's 180-minute timeout. Under the CPU throttling observed on this machine it takes about 1.5 times as long and exceeds the timeout. The first Tuesday adds about 2.2 hours for the comparators' first extension. Mitigations are in section 4 |

Measured on a realistic-size synthetic window: 18 weeks, about 540 rows per week per model, 14 model labels and a consensus frame; one process, above-normal priority.

| step (18 weeks scored) | full frame list (as the scorer runs now) | line-relevant frames only |
| --- | --- | --- |
| 500 draws (binding final, or the weekly cumulative 500) | 11.6 min | 5.3 min |
| 5,000 draws (sensitivity: weekly with `--weekly-sensitivity`, and at the final) | 114 min (1.37 s per draw, measured at 100 and 500 draws) | 53 min (0.64 s per draw) |
| final total (500 + 5,000) | about 126 min | about 58 min |
| peak working set | 345 MB | 345 MB |

## 1. The change-log exemption (4c35fea)

`_changelog_append_only(sha)` exempts a commit from needing its own entry
only when all of these hold:

- it has exactly one parent, so merges and root commits are excluded;
- `git diff-tree --name-only -r` lists exactly the change log;
- `--numstat` reports one row with added > 0 and deleted = 0.

`uncovered_commits` applies the exemption in both places it is used: X..build
commit per week, and X..HEAD for the final.

**Correct.** An entry can name only a commit that already exists, so without
the exemption the commit adding the last entry could never be covered and the
final check could never pass. The exemption removes exactly that
impossibility.

**Narrow.** The following all fail the exemption and stay subject to the
entry requirement:

- an edit of an existing line, which numstat shows as one deleted plus one
  added line;
- a deletion;
- writing into the template's empty row (an edit);
- a rename, which `diff-tree` without `-M` lists as two paths;
- a mode-only change (0 added lines);
- a commit that also touches another path;
- a merge.

QA's rehearsal reproduces six of these cases (6b to 6f) and the unit tests
cover them. An exempt commit cannot touch a frozen path, and frozen paths are
enforced separately by the commit relation in any case. Line-ending
normalisation at commit time does not create deletions (the blobs are LF).

**Refinement, about a gap that predates the fix.** The pre-registration
requires every allowed change to be logged *before first use*. The scorer
checks only that an entry exists in the change log at the evaluating HEAD. An
unlogged change used by a build can therefore be logged after the fact by an
append-only commit, and the final passes.

A machine check is cheap: for each week, read the change log at that week's
build commit (`git show <code.commit>:docs/status/livetest-changelog-2026.md`)
and require entries for every non-exempt commit in X..code.commit. The final's
X..HEAD check stays as it is.

It is not on the branch; it is a one-function change if the owner wants it.
It changes no verdict in the rehearsal: every rehearsal build is at X, and the
post-season changes are logged before the final.

## 2. QA's rehearsal (1f2baf9)

**It exercises the verdict path the final will use.** The final is the real
CLI (`python -m ffml.eval.livetest_score --final`) run from a clean clone at
the candidate X. It reads data through the scorer's own loaders
(`load_dim_game`, `harness_outcomes`, `ensure_comparators`, `consensus_rows`,
`_all_consensus`), and the runner's real `init`, `run` and `seal` commands
built the branch.

The weekly hook (`score_week(..., n_boot=5, sensitivity_n_boot=5)`) affects
only the sealed weekly JSON. The final recomputes every line from the sealed
parts, which do not depend on the draw count, so the hook cannot change a
verdict.

The PASS, FAIL, miss, tamper, change-log, frozen-path and forced-sensitivity
scenarios each drive the binding code path to the expected result.

**Gaps.** None of these changes a rehearsal result; each should be known
before the freeze.

- **The comparators' real extension never ran.** The comparators run was
  pre-written, so `ensure_comparators` always found the week and never called
  `run_comparators`: the `livetest_comparators` configuration with
  `strict_models=True`, purpose `livetest_score`, the five baselines and the
  challenger per scoring. The first live Tuesday (2026 week 10, about
  2026-11-17) will run it for the first time, inside the weekly scorer
  subprocess with its 180-minute timeout.
  - Cost: the validated run's challenger takes about 31 minutes per scoring
    for 72 weeks, about 1.3 minutes per week over the three scorings. The
    first extension (2021 week 1 to 2026 week 10, about 100 weeks) is
    therefore about 2.2 hours before any scoring starts.
  - This is a real risk of a timed-out, unsealed week.
  - **Recommendation:** pre-warm `data/backtests/livetest_comparators` before
    Week 10's scoring with the frozen code (resumable parts; each week's part
    uses only earlier data, so pre-warmed parts are identical to the ones the
    scorer would write). Prove the strict-model build once on 2026 weeks 1-8,
    which are final now.
- **A reduced reference set.** The rehearsal's comparators omit
  `rolling_avg_3`, `rolling_ew_035` and `direct_lgbm`. Those models enter only
  the D-077 reference mean and the main bootstrap's frame list, so the pair
  set and the cost differ from the live test; the logic does not.
- **The Tuesday job was not run whole.** `livetest_week.py score-week` with
  `--weekly-sensitivity` was never run as one job. Its two steps ran
  separately (QA says so). Section 4 shows that its cumulative 5,000-draw step
  does not fit the timeout late in the window.
- **The real 5,000-draw final never completed.** It is still unproven
  end-to-end. Its arithmetic is the same `score_frames` call as the binding
  one with `n_boot=5000`, and the forced-disagreement checks (7a, 7b) show it
  cannot reach the verdict.
- **Not rehearsed:**
  - a postponed game that stays unfinal for weeks (the final is refused until
    it is final, which is correct but could delay the verdict);
  - a kickoff moved earlier after a build (unit-tested);
  - `late/` commitments;
  - weeks without a consensus scrape (unit-tested).

**A note on timing of the scoring (no defect).** Weekly scoring seals the
actuals of the processed snapshot current on Tuesday, and the final reuses
those sealed parts. nflverse stat corrections usually arrive Wednesday to
Thursday (D-031). Sealed outcomes can therefore miss corrections.

This is pre-registered ("the processed snapshot current at scoring time") and
deterministic, and its effect on the metrics is small. If the owner prefers
corrected stats, scoring after the Thursday full refresh is a runbook change
(`docs/`, `scripts/`, both allowed).

## 3. QA's defects and the proposed fixes

**QA-F27 (high): agree.**

- **Fix (branch):** the weekly verification report and the final report
  default to `get_settings().reports_dir / "livetest"`
  (`default_reports_dir()`). The runner already sets `FFML_REPORTS_DIR`
  outside the pinned worktree; in the main checkout the default is unchanged
  (`reports/livetest/`).
- **No other default write lands in the checkout:** the sealed store is under
  `settings.data_dir`, and the pre-registration and git are only read.
- **Test:** `test_reports_go_to_settings_reports_dir_not_the_checkout`. With
  the default reports directory, a weekly run writes the report under
  `FFML_REPORTS_DIR` and the sealed files under `FFML_DATA_DIR`, and
  `git status --porcelain --untracked-files=all -- reports data` of the
  checkout is unchanged.
- **Verdicts:** unchanged in every rehearsal scenario. Only the location of
  the reports moves.

**QA-F28 (medium): refine.**

- **Fix (branch):** the final asks the branch record, for every window week,
  whether it holds `<week>/commitment.json` or `scoring/<week>.sha256`.
  - Such a week is never a miss.
  - If it has no local sealed record, the final is refused ("never excluded as
    a miss; restore its sealed files or score it"). Deleting local files can
    no longer turn a committed week into an exclusion.
  - A `MISSED.json` for a committed week is listed: in the integrity table as
    "markers ignored for committed weeks, which are scored", and in a section
    "Integrity findings that do not change the verdict".
  - Only a marker for a week with neither a commitment nor a seal on the
    branch counts as a miss.
- **On the alternative: I agree with the orchestrator's lean, for a further
  reason.** If a conflicting marker made the test INVALID, anyone able to push
  one marker could invalidate a year-long test, and an accidental marker would
  do the same. Scoring the committed week is what the pre-registration
  requires anyway ("a miss is a week with no valid commitment on livetest-2026
  at its deadline"). It cannot help a manipulator, and the listing keeps the
  event visible to the owner.
- **Tests:** `test_missed_marker_cannot_exclude_a_committed_week` covers:
  - the marker listed and the week scored;
  - the attack (local sealed files moved away) refused;
  - a genuine miss still excluded.
- **Verdicts:** unchanged in every rehearsal scenario except the attack, 4e.
  That case now refuses the final; with the local files present, the week is
  scored over 18 weeks with the marker listed.

**QA-F29 (medium): agree, with one addition.**

- **Fix (branch):** `--final` ignores `--worktree`. It runs
  `git fetch origin +refs/heads/livetest-2026:refs/remotes/origin/livetest-2026`,
  refuses if that fails, and reads every branch file with `git show` from the
  fetched ref.
- **Branch-record check:** a "branch record" check requires the tracking ref
  to equal `git ls-remote`'s head. The report states the ref, its sha and the
  fetch time.
- **The addition:** a per-week "branch commitment" check compares the ref's
  `<week>/commitment.json` (and `player_summary.parquet` when present) with
  the sha256 that weekly scoring recorded. Weekly scoring still reads the
  runner's worktree, so a week scored from an uncommitted file is caught at
  the final.
- **Tests:**
  - `test_final_checks_the_branch_record_against_what_was_scored`: a changed
    branch commitment, and a tracking ref that differs from the remote;
  - `test_fetch_branch_ref_refuses_without_a_remote`;
  - `test_fetch_branch_ref_reads_the_remote_head`, against a bare remote.
- **Verdicts:** unchanged in every rehearsal scenario. QA's F29 reproduction
  (an uncommitted worktree record) is now refused or INVALID instead of
  accepted. Scenario 4d already used `--no-worktree`.
- The weekly default stays the worktree, so the runner's call is untouched.

**Latent `int(allowed or 1)`: agree.**

- **Fix (branch):** `missed_weeks.allowed` must be a non-negative integer
  (booleans excluded). A missing or invalid value fails "missed weeks (miss
  rule)" with the reason, and 0 is honoured.
- **Test:** `test_missed_weeks_allowed_is_read_exactly` covers 0, missing,
  `"2"`, `true` and -1. The unit fixtures now carry
  `missed_weeks.allowed = 2` like the real pre-registration.
- **Verdicts:** unchanged (the real value is 2).

## 4. Cost of the cumulative step and the final (measured)

**How it was measured.** The script is `cost_timing.py` plus stages 2 and 3 in the session scratchpad; the
numbers are in `data/reports/livetest_scorer_cost.json`. It builds a synthetic window the size of the live one:

- 18 weeks; per week QB 75, RB 122, WR 176, TE 106, K 31 and DST 30 rows, the 2022-2024 aligned counts;
- about 78% of rows active;
- 14 labels: `chain_v2` raw and calibrated, the five baselines and `direct_lgbm`, each raw and calibrated;
- a consensus frame on about 82% of rows.

It times `score_frames`, the exact call behind the weekly cumulative step and the final, at 100 and 500 draws.
Bootstrap cost is linear in the draw count (fixed cost about 9 s), so 5,000 draws are extrapolated from the
measured slope.

**The weekly job at week k.** The cumulative step's cost grows with the weeks scored so far, so the last week
of the window is the worst case. At week 18 the Tuesday job is:

- the cumulative 500-draw step, 11.6 min;
- the cumulative 5,000-draw step, about 114 min;
- the one-week score, the comparators' one-week extension (about 1.3 min of challenger fits), and the
  outcome and consensus loading, a few minutes together;
- about 130 minutes in total at above-normal priority.

The runner's timeout is 180 minutes (`scorer_timeout_minutes`).

**Throttling.** The same 100 draws took 4.5 minutes at normal priority in the background (the first timing
run) and 2.4 minutes at above-normal priority. That is Windows throttling a background process; the branch's
test run showed the same, at 0.2 cores until its priority was raised.

At the throttled rate the week-18 job takes about 190 minutes and times out. A timed-out week is retried from
scratch and is not sealed, and the final refuses an unsealed week. Late in the window this is a real risk of
an unsealed week, which would need the Evaluator to score it by hand.

**The first Tuesday.** 2026 week 10 (about 2026-11-17) adds the comparators' first extension: 2021 week 1
through 2026 week 10, about 100 weeks of the challenger at about 1.3 minutes per week, roughly 2.2 hours. It
also runs the strict-model build for the first time (section 2). With one week scored the cumulative steps
are small, so the job is about 2.3 hours, inside 180 minutes but without margin under throttling.

**Options, for the owner to choose; none is on the branch:**

1. **Bootstrap only the frames the 22 lines read.**
   - The frames are `chain_v2`, `chain_v2+cal`, the three line baselines' calibrated forms and the
     consensus. All frames are still used for alignment and the D-077 reference.
   - Every position group draws the same cluster index matrix whatever other frames are present, because the
     aligned frames share their clusters. The line results are therefore identical by construction,
     confirmed here: identical 22 line results at 100 draws.
   - It cuts cost about 2.2 times (week-18 job about 60 minutes, final about 58 minutes).
   - It is a frozen file, so it must be done before the freeze. It is a one-function change with an
     equivalence test, which I can add to the branch if the owner wants it. The sealed weekly JSON would
     then hold point metrics for those frames only (not binding).
2. **Runner changes on allowed paths** (`scripts/`): launch the scorer at above-normal priority with power
   throttling off, and raise `scorer_timeout_minutes` to, say, 300.
3. **Pre-warm `data/backtests/livetest_comparators`** before 2026 week 10 is scored (section 2). This also
   proves the strict-model configuration once on 2026 weeks 1-8.
4. Run the weekly cumulative 5,000-draw step only at the final. This is a pre-registration change (R4 kept
   it weekly), so it is the owner's ruling and is listed only for completeness.

Options 1 to 3 change no verdict: 1 is an identity, and 2 and 3 are operations.

## Branch `prefreeze-fixes`

Branch `prefreeze-fixes`, worktree `C:/Users/Ethan/ffml-prefreeze-fixes`, rebased onto main `81346bc`
(QA-F30, runner only):

- `c06b532` (before the rebase `075aa18`, on 1f2baf9): the four scorer fixes and their tests, in
  `ffml/eval/livetest_score.py` and `tests/eval/test_livetest_score.py` only;
- `git diff --name-only main prefreeze-fixes` lists exactly those two files;
- not merged and not pushed.

**Tests on the branch** (before the rebase; main's later commits touch no eval file):
`pytest -q tests/eval/test_livetest_score.py tests/eval/test_livetest_comparators.py tests/eval/test_audits.py`
gave **47 passed** in 19 minutes. That includes the eight new tests (one for QA-F27, one for QA-F28, three
for QA-F29, three more for the `allowed` read) and the audit's eval-code check. `ruff` is clean.

**Effect on the rehearsal's scenarios** (reasoned from the code, not re-run; the rehearsal takes about
75 minutes with three workers):

| fix | scenarios whose result changes |
| --- | --- |
| QA-F27 | none; only report locations move |
| QA-F28 | only 4e, which is now refused instead of a 17-week verdict. 3b, 3c and 3e keep their results, because their missed weeks have no commitment or seal on the branch |
| QA-F29 | none of the binding scenarios. QA's F29 reproduction is now caught |
| `allowed` read | none; the value is 2 |

**One thing to re-run after merging:** 4d (missing branch hash record) and the change-log scenarios, under
the new fetch-only final. They should give the same results; it is a quick subset
(`FFML_REHEARSAL_ONLY=A_pass`).
