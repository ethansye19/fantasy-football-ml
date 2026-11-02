# Phase 2 / Data Engineer (context tables) / DE-2.4b, DE-2.6, DE-2.8
Date: 2026-11-01
Scope: context tables (injury reports, depth charts, weekly rosters), the
Sleeper players table, the restricted FantasyPros ECR table, the DuckDB
warehouse over LATEST snapshots, and the generated data dictionary.

Task list restated: A `ffml/data/process/context.py` (`build_context` ->
`fact_injury_report`, `fact_depth_chart`, `fact_roster_week`); B
`ffml/data/process/sleeper_players.py` (`build_sleeper_players`); C
`ffml/data/process/ecr.py` (`build_ecr` -> restricted `fact_ecr_weekly`); D
`ffml/data/warehouse.py` (`rebuild_warehouse`); E `ffml/data/dictionary.py`
(`write_data_dictionary`); tests `tests/data/test_process_context.py` and
`tests/data/test_warehouse.py`; one real 2016-2026 run.

Done:
- `ffml/data/process/context.py`. Kickoffs come from raw `schedules` through
  `with_kickoff_utc`; no dependency on `dim_*`. `week_windows` builds one
  window per `(season, week)` (inclusive upper bound = last kickoff of the
  week, postseason weeks included) plus a week-0 row per season ending at the
  first kickoff minus 60 days; `assign_week` is a forward `join_asof`, so
  week w = (last kickoff of w-1, last kickoff of w], pre-season timestamps go
  to week 1, earlier ones to week 0 with `game_type = "offseason"`, and
  anything after the last scheduled kickoff falls back to the raw partition
  season, week 0 (logged; none today).
  - `fact_injury_report`: both layouts unified (`game_type` coalesced with
    `season_type`, `date_modified` kept as `report_modified_at`, null for
    2025+, D-029), `snapshot_ts` = raw manifest `fetched_at`, dedupe keeps
    the latest `report_modified_at` then Out > Doubtful > Questionable >
    other; key `(season, week, player_id, team)` asserted.
  - `fact_depth_chart`: legacy rows (2016-2024, `club_code`, `depth_team`,
    `depth_position`, `formation`) get `snapshot_ts` = first kickoff of the
    week minus 48 h, `game_type` from the schedule, `pos_abb` =
    `depth_position`, `pos_rank` = `depth_team`, `pos_grp` = `formation`;
    timestamped rows (2025+, `dt`) get `(season, week, game_type)` from the
    window rule and `formation` = `pos_slot` as a string. Blank `pos_abb`
    becomes null, null-week `SBBYE` rows and rows without `gsis_id` are
    dropped and counted, duplicates on
    `(snapshot_ts, team, player_id, pos_abb)` keep the best `pos_rank` and
    the removed count is logged.
  - `fact_roster_week`: string ids via `id_as_string` (no trailing `.0`),
    `height` cast to Int32 inches (Float64 in 2016-2024, Int32 in 2025+),
    `status_description_abbr` -> `status_description`, null-`gsis_id` rows
    dropped; key `(season, week, player_id, team)` so a traded player keeps
    both rows.
- `ffml/data/process/sleeper_players.py`: `SleeperPlayersCache(...).get()`
  (one fetch per UTC day, D-026), documented columns, stripped `gsis_id`,
  string ids, canonical team (`LAR` -> `LA`), `snapshot_date`; falls back to
  the newest cached snapshot when the fetch fails and returns `{}` when there
  is none. Records `sleeper_players=<YYYYMMDD>` in the lineage inputs.
- `ffml/data/process/ecr.py`: module docstring states the D-007 rule. Keeps
  `ecr_type == "wp"` for QB/RB/WR/TE/K/DST (`PK` -> `K`, `DEF` -> `DST`),
  `scoring` from the normalised page name (`ppr-*` -> ppr,
  `half-point-ppr-*` -> half, else std; `fp_page` is stored normalised,
  e.g. `ppr-wr`, because the archive switched from `/nfl/rankings/ppr-wr.php`
  to bare names), week assignment through the same window rule (scrape date
  = midnight ET), `valid_for_week` (scrape date strictly before the ET date
  of the week's first kickoff), FantasyPros id -> gsis through raw
  `ff_playerids` (first row wins for the 4 duplicated ids), `FA` team ->
  null, dedupe on `(scrape_date, fp_page, fantasypros_id)`, seasonal
  partitions. Added two columns beyond the brief, `team_game_date` and
  `valid_for_game`, because the archive scrapes on Fridays (see Evidence).
- `ffml/data/warehouse.py`: `rebuild_warehouse(path=None, *, settings=None)`
  writes to `<path>.tmp` and renames into place, one `raw_<dataset>` view per
  non-restricted raw dataset and one `<table>` view per non-restricted
  processed table over LATEST (hive partitioning for integer `season=`
  directories so timestamped depth charts get a `season`, plain file lists
  for `season=all` and the Sleeper cache layout, `union_by_name=true`),
  `meta_snapshots(kind, name, snapshot_id, fetched_at, rows)`; anything under
  a `restricted` directory gets no view and is not listed (code comment and
  docstring say so). Connection closed before return; `list_views()` helper.
- `ffml/data/dictionary.py`: `write_data_dictionary(path=None, *,
  settings=None)`; sections per processed table with description, snapshot,
  rows, partitions, lineage inputs, and per column dtype, null share and one
  example from a sample of at most 50,000 rows spread over partitions; raw
  summary table from manifests; restricted datasets as a one-line note.
- Tests: `tests/data/test_process_context.py` (12: windows, week rule,
  injury dedupe and severity, new injury layout, depth-chart unification with
  a three-week synthetic schedule for 2024 and 2025, roster id casting, ECR
  page/scoring, ECR week and validity around a Thursday kickoff and a
  Sunday-only week plus crosswalk, Sleeper normalisation, fetch-failure
  path, end to end through `run_process` on a tiny raw lake) and
  `tests/data/test_warehouse.py` (4: restricted detection, views and
  `meta_snapshots`, idempotency and released file handle, dictionary
  sections).
- Commits on main: `1ff600e` (modules and tests), `39db4bf` (dictionary),
  plus this note.

Evidence:
- `FFML_OFFLINE=1 .venv/Scripts/python -m pytest tests/data/test_process_context.py tests/data/test_warehouse.py -q`:
  `16 passed in 2.79s`.
- `FFML_OFFLINE=1 .venv/Scripts/python -m pytest tests/data -q`:
  `108 passed, 2 skipped in 26.50s` (skips are the network tests).
- `ruff check .`: All checks passed. `ruff format` applied to my files.
- Real run `run_process(range(2016, 2027), tables=[...])` then
  `rebuild_warehouse()` and `write_data_dictionary()`: process 3.9 s,
  warehouse 0.7 s, dictionary 0.2 s, 5.0 s total; `data/processed` 7.2 MB,
  `data/warehouse.duckdb` 524 KB.

  | table | rows | partitions | notes |
  | --- | ---: | ---: | --- |
  | fact_injury_report | 58,013 | 11 | 2 duplicate rows removed; `report_modified_at` present on 49,486 of 49,486 rows 2016-2024 and 0 of 8,527 rows 2025+ |
  | fact_depth_chart | 1,578,397 | 11 | weekly 322,973 (21-22 snapshots per season), timestamped 1,255,424 (2025: 187 snapshots, 2026: 305); dropped 2,156 null-week SBBYE rows and 29,910 rows without gsis_id (all 2025+); 7,045 duplicates removed |
  | fact_roster_week | 485,999 | 11 | 149 null-gsis rows dropped; 13 player-weeks appear on two teams and are kept |
  | sleeper_players | 12,229 | 1 (all) | fetched live, snapshot 20261102; `gsis_id` on 3,893 rows (3,886 distinct) |
  | fact_ecr_weekly (restricted) | 54,397 | 8 (2019-2026) | written under `data/processed/restricted/` |

  Per season: injuries 5,115 / 5,104 / 5,133 / 5,392 / 5,661 / 5,587 / 5,682
  / 5,599 / 6,213 / 6,068 / 2,459 (2016-2026); depth charts 35,723 / 35,777
  / 35,634 / 35,359 / 35,092 / 36,431 / 36,602 / 36,208 / 36,147 / 457,588 /
  797,836 (the 2026 partition includes the 2025 raw partition's February to
  March 2026 snapshots relabelled 2026 week 0); rosters 35,020 / 51,321 /
  52,200 / 51,630 / 44,124 / 46,670 / 46,136 / 45,650 / 46,572 / 46,820 /
  19,856.
- ECR per season (player match rate excludes DST, which never matches the
  player crosswalk):

  | season | rows | scrape dates | weeks | (week, scoring) combos | valid_for_week combos | valid_for_game combos | valid_for_game share | match rate |
  | --- | ---: | ---: | ---: | ---: | ---: | ---: | ---: | ---: |
  | 2019 | 373 | 1 | 1 | 2 | 2 | 2 | 0.997 | 1.000 |
  | 2020 | 9,350 | 20 | 16 | 32 | 8 | 32 | 0.903 | 1.000 |
  | 2021 | 9,243 | 19 | 17 | 34 | 4 | 34 | 0.931 | 0.998 |
  | 2022 | 8,060 | 16 | 16 | 32 | 0 | 32 | 0.922 | 0.998 |
  | 2023 | 7,802 | 16 | 16 | 32 | 0 | 32 | 0.919 | 0.998 |
  | 2024 | 7,063 | 15 | 14 | 28 | 2 | 28 | 0.914 | 0.992 |
  | 2025 | 8,066 | 16 | 16 | 32 | 0 | 32 | 0.912 | 0.984 |
  | 2026 | 4,440 | 8 | 8 | 16 | 0 | 16 | 0.915 | 0.972 |

  The archive scrapes on Fridays (14 of 15 scrape dates in 2024), i.e. after
  Thursday Night Football, so the brief's `valid_for_week` is false for
  almost every week; `valid_for_game` (scrape date strictly before the
  ranked team's own game date, ET) is true for about 91 percent of rows and
  for every (week, scoring) combination. Scoring pages present: `ppr-rb`,
  `ppr-wr`, `ppr-te` and the scoring-independent `qb`, `k`, `dst`; no
  half-PPR or standard RB/WR/TE weekly pages exist in the archive. Coverage
  starts 2020-10 and 2024 begins at week 4.
- Warehouse: 24 views (`raw_depth_charts`, `raw_ff_opportunity`,
  `raw_ff_playerids`, `raw_ftn_charting`, `raw_injuries`,
  `raw_nextgen_passing`, `raw_nextgen_receiving`, `raw_nextgen_rushing`,
  `raw_pbp`, `raw_pfr_advstats_pass`, `raw_pfr_advstats_rec`,
  `raw_pfr_advstats_rush`, `raw_player_stats`, `raw_players`,
  `raw_rosters_weekly`, `raw_schedules`, `raw_sleeper_players`,
  `raw_snap_counts`, `raw_team_stats`, `raw_teams`, `fact_depth_chart`,
  `fact_injury_report`, `fact_roster_week`, `sleeper_players`);
  `meta_snapshots` has 20 raw and 4 processed rows; no view SQL and no meta
  row mentions `restricted`; `raw_depth_charts` returns 1,617,508 rows with a
  non-null `season` for every row (hive column).
- `git check-ignore`: `data/processed/restricted/fact_ecr_weekly`,
  `data/warehouse.duckdb` and `data/raw/sleeper_players` are ignored.

Not done / blocked:
- `docs/DATA_SCHEMA.md` section 2 still lists the older keys
  `(snapshot_ts, season, week, player_id)` for `fact_injury_report` and
  `(season, week, player_id, snapshot_ts)` for `fact_roster_week`, and does
  not mention `team_game_date` / `valid_for_game`; not my file.
- `ffml/data/nflverse.py`'s docstring says nothing outside `ffml/eval` may
  read restricted data; per the brief `ffml/data/process/ecr.py` now does
  (output stays under `data/processed/restricted/`). The docstring should
  name that builder as the one other reader; not my file.
- Timestamps after the last scheduled kickoff (for example January 2027
  depth charts before nflverse adds the playoff games) are labelled week 0
  offseason until the schedule catches up; a rebuild relabels them.
- The dictionary's "Built from raw snapshots" line lists every raw input of
  the run, because the runner records run-wide `inputs`; per-table lineage
  would need a change in `ffml/data/process/__init__.py`.
- 2026 depth-chart week 1 holds 65 snapshots spanning 2026-07-12 to
  2026-09-14 (the 60-day pre-season window belongs to week 1 by the rule);
  point-in-time consumers must select by `snapshot_ts <= cutoff`, never by
  week alone.

Decisions proposed:
- D-036 Consensus-baseline comparability uses `fact_ecr_weekly.valid_for_game`
  (scrape date strictly before the ranked team's game date in ET);
  `valid_for_week` is kept for the record but the archive's Friday scrapes
  make it false for nearly all Thursday-start weeks. The evaluator reports
  both counts.
- D-037 Depth-chart point-in-time rule: legacy weekly charts carry
  `snapshot_ts` = first kickoff of the week minus 48 h and `game_type` from
  the schedule; timestamped charts get `(season, week)` from the schedule
  windows with week 0 = offseason; rows without `gsis_id` are dropped (all
  2025+ unsigned rookies) because the key and every join need `player_id`.
- D-038 `fact_injury_report` and `fact_roster_week` are keyed
  `(season, week, player_id, team)`; `snapshot_ts` is the raw manifest
  `fetched_at` and is lineage, not part of the key. Update DATA_SCHEMA.md.
- D-039 The warehouse and the data dictionary never expose restricted data:
  no views, no `meta_snapshots` rows, one-line existence note only.
- D-040 ECR normalisation (numbers start after the D-035 already in the
  log): `fp_page` stored as the page basename, `FA` team
  as null, `PK` -> `K`, `DEF` -> `DST`; team defenses keep a null
  `player_id`.

Next:
- Orchestrator: regenerate `docs/DATA_DICTIONARY.md` after `dim_*`,
  `fact_player_game` and `fact_team_game` land (`ffml dictionary`, 0.2 s),
  then `ffml warehouse`.
- Feature Engineer: select depth charts with the latest `snapshot_ts` at or
  before the cutoff per `(team, player_id, pos_abb)`; apply the D-006 lag to
  `fact_injury_report` and use `report_modified_at` (2016-2024) to audit the
  same-week variant.
- Evaluator: join `fact_ecr_weekly` on `(season, week, player_id)` with
  `valid_for_game` true and the latest `scrape_date` per week; aggregate
  metrics only in committed reports (D-007).
