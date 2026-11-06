# Live-test final rehearsal: the scorer's verdict path end to end on synthetic data

QA and Security Reviewer, 2026-11-06. Candidate frozen commit X = `4c35fea`
(`4c35feaf7abed2b69d9b69b8a159aacd75d988db`). Owner request under D-115: before X
is chosen, run the frozen scorer on a temporary copy with synthetic post-freeze
commits and synthetic data only, and show that the verdict path gives the right
result. Test: `tests/rehearsal/test_livetest_final_rehearsal.py`.

**Bottom line.** The verdict path gives the pre-registered result in every
scenario on the owner's list: APPROVED, NOT APPROVED naming the failing line,
exclusion of one or two missed weeks without extending the window, INVALID on a
third miss, refusal of tampered or unrecorded sealed files, INVALID for an
unlogged commit, an edited, deleted or template-row entry, a merge, a commit
mixing the log with another path, and a frozen-path commit. The report-only
sensitivity check cannot change the verdict in either direction; this was shown
with a forced disagreement, because the real 5,000-draw run did not complete
(see "Runs"). The change-log fix in X works: commits that only add change-log
lines need no entry, and the run is valid with them.

The rehearsal also found four problems outside the verdict logic. They are not
fixed here; the details are under "Findings".

* **QA-F27 (high).** The weekly scorer, run by the runner from the pinned
  worktree, leaves an untracked report there. The runner's pin check then
  refuses every later build: unattended, three misses and INVALID.
* **QA-F28 (medium).** A `MISSED.json` pushed by hand for a committed, sealed
  week is not cross-checked against the branch. Two such weeks can be excluded
  without the scorer noticing.
* **QA-F29 (medium).** The final's default worktree mode trusts uncommitted
  files in the commitments worktree as the branch record.
* **QA-F30 (low).** The runner's miss count restarts in 2027.

## Runs

| run | what | result | time |
| --- | --- | --- | --- |
| 1 | `FFML_REHEARSAL=1 FFML_REHEARSAL_QUICK=1 FFML_REHEARSAL_WORKERS=5 pytest -m slow tests/rehearsal` (all seven worlds, without the real 5,000-draw run; `FFML_REHEARSAL_QUICK=1` was the switch then, and this is now the default) | **25 passed, 1 skipped** (the 5,000-draw test) | 4,349 s (1 h 12 min) |
| 2 | the same with the real 5,000-draw final and four workers | **stopped by Claude Code for low system memory** at about 50 minutes, during the finals; not restarted. Before the stop it finished worlds B and C: again `Verdict: NOT APPROVED` with only L17 or only L07, and B's binding lines are identical to run 1's | n/a |
| 3 | the three scenarios added during run 1 (an unlogged fill commit, the final from the worktree pinned at X, a hand-pushed `MISSED.json`), on run 1's world A. They are INVALID or refused before any metric, so no bootstrap was needed | as expected (rows 4e, 6h and 6i below) | about 2 min |

Run 1 used the test as it stood when it started. Four tests were added during
it: rows 4e, 6h and 6i, and a check on the sealed JSON records. Run 3
exercised the same code paths, and the sealed-record assertions were checked
against run 1's data. A complete run of the current test file has not finished.
After the memory stop the defaults are three workers and no 5,000-draw job
(`FFML_REHEARSAL_5000=1` adds it). A 500-draw final over 18 weeks takes 5 to 9
minutes here, and the 5,000-draw check about 10 times as long.
**The real 5,000-draw CLI run has therefore not been completed.** Scenario 7 rests
on the forced-disagreement checks (7a, 7b), which show that the report-only
intervals cannot reach the verdict in either direction, and on the code path
(`final_evaluation` computes the verdict from the 500-draw results before the
sensitivity results, `ffml/eval/livetest_score.py:1308-1315`; `sens` feeds only the report tables).

## Results by scenario

| # | scenario (world) | setup | expected | actual (CLI `--final` unless noted) |
| --- | --- | --- | --- | --- |
| 1 | PASS (A) | 18 weeks committed and sealed; chain calibrated, beats every baseline | exactly one `Verdict: APPROVED` | `Verdict: APPROVED`; 22 of 22 lines pass; 64 of 64 integrity checks PASS; the same line and the same binding results on a second run |
| 2a | FAIL, coverage (B) | the calibrated chain's QB spread narrowed by 20% | `Verdict: NOT APPROVED`, L17 named | `Verdict: NOT APPROVED`; only L17 not passed: "tie-aware coverage 0.694: `chain_v2+cal` at QB is outside [0.75, 0.85]"; every integrity check PASS |
| 2b | FAIL, accuracy (C) | `prior_season+cal` at WR identical to the chain's calibrated WR rows | `Verdict: NOT APPROVED`, one accuracy line named | `Verdict: NOT APPROVED`; only L07 not passed: "does not beat `prior_season+cal` at WR on MAE (difference +0.00, 95% interval +0.00 to +0.00) ..." |
| 2c | determinism (B2) | world B rebuilt from scratch (new clone, remote, data) | identical results | `Verdict: NOT APPROVED`, L17 only; identical binding lines JSON, identical sealed part sha256 for all 18 weeks, identical commitment sha256 for all 18 weeks |
| 3a | 0 misses (A) | no `MISSED.json` | 18 weeks scored | "0 missed week(s) [], 2 allowed"; 18 weeks scored |
| 3b | 1 miss (D1) | 2026w12 never committed; the runner pushed `MISSED.json` (machine_offline) | excluded, no extension, verdict over 17 weeks | `Verdict: APPROVED`; "Weeks scored:" lists 17 weeks, then "excluded (miss rule): 2026w12"; "1 missed week(s) ['2026w12'], 2 allowed; excluded, the window is not extended" |
| 3c | 2 misses (D2) | 2026w12 and 2027w09 (the last week) missed | excluded, no extension, verdict over 16 weeks | `Verdict: APPROVED` over 16 weeks; "excluded (miss rule): 2026w12, 2027w09"; no 2027w10 anywhere |
| 3d | final before the last week is final (D2) | one 2027w09 game set to `scheduled` in a new dim_game snapshot; 2027w09 is the excluded miss | refused | exit 3, "refused: the final is refused until 2027w09 is final in dim_game (7 of 8 games final)"; no report written |
| 3e | 3 misses (D3) | 2026w12, 2026w16, 2027w03 missed | `Verdict: INVALID (...)` | `Verdict: INVALID (missed weeks (miss rule): 3 missed week(s) ['2026w12', '2026w16', '2027w03'], 2 allowed; more than allowed: INVALID; ...)`; no metric computed |
| 4a | weekly outputs (A, D1) | 18 verification reports, 18 `.sha256`, every scoring file on the branch | no metric value, no `Verdict` | no decimal number and no metric word in any of them; a real-CLI weekly run writes exactly `sealed/<week>.{json,parquet,sha256}` and `reports/livetest/<week>_verification.md` (plus a line in the synthetic access log); `FFML_REPORTS_DIR` untouched; stdout "sealed 2026w10: 26 of 26 checks pass; no metric is printed before the final (D-111)" |
| 4b | tampered sealed part (A) | one row dropped from `sealed/2026w14.parquet` | refused before reading | exit 3, "refused: sealed files do not match the branch record: 2026w14 sealed hashes: 2026w14.parquet sha256 differs from the branch record"; no report written |
| 4c | tampered sealed record (A) | `files_verified` changed in `sealed/2026w11.json` | refused | exit 3, "... 2026w11.json sha256 differs from the branch record" |
| 4d | missing branch hash record (A) | the branch as before the 2027w09 seal (`--no-worktree`) | refused or INVALID | exit 3, "... 2027w09 sealed hashes: scoring/2027w09.sha256 is not on the branch" |
| 4e | a hand-pushed `MISSED.json` for a committed, sealed week (A, run 3) | `2026w13/MISSED.json` pushed although the branch holds `2026w13/commitment.json` and `scoring/2026w13.sha256`; the week's local sealed files removed | should be refused | **not refused**: all 44 integrity checks and the 17 remaining seal checks pass, "17 scored, 1 missed", 2026w13 excluded (QA-F28) |
| 5 | missing change-log entry (A) | `scripts/x.py` committed, no entry | INVALID | `Verdict: INVALID (final evaluation change log: commits in X..HEAD without a change-log entry: ['bf97728278d1'])`; that is the only failing check |
| 6a | change-log-only commits (A) | HEAD = X → F → L1 (log F) → A (`scripts/`, logged) → L2 (log A) | valid | scenario 1 runs at this HEAD: "final evaluation change log: 2 logged commits; every commit in X..HEAD is logged" |
| 6b | an existing entry edited (A) | the reason cell of A's entry reworded | INVALID | `Verdict: INVALID (final evaluation change log: ... ['d6b3b47c1bfd'])`, the edit commit |
| 6c | an entry deleted (A) | A's row removed | INVALID | INVALID; both the deletion commit and A are uncovered again (`['801ca0e46fac', '9733a9fd5820']`) |
| 6d | the first entry written into the template's empty row (A) | from F, the `\| \| \| ...` row replaced by F's entry | INVALID (an edit) | INVALID naming that commit (`['edab6ef10126']`) |
| 6e | change log plus another path in one commit (A) | an appended entry and `docs/status/rehearsal-note.md` | INVALID | INVALID naming that commit (`['2ae6f4d61943']`) |
| 6f | merge commit (A) | a side branch appends an entry; `git merge --no-ff` | INVALID | INVALID naming only the merge (`['47679c7a01e9']`); the side branch's append-only commit is exempt |
| 6g | frozen-path commit (A) | `ffml/models/x.py` committed and logged | INVALID | `Verdict: INVALID (final evaluation commit: HEAD de9f35f6c936: Y ... descends from X 4c35feaf7abe but touches ['ffml/models/x.py']; working tree clean)`; the change-log check passes |
| 6h | the fill commit F with no change-log entry (A, run 3) | HEAD = F | INVALID | `Verdict: INVALID (final evaluation change log: commits in X..HEAD without a change-log entry: ['00b1a56a7419'])`, which is F |
| 6i | the final run from the worktree pinned at X (A, run 3) | `cwd` and `PYTHONPATH` = the pinned worktree, HEAD = X | INVALID: X's pre-registration has the placeholder | `Verdict: INVALID (frozen commit: pre-registered commit '<frozen commit>')`, the only failing check |
| 7a | 5,000-draw check forced to fail every line (A) | `sensitivity_fn` returns every line FAIL | verdict unchanged | `Verdict: APPROVED`; all 22 lines listed under "Lines whose PASS/FAIL would differ"; one `Verdict:` line |
| 7b | 5,000-draw check forced to pass every line (B) | `sensitivity_fn` returns every line PASS | verdict unchanged | `Verdict: NOT APPROVED`; only L17 listed as differing |
| 7c | the real 5,000-draw run (CLI default) | world A copy | `Verdict: APPROVED` and the sensitivity table | not completed (run 2 stopped for memory) |

The commit shas in the table are those of the rehearsal clones. They are
deterministic: fixed commit dates and identity give the same sha in every world.

### The integrity checks of the PASS final (scenario 1)

| check | result | detail |
| --- | --- | --- |
| approval lines | PASS | the pre-registration pins the 22 lines of this code |
| rules hash | PASS | rules_sha256 08fcf2116a7ab297, canonical rules block 08fcf2116a7ab297 |
| bootstrap and seeds | PASS | pre-registered {'n_boot': 500, 'seed': 0, 'metric_seed': 0}, the pinned spec uses {'n_boot': 500, 'seed': 0, 'metric_seed': 0} |
| frozen commit | PASS | pre-registered commit '4c35feaf7abed2b69d9b69b8a159aacd75d988db' |
| allowed paths | PASS | 16 allowed prefixes, 19 frozen prefixes |
| final evaluation commit | PASS | HEAD e01d1ed6894a: Y e01d1ed6894a descends from X 4c35feaf7abe; 3 allowed paths; working tree clean |
| final evaluation change log | PASS | 2 logged commits; every commit in X..HEAD is logged |
| window complete | PASS | 18 scored, 0 missed, 0 unaccounted [] |
| missed weeks (miss rule) | PASS | 0 missed week(s) [], 2 allowed; rule: D-106 as revisited ... |
| no week both scored and missed | PASS | [] |
| `<week>` commitment (18 rows) | PASS | each: commitment sha256, GitHub push time, kickoff, 107 to 123 files verified; the 26 weekly checks of every week passed (digest, every listed file hash, created before and pushed before the binding deadline, push record and branch commit, labels, model version, calibrator history, code commit at X, pins, stored-parts digest, calibrator strings, pinned files) |
| `<week>` sealed part (18 rows) | PASS | the part's sha256 unchanged since scoring |
| `<week>` sealed hashes (18 rows) | PASS | 2 sealed files match the branch record |

In the other worlds every check is PASS except as stated: in D1 and D2 the
"window complete" and "missed weeks" rows show the excluded weeks; in D3 "missed
weeks (miss rule)" FAILs; and in each change-log scenario exactly the named check
FAILs. The binding intervals of the PASS world include, for example, L07 MAE
[-1.910, -1.016] and L13 [+0.046, +0.136]. Coverage is QB 0.799, RB 0.800,
WR 0.799, TE 0.797, K 0.795, DST 0.788.

## How the rehearsal runs

Each world is built from nothing in a temporary directory:

| piece | what it is |
| --- | --- |
| `clone/` | `git clone --no-local --no-hardlinks --single-branch --branch main` of this repository, reset to X. Only `main` is fetched, so the real `livetest-2026` is never read |
| `remote.git/` | a temporary bare repository, the clone's only remote (`origin`); the runner creates `livetest-2026` there as an orphan branch |
| `ffml-livetest-commitments/` | the runner's commitments worktree, at the path both the runner and the scorer use by default (`<checkout>/../ffml-livetest-commitments`) |
| `clone/data/`, `clone/artifacts/` | `FFML_DATA_DIR` and `FFML_ARTIFACTS_DIR`: synthetic stores in the clone's gitignored directories, the production layout of the main checkout. `FFML_REPORTS_DIR` points at an empty `settings-reports/`, which stays empty |
| `ffml-livetest-code/` | (world A only) a worktree pinned at X, for the pinned-worktree reproduction |

The freeze is done as the real freeze will be done: commit F fills the
pre-registration's `commit` field with X, then commit L1 adds the change-log
entry for F. L1 only adds lines to the change log, so it needs no entry of its
own. For the rehearsal only, F also replaces `history_sources.stored_parts.digest`
with the digest of 97 synthetic stored parts written at the real paths. The real
digest pins 97 real files that the rehearsal may not read. That field is outside
`rules`, so `rules_sha256` and `lines_sha256` keep their pre-registered values.
In every world, `git diff --name-only X HEAD -- ffml pyproject.toml requirements.lock.txt`
is empty.

The season then runs week by week, in calendar order, with simulated time:

1. **Build** (`created_at` = K - 24 h 10 min, K = the week's first kickoff). The
   week's commitment is written with the builder's own helpers
   (`ffml.sim.livetest.write_commitment`, `file_records`, `digest_of`, `rel`,
   `stored_part`, `walkforward_dir`, `ffml.models.chain_v2.check_pins()`). It
   lists the synthetic `player_summary.parquet` (calibrated rows, `chain_v2`),
   `player_summary_raw.parquet` (`chain_v1`), the run manifest, a model artifact,
   a calibrator state, and the 97 stored history parts plus the walk-forward
   parts. The chain itself is not run.
2. **Push** (K - 24 h 05 min). The runner's own CLI entry
   (`livetest_week.main(["run", "--season", S, "--week", W, ...])`) finds the
   built commitment and verifies it. It then commits the commitment and the
   summary to `livetest-2026`, pushes to the bare remote, writes
   `push_record.json` through its own `external_record`, and runs
   `ffml livetest verify` (the builder's check) as a subprocess. A missed week gets
   no commitment; the runner's `record_misses` pushes its `MISSED.json` on the
   next run, or on an explicit run after a season's last kickoff.
3. **Weekly scoring** (the Tuesday after). The scorer writes the verification
   report and the sealed trio `data/livetest/scoring/sealed/<week>.parquet`,
   `.json` and `.sha256`.
4. **Seal.** The runner's `seal` command publishes `<week>/sealed_hashes.json` and
   the sealed file verbatim at `scoring/<week>.sha256`.

Before 2027 week 1 the runner is initialised again
(`init --season 2027 --weeks 1-9 --force`), because its configuration holds a
single season.

After the season, the world gets a post-freeze change on an allowed path
(`scripts/rehearsal_logged_change.py`, commit A) and its change-log entry
(commit L2). The valid HEAD is therefore X → F → L1 → A → L2. The final is
`python -m ffml.eval.livetest_score --final`, run from the clone.

**The clone's code, not the main checkout's.** The venv's editable install
points at the main checkout. So every scorer subprocess starts through a short
wrapper that imports `ffml`, asserts that `ffml.__file__` lies inside the clone,
and then runs `ffml.eval.livetest_score` as `__main__` with `runpy` (what
`python -m` does). The driver that runs the runner and the hooks refuses to
start unless both `ffml` and `scripts/livetest_week.py` come from the clone.
Each world's probe records `ffml.__file__`, `PROJECT_ROOT`, the default
commitments worktree and the data directories.

## The synthetic world

The world has sixteen invented teams (`S01`-`S16`) playing eight games a week.
Each team has one QB, two RBs, three WRs, two TEs, one K and the DST: 160
player-weeks a week, 2,880 over the window. `dim_game`, `fact_player_game`,
`fact_dst_game`, `dim_player` and the restricted `fact_ecr_weekly` hold 2024
(18 weeks), 2026 (18 weeks) and 2027 weeks 1-9, with every game final. 2024 and
2026 weeks 1-9 serve as history (position bounds and the consensus
rank-to-points curve). The kickoffs of 2026 weeks 10-18 follow the
pre-registered schedule as of 2026-11-04, with final, non-tentative times for
week 18. The 2027 weeks start on 2027-09-10 at 00:20 UTC.

* **Truth.** Each player has a skill, and each week a matchup effect. The
  outcome is log-normal around skill × matchup with a spread per position. In
  the window it is drawn with a stratified probability integral transform, so a
  calibrated model's coverage is about 0.80 by construction. The stat lines are
  built so that `ffml/scoring` PPR scores them exactly: QB passing touchdowns
  and rushing yards, RB receptions and rushing yards, WR and TE receptions and
  receiving yards, K field goals and extra points, DST sacks with 24 points
  allowed.
* **The chain** (`chain_v2`, calibrated) predicts the true distribution. Its raw
  rows (`chain_v1`) have the same median, with intervals 20% too narrow.
* **Comparators** (`data/backtests/livetest_comparators/predictions.parquet`):
  `rolling_avg_4`, `prior_season` and `rolling_median_8`, raw and `+cal`. They
  are centred on skill, with noise and without the matchup effect. Their
  outcome columns come from the scorer's own `harness_outcomes`.
* **Consensus** (synthetic `fact_ecr_weekly`): ranks from skill, half the
  matchup effect and noise. The scorer's `consensus_predictions` builds the
  consensus frame from them.
* **A release marker** `artifacts/holdout_release.json` (2025 released,
  `approved: false`). This is a synthetic copy of the production state. It is
  needed because the scorer reads actuals for `range(2016, season + 1)`, which
  includes 2025. `FFML_HOLDOUT_UNLOCK` is never set; the probe records
  `holdout_unlock: false`.

World variants:

* **B** narrows the calibrated chain's QB spread by 20%, so the QB intervals are
  too narrow.
* **C** makes `prior_season+cal` at WR identical to the chain's calibrated WR
  rows, so the chain cannot beat it there.
* **D1, D2 and D3** leave one, two or three weeks without a commitment.

## What ran through the real CLI, and the injection hooks

These ran through the real paths:

* every final: `python -m ffml.eval.livetest_score --final`, with and without
  `--no-sensitivity`, and with `--no-worktree` for the missing-record check;
* the weekly `--week` CLI for 2026w10 and 2027w01 in world D1;
* the runner's `init`, `run` (push and misses) and `seal`, through
  `livetest_week.main`;
* the builder's `ffml livetest verify` on every pushed commitment;
* the scorer's own data loaders (`load_dim_game`, `harness_outcomes`,
  `ensure_comparators`, `consensus_rows`, `_all_consensus`). The unit tests
  never run these: they are marked `# pragma: no cover`.

| hook | where | why |
| --- | --- | --- |
| weekly scoring through `score_week(..., n_boot=5, sensitivity_n_boot=5)` in-process, with every other argument wired exactly as `livetest_score.main` wires it (`_rehearsal_driver.weekly_hook`) | every week except D1's two CLI weeks | One weekly CLI run is two 500-draw bootstraps (21 to 31 minutes under the rehearsal's load); 18 weeks in 7 worlds would take about 20 CPU hours. The final never reads the weekly sealed metrics: it recomputes everything from the sealed parquet parts, and a part's bytes do not depend on the draw count. The hook's part equals the CLI's part byte for byte (D1, both CLI weeks) |
| `final_evaluation(sensitivity_fn=...)`, returning every line FAIL (world A) or PASS (world B) | scenario 7 only | A real 5,000-draw run cannot be made to disagree on demand. This is the same hook the unit tests use |
| runner `Deps`: simulated clock, no-op alert, no live schedule, simulated GitHub view; `core.parse_github_remote` names the bare remote a GitHub repository | every runner call | There is no GitHub in the rehearsal, so `push_activity.timestamp` is the simulated server time of the push (seconds after K - 24 h 05 min). No alert or network call may leave the machine |
| commitments built with synthetic rows and the builder's helpers, not `build-week` | every week | `build-week` needs the real feature store and fits LightGBM |
| comparators run pre-written with three baselines (`rolling_avg_3`, `rolling_ew_035` and `direct_lgbm` left out) | every world | Those three are information only and enter only the D-077 reference mean; each extra model adds about 12% to every bootstrap |

## Findings

Nothing under `ffml/` or any other frozen file was changed. Each finding gives
the file and line, a minimal reproduction (part of the rehearsal test) and the
impact on the verdict.

### QA-F27 (high, verdict-affecting through misses): weekly scoring leaves the pinned worktree dirty, and the runner then refuses every build

* **Where.** `ffml/eval/livetest_score.py:1038`: weekly `score_week` writes
  `PROJECT_ROOT / "reports" / "livetest" / <week>_verification.md`
  (`:1085`), and the CLI (`:1433`) never passes `reports_dir`.
  `scripts/livetest_week.py:1686-1690`: the Tuesday job runs the scorer with
  `cwd=cfg.code_cwd`, which is the pinned worktree, and with the pinned venv,
  whose editable install makes `PROJECT_ROOT` the pinned worktree.
  `.gitignore` does not ignore `reports/livetest/`.
  `scripts/livetest_core.py:781-786`: before every build, the runner's pin check
  (`verify_pin`, `scripts/livetest_week.py:455-469`) refuses a worktree whose
  `git status --porcelain --untracked-files=all` is not empty (exit 2, alert
  `build_failure`).
* **Reproduction** (`test_weekly_scorer_dirties_the_pinned_worktree`). In world
  A, a worktree is pinned at X. Before scoring, its status is empty and the pin
  check passes. Then 2026w10 is scored from it, wired exactly as `main()` wires
  it: the status shows `?? reports/livetest/2026w10_verification.md`, and
  `core.verify_code_pin` raises `PinError: worktree ... is not clean`.
* **Impact.** The first Tuesday scoring (2026w10, about 2026-11-17) would block
  the 2026w11 build two days later, and every later build, until someone deletes
  the file. Unattended, that is three missed weeks by 2026w13, and the test
  becomes `INVALID`. The verdict logic itself is unaffected. `.gitignore` is not
  an allowed path, so the cheapest fix (ignore `reports/livetest/`) must land
  before the freeze. Other fixes: the runner removes or moves the report after
  scoring (`scripts/`, allowed and logged), or the scorer writes weekly reports
  under `settings.reports_dir` (frozen file, so before the freeze only).

### QA-F28 (medium, verdict-affecting by manipulation): the final does not cross-check MISSED markers against the branch

* **Where.** `ffml/eval/livetest_score.py:1294`: `misses` is looked up only for
  weeks that have no local sealed record, so the overlap check at `:1213-1214`
  ("no week both scored and missed") can never fail. Nothing in the final checks
  that a week marked missed has no `commitment.json` and no
  `scoring/<week>.sha256` on the branch, and `verify_seals` (`:1299`) checks only
  the weeks that have a local record.
* **Reproduction** (`test_missed_marker_for_a_committed_sealed_week_is_not_cross_checked`).
  World A, after everything else: a `2026w13/MISSED.json` is pushed by hand,
  although the branch already holds `2026w13/commitment.json` and
  `scoring/2026w13.sha256`. With the local sealed files in place, the marker is
  silently ignored and the overlap check passes ("18 scored, 0 missed"). Then the
  three local sealed files of 2026w13 are moved away. All 44 of the final's
  integrity checks pass ("17 scored, 1 missed"; "1 missed week(s) ['2026w13'] ...
  excluded"), and so do the 17 remaining seal checks. Once every check passes,
  the final computes and prints `Verdict: APPROVED` or `Verdict: NOT APPROVED`
  over 17 weeks, with a week that was committed on time and sealed counted as an
  excluded miss. That is what this reproduction shows: on the quick run's world
  A, the checks were run directly without the 500-draw computation, after the
  memory stop. The test also runs the full CLI final.
* **Impact.** The sealed parts are hash commitments, not hidden data: the sealed
  JSON holds each week's line results in plain text on the machine. Anyone who
  can write to the data directory and push to the branch can therefore exclude up
  to two committed weeks after seeing them, and the scorer does not notice. The
  runner never writes such a marker (`record_misses` skips weeks with a commitment
  on the remote). A guard would need a frozen-file change before the freeze (the
  final refuses a missed week that has a commitment or a sealed record on the
  branch), or an Evaluator procedure at the final: list the branch and confirm
  every MISSED week has neither.

### QA-F29 (medium, integrity in depth): the default worktree mode trusts uncommitted files as "the branch record"

* **Where.** `ffml/eval/livetest_score.py:192-197`: `BranchSource.read` returns
  the commitments worktree's file when one exists, whether or not it is
  committed. The CLI uses the worktree by default (`--worktree`), and
  `verify_seals` compares the sealed files with that copy.
* **Reproduction** (`test_worktree_mode_trusts_uncommitted_record_copy`). In
  world A, the 2026w10 sealed part is altered (every `actual` + 5), the record's
  `part_sha256` and the local `.sha256` are rewritten, and the same text is
  written over the worktree's `scoring/2026w10.sha256` without committing.
  `verify_seals` in worktree mode accepts it, and so does the `sealed part`
  integrity check. The same call reading the branch through git only
  (`--no-worktree`) refuses it.
* **Impact.** No effect on an honest run. Against deliberate local tampering, the
  protection is only as strong as the working tree. Recommendation (procedure, no
  code change): run the final with `--no-worktree` after
  `git fetch origin livetest-2026`, and check that the remote head equals
  GitHub's.

### QA-F30 (low): the runner's single-season configuration restarts the miss count in 2027

* **Where.** `scripts/livetest_week.py:753` and `:768-770`: miss numbers and the
  `VALID`/`INVALID` text in `MISSED.json`, `STATUS.md` and the alerts count only
  the configured season. 2027 needs `init --season 2027 --weeks 1-9 --force`.
* **Reproduction.** World D3 misses 2026w12, 2026w16 and 2027w03. The scorer
  correctly returns `Verdict: INVALID (missed weeks ...: 3 ...)`, but the
  runner's marker for 2027w03 reads "VALID: miss 1 of 2 allowed".
* **Impact.** None on the verdict: the scorer counts every marker in the window.
  The operator's alerts understate the miss count in 2027, and a 2026w18 miss is
  marked only if a 2026-config run happens after its kickoff and before the
  re-init. Otherwise the final refuses with "weeks neither sealed nor marked
  MISSED". `scripts/` is an allowed path.

### Lower-severity notes

* `ffml/eval/livetest_score.py:1206`: `int(allowed or 1)` would treat a
  pre-registered `allowed: 0` as 1. This is latent (the value is 2).
* `ffml/eval/livetest_score.py:1107`: the final report's binding lines table
  reuses the weekly header "status (information only)". `Verdict: NOT APPROVED`
  does not name the failing line; the table does.
* Running the weekly scorer from the worktree pinned at X records
  `code commit: FAIL (the pre-registration has no frozen commit yet)` every week,
  because X's pre-registration still has the placeholder. The final re-evaluates
  that check (`:1218-1220`). The final itself must run where the commit field is
  filled and F is logged: run at X it is `INVALID (frozen commit ...)`, and run at
  F without L1 it is `INVALID (final evaluation change log ...)` (both
  reproduced). The runbook does not say this yet.
* The first change-log entry must be added as a new row. Writing it into the
  template's empty row is an edit, so that commit is not exempt and the final is
  INVALID (reproduced).
* Cost: on this world (8 models, 160 rows a week), one bootstrap draw over 18
  weeks takes about 0.6 s alone and about 1 s under the rehearsal's load. A
  500-draw final therefore takes 5 to 9 minutes, and the 5,000-draw check 50 to
  90 minutes on one core. The real run has 14 models and more rows, so expect
  longer. One weekly CLI scoring (two 500-draw bootstraps) took 21 to 31 minutes
  under load. The runner's Tuesday job adds a cumulative 5,000-draw bootstrap
  each week (`--weekly-sensitivity`), against a scorer timeout of 180 minutes
  (`scripts/livetest_week.py`, `scorer_timeout_minutes`). Scaled from these
  figures, that step could come close to the timeout late in the window. A week
  that times out is retried from scratch and never sealed, and the final refuses
  an unsealed week. It is worth timing on real data before Week 10 (the Evaluator
  estimated about an hour, review R4).

## Not covered

* **The real 5,000-draw run did not complete** (run 2, stopped for memory). The
  owner's item 7 is shown with the forced-disagreement hook in both directions
  and by the code path. Rerun with `FFML_REHEARSAL_5000=1` (about 1.5 hours more
  of one core) when the machine has room.
* **The Tuesday job `livetest_week.py score-week` was not run as a whole.** It
  always passes `--weekly-sensitivity`, which makes each week a cumulative
  5,000-draw bootstrap. The two steps it chains were run separately: the
  scorer's `--week` CLI (and the hook with `weekly_sensitivity=True`), then the
  runner's `seal`.
* **No real `build-week`.** The commitments carry synthetic rows; the build and
  identity checks belong to the ML Engineer's replay evidence.
* **The runner ran unpinned**, from the clone. The pinned worktree was used only
  for the QA-F27 reproduction and for row 6i.
* Weeks without a consensus scrape, tentative kickoff times, a kickoff moved
  earlier after the build, and late commitments under `<week>/late/` were not
  rehearsed; the unit tests cover the scorer's handling of the first three.

## Rerunning

```
FFML_REHEARSAL=1 .venv/Scripts/python -m pytest -m slow tests/rehearsal -q    # about 75 min, 3 workers
FFML_REHEARSAL=1 FFML_REHEARSAL_5000=1 ...                                    # plus the real 5,000-draw final
FFML_REHEARSAL_ONLY=A_pass,D3_three_misses ...                                # a subset of worlds
FFML_REHEARSAL_EVIDENCE=<file.json> ...                                       # the JSON evidence behind this report
FFML_REHEARSAL_COMMIT=<sha> ...                                               # rehearse another candidate X
```

Without `FFML_REHEARSAL=1` the module is skipped, so `pytest -q` in CI is
unchanged. The test needs the candidate commit in the checkout.

Files: `tests/rehearsal/test_livetest_final_rehearsal.py` (scenarios and
assertions), `tests/rehearsal/_rehearsal_scenarios.py` (pipelines),
`tests/rehearsal/_rehearsal_world.py` (synthetic world, git helpers, CLI wrapper),
`tests/rehearsal/_rehearsal_driver.py` (runner, hooks and reproductions inside
the clone's interpreter context).
