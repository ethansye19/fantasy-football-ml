# API contract (Phase 4)

Owner: Backend Engineer. Consumers: the web app (`web/`), Playwright tests, QA.
Status: contract v1.3, 2026-11-04. Every endpoint below is implemented and
tested (tests/api). Changes are recorded in the changelog at the end.

Code: `api/` (FastAPI, Pydantic v2, SQLAlchemy 2). Run locally:

```
.venv/Scripts/uvicorn api.main:app --reload                     # production rules (default)
FFML_APP_ENV=private_test .venv/Scripts/uvicorn api.main:app    # private test rules
```

All responses are JSON (UTF-8). Times are ISO 8601 UTC. Ids are strings.

## 1. Why projections are switched off (D-097, D-098, D-099)

No model is approved: the chain_v1 candidate failed its pre-registered 2025
holdout test. The owner allowed Phase 4 to start as a logged exception with
these limits, which the API enforces:

- In the real app (`production`), every projection-based feature returns a
  clear "no approved model" state until a model passes its pre-registered test:
  weekly projections, start/sit, waivers, trades, rest-of-season rankings.
- Only in private test environments (`private_test`) may those features serve
  fixtures, historical replays, or v2 outputs, and every such response is
  labeled "not approved".
- No public recommendations and no claim of model approval.
- League import, league settings, meta, attribution and health work normally
  in both environments.
- FantasyPros/ECR data is never read or served by the API (D-007, D-051).

## 2. Environments and settings

| Variable | Default | Meaning |
| --- | --- | --- |
| `FFML_APP_ENV` | `production` | `production` or `private_test`. Any other value is treated as `production`. |
| `FFML_DATABASE_URL` | `sqlite:///data/app.db` | SQLAlchemy URL. PostgreSQL in compose and CI (`postgresql+psycopg://...`). |
| `FFML_DB_AUTO_MIGRATE` | `true` | Run Alembic `upgrade head` at startup. |
| `FFML_CORS_ORIGINS` | `["http://localhost:3000","http://127.0.0.1:3000"]` | JSON list of allowed browser origins. |
| `FFML_API_FIXTURE_PREDICTIONS_DIR` | unset | `private_test` only: a predictions store holding fixture runs. |
| `FFML_API_REPLAY_RUN` | unset | `private_test` only: pin one replay as `<season>/<week>/<run_id>`. |
| `FFML_APPROVAL_TTL_SECONDS` | `5` | How long one gate evaluation is reused. |
| `FFML_BODY_MAX_BYTES` | `262144` | Largest accepted request body (413 above it; `0` disables the check). |
| `FFML_DATA_DIR`, `FFML_ARTIFACTS_DIR` | repo `data/`, `artifacts/` | Shared with the pipeline (`ffml.config`). |

## 3. The approval gate

### 3.1 Gated routes

| Method | Path | Task |
| --- | --- | --- |
| GET | `/projections` | BE-4.3 (preset scoring, no league) |
| GET | `/ros` | BE-4.7 (preset scoring, no league) |
| GET | `/leagues/{league_id}/projections` | BE-4.3 |
| POST | `/leagues/{league_id}/start-sit` | BE-4.4 |
| GET | `/leagues/{league_id}/waivers` | BE-4.5 |
| POST | `/leagues/{league_id}/trades/evaluate` | BE-4.6 |
| GET | `/leagues/{league_id}/ros` | BE-4.7 |

The gate runs before the request is parsed, so a gated route answers the
blocked response below even for an unknown league or an invalid body.

### 3.2 production

A gated route serves data only when **all** of these hold:

1. the model registry (`artifacts/models/REGISTRY.json`) has a live version
   (`ffml.models.registry.get_live()`) whose evaluator report exists under the
   reports directory and carries the exact line `Verdict: APPROVED`
   (`ffml.models.registry.check_report`); a path alone is not an approval;
2. `data/predictions/LATEST` points at a readable run whose `_manifest.json`
   has `approval_status == "approved"` (top level, or under `extra` for older
   manifests) and `environment == "live"`. The manifest `environment` key is
   the prediction mode written by `ffml predict` (`"live"`, `"private_test"`
   or `"replay"`), not the app environment; `"private_test"` and `"replay"`
   runs are refused, and a missing key is accepted only for older manifests,
   together with all the other conditions (a run whose `mode` is `replay` is
   refused either way). If `LATEST` itself carries `approval_status`,
   `environment` or `model_version`, they must agree;
3. that manifest's `model_version` equals the live version.

Anything missing, unreadable or malformed means no serving: a missing
`approval_status` counts as not approved. The private store, replays and
fixtures are never consulted in production.

Blocked response (every gated route, any request):

```
HTTP/1.1 503 Service Unavailable
X-FFML-Approval: none
Cache-Control: no-store

{"status": "no_approved_model",
 "message": "No approved model. Projections are off until a model passes its pre-registered test."}
```

When the gate passes, responses carry `X-FFML-Approval: approved` and
`"approval_status": "approved"`. That cannot happen until a model passes its
pre-registered test and the Independent Evaluator approves it.

### 3.3 private_test

Gated routes serve the first readable source in this order:

1. the private-test store `data/predictions_private/LATEST`
   (`ffml.config.Settings.private_predictions_dir`; manifests carry
   `environment: "private_test"`, `approval_status: "not approved"`,
   `label: "not approved"`);
2. a replay run: `FFML_API_REPLAY_RUN` if set, else the newest run whose
   manifest says `mode: "replay"` in the private or public store (a replay is a
   past week predicted from information available before it, for example
   2024 week 10);
3. the fixture store `FFML_API_FIXTURE_PREDICTIONS_DIR`, if set.

Every response in `private_test` (gated or not) has the header
`X-FFML-Approval: not-approved`, and every projection-derived body has
`"approval_status": "not approved"` and `"label": "not approved"`. The API never
emits `"approved"` in `private_test`, whatever a manifest says. With no source
the gated routes return:

```
HTTP/1.1 503
{"status": "no_private_test_snapshot", "message": "...",
 "approval_status": "not approved", "label": "not approved"}
```

### 3.4 Envelope of every projection-derived response

```json
{
  "approval_status": "not approved",
  "label": "not approved",
  "environment": "private_test",
  "run": {
    "source": "replay",
    "run_id": "2024w10-20261103T010442Z-replay",
    "season": 2024,
    "week": 10,
    "model_version": "chain_v1",
    "written_at": "2026-11-03T01:04:55Z",
    "n_sims": 2000,
    "is_replay": true,
    "has_rest_of_season": true
  },
  "notices": [
    "Not approved: these numbers come from a model that has not passed its pre-registered test. Private test environment only; not a recommendation.",
    "Historical replay of 2024 week 10: ..."
  ],
  "...route fields...": "..."
}
```

`run.source` is one of `published`, `private_test`, `replay`, `fixture`. The UI
must show `label` and the `notices` prominently whenever `approval_status` is
not `"approved"`.

## 4. Errors

All errors other than the gate's 503 use one envelope:

```json
{"error": {"code": "league_not_found", "message": "No league with id ...", "details": null}}
```

| HTTP | code | when |
| --- | --- | --- |
| 404 | `not_found`, `league_not_found`, `team_not_found`, `week_not_available` | an unknown resource named in the path or query (404 is reserved for those); `details` may list valid choices |
| 422 | `player_not_found` | unknown player ids in a request body (manual-league `teams[].players`, start/sit `player_ids`); `details.player_ids` lists them |
| 404 | `sleeper_not_found` | Sleeper has no such user or league |
| 409 | `multiple_leagues` | a username with several leagues and no `league_id`; `details.leagues` lists them |
| 413 | `payload_too_large` | request body over `FFML_BODY_MAX_BYTES` (256 KiB by default), checked before parsing |
| 409 | `no_team_selected` | waivers or trades without a "my team" choice or `league_team_id` |
| 409 | `not_a_sleeper_league` | sync on a manual league |
| 409 | `offseason` | start/sit or weekly projections when no regular-season game remains (live runs only) |
| 422 | `invalid_trade` | trade sides overlap or players are not on the stated rosters |
| 422 | `validation_error` | invalid input; `details` is a list of `{loc, msg, type}` |
| 422 | `invalid_scoring`, `invalid_roster_positions` | settings that cannot be normalized |
| 502 | `sleeper_unavailable` | Sleeper failed after retries |
| 503 | `no_approved_model` | gate (3.2); body shape is the fixed one above, not this envelope |
| 500 | `internal_error` | anything unexpected (no stack traces in responses) |

## 5. Meta endpoints (never gated)

### GET /meta/health

```json
{"status": "ok", "version": "0.1.0", "environment": "production",
 "checks": {"database": "ok", "projections": "off"},
 "time": "2026-11-04T20:00:00+00:00"}
```

Always HTTP 200 while the process runs; `status` is `degraded` if a check
errors. `checks.projections` is `serving` or `off`.

### GET /meta/approval

```json
{"environment": "production", "serving": false, "approval_status": "not approved",
 "label": "not approved", "reason": "no_live_model",
 "message": "No approved model. Projections are off until a model passes its pre-registered test.",
 "checked_at": "...", "run": null, "live_model_version": null}
```

`reason` codes (production): `no_live_model`, `registry_unreadable`,
`registry_malformed`, `live_model_without_approval`, `no_latest`,
`latest_malformed`, `pointer_malformed`, `manifest_missing`,
`manifest_malformed`, `manifest_mismatch`, `snapshot_unreadable`,
`snapshot_incomplete`, `snapshot_not_approved`, `pointer_not_approved`,
`model_version_mismatch`, `snapshot_not_live`, `snapshot_is_replay`,
`approved_live_model`. private_test: `private_test_private_test`,
`private_test_replay`, `private_test_fixture`, `no_private_test_snapshot`.
`live_model_version` is present in production only. `run` is the envelope's
`run` object when serving, else null.

### GET /meta/attribution

```json
{"sources": [
  {"id": "nflverse", "name": "nflverse", "license": "CC-BY-4.0",
   "url": "https://github.com/nflverse/nflverse-data", "in_use": true,
   "text": "Data from nflverse (github.com/nflverse/nflverse-data), CC-BY-4.0. Modified: aggregated and modeled by this project.",
   "notes": ["Next Gen Stats originate from NFL Next Gen Stats ...", "Snap counts from Pro Football Reference ..."]},
  {"id": "ffopportunity", ...}, {"id": "sleeper", ...},
  {"id": "ftn", "name": "FTN charting", "license": "CC-BY-SA-4.0", "in_use": false,
   "text": "FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0.", ...}
 ],
 "usage": "Personal, non-commercial use (D-013).",
 "not_displayed": "Expert consensus rankings are used only in private model evaluation and are never shown in this app."}
```

FantasyPros is not listed as a displayed source. FTN wording is always
present, with `in_use` showing whether FTN features are on (D-009).

### GET /meta/model-card

Structured summary for the about page: model name and version, registry
status, the latest evaluator verdict, what the model predicts (football chain,
scoring applied at request time), evaluation summary, known limitations (K
and DST boom/bust are low confidence, D-089; the chain_v1 holdout failure,
D-097), and links to the committed reports. No consensus comparison rows.

### GET /meta/run-info

```json
{"environment": "production", "serving": false, "approval_status": "not approved",
 "label": "not approved", "run": null, "message": "..."}
```

When serving, `run` is the envelope's run object plus `seeds`, `as_of`,
`availability_config` and `rest_of_season_weeks` from the manifest.

### GET /meta/season

```json
{"season": 2026, "mode": "regular", "projection_week": 10, "week_in_progress": false,
 "offseason": false, "banner": null, "as_of": "..."}
```

`mode` is `regular`, `postseason`, `offseason` or `unknown` (no schedule
snapshot). `offseason` is `true` in `postseason` and `offseason`, `false` in
`regular`, and `null` when the mode is `unknown` (the UI keys on `mode` and
shows "season status unknown"). In `postseason`/`offseason` `banner` explains
that weekly features are paused and rankings are season-level.

## 6. Leagues (never gated)

### League object

```json
{
  "id": "6f1c...-uuid",
  "source": "sleeper",
  "external_id": "1389750342958223360",
  "name": "Bgr fantasy leuge",
  "season": 2026,
  "num_teams": 8,
  "scoring": {
    "settings": {"pass_td": 4.0, "rec": 1.0, "...": 0},
    "hash": "3f2a9c1d0b7e6a55",
    "unsupported_keys": ["st_fum_rec"],
    "preset_like": "ppr"
  },
  "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN", "BN", "BN"],
  "starting_slots": {"QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 1, "K": 1, "DEF": 1},
  "settings": {"playoff_week_start": 15, "waiver_type": 0, "trade_deadline": 14},
  "my_team_id": "a9d2...-uuid",
  "created_at": "...", "updated_at": "...", "last_synced_at": "...",
  "teams": [
    {"id": "a9d2...-uuid", "external_roster_id": "3", "team_name": "Team Ethan",
     "owner_name": "ethanye19", "owner_external_id": "1257514331365462016", "is_me": true,
     "players": [
       {"player_id": "00-0036322", "sleeper_id": "6794", "name": "Justin Jefferson",
        "position": "WR", "team": "MIN", "slot": "starter", "injury_status": null}
     ]}
  ]
}
```

- `scoring.settings` uses Sleeper key names with zero entries removed
  (D-024). `unsupported_keys` are ignored by the scoring engine
  (`ffml.scoring.rules`). `hash` changes only when a supported key changes.
- `slot` is `starter`, `bench`, `ir` or `taxi`.
- `player_id` is the nflverse gsis id (team code for defenses), null when the
  crosswalk has no match; `sleeper_id` is always kept.

### GET /sleeper/users/{username}/leagues?season=2026

```json
{"user": {"user_id": "1257514331365462016", "username": "ethanye19", "display_name": "ethanye19"},
 "season": 2026,
 "leagues": [{"league_id": "1389750342958223360", "name": "Bgr fantasy leuge", "season": 2026,
              "num_teams": 8, "status": "in_season", "preset_like": "ppr"}]}
```

`season` defaults to the current season. 404 `sleeper_not_found` for an
unknown user.

### POST /leagues/import/sleeper

Body: `{"league_id": "1389750342958223360"}` or `{"username": "ethanye19", "season": 2026}`
(or both: the username then marks that user's roster as "my team").
Returns **201** with the League object. Importing a league that already exists
(same Sleeper id and season) re-syncs it and returns 200. 409
`multiple_leagues` when a username has several leagues and no `league_id`
(`details.leagues` as in the listing above). Read-only: nothing is written to
Sleeper (D-020).

### POST /leagues (manual league)

```json
{
  "name": "Office league",
  "season": 2026,
  "num_teams": 10,
  "scoring": {"preset": "half", "overrides": {"pass_td": 6}},
  "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN", "BN", "BN", "BN", "BN"],
  "settings": {"playoff_week_start": 15},
  "teams": [{"name": "My team", "is_me": true, "players": ["00-0036322", "KC"]}]
}
```

`scoring` is either `{"preset": "ppr"|"half"|"std", "overrides": {...}}` or
`{"settings": {<Sleeper key>: points}}`. League `settings` accepts only the
known keys (`num_teams`, `playoff_week_start`, `playoff_teams`, `start_week`,
`leg`, `last_scored_leg`, `trade_deadline`, `waiver_type`, `waiver_budget`,
`reserve_slots`, `taxi_slots`, `best_ball`, `type`, `status`) with scalar
values: booleans, numbers within one million, strings up to 100 characters,
or null; anything else is 422. Roster slots: `QB, RB, WR, TE, FLEX,
SUPER_FLEX, WRRB_FLEX, REC_FLEX, K, DEF, BN, IR, TAXI` (IDP slots are accepted
and ignored). `players` accepts gsis ids, Sleeper ids or team codes. Returns
201 with the League object.

### Other league routes

| Method | Path | Body | Returns |
| --- | --- | --- | --- |
| GET | `/leagues` | | `{"leagues": [League without teams]}` |
| GET | `/leagues/{id}` | | League |
| PUT | `/leagues/{id}/settings` | `{"name"?, "scoring"?, "roster_positions"?, "settings"?}` | League (custom scoring and roster settings for any league) |
| PUT | `/leagues/{id}/my-team` | `{"league_team_id": "..."}` | League |
| POST | `/leagues/{id}/sync` | | League (re-reads Sleeper rosters; 409 for manual leagues) |
| DELETE | `/leagues/{id}` | | 204 |

### GET /players?search=&position= (never gated)

Player lookup for league setup. Returns identity only: no projections, no
gate, no restricted data.

```json
{"query": "jeff", "position": null,
 "players": [{"player_id": "00-0036322", "name": "Justin Jefferson", "position": "WR", "team": "MIN"}]}
```

`search` is required, 2 to 60 characters, matched case-insensitively against
the name (an exact gsis id, team code or team alias such as `LAR` also
matches); `position` is one of `QB|RB|WR|TE|K|DST`; `limit` 1-50 (default 50).
Order: names starting with the query (any word) first, then the most recently
active players, then players with a team, then alphabetical.
Defenses appear as `<team> D/ST` with the team code as `player_id`. Responses
carry `Cache-Control: public, max-age=300`.

## 7. Projection routes (gated, section 3)

All take the league's scoring (or `scoring=ppr|half|std` on the preset
routes) and apply it to the simulated stat lines at request time (D-003).
Results are cached in memory per run and scoring hash.

### Player row (shared)

```json
{
  "player_id": "00-0036322", "sleeper_id": "6794", "name": "Justin Jefferson",
  "position": "WR", "team": "MIN", "opponent": "JAX", "game_id": "2024_10_MIN_JAX",
  "floor": 8.1, "median": 15.2, "ceiling": 25.3, "mean": 16.0, "expected": 15.7,
  "boom_prob": 0.31, "bust_prob": 0.05, "active_prob": 0.98,
  "boom_bust_confidence": "normal", "low_sample": false,
  "position_rank": 3, "injury_status": null,
  "drivers": [
    {"group": "opportunity", "points": 10.1,
     "text": "Gets a large share of his team's targets and carries (+10.1 pts vs an average WR)."}
  ],
  "roster": {"league_team_id": "...", "team_name": "Team Ethan", "is_mine": true}
}
```

`floor` is the 10th percentile, `median` the 50th and `ceiling` the 90th of
simulated points under the league scoring, and `mean` their average, all over
the simulations in which the player is active (D-067); `active_prob` is the
share of those simulations and `expected` the mean including games missed
(the value used for lineups, waivers and trades). `low_sample` marks players
active in fewer than 100 simulations; below 20 their conditional fields are
null. Quantiles are raw simulation quantiles under the league scoring (the
model's calibration is fitted on reference scorings and is not applied at
request time; `scoring.quantiles` says so). `boom_prob` is the chance of a
top-N positional finish within the simulation and `bust_prob` the chance of
finishing outside the startable range (QB 6/18, RB 12/36, WR 12/48, TE 6/18,
K and DST 6/20; D-017). `boom_bust_confidence` is `low` for positions whose
boom/bust calibration is not trusted (D-104): the run manifest's
`low_confidence_positions` when present, else QB, K and DST (K and DST by
D-089; QB because its boom/bust slopes were 0.56 and 0.77 on the 2025
holdout, D-097). The set in force is returned as `boom_bust_low_confidence`
on the projections and start/sit responses. `drivers` are plain-language explanations of the projection
relative to a position-average player (groups: opportunity, efficiency,
matchup, environment, availability), largest first. `roster` is null for free
agents and on preset routes.

### GET /leagues/{league_id}/projections (BE-4.3)

Query: `week` (default: the week of the run being served; once runs cover
several weeks the default is the latest published run's week, and
`details.available_weeks` on a 404 `week_not_available` lists what can be
asked for), `position`
(`QB|RB|WR|TE|K|DST`), `search` (name substring), `availability`
(`all|rostered|free_agents|mine`, default `all`), `sort`
(`median|mean|ceiling|floor|boom|bust`, default `median`), `limit` (1-500,
default 100), `offset`.

```json
{...envelope, "league_id": "...", "season": 2024, "week": 10,
 "boom_bust_low_confidence": ["DST", "K", "QB"],
 "scoring": {"hash": "...", "unsupported_keys": [], "preset_like": "ppr",
             "drivers_reference": "ppr", "quantiles": "Raw simulation quantiles ..."},
 "total": 512, "players": [PlayerRow, ...]}

`sort=bust` sorts ascending (safest first); every other sort is descending.
In offseason or postseason mode, weekly projections and start/sit return 409
`offseason` for live runs (replays and fixtures are historical and exempt).
```

`GET /projections?scoring=ppr` has the same shape without `league_id` and
`roster`.

### POST /leagues/{league_id}/start-sit (BE-4.4)

Body: `{"player_ids": ["00-0036322", "00-0035676"], "week": 10}` (2-4 distinct
ids; `week` optional).

```json
{...envelope, "week": 10,
 "players": [PlayerRow, ...],
 "pairwise": [{"a": "00-0036322", "b": "00-0035676", "p_a_beats_b": 0.62}],
 "p_highest": {"00-0036322": 0.62, "00-0035676": 0.38},
 "recommendation": {"player_id": "00-0036322", "strength": "lean",
                    "text": "Lean Justin Jefferson: he outscores Amon-Ra St. Brown in 62% of simulations."},
 "explanation": ["Jefferson's edge comes mostly from opportunity (+3.1 pts) ...", "..."],
 "disclaimer": "No guarantees: these are probabilities from simulations of possible outcomes, not predictions of what will happen. Use them as one input to your own decision."}
```

`strength` is `toss-up` (top probability below 0.55), `lean` (0.55-0.70) or
`clear` (above 0.70). Probabilities come from paired simulations, so
teammates and opponents keep their correlation.

### GET /leagues/{league_id}/waivers (BE-4.5)

Query: `league_team_id` (default: my team; 409 `no_team_selected` without
either), `position`, `limit` (1-100, default 25).

```json
{...envelope, "league_id": "...", "scoring": {...}, "league_team_id": "...", "week": 10,
 "weeks": [10, 11, 12, 13, 14, 15, 16, 17, 18], "open_roster_spot": false,
 "options": [
   {"add": PlayerRow, "drop": {"player_id": "...", "name": "...", "position": "TE", "team": "...", "lineup_loss": 0.4},
    "gain_next_week": 1.2, "gain_rest_of_season": 8.4, "score": 9.6, "starts": 6,
    "reason": "Would start at FLEX in 6 of 9 remaining weeks; adds 8.4 expected lineup points."}
 ],
 "drop_candidates": [{"player_id": "...", "name": "...", "position": "...", "team": "...", "lineup_loss": 0.4}],
 "disclaimer": "..."}
```

Free agents are players with projections who are on no roster in the league.
Gains are changes in the roster's optimal starting lineup (league slots and
flex eligibility) per week, summed over the remaining weeks, after dropping
the suggested player (the best of the three lowest-loss players, or none when
the roster has an open spot). `score` = `gain_rest_of_season` (which includes
this week) + `gain_next_week`, so immediate help ranks higher. Options with no
gain are left out.

### POST /leagues/{league_id}/trades/evaluate (BE-4.6)

Body: `{"give": ["00-..."], "receive": ["00-..."], "league_team_id": "...", "partner_team_id": "..."}`
(`league_team_id` defaults to my team; `partner_team_id` defaults to the
roster that holds the `receive` players).

```json
{...envelope, "league_id": "...", "scoring": {...},
 "league_team_id": "...", "partner_team_id": "...",
 "weeks": [10, 11, 12, 13, 14, 15, 16, 17, 18],
 "give": [{"player": PlayerRow, "ros_mean": 120.4, "value_over_replacement": 31.2, "trade_value": 31.2}],
 "receive": [{"player": PlayerRow, "ros_mean": 131.0, "value_over_replacement": 40.8, "trade_value": 40.8}],
 "value": {"give": 31.2, "receive": 40.8, "net": 9.6},
 "roster_fit": {"mine": {"before": 1040.2, "after": 1052.9, "change": 12.7},
                "partner": {"before": 990.1, "after": 985.0, "change": -5.1}},
 "replacement_level": {"QB": 160.2, "RB": 80.1, "WR": 85.3, "TE": 60.0, "K": 70.2, "DST": 65.1},
 "fairness": {"band": "favors_you", "difference": 9.6,
              "text": "This trade favors you by about 10 rest-of-season points over replacement."},
 "verdict_text": "This trade favors you ... Your best weekly lineups would gain 12.7 points over weeks 10-18.",
 "disclaimer": "No guarantees: ..."}
```

`band` is `fair`, `favors_you`, `favors_partner`, `lopsided_for_you` or
`lopsided_for_partner`. Rest-of-season value is points over the league's
replacement level at the position (the best free agent, or the rank implied
by league size and starting slots when no roster data exists). `value` sums
`trade_value` = max(0, value over replacement) per side, because a player
below replacement can be replaced from free agency at no cost; `fairness`
compares the two sums (fair when the gap is under 5 points or 10% of the
larger side, lopsided above 35%). 422 `invalid_trade` when a player is on both
sides, the received players are not all on one other roster, or a player is
not on the stated roster.

### GET /leagues/{league_id}/ros (BE-4.7)

Query: `position`, `availability`, `limit` (1-500), `offset`,
`include_current_week` (default `true`).

```json
{...envelope, "league_id": "...", "scoring": {...}, "first_week": 10, "last_week": 18,
 "replacement_level": {"QB": 160.2, "...": 0}, "total": 512,
 "players": [{"player_id": "...", "sleeper_id": "...", "name": "...", "position": "WR", "team": "MIN",
              "ros_mean": 140.2, "ros_floor": 101.3, "ros_median": 139.0, "ros_ceiling": 178.5,
              "games_remaining": 9, "bye_weeks": [], "value_over_replacement": 50.1,
              "overall_rank": 4, "position_rank": 2, "roster": null}]}
```

Totals cover the run week (when `include_current_week`) plus the rest-of-season
simulation weeks; week totals of simulation i are added to rest-of-season
simulation i. `GET /ros?scoring=ppr` has the same shape without `roster`, with
replacement ranks for a 12-team 1QB/2RB/2WR/1TE/1FLEX/K/DST league. Rankings update
whenever a new run is published (weekly).

## Changelog

- v1 (2026-11-04): first contract; gate, meta health and approval implemented.
- v1.3 (2026-11-04), BE-4.9 rulings:
  - `boom_bust_confidence` is `low` for the manifest's `low_confidence_positions`,
    default QB, K, DST (D-104); responses carry `boom_bust_low_confidence`.
  - `/meta/model-card` `models[].registry_status` is one of `candidate`, `rejected`,
    `retired`, `awaiting_release`, `live` (never the bare word "approved"); `serving` added.
  - `player_not_found` is 422 for body ids (manual league, start/sit) with
    `details.player_ids`; 404 is reserved for path and query resources.
  - New ungated `GET /players?search=&position=`.
  - `/meta/season` returns `offseason: null` when `mode` is `unknown`.
  - Week default documented: the served run's week; the latest published run once
    runs cover several weeks.
- v1.2 (2026-11-04): all endpoints implemented. Added fields: player row `expected`,
  `low_sample`, `injury_status`; `scoring.drivers_reference` and `scoring.quantiles`;
  waivers `open_roster_spot`, option `starts`; trade `league_team_id`, `partner_team_id`,
  per-player `trade_value`, `verdict_text`; ROS `replacement_level`, `total`, `sleeper_id`.
  Error code `invalid_trade`. Gated routes now set `X-FFML-Approval` on their error
  responses too.
- v1.1 (2026-11-04): section 3.2 condition 2 corrected: the manifest `environment` is the
  prediction mode; production requires `"live"` (missing accepted for older manifests) and
  refuses `"private_test"` and `"replay"`. Reason code `snapshot_not_production` renamed
  `snapshot_not_live`.
