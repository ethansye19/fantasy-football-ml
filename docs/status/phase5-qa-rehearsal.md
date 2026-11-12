# Phase 5 / QA / live-test final rehearsal

Date: 2026-11-12. QA and Security Reviewer.

Scope: an end-to-end rehearsal of the scorer's verdict path, the runner and the
freeze procedure (D-120), on temporary clones with synthetic post-freeze commits
and synthetic data only:

* the frozen commit is **X = 4799913** (D-129);
* each clone starts from **Z = 765fb1a** (run 7), which holds the final
  pre-registration text (the D-127 window, 2026 w11-18 plus 2027 w1-10);
* run 9 starts from Z = cfcfcd6 and confirms the result (run 8 was stopped
  for low memory, D-132).

Nothing under a frozen path changed. Nothing was read from or written to the
real `data/`, `artifacts/`, `livetest-2026`, registry or access log. Nothing
was pushed, and `FFML_HOLDOUT_UNLOCK` was never set.

Details: `reports/livetest_final_rehearsal.md`. Evidence:
`reports/evidence/rehearsal/` (`MANIFEST.md` has the hashes). Test:
`tests/rehearsal/test_livetest_final_rehearsal.py`, marked `slow` and skipped
unless `FFML_REHEARSAL=1`. Two workers take about 2 hours.

## Result

At X, every scenario gave the pre-registered result. Run 7 (all ten worlds):
43 passed, 1 failed, 1 skipped.

* **The failure was a test defect (QA-F33)**, fixed in 34a6644 (D-131). The
  scorer was right: each per-week check appears 18 times and every check is
  PASS.
* The skip is the optional real 5,000-draw run.
* No scorer or runner defect was found at X.

What the run showed:

* **Verdicts.** PASS gives `Verdict: APPROVED`, the same again with the
  consensus archive and the fact tables moved away.
  * QB intervals too narrow, and one accuracy line tied, each give
    `Verdict: NOT APPROVED`, with L17 or L07 the only line not passed.
  * A rebuilt world reproduces its twin exactly.
* **Missed weeks.**
  * One or two misses are excluded without extending the window, including a
    miss in the last week (2027w10).
  * The final is refused until 2027w10 is final.
  * A third miss across the two seasons gives `INVALID` in the scorer and in
    the runner.
* **Sealed results.**
  * Weekly outputs carry no metric.
  * The final refuses before reading anything when:
    * a sealed part or record is tampered with;
    * the branch lacks the last seal record;
    * the local files and the worktree copy are tampered with consistently.
  * A failed GitHub fetch exits 75 with nothing recorded, and the retry gives
    the verdict.
  * A stray MISSED marker on a committed week is listed and changes nothing.
  * Removing that week's local sealed files is refused.
* **Change log.** Commits that only add change-log lines need no entry. Each of
  these gives `INVALID`, naming the commit:
  * an unlogged fill;
  * an unlogged change;
  * an edited or deleted entry;
  * an entry in the template row;
  * a log commit with another path;
  * a merge;
  * a frozen-path change.
* **The freeze procedure (D-120), in every clone.**
  * The fill commit sets `commit` to X and nothing else.
  * One append-only change-log commit Y holds a row for every commit in X..fill:
    the helper's rows, and hand-written rows where the helper refuses.
  * The builds run at Y.
  * `--prebuild-check --to Y` exits 0, and exits 5 when one row is removed.
  * The final from the worktree pinned at Y gives the same verdict as from HEAD.
* **Blocked builds.**
  * An unlogged change blocks the build (alert `prebuild_blocked`) until it is
    logged, and is not a miss.
  * Left past the deadline, it is a miss with the detail "pre-build check
    blocked".
  * A frozen-path change blocks the build.
* **Data cutoff and stat corrections.**
  * `--week` exits 4 before the cutoff, and before a snapshot at or after it.
  * The pins are the first snapshot at or after the cutoff.
  * Corrections after the cutoff never reach the sealed weeks or the final.
  * A rescore reuses the recorded pins.
* **D-121.**
  * A week with a game never final waits (exit 4).
  * Later weeks are scored and sealed meanwhile, by the runner's own
    `score-week`.
  * After the cutoff plus 14 days, the week is scored without the game. The
    game is listed in the sealed record and in the final, and the verdict equals
    the all-final world's.
  * A game that becomes final earlier is scored from the first snapshot in
    which it is final, with nothing excluded.
  * The comparators extension leaves the excluded game's rows out (the QA-F31
    fix).
* **The report-only 5,000-draw check** cannot change the verdict. A forced
  disagreement in both directions shows it.

## Findings

| id | severity | finding | where | status |
| --- | --- | --- | --- | --- |
| QA-F33 | test only | The PASS-world test's suffix match counted "<week> branch commitment" as a commitment check (36, not 18). No effect on the verdict | `tests/rehearsal/test_livetest_final_rehearsal.py:190` (765fb1a) | fixed in 34a6644 (D-131); run 9 confirms |
| QA-F31 | high | `ensure_comparators` ignored the D-121 exclusion: weeks could never be sealed and the final could never run | `ffml/eval/livetest_score.py:846-863` (071c3a4) | fixed in 4799913 (D-128); rechecked |
| QA-F32 | low | `--data-cutoff` for a season with no `dim_game` partition exited 1 with a traceback, not 4 | `ffml/eval/livetest_score.py:2115` (071c3a4) | fixed in 4799913 (D-128); rechecked |
| QA-F27 to QA-F30 | | from the first rehearsal | | fixed earlier; rechecked, all hold |

The coordinator's fix in 34a6644 is right and weakens nothing. A possible
tightening: compare the week set with the window instead of comparing the count
with 18.

Limitations:

* The real comparators extension (`run_comparators`) is exercised only on real
  data: at the pre-warm after the freeze and in the Week 10 rehearsal.
* The runner runs unpinned in the clone at Y. The worktree pinned at Y is
  checked separately.
* The real 5,000-draw final was not run.

## Runs

* **Run 7**: X = 4799913, Z = 765fb1a, two workers, 2026-11-11 22:16 to
  2026-11-12 00:23 UTC (2 h 06 min). It is the result of record.
* **Run 6** (the same settings, 17:43 to 20:20 UTC): stopped by the owner's
  pause.
  * The coordinator suspended its processes; the waiting agent stalled; the
    processes were killed.
  * The owner ruled that processes are never suspended again (D-130).
  * Its 70 streamed records are archived. All 29 of its final and refusal
    records recur in run 7 unchanged.
* **Run 5** (X = 071c3a4): stopped for low memory before any final. It found
  QA-F31 and QA-F32.
* **Earlier runs** at bd3bf21 and 2872d13 were stopped. Every final that
  completed matched.
* **Run 8** (Z = 34a6644): stopped by Claude Code for low memory after two
  worlds, both consistent with run 7 (D-132).
* **Run 9 confirmation** (X = 4799913, Z = cfcfcd6, one worker, 2026-11-12
  14:53 to 19:48 UTC): **44 passed, 1 skipped**. All 30 final and refusal
  records equal run 7's in verdict, exit code, line values and check statuses
  once commit shas are masked; only the counts of commits and paths after X
  differ, because the base is newer. Filled in by the coordinator from the
  evidence (`reports/evidence/rehearsal/run9_X4799913.json`).

## The first rehearsal (D-115, 2026-11-06, X = 4c35fea)

Seven worlds gave 25 passed and 1 skipped in 1 h 12 min. Every scenario on the
owner's list gave the pre-registered result. It found QA-F27 (high), QA-F28 and
QA-F29 (medium) and QA-F30 (low), all fixed before this rehearsal.

One slip in that rehearsal, disclosed then: to confirm that nothing had been
written to the real `artifacts/holdout_access.log`, I read its last two lines,
not only its timestamp. Nothing was written to it.
