# Phase 3 / Independent Evaluator / EV-3.2 and EV-3.3 (wave two, draft)
Date: 2026-11-03
Scope: the leakage and inputs audit of the release candidate (EV-3.2) and the
walk-forward validation report on 2022-2024 (EV-3.3), built as a reproducible
generator and run on the current backtest file before the chain parts land.
All consensus figures are aggregates (D-007). Season 2025 was not read.

Task list (from the brief):
1. `ffml/eval/report_backtest.py`: `build_backtest_report(run_id, out_name, ablation_run_id=None) -> Path`, sections Setup, scoring cross-check, PPR headline tables with chain-minus-comparator CIs, by week, calibration, lineup efficiency, half-PPR and standard, ablations, verdict; per-week CSV under `data/reports/`.
2. EV-3.2: rerun `python -m ffml.eval.audits --write evaluator_audit_<date>_wave2` and summarise it in the report.
3. Tests on a tiny synthetic backtest frame: model-agnostic pair set, CI sign convention, verdict rendering.
4. Run on the current file as `backtest_2026-11-03_draft`; commit the draft and this note.

Done:
- `ffml/eval/metrics.py` (4d35c63, plus a column fix in 4e2103d):
  - D-074 common pair set: `reference_ranks` / `with_reference_rank` add `ref_rank`, one positional rank per season-week from the mean `pred_q50` of the compared models (ties by `entity_id`; actuals are never read). `start_sit_pairs(..., common=True)`, `spearman_within(..., common=True)` and the bundle metrics `start_sit_common`, `start_sit_common_weighted` and `spearman_common` score every model on that set. A pair that a model ranks as a true tie stays in the set as 0.5, so the pair count is identical across models. The report checks this at run time and prints the count per position.
  - `mae_mean`: D-053 asks for the MAE of both `pred_mean` and `pred_q50`.
  - The metric bundle now ranks and filters once per call. That makes it about 2x faster: 78 ms to 37 ms on a real WR slice. A new test checks that it returns exactly what the individual functions return, including on a replicate-tagged resample with ties and nulls.
  - `paired_bundle_bootstrap`, `percentile_interval` and `cluster_universe`: the report bootstraps chunks of models in separate processes. Replicates still pair because every chunk draws from the same cluster universe with the same seed, and the report asserts this.
  - The bundle treats an optional column that one frame lacks as null. Before this fix it crashed when frames with different columns were compared together (see findings).
- `ffml/eval/ecr_baseline.py` (fbb679e): `reference_actuals(seasons)` returns the evaluator's reference PPR points with each row's own kicking points, for the D-052 cross-check.
- `ffml/eval/audits/` (b7e0f1d):
  - New `inputs_audit.py` with three checks:
    - no reference to the restricted archive outside `ffml/data` and `ffml/eval`;
    - chain artifacts' LightGBM inputs contain no `y_` column, and primary artifacts no `avail__` column, at the backtests' feature version;
    - backtest runs have purpose `backtest`, no holdout-season rows, and feature snapshots equal to the audited LATEST.
  - The timestamp audit and the shuffle probe now also run on `team_week` and `dst_week`, which the chain reads.
- `ffml/eval/report_backtest.py` (4e2103d): the generator and its CLI, `python -m ffml.eval.report_backtest --run-id ... --out ... [--ablation-run-id ...]`. Sections:
  - setup and populations;
  - D-052 scoring cross-check;
  - PPR headline tables per position for every model, raw and calibrated;
  - `focus - comparator` deltas with paired, week-clustered 95% intervals and B/W tags;
  - MAE and coverage by week of season;
  - calibration: Brier and logistic slope for boom/bust, pooled reliability tables, PIT deviation per position and model, pooled PIT histogram;
  - secondary population with inactive rows and the availability layer;
  - lineup efficiency with intervals (test-league slots, D-050 pool);
  - consensus comparison (PPR, no Thursday players);
  - half-PPR and standard, condensed;
  - ablations and the final-report sensitivity;
  - audit summary;
  - verdict: calibrated and raw grids, the section 8 acceptance lines, the coverage acceptance table, and the complete list of every cell where the focus model does not beat a comparator.
  - Outputs under `data/reports/`: `<out>_per_week.csv`, `<out>_deltas.csv`, `<out>_meta.json`.
- Tests:
  - `tests/eval/test_report_backtest.py` (11): the pair set is model-agnostic and independent of actuals; CI sign convention and status rule; chunked bootstraps pair exactly; verdict rendering is complete (one phrase per non-beat cell); coverage PASS/FAIL; the cross-check classifies emergency kickers; end-to-end build with the consensus and cross-check readers replaced by synthetic frames, reproducible byte for byte; draft fallback; the reference excludes the final-report variant.
  - `tests/eval/test_inputs_audit.py` (6).
  - One equivalence test added to `tests/eval/test_metrics.py`.
- `reports/evaluator_audit_2026-11-03_wave2.md` and `reports/backtest_2026-11-03_draft.md`.

Evidence:
- `.venv/Scripts/python -m pytest tests/eval -q`: `80 passed in 75.25s`.
- `.venv/Scripts/python -m pytest -q` (whole repository, after all evaluator commits, with other agents' uncommitted work in the tree): `391 passed, 1 warning in 357.15s (0:05:57)`. The warning is the expected unsupported-key warning in `tests/scoring`.
- `.venv/Scripts/ruff check .`: `All checks passed!`
- `python -m ffml.eval.audits --write evaluator_audit_2026-11-03_wave2` (exit 0): overall PASS. The run used the audit code committed afterwards as b7e0f1d, unchanged since; the report header shows HEAD `fd963d0` at run time.
  - Holdout: PASS 4, WARN 1. The WARN is six docstring mentions of the holdout year; two of them are new, in `ffml/models/train.py:21` and `ffml/sim/predict.py:11`.
  - Leakage: PASS 7.
    - Suite: `14 passed`.
    - Timestamp audit: player_week 28,897 rows, team_week 1,708, dst_week 1,708, with 0 violations.
    - Shuffle probe, max |corr|: player_week 0.851, team_week 0.388 (`ctx__implied_team_total` vs `y_points`), dst_week 0.395.
  - Inputs: PASS 3.
    - 49 files scanned, with no restricted reference.
    - 3 backtest runs, with no holdout rows and snapshots equal to LATEST.
    - The chain artifact `chain_v1-primary-c2024w19-20261102T222634Z` has 19 LightGBM stages, 414 inputs and 0 `avail__` inputs, at feature version 0.2.0.
- Draft report: `python -m ffml.eval.report_backtest --run-id default_20261102 --out backtest_2026-11-03_draft --n-boot 500 --n-jobs 4 --audit evaluator_audit_2026-11-03_wave2`.
  - Run time: 636 s, generated from committed code at 4e2103d. The machine was shared with five model-training processes.
  - The predictions sha256 `3ddc52a1...` matches the manifest.
  - The numbers are identical to an earlier run with the same seed.
- D-052 scoring cross-check:
  - 29,191 of 29,192 PPR rows match (99.9966%) across 2022-2024, every position, and active flags agree on every row.
  - The one difference is an RB in 2023 week 9 who kicked a field goal (3.0 points). `ffml/scoring` scores kicking for every non-DST row, as Sleeper does; my reference scores it for kickers only. The harness value is correct, so this is not a finding.
- Populations:
  - Every model has 29,192 PPR rows, 0 dropped by alignment, and 23,141 active rows.
  - The chain's universe equals the baselines' on the 18 weeks of 2021 written so far: 9,812 PPR rows each, with no key differences.
  - Common pair set: QB 3,117, RB 12,843, WR 29,187, TE 3,120 pairs, identical for every model.
- PPR headline, 2022-2024, active population. MAE is of `pred_q50` (raw); coverage is 80% after calibration. The consensus row is on its own population (no Thursday players; n QB 1,359, RB 3,743, WR 5,906, TE 3,631, K 1,235, DST 1,270).

  | model (MAE / cov80 cal) | QB | RB | WR | TE | K | DST |
  | --- | --- | --- | --- | --- | --- | --- |
  | `direct_lgbm` | 5.737 / 0.797 | 3.937 / 0.809 | 4.081 / 0.803 | 2.698 / 0.794 | 3.662 / 0.792 | 4.191 / 0.813 |
  | `rolling_avg_4` | 6.252 / 0.785 | 4.395 / 0.804 | 4.567 / 0.796 | 3.063 / 0.796 | 4.044 / 0.791 | 4.829 / 0.813 |
  | `rolling_avg_3` | 6.304 / 0.791 | 4.457 / 0.805 | 4.649 / 0.798 | 3.134 / 0.802 | 4.189 / 0.792 | 4.929 / 0.799 |
  | `rolling_ew_035` | 6.113 / 0.787 | 4.283 / 0.808 | 4.481 / 0.801 | 3.012 / 0.800 | 4.013 / 0.794 | 4.720 / 0.799 |
  | `prior_season` | 6.858 / 0.808 | 4.816 / 0.805 | 4.919 / 0.810 | 3.257 / 0.807 | 3.781 / 0.795 | 4.574 / 0.809 |
  | `rolling_median_8` | 6.309 / 0.793 | 4.254 / 0.805 | 4.376 / 0.802 | 2.869 / 0.799 | 3.945 / 0.799 | 4.618 / 0.810 |
  | `consensus_ecr` (own population) | 5.853 / 0.804 | 4.097 / 0.849 | 4.106 / 0.871 | 2.789 / 0.884 | 3.634 / 0.804 | 4.219 / 0.825 |

- Stand-in verdict (focus `direct_lgbm`; a format check, not a chain result):
  - It beats `rolling_avg_4`, `prior_season` and `rolling_median_8` on MAE and on pinball q10, q50 and q90 at every position.
  - Calibrated coverage is 0.79-0.81 at every position.
  - Against the consensus, it is not better on MAE at any position and is worse on pinball q10 for QB, RB, WR and TE. Start/sit is within the interval at every position, so it passes the consensus rule.

Findings (for the ML Engineers and the orchestrator):
- F-1 (orchestrator, naming): the run labels the chain `chain_v1` and `chain_v1_final_report`. The brief said `chain_v1-primary` and `chain_v1-final_report`. The ablation run's parts use `chain_v1-primary-no_vegas` and similar. The report accepts all of these spellings. One convention would avoid silent mismatches.
- F-2 (model card, informational): the consensus baseline is the strongest comparator. On its population the challenger does not beat it on MAE at any position (QB +0.11 [-0.04, +0.27] calibrated) and is worse on pinball q10 for QB, RB, WR and TE. The chain will face the same bar. Section 8 only requires that start/sit is not worse.
- F-3 (D-074 confirmed): on each model's own top-N, `prior_season` gets start/sit RB 0.605 and TE 0.610. On the common pair set it gets 0.560 and 0.581, below the challenger's 0.571 and 0.604. The own-set advantage B2 noted was mostly a pair-selection effect.
- F-4 (evaluator, self): the consensus reader's 80% intervals are over-wide (coverage 0.85-0.88 for RB, WR and TE), because the curve's rank-window quantiles are not calibrated. The verdict does not use consensus coverage. Calibrating the curve intervals is an improvement for my own module, not a model finding.
- F-5 (evaluator, self, fixed): `metric_bundle` built from the union of columns raised an error when one frame lacked an optional column. This was exposed by the consensus frame having no `boom_prob`, and `compare` had the same latent problem. Fixed in 4e2103d with a regression test.
- No finding against the model or harness code: the cross-check is clean, the universes agree, and B2's reported metrics reproduce exactly. For example, `direct_lgbm` QB MAE 5.737, coverage 0.792 and own start/sit 0.582 match B2's 5.74, 0.792 and 0.582.

Not done / blocked:
- The chain is not in the run yet. 49 of 72 `chain_v1` parts and 48 of 72 `chain_v1_final_report` parts were written at about 20:25 local time. The draft uses `direct_lgbm` as the focus model, and its verdict is a format check only.
- Not exercised on real rows: the boom/bust reliability, Brier, slope and availability metrics (they need the chain's columns, and the synthetic tests cover them), and the ablation section. `data/backtests/ablations_20261103/` has parts but no manifest or predictions yet; the hook is `ablation_run_id`.

Decisions proposed:
- D-077 (refines D-074): the D-074 reference ranking.
  - It is the mean `pred_q50` of the raw primary-configuration models in the comparison.
  - The final-report variant is excluded, because it uses same-week injury reports, and so are the calibrated forms.
  - The consensus is included only in the consensus population.
  - Ties are broken by `entity_id`. A model's own tie on a common pair counts 0.5.
- D-078: verdict conventions.
  - `delta = chain - comparator`.
  - "Beats" means the 95% paired, week-clustered percentile interval (500 draws, seed 0) lies entirely on the good side of zero.
  - The headline verdict uses the calibrated forms (`chain_v1+cal` against `<baseline>+cal`, and against the raw consensus), with the raw grid reported alongside.
  - Coverage acceptance uses `chain_v1+cal` per position.
  - Spearman and start/sit use the common set.
- D-079 (resolves D-052): the harness `actual` (the `ffml/scoring` PPR preset) is the scoring of record, and the consensus rows are scored against it. A reference difference equal to a non-kicker's own kicking points is classified as an emergency-kicker row and is not a finding.
- D-080: the release-candidate inputs audit is part of the Gate 3 evaluator audit: restricted reads, artifact feature lists, and backtest inputs. The leakage checks cover `team_week` and `dst_week`.

Next:
- When the orchestrator confirms the chain parts are combined, run `python -m ffml.eval.report_backtest --run-id default_20261102 --out backtest_2026-11-03` (with `--ablation-run-id ablations_20261103` once that run has a manifest). Then confirm the pair counts are identical, review the chain verdict and its calibration tables, file any findings, and rerun the audit if model code has changed.

## Addendum (2026-11-03): owner requirements D-081 to D-084 and the coverage observation

Done (code only; the final report is not built yet):
- D-081, matched comparison. `consensus_population` now restricts every model to the player-weeks that have a valid consensus row with q10, q50 and q90 (`matched_frames`). `population_keys_identical` checks the restriction, and section 8 prints the result.
  - The table shows the focus model and the challenger, raw and calibrated, the section 8 baselines and the best baseline (calibrated), and the consensus.
  - Its CIs are focus minus consensus, under the D-078 rule.
- D-081, the Thursday effect.
  - `ecr_baseline.exclusion_reasons` classifies every active player-week as valid, early game (game on or before the scrape date), no ECR match, or no scrape that week. `consensus_status` adds a fifth class: valid but no projection.
  - Section 8.1 gives the counts with a sum check. It reports the metrics of the calibrated focus model and of the best baseline on three populations: (a) matched, (b) early game, (c) all active.
  - It also reports paired CIs for focus-minus-best MAE in each population and for the early-minus-matched difference, then one plain sentence on the result.
- D-082: each verdict cell is one plain sentence, for example "The chain does not beat the FantasyPros consensus on RB MAE (difference ..., 95% interval ... to ...; calibrated)". Every calibrated consensus cell has a sentence. Raw consensus cells get a sentence where they differ from the calibrated result. Every other comparator's non-beaten cells are listed. The coverage gap is added to the verdict metrics.
- D-084: a "Holdout session pending" note at the top of the report. The season is taken from settings, so no literal year appears in the code.
- Coverage (orchestrator request):
  - The acceptance table shows PASS/FAIL for raw and calibrated, with one plain line per position; the rule itself is unchanged.
  - Section 5.1 adds a tail decomposition (below q10, tied at q10, inside, tied at q90, above q90) and a labelled tie-aware diagnostic (randomized PIT in (0.1, 0.9), seed 0, using the q05..q95 knots and prior-season position bounds from the reference actuals). It also adds q10 and q90 pinball for the calibrated focus model against every comparator.
  - A neutral paragraph lists the three options whenever a position fails and has material ties.
- Tests: `tests/eval/test_report_backtest.py` now has 17 tests, covering matched keys, Thursday counts, split deltas, the D-082 wording, tails and the randomized PIT, and the coverage paragraph.

Evidence:
- `pytest tests/eval -q`: `86 passed in 52.78s`. `ruff check .`: `All checks passed!`
- Stand-in run on the combined file (focus `direct_lgbm`, 200 draws, written to the scratchpad, not to `reports/`):
  - The matched table's row keys are identical for every model.
  - Active player-weeks by status: matched 17,144; early game 1,429; no ECR match 943; no scrape that week 3,625; valid without projection 0; all 23,141. The sum check is ok at every position.
  - Pooled `direct_lgbm+cal` minus `rolling_median_8+cal` MAE: -0.27 [-0.40, -0.15] on early-game player-weeks and -0.29 [-0.33, -0.24] on the matched population. The difference, +0.02 [-0.12, +0.15], includes zero.
- Independent check of the coverage observation (my own `tail_decomposition`, PPR 2022-2024, active rows). `chain_v1+cal` closed coverage:

  | position | coverage | below q10 | tied at q10 | above q90 |
  | --- | --- | --- | --- | --- |
  | QB | 0.803 | 9.3% | 0.2% | 10.4% |
  | RB | 0.863 | 5.3% | 15.6% | 8.4% |
  | WR | 0.852 | 4.7% | 24.6% | 10.1% |
  | TE | 0.872 | 2.3% | 40.1% | 10.5% |
  | K | 0.820 | — | — | — |
  | DST | 0.826 | — | — | — |

  - These match the orchestrator's numbers.
  - The RB, WR and TE tie shares are identical in the raw and calibrated forms, so calibration does not move those q10 values.
  - `direct_lgbm+cal` (0.79-0.81) and `rolling_ew_035+cal` (0.79-0.81) have almost no ties.
