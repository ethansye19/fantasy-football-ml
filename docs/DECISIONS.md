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
| D-035 | 2026-11-02 | superseded | Not triggered: crosswalk coverage is 99.6 to 100 percent in every season, so the 98 percent gate applies to all seasons |
| D-041 | 2026-11-02 | accepted | Manual crosswalk overrides win over every source and may force a fantasy position |
| D-042 | 2026-11-02 | accepted | Official attempt definitions for pass_att and rush_att; `plays` excludes kneels and spikes |
| D-043 | 2026-11-02 | accepted | fact_player_game includes roster-only rows with `played=false`; kickers count in the coverage metric |
| D-044 | 2026-11-02 | accepted | Next Gen Stats postseason weeks are remapped to nflverse week numbers by rank |
| D-045 | 2026-11-02 | accepted | DST attribution rules for return and defensive touchdowns, blocks, and points allowed |
| D-046 | 2026-11-02 | accepted | Feature cutoff is the first kickoff of the season-week; off-field snapshots use an as-of time one hour before that cutoff |
| D-047 | 2026-11-02 | accepted | Feature naming `<group>__<stat>__<window>`, targets prefixed `y_`, availability inputs prefixed `avail__` |
| D-048 | 2026-11-02 | accepted | Canonical stat-line vocabulary shared by simulations, actuals, and scoring |
| D-049 | 2026-11-02 | accepted | "Holdout touched once" means one `holdout_eval` session: same purpose, git sha, and user within two hours |
| D-050 | 2026-11-02 | accepted | Evaluation conventions: rank tie-breaks, startable sets, lineup pool, population alignment across models |
| D-051 | 2026-11-02 | accepted | Baseline 3 (consensus rankings) lives only in `ffml/eval`; no model, feature, API, or web code reads restricted data |
| D-052 | 2026-11-02 | accepted | Consensus rank-to-points curve built from prior seasons only; the evaluator keeps its own PPR scoring and cross-checks it against `ffml/scoring` |
| D-053 | 2026-11-03 | accepted (modified) | Each baseline's median is its own estimate; intervals are re-centred residual quantiles; a rolling-median baseline is added |
| D-054 | 2026-11-03 | accepted | Scoring conventions: two-point weight, return-TD key precedence, exclusive yardage bands |
| D-055 | 2026-11-03 | accepted | `fact_dst_game.forced_fumbles` added; the stat-line vocabulary gains a scored forced-fumble column |
| D-056 | 2026-11-03 | accepted | Game-level simulation draws use total sd 13.2 and margin sd 12.8, fitted on 2016-2024 |
| D-057 | 2026-11-03 | accepted (modified) | QB passing totals are derived from the same simulation's receiver outcomes |
| D-058 | 2026-11-03 | accepted | 2025 stays locked until the evaluator's single holdout session; a release marker then unlocks it for refits and 2026 history |
| D-059 | 2026-11-03 | rejected | Legacy (2016-2024) depth charts keep the documented week w-1 rule in the primary configuration; week w is a labelled sensitivity |
| D-060 | 2026-11-03 | accepted | Feature window semantics as built (docs/FEATURES.md, As built) |
| D-061 | 2026-11-03 | accepted | Injury point-in-time audits use `report_modified_at`, or the report week's last kickoff where it is missing |
| D-062 | 2026-11-03 | accepted | Targets are null for games that are not final; roster-only player rows exist only for final games with a dim_player record |
| D-063 | 2026-11-03 | accepted | Stage-selection metrics: weighted absolute error for shares and rates, Poisson deviance for counts, log loss for availability |
| D-064 | 2026-11-03 | accepted | "Active" means `y_played`; the engine always fields a passer per team |
| D-065 | 2026-11-03 | accepted (modified) | Final-report availability caps the primary probability; game-day `INA` is excluded from backtests and applied only in the live layer |
| D-066 | 2026-11-03 | accepted | Engine volume conventions (scrambles in dropbacks, scrambles and kneels as QB carries, designed-run carry share) |
| D-067 | 2026-11-03 | accepted | Boom and bust are conditional on active; rarely active entities use a forced-active simulation |
| D-068 | 2026-11-03 | deferred | Lagged player fumble-rate and return-TD features go to the improvements list |
| D-069 | 2026-11-03 | accepted | Team-TD structural heuristic uses a divisor fitted inside each training window (about 9.4), not 7 |
| D-070 | 2026-11-03 | accepted | Baseline residual and universe details under D-053 |
| D-071 | 2026-11-03 | accepted | Harness outcome, PIT, and refit conventions |
| D-072 | 2026-11-03 | accepted | Online conformal calibration with 2021 warm-up; frozen offsets fitted on 2022-2024; custom scorings use the nearest preset's offsets |
| D-073 | 2026-11-03 | accepted | Direct challenger specification (pooled LightGBM quantile model plus a DST model; not shipped) |
| D-074 | 2026-11-03 | accepted (modified) | Start/sit and Spearman are compared on one model-agnostic pair set; each model's own top-N is secondary |
| D-075 | 2026-11-03 | accepted | Backtest run ids resume; `ffml backtest --run-id --n-jobs` |
| D-076 | 2026-11-03 | accepted | Registry transitions require an evaluator report with `Verdict: APPROVED` |
| D-077 | 2026-11-03 | accepted | Start/sit reference ranking = mean raw `pred_q50` of the primary-configuration models compared; ties by entity id |
| D-078 | 2026-11-03 | accepted | Verdict rule: 95% paired week-clustered interval entirely on the good side of zero; headline compares calibrated forms, raw grid alongside |
| D-079 | 2026-11-03 | accepted | Harness actuals (`ffml/scoring` presets) are the scoring of record; the consensus is scored against them |
| D-080 | 2026-11-03 | accepted | The release-candidate inputs audit and team/DST leakage checks are part of the Gate 3 evaluator audit |
| D-081 | 2026-11-03 | accepted (owner) | Chain vs consensus is compared on identical player-weeks; the effect of the missing Thursday players is shown separately |
| D-082 | 2026-11-03 | accepted (owner) | If the chain does not beat the consensus, the report says so plainly; no consensus features, no verdict-rule changes, no holdout tuning |
| D-083 | 2026-11-03 | accepted (owner) | The chain timing budget is rerun on an idle machine after the backtests and included in the Gate 3 evidence |
| D-084 | 2026-11-03 | accepted (owner) | The owner sees the final validation report before the one-time holdout session opens |
| D-085 | 2026-11-03 | accepted (owner) | Coverage acceptance uses tie-aware (randomized PIT) coverage, 75-85% band, fixed seed, same rule for every model; frozen before 2025; recorded in the model card as a rule change made after validation |
| D-086 | 2026-11-03 | accepted (owner) | Boom/bust calibration per position, walk-forward (out-of-sample), before/after shown for every position, frozen before 2025; kicker boom/bust hidden or marked low confidence if still poorly calibrated |
| D-087 | 2026-11-03 | accepted (owner) | The owner sees the updated report again before the holdout session |
| D-088 | 2026-11-03 | accepted (owner) | The conformal calibrator targets the tie-aware rule, with the same seeded tie handling for every position and model; out-of-sample; seed sensitivity shown; last calibration change before the holdout; any position still failing is frozen and disclosed |
| D-089 | 2026-11-03 | accepted (owner) | K and DST boom/bust are low confidence: hidden or marked in the app and disclosed in the model card; a smoother calibrator goes on the improvements list |
| D-090 | 2026-11-03 | accepted (owner) | The owner sees the v3 summary and gives the go-ahead before 2025 is opened |
| D-091 | 2026-11-04 | accepted (owner) | Go-ahead for the one-time 2025 holdout, conditional on pre-registration (commit, hashes, seeds, approval rules, calibration mode) |
| D-092 | 2026-11-04 | accepted (owner) | A 2025 failure means not approved: no tuning on 2025, no rerun for a better result, no Week 9 projections published as approved |
| D-093 | 2026-11-04 | accepted (owner) | Rerun only after an infrastructure failure that stops the session before any metric is read, with the same frozen inputs and the reason logged |
| D-094 | 2026-11-04 | accepted (owner) | If 2025 passes but the 2026 forward check misses the same rules, stop before publishing projections and show the owner |
| D-095 | 2026-11-04 | accepted | Holdout and forward-check calibration is FIXED: each model's calibrator is frozen on 2022-2024 and never updated from 2025 or 2026 weeks |
| D-096 | 2026-11-04 | accepted | The 2026 forward check covers weeks 1-7, the weeks fully final in the frozen data snapshot (week 8 had 2 of 14 games unfinished) |
| D-097 | 2026-11-04 | accepted | Holdout outcome: chain_v1 NOT APPROVED (QB coverage 0.739); Gate 3 fails; no release as approval, no Week 9 projections |
| D-098 | 2026-11-04 | accepted (owner) | chain_v2 = chain_v1 + 2025 in training + walk-forward calibrator; pass-probability estimate before freezing; live test from Week 10 with hashed pre-kickoff projections |
| D-099 | 2026-11-04 | accepted (owner) | Phase 4 and 5 start as an exception; the real app shows no projections until a model passes; private tests labeled "not approved" |
| D-100 | 2026-11-04 | accepted | Approval gate in `ffml predict`: live needs the registry live version; private tests are candidates only, in a private store, labeled; `rejected` status |
| D-101 | 2026-11-04 | accepted | `FFML_APP_ENV`: production serves only approved live runs; private_test reads private store, then replay, then fixture; unknown values mean production |
| D-102 | 2026-11-04 | accepted | API summaries are conditional on active; lineup values use the unconditional mean; trade value is max(0, ROS mean - replacement) |
| D-103 | 2026-11-04 | accepted | Rest-of-season totals include the run week by default |
| D-104 | 2026-11-04 | accepted | QB boom/bust is low confidence alongside K and DST until a live test shows otherwise; the set is read from the run manifest, default {QB, K, DST} |
| D-105 | 2026-11-05 | accepted (owner) | Live-test rules resized to 9 weeks, chosen by pre-set limits (corrected estimates: no-edge pass <= 10%, good-model pass >= 70%, lowest false approval, simpler if close); pass = provisional approval; 22 rules still reported through 2027 w9 |
| D-106 | 2026-11-05 | accepted (owner) | One missed week excluded without extension, a second makes the test INVALID; deadline = official first kickoff; runs start 24 h early; misses logged with reason |
| D-107 | 2026-11-05 | accepted (owner) | Logged-off scheduled tasks with least privilege plus a GitHub watchdog at 6 h and 1 h; AC power, wake, catch-up; logged-off dry run on a test branch before Week 10 |
| D-108 | 2026-11-05 | accepted (conditional) | LightGBM `num_threads` 8 -> 4 for determinism on Linux CI, only if all four identity weeks stay byte-identical; otherwise threads stay 8 and the CI test pins 1 thread |
| D-109 | 2026-11-05 | accepted | Live-test timing constants: window opens K-25h, first run K-24h15m, retries every 30 min, build cutoff K-30m, push deadline K-5m; GitHub watchdog windows [6.5h, 5.5h) and [1.5h, 0) before kickoff; MISSED.json format missed/2 |
| D-036 | 2026-11-02 | accepted | Consensus rankings validity is per game (`valid_for_game`), not per week |
| D-037 | 2026-11-02 | accepted | Depth-chart snapshot timestamps and week assignment rule; rows without gsis_id dropped |
| D-038 | 2026-11-02 | accepted | Context table keys are `(season, week, player_id, team)` |
| D-039 | 2026-11-02 | accepted | Warehouse views and the data dictionary never expose restricted data |
| D-040 | 2026-11-02 | accepted | ECR normalisation: page basename, FA team to null, PK to K, DEF to DST |

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

## D-036 to D-040 Context tables and restricted rankings (proposed by the Data Engineer, context)

The rankings archive is scraped on Fridays, after Thursday games, so the
week-level validity flag is true for almost no week. The per-game flag
`valid_for_game` (scrape date before the date of the player's own game,
Eastern time) holds for about 90 percent of rows and is what the evaluator
uses; Thursday players drop out of that comparison (D-036). Legacy weekly
depth charts receive a synthetic `snapshot_ts` 48 hours before the first
kickoff of the week; timestamped charts are assigned the week whose window
contains them, with the pre-season folded into week 1; rows without a
`gsis_id` (29,910) are dropped and counted (D-037). Injury and roster tables
key on `(season, week, player_id, team)` because trades can place a player
on two teams in one week (D-038). The DuckDB warehouse and the generated
data dictionary never reference anything under a restricted directory
(D-039). ECR pages are normalised by basename, free agents get a null team,
PK becomes K and DEF becomes DST (D-040).

## D-041 to D-045 Core processed tables (proposed by the Data Engineer, core tables)

The manual override file `ffml/data/crosswalk_overrides.csv` resolves ids
that no automated source matches and may force a fantasy position (Travis
Hunter is CB in nflverse but plays WR) (D-041). Team attempts follow the
official definitions, spikes and kneels included and two-point tries
excluded, so player targets tie exactly to the weekly stats file, while
`plays` excludes kneels and spikes for pace work (D-042). The player-game
table carries roster-only rows (`row_source = "roster"`, `played = false`)
so availability can be modeled, and kickers are included in the crosswalk
coverage metric (D-043). Next Gen Stats number the Super Bowl one week later
than nflverse; postseason weeks are remapped by rank (D-044). DST events are
attributed from play-by-play: interception and fumble returns to the
defense, kick and punt returns to the special-teams unit, blocked punts,
field goals and extra points as blocks, points allowed as the opponent's
final score (D-045). Crosswalk coverage came out at 99.6 to 100 percent for
every season 2016-2026, so D-035 was not needed.

## D-046 to D-048 Phase 3 contracts (orchestrator)

Game-derived features for any row in season-week w use only games whose
kickoff precedes the first kickoff of that week, so projections published
before Thursday are reproducible and Thursday and Sunday players share one
information set; depth charts, roster status, and lines use an as-of time one
hour before that cutoff for historical rows and the current time for live
rows (D-046). Feature columns are named `<group>__<stat>__<window>` with
groups ctx, team, def, opp, eff, attr, depth, avail and windows l1, l3, l5,
ew, std, ps, car; targets carry the `y_` prefix and are null for future rows;
same-week injury and roster inputs carry the `avail__` prefix and are not
model features in the primary configuration (D-047, refines D-006). One
stat-line vocabulary (`ffml/scoring/statline.py`) is used by simulated
samples, actual outcomes, and the scoring engine, so any league's rules apply
to either (D-048, implements D-003).

## D-049 to D-052 Evaluation conventions (proposed by the Independent Evaluator)

The holdout guard logs one line per call, and one evaluation run makes many
calls, so the Gate 3 rule "holdout touched exactly once" is checked as one
session: all `holdout_eval` lines share a git sha and user and fall within
two hours (D-049). Metrics break rank ties with `rank_pred`, restrict
start/sit to startable QB, RB, WR, TE (no K or DST), choose lineups from
each team's active pool with Sleeper flex eligibility, and compare models
only on rows every model predicts (D-050). The restricted consensus reader
lives in `ffml/eval/ecr_baseline.py` and nowhere else (D-051). Its
rank-to-points curve is fitted on seasons strictly before the evaluated
season; the evaluator computes PPR actuals independently and reconciles them
with `ffml/scoring` during the validation report, because
`fantasy_points_ppr_ref` omits kicking points (D-052).

## D-053 to D-058 Scoring, baselines, and simulation (proposed by the ML Engineer, rulings by the orchestrator)

**D-053 (modified).** As first specified, every baseline variant took its
quantiles, including the median, from the same last-8-games distribution,
so the rolling-average and prior-season baselines had identical median
errors. Ruling: each baseline's point estimate is its own average (the brief
defines baseline 1 as a rolling player average and baseline 2 as a
prior-season average), `pred_q50 = pred_mean` = that average, and `q10` and
`q90` are the estimate plus the entity's empirical residual quantiles over
its last 8 games (position residuals when fewer than 4 exist). A third
transparent baseline, the median of the last 8 games, is added because it is
the strongest simple predictor under MAE; the model must be compared against
it too. The evaluator reports MAE of both `pred_mean` and `pred_q50`.

**D-054.** Two-point conversions are scored with the mean of the league's
pass, rush, and receiving two-point values present; `fum_rec_td` takes
precedence over `st_td` for offensive return touchdowns; yardage-allowed
bands are mutually exclusive. Sleeper and the engine agree on 82 of 83
offensive players in 2026 week 8; the one gap is a stat-recording
difference, not a rules difference.

**D-055.** All four defense mismatches in week 8 equal that defense's forced
fumbles in play-by-play. `fact_dst_game.forced_fumbles` now counts plays where
either listed forcing player belongs to the team (defense and special teams
together; leagues that weight `ff` and `def_st_ff` differently are
approximated). The ML Engineer adds the column to the stat-line vocabulary
and the `ff` key to the scoring engine in wave two.

**D-056.** With total sd 10.0 and margin sd 13.5, opponents' simulated points
correlated at -0.29. Historical residuals against Vegas lines (2016-2024, no
holdout) give 13.2 and 12.8 with near-zero correlation, so those are the
defaults.

**D-057 (modified).** In wave one the QB's completions and yards were drawn
from the QB's own parameters, so they matched his receivers only on average.
Ruling: within each simulation, QB completions, passing yards, and passing
touchdowns are the sums over that team's simulated receptions, receiving
yards, and receiving touchdowns (with an unmodeled-receiver bucket for
targets to players without projections). This preserves the QB to receiver
correlation the brief asks for.

**D-058.** Forward predictions for 2026 need 2025 as history, which the
holdout lock forbids. Ruling: nothing reads 2025 until the Independent
Evaluator's single holdout session (D-049) approves a model. The evaluator
then calls `ffml.holdout.release_holdout(model_version, report)`, which writes
`artifacts/holdout_release.json` and permanently unlocks the season. After
release, production models are refit through 2025 and 2026 to date, and the
weekly predictions use 2025 history. Accesses after release are still logged.

*Amended 2026-11-04 (D-098, D-100):*
- The release no longer depends on approval. It follows the single holdout
  session whatever its verdict.
- It records `approved` (false for chain_v1) and a `note`.
- Approval is recorded only in the model registry.
- Releasing 2025 makes it training data and history. It approves nothing,
  and live predictions still need an approved, live registry version (D-100).

## D-059 to D-062 Feature build (proposed by the Feature Engineer, rulings by the orchestrator)

**D-059 (rejected).** Legacy weekly depth charts carry a synthetic timestamp
48 hours before the week's first kickoff, so as built the week w chart fed
week w features. Nothing in the source proves those charts were published
before the games; they may include later edits, including injury moves. The
documented ground rule (FEATURES.md rule 1: week w-1 where no timestamp
exists) stays the primary configuration, which is the conservative reading
of the brief's point-in-time requirement. The week w version is built as a
labelled sensitivity (`depth_variant = "same_week"`) and the evaluator
reports both. For 2025 onward the real timestamps apply unchanged.

**D-060.** Window semantics as documented in the As-built section of
docs/FEATURES.md are accepted.

**D-061.** `fact_injury_report.snapshot_ts` is the ingest time on every row,
so point-in-time audits of the injury table use `report_modified_at`, or the
report week's last kickoff where that is missing.

**D-062.** All `y_` targets are null for games that are not final, and the
player-game table now holds roster-only rows only for final games and only
for players with a dim_player record. The rebuild removed 68 rows for two
unfinished week 8 games and 47 rows for practice-squad players missing from
the nflverse players table (98,530 to 98,415 rows). Field-goal misses by
distance were added at the same time so kicker accuracy by distance can be
modeled, and stadium roof and surface values were normalised.

## D-063 to D-076 Chain, harness, and calibration (proposed by ML Engineers B1 and B2, rulings by the orchestrator)

Details are in docs/status/phase3-ml-engineer-b1.md and -b2.md; only the
rulings that differ from the proposals are explained here.

**D-065 (modified).** The same-week roster status `INA` is the game-day
inactive list, published about 90 minutes before kickoff. In history it
identifies non-participation almost perfectly (6,593 rows in 2016-2024, none
played), so using it in any backtest configuration would measure the outcome,
not a forecast. It is excluded from both backtest configurations. The live
availability layer applies it when present, because at that moment it is
genuine pre-kickoff information.

**D-068 (deferred).** Adding lagged fumble and return-TD windows needs a
feature-version bump and a rerun of every backtest; their fantasy-point
impact is small. They go to the prioritised improvements list.

**D-069.** The divisor `k` in the team-TD heuristic is computed from each
training window's own rows, so it is not tuned on validation data; 9.4 is its
typical value on 2016-2024.

**D-074 (modified).** Each model's startable set being its own top-N lets
models be scored on different pairs. The evaluator defines one pair set per
season-week and position from the average `pred_q50` over all compared models
(a model-agnostic reference that never looks at actuals) and scores every
model on exactly those pairs; each model's own top-N result is reported as a
secondary column.

## D-081 to D-084 Owner instructions for the final validation report and Gate 3 (2026-11-03)

1. The chain and the FantasyPros consensus are compared on exactly the same
   player-weeks (the consensus population, which lacks players whose game
   precedes the Friday scrape). The chain's results on the excluded Thursday
   player-weeks, and on all player-weeks, are reported separately so the
   effect of the missing Thursday players is visible.
2. If the chain does not beat the consensus, the report says so plainly.
   Consensus rankings are never added as a model feature (they are also
   restricted, D-007), the verdict rule (D-078) is not changed after seeing
   results, and nothing is tuned against the 2025 holdout.
3. After the backtests finish, the chain timing test runs again on an idle
   machine with `FFML_ENFORCE_TIMING=1`, and its output is part of the Gate 3
   evidence.
4. The final validation report goes to the owner before the one-time holdout
   session (EV-3.4) opens; the session waits for the owner's go-ahead.

## D-085 to D-087 Owner decisions after the validation report (2026-11-03)

**D-085, coverage rule change after validation.** The closed-interval rule
counted every scoreless game as covered whenever a model's 10th percentile
was exactly 0, which the chain correctly predicts for many RB, WR and TE
player-weeks (ties at q10: RB 15.7%, WR 24.6%, TE 40.1%). Acceptance now uses
tie-aware coverage: the share of rows whose randomized PIT, drawn uniformly
between F(y-) and F(y) from the quantile knots with a fixed seed (0), lies in
(0.1, 0.9). The 75-85% band is unchanged. The same rule applies to every model.
Closed-interval coverage and both tails (below q10, tied at q10, tied at q90,
above q90) are still reported by position. This was changed after the
2022-2024 results were seen and before 2025 was opened; the model card says
so.

**D-086, per-position boom/bust calibration.** The isotonic boom/bust
calibrator was pooled across positions, and kicker boom/bust probabilities
were poorly calibrated (slopes 0.14-0.26). It becomes per position, fitted
walk-forward on earlier weeks only (pooled fallback when a position has too
few rows), so every reported result stays out-of-sample. The report shows
before and after for every position. The frozen calibrator for the holdout
and live use is fitted on 2022-2024 and saved with the candidate before 2025
is opened. If kicker slopes remain poor, the app hides kicker boom/bust or
marks it low confidence, and the model card discloses it.

**D-087.** The owner reviews the updated report before the holdout session.

## D-088 to D-090 Final calibration change before the holdout (owner, 2026-11-03)

Report v2 showed the chain passing tie-aware coverage at QB, RB, WR, TE and DST
but failing at K (0.740 calibrated, 0.819 raw), because the conformal step
still counted a score equal to a quantile as "at or below" it and so targeted
the closed-interval definition, not the tie-aware one adopted in D-085.
D-088: the calibrator is aligned with the acceptance rule. It uses the same
randomized-PIT tie handling and seed scheme as the metric, applied identically
to every position and model, fitted walk-forward on earlier weeks only. The
results are shown to be insensitive to the seed, and v3 shows before and
after. This is the last calibration change before the holdout. Any position
that still fails is frozen as is and disclosed. D-089: K and DST boom/bust
stay flagged under the pre-set low-confidence rule; no further post-hoc
choice is made for them. D-090: the owner reviews the v3 summary and gives
the go-ahead before 2025 is opened.

## D-091 to D-096 Holdout pre-registration (owner, 2026-11-04)

The owner approved the single 2025 holdout session on these conditions, all
fixed before 2025 is opened and recorded in docs/status/holdout-preregistration.md:
the commit, data and feature snapshot ids, model and calibrator hashes, seeds,
the 22 approval lines, and the calibration mode (D-091). A failure on 2025
means the candidate is not approved: nothing is tuned on 2025, the session is
not rerun for a better result, and no Week 9 projections are published as
approved (D-092). The only allowed rerun is after an infrastructure failure
that stops the session before any metric is read, with the same frozen inputs
and the reason logged (D-093). If 2025 passes but the 2026 forward check
misses the same rules, the orchestrator stops before publishing projections
and shows the owner the results (D-094). Calibration during both runs is
fixed (D-095), so that what is tested is exactly the frozen candidate. The
forward check covers 2026 weeks 1-7 because the frozen snapshot predates two
week 8 games (D-096). None of this is relaxed to make the Week 9 kickoff (D-021).

Correction recorded at the owner's request: from v2 to v3 the raw predictions
were unchanged (identical raw-row hash), but the calibrated quantiles and
coverage changed, and 15 calibrated verdict cells moved. It is not accurate to
say that accuracy was unchanged. The v2 TE tie-aware coverage was 0.791, as in
both committed reports. The figure 0.793 in an earlier summary was a
transcription error.

## D-097 Holdout outcome: chain_v1 not approved; Gate 3 fails (orchestrator, 2026-11-04)

The single 2025 holdout session (EV-3.4) ran under the pre-registration at
294aac0 and ended with `Verdict: NOT APPROVED` and `Forward check: PASS`
(reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md,
commit 824162d).

- 21 of 22 lines passed on 2025. L17 failed: QB tie-aware coverage was 0.739,
  outside [0.75, 0.85]. QB 80% ranges were too narrow, with 13.5% of outcomes
  below q10 and 11.6% above q90. Raw QB coverage was 0.693.
- All 22 lines passed on the 2026 week 1-7 forward check, with QB at 0.774.
  The forward check never changes the 2025 verdict (D-094).
- Against the FantasyPros consensus on 2025 (same player-weeks, calibrated
  MAE):
  - the chain is worse at QB, +0.263 [+0.099, +0.418];
  - it is worse at RB, +0.086 [+0.000, +0.172];
  - there is no detectable difference at WR, TE, K and DST.
- Start/sit against the consensus ties at QB, RB and WR and is better at TE.
- QB boom/bust probabilities were miscalibrated on 2025, with slopes of 0.56
  and 0.77, on top of the K and DST flags (D-089).
- One infrastructure rerun was used under D-093: the memory-pressure reaper
  killed the first run before any metric was read. The reason is in the
  evaluator's addendum.

Consequences, under D-092:
- chain_v1 is not approved;
- nothing is tuned on 2025;
- the session is not rerun;
- no Week 9 projections are published as approved.

`release_holdout` and `registry.approve` were not called. The registry entry
stays at status candidate, and `approve` cannot accept this report because it
lacks the `Verdict: APPROVED` line. Gate 3 fails. Phase 4 does not start
until the owner decides how to continue (PROMPT_1: "Do not start a phase
until the prior gate passes").

These are not options, because each would select or adjust on the holdout:
- accepting 0.739 as close enough;
- switching to `chain_v1_final_report` (QB 0.758 on 2025), `direct_lgbm`, or
  a baseline chosen after seeing 2025;
- recalibrating on 2025 and re-scoring 2025.

## D-098 Path after the failed holdout: chain_v2 and a live 2026 test (owner, 2026-11-04)

1. **chain_v1 stays not approved and unpublished.**

2. **chain_v2 makes only two changes:**
   - (a) 2025 is added to training. 2025 is released as training data and
     history. The release records the NOT APPROVED session and is not an
     approval.
   - (b) The calibrator becomes walk-forward: the v3 tie-aware quantile
     method and the per-position boom/bust calibrator, updating each week
     from earlier weeks only.

   Nothing is tuned against 2025. Stage choices (selection.json),
   hyperparameters, features 0.2.0, calibrator settings and seeds are
   unchanged.

3. **Before v2 is frozen, the Independent Evaluator estimates the chance that
   a well-calibrated model passes all 22 rules over 2026 weeks 10-18.** The
   estimate accounts for:
   - the number of games each week (14, 13, 16, 14, 15, 16, 16, 16, 16);
   - correlation within a week.

   The owner sees the estimate and decides the test design before anything
   is frozen.

4. **v2 is frozen and pre-registered before the Week 10 kickoff**
   (2026-11-13 01:15 UTC). If the review needs more time, the live test
   starts at the next full week instead.
   - Each week's projections are saved and hashed before that week's first
     kickoff.
   - They are scored only after the games finish.

5. **Safeguard.** Releasing 2025 removes the lock that currently blocks
   `ffml predict`. So before the release, live predictions are made to
   require an approved, live registry model. Every other run is a private
   test labeled "not approved" or a historical replay.

## D-099 Phase-order exception for Phase 4 and Phase 5 (owner, 2026-11-04)

As an owner exception to "Do not start a phase until the prior gate passes",
Phase 4 (App) and Phase 5 (Automation) work starts now, although Gate 3
failed. The limits:

- **Real app.** Projections, start/sit, waivers, trades and rest-of-season
  rankings are off and show "no approved model" until a model passes its
  pre-registered test.
- **Private test environments only.** Those screens may be built and tested
  with fixtures, historical replay, or v2 outputs labeled "not approved".
- **No approval claims.** No public recommendations and no claims of model
  approval until v2 passes.
- **Gate 4** stays open until an approved model is connected and tested end
  to end.
- **Phase 5** continues under the same limits.

## D-100 Approval gate for predictions (ML Engineer proposal, accepted by the orchestrator, 2026-11-04)

`ffml predict` has three modes, and every mode check runs before anything
is read or written.

- **Live, the default.**
  - Runs only `registry.get_live()`, which must carry an evaluator report.
  - Refuses `model_dir`, `model`, `model_version` and `chain_root`, and has
    no newest-artifact fallback.
  - With no live version it raises `NoApprovedModel` and the CLI exits 2.
  - Writes manifest `environment: "live"`, `approval_status: "approved"`.
- **Private test.**
  - Registry candidates only.
  - Writes only to `Settings.private_predictions_dir`, default
    `data/predictions_private`.
  - Labeled `approval_status: "not approved"`, `label: "not approved"`
    in the manifest, the private LATEST and an `approval_status` column.
- **Replay.**
  - Labeled `environment: "replay"`.
  - Never moves LATEST.

The registry gains a terminal `rejected` status, set by `reject()` from a
report carrying the exact line `Verdict: NOT APPROVED`. The API's production
gate (docs/API.md section 3, v1.1) serves only a LATEST run that meets all
of these:
- `environment` is "live" and the run is approved;
- its `model_version` equals the registry live version.

## D-101 to D-103 API conventions (Backend Engineer proposals, accepted by the orchestrator, 2026-11-04)

**D-101.** `FFML_APP_ENV` takes two values.
- `production`, also used for any unrecognised value: gated routes serve only
  under the D-100 conditions.
- `private_test`: gated routes serve the first source available, in this
  order:
  1. the private store;
  2. a replay;
  3. fixtures.
  Every response is labeled "not approved".

The gate runs before the request is parsed, so a gated route gives the same
answer whatever the request contains.

**D-102.** Request-time summaries follow the chain's convention: quantiles
and boom/bust are conditional on the player being active. Lineup and
replacement calculations use the unconditional mean. A player's trade value
is `max(0, ROS mean - replacement level)`.

**D-103.** Rest-of-season totals include the run week unless the request
says otherwise. Simulation i of the week is added to ROS simulation i.

Before Gate 4, request-time calibration is required (Backend proposal P-2
for the ML Engineer). The evaluator approves calibrated numbers, and the API
today serves raw simulation quantiles under league scoring, with a notice
saying so.

## D-104 QB boom/bust low confidence (orchestrator, 2026-11-04)

On the 2025 holdout (D-097) the chain's QB boom and bust probabilities had
calibration slopes of 0.56 and 0.77, both outside the D-086 rule. Until a
pre-registered test shows otherwise, the app marks QB boom/bust low
confidence, as it already does for K and DST (D-089). The API reads
`low_confidence_positions` from the run manifest when present and otherwise
uses {QB, K, DST}. The model card discloses it.

## D-105 to D-107 Live-test design, missed weeks, operations (owner, 2026-11-05)

Decided after the owner saw the Evaluator's pass-probability estimate
(reports/evaluator_live_test_power_2026w10-18.md): under the 22 lines as
written, a well-calibrated model with the validated accuracy passes 2026
weeks 10-18 about 10% of the time (4% after the variance correction), and
about 77% over 18 weeks; the 12 per-position "beats" lines drive the failures.

**D-105 Test design: resized rules chosen by pre-set limits.** The accuracy
lines are resized to the 9-week window; coverage and start/sit lines, the
metrics and the D-078 bootstrap are unchanged. The choice among candidate
rule sets is made by these limits, fixed before any candidate result was
seen:

1. Corrected estimates (the variance-corrected scenario) are used. A
   candidate qualifies only if a model with **no edge** passes at most 10%
   of the time and a **good** model (validated accuracy, well calibrated)
   passes at least 70% of the time.
2. Among qualifying candidates, the one with the lowest false-approval rate
   is chosen; if rates are close, the simpler design.
3. If none qualifies, the limits are not weakened: "no approved model"
   stays for the rest of 2026, and the unchanged 22 rules are applied over
   2026 weeks 10-18 plus 2027 weeks 1-9.
4. A pass under a resized design means **provisionally approved**, with
   claims limited to what the rules tested. The original 22 rules keep being
   reported through 2027 week 9; if that longer window fails, provisional
   approval is removed and the app returns to "no approved model".
5. The design is frozen before the Week 10 kickoff (2026-11-13 01:15 UTC).
   The owner sees the candidate table and the choice the limits select.

**D-106 Missed weeks.** One miss is allowed: the first missed week is
excluded from scoring and the window is not extended; a second miss makes
the test INVALID (not failed). Each deadline is the official schedule's
first kickoff of the week, including Thanksgiving and other early games.
Each run starts at least 24 hours before the deadline, with retries and a
failure alert. A miss counts only for a real technical failure or an offline
machine, and is logged with its reason. Projections that exist are never
withheld. With one week excluded, the frozen rules are unchanged and the
lower pass rate of an eight-week test is reported.

**D-107 Operations.** Logged-off scheduled tasks on the owner's laptop plus
a GitHub Actions watchdog:
- the weekly run starts at least 24 hours before first kickoff and retries
  every 30 minutes until a valid commitment is pushed;
- the GitHub watchdog alerts at 6 hours and at 1 hour before kickoff;
- the task has only the permissions it needs, no administrator rights
  without a shown reason, runs only this project's weekly job, and the
  owner's password is stored in no file or log;
- the task runs on AC power, wakes the laptop if allowed, and catches up
  after a missed start; required Windows sleep or power settings are shown
  to the owner;
- before Week 10, a logged-off dry run on a test branch proves build, push,
  retry, failure alert and watchdog; it does not count as a live commitment;
- the owner runs the registration command, shown and explained first.

## D-108 LightGBM thread count and determinism (ML Engineer diagnosis, orchestrator ruling, 2026-11-05)

Diagnosis: `test_default_feature_lists_are_unchanged` fails intermittently on
Linux CI because LightGBM with `num_threads: 8` on a 4-vCPU runner gives
run-to-run differences in 1-3 weighted cross-entropy stages (OpenMP
reduction order, inferred). Reproduced in WSL2 (9 of 10 repeats at 4 vCPUs
and 8 threads; 0 of 32 at 4 or 1 threads). Not reproducible on the
development machine, where real-data refits are byte-identical, and a 2026
week 4 refit with 4 threads or 1 thread equals the stored 8-thread rows.

Ruling: set `num_threads` to 4 in `ffml/models/chain/lgbm.py` **only if**
the four stored identity weeks (2022 w6, 2024 w15, 2025 w9, 2026 w4)
recomputed at 4 threads remain byte-identical to the stored raw rows; the
calibrator state for 2026 week 8 must also be unchanged. If any row
differs, threads stay at 8 and the CI test pins 1 thread for itself only.
This is a determinism fix with zero measured effect on predictions, not a
model change; the evidence goes in the chain_v2 pre-registration.

## D-109 Live-test timing constants (Data Engineer proposal, accepted, 2026-11-05)

K is the official first kickoff of the week from the schedule. The runner's
window opens at K-25h; the scheduled task first runs at K-24h15m so the
first attempt is at least 24 hours before K even with Task Scheduler's
start delay (D-106); it retries every 30 minutes until K-5m; no refresh
starts after K-3h, no build after K-30m, no push after K-5m, nothing is
pushed for the week after K. A commitment built before K but pushed late
goes under `<week>/late/` with the MISSED marker and is never withheld. The
GitHub watchdog runs hourly and alerts inside [6.5h, 5.5h) and [1.5h, 0)
before K. Rehearsals and dry runs never write to `livetest-2026`.

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

- Which candidate rule set the D-105 limits select (Evaluator report pending).
