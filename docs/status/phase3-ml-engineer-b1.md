# Phase 3 / ML Engineer B1 / wave two: structural chain, stage selection, wave-one engine fixes
Date: 2026-11-03
Scope: two deliverables. (1) The wave-one fixes in `ffml/scoring` and `ffml/sim` (D-055, D-056, D-057). (2) The structural model chain in `ffml/models/chain/`: football outcomes first, fantasy points last. It has a structural baseline and a LightGBM candidate per stage, walk-forward stage selection, a parameter builder into the Monte Carlo engine, a `ChainModel` under the WeeklyModel protocol, and `ffml train`.

Task list (from the brief):
1. D-055 forced fumbles (vocabulary, actuals, `ff` and `def_st_ff` keys, engine, tests), then rerun the Sleeper week 8 comparison.
2. D-056 game-level defaults.
3. D-057 QB passing totals from the receivers, with an unmodeled-receivers bucket, and conservation tests.
4. Stages 1a-1c, 2, 3, 4, availability (primary and final_report), kicker and DST. Each stage gets a structural baseline (MODELING.md section 3) and a LightGBM candidate.
5. Selection on walk-forward 2022-2024 with a week-clustered bootstrap CI. Results go to `selection.json` and the report.
6. Dispersion estimated from training-window residuals only.
7. `params_builder.py`, `model.py` (`ChainModel`, predict frame with drivers) and `train.py` (artifact).
8. Tests and the timing budget (fit < 90 s, predict < 20 s).
9. This note.

Done:
- **D-055** (commit 2e836f8):
  - `forced_fumbles` is in the DST part of `STAT_COLUMNS`.
  - `actual_statlines` maps `fact_dst_game.forced_fumbles`. Older snapshots without the column read 0.
  - `ff` and `def_st_ff` both score the combined count, with the mean of the values present. This is the D-054 two-point convention, documented in `rules.py`. `st_ff` (an individual player key) stays unsupported.
  - The engine draws `forced = Binomial(fumble recoveries, ff_per_fum_rec) + Poisson(max(ff_rate - q * E[recoveries], 0.02))`.
  - `DstGameParams` gains `ff_rate` and `ff_per_fum_rec`.
  - On 2016-2024 the fitted q (the slope of forced fumbles on recoveries) is 0.84.
- **D-056**: `TeamGameParams` defaults are `total_sd = 13.2` and `margin_sd = 12.8`.
- **D-057**: within every simulation, the passers' completions, yards and TDs are the sums of the team's receptions, receiving yards and receiving TDs, bucket included.
  - The bucket (`unmodeled_tgt_share`, `unmodeled_catch_rate`, `unmodeled_yards_per_rec`) absorbs throwaways, spikes and targets to players without a row. Fitted on 2016-2024 it holds 3.5% of attempts with 0.9% caught.
  - Several passers split completions by a hypergeometric draw over attempts, TDs by a hypergeometric draw over completions, and yards in proportion to completions with the integer remainder going to the passer with the most completions.
  - Interceptions and sacks still come from QB rates on dropbacks.
  - Two corrections I found while reconciling engine volumes with `fact_team_game`:
    - `dropbacks` include scrambles, and `plays` exclude kneel-downs. QB scrambles (`scramble_rate`) and kneel-downs (`exp_kneels`, 0.70 per team-game in 2016-2024) are now QB carries.
    - `carry_share` is the share of designed runs.
    - Without these, the engine's runs would be about 9% below real team carries (24.3 vs 26.6 per game) and its attempts about 5% above real pass attempts (35.9 vs 34.3). These are 2016-2024 means, computed from the data, not from engine runs.
  - A team always fields a passer: if no QB is drawn active, the second-ranked QB plays, and his draw probability is lowered so that his marginal stays equal to `availability_prob`.
- **Chain package `ffml/models/chain/`** (f59ef9d, 456f321, db78c70):
  - `stages.py`: 30 stage specifications, covering targets, populations, weights, the structural method, the LightGBM objective, curated feature lists and monotone constraints.
    - Team: plays, pass rate, offensive TDs, FG attempts, two-point attempts.
    - Shares: target, carry, dropback, goal-line carry, red-zone target.
    - Efficiency: catch rate, yards per reception, yards per carry, completion rate, INT rate, sack rate, scramble rate, fumble rate.
    - TD shares: receiving, rushing.
    - Availability.
    - Kicker FG%.
    - DST: sacks, INT, fumble recoveries, forced fumbles, defensive TDs, return TDs, safeties, blocks.
    - The kicker distance mix and the XP rate are structural only (no candidate).
  - `structural.py`: the MODELING.md section 3 baselines, each with at most two fitted numbers and a documented fallback chain:
    - L5 plays;
    - EW pass rate plus a one-parameter spread fit;
    - implied total over a fitted divisor;
    - EW blends;
    - EW and shrunk EW shares, with a position-and-depth prior learned from players without history;
    - shrunk EW rates;
    - geometric-mean DST rates.
  - `lgbm.py`: fixed hyperparameters. Early stopping on the last 8 weeks inside the training window, then a refit on the full window. The early-stopping hold-out predictions are kept for dispersion.
  - `availability.py`:
    - Primary: a per-position logistic regression on lagged inputs only. Inputs are games missed of the last 5, weeks since the last game, the previous week's report status, weeks since last Out, depth rank and starter flag, snap share L1 and EW, games played, and the rookie flag. The frame is stripped of `avail__` before the model sees it.
    - `final_report`: `min(section G cap, primary P)`. The Questionable rate is estimated per position among starters: WR 0.75, TE 0.78, RB 0.70, K 0.76, QB 0.63 at cutoff 2023 w10.
  - `dispersion.py`: method-of-moments matches against the engine's own noise for:
    - sd of plays by week bucket;
    - pass-rate Beta concentration;
    - Dirichlet concentration per position;
    - Gamma shapes for yards per reception and per carry.
  - `params_builder.py`: stage predictions become the three engine frames.
    - Shares are scaled so that `sum P(active) * share = 1`, or `1 - bucket` for targets.
    - The carry share is converted to designed runs.
    - Kicker bands come from the 0.2.0 band features, with a logit shift when the FG% stage is LightGBM.
    - For DST, when the count model is LightGBM, the rate passed is chosen so the engine's blend reproduces the predicted count.
  - `summary.py`: quantiles q05-q95, the mean, boom and bust are conditional on active.
    - Ranks are taken within each simulation among all simulated entities, with inactive draws at 0 (D-017).
    - `active_prob`, `pred_mean_uncond` and `pred_q50_uncond` come from all simulations.
    - Entities with fewer than 100 active simulations take their conditional columns from a second simulation in which they are forced active.
  - `drivers.py`: signed contributions relative to a position-average player, by sequential substitution on an analytic expected-points function with the league's linear weights.
    - The order is environment, opportunity, efficiency, matchup.
    - Matchup is the change from neutral-opponent inputs to actual inputs: defense features at their training means for LightGBM stages, plus the engine's DST rate blend.
    - Availability is `(P(active) - 1) * F`.
    - DSTs are split from the simulation (band points vs event points).
    - The JSON carries `reference` and `method`.
  - `model.py`: `ChainModel(config, stage_choice, n_sims, seed)` with `fit`, `predict`, `save` and `load`. The seed per week is `seed + 100 * season + week`.
  - `selection.py`: the walk-forward stage selection runner.
  - `synthetic.py`: a small synthetic league for tests.
  - `ChainData.shared()`: one feature cache per process, so B2's primary and final-report models load the features once.
- **Selection** (456f321): `ffml/models/chain/selection.json` and `reports/ml_stage_selection_2026-11-03.md`. The full table is in the report. LightGBM is chosen for 19 of 30 stages.
- **`ffml/models/train.py`**: `run_train(config_name)` with configurations `default` / `primary` (cutoff 2024 w19), `final_report`, or a JSON file path. It refuses a cutoff in the holdout season.
  - It writes `artifacts/models/chain/<model_version>/`: `params.json`, `lgbm/<stage>.txt`, `features.json` (feature version, fingerprint and its SHA-256, LightGBM inputs), `selection.json` and `train_manifest.json` (feature snapshot ids, git sha, seeds, timings, row counts).
  - Trained artifact: `artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z` (6.4 MB). `ChainModel.load` reads it back.
- **Tests**:
  - `tests/models/test_chain_structural.py` (12): one test per baseline family; the availability model reads no `avail__` column (column-name inspection and scrambling); section G caps.
  - `test_chain_params.py` (8): renormalisation sums to 1 among available players; builder dtypes and ranges; designed-run conversion; kicker shift; the DST implied rate; drivers adding up.
  - `test_chain_model.py` (10): protocol frame and `validate_predictions`; monotone quantiles; deterministic seeds; save/load; LightGBM round trip; the primary model ignores same-week reports while final_report uses them; `run_train` artifact; selection rule.
  - `test_chain_real_data.py` (slow): the end-to-end run described under Evidence.
  - `tests/sim/test_engine.py`: conservation of passing totals in every simulation; bucket with two passers; scrambles and kneels; passer fallback; forced fumbles.
  - `tests/scoring`: `ff`/`def_st_ff` and the actuals mapping.

Evidence:
- Test runs:
  - `.venv/Scripts/python -m pytest -q`: `355 passed, 1 warning in 80.70s`. The warning is the expected unsupported-key warning in the Sleeper test.
  - Slow tests: `pytest tests/models/test_chain_real_data.py tests/scoring/test_sleeper_points_week8.py -m slow -q -s`: `2 passed, 1 warning in 19.99s`.
  - `ruff check .`: `All checks passed!`.
- Sleeper week 8 comparison, test league, games final at capture:
  - offense and kickers 82/83 (98.8%), unchanged: the Jalen Coker stat-source gap;
  - **DST 7/7 (100.0%)**, up from 3/7. The DST gate in the test is now at least 0.85.
- Timings: the machine was shared with other agents' jobs, so the same fit varied.
  - Selected chain at cutoff (2023, 10): fit 15.0 s, 28.8 s and 29.4 s on three runs. Predict for 2023 week 10, one scoring, 2,000 simulations: 2.9-4.5 s.
  - `ffml train --config default` (through 2024): fit 41.2 s.
  - The loaded artifact predicts 2024 week 18 under three scorings in 7.6 s.
  - Structural-only chain: fit 2.0 s, predict 4.0 s.
  - All 30 stages as LightGBM: fit 48.9 s.
- Sanity check, 2023 week 10, PPR, active players (fit at (2023, 10), never 2025):

  | position | n | chain median of `pred_q50` | chain mean of `pred_mean` | actual mean | actual median |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | QB | 37 | 11.86 | 12.25 | 12.36 | 9.6 |
  | RB | 87 | 3.90 | 7.30 | 7.28 | 4.8 |
  | WR | 138 | 4.50 | 6.72 | 6.82 | 3.8 |
  | TE | 83 | 1.50 | 4.12 | 3.83 | 1.6 |

  Every active QB, RB, WR and TE has a row: 0 missing.
- My own diagnostic, not the evaluator's numbers: 2023 weeks 2-18, refit every 4 weeks, 1,000 simulations, PPR, active players.

  | position | MAE of `pred_mean`: chain | MAE: L4 average | uncalibrated q10-q90 coverage |
  | --- | ---: | ---: | ---: |
  | QB | 5.97 | 6.12 | 0.73 |
  | RB | 3.98 | 4.16 | 0.87 |
  | WR | 4.19 | 4.43 | 0.86 |
  | TE | 2.84 | 3.00 | 0.88 |
  | K | 3.55 | 3.97 | 0.88 |
  | DST | 4.49 | 5.03 | 0.80 |

  MAE of `pred_q50` is lower still (QB 5.85, RB 3.81, WR 3.94, TE 2.65).
- Availability log loss, 2023 week 10, all rostered rows: structural primary 0.343, final_report 0.278.
- Selection: 12 of the 15 refits of all 30 stages took 44-54 s each. Three took 62-132 s while my test suite ran at the same time, so the mean was 56 s.
- Fitted through 2024 (artifact):
  - team-TD divisor 1/0.1066 = 9.4;
  - share concentration QB 2.0 (at the clip), RB 7.5, WR 76, TE 63;
  - pass-rate concentration 206;
  - sd of plays 8.2 for weeks 5+.

Not done / blocked:
- Dispersion is from training residuals only, as briefed. Uncalibrated intervals are too wide for RB, WR, TE and K and too narrow for QB (2023 diagnostic above). B2's conformal step adjusts them. Tuning concentrations on the validation seasons (MODELING.md section 6) is a follow-up if the orchestrator wants it before calibration.
- For LightGBM stages, dispersion uses the early-stopping hold-out, which is the last 8 weeks of the window.
  - Week buckets missing from that slice fall back to the engine defaults. Through 2024 this applies to plays in weeks 1-4, where the sd is the default 7.5.
  - A wider residual window (an out-of-fold pass) would fix it.
- `final_report` reuses the primary stage selection; it has no selection of its own.
- No lagged return-TD or fumble windows exist for players:
  - `ret_td_rate` is 0, so individual returners score no return TDs (the DST unit still does);
  - fumble rate is a position prior (D-068).
- A backup QB's effect on his receivers' efficiency is not modelled beyond what the receivers' features carry.
- Drivers use linear scoring terms only (no bands, no two-point conversions).
- ROS mode and 2026 predictions are not in this brief. 2026 needs 2025 history (D-058).
- Protocol: no change needed. `ChainModel(config=...)` matches B2's `chain_models()`. For a weekly-refit default backtest, the chain's fit of 15-41 s per week dominates the run time.

Decisions proposed (B2 used D-070 to D-076):
- D-063 Stage-selection metrics: absolute error for continuous targets, shares and rates (rates weighted by their denominator), mean Poisson deviance for counts, log loss for availability. MAE is not a proper score for an expected count. Selection may refit every 4 weeks.
- D-064 "Active" means `y_played`, a snap or a stat in the game. The engine always fields a passer per team: if no QB is drawn active, the second QB plays, with a marginal-preserving draw probability.
- D-065 Final-report availability is `min(section G cap, primary P(active))`, so a healthy backup keeps his low participation probability. The Questionable rate is estimated per position among starters. Same-week roster status `INA` (6,593 rows in 2016-2024, none played) is not in the section G list and is not used. Should the live layer add it?
- D-066 Engine volume conventions:
  - dropbacks include scrambles;
  - scrambles and kneel-downs are QB carries;
  - `carry_share` is the designed-run share;
  - the unmodeled bucket share and catch rate are fitted on the training window.
- D-067 `boom_prob` and `bust_prob` are conditional on active, like the `pred_*` columns, with ranks among all simulated entities and inactive draws at 0. Entities with fewer than 100 active simulations take their conditional columns from a forced-active second simulation.
- D-068 Feature request for the Feature Engineer: lagged player fumble-rate and return-TD windows. Today, fumble rate is a position prior and returners' TDs are not simulated per player.
- D-069 The team-TD structural heuristic uses a fitted divisor (`implied / k`, k about 9.4 on 2016-2024) instead of the literal `/ 7` in MODELING.md section 4. With 7, expected team points would be about 27 against an average implied total of 22.6.

Next:
- Review B2's chain backtest (weekly refits, 2022-2024) against the baselines once it runs.
- If asked: tune the share concentrations and Gamma shapes on the validation seasons, and run a final_report-specific selection.
