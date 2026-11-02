# Decision Log and Assumptions

Format: id, date, status, context, decision, consequences. Status is one of
proposed, accepted, superseded. Open questions for the owner are at the end.

| id | date | status | title |
| --- | --- | --- | --- |
| D-001 | 2026-11-01 | accepted | Python 3.11 virtual environment |
| D-002 | 2026-11-01 | accepted | Monorepo layout with `ffml`, `api`, `web` |
| D-003 | 2026-11-01 | accepted | Scoring applied after simulation at request time |
| D-004 | 2026-11-01 | accepted | SQLite for local development, PostgreSQL in deployment and CI |
| D-005 | 2026-11-01 | accepted | 2025 season is the final holdout; 2022-2024 walk-forward validation |
| D-006 | 2026-11-01 | accepted | Injury information policy: lagged in the model, latest report in the availability layer |
| D-007 | 2026-11-01 | accepted | Public consensus baseline from the FantasyPros archive, private evaluation only |
| D-008 | 2026-11-01 | accepted | Vegas lines from nflverse schedules with a documented closing-line caveat |
| D-009 | 2026-11-01 | accepted | FTN charting features off by default |
| D-010 | 2026-11-01 | accepted | Full chain for QB, RB, WR, TE; simplified chains for K and DST |
| D-011 | 2026-11-01 | accepted | No user accounts in the MVP |
| D-012 | 2026-11-01 | accepted | Agent orchestration via subagents with path restrictions |
| D-013 | 2026-11-01 | accepted | Non-commercial use assumption |
| D-014 | 2026-11-01 | accepted | Branch `main`, small commits, no push until a remote is approved |
| D-015 | 2026-11-01 | accepted | UTF-8 everywhere on Windows |
| D-016 | 2026-11-01 | accepted | Ingestion bypasses the nflreadpy cache and keeps its own snapshots |
| D-017 | 2026-11-01 | accepted | Boom and bust defined by positional finish in the simulation |
| D-018 | 2026-11-01 | accepted | Node.js LTS installed locally with winget |
| D-019 | 2026-11-01 | accepted | Private GitHub repository as the remote; CI and scheduled jobs run there |
| D-020 | 2026-11-01 | accepted | Sleeper user `ethanye19` is the test league source |
| D-021 | 2026-11-01 | accepted | Gates are never weakened for the week 9 kickoff |
| D-022 | 2026-11-01 | accepted | Player crosswalk joins Sleeper through `ff_playerids.sleeper_id` first; Sleeper `gsis_id` is a fallback |
| D-023 | 2026-11-01 | accepted | External player ids are strings everywhere |
| D-024 | 2026-11-01 | accepted | League scoring stored as Sleeper keys minus zero entries; missing key means 0 |
| D-025 | 2026-11-01 | accepted | Sleeper client limits: 300 requests per minute, burst 30, one shared client per process |
| D-026 | 2026-11-01 | accepted | Sleeper players snapshot id is the UTC date |
| D-027 | 2026-11-01 | accepted | Schema pins follow the newest season; older-season layout differences only warn |
| D-028 | 2026-11-01 | accepted | Seasons before a dataset's start are skipped and recorded; any other missing season fails |
| D-029 | 2026-11-01 | accepted | Injury `date_modified` (present 2016-2024) is kept as `report_modified_at` |
| D-030 | 2026-11-01 | accepted | Schema pin JSON ships as package data |
| D-031 | 2026-11-01 | accepted | Nightly ingest refreshes only the current season; a full run follows Thursday stat corrections |
| D-032 | 2026-11-02 | accepted | Validation severity: exit 1 only on FAIL; absent tables SKIP; a crashing check is FAIL; gaps in the current week WARN |
| D-033 | 2026-11-02 | accepted | Weekly volume checks are normalised per final game |
| D-034 | 2026-11-02 | accepted | Datasets whose schema is fixed in code (Sleeper players) are exempt from schema pins |
| D-035 | 2026-11-02 | proposed | If 2016-2019 crosswalk coverage is below 98 percent, the gate applies to the current season only |

## D-001 Python 3.11

Context: the machine has Python 3.14.7 (default) and 3.11.9. Both can install
the full stack (`pip --dry-run` confirmed nflreadpy 0.1.5, polars 2.1.0,
duckdb 2.0.0, lightgbm 4.7.0, scikit-learn 1.9.1, pyarrow 26.0.0, fastapi
0.162.0 on each). Decision: 3.11 for the project venv and `python:3.11-slim`
in Docker, because the ML stack has the longest track record there and the
local and container interpreters match. Consequence: `py -3.11 -m venv .venv`
is the documented setup step; `pyproject.toml` requires `>=3.11,<3.13`.

## D-002 Monorepo

One repository with the pipeline package, the API, and the web app, plus
docs and reports. Consequence: one CI pipeline with path filters.

## D-003 Scoring after simulation

The brief requires any league's scoring without retraining. Models predict
stat-line distributions; the API applies a ScoringRules vector to simulated
stat lines. Consequence: predictions are stored as simulation samples
(about 15 MB per week), and the API keeps them in memory.

## D-004 SQLite locally, PostgreSQL in deployment

Docker and PostgreSQL are not installed locally. WSL2 with Ubuntu exists and
winget offers Docker Desktop 4.97. Owner decision (2026-11-01): develop
against SQLite through SQLAlchemy, run PostgreSQL in docker compose and as a
CI service, and do not install Docker Desktop for now. Consequence:
migrations and the API test suite run on both engines in CI; container
builds and the fresh-install check are verified in CI, not locally.

## D-005 Holdout and validation

Training from 2016, walk-forward validation on 2022-2024, final holdout 2025
evaluated once by the evaluator, 2026 weeks 1-8 as a forward check. A loader
guard refuses 2025 rows unless an explicit unlock flag is set and logged.

## D-006 Injury information policy

The injuries dataset has no timestamps, so same-week designations cannot be
proven to predate kickoff in history. Primary backtests lag injury
information; the simulation applies the latest report as an availability
layer at prediction time; a labelled final-report variant is reported as a
sensitivity. See MODELING.md section 9.

## D-007 Consensus baseline, private evaluation only

The DynastyProcess archive of FantasyPros weekly ECR has dated scrapes from
October 2020 and near-weekly coverage 2021-2026, so timestamps make a valid
comparison possible. Its data license is not stated and FantasyPros content
is proprietary. Owner decision (2026-11-01): use it for private evaluation
only. Consequences: the raw archive and any derived table stay under
`data/` (gitignored) and are never loaded by the API or the web app; the
committed backtest report and model card show only aggregate comparison
metrics (no ranking rows); if a public report is ever produced, the
comparison is removed unless the license is confirmed. The evaluator checks
these constraints at Gate 3.

## D-008 Vegas lines

Historical lines in nflverse schedules are closing lines, which is slightly
optimistic for a Thursday prediction. Accepted with an ablation without lines
in the report and a note in the model card. Live lines refresh every five
minutes in the schedule feed.

## D-009 FTN off by default

FTN charting is CC-BY-SA-4.0. Share-alike could extend to published derived
data. FTN features are behind a flag, off in the MVP, and the attribution
page includes the FTN wording whenever the flag is on.

## D-010 Positions

QB, RB, WR, TE use the full chain. K uses team red-zone and scoring
expectations with kicker accuracy by distance. DST uses opponent turnover and
sack rates with points-allowed distributions. IDP is out of scope.

## D-011 No accounts

Leagues are imported by Sleeper id or configured manually; the browser keeps
a signed session cookie pointing at a league and team. Accounts are a
stretch item; the QA reviewer still checks headers, CORS, validation, and
rate limits.

## D-012 Orchestration mechanics

The main session is the orchestrator; specialists are subagents with path
restrictions enforced by diff review (AGENTS.md). The evaluator cannot edit
model, feature, or simulation code and must approve a release candidate.

## D-013 Non-commercial use

Sleeper's API is free for non-commercial use; nflverse is CC-BY-4.0 either
way. The project assumes personal, non-commercial use and says so in the
attribution page. Commercial use would need Sleeper's permission.

## D-014 Git workflow

The repository had no commits; the branch is renamed to `main`. Commits are
small and descriptive. Nothing is pushed until the owner approves a remote.

## D-015 UTF-8 on Windows

The console encoding is cp1252 and Polars output fails without UTF-8. All
entry points set `PYTHONUTF8=1`; the setup guide documents it.

## D-016 Ingestion owns snapshots

nflreadpy's cache is disabled during ingestion so that the raw layer is the
single source of truth with manifests and hashes; development notebooks may
enable the cache locally.

## D-017 Boom and bust

Boom = probability of a top-N positional finish within the simulation, bust =
probability of finishing outside the startable range, with thresholds per
position listed in MODELING.md. These are league-scoring aware because the
ranks are computed after scoring is applied.

## D-018 Node.js via winget

Owner approved installing Node.js LTS with winget (package
`OpenJS.NodeJS.LTS`) so the Next.js app and Playwright run locally.

## D-019 Private GitHub remote

Owner approved creating a private repository under the authenticated GitHub
account and pushing `main`. CI, scheduled data-quality and refresh workflows,
and the clean-runner fresh-install check run there.

## D-020 Test league

Owner provided Sleeper username `ethanye19` for import tests. Only public
league data is read; nothing is written to Sleeper.

## D-021 Gates are not weakened for kickoff

Owner instruction: do not weaken any gate to make the week 9 kickoff. If a
gate is not passed in time, week 9 ships late or not at all, and the status
notes say so.

## Assumptions recorded

- Regular season weeks 1-18; fantasy playoffs default to weeks 15-17 unless
  the league settings say otherwise.
- Floor is the 10th percentile and ceiling the 90th.
- 2,000 simulations per entity per week; raised if calibration needs it.
- Replacement level for value calculations is the best projected free agent
  at the position in the league, falling back to the positional rank implied
  by league size and starting slots when no roster data exists.
- Sleeper's players map is pulled at most once per day.
- The deployment target is a single host running docker compose; the guide
  notes how to split services.

## D-022 to D-026 Sleeper integration (proposed by the Data Engineer, Sleeper)

Context: in the test league only 26 of 110 rostered players carry a
`gsis_id` in the Sleeper players map (none drafted 2020 or later do), 866
map-wide `gsis_id` values have a leading space, and 14 are duplicated.
`ff_playerids` matches 563 of 570 active skill players. Decisions: join
through `ff_playerids.sleeper_id` first, then weekly rosters, then the
stripped Sleeper `gsis_id`, then name plus team plus position, then a manual
override file (D-022). All external ids are strings (D-023). League scoring
is stored as the Sleeper key set with zero entries removed; the scoring
engine treats a missing key as zero and must support the 41 keys seen in the
test league, including kicker distance buckets and DST points-allowed bands
(D-024). Client limits are 300 requests per minute with a burst of 30 and one
shared HTTP client per process (D-025). The players-map snapshot id is the
UTC date and is fetched at most once per day (D-026).

## D-027 to D-031 Ingestion policies (proposed by the Data Engineer, nflverse)

The first full ingest (2016-2026, 20 datasets, 5,394,835 rows, 207 MB)
showed per-season layout differences inside otherwise stable datasets: pbp
`goal_to_go` is Int32 in some seasons and Float64 in others, weekly rosters
`height` changed type in 2025, injuries 2016-2024 carry `date_modified` and
`season_type` while 2025+ drop them for `game_type`, and depth charts have a
15-column and a 12-column format. Decisions: the pinned schema is the newest
season's and older seasons only warn (D-027); a season that nflreadpy reports
as before the dataset's start is skipped and listed in the manifest, any
other failure aborts (D-028); the historical injury timestamp is preserved in
`fact_injury_report.report_modified_at` so point-in-time audits can use it
where it exists (D-029, refines D-006); the schema pins are package data
(D-030); the nightly refresh ingests only the current season and the
Thursday run after NFL stat corrections re-ingests everything (D-031).

## D-032 to D-035 Validation policies (proposed by QA)

`ffml validate` runs twelve checks in a raw tier and a processed tier and
writes a markdown report. Exit status is non-zero only when a check FAILS;
checks whose table is absent report SKIP; an exception inside a check is
reported as FAIL rather than crashing the run; completeness gaps for games
less than three days old are WARN because nflverse may still be loading them
(D-032). Weekly volume thresholds compare rows per final game, not raw rows,
so bye weeks do not trip them (D-033). The Sleeper players cache has a
schema fixed in code and is exempt from the nflverse schema pins (D-034).
D-035 is conditional on the crosswalk coverage numbers for 2016-2019 and is
decided at the Gate 2 review.

## Owner answers received 2026-11-01

1. Node.js LTS: install with winget (D-018).
2. Database: SQLite locally, PostgreSQL in compose and CI, no Docker Desktop
   for now (D-004).
3. GitHub: create a private repository and push (D-019).
4. Consensus baseline: private evaluation only; raw data stays out of git,
   the app, and any public report unless the license is confirmed (D-007).
5. Test league: Sleeper user `ethanye19` (D-020).
6. Gates are never weakened to make the week 9 kickoff (D-021).

## Open questions

None at this time.
