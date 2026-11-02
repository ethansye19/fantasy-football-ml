# Build Plan

Phase 1 (Plan) is complete as of 2026-11-01. Phase 2 (Data) passed Gate 2 on
2026-11-02 (docs/status/gate-2.md). Phases 3-6 start only after the prior
gate passes. Task ids use role prefixes: DE data, FE features, ML
models, EV evaluator, BE backend, UI frontend, QA quality and security, OR
orchestrator.

## Context that shapes the plan

- Today is Sunday 2026-11-01, week 8 of the 2026 season is in progress.
  Week 9 kicks off Thursday 2026-11-05. The first live deliverable is week 9
  projections plus rest-of-season rankings for weeks 9-18.
- Historical data 2016-2025 is complete and schema-stable; 2026 updates nightly.
- Machine: Windows 11, 8 cores, 32 GB RAM, Python 3.11 venv at `.venv`.
  Node.js, Docker, and PostgreSQL are not installed (see DECISIONS, open
  questions). The plan works without Docker locally and uses CI for container
  verification.

## Phase 2: Data (reproducible pipeline with passing tests)

| id | task | deliverable |
| --- | --- | --- |
| OR-2.1 | scaffold `pyproject.toml` (ffml, api extras, dev extras), pinned lock, `ffml` CLI skeleton, `PYTHONUTF8` handling, pre-commit with ruff | repo builds, `ffml --help` |
| DE-2.1 | snapshot store: write raw Parquet with manifests, LATEST pointers, retention, schema hash | `ffml/data/store.py`, tests |
| DE-2.2 | nflreadpy ingestors for all datasets in DATA_SOURCES.md with explicit season ranges, `cache_mode="off"`, retries, dtype casts (ff_opportunity season and week) | `ffml ingest --seasons 2016-2026` |
| DE-2.3 | Sleeper client: state, user, leagues, league, rosters, users, matchups, transactions, players map (daily cache), rate limiting and retries | `ffml/data/sleeper.py`, tests with recorded fixtures |
| DE-2.4 | processed tables: dim_player (crosswalk union of ff_playerids, rosters, Sleeper gsis_id, name match, manual overrides), dim_team with aliases, dim_game with kickoff_utc and status, fact_player_game (pbp-derived situational counts, snap join via pfr_id, ffopportunity, NGS), fact_team_game with allowed_* mirrors, fact_dst_game, fact_injury_report, fact_depth_chart (both formats), fact_roster_week, sleeper_players, fact_ecr_weekly (eval only) | `ffml process` |
| DE-2.5 | validation suite from DATA_SCHEMA.md section 6 with a markdown report | `ffml validate` exits non-zero on failure |
| DE-2.6 | DuckDB warehouse views over LATEST | `ffml warehouse` |
| DE-2.7 | season/week resolver with tests for preseason, in-progress week, bye weeks, postseason, offseason | `ffml/config.py` |
| DE-2.8 | data dictionary generated from processed schemas | `docs/DATA_DICTIONARY.md` |
| QA-2.1 | tests for missing data, duplicate players, ID mismatches, team changes, late schema changes (fixtures built from small real slices) | `tests/data/` |

Gate 2 checklist: `ffml ingest`, `process`, `validate` run end to end from a
clean `data/` directory and are repeatable (same manifests except timestamps);
`pytest tests/data` passes; coverage of the crosswalk at or above 98% for
2026 skill players; data dictionary committed; status notes with row counts.

## Phase 3: Models (baselines, walk-forward backtests, evaluator report)

| id | task | deliverable |
| --- | --- | --- |
| FE-3.1 | feature builders per group in FEATURES.md with cutoff semantics, windows, shrinkage, team-change resets, depth-chart snapshot selection | `ffml features --through 2026-08` |
| FE-3.2 | leakage test suite (six tests in FEATURES.md section J) | `tests/features/` |
| ML-3.1 | scoring engine: ScoringRules from Sleeper `scoring_settings` and manual input, applied to stat-line frames, with bonuses and position-specific rules; tests against Sleeper-computed points for a real league week | `ffml/scoring/` |
| ML-3.2 | baselines 1 and 2 and structural per-stage baselines (baseline 3, the restricted consensus reader, lives only in `ffml/eval/ecr_baseline.py`, D-051) | `ffml/models/baselines.py` |
| ML-3.3 | stage models (1a-1c, 2, 3, 4) with LightGBM, walk-forward training harness, hyperparameter search on walk-forward folds only | `ffml train`, `ffml backtest` |
| ML-3.4 | Monte Carlo engine with game-level, team-level, Dirichlet share and per-touch draws; availability layer; K and DST; ROS mode | `ffml/sim/` |
| ML-3.5 | calibration (conformal offsets, dispersion tuning) on validation seasons | `ffml/models/calibration.py` |
| ML-3.6 | direct challenger model (quantile LightGBM) for the report | `ffml/models/challenger.py` |
| EV-3.1 | metrics module (MAE, pinball, coverage, calibration, Spearman, start/sit accuracy, lineup efficiency) with bootstrap intervals | `ffml/eval/metrics.py`, `tests/eval/` |
| EV-3.2 | leakage audit: rerun the leakage suite, shuffle tests, timestamp audit, holdout guard check, review of feature code for cutoff violations | `reports/evaluator_audit_<date>.md` |
| EV-3.3 | validation report on 2022-2024 for chain, challenger, baselines, by position and week, with ablations | `reports/backtest_<date>.md` |
| EV-3.4 | after the orchestrator freezes the candidate: single holdout run on 2025 and forward check on 2026 weeks 1-8; approval or rejection | `reports/evaluator_release_<candidate>.md`, `artifacts/models/REGISTRY.json` |
| OR-3.1 | model card draft with explicit baseline comparisons | `reports/MODEL_CARD.md` |

Gate 3 checklist: leakage suite green; backtest report present with all three
baselines; evaluator release note says APPROVED for a named model version
(baseline-only chain counts if LightGBM stages do not beat baselines); holdout
touched exactly once (guard log); model card states where baselines were not
beaten; week 9 predictions snapshot produced from the approved version.

## Phase 4: App (real model outputs, no placeholders)

| id | task | deliverable |
| --- | --- | --- |
| BE-4.1 | SQLAlchemy models and Alembic migrations for DATA_SCHEMA.md section 5; SQLite and PostgreSQL both tested | `api/db/` |
| BE-4.2 | league import: Sleeper username or league id, scoring and roster normalization, roster sync, "my team" selection; manual league form endpoint | `POST /leagues/import/sleeper`, `POST /leagues` |
| BE-4.3 | projection service: load `player_sims` for the published run, apply ScoringRules, quantiles, boom and bust via positional ranks per simulation, in-memory cache per run and scoring hash | `GET /leagues/{id}/projections?week=` |
| BE-4.4 | start/sit: compare 2-4 players, P(A beats B) from paired samples, drivers from the chain components in plain language | `POST /leagues/{id}/start-sit` |
| BE-4.5 | waivers: free agents for a league from Sleeper rosters, ranked by ROS value over the roster's replacement at each slot plus next-week points; drop candidates | `GET /leagues/{id}/waivers` |
| BE-4.6 | trade analyzer: ROS value over league replacement level, roster fit via optimal weekly lineups before and after, fairness band, explicit "no guarantees" language | `POST /leagues/{id}/trades/evaluate` |
| BE-4.7 | rest-of-season rankings per scoring with weekly refresh | `GET /leagues/{id}/ros` |
| BE-4.8 | attribution, model-card, health, run-info endpoints; offseason mode behaviour | `GET /meta/*` |
| UI-4.1 | app shell, responsive layout, theme, navigation, error boundary, offseason banner | `web/app/` |
| UI-4.2 | league setup: Sleeper import flow and manual settings form with validation | `/setup` |
| UI-4.3 | projections table with position tabs, distribution strip (floor, median, ceiling), boom and bust badges, filters, search | `/projections` |
| UI-4.4 | start/sit compare view with drivers | `/start-sit` |
| UI-4.5 | waivers view with add/drop pairs for my roster | `/waivers` |
| UI-4.6 | trade analyzer view | `/trade` |
| UI-4.7 | ROS rankings view | `/rankings` |
| UI-4.8 | about, attribution, model card and limitations pages; empty and error states for every page | `/about/*` |
| UI-4.9 | Playwright smoke tests against the real API | `web/tests/` |
| QA-4.1 | API tests (pytest + httpx), input validation, CORS, rate limits, secrets scan, dependency audit, performance check (projections endpoint under 300 ms warm) | `tests/api/`, `docs/SECURITY.md` |

Gate 4 checklist: a real Sleeper league imports; every page renders real
week 9 numbers from the approved run; the API refuses to serve without a
published run; Playwright smoke suite green; QA findings closed or logged.

## Phase 5: Automation

| id | task | deliverable |
| --- | --- | --- |
| DE-5.1 | `ffml refresh` orchestration command with idempotent steps and a run log | `scripts/refresh.py` |
| DE-5.2 | scheduler worker (APScheduler): post-game update Tue 09:00 UTC and Thu 09:00 UTC (stat corrections), injury and depth-chart refresh daily 12:00 UTC plus Fri, Sat, Sun 18:00 UTC, data-quality checks after every ingest, retraining check Thu after update, Sleeper players map daily | `Dockerfile.pipeline`, `scheduler` service |
| DE-5.3 | GitHub Actions equivalents (`data_quality.yml`, `weekly_refresh.yml`, `injury_refresh.yml`, `retrain_check.yml`) | `.github/workflows/` |
| QA-5.1 | failure alerts via webhook or SMTP with a test command; alert on data-quality failure, refresh failure, stale predictions (older than 8 days in season) | `scripts/alert.py` |
| OR-5.1 | weekly operations runbook | `docs/RUNBOOK.md` |

Gate 5 checklist: each job executed once manually with logs attached; a forced
failure produces an alert; stale-prediction alert tested with a clock override.

## Phase 6: Release

| id | task | deliverable |
| --- | --- | --- |
| QA-6.1 | end-to-end tests: ingest small slice, process, predict, serve, import league, render pages | `ci.yml` e2e job |
| BE-6.1 | docker compose stack (web, api, scheduler, postgres, volume), health checks | `docker-compose.yml` |
| OR-6.1 | deployment guide and setup guide; fresh-install verification on a clean CI runner (`docker compose up`, health, smoke) | `docs/DEPLOYMENT.md`, `docs/SETUP.md` |
| OR-6.2 | final model card, backtest report links, attribution page review, prioritized limitations and next improvements | `reports/MODEL_CARD.md`, `docs/LIMITATIONS.md` |

Gate 6 checklist: e2e green in CI; fresh install verified with logs; all
deliverables in the README table exist.

## Stretch (after the MVP gate)

Live draft assistant (offseason feature), ESPN and Yahoo import, paper-only
prop comparison (no betting, no guarantees), user accounts.

## Risk register

| Risk | Mitigation |
| --- | --- |
| nflverse schema change mid-build | schema hash tests, loader isolation, dtype casts at the boundary |
| LightGBM stages do not beat baselines | ship the structural baseline chain; report honestly; keep the chain for scoring flexibility |
| Consensus baseline licensing | evaluation-only, never served; can be dropped without affecting the product |
| Injury timestamp ambiguity | two labelled configurations; availability layer uses the latest report live |
| No Node or Docker locally | ask for Node install; Docker verification in CI; SQLite locally |
| Partial-week states (today) | resolver treats in-progress weeks explicitly; tests cover them |
| Offseason arrives mid-project (after 2027-01-10) | offseason mode is built in Phase 4, not deferred |
