# Gridiron Projections (working title)

An original, ML-powered fantasy football analytics app: weekly projections with
floor / median / ceiling and boom-bust probabilities, start/sit comparisons with
explained drivers, waiver-wire rankings for a specific roster, a trade analyzer,
and rest-of-season rankings. League setup via Sleeper import or manual settings,
with custom scoring and roster rules applied at request time (no retraining).

**Status:** Phase 1 (Plan) complete on 2026-11-01. Nothing below the plan is built yet.
See [docs/PLAN.md](docs/PLAN.md) for phases and gates, and
[docs/DECISIONS.md](docs/DECISIONS.md) for the decision log and open questions.

## Documents

| Document | Purpose |
| --- | --- |
| [docs/PLAN.md](docs/PLAN.md) | Build plan: phases, gates, task lists per agent |
| [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md) | System architecture and diagrams |
| [docs/DATA_SOURCES.md](docs/DATA_SOURCES.md) | Verified data APIs, availability, update cadence, licenses |
| [docs/DATA_SCHEMA.md](docs/DATA_SCHEMA.md) | Parquet layers, processed tables, app database schema |
| [docs/FEATURES.md](docs/FEATURES.md) | Point-in-time feature list with lag rules |
| [docs/MODELING.md](docs/MODELING.md) | Model chain, baselines, validation protocol, metrics |
| [docs/AGENTS.md](docs/AGENTS.md) | Agent team charter, handoff protocol, evaluator independence |
| [docs/DECISIONS.md](docs/DECISIONS.md) | Decision log, assumptions, open questions |

## Data attribution (summary)

Play-by-play, player and team statistics, schedules, rosters, injuries, depth
charts, Next Gen Stats, and Pro Football Reference snap counts are provided by
the [nflverse](https://nflverse.nflverse.com/) project under CC-BY-4.0.
FTN charting data, if used, is CC-BY-SA-4.0 and must be attributed to
"ftndata.com via nflverse". League data comes from the public Sleeper API
(non-commercial use). A full attribution page ships with the app.

Nothing in this project places bets or presents any pick as guaranteed.
