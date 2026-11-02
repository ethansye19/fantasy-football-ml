# Architecture

## 1. System context

```mermaid
flowchart LR
    subgraph Sources
        NV[nflverse releases<br/>via nflreadpy]
        SL[Sleeper API]
    end
    subgraph Pipeline["Python pipeline (package ffml)"]
        ING[Ingest + snapshot] --> VAL[Validate] --> PROC[Process] --> FEAT[Point-in-time features]
        FEAT --> TRAIN[Train + backtest] --> PRED[Predict + simulate]
    end
    subgraph Storage
        LAKE[(Versioned Parquet lake<br/>raw / processed / features / predictions)]
        DUCK[(DuckDB views)]
        PG[(PostgreSQL<br/>leagues, rosters, runs)]
        ART[(Model artifacts)]
    end
    API[FastAPI service<br/>scoring engine + recommendations]
    WEB[Next.js web app]
    SCHED[Scheduler worker<br/>refresh jobs + alerts]
    USER((Fantasy manager))

    NV --> ING
    SL --> API
    ING --> LAKE
    PROC --> LAKE
    PRED --> LAKE
    TRAIN --> ART
    LAKE --> DUCK
    LAKE --> API
    ART --> PRED
    API <--> PG
    WEB --> API
    USER --> WEB
    SCHED --> Pipeline
    SCHED --> API
```

## 2. Components

| Component | Technology | Responsibility |
| --- | --- | --- |
| `ffml` pipeline | Python 3.11, Polars, DuckDB, PyArrow, scikit-learn, LightGBM, NumPy | ingestion with snapshot manifests, validation suite, processed tables, point-in-time features, baselines and models, walk-forward backtests, Monte Carlo simulation, prediction publishing |
| Parquet lake | Parquet files under `data/` (not in git), `snapshot=` partitions, manifests | reproducible raw and processed data; every run records input snapshot ids |
| DuckDB | single file `data/warehouse.duckdb`, views over latest snapshots | ad-hoc analysis, backtest reporting, feature QA; disposable |
| API | FastAPI, SQLAlchemy 2, Alembic, Pydantic v2 | league import (Sleeper) and manual setup, scoring engine, projection service, start/sit, waivers, trade analyzer, rest-of-season rankings, attribution and model-card endpoints |
| App database | PostgreSQL in deployment and CI; SQLite for local development (same SQLAlchemy models) | leagues, teams, rosters, scoring and roster settings, projection run registry, per-league caches |
| Web | Next.js (App Router), TypeScript, Tailwind CSS, lightweight SVG charts | responsive UI with explanations, empty and error states, offseason mode |
| Scheduler | APScheduler worker container; equivalent GitHub Actions cron workflows | post-game updates, injury and depth-chart refresh, retraining checks, data-quality checks, failure alerts |
| CI/CD | GitHub Actions, Docker, docker compose | lint, unit and integration tests, Playwright end-to-end tests, image builds, fresh-install verification on a clean runner |

## 3. Pipeline DAG

```mermaid
flowchart TD
    A[ingest nflverse datasets<br/>pbp, player_stats, team_stats, schedules,<br/>rosters_weekly, injuries, depth_charts, snap_counts,<br/>nextgen_stats, ff_opportunity, players, ff_playerids] --> B[raw snapshot + manifest]
    A2[ingest Sleeper players map daily] --> B
    B --> C[validation suite<br/>schema fingerprint, nulls, duplicates,<br/>ID coverage, team changes, week completeness]
    C -->|pass| D[processed tables<br/>dim_player, dim_team, dim_game,<br/>fact_player_game, fact_team_game,<br/>fact_injury_report, fact_depth_chart, fact_roster_week]
    C -->|fail| X[alert + halt]
    D --> E[feature sets per cutoff<br/>team_env, opportunity, efficiency, touchdowns, availability]
    E --> F[train stage models<br/>walk-forward by season and week]
    F --> G[backtest report + evaluator audit]
    E --> H[predict next week and rest of season]
    H --> I[Monte Carlo simulation<br/>game script, Dirichlet shares, per-touch outcomes]
    I --> J[predictions snapshot<br/>player_sims, player_summary, team_env]
    J --> K[API loads samples;<br/>league scoring applied at request time]
```

## 4. Model chain (football before fantasy)

```mermaid
flowchart LR
    T[Team plays<br/>and pass/run mix] --> O[Player opportunity share<br/>targets, carries, dropbacks,<br/>red-zone and goal-line]
    O --> E[Efficiency<br/>catch rate, yards per target,<br/>yards per carry, YAC, aDOT]
    E --> TD[Touchdowns<br/>team TD expectation x player share]
    TD --> SIM[Monte Carlo stat lines<br/>correlated within game and team]
    SIM --> FP[Fantasy points<br/>any scoring rules, no retraining]
```

Every stage emits a distribution, not a point. The simulation threads shared
game-level draws (pace, pass rate, total, margin) through both teams so that a
QB and WR on the same team move together and opposing defenses pull the other
way. Teammates draw opportunity shares from a Dirichlet around predicted
shares, which gives negative correlation among them. Scoring rules are applied
to the simulated stat lines at request time, so any league configuration works
without retraining. Details are in MODELING.md.

## 5. Request flow for a projection with custom scoring

```mermaid
sequenceDiagram
    participant W as Web
    participant A as API
    participant P as Projection service
    participant L as Parquet lake
    participant DB as PostgreSQL
    W->>A: GET /leagues/{id}/projections?week=9
    A->>DB: league scoring_settings, roster_positions, rosters
    A->>P: score(run_id, week, scoring)
    P->>L: load player_sims (cached in memory per run)
    P->>P: apply scoring vector to stat-line samples<br/>quantiles, boom/bust, positional ranks per sim
    P-->>A: per-player distributions + drivers
    A-->>W: JSON with floor / median / ceiling / boom / bust / explanations
```

Sample matrices are small (about 600 players x 2,000 samples x 25 stat
columns per week, roughly 15 MB in Parquet). Scoring is a vectorized dot
product and finishes in milliseconds.

## 6. Deployment topology

```mermaid
flowchart TB
    subgraph host["Single host (docker compose)"]
        web[web: Next.js]
        api[api: FastAPI + uvicorn]
        sched[scheduler: APScheduler worker]
        pg[(postgres)]
        vol[(shared volume: data/, artifacts/)]
    end
    web --> api
    api --> pg
    api --> vol
    sched --> vol
    sched --> pg
    sched -. alerts .-> hook[Webhook / email]
```

Local development runs the API and web app directly (SQLite app database),
with the same Parquet lake on disk. CI runs PostgreSQL as a service container
and verifies a fresh install with docker compose on a clean runner.

## 7. Repository layout

```
fantasy-football-ml/
  pyproject.toml           Python project (ffml + api), pinned dependencies
  ffml/                    pipeline package
    config.py              paths, season/week resolver, run ids
    data/                  nflreadpy wrappers, Sleeper client, snapshot store, validation
    features/              point-in-time feature builders and leakage guards
    models/                baselines, stage models, quantiles, calibration
    sim/                   Monte Carlo engine and scoring-agnostic stat lines
    scoring/               league scoring rules engine (Sleeper map + manual)
    eval/                  backtest harness, metrics, calibration, audits, reports
    cli.py                 ffml ingest | validate | process | features | train | backtest | predict | refresh
  api/                     FastAPI application
    main.py routers/ services/ db/ schemas/ alembic/
  web/                     Next.js application (app/, components/, lib/, tests/)
  tests/                   pytest suites: data, features, models, sim, scoring, api
  reports/                 backtest reports, model card, evaluator sign-offs (committed)
  docs/                    plan, architecture, schema, features, modeling, runbook, deployment, attribution
  docs/status/             agent status notes with evidence
  scripts/                 operational scripts (refresh, alerts, smoke tests)
  .github/workflows/       ci.yml, data_quality.yml, weekly_refresh.yml, injury_refresh.yml, retrain_check.yml
  docker-compose.yml  Dockerfile.api  Dockerfile.web  Dockerfile.pipeline
  data/  artifacts/        gitignored; README files describe layout
```

## 8. Cross-cutting rules

- **Point-in-time discipline.** Every feature row carries a `cutoff_ts`. Builders
  read only rows whose event time is strictly earlier than the cutoff. Leakage
  tests rebuild features from truncated inputs and assert equality.
- **Reproducibility.** Every pipeline run has a `run_id`; its manifest records
  input snapshot ids, git SHA, package versions, random seeds, and metrics.
  Predictions carry `model_version` and `run_id` all the way to the UI.
- **Evaluator independence.** Model and feature code can only be changed by the
  ML and Feature Engineer roles; the Independent Evaluator writes under
  `ffml/eval/`, `tests/eval/`, and `reports/` and must sign off before Phase 4.
- **No placeholder predictions.** The API refuses to start in production mode
  without a published predictions snapshot; the UI shows an explicit empty state.
- **Offseason mode.** When no regular-season game remains, the season/week
  resolver switches the app to season-level rankings with an explanatory banner;
  start/sit is disabled with a reason, waivers and trades use season values.
- **Security.** No secrets in git; `.env.example` documents configuration;
  input validation on every endpoint; Sleeper calls rate-limited and cached;
  CORS allowlist; dependency audits (`pip-audit`, `npm audit`) in CI; security
  headers on the web app.
- **Attribution.** An `/about/attribution` page and an API endpoint list every
  source and license, including FTN-specific wording when FTN features are on.
