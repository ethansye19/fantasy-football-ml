# Phase 4 / Backend Engineer / BE-4.1 to BE-4.8
Date: 2026-11-04
Scope: API contract, approval gate (D-097, D-099), app database, league
import, meta endpoints, and the projection-based services (projections,
start/sit, waivers, trades, rest of season) behind the gate. Phase 4 runs
as an owner exception (D-099): no model is approved, so the real app keeps
every projection feature off.

## Task list (as briefed)

0. API contract (docs/API.md), FastAPI skeleton, `/meta/health`,
   `/meta/approval`, 503 gate on projection routes, tests.
1. BE-4.1 SQLAlchemy models and Alembic migrations (SQLite tested locally,
   PostgreSQL in CI).
2. BE-4.2 league import: Sleeper username or league id, scoring and roster
   normalization, roster sync, my team, manual league form.
3. BE-4.8 attribution, model card, health, run info, offseason mode,
   approval status.
4. BE-4.3 to BE-4.7 projections, start/sit, waivers, trade analyzer, ROS,
   with request-time scoring, caching, plain-language drivers, "no
   guarantees" language and the gate.

## Done

| Commit | What |
| --- | --- |
| 5202a8f | API contract `docs/API.md`; `api/main.py`, `api/settings.py`, `api/approval.py`, `api/gate.py`, `api/errors.py`, `api/envelope.py`; `/meta/health`, `/meta/approval`; 503 gate on all 7 projection routes |
| 53d34f0 | Coordinator correction: production requires manifest `environment == "live"` (missing key accepted for older manifests), refuses `private_test` and `replay`; contract v1.1 |
| a75e4f5 | `pythonpath = ["."]` in `[tool.pytest.ini_options]` (coordinator request; plain `pytest -q tests/api` passes) |
| ab1a62d | BE-4.1 `api/db/models.py`, `api/db/session.py`, `api/migrations/` (0001_initial), `api/alembic.ini`; startup migration |
| 116be3f | BE-4.2 `api/services/leagues.py`, `api/services/players.py`, `api/routers/leagues.py`, `api/deps.py` |
| 052b6e1 | BE-4.8 `/meta/attribution`, `/meta/model-card`, `/meta/run-info`, `/meta/season` (`api/services/meta_content.py`, `api/services/season.py`) |
| df6f98d | BE-4.3 to BE-4.7 `api/services/projection.py`, `recommend.py`, `lineup.py`, `explain.py`, `api/routers/projections.py`; API contract v1.2 |
| a183f5f | `Dockerfile.api`, `docker-compose.yml` (api + postgres), not verified locally |

### Approval gate (how D-099 is enforced)

- `FFML_APP_ENV`: `production` (default; any unrecognised value is
  production) or `private_test`.
- The gate lives in a custom `APIRoute` (`api/gate.py`) and runs before
  body parsing. In production every request to a projection route gets the
  fixed 503 body, whether it is valid or not:
  `{"status": "no_approved_model", "message": "No approved model. Projections are off until a model passes its pre-registered test."}`.
- Production serves only when all of these hold:
  - `registry.get_live()` returns a live entry with an evaluator report;
  - `data/predictions/LATEST` resolves to a complete run;
  - the manifest has `approval_status == "approved"` (top level or `extra`);
  - the manifest has `environment == "live"` (or the key is missing) and is
    not a replay;
  - its `model_version` equals the live version;
  - any `approval_status`, `environment` or `model_version` on `LATEST`
    itself agrees.
- Production never reads the private store, replays or fixtures.
- private_test serves, in order: the private store's `LATEST`, a pinned or
  newest replay (private and public stores), then the fixture store.
- In private_test, every response has `X-FFML-Approval: not-approved` and
  every projection body has `approval_status`/`label` = `"not approved"`,
  whatever the manifest says. Tests assert that no string value `approved`
  appears anywhere in any private_test response.

### Services

- **Request-time scoring.** Scoring is applied to `player_sims` and
  `ros_sims` at request time and cached per `(run, scoring hash)`, with one
  run in memory.
- **Summary conventions.** These are the chain's (`ffml.models.chain.summary`):
  ranks are computed over all draws, and quantiles, boom and bust are taken
  conditional on active. With the PPR preset on the real 2024 week 10
  replay, the request-time values equal the ML `player_summary` (median
  absolute difference 0) except for the 13 forced-active entities.
- **Waivers and trades.** Both use optimal weekly lineups solved exactly
  with `scipy.optimize.linear_sum_assignment` over league slots and flex
  eligibility.
- **Trade value.** Value is measured over league replacement level (the
  best free agent, with a rank fallback) and is clipped at zero per player.
- **Disclaimers.** Start/sit, waivers and trades carry the "No guarantees"
  disclaimer.
- **Low confidence.** K and DST boom/bust are flagged `low` (D-089).
- **Restricted data.** No FantasyPros/ECR access, enforced by a source-scan
  test and a `sys.modules` check. No holdout unlock is used anywhere.

## Evidence

- Lint: `.venv/Scripts/ruff check api tests/api` -> `All checks passed!`
- Tests: `PYTHONUTF8=1 .venv/Scripts/pytest -q tests/api` ->
  `141 passed, 2 warnings in 71.29s`. Breakdown:
  - test_gate 61, including every projection route in production for each
    of 16 missing or malformed conditions, the live-environment case,
    non-live environments, and private_test labels and source order;
  - test_db 5;
  - test_leagues 27;
  - test_meta 20;
  - test_projections 28.
- Full suite still collects with the new pytest path:
  `pytest -q --collect-only` -> 531 tests.
- Live Sleeper check (manual, real network; tests use recorded fixtures
  through respx): `POST /leagues/import/sleeper {"username": "ethanye19",
  "season": 2026}` -> 201 in 0.69 s.
  - League: "Bgr fantasy leuge", 8 teams, PPR-like.
  - Unsupported keys reported: `def_st_fum_rec`, `st_ff`, `st_fum_rec`.
  - Players: 118 rostered, 0 unmapped, 79 starters; my team "Team ethanye19".
  - Sync -> 200.
- Production against the real repo state:
  - `/leagues/{id}/projections` returns 503 with the fixed body;
  - `/meta/approval` gives `reason: no_live_model`;
  - `/meta/model-card` lists chain_v1 as candidate (the registry has since
    moved it to rejected, 71bc523).
- Replay for private_test: `run_predict(season=2024, week=11, replay=True,
  model_dir=artifacts/models/replay/chain_v1-primary-c2024w10,
  root=private_predictions_dir)`.
  - Output: `data/predictions_private/season=2024/week=11/run=2024w11-20261104T225732Z-replay`
    in 17.6 s.
  - Manifest: `environment: replay`, `approval_status: not approved`.
  - History runs through 2024 only; FFML_HOLDOUT_UNLOCK was never set.
  - The CLI has no `--replay` flag, so the Python API was used.
- Timings on that replay with the imported league (private_test):

  | Route | Time |
  | --- | --- |
  | projections, cold (load + score) | 524 ms |
  | projections, warm | 7 ms median, 8 ms max (target under 300 ms) |
  | start/sit | 7 ms |
  | waivers (25 options) | 107 ms |
  | trade | 10 ms |
  | ROS | 9 ms |

  `test_warm_projections_under_300ms_on_real_replay` asserts the 300 ms
  target on the 567-entity x 2,000-simulation week 10 replay; it is skipped
  when the local snapshot is absent, as in CI.

## Not done / open

- **Gate 4 stays open (D-099).** No approved model exists, so production
  returns 503 on every projection route. The approved path is tested only
  with synthetic snapshots.
- **PostgreSQL** has not been run locally (no server here). `test_db` and
  every API test switch to PostgreSQL through `FFML_TEST_DATABASE_URL`, in
  the CI job proposed below.
- **Docker image and compose** are unverified (no Docker locally); see the
  proposed CI job.
- **Calibration** is not applied at request time. Quantiles are raw
  simulation quantiles under the league scoring, and responses say so in
  `scoring.quantiles`. Needed before a production launch (proposal P-2).
- **Forced-active players.** Conditional numbers for players active in
  fewer than 100 simulations differ from the chain's forced-active pass.
  They are flagged `low_sample` (proposal P-1).
- **DB-backed tables.** `projection_runs` and `league_projection_cache`
  exist in the schema but are not written; caching is in memory only.
- **Sessions.** The cookie session (D-011) is not built; "my team" is the
  `is_me` flag.
- **QA-4.1 items** are not built here: rate limiting and security headers
  beyond `nosniff`/`no-store`. CORS is an allowlist (`FFML_CORS_ORIGINS`).
- **Replay context.** In private_test, replays use 2024 games with 2026
  rosters. Notices say so, and waiver and trade output reflects 2024
  players (for example 2024 starters who are free agents in 2026).
- **Stale schedule.** `/meta/season` resolves 2026 week 8 "in progress"
  because the raw schedules snapshot is from 2026-11-01 (a data refresh
  item).
- **Model card.** The card text is static and must be updated when v2
  passes or fails its live test.

## Decisions proposed

- **D-101 (proposal)**: `FFML_APP_ENV` semantics as implemented:
  - production serves only the conditions above;
  - private_test sources come in the order private store, replay, fixture;
  - an unrecognised value counts as production;
  - the gate runs before request parsing.
- **D-102 (proposal)**: request-time summaries follow the chain's
  conditional-on-active conventions. Lineup values use the unconditional
  mean. Trade value per player is `max(0, ROS mean - replacement)`.
- **D-103 (proposal)**: rest-of-season totals include the run week by
  default (`include_current_week=true`). Simulation i of the week is added
  to ROS simulation i (independent draws across those two files).

## Proposals for other roles

**P-CI (QA, `.github/workflows/ci.yml`).** Add these two jobs next to
`python`:

```yaml
  api-postgres:
    runs-on: ubuntu-latest
    services:
      postgres:
        image: postgres:16-alpine
        env:
          POSTGRES_USER: ffml
          POSTGRES_PASSWORD: ffml
          POSTGRES_DB: ffml_test
        ports: ["5432:5432"]
        options: >-
          --health-cmd "pg_isready -U ffml -d ffml_test"
          --health-interval 5s --health-timeout 3s --health-retries 20
    env:
      PYTHONUTF8: "1"
      FFML_OFFLINE: "1"
      FFML_TEST_DATABASE_URL: postgresql+psycopg://ffml:ffml@localhost:5432/ffml_test
    steps:
      - uses: actions/checkout@v4
      - uses: actions/setup-python@v5
        with:
          python-version: "3.11"
          cache: pip
      - name: Install
        run: python -m pip install -e ".[api,dev]"
      - name: API tests on PostgreSQL
        run: pytest -q tests/api

  api-image:
    runs-on: ubuntu-latest
    steps:
      - uses: actions/checkout@v4
      - name: Build and start api + postgres
        run: |
          mkdir -p data artifacts reports
          docker compose up -d --build --wait
      - name: Smoke (production rules, no approved model)
        run: |
          curl -fsS http://localhost:8000/meta/health
          test "$(curl -s -o /dev/null -w '%{http_code}' http://localhost:8000/projections)" = "503"
          curl -s http://localhost:8000/projections | grep -q no_approved_model
      - if: always()
        run: docker compose logs api && docker compose down -v
```

**ML Engineer (ffml/).**
- **P-1.** Store the forced-active simulations (`forced_sims.parquet` plus
  the forced ids) in prediction snapshots. Request-time conditional
  summaries could then match the chain for players with fewer than 100
  active simulations (13 entities in 2024 week 10).
- **P-2.** Expose request-time calibration for arbitrary league scoring.
  With the v3 tie-aware method, a calibrated quantile is the raw quantile at
  level `G^-1(tau)`. The manifest (or a small JSON next to it) could carry
  the frozen per-position level map per reference scoring, for example
  `{"ppr": {"QB": {"q10": 0.07, "q50": 0.5, "q90": 0.93}}}`, plus the
  per-position boom/bust isotonic maps. The API would take
  `np.quantile(points, level)` for the league's nearest preset (D-072).
  Without this, a production launch would serve uncalibrated quantiles,
  which is not what the evaluator approves.
- **P-3.** Add a CLI flag for replays (`ffml predict --replay --season
  --week --model-dir`). It is currently Python-only (`run_predict(...,
  replay=True)`).

**Data Engineer.** Refresh the raw schedules snapshot (and Sleeper
players) nightly, so `/meta/season` and injury status are current.

**Frontend Engineer.** Contract v1.2 in docs/API.md. Notes:
- Every gated response has the `X-FFML-Approval` header; it is listed in
  CORS `expose_headers`.
- Show `label` and `notices` whenever `approval_status` is not `"approved"`.
- A 503 with `status: "no_approved_model"` is the production empty state.

## Dependencies

None added. Everything used is already in the `api` extra (fastapi,
uvicorn, sqlalchemy, alembic, psycopg, itsdangerous) or in `dev` (respx);
scipy and httpx are core. The only pyproject edit was the pytest
`pythonpath` line requested by the coordinator.

## Next

- Run the proposed CI jobs and fix any PostgreSQL type differences that
  `compare_metadata` reports.
- When chain_v2 has a private-test or approved run:
  - point private_test at it (private store `LATEST`) and re-check timings;
  - wire calibration (P-2) before any production serving;
  - run the end-to-end Gate 4 check.
