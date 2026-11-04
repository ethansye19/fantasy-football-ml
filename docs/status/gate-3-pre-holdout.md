# Gate 3 evidence, before the holdout session

Date: 2026-11-03 (UTC). Status: **OPEN**. The candidate is frozen pending the
owner's decision on coverage acceptance; the single holdout session (EV-3.4)
has not run and waits for the owner's go-ahead (D-084).

## Candidate

`chain_v1+cal`: the structural chain in the primary configuration
(ffml/models/chain, stage choices in `ffml/models/chain/selection.json`,
LightGBM for 19 of 30 stages) with online conformal calibration (D-072),
features 0.2.0. Recommended by the Independent Evaluator.

## Checklist items with evidence so far

| Item | Evidence | Result |
| --- | --- | --- |
| Leakage suite green | `reports/evaluator_audit_2026-11-03_gate3.md`: leakage PASS 7 (suite 14 tests, timestamp audit, target-copy probe, team and DST checks), inputs audit PASS 3 | pass |
| Holdout untouched | same audit: holdout PASS 4, WARN 1 (docstring mentions only); no `artifacts/holdout_access.log` exists | pass |
| Backtest report with all three baselines | `reports/backtest_2026-11-03.md` (1,160 lines): rolling averages, prior season, rolling median, the FantasyPros consensus on identical player-weeks (D-081), the direct challenger, ablations | pass |
| Accuracy rules (MODELING.md section 8) | chain beats rolling_avg_4, prior_season and rolling_median_8 on MAE and pinball at QB, RB, WR, TE; start/sit not worse than the consensus at any position | pass |
| Coverage acceptance (75-85% per position after calibration, closed interval) | QB 0.803, K 0.820, DST 0.826 pass; RB 0.863, WR 0.852, TE 0.872 fail; cause is ties at a point mass at 0 (report section 5.1) | **fail as written; owner decision pending** |
| Model card states where baselines were not beaten | to be written after the holdout session | open |
| Holdout evaluated exactly once | not run | open |
| Week 9 predictions from the approved version | blocked until the holdout release (D-058) | open |

## Update after the owner's decisions (report v2, D-085 to D-087)

`reports/backtest_2026-11-03_v2.md`, audit `reports/evaluator_audit_2026-11-03_gate3b.md`
(PASS, HEAD 4d79b05; registry holds one candidate, none approved; frozen
calibrator SHA-256 `860af892...a21da19` matches).

| Item | Result |
| --- | --- |
| Tie-aware coverage, `chain_v1+cal` (75-85%, seed 0) | QB 0.801, RB 0.811, WR 0.778, TE 0.791, DST 0.762 pass; **K 0.740 fail** (raw K 0.819) |
| Same rule, challenger and five baselines | all pass (0.785-0.813) |
| Boom/bust slopes after per-position calibration | QB, RB, WR, TE 0.92-1.02 (not flagged); K boom 0.12, K bust 0.46, DST boom 0.77, DST bust 0.29 (all low confidence under the pre-set rule) |
| Verdict cells changed from v1 | 27 of 400, all coverage-gap cells (definition change); no MAE, pinball, Spearman or start/sit cell moved |
| Holdout | untouched; waits for the owner (D-087) |

## Update after calibration v3 (report v3, D-088 to D-090)

`reports/backtest_2026-11-03_v3.md`, audit `reports/evaluator_audit_2026-11-03_gate3c.md`
(PASS, HEAD 34937b6; one registry entry, candidate only; v3 calibrator
`calibration.json` SHA-256 `3c77725c4e6cf21cd2fc26acaeeea2b3aff22991087614bf35dcd9da280bcd39`,
v2 kept as `calibration_v2.json`, `860af892...`).

| Item | Result |
| --- | --- |
| Tie-aware coverage, `chain_v1+cal`, seed 0 | QB 0.779, RB 0.785, WR 0.786, TE 0.793, K 0.807, DST 0.820: all pass |
| Same rule, every other calibrated model | all pass (0.785-0.813) |
| Metric seeds 0-4 | widest chain range TE 0.792-0.799; no PASS/FAIL change for any model |
| Calibrator seeds 0-4 (evaluator recomputation) | at most 0.002 range; agrees with the ML Engineer's numbers |
| Atom rule dependence | without it K 0.772, DST 0.763; no PASS/FAIL depends on it |
| Section 8 accuracy and start/sit lines | all 16 PASS |
| Verdict cells changed from v2 | 15 calibrated cells (9 coverage-gap cells to tie; RB now beats the challenger on MAE and q50, WR no longer does; two consensus q10 cells to tie); raw cells unchanged |
| Boom/bust | K and DST low confidence (hide or mark in the UI, D-089) |
| Holdout | untouched; waits for the owner's go-ahead (D-090) |

## Timing (D-083)

Chain timing test with the budget enforced, run after both backtests finished
and with no project processes running:

```
FFML_ENFORCE_TIMING=1 pytest tests/models/test_chain_real_data.py -m slow -q -s
fit 23.1s predict 3.0s
1 passed in 28.91s
```

Budget: fit under 90 s, predict under 20 s. Machine CPU load was 53 percent
before and after the run, all from another project's processes
(`rating-system`: a training job and a local web server), which were left
running. No fantasy-football-ml process was active. Run at
2026-11-03 02:16 UTC on commit 7a651a2.

## Backtest runs

| Run | Contents | Wall time |
| --- | --- | --- |
| `data/backtests/default_20261102` | 5 baselines, challenger (3 scorings), chain primary and final-report, raw and calibrated, 2021-2024 weeks 1-18, weekly refits; 1,872,192 rows | 86 min for the chain session |
| `data/backtests/ablations_20261103` | chain without Vegas lines, without NGS, with same-week depth charts, 2022-2024 | 77 min |
