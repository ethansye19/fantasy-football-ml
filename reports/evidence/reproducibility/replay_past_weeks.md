# Replaying past weekly builds (D-137)

Date: 2026-11-13 (UTC). The question: can a past week's projections be reproduced exactly by replaying with the
build time and data snapshots its commitment records?

**Answer: yes.** Three replays pinned the recorded build time and every recorded snapshot id:

- A: 2026 w10;
- B: 2026 w09 with the frozen code;
- C: 2026 w09 with its own recorded code.

In all three, every projection file, the calibrator state and the walk-forward rows matched the commitment's SHA-256 byte for byte. With the recorded code (A and C), every model file matched too. Three JSON manifests differ, and only in fields that describe the replay itself: wall-clock timings, write times, the git id and one absolute path. No frozen code was changed.

## What a commitment records

| Field | Content |
| --- | --- |
| `code.commit` | the commit the build ran |
| `as_of` (and, from b22973f on, `rows_as_of`) | the build time; the live feature rows are built as of it (D-134) |
| `snapshots` | the ids of the 3 feature and 9 processed snapshots the build read |
| `calibration_history.sources` | path and SHA-256 of each history part (98 for w09, 99 for w10) |
| `outputs` | path and SHA-256 of the 32 files the build wrote |

## Method

All four steps below run in a separate copy, with the real stores untouched.

1. **Stores.** A new folder is used as `FFML_DATA_DIR`, holding copies of the stores the build reads:
   - `processed`, without `restricted`: the FantasyPros archive is never copied;
   - `features`;
   - `backtests`, without `livetest_comparators`;
   - `schema_pins` and `raw/schedules`;
   - the walk-forward parts that existed before the recorded build.

   Stored paths are relative ("data/..."), so they name the same files as in the recorded build.
2. **Pinning.** No code was changed:
   - `build_week(..., clock=lambda: as_of)` uses the function's existing test hook;
   - a wrapper around `SnapshotStore.latest_id`, local to the replay process, returns the recorded id for each
     dataset the commitment lists.

   A fallback for unlisted datasets (the newest snapshot at or before `as_of`) was never needed: all 12 reads in every replay were recorded ids.
3. **Code.**
   - A and B ran with the pinned worktree at Y, whose `ffml/` equals X.
   - C ran with a temporary worktree at e43e349 and the same venv. `pyproject.toml` and the lock file have no diff between e43e349 and Y. Builds before 7075790 resolve paths from the folder that holds `ffml`, so C's stores sat in that worktree's own git-ignored `data/` and `artifacts/`.
4. **Comparison and guards.**
   - Each output's SHA-256 is compared with the commitment, JSON files field by field and Parquet files frame by frame.
   - The real `processed`, `features`, `raw` and `livetest` stores were fingerprinted before and after each replay, plus `artifacts` for C. All were unchanged.
   - The pinned worktree is still clean at Y.
   - The only shared file written is `artifacts/holdout_access.log`, the audit log of holdout-season reads, which every build appends to by design.

The scripts used are in [replay/](replay/). They were edited for lint only: `zip(..., strict=True)` after equal-length checks, and one pair of parentheses removed. Their reports are `replay/replay_A_2026w10.json`, `replay/replay_B_2026w09_frozen_code.json` and `replay/replay_C_2026w09_e43e349.json`.

## The three replays

| Replay | Week | Recorded build | Code used | Data since the build | Byte-identical |
| --- | --- | --- | --- | --- | --- |
| A | 2026 w10 | 2026-11-12 14:49:18.98 UTC at cfcfcd6 (`ffml/` = X) | Y (`ffml/` = X) | no newer snapshots (all 12 ids are still LATEST) | 29 of 32 |
| B | 2026 w09 | 2026-11-04 23:36:07.98 UTC at e43e349, before the freeze | Y (frozen) | 8 days newer: all 12 pinned ids are older than today's LATEST | 9 of 32 |
| C | 2026 w09 | the same | e43e349 (recorded) | the same | 29 of 32 |

Each replay took 148, 175 and 154 seconds respectively.

### 2026 w10 (A)

| Output | Role | Recorded SHA-256 (first 16) | Replay A |
| --- | --- | --- | --- |
| _manifest.json | predictions | `fed31aed3d9a2858` | differs: extra.model_source.path, extra.timings.load_model, extra.timings.projection_week, extra.timings.rows, written_at |
| player_sims.parquet | predictions | `0e9802fbcf8f9573` | identical |
| player_summary.parquet | predictions | `5886903e46ed3019` | identical |
| player_summary_raw.parquet | predictions | `153445a2e4c92299` | identical |
| team_env.parquet | predictions | `3c8343af9e412cdd` | identical |
| calibration_state.json | model_artifact | `a519afd9fe696d8f` | identical |
| chain_v2.json | model_artifact | `71ffbfb1e14fceea` | identical |
| features.json | model_artifact | `59847bfddbb27025` | identical |
| lgbm/*.txt (19 stage models) | model_artifact | (per file) | 19 identical |
| params.json | model_artifact | `24bf6bd7dc135842` | identical |
| selection.json | model_artifact | `6985d099342fc5b8` | identical |
| train_manifest.json | model_artifact | `a0450869186f2ee7` | differs: fit_seconds, git, timings.fit_total, timings.load, timings.stages |
| 2026_w09.json | walkforward_part | `01e364e7e24e38bd` | differs: created_at, fit_seconds, git, predict_seconds |
| 2026_w09.parquet | walkforward_part | `d7219c609616341c` | identical |

### 2026 w09 (B and C)

| Output | Role | Recorded SHA-256 (first 16) | Replay B (frozen code) | Replay C (recorded code) |
| --- | --- | --- | --- | --- |
| _manifest.json | predictions | `22e4f2835c31a6b0` | differs: extra.model_source.path, extra.timings.*, written_at | differs: the same fields |
| player_sims.parquet | predictions | `12d7ba3de87edb1b` | identical | identical |
| player_summary.parquet | predictions | `9d366836b01d20fe` | identical | identical |
| player_summary_raw.parquet | predictions | `c3ae2225ea2be7ae` | identical | identical |
| team_env.parquet | predictions | `31d6ad83778cec25` | identical | identical |
| calibration_state.json | model_artifact | `f3fc0518c5690873` | identical | identical |
| chain_v2.json | model_artifact | `f8b36b8d1cbed315` | differs: pin_hashing, unchanged.stage_choices.sha256, unchanged.stage_choices.sha256_crlf_working_copy | identical |
| features.json | model_artifact | `59847bfddbb27025` | identical | identical |
| lgbm/*.txt (19 stage models) | model_artifact | (per file) | 19 differ, each only in the header line `[num_threads: 8]` -> `[num_threads: 4]` | 19 identical |
| params.json | model_artifact | `6a75a52c5e754a67` | identical | identical |
| selection.json | model_artifact | `6985d099342fc5b8` | identical | identical |
| train_manifest.json | model_artifact | `d09b5b39b3f89988` | differs: fit_seconds, git, timings.* | differs: the same fields |
| 2026_w08.json | walkforward_part | `e5572a9726b7a496` | differs: created_at, fit_seconds, git, predict_seconds | differs: created_at, fit_seconds, predict_seconds |
| 2026_w08.parquet | walkforward_part | `15c4b3feb81005e6` | identical | identical |

For B's 19 LightGBM files, the check was exact: replacing `[num_threads: 8]` with `[num_threads: 4]` in each recorded file gives the replayed file byte for byte. The thread count changed by D-108, and the trees are the same. chain_v2.json differs because b22973f added LF-normalized pin hashing; the hash values describe the same `selection.json`.

## Differences, and why each is expected

These are all the fields that differ. They are the same in every replay.

- **`predictions/_manifest.json`.** Three fields differ:
  - `written_at`;
  - `extra.timings.*`;
  - `extra.model_source.path`, the absolute path of the model folder, because the replay root is a different folder.
- **`train_manifest.json`.** `fit_seconds` and `timings.*` differ, and so does `git`, the commit read at fit time. The recorded w09 file says 1c14b6c because HEAD moved during that build: 1c14b6c was committed at 23:40:26 UTC, mid-build. It changed `scripts/` and `tests/` only, with no `ffml/` diff. This is what 56d2d77 later fixed by recording `commit_at_end` and `head_moved`.
- **The walk-forward part `.json`.** `created_at`, `fit_seconds` and `predict_seconds` differ, plus `git` when the code differs.
- **`commitment.json`.** The following fields differ:
  - `created_at`: the replay clock stays at `as_of`;
  - `build` timings;
  - `code`: the commit and the working-tree status. The recorded w09 tree had uncommitted `scripts/` and `web/` files, none in `ffml/`;
  - `seconds_before_kickoff`;
  - the hashes of the files above: `model_artifact.sha256`, the train manifest hash, `predictions.manifest_sha256`;
  - `digest`, which covers all of these.

  In C, every other field equals the record: `as_of`, `snapshots`, `seeds`, `pins`, `calibration_history`, `calibrator_state`, `model_version` and `holdout_release`. In B, the frozen code also adds `cutoff` and `rows_as_of` (equal to `as_of`) and the LF form of `pins.selection_sha256`.

## Line endings: C's first attempt

C's first attempt stopped in `chain_v2.check_pins` before building anything:

- e43e349 hashes the raw bytes of `ffml/models/chain/selection.json`.
- The main checkout still holds that file in CRLF form. Its hash is d6c1891…, the pinned value, which is documented as the CRLF working-copy form in docs/status/phase4-ml-engineer-chain-v2.md.
- A fresh worktree checks the file out with LF line endings, because `.gitattributes` sets `eol=lf`. That gives hash 75477b2….

The replay copied the main checkout's bytes for that one file, which is the same git blob, and marked it assume-unchanged so that `git status` reads as it did at the build.

The frozen code hashes LF-normalized text (b22973f). Live-test builds and their replays do not depend on line endings.

## Every input is recorded

- `build_week` reads 12 snapshot pointers, 3 feature and 9 processed, all listed in `commitment.snapshots`. It reads no raw store.
- Each processed snapshot's manifest lists the raw snapshot ids it was built from, in `extra.inputs`. For example, fact_player_game 20261104T233039Z was built from schedules 20261104T232809Z, pbp 20261104T232742Z and player_stats 20261104T232801Z, among others.
- So the chain from raw data to processed data, features, rows and projections is traceable for every committed week. Snapshots are never pruned during the window.
- In C, 97 of the w09 build's 98 calibration sources were byte-identical today. The 98th is the part the build itself writes.

## How the replay-proof compatibility rule will work

The pre-registration's rule (`outside_changes.rule`):

> only logged compatibility fixes that are proven not to change model behaviour, by replay checks against earlier
> committed weeks: the raw predictions and the calibrator state of the four identity weeks (2022 w6, 2024 w15,
> 2025 w9, 2026 w4) and of the three most recent committed live-test weeks are rebuilt and must be byte-identical;
> the recomputed feature rows of every committed week must be byte-identical; the change is logged in
> docs/status/livetest-changelog-2026.md with the replay evidence and verified by the Evaluator before first use

If a fix is not provable, the rule says stop: no further commitment is built until the owner rules (`if_not_provable`).

The steps below put that rule into practice. None of them changes or relaxes it.

1. **When.** Before the first use of any change made because of an outside change: an nflverse schema change, a broken dependency, a Sleeper API change, or a replaced laptop. For a replaced laptop, this happens on the new machine before its first live build (`laptop_replacement`).
2. **Baseline first.** Replay the comparison weeks with the unchanged pinned code (the worktree at Y), on the same machine and environment the fix will run on. Each week must reproduce its recorded hashes as A did. If the baseline fails, nothing can be proved, and the rule's stop applies.
3. **The fix.** Run the same replay in a worktree at the fix commit, with the same recorded `as_of` and snapshot ids:
   - **The three most recent committed live-test weeks.** All of them while fewer than three are committed. `player_summary_raw.parquet` (the raw predictions) and `calibration_state.json` (the calibrator state) must equal the recorded SHA-256. That comparison is binding. Every other output is compared too, and every difference is listed field by field. A difference outside the fields named in the section above goes to the Evaluator.
   - **The four identity weeks.** Recompute the raw rows with the feature snapshot that produced the stored rows (20261102T2139xx), the same check as D-108 (ML-4.2). The stored backtest parts to compare against are:
     - default_20261102 for 2022 w6 and 2024 w15;
     - holdout_2025 for 2025 w9;
     - forward_2026 for 2026 w4.

     Every column must be equal, and the files must be byte-identical where a stored file exists. Each identity week's calibrator state is rebuilt and must be byte-identical.
   - **The feature rows of every committed week.** Rebuild each week's live rows at its `rows_as_of`, from its recorded snapshot ids, with the fix and with the frozen code. They must be identical in every value, dtype and row order, which means byte-identical when written the same way. If the fix touches ingest or processing, first rebuild the processed and feature snapshots with the fix, from the raw ids their manifests record. The rows built from them must equal the rows built from the recorded snapshots.
4. **Log and verify.** Add a change-log row in docs/status/livetest-changelog-2026.md, with:
   - the replay reports: per-file hashes, both commits, the machine and the list of snapshot reads;
   - the Evaluator's verification before first use.

   If any binding comparison fails, the rule's stop applies (`if_not_provable`) and the owner rules.
5. **Tools.** The replay scripts in [replay/](replay/) are the ones used here. Before a real fix, they move into `scripts/` with a hand-written change-log row and the Evaluator's verification, since the helper does not log `scripts/` commits by itself. Nothing in `ffml/` is needed: `build_week`'s `clock` hook and the `latest_id` wrapper do the pinning.
6. **Never for scoring.** A replay is a proof tool only. The verdict scores the committed, sealed files. A committed week is never rebuilt to be scored.
