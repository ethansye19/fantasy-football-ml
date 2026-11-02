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
