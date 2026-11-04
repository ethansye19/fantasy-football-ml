# Phase 4 / Frontend Engineer / UI-4.1 to UI-4.9
Date: 2026-11-04
Scope: the web app in `web/` (Next.js 16 App Router, TypeScript, Tailwind 4)
built against docs/API.md (v1, then v1.2), plus `Dockerfile.web`. Phase 4
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

## Done

| Commit | What |
| --- | --- |
| 439f0d1 | UI-4.1 shell. Covers the layout, themes (no flash; system, light or dark), skip link, mobile menu, error boundaries, 404, the global approval banner and the season banner. Typed client `web/lib/api/{types,client,result,hooks}.ts`. Same-origin `/api/*` proxy (`web/app/api/[...path]/route.ts`). Contract mock `web/mock/`. Unit tests. |
| 73ff345 | UI-4.8 info pages. `/about`, `/about/model-card`, `/about/limitations`, `/about/attribution`. Content comes from `/meta/*`, with built-in copy as a fallback (DATA_SOURCES.md wording). |
| d243032 | UI-4.2 `/setup`. Sleeper import (username, then league, then my team; or import by league id), manual form, league list with use/sync/delete, and a settings editor for the current league. |
| 687d891 | UI-4.3 `/projections`. Distribution strip, boom/bust badges with low-confidence marking, drivers, filters, preset scoring when no league is set, and `GatedView`. |
| 0df2fb5 | UI-4.4 to UI-4.7: `/start-sit`, `/waivers`, `/trade`, `/rankings`. |
| 1519ab5 | UI-4.9 Playwright harness. Start/sit quick picks now come from my roster's projections. |
| c4f2456 | `Dockerfile.web` (standalone Next server, non-root, health check). |
| this commit | Status note; the projections lead now says ranges are for when the player plays (D-102). |

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
  - The model card maps a registry status of `approved` to "Awaiting
    release" while the gate is not approved.
  - `tests/unit/wording.test.ts` scans all UI source for "lock",
    "guaranteed", "can't miss", FantasyPros/ECR/consensus and sportsbook
    words, and for approval claims.
  - Every Playwright page visit checks the rendered text for the same.
  - Start/sit, waivers and trade show the API's "No guarantees"
    disclaimer, or a built-in one if it is missing.
- **Low confidence.** Boom/bust badges are dashed and say "Low confidence"
  whenever `boom_bust_confidence == "low"`.
- **Restricted data.** No FantasyPros or consensus data. The only
  related text is the API's attribution sentence saying such data is never
  shown. The built-in copy avoids even the word.

### Design and accessibility

- **Visual identity.** Original: a "Gridiron Projections" mark (yard lines
  with a distribution curve), warm neutral surfaces, a turf-green accent,
  and system fonts with tabular numbers. All pages are static except the
  API proxy.
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

## Evidence

Run in `web/` with Node 26.13.0 and npm 11.20.0.

- `npm run lint` (`eslint . --max-warnings=0`): exit 0, no problems.
- `npm run typecheck` (`tsc --noEmit`): exit 0.
- `npm test` (vitest 5.1.0): `Test Files 11 passed (11)`,
  `Tests 97 passed (97)`. By file:
  - result 21 (state mapping: 503 no_approved_model, 503
    no_private_test_snapshot, the not-approved envelope, header/body
    disagreement, missing envelope, errors, 422 details, unreachable, 204);
  - client 7;
  - approval-mode 7;
  - banner 8;
  - about 9;
  - league-form 12;
  - setup 6 (import and create flows run against the contract mock);
  - projections 5;
  - views 9;
  - contrast 3;
  - wording 10.
- `npx next build`: compiled successfully, TypeScript passed, and 13 routes
  were generated (12 static, 1 dynamic: `/api/[...path]`). The build takes
  about 20 s.
- `npm run e2e` (Playwright 1.64.1, Chromium, contract mock):
  `26 passed (1.3m)`. That is 11 production and 15 private_test tests.
- `npm run e2e:real` (real API, uvicorn per mode, fresh SQLite, read-only
  Sleeper import of `ethanye19`): `26 passed (2.5m)`.
  - In production, every projection view shows "No approved model", and
    `GET /api/projections` returns 503 with `x-ffml-approval: none`.
  - In private_test, the API served the 2024 week 11 replay. The views
    showed the replay notice, model `chain_v1`, and "Not approved".
  - The first real run found a UX issue. Quick picks offered roster
    players who are not in the replay (a DST and a 2025 rookie K), and the
    API answered `player_not_found`; the UI showed that error state
    correctly. Fixed in 1519ab5: quick picks now use `availability=mine`.
- **Screenshots** (scratchpad, not committed). Every page was checked at
  360 px and 1280 px, in light and dark, with no horizontal page overflow
  and no console errors. Two layout bugs were fixed along the way:
  - unlayered `.btn` padding beat Tailwind's utility classes;
  - the boom/bust column overflowed at 360 px.
- **Standalone check** (the steps of `Dockerfile.web`, run natively). It
  used `NEXT_OUTPUT=standalone next build` and then `node server.js`.
  - Pages and CSS returned 200.
  - The CSP header was present.
  - `/api/projections` returned 503 with `x-ffml-approval: none`.
- **Processes.** All dev, mock, uvicorn and standalone servers were
  stopped; no node or uvicorn processes remain.

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
- **Manual-league rosters.** These accept ids only (Sleeper ids, gsis ids,
  team codes). A player search needs an ungated endpoint; see question 4.

## Contract questions for the Backend Engineer

1. **QB low confidence.** `/leagues/{id}/projections` returns
   `boom_bust_confidence: "normal"` for QBs on the real replay. gate-3.md
   and D-097 say QB boom/bust "is now low confidence" (slopes 0.56 and
   0.77), and the API's own model card lists QB miscalibration. Should QB
   rows be `low` too? The UI already renders whatever the API sends.
2. **Registry status wording.** `/meta/model-card` `models[].registry_status`
   can be `approved` (a registry state) while the gate says not approved.
   The UI shows "Awaiting release" in that case. Could the API expose this
   as something other than the bare word "approved"? Alternatively, add
   `serving` per model.
3. **Manual-league error status.** A manual league with unknown player ids
   returns 422 `player_not_found`; section 4 lists `player_not_found` under
   404. Please note the 422 case in the contract. The UI handles both.
4. **Player search.** There is no ungated way to search players, so manual
   leagues need raw ids for "my team". Could `GET /players?search=` return
   names, positions, teams and ids only, with no projections and no gate?
5. **`/meta/season` for unknown mode.** It returns `offseason: true` when
   `mode == "unknown"`. The UI keys on `mode` and shows "Season status
   unknown", not an offseason banner. Is `offseason: true` intended for
   unknown?
6. **Default week.** Projections and start/sit take an optional `week`.
   The UI sends none, so the API uses the run's week; on a
   `week_not_available` 404 it offers the weeks in `details`. Confirm this
   is the intended default once runs carry several weeks.

## Decisions proposed

- **D-UI-1.** The web app calls the API only through its own `/api/*` route
  handler, which forwards to `FFML_API_URL` at request time.
  - It allows only the contract's route families (`meta`, `leagues`,
    `sleeper`, `projections`, `ros`).
  - It rejects dot segments, limits bodies to 256 KB, and passes through
    `X-FFML-Approval`, `Content-Type`, `Cache-Control` and `Retry-After`.
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

## Next

- Add the web service to docker-compose and a CI job (Backend Engineer and
  QA).
- Re-run `npm run e2e:real` when chain_v2 private-test runs land, and again
  when a model passes its pre-registered test (Gate 4).
