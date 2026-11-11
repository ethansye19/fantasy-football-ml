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

**Refresh driver**: `ffml/data/refresh.py` (DE-5.4; at first
`scripts/refresh_data.py`, now a thin wrapper), one step per process
(`ingest`, `sleeper`, `process`, `validate`, `features`).
- The runner calls `python -m ffml.data.refresh` with the pinned
  interpreter, so data are processed by frozen code.
- Ingest is a full 2016-S run (see finding 2).
- Schema pins are read from and updated in the working copy
  `data/schema_pins/expected_schemas.json`, so the code checkout is never
  modified (see finding 3 and the DE-5.4 section).
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
   - Fixed in DE-5.4: `ffml/data/refresh.py`, and the one-line import
     change in `ffml/cli.py`.
2. **D-031 does not hold as written.** `ffml ingest --seasons 2026` writes a
   snapshot with only the 2026 partition and moves `LATEST` to it. After
   that, `process` and `features` would see no history.
   - The live test always ingests 2016-S.
   - Proposal: amend D-031, or make ingest carry the older partitions
     forward.
3. **`ffml ingest` rewrites the tracked file
   `ffml/data/schemas/expected_schemas.json`** when columns are added. In a
   pinned worktree, that would make the next pin check fail. Fixed in
   DE-5.4: the default is the working copy
   `data/schema_pins/expected_schemas.json`; the tracked file changes only
   with `update_schema_pin=True` or `FFML_UPDATE_SCHEMA_PIN=1`.
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
   `FFML_ALERT_WEBHOOK`.** `alert.py` reads both. Renamed in DE-5.4 at the
   orchestrator's request; `ALERT_WEBHOOK_URL` stays as a documented alias.

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
  - Schema pins come from the working copy `data/schema_pins/`.
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
- QA: the watchdog workflow.

## DE-5.4 follow-ups (2026-11-04)

Done at the orchestrator's request, after the review of the runner.

1. **`ffml refresh` works.** New `ffml/data/refresh.py` holds the step
   implementations (`ingest`, `sleeper`, `process`, `validate`, `features`);
   `scripts/refresh_data.py` is a thin wrapper, so there is one
   implementation. `ffml refresh --step <name>` runs one step in process.
   `all` (alias `data`) runs the five steps, one child process each
   (`python -m ffml.data.refresh --step ...`), and stops at the first
   failure. `injuries` is an ingest of injuries, depth charts and weekly
   rosters, then process. `retrain-check` and `predict` are refused with a
   message (D-099, D-100). The live-test runner now calls
   `python -m ffml.data.refresh` with the pinned interpreter, so the refresh
   code is pinned together with the model code. The one authorized edit in
   `ffml/cli.py` is the import, `from ffml.data.refresh import run_refresh`.
   The help string still lists the old step names; a test checks that every
   one of them either resolves or is refused clearly.
2. **Schema pins no longer dirty a pinned worktree.** `resolve_pin_path` in
   `ffml/data/ingest.py`: an explicit `pin_path` wins; otherwise the working
   copy `Settings.schema_pin_path`, which is
   `<data_dir>/schema_pins/expected_schemas.json`, seeded from the tracked
   file and topped up with any dataset the tracked file pins that the copy
   lacks. The tracked `ffml/data/schemas/expected_schemas.json` is written
   only with `run_ingest(update_schema_pin=True)` or
   `FFML_UPDATE_SCHEMA_PIN=1`, and is then mirrored into the working copy.
   `ffml validate` reads the same copy. Not done: an `--update-schema-pin`
   option on `ffml ingest` itself needs a small change to the `ingest`
   command in `ffml/cli.py`, outside the authorized edit; the patch is in
   the hand-off, and the environment variable works without it.
3. **D-031 correction** (for the decision log). D-031 said the nightly
   refresh calls `ffml ingest --seasons <current>` for the mutable datasets
   and the full 2016-2026 run only after Thursday's stat corrections. That
   does not work with the snapshot store as built: a raw snapshot holds only
   the seasons it was asked for, and `LATEST` points at one snapshot, so a
   `--seasons 2026` ingest moves `LATEST` to a 2026-only snapshot, and
   `process` and `features` then see no 2016-2025 history. The rule the live
   test and `ffml refresh` use: every ingest requests all seasons from
   `Settings.first_season` (2016) through the current season, which is the
   later of the schedule snapshot's season and the calendar season (a year
   counts from September, when nflverse starts publishing it; asking earlier
   fails as a gap under D-028). The ingest loads one season at a time, so
   memory is bounded. A full run costs about 80 s and 207 MB per snapshot
   set, and no snapshot is pruned during the live test (about 0.6 GB a
   week). A per-season carry-forward in the store, copying unchanged
   partitions into the new snapshot, would restore the cheap nightly run; it
   is a follow-up, not a prerequisite.
4. **`.env.example`**: `FFML_ALERT_WEBHOOK=` replaces `ALERT_WEBHOOK_URL=`.
   The old name is kept as a documented alias, since `alert.py` reads both,
   and `FFML_UPDATE_SCHEMA_PIN` is documented there as well.

Evidence:
- `PYTHONUTF8=1 FFML_OFFLINE=1 .venv/Scripts/python -m pytest tests/data -q`:
  **177 passed, 2 skipped** (network) in 157 s, with 7 new tests. In
  `tests/data/test_refresh.py`: CLI help, every step name, an in-process
  ingest using the working pin copy while the tracked file's hash is
  unchanged, `all` running one process per step and stopping at a failure,
  default seasons, and the `python -m ffml.data.refresh` result line. In
  `tests/data/test_ingest.py`: the new pin default and the explicit re-pin.
  The late-schema-change tests are unchanged and pass.
- `ruff check ffml/data/ ffml/config.py ffml/cli.py scripts/ tests/data/`:
  All checks passed.
- From the installed CLI: `ffml refresh --help` exits 0;
  `ffml refresh --step predict` raised `RefreshError` at the time; since
  DE-5.5 it exits 1 with `refresh failed: step 'predict' is refused: ...`.

## DE-5.5: operating rules D-106 and D-107 (2026-11-05)

Done, per the owner's rules (D-106, D-107) and the orchestrator's brief.

1. **24-hour start.** `Timing`: the window opens at K - 25 h, the scheduled
   run starts at K - 24 h 15 min (at least 24 h before the official first
   kickoff even with Task Scheduler's start delay) and repeats every 30 min
   until K - 5 min; the build cutoff K - 30 min and the push deadline
   K - 5 min are unchanged. Week 12: the run starts 2026-11-25 00:45Z for
   K = 2026-11-26 01:00Z. Tests updated (triggers, too early at K - 26 h,
   runs at K - 24 h 15 min).
2. **One-miss rule.** `MISSED.json` (format `ffml.livetest.missed/2`)
   carries `reason.category` (`technical_failure` with the last failed step
   and message, or `machine_offline` when no runner log exists between
   K - 25 h and K), `run_logs` (window, files, last 12 events of the latest
   log), `local_commitment`, `late_commitment`, `miss_number`,
   `earlier_misses` and `test_status` (VALID: excluded, window not extended;
   INVALID on the second miss). A commitment built before K but not pushed
   in time is pushed under `<week>/late/` with the marker, so projections
   that exist are never withheld. `STATUS.md` on the branch is regenerated
   at every push (commitments and misses) with the per-week state, the miss
   count and the test state; the branch README explains the layout and the
   rule (`README.md` and `STATUS.md` are the only rewritable files on the
   branch). `status` prints `missed`, the reason, `late` and a footer
   `misses: N (...); allowed 1; test state: VALID|INVALID`. The retry after
   a push failure still pushes the existing commitment as is (tested).
3. **GitHub watchdog.** `.github/workflows/livetest_watchdog.yml` runs
   `scripts/livetest_watchdog_gh.py` hourly on `main` (standard library
   only). It reads the tracked `scripts/livetest_schedule_2026.json`
   (exported from dim_game with `plan --export`; week 18 flagged
   tentative), takes the next test week and, inside [6.5 h, 5.5 h) and
   [1.5 h, 0) before kickoff, checks `livetest-2026` for
   `<week>/commitment.json` through the contents API. Missing: exit 1
   (workflow failure, GitHub mails the owner) plus the webhook when the
   `FFML_ALERT_WEBHOOK` secret exists (through `scripts/alert.py`, no
   toast). `workflow_dispatch` takes `branch`, `now` and `force_window` for
   the dry run.
4. **Least privilege.** `scripts/livetest_schedule.ps1`: default
   `-LogonMode Password`; `-RunLevel Limited` in both registration paths;
   the password is prompted by `Get-Credential`, handed to
   `Register-ScheduledTask` and cleared, written to no file, script or log
   (the run log records only the command line and the configuration);
   settings `StartWhenAvailable`, `WakeToRun` (unless `-NoWake`), start only
   on AC power (no `-AllowStartIfOnBatteries`; a running job is not stopped
   on unplugging), `IgnoreNew`, 5 h limit. New read-only
   `scripts/livetest_power_check.ps1`, and RUNBOOK section 12 with the exact
   `powercfg` commands. Readings on this laptop (nothing changed): Balanced;
   wake timers Enable on AC and Disable on battery; sleep after 60 min;
   hibernate never on AC; Modern Standby; Fast Startup on, so a "Shut down"
   hibernates and wake timers do not fire; the lid setting is not exposed
   by `powercfg` here.
5. **Logged-off dry run.** `--dry-run-branch <name>` on `run`, `watchdog`,
   `status` and `verify`: refuses `livetest-2026`, `main` and any name
   starting with `livetest-2026`; pushes to that branch from its own
   worktree `../ffml-<branch>-commitments`; builds in the rehearsal sandbox
   with no deadlines; the branch layout mirrors the real one so both
   watchdogs find the file; dry-run branches are not immutable (a later dry
   run replaces the commitment). `--force-fail refresh|build|push` (dry run
   only) fails that step on purpose and alerts; the retry pushes the built
   commitment as is. `livetest_schedule.ps1 -DryRunBranch ... [-ForceFail
   push] [-DryRunStub] [-DryRunSkipRefresh] [-DryRunAt]` defines the two
   dry-run tasks. Procedure: RUNBOOK section 13.
6. **Registration command**, every parameter explained: RUNBOOK section 14.
7. **CLI** (authorized edits): `ffml ingest --update-schema-pin`;
   `ffml refresh` catches `RefreshError` and exits 1 with a message. CI fix:
   `test_cli_help_resolves` invokes Typer with `NO_COLOR=1`, `TERM=dumb` and
   `COLUMNS=200` and strips ANSI escapes; new tests cover `--step nonsense`
   (exit 1, "unknown step") and `--step predict` (exit 1, "refused", no
   traceback).

Evidence:
- `PYTHONUTF8=1 FFML_OFFLINE=1 .venv/Scripts/python -m pytest -q tests/data`:
  **189 passed, 2 skipped** (network) in 223 s, with 12 new tests. In
  `test_livetest.py`: the `machine_offline` reason, the technical-failure
  reason with its log excerpt, the late commitment under `late/`, the second
  miss INVALID, the status footer, the plan export, and the dry-run branch
  with forced push and build failures. In `test_watchdog_gh.py`: the windows
  around the Thanksgiving kickoff, `decide`, `main` with a fake contents API
  and the alert dispatch, and the local alert log. In `test_refresh.py`:
  refused and unknown steps exit 1, and `--update-schema-pin`.
- `ruff check scripts/ tests/data/ ffml/data/ ffml/config.py ffml/cli.py`:
  All checks passed.
- `scripts/livetest_schedule.ps1 -Weeks 10-18 -Season 2026` (dry run): the
  week 10 run task at 2026-11-11 20:00 local (2026-11-12T01:00Z), every
  30 min until 2026-11-12 20:10 local; `-DryRunBranch livetest-dryrun
  -ForceFail push -DryRunStub` prints the two dry-run tasks;
  `-DryRunBranch livetest-2026` throws. `Get-ScheduledTask -TaskPath \ffml\`
  returns 0: nothing registered.
- `plan --export scripts/livetest_schedule_2026.json --weeks 10-18 --season 2026`
  wrote 9 weeks from dim_game snapshot 20261104T233034Z with the live
  schedule checked.
- `scripts/livetest_watchdog_gh.py --repo ethansye19/fantasy-football-ml`
  against the real branch (read only; token from `gh auth token`; alert log
  pointed at a scratch file): `--now 2026-11-12T19:00:00Z` exit 1 ("NO
  commitment on the branch 6.2 h before the first kickoff", window 6h);
  `--now 2026-11-10T12:00:00Z` exit 0 ("61.2 h to kickoff; outside the
  alert windows"); `--branch livetest-dryrun --force-window` exit 1 with the
  alert logged (webhook not configured).
- `livetest_week.py status --season 2026` against the real branch: every
  week `pushed False`; footer `misses: 0 (none); allowed 1; test state:
  VALID`.
- `scripts/livetest_power_check.ps1` runs read only (readings above).

Not done:
- No task was registered, no dry run was executed logged off and no secret
  was set: the owner does these (RUNBOOK sections 11 to 14). The workflow
  runs only once it is on `main` (orchestrator push).
- The GitHub watchdog's webhook path is tested with the alert module against
  a local server (`test_alert.py`) and the dispatch wiring with a fake, not
  against a real webhook URL.

Proposed for the decision log, if wanted: D-108, the timing constants
(window K - 25 h, start K - 24 h 15 min, repetition until K - 5 min, cutoffs
unchanged) and the GitHub watchdog windows [6.5, 5.5) and [1.5, 0) hours
before kickoff.

(Adopted as D-109.)

## DE-5.6: D-111 miss count, sealed hashes, schedule export, QA-F25, scoring settings (2026-11-05)

Done:
1. **Allowed misses from the pre-registration.** The runner no longer has a
   constant: `missed_weeks.allowed` is read from the pre-registration (the
   fenced JSON block of `docs/status/livetest-preregistration-2026.md`, found
   by default, or `--preregistration <.json|.md>`; `init` records the path and
   the count). The real document gives 2. `MISSED.json` gains `allowed`
   (`miss_number` existed); `test_status` reads `VALID: miss N of 2 allowed`
   for misses 1 and 2 and `INVALID: miss 3 of 2 allowed` for the third.
   `STATUS.md`, the branch README ("up to 2 missed weeks") and the `status`
   footer (`allowed 2`) carry the count. A real run, `check` or `seal` that
   would write any of these refuses (exit 4) when the pre-registration cannot
   be read; `status` then shows the state as unknown.
2. **Sealed hashes.** `livetest_week.py seal --season S --week W` reads
   `data/livetest/scoring/sealed/<week>.sha256` (sha256sum lines, a JSON
   object or bare hashes), re-hashes any sealed part present locally, and
   commits and pushes `<week>/sealed_hashes.json` (the file's SHA-256, its
   entries and raw text) to `livetest-2026` with a regenerated `STATUS.md`
   (new `sealed` column). Idempotent (the same file is never committed
   twice); immutable (different local hashes are refused, exit 6, integrity
   alert); the push and GitHub's view go to `seal_record.json`.
   `seal-check` compares the branch copy with the local sealed file and
   parts (exit 0 match, 1 not sealed yet, 6 difference). `score-week` is the
   Tuesday job: oldest test week that kicked off with a commitment, not
   missed and not sealed; refresh; every game of the week final in
   `dim_game` (else exit 1 without an alert); the Evaluator's scorer
   (`python -m ffml.eval.livetest_score --week <week>`) from the pinned
   worktree when no sealed file exists; then `seal`. New alert kind
   `scoring_failure`. The runner never reads a sealed part beyond hashing it.
   Scheduler: `\ffml\livetest-weekly-score`, Tuesdays at `-ScoreTime` (15:30
   local), every 2 h for 12 h (dry run shows it; nothing registered).
3. **Schedule export.** `plan --export` now leaves the tracked file alone
   when the season, test weeks and kickoffs are unchanged. Re-run against
   the latest `dim_game` snapshot (still 20261104T233034Z: no refresh has run
   since, so week 9's finals are not in it yet; the file holds only weeks
   10-18, whose kickoffs do not depend on week 9's status): **unchanged**,
   week 18 still tentative. Re-export after the next refresh if the NFL sets
   the week 18 times.
4. **QA-F25** (RUNBOOK section 11): GitHub e-mails a failed scheduled run
   only to the user who last modified the cron line, and documents a 60-day
   inactivity disable for public repositories; the runbook says what to do
   about both (notification settings, re-owning the cron line, the webhook
   secret as a second channel, a monthly `gh workflow list --all` and
   `gh workflow enable`). This corrects my DE-5.5 sentence ("after 60 days
   without a push"). The comment in `.github/workflows/livetest_watchdog.yml`
   carries the same imprecision; I left the workflow file alone (editing it
   was authorized only in DE-5.5) and it is a one-line fix for whoever edits
   it next.
5. **Backend finding:** `ffml.data.sleeper.normalize_scoring_settings` now
   refuses non-finite values: NaN (as before, also the string `"nan"`),
   infinities (`inf`, `-inf`, `"inf"`, `"-Infinity"`, `"1e999"`) and ints
   beyond the float range (`10**400`, which raised `OverflowError` before),
   all as `ValueError` naming the key; large finite values pass.

Evidence:
- `PYTHONUTF8=1 FFML_OFFLINE=1 .venv/Scripts/python -m pytest -q tests/data`:
  **200 passed, 2 skipped** (network) in 173 s; the 4 tests touched by the
  last change (the `check` refusal) were re-run after it: 4 passed.
- `ruff check scripts/ tests/data/ ffml/data/ ffml/config.py ffml/cli.py`:
  All checks passed.
- `livetest_week.py status --season 2026 --json` on the real branch:
  `allowed: 2`, `test_state: VALID`, pre-registration path resolved.
- `livetest_schedule.ps1 -Weeks 10-18 -Season 2026` (dry run) lists
  `\ffml\livetest-weekly-score`; `Get-ScheduledTask -TaskPath \ffml\` = 0.

## DE-5.7: QA-F30, the miss count spans the whole window (2026-11-05)

The runner counted misses only in the configured season, so after the 2027
re-init (`init --season 2027 --weeks 1-9 --force`) a third miss read "VALID:
miss 1 of 2". The window is now read from the pre-registration's `window`
(2026 w10-18, 2027 w1-9; `core.window_from`), and the miss number, the earlier
misses, `STATUS.md` (one row per window week, "not scheduled yet" for 2027
weeks before their schedule exists, header "Window 2026w10 to 2027w09
(18 weeks)") and the `status` footer count across both seasons. The weeks
`record_misses` marks are the due window weeks of both seasons, so a 2026w18
miss is still marked by the first run after the re-init. Without a `window`
the runner falls back to the configured season's weeks. Tests: QA's world D3
(2026w12, 2026w16, 2027w03 missed: miss 3, INVALID, footer and STATUS.md),
and a 2026w18 miss marked after the 2027 re-init.

## DE-5.8: the runner side of D-116 (A1, A3, A4, A7) (2026-11-06)

1. **Pre-build check (A1).** Before every `build-week` (real runs, rehearsals
   and dry-run branches; not `--dry-run`, which builds nothing), the runner
   runs `<pinned python> -m ffml.eval.livetest_score --prebuild-check --from
   <code commit of the latest commitment on the branch, by created_at, among
   <week>/commitment.json and <week>/late/commitment.json; "" before the
   first build> --to <pinned HEAD>` with cwd the pinned worktree. When the
   remote cannot be read, the local commitments stand in (logged). Exit 0
   builds. Exit 5 blocks: the check's JSON is logged, a `prebuild_blocked`
   alert (critical) carries the uncovered and frozen commits, the run exits 2
   and the normal 30-minute repetition retries. Any other exit (the check
   cannot run, for example before the Evaluator's flag exists in the pinned
   code) also blocks, with "could not run" in the alert. A block fixed before
   the deadline is not a miss; at the deadline the existing rule applies and
   `MISSED.json` carries `technical_failure` with detail exactly "pre-build
   check blocked" (plus `reason.check`). `--force-fail prebuild` (dry-run
   branches only; `-ForceFail prebuild` in the scheduler) blocks without
   calling the scorer.
2. **Friday scoring (A3).** `score-week` asks `--data-cutoff <week>` for the
   cutoff (JSON with `data_cutoff_utc`, also accepted: `data_cutoff`,
   `cutoff_utc`, `cutoff`, or a bare ISO line), does nothing before it (exit
   0), and at or after it refreshes the data unless a `dim_game` snapshot
   taken at or after the cutoff exists. Scorer exit 4 (`--data-cutoff` or
   `--week`) is "not yet": exit 1, retried, no alert. `seal_record.json`
   gains `data_cutoff_utc` and `scorer` (the snapshot ids and any
   `outcome_rows_sha256` from `data/livetest/scoring/weeks/<week>.json`, with
   that file's SHA-256). The task `\ffml\livetest-weekly-score` runs Fridays
   from 13:00 UTC (a UTC trigger, so daylight saving does not move it),
   every 2 h for 12 h; `-ScoreTimeUtc` replaces `-ScoreTime`.
3. **Priority and timeout (A7).** The weekly scorer is launched with
   `creationflags=ABOVE_NORMAL_PRIORITY_CLASS` (0x00008000) on Windows,
   nothing elsewhere, and a 360-minute timeout.
4. **Final fetch (A4).** RUNBOOK section 15 now states that `--final` exit 75
   means a failed GitHub fetch, no verdict recorded, retry the final.

Interface the runner expects from the Evaluator's CLI (honoured by
`scripts/livetest_stub_score.py`, used with `--stub-score` in tests and dry
runs): `--prebuild-check --from A --to B` prints a JSON object with
`uncovered` and `frozen` lists and exits 0 or 5; `--data-cutoff <week>`
prints a JSON object with `data_cutoff_utc` (ISO, UTC) and exits 0 (4 = not
yet); `--week <week> --weekly-sensitivity` exits 0 (4 = not yet), writes
`data/livetest/scoring/sealed/<week>.sha256` and
`data/livetest/scoring/weeks/<week>.json` with `dim_game_snapshot` and
`processed_snapshots`.

## DE-5.9: before the freeze (D-120, D-121; the Evaluator's F1, R1, R2, R3) (2026-11-07)

1. **Pin at Y (F1, D-120).** `livetest_setup_worktree.py --commit <Y>` and
   `init --expected-commit <Y>`, where Y is the append-only change-log commit
   after the fill commit. After the existing pin check, the runner (and the
   setup script) verify, once the pre-registration's `commit` field holds X,
   that Y is X or a descendant of X and that `git diff --name-only X Y` touches
   only allowed paths (`core.verify_descends_from_frozen`, the scorer's path
   policy reimplemented in `core.path_allowed` and tested equal to it); before
   the fill the check is skipped and logged. RUNBOOK section 3 states that the
   final always runs from the main checkout.
2. **Refresh before scoring (R1).** `score-week` refreshes when any dataset the
   scorer pins (`dim_game`, `fact_player_game`, `fact_dst_game`,
   `restricted/fact_ecr_weekly`, tested equal to the scorer's
   `PINNED_DATASETS`) lacks a snapshot taken at or after the pin point; at most
   two refreshes per week and pin point, so a straddling refresh is repeated on
   the next 2-hour repetition instead of delaying the week.
3. **A game that never becomes final (D-121).** `score-week` tries every due,
   unsealed week in order in one run; a waiting week (not final, or scorer
   exit 4) never holds a later one. `exclusion_after_utc` is read from
   `--data-cutoff` (stubbed as cutoff + 14 days until the Evaluator's lands),
   logged, and after it a week with a game still not final is passed to the
   scorer, which excludes the game; `seal_record.json` records
   `exclusion_after_utc` and `not_final_at_scoring`.
4. **2027 re-initialisation (R3):** RUNBOOK section 16.
5. **Change-log helper:** `scripts/livetest_changelog.py` (RUNBOOK section 17).
   It lists the commits in X..HEAD without a row and with `--append` adds rows
   only for commits whose every path is outside the pinned build; it refuses
   `ffml/`, `scripts/`, `pyproject.toml`, `requirements.lock.txt`, merges, and
   paths the pre-registration freezes or does not allow; it never edits a row;
   `--commit` commits the change log alone and checks the commit is
   append-only (exempt). Its parser and exemption rule are tested equal to the
   scorer's `changelog_commits` and `_changelog_append_only`.

**Finding (2027 week 1, needs a later fix in `ffml/data/`).** `seasons_to_load`
has no cap, so an ingest of 2016-2027 before nflverse publishes a dataset's
2027 file fails as a gap (D-028) for that dataset. The week 1 build's refresh
will therefore fail for play-by-play, player stats, snap counts and the other
weekly files until the first 2027 games are played; the runner then builds on
the newest snapshots after three attempts or K - 3 h, with an alert. Section 16
works around the schedule part by hand. Proposed fix: treat a requested season
newer than every published season of a dataset as not yet published (skip
with a warning), as for seasons before the data start. It is an `ffml/data/`
change (allowed path), so it needs a change-log row, the Evaluator's
byte-identical check on every committed week's feature rows, and a re-pin of
the code worktree, before 2027 week 1 (2027-09).

## DE-5.10: D-122, the unpublished newest season (2026-11-08)

Fixes the DE-5.9 finding before the freeze. `ingest_dataset` skips a
requested season with a warning as "not yet published" only when its file
is not found and it is newer than every season the source has published for
the dataset (the seasons in the dataset's current `LATEST` snapshot and the
seasons loaded in this run). It is recorded in
`DatasetResult.not_yet_published`, in the manifest's
`extra.not_yet_published_seasons` (only when non-empty) and in the ingest
summary, not as a D-028 gap. If a later requested season loads, the skipped
one was not the newest and the dataset fails as a gap. A missing season
known to be at or before the newest published one fails at once, exactly as
before. `seasons_to_load` is unchanged, and a fully published 2016-2026
source loads the same seasons in the same order with the same manifest keys.
`ffml.data.refresh` reports `not_yet_published` per dataset. RUNBOOK section
16 keeps one refresh by hand before 2027 week 1 (the runner refreshes only
inside a week's window, so the 2027 schedule must reach `dim_game` first),
now without the per-dataset workaround, and drops the week 1 limit note.

## DE-5.11 and D-127 (2026-11-11)

- **D-126 (F8), commit 162715a:** the D-122 skip also requires that no
  regular-season game of the season is final in the schedule loaded in the
  same run; `schedules` is ingested first; once a game is final a missing
  file is a gap and the refresh stops before process.
- **D-127, the window shift:** the runner already read the window from the
  pre-registration; only text changed (help, docstrings, the branch README,
  the setup script's printed `init`, the scheduler's example). For a
  dry-run-branch week the runner now passes the scorer `--worktree <dry-run
  worktree> --allow-rehearsal --push-record <dry-run push record>`, so the
  Week 10 rehearsal is scored from `livetest-dryrun`. The tracked schedule
  `scripts/livetest_schedule_2026.json` holds weeks 11-18. RUNBOOK sections
  1-5, 9, 11, 13, 14, 15 and 16 use week 11 and 2027 weeks 1-10; new section
  19 is the Week 10 rehearsal. Tests: the D-127 window (week 10 never built,
  missed or counted; STATUS "Window 2026w11 to 2027w10 (18 weeks)") and the
  Week 10 rehearsal built and scored on `livetest-dryrun` only.
