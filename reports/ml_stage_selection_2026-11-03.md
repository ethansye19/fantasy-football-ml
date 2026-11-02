# Stage selection: structural baselines vs LightGBM (walk-forward 2022-2024)

Date: 2026-11-03. Author: ML Engineer B1 (Phase 3 wave two).
Code: `ffml/models/chain/selection.py` (`python -m ffml.models.chain.selection`).
Output: `ffml/models/chain/selection.json`, read by `ChainModel(stage_choice=None)`.
The per-week loss sums are in `artifacts/selection/stage_selection.records.json`, which is not committed.
Features: version 0.2.0, primary configuration (no `avail__` inputs). Legacy depth charts are the week w-1 chart (D-059).
The 2025 holdout was never read.

## Protocol

- Validation seasons are 2022, 2023 and 2024, weeks 2-18 (MODELING.md section 2). That is 51 week clusters.
- **The models were refit every 4 weeks, not every week, to save time.**
  - Refits happen at weeks 2, 6, 10, 14 and 18 of each season: 15 refits in all.
  - Each refit trains both candidates of every stage on all regular-season rows strictly before that week.
  - The candidates then predict each week up to the next refit.
  - One refit with all 29 LightGBM candidates took 44-54 s on an idle machine. Three refits ran beside other jobs and took 62-132 s, so the mean is 56 s.
  - B2's harness refits every week. Refitting every 4 weeks means slightly staler models for both candidates.
- Populations:
  - Team stages: every team-game.
  - Stages 2-4: the played rows of the stage's positions with a positive denominator. These targets are conditional on the player being active.
  - Availability: every rostered row.
  - Kicker FG%: kickers with at least one attempt.
  - DST stages: every team-game.
- Metric per stage:
  - Absolute error for continuous targets, shares and rates. Rates are weighted by their denominator: targets, receptions, carries, attempts, dropbacks, touches, FG attempts or plays.
  - Mean Poisson deviance for counts: team touchdowns, field-goal and two-point attempts, and DST events.
  - Log loss for availability.
  - Counts use deviance instead of MAE. MAE rewards a prediction shaded toward the median, so it is not a proper score for an expected count (proposed as D-063).
- Decision rule (MODELING.md section 8):
  - The test statistic is LightGBM loss minus structural loss, averaged with the stage weights.
  - Its 95% interval comes from 2,000 bootstrap resamples of whole weeks.
  - **LightGBM replaces the structural baseline only when the whole interval is below zero.**
- Candidates:
  - The structural baselines follow MODELING.md section 3:
    - plays: the L5 mean;
    - pass rate: the EW rate plus a one-parameter spread fit;
    - team touchdowns: the implied total divided by a fitted number;
    - shares: EW share, or shrunk EW share for red-zone, goal-line and touchdown shares. Players without history get a position-and-depth prior;
    - efficiency: shrunk EW rates;
    - DST counts: geometric-mean rates times opponent volume, or the shrunk per-game rate for rare events.
  - The LightGBM candidates use the fixed hyperparameters in `ffml/models/chain/lgbm.py`.
    - Early stopping uses the last 8 weeks inside the training window. The model is then refit on the full window with the chosen number of rounds.
    - Monotone constraints are set where the direction is clear: the player's own window, and opponent rates such as INT rate, sack rate and yards per carry allowed.

## Results

| stage | metric | rows | structural | LightGBM | LightGBM - structural [95% CI] | choice |
| --- | --- | ---: | ---: | ---: | --- | --- |
| team_plays | mae | 1,534 | 7.0072 | 6.4937 | -0.5135 [-0.6951, -0.3314] | **lgbm** |
| team_pass_rate | mae | 1,534 | 0.0849 | 0.0789 | -0.00600 [-0.00798, -0.00395] | **lgbm** |
| team_off_td | poisson | 1,534 | 0.8360 | 0.8407 | 0.00472 [-0.00385, 0.0143] | **structural** |
| team_fg_att | poisson | 1,534 | 0.9624 | 0.9622 | -0.00014 [-0.00520, 0.00483] | **structural** |
| team_two_pt_att | poisson | 1,534 | 0.7644 | 0.7481 | -0.0163 [-0.0302, -0.00397] | **lgbm** |
| share_target | mae | 16,846 | 0.0466 | 0.0452 | -0.00141 [-0.00184, -0.00100] | **lgbm** |
| share_carry | mae | 18,739 | 0.0359 | 0.0356 | -0.00026 [-0.00062, 0.00010] | **structural** |
| share_dropback | mae | 1,893 | 0.1512 | 0.1354 | -0.0158 [-0.0221, -0.00954] | **lgbm** |
| share_gl_carry | mae | 8,836 | 0.1268 | 0.1059 | -0.0209 [-0.0224, -0.0195] | **lgbm** |
| share_rz_target | mae | 15,823 | 0.1163 | 0.1079 | -0.00838 [-0.00902, -0.00773] | **lgbm** |
| eff_catch_rate | mae | 12,170 | 0.1863 | 0.1842 | -0.00212 [-0.00300, -0.00129] | **lgbm** |
| eff_ypr | mae | 10,918 | 4.0604 | 3.9699 | -0.0905 [-0.1118, -0.0708] | **lgbm** |
| eff_ypc | mae | 6,268 | 1.6781 | 1.6319 | -0.0462 [-0.0637, -0.0288] | **lgbm** |
| eff_comp_rate | mae | 1,773 | 0.0755 | 0.0734 | -0.00211 [-0.00326, -0.00088] | **lgbm** |
| eff_int_rate | mae | 1,773 | 0.0225 | 0.0221 | -0.00033 [-0.00068, 0.00005] | **structural** |
| eff_sack_rate | mae | 1,783 | 0.0378 | 0.0369 | -0.00090 [-0.00147, -0.00029] | **lgbm** |
| eff_scramble_rate | mae | 1,783 | 0.0328 | 0.0310 | -0.00174 [-0.00260, -0.00089] | **lgbm** |
| eff_fumble_rate | mae | 14,106 | 0.00925 | 0.00928 | 0.00003 [0.00000, 0.00005] | **structural** |
| td_pass_share | mae | 13,258 | 0.1434 | 0.1357 | -0.00767 [-0.00871, -0.00667] | **lgbm** |
| td_rush_share | mae | 8,364 | 0.1387 | 0.1224 | -0.0162 [-0.0177, -0.0147] | **lgbm** |
| availability | logloss | 25,938 | 0.3460 | 0.3187 | -0.0273 [-0.0311, -0.0242] | **lgbm** |
| k_fg_pct | mae | 1,362 | 0.1976 | 0.2013 | 0.00369 [0.00167, 0.00597] | **structural** |
| dst_sacks | poisson | 1,534 | 1.4671 | 1.3943 | -0.0728 [-0.1185, -0.0252] | **lgbm** |
| dst_int | poisson | 1,534 | 1.2885 | 1.1580 | -0.1305 [-0.1747, -0.0870] | **lgbm** |
| dst_fum_rec | poisson | 1,534 | 1.1413 | 0.9951 | -0.1462 [-0.1879, -0.1072] | **lgbm** |
| dst_ff | poisson | 1,534 | 1.0997 | 1.1024 | 0.00275 [-0.00274, 0.00878] | **structural** |
| dst_def_td | poisson | 1,534 | 0.4941 | 0.4852 | -0.00889 [-0.0189, 0.00046] | **structural** |
| dst_ret_td | poisson | 1,534 | 0.2051 | 0.2029 | -0.00221 [-0.0126, 0.0102] | **structural** |
| dst_safety | poisson | 1,534 | 0.2109 | 0.2086 | -0.00225 [-0.0143, 0.0119] | **structural** |
| dst_blocks | poisson | 1,534 | 0.3998 | 0.3936 | -0.00626 [-0.0169, 0.00406] | **structural** |

**Summary: LightGBM is chosen for 19 of 30 stages, and the structural baseline stays for 11.**

Two stages have no LightGBM candidate. They are structural by design:

- the kicker's distance-bucket attempt mix, taken from the shrunk lagged band shares;
- the extra-point make rate, taken from the shrunk career PAT%.

## Reading the table

- **Team environment.**
  - LightGBM clearly improves plays (MAE 7.0 to 6.5) and pass rate.
  - The implied-total heuristic for team touchdowns is not beaten.
  - The fitted divisor is about 9.4, not the 7 quoted in MODELING.md section 4 (proposed as D-069).
  - Field-goal attempts are not beaten either.
  - Two-point attempts improve only slightly. The interval just excludes zero, so the rule selects LightGBM.
- **Shares.**
  - LightGBM wins every share stage except carry share, where the EW share holds: -0.00026 [-0.00062, 0.00010].
  - The largest gains are in dropback share, goal-line carry share and the two touchdown shares. These LightGBM candidates see the depth chart and several red-zone windows, which the single EW window does not; this report does not test which input drives the gain.
- **Efficiency.**
  - The gains are small but clear for catch rate, yards per reception, yards per carry, sack rate and scramble rate.
  - Interceptions per attempt stay structural (the interval includes zero).
  - Fumble rate stays structural. LightGBM is very slightly worse, and no lagged player fumble window exists in the features.
  - Completion rate is selected for LightGBM. Since D-057 it is used only as a consistency check, because QB completions are the receivers' receptions.
- **Availability.**
  - The LightGBM classifier on lagged inputs beats the per-position logistic model by 0.027 log loss.
  - Both are the primary configuration: neither reads `avail__` columns, which is asserted in the tests.
- **Kicker.** The shrunk career FG% beats LightGBM, which is worse with a significant interval.
- **DST.**
  - LightGBM clearly improves sacks, interceptions and fumble recoveries over the geometric-mean structural rates.
  - The rare events (defensive and return touchdowns, safeties, blocks, forced fumbles) stay with the shrunk per-game rates.

## Caveats

- Stage-level metrics measure each stage in isolation. Whether the chain beats the transparent baselines on fantasy points is a separate question. B2's walk-forward harness and the evaluator answer it.
- Refitting every 4 weeks (see Protocol) affects both candidates alike. It could narrow small differences, but it does not favour either one.
- The interval for eff_fumble_rate is [0.00000, 0.00005], which touches zero. LightGBM is not better there under any reading.
