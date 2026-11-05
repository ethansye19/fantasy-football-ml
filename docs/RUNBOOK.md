# Weekly operations runbook (OR-5.1)

This runbook covers the chain_v2 live test over 2026 weeks 10-18 (D-098).
Each week's projections are saved and hashed before that week's first
kickoff. They are scored only after the games finish. It also covers the
alerts that support the test (QA-5.1).

The test design is decided by the owner after the evaluator's pass-probability
estimate. That design sets the frozen code commit, the window and how missed
weeks count. The machinery below follows that design. It does not decide any
of it.

## 1. Limits that always apply (D-099, D-100)

- **No public projections.** The real app shows "no approved model". `ffml predict`
  in live mode exits 2 until a model passes its pre-registered test.
- **Private, labeled outputs only.** chain_v2 outputs exist only in the private
  store and in private tests, labeled "not approved".
  - Real builds write to `data/predictions_private`.
  - Rehearsals write to the sandbox under `data/livetest/rehearsal/`.
- **No approval claims.** Nothing here approves a model.
- **The holdout is never unlocked.** Never set `FFML_HOLDOUT_UNLOCK`. The runner refuses to start when it
  is set, and it strips the variable from every subprocess.
- **No automated pushes of `main`.** Automation pushes only `refs/heads/livetest-2026`, from its own worktree.
  It never force-pushes. Unreviewed work on `main` therefore never reaches the remote this way.
- **Snapshots are append-only.** A refresh adds snapshots and moves `LATEST`. It never rewrites or prunes a
  snapshot. The runner fingerprints every snapshot before and after the refresh, and it stops with exit 7
  if one changed.

## 2. Components

| Piece | Path | Role |
| --- | --- | --- |
| Weekly runner | `scripts/livetest_week.py` | `run`, `watchdog`, `check`, `plan`, `status`, `verify`, `init` |
| Shared machinery | `scripts/livetest_core.py` | week plan, commitment re-hash, pin check, commitments repo, GitHub record, fingerprints |
| Refresh driver | `python -m ffml.data.refresh` (also `ffml refresh --step`, `scripts/refresh_data.py`) | `ingest`, `sleeper`, `process`, `validate`, `features`, one step per process; the runner uses the pinned interpreter |
| Build | `ffml livetest build-week --season S --week W [--rehearsal]` | ML Engineer: refit, calibrate, predict privately, write `commitment.json` |
| Build stub | `scripts/livetest_stub_build.py` | the same contract with no model output (tests and machinery rehearsals) |
| Pinned code | `scripts/livetest_setup_worktree.py` | worktree at the pre-registered commit with its own venv |
| Scheduler | `scripts/livetest_schedule.ps1` | Windows Task Scheduler tasks (dry run unless `-Register`) |
| Alerts | `scripts/alert.py` | local log, webhook (`FFML_ALERT_WEBHOOK`), Windows toast |

Files written under `data/`. None of them is in git.

| Path | Content |
| --- | --- |
| `data/livetest/config.json` | test window and pin, written by `init` (keeps a history of replaced values) |
| `data/livetest/{S}w{WW}/commitment.json` | written by build-week |
| `data/livetest/runner/{S}w{WW}/` | `state.json`, `push_record.json` (push result and GitHub's record), `verify.json`, `MISSED.json` |
| `data/livetest/logs/*.jsonl` | one log per run (every step, exit code, alert) and validation reports |
| `data/livetest/schedule_plan.json` | the plan the tasks were registered from (used by the drift check) |
| `data/schema_pins/expected_schemas.json` | working copy of the schema pins; every ingest reads and updates it, so the tracked `ffml/data/schemas/expected_schemas.json` (and a pinned checkout) stays clean unless `FFML_UPDATE_SCHEMA_PIN=1` asks for a deliberate re-pin |
| `data/alerts/alerts.jsonl` | every alert, with the outcome of each channel |

The commitments branch `livetest-2026` is an orphan branch, with no history
shared with `main`. It is checked out in `../ffml-livetest-commitments`.

```
README.md, .gitattributes (* -text: bytes are committed exactly)
2026w10/commitment.json
2026w10/player_summary.parquet        copy when under 5 MB; its SHA-256 is in the commitment
2026w11/MISSED.json                   only when nothing was pushed before kickoff
rehearsal/2026w10-<created stamp>/... rehearsals, not part of the test
```

## 3. One-time setup (after the owner signs off on the design)

Run everything from the repository root in PowerShell. Set
`$env:PYTHONUTF8 = "1"` first.

1. **Create the pinned code worktree.** Use the commit that was pre-registered.

   ```powershell
   .venv\Scripts\python scripts\livetest_setup_worktree.py --commit <sha>
   ```

   This creates `..\ffml-livetest-code`, detached at `<sha>`, and a Python 3.11
   venv at `..\ffml-livetest-code\.venv`. The venv is installed from that
   commit's `requirements.lock.txt`, plus the worktree as an editable package.
   The script ends with the pin check described in section 6, and it prints
   the `init` command. Use `--dry-run` to see the commands first.

   Two practical notes:
   - The setup took about 8 minutes on this laptop, mostly downloads.
   - Keep the worktree at the short default path. The venv has deep paths,
     and under a long parent directory (such as `%TEMP%`) they pass the
     Windows 260-character limit. `git worktree remove` then fails with
     "Filename too long".

2. **Record the window and the pin.**

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py init --season 2026 --weeks 10-18 `
     --code-worktree ..\ffml-livetest-code --python ..\ffml-livetest-code\.venv\Scripts\python.exe `
     --expected-commit <sha> --note "pre-registration <commit of the pre-registration note>"
   ```

   If the design starts at the next full week, use `--weeks 11-18`. With
   `--force` the config is replaced. The old values are kept under `history`.

3. **Configure alerts.**
   - Put `FFML_ALERT_WEBHOOK=<url>` in `.env`. Slack, Discord and ntfy URLs
     are detected automatically. Any other URL gets a JSON body with `text`
     and `alert`.
   - Then send a test alert:

   ```powershell
   .venv\Scripts\python scripts\alert.py test
   ```

   Check that the webhook received it and that a toast appeared. The result
   is also printed, and logged in `data/alerts/alerts.jsonl`.

4. **Rehearse with the real build before week 10.** This uses the
   `build-week` rehearsal sandbox and pushes under `rehearsal/`:

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py run --rehearsal --season 2026 --week 10 --skip-refresh
   ```

5. **Register the scheduled tasks.** Dry run first:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1
   powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -Register -LogonMode Password
   ```

   Logon modes:
   - `-LogonMode Password` runs whether or not the user is logged on. It asks
     for the Windows password once, and Task Scheduler stores it.
   - `Interactive` runs only while the user is logged on. A locked screen is
     fine.
   - `S4U` cannot read Windows Credential Manager, so `git push` and `gh`
     fail. Do not use it.

   Registering also saves `data/livetest/schedule_plan.json`. Re-run
   `-Register` whenever the daily check reports schedule drift. It replaces
   the tasks.

6. **Set up power.**
   - In Power Options, set "Allow wake timers" to Enable on battery and when
     plugged in.
   - Keep the laptop plugged in and asleep at most (not shut down) on
     kickoff days.

## 4. The week cycle

K is the week's first kickoff in `dim_game.kickoff_utc`. The runner
cross-checks it against the live nflverse schedule and uses the earlier of
the two.

A week is **tentative** while every one of its games sits at one identical
time. Week 18 is tentative until the NFL sets its times, which may include
Saturday games. For a tentative week, K is moved 24 hours earlier until the
real times appear.

| Time | What happens |
| --- | --- |
| K - 12 h | the runner's window opens (earlier runs exit 0 "too early") |
| K - 9 h | scheduled run starts, then repeats every 30 min |
| run | 1. refresh with the pinned interpreter (`python -m ffml.data.refresh`, one step per process): ingest of all seasons 2016 through the current one, Sleeper map (once per day), process, validate, features |
| run | 2. `ffml livetest build-week` from the pinned worktree |
| run | 3. verify the commitment (hashes, digest, season/week, `created_at` < K, pinned commit, clean tree) |
| run | 4. commit to `livetest-2026` and push that branch only |
| run | 5. record `git ls-remote` and GitHub's view, then re-hash against the manifest and the pushed blobs |
| K - 3 h | no new refresh starts; if no refresh succeeded, the build uses the newest snapshots, with an alert |
| K - 1 h | watchdog: alert if the commitment is not on the remote branch |
| K - 30 min | no refresh or build starts |
| K - 5 min | no push starts |
| K | nothing is committed for the week any more |
| after K | the next run or the daily check pushes `{S}w{WW}/MISSED.json` if nothing was pushed |
| after the games | scoring, by the evaluator, outside this runner |

The runner is idempotent:
- a week whose commitment is on the branch is left alone (exit 0);
- a built but unpushed commitment is pushed as it is, never rebuilt;
- `build-week` itself refuses a second real build of a week (exit 4).

A refresh that fails is retried on the next 30-minute repetitions, at most
three attempts in all. The build waits for a successful refresh until the
attempts are used up or K - 3 h passes, and then uses the newest snapshots.
The same applies when earlier weeks' games are still not final in the data.

Schedule examples (UTC):
- week 10: K = 2026-11-13 01:15; the run starts 2026-11-12 16:15.
- week 12 is on Thanksgiving eve: K = 2026-11-26 01:00 (Wednesday 20:00 ET);
  the run starts 2026-11-25 16:00.
- week 18 is tentative in the current schedule (all games 2027-01-10 18:00);
  it is scheduled from 2027-01-09 09:00.

Manual run, the same command the task uses:

```powershell
.venv\Scripts\python scripts\livetest_week.py run --season 2026 --week 10
.venv\Scripts\python scripts\livetest_week.py run --season 2026 --week 10 --dry-run   # changes nothing
.venv\Scripts\python scripts\livetest_week.py run --retry-every-minutes 30            # keep retrying until the cutoff
```

## 5. Checking that a commitment was pushed before kickoff

1. Overview:

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py status
   ```

   It prints a row per week:
   - the local commitment hash;
   - whether the remote branch holds identical bytes;
   - GitHub's push time;
   - `on time`, meaning the push time is before K;
   - whether a MISSED marker exists.

2. **External timestamp.** Git commit dates come from this laptop's clock.
   GitHub's repository activity record does not: GitHub's servers set it when
   the push is received.

   ```powershell
   gh api "repos/ethansye19/fantasy-football-ml/activity?ref=refs/heads/livetest-2026"
   ```

   Find the entry whose `after` is the commit sha, in
   `data/livetest/runner/2026w10/push_record.json`. Compare its `timestamp`
   with K.

   The push record also stores:
   - the activity entry;
   - `gh api repos/.../commits/<sha>`;
   - the branch ref;
   - the clock skew between the laptop and GitHub. A skew over 120 s raises
     an alert.

3. **Bytes on the remote equal the local files and the manifest:**

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py verify --season 2026 --week 10
   git fetch origin livetest-2026
   git show origin/livetest-2026:2026w10/commitment.json > $env:TEMP\c.json; Get-FileHash $env:TEMP\c.json
   ..\ffml-livetest-code\.venv\Scripts\python -m ffml.cli livetest verify data\livetest\2026w10\commitment.json
   ```

   `verify` recomputes the digest, re-hashes every listed file, compares the
   pushed blobs, and runs `ffml livetest verify` as a second opinion. It
   exits 6 on any mismatch.

## 6. The pinned worktree

`scripts/livetest_setup_worktree.py` creates the worktree with
`git worktree add --detach`. Its venv is `.venv` inside it, which git
ignores. The worktree shares data with the main checkout through
`FFML_DATA_DIR`, `FFML_ARTIFACTS_DIR` and `FFML_REPORTS_DIR`. The runner sets
these for every subprocess, and it drops `PYTHONPATH`, `PYTHONHOME` and
`VIRTUAL_ENV`.

The refresh also runs with the pinned interpreter, so the data the frozen
model sees are produced by frozen code.

Before every run, the runner refuses (exit 2, alert) unless all of these hold:
- the path is a git worktree other than the main checkout;
- `HEAD` equals the expected commit;
- `git status --porcelain --untracked-files=all` is empty (ignored files such
  as `.venv` are allowed);
- the interpreter imports `ffml` from inside the worktree, both from the
  worktree directory and from a neutral directory (the editable install);
- `pip freeze --exclude-editable` equals the worktree's
  `requirements.lock.txt`.

The commitment must also name the pinned commit, with `code.clean: true`.

To check by hand:

```powershell
git -C ..\ffml-livetest-code rev-parse HEAD
git -C ..\ffml-livetest-code status --porcelain --untracked-files=all
..\ffml-livetest-code\.venv\Scripts\python -c "import ffml; print(ffml.__file__)"
```

Never edit, check out or `pip install` anything in the pinned worktree, and
never run an ingest with `FFML_UPDATE_SCHEMA_PIN=1` from it: that writes the
tracked pin file and makes the tree dirty. A routine ingest writes new
columns to `data/schema_pins/expected_schemas.json` instead. If the
pre-registration changes, that is a new decision:
1. `git worktree remove ..\ffml-livetest-code`;
2. set up again;
3. `init --force`.

## 7. What to do when something fails

Every alert names the run log. Open it first: one JSON line per step, with
the stderr tails of the refresh and the build.

| Alert or exit | Meaning | Action |
| --- | --- | --- |
| `refresh_failure`, exit 1 | a refresh step failed (network, nflverse, Sleeper) | Nothing, if before K - 3 h: the next repetition retries. Check the network. After K - 3 h the build uses the newest snapshots. Never edit or delete snapshots by hand. |
| `data_quality` | `validate` FAILED, or earlier weeks' games are not final yet | Read the validation report in `data/livetest/logs/validation_*.md`. Retried like a refresh failure. |
| `build_failure`, exit 2 | the pin check refused, or build-week was refused by the approval gate | Read the reason. Do not "fix" the pinned worktree: tell the orchestrator. A refused gate is a configuration problem, not a model problem. |
| `build_failure`, exit 1 | build-week failed (traceback in the log) | Re-run `run --season S --week W` by hand once the cause is fixed (network, memory). It is safe: nothing was committed. |
| `push_failure`, exit 1 | remote unreachable or push rejected | Check `gh auth status`, the network and `git -C ..\ffml-livetest-commitments status`. Re-run the runner: it pushes the existing commitment, never a new build. Never force-push. Never push `main`. |
| `missed_commitment` at K - 1 h | the watchdog found nothing on the remote | Run the runner by hand now. If it cannot push before K - 5 min, the week is missed. |
| `missed_commitment` after K, exit 3 | the week kicked off without a pushed commitment | No action on the branch: the MISSED marker is pushed automatically. Tell the owner. The test design decides how it counts. |
| `integrity`, exit 6 | the branch holds a different commitment, a file no longer matches its hash, or post-push verification failed | Stop. Do not rebuild, delete or re-push. Keep the logs and tell the orchestrator. |
| `integrity`, exit 7 | an existing snapshot changed during a refresh | Stop. Do not build. Tell the orchestrator. |
| `integrity` (clock skew) | the laptop clock differs from GitHub by over 2 min | Resync Windows time (Settings, Time, Sync now). Use GitHub's timestamp as the record. |
| `schedule_drift` | kickoff times changed after the tasks were registered (week 18 flex) | Re-run `scripts\livetest_schedule.ps1 -Register` (dry run first). |
| `stale_predictions` | in season, the newest private run (or the public run, once a model is live) is over 8 days old | During the test, check that the last week's run completed. Test with `scripts\alert.py check-stale --now <time>`. |

## 8. Laptop off, asleep or logged out

The tasks use these settings:
- `StartWhenAvailable`: a missed start runs when the machine is available
  again;
- `WakeToRun`: wake from sleep, when wake timers are allowed;
- running on battery is allowed;
- `IgnoreNew`: no overlapping runs;
- a 5-hour limit;
- each run trigger ends at kickoff.

| State during the window | Result |
| --- | --- |
| asleep, wake timers on | the task wakes the laptop and runs |
| asleep, wake timers off (Windows often disables them on battery) | nothing runs until the laptop wakes. Missed repetitions run once on wake, while still before kickoff. |
| shut down or hibernated past kickoff | nothing runs. At the next boot the trigger has expired, so the runner is not started for that week. The daily check (or the next week's run) pushes `MISSED.json`, and alerts. |
| logged out, `-LogonMode Interactive` | nothing runs (same as shut down) |
| logged out, `-LogonMode Password` | the task runs, and git and gh credentials work |
| no network | the build succeeds locally, the push fails and is retried every 30 min until K - 5 min. The watchdog alerts at K - 1 h. |

A missed week shows up in four places, and the signals agree:
1. an alert at K - 1 h, plus another when the marker is pushed;
2. `status`, where `pushed False` and `missed True`;
3. `MISSED.json` on the branch. It records any local commitment that was
   built but not pushed, with its hash.
4. the absence of a commitment with a GitHub push timestamp before K.

A commitment pushed after K is never made: the runner stops pushing at
K - 5 min. If a push still lands after K (an extreme case), the push record
says `on_time: false` and an alert is raised.

## 9. Daily check and Gate 5 evidence

`livetest_week.py check` runs daily at 15:00 local. It does three things:
- the stale-prediction check;
- schedule drift against `schedule_plan.json`;
- MISSED markers.

Useful one-off commands:

```powershell
.venv\Scripts\ffml refresh --step all                               # the five refresh steps, one child process each
.venv\Scripts\python scripts\livetest_week.py plan                  # the week plan and trigger times
.venv\Scripts\python scripts\livetest_week.py watchdog --season 2026 --week 10
.venv\Scripts\python scripts\alert.py tail -n 20
.venv\Scripts\python scripts\alert.py send --kind push_failure --message "forced failure test" --severity warning
.venv\Scripts\python scripts\alert.py check-stale --now 2026-11-21T12:00:00Z
```

## 10. Exit codes of `livetest_week.py`

| Code | Meaning |
| --- | --- |
| 0 | done, already pushed, or nothing to do (outside the window, too early, another run holds the lock) |
| 1 | failed; the next repetition retries (refresh, build, push, watchdog finding) |
| 2 | build blocked: pin refused, or approval gate refused |
| 3 | missed: too late for this week |
| 4 | refused: unsafe environment or bad arguments (`FFML_HOLDOUT_UNLOCK`, `--now` on a real run, stub outside a rehearsal, no `init`) |
| 6 | integrity problem; needs a human |
| 7 | an existing snapshot was modified; needs a human |
