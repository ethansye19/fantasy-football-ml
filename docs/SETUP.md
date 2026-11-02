# Setup Guide (local development)

This guide gets a developer from a clean checkout to running the pipeline,
the API, and the web app locally. The deployment guide covers containers.

## Prerequisites

| Tool | Version | Notes |
| --- | --- | --- |
| Python | 3.11 (3.12 also supported by `pyproject.toml`) | On Windows use the launcher: `py -3.11` |
| Node.js | LTS (22 or newer) | Needed for the web app and Playwright only |
| Git | any recent | |
| PostgreSQL | not required locally | SQLite is used for local development; PostgreSQL runs in docker compose and CI |

Windows note: the console code page is usually cp1252, which breaks Polars
table output. The package sets `PYTHONUTF8=1` on import, but set it in your
shell as well when running ad-hoc Python:

```powershell
$env:PYTHONUTF8 = "1"
```

## 1. Python environment

```powershell
git clone https://github.com/ethansye19/fantasy-football-ml.git
cd fantasy-football-ml
py -3.11 -m venv .venv
.venv\Scripts\python -m pip install -U pip
.venv\Scripts\python -m pip install -e ".[api,dev]"
```

On macOS or Linux replace the last three lines with `python3.11 -m venv .venv`
and `.venv/bin/python -m pip install -e ".[api,dev]"`.

`requirements.lock.txt` pins the exact versions that were tested; install
from it with `pip install -r requirements.lock.txt` for a reproducible
environment.

## 2. Configuration

Copy `.env.example` to `.env` and adjust if needed. Defaults put data under
`./data` and model artifacts under `./artifacts`. Both directories are
ignored by git. Never set `FFML_HOLDOUT_UNLOCK=true` outside the evaluator's
release run; the flag unlocks the 2025 holdout season and is logged.

## 3. Pipeline commands

```powershell
.venv\Scripts\ffml --help
.venv\Scripts\ffml info                          # current season, week, mode
.venv\Scripts\ffml ingest --seasons 2016-2026    # raw snapshots from nflverse
.venv\Scripts\ffml validate --seasons 2016-2026  # data-quality suite, non-zero exit on failure
.venv\Scripts\ffml process --seasons 2016-2026   # processed dim_* and fact_* tables
.venv\Scripts\ffml warehouse                     # DuckDB views over the latest snapshots
.venv\Scripts\ffml features --seasons 2016-2026
.venv\Scripts\ffml train
.venv\Scripts\ffml backtest
.venv\Scripts\ffml predict                       # next week plus rest of season
```

A full first ingest downloads roughly a gigabyte of Parquet; later runs only
add new snapshots. The data layout is documented in docs/DATA_SCHEMA.md.

## 4. Tests

```powershell
.venv\Scripts\python -m pytest -q                 # everything, including network tests
$env:FFML_OFFLINE = "1"; .venv\Scripts\python -m pytest -q   # offline only
.venv\Scripts\python -m ruff check .
```

Network tests download small slices from nflverse and read public Sleeper
endpoints. They are skipped when `FFML_OFFLINE=1`.

## 5. API and web app

These sections are completed in Phase 4. The intended commands are:

```powershell
.venv\Scripts\uvicorn api.main:app --reload --port 8000
cd web; npm install; npm run dev     # http://localhost:3000
```

## 6. Directory map

See docs/ARCHITECTURE.md section 7 for the repository layout and
docs/DATA_SCHEMA.md for the Parquet lake and application database schema.
