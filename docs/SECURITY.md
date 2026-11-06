# Security and QA review (QA-4.1)

Owner: QA and Security Reviewer. Review date: 2026-11-04; re-reviewed
2026-11-05 (QA-4.2) against `main` at `2cba0f4`, after the Backend's fixes
68b2e5d to b8ad1ef and with the frontend's contract v1.3 work in progress. Scope: the FastAPI backend in `api/`, the Next.js app in
`web/`, the Docker image and compose file, CI, the repository history and
the dependency locks. Phase 4 runs under the owner exception D-099: no model
is approved, production must answer every projection route with 503
`no_approved_model`, private_test output is labeled "not approved", and
FantasyPros/ECR data never reaches the app (D-007).

Findings are filed for the owning role; QA does not edit `api/`, `web/` or
`ffml/`. While a finding is open, the test that shows it is a **strict
xfail** named in the table; the fixing commit removes the marker and the test
stays as a regression test. Status on 2026-11-05: every Backend finding is
fixed (commit in the status column) and its tests pass without markers. Open:
QA-F10 (accepted), QA-F14 (Gate 6), QA-F15 and QA-F16 (Frontend), QA-F18
(dev tooling), QA-F19 (dev machine). Section 11 adds the live-test watchdog
review (QA-F23 to QA-F25).

## 1. Findings, by severity

| id | severity | finding | where | owner | tracked by | status |
| --- | --- | --- | --- | --- | --- | --- |
| QA-F01 | High | The production gate accepts any truthy `evaluator_report` on the live registry entry. It does not check that the report exists under `reports/` and carries the exact line `Verdict: APPROVED`. With a hand-edited or corrupted `REGISTRY.json` (status `live`, report saying `Verdict: NOT APPROVED`) and an approved-looking `LATEST`, production serves projections labeled `"approved"`. Fix: call `ffml.models.registry.check_report(entry["evaluator_report"])` in `_production()` and refuse on `RegistryError` (reason `live_model_without_approval`). The backend's own approved-path fixture (`tests/api/api_factory.py` `live_entry`) points at a report that does not exist and must write one. | `api/approval.py:329` | Backend | `tests/api/test_qa_gate_fuzz.py::test_live_entry_needs_an_approval_verdict_on_disk` (3 cases) | fixed 68b2e5d |
| QA-F02 | Medium | No request body size limit, and free-form league `settings` values are unbounded (`dict[str, Any]`, 50 keys, any value). A 20 MB string was accepted (201), stored, and is returned by every `GET /leagues` and `GET /leagues/{id}`. Fix: reject bodies over 256 KiB with 413 (same limit as the web proxy) in a small ASGI middleware that checks `Content-Length` and counts streamed bytes; restrict `settings` to the known Sleeper keys with scalar values (strings at most 100 chars). | `api/schemas.py:71`, `:79`; `api/main.py` (no limit) | Backend | `test_qa_input_validation.py::test_oversized_body_is_refused`, `::test_free_form_setting_values_are_bounded` | fixed b9bc159 |
| QA-F03 | Medium | No inbound rate limiting and no hook for one. Every route can be called without bound; Sleeper-backed routes amplify (up to 6 Sleeper calls per import), and each new scoring hash costs about 1 s of CPU to score (cache of 8 runs x scorings, so alternating scorings thrash it). Design in section 5. | `api/main.py` (`create_app`) | Backend | section 5 (design); QA adds 429 tests once the hook exists | fixed f988e60 |
| QA-F04 | Medium | D-025 (one shared Sleeper client per process, 300 requests per minute, burst 30) is not enforced by the API: `sleeper_client()` builds a new `SleeperClient`, with a fresh full token bucket, for every request. Concurrent imports can exceed Sleeper's limit and get the server's IP blocked. Fix: one process-wide client (or at least one shared `TokenBucket`) created in `create_app` and closed in the lifespan. | `api/deps.py:24-29`, `api/main.py:61` | Backend | `test_qa_rate_limits.py::test_sleeper_budget_is_shared_across_requests` | fixed fbc6fa3 |
| QA-F14 | Medium (deployment blocker) | No authentication or ownership (D-011 defers accounts; the signed session cookie is not built). Any client that reaches the API can list every league with Sleeper owner names and ids (`GET /leagues`), change settings, and delete any league. Acceptable for one person on localhost; not for any shared or public deployment (Gate 6). Fix before deployment: the D-011 session cookie scoping leagues to a browser, or bind the API to localhost behind the web app only. | `api/routers/leagues.py:70-113` | Backend, Orchestrator | design item; deployment guide | open: Gate 6 (deployment) |
| QA-F20 | Medium | CI never runs the web checks. `web/tests/unit/wording.test.ts` and the approval-mode tests are the frontend's compliance guards, and `npm run lint`, `typecheck`, `test` and `build` run only on the developer machine. The pytest scans in section 4 cover the copy rules in CI, not the UI logic. Proposal P-CI-WEB in `docs/status/phase4-qa.md`. | `.github/workflows/ci.yml` | Orchestrator (QA can add the job) | proposal | fixed QA-4.2 (`web` job) |
| QA-F05 | Low | Scoring weights accept `Infinity`, `-Infinity` and overflowing literals such as `1e309` (Python's JSON parser accepts them, Pydantic floats allow inf). They are stored as `inf` and shown as `null`, so the league page and the stored rules disagree; projections under that scoring would be inf or NaN. Only NaN is rejected. Fix: `Field(allow_inf_nan=False)` and a sane bound (for example `abs(weight) <= 100`) on `overrides` and `settings` values; the web form already bounds them (`web/lib/league-form.ts`). | `api/schemas.py:55-56`; `ffml/data/sleeper.py:473-478` | Backend | `test_qa_input_validation.py::test_non_finite_scoring_weights_are_refused` (3 cases) | fixed e30c8f9 |
| QA-F06 | Low | `GET /sleeper/users/{username}/leagues` validates with `str.isalnum()`, which is Unicode-aware, so names such as `café`, Arabic-Indic digits or full-width letters are sent to Sleeper. The import body uses `^[A-Za-z0-9_]{1,40}$`. Fix: use the same ASCII pattern on the path parameter. | `api/routers/leagues.py:30` | Backend | `test_qa_input_validation.py::test_lookup_refuses_non_ascii_usernames` (3 cases) | fixed e30c8f9 |
| QA-F07 | Low | Validation errors echo the full input: an unknown roster slot of 1 MB produced a 1 MB 422 body, and unknown ids are echoed with `repr`. Not an injection (JSON), but it amplifies responses. Fix: truncate echoed values (for example 40 chars) and cap the list of unknown slots. | `ffml/data/sleeper.py:508`; `api/services/leagues.py:91`, `:171` | Backend (Data Engineer for the ffml message) | none | fixed b8ad1ef |
| QA-F08 | Low | API security headers are only `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. `/docs`, `/redoc` and `/openapi.json` are public in production, and `/docs` loads Swagger UI scripts from `cdn.jsdelivr.net`. For a JSON API the missing frame, referrer and CSP headers matter mainly on those HTML pages. Fix: `FastAPI(docs_url=None, redoc_url=None, openapi_url=None)` in production (keep them in private_test), and add `Referrer-Policy: no-referrer`, `X-Frame-Options: DENY` and `Content-Security-Policy: default-src 'none'; frame-ancestors 'none'` to JSON responses. The web app already sends a good set (`web/next.config.ts`). | `api/main.py:51-56`, `_headers` middleware | Backend | none | fixed 68a58f9 |
| QA-F09 | Low | The image does not install from the lock: `pip install ".[api]"` resolves pyproject ranges (`uvicorn>=0.34`, `sqlalchemy>=2.0,<3`, ...) at build time, the base image is a floating tag, and the base ships its own pip and setuptools. Builds are not reproducible and the audited lock is not what runs. Fix: an API lock (or `pip install -r requirements.lock.txt` then `pip install --no-deps .`), upgrade or remove setuptools in the image, pin `python:3.11-slim@sha256:...`. The `api-image` CI job audits the installed packages (report only until this is fixed). | `Dockerfile.api:2`, `:14` | Backend | CI `api-image` step "Audit Python packages installed in the image" | fixed b8c193b |
| QA-F10 | Low | `GET /leagues/{id}/waivers` is slow on a realistic league: 10 teams, 17 roster slots with SUPER_FLEX, real 2024 week 11 replay: p50 270 to 320 ms, p95 320 to 445 ms (in process and over HTTP; the backend's 107 ms was an 8-team league). The PLAN.md 300 ms target is for projections, which pass easily (section 6). Fix: compute each week's baseline lineup once, prune candidates by ROS mean before solving, or cache per (run, scoring, roster version). | `api/services/recommend.py:415` (`waivers`) | Backend | `test_qa_performance.py` (1 s regression bound; prints timings) | open: accepted, 1 s bound |
| QA-F11 | Low | `manifest_is_approved()` lowercases and strips (`"Approved"`, `" approved "` pass) while its docstring says "exact string", and an explicit `"environment": null` is treated as a missing key and accepted. Neither is reachable through `ffml predict`, which writes exact labels; both loosen a gate that should be exact. Fix: compare `value == "approved"`; accept a missing `environment` only when the key is absent. | `api/approval.py:204-207`, `:353-354` | Backend | `test_qa_gate_fuzz.py::test_labels_must_match_exactly` (3 cases) | fixed e30c8f9 |
| QA-F12 | Low | `GET /meta/model-card` raises (HTTP 500) when `REGISTRY.json` lists entries that are not objects (`"models": ["live"]`, `[1]`, `[null]`, `[[]]`, or `"models": "live"`): the summary calls `.get()` on them outside its `try`. The gate itself refuses such registries correctly. Fix: skip entries that are not dicts. | `api/routers/meta.py` (`registry_summary`, `registry_status_word`) | Backend | `test_qa_gate_fuzz.py::test_meta_routes_survive_malformed_registries` (5 cases) | fixed e30c8f9 |
| QA-F13 | Low | `docker-compose.yml` publishes the unauthenticated API on all interfaces (`"8000:8000"`) and mounts all of `data/`, including `data/processed/restricted` and `data/raw/restricted` (FantasyPros/ECR, D-007), into the API container. The API never reads them (section 4d), but the container should not see them. With the `web` service (BE-4.9) the browser only needs port 3000; the API port can be local-only. Fix: `"127.0.0.1:8000:8000"`, and mount only `data/predictions`, `data/processed` without `restricted` (or an empty tmpfs over the restricted paths) and `data/raw/schedules`. | `docker-compose.yml:28`, `:32` | Backend | none | fixed b8c193b |
| QA-F15 | Low | The web proxy reads the whole body before checking its size, and checks `body.length` (UTF-16 code units, not bytes), so a 256 Ki-character body of multi-byte text passes as up to about 768 KiB. Fix: check `Content-Length` first and compare `new TextEncoder().encode(body).length`, or stream with a byte counter. | `web/app/api/[...path]/route.ts:41-44` | Frontend | none | open: Frontend |
| QA-F16 | Low | The production CSP allows `'unsafe-inline'` scripts for the theme bootstrap and Next.js inline scripts, which removes most of the CSP's XSS protection. No injection sink was found (the only `dangerouslySetInnerHTML` is a static constant, and attribution links go through `safeUrl`). Fix: a per-request nonce (Next.js middleware) or a hash for the static theme script. | `web/next.config.ts:10` | Frontend | none | open: Frontend |
| QA-F17 | Low | `.env.example` lists `API_DATABASE_URL`, `API_SECRET_KEY=change-me` and `API_CORS_ORIGINS`, which the code never reads (the API uses `FFML_DATABASE_URL`, `FFML_CORS_ORIGINS`). When the D-011 session cookie is built, its secret must be required in production with no default. | `.env.example:9-11` | Backend, Orchestrator | none | fixed b8ad1ef |
| QA-F18 | Low | `npm audit`: 0 runtime vulnerabilities; 8 moderate in dev tooling from one advisory, minimatch 10.2.6 ReDoS (GHSA-g3hv-75wg-m249) via typescript-eslint under `eslint-config-next`. npm's suggested fix is a major downgrade; use an `overrides` entry to a patched minimatch when one is published for the 10.x line, or accept (lint-time only). | `web/package-lock.json` | Frontend | CI `security` job audits runtime deps | open: accepted (dev tooling) |
| QA-F21 | Low | `FFML_CORS_ORIGINS` is not validated. `["*"]` is accepted (it then answers `Access-Control-Allow-Origin: *`, still without credentials). Fix: refuse `*` and non-http(s) origins in `ApiSettings` in production. | `api/settings.py:50`, `api/main.py:68` | Backend | `test_qa_headers_cors.py::test_wildcard_origin_setting_never_allows_credentials` documents the behavior | fixed e30c8f9, d9c0f4c |
| QA-F22 | Low | Both images build with context `.` and there is no `.dockerignore`, so every local `docker compose build` sends the whole checkout to the daemon: `data/` (1.5 GB, restricted FantasyPros/ECR tables included), `.venv`, `web/node_modules`, `artifacts/`. The Dockerfiles copy only what they need, so nothing restricted lands in an image, but builds are slow and the daemon reads data it should not see. CI is unaffected (empty `data/`). Fix: a root `.dockerignore` with `data/`, `artifacts/`, `reports/`, `.venv/`, `web/node_modules/`, `web/.next/`, `.git/`, `tests/`, `docs/`. | `Dockerfile.api`, `Dockerfile.web`, `docker-compose.yml:20-22`, `:37-40` | Backend (api), Frontend (web) | none | fixed b8c193b |
| QA-F23 | Low | `livetest_watchdog.yml` checked out the repository with the default `persist-credentials: true`, leaving the `GITHUB_TOKEN` in `.git/config` although the script only uses the token from the environment for the contents API. | `.github/workflows/livetest_watchdog.yml` | QA | fixed QA-4.2 (`persist-credentials: false`) |
| QA-F24 | Info | Actions are pinned by major tag (`actions/checkout@v4`, `setup-python@v5`, `setup-node@v4`, `upload-artifact@v4`) in both workflows; tags are mutable. SHA pins with Dependabot `github-actions` updates (`.github/dependabot.yml`) would close that; QA can add it on request. | `.github/workflows/*.yml` | Orchestrator | open: accepted |
| QA-F25 | Info | GitHub sends failure mail for scheduled runs only to the user who last changed the workflow's `cron` line (the owner today), and disables the schedule after 60 days without a push. The runbook should say both, so a change of author or a quiet repository does not silence the watchdog. | `.github/workflows/livetest_watchdog.yml`, `docs/RUNBOOK.md` | Data Engineer | open |
| QA-F19 | Info | The local `.venv` has setuptools 65.5.0 (bundled with Python 3.11), with 5 advisories (CVE-2022-40897, CVE-2024-6345, CVE-2025-47273, CVE-2026-59890, GHSA-xmj3-9gf7-h52w). Development machine only; builds use an isolated `setuptools>=69`. Fix: `.venv/Scripts/python -m pip install -U setuptools`. | `.venv` | Orchestrator | none | open: dev machine |

Informational notes, no action needed:

- The model card says "betting lines" twice, as a description of model
  inputs (nflverse closing lines, D-008). The language scans allow this exact
  phrase and nothing else; disclaimers ("Nothing here is betting advice") pass
  because they are negated. If the owner wants no betting vocabulary at all,
  "game lines" works.
- The limitations copy names ESPN and Yahoo ("import is not available"). They
  are league platforms named in PROMPT_1's stretch goals, not branding; the
  product-name scan does not list them.
- The frontend's own wording test (`web/tests/unit/wording.test.ts`) allows
  any quoted `"approved"` and skips `lib/api/` and `mock/`. The pytest scan
  in section 4b is stricter and covers those folders.
- CSRF: with no cookies or auth there is no session to ride, and the
  state-changing routes need a JSON body. FastAPI 0.162 refuses `text/plain`,
  form, multipart and missing content types with 422, so cross-site "simple"
  requests cannot create leagues (tested); PUT and DELETE need a CORS
  preflight that other origins fail.
- `GET /players?search=` (contract v1.3, bc1debc) answers
  `Cache-Control: public, max-age=300`. It returns identity data only (id,
  name, position, team), so a shared cache is acceptable. It is included in
  the input-validation matrix and the response sweep.
- Contract v1.3 moved unknown body player ids (manual league `teams[].players`,
  start/sit `player_ids`) from 404 to 422 `player_not_found`; the QA tests
  accept either on body routes and expect 404 only for path and query ids.

## 2. Input validation

Tests: `tests/api/test_qa_input_validation.py`. The app runs in private_test
with a fixture snapshot, so the gated routes parse input as well (in
production the gate answers 503 before parsing; `tests/api/test_gate.py` and
section 4a).

| check | result |
| --- | --- |
| Unknown and hostile ids on every `{league_id}` route (14 values: unknown UUID, SQL and template injection, `<script>`, `%00`, encoded traversal, Arabic-Indic digits, 2,000 chars) | 404 or 422 error envelope on every route; no 500, no stack trace; the league table is unchanged afterwards |
| Hostile team ids (`PUT /my-team`, `?league_team_id=`) | 404 `team_not_found` or 422 |
| Malformed bodies on every body route (bad JSON, empty, `null`, list, string, number, wrong object, invalid UTF-8, 5,000-deep nesting) | 400 or 422 envelope |
| Wrong types and ranges (62 bodies: strings for numbers, out-of-range seasons and team counts, unknown presets, slots and fields, duplicate players, too many teams, slots or settings) | 422 on every body; nothing reaches Sleeper |
| Bad query values (35 cases: weeks, limits, offsets, positions, sorts, scorings, availability, short and long search, player search) | 422 on every case; unknown query parameters are ignored |
| Oversized lists and strings within the schema limits (10,000 player ids, 33 teams, a 100,000-char slot) | 422 |
| Oversized body (2 MB) and an unbounded settings value | 413 and 422 since b9bc159 (QA-F02 fixed; `tests/api/test_hardening.py`, `test_qa_input_validation.py`) |
| `Infinity`, `-Infinity`, `1e309` scoring weights | 422 since e30c8f9 (QA-F05 fixed); `NaN` was always refused |
| Sleeper usernames (17 hostile values: traversal, `?`, `#`, spaces, CR/LF, NUL, `%2F`, shell and SQL text, 41 chars) | 422 on import and on lookup, zero requests to Sleeper |
| Sleeper league ids (12 hostile values) | 422, zero requests to Sleeper |
| Accepted inputs | only `GET https://api.sleeper.app/v1/{state/nfl, user/<name or id>[/leagues/nfl/<season>], league/<id>[/rosters, /users]}`; surrounding whitespace is stripped before use |
| Non-ASCII usernames on the lookup route | 422, zero requests to Sleeper, since e30c8f9 (QA-F06 fixed) |
| Cross-site simple requests (text/plain, form, multipart, no content type) | 422, no league created |
| Internal error | generic 500 envelope; no message, path or traceback leaks |

## 3. CORS and security headers

Tests: `tests/api/test_qa_headers_cors.py` (34 cases).

- CORS is an allowlist from `FFML_CORS_ORIGINS` (default
  `http://localhost:3000`, `http://127.0.0.1:3000`; compose sets
  `http://localhost:3000`). `allow_credentials=False`, methods GET, POST,
  PUT, DELETE, request header `Content-Type` only, `X-FFML-Approval` exposed.
- Preflights from other origins (including `null`, a suffix trick
  `localhost:3000.evil.example`, other ports and https) get 400 and no
  `Access-Control-Allow-Origin`; simple requests from them get no grant.
  PATCH, TRACE, CONNECT and an `Authorization` request header are refused.
- Every sampled response (meta, leagues, every projection route, 404 from
  the router, 405, 422, 503 gate, in both environments) carries
  `X-Content-Type-Options: nosniff`, `Cache-Control: no-store` and a JSON
  content type; private_test responses all carry `X-FFML-Approval:
  not-approved`; projection routes in production carry `X-FFML-Approval:
  none`. Uvicorn's `server: uvicorn` header carries no version.
- Since 68a58f9 (QA-F08) the API also sends frame, referrer and CSP
  headers and turns `/docs`, `/redoc` and `/openapi.json` off in production
  (`tests/api/test_hardening.py`). The web app sets `X-Content-Type-Options`, `Referrer-Policy`,
  `X-Frame-Options: DENY`, `Permissions-Policy` and, in production, a CSP
  with `frame-ancestors 'none'` (QA-F16 for its `'unsafe-inline'`).

## 4. Compliance scans

### (a) No projection data in production without a live, approved registry entry

`tests/api/test_qa_gate_fuzz.py`. A temporary production world that serves
(live registry entry with an approval report, approved live run behind
`LATEST`) is broken in 169 single ways and 120 seeded random combinations
of 2 or 3 independent breaks, plus 60 files of random bytes:

- registry: absent, empty, not JSON, BOM, wrong or missing format, `models`
  not a list or holding non-objects, every non-`live` status and near
  misses (`Live`, `" live"`), missing or falsy report, bad versions, two live
  entries;
- versions: registry and manifest disagree (case, whitespace, type);
- `LATEST`: absent, garbage, wrong types, out-of-range season and week, and
  19 `run_id` values including `../`, absolute paths, Windows paths, NUL,
  200 chars, non-ASCII, `run=` prefixes;
- pointer labels: not approved, wrong environment, wrong model version;
- manifest labels: missing, null, empty, near misses (`approved?`,
  `pre-approved`), wrong types, top-level "not approved" with `extra`
  "approved";
- manifest environment: replay, private_test, `LIVE`, `" live"`, wrong
  types, `extra.environment` replay, `mode: replay`, run id ending
  `-replay`;
- shape: manifest absent or not an object, season/week/run id mismatch,
  missing or directory-shaped `player_sims.parquet` and
  `player_summary.parquet`.

Result: every broken world answers all 7 projection routes with the fixed
503 body and `X-FFML-Approval: none`, and `/meta/approval` reports
`serving: false`, `approval_status: "not approved"`. A control confirms the
unbroken world serves, before and after the sweep. A file audit (Python
audit hook plus Polars/DuckDB reader wrappers) shows that no gate read
leaves the temporary tree for any traversal value. The three gaps found on
2026-11-04 (QA-F01 report not verified, QA-F11 inexact labels, QA-F12 model
card crash on non-object registry entries) are fixed (68b2e5d, e30c8f9); the
tests that pinned them now pass as regression tests.

The `api-image` CI job repeats the production check on the built image: all
7 routes answer exactly `{"status": "no_approved_model", "message": ...}`
with `X-FFML-Approval: none`, including an invalid request.

### (b) No approval claims in private_test responses or in web copy

- Runtime (`tests/api/test_qa_response_compliance.py`): an adversarial
  private_test world in which every source claims approval (live registry
  entry with an approval report, an approved published run, and private,
  replay and fixture runs whose manifests say `approval_status: "approved"`,
  `environment: "live"`). Every API operation in the OpenAPI schema is
  called (the test fails if one is skipped) with valid inputs and error
  cases, about 50 requests. Every response carries `X-FFML-Approval:
  not-approved`, every `approval_status` field is "not approved", no string
  uses "approved" except negated ("not approved", "No model is approved"),
  and the published run is never served. Passes.
- Static (`tests/compliance/test_qa_source_scans.py`): string literals and
  JSX text in `web/app`, `web/components`, `web/lib` and `web/mock`
  (comments excluded). "approved" is allowed only negated, or as the exact
  API value in the approved code path: comparisons (`=== "approved"`),
  `case "approved":`, `kind: "approved"`, type unions, ternaries guarded by
  an `*approved*` condition (`apiApproved ? "approved" : ...`), and
  `data-*` attributes. 16 such occurrences in 73 files today, all in
  `approval-mode.ts`, `result.ts`, `types.ts`, `ApprovalBanner`,
  `HomeStatus`, `ModelCardView` and `ProjectionNotice`; 75 negated uses.
  Passes.

### (c) No "lock", "guaranteed", "can't miss" or betting language

Rules in `tests/compliance/qa_text_rules.py`: lock, guarantee(d), can't
miss, sure thing, risk-free, free money, and betting words (bet, betting,
wager, parlay, sportsbook, moneyline, bet slip, best bet). Negated
disclaimers pass ("No guarantees", "Nothing here is betting advice");
"betting lines" passes as a data description. Scanned: every non-docstring
string constant in `api/` (AST), all web copy (as in b), and every string
the API returned in the runtime sweep in both environments. No hits. Rule
self-tests cover 13 positive and negative sentences.

### (d) No consensus (ECR) reads and no holdout unlock in api/ or web/

- Static: no file in `api/` or `web/` (tests and mock included, lockfile
  excluded) mentions `fact_ecr_weekly`, `ecr_baseline`, `ffml.eval.ecr`,
  `build_ecr`, `load_ff_rankings`, `ff_rankings`, `fantasypros_id`, `fpecr`,
  `fp_latest_weekly`, `fantasypros.com`, `processed/restricted`,
  `raw/restricted`, `RESTRICTED_TABLES`, `FFML_HOLDOUT_UNLOCK`,
  `holdout_unlock`, `release_holdout` or `assert_seasons_allowed`;
  `Dockerfile.api` and `docker-compose.yml` never set the unlock.
- Runtime: decoy files under `data/processed/restricted/fact_ecr_weekly/`,
  `data/raw/restricted/ecr/` and `data/raw/restricted/fantasypros/`; the full
  route sweep in production and private_test, including the real player
  directory load, opens, lists or reads none of them and imports no consensus
  module. A control proves the audit records such reads when they happen.
- Import closure: a fresh interpreter importing every `api` module loads no
  `ffml.eval` module and no ECR reader.

### (e) Original branding

PROMPT_1.md names the product the app is inspired by; the scan reads the
name from it and matches it with any spacing, case or punctuation. It
appears nowhere in `api/` or `web/` (file contents and file names; the web
package is "Gridiron Projections"). Fantasy-advice and betting product
names (FantasyPros, DraftKings, FanDuel, PrizePicks, Underdog Fantasy,
Rotowire, Rotoworld, numberFire, 4for4, PFF, BetMGM and others) appear in
no user-facing text in `api/` or `web/` (docstrings, comments and the
frontend's banned-word list are not user-facing). Passes.

## 5. Rate limiting

Implemented by the Backend in f988e60 (`api/middleware.py`, QA-F03) along
the design below, with the process-wide Sleeper client and bucket of
fbc6fa3 (QA-F04). The design as proposed on 2026-11-04:

1. **Where.** One pure ASGI middleware in `api/main.py` (no new
   dependency), reusing `ffml.data.sleeper.TokenBucket` keyed by
   `(client, bucket)`. Client = the socket peer address, or the first
   `X-Forwarded-For` entry only when the peer is in `FFML_TRUSTED_PROXIES`
   (the Next.js proxy forwards every browser call, so without this the API
   sees one client). The web proxy should add `X-Forwarded-For`.
2. **Buckets** (per client, per minute, burst):
   - `sleeper`: `POST /leagues/import/sleeper`, `POST /leagues/{id}/sync`,
     `GET /sleeper/users/{u}/leagues`: 10, burst 5 (each makes at most 6
     Sleeper calls; measured in `test_qa_rate_limits.py`);
   - `write`: other POST, PUT, DELETE on `/leagues*`: 30, burst 10
     (scoring changes create new cache entries, so this also bounds cache
     thrash);
   - `heavy`: waivers and trades: 30, burst 10;
   - `read`: other projection routes and `/players`: 120, burst 30;
   - `meta`: 300, burst 60.
   Plus the process-wide Sleeper bucket of D-025 (fix QA-F04), so the total
   stays under Sleeper's limit whatever the client mix.
3. **Response.** 429 with the error envelope (`rate_limited`, already in
   `api/errors.py`) and `Retry-After` in whole seconds; the web proxy
   already passes `retry-after` through. The gate's 503 is unaffected.
4. **Config and tests.** Limits in `ApiSettings` (`FFML_RATE_LIMIT_*`,
   enabled by default, `0` disables a bucket), injectable clock, and the
   test factory sets them high so existing tests are unaffected. QA will
   then add tests for 429, `Retry-After`, per-client isolation, trusted-proxy
   handling and the Sleeper budget.
5. **Body size** (QA-F02) belongs in the same middleware: 413 above
   256 KiB.

Tests: `tests/api/test_hardening.py` (Backend: 429 and refill, family
independence, before the gate, trusted proxy, defaults, parsing) and
`tests/api/test_qa_rate_limits.py` (QA-4.2: the 429 envelope with
`Retry-After` and the security headers, the private_test label on a 429,
preflights never counted, spoofed `X-Forwarded-For` cannot evade a limit,
the limit runs before validation and before the body limit, a disabled
family stays unlimited, `Retry-After` honoured with an injected clock, and
every OpenAPI operation classified into its documented family). Still to
do: the web proxy does not add `X-Forwarded-For` yet, so behind the Next.js
app the API sees one client unless the deployment's reverse proxy sets it;
`FFML_TRUSTED_PROXIES` must then name that proxy.

## 6. Performance

`tests/api/test_qa_performance.py` (skipped where `data/` has no replay, as
in CI). Real replay `2024w11-20261104T225732Z-replay` (chain_v1, 567
entities x 2,000 simulations) in private_test, the real player directory,
and a 10-team half-PPR league (pass TD 6) with 17 roster slots including
SUPER_FLEX. 3 warm-up then 30 timed requests per route; other agents were
using the CPU at the same time. Target (PLAN.md QA-4.1): projections under
300 ms warm.

In process (TestClient), ms:

| route | p50 | p95 | max |
| --- | --- | --- | --- |
| `GET /projections?scoring=ppr&limit=500` | 25.9 | 32.6 | 113.8 |
| `GET /leagues/{id}/projections` (100 rows) | 24.2 | 32.0 | 106.8 |
| `GET /leagues/{id}/projections?limit=500` | 35.0 | 43.4 | 124.0 |
| `POST /leagues/{id}/start-sit` (3 players) | 20.4 | 21.6 | 28.1 |
| `GET /leagues/{id}/waivers` | 322.8 | 430.5 | 531.7 |
| `POST /leagues/{id}/trades/evaluate` (2 for 1) | 36.0 | 44.2 | 150.8 |
| `GET /leagues/{id}/ros?limit=500` | 38.1 | 46.4 | 148.7 |
| `GET /ros?scoring=ppr&limit=500` | 22.9 | 24.8 | 27.0 |

Over HTTP (uvicorn on 127.0.0.1, one keep-alive client, same league), ms:

| route | p50 | p95 | max | body |
| --- | --- | --- | --- | --- |
| `GET /projections?scoring=ppr&limit=500` | 23.6 | 46.9 | 48.2 | 325 KB |
| `GET /leagues/{id}/projections` | 19.0 | 33.2 | 134.2 | 80 KB |
| `GET /leagues/{id}/projections?limit=500` | 28.6 | 43.5 | 46.9 | 333 KB |
| `POST /leagues/{id}/start-sit` | 15.6 | 17.5 | 19.3 | 4 KB |
| `GET /leagues/{id}/waivers` | 270.2 | 342.2 | 431.5 | 16 KB |
| `POST /leagues/{id}/trades/evaluate` | 26.9 | 36.7 | 128.4 | 5 KB |
| `GET /leagues/{id}/ros?limit=500` | 28.9 | 34.2 | 36.5 | 161 KB |

Cold (first request for a new run and scoring: load and score): 932 to
1,009 ms in process, 952 ms over HTTP. Projections meet the target with a
wide margin. Waivers does not (QA-F10); the test holds the other routes to a
1 s regression bound.

## 7. Secrets scan

- **gitleaks 8.30.1** (release binary, checksum verified, no install):
  `gitleaks git` over the full history (134 commits, about 4.1 MB): no
  leaks. `gitleaks dir` over the working tree (59 MB, `.venv`,
  `node_modules`, data stores and model artifacts skipped): 3 hits, all false
  positives of the `generic-api-key` rule: Sleeper scoring keys
  (`key: "bonus_rec_yd_100"`, `web/lib/scoring.ts`) and a model key in an
  ignored artifact. `.github/gitleaks.toml` allowlists only short lower-case
  snake_case values for that one rule; a fake GitHub token, a Stripe key and
  a random API key in a scratch file are still caught.
- **Regex scan** of every added line in the history (passwords, secrets,
  tokens, API keys, bearer and authorization values, private-key blocks,
  GitHub, AWS, Slack and OpenAI-style tokens, credentials in URLs): one hit,
  `postgresql+psycopg://ffml:ffml@localhost:5432/ffml_test`, the throwaway
  CI service database.
- **Files**: no `.env`, database, key or certificate file was ever
  committed; no ECR or FantasyPros data file was ever committed (only code
  and test fixtures from public nflverse slices).
- **Placeholders**: `docker-compose.yml` defaults the PostgreSQL password to
  `ffml-local-only` (overridable with `FFML_POSTGRES_PASSWORD`; the database
  has no published port). `.env.example` listed unused `API_*` variables
  (QA-F17, fixed b8ad1ef).
- CI: the `security` job runs the same gitleaks version over the full
  history on every push.

## 8. Dependency audit

| target | tool | result |
| --- | --- | --- |
| `requirements.lock.txt` (92 pinned packages) | pip-audit 2.10.1, `--no-deps --disable-pip` | no known vulnerabilities |
| local `.venv` | pip-audit `-l` | setuptools 65.5.0, 5 advisories (QA-F19, dev machine only) |
| `web/package-lock.json`, runtime (`--omit=dev`, 20 packages) | npm audit (npm 11.20.0) | 0 vulnerabilities |
| `web/package-lock.json`, all (536 packages) | npm audit | 8 moderate, one advisory: minimatch 10.2.6 via typescript-eslint (QA-F18, lint-time only) |
| API image | pip-audit of `pip freeze` in the container | CI `api-image` job, blocking since b8c193b (the image installs from the lock and pins setuptools, QA-F09) |

The lock file has no hashes (pip-audit warns); hashes would let `pip
install --require-hashes` refuse tampered downloads. Suggested with QA-F09.

## 9. CI (`.github/workflows/ci.yml`)

Validated with actionlint 1.7.12 (no findings). The workflow token is
`contents: read`.

- `python` (existing): ruff and the full offline suite on SQLite, which now
  includes the QA tests in `tests/api/test_qa_*.py` and
  `tests/compliance/`.
- `api-postgres`: `tests/api` (backend and QA tests) on a PostgreSQL 16
  service via `FFML_TEST_DATABASE_URL`.
- `api-image`: `docker compose up --build --wait api web` (both images and
  postgres) with an
  empty registry and no predictions, as in reality; health and approval
  state; every projection route answers exactly the 503 `no_approved_model`
  body with `X-FFML-Approval: none` and `Cache-Control: no-store`, also for
  an invalid request; a league create, list, delete round trip on
  PostgreSQL; security headers; the process runs as uid 10001; image
  package audit (blocking); the web image: `/`, `/about` and `/projections`
  render with `X-Frame-Options: DENY`, a CSP with `frame-ancestors 'none'`,
  `nosniff`, a referrer policy and no `X-Powered-By`, and `/api/projections`
  through the same-origin proxy is the 503 `no_approved_model` body with
  `X-FFML-Approval: none`; logs and `down -v` always.
- `web` (QA-4.2, closes QA-F20): `npm ci`, `npm run lint`, `typecheck`,
  `test` (vitest), `build`, then the Playwright chromium smoke suite against
  the contract mock in both modes (`npm run e2e`); the HTML report is
  uploaded on failure. Independent of the `python` job.
- `security`: gitleaks over the full history, pip-audit on the lock,
  `npm audit --omit=dev --audit-level=moderate`.

## 10. How to re-run

```
PYTHONUTF8=1 .venv/Scripts/pytest -q tests/api/test_qa_*.py tests/compliance   # QA tests
PYTHONUTF8=1 .venv/Scripts/pytest -q tests/api/test_qa_performance.py -s       # timings (needs a local replay)
PYTHONUTF8=1 .venv/Scripts/pytest -q --runxfail tests/api/test_qa_*.py        # see the open findings fail
.venv/Scripts/pip-audit -r requirements.lock.txt --no-deps --disable-pip
(cd web && npm audit --omit=dev)
(cd web && npm run check && npm run build && npm run e2e)                    # the web job locally
gitleaks git --config .github/gitleaks.toml --redact .
actionlint .github/workflows/ci.yml
```

## 11. Live-test watchdog review (QA-4.2)

`.github/workflows/livetest_watchdog.yml` and `scripts/livetest_watchdog_gh.py`
(Data Engineer, 0487a8d; D-107). Checked:

- **Least privilege.** `permissions: contents: read` at the workflow level;
  the `GITHUB_TOKEN` is only used for `GET /repos/{repo}/contents/...` on the
  commitments branch, which that scope covers on a private repository. Fixed
  in QA-4.2: `persist-credentials: false` on the checkout (QA-F23), since the
  script never needs git credentials.
- **Secret handling.** `FFML_ALERT_WEBHOOK` reaches only the one step's
  environment and is read by `scripts/alert.py`; it is never written to the
  log: the `--json` output carries channel status words only, and a failed
  post is reported as `failed: <ExceptionType>: <message>` (`HTTPError` and
  `URLError` messages do not include the URL). GitHub's log masking of the
  secret value is a second layer. The alert log the script appends goes to
  the runner's ephemeral `data/alerts/`.
- **Inputs.** `branch`, `now` and `force_window` reach the shell through
  `env:`, never interpolated into `run:`, so a manual dispatch cannot inject
  commands; the branch is URL-quoted before it reaches the API.
- **Failure without the secret.** A missing commitment inside a window exits
  1, which fails the run (GitHub mails the owner, see QA-F25) and prints a
  `::error` annotation; the webhook is additional. The script and
  `alert.py` are standard library only, so no install step can break the
  alert path on a bare runner; an alert-dispatch exception is swallowed and
  the exit code still fails the run.
- **Robustness.** `concurrency` with no cancellation, `timeout-minutes: 10`,
  hourly schedule with windows sized for cron delay, dry runs through
  `workflow_dispatch`, unit tests in `tests/data/test_watchdog_gh.py`.
- **Pinning.** Actions by major tag only (QA-F24, same as `ci.yml`).
