# Feature List (point-in-time)

## Ground rules

1. **Cutoff.** A training or prediction row for player p in week w of season s
   has `cutoff_ts` = scheduled kickoff of p's game. Game-derived statistics
   from week w are never visible. Off-field information (depth charts, roster
   status, schedule lines) is visible only if its snapshot timestamp is
   earlier than the cutoff. For historical seasons where no timestamp exists,
   the week w-1 version is used.
2. **Lag everything that comes from games.** All usage, efficiency, and
   team-environment features are computed from games strictly before week w.
   Rolling windows are in games played, not calendar weeks, and are shifted by
   one game. Cross-season windows continue with decay (prior-season values
   carry a weight of 0.5 at week 1 that decays to 0 by week 8).
3. **Injury information policy.** Same-week injury designations and roster
   status are not model features in the primary configuration (the source has
   no timestamp). They drive the **availability layer** in the simulation
   (rules, see MODELING.md). A "final-report" variant that includes them is
   backtested and reported separately.
4. **Shrinkage.** Rate features are empirical-Bayes shrunk toward the
   position prior with a pseudo-count that is tuned on the validation seasons.
   The raw sample size is always carried as its own feature.
5. **Team changes.** Share windows reset when the player changes team; prior
   values are kept in `prev_team_*` features so the model can learn how much
   to trust them.

Windows used: last game (L1), last 3 games (L3), last 5 games (L5),
exponentially weighted mean with alpha 0.35 (EW), season to date (STD), prior
season per game (PS), career per game (CAR).

## A. Game context (known before kickoff)

| feature | definition | source |
| --- | --- | --- |
| season, week, week_of_season_frac | | dim_game |
| home | 1 if home team | dim_game |
| spread, total_line | from the schedule snapshot | dim_game |
| implied_team_total, implied_opp_total | total/2 minus or plus spread/2 | derived |
| favorite_flag | spread sign | derived |
| roof_dome, roof_outdoors, surface_turf | one-hot | dim_game |
| rest_days, opp_rest_days, short_week, off_bye | | dim_game |
| div_game, primetime | kickoff later than 19:00 local | dim_game |
| temp, wind | **sensitivity only**: actual game values in history | dim_game |

## B. Team offense environment (lagged, windows L3, L5, EW, STD, PS)

| feature | definition |
| --- | --- |
| plays_pg | offensive plays per game (pass attempts + sacks + rushes, excluding kneels and spikes) |
| sec_per_play, neutral_sec_per_play | pace overall and in neutral game script (win probability 20-80%, quarters 1-3) |
| pass_rate, neutral_pass_rate | pass attempts plus sacks over plays |
| proe | mean `pass_oe` from pbp (pass rate over expectation) |
| early_down_pass_rate | 1st and 2nd down, neutral script |
| epa_per_play, pass_epa_per_play, rush_epa_per_play, success_rate | |
| points_pg, pass_td_pg, rush_td_pg, rz_trips_pg, rz_td_rate, gl_plays_pg | scoring and red-zone volume |
| sack_rate_allowed, int_rate | |
| qb_change_flag | starting QB identity differs from the last game (depth chart QB1 at cutoff vs. last game passer with most attempts) |
| team_targets_pg, team_carries_pg | denominators for share features |

## C. Opponent defense (lagged, same windows, plus season-prior shrinkage)

| feature | definition |
| --- | --- |
| def_plays_faced_pg, def_sec_per_play_faced | pace the defense induces |
| def_pass_rate_faced, def_proe_faced | |
| def_epa_per_play_allowed, def_pass_epa_allowed, def_rush_epa_allowed, def_success_rate_allowed | |
| def_ypt_allowed_rb, def_ypt_allowed_wr, def_ypt_allowed_te | yards per target allowed by position |
| def_tgt_share_allowed_rb, def_tgt_share_allowed_wr, def_tgt_share_allowed_te | how opponents distribute targets against this defense |
| def_ypc_allowed, def_rush_td_rate_allowed, def_rec_td_rate_allowed | |
| def_sack_rate, def_int_rate, def_pressure_rate (NGS, if available) | |
| def_points_allowed_pg, def_rz_td_rate_allowed | |

## D. Player opportunity (lagged; windows L1, L3, L5, EW, STD, PS, CAR)

| feature | definition | positions |
| --- | --- | --- |
| target_share | targets / team targets | RB, WR, TE |
| air_yards_share, wopr, adot | | RB, WR, TE |
| rz_target_share, ez_target_share | red-zone and end-zone target shares | RB, WR, TE |
| rush_share | carries / team carries | RB, QB, WR |
| gl_carry_share, rz_carry_share | goal-line (inside the 5) and red-zone carry shares | RB, QB |
| snap_share | offense_pct from snap counts | all |
| route_proxy | snap share on pass plays (from pbp participation is unavailable in season, so snap share on team dropbacks is approximated by snap_share x team pass rate) | RB, WR, TE |
| targets_pg, carries_pg, touches_pg, dropbacks_pg | raw volume | all |
| designed_rush_rate, scramble_rate | | QB |
| team_pass_td_share, team_rush_td_share | share of team TDs | all |
| games_played_std, games_played_career, weeks_since_last_game | sample sizes and layoffs | all |
| new_team_flag, weeks_on_team, prev_team_target_share_ew | team-change handling | all |
| depth_rank, depth_rank_change, players_ahead | from the latest depth chart snapshot before cutoff | all |
| starter_flag | depth_rank == 1 at cutoff | all |
| teammates_same_pos_count_active | active same-position teammates by depth chart | all |

## E. Player efficiency (lagged, shrunk)

| feature | definition | positions |
| --- | --- | --- |
| catch_rate, yards_per_target, yards_per_reception, yac_per_rec | | RB, WR, TE |
| yards_per_carry, rush_first_down_rate | | RB, QB, WR |
| yards_per_attempt, completion_rate, cpoe, int_rate, sack_rate | | QB |
| td_per_target, td_per_carry, td_per_attempt | heavily shrunk | all |
| epa_per_target, epa_per_carry, epa_per_dropback | | all |
| exp_rec_yds_per_target, exp_rec_td_per_target, exp_rush_yds_per_carry, exp_rush_td_per_carry | expected values from ffopportunity (regression-to-expected signal) | all |
| actual_minus_expected_rec_yds, actual_minus_expected_td | luck signals, lagged | all |
| ngs_avg_separation, ngs_avg_cushion, ngs_yac_above_expectation, ngs_intended_air_yards | | WR, TE, RB |
| ngs_ryoe_per_att, ngs_pct_8_plus_defenders | | RB |
| ngs_time_to_throw, ngs_aggressiveness, ngs_cpoe | | QB |
| ftn_* (flag-gated, off by default) | play-action rate, motion rate, screen rate, drop rate, contested target rate | all |

## F. Player attributes

`position, age_at_week, years_exp, rookie_flag, draft_round, draft_pick,
height, weight, undrafted_flag`.

## G. Availability layer inputs (rules, not model features in the primary configuration)

| input | source | rule |
| --- | --- | --- |
| report_status | latest injury report snapshot | Out or IR: availability 0; Doubtful: 0.15; Questionable: position-specific base rate estimated from history (about 0.75); none: 1.0 |
| practice_status | latest injury report | DNP all week with Questionable lowers availability to 0.5 |
| roster status | latest weekly roster | RES, PUP, NFI, SUS, CUT: 0 |
| depth chart presence | latest snapshot | absent from the depth chart with a starter history: flag for review, availability 0.5 |
| Sleeper injury_status | daily players map | used as a tie-breaker when nflverse is stale |
| teammates out | the above applied to teammates | shares re-normalized among active players with position-specific absorption factors estimated from history |

## H. Kicker and DST inputs

Kickers: team implied total, team rz_trips_pg and rz_td_rate (more stalled
drives mean more field goals), kicker fg_att_pg, fg_pct by distance bucket
(shrunk), roof, wind (sensitivity). DST: opponent implied total, opponent
sack_rate_allowed, opponent int_rate and fumble rate, own def_sack_rate,
def_int_rate, def_points_allowed_pg, return TD base rate.

## I. Targets by stage (what each model predicts)

| stage | entity | target | conditioning |
| --- | --- | --- | --- |
| 1a | team-game | offensive plays | none |
| 1b | team-game | pass rate | none |
| 1c | team-game | offensive touchdowns and field-goal attempts | implied total |
| 2 | player-game | target share, rush share, dropback share (QB), goal-line share, red-zone target share | player active |
| 3 | player-game | catch rate, yards per target, yards per carry, yards per attempt, completion rate, interceptions per attempt | player active |
| 4 | player-game | share of team passing TDs, share of team rushing TDs | player active |
| 5 (direct challenger, not shipped) | player-game | PPR points | player active |

## J. Leakage test suite (tests/features/test_leakage.py)

1. **Truncation equality.** Build features from data truncated at week w; rows
   with cutoff before week w must be byte-identical to the full build.
2. **Future-week invariance.** Adding week w+1 data must not change any row for
   weeks up to w.
3. **Outcome scramble.** Permuting week w stats must not change week w
   features.
4. **Shift check.** For every rolling feature, the correlation with the
   same-week raw stat must not exceed the correlation with the lagged stat by
   more than noise.
5. **Timestamp audit.** Every depth-chart and injury snapshot used has
   `snapshot_ts < cutoff_ts`.
6. **Schema fingerprint.** Feature column names, order, and dtypes match the
   pinned `features.json` for the model version.

## As built (feature version 0.1.0, FE-3.1 and FE-3.2, 2026-11-02)

Code lives in `ffml/features/`: `spec.py` (version, PARAMS, naming, stat
specs), `windows.py` (vectorised window helpers), `data.py` (inputs and week
calendar), `targets.py`, `context.py`, `team.py`, `player.py`, `dst.py`,
`manifest.py`, `build.py` and `access.py`. `ffml features --seasons 2016-2026`
writes three datasets to `data/features/`, partitioned by season. Each
manifest's `extra` block holds `feature_version`, the feature columns with
dtypes and kinds, `params` and the processed snapshot ids used as `inputs`.

| dataset | row | rows 2016-2026 | columns | model features |
| --- | --- | ---: | ---: | ---: |
| player_week | every fact_player_game row (REG and POST, roster-only rows included) | 98,530 | 744 | 654 |
| team_week | every fact_team_game row | 5,760 | 304 | 268 |
| dst_week | every fact_dst_game row | 5,760 | 360 | 331 |

Model code reads features only through `ffml.features.access`:
`load_features(dataset, seasons, purpose)` calls
`ffml.holdout.assert_seasons_allowed` before reading, and
`training_frame(dataset, seasons, purpose, regular_season_only=True,
played_only=None, config="primary")` returns keys, the model inputs of the
chosen configuration and the targets (`played_only` defaults to true for
player_week). `feature_names(dataset, config)` lists the inputs:
`primary` = kind `feature`; `final_report` adds the `avail__` columns;
`sensitivity` adds actual weather. For live rows the predictor calls
`future_targets(season, week)` and passes the frames to
`build_player_week`, `build_team_week` and `build_dst_week`.

### Timing rules (D-046, D-047, D-006)

- `cutoff_ts` is the first kickoff of the row's season-week across the league
  (dim_game, all game types; postseason weeks have their own cutoffs).
- Every game-derived value comes from a per-entity state computed after each
  game, attached with a strict as-of join (state kickoff `< cutoff_ts`). So
  the row's own game and every other game of the same week never contribute,
  and Thursday and Sunday rows of one week share one information set.
- `asof_ts = cutoff_ts - 1 hour` for historical rows. `future_targets()`
  sets it to the call time. Depth charts use the row team's latest snapshot
  with `snapshot_ts <= asof_ts`. Legacy 2016-2024 charts carry the D-037
  synthetic time (first kickoff of the week minus 48 hours), so week w's
  legacy chart is the one used for week w rows. Ground rule 1 above ("use the
  week w-1 version where no timestamp exists") is therefore not what runs for
  those seasons; the status note proposes a decision.
- Lines are the schedule snapshot's closing lines (D-008). `ctx__temp` and
  `ctx__wind` are actual game values and have kind `sensitivity`.
- Same-week injury designation, practice status, roster status, depth-chart
  presence and teammates-out sums have kind `avail` and are not primary
  features. Lagged injury features are regular features:
  `attr__inj_prev_status` (the report of the team's previous game week in the
  same season), `attr__inj_weeks_since_out` (capped at 52) and
  `attr__games_missed__l5`.
- The timestamp audit checks the time each injury report was published:
  `report_modified_at` for 2016-2024 and the last kickoff of the report's week
  for 2025 onward. `fact_injury_report.snapshot_ts` is the ingest time of the
  whole raw snapshot, so it says nothing about when a report came out.

### Windows and transformations

- Player windows count games with `played = true`; team and DST windows count
  team games. Ratio statistics are ratios of sums over the window
  (`sum(num) / sum(den)`). A per-game mean uses one unit of denominator per
  game in which the value is not null (this is how NGS means skip missing
  games).
- `l1, l3, l5`: the last 1, 3 or 5 games. `ew`: exponential weights with
  `adjust=True` and alpha 0.35 over all earlier games, continuous across
  seasons. `ps`: totals of season s - 1. `car`: all games since 2016, the
  start of the data, so veterans' careers are cut short.
- `std` is season to date blended with `ps`:
  `w = max(0, 0.5 - 0.0625 (week - 1))` and `std = w * ps + (1 - w) * raw`.
  When raw is null the value is `ps`, and when `ps` is null it is raw.
  Postseason weeks have `w = 0`. The raw season-to-date value is not
  published separately; `_n` columns carry the sample sizes.
- Share statistics (the share rows of the opp table below) run their L1 to
  STD windows within a team stint. When the row's team differs from the team
  of the player's last game, those windows are null and the shrunk variants
  equal the prior. PS and CAR stay at the player level (any team).
  `opp__prev_team_<share>__ew` carries the previous team's EW share; also
  emitted are `attr__new_team_flag` and `attr__weeks_on_team` (consecutive
  team games on the roster, any row source).
- Shrinkage is `(sum + k * prior) / (n + k)` over the window's sums (EW uses
  the un-normalised weighted sums). The prior is the position total over all
  seasons strictly before the row's season (the league total for def and DST),
  so 2016 rows have no prior and null shrunk values.
- Float sums are ordered cumulative sums, which makes the leakage comparisons
  exact rather than tolerance-based.
- `team__` describes the offense on the field and `def__` the defense on the
  field. In dst_week, `team__` is the opponent's offense and `def__` is the
  row team's own unit.
- `depth__rank` = 1 + same-group teammates listed at a better rank. This puts
  legacy charts (several WRs at rank 1) and 2025+ charts (WRs numbered 1..n)
  on one scale. `depth__starter_flag` = rank within `n_starters` (WR 3,
  otherwise 1). `depth__rank_change` compares with the chart at the previous
  week's historical as-of time.
- `team__qb_change_flag` = depth-chart QB1 at `asof_ts` differs from the
  team's top passer in its last game before the cutoff.
- Targets are Float64. They are null for future rows and for rows whose game
  is not final in dim_game.

### Leakage suite as built (tests/features/test_leakage.py)

The suite runs offline on `tests/fixtures/features/`: KC and DET for 2022 and
2023 weeks 1-6, with every team for the team-game and DST tables (278 KB of
Parquet, regenerated by `make_fixture.py`).

1. Truncation equality: a build from data truncated at 2023 weeks 2, 4 and 6
   equals the full build for every row with an earlier cutoff, compared with
   exact frame equality and row hashes.
2. Future-week invariance: adding week w + 1 data (w = 1, 3, 5) leaves every
   row up to week w unchanged.
3. Outcome scramble: permuting all stat columns of 2023 week 1 (the Thursday
   opener) and week 4 in the player, team and DST tables leaves every
   non-target column of those weeks unchanged, while the targets do change.
4. Shift check: for every rolling player (opp, eff) and team (team, def)
   feature with at least 30 rows, the correlation with the same-game raw
   statistic exceeds the correlation with the previous game's statistic by at
   most 0.10. 264 player, 100 team and 101 def columns are checked; the
   largest observed excess is 0.043. A positive control (a same-game share)
   is flagged.
5. Timestamp audit: `cutoff_ts <= kickoff_utc` and `asof_ts = cutoff_ts - 1h`.
   Every `meta__` time (last game used per entity, depth snapshots, injury
   report time) is before `cutoff_ts`, depth snapshots are at or before
   `asof_ts`, and each depth snapshot used is the team's latest at or before
   `asof_ts`.
6. Schema fingerprint: `fingerprint_json()` equals the pinned
   `tests/fixtures/features/feature_manifest_0.1.0.json`, and every built
   frame matches the manifest's names, order and dtypes.

### Known gaps

- Kicker accuracy by distance cannot be computed: fact_player_game has field
  goals made per distance band but attempts and misses only in total. The
  build has `eff__fg_pct` overall plus `eff__fg_made_share_40_49` and
  `eff__fg_made_share_50p`.
- Not built: NGS `pct_8_plus_defenders` and NGS CPOE (the processed table has
  neither; pbp `cpoe` is used), defensive pressure rate (no source column),
  FTN features (off by D-009).
- `ctx__primetime` uses the kickoff hour in Eastern time (at or after 19:00)
  because stadium time zones are not in dim_game.
- `attr__weeks_since_last_game` counts calendar weeks, including postseason
  weeks in which the player's team did not play.
- Shrinkage pseudo-counts are documented defaults to be tuned on the
  validation seasons.
- 47 roster-only fact_player_game rows (2016: 42, 2022: 1, 2024: 4) have no
  dim_player row, so their `attr__` columns are null.

### Feature names by group (generated from `feature_manifest()`)

Brace lists expand to one column per entry: `team__plays__{l3,l5}` means
`team__plays__l3` and `team__plays__l5`. "sparse" columns are null by
construction for many rows (NGS coverage, previous-team carry-over, first
games) and are exempt from the 60 percent null check in the integration test.

#### player_week: 744 columns (13 key, 6 meta, 654 feature, 2 sensitivity, 9 avail, 60 target)

Keys: `player_id`, `season`, `week`, `game_id`, `team`, `opponent`, `position`, `game_type`, `home`, `kickoff_utc`, `cutoff_ts`, `asof_ts`, `feature_version`.
Meta (audit only): `meta__last_game_kickoff`, `meta__team_last_game_kickoff`, `meta__def_last_game_kickoff`, `meta__qb_depth_snapshot_ts`, `meta__depth_snapshot_ts`, `meta__inj_info_ts`.
Sensitivity only: `ctx__temp`, `ctx__wind`.

ctx (game context, A), 18 columns:

| columns | positions | notes |
| --- | --- | --- |
| `ctx__week` | all |  |
| `ctx__week_of_season_frac` | all |  |
| `ctx__postseason` | all |  |
| `ctx__home` | all |  |
| `ctx__spread` | all |  |
| `ctx__total_line` | all |  |
| `ctx__implied_team_total` | all |  |
| `ctx__implied_opp_total` | all |  |
| `ctx__favorite_flag` | all |  |
| `ctx__roof_dome` | all |  |
| `ctx__roof_outdoors` | all |  |
| `ctx__surface_turf` | all |  |
| `ctx__rest_days` | all |  |
| `ctx__opp_rest_days` | all |  |
| `ctx__short_week` | all |  |
| `ctx__off_bye` | all |  |
| `ctx__div_game` | all |  |
| `ctx__primetime` | all |  |

team (offense on the field, B), 126 columns:

| columns | positions | notes |
| --- | --- | --- |
| `team__plays__{l3,l5,ew,std,ps}` | all | source plays |
| `team__sec_per_play__{l3,l5,ew,std,ps}` | all | source sec_per_play |
| `team__neutral_sec_per_play__{l3,l5,ew,std,ps}` | all | source neutral_sec_per_play |
| `team__pass_rate__{l3,l5,ew,std,ps}` | all | source pass_plays / plays |
| `team__neutral_pass_rate__{l3,l5,ew,std,ps}` | all | source neutral_pass_plays / neutral_plays |
| `team__proe__{l3,l5,ew,std,ps}` | all | source proe |
| `team__early_down_pass_rate__{l3,l5,ew,std,ps}` | all | source early_down_pass_rate |
| `team__epa_per_play__{l3,l5,ew,std,ps}` | all | source epa_total / plays |
| `team__pass_epa_per_play__{l3,l5,ew,std,ps}` | all | source pass_epa_per_play |
| `team__rush_epa_per_play__{l3,l5,ew,std,ps}` | all | source rush_epa_per_play |
| `team__success_rate__{l3,l5,ew,std,ps}` | all | source successes / plays |
| `team__points__{l3,l5,ew,std,ps}` | all | source points |
| `team__pass_td__{l3,l5,ew,std,ps}` | all | source pass_td |
| `team__rush_td__{l3,l5,ew,std,ps}` | all | source rush_td |
| `team__rz_trips__{l3,l5,ew,std,ps}` | all | source rz_trips |
| `team__rz_td_rate__{l3,l5,ew,std,ps}` | all | source rz_tds / rz_trips |
| `team__gl_plays__{l3,l5,ew,std,ps}` | all | source gl_plays |
| `team__sack_rate_allowed__{l3,l5,ew,std,ps}` | all | source sacks / dropbacks |
| `team__int_rate__{l3,l5,ew,std,ps}` | all | source interceptions / pass_att |
| `team__fumble_rate__{l3,l5,ew,std,ps}` | all | source fumbles_lost / plays |
| `team__team_targets__{l3,l5,ew,std,ps}` | all | source team_targets |
| `team__team_carries__{l3,l5,ew,std,ps}` | all | source rush_att |
| `team__pass_att__{l3,l5,ew,std,ps}` | all | source pass_att |
| `team__dropbacks__{l3,l5,ew,std,ps}` | all | source dropbacks |
| `team__field_goal_att__{l3,l5,ew,std,ps}` | all | source field_goal_att |
| `team__qb_change_flag` | all | depth-chart QB1 at asof differs from the passer with most attempts in the last game |

def (defense on the field, C and DST), 124 columns:

| columns | positions | notes |
| --- | --- | --- |
| `def__plays_faced__{l3,l5,ew,std,ps}` | all | source allowed_plays |
| `def__sec_per_play_faced__{l3,l5,ew,std,ps}` | all | source allowed_sec_per_play |
| `def__pass_rate_faced__{l3,l5,ew,std,ps}` | all | source allowed_pass_plays / allowed_plays |
| `def__proe_faced__{l3,l5,ew,std,ps}` | all | source allowed_proe |
| `def__epa_per_play_allowed__{l3,l5,ew,std,ps}` | all | source allowed_epa_total / allowed_plays |
| `def__pass_epa_allowed__{l3,l5,ew,std,ps}` | all | source allowed_pass_epa_per_play |
| `def__rush_epa_allowed__{l3,l5,ew,std,ps}` | all | source allowed_rush_epa_per_play |
| `def__success_rate_allowed__{l3,l5,ew,std,ps}` | all | source allowed_successes / allowed_plays |
| `def__ypt_allowed_rb__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_rb_rec_yds / allowed_rb_targets |
| `def__ypt_allowed_wr__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_wr_rec_yds / allowed_wr_targets |
| `def__ypt_allowed_te__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_te_rec_yds / allowed_te_targets |
| `def__tgt_share_allowed_rb__{l3,l5,ew,std,ps}` | all | source allowed_rb_targets / allowed_skill_targets |
| `def__tgt_share_allowed_wr__{l3,l5,ew,std,ps}` | all | source allowed_wr_targets / allowed_skill_targets |
| `def__tgt_share_allowed_te__{l3,l5,ew,std,ps}` | all | source allowed_te_targets / allowed_skill_targets |
| `def__ypc_allowed__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_rb_rush_yds / allowed_rb_rush_att |
| `def__rush_td_rate_allowed__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_rush_td / allowed_rush_att |
| `def__rec_td_rate_allowed__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_pass_td / allowed_pass_att |
| `def__sack_rate__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_sacks / allowed_dropbacks |
| `def__int_rate__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_interceptions / allowed_pass_att |
| `def__points_allowed__{l3,l5,ew,std,ps}` | all | source allowed_points |
| `def__rz_td_rate_allowed__{l3,l5,ew,std,ps,std_shrunk}` | all | source allowed_rz_tds / allowed_rz_trips |
| `def__qb_pass_yds_allowed__{l3,l5,ew,std,ps}` | all | source allowed_qb_pass_yds |
| `def__rb_rush_yds_allowed__{l3,l5,ew,std,ps}` | all | source allowed_rb_rush_yds |

opp (player opportunity, D and K volume), 138 columns:

| columns | positions | notes |
| --- | --- | --- |
| `opp__target_share__{l1,l3,l5,ew,std,ps,car}` | RB,WR,TE | source targets / team_targets |
| `opp__air_yards_share__{l3,l5,ew,std,ps}` | RB,WR,TE | source rec_air_yards / team_air_yards |
| `opp__rz_target_share__{l1,l3,l5,ew,std,ps,car,ew_shrunk}` | RB,WR,TE | source rz_targets / team_rz_targets |
| `opp__ez_target_share__{l3,l5,ew,std,ps}` | RB,WR,TE | source ez_targets / team_ez_targets |
| `opp__rush_share__{l1,l3,l5,ew,std,ps,car}` | RB,QB,WR | source carries / team_carries |
| `opp__gl_carry_share__{l1,l3,l5,ew,std,ps,car,ew_shrunk}` | RB,QB | source gl_carries / team_gl_carries |
| `opp__rz_carry_share__{l3,l5,ew,std,ps}` | RB,QB | source rz_carries / team_rz_carries |
| `opp__snap_share__{l1,l3,l5,ew,std,ps,car}` | QB,RB,WR,TE | source offense_pct |
| `opp__route_proxy__{l3,l5,ew,std,ps}` | RB,WR,TE | source route_num |
| `opp__dropback_share__{l1,l3,l5,ew,std,ps,car}` | QB | source dropbacks / team_dropbacks |
| `opp__pass_td_share__{l3,l5,ew,std,ps,ew_shrunk}` | RB,WR,TE | source rec_td / team_pass_td |
| `opp__rush_td_share__{l3,l5,ew,std,ps,ew_shrunk}` | RB,QB,WR | source rush_td / team_rush_td |
| `opp__targets__{l1,l3,l5,ew,std,ps,car}` | RB,WR,TE | source targets |
| `opp__carries__{l1,l3,l5,ew,std,ps,car}` | RB,QB,WR | source carries |
| `opp__touches__{l1,l3,l5,ew,std,ps,car}` | RB,WR,TE | source touches |
| `opp__dropbacks__{l1,l3,l5,ew,std,ps,car}` | QB | source dropbacks |
| `opp__pass_att__{l1,l3,l5,ew,std,ps,car}` | QB | source pass_att |
| `opp__fg_att__{l3,l5,ew,std,ps,car}` | K | source fg_att |
| `opp__pat_att__{l5,ew,std,ps}` | K | source pat_att |
| `opp__adot__{l5,ew,std,ps,car}` | RB,WR,TE | source rec_air_yards / targets |
| `opp__designed_rush_rate__{ew,std,ps,car}` | QB | source designed_rush / designed_rush_den |
| `opp__scramble_rate__{ew,std,ps,car}` | QB | source scrambles / dropbacks |
| `opp__wopr__ew` | RB,WR,TE | 1.5 * target_share__ew + 0.7 * air_yards_share__ew |
| `opp__prev_team_target_share__ew` | all | sparse; EW share with the previous team (null without a team change in the data) |
| `opp__prev_team_rush_share__ew` | all | sparse; EW share with the previous team (null without a team change in the data) |
| `opp__prev_team_snap_share__ew` | all | sparse; EW share with the previous team (null without a team change in the data) |

eff (player efficiency, E and K accuracy), 226 columns:

| columns | positions | notes |
| --- | --- | --- |
| `eff__catch_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}` | RB,WR,TE | source receptions / targets |
| `eff__yards_per_target__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,WR,TE | source rec_yds / targets |
| `eff__yards_per_reception__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}` | RB,WR,TE | source rec_yds / receptions |
| `eff__yac_per_rec__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,WR,TE | source rec_yac / receptions |
| `eff__yards_per_carry__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}` | RB,QB,WR | source rush_yds / carries |
| `eff__rush_first_down_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,QB,WR | source rush_first_downs / carries |
| `eff__yards_per_attempt__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}` | QB | source pass_yds / pass_att |
| `eff__completion_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | QB | source completions / pass_att |
| `eff__int_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | QB | source interceptions / pass_att |
| `eff__sack_rate__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}` | QB | source sacks / dropbacks |
| `eff__cpoe__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | QB | source cpoe_num / cpoe_att |
| `eff__td_per_target__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,WR,TE | source rec_td / targets |
| `eff__td_per_carry__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,QB,WR | source rush_td / carries |
| `eff__td_per_attempt__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | QB | source pass_td / pass_att |
| `eff__epa_per_target__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,WR,TE | source rec_epa / targets |
| `eff__epa_per_carry__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,QB,WR | source rush_epa / carries |
| `eff__epa_per_dropback__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | QB | source pass_epa / dropbacks |
| `eff__exp_rec_yds_per_target__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,WR,TE | source rec_yards_exp / targets |
| `eff__exp_rec_td_per_target__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,WR,TE | source rec_td_exp / targets |
| `eff__exp_rush_yds_per_carry__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,QB,WR | source rush_yards_exp / carries |
| `eff__exp_rush_td_per_carry__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk}` | RB,QB,WR | source rush_td_exp / carries |
| `eff__fg_pct__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}` | K | source fg_made / fg_att |
| `eff__pat_pct__{l5,ew,std,ps,car,ew_shrunk,std_shrunk,car_shrunk,std_n,car_n}` | K | source pat_made / pat_att |
| `eff__actual_minus_expected_rec_yds__{ew,std,car}` | RB,WR,TE | source ame_rec_yds |
| `eff__actual_minus_expected_td__{ew,std,car}` | QB,RB,WR,TE | source ame_td |
| `eff__fg_made_share_40_49__{ew,std,car}` | K | source fg_made_40_49 / fg_made |
| `eff__fg_made_share_50p__{ew,std,car}` | K | source fg_made_50p / fg_made |
| `eff__ngs_avg_separation__{ew,std}` | WR,TE,RB | source avg_separation; sparse |
| `eff__ngs_avg_cushion__{ew,std}` | WR,TE | source avg_cushion; sparse |
| `eff__ngs_yac_above_expectation__{ew,std}` | WR,TE,RB | source yac_above_expectation; sparse |
| `eff__ngs_intended_air_yards__{ew,std}` | WR,TE,QB | source avg_intended_air_yards; sparse |
| `eff__ngs_ryoe_per_att__{ew,std}` | RB | source rush_yards_over_expected / carries; sparse |
| `eff__ngs_rush_efficiency__{ew,std}` | RB | source efficiency; sparse |
| `eff__ngs_time_to_throw__{ew,std}` | QB | source time_to_throw; sparse |
| `eff__ngs_aggressiveness__{ew,std}` | QB | source aggressiveness; sparse |

attr (attributes, sample sizes, team change, lagged injury, F), 17 columns:

| columns | positions | notes |
| --- | --- | --- |
| `attr__games__{std_n,ps_n,car_n}` | all | source _one |
| `attr__age` | all |  |
| `attr__years_exp` | all |  |
| `attr__rookie_flag` | all |  |
| `attr__draft_round` | all |  |
| `attr__draft_pick` | all |  |
| `attr__undrafted_flag` | all |  |
| `attr__height` | all |  |
| `attr__weight` | all |  |
| `attr__weeks_since_last_game` | all |  |
| `attr__new_team_flag` | all | sparse; null before the player's first game in the data |
| `attr__weeks_on_team` | all |  |
| `attr__inj_prev_status` | all | report of the team's previous game week: 0 none, 1 Q, 2 D, 3 Out |
| `attr__inj_weeks_since_out` | all | capped at 52 |
| `attr__games_missed__l5` | all | team games this season before cutoff, of the last 5 |

depth (depth chart at asof_ts), 5 columns:

| columns | positions | notes |
| --- | --- | --- |
| `depth__rank` | all |  |
| `depth__players_ahead` | all |  |
| `depth__starter_flag` | all |  |
| `depth__rank_change` | all | sparse; null in week 1 and when unranked in either week |
| `depth__same_pos_count` | all |  |

avail (same-week; not primary features), 9 columns:

| columns | positions | notes |
| --- | --- | --- |
| `avail__report_status` | all | sparse; same-week; not a primary feature |
| `avail__practice_status` | all | sparse; same-week; not a primary feature |
| `avail__roster_status` | all | sparse; same-week; not a primary feature |
| `avail__report_status_ord` | all | same-week; not a primary feature |
| `avail__on_injury_report` | all | same-week; not a primary feature |
| `avail__depth_chart_present` | all | same-week; not a primary feature |
| `avail__teammates_out_same_pos` | all | same-week injury report (Out or Doubtful teammates) |
| `avail__teammates_out_target_share_ew` | all | same-week injury report (Out or Doubtful teammates) |
| `avail__teammates_out_rush_share_ew` | all | same-week injury report (Out or Doubtful teammates) |

Targets (60): `y_played`, `y_offense_snaps`, `y_offense_pct`, `y_fantasy_points_ppr_ref`, `y_fantasy_points_std_ref`, `y_pass_att`, `y_completions`, `y_pass_yds`, `y_pass_td`, `y_interceptions`, `y_sacks`, `y_sack_yards`, `y_pass_air_yards`, `y_dropbacks`, `y_carries`, `y_rush_yds`, `y_rush_td`, `y_rush_first_downs`, `y_designed_rush`, `y_scrambles`, `y_targets`, `y_receptions`, `y_rec_yds`, `y_rec_td`, `y_rec_air_yards`, `y_rec_yac`, `y_rz_targets`, `y_rz_carries`, `y_gl_carries`, `y_ez_targets`, `y_fg_att`, `y_fg_made`, `y_fg_made_0_19`, `y_fg_made_20_29`, `y_fg_made_30_39`, `y_fg_made_40_49`, `y_fg_made_50_59`, `y_fg_made_60p`, `y_fg_made_0_39`, `y_fg_made_50p`, `y_fg_missed`, `y_fg_blocked`, `y_pat_att`, `y_pat_made`, `y_fumbles_lost`, `y_two_pt_conversions`, `y_return_tds`, `y_target_share`, `y_carry_share`, `y_gl_carry_share`, `y_rz_target_share`, `y_pass_td_share`, `y_rush_td_share`, `y_dropback_share`, `y_catch_rate`, `y_yards_per_target`, `y_yards_per_carry`, `y_yards_per_attempt`, `y_completion_rate`, `y_int_per_attempt`.

#### team_week: 304 columns (11 key, 3 meta, 268 feature, 2 sensitivity, 3 avail, 17 target)

Keys: `team`, `season`, `week`, `game_id`, `opponent`, `game_type`, `home`, `kickoff_utc`, `cutoff_ts`, `asof_ts`, `feature_version`.
Meta (audit only): `meta__team_last_game_kickoff`, `meta__def_last_game_kickoff`, `meta__qb_depth_snapshot_ts`.
Sensitivity only: `ctx__temp`, `ctx__wind`.

ctx (game context, A), 18 columns: identical to player_week above.

team (offense on the field, B), 126 columns: identical to player_week above.

def (defense on the field, C and DST), 124 columns: identical to player_week above.

avail (same-week; not primary features), 3 columns:

| columns | positions | notes |
| --- | --- | --- |
| `avail__out_target_share_ew` | all | same-week injury report (Out or Doubtful players) |
| `avail__out_rush_share_ew` | all | same-week injury report (Out or Doubtful players) |
| `avail__out_dropback_share_ew` | all | same-week injury report (Out or Doubtful players) |

Targets (17): `y_plays`, `y_pass_rate`, `y_pass_att`, `y_rush_att`, `y_dropbacks`, `y_sacks`, `y_pass_td`, `y_rush_td`, `y_offensive_td`, `y_points`, `y_field_goal_att`, `y_field_goal_made`, `y_rz_trips`, `y_interceptions`, `y_fumbles_lost`, `y_two_pt_att`, `y_two_pt_conv`.

#### dst_week: 360 columns (11 key, 4 meta, 331 feature, 2 sensitivity, 3 avail, 9 target)

Keys: `team`, `season`, `week`, `game_id`, `opponent`, `game_type`, `home`, `kickoff_utc`, `cutoff_ts`, `asof_ts`, `feature_version`.
Meta (audit only): `meta__team_last_game_kickoff`, `meta__def_last_game_kickoff`, `meta__dst_last_game_kickoff`, `meta__qb_depth_snapshot_ts`.
Sensitivity only: `ctx__temp`, `ctx__wind`.

ctx (game context, A), 18 columns: identical to player_week above.

team (offense on the field, B), 126 columns: identical to player_week above.

def (defense on the field, C and DST), 187 columns: the 124 columns of player_week above plus:

| columns | positions | notes |
| --- | --- | --- |
| `def__dst_sacks__{l5,ew,std,ps,car}` | all | source sacks |
| `def__dst_interceptions__{l5,ew,std,ps,car}` | all | source interceptions |
| `def__dst_fumble_recoveries__{l5,ew,std,ps,car}` | all | source fumble_recoveries |
| `def__dst_def_td__{l5,ew,std,ps,car,std_shrunk,car_shrunk}` | all | source def_td / _one |
| `def__dst_return_td__{l5,ew,std,ps,car,std_shrunk,car_shrunk}` | all | source return_td / _one |
| `def__dst_safeties__{l5,ew,std,ps,car,std_shrunk,car_shrunk}` | all | source safeties / _one |
| `def__dst_blocked_kicks__{l5,ew,std,ps,car,std_shrunk,car_shrunk}` | all | source blocked_kicks / _one |
| `def__dst_points_allowed__{l5,ew,std,ps,car}` | all | source points_allowed |
| `def__dst_yards_allowed__{l5,ew,std,ps,car}` | all | source yards_allowed |
| `def__dst_sack_rate__{l5,ew,std,ps,car}` | all | source sacks / allowed_dropbacks |
| `def__dst_int_rate__{l5,ew,std,ps,car}` | all | source interceptions / allowed_pass_att |

avail (same-week; not primary features), 3 columns: identical to team_week above.

Targets (9): `y_sacks`, `y_interceptions`, `y_fumble_recoveries`, `y_def_td`, `y_return_td`, `y_safeties`, `y_blocked_kicks`, `y_points_allowed`, `y_yards_allowed`.

PARAMS (`ffml/features/spec.py`):

```json
{
  "ew_alpha": 0.35,
  "blend_w0": 0.5,
  "blend_decay": 0.0625,
  "asof_offset_hours": 1,
  "shrink_k": {
    "targets": 20,
    "receptions": 15,
    "carries": 30,
    "attempts": 60,
    "dropbacks": 60,
    "td": 100,
    "fg_att": 20,
    "pat_att": 30,
    "share_rz_targets": 10,
    "share_gl_carries": 6,
    "share_pass_td": 5,
    "share_rush_td": 4,
    "def_targets": 40,
    "def_carries": 60,
    "def_attempts": 120,
    "def_dropbacks": 120,
    "def_td": 250,
    "def_rz_trips": 20,
    "dst_games": 16
  },
  "short_week_max_rest": 5,
  "off_bye_min_rest": 13,
  "primetime_hour_et": 19,
  "games_missed_window": 5,
  "inj_prev_lookback_weeks": 2,
  "weeks_since_out_cap": 52,
  "undrafted_round": 8,
  "undrafted_pick": 300,
  "n_starters": {
    "QB": 1,
    "RB": 1,
    "WR": 3,
    "TE": 1,
    "K": 1
  }
}
```
