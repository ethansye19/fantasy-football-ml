# Phase 5 / QA / live-test final rehearsal (D-115)

Date: 2026-11-06. QA and Security Reviewer.

Scope: an end-to-end rehearsal of the frozen scorer's verdict path on a
temporary copy at the candidate frozen commit X = `4c35fea`. It uses synthetic
post-freeze commits and synthetic data only. Nothing under `ffml/`,
`pyproject.toml` or `requirements.lock.txt` changed. Nothing was written to the
real `data/`, `artifacts/`, `livetest-2026`, registry or access log, nothing was
pushed, and `FFML_HOLDOUT_UNLOCK` was never set.

One slip: to confirm that the rehearsal had not written to the real
`artifacts/holdout_access.log`, I read its timestamp and its last two lines,
not only the timestamp. It was last modified on 2026-11-05 at 15:14 UTC, before
this work. Nothing was written to it.

Evidence: `reports/livetest_final_rehearsal.md`. Test:
`tests/rehearsal/test_livetest_final_rehearsal.py`, marked `slow` and skipped
unless `FFML_REHEARSAL=1`. It takes about 75 minutes.

## Result

The verdict path gives the pre-registered result in every scenario on the
owner's list:

* PASS gives `Verdict: APPROVED`, deterministic over repeated runs.
* QB intervals too narrow give `Verdict: NOT APPROVED`, with L17 the only line
  not passed.
* One accuracy line made to fail gives `Verdict: NOT APPROVED`, with L07 the
  only line not passed.
* One or two missed weeks are excluded without extending the window, and the
  verdict is computed over 17 or 16 weeks. The final is refused until 2027w09 is
  final, even when 2027w09 is the miss. A third miss gives `INVALID`.
* Weekly outputs carry no metric. A tampered sealed part or record, and a
  missing branch record, are refused before anything is read.
* An unlogged commit, an edited or deleted entry, an entry written into the
  template row, a commit mixing the log with another path, a merge, a
  frozen-path commit, an unlogged fill commit, and a final run from the worktree
  pinned at X are each `INVALID`, naming the commit or path.
* Commits that only add change-log lines need no entry, and the run is valid
  with them.
* The report-only sensitivity check cannot change the verdict. This was shown
  with a forced disagreement in both directions.

Runs: run 1 (all seven worlds, without the real 5,000-draw run) gave 25 passed
and 1 skipped in 1 h 12 min. Run 2 (with the 5,000-draw run, four workers) was
stopped by Claude Code for low system memory and has not been restarted. Before the stop, its worlds B and C reproduced run 1 exactly. Run 3
covered the three scenarios added during run 1: no bootstrap, about 2 minutes,
all as expected. The real 5,000-draw CLI run is still to do
(`FFML_REHEARSAL_5000=1`).

## Findings for the owner (not fixed; frozen files untouched)

| id | severity | finding | where |
| --- | --- | --- | --- |
| QA-F27 | high | Weekly scoring from the pinned worktree leaves `reports/livetest/<week>_verification.md` there, untracked. The runner's pin check then refuses every later build: three misses and INVALID unless someone intervenes. The cheapest fix is `reports/livetest/` in `.gitignore`, which is a frozen path, so it must land before the freeze | `ffml/eval/livetest_score.py:1038`, `scripts/livetest_week.py:1686-1690`, `scripts/livetest_core.py:781-786` |
| QA-F28 | medium | A hand-pushed `MISSED.json` for a committed, sealed week is not cross-checked against the branch. With the week's local sealed files removed, every check passes and the week is excluded. The overlap check can never fail | `ffml/eval/livetest_score.py:1294`, `:1213-1214` |
| QA-F29 | medium | The default worktree mode trusts uncommitted worktree files as the branch record. Run the final with `--no-worktree` after a fetch | `ffml/eval/livetest_score.py:192-197` |
| QA-F30 | low | The runner's miss count restarts with the 2027 re-init: the third miss is marked "VALID: miss 1 of 2". The scorer counts correctly | `scripts/livetest_week.py:753`, `:768-770` |

Procedure notes for the freeze, so the runbook can say them:

* Log the fill commit in a later commit that only adds change-log lines.
* Add the first entry as a new row, not in the template row.
* Run the final from a checkout that contains the filled pre-registration, not
  from the pinned worktree.
* Time the Tuesday job's cumulative 5,000-draw step on real data against the
  180-minute timeout.
