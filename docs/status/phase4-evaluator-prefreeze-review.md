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

Updated 2026-11-07 for EV-4.7 (D-116, D-117): the last section, "EV-4.7: D-116 implemented in the scorer".

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


## EV-4.7: D-116 implemented in the scorer (2026-11-06)

Implemented on main in `ffml/eval/livetest_score.py`, with a small change to `ffml/eval/ecr_baseline.py`
(optional stores, so the consensus can be pinned). It follows D-116 and the orchestrator's assumptions A1-A7,
and the runner's interface note (7d309ab, d2af36b; `scripts/livetest_stub_score.py`).

Two commits: bd3bf21 holds every D-116 change with the bootstrap subset present but off (the full computation),
and the commit after it turns the subset on, once the proof below showed zero differences.

### Exit codes (CLI `python -m ffml.eval.livetest_score`)

| code | meaning | where |
| --- | --- | --- |
| 0 | ok | every mode |
| 2 | usage (argparse), or `--final` without a pre-registration | unchanged |
| 3 | refused: an integrity refusal or another failure a retry will not fix | unchanged |
| **4** | retry later | `--week`: before the data cutoff, the week not final in the pinned `dim_game`, or no snapshot at or after the cutoff yet. `--data-cutoff`: the week is not in `dim_game` yet |
| **5** | blocked | `--prebuild-check`: an uncovered or a frozen-path commit, or input it cannot resolve |
| **75** | retryable fetch failure | `--final`: `git fetch` or the `ls-remote` check failed |

No code clashed with an existing one; 4, 5 and 75 were free. Exit 75 is raised before anything is computed,
so it writes no `Verdict:` line, no final report and nothing in the sealed directory (A4). That is tested
through `main()`.

### The changes

- **Flagged markers (A5).** The final report lists every flagged MISSED marker in a table:
  - the week;
  - the branch commit that added the marker and its committer time (`git log --diff-filter=A` on the fetched
    ref; the time is the commit's own date as GitHub shows it);
  - `created_at` from the marker, if present;
  - why it was flagged: a commitment on the branch, a sealed-hash record on the branch, a local sealed record.

  The same list is stored in `sealed/final_lines.json`.
- **Pre-build check (B1, A1).** `--prebuild-check --from <sha|""> --to <sha>` prints
  `{"ok", "uncovered", "frozen", "detail", "from", "to", "x", "commits"}` and exits 0 or 5.
  - An empty `--from` means X.
  - X is read from the first filled `commit` in, in order: `--prereg`, the pre-registration at `--to`,
    `main`, `origin/main`. The pinned worktree at X carries the placeholder; `main` is a ref the worktree
    shares.
  - Every commit in from..to must have an entry in the change log as it stands at `--to`; append-only
    change-log commits are exempt.
  - No commit may touch a path that is not allowed (the path policy: unlisted means frozen); merges are
    checked against each parent (`diff-tree -m`).
  - The `frozen` list holds `{"commit", "paths"}` objects.
- **A2 in the final.** For each scored week, every commit between X and that week's build commit must be in
  the change log as it stood at the build commit (`git show <build>:docs/status/livetest-changelog-2026.md`).
  A change logged only after its first use now fails that week's check.
- **Friday data cutoff (B3, A3).**
  - `--data-cutoff <week>` prints `{"week", "data_cutoff_utc", "last_kickoff_utc", "rule"}`: 12:00 UTC on the
    first Friday at least 72 hours after the week's last regular-season kickoff in `dim_game`.
  - `--week` exits 4 before the cutoff.
  - It then pins, for each dataset it reads (`processed/dim_game`, `processed/fact_player_game`,
    `processed/fact_dst_game`, `restricted/fact_ecr_weekly`), the first snapshot taken at or after the
    cutoff. The stores it uses refuse to read any dataset that has no pin.
  - The week-final check uses the pinned `dim_game`; not final gives exit 4.
  - The pins and a SHA-256 of the outcome rows (`outcome_rows_sha256`) are recorded in the sealed JSON, in
    the verification report and in the new public status file `scoring/weeks/<week>.json` (snapshots,
    hashes, check names; no metric).
  - A rescore reuses the pins recorded in the week's sealed JSON.
- **The final recomputes only from sealed parts.** Two gaps were found and closed:
  1. The final used to rebuild the consensus from fresh data (`_all_consensus`). The rank-to-points curve is
     fitted on earlier seasons' actuals, so a later correction could have reached L13-L16 and the D-077 pair
     set. Each week's consensus rows are now sealed as `sealed/<week>_consensus.parquet` (listed in
     `<week>.sha256` and verified at the final), and the final reads only those.
  2. The comparators' rows carried their own outcome columns from the comparators run, rebuilt at each
     extension. Every sealed week now takes the chain's outcome columns (`with_outcomes_of`), so all models
     are scored against the one pinned set of outcomes.

  `test_final_uses_only_sealed_parts_after_a_correction` replaces every outcome and consensus source with
  one that raises, and the final gives the identical verdict and binding lines.
- **Bootstrap subset (B2, A6).** The main bootstrap runs on the frames the 22 lines read: `chain_v2+cal` and
  the three line baselines' calibrated forms. The consensus bootstrap is unchanged.
  - *By construction:* `run_bootstrap` draws `default_rng(seed).integers(0, K, (n_boot, K))` per position
    from the cluster universe of the frames it is given, and computes each frame's metrics from that frame
    alone. The aligned frames share their keys, so the universe and the draw matrix do not depend on which
    other frames are included.
  - `line_frames` checks the universes at every position and falls back to the full computation if they
    differ; that fallback is tested.
  - *Demonstrated:* section "Bootstrap-subset proof" below.

### Bootstrap-subset proof

`tests/eval/test_bootstrap_subset_proof.py` runs QA's rehearsal worlds (from `tests/rehearsal/_rehearsal_world.py`'s
generators: 18 weeks, 8 labels and 23,040 rows in all, about 160 rows per week per label: `chain_v2` and the three line baselines, each raw and
calibrated, plus a synthetic consensus). It scores each world twice through `score_frames`, once with every frame
(`subset=False`) and once with only the line frames (`subset=True`). It then compares, exactly:

- the point value and the whole replicate array (`np.array_equal`) of every bootstrapped metric of every line frame;
- each beats-baseline line's delta, interval bounds and status;
- the start/sit deltas;
- the line interval strings, the 22 line results and the verdict text.

Floats are compared with `==`, not a tolerance. The gated cases run with `FFML_SUBSET_PROOF=1`. The evidence is in
`data/reports/livetest_subset_proof.json` (copy: `reports/livetest_subset_proof_2026-11-07.json`).

| world | draws | differences | lines passed | verdict (if every check passes) | wall, full / subset (min) | CPU, full / subset (min) |
| --- | --- | --- | --- | --- | --- | --- |
| A_pass | 500 | **0** | 22 | APPROVED | 8.0 / 4.8 | not recorded |
| B_qb_narrow | 500 | **0** | 21 (L17 fails) | NOT APPROVED | invalid (the session paused) / 4.8 | not recorded |
| C_accuracy_tie | 500 | **0** | 21 (L07 fails) | NOT APPROVED | **4.1 / 2.6** (uninterrupted) | 7.1 / 4.6 |
| A_pass | 5,000 | **0** | 22 | APPROVED | full not uninterrupted (137.7) / **59.5** (uninterrupted) | 127.3 / 114.0 |
| C_accuracy_tie | 5,000 | **0** | 21 (L07 fails) | NOT APPROVED | both contended (81.8 / 46.0) | 200.3 / 108.3 |

There are zero differences in all five cases, and each verdict is the one the world was built for. Two fast tests
always run in the suite:

- `test_subset_identical_on_fixture_weeks`, the same comparison on the scorer's fixture weeks;
- `test_line_frames_falls_back_when_universes_differ`, which gives a non-line frame a cluster the line frames lack
  and checks that the scorer falls back to every frame.

**Adopted.** Per A6 the subset is turned on (`BOOTSTRAP_LINE_FRAMES_ONLY = True`) in a separate commit after the
5,000-draw cases. bd3bf21 runs the full computation. Each sealed weekly JSON records which was used
(`bootstrap_frames`: `full` or `lines`).

### Timing at 5,000 draws

Wall times are quoted only from uninterrupted runs. Two of the long runs were not uninterrupted:

- **A_pass at 5,000 draws, full pass.** Between 22:04 and 23:45 the process averaged about 0.5 cores, against about
  2 afterwards. That is background throttling, or the session being idle.
- **C_accuracy_tie at 5,000 draws, both passes.** These ran alongside QA's full rehearsal (`pytest -m slow
  tests/rehearsal`, started at 01:33 with up to three worker processes).

Their wall times are in the table for completeness only. The A_pass subset pass at 5,000 draws ran from 00:22 to
01:21, before QA's run started, at about 1.9 cores.

| measurement | full | line frames only |
| --- | --- | --- |
| rehearsal world, 500 draws (C, uninterrupted) | 4.1 min | 2.6 min |
| rehearsal world, 5,000 draws (A, uninterrupted) | not measured uninterrupted | **59.5 min** (0.71 s per draw) |
| realistic-size window, 500 draws (EV-4.6, measured) | 11.6 min | 5.3 min |
| realistic-size window, 5,000 draws (EV-4.6, extrapolated from 100 and 500 draws) | about 114 min | about 53 min |

The rehearsal world has 8 labels, so dropping 4 of them saves less there (a ratio of about 0.6) than on the live
window, where 10 of 14 labels drop (about 0.46).

The one direct, uninterrupted 5,000-draw measurement is about 10% above EV-4.6's extrapolated rate (0.71 against
0.64 s per draw). It was taken on a world with about a third of the live window's rows per label, which shows the
fixed per-draw work dominates. So I treat EV-4.6's 5,000-draw figures as optimistic by up to about 10%, and plan
on about 60 minutes (subset) and about 125 minutes (full) for the live window's cumulative 5,000-draw step at the
18th week.

**At the 18th week the weekly job is therefore about 70 minutes with the subset:**

- cumulative 500 draws, about 5 minutes;
- cumulative 5,000 draws, about 60 minutes;
- the one-week score, the comparators' extension and the loading, a few minutes.

With the full computation it is about 140 minutes. The final, 500 plus 5,000 draws, is about 65 minutes against
about 135. The runner now launches the scorer above normal priority with a 360-minute timeout (A7,
`scripts/livetest_week.py`). Even the full computation under the throttling seen here (about 1.5 times as long)
fits inside that timeout, with margin.

### Notes for the Data Engineer and QA

- **Sealed records now list up to three files:** `<week>.parquet`, `<week>_consensus.parquet` (when the
  week has consensus rows) and `<week>.json`. The runner publishes `<week>.sha256` verbatim, so nothing
  changes for it. QA's rehearsal reproduction that rewrites a two-file record will need the third line.
- **The rehearsal driver** calls `final_evaluation(consensus=LS._all_consensus(prereg))` in its
  forced-sensitivity hook. The keyword is kept as an explicit override, so that still runs. The CLI final
  never passes it and reads only sealed consensus. The full rehearsal rerun (D-116 C) should drop the
  override.
- **Pinned snapshots and pruning:** `SnapshotStore.prune` keeps the newest 8 snapshots. A week's pinned
  snapshot can therefore be pruned within about a week of nightly refreshes. The final never needs it (it
  reads sealed parts), but a rescore of an old week would. If rescoring must stay possible, protect the
  pinned ids from pruning.
- **Snapshot policy:** the refresh observed on 2026-11-04 wrote a new snapshot of every processed and
  restricted dataset. If a future refresh skipped an unchanged dataset, `--week` would wait (exit 4) for its
  next snapshot.
- **QA's rehearsal pins bd3bf21** (`FFML_REHEARSAL_COMMIT`), which runs the full computation. QA's uncommitted
  rehearsal test asserts `bootstrap_line_frames_only is False` and `bootstrap_frames == "full"`. Against the
  subset commit those become `True` and `"lines"`; nothing else in the rehearsal should change, because the
  line results are identical.
- **`--data-cutoff` for a week not in `dim_game`** returns 4 (not yet; for example 2027 weeks before the
  schedule is published). The stub returned 2.

### Tests

On main with the subset on (the second EV-4.7 commit), one process at normal priority alongside QA's rehearsal:

- `pytest -q tests/eval` gave **179 passed, 5 skipped** in 28 minutes. The 5 skips are the gated rehearsal-world
  proof cases.
- That includes the 11 new scorer tests for D-116 and the two fast subset tests.
- `ruff check ffml tests/eval scripts` is clean.

The gated proof, `FFML_SUBSET_PROOF=1 pytest tests/eval/test_bootstrap_subset_proof.py`, was run over two sessions:

- A, B and C at 500 draws, then A and C at 5,000 draws;
- all 5 passed (the last three cases in 5 h 32 min);
- the evidence is in `data/reports/livetest_subset_proof.json` (local), with a copy committed as
  `reports/livetest_subset_proof_2026-11-07.json`. Both hold synthetic worlds only, with no player rows.

## Freeze review (EV-4.8, D-117)

Part 1, 2026-11-07. This reviews the pre-registration at d9c6808 (`docs/status/livetest-preregistration-2026.md`) against
the code at the candidate X = 2872d13 (D-118). `git diff --name-only 2872d13 d9c6808 -- ffml scripts pyproject.toml`
is empty, and X..d9c6808 touches only `docs/` and `tests/`, so the scorer and the runner at d9c6808 are the code at X.
Part 2, the review of QA's full rehearsal at 2872d13, follows when it lands and ends this section with the freeze line.

### Hashes and ancestry

- **`rules_sha256` reproduces.** The canonical JSON of `rules` without the field
  (`json.dumps(sort_keys=True, separators=(',', ':'), ensure_ascii=False)`, UTF-8) hashes to
  `8c4212e3d77614c7f34a923a2b5905b6aa05173e4439aaaace3d0f28a0ba7574`. That equals the stated value and the scorer's own
  `rules_hash`.
- **`lines_sha256` = `lines_spec_sha256()`** at X: `ea706e17578e6b5caa85eb9db3fbffa6758a6bc85dae2169a707b425b91de2ad`.
- **`frozen_commit_must_include`.** All eight commits (1d0d2b6, 2872d13, 6e29122, a2f2af0, a30cd08, b22973f, bd3bf21,
  d24a43a) are ancestors of 2872d13 (`git merge-base --is-ancestor`). No code reads this key; it is a one-time check
  at the freeze, done here by hand.
- **The scorer's pre-metric checks.** I ran `livetest_integrity_checks` on a copy of d9c6808 with `commit` filled as
  2872d13. These pass:
  - approval lines, rules hash, bootstrap and seeds;
  - frozen commit, allowed paths;
  - final evaluation commit (X..d9c6808 touches 7 allowed paths).

  "Final evaluation change log" fails: three commits without an entry. See F2.

### The D-116 / D-118 keys against the code

| key | stated | code at X | result |
| --- | --- | --- | --- |
| `scoring.when`, `window.weekly_scoring` | the Friday job at or after the week's data cutoff, once every game is final; verification-only output plus sealed parts | `score-week` asks `--data-cutoff`, does nothing before it, and exits 4 (retry) while the week is not final in the pinned `dim_game`; the scorer prints no metric | match |
| `data_cutoff` | 12:00 UTC on the first Friday at least 72 h after the week's last kickoff in `dim_game`; `--data-cutoff <week>`; `--week` exits 4 before it | `data_cutoff()`: REG games, max kickoff + 72 h, then the first Friday 12:00 UTC at or after that. The runner calls `--data-cutoff <key>` and reads `data_cutoff_utc`. Exit 4 matches the runner's `SCORER_NOT_YET` | match |
| `snapshot_pins` | first snapshot at or after the cutoff; ids and `outcome_rows_sha256` sealed and in `scoring/weeks/<week>.json`; a rescore reuses them; the final reads only sealed parts, including the consensus; no pruning | `choose_pins` / `pinned_stores`. The ids and the outcome hash are in the sealed JSON (its hash is in the pushed `.sha256`) and in `data/livetest/scoring/weeks/<week>.json`. A rescore takes `data_snapshots` from the sealed JSON. The final reads `sealed/<week>.parquet` and `<week>_consensus.parquet`. No code calls `SnapshotStore.prune`, and the process step writes every table on each run | match (wording: F6) |
| `prebuild_check` | `--prebuild-check --from <last build's code commit> --to <pinned HEAD>`; logged entry and no frozen path; exit 5 alerts and retries; a miss only at the deadline; the final's per-week A2 check | The runner passes exactly these (`last_build_commit`, `code_head`). Exit 5 or any failure blocks the build with a critical `prebuild_blocked` alert, retried every 30 minutes; the miss reason is "pre-build check blocked". The scorer checks coverage at `--to` (append-only change-log commits exempt) and frozen paths per commit (merges against each parent). The final checks A2 at each build commit other than X | match |
| `final_fetch` | the fetched GitHub ref, checked against `ls-remote`; each scored week compared with its recorded hashes; exit 75 before computing or writing anything | `fetch_branch_ref` raises `FetchFailed` on a fetch failure, an `ls-remote` failure or a ref mismatch, and the CLI returns 75 before any computation. A mismatch in a week's hashes is an integrity failure | match (wording: F5) |
| `committed_weeks` | a commitment or sealed record on the branch means the week is never excluded; a stray MISSED marker is listed with its week, commit time and `created_at` | The final never excludes such a week; `flagged_marker` gives week, commit, committer time (`%cI`), `created_at` and reason, in the report and in `sealed/final_lines.json` | match (wording: F7) |
| `bootstrap_frames` | `lines`, identical by construction, shown at 500 and 5,000 draws | `BOOTSTRAP_LINE_FRAMES_ONLY = True`, `LINE_FRAME_LABELS`, the fall-back when universes differ, and `bootstrap_frames` in the sealed JSON. The evidence file is at X | match (consequence: F4) |
| `expected_verdict` | about 2027-11-12, between about 2027-11-05 and 2027-11-19 | 2027 week 9's Monday game, about 2027-11-09 01:15 UTC, plus 72 h gives the cutoff Friday 2027-11-12 12:00 UTC | consistent |
| exit codes | scorer 4, 5, 75; build-week 0-4 | scorer `EXIT_RETRY_LATER` 4, `EXIT_BLOCKED` 5, `EXIT_FETCH_RETRY` 75; runner `SCORER_NOT_YET` 4 and `PREBUILD_BLOCKED` 5; build-week codes as in `ffml/cli.py` and the runner's handling | match |

**Demonstrated with a filled copy** (`--prereg`):
- `--prebuild-check --from "" --to 2872d13` exits 0, with 0 commits.
- `--from "" --to d9c6808` exits 5, listing 997fb22, 7d5749b and d9c6808 as uncovered.

Without a filled `commit` anywhere (today), every call exits 5 ("no pre-registration with a filled commit"), as intended.

**Nothing depends on 2026 week 10 or later.** Every mention of weeks 10-18 is one of:
- the window;
- the deadlines "as scheduled 2026-11-04", which are schedule, not outcomes;
- the verdict dates.

The stored history ends at 2026 week 7, and the walk-forward from week 8 is computed at build time. The latest fact
recorded is the week 9 rehearsal build of 2026-11-04.

### Findings

**F1. The pinned worktree at X makes every weekly "code commit" check fail. Fix before the freeze (procedure and
docs; no frozen code).**

- *What happens.* RUNBOOK section 3 step 1 says "Use the commit that was pre-registered", and the pre-registration's
  `build.code_worktree` says "a worktree pinned at the frozen commit". So the worktree sits at X. Its copy of the
  pre-registration still has `"commit": "<frozen commit>"`, because the field is filled in a later commit.
  - `score-week` runs `--week` there (`cwd` = the worktree, `PROJECT_ROOT` = the worktree), so `load_prereg()` reads
    that copy. It is also the pre-D-119 text, with rules hash 08fcf211.
  - `verify_week` then records "code commit: the pre-registration has no frozen commit yet" as a failed check for
    every week. Confirmed: `_frozen_commit()` of the copy at 2872d13 is `None`.
- *Why it matters.*
  - The verdict is not affected, because the final drops the weekly result and recomputes the relation against X.
  - But every weekly verification report and every public `weeks/<week>.json` would show a failed check.
  - The only check of a build's code commit before the final would be inert for the whole window.
  - Running the final from that worktree gives `Verdict: INVALID (frozen commit …)`. QA's
    `test_final_from_the_pinned_worktree_at_x_is_invalid` asserts exactly that, and nothing in the runbook says
    where the final must run.
- *Coverage.* QA's main flow runs the runner unpinned from the clone's HEAD, a descendant with the field filled. It
  never exercised the X-pinned setup. `repro_pinned` scores one week from the worktree at X but asserts only the exit
  code and a clean worktree.
- *Fix.* Pin the worktree at Y instead of X. Y is the append-only change-log commit made after the commit F that
  fills `commit` (see F2). Use it for `livetest_setup_worktree.py --commit Y` and `init --expected-commit Y`.
  - The pre-registration already allows this: "valid at the frozen commit X, or at a descendant Y". Y differs from X
    only under `docs/`, `tests/` and `reports/`.
  - `verify_code_pin` compares HEAD with `--expected-commit` only, and the build does not read the
    pre-registration's commit.
  - Commitments then record `code.commit` = Y. The weekly check passes (X to Y allowed). The pre-build check
    `--from "" --to Y` passes once X..Y is logged. The final's A2 check reads the change log at Y.
  - The weekly scorer then reads the frozen text of the pre-registration, and the final can run from either
    checkout.
  - Change `build.code_worktree` (not in `rules`; the hash is unchanged) and RUNBOOK section 3 steps 1-2 to say "the
    change-log commit after the freeze commit". Add one line on where the final runs.
- *Alternative.* Make the scorer resolve X through `main` for `--week` too, as `--prebuild-check` does. That changes
  a frozen file, so it means a new X and a new full rehearsal six days before the first deadline. I do not recommend
  it.

**F2. Log every commit after X. Required at the freeze.**

- The change log has no rows. X..HEAD already holds 997fb22, 7d5749b and d9c6808; this commit, QA's result commits,
  any text fixes and F will follow.
- The final's "final evaluation change log" check makes the test INVALID if any commit in X..HEAD has no entry. With
  F1, the pre-build check also needs every commit in X..Y logged before the first build.
- *Order:*
  1. text fixes;
  2. F (only the `commit` field);
  3. one commit that only appends rows for every commit in X..F. That commit is Y, and is itself exempt.

  If that commit touches anything else it is not exempt and needs its own entry.
- Every later commit on main must be logged before the final.

**F3. Stale "reads" statements contradict `snapshot_pins`. Fix before the freeze (outside `rules`; the hash is
unchanged).**

- `scoring.reads[2]` says "actuals from ffml.scoring.actuals on the latest processed data".
- The prose "Reads" bullet says "on the processed snapshot current at scoring time".

The scorer uses the pinned snapshots, and the final uses only sealed parts. Two more places are out of date:
- the prose "When" bullet does not mention the cutoff (a pointer to the new D-116 section is enough);
- the prose "Computes" bullet lists the sealed `.parquet` and `.json` but not `<week>_consensus.parquet`.

**F4. `rules.comparators.rows_used` claims rows are "reported" that the frozen scorer never reports. Decide before
the freeze (inside `rules`, so the hash changes).**

- `rows_used` and the prose say "rolling_avg_3 and rolling_ew_035 reported". The final report has no per-model
  table, so they were never reported.
- With the subset on, their metrics (and `direct_lgbm`'s) are no longer computed: `week_points` in the sealed JSON
  holds only the four line frames.
- They still enter the alignment, the D-077 reference ranking and the pair counts, which are unchanged.
- *Fix.* Reword, for example to "rolling_avg_3, rolling_ew_035 and direct_lgbm are run for the D-077 reference
  ranking only; no metric of them is reported", and recompute `rules_sha256`. Or record it as a known deviation.
  I recommend the reword.

**F5. Wording (low): `scoring.final_fetch` and the prose "The final".** Change "if the fetch or the check fails it
exits 75" to "if the fetch or the ls-remote check fails". A mismatch in a week's commitment or summary hash is an
integrity failure (INVALID), not exit 75. `rules.verdict` already states this correctly.

**F6. Wording (low): `scoring.snapshot_pins`.** Four changes:
- The scorer also pins the restricted consensus archive (`restricted/fact_ecr_weekly`), not only processed datasets.
- "Corrections after the cutoff are ignored" holds exactly for corrections after the pinned snapshot, the first one
  at or after the cutoff; normally that is the Friday job's own refresh.
- D-118's no-pruning rule (this key and the runbook) names processed snapshots. It should also name the restricted
  consensus snapshots, which a rescore pins.
- `scoring/weeks/<week>.json` is the local `data/livetest/scoring/weeks/<week>.json`, not a branch path.

**F7. Wording (low): `scoring.committed_weeks`.** "The GitHub commit time" is the branch commit's committer timestamp
(`%cI`), written by the committing machine and shown by GitHub. It is not a server-side push time. It is
informational, since a flagged marker never changes the verdict. Say "the committer time of the branch commit that
added it".

### Residual risks (runner and runbook; allowed paths, fixable after the freeze with a log entry)

- **R1. A Friday refresh that straddles 12:00 UTC.** The runner refreshes only when the LATEST `dim_game` snapshot is
  before the cutoff, but the scorer pins four datasets. A refresh that straddles 12:00 UTC could leave `dim_game` at
  or after the cutoff and another dataset before it. The scorer then exits 4 on every repetition, and no refresh
  runs until the next build window. That is a delay of days, never a wrong result. Fix: refresh after an exit 4 for
  "no snapshot at or after the cutoff". It is unlikely, because no scheduled refresh runs near Friday 12:00 UTC.
- **R2. A game that never becomes final.** Such a game (for example the 2022 week 17 no contest) leaves its week
  unscoreable. Because `pick_week_to_score` always takes the oldest unsealed week, automatic scoring of every later
  week stops too; `score-week --season --week` still works by hand. This predates D-116. It needs an owner ruling if
  it happens, and the runbook should say so.
- **R3. The 2027 re-initialisation.** The runner holds one `test_season`. The runbook has no step for
  `init --season 2027 --weeks 1-9` (the runner mentions the re-init under QA-F30). Write it before 2027 week 1.

### Tests (fast only; QA's full rehearsal is running)

These 7 tests passed in 18 s:
- `tests/data/test_livetest.py::test_the_real_preregistration_sets_the_allowed_misses`
- `tests/data/test_livetest.py::test_a_real_run_refuses_without_the_preregistration`
- `tests/eval/test_report_holdout.py::test_prereg_lines_are_the_22_registered_lines`
- `tests/eval/test_livetest_score.py::test_prereg_window_and_week_parsing`
- `tests/eval/test_livetest_score.py::test_data_cutoff_rule`
- `tests/eval/test_livetest_score.py::test_prebuild_check_logged_before_first_use_and_frozen_paths`
- `tests/eval/test_bootstrap_subset_proof.py::test_line_frames_falls_back_when_universes_differ`

The hash, ancestry, CLI and integrity-check evidence above was produced by direct calls into the code at d9c6808.

### Part 1 result

**The pre-registration matches the code at X on every D-116/D-118 key. Both hashes reproduce, and nothing depends on
2026 week 10 or later.** It is not ready to freeze as it stands:
- F1 and F2 must be done in the freeze procedure;
- F3 and F4 must be fixed in the text;
- F5-F7 are wording.

None of them needs a change to frozen code, so X = 2872d13 can stand. The freeze line follows Part 2.

### Candidate X = 071c3a4 (D-125): audit, 12e2faa and 891f56a (EV-4.10, 2026-11-10)

**Frozen paths since the earlier candidate.** `git diff --name-only 2872d13 071c3a4` touches these frozen paths only:
- `ffml/eval/livetest_score.py`: my D-121 (686fbb5) and `pinned_datasets` (f5a2ff2) commits;
- `ffml/sim/predict.py`: 12e2faa.

The D-124 app work (`api/`, `web/`, docs) and D-122 (`ffml/data/`) are on allowed paths. Part 1's F1/F2 went to D-120,
F3-F7 to 1943195, R1/R3 to b31a11e and d9e8b79, and R2 to D-121 (686fbb5). Part 2 re-reads the pre-registration at X.

**Inputs audit at X: passes (D-123).**
- `pytest tests/eval/test_inputs_audit.py` gives 8 passed on a clean export of 071c3a4 (`git archive`, run with that
  tree's `ffml`). It also gives 8 passed in the main checkout.
- No file under `scripts/` names the restricted dataset any more: the runner reads `pinned_datasets` from
  `--data-cutoff`.
- The audit itself is unchanged.

**12e2faa (replays default to `data/predictions_private/replays_test_only`): cannot change a commitment or a
verdict.**
- The new branch runs only when `replay=True` and no `root` is given. It changes the directory written to, nothing
  that is computed.
- Every call site:
  - The build (`ffml/sim/livetest.py` `build_week`) calls `run_predict(private_test=True, ...)`, which sets
    `root = private_store(rehearsal)`. `run_predict` refuses `replay` together with `private_test`.
  - `ffml/cli.py` never passes `replay`.
  - The scorer, the comparators run and the walk-forward history (`recompute_week`) do not call `run_predict`.
- So no commitment's rows, files, paths or hashes change, and nothing the scorer reads changes.
- `tests/sim/test_predict.py -k replay`: 3 passed.

**891f56a (D-122: skip a newest season that is not published yet).**
- *What changed.* Only the `SeasonNotAvailable` branch of `ingest_dataset` is new. A missing season that is newer than
  every season in the dataset's LATEST manifest (`seasons`) and every season loaded in this run is now skipped. It is
  recorded as `not_yet_published` and is not a D-028 gap. A later season loading after the skip still fails as a
  gap. Published seasons load exactly as before. `tests/data -k publish`: 3 passed.
- *Commitments.* The new branch fires only when a requested season has no file upstream. That never happens for the
  2026 weeks: the runner requests `first_season`-2026, all published. From 2027 week 1 the refresh now succeeds with
  exactly the published data, where the old code failed and the runbook's manual per-dataset workaround produced the
  same seasons. No row of a published season changes, so I confirm it cannot change a commitment beyond what the
  documented procedure already produced.
- *Verdict: one new path, F8 below.*

**F8. D-122 can let 2027 week 1 be scored against a snapshot without 2027 stats. Fix before 2027 week 1 is scored
(about 2027-09-17); not a blocker for this freeze.**

How it happens:
1. Before D-122, a requested season with no file upstream failed the ingest step. `run_refresh` stops at the first
   failed step, so no processed snapshot was written, and the scorer waited (exit 4).
2. With D-122, suppose the 2027 player or team stats file is still not published at the refresh after 2027 week 1's
   data cutoff (at least 72 h after the Monday game; nflverse is usually hours late, not days). The refresh then
   succeeds and writes `fact_player_game` / `fact_dst_game` without 2027 rows.
3. `choose_week_pins` pins the first `dim_game` snapshot at or after the cutoff in which the week is final. The
   schedule is final, so it pins this snapshot.
4. `join_outcomes` gives every row `actual` null and `active` False, and nothing in the scorer checks that the
   week's outcomes exist. Because the pins are fixed, the week is either refused against the same pins on every
   retry, or sealed against empty outcomes.

It can only happen in the first scored week of a season, because once a 2027 file is published a later missing 2027
is a gap again. It is rare, but it lands on a binding week. Options:
- (a) In `ffml/data/` (allowed path): skip a season as "not yet published" only while no regular-season game of that
  season is final in the schedule. After that the old failure returns.
- (b) In the runner (`scripts/`): the Friday scoring refresh requires the scored week's season for the stats
  datasets. This needs a flag from (a)'s module.
- (c) In the scorer (frozen; a new X and a new rehearsal): treat "no outcome rows for the week in the pinned
  snapshots" as not final yet, and pin a later snapshot.

I recommend (a), logged in the change log under the `ffml/data/` rule. The recomputed feature rows of committed
weeks stay byte-identical, because the change can only make an ingest fail. It must land before 2027 week 1's cutoff.
Until then nothing can trigger it.

### Part 2: run 7 at X = 4799913 (provisional; 2026-11-12)

This is my own reading of the raw evidence in `reports/evidence/rehearsal/`. The four files hash as `MANIFEST.md`
says, both as committed (LF) and in the working copy. Run 8 at base 34a6644 confirms it; the freeze line follows run
8.

**Provenance.**
- Every world records X = 4799913 and base Z = 765fb1a, with an empty `diff_x_head_frozen_code`.
- The final runs assert that `ffml` was imported from the world's clone.

**Pre-registration at 765fb1a.**
- It is unchanged through 34a6644 and HEAD (`git diff --quiet`).
- `rules_sha256` 3c187d88… reproduces, and `lines_sha256` equals `lines_spec_sha256()`.
- `prereg_window` at X gives 2026 w11-18 plus 2027 w1-10, 18 weeks, equal to `window.weeks_total` (line 73).
- The pass rates (lines 255-257 and 369) are 0.580 / 0.540 / 0.498 corrected, with 0 of 5,000 for no edge.
- The verdict date, about 2027-11-19 (line 312), is correct: 2027 week 10's Monday game is about 2027-11-16
  01:15 UTC, and 72 h later the first Friday at 12:00 UTC is 2027-11-19.
- All 15 `frozen_commit_must_include` commits are ancestors of X.
- The deadline table covers 2026 w11-18.

**Pass rates, reproduced** with `ffml/eval/power.py` `simulate_rules`, 5,000 replicates, 2 threads:

| run | corrected | uncorrected | no edge |
| --- | --- | --- | --- |
| seed index 11 (as recorded) | 2,899 = 0.580, **exact** | 3,682 = 0.736, **exact** | 0 |
| seed index 21 (independent) | 2,924 = 0.585 | 3,667 = 0.733 | 0 |

The independent seed is within one standard error (0.007). The rise from the original window's 0.557 shows at both
seeds, so it is not one seed's noise.

**Scenario table (run 7).** Every world's final matches its design:

| world | design | final verdict | lines not passed | checks |
| --- | --- | --- | --- | --- |
| A_pass | good model, no miss | APPROVED (also the repeat with the stores moved away) | none | 102, all PASS |
| G_unfinal_game | a game never final (2026w14), a game final late (2027w02) | APPROVED | none | 103 (one `games excluded (D-121)`), all PASS |
| P_prebuild | blocked builds: one fixed in time, one left, a frozen-path change | APPROVED over 17 weeks | none | 97, all PASS |
| E_stat_correction | corrections after the cutoff | APPROVED | none | 102, all PASS |
| D1_one_miss | 2026w12 missed | APPROVED over 17 weeks | none | 97, all PASS |
| D2_two_misses | 2026w12 and the last week 2027w10 missed | APPROVED over 16 weeks | none | 92, all PASS |
| B_qb_narrow / B2 (rebuilt) | QB intervals too narrow | NOT APPROVED (B2 = B) | L17 only | 102, all PASS |
| C_accuracy_tie | one accuracy tie | NOT APPROVED | L07 only | 102, all PASS |
| D3_three_misses | three misses | INVALID (miss rule) | not computed | 87; only `missed weeks (miss rule)` fails |

The check counts follow 12 global checks plus 5 per scored week, plus one for G's excluded week.

Run 6's eleven final records recur in run 7 with identical verdicts and lines.

**The run 7 failure is a test defect, and 34a6644 fixes it correctly.** I checked this independently of D-131.
- The scorer emits `"{wk} commitment"` (`ffml/eval/livetest_score.py:1915`) and `"{wk} branch commitment"`
  (`:1944`) for every scored week.
- The assertion at `tests/rehearsal/test_livetest_final_rehearsal.py:190` (before the fix) counted names ending in
  `" commitment"`. It therefore counted both, giving 36. It could never pass in a complete pass-world run.
- In run 7's evidence each of the five per-week kinds occurs exactly 18 times, and all 102 checks are PASS.
- The fix (`:190-191`, `re.fullmatch(r"\d{4}w\d\d " + re.escape(kind), n)`) makes each count exact. It weakens
  nothing: the four other kinds already counted 18 either way, and the earlier required-names assertion is
  unchanged.
- I replayed every test function at 34a6644 against run 7's results, calling them directly on the JSON, with
  `FFML_REHEARSAL_COMMIT=4799913` and `FFML_REHEARSAL_BASE=765fb1a`. This replays assertions only; I did not rerun
  the rehearsal. Result: **44 passed, 1 skipped** (the optional real 5,000-draw run). That includes every assertion
  after line 191 that run 7 never reached.

**Commits after X.**
- 765fb1a, fac6fcb, b2900b0 and 34a6644 touch 13 files, all on allowed paths (`path_allowed`; none frozen).
- `scripts/livetest_changelog.py --from 4799913` lists exactly these four as unlogged, matching
  `git log 4799913..HEAD`. With this review commit and run 8's evidence commit, they go in D-120's append-only
  change-log commit Y. That commit must touch only the change log.

**The app text in b2900b0 is consistent with the pre-registration.** `api/services/meta_content.py:124-127` states
the D-127 window, the verdict about 2027-11-19 and about a 58% chance. b2900b0 also reworded the change log's header
(`docs/status/livetest-changelog-2026.md:4`). That is not a row, and b2900b0 is on the helper's list.

**Findings**
- Low: `tests/rehearsal/_rehearsal_world.py:47` and `:50`, the defaults `FFML_REHEARSAL_COMMIT` = 071c3a4 and base
  = the same. Without the environment variables, the suite rehearses the old candidate and the D-110 window
  (`FIRST_WEEK` 2026w10). Runs 7 and 8 set both, so the evidence is not affected. Update the defaults to X, or make
  the variables required (`tests/` is allowed, with a log row).
- No scorer, runner or pre-registration defect was found in run 7.

**Provisional verdict on run 7: pass.** It holds if run 8 (base 34a6644) gives 44 passed and 1 skipped, with every
world's verdict and lines identical to run 7's.

### Part 2 final: run 9 and the freeze line (2026-11-12)

Run 8 stopped after two worlds (D-132). After the restart, D-133 replaced it with run 9: all ten worlds at
X = 4799913, base Z = cfcfcd6, one worker, 14:53 to 19:48 UTC. The bar is unchanged from the provisional Part 2:
44 passed and 1 skipped, with every world's verdict and lines identical to run 7's.

This is my own reading of the raw files, made with my own comparison scripts (kept in my scratchpad, outside the
repository). I did not rerun the rehearsal or any build, and I wrote nothing except this subsection.

**1. Evidence integrity: passes.**
- For each of the eight files in `reports/evidence/rehearsal/`, I ran `sha256sum` on the working copy and on
  `git show HEAD:<path>`. Every raw hash and every LF hash equals MANIFEST.md, and each LF hash also equals the
  working copy with CR removed.
- Run 9's three files are byte-identical to the run's original outputs, which are still in the orchestrator's
  scratchpad (`evidence_run9.json`, its `.partial.jsonl` and `run9.log`; raw SHA-256 `1a405e87…`, `5345a9da…`,
  `ada5162c…`).
- The partial file holds 72 records with the same keys as run 7's:
  - 10 `freeze`, 15 `step`, 29 `final` and 8 single records;
  - 10 `world_done`, each with `error` null.

**2. Run 9 against the bar: met.**
- *Pytest* (`run9_X4799913_pytest.txt`): 45 collected, `44 passed, 1 skipped in 17702.48s (4:55:02)`, `exit=0`.
  - The skip is `test_real_5000_draw_run_reports_beside_the_binding_verdict`, which needs `FFML_REHEARSAL_5000=1`.
  - `test_pass_world_is_approved`, run 7's one failure, passes. My replay of the assertions on run 7's results is
    now confirmed by a real run.
- *The test code is the committed code.* The tests ran from the main checkout:
  - `git diff cfcfcd6 HEAD -- tests/rehearsal` is empty;
  - `ffml/`, `scripts/` and `pyproject.toml` are unchanged from X to HEAD.
- *Provenance, in all ten worlds:*
  - `x` is 47999132bba4… and `base` is cfcfcd6dce53…;
  - `base_commits` is 8, which equals `git rev-list --count 4799913..cfcfcd6`;
  - `diff_x_head_frozen_code` is empty and there is no `error`;
  - the probe shows `ffml` imported from the world's clone;
  - the 20 paths in `diff_x_head` are exactly `git diff --name-only 4799913 cfcfcd6`.
- *Every final and refusal record, compared with run 7.* A record is any dict with `label` and `proc`. Each run has
  29, at the same 29 paths. For each record I compared these fields, with commit shas and the temp root masked:
  - exit code, flags and the verdict lines (stdout, report and `final_lines.json`);
  - the 22 line results, including their `detail` and `intervals` text, which carry the line values;
  - check names and statuses, in the report table and in `final_lines.json`;
  - flagged markers, excluded games, unsealed weeks, sensitivity, `n_boot`, `dry_run` and sealed-directory changes.
- *Result:*
  - 27 records are identical on all of these.
  - 2 records (A's `fill_unlogged` and `fill_placeholder`) are identical except the INVALID text. It lists 3
    unlogged commits in run 7 and 5 in run 9.
    - The scorer prints at most five (`missing[:5]`, `livetest_score.py:1872`).
    - Run 9's clone has 10 unlogged commits (8 base commits, plus the digest and fill commits); run 7's had 3.
    - The same check fails and the verdict is the same.
  - The check details differ in 72 places, all in two checks. "Final evaluation commit" counts allowed paths
    (for example 4 against 21), and "final evaluation change log" counts logged commits (for example 4 against 11).
    Both counts grow with the base.

| world | run 9 final (equal to run 7) | lines not passed | checks passed |
| --- | --- | --- | --- |
| A_pass (also the repeat, the Y-pinned final, the stray-marker final) | APPROVED, exit 0 | none | 102 of 102 |
| A's eight post-freeze commit scenarios | INVALID (change log; frozen path for `frozen_path`) | none | 101 of 102 |
| A's six refusals: four tampered or missing sealed records, the marker attack, the failed fetch | refused, exit 3 (the fetch: exit 75); no report | n/a | n/a |
| G_unfinal_game | APPROVED | none | 103 of 103 |
| P_prebuild | APPROVED, 17 weeks | none | 97 of 97 |
| E_stat_correction (the final after a local rescore is refused: sealed files differ from the branch record, exit 3) | APPROVED | none | 102 of 102 |
| D1_one_miss | APPROVED, 17 weeks | none | 97 of 97 |
| D2_two_misses (the final is refused while 2027w10 is not final: exit 3) | APPROVED, 16 weeks | none | 92 of 92 |
| B_qb_narrow, B2_qb_narrow_repeat | NOT APPROVED | L17 | 102 of 102 |
| C_accuracy_tie | NOT APPROVED | L07 | 102 of 102 |
| D3_three_misses | INVALID (miss rule) | not computed | 86 of 87 |

- The 30th record in MANIFEST.md's count is world E's `rescore` call. It exits 0 in both runs, and its stderr differs
  only in the holdout-access timestamp.
- *Every other difference in the two files* is one of three kinds:
  - commit shas, and the counts of commits or paths after X:
    - `freeze.listed`;
    - the helper's appended rows (3 against 10);
    - the pre-build check output (all 15 differing outputs have the same `ok` and the same number of uncovered
      commits; only `commits` rises by 7);
  - wall-clock times:
    - holdout-access lines, the push `external_timestamp` and score-event times;
    - a 1-second difference in `recorded_at` in two missed markers;
    - G's `scored_at` (the simulated clock plus elapsed run time);
  - hashes of records that contain these. Each week's commitment and sealed `.json` hold `code_commit`,
    `branch_commit`, `commitment_sha256` and `scored_at`.
- *What carries the result is identical.* In every world and week these match run 7:
  - the sealed `.parquet` and `_consensus.parquet` hashes;
  - `outcome_rows_sha256`, `data_cutoff`, the pinned `data_snapshots` and the snapshot id lists;
  - every weekly step's status and exit code.
- I accept the differences above. Each follows from the other base, temp root or wall clock, and none reaches a
  verdict, a line, a value or a check status.
- One worker instead of two only lengthened the run: 4 h 55 min, against 2 h 6 min for run 7.

**3. Commits after X: all on allowed paths, and the helper can log every one.**
- `git log --oneline 4799913..HEAD` gives 11 commits, none a merge: 765fb1a, fac6fcb, b2900b0, 34a6644, 40065aa,
  80c1650, eeb5c6c, cfcfcd6, f43d69f, ba67ea1, 41e2dc0.
- `git diff --name-only 4799913 HEAD` gives 25 paths. All are under `README.md`, `api/`, `docs/`, `reports/` and
  `tests/`; the test paths are `tests/api/test_meta_season.py`, `tests/models/test_chain_ablation.py` and two
  files in `tests/rehearsal/`.
- *Per commit* (`git diff-tree -r -m`), I checked each path twice: with the scorer's `allowed_prefixes` and
  `path_allowed`, and with my own prefix rule written from `path_policy`. No path is disallowed and none is frozen.
  - The scorer's frozen list leaves out `pyproject.toml` and `requirements.lock.txt`, because they have no slash.
  - Its rule denies by default, so both stay frozen (`path_allowed` is false for both). There is no effect.
- *The helper.* `.venv\Scripts\python scripts\livetest_changelog.py --from 4799913` (listing only) lists the same
  11 commits, each `auto`, and exits 1, which in listing mode means rows are still missing.
  - None needs a hand-written row or a ruling, so the helper can row all of them in Y.
  - The change log has no rows yet.
- *The two CI fixes.* cfcfcd6 is a 100% rename (`git diff -M --summary`). f43d69f changes one test only: a
  `monkeypatch` of `BASE_PARAMS["num_threads"]` to 1 inside `test_default_feature_lists_are_unchanged`.
- *The pre-registration is unchanged since 765fb1a.*
  - `git diff --quiet 765fb1a HEAD -- docs/status/livetest-preregistration-2026.md` exits 0. The blob is 8661fb5a…
    at 765fb1a, at HEAD and in the working copy.
  - `rules_sha256` reproduces: the recomputed value, the stated value and the scorer's `rules_hash` are all
    `3c187d88405f22a1111ece81697fe7df5c23b87773e660d7992a0ccf3125d83d`.
  - `lines_sha256` equals `lines_spec_sha256()` (`ea706e17…`).
  - `commit` still reads `<frozen commit>`.
  - All 15 `frozen_commit_must_include` commits are ancestors of X.

**4. CI: green, and that is enough.**
- `gh run list -L 5 --workflow ci.yml`:
  - failure at eeb5c6c (the collection error) and at cfcfcd6 (the D-108 flake);
  - success at f43d69f (15:03 UTC), ba67ea1 (17:19) and 41e2dc0 (21:50; it finished during this review).
- At f43d69f all five jobs pass (python, api-postgres, api-image, web, security), lint is clean, and the fast suite
  gives `1046 passed, 16 skipped`. At 41e2dc0 the result is the same: `1046 passed, 16 skipped`.
- This is enough:
  - f43d69f is the last commit that touches code or tests. ba67ea1 and 41e2dc0 touch only `docs/` and `reports/`,
    and both are green anyway.
  - The fill commit F and Y will touch only docs. Their CI runs should still be seen green as part of D-120.
  - The rehearsal suite is gated and does not run in CI; run 9 is its evidence.

**5. D-134: confirmed, with one qualification.**

*Method.* I compared the four commitments myself:
- r0 from the `livetest-dryrun` worktree (a2bc721, equal to `ls-remote`);
- r1, r2 and the fixed-clock build from the scratchpad.

I keyed every listed file by its name with the build stamp removed. I also read the four builds' prediction parquet
files, diffed their calibrator state and train manifest JSON, and read the code paths. I did not rebuild: the four
existing builds answer every question.

- *At a fixed build time the build is exact: confirmed.*
  - My table of the 32 listed files matches the report's table row for row.
  - The fixed build equals r0 in the 23 model files, `player_sims`, `player_summary_raw`, `team_env` and the w09
    walk-forward parquet.
  - `player_summary` (1,458 rows) differs from r0 only in `calibration_version` (`walk_forward:a519afd9fe69`
    against `walk_forward:8bc6fdcd26f0`).
  - The calibrator state differs from r0 only in `meta.sources_digest` for the fixed build (its sandbox paths), and
    only in `meta.as_of` for r1 and r2.
  - The train manifest differs only in timings, `git`, `as_of` and `model_version`.
  - Snapshots, seeds, pins and holdout release are equal in all four commitments.
  - *Qualification:* a byte-exact `player_summary` and calibrator state also need the same store location, because
    the state file records its source paths. The values do not depend on it.
- *Projections depend on the build time: confirmed.*
  - From r0 to r1 (2 h 16 min apart, same snapshots), all 1,458 `pred_mean` values move. Keyed by `entity_id` and
    `scoring`, the mean absolute change is 0.0961 and the largest is 0.6562. r1 and r2 differ only in
    `calibration_version`.
  - The mechanism is in the code. Live rows use `asof_ts = now` (`ffml/sim/predict.py:52-53`).
    `depth__chart_age_days` is `asof_ts` minus the chart's `snapshot_ts` (`ffml/features/context.py:160-163`).
    Historical rows use `asof = cutoff_ts - 1 h` (`ffml/features/data.py:7-8`, `:108`).
  - The w09 walk-forward part is byte-identical across builds 2 h 18 min apart. So the history that feeds later
    calibrations does not depend on the build time.
- *It does not threaten the freeze.* The dependence is a property of the frozen feature design, documented in
  FEATURES.md (`depth__chart_age_days`). Removing it would change `ffml/features/` or `ffml/sim/`, which means a new
  X and a new rehearsal, for no gain in validity.
- *It does not threaten the weekly commitments.*
  - Each commitment records `as_of`, `rows_as_of`, the snapshots, `code.commit` and the seeds. With these, the fixed
    build reproduced r0.
  - A real build cannot be repeated for a week: `build_week` raises `AlreadyCommitted` (exit 4) when the commitment
    exists (`ffml/sim/livetest.py:672-675`).
  - Only `--rehearsal` rebuilds, and only under `data/livetest/rehearsal/`. That is why r1 and r2 needed the backup
    and restore.
  - The restore holds: the five overwritten sandbox files match `backup_hashes.json`, and the local commitment
    equals the pushed one (a5197d01…).
- *It does not threaten the verdict.*
  - The scorer and the final score the committed projections, sealed weekly. Nothing is rebuilt or refitted at
    scoring time.
  - No outcome exists before kickoff, so choosing a build time carries no information about results.
- *What it does change is what the test measures.*
  - The committed rows measure chart age at the build, about 25 h before the first kickoff under D-106. Training
    and calibration rows measure it 1 h before kickoff.
  - This train/serve difference comes with committing a day ahead. It is the same every week, it is fixed before
    any outcome, and it is part of what the live test measures. It can only change how well the frozen model does.
  - The pass-rate estimate (0.580) rests on history built with the 1-hour convention, so it may be slightly
    optimistic. The size is unknown; for scale, 2 h 16 min moved projections by 0.096 points on average.
  - No action is needed for the freeze.
- *D-108 covers the production path for thread effects: confirmed.*
  - `num_threads` is 4 (`ffml/models/chain/lgbm.py:60`) on the 8-logical-core build machine.
  - The 23 model files are byte-identical in all four builds:
    - r0 was built alone, 14:49 to 14:52 UTC, before run 9 started;
    - r1, r2 and the fixed build ran from 17:05 to 17:16 UTC, while run 9's world E was working (its records run
      from 16:53 to 17:29).
  - So the refit was also stable under that load.
  - The thread effect matters only for rebuilding a commitment, never for the verdict. CI's smaller runner is
    handled by the test-only pin in f43d69f.
  - If the builds ever move to a machine with fewer than 4 cores, D-108's premise no longer holds. That too would
    affect only reproducibility.

**6. Other checks.**
- *The provisional Part 2's low finding is closed.* That is the harness defaults, finding 6 in D-132.
  `FFML_REHEARSAL_COMMIT` now defaults to 4799913 and the base to X (`tests/rehearsal/_rehearsal_world.py:47-50`,
  eeb5c6c).
- *The extra D-134 builds did not touch run 9.* They overlapped on the machine but share no files: each world has
  its own clone and data directories. Neither result is affected: run 9 equals run 7, and the builds' model files
  equal r0's.

**Findings**

**F9. Pre-registration fields outside `rules` are unguarded after the freeze. Medium; does not block the freeze.
Close it before the first counted build (window opens about 2026-11-19 00:15 UTC).** After the freeze, the binding
fields of the pre-registration outside `rules` can be edited without any automated check noticing.
- The final reads three of them from the main checkout's pre-registration at HEAD:
  - `window` (`livetest_score.py:1695` and `:2017`);
  - `missed_weeks.allowed` (`:1878`);
  - `allowed_paths` and `frozen_paths` (`:1860`).
- None of these is inside `rules`, so `rules_sha256` does not cover them.
- The file is under `docs/`, an allowed path, and `commit_relation_allowed` has no special case for it.
- `livetest_changelog.py --append` would row such a commit automatically, as "paths outside the pinned build; no
  effect on commitments".
- The pre-registration's own rule says "the pre-registration itself changes only in its commit field at the
  freeze". Nothing enforces that rule except git history, the change-log row and the 16-hex pre-registration sha256
  printed in the final report.

Fix, in allowed paths and without a new X:
1. `scripts/livetest_changelog.py` classes any commit after F that touches
   `docs/status/livetest-preregistration-2026.md` as `needs_ruling`. Such a commit then gets a hand-written row with
   my verification (D-115).
2. RUNBOOK sections 15 and 17: before the final, the Evaluator runs
   `git diff --exit-code <F> HEAD -- docs/status/livetest-preregistration-2026.md` and records the result. Any
   difference needs the owner's ruling (D-111) before the final runs.
3. F's row in the change log (written in Y) states the LF SHA-256 of the pre-registration at F, as a fixed
   reference.

The code alternative, the scorer hashing the whole JSON, changes a frozen file. I do not recommend it.

**F10. The build-time effect is undocumented in operations. Low; does not block; documentation.**
- Neither the build docstring nor RUNBOOK section 4 says that, with the same snapshots, a later build gives
  different projections through `depth__chart_age_days`.
- The docstring (`ffml/sim/livetest.py:22-31`, frozen) says that running earlier "only means fewer late roster or
  injury updates, never a different cutoff".
- Add one sentence with D-134's figures to RUNBOOK section 4, so that a later reader does not take it for
  nondeterminism.

**F11. Rehearsal log events use the real clock. Informational; does not block.**
- In the rehearsal, the runner stamps its log events with the real clock (`RunLog.event`,
  `scripts/livetest_core.py:1302`). Its decisions use the injected clock.
- So P_prebuild's 2026w13 missed marker carries a 2026-11-12 `reason.check.ts` inside a record dated 2026-12-10.
- In production both clocks are real time, so no verdict is affected. A fix under `scripts/` is optional.

**Freeze line.** A complete run at the committed test code meets every condition of the provisional Part 2:
- 44 passed and 1 skipped;
- all ten worlds at X, with an empty frozen-code diff;
- every final and refusal record equal to run 7's in exit code, verdict, line results and values, and check names
  and statuses.

The other checks also pass:
- every commit after X is on an allowed path and can be logged;
- the pre-registration and both hashes are unchanged;
- CI is green at the last code commit and at HEAD;
- D-134 is confirmed, and it does not affect the freeze, the commitments or the verdict.

F9 and F10 are fixed after the freeze in allowed paths, each with a change-log row. The freeze then proceeds by D-120:
1. the fill commit F;
2. the append-only change-log commit Y, covering 765fb1a through F;
3. the worktree pinned at Y.

Freeze at X = 4799913: pass
