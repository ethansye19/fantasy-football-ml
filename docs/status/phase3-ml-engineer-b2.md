# Phase 3 / ML Engineer B2 / wave two: harness, baselines, challenger, calibration, registry
Date: 2026-11-02
Scope: the walk-forward backtest harness, the five transparent baselines under the WeeklyModel protocol (D-053 ruling), the direct LightGBM challenger, post-hoc calibration, and the model registry; quick and default backtests on 2021-2024 with PPR metrics for 2022-2024.

Task list (from the brief):
1. `ffml/models/baselines.py`: D-053 baselines (`rolling_avg_4`, `rolling_avg_3`, `rolling_ew_035`, `prior_season`, new `rolling_median_8`) as WeeklyModel classes.
2. `ffml/models/challenger.py`: `DirectQuantileModel(scoring_name)`.
3. `ffml/eval/harness.py` and `ffml/eval/backtest_configs.py`: `run_walk_forward`, `run_backtest`, configs `default` and `quick`, resumable part files, manifest.
4. `ffml/models/calibration.py`: `OnlineConformal`, `OnlineIsotonic`, `fit_final`.
5. `ffml/models/registry.py`: `REGISTRY.json` with candidate, approved, live, retired.
6. Tests for each, real quick and default runs, PPR 2022-2024 metrics, this note.

Done:
- `ffml/models/baselines.py` (commit 2f79fc9): D-053 ruling. Each baseline's point estimate is its own average, and `pred_q50 = pred_mean` equals that estimate. The other quantiles are the estimate plus the entity's empirical residual quantiles over its last 8 played games. A residual is game points minus that game's own baseline estimate, fallback estimates included. With fewer than 4 residuals, position residual quantiles from prior seasons are used. Lower quantiles are capped at the estimate and upper quantiles floored at it, so the frame is monotone with q50 equal to the estimate. Every quantile is clipped at the position's minimum observed score in prior seasons.
  - Models: `rolling_avg_4`, `rolling_avg_3`, `rolling_ew_035`, `prior_season`, and the new `rolling_median_8` (`RollingMedianBaseline`).
  - WeeklyModel adapter: `fit(cutoff)` records the cutoff; `predict(season, week, scorings)` returns one week under each scoring. The week is computed once per season and scoring, then cached.
  - `predict_seasons` is the wave-one batch form (renamed from `predict`, because the protocol needs that name). `quantile_mode` was removed (superseded by D-053).
  - Universe: every regular-season stat-line row of the week, played or not. This is the same universe as `player_week`/`dst_week`, which I checked: 9,182 + 544 rows in 2023, a one-to-one match.
- `ffml/models/challenger.py` (eb6373b): `DirectQuantileModel(scoring_name)`, named `direct_lgbm`, with key `direct_lgbm_<scoring>`.
  - One pooled LightGBM model for QB/RB/WR/TE/K on `player_week` (position as a categorical), and a separate small DST model on `dst_week`. DST is included, not omitted.
  - Seven quantile boosters plus an L2 mean booster.
  - Features: the primary configuration, which has no `avail__` and no `y_` columns (asserted).
  - Training rows: played regular-season rows before the cutoff week's first kickoff.
  - Fixed hyperparameters: `PLAYER_PARAMS` and `DST_PARAMS` in the module.
  - Rounds are chosen once per season by early stopping. The validation set is the last 8 played weeks of earlier seasons; the cap is 500 rounds and patience is 30.
  - Quantiles are sorted per row and clipped at the training position minimum.
- `ffml/eval/harness.py` (9b541c4, b928006): `run_walk_forward(models, seasons, weeks, scorings, refit_every, run_id, purpose)`.
  - Holdout guard first. Refits at each season's first week, then every `refit_every` weeks.
  - Validation: `validate_predictions` plus checks on season, week, scoring and model label, then casts to contract types.
  - Part files per model and week. Resume skips written parts and refits at the cutoff that the first missing week belongs to.
  - Outcome join: `actual` per scoring, `active` (played), `boom_actual`/`bust_actual` (min rank for boom, max rank for bust, `BOOM_BUST_THRESHOLDS`), `pred_cdf_at_actual` (mid-point PIT through position min, q05..q95, position max), plus `kickoff_utc` and `week_cutoff`.
  - Online calibration writes `<model>+cal` rows.
  - Output: `predictions.parquet` plus `_manifest.json`, with models, wall/fit/predict seconds, params, seasons, weeks, `report_seasons`, `warmup_seasons`, refit, scoring hashes, git, feature version, input snapshot ids, rows and file hash.
  - `load_backtest(run, scoring)` returns rows that `ffml.eval.metrics.evaluate` accepts as is. `evaluate` needs one row per (model, entity, season, week), so it is called once per scoring.
  - `run_backtest(config_name)` runs models in spawn worker processes (`n_jobs`, 3 for default) and then combines. Run id: `$FFML_BACKTEST_RUN_ID`, else `<config>_<UTC date>`.
- `ffml/eval/backtest_configs.py` (9b541c4, 38eee0a): configs `default` (2021-2024, weeks 1-18, refit 1, report 2022-2024), `quick` (2023 weeks 5-8, refit 4) and `baselines`. `ChainModel(config="primary"|"final_report")` is included when importable. Any import or construction error leaves it out with a warning.
- `ffml/models/calibration.py` (21d5f66): calibration is a post-processing step in the harness.
  - `OnlineConformal(levels, by=("position",), min_count=50)`: per-level CQR offsets (the `tau`-quantile of `actual - q_tau`), per position, for each predicted week. Offsets come only from earlier weeks of the run, active rows with an actual, and games before the week's first kickoff. Quantiles are re-sorted and clipped at the position minimum; `pred_mean` is unchanged.
  - `OnlineIsotonic` handles `boom_prob`/`bust_prob` when present.
  - `fit_final(frame, isotonic)` returns a `FrozenCalibration` (JSON via `save_next_to(artifact)`, `apply(frame, use_scoring=...)`).
- `ffml/models/registry.py` (04e70f7): `artifacts/models/REGISTRY.json`.
  - Functions: `register_candidate`, `approve(version, report)`, `set_live` (requires approved; the previous live version is retired), `retire`, `get_live`, `list_models`, `get`.
  - `approve` requires a candidate and a report file under `reports/` with the exact line `Verdict: APPROVED`.
  - Every transition is kept in `history`.
- Tests:
  - `tests/models/test_baselines.py` (22): no current or later game reaches a week's prediction, for all 5 models and 3 weeks; residual quantile values; position fallback and min clip; monotonicity; the median baseline; the protocol adapter; the holdout guard.
  - `tests/models/test_challenger.py` (6): fit/predict on a synthetic frame; rows at or after the cutoff and postponed games never reach the fit; rounds tuned once per season from earlier seasons; feature exclusions; no-DST mode.
  - `tests/models/test_calibration.py` (11): week w offsets and isotonic maps unchanged when week w and later residuals are perturbed; coverage restored; per-position offsets and min_count; partitions; kickoff filter; re-sort and clip; frozen round trip.
  - `tests/models/test_registry.py` (3): transitions and verdict-line checks.
  - `tests/eval/test_harness.py` (11, fake model): join correctness and ghost rows, boom/bust rank rules, PIT interpolation, resumability after a crash with refit_every 2 (identical to a clean run), refusal of 2025, bad frames, manifest and calibration, `evaluate` accepting the output, params persisting across sessions.

Evidence:
- `.venv/Scripts/python -m pytest tests/models/test_baselines.py tests/models/test_challenger.py tests/models/test_calibration.py tests/models/test_registry.py tests/eval/test_harness.py -q`: `53 passed in 10.98s`.
- `.venv/Scripts/python -m pytest -q` (whole repo, during the default run, before the harness params test was added): `323 passed, 1 warning in 71.25s (0:01:11)`. The warning is the expected unsupported-key warning in `tests/scoring`. Final run after all commits: `355 passed, 1 warning in 89.57s (0:01:29)` (an earlier run had 1 failure in B1's `tests/models/test_chain_model.py` while `ffml/models/chain/model.py` was being edited; it passed on rerun).
- `ruff check .`: `All checks passed!`
- Quick backtest: `ffml backtest --config quick`, real 1m6.06s (harness 63.5 s).
  - Run `data/backtests/quick_20261102`: 2023 weeks 5-8, refit at week 5.
  - 73,908 rows: 36,954 raw + 36,954 calibrated.
  - Per model: 6,159 rows per baseline (3 scorings) and 2,053 per challenger instance.
  - Wall time: baselines 0.29-0.33 s each; challenger 17.5 / 21.0 / 21.8 s (ppr / half / std; tuning is most of it).
  - Manifest: feature version 0.2.0; input snapshots fact_player_game 20261102T205628Z, fact_dst_game 20261102T204833Z, dim_game and dim_player 20261102T205627Z, player_week 20261102T213907Z, team_week and dst_week 20261102T213908Z.
- Default backtest: `ffml backtest --config default`, real 31m23.3s (harness 1,880.9 s), 3 worker processes with 2 LightGBM threads each.
  - Run `data/backtests/default_20261102`, manifest `data/backtests/default_20261102/_manifest.json`.
  - 2021-2024 weeks 1-18 (72 weeks), refit every week. `report_seasons` [2022, 2023, 2024], `warmup_seasons` [2021].
  - 1,404,144 rows: 702,072 raw + 702,072 `+cal`. That is 117,012 per baseline (39,004 per scoring) and 39,004 per challenger instance; the challenger covers the same rows as the baselines.
  - Wall time per model (fit + predict, summed): rolling_avg_4 1.8 s, rolling_avg_3 1.7 s, rolling_ew_035 1.7 s, prior_season 1.7 s, rolling_median_8 1.8 s; direct_lgbm_ppr 1,849.5 s, direct_lgbm_half 1,821.8 s, direct_lgbm_std 1,863.6 s. Each challenger ran 72 fits at 10-20 s per weekly fit. Combine plus calibration took 13.2 s.
  - Feature version 0.2.0, same input snapshots as the quick run.
  - Code was at 9b541c4 when the run started. The manifest `git` (456f321) is HEAD when the combine ran; a3eaf87 now records the sha at session start.
  - ChainModel was not importable when the run started (logged), so it is not in this run.
  - Tuned rounds for 2021, player model q05..q95 then mean: ppr 145/95/127/68/58/83/76/63, half 104/128/176/68/58/57/66/49, std 183/147/157/139/83/53/58/89. DST: 33/55/57/77/64/125/25/80.
- Evaluation, PPR, 2022-2024 only (`load_backtest(run, "ppr")`, 23,141 active rows per model). Computed with `ffml.eval.metrics.evaluate(frame, by=("position",), per_week=False, n_boot=0)`, which took 2.0 s. MAE uses `pred_q50`; "MAE mean" uses `pred_mean`; Spearman and start/sit follow the evaluator's ordering rule on `pred_q50`.

  | pos | model | MAE q50 | MAE mean | pin q10 | pin q50 | pin q90 | cov80 | Spearman | start/sit |
  | --- | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | QB | rolling_avg_4 | 6.25 | 6.25 | 1.67 | 3.13 | 1.76 | 0.659 | 0.176 | 0.563 |
  | QB | rolling_avg_4+cal | 6.25 |  | 1.54 | 3.12 | 1.67 | 0.785 | 0.176 | 0.563 |
  | QB | rolling_avg_3 | 6.30 | 6.30 | 1.72 | 3.15 | 1.75 | 0.666 | 0.111 | 0.539 |
  | QB | rolling_avg_3+cal | 6.31 |  | 1.58 | 3.15 | 1.66 | 0.791 | 0.111 | 0.539 |
  | QB | rolling_ew_035 | 6.11 | 6.11 | 1.59 | 3.06 | 1.71 | 0.667 | 0.199 | 0.576 |
  | QB | rolling_ew_035+cal | 6.10 |  | 1.49 | 3.05 | 1.62 | 0.787 | 0.199 | 0.576 |
  | QB | prior_season | 6.86 | 6.86 | 1.47 | 3.43 | 1.77 | 0.647 | 0.219 | 0.577 |
  | QB | prior_season+cal | 6.87 |  | 1.37 | 3.43 | 1.63 | 0.808 | 0.219 | 0.577 |
  | QB | rolling_median_8 | 6.31 | 6.31 | 1.56 | 3.15 | 1.80 | 0.649 | 0.189 | 0.574 |
  | QB | rolling_median_8+cal | 6.31 |  | 1.44 | 3.15 | 1.69 | 0.793 | 0.189 | 0.574 |
  | QB | direct_lgbm | 5.74 | 5.85 | 1.12 | 2.87 | 1.42 | 0.792 | 0.236 | 0.582 |
  | QB | direct_lgbm+cal | 5.74 |  | 1.12 | 2.87 | 1.42 | 0.797 | 0.236 | 0.582 |
  | RB | rolling_avg_4 | 4.40 | 4.40 | 0.98 | 2.20 | 1.43 | 0.686 | 0.165 | 0.557 |
  | RB | rolling_avg_4+cal | 4.34 |  | 0.92 | 2.17 | 1.37 | 0.804 | 0.162 | 0.556 |
  | RB | rolling_avg_3 | 4.46 | 4.46 | 1.02 | 2.23 | 1.42 | 0.686 | 0.215 | 0.572 |
  | RB | rolling_avg_3+cal | 4.42 |  | 0.95 | 2.21 | 1.36 | 0.805 | 0.213 | 0.572 |
  | RB | rolling_ew_035 | 4.28 | 4.28 | 0.94 | 2.14 | 1.40 | 0.693 | 0.210 | 0.569 |
  | RB | rolling_ew_035+cal | 4.22 |  | 0.89 | 2.11 | 1.33 | 0.808 | 0.209 | 0.569 |
  | RB | prior_season | 4.82 | 4.82 | 0.83 | 2.41 | 1.39 | 0.688 | 0.297 | 0.605 |
  | RB | prior_season+cal | 4.62 |  | 0.79 | 2.31 | 1.34 | 0.805 | 0.268 | 0.596 |
  | RB | rolling_median_8 | 4.25 | 4.25 | 0.91 | 2.13 | 1.44 | 0.680 | 0.185 | 0.561 |
  | RB | rolling_median_8+cal | 4.25 |  | 0.87 | 2.13 | 1.37 | 0.805 | 0.185 | 0.561 |
  | RB | direct_lgbm | 3.94 | 4.18 | 0.63 | 1.97 | 1.18 | 0.815 | 0.210 | 0.569 |
  | RB | direct_lgbm+cal | 3.94 |  | 0.63 | 1.97 | 1.18 | 0.809 | 0.210 | 0.569 |
  | WR | rolling_avg_4 | 4.57 | 4.57 | 0.99 | 2.28 | 1.48 | 0.690 | 0.175 | 0.561 |
  | WR | rolling_avg_4+cal | 4.52 |  | 0.94 | 2.26 | 1.42 | 0.796 | 0.181 | 0.563 |
  | WR | rolling_avg_3 | 4.65 | 4.65 | 1.04 | 2.32 | 1.48 | 0.690 | 0.203 | 0.571 |
  | WR | rolling_avg_3+cal | 4.62 |  | 0.97 | 2.31 | 1.42 | 0.798 | 0.202 | 0.571 |
  | WR | rolling_ew_035 | 4.48 | 4.48 | 0.97 | 2.24 | 1.45 | 0.697 | 0.212 | 0.573 |
  | WR | rolling_ew_035+cal | 4.41 |  | 0.92 | 2.20 | 1.39 | 0.801 | 0.211 | 0.574 |
  | WR | prior_season | 4.92 | 4.92 | 0.84 | 2.46 | 1.41 | 0.715 | 0.247 | 0.587 |
  | WR | prior_season+cal | 4.66 |  | 0.81 | 2.33 | 1.36 | 0.810 | 0.247 | 0.587 |
  | WR | rolling_median_8 | 4.38 | 4.38 | 0.94 | 2.19 | 1.47 | 0.696 | 0.231 | 0.580 |
  | WR | rolling_median_8+cal | 4.38 |  | 0.90 | 2.19 | 1.40 | 0.802 | 0.231 | 0.580 |
  | WR | direct_lgbm | 4.08 | 4.34 | 0.63 | 2.04 | 1.25 | 0.799 | 0.218 | 0.576 |
  | WR | direct_lgbm+cal | 4.08 |  | 0.63 | 2.04 | 1.25 | 0.803 | 0.218 | 0.576 |
  | TE | rolling_avg_4 | 3.06 | 3.06 | 0.67 | 1.53 | 1.04 | 0.698 | 0.159 | 0.561 |
  | TE | rolling_avg_4+cal | 3.03 |  | 0.63 | 1.52 | 1.00 | 0.796 | 0.154 | 0.561 |
  | TE | rolling_avg_3 | 3.13 | 3.13 | 0.71 | 1.57 | 1.06 | 0.700 | 0.193 | 0.573 |
  | TE | rolling_avg_3+cal | 3.13 |  | 0.66 | 1.57 | 1.02 | 0.802 | 0.188 | 0.571 |
  | TE | rolling_ew_035 | 3.01 | 3.01 | 0.65 | 1.51 | 1.04 | 0.708 | 0.197 | 0.573 |
  | TE | rolling_ew_035+cal | 2.97 |  | 0.62 | 1.48 | 0.99 | 0.800 | 0.201 | 0.575 |
  | TE | prior_season | 3.26 | 3.26 | 0.52 | 1.63 | 1.01 | 0.739 | 0.291 | 0.610 |
  | TE | prior_season+cal | 3.09 |  | 0.51 | 1.55 | 0.98 | 0.807 | 0.273 | 0.606 |
  | TE | rolling_median_8 | 2.87 | 2.87 | 0.60 | 1.43 | 1.04 | 0.712 | 0.158 | 0.555 |
  | TE | rolling_median_8+cal | 2.87 |  | 0.58 | 1.43 | 1.00 | 0.799 | 0.158 | 0.555 |
  | TE | direct_lgbm | 2.70 | 2.96 | 0.38 | 1.35 | 0.89 | 0.810 | 0.252 | 0.595 |
  | TE | direct_lgbm+cal | 2.70 |  | 0.38 | 1.35 | 0.89 | 0.794 | 0.252 | 0.595 |
  | K | rolling_avg_4 | 4.04 | 4.04 | 0.96 | 2.02 | 1.19 | 0.669 | -0.019 | n/a |
  | K | rolling_avg_4+cal | 4.04 |  | 0.90 | 2.02 | 1.13 | 0.791 | -0.019 | n/a |
  | K | rolling_avg_3 | 4.19 | 4.19 | 1.00 | 2.09 | 1.20 | 0.669 | -0.025 | n/a |
  | K | rolling_avg_3+cal | 4.19 |  | 0.93 | 2.09 | 1.14 | 0.792 | -0.025 | n/a |
  | K | rolling_ew_035 | 4.01 | 4.01 | 0.95 | 2.01 | 1.17 | 0.667 | -0.010 | n/a |
  | K | rolling_ew_035+cal | 4.00 |  | 0.90 | 2.00 | 1.12 | 0.794 | -0.010 | n/a |
  | K | prior_season | 3.78 | 3.78 | 0.85 | 1.89 | 1.08 | 0.648 | 0.015 | n/a |
  | K | prior_season+cal | 3.78 |  | 0.79 | 1.89 | 1.03 | 0.795 | 0.015 | n/a |
  | K | rolling_median_8 | 3.94 | 3.94 | 0.93 | 1.97 | 1.16 | 0.661 | 0.055 | n/a |
  | K | rolling_median_8+cal | 3.94 |  | 0.87 | 1.97 | 1.10 | 0.799 | 0.055 | n/a |
  | K | direct_lgbm | 3.66 | 3.66 | 0.71 | 1.83 | 0.92 | 0.781 | 0.027 | n/a |
  | K | direct_lgbm+cal | 3.66 |  | 0.71 | 1.83 | 0.91 | 0.792 | 0.027 | n/a |
  | DST | rolling_avg_4 | 4.83 | 4.83 | 1.13 | 2.41 | 1.47 | 0.657 | 0.026 | n/a |
  | DST | rolling_avg_4+cal | 4.81 |  | 1.04 | 2.40 | 1.37 | 0.813 | 0.026 | n/a |
  | DST | rolling_avg_3 | 4.93 | 4.93 | 1.16 | 2.46 | 1.48 | 0.660 | 0.061 | n/a |
  | DST | rolling_avg_3+cal | 4.91 |  | 1.06 | 2.46 | 1.39 | 0.799 | 0.061 | n/a |
  | DST | rolling_ew_035 | 4.72 | 4.72 | 1.09 | 2.36 | 1.47 | 0.653 | 0.037 | n/a |
  | DST | rolling_ew_035+cal | 4.69 |  | 1.01 | 2.34 | 1.38 | 0.799 | 0.037 | n/a |
  | DST | prior_season | 4.57 | 4.57 | 0.96 | 2.29 | 1.39 | 0.655 | 0.077 | n/a |
  | DST | prior_season+cal | 4.53 |  | 0.88 | 2.27 | 1.31 | 0.809 | 0.077 | n/a |
  | DST | rolling_median_8 | 4.62 | 4.62 | 1.06 | 2.31 | 1.49 | 0.660 | 0.010 | n/a |
  | DST | rolling_median_8+cal | 4.62 |  | 0.97 | 2.31 | 1.39 | 0.810 | 0.010 | n/a |
  | DST | direct_lgbm | 4.19 | 4.31 | 0.79 | 2.10 | 1.17 | 0.804 | 0.144 | n/a |
  | DST | direct_lgbm+cal | 4.19 |  | 0.79 | 2.10 | 1.17 | 0.813 | 0.144 | n/a |

  - Lineup efficiency (all positions): direct_lgbm 0.563 (+cal 0.564), rolling_median_8 0.522, prior_season 0.514, rolling_ew_035 0.511, rolling_avg_4 0.510, rolling_avg_3 0.500.
  - Paired cluster bootstrap (`compare`, 200 draws over season-weeks, aligned rows, raw, baseline rolling_median_8):
    - direct_lgbm MAE delta: QB -0.57 [-0.75, -0.44], RB -0.32 [-0.38, -0.26], WR -0.30 [-0.35, -0.24], TE -0.17 [-0.22, -0.13]. Pinball q10 and q90 are lower at every position with intervals below zero.
    - Start/sit delta: QB +0.009 [-0.024, 0.044], RB +0.008 [-0.014, 0.030], WR -0.004 [-0.022, 0.017], TE +0.040 [0.007, 0.070].
    - prior_season beats rolling_median_8 on start/sit for RB (+0.044 [0.015, 0.073]) and TE (+0.055 [0.011, 0.091]), and on Spearman for RB and TE, despite a 0.39-0.56 higher MAE. Calibrated rows (baseline rolling_median_8+cal) give the same picture: direct_lgbm+cal MAE delta QB -0.57 [-0.75, -0.43], RB -0.32 [-0.38, -0.26], WR -0.30 [-0.36, -0.25], TE -0.17 [-0.22, -0.14]; 80% coverage deltas within +-0.02 of the median baseline's 0.79-0.81.
  - Calibration: online offsets move raw baseline 80% coverage from 0.65-0.74 to 0.78-0.81 at every position. The challenger is already at 0.78-0.82 raw and 0.79-0.81 calibrated.
  - Diagnostic (not a report number): ranking the challenger by `pred_mean` instead of `pred_q50` raises its Spearman to QB 0.273, RB 0.219, WR 0.223, TE 0.264, and start/sit to QB 0.597, RB 0.574, WR 0.579, TE 0.598.
- Frozen calibration on real rows: `OnlineConformal().fit_final` on the quick run's `rolling_median_8` rows, saved to the scratchpad (no model artifact exists yet). PPR offsets q10/q50/q90: WR -0.06/0.00/2.13, RB -0.49/0.00/1.91, QB -1.67/0.06/4.11.

Not done / blocked:
- ChainModel is not in the default run, because B1 landed it (f59ef9d, 17:14 local) after the run started (16:58). The default config now builds `chain_v1` and `chain_v1_final_report` (unique keys, checked). To add them to the same run, use `FFML_BACKTEST_RUN_ID=default_20261102 ffml backtest --config default`. Every baseline and challenger part is skipped, only the chain runs, then the combine recomputes calibration and the manifest. The CLI has no `--run-id` option, so the env var is the way to pass it.
- No frozen calibration file is saved next to a model artifact, because there is no artifact yet. `save_next_to(frozen, artifact)` is ready for the chain.
- Live (non-backtest) baseline predictions need a roster universe for the coming week. `actual_statlines` only has final games (D-062), so `predict` currently needs the week's stat-line rows. Before the baselines serve live, the universe should come from the `player_week` and `dst_week` keys.
- Half-PPR and standard metrics were computed into the predictions file but not tabulated here.

Decisions proposed:
- D-070 Baseline details under D-053:
  - Residuals use the estimate the baseline issued for that game, fallback included.
  - Lower quantiles are capped at, and upper quantiles floored at, the estimate, which keeps `q50 = pred_mean` monotone.
  - Every quantile is clipped at the position's minimum observed score in prior seasons.
  - The prediction universe is every rostered stat-line row of the week, played or not, the same as the chain's and the challenger's.
- D-071 Harness outcome conventions:
  - `actual` is 0 for a rostered player who did not play and null without a row; `active = played`.
  - `boom_actual` and `bust_actual` are null for rows that did not play.
  - PIT is the mid-point PIT through the knots (position min of prior seasons, q05..q95, position max of prior seasons).
  - Refits are due at each season's first scheduled week and then every `refit_every` weeks.
  - `evaluate` is called once per scoring, through `load_backtest`.
- D-072 Calibration:
  - Per-level CQR offsets per position, from an expanding window over earlier weeks of the run (2021 warm-up), limited to games before the week's first kickoff, with a minimum of 50 rows. `pred_mean` is unchanged.
  - Frozen offsets for the holdout are fitted on the 2022-2024 rows per scoring.
  - A league scoring that is not one of the presets uses the offsets of the preset with the nearest reception weight (`apply(use_scoring=...)`) until per-league calibration exists.
- D-073 Challenger:
  - One pooled QB/RB/WR/TE/K model with position as a categorical, plus a separate DST model.
  - Fixed hyperparameters as in the module; rounds tuned once per season on the last 8 played weeks of earlier seasons.
  - Not shipped. It beats every transparent baseline on MAE and pinball at every position, with paired intervals excluding zero.
- D-074 Start/sit and Spearman comparisons: each model's startable set is its own top-N, so populations differ across models. The evaluator should consider also reporting start/sit on a common startable set (for example, the union, or the set of a fixed reference ranking), because prior_season wins RB/TE start/sit while losing MAE by 0.4-0.6 points.
- D-075 Backtest identity: the run id defaults to `<config>_<UTC date>`, overridable by `FFML_BACKTEST_RUN_ID`. A rerun with the same id resumes. Parts are per model and week, and the manifest records sessions with git sha. Proposed for the CLI owner: add `--run-id` and `--n-jobs` options to `ffml backtest`.
- D-076 Registry:
  - `approve` requires a candidate and a report under `reports/` with the exact line `Verdict: APPROVED`.
  - `set_live` requires approved and retires the previous live version.
  - Retired versions cannot go live again without a new candidate entry.
- For the orchestrator: `ffml/models/__init__.py` (not my file) could also export `RollingMedianBaseline`, `default_baselines` and `DirectQuantileModel`.

Next:
- Add ChainModel primary and final_report to `default_20261102` (resume command above), then tabulate chain versus baselines and challenger.
- Fit and save frozen calibration next to the chain artifact once it is registered as a candidate.
- Build a live roster universe for the baselines.

---

# Follow-up / ML Engineer B2 / calibration v2 (D-086)
Date: 2026-11-03
Scope: per-position walk-forward isotonic boom/bust calibration with a pooled fallback; recalibrate the main and ablation runs without refitting; freeze the calibrator next to the chain candidate and register the candidate.

Done:
- `ffml/models/calibration.py` (3455c29): calibration version `v2-per-position-isotonic`.
  - `OnlineIsotonic(by=("position",), min_count=200)` fits one map per position for each predicted week. Maps use earlier weeks only and the same kickoff rule. A position under `min_count` uses the map pooled over positions, then the identity.
  - The source of each row is written to `cal_boom_source` / `cal_bust_source` (`position`, `pooled`, `identity`).
  - `fit_final` freezes `(scoring, position)` maps plus a pooled map per scoring. The format is `ffml.calibration/2`; v1 files still load.
  - `FrozenCalibration.apply` writes the sources and `calibration_version`.
  - Conformal quantile offsets are unchanged: the calibrated quantile columns are identical to v1 across all 936,096 calibrated rows.
- `ffml/eval/harness.py`: `calibration_version` goes on the calibrated rows (null on raw rows) and into the manifest (`calibration_version`, `calibration.version`). Every combine recalibrates from the raw parts, so `--recalibrate` was not needed.
- `ffml/models/registry.py`: `register_candidate(..., calibration=path)` records the file's path, SHA-256 and version.
- Tests:
  - `tests/models/test_calibration.py` (+5): per-position maps use only earlier weeks (kicker outcomes flipped from week w on leave every position's week-w probabilities and sources unchanged); per-position beats pooled on a flat kicker truth; pooled-then-identity fallback with sources; frozen per-position round trip, unknown scoring, `use_scoring`, and a v1 file.
  - `tests/eval/test_harness.py` (+1): version and source columns, and recombine reproducing the file without refits.
  - `tests/models/test_registry.py` (+1): calibration field.
- Recalibrated `data/backtests/default_20261102` and `data/backtests/ablations_20261103`. The v1 manifests are kept as `_manifest_cal_v1_pooled_isotonic.json`, and the ablation v1 predictions as `predictions_cal_v1_pooled_isotonic.parquet`. The preserved main-run v1 file was not touched (SHA-256 still `d4a420c0c95c57ca0464a186861b356c05ca4078db0ee7bd6f5fbaf843d8027c`).
- Frozen calibrator for the candidate: `artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z/calibration.json`.
  - SHA-256 `860af8926e75b93e781eb94a0c6055206b8fdf78b9e539f072eb80293a21da19`.
  - Fitted on the 87,576 raw `chain_v1` rows of 2022-2024 under ppr, half and std.
  - Contents: 18 offset rows, and 18 per-position maps plus 3 pooled maps for each of boom and bust.
- Registry: `artifacts/models/REGISTRY.json` holds `chain_v1-primary-c2024w19-20261102T222634Z` as `candidate`, with `backtest_run_id` `default_20261102`, the calibration hash above, and `evaluator_report` null. Nothing is approved.

Evidence:
- `.venv/Scripts/python -m pytest tests/models/test_calibration.py tests/eval/test_harness.py tests/models/test_registry.py -q`: `32 passed in 8.24s`.
- `.venv/Scripts/python -m pytest -q`: `407 passed, 1 warning in 126.85s (0:02:06)`. One earlier full run had `1 failed, 406 passed`, in `tests/eval/test_report_backtest.py::test_build_report_end_to_end`. That test passed alone (20 passed) and in the rerun of the whole suite.
- `ruff check .`: `All checks passed!`
- `ffml backtest --config default --run-id default_20261102 --n-jobs 1`: real 0m50.8s (harness 46.9 s, combine 46.5 s). All 10 models skipped every part, so nothing was refit. 1,872,192 rows (936,096 raw + 936,096 calibrated), as before. Git 3455c29.
- `ffml backtest --config ablations --run-id ablations_20261103 --n-jobs 1`: real 0m20.8s, 525,456 rows (262,728 + 262,728).
- Raw rows identical: SHA-256 of the sorted raw rows (uncompressed IPC, v1 columns) is `2cb459a37800f06ba7e57befb1bab7141c1a34a059b258983eb3ce1efb03ebd5` in both the preserved and the new main file (936,096 rows). For the ablation run it is `54b0c3c6b04ab237379b48ee10bdad47bfef808f74904125878d78c85d24fc32` in both (262,728 rows). The three new columns are null on every raw row.
- Sources: every 2022-2024 `chain_v1+cal` row in the main run used its own position map, because 2021 warms it up. The ablation run, which has no 2021, uses identity and then pooled maps in the first weeks of 2022.
- Before/after, `chain_v1+cal`, PPR 2022-2024 (logistic calibration slope / Brier; quick check, the evaluator does the formal version):

  | pos | boom slope raw / v1 / v2 | boom Brier raw / v1 / v2 | bust slope raw / v1 / v2 | bust Brier raw / v1 / v2 |
  | --- | --- | --- | --- | --- |
  | QB | 1.287 / 1.185 / 0.923 | 0.117 / 0.116 / 0.116 | 1.313 / 1.199 / 0.961 | 0.189 / 0.189 / 0.190 |
  | RB | 1.162 / 1.108 / 0.966 | 0.088 / 0.088 / 0.088 | 1.176 / 1.105 / 1.016 | 0.143 / 0.142 / 0.142 |
  | WR | 0.893 / 0.913 / 0.946 | 0.064 / 0.064 / 0.065 | 1.066 / 1.012 / 1.006 | 0.148 / 0.148 / 0.148 |
  | TE | 0.946 / 0.956 / 0.930 | 0.052 / 0.052 / 0.052 | 1.028 / 1.005 / 0.949 | 0.113 / 0.113 / 0.114 |
  | K | 0.155 / 0.142 / 0.118 | 0.177 / 0.177 / 0.177 | 0.259 / 0.153 / 0.456 | 0.232 / 0.233 / 0.229 |
  | DST | 1.454 / 1.295 / 0.774 | 0.165 / 0.164 / 0.165 | 1.244 / 1.102 / 0.289 | 0.221 / 0.221 / 0.222 |

Not done / findings:
- Kicker boom is still poorly calibrated after v2 (slope 0.118). The raw kicker probabilities carry almost no signal, and isotonic maps cannot add any. Under D-086 the app should hide kicker boom/bust or mark it low confidence.
- DST bust gets worse under v2 (slope 1.102 to 0.289) while Brier is flat. With about 32 rows per week, the per-position isotonic maps overfit noisy steps. DST should be treated like K, or given a smoother per-position method.
- Brier moves by at most 0.004 at any position, so v2 mainly changes slopes, not accuracy.

Decisions proposed:
- Proposal (next free number): for positions whose walk-forward slope stays outside 0.7-1.3 after v2 (today K boom, K bust, DST bust), recalibrate with a two-parameter logistic (Platt) map per position. Alternatively, keep isotonic and require the larger `min_count` for those positions before a per-position map is used. Either way, confirm walk-forward before the holdout freeze. Until then, the frozen v2 file stands, and K, plus DST bust, are shown as low confidence in the app.
