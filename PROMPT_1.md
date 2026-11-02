Build an original, ML-powered fantasy football analytics app inspired by WalterPicks' feature set. Do not use its name, branding, copy, UI, data, or proprietary methods.



\## Goal

Create a working, deployable web app with a reproducible ML pipeline and an agentic workflow for building and maintaining it. Use the current date to determine the NFL season and week. The app must generate useful projections for the remaining games, and it must include a fallback mode for the offseason.



\## MVP

\- League setup: Start with Sleeper import and manual settings. Support custom scoring and roster settings.

\- Weekly projections: Show floor, median, ceiling, and boom/bust probabilities.

\- Start/sit: Compare players and explain the main drivers.

\- Waiver wire: Rank add/drop options for a specific roster.

\- Trade analyzer: Evaluate rest-of-season value, roster fit, and replacement level.

\- Rest-of-season rankings: Update them weekly.



Stretch goals: live draft assistant, ESPN/Yahoo import, and paper-only player prop comparisons. Do not place bets or present any pick as guaranteed.



\## Data

\- Use nflreadpy and nflverse as the primary sources.

\- Before writing the ingestion code, confirm the current APIs, schemas, data availability, and license requirements.

\- Store raw and processed data as versioned Parquet files.

\- Add attribution in the app. Add source-specific attribution for FTN data if it is used.

\- Create tests for missing data, duplicate players, ID mismatches, team changes, and late schema changes.



\## ML requirements

Model football outcomes before fantasy scoring:

team plays -> pass/run mix -> player opportunity share -> efficiency -> touchdowns -> fantasy points



This structure must allow any league's scoring rules to be applied without retraining.



Requirements:

\- Start with transparent baselines, then test LightGBM or similar models.

\- Use quantile models or calibrated distributions for uncertainty.

\- Use Monte Carlo simulation for boom/bust and matchup outcomes, preserving correlation among teammates where practical.

\- Use only information available at prediction time. Lag all same-week usage, efficiency, and injury information.

\- Use walk-forward validation by season and week. Never use random train/test splits.

\- Keep the final holdout untouched until the model is selected.



Report results by position and week against these baselines:

1\. Rolling player average

2\. Prior-season average

3\. Public consensus rankings, if licensing and timestamps make the comparison valid



Evaluate MAE, pinball loss, interval coverage, calibration, rank correlation, and start/sit decision accuracy. If the model does not beat a baseline, say so clearly and do not ship claims that it does.



\## Agent team

Use one orchestrator and these specialized agents:

\- Data Engineer: ingestion, schemas, validation, and refresh jobs

\- Feature Engineer: point-in-time features and leakage tests

\- ML Engineer: baselines, models, uncertainty, and simulation

\- Independent Evaluator: backtests, calibration, and leakage audits. This agent cannot edit model code and must approve release candidates.

\- Backend Engineer: database, API, league logic, and recommendation services

\- Frontend Engineer: responsive UI, charts, explanations, and empty/error states

\- QA and Security Reviewer: tests, secrets, dependencies, performance, and auth



Each agent must work from a clear task list, commit small changes, run tests, and hand off a short status note with evidence. The orchestrator must resolve conflicts and keep a decision log.



\## Delivery phases and gates

1\. Plan: Inspect the data, then write the architecture, schema, feature list, and build plan.

2\. Data: Create a reproducible pipeline with passing tests.

3\. Models: Complete baselines, walk-forward backtests, and the evaluator's report.

4\. App: Connect the backend and frontend to real model outputs. Do not use placeholder predictions.

5\. Automation: Add scheduled jobs for post-game updates, injury refreshes, retraining checks, data-quality checks, and failure alerts.

6\. Release: Run end-to-end tests, write a deployment guide, and verify a fresh install.



Do not start a phase until the prior gate passes. If a gate fails, fix the issue or document why it remains open.



\## Recommended stack

Use these defaults unless you find a better option and explain why:

\- Python, Polars, DuckDB, Parquet, scikit-learn, and LightGBM

\- FastAPI and PostgreSQL

\- Next.js, TypeScript, and Tailwind CSS

\- pytest, Playwright, Docker, and GitHub Actions



\## Final deliverables

\- Complete Git repository and working app

\- README, architecture diagram, setup guide, and deployment guide

\- Model card and backtest report

\- Data dictionary and attribution page

\- Weekly operations runbook

\- Prioritized list of known limitations and next improvements



\## How to work

Begin by inspecting the environment and sources, then present a brief plan and any decisions that truly need my input. Make reasonable assumptions for everything else and record them. Keep working through the phases until the MVP is complete or a real blocker requires me. Do not call a feature finished until you've run it and shown the evidence.

