# Live-test final rehearsal: the scorer's verdict path end to end on synthetic data

QA and Security Reviewer, 2026-11-12. The final rehearsal ran at the frozen
commit **X = 4799913** (D-129). In run 7 each clone started from **Z = 765fb1a**,
the commit that holds the final pre-registration text (the D-127 window). Run 8
starts from Z = 34a6644, the fix for the test assertion defect, and confirms the
result (see "Run 8 confirmation").

* Test: `tests/rehearsal/test_livetest_final_rehearsal.py`.
* Evidence: `reports/evidence/rehearsal/`, with hashes in `MANIFEST.md`.
* Run 7's full evidence: `run7_X4799913.json`.

## Result

At X = 4799913, every scenario gave the pre-registered result. Run 7 ran all
ten worlds: 43 tests passed, 1 failed and 1 was skipped.

* **The failure is a defect in the test, not in the scorer** (QA-F33 below).
  The PASS-world test counts each per-week check kind. Its suffix match counted
  "<week> branch commitment" as a "<week> commitment" check, so it found 36,
  not 18. In the evidence, each of the five per-week check names appears
  exactly 18 times, and all 102 checks are PASS. The assertion was fixed to an
  exact name match in 34a6644 (D-131).
* The skip is the optional real 5,000-draw run (`FFML_REHEARSAL_5000=1`).
* **No scorer or runner defect was found at X.** The fixes for QA-F31 and
  QA-F32 hold at X, and the rechecks of QA-F27 to QA-F30 pass.
* The freeze procedure (D-120) ran in every clone:
  * a fill commit;
  * one append-only change-log commit Y;
  * the builds at Y;
  * the real `--prebuild-check`, which exits 0 at Y and 5 with one row removed.

## Scenario table at X (run 7)

### Verdicts

| scenario | world | expected | actual |
| --- | --- | --- | --- |
| PASS | A | `Verdict: APPROVED`, 22 of 22 lines pass, every integrity check PASS | `Verdict: APPROVED`, 22 of 22 lines pass, 102 checks all PASS. Each per-week check (sealed hashes, commitment, branch commitment, change log, sealed part) appears 18 times |
| PASS again, with the consensus archive and the fact tables moved away | A | the same verdict and lines: the final reads only sealed parts | `Verdict: APPROVED`, binding lines identical |
| QB intervals 20% too narrow | B | `Verdict: NOT APPROVED`, L17 the only line not passed | `Verdict: NOT APPROVED`, L17 only: "tie-aware coverage 0.694: `chain_v2+cal` at QB is outside [0.75, 0.85]" |
| B rebuilt from scratch | B2 | identical to B | identical binding lines, sealed parts, outcome hashes, pins and commitments |
| one accuracy line tied | C | `Verdict: NOT APPROVED`, L07 the only line not passed | `Verdict: NOT APPROVED`, L07 only: "`chain_v2+cal` does not beat `prior_season+cal` at WR" |

### Missed weeks

| scenario | world | expected | actual |
| --- | --- | --- | --- |
| no miss | A | 18 weeks scored | 18 weeks scored |
| one miss | D1 | 2026w12 excluded; 17 weeks; the window not extended | `Verdict: APPROVED` over 17 weeks, 2026w12 excluded, no 2027w11 |
| two misses, the second in the last week | D2 | 2026w12 and 2027w10 excluded; 16 weeks | `Verdict: APPROVED` over 16 weeks, both excluded |
| the final before the last week is final, when the last week is the miss | D2 | refused | exit 3: "the final is refused until 2027w10 is final in dim_game (7 of 8 games final)" |
| three misses across the two seasons | D3 | `INVALID` in the scorer and in the runner | scorer: `INVALID` ("3 missed week(s) ['2026w12', '2026w16', '2027w03'], 2 allowed"); runner `STATUS.md`: "Misses: 3 ... Test state: INVALID" |

### Sealed results

| scenario | world | expected | actual |
| --- | --- | --- | --- |
| weekly outputs | A, G | no metric in any weekly report, status file, sealed-hash file or branch file | no metric in A's 92 files or in G's |
| the sealed `.sha256` file | every world | lists the part, the week's consensus rows and the record | those three files in every sealed week |
| a tampered sealed part | A | refused before anything is read; nothing written | exit 3: "2026w14.parquet sha256 differs from the branch record"; no report, sealed directory unchanged |
| a tampered sealed record | A | refused | exit 3: "2026w11.json sha256 differs from the branch record" |
| a branch without the last seal record | A | refused | exit 3: "scoring/2027w10.sha256 is not on the branch" |
| the final's GitHub fetch fails | A | exit 75, no Verdict line, no report, sealed directory untouched; a retry gives the verdict | exit 75: "git fetch origin livetest-2026 failed"; no report; sealed directory unchanged; the retry is the PASS final above |
| a consistent local tampering that includes the worktree's copy of the branch record (QA-F29) | A | refused | exit 3: "local .sha256 differs from the branch copy; ..." |
| a stray MISSED marker on a committed week | A | listed with its week and time; verdict unchanged | `Verdict: APPROVED`; 2026w13 listed with the marker's commit time (2027-11-26T09:05Z) and its `created_at` (09:00) |
| the same week's local sealed files then removed (the QA-F28 attack) | A | refused; a committed week is never excluded | exit 3: "weeks with a commitment or sealed record on the branch but no local sealed record: ['2026w13']; such a week is never excluded as a miss" |

### The change log and the commit relation (world A)

| scenario | expected | actual |
| --- | --- | --- |
| the valid history X → Z → S → F → Y → A → L2, where Y and L2 only add change-log lines | valid | `Verdict: APPROVED`: "every commit in X..HEAD is logged" |
| the fill commit not logged | `INVALID`, naming it | `INVALID`: "commits in X..HEAD without a change-log entry: ['1e26ddd3dab1', 'c06653767028', '765fb1a8e55a']" (F, S, Z) |
| an unlogged change on `scripts/` | `INVALID`, naming it | `INVALID`, naming 095c52f80e0f |
| an edited entry | `INVALID` | `INVALID`, naming the edit commit |
| a deleted entry | `INVALID` | `INVALID`, naming the deletion and the commit it covered (2c7f087f7f84, 538a77fca8d8) |
| the first entry written into the template row | `INVALID` | `INVALID`, naming that commit |
| a change-log entry and another path in one commit | `INVALID` | `INVALID`, naming it |
| a merge | `INVALID` | `INVALID`, naming the merge |
| a logged change on a frozen path | `INVALID` | `INVALID`: "final evaluation commit: ... touches ['ffml/models/x.py']" |

### The freeze procedure (D-120), in every world

| step | expected | actual |
| --- | --- | --- |
| (a) the fill commit F sets `commit` to X, nothing else | one-line change | done; the synthetic stored-parts digest is in an earlier rehearsal-only commit S |
| (b) one append-only commit Y with a row for every commit in X..F: helper rows for the non-code commits, hand-written rows ("verified by Evaluator (rehearsal)") for the commits the helper refuses | every commit rowed; nothing needs a ruling | X..F holds Z, S and F, all outside the pinned code, so the helper rowed all three. No hand-written row was needed: X..Z touches only `docs/`. The hand-written path was smoke-tested on 12e2faa..765e287, where the helper refused 071c3a4 (`scripts/`) and the row was written by hand |
| (c) builds at Y | every sealed week's `code_commit` is Y (worlds other than P) | yes (Y = 643d27d572ef in every world) |
| (d) `--prebuild-check --from "" --to Y` | exit 0 | exit 0 in all ten worlds |
| (d) the same commit Y with one row removed | exit 5, `uncovered` = that commit | exit 5 in all ten worlds, `uncovered = ['765fb1a8e55a...']` |
| the final from the worktree pinned at Y | the same verdict as from HEAD | `Verdict: APPROVED`, binding lines identical. QA-F27: the worktree was clean before and after weekly scoring from it, and the runner's pin check passed both times |

### Blocked builds (world P)

| scenario | expected | actual |
| --- | --- | --- |
| an unlogged change on `ffml/data/`, logged before the deadline | blocked with alert `prebuild_blocked`, retried; built once the entry is logged; not a miss | attempts at 01:00 and 01:30 blocked (runner exit 2, `--prebuild-check` exit 5, two `prebuild_blocked` alerts). Built at 02:00 after the entry; the week is scored |
| an unlogged change left past the deadline | a miss with detail "pre-build check blocked" | three attempts blocked, the last at kickoff - 35 min. 2026w13 `MISSED`, category `technical_failure`, detail "pre-build check blocked"; excluded in the final |
| a logged change on a frozen path | blocked | blocked (`--prebuild-check` exit 5); built after the change was reset |
| the final | `Verdict: APPROVED`, 2026w13 excluded | `Verdict: APPROVED`, 2026w13 excluded |

### The data cutoff, pins and stat corrections (world E)

| scenario | expected | actual |
| --- | --- | --- |
| `--data-cutoff 2027w01` before the 2027 schedule exists (QA-F32) | exit 4 | exit 4 |
| `--data-cutoff 2026w18` | exit 0, 2027-01-15T12:00Z | exit 0, 2027-01-15T12:00Z |
| `--week 2027w01` at its cutoff - 1 h | exit 4 | exit 4: "the data cutoff for 2027w01 is 2027-09-17T12:00:00+00:00" |
| `--week 2027w01` at its cutoff + 10 min, before any snapshot after the cutoff | exit 4 | exit 4: "no snapshot at or after the data cutoff ... yet" |
| the pins | the first snapshot at or after each cutoff, in every week | yes; for example, 2027w01 is pinned to 20270917T123000Z |
| corrections after the cutoff | never reach the sealed weeks or the final | E's outcome hashes and parts equal A's in all 18 weeks; `Verdict: APPROVED` with A's binding lines. The corrected LATEST data would give another 2027w01 outcome hash (342fe03c... against the sealed 259754fd...) |
| the rescore of 2027w01 through the CLI after its correction | reuses the recorded pins | pins, outcome hash and part unchanged |

### D-121: a game not final (world G)

| scenario | expected | actual |
| --- | --- | --- |
| 2026w14 with a game never final, scored on the Friday after the cutoff | exit 4 | exit 4: "2026w14 is not final in the pinned dim_game (7 of 8 games); a game still not final at 2027-01-01T12:00:00+00:00 is excluded" |
| the real `--week 2026w14` a week later | exit 4, nothing written | exit 4, nothing written |
| the runner's `score-week` on 2026-12-25 | 2026w14 waits; 2026w15 is scored and sealed | 2026w14 "week not final yet"; 2026w15 scored and sealed; no alert |
| the runner's `score-week` after the cutoff plus 14 days | 2026w14 scored without the game | 2026w14 scored and sealed without `2026_14_S04_S13` (teams S04 and S13, "scheduled" in the pinned `dim_game`), pins from 2027-01-01T12:00Z (snapshot 20270101T123000Z); 2026w16 sealed in the same run |
| 2027w02 with a game that becomes final 4 days after the cutoff | waits, then is scored from the first snapshot in which the game is final, with nothing excluded | exit 4, then scored with pins from 2027-09-29T23:05Z, nothing excluded, 26 of 26 checks |
| the final | `Verdict: APPROVED`; "2026w14 games excluded (D-121)" PASS; the game listed | `Verdict: APPROVED` over 18 weeks; that check PASS; the "Games excluded (D-121)" section lists the game. Outcome hashes equal A's in every week but 2026w14 |
| the comparators run extended to 2026w14 and to 2026w17 (QA-F31) | no refusal; the game's rows left out | both extended. The actuals hold 0 rows of the game and the week's other 140 rows, and run up to the week scored |

### The report-only sensitivity check, and the weekly CLI

| scenario | world | expected | actual |
| --- | --- | --- | --- |
| the 5,000-draw check forced to fail every line | A | `Verdict: APPROVED`; the 22 differing lines listed | `Verdict: APPROVED`, 22 listed |
| the check forced to pass every line | B | `Verdict: NOT APPROVED`; L17 listed | `Verdict: NOT APPROVED`, L17 listed |
| the real 5,000-draw run | A | report only | not run (optional) |
| the hook and the real `--week` CLI on the same week | D1 (2026w11) | the same part, consensus part and outcome hash | equal. The CLI wrote only the five sealed and status files and the verification report under `FFML_REPORTS_DIR`, nothing in the checkout |

## Defects and findings (not fixed by QA; frozen files untouched)

| id | severity | finding | where | repro | impact on the verdict | status |
| --- | --- | --- | --- | --- | --- | --- |
| QA-F33 | test only | The PASS-world test counted each per-week check kind with a suffix match, so `" commitment"` also matched "<week> branch commitment" and counted 36. My defect: the assertion dates from 7d5749b | `tests/rehearsal/test_livetest_final_rehearsal.py:190` at 765fb1a | run 7, `test_pass_world_is_approved`: "AssertionError: commitment / assert 36 == 18" | none: the scorer wrote each per-week check exactly 18 times, all PASS | fixed in 34a6644 (exact name match, D-131); run 8 confirms |
| QA-F31 | high | `ensure_comparators` ignored the D-121 exclusion. A week with an excluded game, and every later week of its season, were refused whenever the comparators run had to be extended | `ffml/eval/livetest_score.py:846-863` at 071c3a4 | world G's comparators probe | at 071c3a4, those weeks could never be sealed and the final could never run | fixed in 4799913; rechecked above |
| QA-F32 | low | `--data-cutoff` for a season with no `dim_game` partition yet raised `FileNotFoundError` (exit 1), not exit 4 | `ffml/eval/livetest_score.py:2115` at 071c3a4 | world E, `--data-cutoff 2027w01` before the 2027 schedule | none | fixed in 4799913; rechecked above |

Rechecks that hold at X:

* QA-F27: reports go to `FFML_REPORTS_DIR`, and the pinned worktree stays clean.
* QA-F28: a committed week is never excluded, and stray markers are flagged.
* QA-F29: the final reads only the fetched branch.
* QA-F30: the runner's miss count spans both seasons.

**Review of 34a6644.** The fix is right and weakens nothing.

* The test still counts distinct check names: the names are a set, as before.
* Each of the five kinds must now match exactly "<week> <kind>" 18 times.
  Before, the suffix match for "commitment" also caught "branch commitment".
* A possible tightening, after run 8: compare the set of week keys with the
  window, instead of comparing the count with 18.
* The similar suffix match at `:395` ("change log") only checks that each match
  is PASS and counts nothing, so it cannot over-count.

Observations (not defects):

* A rescore after sealing rewrites the week's sealed JSON (`scored_at`). The
  final then refuses the week until the original sealed files are put back
  (world E: "local .sha256 differs from the branch copy").
* `--prebuild-check` blocks when `--from` is not an ancestor of `--to`.

## Runs

| run | X | clones start from | workers | when (UTC) | result |
| --- | --- | --- | --- | --- | --- |
| 8 | 4799913 | 34a6644 | 2 | 2026-11-12, running at the time of writing | see "Run 8 confirmation" |
| **7** | **4799913** | **765fb1a** | 2 | 2026-11-11 22:16 to 2026-11-12 00:23 (pytest 7,578 s, 2 h 06 min) | all ten worlds; 43 passed, 1 failed (QA-F33), 1 skipped |
| 6 | 4799913 | 765fb1a | 2 | 2026-11-11 17:43 to 20:20 | stopped by the owner's pause (below); nine worlds finished, B2 only started; 70 streamed records archived, not used for the result |
| 5 | 071c3a4 | 071c3a4 | 3 | 2026-11-10 18:48 to about 19:09 | stopped by Claude Code for low memory; no final ran; found QA-F31 (by code reading) and QA-F32 |
| earlier | bd3bf21, 2872d13 | the same | 3 | 2026-11-06 to 11-07 | stopped for pauses and memory; every final that completed matched |

World times in run 7, in seconds:

| A | G | E | D1 | P | B | C | D2 | B2 | D3 |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| 3,308 | 3,251 | 1,977 | 1,320 | 1,038 | 1,002 | 772 | 756 | 616 | 523 |

**Run 6 and the pause.**

* At 20:20 UTC, on the owner's pause, the coordinator suspended run 6's
  processes with `NtSuspendProcess`.
* The agent waiting on them stalled, and the processes were later killed.
* The owner then ruled that agent processes are never suspended again; a pause
  stops at a clean checkpoint instead (D-130).
* Run 6's streamed results (`run6_X4799913_partial.jsonl`) are archived with
  their hash. They cannot drive the suite's checks, so the coordinator reran all
  ten worlds as run 7, with the same settings.
* All 29 final and refusal records of run 6 recur in run 7 with identical
  verdicts and lines.

## Run 8 confirmation

> **TO BE FILLED IN WHEN RUN 8 FINISHES.**
>
> * Settings: X = 4799913, clones from Z = 34a6644, two workers, evidence
>   `evidence_run8.json`.
> * Expected: 44 passed and 1 skipped (the 5,000-draw run); every final and
>   refusal record identical to run 7's; the freeze steps as in run 7, with the
>   commits between X and Z now including 34a6644.
> * Actual: _pending_.

## How the rehearsal runs

Each world is built from nothing in a temporary directory:

| piece | what it is |
| --- | --- |
| `clone/` | `git clone --no-local --no-hardlinks --single-branch --branch main` of this repository, reset to Z (`FFML_REHEARSAL_BASE`), a descendant of X (`FFML_REHEARSAL_COMMIT`). Only `main` is fetched, so the real `livetest-2026` is never read |
| `remote.git/` | a temporary bare repository, the clone's only remote (`origin`); the runner creates `livetest-2026` there |
| `ffml-livetest-commitments/` | the runner's commitments worktree, at the default path that the runner and the scorer both use |
| `clone/data/`, `clone/artifacts/` | `FFML_DATA_DIR` and `FFML_ARTIFACTS_DIR`: synthetic stores in the clone's gitignored directories (the production layout) |
| `settings-reports/` | `FFML_REPORTS_DIR`: the weekly verification reports and the final report go to `<FFML_REPORTS_DIR>/livetest/` |
| `ffml-livetest-code/` | (world A only) a worktree pinned at Y, for the QA-F27 recheck and the final from the pinned worktree |

**The window comes from the pre-registration.** The harness reads `window` from
the pre-registration at Z: 2026 weeks 11-18 and 2027 weeks 1-10 (D-127). The
runner is initialised with those spans. Every week a scenario names is asserted
to lie inside the window.

**The freeze** follows D-120, from Z:

* S, rehearsal only: the pre-registration's
  `history_sources.stored_parts.digest` set to the digest of the 97 synthetic
  stored parts. The real digest pins 97 real files the rehearsal may not read.
  The field is outside `rules`, so `rules_sha256` and `lines_sha256` keep their
  pre-registered values.
* F: the fill commit, with `commit` set to X.
* Y: one change-log commit that only adds lines, with a row for every commit in
  X..F. The rows come from `scripts/livetest_changelog.py --append`, plus
  hand-written rows for any commit it refuses.
* The checks of step (d).

No frozen path differs between X and HEAD, under the pre-registration's own path
policy.

**A simulated calendar.** The processed and restricted stores are written as
real snapshot directories. Their ids are the simulated UTC time of each
refresh:

* a first snapshot on 2026-11-10;
* a pre-build refresh one hour before each build;
* a Friday refresh 30 minutes after each week's data cutoff (12:00 UTC on the
  first Friday at least 72 hours after the last kickoff).

Each snapshot holds the world as of its time:

* a game is final 4 hours after kickoff, except world G's two games;
* consensus ranks appear once they are scraped, the day before the week;
* the 2027 schedule appears only from 2027-05-15.

**The season runs in calendar order**, as events sorted by simulated time:

1. The pre-build refresh.
2. The runner's own CLI (`run`) at K - 24 h 15 min, where K is the week's first
   kickoff. The runner:
   * records any misses;
   * runs the clone's real `--prebuild-check`;
   * builds (through the build hook below), verifies, commits and pushes to
     the bare remote;
   * writes `push_record.json` and runs `ffml livetest verify`.

   A blocked build retries at the times the scenario gives.
3. The Friday refresh after the data cutoff.
4. Weekly scoring at 13:00 UTC on that Friday:
   * the real `--week` CLI for D1's 2026w11 and E's 2027w01;
   * the runner's own `score-week` for G's 2026w14 to w16;
   * the hook otherwise.
5. The runner's `seal`, which pushes `<week>/sealed_hashes.json` and the sealed
   file verbatim at `scoring/<week>.sha256`.

Before 2027 week 1 the runner is initialised for 2027; the miss count spans
both seasons (QA-F30).

After the season comes a logged post-freeze change on an allowed path
(`scripts/rehearsal_logged_change.py`, commit A) and its change-log entry
(commit L2). The builds ran at Y, so the scorer's per-week check runs every
week: "every commit between X and the build commit was in the change log at the
build commit" (D-116 A2). The final evaluations run at HEAD = L2. World G's
final runs at a simulated time after the window, because its excluded game is
never final.

**The clone's code, not the main checkout's.** Every scorer subprocess,
including the ones the runner starts, runs through a short wrapper:

* it imports `ffml` and asserts that `ffml.__file__` and
  `ffml.eval.livetest_score` lie inside the clone (the venv's editable install
  points at the main checkout);
* it then calls `livetest_score.main(argv)`.

The driver that runs the runner and the hooks refuses to start unless `ffml` and
`scripts/livetest_week.py` come from the clone.

**Streamed results.** Every final, refusal and tagged step is appended to
`<evidence>.partial.jsonl` as soon as it exists, so a stopped run keeps its
results.

## The synthetic world

Sixteen invented teams (`S01`-`S16`) play eight games a week: Thursday night,
Sunday early, late and night, and Monday night. Each team has one QB, two RBs,
three WRs, two TEs, one K and the DST: 160 player-weeks a week.

* **Seasons.** 2024 (history), 2026 weeks 1-18 and 2027 weeks 1-10. The 2026
  window kickoffs follow the pre-registered schedule. The 2027 weeks start on
  2027-09-10.
* **Truth.** Each player has a skill, and each week a matchup effect. The
  outcome is log-normal around skill × matchup. In the window it is drawn with
  a stratified probability integral transform, so a calibrated model's coverage
  is about 0.80. The stat lines score exactly to the outcome under PPR.
* **The chain.** `chain_v2` predicts the true distribution. Its raw rows
  (`chain_v1`) have intervals 20% too narrow.
* **Comparators.** A pre-written `livetest_comparators` run holds
  `rolling_avg_4`, `prior_season` and `rolling_median_8`, raw and `+cal`. They
  are centred on skill, with noise and without the matchup effect.
* **Consensus.** Synthetic `fact_ecr_weekly` ranks built from skill, half the
  matchup effect and noise.
* **A release marker.** `artifacts/holdout_release.json` (2025 released,
  `approved: false`) is a synthetic copy of the production state.
  `FFML_HOLDOUT_UNLOCK` is never set.

World variants:

* **B** narrows the calibrated chain's QB spread by 20%; **B2** is B rebuilt
  from scratch.
* **C** makes `prior_season+cal` at WR identical to the chain at WR.
* **D1, D2 and D3** miss one, two and three weeks.
* **E** is A's truth with stat corrections:
  * every window week has preliminary values (40% of rows ±0.7 points, ±1 for
    K and DST) in every snapshot before its cutoff;
  * the 2027 weeks have corrected values (30% of rows ±1.4 points) in every
    snapshot from a week after the cutoff.
* **G** has a game that is never final (2026w14, `2026_14_S04_S13`) and a game
  that becomes final 4 days after its week's cutoff (2027w02).
* **P** has the blocked builds.

## What ran through the real code, and the injection hooks

These ran through the real paths:

* every final, including the failed fetch, the missing record and the run from
  the pinned worktree;
* the weekly `--week` CLI for D1's 2026w11, E's 2027w01, the runner's scoring of
  G's 2026w14 to w16, the rescore of 2027w01, and four exit-4 calls;
* `--data-cutoff`, and every `--prebuild-check`;
* the change-log helper `scripts/livetest_changelog.py`;
* the runner's `init`, `run` (build, push, misses), `score-week` and `seal`;
* `ffml livetest verify` on every pushed commitment;
* the scorer's own data loaders, with pinned stores.

| hook | where | why |
| --- | --- | --- |
| `Deps.run`: `ffml livetest build-week` writes a synthetic commitment with the builder's own helpers (`write_commitment`, `file_records`, `digest_of`, `rel`, `code_state`, `chain_v2.check_pins()`). Every scorer command goes through the wrapper. Every other command runs unchanged | every runner call | the real build needs the feature store and LightGBM. The pre-build check must run inside the runner's own build step, so the runner has to build |
| the runner's scorer call without `--weekly-sensitivity` | G's runner scoring | the weekly 5,000-draw step would take hours here; world A covers the weekly sensitivity through the hook, at 5 draws |
| runner `Deps`: a simulated clock, a recording alert (nothing sent), no live schedule, a simulated GitHub view. `core.parse_github_remote` names the bare remote a GitHub repository | every runner call | there is no GitHub; `push_activity.timestamp` is the simulated server time of the push |
| `livetest_score.utcnow` set to the simulated time in the wrapper (`FFML_REHEARSAL_NOW`) | every scorer CLI call with a time | the scorer's documented test seam: `--week` compares it with the week's data cutoff |
| snapshot ids at simulated times (`ffml.data.store._utc_now` patched while the synthetic data are written) | every refresh | the scorer pins snapshots by the time in their id |
| weekly scoring in-process, wired as `main()` wires `--week`, but with 5 draws (`n_boot=5`, `sensitivity_n_boot=5`): `plan_week` with the recorded or chosen pins, `ensure_comparators` with the D-121 exclusions and the time, the pinned stores, the week's consensus rows, `score_week` with `excluded_games` and `pin_from` | the weeks not scored by the CLI or the runner | a real weekly CLI run is two 500-draw bootstraps. The final never reads the weekly metrics. The part, consensus part and outcome hash equal the CLI's byte for byte (D1, checked) |
| `final_evaluation(sensitivity_fn=...)` with every line forced to FAIL (A) or PASS (B) | the sensitivity scenarios | a real 5,000-draw run cannot be made to disagree on demand |
| comparators pre-written with three baselines; G's extension probe calls `ensure_comparators` in-process with a stub runner | every world; G | `rolling_avg_3`, `rolling_ew_035` and `direct_lgbm` are information only; the real `run_comparators` is the full walk-forward |

## Limitations

* **The real comparators extension (`run_comparators`) is not rehearsed.** G's
  probe checks the D-121 logic of `ensure_comparators` with a stub runner. The
  real path runs on real data at two points: the comparators pre-warm right
  after the freeze, and the Week 10 real-data rehearsal on 2026-11-20 (D-127).
* **The runner itself is not pinned.** It is not started with
  `--code-worktree`/`--expected-commit`. The builds run in the clone at HEAD = Y.
  The worktree pinned at Y is checked separately with the runner's
  `verify_code_pin`, and the final is run from it. A pinned runner's children
  would import `ffml` from the worktree, and the scorer wrapper only accepts
  the clone's `ffml`.
* **The real 5,000-draw final through the CLI default was not run** (optional,
  `FFML_REHEARSAL_5000=1`).
* **Not covered here:**
  * the ingest of a season that is not yet published (D-122, D-126), which needs
    network fetches; the Data Engineer's unit tests cover it;
  * the replay store (12e2faa, D-124), because the rehearsal runs no replay.
* Synthetic data only. Nothing was read from or written to the real `data/`,
  `artifacts/`, `livetest-2026`, registry or access log; nothing was pushed;
  `FFML_HOLDOUT_UNLOCK` was never set.

## Commits

* **X = 4799913**: the frozen commit (D-129).
* **Z = 765fb1a**: run 7's base. **34a6644**: run 8's base, with the QA-F33 fix
  (D-131).
* **Rehearsal harness:** 7d5749b, d93b1d7, 04a2c42, cf0b629 and eabb201.
* **Evidence archive:** fac6fcb (run 6) and 34a6644 (run 7).

## The first rehearsal (D-115, X = 4c35fea, commit 1f2baf9)

Seven worlds, without the 5,000-draw run, gave 25 passed and 1 skipped in
1 h 12 min:

* PASS gave `Verdict: APPROVED`, deterministically.
* L17 and L07 were each the only line not passed in their world.
* One or two misses were excluded without extending the window; a third gave
  `INVALID`.
* Tampered sealed files were refused.
* Each post-freeze commit case gave `INVALID`.
* The report-only check never changed the verdict.

It found QA-F27 to QA-F30, all fixed before this rehearsal.
