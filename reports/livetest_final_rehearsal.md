# Live-test final rehearsal: the scorer's verdict path end to end on synthetic data

QA and Security Reviewer. **Work in progress (2026-11-11).** The full run at the
final candidate has not run yet. This version keeps the evidence of the runs that
were stopped before they finished. The first rehearsal (D-115, 2026-11-06,
X = 4c35fea, commit 1f2baf9) is summarised at the end. Test:
`tests/rehearsal/test_livetest_final_rehearsal.py`.

Next: one full run, with two workers (D-128):

* each clone starts from the commit Z that holds the D-127 window (2026 w11-18
  plus 2027 w1-10);
* the frozen commit filled in is the X of D-128, the commit with the QA-F31 and
  QA-F32 fixes.

The harness now reads its window from the pre-registration (commit 04a2c42).

## Evidence from the stopped runs

The evidence JSON is written only when every world has finished. The stopped
runs before D-121 therefore left only what was read from their saved final
reports during the run. Since commit d93b1d7, every final, refusal and tagged
step is also appended to `<evidence>.partial.jsonl` as soon as it exists.

**Runs at bd3bf21 (QA-5.2) and at 2872d13 (D-118).** Each was stopped for the
owner's pauses or for low memory before all worlds finished. Every final that
completed gave the expected result:

| world | final | result |
| --- | --- | --- |
| A | PASS final | `Verdict: APPROVED` |
| A | the same final with the consensus archive and the fact tables moved away | `Verdict: APPROVED`, identical lines |
| A | after a stray MISSED marker on a committed week | `Verdict: APPROVED`, the marker listed with its week and time |
| A | from the worktree pinned at X | `INVALID` (the frozen commit) |
| A | the eight post-freeze commit cases | `INVALID` in each, naming the commit or path |
| B | QB intervals 20% too narrow | `Verdict: NOT APPROVED`, L17 the only line not passed |
| C | one accuracy line tied | `Verdict: NOT APPROVED`, L07 the only line not passed |
| D1 | one miss | `Verdict: APPROVED`, 2026w12 excluded |
| D2 | two misses | `Verdict: APPROVED`, 2026w12 and 2027w09 excluded |
| D3 | three misses across the seasons | `INVALID` |
| E | stat corrections after the cutoff | `Verdict: APPROVED` |
| P | blocked builds | `Verdict: APPROVED`, 2026w13 excluded |

**Run 5 at X = 071c3a4 (D-125), 2026-11-10.** It started at 18:47:50 UTC with
three workers. About 20 to 25 minutes in, Claude Code stopped it for low system
memory. Only worlds A, G and E had started. No final ran.

What had run by then:

* A had sealed 17 of its 18 weeks, E 9 and G 4.
* Every sealed week passed all its checks, with `bootstrap_frames = lines`.

Streamed results:

| world / step | expected | actual |
| --- | --- | --- |
| G: 2026w14 at its cutoff + 1 h, one game never final | exit 4 | exit 4: "2026w14 is not final in the pinned dim_game (7 of 8 games); a game still not final at 2027-01-01T12:00:00+00:00 is excluded and the week scored without it (D-121)" |
| G: the real `--week 2026w14` at its cutoff + 7 days | exit 4, nothing written | exit 4, the same message, nothing written |
| G: the runner's `score-week` at 2026-12-25 13:00, read from the runner's log | 2026w14 waits, 2026w15 is scored | 2026w14 logged as "week not final yet", with the game `2026_14_S04_S13` and the exclusion point 2027-01-01T12:00Z; the runner went on to score 2026w15 (still running at the stop) |
| E: `--data-cutoff 2027w01` before the 2027 schedule | exit 4 | **exit 1 with a traceback** (QA-F32) |
| E: `--data-cutoff 2026w18` | exit 0, cutoff 2027-01-15T12:00Z | exit 0 |
| E: `--week 2027w01` at its cutoff - 1 h | exit 4 | exit 4: "the data cutoff for 2027w01 is 2027-09-17T12:00:00+00:00" |
| E: `--week 2027w01` at its cutoff + 10 min | exit 4 | exit 4: "no snapshot at or after the data cutoff ... yet" for the four pinned datasets |

## How the rehearsal runs

Each world is built from nothing in a temporary directory:

| piece | what it is |
| --- | --- |
| `clone/` | `git clone --no-local --no-hardlinks --single-branch --branch main` of this repository, reset to X. Only `main` is fetched, so the real `livetest-2026` is never read |
| `remote.git/` | a temporary bare repository, the clone's only remote (`origin`); the runner creates `livetest-2026` there |
| `ffml-livetest-commitments/` | the runner's commitments worktree, at the default path both the runner and the scorer use |
| `clone/data/`, `clone/artifacts/` | `FFML_DATA_DIR` and `FFML_ARTIFACTS_DIR`: synthetic stores in the clone's gitignored directories (the production layout) |
| `settings-reports/` | `FFML_REPORTS_DIR`: since QA-F27 the weekly verification reports and the final report go to `<FFML_REPORTS_DIR>/livetest/` |
| `ffml-livetest-code/` | (world A only) a worktree pinned at X, for the QA-F27 recheck and the final run from X |

**The freeze** is done as the real one will be. Commit F fills the
pre-registration's `commit` field with X. Commit L1 adds the change-log entry
for F; L1 only adds lines, so it needs no entry of its own. For the rehearsal
only, F also replaces `history_sources.stored_parts.digest` with the digest of
97 synthetic stored parts written at the real paths, because the real digest
pins 97 real files the rehearsal may not read. That field is outside `rules`,
so `rules_sha256` and `lines_sha256` keep their pre-registered values. In every
world, `git diff --name-only X HEAD -- ffml pyproject.toml requirements.lock.txt`
is empty.

**A simulated calendar.** The processed and restricted stores are written as
real snapshot directories whose ids are the simulated UTC time of each refresh:

* a first snapshot on 2026-11-10;
* a pre-build refresh one hour before each build;
* a Friday refresh 30 minutes after each week's data cutoff (12:00 UTC on the
  first Friday at least 72 hours after the last kickoff).

Each snapshot holds the world as of its time. A game is final 4 hours after
kickoff. Consensus ranks appear once they are scraped, the day before the
week. The 2027 schedule appears only from 2027-05-15. The scorer's "first
snapshot at or after the cutoff" rule (D-116 A3) therefore runs on real
snapshot directories, with earlier and later snapshots present.

**The season runs in calendar order**, as events sorted by simulated time:

1. pre-build refresh;
2. the runner's own CLI (`livetest_week.main(["run", "--season", S, "--week", W, ...])`)
   at K - 24 h 15 min (K = the week's first kickoff). It records any misses,
   runs the **real `--prebuild-check` CLI of the clone**, builds (through the
   build hook below), verifies, commits and pushes to the bare remote, writes
   `push_record.json` and runs `ffml livetest verify`. A blocked build retries
   at the times the scenario gives;
3. the Friday refresh after the data cutoff;
4. weekly scoring at 13:00 UTC on that Friday: the real `--week` CLI for
   D1's 2026w10 and E's 2027w01, the hook otherwise;
5. the runner's `seal` command, which pushes `<week>/sealed_hashes.json` and the
   sealed file verbatim at `scoring/<week>.sha256`.

Before 2027 week 1 the runner is initialised for 2027, because its
configuration holds one season; the miss count spans both (QA-F30). After the
season comes a post-freeze change on an allowed path
(`scripts/rehearsal_logged_change.py`, commit A) and its change-log entry
(commit L2). The valid HEAD is X → F → L1 → A → L2. Builds run at the clone's
HEAD at the time, L1 in most weeks, so the scorer's per-week check "every
commit between X and the build commit was in the change log at the build
commit" (D-116 A2) runs every week.

**The clone's code, not the main checkout's.** Every scorer subprocess,
including the ones the runner starts, runs through a short wrapper. The
wrapper imports `ffml` and asserts that `ffml.__file__` and
`ffml.eval.livetest_score` lie inside the clone; the venv's editable install
points at the main checkout. It then calls `livetest_score.main(argv)`. The
driver that runs the runner and the hooks refuses to start unless `ffml` and
`scripts/livetest_week.py` come from the clone.

## The synthetic world

The world has sixteen invented teams (`S01`-`S16`) playing eight games a week:
Thursday night, Sunday early, late and night, and Monday night. Each team has
one QB, two RBs, three WRs, two TEs, one K and the DST: 160 player-weeks a
week. The seasons are 2024 (history), 2026 weeks 1-18 and 2027 weeks 1-9. The
2026 window kickoffs follow the pre-registered schedule as of 2026-11-04. The
2027 weeks start on 2027-09-10.

* **Truth.** Each player has a skill and each week a matchup effect. The outcome
  is log-normal around skill × matchup, drawn with a stratified probability
  integral transform in the window, so a calibrated model's coverage is about
  0.80. The stat lines score exactly to the outcome under PPR.
* **The chain** (`chain_v2`) predicts the true distribution. Its raw rows
  (`chain_v1`) have intervals 20% too narrow.
* **Comparators** (a pre-written `livetest_comparators` run): `rolling_avg_4`,
  `prior_season` and `rolling_median_8`, raw and `+cal`, centred on skill with
  noise and without the matchup effect. Since D-116 A3 the scorer replaces
  their outcome columns with the chain's pinned ones.
* **Consensus**: synthetic `fact_ecr_weekly` ranks from skill, half the matchup
  effect and noise.
* **A release marker** `artifacts/holdout_release.json` (2025 released,
  `approved: false`), a synthetic copy of the production state.
  `FFML_HOLDOUT_UNLOCK` is never set.

World variants:

* **B** narrows the calibrated chain's QB spread by 20%. **B2** is B rebuilt
  from scratch.
* **C** makes `prior_season+cal` at WR identical to the chain at WR.
* **D1, D2 and D3** miss one, two and three weeks.
* **E** is A's truth with stat corrections. Every window week has preliminary
  values (40% of rows ±0.7 points, ±1 for K and DST) in every snapshot before
  its cutoff. The 2027 weeks have corrected values (30% of rows ±1.4 points)
  in every snapshot from a week after the cutoff.
* **P** has the blocked builds.

## What ran through the real CLI, and the injection hooks

These ran through the real paths:

* every final (`--final`, including the failed fetch, the missing record and
  the run from the pinned worktree);
* the weekly `--week` CLI for D1's 2026w10 and E's 2027w01, the rescore of
  2027w01, and four exit-4 calls;
* `--data-cutoff`;
* every `--prebuild-check`, started by the runner itself;
* the runner's `init`, `run` (build, push, misses) and `seal`;
* `ffml livetest verify` on every pushed commitment;
* the scorer's own data loaders with pinned stores.

| hook | where | why |
| --- | --- | --- |
| `Deps.run`: `ffml livetest build-week` writes a synthetic commitment with the builder's own helpers (`write_commitment`, `file_records`, `digest_of`, `rel`, `code_state`, `chain_v2.check_pins()`); every scorer command goes through the wrapper; every other command runs unchanged | every runner call | the real build needs the feature store and LightGBM. The pre-build check must run inside the runner's own build step, so the runner has to build |
| runner `Deps`: simulated clock, recording alert (nothing sent), no live schedule, simulated GitHub view; `core.parse_github_remote` names the bare remote a GitHub repository | every runner call | there is no GitHub; `push_activity.timestamp` is the simulated server time of the push |
| `livetest_score.utcnow` set to the simulated time in the wrapper (`FFML_REHEARSAL_NOW`) | every scorer CLI call | the scorer's documented test seam. `--week` compares it with the week's data cutoff, which lies in the future of the real clock |
| snapshot ids at simulated times (`ffml.data.store._utc_now` patched while the synthetic data are written) | every refresh | the scorer pins snapshots by their id's time |
| weekly scoring through `score_week(..., n_boot=5, sensitivity_n_boot=5)` in-process, with every other argument wired as `main()` wires `--week` (data cutoff, recorded or chosen pins, pinned stores, the week's consensus rows) | every week except the real-CLI weeks | a real weekly CLI run is two 500-draw bootstraps. The final never reads the weekly metrics, and the part, consensus part and outcome hash equal the CLI's byte for byte (D1, checked) |
| `final_evaluation(sensitivity_fn=...)`, every line FAIL (A) or PASS (B) | scenario 7 | a real 5,000-draw run cannot be made to disagree on demand |
| comparators pre-written with three baselines | every world | `rolling_avg_3`, `rolling_ew_035` and `direct_lgbm` are information only (the Evaluator notes that the real comparator extension path is still unrehearsed) |

## Findings so far (not fixed by QA; frozen files untouched)

| id | severity | finding | where | status |
| --- | --- | --- | --- | --- |
| QA-F31 | high | `ensure_comparators` ignores the D-121 exclusion. When the comparators run must be extended to the week being scored, as each week in production, the week with the excluded game is refused ("is not final in dim_game"), because the pinned `dim_game` still holds the game. Every later week of that season is then refused ("earlier weeks not final"). Those weeks can never be sealed, so the final can never run. Repro: world G's comparators probe (the real `--week` CLI with the comparators run cut before 2026w14) | `ffml/eval/livetest_score.py:846-863`, called at `:2173` (071c3a4) | fixed by the Evaluator before the freeze (D-128); to recheck at the new X |
| QA-F32 | low | `--data-cutoff` for a week whose season has no `dim_game` partition yet raises `FileNotFoundError` (exit 1, traceback), not exit 4. The documented exit-4 branch cannot be reached; `--week` loads `dim_game` the same way. No effect on the verdict: the runner only asks about weeks that have kicked off, and on exit 1 it retries with an alert | `ffml/eval/livetest_score.py:2115` and `:2159`, `ffml/data/store.py:406` (071c3a4) | fixed before the freeze (D-128); to recheck |

Observations (not defects):

* A rescore after sealing rewrites the week's sealed JSON (`scored_at`), so the
  final refuses the week until the original sealed files are put back.
* `--prebuild-check` blocks when `--from` is not an ancestor of `--to`.
