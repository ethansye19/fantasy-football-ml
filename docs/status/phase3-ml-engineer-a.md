# Phase 3 / ML Engineer / ML-3.1, ML-3.2, ML-3.4 core (wave one)
Date: 2026-11-02
Scope: the scoring engine on the D-048 stat-line vocabulary, the transparent baselines 1 and 2, and the Monte Carlo core (parameter contracts, engine, summaries, prediction snapshots). Stage models, parameter builders from features and ROS mode are wave two.

Task list (from the brief): scoring `statline`, `rules`, `actuals` with tests and a Sleeper week-8 comparison; `RollingAverageBaseline` (L4, L3, EW 0.35) and `PriorSeasonBaseline` with tests; `ffml/sim` params, engine, summarize, snapshot with tests including a 600-player timing test; this note.

Done:
- `ffml/scoring/statline.py`: `STAT_COLUMNS` (15 offense, 9 kicking, 9 DST), key columns, positions, storage types (Int16 in samples, Int32 in actuals).
- `ffml/scoring/rules.py`: frozen `ScoringRules` with `from_sleeper` (uses `normalize_scoring_settings`), `from_dict`, `from_preset("ppr"|"half"|"std")`, one vectorised Polars expression (`score`, `with_points`, `expression`), `supported_keys`, `unsupported_keys` (one `UnsupportedScoringKeyWarning` per distinct key set, then ignored), `scoring_hash` (16 hex digits over the supported keys plus `ENGINE_VERSION`). Every key family in the brief is supported, plus `bonus_rec_rb`, `bonus_rec_wr`, `bonus_pass_cmp_25`, `bonus_rush_att_20`. Semantics are in the module docstring.
- `ffml/scoring/actuals.py`: `actual_statlines(seasons, purpose)` from `fact_player_game` and `fact_dst_game` (regular season by default; rows with `played = false` kept with zero stats; holdout guard first).
- `ffml/models/baselines.py`: `RollingAverageBaseline(window=4 | 3, ew_alpha=0.35)` and `PriorSeasonBaseline()`. `predict(seasons, scoring, purpose)` returns the evaluator's prediction contract. The D-046 cutoff is applied by an as-of join on the first kickoff of the season-week. Quantiles come from the last 8 games (at least 4), else from all games at the position in earlier seasons. The rookie fallback is the position-and-draft-round average of players without a previous season (first data season excluded; cells under 20 games fall back to the position level, then to the position average). There is an optional `quantile_mode="shifted"`. The holdout guard covers every season read.
- `ffml/sim/params.py`: documented contracts `TeamGameParams`, `PlayerGameParams`, `DstGameParams` (validate, defaults, ranges, keys, `describe()`), plus `synthetic_slate()`.
- `ffml/sim/engine.py`: `simulate()` / `simulate_full()` following MODELING.md section 5 (module docstring lists every step and constant). Output: long frame with `entity_id, season, week, game_id, team, position, sim (Int16), active (Boolean)` and `STAT_COLUMNS` as Int16, plus a team-level draw frame.
- `ffml/sim/summarize.py`: `summarize()` (mean, q10, q50, q90, boom/bust by positional rank within each simulation and season-week, `active_prob`), `prob_beats()`, and `BOOM_BUST_THRESHOLDS`.
- `ffml/sim/snapshot.py`: `write_predictions_snapshot()` (staging directory then atomic rename, immutable runs, `_manifest.json` with model_version, seeds, n_sims, inputs and file hashes, then `LATEST` json), `read_latest_predictions()`, `read_predictions()`.
- Tests: `tests/scoring/` (rules 25, actuals 4, Sleeper week 8 one slow), `tests/models/test_baselines.py` (15), `tests/sim/` (engine 11, summarize 5, snapshot 4).
- Commits: 7195747 (scoring), b9e4306 (baselines), 299ae2f (simulation).

Evidence:
- `.venv/Scripts/python -m pytest -q`: `273 passed, 1 warning in 39.07s` (whole repo; the warning is the expected unsupported-key warning in the Sleeper test).
- `.venv/Scripts/python -m pytest tests/scoring tests/models tests/sim -q -s`: `65 passed, 1 warning in 4.10s`.
- `ruff check` on `ffml/scoring ffml/models ffml/sim tests/scoring tests/models tests/sim`: `All checks passed!`. Repo-wide `ruff check .` reported 2 import-order errors, both in the Feature Engineer's `tests/features/` files that were uncommitted at the time.
- Sleeper comparison, test league scoring (41 keys, 36 supported; unsupported `ff, def_st_ff, def_st_fum_rec, st_ff, st_fum_rec`), 2026 week 8, games kicked off before 2026-11-02 00:00 UTC, tolerance 0.01:
  - offense and kickers **82/83 = 98.8%**;
  - DST 3/7.
  - Mismatch classes:
    - (1) One WR (Jalen Coker): engine 11.60, Sleeper 9.60. nflverse records 2 catches, 36 yards, 1 TD and no lost fumble, so the sources disagree on one stat (20 yards or a lost fumble), not on the rules.
    - (2) DST forced fumbles: the league scores `ff` at 1 point, and the stat line cannot compute it. All four DST gaps (CIN -3, PIT -2, KC -1, LAR -1) equal those defenses' forced fumbles in nflverse play-by-play exactly, so points-allowed bands, sacks, takeaways and safeties agree.
    - (3) Id mapping: Sleeper `LAR` maps to `LA` through `dim_team.aliases`. The 14 roster players without a week-8 row were on bye or off the roster; all have 0.0 on Sleeper.
- PPR and standard presets vs nflverse `fantasy_points_ppr_ref` / `_std_ref` on non-kicker regular-season rows: 2022 8,686/8,686, 2023 8,626/8,627, 2024 8,571/8,571. The one 2023 row is a RB with a made 20-29 yard field goal; nflverse fantasy points ignore kicking.
- Baseline sanity, PPR, seasons 2022-2024 only (23,141 played rows), MAE of `pred_q50`, default empirical quantiles: QB 6.45, RB 4.29, WR 4.48, TE 2.90, K 3.92, DST 4.62. These are identical for all four variants by construction (see D-053). 80% interval coverage: QB 0.63, RB 0.68, WR 0.72, TE 0.76, K 0.66, DST 0.66.
- MAE of `pred_mean`:

  | variant | QB | RB | WR | TE | K | DST |
  | --- | --- | --- | --- | --- | --- | --- |
  | L4 | 6.25 | 4.40 | 4.57 | 3.06 | 4.04 | 4.83 |
  | L3 | 6.30 | 4.46 | 4.65 | 3.13 | 4.19 | 4.93 |
  | EW 0.35 | 6.11 | 4.28 | 4.48 | 3.01 | 4.01 | 4.72 |
  | prior season | 6.86 | 4.82 | 4.92 | 3.26 | 3.78 | 4.57 |

- Shifted quantiles, MAE of `pred_q50`:

  | variant | QB | RB | WR | TE | K | DST |
  | --- | --- | --- | --- | --- | --- | --- |
  | L4 | 6.43 | 4.42 | 4.61 | 3.13 | 4.13 | 4.87 |
  | EW 0.35 | 6.26 | 4.29 | 4.50 | 3.05 | 4.10 | 4.74 |
  | prior season | 6.95 | 4.73 | 4.83 | 3.23 | 3.88 | 4.62 |

  Rookie fallbacks are 1-2% of rows for rolling and 15-21% for prior season. Each baseline predicts 2022-2024 in 0.1-0.2 s.
- Simulation timing: `synthetic_slate(16)` = 608 players + 32 DST x 2,000 sims in 1.03 s and 1.99 s on two runs (test limit 30 s). Output 1,280,000 rows, about 127 MB in memory. Summaries of 500-sim slates take about 0.02 s.
- Historical game-level spread (dim_game 2016-2024 REG, 2,367 games): sd of (total - total_line) 13.17, sd of (result - spread_line) 12.77, home/away score correlation -0.02.

Not done / blocked:
- ML-3.4 ROS mode and ML-3.2 structural per-stage baselines and parameter builders (feature-driven `TeamGameParams` / `PlayerGameParams`): wave two, as briefed. The baseline 3 reader exists from commit 01500bf (not mine).
- Forward predictions for 2026 read 2025 outcomes as lags, so `RollingAverageBaseline().predict([2026], ...)` raises `HoldoutLockedError` unless the holdout is unlocked (tested). This is a policy question (D-058).
- DST definitions: `ff` and special-teams fumble keys cannot be scored until the data carries forced fumbles (D-055).

Decisions proposed (numbers provisional; the log ends at D-052):
- D-053 Baseline quantiles. As specified, baselines 1 and 2 share `pred_q10/q50/q90` (the last-8 empirical distribution), so MAE and pinball on `pred_q50` cannot separate them. Proposal: keep the empirical default (it is the stronger bar: for example RB MAE 4.29 vs 4.73 for shifted prior season), have the evaluator also report MAE of `pred_mean` for baselines, and add `PriorSeasonBaseline(quantile_mode="shifted")` as a labelled variant so the report shows a prior-season median.
- D-054 Scoring semantics:
  - `two_pt` is scored with the mean of the present `pass_2pt/rush_2pt/rec_2pt`.
  - Offensive `ret_td` uses `st_td`, else `fum_rec_td`; DST `st_td` uses `def_st_td`, else `st_td`.
  - Yardage bonuses are exclusive Sleeper bands (100-199, 200+; 300-399, 400+), and kicking keys are additive.
  - Presets match nflverse offense (`pass_int` -2, `fum_lost` -2) with Sleeper default kicking and DST.
  - `scoring_hash` ignores unsupported keys.
- D-055 Forced fumbles: Data Engineer adds `forced_fumbles` to `fact_dst_game` (from pbp `forced_fumble_player_1_team`), and D-048 gains a DST `forced_fumbles` column scored by `ff`. This would have made every week-8 DST match.
- D-056 Game-level dispersion defaults: the brief's `total_sd` 10.0 with `margin_sd` 13.5 makes opponents' drawn points correlate negatively (-0.29 by construction). History says 13.2 and 12.8 with near-zero score correlation. Proposal: defaults `total_sd` = `margin_sd` = 13.0 until ML-3.5 tunes them on 2022-2024.
- D-057 Engine conventions:
  - QB completions and yards are drawn from QB parameters, so QB yards equal receivers' yards only in expectation. They are linked by shared attempts, a shared passing-efficiency factor and touchdowns (QB passing TDs equal the sum of receivers' receiving TDs).
  - DST sacks, interceptions and fumble recoveries are the opposing offense's simulated events. DST rates are blended in by a geometric mean.
  - Points allowed is the opponent's realised score.
  - Boom uses `min` rank and bust uses `max` rank on ties, so zero-point blocks bust.
  - One kicker per team per sim.
- D-058 Forward-test lags: allow reading 2025 outcomes as lag inputs for 2026 forward predictions after the evaluator's single holdout run, or route 2026 forward predictions through the unlock log. The orchestrator decides.
- DATA_SCHEMA.md section 4 should list `entity_id` and the D-048 columns (fine field-goal buckets, `fg_missed`, DST columns) instead of `player_id` and `fg_made_0_39 / 40_49 / 50p` (orchestrator edit).

Next:
- Wave two: parameter builders from `ffml/features` (structural baselines first), stage models with the walk-forward harness, ROS mode, and dispersion tuning on 2022-2024.
