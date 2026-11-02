# Agent Team Charter

One orchestrator (the main Claude Code session) coordinates seven specialist
roles. Specialists run as subagents with narrow briefs; each brief names the
documents to read, the files it may touch, the tests it must run, and the
status note it must write. The orchestrator reviews every diff, runs the
test suite, resolves conflicts, and keeps the decision log.

## Roles and write permissions

| Role | Scope | May edit | Must not edit |
| --- | --- | --- | --- |
| Orchestrator | plan, gates, decisions, integration, conflict resolution | anything, but prefers delegating | n/a |
| Data Engineer | ingestion, snapshot store, schemas, validation suite, refresh jobs | `ffml/data/`, `ffml/config.py`, `tests/data/`, `docs/DATA_DICTIONARY.md`, `scripts/refresh_*` | models, features, api, web |
| Feature Engineer | point-in-time feature builders and leakage tests | `ffml/features/`, `tests/features/`, `docs/FEATURES.md` | models, api, web |
| ML Engineer | baselines, stage models, uncertainty, simulation, scoring-agnostic stat lines, backtest harness | `ffml/models/`, `ffml/sim/`, `ffml/scoring/`, `ffml/eval/harness.py`, `tests/models/`, `tests/sim/` | `ffml/eval/audits/`, `reports/evaluator_*`, api, web |
| Independent Evaluator | backtests, calibration, leakage audits, holdout evaluation, release approval | `ffml/eval/audits/`, `ffml/eval/metrics.py`, `tests/eval/`, `reports/` | **any file under `ffml/models/`, `ffml/features/`, `ffml/sim/`**; may file findings instead |
| Backend Engineer | database, API, league import, scoring engine wiring, recommendation services | `api/`, `tests/api/`, `docker-compose.yml`, `Dockerfile.api` | models, features, web |
| Frontend Engineer | responsive UI, charts, explanations, empty and error states, Playwright tests | `web/`, `Dockerfile.web` | api internals, models |
| QA and Security Reviewer | tests, secrets scan, dependency audit, performance checks, auth and headers | `tests/`, `.github/workflows/`, `docs/SECURITY.md`; files findings for others | model logic, feature logic |

Enforcement: the orchestrator checks `git diff --stat` of each specialist
handoff against the allowed paths before merging. Evaluator commits that touch
forbidden paths are reverted and logged as a violation.

## Working protocol for every task

1. Read the brief and the referenced docs; restate the task list in the status
   note before starting.
2. Work in small commits on the current branch, each with a focused message.
3. Run the relevant tests and paste the summary output (counts, failures) in
   the status note. Never report a feature finished without having run it.
4. Hand off with a short status note at `docs/status/<phase>-<role>-<n>.md`
   using the template below.
5. Record any decision that affects other roles as a proposal in the status
   note; the orchestrator adopts it into `docs/DECISIONS.md` or rejects it.

## Status note template

```
# <Phase> / <Role> / <task id>
Date: YYYY-MM-DD
Scope: one or two sentences
Done:
- item with file paths
Evidence:
- command run and summary output (test counts, timings, row counts)
Not done / blocked:
- item and reason
Decisions proposed:
- D-xxx proposal text
Next:
- item
```

## Gate review

The orchestrator opens each gate with a checklist (PLAN.md), checks every item
against evidence in the status notes, runs the full test suite itself, and
writes `docs/status/gate-<n>.md` with PASS or FAIL. A FAIL lists the fixes
required or documents why the gate remains open and what is allowed to proceed.

## Conflict resolution

- Schema or interface disputes: the consumer role drafts the contract, the
  producer role implements it, the orchestrator arbitrates and logs the result.
- Evaluator findings against model code: the ML Engineer must respond in a
  status note with a fix or a rebuttal; the evaluator decides whether the
  finding is closed. Unresolved findings block the release gate.
- Scope creep: anything not in PLAN.md goes to the limitations and
  improvements list, not into the build.

## Decision log format (docs/DECISIONS.md)

`D-nnn | date | title | status (proposed, accepted, superseded) | context | decision | consequences`
