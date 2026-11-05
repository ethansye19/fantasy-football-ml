# Phase 4 / Independent Evaluator / live-test pass probability (D-098 item 3)
Date: 2026-11-04
Scope: estimate the chance that a well-calibrated model passes all 22
pre-registered lines over 2026 weeks 10-18, accounting for the 2026 game
counts and within-week correlation, before chain_v2 is frozen. Estimate only;
the owner decides the test design. 2022-2024 rows only, purpose `backtest`;
no 2025 or 2026 outcome was read, `FFML_HOLDOUT_UNLOCK` was not set, and the
consensus is used in aggregate only (D-007).

Task list (from the brief):
1. Simulate windows of 2022-2024 slates (one per week number, thinned to the 2026 game counts by dropping whole games), evaluate the 22 lines exactly as `report_holdout.py` does with the pre-registered seeds.
2. Scenarios A (well calibrated: rank-calibrated PIT), B (as validated), C (accuracy edge shrunk, k = 0.75 and 0.5); windows (i) weeks 10-18, (ii) weeks 11-18, (iii) weeks 1-18 full slates; at least 2,000 replicates with Monte Carlo SEs.
3. Report joint and per-line pass probabilities, failure drivers, expected rows per position, coverage design effects.
4. Prove the fast path reproduces the real code (v3 cells; at least three sub-windows to 1e-9); tests under `tests/eval/`.
5. Deliver `ffml/eval/power.py`, tests, `reports/evaluator_live_test_power_2026w10-18.md`, this note.

Done:
- `ffml/eval/power.py` (69ad622): inputs built with the real report functions (`headline_frames`, `with_randomized_pit`, `bounds_from_run`, `consensus_population`); per-game sums and counts; bootstrap count matrices from the same generator calls as `metrics._paired_bundle_bootstrap`; the D-077 pair set re-ranked on thinned slates; the scenario-B PIT redrawn with metric seed 0 over the window in the live sort order; `real_window_lines` runs `run_bootstrap`, `with_randomized_pit`, `with_reference_rank` and `evaluate_lines` on the same window; `python -m ffml.eval.power` runs everything and writes the report.
- `tests/eval/test_power.py` (69ad622): 19 tests on synthetic slates (thinned windows, re-ranked pair sets, tied projections and outcomes, tied PIT knots, a week without consensus rows): fast path equals the real functions to 1e-9 for B, A and B_fixed coverage on thinned windows and on all slates; bootstrap count matrix; PIT stream prefix; scenario A calibration; C invariance.
- `reports/evaluator_live_test_power_2026w10-18.md`: method, equivalence check, results for every scenario and window, assumptions. No recommendation.

Evidence:
- `python -m pytest tests/eval/test_power.py -q`: 19 passed in 111 s.
- `ruff check ffml/eval/power.py tests/eval/test_power.py`: all checks passed.
- Equivalence on the real 2022-2024 rows:
  - 58 of 58 printed cells of `reports/backtest_2026-11-03_v3.md` behind the 22 lines are reproduced at the printed precision (48 calibrated delta cells with intervals, 4 start/sit deltas, 6 tie-aware coverages).
  - 4 simulated windows with dropped games (weeks 10-18 with B and A coverage, weeks 11-18 with B and B_fixed): largest absolute difference against the real functions 9.4e-16 to 1.8e-15 over 162 values per window (point deltas, interval ends, coverages); all 22 verdicts equal in every window. The real path takes about 5 minutes per window; the fast path about 15-40 ms per replicate for all nine scenarios.
- `python -m ffml.eval.power --reps 10000 --equiv-windows 4`: 42 minutes, one process, under 1 GB (the four real-path windows took 320-380 s each; the 30,000 replicates took 18 minutes on a shared machine). The report header records HEAD `7075790`; `ffml/eval/power.py` is at `69ad622`, and `git diff 69ad622 7075790 -- ffml/eval ffml/holdout.py tests/eval` is empty.

Results (10,000 replicates per window; Monte Carlo SE in parentheses):

| scenario | weeks 10-18: all 22 | 6 coverage | 16 accuracy + start/sit | weeks 11-18: all 22 | weeks 1-18 (reference): all 22 |
| --- | --- | --- | --- | --- | --- |
| A: well calibrated (rank-calibrated PIT over all 2022-2024 weeks), accuracy as validated | 0.104 (0.003) | 0.897 (0.003) | 0.105 (0.003) | 0.081 (0.003) | 0.767 (0.004) |
| A_w: A calibrated on weeks 10-18 only | 0.105 (0.003) | 0.995 (0.001) | 0.105 (0.003) | 0.084 (0.003) | 0.766 (0.004) |
| A_vc: A with the three-slate variance correction | 0.043 (0.002) | 0.822 (0.004) | 0.044 (0.002) | 0.023 (0.001) | 0.628 (0.005) |
| B: as validated (chain_v1+cal) | 0.077 (0.003) | 0.556 (0.005) | 0.105 (0.003) | 0.049 (0.002) | 0.748 (0.004) |
| C: A, mean edge on L01-L12 shrunk to 0.75x, noise kept | 0.006 (0.001) | 0.897 (0.003) | 0.006 (0.001) | 0.003 (0.001) | 0.457 (0.005) |
| C: A, mean edge shrunk to 0.5x | 0.000 (0.000) | 0.897 (0.003) | 0.000 (0.000) | 0.000 (0.000) | 0.052 (0.002) |
| C literal (per-row differences scaled by 0.75 or 0.5) | identical to A in every replicate (an interval scaled by k > 0 keeps its sign) | | | | |

- Per-line pass probabilities, scenario A, weeks 10-18: L01 0.455, L02 1.000, L03 1.000, L04 0.927, L05 0.512, L06 1.000, L07 1.000, L08 0.788, L09 0.596, L10 0.994, L11 1.000, L12 0.625, L13 0.968, L14 0.974, L15 0.789, L16 0.990, L17 0.909, L18 1.000, L19 1.000, L20 1.000, L21 0.999, L22 0.986 (MC SE at most 0.005).
- Scenario A's QB coverage averages 0.778 in weeks 10-18 (the chain's coverage drifts with the week of the season), so L17 passes 0.909 in A and 0.996 in A_w.
- What drives the failures: the accuracy lines, not coverage. In A, weeks 10-18, the 12 accuracy lines pass together 0.152 of the time, the 6 coverage lines 0.897, the 4 start/sit lines 0.735. The QB lines fail most: L01 (QB vs `rolling_avg_4`) 54.5%, L05 (QB vs `prior_season`) 48.8%, L09 (QB vs `rolling_median_8`) 40.4%; then L12 (TE vs `rolling_median_8`) 37.5%, L08 (TE vs `prior_season`) 21.1% and L15 (WR start/sit) 21.1%. Cell by cell: QB MAE and pinball q50 against `rolling_avg_4` and `rolling_median_8`, QB pinball q10 and q90 against `prior_season`, TE MAE and q50 against `rolling_median_8`, and TE q90 against `prior_season`. The chain's edge in these cells is smaller in weeks 10-18 than over the whole season (for example QB MAE against `rolling_avg_4`: -0.483 in weeks 10-18 against -0.573 overall), and a 9-week interval is typically about twice as wide as the 54-week one (median ratio 2.2 for the upper half of the interval). In B, L17 (QB coverage) also fails 41% of the time (window coverage averages 0.754).
- Design effect of coverage (week clusters, scenario A, 54 weeks): QB 1.00, RB 1.19, WR 1.13, TE 1.34, K 0.84, DST 1.23 (game clusters 0.96-1.11). Expected rows with a PIT over weeks 10-18: QB 337, RB 831, WR 1,298, TE 797, K 263, DST 266. Start/sit pairs: QB 496, RB 2,051, WR 4,588, TE 485, on 8 weeks (no week 18 consensus in 2022-2024).

Not done / blocked:
- The 54-week comparison with the real functions at 1e-9 (`--full-real`) was not run (about 30 more minutes). The full-slate path is covered by the 58 printed v3 cells and by the synthetic all-slates test at 1e-9.
- Season-level shifts (as in 2025, QB coverage 0.779 to 0.739) are not simulated: weeks are drawn independently across 2022-2024. The report shows the coverage lines' pass probability as a function of the true coverage level instead.

Decisions proposed:
- None. The test design (rules, window, missed weeks) is the owner's decision (D-098 item 3, open question in docs/DECISIONS.md).

Next:
- The owner reviews the estimate and decides the test design before chain_v2 is frozen (Week 10 kickoff 2026-11-13 01:15 UTC).

## Follow-up 2026-11-05: D-105 candidate rule sets (EV-4.2)

Scope: the coordinator's candidate rule sets R0-R3 under the D-105 limits
(fixed by the owner before any candidate result was seen), on weeks 10-18
and 11-18, with a no-edge null, the 18-week fallback window and the D-106
one-miss case. Same simulator, data and seeds as the first report; 5,000
replicates per window. Also the CI item: the holdout audit flagged the
holdout-season literal in `power.py` report prose; the season now comes from
settings and the audit's eval-code check passes.

Done:
- `ffml/eval/power.py` (d1a5163 and this commit): pooled QB-TE accuracy lines, the not-worse rule, the no-edge null `N0` (every chain-minus-comparator and start/sit difference centred on 0 at each week number, noise kept) and `N0_vc`, windows `ext18`, `one_miss`, `ext18_one_miss`, `RULESETS` R0-R3, `evaluate_rules`, `select_d105`, `real_pooled_lines` (check against `metric_bundle` plus `paired_bundle_bootstrap`), `null_calibration`, `render_candidates_report`, CLI `--candidates`.
- `tests/eval/test_power.py`: 7 new tests (26 in all): pooled fast path equals the real bundle on thinned windows (1e-9), null centring, rule-set sizes 22/28/31/34, R0 under `evaluate_rules` equals the 22-line verdicts, one-miss draws, selection rule.
- `reports/evaluator_live_test_power_candidates.md`: method deltas, checks, one table per window, the 18-week and one-miss tables, per-line tables, assumptions. No recommendation; the D-105 pick is stated.

Evidence:
- `pytest -q tests/eval/test_power.py`: 26 passed in 112 s. `pytest -q tests/eval/test_audits.py`: 20 passed (the eval-code check included). `ruff check`: clean.
- Real-data checks: pooled lines against the real bundle on one thinned window, largest difference 6.7e-16 over 36 values; null centring residual 5.7e-13 over all 54 slates; R0 reproduces the first report (A 0.106 against 0.104 at 10,000 replicates; A_vc 0.044 against 0.043).
- `python -m ffml.eval.power --candidates --reps 5000`: about 12 minutes, one process (the run on disk).

Results, weeks 10-18 (MC SE in parentheses; D-105 uses the A_vc and N0_vc columns):

| candidate | lines | good model, A_vc | no edge, N0_vc | good model, A | no edge, N0 | qualifies |
| --- | --- | --- | --- | --- | --- | --- |
| R0 | 22 | 0.044 (0.003) | 0.000 (0.000) | 0.106 (0.004) | 0.000 (0.000) | no: pass < 0.70 |
| R1 | 28 | 0.614 (0.007) | 0.000 (0.000) | 0.659 (0.007) | 0.000 (0.000) | no: pass < 0.70 |
| R2 | 31 | 0.124 (0.005) | 0.000 (0.000) | 0.192 (0.006) | 0.000 (0.000) | no: pass < 0.70 |
| R3 | 34 | 0.614 (0.007) | 0.000 (0.000) | 0.659 (0.007) | 0.000 (0.000) | no: pass < 0.70 |

- **Under D-105 no candidate qualifies.** Every false-approval rate is 0.000 (within the 10% limit), but the highest good-model pass rate is 0.614 (R1 and R3; 0.659 before the variance correction), below the 70% floor. Under D-105 item 3 the unchanged 22 rules apply over 2026 weeks 10-18 plus 2027 weeks 1-9; on that window R0 passes a good model 0.557 (A_vc; 0.722 uncorrected) with false approval 0.000. Weeks 11-18: R1/R3 0.520, R2 0.088, R0 0.024 (A_vc).
- **Why R1 and R3 stop at 0.61.** Their 18 accuracy lines each pass in at least 99.9% of good-model replicates; the shortfall is the ten unchanged lines, L13-L22, whose joint pass rate under A_vc is about 0.61 (WR start/sit L15 0.815, QB coverage L17 0.859, DST coverage L22 0.959, the other start/sit lines 0.96-0.98). Resizing only the accuracy lines cannot lift a 9-week design above that ceiling; this is a description of the candidates, not a proposal.
- **One miss (D-106).** Eight of nine weeks: R0 0.030, R1/R3 0.558, R2 0.093 (A_vc), null 0.000. The fallback's one-miss case (17 of 18 weeks): R0 0.518 (A_vc; 0.696 uncorrected), null 0.000; R1/R3 0.821.
- **Null calibration.** A lone one-metric beats line passes a no-edge model 4.2-6.5% of the time on nine weeks, not the nominal 2.5%, and a lone not-worse line fails it 3.9-6.1%; the two tails are equal in size, so the null is centred and the excess is the width of the D-078 percentile interval with nine clusters. Reported, not corrected.
- R1 and R3 have identical pass rates on every window: the added pooled q10 and q90 lines do not change a verdict at this precision.

Not done / blocked:
- `report_holdout.py` has no pooled QB-TE group; adopting R1-R3 would need one. Not built, since no candidate qualifies.

Decisions proposed:
- None. The D-105 limits select no candidate; item 3 (the 22 rules over 18 weeks, "no approved model" meanwhile) applies unless the owner decides otherwise.

Next:
- The owner sees the candidate table and the fallback numbers before the Week 10 kickoff.
