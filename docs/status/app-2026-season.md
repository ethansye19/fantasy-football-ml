# App follows the current 2026 season (D-124)
Date: 2026-11-10
Role: App engineer (api/, web/, tests/api, docs/API.md, docs/RUNBOOK.md)
Scope: the owner reported that the app shows 2024 data. D-124: the real app
takes the current season, week and season type from Sleeper, cross-checked
with the nflverse schedule; data refreshed; 2024 replays only in private test,
labeled test data; gated features stay on "no approved model" with the 2026
season and week as context (D-124 assumption).

## Audit: every source of season or week

Status: **fixed** (changed here), **test-only** (never read by the real app),
**n/a** (already correct, or not a season source the app shows).

| # | Source | Before | Status |
| --- | --- | --- | --- |
| 1 | `api/services/season.py` (`/meta/season`) | `ffml.season.current_state` on the latest raw `schedules` snapshot only; that snapshot was 20261107T225529Z (week 9 with only Thursday final) | **fixed**: Sleeper state endpoint first, through the shared `SleeperClient` and its D-025 rate limiter, cross-checked with the schedule; disagreement shows the schedule's answer and is logged; Sleeper down or slower than 5 s uses the schedule and says so; new fields `week`, `season_type`, `source`, `cross_check`; cached 180 s (60 s when a source is missing) |
| 2 | Raw `schedules` snapshot (cross-check input) | 20261107T225529Z | **fixed**: refreshed, now 20261110T023724Z |
| 3 | `api/services/leagues.py` `current_sleeper_season` | Sleeper state `league_season or season` for the import and league list default | n/a: already live from Sleeper (2026); unchanged |
| 4 | `api/routers/leagues.py` `?season=` default; `api/schemas.py` import `season` (optional) and manual league `season` (required, no default) | default = Sleeper's current season | n/a |
| 5 | Database defaults: `leagues.season`, `projection_runs.season/week`, `league_projection_cache.week` (`api/db/models.py`, migration 0001) | no defaults or server defaults; values come from Sleeper or the request | n/a |
| 6 | Database contents `data/app.db` | 1 league, Sleeper `1389750342958223360`, season 2026; `projection_runs`, `league_projection_cache`, `sessions` empty | n/a; re-synced (section "Data") |
| 7 | Public prediction store `data/predictions/season=2024/week=10/run=2024w10-20261103T010442Z-replay` (the public store held a **week 10** replay, not week 11) | read by private test's replay search; never served by production (D-100 gate reads only `LATEST`, refuses replays) | **fixed**: moved (SHA-256 of all 6 files identical before and after) to `data/predictions_private/replays_test_only/season=2024/week=10/`; the public store is now empty |
| 8 | Private store `data/predictions_private/season=2024/week=11/run=2024w11-20261104T225732Z-replay` | the replay private test served | **test-only**: moved (hashes identical) to `data/predictions_private/replays_test_only/season=2024/week=11/` so every 2024 replay sits in one clearly named folder (README.txt there) |
| 9 | `api/approval.py` replay discovery (`find_replays` over private **and public** stores; pinned replay looked up in both) | public store searched in private test | **fixed**: private test searches the private store and `replays_test_only` only, never the public store; a pinned run must be a replay; production unchanged (never reads replays) |
| 10 | `FFML_API_REPLAY_RUN` | private test only; unset in `.env` and in the user, machine and process environment | **test-only**; production now logs a startup warning that it is ignored |
| 11 | `FFML_API_FIXTURE_PREDICTIONS_DIR` | private test only; unset | **test-only**; production startup warning when set |
| 12 | `FFML_APP_ENV` | unset (`.env` has it commented out), so `production`; `Dockerfile.api` and `docker-compose.yml` default `production` | n/a |
| 13 | New: `FFML_API_TEST_REPLAYS_DIR`, `FFML_SEASON_SLEEPER_ENABLED`, `FFML_SEASON_TTL_SECONDS`, `FFML_SEASON_SLEEPER_TIMEOUT_SECONDS` | - | **fixed** (documented, docs/API.md section 2) |
| 14 | `api/envelope.py` notices, `PredictionSource.describe()`, `/meta/approval`, `/meta/run-info` | replays said "Historical replay of 2024 week N" but had no test-data label | **fixed**: `test_data_label` "TEST DATA: 2024 week 11 replay, not approved, not a recommendation" (first notice, `run.test_data_label`, top level of both meta routes); fixtures get "TEST DATA: fixture data, ..." |
| 15 | `api/routers/projections.py` `week` default (the served run's week) and offseason check | production never serves (503 `no_approved_model`) | n/a in production; test-only in private test |
| 16 | Model registry version shown on `/about/model-card`: `chain_v1-primary-c2024w19-20261102T222634Z` (training cutoff 2024 week 19, rejected model) | rendered in a Version column on the production page | **fixed in the page**: the registry table shows model, status and created date, not version ids; `/meta/model-card` JSON still carries `version` (the registry is outside this task) |
| 17 | `web/components/home/HomeStatus.tsx` season line | `projection_week` from `/meta/season` | **fixed**: uses `week` via `web/lib/season.ts` |
| 18 | `web/components/setup/SetupView.tsx` default season (Sleeper import and manual form) | `/meta/season` season, calendar fallback only when the endpoint fails | n/a (2026 now); fallback kept |
| 19 | Gated views (`GatedView`, `NoApprovedModelState`) | "No approved model" with no season context | **fixed**: "Current week: 2026 season, week N" |
| 20 | Season shown on other pages | only the offseason/postseason/unknown banner (`SeasonBanner`) | **fixed**: `SeasonContext` strip on every page ("Now: 2026 season, week 9 (in progress) · From Sleeper, matching the nflverse schedule.") |
| 21 | `web/mock/app.mjs` `/meta/season` (2026 week 10), `web/mock/fixtures.mjs` `FIXTURE_RUN` (2026 week 10) | contract v1.4 shape | **test-only**; updated to the v1.5 shape and test-data label |
| 22 | Test fixtures: `tests/api/api_factory.write_snapshot` (default 2024 week 10), `tests/fixtures/sleeper/state.json` (2026 week 8), `tests/fixtures/schedules_2026.parquet` (week 8), `web/tests/unit/result.test.ts` (2024w10 replay id) | - | **test-only** |
| 23 | New recorded fixtures `tests/api/fixtures/sleeper_state_2026w09.json` (Sleeper state 2026-11-10 02:35 UTC) and `tests/api/fixtures/schedules_2026_w09.parquet` (snapshot 20261110T023724Z, 2026 rows) | - | **test-only** |
| 24 | `tests/api/test_projections.py`, `tests/api/test_qa_performance.py` real-replay paths | read the replay from the public store | **test-only**; now read `replays_test_only` |
| 25 | `ffml.data.refresh.default_seasons`, `ffml.config.first_season` (2016) | pipeline, frozen path | n/a (gave 2016-2026) |
| 26 | `docs/API.md` examples with 2024 week 10/11 | private-test replay examples | n/a (documentation) |
| 27 | `web/lib/content.ts` `CONTENT_AS_OF` 2026-11-04 | static copy date | n/a |
| 28 | `ffml predict` replay mode writes to the public store by default (`ffml/sim/snapshot.py`) | frozen path, not editable here | **open** (owner/orchestrator item below); the API ignores and reports a replay there |

## What changed

API (commit 57a38d5):
- `api/services/season.py`: rewritten. `SeasonService` asks Sleeper
  (`app.state.sleeper`, the process-wide client and its token bucket) with a
  5 s deadline and one call in flight at a time, resolves the schedule with
  `ffml.season.current_state` plus the snapshot id, compares season, season
  type and (in the regular season) week, and returns `source` and
  `cross_check`. Disagreements are logged at WARNING once per distinct
  disagreement; Sleeper failures at WARNING. Cache 180 s, 60 s when degraded,
  single-flight.
- `api/settings.py`: `season_sleeper_enabled`, `season_ttl_seconds`,
  `season_sleeper_timeout_seconds`, `api_test_replays_dir`
  (`test_replays_dir` property), `TEST_REPLAYS_DIRNAME`.
- `api/main.py`: season service wiring; `report_test_sources` logs, at
  startup, test-only settings production ignores and any replay left in the
  public store.
- `api/approval.py`: `replay_roots` (private store and `replays_test_only`,
  never the public store), pinned run must be a replay, `test_data_label`,
  `public_store_replays`; production gate unchanged.
- `api/envelope.py`, `api/routers/meta.py`: test-data label first in
  `notices`, in `run`, and at the top of `/meta/approval` and
  `/meta/run-info`.
- Tests: `tests/api/test_season.py` (new, 20 tests: agree, disagree and its
  log, season mismatch, preseason, Sleeper down, hung Sleeper cut by the
  deadline, no schedule, neither, disabled, cache, degraded TTL, wiring
  through the shared client with respx, no network unless opted in,
  production season ignores replays and test settings, startup report,
  test-data labels); `tests/api/conftest.py` (Sleeper off by default in
  tests, `test_replays` dir); `tests/api/test_gate.py` (replays in the
  test-only folder; public-store replays ignored; pinned run must be a
  replay); replay paths in `tests/api/test_projections.py` and
  `tests/api/test_qa_performance.py`.

Web (commits 32c2b7a, 4d1052a):
- `web/components/status/SeasonContext.tsx` (new, in `web/app/layout.tsx`):
  "Now: 2026 season, week 9 (in progress) · From Sleeper, matching the
  nflverse schedule." on every page.
- `web/lib/season.ts` (new): `seasonLabel`, `seasonSourceText`, `testDataLabel`.
- `web/components/states/States.tsx`, `web/components/projections/GatedView.tsx`:
  "Current week: 2026 season, week 9 (in progress)" on every gated view.
- `web/components/status/TestDataLabel.tsx` (new),
  `web/components/status/ApprovalBanner.tsx`,
  `web/components/projections/ProjectionNotice.tsx`: the TEST DATA label next
  to the not-approved label (banner on every page, notice on every
  projection view), not repeated in the notice list.
- `web/components/home/HomeStatus.tsx`: season line from `seasonLabel`.
- `web/components/about/ModelCardView.tsx`: registry table without version ids.
- `web/lib/api/types.ts`: v1.5 types. `web/mock/app.mjs`, `web/mock/fixtures.mjs`:
  v1.5 shapes.
- Tests: `web/tests/unit/season.test.tsx` (new, 10 tests);
  `web/tests/e2e/helpers.ts` (`ALL_PAGES`, `expectCurrentSeasonOnly`,
  `expectedTestDataLabel`; the import picks the D-020 league when the user has
  several), `web/tests/e2e/production.spec.ts` (4 new tests: every page shows
  2026 and no 2024, with and without the imported league; gated context; meta
  endpoints), `web/tests/e2e/private-test.spec.ts` (TEST DATA label on the
  banner, the projections notice and the first notice; every projection view
  checks it), `web/playwright.real.config.ts` (`E2E_SLEEPER_LEAGUE`, default
  1389750342958223360).

Docs (commit 5bcf985): `docs/API.md` contract v1.5 (settings, section 3.3
replay locations and test-data label, 3.4 envelope example, section 5
`/meta/season`, changelog); `docs/RUNBOOK.md` section 18 (season source,
refresh, league re-sync, test-only replays).

Data (not in git):
- `data/predictions/season=2024/...` moved to
  `data/predictions_private/replays_test_only/season=2024/week=10/`, and
  `data/predictions_private/season=2024/...` to
  `.../replays_test_only/season=2024/week=11/` (moves, not deletes; SHA-256 of
  every file identical before and after; a README.txt explains the folder).
  `data/predictions` is now empty.

## Data refresh (2026-11-10 02:36-02:39 UTC)

`ffml refresh --step all` (seasons 2016-2026, every step OK; validation PASS,
11 passed, 1 warned (sleeper_id coverage), 0 failed;
`reports/validation_20261110T023829Z.md`). New snapshots, none deleted:

| Tier | Snapshot ids |
| --- | --- |
| raw | pbp 20261110T023648Z, player_stats 20261110T023717Z, team_stats 20261110T023721Z, schedules 20261110T023724Z, rosters_weekly 20261110T023726Z, injuries 20261110T023729Z, depth_charts 20261110T023732Z, snap_counts 20261110T023735Z, nextgen_passing 20261110T023738Z, nextgen_rushing 20261110T023741Z, nextgen_receiving 20261110T023743Z, ff_opportunity 20261110T023745Z, ftn_charting 20261110T023749Z, pfr_advstats_rec 20261110T023751Z, pfr_advstats_rush 20261110T023753Z, pfr_advstats_pass 20261110T023756Z, players 20261110T023800Z, ff_playerids 20261110T023800Z, teams 20261110T023802Z, ff_rankings_all 20261110T023803Z (restricted, pipeline only; never read by the app) |
| sleeper | sleeper_players 20261110 |
| processed | dim_game, dim_player, dim_team 20261110T023815Z; fact_dst_game, fact_team_game 20261110T023817Z; fact_player_game 20261110T023820Z; fact_injury_report 20261110T023823Z; fact_depth_chart 20261110T023824Z; fact_roster_week 20261110T023825Z; sleeper_players 20261110T023826Z |
| features | player_week 20261110T023843Z, team_week 20261110T023845Z, dst_week 20261110T023846Z, player_week_depth_same_week 20261110T023849Z |

Week 9: **14 of 15 games final** in the schedule snapshot; the Monday night
game BUF at MIN (2026_09_BUF_MIN, kickoff 2026-11-10 01:15 UTC) was in
progress. `fact_player_game` has 2026 weeks 1-9 (week 9: 481 rows, 14
games); injuries and weekly rosters cover 2026 weeks 1-9. The schedule
resolves to 2026, regular, week 9 in progress, weeks 1-8 complete, 135 of 272
games final.

League re-sync through the API (`POST /leagues/ea84ce96-14f4-434f-83e0-0d464b065a7d/sync`,
production API on `data/app.db`, 2026-11-10 03:04:40 UTC): HTTP 200, "Bgr
fantasy leuge", Sleeper `1389750342958223360`, season 2026, 8 teams, 119
rostered players, all resolved to gsis ids; my team kept ("Team ethanye19",
15 players). Sleeper also lists a second 2026 league for `ethanye19`
("max ethan avneesh nga balls", 10 teams, drafting); it was not imported.

## Live run (production API on 127.0.0.1:8000, web on 127.0.0.1:3000)

`GET /meta/season` at 2026-11-10 03:04:21 UTC:

```json
{"season": 2026, "week": 9, "season_type": "regular", "mode": "regular",
 "projection_week": 9, "week_in_progress": true, "last_completed_week": 8,
 "next_kickoff_utc": "2026-11-13T01:15:00+00:00", "offseason": false, "banner": null,
 "source": "sleeper", "as_of": "2026-11-10T03:04:21.431747+00:00",
 "cross_check": {"status": "agree", "differences": [],
   "message": "Sleeper and the nflverse schedule agree.",
   "sleeper": {"season": 2026, "season_type": "regular", "week": 9, "display_week": 9,
               "raw_season_type": "regular"},
   "schedule": {"season": 2026, "season_type": "regular", "week": 9, "week_in_progress": true,
                "last_completed_week": 8, "games_final": 135, "games_total": 272,
                "snapshot_id": "20261110T023724Z"}}}
```

Other endpoints (production):
- `/meta/health`: ok, environment production, database ok, projections off.
  No startup warning (no test setting set, no replay in the public store).
- `/meta/run-info`: `run: null`, `test_data_label: null`, "No approved model...".
- `/meta/approval`: `serving: false`, `reason: no_live_model`, `run: null`.
- `/meta/model-card`: production, not approved, not serving; `models` lists
  chain_v1 `rejected`. Its `version` field is
  `chain_v1-primary-c2024w19-20261102T222634Z` (the registry id; the page no
  longer shows it).
- `/leagues`, `/leagues/{id}`, sync: season 2026, no "2024" in the body.
- `/players?search=jefferson`: Justin Jefferson (WR, MIN), Van Jefferson (WR,
  WAS), Jermar Jefferson (RB, ARI).
- `/sleeper/users/ethanye19/leagues`: season 2026, two leagues.
- `/projections`, `/leagues/{id}/projections`: 503 `no_approved_model`.

Every page, production, with the synced league selected (Playwright, body text):

| Page | Season shown | Gated context | "2024" on page |
| --- | --- | --- | --- |
| Home `/` | 2026 season, week 9 (in progress) | - | none |
| Setup `/setup` | 2026 season, week 9 (in progress) | - | none |
| Projections | 2026 season, week 9 (in progress) | Current week: 2026 season, week 9 (in progress) | none |
| Start/sit | same | same | none |
| Waivers | same | same | none |
| Trade | same | same | none |
| Rankings | same | same | none |
| About | 2026 season, week 9 (in progress) | - | none |
| About / model card | same | - | none |
| About / limitations | same | - | none |
| About / attribution | same | - | none |

Private test (API on 8001 with a scratch database, web on 3001): the API
served the replay `2024w11-20261104T225732Z-replay` from
`replays_test_only` (`reason: private_test_replay`); `/meta/approval`,
`/meta/run-info`, every envelope `run` and the first notice carry "TEST DATA:
2024 week 11 replay, not approved, not a recommendation"; header
`X-FFML-Approval: not-approved`; every page shows the label in the banner
(and again on the projections and rankings notices), next to the "NOT
APPROVED" pill, with "Now: 2026 season, week 9 (in progress)" as context.

All four servers were stopped afterwards (no listener on 8000, 8001, 3000, 3001).

## Tests

All on 2026-11-10 after the changes.

- `pytest -q tests/api tests/compliance`: 382 passed (4 min 50 s).
- `ruff check api tests/api`: all checks passed.
- Web: `npm run lint` clean, `npm run typecheck` clean, `npm test` 116 passed
  (13 files).
- Playwright, contract mock (`npm run e2e`): 33 passed.
- Playwright, real API (`npm run e2e:real`, both modes, read-only Sleeper import of
  `ethanye19`): 33 passed. The first two runs had 2 private-test failures that
  were not caused by D-124 but by the harness: the import took the first of
  the user's two 2026 Sleeper leagues (a new one, still drafting, with empty
  rosters), back-to-back imports hit the API's Sleeper limit (QA-F03, 10/min,
  burst 5), and the projections test clicked "Why" before the "All" reload.
  Fixed in 4d1052a (import league 1389750342958223360, wait out a 429, wait
  for the reload); the last real run before this one was 2026-11-04, before
  the rate limiter.
- Playwright, contract mock, rerun after 4d1052a: 33 passed.

## Not done / open

- `ffml predict` replay mode writes to the public store by default
  (`ffml/sim/snapshot.py`, frozen path). The API now ignores and reports a
  replay there, but a new replay should be written to (or moved into)
  `data/predictions_private/replays_test_only/`.
- The Monday night game of week 9 was not final at refresh time. The next
  refresh (the live-test runner refreshes before the week 10 build, or
  `ffml refresh --step all` by hand) brings it in; until then `/meta/season`
  says week 9 in progress, which matches Sleeper. After a refresh on Tuesday,
  expect a short, logged `week` disagreement (schedule 10, Sleeper 9) until
  Sleeper moves to week 10; the schedule's week 10 is shown.
- `/meta/model-card` JSON still carries the registry `version` id with
  `c2024w19`; the page does not show it. Changing the registry is outside
  this task.
- These commits touch only `api/`, `web/`, `tests/api/` and `docs/`; if they
  land after the live-test commit X is chosen, each needs a change-log row
  (`scripts/livetest_changelog.py` adds rows for such paths).

## Decisions proposed

- None new. Implemented D-124 as logged, including its assumption for item 4.
  Judgment calls: Sleeper's preseason agrees with a schedule at week 1 with no
  final game; weeks are compared only in the regular season; the private
  2024 week 11 replay was also moved into `replays_test_only` so every replay
  is in one test-only folder; fixtures get a TEST DATA label too.

## Commits

| Commit | What |
| --- | --- |
| 57a38d5 | API: `/meta/season` from Sleeper, cross-checked with the schedule; replays test-only with the TEST DATA label; tests |
| 32c2b7a | Web: season and week on every page and gated view; TEST DATA label; model card without version ids; mock v1.5; unit and e2e tests |
| 5bcf985 | Docs: API contract v1.5, runbook section 18 |
| 4d1052a | Web e2e harness: D-020 league, Sleeper rate limit, reload wait |
| (this note) | Status note |

Nothing was pushed.
