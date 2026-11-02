# Sleeper API fixtures

Real responses from `https://api.sleeper.app/v1`, recorded once on
2026-11-02 01:43 UTC (Sunday evening 2026-11-01 US time, during NFL week 8 of
the 2026 season) with `curl -sS -A "ffml/0.1 (+https://github.com/ethansye19/fantasy-football-ml)"`.
All endpoints are public and read-only. Identifiers belong to the project
owner's own leagues.

| File | Endpoint | Notes |
| --- | --- | --- |
| `state.json` | `GET /state/nfl` | season 2026, week 8, regular season |
| `user_ethanye19.json` | `GET /user/ethanye19` | user_id `1257514331365462016` |
| `user_leagues_2026.json` | `GET /user/1257514331365462016/leagues/nfl/2026` | one league, in season |
| `user_leagues_2025.json` | `GET /user/1257514331365462016/leagues/nfl/2025` | three complete leagues (12, 12, 10 teams) |
| `league.json` | `GET /league/1389750342958223360` | 8 teams, PPR, 43 scoring keys (41 non-zero), 15 roster slots, playoffs start week 15 |
| `rosters.json` | `GET /league/1389750342958223360/rosters` | 8 rosters, 119 unique player ids including 9 team defenses |
| `users.json` | `GET /league/1389750342958223360/users` | 9 users (one is_owner, one with `is_owner: null`, some without team_name) |
| `matchups_week8.json` | `GET /league/1389750342958223360/matchups/8` | 8 entries, 4 matchup ids |
| `transactions_round1.json` | `GET /league/1389750342958223360/transactions/1` | 5 transactions: 2 waiver, 3 free_agent; includes null `adds`/`drops` |
| `trending_add.json` | `GET /players/nfl/trending/add?lookback_hours=24&limit=25` | includes team defenses (`WAS`, `TB`, `DET`) |
| `players_subset.json` | `GET /players/nfl` (14,663,896 bytes, 12,229 entries), filtered | see below |

## players_subset.json

The full map was fetched exactly once (Sleeper asks for at most one call per
day) into a scratch directory, then filtered to the 119 player ids that appear
on the 2026 league rosters. Entries are copied verbatim (keys sorted, compact
JSON). Composition: 43 WR, 34 RB, 13 QB, 12 TE, 9 DEF, 8 K; 22 entries have an
`injury_status` (Questionable, Out, IR). Nothing was added beyond the roster
ids because the roster set already contains defenses, kickers and injured
players.

Quirks preserved on purpose, because the client has to handle them:

- Team defenses (`KC`, `CIN`, ...) carry only nine keys and no `full_name`.
- `gsis_id` values such as `" 00-0035676"` have a leading space (866 of the
  12,229 entries in the full map).
- Only 26 of the 110 non-defense roster players have a `gsis_id`; 87 of the
  119 entries have no `espn_id`/`yahoo_id`. Sleeper's own crosswalk is thin for
  players who entered the league in 2020 or later, so `ff_playerids.sleeper_id`
  remains the primary join path (DATA_SOURCES.md).

## Not-found behaviour observed

- Unknown username: HTTP 200 with body `null`.
- Unknown league id (`/league/1`, `/league/1/rosters`): HTTP 404 with body `null`.

## Refreshing

Re-run the same curl commands with the project User-Agent and regenerate the
subset from a single `/players/nfl` download filtered to the roster ids. Keep
the directory under 300 KB.
