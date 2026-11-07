# Phase 6 / Documentation / OR-6.2

Date: 2026-11-07

Scope: final model card, a prioritized limitations list, the README
deliverables table and status sections, and a review of the attribution page
(docs/PLAN.md, OR-6.2). Documentation only. No code, decision log,
pre-registration, `ffml/`, `api/`, `web/` or `scripts/` file was changed.

## Task list

1. Write `reports/MODEL_CARD.md`.
   - Intended and out-of-scope use.
   - Data and attribution.
   - The chain.
   - The protocol.
   - Metrics from the v3 and 2025 holdout reports.
   - Calibration disclosures for D-085, D-086 and D-088.
   - The low-confidence boom/bust positions.
   - The approval status and the live-test design.
   - Ethics.
2. Write `docs/LIMITATIONS.md`: a prioritized list with evidence, current mitigation and the next improvement for each item.
3. Update `README.md`: a deliverables table covering every PROMPT_1 deliverable, and a current-status section.
4. Review the attribution page against docs/DATA_SOURCES.md.

## Done

- **`reports/MODEL_CARD.md`.**
  - Every number is copied from:
    - reports/backtest_2026-11-03_v3.md;
    - reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md and its addendum;
    - docs/status/gate-3.md;
    - docs/status/livetest-preregistration-2026.md;
    - reports/ml_stage_selection_2026-11-03.md;
    - docs/DECISIONS.md.
  - No metric was computed, and nothing under `data/` or `artifacts/` was read.
  - The card neither names the consensus source nor gives its figures. The comparison is described qualitatively, and the private reports are cited with the D-007 note.
- **`docs/LIMITATIONS.md`.** Items P1 to P14:
  - P1: no approved model;
  - P2: QB ranges too narrow;
  - P3: request-time calibration not built (blocks Gate 4);
  - P4: parity with the consensus, not superiority;
  - P5: QB, K and DST boom/bust low confidence;
  - P6: a small-sample live test with about a 56% pass rate for a good model;
  - P7: a single-laptop live test;
  - P8: no authentication (QA-F14, Gate 6);
  - P9: Docker verified in CI only, with no deployment guide and no end-to-end job;
  - P10: no scheduler container or refresh workflows;
  - P11: stat corrections after the Friday cutoff ignored;
  - P12: deferred features (D-068 and others);
  - P13: optimism from closing lines and lagged injuries;
  - P14: product scope and attribution items.
- **`README.md`.** The outdated Phase 3 status paragraph is replaced, and "Current status" and "Deliverables" sections are added. The Documents table and the attribution summary are unchanged.

## Evidence

- **Copy rules.** The project's own rules in `tests/compliance/qa_text_rules.py` (approval claims, banned language, other product names) were run over the three files. Results:
  - `reports/MODEL_CARD.md`: approval [], banned [], products [].
  - `README.md`: approval [], banned [], products [].
  - `docs/LIMITATIONS.md`: approval [], products []. One "lock" hit, which is the file name `requirements.lock.txt`.
  - "WalterPicks" and "FantasyPros" appear in none of the three files.
- **CI.** All five jobs of `ci.yml` passed on 2872d13 (run 42357904277) and on 997fb22 (run 42357978323), according to `gh run view`. The five jobs are python, api-postgres, api-image, web and security. The run on d9c6808 was still in progress when checked. These runs are cited in LIMITATIONS P9 for "Docker verified in CI only".
- **Files checked for the deliverables table.**
  - Present: `docs/ARCHITECTURE.md` (Mermaid diagrams), `docs/SETUP.md`, `docs/DATA_DICTIONARY.md`, `docs/RUNBOOK.md` and `web/app/about/attribution/`.
  - Absent: `docs/DEPLOYMENT.md`.
  - `docker-compose.yml` defines postgres, api and web.
  - `.github/workflows/` holds `ci.yml` and `livetest_watchdog.yml`.

## Attribution page review

The page reviewed is `GET /meta/attribution` (`api/services/meta_content.py`),
with `web/lib/content.ts` as the fallback. It was checked against
docs/DATA_SOURCES.md.

- **Matches.**
  - nflverse text and license (CC-BY-4.0), with the Next Gen Stats and Pro Football Reference notes.
  - The FTN wording, with `in_use` tied to the flag (off, D-009).
  - The Sleeper credit and the non-commercial usage note (D-013).
  - The ffopportunity credit, with "license not stated".
  - The consensus is marked as never displayed (D-007).
- **Finding F-DOC-1 (low, Frontend).** The web fallback attribution in `web/lib/content.ts` lists four sources. It lacks the DynastyProcess player-id entry that the API returns. The fallback is shown only when the API is unreachable.
- **Finding F-DOC-2 (low, Backend).**
  - The API model-card `status_summary` in `api/services/meta_content.py` does not give the live-test window, the expected verdict date (about 2027-11-12, D-119) or the "about 56%" pass-rate statement.
  - Its limitations say that QB boom/bust "were miscalibrated" but do not say "low confidence" as D-104 does.
- **Open (LIMITATIONS P14).** The ffopportunity data license is still unverified, although DATA_SOURCES.md asked for it to be confirmed before the data is shown in the UI. Today it is only a model input.
- **Not done.** The pages were not rendered. The app was not run, because a long rehearsal was running and the brief asked for no heavy jobs.

## Not done / blocked

- **`docs/DEPLOYMENT.md` is missing** (OR-6.1). It is the only PROMPT_1 deliverable with no file. It is listed as missing in the README table.
- **Drafts.**
  - `docs/SETUP.md` section 5 still lists the API and web commands as "intended".
  - The docs/ARCHITECTURE.md topology shows the scheduler container, which is not built.
  - Both are marked draft and need their owners, because they are outside this brief.
- **Live-test change log.**
  - Under the pre-registration's commit relation and the final's change-log rule (D-115, D-116 A1), every commit after the frozen commit X needs an entry in docs/status/livetest-changelog-2026.md.
  - If X is earlier than this commit, the commit needs a row: paths `reports/MODEL_CARD.md`, `docs/LIMITATIONS.md`, `README.md` and `docs/status/phase6-docs.md`; reason "OR-6.2 documentation"; verification "documentation only, no effect on commitments".
  - If X is 2872d13, the same applies to the commits after it that are already on `main`.
  - This note does not edit the change log, because it is outside the brief.

## Decisions proposed

- None.

## Next

- Write OR-6.1: docs/DEPLOYMENT.md, docs/SETUP.md section 5, and a fresh-install record.
- Act on findings F-DOC-1 (Frontend) and F-DOC-2 (Backend).
- Revise the model card at the live-test verdict.
