# Phase 2 / Data Engineer (nflverse) / DE-2.1, DE-2.2, DE-2.7
Date: 2026-11-01
Scope: raw Parquet snapshot store, nflreadpy ingestion of all datasets in
DATA_SOURCES.md with schema pinning and drift detection, and the schedule-based
season/week resolver behind `ffml info`.

Task list restated: DE-2.1 `ffml/data/store.py` (SnapshotStore, manifests,
LATEST, prune, schema_hash); DE-2.2 `ffml/data/nflverse.py` registry and
`ffml/data/ingest.py` driver with `expected_schemas.json`; DE-2.7
`ffml/season.py` resolver; tests under `tests/data/`; one real
`ffml ingest --seasons 2016-2026` run.

Done:
- `ffml/data/store.py`: `SnapshotStore(root)` (defaults to `settings.raw_dir`,
  also used for `processed_dir`). Layout exactly as DATA_SCHEMA.md section 1:
  `<root>/<dataset>/snapshot=<YYYYMMDDTHHMMSSZ>/season=<key>/part-0.parquet`,
  `_manifest.json`, and a `LATEST` text file. Writes go to a hidden staging
  directory, are renamed into place, and `LATEST` is replaced last through a
  temp file, so a crash never leaves a half-written snapshot pointed to. Ids
  get a `-001` counter suffix when two writes share a second (or the clock
  steps back), and lexicographic order stays chronological. `read()` filters
  partitions by season, concatenates with `vertical_relaxed`, and falls back to
  `diagonal_relaxed` with a warning when partitions have different column sets.
  `prune(dataset, keep=8)` never deletes the LATEST snapshot. `SnapshotWriter`
  (via `begin_snapshot`) streams one partition at a time so play-by-play never
  sits in memory as a whole. Manifest fields: dataset, snapshot_id, loader,
  loader_kwargs, seasons, partitions, fetched_at, written_at,
  nflreadpy_version, rows {total, per_season}, columns, schema_hash (newest
  partition), schema_partition, schema_uniform, partition_schema_hashes,
  file_sha256 and files per partition, bytes, source_license, restricted,
  extra (caller metadata).
- `ffml/data/nflverse.py`: `DATASETS` registry of 20 `DatasetSpec`s with the
  loaders, kwargs, min seasons, key columns, casts (`ff_opportunity` season and
  week to Int32), licences (nflverse CC-BY-4.0, FTN CC-BY-SA-4.0, rankings
  "unverified; private evaluation only"), `restricted=True` for
  `ff_rankings_all` (stored under `raw/restricted/ff_rankings_all/`, manifest
  `restricted: true`; module docstring forbids reads outside `ffml/eval`).
  `configure_nflreadpy()` sets `cache_mode="off"` and the settings timeout.
  `fetch()` retries transient network errors 3 times with exponential backoff
  and raises `SeasonNotAvailable` for HTTP 404 or nflreadpy's own
  "Season must be between" range check (which is what nflreadpy actually
  raises for pre-data seasons; it never reaches the network).
- `ffml/data/ingest.py`: `run_ingest(seasons, datasets)` loads seasonal
  datasets one season at a time, drops null-key rows (player_stats
  `player_id`) with a logged count, applies casts, writes one snapshot per
  dataset, logs rows per season, and checks the newest partition's schema
  against `ffml/data/schemas/expected_schemas.json`
  (`dataset -> {schema_hash, columns, season, pinned_at}`). No pin: pin it.
  Added columns: warn and update. Removed or retyped columns:
  `SchemaDriftError`; the snapshot is still committed for inspection but
  `LATEST` is not advanced. Older seasons whose layout differs from the pinned
  (newest) season only warn. A season that is missing upstream before any
  season loaded is skipped with a warning and recorded in the manifest; a gap
  after a loaded season fails the dataset. Failures are per dataset; the run
  continues and raises at the end.
- `ffml/season.py`: `resolve_state(schedules, now_utc) -> SeasonState` (frozen,
  `to_dict()`), `kickoff_utc` from `gameday` + `gametime` in America/New_York
  (null time = 13:00 ET), candidate season = largest season whose first REG
  kickoff minus 45 days is at or before now, modes regular / postseason /
  offseason, projection_week, week_in_progress, completed_weeks,
  last_completed_week, next_kickoff_utc, last_final_kickoff_utc.
  `describe_current_state()` reads the latest schedules snapshot; `ffml info`
  prints it.
- Tests: `tests/data/test_store.py` (12), `tests/data/test_ingest.py` (13, one
  `network`), `tests/data/test_season.py` (13). Fixture
  `tests/fixtures/schedules_2026.parquet` (29 KB, 272 rows, the 2026 schedule
  as of 2026-11-01 evening with 12 of 14 week-8 games final).
- Commits: `9ca417c` (code and tests), `1e5875d` (schema pins), plus this note.

Evidence:
- `FFML_OFFLINE=1 .venv/Scripts/python -m pytest tests/data/test_store.py tests/data/test_ingest.py tests/data/test_season.py -q`:
  `37 passed, 1 skipped in 3.26s` (the skip is the network test).
- `.venv/Scripts/python -m pytest tests/data/test_ingest.py -m network -q`:
  `1 passed, 12 deselected in 3.02s` (teams 36 rows, schedules 2026 = 272 rows).
- Whole `tests/data` offline: `1 failed, 74 passed, 2 skipped` where the one
  failure is `tests/data/test_sleeper.py::...rate limit` (another agent's
  timing assertion, `0.984 != 1.0`), not in this scope.
- `ruff check .`: All checks passed. `ruff format --check` clean on my files.
- `.venv/Scripts/ffml ingest --seasons 2016-2026` (all 20 datasets, exit 0,
  about 80 s wall clock, no retries, no skipped seasons):

  | dataset | rows | partitions | seconds |
  | --- | ---: | ---: | ---: |
  | pbp | 505,095 | 11 | 18.4 |
  | player_stats | 190,327 | 11 | 4.3 |
  | team_stats | 5,760 | 11 | 2.6 |
  | schedules | 3,033 | 11 | 1.6 |
  | rosters_weekly | 486,148 | 11 | 3.1 |
  | injuries | 58,015 | 11 | 3.2 |
  | depth_charts | 1,617,508 | 11 | 3.5 |
  | snap_counts | 263,180 | 11 | 3.0 |
  | nextgen_passing | 6,188 | 11 | 1.5 |
  | nextgen_rushing | 6,314 | 11 | 1.5 |
  | nextgen_receiving | 15,354 | 11 | 1.8 |
  | ff_opportunity | 60,812 | 11 | 3.7 |
  | ftn_charting (2022+) | 203,589 | 5 | 1.7 |
  | pfr_advstats_rec (2018+) | 37,465 | 9 | 3.7 |
  | pfr_advstats_rush (2018+) | 19,318 | 9 | 2.7 |
  | pfr_advstats_pass (2018+) | 5,669 | 9 | 2.8 |
  | players | 24,863 | 1 | 1.1 |
  | ff_playerids | 12,534 | 1 | 1.2 |
  | teams | 36 | 1 | 0.1 |
  | ff_rankings_all (restricted) | 1,873,627 | 1 | 3.1 |
  | total | 5,394,835 | | |

  pbp per season 2016-2025: 47,651 / 47,245 / 47,109 / 47,260 / 47,705 /
  49,922 / 49,434 / 49,665 / 49,492 / 48,771; 2026 through week 8: 20,841.
  player_stats per season: 17,341 to 19,400; 2026: 8,287. Null `player_id`
  rows dropped: 223 across 2016-2026 (21-22 per season, 8 in 2026).
  `ff_opportunity` manifest columns: season Int32, week Int32 (casts applied).
- `du -sh data/raw`: 207M (pbp 129M, restricted/ff_rankings_all 38M,
  ff_opportunity 9.3M, player_stats 6.5M, rosters_weekly 5.8M, depth_charts
  5.4M, rest under 3M each).
- `.venv/Scripts/ffml info` at 2026-11-02T02:01Z: season 2026, mode regular,
  projection_week 8, week_in_progress True, completed_weeks 1-7,
  next_kickoff_utc 2026-11-03T01:15Z (CHI@SEA Monday night; PHI@WAS had
  kicked off by then), last_final_kickoff_utc 2026-11-01T21:25Z,
  games_final 119 of 272.
- Schema surprises found by the per-partition hashes (none affect the 2026
  pin; all are handled by `vertical_relaxed` or the diagonal fallback):
  - pbp: column names identical 2016-2026 as documented, but `goal_to_go` is
    Int32 in 2016-2019 and 2021-2023 and Float64 in 2020 and 2024-2026.
  - rosters_weekly: `height` is Float64 in 2016-2024 and Int32 in 2025-2026.
  - injuries: 2016-2024 files have `date_modified` (a timestamp) and
    `season_type`; 2025-2026 files drop `date_modified` and rename
    `season_type` to `game_type`; 2016-2020 have Float64 season/week. So
    DATA_SOURCES.md's "no timestamp column" holds only for 2025+.
  - depth_charts: 2016-2024 weekly format (15 columns) vs 2025-2026 `dt`
    snapshot format (12 columns), as documented; `store.read("depth_charts")`
    returns the diagonal union with a warning.

Not done / blocked:
- `docs/DATA_DICTIONARY.md` (optional) not written; the manifests' `columns`
  maps are the source for it.
- Retention rule "plus one snapshot per completed week forever"
  (DATA_SCHEMA.md section 1) is not implemented; `prune(keep=8)` only keeps
  the newest N and LATEST. Needs the refresh job to know week boundaries.
- `ffml/data/schemas/expected_schemas.json` is read via `__file__`; it ships
  in editable installs but `pyproject.toml` has no package-data entry for a
  wheel/Docker install (I may not edit pyproject).
- `tests/data/test_sleeper.py` has one timing-sensitive failure (not mine).

Decisions proposed:
- D-027 Schema pins are taken from the newest season partition of each
  dataset and record that season. Removed or retyped columns in that season
  fail ingest; the drifted snapshot is committed but LATEST is not advanced.
  Older seasons with a different layout (depth charts, injuries) only warn, so
  historical backfills never block and a `--seasons 2026` nightly refresh is
  checked against the same pin.
- D-028 Missing-season policy: a season nflverse does not publish (404 or
  nflreadpy range check) before any season of that dataset loaded is skipped
  with a warning and recorded in `extra.skipped_seasons`; a missing season
  after a loaded one is a gap and fails the dataset. Ingest continues with the
  other datasets and raises at the end.
- D-029 Injuries 2016-2024 carry `date_modified`. Propose that
  `fact_injury_report` keep it as `report_modified_at` (null for 2025+) so the
  evaluator can verify the lagged-injury policy (D-006) against real report
  timestamps for historical seasons, instead of treating all history as
  unverifiable.
- D-030 Add `[tool.setuptools.package-data] ffml = ["data/schemas/*.json"]`
  to pyproject.toml (orchestrator-owned file).
- D-031 Weekly refresh calls `ffml ingest --seasons <current>` for the mutable
  datasets (about 10 s) and the full 2016-2026 run only after nflverse stat
  corrections on Thursday; the full run costs about 80 s and 207 MB per
  snapshot set, so `prune(keep=8)` bounds disk at about 2 GB.

Next:
- DE-2.4 processed tables on top of `SnapshotStore.read`, DE-2.5 validation
  suite (the `key_columns` in the registry are the uniqueness keys to test),
  DE-2.6 warehouse views, DE-2.8 data dictionary from the manifests.
