# Evaluator audit

Overall: **PASS**

- Holdout audit (season 2025): **PASS**
- Leakage audit: **PASS**
- Release-candidate inputs audit: **PASS**

## Holdout audit (season 2025)

Overall: **PASS** (PASS 4, WARN 1). Run at 2026-11-03T01:06:27+00:00 on the working tree at git `fd963d0`.

| check | status | detail |
| --- | --- | --- |
| holdout access log | PASS | artifacts/holdout_access.log absent: no holdout access recorded |
| literal holdout season | WARN | 2025 appears only in docstrings or comments (6; 56 files under ffml/features, ffml/models, ffml/sim, ffml/scoring, ffml/eval) |
| unlock assignments | PASS | none outside tests and the guard (78 files scanned) |
| .env unlock | PASS | .env absent |
| process environment | PASS | FFML_HOLDOUT_UNLOCK not set |

### literal holdout season (WARN)

- ffml/features/access.py:4 (docstring)
- ffml/features/context.py:171 (docstring)
- ffml/features/context.py:250 (docstring)
- ffml/models/baselines.py:497 (docstring)
- ffml/models/train.py:21 (docstring)
- ffml/sim/predict.py:11 (docstring)

## Leakage audit

Overall: **PASS** (PASS 7). Run at 2026-11-03T01:06:29+00:00 on the working tree at git `fd963d0`.

| check | status | detail |
| --- | --- | --- |
| leakage suite | PASS | pytest tests/features/test_leakage.py -q: 14 passed in 14.43s |
| timestamp audit | PASS | 28897 rows checked against week first kickoff from dim_game; no violations |
| shuffle probe | PASS | no feature copies a target; 772 feature columns x 66 targets (60 position-specific, compared within position); sampled rows: ALL 2000, K 1761, QB 2000, RB 2000, TE 2000, WR 2000; 74377 pairs computed on rows where both are non-null, 15908 skipped for overlap below 200, 10075 undefined (constant); max \|corr\| 0.851, shuffled-target reference 0.222 |
| timestamp audit (team_week) | PASS | 1708 rows checked against week first kickoff from dim_game; no violations |
| shuffle probe (team_week) | PASS | no feature copies a target; 274 feature columns x 17 targets (0 position-specific, compared within position); sampled rows: ALL 1708; 4658 pairs computed on rows where both are non-null, 0 skipped for overlap below 200, 0 undefined (constant); max \|corr\| 0.388, shuffled-target reference 0.089 |
| timestamp audit (dst_week) | PASS | 1708 rows checked against week first kickoff from dim_game; no violations |
| shuffle probe (dst_week) | PASS | no feature copies a target; 342 feature columns x 10 targets (0 position-specific, compared within position); sampled rows: ALL 1708; 3420 pairs computed on rows where both are non-null, 0 skipped for overlap below 200, 0 undefined (constant); max \|corr\| 0.395, shuffled-target reference 0.081 |

### timestamp audit (PASS)

- rows: 28897
- null_cutoff_ts: 0
- null_asof_ts: 0
- null_kickoff_utc: 0
- cutoff_after_kickoff: 0
- asof_after_kickoff: 0
- asof_after_cutoff: 0
- week_without_schedule: 0
- cutoff_not_week_first_kickoff: 0
- asof_not_cutoff_minus_1h: 0

### shuffle probe (PASS)

- top 1: opp__snap_share__ew vs y_offense_pct [WR]: corr 0.851 (n=1648)
- top 2: opp__snap_share__l3 vs y_offense_pct [WR]: corr 0.837 (n=1648)
- top 3: opp__snap_share__l5 vs y_offense_pct [WR]: corr 0.834 (n=1648)
- top 4: opp__snap_share__std vs y_offense_pct [WR]: corr 0.831 (n=1648)
- top 5: opp__snap_share__l1 vs y_offense_pct [WR]: corr 0.830 (n=1648)
- compared within K: 16 targets (y_fg_att, y_fg_blocked, y_fg_made, y_fg_made_0_39, y_fg_made_20_29, y_fg_made_30_39, y_fg_made_40_49, y_fg_made_50_59, y_fg_made_50p, y_fg_missed, y_fg_missed_30_39, y_fg_missed_40_49, y_fg_missed_50_59, y_fg_missed_60p, y_pat_att, y_pat_made)
- compared within QB: 15 targets (y_completion_rate, y_completions, y_dropback_share, y_dropbacks, y_int_per_attempt, y_interceptions, y_pass_air_yards, y_pass_att, y_pass_td, y_pass_yds, y_sack_yards, y_sacks, y_scrambles, y_two_pt_conversions, y_yards_per_attempt)
- compared within QB/RB: 4 targets (y_gl_carries, y_gl_carry_share, y_rush_td, y_rush_td_share)
- compared within QB/RB/TE/WR: 10 targets (y_carries, y_carry_share, y_designed_rush, y_fantasy_points_ppr_ref, y_fantasy_points_std_ref, y_offense_pct, y_offense_snaps, y_rush_first_downs, y_rush_yds, y_yards_per_carry)
- compared within QB/RB/WR: 2 targets (y_fumbles_lost, y_rz_carries)
- compared within RB/TE/WR: 13 targets (y_catch_rate, y_ez_targets, y_pass_td_share, y_rec_air_yards, y_rec_td, y_rec_yac, y_rec_yds, y_receptions, y_rz_target_share, y_rz_targets, y_target_share, y_targets, y_yards_per_target)

### timestamp audit (team_week) (PASS)

- rows: 1708
- null_cutoff_ts: 0
- null_asof_ts: 0
- null_kickoff_utc: 0
- cutoff_after_kickoff: 0
- asof_after_kickoff: 0
- asof_after_cutoff: 0
- week_without_schedule: 0
- cutoff_not_week_first_kickoff: 0
- asof_not_cutoff_minus_1h: 0

### shuffle probe (team_week) (PASS)

- top 1: ctx__implied_team_total vs y_points [ALL]: corr 0.388 (n=1708)
- top 2: ctx__implied_team_total vs y_offensive_td [ALL]: corr 0.375 (n=1708)
- top 3: ctx__spread vs y_points [ALL]: corr -0.325 (n=1708)
- top 4: ctx__implied_team_total vs y_rz_trips [ALL]: corr 0.322 (n=1708)
- top 5: team__proe__ew vs y_pass_rate [ALL]: corr 0.301 (n=1708)

### timestamp audit (dst_week) (PASS)

- rows: 1708
- null_cutoff_ts: 0
- null_asof_ts: 0
- null_kickoff_utc: 0
- cutoff_after_kickoff: 0
- asof_after_kickoff: 0
- asof_after_cutoff: 0
- week_without_schedule: 0
- cutoff_not_week_first_kickoff: 0
- asof_not_cutoff_minus_1h: 0

### shuffle probe (dst_week) (PASS)

- top 1: ctx__implied_opp_total vs y_yards_allowed [ALL]: corr 0.395 (n=1708)
- top 2: ctx__implied_opp_total vs y_points_allowed [ALL]: corr 0.388 (n=1708)
- top 3: ctx__spread vs y_points_allowed [ALL]: corr 0.325 (n=1708)
- top 4: ctx__spread vs y_yards_allowed [ALL]: corr 0.304 (n=1708)
- top 5: team__epa_per_play__std vs y_yards_allowed [ALL]: corr 0.273 (n=1708)

## Release-candidate inputs audit

Overall: **PASS** (PASS 3). Run at 2026-11-03T01:07:09+00:00 on the working tree at git `fd963d0`.

| check | status | detail |
| --- | --- | --- |
| restricted data reads | PASS | no reference to the restricted archive (49 files under ffml, api, web, scripts outside ffml/data, ffml/eval) |
| backtest inputs | PASS | 3 runs: purpose backtest, no holdout-season rows, feature snapshots equal the audited LATEST |
| candidate feature inputs | PASS | 1 chain artifacts: no target inputs, no same-week injury inputs in primary artifacts |

### backtest inputs (PASS)

- default_20261102: purpose backtest, seasons [2021, 2022, 2023, 2024], feature version 0.2.0, feature snapshots match LATEST
- dev_baselines: purpose backtest, seasons [2022, 2023], feature version 0.2.0, feature snapshots match LATEST
- quick_20261102: purpose backtest, seasons [2023], feature version 0.2.0, feature snapshots match LATEST

### candidate feature inputs (PASS)

- chain_v1-primary-c2024w19-20261102T222634Z: primary, 19 LightGBM stages, 414 distinct inputs, 0 avail__ inputs, feature version 0.2.0
