# Addendum to the holdout release report: `chain_v1-primary-c2024w19-20261102T222634Z`

Written by the Independent Evaluator on 2026-11-04 after the EV-3.4 session. No 2025 or 2026 data was read to write it. The generated report `reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md` is unchanged (git blob `0cee2a1e7ae8c4ab929da3289b0899e6de43bd10`, committed in `824162d`). This addendum records two things that the generated report omits or mislabels.

## (a) D-093 rerun

Owner rule 2 of `docs/status/holdout-preregistration.md` requires the rerun reason to be logged in the session log and in the evaluator's report. The generated report does not mention the rerun, so it is recorded here.

The text of `data/backtests/holdout_2025/RERUN_REASON.txt` (git-ignored), quoted verbatim:

> D-093 rerun 1 of 1. holdout_2025 backtest killed at about 17:38 UTC on 2026-11-04 by the Claude Code memory-pressure reaper (machine critically low on memory); no metric was read; no report was generated. Rerun with the same frozen inputs at commit 294aac0, resuming from intact part files (Parquet footers verified by the orchestrator), with --n-jobs 1 instead of 3 to lower peak memory. Worker count is execution parallelism, not a model input: each model's weekly predictions depend only on the commit, snapshots, calibrators and seeds. Authorized by the orchestrator under owner rule D-093.

Timeline (UTC, 2026-11-04):

| run | start | end | outcome |
| --- | --- | --- | --- |
| `holdout_2025`, first run, `--n-jobs 3` | 17:07:02 | about 17:38 | killed by the Claude Code memory-pressure reaper (process alive at 17:37:33, gone by 17:39:16; no traceback in its log) |
| `holdout_2025`, D-093 rerun, `--n-jobs 1` | 17:42:27 | 17:58:41 | rc 0; all 10 models at 18 of 18 weeks |

Facts:

- No metric was read before the kill. The first run produced only part files, and no report or evaluation step had started.
- The rerun ran at commit `294aac0` with the same frozen inputs. It resumed from the intact part files: at the kill, `chain_v1` and `chain_v1_final_report` had 8 weeks written and the eight comparators were complete. The harness skips parts already written and keeps the same fit schedule.
- `--n-jobs` affects thread counts only for models that expose `num_threads`, which means the direct challenger (`direct_lgbm`). All of its parts were complete before the rerun.
- The chain's LightGBM stages use fixed parameters, `num_threads: 8` and `deterministic: True` (`ffml/models/chain/lgbm.py`), so the worker count does not affect them.
- This was the only rerun. The report's integrity checks found one `holdout_eval` access session at `294aac0` covering both runs, the forward run and the report.

## (b) Erratum: Thursday-split population table header

The header line above the population table in the Thursday split (D-081) has the wrong season label in both sections:

| report line | printed | should read |
| --- | --- | --- |
| 343 (holdout season) | "Active player-weeks by consensus status (PPR, 2022-2024)." | "Active player-weeks by consensus status (PPR, 2025)." |
| 730 (forward check) | "Active player-weeks by consensus status (PPR, 2022-2024)." | "Active player-weeks by consensus status (PPR, 2026 weeks 1-7)." |

This is a label error only. The row counts in both tables are the 2025 rows and the 2026 weeks 1-7 rows respectively, and no number in either table changes.

## (c) Verdict

This addendum does not change the verdict line (`Verdict: NOT APPROVED`) or the forward-check line (`Forward check: PASS`) of the generated report.
