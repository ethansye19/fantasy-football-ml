# Phase 2 / QA and Security Reviewer / DE-2.5 + QA-2.1
Date: 2026-11-01
Scope: the data validation suite behind `ffml validate` (DATA_SCHEMA.md
section 6) with a markdown report, its synthetic-fixture tests, and one
timing-dependent Sleeper test made deterministic.

Task list restated from the brief:
1. `ffml/data/validate.py`: CheckResult framework, six raw-tier checks, six
   processed-tier checks that SKIP when their table is absent, markdown report,
   `run_validation(...) -> bool` for the CLI, exceptions inside a check become
   FAIL instead of a crash.
2. `tests/data/test_validate.py`: synthetic raw and processed snapshots in tmp
   SnapshotStores, one test per failure mode, one slow integration run of the
   raw tier against `data/raw`.
3. Harden the wall-clock assertion in `tests/data/test_sleeper.py` without
   touching `ffml/data/sleeper.py`.
4. This status note.

Done:
- `ffml/data/validate.py` (new). `CheckResult(name, tier, status, message,
  metrics)`, `ValidationContext` (cached LATEST reads for both stores, seasons,
  pins, injectable clock), `CHECKS` registry, `run_check` (traceback tail on
  crash), `run_suite(...) -> SuiteResult` and `run_validation(...) -> bool`
  with the signature the CLI calls plus keyword-only `pin_path` and `now_utc`
  for tests. Report goes to `reports/validation_<UTC>.md` by default and a
  compact table is logged.
  - Raw: `schema_pins` (reuses `ingest.check_schema` on a deep copy of the
    pins, so validation never rewrites them; removed or retyped columns FAIL,
    added or reordered columns WARN, datasets without a pin WARN; datasets
    whose manifest is not SnapshotStore format, currently the Sleeper players
    cache, are listed as "schema kept in code" and do not affect status),
    `duplicates` (registry `key_columns`: pbp, player_stats, team_stats,
    schedules; count plus three examples), `game_completeness` (every
    schedules row with a result needs >= 100 pbp rows and player_stats rows
    for both teams; gaps with gameday older than three days FAIL, newer gaps
    WARN; team codes canonicalised through `process.base.TEAM_ALIASES`),
    `weekly_volume` (latest season, rows per final game per completed week
    against the median of previous completed weeks, 25 percent tolerance,
    WARN only for the most recent completed week; normalised per game so bye
    weeks do not trip it), `null_rates` (player_stats player_id, team,
    opponent_team, week must be 0; pbp game_id and posteam below 1 percent
    after excluding play_type null, kickoff, timeout and end-of-period rows;
    schedules game_id and gameday non-null), `restricted_hygiene` (restricted
    specs only under a `restricted/` path with `restricted: true`, and no
    `fact_ecr_weekly` directory or manifest under `data/processed` outside
    `restricted/`).
  - Processed: `dim_player` (required columns, unique player_id, unique
    non-null sleeper_id), `id_crosswalk` (per season share of QB/RB/WR/TE/K
    players with a target, carry or pass attempt in fact_player_game, falling
    back to raw player_stats, whose dim_player row has a sleeper_id; FAIL below
    0.98, WARN below 1.0; unmatched names and espn/yahoo coverage in metrics),
    `fact_keys` (fact_player_game and fact_team_game keys unique, two team
    rows per game, every `status == "final"` dim_game game present),
    `team_changes` (played player-weeks need a fact_roster_week row with the
    same team in weeks w..w+2; FAIL above 0.5 percent, ten examples; players
    with more than one team per season reported as a metric), `point_in_time`
    (fact_depth_chart.snapshot_ts never null and never later than the raw
    depth_charts manifest `fetched_at`; fact_injury_report snapshot_ts and
    report_modified_at likewise against the injuries manifest), `partial_week`
    (projection week from `ffml.season.current_state`; no player_stats rows for
    teams whose dim_game row is still `scheduled`).
- `tests/data/test_validate.py` (new, 17 tests). A scenario builder writes
  season 2025 with four teams, 100 players, three completed weeks, a fourth
  week with one final and one scheduled game, and one player traded KC to PHI
  whose roster row lags one week, into tmp SnapshotStores (schedules, pbp,
  player_stats, rosters_weekly, dim_player, dim_game, fact_player_game,
  fact_team_game, fact_roster_week, fact_depth_chart, fact_injury_report) with
  pins generated from the clean frames through `ingest.check_schema`. Tests:
  clean fixture passes all twelve checks; removed column fails schema pins;
  duplicate player-week fails; stale missing game fails and a fresh one only
  warns; crosswalk 0.90 fails and 0.99 warns; team-change inconsistency above
  0.5 percent fails; null snapshot_ts fails; stats for a scheduled game fail
  partial_week; processed checks SKIP when the processed store is empty; tier
  selection; report file with the summary line; crashing check becomes FAIL;
  `run_validation` returns False on failure; CheckResult validation; and the
  `@pytest.mark.slow` raw-tier run against `data/raw`.
- `tests/data/test_sleeper.py`: `test_client_rate_limiter_sleeps_between_bursts`
  relied on `time.monotonic` inside the client's TokenBucket, so the second
  call's wait was `1.0 - elapsed` (observed 0.984 under load). The test now
  replaces `client.bucket` with a `TokenBucket` built on a frozen injected
  clock, so the wait is exactly one token. No production change was needed.
  Every other sleep assertion in that file is driven by injected `sleep`
  recorders or `freeze_time`.
- No `tests/fixtures/validate/**` was needed; the fixture is generated in code.

Evidence:
- `.venv/Scripts/python -m pytest tests/data/test_validate.py -q` ->
  `17 passed in 9.19s` (includes the slow raw-tier integration test).
- `.venv/Scripts/python -m pytest tests/data -q` -> `94 passed in 28.90s`.
- `.venv/Scripts/ruff check .` -> `All checks passed!` (ruff 0.17.3);
  `ruff format --check` clean for the three files I touched.
- `python -m ffml.cli validate --seasons 2016-2026 --report <tmp>` against
  the real lake (21 raw datasets, snapshot 2026-11-02T02:00Z, no
  `data/processed` yet): exit code 0, `6 passed, 0 warned, 0 failed,
  6 skipped`, about one second. Per check:
  - schema_pins PASS: all 20 pinned datasets match (sleeper_players listed as
    schema-in-code).
  - duplicates PASS: no duplicate keys in pbp, player_stats, team_stats,
    schedules (2016-2026).
  - game_completeness PASS: all 2,880 final games have >= 100 plays and stats
    for both teams.
  - weekly_volume PASS: 2026 completed weeks 1-7, player_stats about 68-70
    rows per game and pbp about 168-172 plays per game.
  - null_rates PASS: 0 nulls on the player_stats keys; pbp posteam null share
    0 after the 901 timeouts and 249 GAME/END rows are excluded.
  - restricted_hygiene PASS: ff_rankings_all only under
    `data/raw/restricted/`, manifest `restricted: true`.
  - all six processed checks SKIP ("not built yet").
- An earlier run before the sleeper_players exemption showed the only WARN
  the real lake produces: `no pin for ['sleeper_players']`. That cache is
  written by `SleeperPlayersCache` with its own manifest and a schema fixed
  in code (`PLAYERS_SCHEMA`), so it is now reported but not warned.

Not done / blocked:
- The processed tier has not run against real tables: `data/processed` did
  not exist during this task (the builders are being written concurrently).
  Only the synthetic fixtures exercise checks 7-12. Re-run `ffml validate`
  once `ffml process` has produced tables; the "89 multi-team players in
  2025" figure is reported as a metric, not asserted.
- Contract the processed checks assume (please confirm with the Data
  Engineer): dim_player has `player_id, full_name, position, sleeper_id,
  espn_id, yahoo_id, crosswalk_source, latest_team`; fact_player_game has
  `played`, `position`, `targets`, `carries`, `pass_att` (or `attempts`);
  fact_team_game has `game_id`; dim_game has `status` in
  {scheduled, in_progress, final}; fact_depth_chart has `snapshot_ts`;
  fact_injury_report has `snapshot_ts` and `report_modified_at`
  (string, naive or tz-aware timestamps are all accepted).
- No `reports/` directory is committed; the default report path is created on
  first run and should stay out of git (`.gitignore` does not list it yet,
  orchestrator-owned file).

Decisions proposed:
- D-032 Validation severities: `ffml validate` exits non-zero only on FAIL;
  WARN never blocks; a processed check whose table is absent is SKIP so the
  command can run before `ffml process`; a check that raises is a FAIL with
  the traceback tail. Game-completeness gaps younger than three days and the
  most recent completed week's volume deviation are WARN because nflverse
  loads game days incrementally.
- D-033 Weekly volume is judged per final game (rows divided by the games
  final that week), not raw rows per week, so bye weeks (13-16 games) never
  look like partial loads; the 25 percent tolerance from DATA_SCHEMA.md is
  kept.
- D-034 Datasets whose manifest is not SnapshotStore format (the Sleeper
  players cache) are exempt from schema pinning because their schema is
  enforced in code; the report lists them under "schema kept in code".
- D-035 (conditional) The 98 percent crosswalk threshold is applied to every
  requested season. If real dim_player coverage for 2016-2019 falls below 98
  percent because retired players lack Sleeper ids, gate on the current
  season and downgrade older seasons to WARN; decide after the first real run.

Next:
- Run `ffml validate` after `ffml process` lands and attach the report to the
  Gate 2 note; fix contract mismatches on either side.
- Add `.gitignore` entry for `reports/validation_*.md` (orchestrator).
- CI: `pytest -m "not slow and not network"` plus a nightly job that runs the
  slow raw-tier test after the ingest.
