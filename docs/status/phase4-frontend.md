# Phase 4 / Frontend Engineer / UI-4.1 to UI-4.10
Date: 2026-11-05 (first version 2026-11-04)
Scope: the web app in `web/` (Next.js 16 App Router, TypeScript, Tailwind 4)
built against docs/API.md (v1 to v1.3), plus `Dockerfile.web`. Phase 4
runs as an owner exception (D-099). No model is approved, so in production
every projection view shows a calm "No approved model" state. Private test
environments show labeled "not approved" data.

## Task list (as briefed)

1. UI-4.1: app shell (responsive to 360 px, light and dark theme,
   navigation, error boundary), offseason banner, global approval banner,
   and a typed API client with one place that maps responses to UI states.
2. UI-4.8: about, attribution, model card and limitations pages, each with
   empty and error states.
3. UI-4.2: league setup (Sleeper import, manual settings form with
   validation, my team).
4. UI-4.3 to UI-4.7: projections, start/sit, waivers, trade and
   rest-of-season views, built against contract fixtures.
5. UI-4.9: Playwright smoke tests in both modes, first against a mock and
   then against the real API.
6. UI-4.10: adopt contract v1.3 (the Backend's answers to the six
   questions below) and fix QA-F15 and QA-F16.

## Done

| Commit | What |
| --- | --- |
| 439f0d1 | UI-4.1 shell. Covers the layout, themes (no flash; system, light or dark), skip link, mobile menu, error boundaries, 404, the global approval banner and the season banner. Typed client `web/lib/api/{types,client,result,hooks}.ts`. Same-origin `/api/*` proxy (`web/app/api/[...path]/route.ts`). Contract mock `web/mock/`. Unit tests. |
| 73ff345 | UI-4.8 info pages. `/about`, `/about/model-card`, `/about/limitations`, `/about/attribution`. Content comes from `/meta/*`, with built-in copy as a fallback (DATA_SOURCES.md wording). |
| d243032 | UI-4.2 `/setup`. Sleeper import (username, then league, then my team; or import by league id), manual form, league list with use/sync/delete, and a settings editor for the current league. |
| 687d891 | UI-4.3 `/projections`. Distribution strip, boom/bust badges with low-confidence marking, drivers, filters, preset scoring when no league is set, and `GatedView`. |
| 0df2fb5 | UI-4.4 to UI-4.7: `/start-sit`, `/waivers`, `/trade`, `/rankings`. |
| 1519ab5 | UI-4.9 Playwright harness. Start/sit quick picks come from my roster's projections. |
| c4f2456 | `Dockerfile.web` (standalone Next server, non-root, health check). |
| 1088eb8 | Status note; the projections lead says ranges are for when the player plays (D-102). |
| this commit | UI-4.10: contract v1.3 adopted, QA-F15 and QA-F16 fixed (section below). |

### How the approval rules are enforced in the UI

- **Single mapping.** `web/lib/api/result.ts` is the one place that maps an
  HTTP response to a UI state. It produces `ok`, `no_approved_model` (any
  503 with that body or with `X-FFML-Approval: none`),
  `no_private_test_snapshot`, `error`, `unreachable` (network failure, or
  the proxy's `api_unreachable`) or `aborted`.
  - `components/states/ApiStateView.tsx` renders every non-ok kind, so
    pages never inspect status codes.
- **What counts as approved.** A response counts as approved only when its
  body says `approval_status: "approved"` and the header (if present) says
  `approved`.
  - Anything missing, contradictory or unknown counts as "not approved".
  - Projection routes must carry the envelope; a missing envelope is read
    as not approved.
  - Body approval fields are read only on projection routes (the
    `/meta/approval` body is not an envelope).
- **Global banner** (`components/status/ApprovalBanner.tsx`). It is driven
  by `/meta/approval` and by every response's envelope and header,
  through a listener in the client.
  - Production with no live model: a calm blue "No approved model" banner.
  - Private test: a sticky amber "NOT APPROVED. Private test environment
    only, not a recommendation." banner.
  - API unavailable: "Model status unavailable", with projections treated
    as off.
  - Approved: no banner.
  - One "not approved" response keeps the banner up until the page
    reloads.
- **`GatedView`.** It wraps every projection view.
  - In production it shows "No approved model" before making any request.
    The Playwright test confirms no gated request is sent, even with a
    league.
  - In a private test without a snapshot, it shows the no-snapshot state.
- **`ProjectionNotice`.** Every projection-derived result shows a
  prominent block with the API's `label`, every `notices[]` entry, and the
  run (source, season and week, model version, sims, written at).
- **Wording.** "approved" is never displayed unless the API returns it.
  - The model card maps the registry words of contract v1.3 (`candidate`,
    `rejected`, `retired`, `awaiting_release`, `live`) to plain language;
    an older registry value of `approved` is shown as "Awaiting release".
  - `tests/unit/wording.test.ts` scans all UI source for "lock",
    "guaranteed", "can't miss", FantasyPros/ECR/consensus and sportsbook
    words, and for approval claims.
  - Every Playwright page visit checks the rendered text for the same.
  - Start/sit, waivers and trade show the API's "No guarantees"
    disclaimer, or a built-in one if it is missing.
- **Low confidence.** Boom/bust badges are dashed and say "Low confidence"
  when the row says `boom_bust_confidence: "low"` or when the response's
  `boom_bust_low_confidence` list names the position (v1.3, D-104). A note
  above each table names those positions. Nothing in the UI hardcodes the
  set.
- **Restricted data.** No FantasyPros or consensus data. The only
  related text is the API's attribution sentence saying such data is never
  shown. The built-in copy avoids even the word.

### Design and accessibility

- **Visual identity.** Original: a "Gridiron Projections" mark (yard lines
  with a distribution curve), warm neutral surfaces, a turf-green accent,
  and system fonts with tabular numbers.
- **Colors.** Tokens are defined on `:root`, with dark values for both
  `prefers-color-scheme` and `data-theme`.
  - `tests/unit/contrast.test.ts` checks text pairs at 4.5:1 and UI parts
    at 3:1 in both themes. It also checks that the two dark blocks are
    identical.
  - Position markers use a categorical palette validated with the dataviz
    validator (CVD and normal-vision separation) on both surfaces. They are
    always next to the position text.
- **Distribution strip.** It uses a shared scale per view, a recessive
  track, the 10th to 90th percentile band in one hue, and an ink median
  tick with a surface ring. Values are printed beside it, with an
  `aria-label` on the strip. Players with fewer than 20 active simulations
  show "Too few simulations for a range".
- **Semantics and keyboard.**
  - Semantic HTML: tables with captions, fieldsets and legends, and labels
    on every field.
  - Forms have an error summary that links to its fields, plus
    `aria-invalid` and `aria-describedby`.
  - Tabs follow WAI-ARIA with arrow, Home and End keys.
  - Buttons use `aria-pressed` and `aria-expanded`; the mobile menu closes
    on Escape.
  - Focus rings are visible, and reduced motion is respected.

## UI-4.10: contract v1.3 and the QA findings (2026-11-05)

The Backend answered the six questions of 2026-11-04 in contract v1.3
(docs/API.md changelog). What changed in `web/`:

1. **422 `player_not_found` with `details.player_ids`.**
   `lib/api/result.ts` gained `playerNotFoundIds()`. Start/sit names the
   players the run does not cover and asks to remove them (a calm state,
   not an error panel); the manual-league form maps the ids onto its
   players field. 404 stays reserved for path and query resources.
2. **`GET /players?search=&position=`.** `components/setup/PlayerPicker.tsx`
   replaces the raw-id textarea: a typeahead that starts at two
   characters, debounces 300 ms, asks for at most 50 rows, filters by
   position, and keeps picks as chips with remove buttons (at most 60,
   the form's limit). Only ids are sent. The proxy now allows the
   `players` root; the API's `Cache-Control: public, max-age=300` is
   passed through.
3. **`boom_bust_low_confidence`.** `BoomBust` takes the response's list
   and `LowConfidenceNote` names the positions ("Boom and bust chances for
   defenses, kickers and quarterbacks (DST, K, QB) are low confidence in
   this run"). The per-row `boom_bust_confidence` still counts; the UI no
   longer decides which positions are low confidence.
4. **Registry words.** `registryStatusLabel` maps `candidate` ("Candidate,
   under test"), `rejected` ("Rejected: did not pass its test"),
   `retired`, `awaiting_release` ("Awaiting release") and `live` ("Live,
   serving projections" when `serving` is true). The word "approved" never
   comes from the UI.
5. **`/meta/season` `offseason: null`.** The type allows null; the banner
   and the home status key on `mode`, as before.
6. **QA-F15 (proxy body size).** `app/api/[...path]/route.ts` refuses a
   `Content-Length` above 256 KiB before reading, then counts bytes while
   the body streams and stops at the limit (413 `payload_too_large`). The
   body is forwarded as bytes, so multi-byte text is measured correctly.
   It also passes `X-Forwarded-For` on, for the API's per-client rate
   limiter behind a reverse proxy (`FFML_TRUSTED_PROXIES`).
7. **QA-F16 (CSP `'unsafe-inline'`).** Removed for scripts. `web/proxy.ts`
   sets a per-request nonce:
   `script-src 'self' 'nonce-…' 'strict-dynamic'`, with the other
   directives as before. Next.js puts the nonce on its own inline scripts
   when it finds it in the request's CSP header, and `app/layout.tsx`
   reads it through `headers()` for the theme bootstrap. The cost, as the
   Next.js CSP guide documents, is that every page renders per request
   instead of being prerendered (the build now lists all 13 routes as
   dynamic). `style-src` keeps `'unsafe-inline'` for React style
   attributes (the distribution strip positions its marks that way); no
   external style sources are allowed. The CSP is only set in production,
   as before.
8. **QA-F18 (minimatch, lint-time).** No patched 10.x exists: the advisory
   covers 7.0.0 to 11.0.0 and 10.2.6 is the newest 10.x. Accepted as
   lint-time only; revisit when a patch lands.

## Evidence

Run in `web/` with Node 26.13.0 and npm 11.20.0 on 2026-11-05.

- `npm run lint` (`eslint . --max-warnings=0`): exit 0, no problems.
- `npm run typecheck` (`tsc --noEmit`): exit 0.
- `npm test` (vitest 5.1.0): `Test Files 12 passed (12)`,
  `Tests 106 passed (106)`. By file:
  - result 22 (state mapping: 503 no_approved_model, 503
    no_private_test_snapshot, the not-approved envelope, header/body
    disagreement, missing envelope, errors, 422 details, 422
    player_not_found ids, unreachable, 204);
  - proxy 6 (allowed roots, dot segments, Content-Length pre-check, a
    600 KB body of 200k three-byte characters refused, a body within the
    limit forwarded byte for byte, 502 api_unreachable);
  - client 7;
  - approval-mode 7;
  - banner 8;
  - about 9;
  - league-form 13;
  - setup 7 (import, create and the /players typeahead run against the
    contract mock);
  - projections 5;
  - views 9;
  - contrast 3;
  - wording 10.
- `npx next build`: compiled successfully, TypeScript passed, 13 routes
  plus the proxy, all rendered per request (nonce CSP).
- `npm run e2e` (Playwright 1.64.1, Chromium, contract mock):
  `28 passed (35.5s)`: 12 production and 16 private_test tests. New in
  this round: the CSP test (header has a script nonce and no
  `'unsafe-inline'`, the nonce changes between requests, client
  navigation and the theme toggle work, no CSP violations on the console)
  and the player picker step in the manual-form test.
- `npm run e2e:real` was not repeated in this round (other agents are
  active; one heavy process at a time). The previous run on 2026-11-04
  passed 26/26 in both modes against the real API with a read-only
  Sleeper import of `ethanye19`; the command is unchanged.
- **Processes.** All mock, `next start` and uvicorn processes were
  stopped; none remain.

## How to run

```
cd web
npm ci
npm run dev                        # needs the API: FFML_API_URL (default http://127.0.0.1:8000)
npm run mock                       # contract mock on :8765 (MOCK_MODE=production|private_test)
npm run check                      # lint + typecheck + unit tests
npm run build && npm run e2e       # Playwright against the mock, both modes
npm run build && npm run e2e:real  # Playwright against the real API, both modes
```

The browser only calls `/api/*` on the web origin. The route handler
forwards those calls to `FFML_API_URL`, read at request time, so one build
works everywhere and CORS is not needed.

## Not done / waits on others

- **Gate 4.** Gate 4 stays open (D-099): no approved model exists to test
  end to end.
  - The approved path is covered only by unit tests. The banner disappears
    and the run line shows instead of the notice.
  - When a model passes, run `npm run e2e:real` against a production API
    that serves it.
- **Docker.** `Dockerfile.web` has not been built locally (no Docker).
  `docker-compose.yml` belongs to the Backend Engineer. It needs a `web`
  service built from `Dockerfile.web` with `FFML_API_URL=http://api:8000`
  and port 3000.
- **CI.** No web job yet (`.github/` is QA's path). Suggested steps:
  `npm ci && npm run check && npm run build && npx playwright install --with-deps chromium && npm run e2e`.

## Contract questions (2026-11-04) and their answers (v1.3)

All six were answered in contract v1.3 and adopted above: QB is in the
default low-confidence set and the set is returned per response; the
registry words never include "approved"; body ids give 422
`player_not_found` with `details.player_ids`; `GET /players` exists;
`/meta/season` returns `offseason: null` for an unknown mode; the default
week is the served run's week (the UI sends none and offers
`details.available_weeks` on a 404). No open contract questions.

## Decisions proposed

- **D-UI-1.** The web app calls the API only through its own `/api/*` route
  handler, which forwards to `FFML_API_URL` at request time.
  - It allows only the contract's route families (`meta`, `leagues`,
    `sleeper`, `players`, `projections`, `ros`).
  - It rejects dot segments, counts request bodies in bytes and refuses
    them above 256 KiB, and passes through `X-FFML-Approval`,
    `Content-Type`, `Cache-Control` and `Retry-After` (and `X-Forwarded-For`
    towards the API).
- **D-UI-2.** The browser remembers the chosen league id in `localStorage`
  (a per-browser convenience). League data and "my team" stay in the API.
  This is an interim choice in place of the signed session cookie
  mentioned in D-011, because the API has no session endpoint.
- **D-UI-3.** The UI never shows the word "approved" unless the API returns
  `approval_status: "approved"`, and one not-approved response keeps the
  global banner up until the page reloads.
- **D-UI-4.** `web/mock/` is a test-only contract mock, used by unit tests,
  Playwright and `npm run mock`. The app never imports it. Its data is
  fictional and always labeled "Fixture data for testing; not real
  projections."
- **D-UI-5.** The production CSP uses a per-request nonce for scripts
  (`web/proxy.ts`); pages are therefore rendered per request rather than
  prerendered. `style-src` keeps `'unsafe-inline'` for React style
  attributes.

## Next

- Add the web service to docker-compose and a CI job (Backend Engineer and
  QA).
- Re-run `npm run e2e:real` when chain_v2 private-test runs land, and again
  when a model passes its pre-registered test (Gate 4).
