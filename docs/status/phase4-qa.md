# Phase 4 / QA and Security Reviewer / QA-4.1
Date: 2026-11-05 (work started 2026-11-04)
Scope: CI jobs for PostgreSQL and the API image; input validation, CORS and
header tests; rate-limit design; secrets scan; dependency audits; performance
check; compliance scans (a) to (e) of the D-099 limits; findings for other
roles. Full report: `docs/SECURITY.md`.

## Task list (as briefed)

1. CI: add `api-postgres` and `api-image` from P-CI; `api-image` asserts
   `/projections` is 503 `no_approved_model` from the built image; validate
   locally; push nothing.
2. QA-4.1 checks with results in docs/SECURITY.md: input validation on every
   route, CORS and security headers, rate limiting (design or test-only
   implementation), secrets scan of repo and history, dependency audit,
   performance (projections warm under 300 ms on a replay in private_test).
3. Compliance scans as tests: (a) no production projection data without a
   live approved registry entry, fuzzed; (b) no approval claims in
   private_test responses or web copy; (c) no lock/guaranteed/betting
   language; (d) no ECR reads or holdout unlock in api/ or web/; (e) original
   branding.
4. Do not touch the flaky `tests/models/test_chain_ablation.py` test.

## Done

- `.github/workflows/ci.yml` (c2ce878, e671d48): jobs `api-postgres`
  (tests/api on PostgreSQL 16), `api-image` (compose build of the api
  service with postgres; all 7 projection routes must answer exactly the
  503 `no_approved_model` body with `X-FFML-Approval: none`, also for an
  invalid request; health, approval state, league round trip, headers,
  non-root user, image package audit as report only) and `security`
  (gitleaks over the full history, pip-audit on the lock, npm audit of web
  runtime deps). Token limited to `contents: read`. `.github/gitleaks.toml`
  with one narrow allowlist. actionlint 1.7.12: no findings.
- Tests (5e1cd6a), 133 passing and 20 strict xfails that pin open findings:
  - `tests/api/test_qa_gate_fuzz.py` (scan a): 169 single breaks of a
    serving production world, 120 random 2-3 way combinations, 60 garbage
    files; file audit for path traversal.
  - `tests/api/test_qa_input_validation.py`: 14 hostile ids on every
    `{league_id}` route, 10 malformed bodies on every body route, 62
    wrong-type bodies, 35 bad query values, 17 hostile usernames and 12
    hostile league ids (none reach Sleeper), cross-site simple requests,
    generic 500s.
  - `tests/api/test_qa_headers_cors.py`: 34 cases.
  - `tests/api/test_qa_response_compliance.py` (scans b, c, d at runtime):
    every operation in the OpenAPI schema, in a world where every source
    claims approval; decoy restricted files plus an audit hook.
  - `tests/api/test_qa_rate_limits.py`, `tests/api/test_qa_performance.py`
    (real replay, skipped in CI), `tests/api/qa_support.py` (audit hook).
  - `tests/compliance/` (scans b, c, d, e static): `qa_text_rules.py` and
    `test_qa_source_scans.py` over `api/` and the working tree of `web/`.
- `docs/SECURITY.md`: 22 findings ranked, results of every check, the
  rate-limit design, timings, scan and audit outputs, re-run commands.

## Evidence

- `PYTHONUTF8=1 .venv/Scripts/pytest -q tests/api/test_qa_*.py tests/compliance`
  against `ec1a8dd` (BE-4.9 included): `133 passed, 20 xfailed in 189 s`.
  `--runxfail` shows each xfail failing for its stated reason (no XPASS).
- Earlier full run of `tests/api tests/compliance` on the 2026-11-04 tree:
  `284 passed, 20 xfailed` plus one failure of the import-closure test whose
  subprocess exited non-zero under machine load (33-minute run); the test
  now reports stderr and allows 600 s, and passes.
- `.venv/Scripts/ruff check tests/api/test_qa_*.py tests/api/qa_support.py tests/compliance/`:
  all checks passed. `actionlint .github/workflows/ci.yml`: clean.
- gitleaks 8.30.1 (checksum verified): history, 134 commits, no leaks;
  working tree, 3 false positives (Sleeper scoring keys, a model key), none
  with the committed config; a fake GitHub token, Stripe key and random API
  key in a scratch file are still caught. Regex scan of all added lines in
  history: only the CI service database URL.
- pip-audit 2.10.1 on `requirements.lock.txt` (92 packages): no known
  vulnerabilities. Local `.venv`: setuptools 65.5.0 advisories (dev only).
- npm audit (npm 11.20.0): runtime `--omit=dev` 0 vulnerabilities; all 536
  packages 8 moderate from one advisory (minimatch via typescript-eslint,
  lint-time only).
- Performance, real replay 2024 week 11 (567 entities x 2,000 sims),
  10-team league with SUPER_FLEX, private_test, 30 warm requests per route,
  machine shared with other agents. Projections p95 32 to 47 ms in process
  and over uvicorn HTTP (target 300 ms); start/sit 22 ms, trades 44 ms, ROS
  46 ms; waivers p95 320 to 445 ms (QA-F10); cold load about 950 ms. The
  uvicorn process used for the HTTP numbers was stopped afterwards.
- Compliance scans: (a) every broken world blocked, control world serves;
  (b) no approval claim in any private_test response or in web copy outside
  the `approval_status == "approved"` code path; (c) no banned language in
  api/ strings, web copy or any response; (d) no restricted read, import or
  identifier in api/ or web/, decoys untouched, import closure clean; (e)
  the inspiration product named in PROMPT_1.md appears nowhere in api/ or
  web/, no other product names in user-facing text.

## Findings (ranked; details, line numbers and fixes in docs/SECURITY.md section 1)

| id | sev | summary | owner | pinned by |
| --- | --- | --- | --- | --- |
| QA-F01 | High | production gate trusts any truthy `evaluator_report`; a registry edited to `live` with a NOT APPROVED report serves "approved" projections (`api/approval.py:329`) | Backend | xfail x3 |
| QA-F02 | Medium | no body size limit; league `settings` values unbounded (20 MB accepted and echoed) | Backend | xfail x2 |
| QA-F03 | Medium | no inbound rate limiting and no hook; design in SECURITY.md section 5 | Backend | design |
| QA-F04 | Medium | Sleeper client and token bucket built per request; D-025 budget not enforced (`api/deps.py:24-29`) | Backend | xfail |
| QA-F14 | Medium | no auth or ownership: anyone reaching the API lists, edits and deletes every league; deployment blocker for Gate 6 (D-011 cookie not built) | Backend, Orchestrator | design |
| QA-F20 | Medium | web lint, typecheck, vitest, build and Playwright never run in CI | Orchestrator (QA can add) | P-CI-WEB |
| QA-F05 | Low | `Infinity`, `-Infinity`, `1e309` scoring weights accepted, stored as inf, shown as null | Backend | xfail x3 |
| QA-F06 | Low | lookup route username check is Unicode `isalnum` (`api/routers/leagues.py:30`) | Backend | xfail x3 |
| QA-F07 | Low | validation errors echo full input (1 MB 422 body) | Backend, Data Engineer | none |
| QA-F08 | Low | API lacks frame/referrer/CSP headers; `/docs`, `/redoc`, `/openapi.json` public in production | Backend | none |
| QA-F09 | Low | image installs from pyproject ranges, floating base tag, base setuptools; not the audited lock | Backend | CI report step |
| QA-F10 | Low | waivers p95 320 to 445 ms on a realistic league (`api/services/recommend.py:415`) | Backend | perf test bound 1 s |
| QA-F11 | Low | `manifest_is_approved` is case/space-insensitive; explicit `environment: null` accepted | Backend | xfail x3 |
| QA-F12 | Low | `/meta/model-card` 500 on registry entries that are not objects | Backend | xfail x5 |
| QA-F13 | Low | compose publishes the API on all interfaces and mounts all of `data/` (restricted included) | Backend | none |
| QA-F15 | Low | web proxy size check counts UTF-16 units after reading the body (`route.ts:41-44`) | Frontend | none |
| QA-F16 | Low | production CSP `script-src 'unsafe-inline'` (`web/next.config.ts:10`) | Frontend | none |
| QA-F17 | Low | `.env.example` lists `API_*` variables the code never reads | Backend, Orchestrator | none |
| QA-F18 | Low | npm dev-tooling advisory (minimatch ReDoS) | Frontend | CI audit |
| QA-F21 | Low | `FFML_CORS_ORIGINS=["*"]` accepted | Backend | documented |
| QA-F22 | Low | no `.dockerignore`; local builds ship `data/` (1.5 GB, restricted tables) to the daemon | Backend, Frontend | none |
| QA-F19 | Info | local `.venv` setuptools 65.5.0 advisories | Orchestrator | none |

## Not done / blocked

- The CI jobs have not run yet: nothing was pushed (per brief). First run
  may need small adjustments (compose `--wait` timing, pipx on the runner).
- Docker is not installed locally, so the `api-image` job is validated by
  actionlint and review only.
- Rate limiting is a design (QA-F03): the backend has no middleware hook to
  implement a test-only limiter against without editing `api/`.
- Web checks are not in CI (QA-F20); proposal below.
- `tests/models/test_chain_ablation.py::test_default_feature_lists_are_unchanged`
  is intermittently failing on CI. Not touched, not marked xfail; the
  coordinator reports it as a CRLF/LF hashing issue assigned to the ML
  Engineer (Gate 3 erratum 4cb2239).

## Decisions proposed

- **P-CI-WEB (QA, `.github/workflows/ci.yml`).** A `web` job: `actions/setup-node`
  (lts), `npm ci` in `web/`, `npm run lint`, `npm run typecheck`,
  `npm run test`, `npm run build`, then `npx playwright install --with-deps chromium`
  and `npm run e2e` against the mock server (`web/playwright.config.ts`);
  `e2e:real` later in the Phase 6 e2e job with the API image. QA can add it
  on request; it needs the Frontend's confirmation that `e2e` is
  self-contained (starts `mock/server.mjs`).
- **P-QA-RL (Backend).** The rate-limit and body-size middleware design in
  docs/SECURITY.md section 5 (buckets per client and route family, 429 with
  `Retry-After`, process-wide Sleeper bucket, limits in `ApiSettings`); QA
  adds the 429 tests once the hook exists.
- **P-QA-XFAIL (convention).** Open findings are strict xfails named
  `QA-Fnn`; the commit that fixes a finding removes the marker, so a fix is
  visible in CI as a test that starts passing, and a regression as a test
  that fails.
- **P-QA-DOCKERIGNORE (Backend, Frontend).** Root `.dockerignore` as listed
  in QA-F22.

## Next

- Orchestrator pushes; QA reads the first CI run of the four jobs and fixes
  workflow issues in `.github/workflows/`.
- When QA-F01, F02, F04, F05, F06, F11, F12 are fixed, the matching xfails
  turn green; QA removes the markers if the fixing commit did not.
- Add 429 and body-size tests after P-QA-RL; add the web job after
  P-CI-WEB.
- Re-run the response sweep whenever a route is added (the sweep fails if an
  OpenAPI operation is not covered) and the performance test when chain_v2
  private-test snapshots exist.

Commits: c2ce878 (CI jobs and gitleaks config), 5e1cd6a (QA tests),
e671d48 (api-image starts only the api service), plus the commit carrying
this note and docs/SECURITY.md.
