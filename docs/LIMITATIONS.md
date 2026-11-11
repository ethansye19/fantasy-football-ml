# Known limitations and next improvements

Last updated 2026-11-11 (OR-6.2; D-127 window). This list is ordered by priority: P1
matters most. Each item gives the evidence, what limits the damage today,
and the next improvement.

**How to read it.** Every number is copied from a committed report or
decision, and the file is named next to it. The chain_v2 live test freezes
the model, feature, simulation, scoring and scorer code from the freeze until
the verdict, about 2027-11-19 (docs/status/livetest-preregistration-2026.md;
D-111, D-127). So any improvement to those paths must wait for the verdict or
be built as a separate v3. A v3 lives on its own branch or outside every
frozen path, never reads the sealed results, and needs its own pre-registered
test on weeks it has not seen (pre-registration, "A separate v3 during the
test"). Changes to `api/`, `web/`, `scripts/`, `docs/` and `.github/` are
allowed, but each must be logged in docs/status/livetest-changelog-2026.md
before first use.

The model card (reports/MODEL_CARD.md) gives the metrics behind P1 to P5.

## P1. No approved model; the app serves no projections until about 2027-11-19

- **Evidence.**
  - chain_v1 was NOT APPROVED on the one-time 2025 holdout, because L17 (QB tie-aware coverage) was 0.739, outside [0.75, 0.85]. Gate 3 failed (D-097; docs/status/gate-3.md; reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md).
  - No resized nine-week design met the pre-set limits; the best good-model pass rate was 0.614, below 0.70. The fallback therefore applies: the unchanged 22 lines over 18 weeks, with no provisional tier (D-110). The window is 2026 weeks 11-18 and 2027 weeks 1-10 (D-127).
  - The verdict is expected about 2027-11-19 (D-127).
- **Effect.**
  - In production, every projection route answers `no_approved_model`. The real app shows no projections, start/sit, waivers, trades or rest-of-season rankings through the rest of 2026, the 2027 draft and 2027 weeks 1-10 (D-099, D-100, D-101, D-127).
  - Gate 4 stays open until a model passes and is connected end to end (D-099).
- **Mitigation now.**
  - chain_v2 is under the pre-registered live test (D-098).
  - The screens are built and tested on fixtures, replays and private outputs labeled "not approved" (D-099, D-101).
- **Next improvement.**
  - Run the live test as frozen.
  - In parallel, a v3 may address P2 and P4 under the v3 rules above. Passing the v2 test confers nothing on v3.

## P2. QB 80% ranges are too narrow

- **Evidence.**
  - On 2025, QB tie-aware coverage was 0.739 calibrated and 0.693 raw. 13.5% of outcomes fell below q10 and 11.6% above q90, against 10% each (D-097).
  - On 2022-2024, raw QB coverage already failed at 0.735 and reached 0.779 only after calibration (reports/backtest_2026-11-03_v3.md section 12).
  - On 2025 the calibrated transparent baselines covered QB at 0.809 to 0.820, so the chain's QB coverage gap was worse than all three (holdout report, calibrated grid).
  - The live-test pass-rate estimate does not simulate season-wide shifts such as this drop from 0.779 to 0.739 (pre-registration, "Not simulated").
- **Effect.**
  - QB floors and ceilings understate the real spread.
  - The coverage line L17 is the most likely single reason for chain_v2 to fail as well.
- **Mitigation now.**
  - chain_v2's calibrator updates walk-forward every week, from earlier weeks including 2025 (D-098). This change was decided after the 2025 result, so only unseen live weeks test it.
  - The app labels QB boom/bust low confidence (P5).
- **Next improvement.**
  - Under v3: investigate the QB dispersion of the simulation (Dirichlet concentration, Gamma shape, play sd). docs/MODELING.md section 6 says these are tuned on validation seasons only, never on the holdout.
  - Report QB coverage by season, not only pooled, so a shift like 2025 is visible before a holdout.

## P3. Request-time calibration is not built (blocks Gate 4)

- **Evidence.**
  - The API applies league scoring to the simulations and serves raw simulation quantiles, with a notice in each response. The Evaluator assesses calibrated numbers, so request-time calibration is required before Gate 4 (D-103).
  - The design is Backend proposal P-2 in docs/status/phase4-backend.md: per-position calibrated levels and boom/bust isotonic maps per reference scoring, carried in the run manifest, with the nearest preset used for custom scoring (D-072).
- **Effect.**
  - Even after a model passes, the app's ranges would not be the calibrated numbers the test assessed.
  - Under unusual scoring, ranges may be somewhat too narrow or too wide (web/lib/content.ts, app limitations).
- **Mitigation now.**
  - Production serves nothing (P1).
  - Private-test responses state that the quantiles are raw.
- **Next improvement.**
  - Build P-2 in `api/` (an allowed path, logged in the change log) from the calibrator state that each week's commitment already stores (pre-registration, "calibration").
  - The Evaluator checks that it reproduces the calibrated preset rows before Gate 4.

## P4. Parity with the public expert consensus, not superiority

- **Evidence.**
  - On 2025 the chain did not beat the public expert consensus on MAE at any position, and it was worse at QB and RB. There was no detectable difference at WR, TE, K and DST (D-097).
  - On 2022-2024 there was no detectable MAE difference at any position (reports/backtest_2026-11-03_v3.md section 12).
  - Start/sit was not worse at any position in either period, and on 2025 it was better at TE (D-097).
  - The figures are in the private evaluator reports listed above and stay private under D-007.
- **Effect.**
  - The project can claim that the chain beats simple baselines, offers full distributions and handles any scoring.
  - It cannot claim to be more accurate than experts.
- **Mitigation now.**
  - The model card states this plainly.
  - The consensus is never a model feature, the verdict rules were not changed after the results were seen, and nothing was tuned on 2025 (D-082).
- **Next improvement.**
  - Work on the QB and RB stages, where the gap shows, is v3 work with its own test.
  - The consensus stays out of the model (D-007, D-051).

## P5. Boom/bust probabilities are low confidence for QB, K and DST

- **Evidence.**
  - On 2022-2024, after the per-position calibrator, the calibration slopes were: K boom 0.118, K bust 0.456, DST boom 0.774, DST bust 0.289 (reports/backtest_2026-11-03_v3.md section 5.2).
  - On 2025: QB boom 0.563 and bust 0.769; DST boom 0.226 and bust 0.528; K bust 0.735 (holdout report).
  - The rule (D-086) treats a slope outside [0.8, 1.2] as low confidence.
- **Effect.** For these positions the boom and bust percentages are not reliable.
- **Mitigation now.**
  - The app hides them or marks them low confidence. The set comes from the run manifest, with {QB, K, DST} as the default (D-089, D-104).
  - RB, WR and TE are within the rule in both periods.
- **Next improvement.**
  - A smoother calibrator for K and DST is on the improvements list (D-089).
  - Boom and bust are not among the 22 lines, so removing a flag needs its own pre-registered check.

## P6. The live test is small: even a good model passes only about 58% of the time

- **Evidence.**
  - Over 18 scored weeks, a good model (the validated accuracy, exactly calibrated) passes the 22 lines with probability 0.580 in the D-127 window (2026 weeks 11-18, then 2027 weeks 1-10). With one missed week the figure is 0.540, and with two it is 0.498 (5,000 replicates each; data/reports/live_test_power_shifted_window.json). The original window gave 0.557, 0.518 and 0.500.
  - A model with no edge passed in none of 5,000 replicates (95% upper bound 0.0006) (pre-registration, "Missed weeks").
  - Any nine-week design that changes only the accuracy lines tops out near 61%, because the start/sit and coverage lines set that limit (D-110).
  - The 500-draw bootstrap's fixed weights move the estimate by about 5 points, depending on the calendar order of the weeks (D-111; docs/status/livetest-power-reconciliation.md).
- **Effect.**
  - A failure would be weak evidence that the chain is poor.
  - A pass would be strong evidence that it meets the rules.
  - Either way the answer arrives a year from now.
- **Mitigation now.**
  - The design, the binding 500-draw seed-0 rule and the report-only 5,000-draw check were fixed before the freeze.
  - Interim results stay sealed and are never used to stop early or tune (D-111).
- **Next improvement.** Size every future test with the power simulator (`ffml/eval/power.py`) before it is frozen, and include season-level shifts in the simulation.

## P7. The live test depends on a single laptop

- **Evidence.**
  - Builds and Friday scoring run as logged-off Windows Task Scheduler tasks on the owner's laptop. A GitHub Actions watchdog alerts 6 hours and 1 hour before kickoff (D-107, D-109; docs/RUNBOOK.md).
  - Up to two missed weeks are excluded, and a third miss makes the test INVALID (D-111).
  - GitHub sends scheduled-run failure mail only to the user who last changed the workflow's cron line. In a public repository it also disables schedules after 60 days without activity (QA-F25, docs/SECURITY.md; docs/RUNBOOK.md section 11).
- **Effect.** A dead laptop, a power or network outage, or a silenced watchdog during a kickoff window costs a week. Three such weeks void the test.
- **Mitigation now.**
  - Runs start at least 24 hours before kickoff and retry every 30 minutes.
  - The tasks are set to run on AC power and to wake the laptop if allowed.
  - A logged-off dry run is required before Week 10 (D-107).
  - The runbook's monthly watchdog check and its notification settings cover QA-F25 (docs/RUNBOOK.md section 11).
  - The pre-registration has a laptop-replacement rule: the pinned worktree is rebuilt from `requirements.lock.txt`, and replay checks must be byte-identical before the first build.
- **Next improvement.** Prepare a standby machine under that replacement rule ahead of time.

## P8. No authentication (QA-F14, Gate 6)

- **Evidence.**
  - Any client that reaches the API can list, change and delete every league (QA-F14, docs/SECURITY.md).
  - Accounts and the D-011 signed session cookie are not built. The browser keeps the league id in localStorage (D-011, D-114).
- **Effect.** The app is safe only for one person on localhost. It must not be deployed to a shared or public host.
- **Mitigation now.**
  - docker compose binds the API to 127.0.0.1, and the browser reaches it only through the web app's proxy (D-113, D-114).
  - The API is documented as single-user (docs/API.md).
- **Next improvement.** Build the D-011 session cookie, scoping leagues to a browser, before Gate 6. Its secret must be required in production with no default (QA-F17 note).

## P9. Docker is verified in CI only; the deployment guide and the end-to-end job are missing

- **Evidence.**
  - Docker is not installed on the development machine (D-004).
  - The `api-image` job in `.github/workflows/ci.yml` builds and starts the postgres, api and web containers. It checks health, the `no_approved_model` answer on every projection route, the security headers and the web proxy. The job passed on 2872d13 and 997fb22 (GitHub Actions runs 42357904277 and 42357978323).
  - `docs/DEPLOYMENT.md` does not exist (OR-6.1).
  - docs/SETUP.md section 5 still lists the API and web commands as "intended".
  - There is no end-to-end job that ingests a small slice, predicts, serves and renders (QA-6.1). The Playwright smoke runs against the mock API (`ci.yml`, web job).
- **Effect.**
  - Gate 6 cannot pass: it requires green end-to-end tests in CI, a fresh install verified with logs, and every deliverable present (docs/PLAN.md).
  - A local container run has never been tried.
- **Mitigation now.** The compose stack is rebuilt and smoke-tested on every push.
- **Next improvement.**
  - Write docs/DEPLOYMENT.md and finish docs/SETUP.md section 5.
  - Add the QA-6.1 end-to-end job.
  - Record a fresh-install run on a clean CI runner with its logs.

## P10. Scheduled refresh jobs exist only for the live test

- **Evidence.**
  - The scheduler container (DE-5.2) and the GitHub refresh workflows (DE-5.3) were outside the Phase 5 brief and are not built (docs/status/phase5-data-engineer-livetest.md).
  - `docker-compose.yml` has only postgres, api and web.
  - `.github/workflows/` holds only `ci.yml` and `livetest_watchdog.yml`.
  - docs/ARCHITECTURE.md section 6 still shows the planned scheduler.
- **Effect.**
  - The scheduled jobs in the brief run only as part of the live-test runner on the laptop: post-game updates, injury refreshes, retraining checks, data-quality checks and failure alerts.
  - A deployed stack would not refresh itself.
- **Mitigation now.**
  - The live-test runner refreshes data through the pinned `ffml refresh` module before each build and each scoring run.
  - Failure alerts and the watchdog cover those jobs (docs/RUNBOOK.md).
- **Next improvement.** Build DE-5.2 and DE-5.3 under `scripts/` and `.github/` (allowed paths, logged in the change log), and update the topology diagram to match.

## P11. Stat corrections after the Friday cutoff are ignored

- **Evidence.**
  - Each live-test week is scored against the first processed snapshot at or after 12:00 UTC on the first Friday at least 72 hours after the week's last kickoff.
  - Later corrections never enter that week. The snapshot ids and a SHA-256 of the outcome rows are sealed with it (D-116 B3, assumption A3 under D-117; pre-registration, "Weekly scoring").
  - nflverse applies NFL stat corrections from Wednesday into Thursday (docs/DATA_SOURCES.md, quirk 7), so the usual corrections land before the cutoff.
- **Effect.** A rare late correction makes a scored outcome differ from the final official stat line. Every model and comparator is scored on the same sealed outcomes.
- **Mitigation now.** The cutoff is fixed in advance. Processed snapshots are never pruned during the window, so every week can be rescored from its pins (D-118).
- **Next improvement.** After the verdict, an information-only comparison of sealed against final outcomes can show the size of the effect. It cannot change the verdict.

## P12. Deferred model features

- **Evidence.**
  - Lagged player fumble-rate and return-TD features are deferred, because they need a feature-version bump and a rerun of every backtest for a small fantasy-point effect (D-068).
  - Also not built: FTN charting features (off by D-009), the Next Gen Stats eight-plus-defenders rate, and a defensive pressure rate. Completion probability over expected comes from play-by-play instead of Next Gen Stats (docs/FEATURES.md, "Known gaps").
- **Effect.** Fumbles lost and return touchdowns rest on simpler rates, and some efficiency signals are missing.
- **Mitigation now.** Features 0.2.0 are pinned and identical across validation, holdout and live test (pre-registration, "pins").
- **Next improvement.** v3 only, with a new feature version and a full backtest rerun. Using FTN features would also bring share-alike duties (D-009).

## P13. Backtest optimism from closing lines and lagged injuries

- **Evidence.**
  - Historical point spreads and totals are closing lines (D-008). Removing them makes the chain clearly worse at QB, RB, K and DST (reports/backtest_2026-11-03_v3.md section 10), so the lines matter.
  - The injury dataset has no timestamps, so backtests lag same-week designations (D-006).
- **Effect.**
  - Validation accuracy is slightly optimistic for a midweek build.
  - Live builds see the latest reports, so live behavior differs from the backtest configuration.
- **Mitigation now.**
  - Live builds read the current schedule lines and the latest injury report (D-006, D-065).
  - The live test measures the real pre-kickoff setting.
- **Next improvement.** After the verdict, compare the lines each live build used with the closing lines, to measure the gap.

## P14. Product scope and attribution items

- **Not built.**
  - The stretch features: live draft assistant, ESPN and Yahoo import, paper-only prop comparison and user accounts (docs/PLAN.md, "Stretch").
  - IDP slots are accepted and ignored (D-010).
- **Waivers speed.** On a realistic league the waivers route runs at p95 320 to 445 ms. The 1-second bound is accepted (QA-F10).
- **Small samples.** Players active in fewer than 100 simulations are flagged low sample (Backend proposal P-1).
- **Attribution.**
  - The ffopportunity data license is not stated on its reference page. DATA_SOURCES.md asked for it to be confirmed before the data is shown in the UI.
  - The app uses it only as a model input, and the attribution page says the license is not stated.
- **Next improvement.**
  - Confirm the ffopportunity license with its maintainers.
  - Keep the attribution page in step with docs/DATA_SOURCES.md whenever a source or flag changes.
