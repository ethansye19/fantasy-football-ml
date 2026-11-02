# Phase 2 / Data Engineer (Sleeper) / DE-2.3
Date: 2026-11-01
Scope: read-only Sleeper API client with typed models, retries, client-side
rate limiting, a once-per-day Parquet cache of the players map, and
normalization helpers for league scoring and roster settings; recorded
fixtures and offline tests.

Task list restated: (1) `ffml/data/sleeper.py` client, models, errors,
players frame, players cache, normalizers; (2) real fixtures under
`tests/fixtures/sleeper/` with a README; (3) `tests/data/test_sleeper.py`
offline via respx plus one network smoke test; (4) this note.

Done:
- `ffml/data/sleeper.py`
  - `SleeperClient(base_url=None, timeout=None, client=None, ...)`: settings
    defaults, shared `httpx.Client`, configured User-Agent sent on every
    request (also when the caller supplies the client), tenacity retries (3
    attempts, exponential backoff 0.5 s to 8 s, `Retry-After` honoured and
    capped at 30 s) on transport errors, 429 and 5xx; 404 and `null` lookup
    bodies raise `SleeperNotFound` without retry; `TokenBucket` limiter at 300
    requests per minute with a burst of 30 (Sleeper's limit is 1,000);
    context manager and `close()`; `request_count` for audits.
  - Methods: `get_state`, `get_user`, `get_user_leagues`, `get_league`,
    `get_rosters`, `get_league_users`, `get_matchups`, `get_transactions`,
    `get_trending` (docstring and `TRENDING_ATTRIBUTION` require crediting
    Sleeper), `get_players` (raw map), plus `get_json` for ad-hoc paths.
  - Pydantic v2 models with `extra="allow"` (unknown keys in `model_extra`,
    original payload in `.raw`): `SleeperState`, `SleeperUser`,
    `SleeperLeague` (season as int, `num_teams`, `playoff_week_start`),
    `SleeperRoster` with `SleeperRosterSettings` (`points_for`,
    `points_against`, `potential_points` from Sleeper's integer plus
    hundredths fields; `bench` helper), `SleeperLeagueUser` (team_name lifted
    from metadata, null `is_owner` becomes False), `SleeperMatchup`,
    `SleeperTransaction` (null adds/drops become `{}`, `week` alias of `leg`,
    `created_at` UTC), `SleeperTrending`.
  - `players_frame(raw, snapshot_date=None)`: 23 typed columns
    (`PLAYERS_SCHEMA`), team defenses kept with composed `full_name`,
    `gsis_id` whitespace stripped, external ids as strings, sorted by
    `sleeper_id`, stable schema even for empty input.
  - `SleeperPlayersCache(root=None, client=None)`: `get(max_age_hours=24,
    force=False)` reads `<raw_dir>/sleeper_players/snapshot=<YYYYMMDD>/players.parquet`
    when today's snapshot exists or the latest one is within `max_age_hours`;
    otherwise fetches once, writes Parquet atomically plus `_manifest.json`
    (dataset, source, endpoint, snapshot_date, fetched_at, rows, columns,
    schema_hash, file_sha256, terms, attribution) and updates `LATEST`.
    Never fetches twice in one UTC day unless `force=True`.
  - `normalize_scoring_settings` (drops zero and null entries, floats, sorted
    keys, raises on non-numeric, boolean or NaN) and
    `normalize_roster_positions` (upper-cases, validates against
    `SLEEPER_ROSTER_SLOTS`, raises naming unknown slots).
  - Errors: `SleeperError`, `SleeperNotFound`, `SleeperRateLimited`
    (`retry_after`), `SleeperServerError`.
- `tests/fixtures/sleeper/`: 11 recorded files (171 KB total) and a README
  with capture time, commands, filtering rule and quirks.
- `tests/data/test_sleeper.py`: 38 offline tests (transport, retries,
  Retry-After, rate limiter, every endpoint, players frame schema and dtypes,
  cache once-per-day behaviour under freezegun, normalizers and their error
  paths) plus `test_live_state_and_league` marked `network`.

Evidence:
- `FFML_OFFLINE=1 .venv/Scripts/python -m pytest tests/data/test_sleeper.py -q`:
  `38 passed, 1 skipped in 4.49s`.
- `.venv/Scripts/python -m pytest tests -q` (network included):
  `39 passed in 4.73s`.
- `.venv/Scripts/python -m pytest tests/data/test_sleeper.py -q -m network`:
  `1 passed, 38 deselected in 0.58s` (live state season 2026 week 8; league
  1389750342958223360 has 8 rosters).
- `ruff check .` and `ruff format --check` clean for `ffml/data/sleeper.py`
  and `tests/data/test_sleeper.py`.
- Fixture capture 2026-11-02 01:43 UTC: 10 small endpoints (HTTP 200 each)
  and one `/players/nfl` download (14,663,896 bytes, 0.88 s, 12,229 entries).
  No second players-map request was made.
- Full-map check (from the single download, not a refetch): `players_frame`
  built 12,229 rows x 23 columns in 0.09 s; Parquet 593,834 bytes; read back
  equal to the frame; 0 `gsis_id` values with whitespace after cleaning; 14
  duplicated `gsis_id` values remain in Sleeper's map.
- League 1389750342958223360: `roster_positions` = QB, RB, RB, WR, WR, TE,
  FLEX, FLEX, K, DEF, BN x5; 43 scoring keys of which 41 are non-zero (`fum`
  and `pts_allow_21_27` are 0):
  offense `pass_yd 0.04, pass_td 4, pass_int -1, pass_2pt 2, rush_yd 0.1,
  rush_td 6, rush_2pt 2, rec 1, rec_yd 0.1, rec_td 6, rec_2pt 2,
  fum_lost -2, fum_rec_td 6`; kicker `xpm 1, xpmiss -1, fgm_0_19 3,
  fgm_20_29 3, fgm_30_39 3, fgm_40_49 4, fgm_50_59 5, fgm_60p 6, fgmiss -1`;
  defense and special teams `sack 1, int 2, ff 1, fum_rec 2, safe 2,
  blk_kick 2, def_td 6, def_st_td 6, def_st_ff 1, def_st_fum_rec 1, st_td 6,
  st_ff 1, st_fum_rec 1, pts_allow_0 10, pts_allow_1_6 7, pts_allow_7_13 4,
  pts_allow_14_20 1, pts_allow_28_34 -1, pts_allow_35p -4`.
- Crosswalk coverage of the 2026 rosters in the Sleeper map: 119 ids, 9 team
  defenses; of the 110 players, 26 have a `gsis_id` and 84 do not (every
  player who entered the league in 2020 or later lacks one); 87 of 119
  entries also lack `espn_id` and `yahoo_id`.
- Not-found behaviour observed live: unknown username returns HTTP 200 with
  body `null`; unknown league returns HTTP 404 with body `null`.

Not done / blocked:
- Migration of the players cache onto `ffml/data/store.py` (SnapshotStore):
  deliberately not done, the store is being built concurrently. The manifest
  already carries `schema_hash` and `file_sha256` to make the move cheap.
- No CLI entry point for the Sleeper client (`ffml/cli.py` is outside my
  write scope); `ffml league sync` belongs to the backend league import.
- Fixtures do not cover a league with `reserve`, `taxi`, `co_owners`, an odd
  team count (bye `matchup_id`), or a trade transaction; the models handle
  those shapes (null lists become empty) but only hand-built inputs test
  them.

Decisions proposed:
- D-022 Sleeper `gsis_id` is a fallback, not a primary crosswalk key: only
  26 of 110 rostered players carry one, 866 of 12,229 map entries pad it
  with a leading space (stripped in `players_frame`), and 14 values are
  duplicated. dim_player should join Sleeper to nflverse through
  `ff_playerids.sleeper_id` first, then `rosters_weekly.sleeper_id`, and use
  Sleeper's `gsis_id` only when both miss; duplicates resolve to the active
  player with the lower `search_rank`.
- D-023 External ids in `sleeper_players` are strings (`espn_id`,
  `yahoo_id`, `rotowire_id`, `sportradar_id`); downstream joins cast the
  nflverse side to string rather than the reverse.
- D-024 `scoring_settings` are stored as Sleeper's keys with zero-valued
  entries removed and keys sorted; the Phase 3 ScoringRules engine must treat
  a missing key as 0 points and must support at least the 41 keys listed in
  Evidence (plus `bonus_*`, `idp_*`, `pass_cmp`, `pass_inc`, `pass_sack`,
  `rec_fd`, `rush_fd`, `pass_fd`, `fum_ret_yd`, `yds_allow_*` buckets, which
  appear in other public leagues but not in the owner's).
- D-025 Sleeper client limits: 300 requests per minute with a burst of 30,
  3 attempts with `Retry-After` capped at 30 s, players map fetched at most
  once per UTC day. Any process-wide scheduler must share one
  `SleeperClient` so the bucket is global.
- D-026 Players cache snapshot id is the UTC date `YYYYMMDD` (not the
  timestamped id used by other raw datasets) because Sleeper's terms make the
  day the natural unit; `LATEST` holds that id.

Next:
- DE-2.4 consumes `players_frame` output for the `sleeper_players` table and
  the crosswalk union; DE-2.1 owner migrates `SleeperPlayersCache._write`
  onto SnapshotStore and applies the retention policy (8 snapshots plus one
  per completed week).
- Backend league import (Phase 4) builds on `get_league`, `get_rosters`,
  `get_league_users` and `normalize_*`; the scoring engine (Phase 3) maps the
  listed keys onto stat lines.
