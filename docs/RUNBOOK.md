# Weekly operations runbook (OR-5.1)

This runbook covers the chain_v2 live test over 2026 weeks 10-18 (D-098,
D-105). Each week's projections are saved and hashed before that week's
official first kickoff. They are scored only after the games finish. The
runbook also covers the alerts that support the test (QA-5.1), the operating
rules for missed weeks (D-106, revisited in D-111: up to two misses), the
operations set-up (D-107), the pre-build change-log check (D-116) and the
Friday scoring and sealing job (section 15). Section 18 covers where the app
takes the current season and week from (D-124).

Sections 11 to 14 are the ones the owner acts on: the GitHub watchdog, the
power settings, the logged-off dry run and the registration command.

## 1. Limits that always apply (D-099, D-100)

- **No public projections.** The real app shows "no approved model". `ffml predict`
  in live mode exits 2 until a model passes its pre-registered test.
- **Private, labeled outputs only.** chain_v2 outputs exist only in the private
  store and in private tests, labeled "not approved".
  - Real builds write to `data/predictions_private`.
  - Rehearsals and dry runs write to the sandbox under `data/livetest/rehearsal/`.
- **No approval claims.** Nothing here approves a model.
- **The holdout is never unlocked.** Never set `FFML_HOLDOUT_UNLOCK`. The runner refuses to start when it
  is set, and it strips the variable from every subprocess.
- **No automated pushes of `main`.** Automation pushes only `refs/heads/livetest-2026` (or the dry-run
  branch), from its own worktree. It never force-pushes. Unreviewed work on `main` therefore never
  reaches the remote this way.
- **Snapshots are append-only.** A refresh adds snapshots and moves `LATEST`. It never rewrites or prunes a
  snapshot. The runner fingerprints every snapshot before and after the refresh, and it stops with exit 7
  if one changed.
- **Least privilege.** The scheduled tasks run as the owner's user with no administrator rights
  (section 14).

## 2. Components

| Piece | Path | Role |
| --- | --- | --- |
| Weekly runner | `scripts/livetest_week.py` | `run`, `watchdog`, `check`, `plan`, `status`, `verify`, `init`, `score-week`, `seal`, `seal-check` |
| Pre-registration | `docs/status/livetest-preregistration-2026.md` (fenced JSON block) | the runner reads `missed_weeks.allowed` (2) from it; a real run refuses without it |
| Scorer | `python -m ffml.eval.livetest_score --week 2026wNN`, `--data-cutoff 2026wNN`, `--prebuild-check --from A --to B` | Evaluator: weekly scoring after the data cutoff, sealed metric parts, the pre-build check; the runner calls it from the pinned worktree and only publishes the sealed hashes |
| Scorer stub | `scripts/livetest_stub_score.py` (`--stub-score`) | the same three calls with no metric (tests and machinery dry runs) |
| Shared machinery | `scripts/livetest_core.py` | week plan, commitment re-hash, pin check, commitments repo, GitHub record, fingerprints |
| Refresh driver | `python -m ffml.data.refresh` (also `ffml refresh --step`, `scripts/refresh_data.py`) | `ingest`, `sleeper`, `process`, `validate`, `features`, one step per process; the runner uses the pinned interpreter |
| Build | `ffml livetest build-week --season S --week W [--rehearsal]` | ML Engineer: refit, calibrate, predict privately, write `commitment.json` |
| Build stub | `scripts/livetest_stub_build.py` | the same contract with no model output (tests and machinery dry runs) |
| Pinned code | `scripts/livetest_setup_worktree.py` | worktree at the pre-registered commit with its own venv |
| Scheduler | `scripts/livetest_schedule.ps1` | Windows Task Scheduler tasks (dry run unless `-Register`); also the dry-run tasks |
| Power check | `scripts/livetest_power_check.ps1` | read-only report of wake timers, sleep, hibernate, lid, Fast Startup |
| GitHub watchdog | `.github/workflows/livetest_watchdog.yml`, `scripts/livetest_watchdog_gh.py`, `scripts/livetest_schedule_2026.json` | hourly check on GitHub's side, alerts 6 h and 1 h before kickoff |
| Alerts | `scripts/alert.py` | local log, webhook (`FFML_ALERT_WEBHOOK`), Windows toast |

Files written under `data/`. None of them is in git.

| Path | Content |
| --- | --- |
| `data/livetest/config.json` | test window and pin, written by `init` (keeps a history of replaced values) |
| `data/livetest/{S}w{WW}/commitment.json` | written by build-week |
| `data/livetest/rehearsal/{S}w{WW}/` | rehearsal and dry-run builds (sandbox) |
| `data/livetest/runner/{S}w{WW}/` | `state.json`, `push_record.json` (push result and GitHub's record), `verify.json`, `MISSED.json`, `seal_record.json`, `seal_check.json` |
| `data/livetest/scoring/sealed/{S}w{WW}.sha256` | written by the Evaluator's scorer: the SHA-256 of the week's sealed metric parts (section 15) |
| `data/livetest/runner/dryrun/<branch>/{S}w{WW}/` | the same for dry runs |
| `data/livetest/logs/*.jsonl` | one log per run (every step, exit code, alert) and validation reports |
| `data/livetest/schedule_plan.json` | the plan the tasks were registered from (used by the drift check) |
| `data/schema_pins/expected_schemas.json` | working copy of the schema pins; every ingest reads and updates it, so the tracked `ffml/data/schemas/expected_schemas.json` (and a pinned checkout) stays clean unless `FFML_UPDATE_SCHEMA_PIN=1` asks for a deliberate re-pin |
| `data/alerts/alerts.jsonl` | every alert, with the outcome of each channel |

The commitments branch `livetest-2026` is an orphan branch, with no history
shared with `main`. It is checked out in `../ffml-livetest-commitments`.

```
README.md, .gitattributes (* -text: bytes are committed exactly)
STATUS.md                             per-week state, misses and the test state; regenerated at every push
2026w10/commitment.json
2026w10/player_summary.parquet        copy when under 5 MB; its SHA-256 is in the commitment
2026w11/MISSED.json                   the week was missed: reason, log excerpt, miss number, test state
2026w11/late/commitment.json          built before kickoff, pushed after it; not part of the test
2026w10/sealed_hashes.json            SHA-256 of the week's sealed metric parts, after scoring (section 15)
rehearsal/2026w10-<created stamp>/... rehearsals, not part of the test
```

Only `README.md` and `STATUS.md` are ever rewritten on the branch; every
other file is immutable. Dry runs use a separate branch (`livetest-dryrun`),
never this one.

## 3. One-time setup (after the owner signs off on the design)

Run everything from the repository root in PowerShell. Set
`$env:PYTHONUTF8 = "1"` first.

1. **Create the pinned code worktree at Y, not X (D-120, the Evaluator's F1).**
   The freeze procedure gives three commits on `main`, in order:
   - X, the frozen code commit;
   - the fill commit, which writes X into the pre-registration's `commit`
     field and changes nothing else;
   - Y, one commit that only appends change-log rows for every commit from X
     to the fill commit (section 17). Y is exempt from needing its own row.

   Pin the worktree at **Y**. A worktree at X would carry the pre-registration
   with the placeholder commit field, so every weekly code-commit check would
   fail.

   ```powershell
   .venv\Scripts\python scripts\livetest_setup_worktree.py --commit <Y>
   ```

   This creates `..\ffml-livetest-code`, detached at Y, and a Python 3.11
   venv at `..\ffml-livetest-code\.venv`. The venv is installed from that
   commit's `requirements.lock.txt`, plus the worktree as an editable package.
   The script ends with the pin check described in section 6, including
   the check that Y descends from X through allowed paths only (X is read from
   the main checkout's pre-registration, or `--frozen-commit <X>`), and it
   prints the `init` command. Use `--dry-run` to see the commands first.

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
     --expected-commit <Y> --note "pinned at Y, the change-log commit after the fill commit (D-120)"
   ```

   `--expected-commit` is Y. Commitments then record `code.commit` = Y; the
   weekly check accepts it because X..Y touches only allowed paths.

   **The final always runs from the main checkout**, never from the pinned
   worktree (D-120): `python -m ffml.eval.livetest_score --final` in this
   repository, where the pre-registration carries the filled commit and the
   change log is complete (section 15).

   If the design starts at the next full week, use `--weeks 11-18`. With
   `--force` the config is replaced. The old values are kept under `history`.
   `init` also records the pre-registration it found
   (`docs/status/livetest-preregistration-2026.md`, or `--preregistration`)
   and the allowed miss count read from it; the printed JSON shows
   `"misses_allowed": 2`.

3. **Configure alerts.**
   - Put `FFML_ALERT_WEBHOOK=<url>` in `.env`. Slack, Discord and ntfy URLs
     are detected automatically. Any other URL gets a JSON body with `text`
     and `alert`.
   - Add the same URL as the repository secret `FFML_ALERT_WEBHOOK` for the
     GitHub watchdog (section 11).
   - Then send a test alert:

   ```powershell
   .venv\Scripts\python scripts\alert.py test
   ```

   Check that the webhook received it and that a toast appeared. The result
   is also printed, and logged in `data/alerts/alerts.jsonl`.

4. **Export and commit the tracked schedule** for the GitHub watchdog
   (section 11), then push `main` through the normal review.

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py plan --export scripts\livetest_schedule_2026.json
   ```

5. **Check the power settings** (section 12) and fix anything marked CHECK.

6. **Run the logged-off dry run** (section 13). It proves build, push, retry,
   failure alert and both watchdogs while the owner is logged off.

7. **Register the weekly tasks** with the command in section 14. Dry run
   first (without `-Register`), then with `-Register`.

   Registering also saves `data/livetest/schedule_plan.json`. Re-run
   `-Register` whenever the daily check reports schedule drift. It replaces
   the weekly tasks and leaves dry-run tasks alone.

## 4. The week cycle

K is the week's official first kickoff in `dim_game.kickoff_utc`, including
Thanksgiving and other early games (D-106). The runner cross-checks it against
the live nflverse schedule and uses the earlier of the two.

A week is **tentative** while every one of its games sits at one identical
time. Week 18 is tentative until the NFL sets its times, which may include
Saturday games. For a tentative week, K is moved 24 hours earlier until the
real times appear.

| Time | What happens |
| --- | --- |
| K - 25 h | the runner's window opens (earlier runs exit 0 "too early") |
| K - 24 h 15 min | scheduled run starts, then repeats every 30 min until a valid commitment is pushed (the task repeats until K - 5 min) |
| run | 1. refresh with the pinned interpreter (`python -m ffml.data.refresh`, one step per process): ingest of all seasons 2016 through the current one, Sleeper map (once per day), process, validate, features |
| run | 2. pre-build check (D-116, A1): `<pinned python> -m ffml.eval.livetest_score --prebuild-check --from <code commit of the latest commitment on the branch, "" before the first> --to <pinned HEAD>` in the pinned worktree; exit 0 builds, exit 5 (or a check that cannot run) blocks with a `prebuild_blocked` alert and the next 30-minute repetition retries |
| run | 3. `ffml livetest build-week` from the pinned worktree |
| run | 4. verify the commitment (hashes, digest, season/week, `created_at` < K, pinned commit, clean tree) |
| run | 5. commit to `livetest-2026` (with a regenerated `STATUS.md`) and push that branch only |
| run | 6. record `git ls-remote` and GitHub's view, then re-hash against the manifest and the pushed blobs |
| K - 6 h | GitHub watchdog: workflow failure plus webhook if the commitment is not on the branch |
| K - 3 h | no new refresh starts; if no refresh succeeded, the build uses the newest snapshots, with an alert |
| K - 1 h | local watchdog and GitHub watchdog: alert if the commitment is not on the remote branch |
| K - 30 min | no refresh or build starts |
| K - 5 min | no push starts |
| K | the deadline (D-106): nothing pushed after it counts |
| after K | the next run or the daily check pushes `{S}w{WW}/MISSED.json` (and `late/` if a commitment was built), see section 8 |
| after the games | scoring, by the evaluator, outside this runner |

Why 24 h 15 min and not 24 h: Task Scheduler starts a task up to a minute
after its trigger, so the first attempt is at least 24 hours before K as
D-106 asks. The build is point-in-time as of the build; a commitment made a
day early uses the information of that time.

The runner is idempotent:
- a week whose commitment is on the branch is left alone (exit 0);
- a built but unpushed commitment is pushed as it is, never rebuilt;
- `build-week` itself refuses a second real build of a week (exit 4).

A refresh that fails is retried on the next 30-minute repetitions, at most
three attempts in all. The build waits for a successful refresh until the
attempts are used up or K - 3 h passes, and then uses the newest snapshots.
The same applies when earlier weeks' games are still not final in the data.

Schedule examples (UTC):
- week 10: K = 2026-11-13 01:15; the run starts 2026-11-12 01:00 (Wednesday
  20:00 ET) and repeats until 2026-11-13 01:10.
- week 12 is on Thanksgiving eve: K = 2026-11-26 01:00 (Wednesday 20:00 ET);
  the run starts 2026-11-25 00:45 (Tuesday 19:45 ET).
- week 18 is tentative in the current schedule (all games 2027-01-10 18:00);
  it is scheduled from 2027-01-08 17:45. When the NFL sets Saturday games the
  daily check reports drift; re-register, and re-export the tracked schedule.

Manual run, the same command the task uses:

```powershell
.venv\Scripts\python scripts\livetest_week.py run --season 2026 --week 10
.venv\Scripts\python scripts\livetest_week.py run --season 2026 --week 10 --dry-run   # changes nothing
.venv\Scripts\python scripts\livetest_week.py run --retry-every-minutes 30            # keep retrying in one process
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
   - `missed`, the reason (`technical_failure` or `machine_offline`),
     whether a late commitment exists, and `sealed` (section 15);

   and a last line: `misses: N (...); allowed 2; test state: VALID` or
   `INVALID (D-106, D-111: more than 2 misses)`. The allowed count is the
   pre-registration's `missed_weeks.allowed`. `STATUS.md` on the branch shows
   the same table.

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

4. **From GitHub's side:** the latest `livetest watchdog` workflow runs
   (Actions tab) inside the 6 h and 1 h windows are green when the
   commitment is there and red when it is not.

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
  `requirements.lock.txt`;
- once the pre-registration's `commit` field is filled with X: the pinned
  commit Y is X or a descendant of X, and `git diff --name-only X Y` touches
  only paths the pre-registration allows (a frozen path, or any path not
  under `allowed_paths`, refuses). Before the fill, this check is skipped
  and the run log says so.

The commitment must also name the pinned commit, with `code.clean: true`.

To check the descendant relation by hand:

```powershell
git merge-base --is-ancestor <X> <Y>; $LASTEXITCODE        # 0: Y descends from X
git diff --name-only <X> <Y>                               # only docs/, tests/, reports/, ... (allowed paths)
```

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
| `build_failure`, exit 1 | build-week failed (traceback in the log) | The next repetition retries. Re-run `run --season S --week W` by hand once the cause is fixed (network, memory). It is safe: nothing was committed. |
| `push_failure`, exit 1 | remote unreachable or push rejected | Check `gh auth status`, the network and `git -C ..\ffml-livetest-commitments status`. The next repetition pushes the existing commitment as it is, never a new build. Never force-push. Never push `main`. |
| `missed_commitment` at K - 6 h or K - 1 h (GitHub workflow red, webhook) | the GitHub watchdog found nothing on the branch | Check the laptop: is it on, on AC, online? Run the runner by hand now (`run --season S --week W`). If it cannot push before K - 5 min, the week is missed. |
| `missed_commitment` at K - 1 h (local) | the local watchdog found nothing on the remote | Same as above. |
| `missed_commitment` after K, exit 3 | the week kicked off without a pushed commitment | No action on the branch: the MISSED marker (with reason and log excerpt) is pushed automatically, and a late commitment with it. Tell the owner. First and second miss: excluded, test goes on. Third miss: the test is INVALID (D-106, D-111). |
| `prebuild_blocked`, exit 2 | the pre-build check found commits between the last build's code commit and the pinned HEAD without a change-log entry, or touching a frozen path, or the check could not run | The alert lists the uncovered and the frozen commits. Add the missing change-log entries (on the pinned code, through the normal review); a frozen-path change needs the owner's ruling (D-111). The runner retries every 30 minutes; a block fixed before the deadline is not a miss. If it is still blocked at K, the week is missed with reason `technical_failure`, detail "pre-build check blocked". |
| `scoring_failure` (Friday) | the refresh before scoring failed, the scorer's `--data-cutoff` or `--week` exited non-zero (other than 4), it wrote no sealed file, or the sealed hashes could not be pushed | The task repeats every 2 h for 12 h. Read the run log's scorer stderr. Scorer exit 4 ("not yet") and a week that is not final yet are retried without an alert. Tell the Evaluator if the scorer itself fails. |
| `integrity` (seal) | the branch already holds different sealed hashes for the week, or a local sealed part no longer matches its hash | Stop. Sealed hashes are immutable. Tell the Evaluator and the orchestrator; never re-seal by hand. |
| `integrity`, exit 6 | the branch holds a different commitment, a file no longer matches its hash, or post-push verification failed | Stop. Do not rebuild, delete or re-push. Keep the logs and tell the orchestrator. |
| `integrity`, exit 7 | an existing snapshot changed during a refresh | Stop. Do not build. Tell the orchestrator. |
| `integrity` (clock skew) | the laptop clock differs from GitHub by over 2 min | Resync Windows time (Settings, Time, Sync now). Use GitHub's timestamp as the record. |
| `schedule_drift` | kickoff times changed after the tasks were registered (week 18 flex) | Re-run `scripts\livetest_schedule.ps1 -Register` (dry run first) and re-export `scripts\livetest_schedule_2026.json` (section 11). |
| `stale_predictions` | in season, the newest private run (or the public run, once a model is live) is over 8 days old | During the test, check that the last week's run completed. Test with `scripts\alert.py check-stale --now <time>`. |

## 8. Missed weeks (D-106, D-111), laptop off, asleep or logged out

The miss rule (the allowed count is read from the pre-registration's
`missed_weeks.allowed`, 2 under D-111; nothing in the scripts hard-codes it):
- a week counts as missed when no commitment is on `livetest-2026` at its
  official first kickoff K;
- up to two missed weeks are **excluded from scoring**; the window is **not
  extended**; the frozen rules are unchanged and the Evaluator reports the
  pass rate of a 17- or 16-week test;
- a **third miss makes the test INVALID** (not failed);
- a miss counts only for a real technical failure or an offline machine, and
  it is logged with its reason;
- projections that exist are never withheld: a commitment that was built but
  not pushed in time is pushed anyway, under `<week>/late/`, and marked as not
  part of the test.

How a miss is recorded. After K, the next runner start or the daily check
pushes `<week>/MISSED.json`:

| Field | Content |
| --- | --- |
| `reason.category` | `technical_failure`: a runner log in the window ending at K shows a failure, or a commitment was built and not pushed; `machine_offline`: no runner log exists between K - 25 h and K (off, asleep, logged off, or the task did not start) |
| `reason.detail` | the last failed step and its message, or the built-but-not-pushed statement; exactly "pre-build check blocked" when the pre-build check was still blocking at K (with `reason.check`: its last message and time) |
| `run_logs` | the window, the log file names in it, and the last 12 events of the latest log |
| `local_commitment` | whether a local commitment exists, its hash, `created_at`, `built_before_kickoff` |
| `late_commitment` | the `late/` path and hash when one was pushed, with its verification result |
| `miss_number`, `allowed`, `earlier_misses`, `test_status` | `miss N of 2 allowed`: `VALID: ...` for misses 1 and 2, `INVALID: ...` for miss 3 |

`STATUS.md` on the branch is regenerated at every push and shows every test
week, the misses, the sealed weeks and the test state. `status` (section 5)
prints the same. The alert for the third miss says INVALID.

The tasks use these settings:
- `StartWhenAvailable`: a missed start runs when the machine is available
  again;
- `WakeToRun`: wake from sleep, when wake timers are allowed (section 12);
- start only on AC power (a run that is already going is not stopped on
  unplugging);
- `IgnoreNew`: no overlapping runs;
- a 5-hour limit;
- each run trigger ends at kickoff.

| State during the window (K - 25 h to K) | Result |
| --- | --- |
| on, on AC | the task runs at K - 24 h 15 min and retries every 30 min until the push |
| asleep, wake timers enabled on AC | the task wakes the laptop and runs |
| asleep, wake timers disabled | nothing runs until the laptop wakes. Missed repetitions run once on wake, while still before K - 5 min. |
| on battery | the task does not start until AC is back (the next repetition then starts it) |
| shut down (Fast Startup on) or hibernated past K | nothing runs. At the next boot the trigger has expired, so the runner is not started for that week. The daily check or the next week's run pushes `MISSED.json` with `machine_offline`, and alerts. |
| logged out, `-LogonMode Password` | the task runs, and git and gh credentials work |
| logged out, `-LogonMode Interactive` | nothing runs (same as shut down) |
| no network | the build succeeds locally, the push fails and is retried every 30 min until K - 5 min; both watchdogs alert; after K the commitment is pushed under `late/` with a `technical_failure` marker |

A missed week shows up in five places, and the signals agree:
1. alerts at K - 6 h and K - 1 h (GitHub) and K - 1 h (local), plus one when
   the marker is pushed;
2. `status`, with `missed True`, the reason and the test state;
3. `STATUS.md` and `<week>/MISSED.json` on the branch;
4. the GitHub watchdog runs inside the windows are red;
5. the absence of a commitment with a GitHub push timestamp before K.

## 9. Daily check, the Friday job and one-off commands

`livetest_week.py check` runs daily at 15:00 local. It does three things:
- the stale-prediction check;
- schedule drift against `schedule_plan.json`;
- MISSED markers (with reasons) for weeks that kicked off.

`livetest_week.py score-week` runs on Fridays from 13:00 UTC (section 15).

Useful one-off commands:

```powershell
.venv\Scripts\ffml refresh --step all                               # the five refresh steps, one child process each
.venv\Scripts\python scripts\livetest_week.py plan                  # the week plan and trigger times
.venv\Scripts\python scripts\livetest_week.py watchdog --season 2026 --week 10
.venv\Scripts\python scripts\livetest_week.py seal-check --season 2026 --week 10   # branch copy vs local sealed file
.venv\Scripts\python scripts\alert.py tail -n 20
.venv\Scripts\python scripts\alert.py send --kind push_failure --message "forced failure test" --severity warning
.venv\Scripts\python scripts\alert.py check-stale --now 2026-11-21T12:00:00Z
powershell -ExecutionPolicy Bypass -File scripts\livetest_power_check.ps1
```

## 10. Exit codes of `livetest_week.py`

| Code | Meaning |
| --- | --- |
| 0 | done, already pushed, or nothing to do (outside the window, too early, another run holds the lock) |
| 1 | failed; the next repetition retries (refresh, build, push, watchdog finding, scorer, week not final yet, not sealed on the branch yet) |
| 2 | build blocked: pin refused, approval gate refused, or the pre-build check blocked (retried every 30 min) |
| 3 | missed: too late for this week |
| 4 | refused: unsafe environment or bad arguments (`FFML_HOLDOUT_UNLOCK`, `--now` on a real run, stub outside a rehearsal, no `init`, `--dry-run-branch livetest-2026`, `--force-fail` outside a dry run) |
| 6 | integrity problem; needs a human |
| 7 | an existing snapshot was modified; needs a human |

## 11. GitHub watchdog (D-107)

The workflow `.github/workflows/livetest_watchdog.yml` runs
`scripts/livetest_watchdog_gh.py` every hour on `main`, on GitHub's runners,
so it does not depend on the laptop. The script:
1. reads the tracked schedule `scripts/livetest_schedule_2026.json`, exported
   from `dim_game` with `livetest_week.py plan --export` and committed;
2. takes the next test week and computes the hours to its official first
   kickoff;
3. inside the windows 6 h 30 min to 5 h 30 min and 1 h 30 min to 0 before
   kickoff, checks `livetest-2026` for `<week>/commitment.json` through the
   GitHub contents API (hourly cron runs land at K - 6 h to K - 6 h 15 min and
   K - 1 h to K - 1 h 15 min for the usual 01:00Z and 01:15Z kickoffs, and a
   run delayed by up to 30 minutes still falls inside; the last run before
   kickoff alerts too);
4. with nothing there: fails the run (GitHub mails the owner about the failed
   workflow) and posts to the webhook when the repository secret
   `FFML_ALERT_WEBHOOK` exists. Outside the windows it exits 0 in seconds.

Set-up:
- repository secret `FFML_ALERT_WEBHOOK` (Settings, Secrets and variables,
  Actions): the same URL as in `.env`;
- the workflow and the schedule file must be on `main` (pushed through the
  normal review).

Two GitHub behaviours that can silence this watchdog (QA-F25), and what to do:

1. **Who gets the failure e-mail.** GitHub sends the notification for a
   failed scheduled run only to the user who last modified the cron line
   (`- cron: "0 * * * *"`) in the workflow file, not to the repository owner
   as such. Today every commit is authored and pushed as `ethansye19`, so the
   owner receives them. To keep it that way:
   - in GitHub, Settings, Notifications, System, Actions: e-mail on, "Only
     notify for failed workflows";
   - if anyone else (another account, a bot or a merged pull request by
     someone else) ever changes the cron line, the owner commits a change to
     that line from their own account (for example rewriting it unchanged in
     a commit they push), so the e-mails go to the owner again;
   - set the `FFML_ALERT_WEBHOOK` secret as well: the webhook is sent by the
     workflow itself and does not depend on who edited the cron line.
2. **Schedules switched off after 60 days.** GitHub documents that scheduled
   workflows in a *public* repository are disabled automatically after 60
   days without repository activity, and shows a warning in the Actions tab
   first. This repository is private, so the documented rule does not apply
   today; the test runs until about 2027-11, and making the repository
   public is an owner decision (D-112), so treat it as a risk anyway:
   - the weekly pushes to `livetest-2026` are repository activity; during the
     offseason (2027-01 to 2027-09) push at least one commit a month to
     `main` (any reviewed change, for example the re-exported schedule);
   - check once a month that the workflow is active, and re-enable it if not:

     ```powershell
     gh workflow list --all                    # "livetest watchdog" must say active
     gh workflow enable livetest_watchdog.yml  # if it says disabled_inactivity or disabled_manually
     gh run list --workflow livetest_watchdog.yml --limit 5
     ```

   The local watchdog task (section 14) and the alert log do not depend on
   GitHub and keep working either way.

Manual run (Actions, `livetest watchdog`, Run workflow): `branch`
(`livetest-dryrun` for the dry run), `now` (a clock override such as
`2026-11-12T19:00:00Z`, which is K - 6 h 15 min for week 10) and
`force_window` (check even outside the windows). The same from a shell:

```powershell
$env:GITHUB_TOKEN = (gh auth token)
.venv\Scripts\python scripts\livetest_watchdog_gh.py --repo ethansye19/fantasy-football-ml --now 2026-11-12T19:00:00Z
.venv\Scripts\python scripts\livetest_watchdog_gh.py --repo ethansye19/fantasy-football-ml --branch livetest-dryrun --force-window
```

Re-export and commit the schedule file when the daily check reports schedule
drift (week 18), so the GitHub side has the same kickoffs as the laptop.

## 12. Power and sleep settings to check (D-107)

Run the read-only report and fix every line marked CHECK:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\livetest_power_check.ps1
```

What it reads, and the exact commands behind it (all read only):

| Setting | Command | What the live test needs |
| --- | --- | --- |
| Allow wake timers | `powercfg /q SCHEME_CURRENT SUB_SLEEP RTCWAKE` | AC index 1 (Enable). Otherwise the task cannot wake a sleeping laptop. (Index 0 Disable, 2 Important wake timers only.) |
| Sleep after | `powercfg /q SCHEME_CURRENT SUB_SLEEP STANDBYIDLE` | any value; the task wakes the machine |
| Hibernate after | `powercfg /q SCHEME_CURRENT SUB_SLEEP HIBERNATEIDLE` | AC index 0 (Never): wake timers do not fire from hibernation |
| Lid close action | `powercfg /q SCHEME_CURRENT SUB_BUTTONS LIDACTION` | AC index 0 (Do nothing) or 1 (Sleep), not 2 (Hibernate) or 3 (Shut down) |
| Sleep states | `powercfg /a` | shows Modern Standby (S0 Low Power Idle) or S3, Hibernate, Fast Startup |
| Fast Startup | `reg query "HKLM\SYSTEM\CurrentControlSet\Control\Session Manager\Power" /v HiberbootEnabled` | 1 means a "Shut down" hibernates the kernel and wake timers do not fire: on kickoff days use Sleep, never Shut down, or turn Fast Startup off |
| Armed wake timers (elevated prompt, optional) | `powercfg /waketimers`, `powercfg /lastwake`, `powercfg /requests` | the registered task appears the day before kickoff |

On this laptop on 2026-11-05 (read only, nothing changed): Balanced scheme;
wake timers Enable on AC and Disable on battery; sleep after 60 min on AC and
battery; hibernate never on AC; Modern Standby, Hibernate and Fast Startup
available; `HiberbootEnabled = 1` (Fast Startup on). The action for the owner
is therefore: keep the laptop plugged in, put it to sleep rather than shutting
it down on kickoff days (or turn Fast Startup off), and keep wake timers
enabled on AC. Settings are changed by the owner in Power Options, not by any
script here.

## 13. Logged-off dry run before Week 10 (D-107)

The dry run proves, while the owner is logged off: the scheduled task starts,
the build runs, the push lands on a test branch, a failure raises an alert,
the retry pushes the existing commitment as it is, and both watchdogs see the
branch. It never touches `livetest-2026`, and a dry-run commitment is not a
live commitment.

Everything goes to the branch `livetest-dryrun`, its own worktree
`..\ffml-livetest-dryrun-commitments`, and `data\livetest\runner\dryrun\`.
The build is `build-week --rehearsal` (the sandbox) or the stub with
`-DryRunStub`; the refresh runs unless `-DryRunSkipRefresh`. Every build runs
the pre-build check first (section 4); `-DryRunStubScore` uses the scorer stub
for it, for a dry run before the Evaluator's `--prebuild-check` is on `main`.

Step by step (about two hours):

1. Sections 11 and 12 done; `init` done (section 3). Plug the laptop in.
2. **Dry run first, nothing registered:**

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -DryRunBranch livetest-dryrun -ForceFail push
   ```

3. **Register the forced-failure dry run** (the first attempt in 10 minutes,
   then every 30 minutes for 3 hours; the dry-run watchdog 75 minutes later):

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -DryRunBranch livetest-dryrun -ForceFail push -Register
   ```

   Enter the Windows password when Get-Credential asks (section 14 explains
   where it goes). Then **log off** (not sleep, not shut down) and wait at
   least 15 minutes. If you want to also prove the wake, put the laptop to
   sleep after logging off; wake timers must be enabled (section 12).
4. **Log on and check the forced failure:**

   ```powershell
   .venv\Scripts\python scripts\alert.py tail -n 5                       # a push_failure alert from the run
   Get-ChildItem data\livetest\logs | Sort-Object LastWriteTime | Select-Object -Last 3
   .venv\Scripts\python scripts\livetest_week.py status --dry-run-branch livetest-dryrun
   ```

   (Optional, before step 3: `-ForceFail prebuild` proves the pre-build block
   the same way: a `prebuild_blocked` alert, no build, and the next run without
   the flag builds.)

   Expected: a run log written while logged off, ending in `push: failed`
   with the forced message, a `push_failure` alert (log, webhook, no toast
   while logged off), the commitment built locally, nothing on the branch,
   `pushed False` in status.
5. **Replace the task without the forced failure** and log off again:

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -DryRunBranch livetest-dryrun -Register
   ```

   The next attempt pushes the existing commitment as it is (no rebuild) to
   `livetest-dryrun`. The dry-run watchdog task, 75 minutes after the first
   start, exits 0 once the push is there.
6. **Log on and check the push and the retry:**

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py status --dry-run-branch livetest-dryrun    # pushed True
   git ls-remote origin livetest-dryrun livetest-2026                                       # only livetest-dryrun moved
   .venv\Scripts\python scripts\livetest_week.py verify --season 2026 --week 10 --dry-run-branch livetest-dryrun
   ```

   The run log shows `build: commitment written` only in the first run and
   `push: ok` in the later one.
7. **GitHub watchdog against the dry-run branch** (Actions, `livetest
   watchdog`, Run workflow): once with `branch = livetest-dryrun` and
   `force_window = true` (green: the commitment is there), and once with
   `branch = livetest-dryrun-empty` and `force_window = true` (red: the alert
   path, and the webhook message if the secret is set).
8. **Clean up:**

   ```powershell
   powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -DryRunBranch livetest-dryrun -Unregister
   ```

   The branch may stay as evidence or be deleted on GitHub; it is never read
   by the test. Note the run-log names and the alert lines in the status note
   or the gate review.

A dry run can also be done logged on, by hand, without the scheduler:

```powershell
.venv\Scripts\python scripts\livetest_week.py run --dry-run-branch livetest-dryrun --season 2026 --week 10 --force-fail push
.venv\Scripts\python scripts\livetest_week.py run --dry-run-branch livetest-dryrun --season 2026 --week 10
```

## 14. Registration command and least privilege (D-107)

The owner runs this, from the repository root, after sections 11 to 13:

```powershell
powershell -ExecutionPolicy Bypass -File scripts\livetest_schedule.ps1 -Register -LogonMode Password
```

Every part, in plain words:

| Part | Meaning |
| --- | --- |
| `powershell -ExecutionPolicy Bypass -File ...` | run this one script with Windows PowerShell even if script execution is otherwise restricted on the machine; nothing else is changed |
| `-Register` | actually register the tasks; without it the script only prints what it would do |
| `-LogonMode Password` | the default; the tasks run whether or not the user is logged on. Get-Credential asks for the Windows password once; Task Scheduler stores it in the system credential store (the same place as for any "run whether user is logged on or not" task). The script keeps it only in memory for the registration calls and writes it to no file, script, log or the console. If the account signs in with a Microsoft account and a PIN, the password is the Microsoft account password. An account without a password cannot run logged-off tasks; use `-LogonMode Interactive` then (runs only while logged on, locked screen is fine). |
| (implicit) run level Limited | the tasks run as the current user with no administrator rights; no elevation is asked for or used |
| (implicit) start only on AC power | the tasks do not start on battery; a run already going is not killed when unplugged |
| (implicit) `StartWhenAvailable` | a start missed because the machine was off or asleep runs as soon as it is available again |
| (implicit) `WakeToRun` | the task may wake the laptop from sleep (needs wake timers enabled, section 12); `-NoWake` turns this off |
| (implicit) `IgnoreNew`, 5-hour limit, 2 restarts | no overlapping runs; a run is stopped after 5 hours; a crashed task is restarted twice, 10 minutes apart |
| `-TaskFolder "\ffml\"` (default) | the tasks live in the `ffml` folder of Task Scheduler. If registration fails with "Access is denied" for the folder, use `-TaskFolder "\"` (the root folder); no administrator rights are needed either way |
| `-Weeks 10-18 -Season 2026` | only if `init` has not been run yet; otherwise the window comes from `data\livetest\config.json` |
| `-DailyCheckTime 15:00` (default) | the daily check's local time |

What the registered tasks can and cannot do:
- they run exactly `.venv\Scripts\python.exe scripts\livetest_week.py run|watchdog|check|score-week`
  from this repository, as the owner's user, with the owner's normal rights;
- git and gh use the owner's existing Credential Manager entries; nothing new is stored by this
  project;
- they push one branch (`livetest-2026`), never `main`, never with force;
- they never set `FFML_HOLDOUT_UNLOCK` and never write to the public predictions store;
- no password or token is written into `data\livetest\logs\`, `data\alerts\` or the task
  definitions (the run log records the command line and the configuration, which contain
  neither).

Check after registering:

```powershell
Get-ScheduledTask -TaskPath "\ffml\" | Format-Table TaskName, State
(Get-ScheduledTask -TaskPath "\ffml\" -TaskName "livetest-2026w10-run").Principal   # LogonType Password, RunLevel Limited
Get-ScheduledTaskInfo -TaskPath "\ffml\" -TaskName "livetest-2026w10-run"            # NextRunTime
```

To remove everything: `scripts\livetest_schedule.ps1 -Unregister`.

The weekly set also contains `\ffml\livetest-weekly-score` (Fridays at
`-ScoreTimeUtc`, default 13:00 UTC, repeating every 2 hours for 12 hours; the
trigger is in UTC, so a daylight-saving change does not move it), the Friday
scoring and sealing job of section 15.

## 15. Friday scoring and sealed hashes (pre-registration `sealed_interim`, D-116)

Interim results are sealed until the verdict. After a week's data cutoff, the
Evaluator's scorer (`python -m ffml.eval.livetest_score --week
2026wNN`) writes the verification status and the sealed metric parts, and the
file `data/livetest/scoring/sealed/2026wNN.sha256` with the SHA-256 of those
parts. The runner publishes only those hashes to the branch, so the parts
cannot be altered later; no metric, line result or ranking is rendered,
printed or committed before the final evaluation.

The data cutoff (D-116, A3) is 12:00 UTC on the first Friday at least 72
hours after the week's last kickoff. The scorer scores against the first
processed snapshot taken at or after it; stat corrections after the cutoff are
ignored, and the sealed week records the snapshot ids and a hash of the
outcome rows used.

The Friday job `livetest_week.py score-week` (task `\ffml\livetest-weekly-score`,
Fridays from 13:00 UTC, repeating every 2 h for 12 h):
1. takes **every** test week that has kicked off, has a commitment on the
   branch, is not missed and has no `sealed_hashes.json` yet, oldest first,
   and works through all of them in the same run (nothing to do: exit 0). A
   week that is still waiting (not final, or scorer exit 4) never holds a later
   week back (D-121); the run exits 1 while any week is waiting, so the next
   repetition retries it;
2. for each week, asks the scorer for the week's cutoff and exclusion point
   (`<pinned python> -m ffml.eval.livetest_score --data-cutoff 2026wNN`, which
   prints `data_cutoff_utc` and `exclusion_after_utc`; both are logged); before
   the cutoff it does nothing for that week; exit 4 means "not yet" (retried,
   no alert);
3. at or after the cutoff, refreshes the data with the pinned interpreter when
   **any** dataset the scorer pins (`dim_game`, `fact_player_game`,
   `fact_dst_game`, `restricted/fact_ecr_weekly`) lacks a snapshot taken at or
   after the pin point (the cutoff, or the exclusion point when that applies).
   A refresh that straddles 12:00 UTC therefore leaves the scoring only until
   the next 2-hour repetition, which refreshes again (at most twice per week
   and pin point), never for a week (R1). Skip with `--skip-refresh`. Then it
   checks that every game of the week is final in `dim_game`; if not, and the
   exclusion point has not passed, that week waits (no alert);
4. runs the scorer (`--week 2026wNN --weekly-sensitivity`) from the pinned
   worktree when no sealed file exists yet (also when a game is still not final
   after the exclusion point: the scorer then excludes it, D-121), at
   above-normal priority on
   Windows with a 360-minute timeout (A7); exit 4 is "not yet" (retried, no
   alert); any other non-zero exit or a missing sealed file raises
   `scoring_failure` (exit 1, retried);
5. `seal`: reads `2026wNN.sha256` (sha256sum lines, a JSON object or bare
   hashes), re-hashes any sealed part present locally, and commits
   `2026wNN/sealed_hashes.json` (the file's own SHA-256, its entries and its
   raw text) plus a regenerated `STATUS.md` with the `sealed` column, then
   pushes `livetest-2026` only and records in
   `data/livetest/runner/2026wNN/seal_record.json` the push, GitHub's view,
   the data cutoff (`data_cutoff_utc`), the exclusion point
   (`exclusion_after_utc`), the games still not final when it was scored
   (`not_final_at_scoring`) and the scorer's snapshot ids (from
   `data/livetest/scoring/weeks/2026wNN.json`).

**A game that never becomes final (D-121).** A week is scored once every game
is final. If a game is still not final 14 days after the week's data cutoff
(the `exclusion_after_utc` point), the week is scored without it: the scorer
excludes its player, team and defense rows, lists the excluded game ids in the
sealed record and the final report, and pins the first snapshots taken at or
after that point. A game the league reschedules into a later week stays in its
original week in the schedule and does not trigger the rule. No owner ruling
is needed; the runner simply keeps trying the week every Friday and scores it
after the exclusion point, while later weeks are scored as usual.

`seal` is idempotent: the same sealed file is never committed twice. It is
also immutable: a local sealed file that differs from the published hashes is
refused with exit 6 and an `integrity` alert; sealed hashes are never
overwritten, and never by hand.

By hand:

```powershell
.venv\Scripts\python scripts\livetest_week.py score-week                          # the Friday job, oldest unsealed week
.venv\Scripts\python scripts\livetest_week.py seal --season 2026 --week 10        # publish the hashes of an already scored week
.venv\Scripts\python scripts\livetest_week.py seal-check --season 2026 --week 10  # branch copy vs local sealed file and parts
git fetch origin livetest-2026; git show origin/livetest-2026:2026w10/sealed_hashes.json
```

`seal-check` exits 0 when the branch copy matches the local sealed file and
every locally present part hashes to its sealed value, 1 when the week is not
sealed yet (locally or on the branch), and 6 on any difference. The scoring
itself, the weekly reports and the final verdict are the Evaluator's; the
runner never reads a sealed part.

The final (Evaluator, once, after 2027 week 9) runs **from the main checkout,
never from the pinned worktree** (D-120): `python -m
ffml.eval.livetest_score --final`. **Exit 75 means the GitHub fetch of
`livetest-2026` failed** (D-116, A4): no verdict was recorded, no `Verdict:`
line and no final report were written, and the sealed files are untouched.
Check the network and `gh auth status`, then simply run the final again.

> **No pruning during the live test (D-118).** Never call `SnapshotStore.prune` or delete
> processed snapshots between the freeze and the final evaluation: each sealed week pins the
> first processed snapshot at or after its data cutoff, and a rescore must find it.

## 16. Re-initialising for 2027 weeks 1-9 (R3)

The window is 2026 weeks 10-18 and 2027 weeks 1-9 (the pre-registration's
`window`). The runner targets one season at a time (`init --season`), so it
is re-initialised for 2027 once the NFL publishes the 2027 schedule (usually
in May). The miss count, `STATUS.md` and the `status` footer already span the
whole window (QA-F30): misses from 2026 carry over, and the allowed count (2)
is still read from the pre-registration.

When the 2027 schedule is published (and before 2027 week 1):

1. **Bring the 2027 schedule into `dim_game`**, with the pinned code. The
   runner refreshes only inside a week's window, so before 2027 week 1 run one
   refresh by hand. Datasets nflverse has not published for 2027 yet are
   skipped as "not yet published" (D-122), not failed as gaps:

   ```powershell
   $env:PYTHONUTF8 = "1"
   $env:FFML_DATA_DIR = (Resolve-Path data).Path
   $env:FFML_ARTIFACTS_DIR = (Resolve-Path artifacts).Path
   $env:FFML_REPORTS_DIR = (Resolve-Path reports).Path
   $py = "..\ffml-livetest-code\.venv\Scripts\python.exe"
   & $py -m ffml.data.refresh --step ingest --seasons 2016-2027
   & $py -m ffml.data.refresh --step process --seasons 2016-2027
   ```

   Each step prints `REFRESH_RESULT {...}` with `"ok": true`; the ingest result
   lists the datasets in `not_yet_published` (for example play-by-play before
   the first 2027 games).
2. **Re-initialise** (keeps the old values under `history`):

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py init --force --season 2027 --weeks 1-9 `
     --code-worktree ..\ffml-livetest-code --python ..\ffml-livetest-code\.venv\Scripts\python.exe `
     --expected-commit <the pinned commit> --note "2027 re-initialisation (R3)"
   ```

   The printed JSON shows `"season": 2027`, `"weeks": [1, ..., 9]`, the
   pre-registration path and `"misses_allowed": 2`.
3. **Verify:**

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py plan            # 2027w01..2027w09 with kickoffs, none tentative except week 9 if unset
   .venv\Scripts\python scripts\livetest_week.py status          # rows 2026w10..2027w09; footer "misses: N (...); allowed 2"
   ```

   `status` must show the 2026 misses (if any) in the footer; `STATUS.md` on
   the branch shows "Window 2026w10 to 2027w09 (18 weeks)" at the next push.
4. **Re-export the tracked schedule** for the GitHub watchdog and commit it
   on `main` with a change-log row (section 17; the file keeps its name):

   ```powershell
   .venv\Scripts\python scripts\livetest_week.py plan --export scripts\livetest_schedule_2026.json
   ```

5. **Re-register the weekly tasks** (the owner; section 14, dry run first).
   The run tasks are generated from the 2027 plan; the Friday scoring task is
   unchanged.

From then on the weekly refresh works as in 2026: a 2027 file nflverse has not
published yet (for example play-by-play before week 1) is skipped as "not yet
published" (D-122); a missing file for any season at or before the newest
published one still fails the refresh as a gap.

## 17. The change log (D-115, D-120)

Every commit after X needs a row in `docs/status/livetest-changelog-2026.md`
before the first build that could use it, and before the final. A commit that
only appends change-log rows needs none (one parent, the change log its only
file, lines added and none deleted). The pre-build check (section 4) blocks a
build while a commit in the build's range has no row.

`scripts/livetest_changelog.py` lists and adds the safe rows:

```powershell
.venv\Scripts\python scripts\livetest_week.py status   # (optional) the state first
.venv\Scripts\python scripts\livetest_changelog.py --from <X>                  # commits in X..HEAD without a row
.venv\Scripts\python scripts\livetest_changelog.py --from <X> --append --commit --trailer "Co-Authored-By: ..."
```

- It adds one row per missing commit whose every path is outside the pinned
  build (`api/`, `web/`, `docs/`, `reports/`, `tests/`, `.github/`, `README.md`
  and the other allowed non-code files): date, commit, paths, the commit
  subject as reason, verification "paths outside the pinned build; no effect
  on commitments", verified by "orchestrator", first week "n/a".
- It refuses to write a row for a commit that touches `ffml/`, `scripts/`,
  `pyproject.toml` or `requirements.lock.txt` (a hand-written row with the
  Evaluator's verification is needed), for a merge commit, and for a commit
  that touches a frozen path or any path the pre-registration does not allow
  (the owner's ruling is needed, D-111; a row would not make it valid).
- It inserts rows after the table's last row and never edits an existing one.
  `--commit` commits the change log alone and confirms the commit is
  append-only, so it is exempt (D-115).
- Exit 0: nothing is missing, or every missing commit was rowed. Exit 1:
  commits remain that need a hand-written row or a ruling. Exit 2: an error
  (for example files already staged, which `--commit` refuses).

## 18. The app's season and week (D-124)

The real app (`FFML_APP_ENV=production`) shows the current season and week on
every page. It never takes them from the calendar or from a prediction run.

- `GET /meta/season` asks Sleeper's state endpoint (`/v1/state/nfl`, through
  the API's one Sleeper client and its rate limiter) and cross-checks the
  answer with the nflverse schedule (the latest raw `schedules` snapshot).
  When they agree the page says "From Sleeper, matching the nflverse
  schedule". When they disagree, or Sleeper cannot be reached within 5 s,
  the schedule's answer is shown and the page says so; the API log has a
  `season cross-check` or `Sleeper state unavailable` warning. The answer is
  reused for 3 minutes (1 minute when a source is missing).
- A `week` disagreement for a day or so after Monday night is expected: a
  refreshed schedule moves to the next week before Sleeper does. A lasting
  disagreement usually means the schedule snapshot is old: check
  `cross_check.schedule.snapshot_id` and run the refresh below.
- Projections, start/sit, waivers, trades and rankings stay on "no approved
  model" in production (D-099, D-124 assumption) and show the current season
  and week as context.

Refreshing the data the app reads (adds snapshots, never deletes any, D-118):

```powershell
$env:PYTHONUTF8 = "1"
.venv\Scripts\ffml refresh --step all     # ingest 2016 through the current season, Sleeper map, process, validate, features
```

Then restart the API (the player directory is loaded once per process) and
re-sync a Sleeper league through the API, for example with the API on port
8000:

```powershell
$id = (Invoke-RestMethod http://127.0.0.1:8000/leagues).leagues | Where-Object external_id -eq "1389750342958223360" | Select-Object -ExpandProperty id
Invoke-RestMethod -Method Post http://127.0.0.1:8000/leagues/$id/sync
```

Test-only replays. Historical replays (for example 2024 weeks 10 and 11) live
only in `data/predictions_private/replays_test_only/`. The public store
`data/predictions` holds no replay: production never reads replays, and the
API warns at startup if one is left there. In `FFML_APP_ENV=private_test`
every page and response that uses a replay is labeled "TEST DATA: 2024 week
11 replay, not approved, not a recommendation" next to the "not approved"
label. `ffml predict` in replay mode writes to the public store by default,
so move a new replay into the test-only folder (or pass its `root`) before
starting the API.
