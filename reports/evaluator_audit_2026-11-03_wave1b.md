# Evaluator audit

Overall: **PASS**

- Holdout audit (season 2025): **PASS**
- Leakage audit: **PASS**

## Holdout audit (season 2025)

Overall: **PASS** (PASS 4, WARN 1). Run at 2026-11-02T22:02:22+00:00 on the working tree at git `04e70f7`.

| check | status | detail |
| --- | --- | --- |
| holdout access log | PASS | artifacts/holdout_access.log absent: no holdout access recorded |
| literal holdout season | WARN | 2025 appears only in docstrings or comments (4; 45 files under ffml/features, ffml/models, ffml/sim, ffml/scoring, ffml/eval) |
| unlock assignments | PASS | none outside tests and the guard (67 files scanned) |
| .env unlock | PASS | .env absent |
| process environment | PASS | FFML_HOLDOUT_UNLOCK not set |

### literal holdout season (WARN)

- ffml/features/access.py:4 (docstring)
- ffml/features/context.py:171 (docstring)
- ffml/features/context.py:250 (docstring)
- ffml/models/baselines.py:497 (docstring)

## Leakage audit

Overall: **PASS** (PASS 3). Run at 2026-11-02T22:02:23+00:00 on the working tree at git `04e70f7`.

| check | status | detail |
| --- | --- | --- |
| leakage suite | PASS | pytest tests/features/test_leakage.py -q: 14 passed in 9.50s |
| timestamp audit | PASS | 28897 rows checked against week first kickoff from dim_game; no violations |
| shuffle probe | PASS | no feature copies a target; 772 feature columns x 66 targets (60 position-specific, compared within position); sampled rows: ALL 2000, K 1761, QB 2000, RB 2000, TE 2000, WR 2000; 74377 pairs computed on rows where both are non-null, 15908 skipped for overlap below 200, 10075 undefined (constant); max \|corr\| 0.851, shuffled-target reference 0.222 |

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
