# Data Sources (verified 2026-11-01)

Everything below was checked by running `nflreadpy 0.1.5` from this repository's
virtual environment and by fetching the source documentation on 2026-11-01.
Re-verify the "latest week" column and the schema fingerprints at the start of
Phase 2; nflverse publishes schema changes without notice (see Quirks).

## Season and week resolution

| Signal | Value on 2026-11-01 |
| --- | --- |
| `nflreadpy.get_current_season()` | 2026 |
| `nflreadpy.get_current_week()` | 8 |
| Sleeper `GET /v1/state/nfl` | season 2026, week 8, `season_start_date` 2026-09-09 |
| `load_schedules(2026)` week 8 | 12 of 14 games final; PHI@WAS (Sun 20:20 ET) and CHI@SEA (Mon) pending |
| Next week to project | Week 9, first kickoff Thu 2026-11-05 |
| Regular season end | Week 18, 2027-01-10 |

The app derives season and week from the schedule table, not from the
calendar. The projection week is the earliest regular-season week that has a
game whose `result` is null. If no such week exists, the app is in
**offseason mode**. A partially played week (the situation above) is a
first-class state: players whose game is final show actuals, the rest show
projections.

## Primary source: nflverse via nflreadpy

- Package: `nflreadpy` 0.1.5 (PyPI), Polars-native. Project interpreter: Python 3.11.
- Configuration is done programmatically with
  `nflreadpy.config.update_config(cache_mode=..., cache_dir=..., cache_duration=..., timeout=..., verbose=...)`.
  Cache modes are `memory` (default), `filesystem`, and `off`; default cache
  duration is 86,400 seconds. The ingestion layer sets `cache_mode="off"` and
  does its own snapshotting so that every byte read is written to the raw layer.
- Data files are Parquet releases on GitHub (`nflverse/nflverse-data`),
  licensed **CC-BY-4.0** (LICENSE.md in that repository).

### Datasets used

| Dataset | Loader | Grain and shape | Seasons | 2026 latest on 11-01 | In-season update cadence | Use |
| --- | --- | --- | --- | --- | --- | --- |
| Play-by-play | `load_pbp(seasons)` | play; 372 columns, identical 2016-2026 | 1999+ (2016+ used) | week 8, 12 games, 20,841 plays | nightly after game days plus intra-day on game days; Wed-Thu re-run for NFL stat corrections | team pace, pass rate, `xpass` / `pass_oe`, red-zone and goal-line opportunities, air yards, EPA, Vegas lines, game context |
| Weekly player stats | `load_player_stats(seasons, summary_level="week")` | player-game; 150 columns | 1999+ (2016+ used; 17-19k rows per season) | week 8 (641 rows, partial) | nightly with pbp | targets, carries, attempts, yards, TDs, `target_share`, `air_yards_share`, `wopr`, reference fantasy points |
| Weekly team stats | `load_team_stats(seasons)` | team-game; 138 columns | 1999+ | week 8 | nightly | cross-check of team totals |
| Schedules | `load_schedules(seasons)` | game; 46 columns | 1999+ | full 2026 slate with `spread_line`, `total_line`, moneylines, `roof`, `surface`, `temp`, `wind`, rest days, QB names, results | every 5 minutes | kickoff times, lines, results for week resolution, game context |
| Weekly rosters | `load_rosters_weekly(seasons)` | player-team-week; 36 columns with `gsis_id`, `sleeper_id`, `espn_id`, `yahoo_id`, `pfr_id`, `status` | 2002+ | week 8 (2,259 rows) | daily 07:00 UTC | team changes, roster status (ACT, INA, RES, DEV, CUT, ...), ID crosswalk |
| Injuries | `load_injuries(seasons)` | player-week report; 16 columns, **no timestamp column** | 2009+ | week 8 | daily 07:00 UTC | `report_status` (Out, Doubtful, Questionable), `practice_status`; availability layer |
| Depth charts | `load_depth_charts(seasons)` | **timestamped snapshots** (`dt`), team, player, `pos_abb`, `pos_rank`, `pos_grp`; 271 snapshots since 2026-03-22 | 2001+; timestamped format from 2025 | 2026-11-01T14:14Z | daily 07:00 UTC, year-round | depth rank at prediction time, point-in-time safe via `dt` |
| Snap counts (PFR) | `load_snap_counts(seasons)` | player-game keyed by `pfr_player_id`; 16 columns | 2012+ | week 8 (92 rows, partial) | 4x daily | snap share, route participation proxy |
| Next Gen Stats | `load_nextgen_stats(seasons, stat_type=passing/rushing/receiving)` | player-week keyed by `player_gsis_id`, qualifying players only | 2016+ | week 8 (partial) | nightly 3-5 AM ET | separation, cushion, YAC over expectation, time to throw, rush yards over expected |
| Expected fantasy points | `load_ff_opportunity(seasons, stat_type="weekly")` | player-week; 159 columns with `*_exp` and `*_team` | 2006+ (2016+ used; 5.5-6.1k rows per season) | week 8 (partial) | nightly (ffverse automation) | expected receptions, yards, TDs as regressors and as a published benchmark. **`season` is String and `week` is Float64** |
| FTN charting | `load_ftn_charting(seasons)` | play; 29 columns (motion, play action, RPO, screen, blitzers, box count, drops, catchable, contested) | 2022+ | week 8 (183 rows, partial) | 4x daily | optional efficiency features; **CC-BY-SA-4.0, attribute "ftndata.com via nflverse"** |
| PFR advanced stats | `load_pfr_advstats(seasons, stat_type=rec/rush/pass)` | player-game keyed by `pfr_player_id` | 2018+ | week 8 (partial) | 4x daily | drops and broken tackles (optional) |
| Players | `load_players()` | player; 24,863 rows, 39 columns | all | n/a | periodic | master dimension, `pfr_id`, `espn_id`, draft and rookie season |
| FF player IDs | `load_ff_playerids()` | crosswalk; 12,534 rows, 35 columns (`gsis_id`, `sleeper_id`, `espn_id`, `yahoo_id`, `fantasypros_id`, `pfr_id`, ...) | all | n/a | weekly (DynastyProcess) | joining Sleeper rosters to nflverse IDs |
| FF rankings | `load_ff_rankings("week")` and `load_ff_rankings("all")` | FantasyPros expert consensus via DynastyProcess; archive 2019-12-27 to 2026-10-30 with 371 scrape dates; weekly positional pages (`ecr_type="wp"`) from 2020-10 at roughly weekly cadence | 2020+ | 2026-10-30 | weekly | **evaluation-only baseline**; see Licensing |
| Teams | `load_teams()` | 36 rows with colours and logos | n/a | n/a | static | UI |
| Participation | `load_participation(seasons)` | play-level personnel and routes | 2016-2024 | **not updated in season** | after the postseason | not used for live features |

Not used: combine, draft picks, contracts, officials, trades.

### Coverage checks run on 2026-11-01

- Weekly player stats 2016-2025: 17,362 to 19,422 rows per season, 1,859 to
  2,088 distinct players per season, weeks 1-21 or 1-22 including postseason.
  Across 2016-2026: 182,464 regular-season rows and 7,879 postseason rows.
- Play-by-play 2016-2025: 47,109 to 49,922 plays per season, 267 to 285
  games. Column set identical across all seasons including 2026.
- Expected fantasy points weekly: 5,473 to 6,081 rows per season 2016-2025.
- Duplicate `(player_id, season, week)` rows in 2026 player stats: 0.
- Players with more than one team in 2025 stats: 89. Team-change handling is
  required, not optional.
- ID crosswalk: of 570 QB, RB, WR, TE, and K with a 2026 stat line, 563 match
  `ff_playerids` with a `sleeper_id`. The 7 misses are deep backups and three
  kickers (Patrick Herbert, Matthew Hibner, Jack Strand, Max Bredeson, Trey
  Smack, Drew Stevens, Dominic Zvada). Weekly rosters alone match only 490 of
  570, so the crosswalk must union both sources, add the `gsis_id` field that
  the Sleeper players payload carries, and fall back to name plus team plus
  position matching with a manual override file.
- Null counts in 2026 player stats for `team`, `opponent_team`, `targets`,
  `carries`, `attempts`, `fantasy_points_ppr`, `target_share`, `wopr`: 0.
  `player_id` is null on 8 rows (dropped and logged).

## League source: Sleeper API

- Base URL `https://api.sleeper.app/v1`, read-only, no authentication.
- Rate limit guidance: stay under 1,000 calls per minute.
- Endpoints used: `user/<username>`, `user/<user_id>/leagues/nfl/<season>`,
  `league/<league_id>` (contains `scoring_settings` and `roster_positions`),
  `league/<league_id>/rosters`, `league/<league_id>/users`,
  `league/<league_id>/matchups/<week>`, `league/<league_id>/transactions/<round>`,
  `players/nfl` (measured 14.7 MB on 2026-11-01; Sleeper asks for at most one
  call per day, so it is cached to Parquet daily), `players/nfl/trending/add|drop`
  (must credit Sleeper if shown), and `state/nfl`.
- Terms: free for non-commercial use; commercial use requires contacting Sleeper.

## Licensing and attribution requirements

| Source | License | Attribution the app must show |
| --- | --- | --- |
| nflverse data releases (pbp, stats, schedules, rosters, injuries, depth charts, players, snap counts, NGS, PFR advanced stats) | CC-BY-4.0 | "Data from nflverse (github.com/nflverse/nflverse-data), CC-BY-4.0. Modified: aggregated and modeled by this project." Also note that Next Gen Stats originate from NFL NGS and snap counts from Pro Football Reference, both redistributed via nflverse. |
| FTN charting | CC-BY-SA-4.0 | "FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0." Share-alike applies to derived datasets we publish, so FTN features are off by default in the MVP behind a flag (DECISIONS D-009). |
| ffopportunity expected points | Published by ffverse automation; package code is MIT, data license not stated on the reference page | "Expected fantasy points from ffverse/ffopportunity via nflverse." Verify the data release license in Phase 2 before showing it in the UI. |
| FantasyPros ECR via DynastyProcess | No data license stated (repository code is GPL-3.0; FantasyPros content is proprietary) | Not displayed and not redistributed. Used only inside the backtest as the public-consensus baseline, with scrape dates checked against kickoff (DECISIONS D-007). |
| Sleeper API | Non-commercial use permitted; trending data must credit Sleeper | "League and player data from Sleeper." |

## Quirks and gotchas found during inspection

1. Windows console encoding: Polars tables raise `UnicodeEncodeError` under
   cp1252. Every project entry point sets `PYTHONUTF8=1`.
2. `load_ff_opportunity` returns `season` as String and `week` as Float64.
   Cast on ingest; the schema test pins this.
3. `load_injuries` for 2025 and 2026 has no timestamp; seasons 2016-2024 carry
   a `date_modified` timestamp (kept as `report_modified_at`, D-029). Reports
   are week-keyed and refreshed daily, so the stored row for a past week is
   the final pre-game report plus any later edits. Backtests therefore treat
   same-week injury designations as a labelled sensitivity variant, not a
   primary feature (MODELING.md, "Injury information policy"); where the
   historical timestamp exists it is used to audit that variant.
4. Depth charts changed format in 2025: `dt` timestamps instead of weeks.
   Older seasons have weekly rows. The feature builder takes "latest snapshot
   at or before the cutoff" for 2025+ and "the week w-1 chart" before that.
5. Snap counts and PFR advanced stats key on `pfr_player_id`; join through
   `players.pfr_id` or `ff_playerids.pfr_id`.
6. `load_ff_rankings("week")` (30 columns, includes `pos_rank`,
   `start_sit_grade`, `r2p_pts`) and `load_ff_rankings("all")` (24 columns,
   includes `ecr_type`, `page_type`) have different schemas.
7. nflverse re-runs pbp from Wednesday night into Thursday to apply NFL stat
   corrections. The weekly retrain runs Thursday morning UTC, not Tuesday.
8. Participation data is not updated during the season and is not used for
   live features.
9. Vegas lines in historical schedules are closing lines. Using them for
   backtests is slightly optimistic relative to a Thursday prediction; this is
   recorded as a known limitation and tested as a sensitivity (lines removed).
