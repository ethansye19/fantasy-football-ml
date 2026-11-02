# Phase 2 / Data Engineer (core processed tables) / DE-2.4 dims, team_game, player_game
Date: 2026-11-02
Scope: the three builder modules behind `dim_team`, `dim_game`, `dim_player`,
`fact_team_game`, `fact_dst_game` and `fact_player_game` under
`ffml/data/process/`, their tests, the manual crosswalk override file, and one
real 2016-2026 build into `data/processed/`.

Task list restated: `dims.py` (`build_dims`), `team_game.py`
(`build_team_game`), `player_game.py` (`build_player_game`), additive helpers in
`base.py`, `ffml/data/crosswalk_overrides.csv`, `tests/data/test_process_core.py`
(unit tests on a fabricated game plus one slow 2025 integration test), a real
run for 2016-2026, and this note.

Done:
- `ffml/data/process/base.py` (additive): `FANTASY_POSITION_MAP` (FB to RB),
  `TEAM_ALIAS_GROUPS`, `fantasy_position()`, `clean_id()` (string ids, blanks to
  null, D-023), `read_raw_or_none()` (missing partition logs and returns None),
  `team_game_map()` (dim_game in long form: one row per season, week, team with
  game_id, opponent, home, game_type, kickoff_utc, points, points_allowed). Also
  removed an unused `typing.Any` import that failed `ruff check .` at HEAD.
- `ffml/data/process/dims.py`:
  - `dim_team` (key `all`, 32 rows): current franchises from raw `teams`
    (historical codes in `TEAM_ALIASES` dropped) with an `aliases` list column
    (`LV: [OAK]`, `LAC: [SD]`, `LA: [STL, SL, LAR]`, `JAX: [JAC]`, ...) instead of
    separate alias rows; team_name, team_nick, conf, division, four colours,
    logo_wikipedia, logo_espn, logo_squared, wordmark.
  - `dim_game` (by season): all schedule rows including postseason,
    `kickoff_utc` via `ffml.season.with_kickoff_utc`, canonical teams, `status`
    = final when `result` is set, in_progress when kickoff is at most 5 hours
    in the past with no result, else scheduled. `build_dim_game(schedules, now)`
    takes an explicit clock; `build_dims(ctx, now=None)` defaults to UTC now.
  - `dim_player` (key `all`, 24,863 rows = raw `players`): string ids
    `sleeper_id, espn_id, yahoo_id, pfr_id, fantasypros_id, mfl_id, nfl_id`,
    `position` (fantasy, FB to RB) plus `position_raw`, birth_date as Date,
    draft fields, headshot_url, latest_team (canonical), status, height, weight,
    college_name, last_season, `last_active_season` (latest requested season
    with an opportunity), `crosswalk_source`, `updated_at`. `sleeper_id`
    precedence: manual overrides, `ff_playerids` (deduplicated per gsis_id,
    preferring rows with a sleeper_id and the newest `db_season`),
    `rosters_weekly` (most recent non-null per player), the Sleeper players map
    through its stripped `gsis_id` (deduplicated, active entries preferred;
    the map is read through `SleeperPlayersCache(root=<raw>/sleeper_players)`,
    so it is fetched at most once per UTC day and the run continues without it
    on any failure), then name matching (lower case, punctuation removed,
    suffixes Jr/Sr/II/III/IV/V removed) on name + fantasy position + most
    recent team for skill players with at least one opportunity. Unique
    non-null `sleeper_id` is enforced: the player with the most recent
    `last_active_season` (then `last_stats_season`, `players.last_season`)
    keeps it, the others are nulled and each case is logged (zero cases in the
    2016-2026 run). `crosswalk_coverage(dim_player, stats)` returns per-season
    players with opportunities, matched count, share and the unmatched list;
    `build_dims` logs it (WARNING below 0.98). Opportunity = targets + carries
    + attempts + fg_att + pat_att, so kickers are inside the metric.
  - `ffml/data/crosswalk_overrides.csv` (`gsis_id, sleeper_id, espn_id,
    yahoo_id, position, note`): all 7 known 2026 misses resolved from the
    Sleeper map (Patrick Herbert 12901, Matthew Hibner = "Matt Hibner" 13324,
    Jack Strand 13602, Max Bredeson 13516, Trey Smack 13545, Drew Stevens 13968,
    Dominic Zvada 13833), the two 2025 misses (Quentin Skinner 12907, Ben Sauls
    13066), and Travis Hunter (12530) with `position=WR`: nflverse lists him as
    CB in both `players` and `player_stats`, which would have dropped his 45
    targets from `fact_player_game` and broke the JAX target and yardage ties.
- `ffml/data/process/team_game.py`: `prepare_pbp()` casts the pbp columns,
  canonicalises team codes (2016 OAK/SD/STL rows come out as LV/LAC/LA while
  `game_id` keeps its original text) and adds the shared play flags used by
  both fact builders. Definitions, each checked against nflverse 2025 team
  totals: `plays` = pass or rush plays excluding no_play, qb_kneel, qb_spike;
  `pass_att` = official attempts (pass_attempt without sack, spikes included,
  two-point tries and nullified plays excluded; 569 of 570 2025 team-games tie
  to `team_stats.attempts`, the one difference is a fake field goal pass);
  `rush_att` = official carries (kneels included; 570 of 570 tie); targets =
  official attempt with a `receiver_player_id` (ties exactly to
  `player_stats` targets in all 544 regular-season team-weeks); sacks,
  pass_td, rush_td, interceptions, field_goal_att and field_goal_made all tie
  570 of 570. Pace = `game_seconds_remaining` difference to the previous
  eligible play within the same `fixed_drive` (negative gaps dropped; mean
  31.2 s in 2025). Neutral = `0.2 <= wp <= 0.8` and `qtr <= 3`. `proe` is the
  mean `pass_oe` in percentage points as published. rz_trips = distinct
  drives with a scrimmage snap (pass, run, kneel, spike, field goal; not
  two-point tries) at or inside the 20; rz_td_rate = share of those drives
  with a pass or rush TD (null with zero trips). Position splits use
  receiver/rusher/passer ids joined to `dim_player.position`. Every offensive
  metric plus `points` is mirrored as `allowed_*` (80 columns in total).
  `fact_dst_game`: sacks, interceptions, fumble_recoveries (recoveries of an
  opponent fumble on any play, special teams included), def_td (touchdowns by
  the defence on non kickoff/punt plays, blocked-kick returns included),
  return_td (touchdowns by the team on kickoff or punt plays), safeties,
  blocked_kicks (punts, field goals, extra points), points_allowed from
  dim_game, yards_allowed = yards gained on the opponent's eligible plays.
  Both tables carry `team, season, week, game_id, opponent, home, game_type`
  and are asserted unique on `(team, season, week)`.
- `ffml/data/process/player_game.py`: base rows from `player_stats`
  (REG and POST) filtered to QB, RB, WR, TE, K using `dim_player.position`
  (FB to RB, manual position override), canonical team and opponent, joined to
  dim_game on (season, week, team) for game_id, opponent, home, game_type and
  kickoff_utc. Duplicate `(player_id, season, week)` rows keep the row with
  more touches (logged; none in 2016-2026). Adds pbp counts per player-game
  (rz_targets, rz_carries, gl_carries, ez_targets, third_down_targets,
  two_min_targets, designed_rush, scrambles, dropbacks from `passer_id`, which
  unlike `passer_player_id` is set on scrambles), adot, team context
  (team_targets, team_rz_targets, team_rz_carries, team_gl_carries from pbp;
  team_pass_att, team_rush_att, team_plays, team_pass_td, team_rush_td from
  fact_team_game), offense_snaps and offense_pct via `dim_player.pfr_id` and
  game_id (12 of 6,969 2025 stats rows have no snap row), ff_opportunity
  expected values renamed to the schema names (null `player_id` rows dropped),
  Next Gen Stats from the three files (week 0 season totals dropped; POST
  weeks remapped by rank because NGS numbers the Super Bowl one week later
  than nflverse), kicking buckets (fg_made_0_39, fg_made_40_49, fg_made_50p,
  fg_blocked kept), fumbles_lost, two_pt_conversions, return_tds,
  fantasy_points_ppr_ref and fantasy_points_std_ref, plus target_share,
  air_yards_share and wopr from the source. Rostered skill players with status
  ACT or INA whose team played that week and who have no stats row are added
  with `played=false`, zero counting stats and null rates (`row_source =
  "roster"`); 806 of the 2,752 such 2025 rows turn out to have offensive
  snaps and are therefore `played=true`. `played` = offense_snaps > 0 or any
  of pass_att, carries, targets, fg_att, pat_att > 0. 89 columns, unique on
  `(player_id, season, week)`.
- `tests/data/test_process_core.py` (19 tests): a temporary raw lake with a
  fabricated KC at BUF game of 22 plays, 11 players, a Sleeper snapshot and all
  side datasets, run through `run_process` so the runner and snapshot writes
  are exercised too. Covers dim_team aliases, dim_game status with a frozen
  clock (final, in_progress, stale scheduled, future), name normalisation,
  crosswalk precedence (manual, ff_playerids, rosters_weekly, Sleeper gsis,
  name match, no match without opportunities), the unique sleeper_id rule, FB
  to RB, the coverage metric, play flags, team-game counts and rates (plays,
  attempts, dropbacks, neutral and early-down rates, seconds per play,
  rz_trips, gl_plays, scoring, turnovers), allowed mirrors, DST inputs,
  player-game situational counts, team context, snap/expected/NGS joins,
  roster rows, the NGS week remap, and the manifests. The slow test runs the
  six builders for 2025 against `data/raw`.
- Commits: `3e26777` (base helpers, dims, override CSV), `b31b973`
  (team_game, player_game), `19bce75` (tests), plus this note.

Evidence:
- `.venv/Scripts/python -m pytest tests/data/test_process_core.py -q -m "not slow"`:
  `18 passed, 1 deselected in 1.61s`.
- `.venv/Scripts/python -m pytest tests/data/test_process_core.py -q -m slow`:
  `1 passed, 18 deselected in 1.57s`.
- `FFML_OFFLINE=1 .venv/Scripts/python -m pytest tests/data -q`:
  `127 passed, 2 skipped in 28.38s` (skips are the two network tests).
- `ruff check .`: All checks passed. `ruff format` applied to the four new files
  (base.py left as found apart from the import).
- Real run, `run_process(range(2016, 2027), tables=[the six])` into
  `data/processed/` at 2026-11-02T02:39Z: wall clock 4.6 s.

  | table | rows | partitions | columns |
  | --- | ---: | ---: | ---: |
  | dim_team | 32 | 1 (all) | 14 |
  | dim_game | 3,033 | 11 | 27 |
  | dim_player | 24,863 | 1 (all) | 28 |
  | fact_team_game | 5,760 | 11 | 80 |
  | fact_dst_game | 5,760 | 11 | 16 |
  | fact_player_game | 98,530 | 11 | 89 |

  fact_team_game per season 2016-2026: 534, 534, 534, 534, 538, 570, 568, 570,
  570, 570, 238 (two rows per pbp game; 2026 = 119 games through week 8).
  fact_player_game per season: 9,478 / 9,187 / 8,960 / 9,016 / 9,394 / 9,718 /
  9,701 / 9,623 / 9,578 / 9,728 / 4,147; roster-only rows added per season:
  3,137 / 2,778 / 2,676 / 2,727 / 2,909 / 2,888 / 2,933 / 2,872 / 2,783 /
  2,752 / 1,208. dim_game status: 2,880 final, 152 scheduled, 1 in_progress
  (2026_08_PHI_WAS, kicked off 01:15Z, build at 02:39Z).
- Crosswalk sources in dim_player: ff_playerids 6,132, sleeper_players 889,
  manual 10, name_match 3, none 17,829 (players with no Sleeper entry).
  Coverage of skill players with at least one opportunity: 2016 597/598
  (0.9983, Isaac Fruechte WR MIN), 2017 593/595 (0.9966, Ed Eagan WR NYG,
  Marquez Williams RB CLE), 2018-2022 1.0000, 2023 598/599 (0.9983, Rod
  Williams TE PIT), 2024 1.0000, 2025 625/625 (1.0000), **2026 532/532
  (1.0000), unmatched list empty**. Zero sleeper_id conflicts were logged.
- Integration checks in the slow test (2025): every one of the 285 games has
  exactly two team rows; all keys unique; coverage 1.0; in all 544 regular
  season team-weeks the sum of player targets plus targets to non-skill
  receivers (OL, DL, P, S on trick plays; 19 team-weeks, one target each)
  equals `team_targets`; the sum of player receiving yards is within 2 percent
  of pbp passing yards to skill receivers in every team-week (max 1.2 percent,
  laterals) and within 2 percent of raw pbp passing yards in 99.4 percent of
  team-weeks (the exceptions are the same trick-play catches, e.g. a 21-yard
  catch by Cody Ford).
- Data surprises: (1) Travis Hunter is CB in both nflverse player tables
  (298 receiving yards in 2025 would have been dropped); handled by the
  `position` override. (2) Matthew Hibner is "Matt Hibner" in Sleeper, so
  exact name matching could not find him. (3) NGS postseason weeks are
  18/19/20/22 (2016-2020) and 19/20/21/23 (2021+), one more than nflverse for
  the Super Bowl. (4) `ff_opportunity` has 423 rows per season with a null
  `player_id` (team rows). (5) 389 roster rows in 2025-2026 carry a gsis_id
  that is not in `players`; they cannot be crosswalked and are not added as
  roster rows. (6) `pass`=1 in pbp includes scrambles and sacks while
  `rush`=0 on kneels; `passer_player_id` is null on scrambles but `passer_id`
  is set. (7) 2025 players with more than one team: 88 across all positions in
  `player_stats` (DATA_SOURCES.md says 89), 32 among QB/RB/WR/TE/K, 47 in
  fact_player_game once roster rows are included; the integration test
  asserts at least 32 because the table is skill-position only by design.

Not done / blocked:
- `started` (depth chart rank 1) from DATA_SCHEMA.md is not in
  fact_player_game; it belongs with `fact_depth_chart` (context builder) and
  the feature layer, since depth charts are snapshot-timed.
- DATA_SCHEMA.md (orchestrator-owned) differs from what was built and should
  be updated: dim_team uses an `aliases` list column rather than `team_alias`
  rows; dim_player adds `position_raw, mfl_id, last_season, last_active_season,
  status, height, weight, college_name` (`nfl_id` is carried from `players`);
  fact_team_game names the kicking columns `field_goal_att` and
  `field_goal_made` (brief) rather than `field_goals`, adds `two_pt_att`,
  `two_pt_conv`, `game_type`, and the position-allowed list is
  `allowed_{rb,wr,te}_targets`, `allowed_{rb,wr,te}_rec_yds`,
  `allowed_rb_rush_att`, `allowed_rb_rush_yds`, `allowed_rb_rush_td`,
  `allowed_rec_td_{rb,wr,te}`, `allowed_qb_pass_yds`, `allowed_qb_pass_td`,
  `allowed_qb_rush_yds`; fact_dst_game carries the same key columns as
  fact_team_game; fact_player_game adds `game_type, home, kickoff_utc,
  fg_blocked, target_share, air_yards_share, wopr, row_source`.
- `offense_snaps` is null (not zero) when snap_counts has no row for a
  player-game (1,793 of 9,728 rows in 2025, almost all roster rows);
  `played` treats null as zero.
- `proe` is kept in nflfastR's percentage-point scale; divide by 100 if the
  feature layer wants a rate.

Decisions proposed:
- D-041 Manual crosswalk overrides win. The override file is applied first,
  before ff_playerids, rosters_weekly, the Sleeper gsis_id and name matching
  (D-022 listed it last, but a human-verified row must be able to correct an
  upstream id). The file may also set `position` to force a fantasy position
  for two-way players (Travis Hunter = WR).
- D-042 Attempt definitions in fact_team_game follow the official stats so
  that team columns tie to player_stats sums: `pass_att` includes spikes,
  `rush_att` includes kneels, both exclude two-point tries and nullified
  plays; `plays`, pace, EPA, success and the pass-rate family exclude kneels,
  spikes and no_play. A target is an official attempt with a receiver.
- D-043 fact_player_game includes rostered (ACT or INA) skill players without
  a stats row for weeks their team played, with `played = offense_snaps > 0 or
  any attempt, carry, target, FG or PAT`; the crosswalk coverage metric counts
  kickers through fg_att and pat_att (stricter than the gate text).
- D-044 Next Gen Stats postseason weeks are remapped by rank onto the
  nflverse postseason weeks of that season (NGS numbers the Super Bowl one
  week later).
- D-045 DST attribution: def_td = touchdowns by the defending team on any
  non kickoff/punt play (blocked field goal and extra point returns included);
  return_td = touchdowns by a team on kickoff or punt plays (blocked punt
  returns included); fumble_recoveries = recoveries of an opponent fumble on
  any play including special teams; blocked kicks are credited to the
  defending team.

Next:
- Validation suite (DE-2.5) can reuse `crosswalk_coverage`, the team-week tie
  checks in the slow test, and `assert_unique`.
- Feature builders should take `position` from dim_player, reset team-share
  windows when `team` changes between rows, and treat `row_source = "roster"`
  rows as the inactive examples for the availability model.
