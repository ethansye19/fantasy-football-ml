# Phase 5 / Data Engineer / live-test machinery (DE-5.1 core, QA-5.1 core, OR-5.1)

Date: 2026-11-04

Scope: the machinery that saves and hashes the chain_v2 projections before
each week's first kickoff (D-098). It works under the D-099 limits and can be
pinned to whatever commit is pre-registered. No model was built, no task was
registered, and nothing was written to a predictions store.

Task list (from the brief):
1. Weekly runner `scripts/livetest_week.py` with these steps:
   - refresh;
   - `ffml livetest build-week` for the week whose first kickoff is next;
   - commit and push `commitment.json` (plus `player_summary.parquet` under
     5 MB) to the dedicated branch `livetest-2026` through a separate
     worktree, then record the push and GitHub's view of it;
   - re-hash the files.

   It must be idempotent, retry until K - 30 min, support a pinned worktree
   with its own venv, and log every run.
2. Windows Task Scheduler script with a dry-run mode, plus documentation of
   the laptop-off and missed-week behavior.
3. `scripts/alert.py`: webhook, local log, toast, a test command, and the
   five alert kinds.
4. `docs/RUNBOOK.md`.
5. Tests under `tests/data/`, ruff, and this note.

## Done

All paths are under the repository root.

**Weekly runner and its library**
- `scripts/livetest_week.py` is the runner, with commands `run`, `watchdog`,
  `check`, `plan`, `status`, `verify` and `init`.
- `scripts/livetest_core.py` holds the shared code.
- Target week and timing:
  - The target week is the first REG week whose first `dim_game.kickoff_utc`
    is strictly after now.
  - That kickoff is cross-checked with the live nflverse schedule, fetched in
    memory with nothing written; the earlier kickoff wins.
  - A week is **tentative** when all its games share one time, as week 18
    does now (all games 2027-01-10 18:00 UTC). For tentative weeks, all
    deadlines move 24 h earlier.
  - Deadlines relative to K:
    - act from K - 12 h;
    - no refresh starts after K - 3 h;
    - no refresh or build starts after K - 30 min;
    - no push starts after K - 5 min;
    - nothing is pushed for the week after K.
- Idempotency:
  - A week whose commitment is on the remote branch is left alone.
  - A built but unpushed commitment is pushed as is, never rebuilt.
  - A different commitment already on the branch is an integrity stop
    (exit 6).
- Commitments branch:
  - `livetest-2026` is an **orphan** branch, so pushing it can never carry
    `main` history.
  - It lives in the worktree `../ffml-livetest-commitments`, with
    `.gitattributes` set to `* -text` so bytes are committed exactly. System
    `core.autocrlf` is true on this machine.
  - The push refspec is `refs/heads/livetest-2026` only. `main`, `master`
    and `refs/...` are refused, and the runner never force-pushes.
- External record, in `data/livetest/runner/<week>/push_record.json`:
  - the push result and `git ls-remote`;
  - `gh api repos/{o}/{r}/commits/{sha}` and the branch ref;
  - the **repository activity API** entry. Its `timestamp` is GitHub's
    server time for the push, and it is used for `on_time`.
  - The clock skew against the local push time; over 120 s raises an alert.
- Verification:
  - Before the push: every listed file (`outputs` plus
    `calibration_history.sources`) is re-hashed, and the digest is
    recomputed with the same canonical JSON as
    `ffml.sim.livetest.commitment_digest`.
  - Also checked: season, week, `created_at` < K, the pinned commit, and
    `code.clean`.
  - After the push: local files are re-hashed and compared with the
    manifest, the pushed blobs are compared, the remote head must contain
    the commit, and `ffml livetest verify` runs as a second opinion.
- Missed weeks: after K, a missing commitment gets
  `<week>/MISSED.json` pushed to the branch, with the local state and any
  built-but-unpushed hash, plus an alert.
- Per-week state, a run lock, and a JSON-lines log per run under
  `data/livetest/logs/`.

**Refresh driver**: `scripts/refresh_data.py`, one step per process
(`ingest`, `sleeper`, `process`, `validate`, `features`).
- It runs with the pinned interpreter, so data are processed by frozen code.
- Ingest is a full 2016-S run (see finding 2).
- Schema pins are read from and updated in
  `data/livetest/schema_pins/expected_schemas.json`, so the code checkout is
  never modified (see finding 3).
- Sleeper is fetched at most once per day, never forced.
- The runner fingerprints every snapshot directory (file sizes, mtimes,
  manifest hash) before and after a refresh. Any change stops the run with
  exit 7. The public predictions store is fingerprinted around the build.

**Build stub**: `scripts/livetest_stub_build.py` follows the build-week
contract and the real format of `ffml.sim.livetest`: `outputs` with
`role`, `first_kickoff`, `code.commit`/`code.clean`, `digest.value`, and
exits 0/1/2/3/4. It is labeled STUB and "not approved" and writes only
under the commitment directory. The runner refuses to push a stub
commitment outside a rehearsal.

**Pinned worktree setup**: `scripts/livetest_setup_worktree.py`.
- `git worktree add --detach <path> <sha>`, then a Python 3.11 venv in
  `<path>/.venv`.
- `pip install -r <path>/requirements.lock.txt`, then
  `pip install --no-deps -e <path>`.
- Then the runner's pin check. It refuses unless all of these hold:
  - the path is a separate worktree;
  - HEAD equals the expected commit;
  - the tree is clean;
  - `ffml` is imported from inside the worktree, both from the worktree cwd
    and from a neutral cwd;
  - `pip freeze` equals the lock.
- Data, artifacts and reports are shared through `FFML_DATA_DIR`,
  `FFML_ARTIFACTS_DIR` and `FFML_REPORTS_DIR`. `Settings` supports them
  (pydantic `FFML_` prefix). Children get `FFML_HOLDOUT_UNLOCK`,
  `PYTHONPATH`, `PYTHONHOME` and `VIRTUAL_ENV` removed.

**Scheduler**: `scripts/livetest_schedule.ps1` is a dry run unless
`-Register`.
- Per week:
  - a `run` task at K - 9 h, repeating every 30 min until K - 30 min, with
    the trigger ending at K;
  - a `watchdog` at K - 1 h.
- A daily check task: stale predictions, schedule drift against the
  registered plan, MISSED markers.
- Settings: StartWhenAvailable, WakeToRun, battery allowed, IgnoreNew, a
  5 h limit.
- Logon modes:
  - Interactive;
  - Password (runs whether or not the user is logged on);
  - S4U, documented as unable to push.

**Alerts**: `scripts/alert.py`.
- Every alert goes to the local log `data/alerts/alerts.jsonl` first, then
  to the webhook (`FFML_ALERT_WEBHOOK`, fallback `ALERT_WEBHOOK_URL`). Slack,
  Discord, ntfy or generic JSON is picked automatically.
- Then a Windows toast through Windows PowerShell WinRT. No new
  dependencies.
- Repeats inside a cooldown are logged but not sent (the runner uses 2 h per
  week and kind).
- Commands: `test`, `send`, `check-stale --now` (clock override), `tail`.
- Kinds wired in the runner: `refresh_failure`, `data_quality`,
  `build_failure`, `push_failure`, `missed_commitment` (watchdog, deadline,
  marker, late push), `stale_predictions` (8 days, in season only; the
  public store only when a live approved model exists), `integrity` and
  `schedule_drift`.

**Runbook**: `docs/RUNBOOK.md` (OR-5.1). It covers:
- the limits;
- the components and files;
- one-time setup;
- the week cycle with examples;
- how to check that a push happened before kickoff;
- the pinned worktree;
- a failure table;
- the laptop-off matrix;
- the exit codes.

**Tests**
- `tests/data/test_livetest.py` (33 tests). Covers:
  - the week 12 Wednesday and week 18 Saturday kickoffs, triggers, next
    week, tentative weeks and merging;
  - end-to-end runs against a temporary bare remote: orphan branch, `main`
    never pushed, idempotency, built-but-unpushed, a different commitment;
  - deadlines: too early, cutoff, kickoff, push deadline;
  - MISSED marker, watchdog, build exit codes, stub refusal, rehearsal
    layout, refresh retry then stale build, snapshot modification;
  - post-push tamper, lock, safety refusals, dry run;
  - pin verification: wrong commit, dirty tree, main checkout, foreign
    `ffml`, ignored files allowed; requirements comparison;
  - manifest re-hash, digest and dirty code, snapshot fingerprints, branch
    guard;
  - a round trip of a commitment written by `ffml.sim.livetest.write_commitment`.
- `tests/data/test_alert.py` (10 tests). Covers:
  - webhook against a local HTTP server, plus the log;
  - no webhook, webhook failure, cooldown, toast hook;
  - payload formats, unknown kind, the CLI `test` command;
  - the stale check with a clock override, and the public store only with a
    live model.
- Fixture: `tests/data/fixtures/livetest_dim_game_2026.csv`. Weeks 8-17 are
  real 2026 kickoffs from `dim_game`. Week 18 is synthetic, with two
  Saturday games, because its real times are not set yet.

## Evidence

- `PYTHONUTF8=1 FFML_OFFLINE=1 .venv/Scripts/python -m pytest tests/data -q`
  ran after the last code change: **170 passed, 2 skipped** (both network
  tests) in 114 s. The total includes the 43 new tests.
- `ruff check scripts/ tests/data/ ffml/data/ ffml/config.py`: All checks
  passed. CI runs `ruff check .` only, and the repository is not
  `ruff format` clean, so format was not enforced.
- **Schedule dry run** (`scripts\livetest_schedule.ps1 -Weeks 10-18 -Season 2026`):
  - 9 run tasks, 9 watchdogs and 1 daily check;
  - the first entry is `\ffml\livetest-2026w10-run`, 2026-11-12 11:15
    local (16:15Z), every 30 min until 19:45, first kickoff 20:15 local
    (2026-11-13T01:15Z);
  - week 12 runs from 2026-11-25 16:00Z, for K = 2026-11-26 01:00Z
    (Wednesday 20:00 ET);
  - week 18 is tentative and runs from 2027-01-09 09:00Z;
  - `Get-ScheduledTask -TaskPath \ffml\` returns 0 tasks.
- **Plan against the real `dim_game`**: matches the dry run. The live
  schedule check fetched 2026 in about 2 s with no differences.
- **Alert test** (`scripts/alert.py test`): toast "shown"; webhook "not
  configured" (no URL on this machine); logged. `check-stale`: not stale.
- **Refresh driver**: run against a scratch `FFML_DATA_DIR`, so the real
  store was not touched. `--step ingest --datasets schedules,teams
  --seasons 2025-2026` gave ok in 2.8 s and seeded the pin copy;
  `ffml/data/schemas/` was unchanged in git.
- **Pinned worktree, real run**:
  - `livetest_setup_worktree.py --commit HEAD` (e43e349) into the
    scratchpad took 8 min 15 s.
  - Pin check: `env_checked: true` (pip freeze equals the lock); `ffml`
    imported from the worktree in both modes. One warning: the commit was
    not on a remote branch yet.
  - The runner's pinned `--dry-run` verified the pin.
  - The worktree was then removed (the frozen commit is not chosen).
    `git worktree remove` failed with "Filename too long" under `%TEMP%`, so
    the runbook now says to keep the short default path.
- **Rehearsal push**, the one permitted test commit:
  - stub build from the pinned worktree, isolated scratch data directory,
    `--skip-refresh`;
  - commit **9e7d5dc** on `origin/livetest-2026`: one commit, no parents,
    message "REHEARSAL livetest 2026w10: stub commitment, not part of the
    test";
  - files: `README.md`, `.gitattributes`,
    `rehearsal/2026w10-20261104T233650Z/{commitment.json, player_summary.parquet}`;
  - GitHub activity: `branch_creation` at 2026-11-04T23:36:53Z, clock skew
    0.0 s;
  - post-push verification ok, digest match;
  - a re-run logged "already pushed" with no new commit, and `status` shows
    pushed True.
  - Logs are copied to `data/livetest/logs/rehearsal_stub_20261104/`.

## Not done / blocked

- **The real refresh was not run against the shared data store.** Other
  agents are reading the current `LATEST` snapshots right now (`dim_game`
  moved at 23:30Z during this session), and memory is limited. The first
  real refresh should be the orchestrator's rehearsal with the real build.
- **`ffml livetest build-week` was not run through the runner.** It is
  heavy, and it is ML Engineer code. The orchestrator's rehearsal command is
  in RUNBOOK section 3, step 4.
- No tasks were registered and no webhook URL was configured, per the brief.
  The webhook was tested only against a local server.
- `init` was not run, so `data/livetest/config.json` does not exist. It
  records the test window and pin after the design is signed off. Until
  then, real (non-rehearsal) runs refuse with exit 4.
- DE-5.2 (APScheduler worker) and DE-5.3 (GitHub Actions refresh workflows)
  are out of this brief.

## Findings for other roles

1. **`ffml refresh` is broken.** `ffml/cli.py` imports `ffml.refresh`, which
   does not exist, so the command raises `ModuleNotFoundError`. The live
   test uses `scripts/refresh_data.py`.
   - Proposal (orchestrator): add a thin `ffml/refresh.py` that calls the
     same steps, or point the command at `ffml.data.refresh` (Data Engineer
     path) in a later task.
2. **D-031 does not hold as written.** `ffml ingest --seasons 2026` writes a
   snapshot with only the 2026 partition and moves `LATEST` to it. After
   that, `process` and `features` would see no history.
   - The live test always ingests 2016-S.
   - Proposal: amend D-031, or make ingest carry the older partitions
     forward.
3. **`ffml ingest` rewrites the tracked file
   `ffml/data/schemas/expected_schemas.json`** when columns are added. In a
   pinned worktree, that would make the next pin check fail. The refresh
   driver therefore passes `pin_path` to a copy under
   `data/livetest/schema_pins/`.
4. **ML Engineer: paths are resolved against the code checkout.** Registry
   entries and `ffml.sim.livetest.rel()` paths are relative to
   `PROJECT_ROOT`, and in a pinned run that is the worktree.
   - Files under the shared data and artifacts directories are therefore
     written as absolute paths. That is fine.
   - Relative entries made from the main checkout (for example chain_v1)
     resolve inside the worktree, where they do not exist.
   - build-week registers its own candidate from the worktree, so this
     should not bite. Worth a test with `FFML_ARTIFACTS_DIR` outside the
     checkout.
5. **The rehearsal sandbox is shared.** A real rehearsal writes
   `data/livetest/rehearsal/<week>/commitment.json` and overwrites any
   earlier one. The runner pushes whatever commitment is there under
   `rehearsal/<week>-<created stamp>/`. My stub rehearsal used a separate
   data directory to avoid colliding with the ML Engineer's.
6. **`.env.example` names `ALERT_WEBHOOK_URL`, but the brief names
   `FFML_ALERT_WEBHOOK`.** `alert.py` reads both. Proposal (orchestrator
   file): rename it in `.env.example`.

## Decisions proposed

- **D-101: commitments branch.** Commitments go to the orphan branch
  `livetest-2026`, from the worktree `../ffml-livetest-commitments`.
  - Only that ref is pushed, never by force.
  - Each week keeps `commitment.json` plus `player_summary.parquet` when it
    is under 5 MB.
  - The external timestamp is GitHub's repository activity entry for the
    push. Commit dates are client-set and are not evidence.
- **D-102: live-test refresh.** The refresh is a full ingest of 2016-S, the
  Sleeper map once per day, process, validate and features, all run with
  the pinned interpreter.
  - Schema pins come from the copy in `data/livetest/schema_pins/`.
  - No snapshot is pruned while the test runs. The commitments reference
    the snapshot ids, and disk grows by about 0.6 GB a week.
- **D-103: deadlines.**
  - Act from K - 12 h. No refresh starts after K - 3 h. No build starts
    after K - 30 min. No push starts after K - 5 min. Nothing is pushed for
    a week after K.
  - Tentative weeks are scheduled 24 h early.
  - Rehearsals apply no deadlines, as build-week allows.
- **D-104: refresh failure (needs the owner's call).** The default: after
  3 failed attempts or K - 3 h, build on the newest existing snapshots, with
  an alert. The alternative is to skip the week. The commitment records the
  snapshot ids either way.
- **D-105: missed weeks are recorded, never repaired.** After K, a week
  without a pushed commitment gets `MISSED.json` on the branch, and nothing
  else is ever pushed for it. How it counts is left to the test design.

## CI needs (proposal for QA; `.github/` not edited)

- No change is needed for the new tests. They need `git` on the runner, and
  `ubuntu-latest` has it. They need no network, no `gh`, no PowerShell.
- **Proposed `livetest_watchdog.yml`**: a cron workflow on `main` that does
  not depend on the laptop.
  - It reads the week plan from a small committed JSON, written by
    `livetest_week.py plan --json` at pre-registration.
  - At about K - 45 min it checks `livetest-2026` for
    `<week>/commitment.json`.
  - If the file is missing, it opens an issue or posts to a webhook secret.
  - Caveat: GitHub cron can start 15-60 min late at busy times, so schedule
    it early enough.
- **Branch protection**: blocking force-pushes and deletion of
  `livetest-2026` needs a paid plan for private repositories. Without it,
  the activity API still records force pushes (`activity_type: force_push`),
  so tampering is visible but not prevented.

## Open questions for the test design

1. **Missed weeks.** One option is to count a missed week as a failure for
   every rule. Another is to exclude it and extend the window (for example
   into the postseason). A third is to cap the number of missed weeks.
   - The machinery makes a miss unambiguous: an alert at K - 1 h, a MISSED
     marker, and no commitment with a pre-kickoff GitHub timestamp.
2. **Laptop-off risk.** This laptop is a single point of failure. A run
   needs the machine on (or asleep with wake timers allowed), logged on (or
   `-LogonMode Password`), and online during roughly K - 9 h to K - 5 min.
   On Thursday weeks that window is 11:15 to 20:10 Eastern.
   - Mitigations: Password logon, AC power, wake timers enabled, and the
     watchdog plus the GitHub Actions watchdog above.
   - Decide whether the owner will run the runner by hand on a
     watchdog alert.
3. **GitHub Actions as an alternative or fallback.**
   - A watchdog on Actions is cheap and recommended.
   - A full build on Actions would need the stored backtest runs and
     artifacts that build-week reads, which are not in git. It would also
     run on Linux, where LightGBM and NumPy results may differ slightly from
     Windows. The pre-registration would have to name the platform, or name
     Actions as an allowed fallback.
4. **Stale data** (D-104): build on older snapshots, or skip the week?
5. **Rehearsals.** They currently go to the same branch under `rehearsal/`.
   Should they use a separate branch instead?
6. **A second, independent timestamp.** Optional: an RFC 3161 timestamp
   authority or OpenTimestamps receipt of the digest, in addition to
   GitHub's.

## Commands

The setup command runs after the commit is pre-registered:

```powershell
$env:PYTHONUTF8 = "1"
.venv\Scripts\python scripts\livetest_setup_worktree.py --commit <sha>
.venv\Scripts\python scripts\livetest_week.py init --season 2026 --weeks 10-18 `
  --code-worktree ..\ffml-livetest-code --python ..\ffml-livetest-code\.venv\Scripts\python.exe --expected-commit <sha>
```

Schedule, as a dry run first. `-Register` is for the orchestrator after
sign-off.

```powershell
powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -Weeks 10-18 -Season 2026
powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -Register -LogonMode Password
```

Manual run and checks:

```powershell
.venv\Scripts\python scripts\livetest_week.py run --season 2026 --week 10 --dry-run
.venv\Scripts\python scripts\livetest_week.py run --season 2026 --week 10
.venv\Scripts\python scripts\livetest_week.py run --rehearsal --season 2026 --week 10 --skip-refresh   # real build-week rehearsal
.venv\Scripts\python scripts\livetest_week.py status
.venv\Scripts\python scripts\livetest_week.py verify --season 2026 --week 10
.venv\Scripts\python scripts\alert.py test
```

## Commits

On `main`, not pushed:

| Commit | Change |
| --- | --- |
| 097753a | alerts |
| e43e349 | runner, core, stub, refresh driver, tests |
| 3a7f9d8 | tentative fix and format round-trip test |
| 1c14b6c | setup and schedule scripts |

On `origin/livetest-2026`: 9e7d5dc, the single rehearsal commit (stub).

The runbook, the schedule-script fix and this note are in the final commit
of this hand-off.

## Next

- Orchestrator, after design sign-off:
  1. pre-register the commit;
  2. `setup_worktree`;
  3. `init`;
  4. real `run --rehearsal` with build-week;
  5. schedule dry run, then `-Register`;
  6. `alert.py test` with a real webhook.
- QA: the watchdog workflow and the `.env.example` rename.
