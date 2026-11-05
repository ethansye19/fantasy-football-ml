# Phase 4 / ML Engineer / chain_v2 and the weekly live-test build (ML-4.1)
Date: 2026-11-05 (UTC)
Scope: define chain_v2 (D-098), build `ffml livetest build-week` (refit,
walk-forward calibrator, private-test prediction, hashed commitment,
deadline), refresh the data, rehearse Week 9, and diagnose the intermittent
CI failure. Nothing was tuned, approved, rejected, released or published. No
freezing or pre-registration was done (that comes after the D-105 design).

Task list (from the brief):
1. chain_v2 = chain_v1 primary plus exactly two changes (2025 in training
   through `fit(cutoff)`; the v3 walk-forward calibrator as `default_20261102`
   applied it).
2. `ffml livetest build-week --season S --week W [--rehearsal]`: refit and
   register a candidate, build the calibrator state from earlier weeks only
   (stored raw walk-forward rows plus fresh weeks), verify raw-row identity,
   predict in private-test mode, write the commitment, refuse at or after
   kickoff with exit 3.
3. Refresh the data (week 8 final, week 9 schedule, injuries, depth) and
   rehearse Week 9; report run time and peak memory.
4. Diagnose the flaky `test_default_feature_lists_are_unchanged` without
   changing behaviour; propose a fix with its measured effect.
5. Later additions from the orchestrator: registry paths that resolve from a
   pinned worktree; LF-normalized pins (CI failure); the D-106 rule that
   runs start 24 h early.

## 1. What chain_v2 is (diff from chain_v1)

`ffml/models/chain_v2.py`; the spec is written into every artifact as
`chain_v2.json`.

| | chain_v1 (rejected) | chain_v2 |
| --- | --- | --- |
| Raw chain | `ChainModel(config="primary")`, stage choices from `selection.json` | the same object, code and defaults |
| Stage choices | `ffml/models/chain/selection.json` | same file; pin SHA-256 `75477b2b97bc7d9bef935c2cfd470158d5b809b62f531fe898de2f7d2da84601` (LF bytes, see section 6) |
| Features | 0.2.0, manifest pin `b119f85cdfdc3546b3fb053ca480da6b5e543349940080a3bd19ae692f2c42f6` | same (version and fingerprint text checked) |
| Hyperparameters | `ffml/models/chain/lgbm.py`, structural, dispersion | unchanged code |
| Seeds, sims | 20261101, week seed `seed + 100*season + week`, 2,000 | same |
| Availability | live layer `final_report` (D-065) | same |
| **(a) Training rows** | frozen at cutoff (2024, 19): 2016-2024 | refit every week at `(S, W)`: every regular-season row before week W whose game is final, so 2025 (released as data, `approved=false`) and 2026 weeks 1..W-1 |
| **(b) Calibration** | FIXED, frozen on 2022-2024 (D-095) | walk-forward: `OnlinePITQuantile(seed=0)` (per position, min_count 50, atoms "nearest", `v3-tie-aware-quantile`) and per-position `OnlineIsotonic` (min_count 200), fitted for week W on every earlier week from 2021 w1 to W-1, games before week W's first kickoff only |
| Registry version | `chain_v1-primary-c2024w19-<stamp>` | `chain_v2-primary-c{S}w{WW}-<stamp>` (cutoff = the predicted week) |

- The calibrator settings are pinned as the exact `repr` strings recorded in
  `data/backtests/default_20261102/_manifest.json`; `check_pins` fails a
  build if any pin moves (selection file, feature version and fingerprint,
  calibrator reprs, availability config, resolved stage choices, the feature
  version of the loaded rows).
- Raw rows keep the model label `chain_v1` (the unchanged chain writes it);
  calibrated rows are labeled `chain_v2` with
  `calibration_version = walk_forward:<first 12 hex of the state SHA-256>`.
- **Calibrator state.** `calibrator_state(history, target, cutoff)` computes
  for one week exactly the levels and isotonic maps that
  `OnlinePITQuantile.transform` followed by `OnlineIsotonic.transform`
  compute, and returns them as a `FrozenCalibration` (saved as
  `calibration_state.json`, hashed, applied with `FrozenCalibration.apply`).
  The randomized PITs are drawn per scoring over all earlier eligible rows in
  (season, week, position, entity) order from one generator seeded 0; the
  draws are sequential, so the 2021-2024 draws equal the validation run's
  and later rows never change an earlier row's PIT. Empty isotonic entries
  are kept so that `identity` sources match the online transform exactly.

## 2. Identity checks

| Check | Result |
| --- | --- |
| Chain code since the runs (`git diff 3ecf610 -- ffml/models/chain ffml/features ffml/sim/engine.py ffml/sim/params.py ffml/sim/summarize.py`) | empty |
| Raw rows recomputed in a fresh process on the frozen snapshot (`player_week 20261102T213907Z`): 2022 w6 and 2024 w15 (`default_20261102`), 2025 w9 (`holdout_2025`), 2026 w4 (`forward_2026`) | **4 of 4 byte-identical** (`DataFrame.equals` on every column, drivers included; 1,530 / 1,680 / 1,524 / 1,734 rows; fits 43-119 s) |
| The same week fitted twice in one process (2026 w4) | identical |
| Calibrator state vs the stored `chain_v1+cal` rows of `default_20261102` (state from stored raw rows of earlier weeks, applied to the week) for 2021 w1, 2021 w3, 2022 w1, 2023 w9, 2024 w18 | **5 of 5 exactly equal** (7 quantiles, boom, bust, `cal_q_source`, `cal_n`, both isotonic sources) |
| Calibrator state vs the harness online transforms on real data, target 2026 w8 (history 2021 w1 to 2026 w7, 97 weeks, 124,905 PIT rows; w8 rows from the fresh walk-forward) | exactly equal |
| Outcome columns (actual, active, boom/bust actual, kickoffs, week cutoff, PIT bounds) recomputed on the refreshed data vs those stored in the three runs (117,012 + 29,451 + 11,640 raw rows) | identical |
| Feature rows, frozen vs refreshed snapshot, chain columns | identical for every season 2016-2023 and every team/DST row; `attr__weight` differs on 4 player rows in 2024 and 8 in 2025 (upstream weight update); 2026 w1-7 differs on 1 row in the air-yards targets (stat correction). The stored raw rows are used as stored |

The identity run took 770 s for 6 fits at a 4.06 GB peak.

## 3. Weekly build: `ffml livetest build-week`

Code: `ffml/sim/livetest.py`, CLI group `ffml livetest` in `ffml/cli.py`,
two keyword-only private-test options in `ffml/sim/predict.py`
(`rehearsal`, `calibrator`), the label key `rehearsal` in
`ffml/sim/snapshot.py`.

Steps (point in time as of the build, D-046):
1. Deadline check (exit 3), one-commitment-per-week check (exit 4), pins.
2. Calibration history: stored raw parts (`STORED_SOURCES`: 2021-2024
   `default_20261102`, 2025 `holdout_2025`, 2026 w1-7 `forward_2026`), then
   each later week computed once by the same walk-forward (`fit((S, w))`,
   `predict`, `ffml.eval.harness.check_frame`) and cached append-only in
   `data/livetest/walkforward/parts/chain_v1/` with a JSON sidecar (hash,
   snapshots, git). Outcomes, PIT bounds and week cutoffs come from the
   harness functions on the latest processed data.
3. Refit at `(S, W)`; artifact `artifacts/models/chain_v2/<version>/`
   (`params.json`, `lgbm/*.txt`, `features.json`, `selection.json`,
   `train_manifest.json`, `chain_v2.json`, `calibration_state.json`).
4. Calibrator state for week W from rows of weeks < W only; the version is
   registered as a **candidate** with the state as its calibration file.
5. `run_predict(private_test=True, ...)`: labeled "not approved",
   `player_summary` calibrated (raw rows kept as `player_summary_raw`),
   private store; rest of season not built (the test is weekly).
6. `data/livetest/{S}w{WW}/commitment.json`: every output file with SHA-256
   and size (prediction run, model artifact, new walk-forward parts), the
   artifact digest, the calibrator-state hash, the history sources with
   hashes and their digest, feature and processed snapshot ids, the code
   commit with clean-tree status (at the start and the end, `head_moved`),
   `as_of`, `rows_as_of`, `cutoff`, `created_at` (UTC), the first kickoff
   from `dim_game`, seeds, pins, run time and peak memory, and
   `digest.value` = SHA-256 of the canonical JSON (sorted keys, separators
   `,` `:`) of every other field. `ffml livetest verify <path>` re-checks the
   digest and every listed file.

Exit codes: 0 committed; 1 failure; 2 blocked (approval gate, or no
regular-season game in `dim_game`); 3 at or after the first kickoff without
`--rehearsal` (checked before anything is written, again before the
projections and again before the commitment); 4 week already committed.

Rehearsal (`--rehearsal`): allowed after kickoff; `rehearsal: true` in the
commitment, the run manifest and the sandbox `LATEST`; everything under
`data/livetest/rehearsal/` (own registry `REGISTRY.json`, artifacts
`models/chain_v2/`, store `predictions_private/`, walk-forward cache). The
real registry, `data/predictions_private` and the public store are not
touched.

**D-106, runs start 24 h early.** Checked and confirmed: `future_targets`
sets `asof_ts = now` (depth charts, rosters, injuries, Sleeper statuses) and
`cutoff_ts` = the week's first kickoff (game windows), so an early build
differs from a late one only in the roster and injury state it sees, never in
its cutoff; the refit and the calibration history use only weeks before W
whose games are final in the data. Added: `rows_as_of = min(now, first
kickoff)`, which equals `now` for every real build (the deadline refuses
`now >= kickoff`) and caps a rehearsal run after kickoff at the kickoff; the
commitment records `as_of`, `rows_as_of` and `cutoff`, and the run id carries
the build time so two rehearsals of one week never collide. Test: a build 30 h
before kickoff reads rows as of the clock with the kickoff as cutoff; a
rehearsal 5 h after kickoff is capped.

**Paths.** Registry and commitment paths use logical roots (`artifacts/...`
under `Settings.artifacts_dir`, `data/...` under `data_dir`, `reports/...`
under `reports_dir`; `registry.store_path` / `resolve_path`), so the pinned
worktree run with `FFML_ARTIFACTS_DIR` and `FFML_DATA_DIR` set to the main
checkout resolves them (Data Engineer review). With default settings the
stored text is unchanged (chain_v1's entry is as it was). Test: the gate runs
from another project root and after moving `FFML_ARTIFACTS_DIR` outside the
checkout.

## 4. Data refresh and the Week 9 rehearsal

Refresh (library calls equal to `ffml ingest/process/validate/features`
for 2016-2026, a full run because a snapshot holds only the seasons it was
asked for; schema pins read and updated in a copy outside the checkout, no
tracked file changed; this ran before `ffml refresh` landed in a2f2af0):

| Step | Result |
| --- | --- |
| ingest | 20 datasets, every LATEST advanced, no schema drift, 79 s, peak 0.97 GB |
| Sleeper players | already fetched 2026-11-04 (12,229 rows) |
| process | 11 tables, 10.7 s; `fact_player_game` 98,485 rows (+70: the two week 8 games now final) |
| validate | passed (one WARN, crosswalk coverage, as before) |
| features | `player_week 20261104T233142Z`, `team_week 20261104T233143Z`, `dst_week 20261104T233144Z`, 10.4 s, peak 2.8 GB |

`dim_game`: 2026 week 8 has 14 of 14 games final; week 9 has 15 games, first
kickoff 2026-11-06 01:15 UTC. Week 9 injury rows at build time: 28
(Wednesday); depth charts through 2026-11-04 15:20 UTC.

**Rehearsal, before kickoff:** `ffml livetest build-week --season 2026 --week 9 --rehearsal`
started 2026-11-04 23:36:07 UTC, exit 0 (about 25.6 h before kickoff).

| | |
| --- | --- |
| Model version | `chain_v2-primary-c2026w09-20261104T233607Z` (sandbox registry, candidate) |
| Commitment | `data/livetest/rehearsal/2026w09/commitment.json`; **digest `a69e91379d1f23a88f858b0d5e221ea4a36f70d30f33cd94b30583bf3fe640c2`**; `ffml livetest verify`: intact (re-checked 2026-11-05 03:01 UTC) |
| Created | 2026-11-04 23:42:34 UTC, 91,946 s before kickoff |
| Calibrator state | SHA-256 `f3fc0518c5690873d18e81342d08ca02ba9a2699f8487b15754a7a4a7be67094`; 126,102 PIT rows; history 2021 w1 to 2026 w8 (98 weeks: 97 stored, 2026 w8 fresh) |
| Model artifact digest | `17ff5a90f605a97e6b371f7f86bdb9c465158c55904f16b6d89cc531233b6db7` (25 files) |
| History digest | `d7bf1661085084e5c894c1c2a9b9690543ddf4b9c34fec0f07fde1e520a6ff3e` |
| Predictions | 1,425 rows (475 entities x 3 scorings), run `2026w09-20261104T233607Z-private`; Sleeper tie-breaker used (snapshot 2026-11-04), INA applied; no final games skipped |
| **Run time** | **386 s** in process (392 s wall): features 2 s, history and the 2026 w8 walk-forward 195 s (its fit 180 s), refit 169 s, state 2 s, predict 17 s |
| **Peak memory** | **4.01 GB** peak working set |
| Code | start commit `e43e349`, `code_clean` true (nothing under `ffml/` modified), tree not clean (other agents' files). HEAD moved to `1c14b6c` during the build (scripts only); the `head_moved` field, `rows_as_of` and `cutoff` were added afterwards, so this commitment lacks them |
| Pins recorded | `selection_sha256` is the CRLF form `d6c1891...` (the LF rule came later; same file, see section 6); feature manifest `b119f85...` |

Effect of the calibrator on week 9 (PPR, mean q90 - q10 width, raw to
calibrated): QB 14.8 to 17.0, RB 12.9 to 12.1, WR 11.8 to 11.8, TE 8.0 to 7.8,
K 13.3 to 12.1, DST 12.5 to 12.5; every position used its own maps. These are
rehearsal outputs, labeled "not approved", never shown in the app. A real
build was not run; the first is Week 10.

## 5. Determinism diagnosis (diagnose only; nothing changed)

**Failure.** `tests/models/test_chain_ablation.py::test_default_feature_lists_are_unchanged`
fails on CI at its last line, `explicit.predict(...).equals(default.predict(...))`,
for two identically seeded LightGBM fits on the synthetic frames (112 x 22
output rows). `gh run list` / `gh run view --log-failed`: the same assertion
failed at 294aac0, b7c664b, 17a0d08, a786515, b947fab, df425e2, e406434,
5700514, e749f1c and f7ec1c5, and passed at b82ba96, 3ecf610, ecce79c,
6e64eb9, 7739504 and others; the latest failure at 71bc523 is a different
test (`tests/api/test_meta.py`, the ECR import). CI is `ubuntu-latest`
(4 vCPUs) with `FFML_OFFLINE=1 pytest -q`.

**Not reproducible on Windows.** The test passed 5 of 5 times; 6 + 6
standalone repeats (Polars 8 and 4 threads) and 8 repeats pinned to 4 CPUs
with 8 LightGBM threads were identical in state, boosters, parameter frames
and predictions; the real-data refits above were byte-identical.

**Reproduced on Linux** (WSL2 Ubuntu, Python 3.14.4, polars 2.1.0,
lightgbm 4.7.0, numpy 2.5.4, scikit-learn 1.9.1, the test's own
construction: fit A, fit B with `ablation="none"`, compare B's predict to
A's):

| CPUs (affinity) | LightGBM `num_threads` | repeats that differ |
| --- | --- | --- |
| 4 (the CI shape) | 8 (the fixed value in `BASE_PARAMS`) | **9 of 10** |
| 8 | 8 | **6 of 8** |
| 4 | 4 | 0 of 8 |
| 4 | 1 | 0 of 8 |
| 8 | 4 | 0 of 8 |

**Where it arises.** In every differing repeat: the training rows,
structural stages, dispersion, constants and neutral means were identical
(`state()` without `lgbm` equal), the team parameter frame was identical,
and one to three LightGBM boosters differed (model strings), always weighted
cross-entropy stages: `share_dropback`, `eff_int_rate`, `eff_scramble_rate`,
`k_fg_pct`. Their `best_iteration` differed widely (45 vs 1, 20 vs 13, 2 vs
16); in `eff_int_rate` the first tree already differed. The player and DST
parameter frames and the predictions then differ downstream. Size on the
synthetic frame, max |difference| per repeat: `pred_mean` 0.56-1.17 points,
`pred_q50` 1.2-2.0, `pred_q90` 2.3-4.5, `pred_q95` 2.3-6.9, `boom_prob`
0.05-0.11; drivers differ too. (Large, because the synthetic stages have a
few hundred rows and early stopping flips between 1 and 45 rounds.)

**Ruled out.** Polars row order: no order-dependent step differs (the state
is identical and the chain's group-bys carry `maintain_order=True` or feed
order-free reductions); Polars 2.1 eager `sum`/`mean` do vary run to run,
but only from about 300k rows per group (2M rows / 6 groups: 25 of 25
runs differ; 2M rows / 2,000 groups and 200k rows: 0 of 25), far above the
chain's largest groups (2,000 sims per entity). Set or dict iteration order
and global RNG state: none in the fit or predict path (grep). Test order and
global state: the failure reproduces in a fresh process with nothing else
loaded.

**Most likely mechanism** (inferred, not proven): `deterministic=true` fixes
LightGBM's histogram and split order, but the OpenMP reductions of the
per-thread gradient, hessian and validation-metric sums follow the thread
schedule; with libgomp on Linux at 8 threads their last bits vary between
runs, and on the small weighted cross-entropy stages a last-bit change in
the early-stopping metric moves `best_iteration` and so the whole booster.
MSVC's OpenMP (Windows) gave stable sums in every trial.

**Effect on real data.** None observed on the owner's machine: the same seed
gives byte-identical `player_summary` across processes (4 stored weeks
recomputed, one week fitted twice) and the stored rows of three runs were
reproduced exactly. Thread count: refitting 2026 w4 with 4 LightGBM threads
gives rows **byte-identical** to the stored 8-thread rows (1,734 rows, every
column), and so does 1 thread. Fit time 8 threads 116 s, 4 threads 91 s, 1 thread 97 s
(LightGBM is not the bottleneck of a fit).

**Proposed fix (needs the orchestrator's go-ahead; not applied).**
- Preferred: set `num_threads = 4` in `ffml/models/chain/lgbm.py`
  `BASE_PARAMS` (or `min(4, available CPUs)`). Measured effect: raw
  predictions on real data unchanged (byte-identical to the stored and
  validated rows for 2026 w4; I would rerun the four identity weeks at 4
  threads before the pre-registration, about 13 minutes), 0 of 24 differing
  repeats on Linux at 4 threads, fits about 20% faster here.
- Minimal alternative: cap at the available CPU count,
  `min(8, len(os.sched_getaffinity(0)))` on Linux, `os.cpu_count()`
  elsewhere: leaves the owner's machine at 8 (predictions unchanged by
  construction), makes CI deterministic (0 of 16 at 4 CPUs), but keeps the
  6-of-8 failure rate on 8-CPU Linux machines.
- Test-only: pin the flaky test's LightGBM to 1 thread. No behaviour change
  and the CI flake disappears, but Linux fits at 8 threads stay
  irreproducible, which matters if a build ever runs there.
Nothing in `ffml/models/chain/` was changed for this.

## 6. Pins and line endings (CI failure, orchestrator item)

`core.autocrlf=true` here, so the Windows working copy of tracked text files
is CRLF while git and CI see LF. The selection pin `d6c1891...` was the SHA-256
of the CRLF bytes; the LF bytes hash to `75477b2b...`. `chain_v2.pin_sha256`
now hashes tracked text files with CRLF normalized to LF (test: a CRLF and an
LF copy give one digest; `text=False` hashes as is), `SELECTION_SHA256` is
the LF form, the CRLF form is kept as `SELECTION_SHA256_CRLF_FORM` and in
`chain_v2.json`. The feature manifest pin
`tests/fixtures/features/feature_manifest_0.2.0.json` is LF already, so
`b119f85...` is unchanged under normalization. Artifact and data files
(calibrators, FROZEN.json, parquet, the commitment's outputs) are hashed as
they are. Correction for the record: the holdout pre-registration's
documented selection.json SHA `d6c1891...` was the CRLF working-copy form of
the same file; its integrity checks used artifact files only, so the holdout
is unaffected.

## Evidence

- Identity: `scratchpad/identity_check.py` (4 weeks + repeat): all
  `identical_frame: true`; 770 s, peak 4,058 MB.
- Calibrator identity: `verify_calibrator_identity([(2021,1),(2021,3),(2022,1),(2023,9),(2024,18)])`:
  5 x `equal: True`; `online_equiv_w8.py`: `2026 w8 state == online transforms: True`, 59 s.
- Rehearsal: `committed chain_v2-primary-c2026w09-20261104T233607Z 2026 week 9 (rehearsal): ... digest a69e9137...e640c2 in 386s, peak memory 4014.8 MiB`, `exit=0 wall=392s`.
- Linux sweep (`det_linux.py`, taskset): `9 of 10` (4 CPUs, 8 threads), `6 of 8` (8, 8), `0 of 8` (4, 4), `0 of 8` (4, 1), `0 of 8` (8, 4).
- Real-data threads (`threads_effect.py`, 2026 w4 vs stored): 8 threads identical, 4 threads identical, 1 thread identical (peak 3.97 GB).
- Tests, targeted: `pytest -q -m "not slow" tests/sim tests/models/test_registry.py tests/models/test_chain_v2.py tests/test_holdout.py`: 101 passed, 1 deselected (before the last two commits); `tests/models/test_chain_v2.py tests/sim/test_livetest_build.py` after them: 30 passed.
- Fast suite, `PYTHONUTF8=1 FFML_OFFLINE=1 pytest -q -m "not slow"` over the whole
  working tree (other agents' untracked API and QA tests included):
  `850 passed, 2 skipped, 6 deselected, 20 xfailed, 2 warnings in 995.88s (0:16:35)`;
  the xfails are the QA reviewer's marked findings in `tests/api`, none mine.
- `ruff check .` (whole repository): All checks passed!
- `ruff check` on every path I touched: All checks passed; `ruff format --check` on my new files: formatted.

## Not done / blocked
- Freezing and pre-registration of chain_v2 (after the D-105 design).
- Git commit and push of commitments (the Data Engineer's runner).
- No real (non-rehearsal) build was run; the first is Week 10.
- The determinism fix (section 5) waits for the go-ahead.

## Decisions proposed
- **chain_v2 definition and weekly build** as in sections 1 and 3: what is
  pinned, the walk-forward state, the commitment format and digest, exit
  codes 0-4, the rehearsal sandbox, one real commitment per week, the
  append-only walk-forward cache, `rows_as_of = min(now, first kickoff)`.
- **Stored paths** use the logical roots `artifacts/`, `data/`, `reports/`
  resolved through settings.
- **Pins of tracked text files** are SHA-256 of LF-normalized bytes; artifact
  and data files are hashed as they are. The pre-registration of chain_v2
  should quote the LF form.
- **LightGBM threads**: fix `num_threads` at 4 (preferred) or cap at the
  available CPUs, after re-running the identity weeks at the chosen value.

## Next
- Owner: the D-105 design; then freeze and pre-register chain_v2 before the
  Week 10 kickoff (2026-11-13 01:15 UTC): the commit, the pins above (LF
  form), and the build interface.
- Orchestrator: decide on the thread fix; I rerun the four identity weeks at
  the chosen thread count.
- Data Engineer: real builds from the pinned worktree; the runner's verifier
  can call `ffml livetest verify`.
- Evaluator: scoring reads `player_summary.parquet` (calibrated) and checks
  the commitment first.

Commits: 2cb9813 (chain_v2 definition), 8bb2ac8 (rehearsal sandbox and
calibrator in `run_predict`), d24a43a (`ffml livetest build-week`), 56d2d77
(`head_moved`), 7075790 (logical-root paths), b22973f (LF pins, D-106 as-of
cap), plus this note.
