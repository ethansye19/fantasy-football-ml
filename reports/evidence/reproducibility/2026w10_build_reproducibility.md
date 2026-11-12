# Week 10 build reproducibility (2026 w10 rehearsal, D-134)

Date: 2026-11-12. Build code: `ffml/`, `scripts/`, `pyproject.toml` and `requirements.lock.txt` are identical to
X = 4799913 at cfcfcd6 and f43d69f (`git diff --quiet 4799913 HEAD -- ...` exits 0). Machine: the development
machine that runs the weekly builds (Windows, 8 logical cores; LightGBM 4 threads, D-108). Data: the same processed
and feature snapshots in every build (processed 20261112T144839Z-144847Z, features 20261112T144900Z-144903Z).

## Builds

| build | how | as_of (UTC) |
| --- | --- | --- |
| r0 | the pushed rehearsal (`livetest-dryrun` a2bc721), by the runner | 14:49:18.98 |
| r1 | `python -m ffml.cli livetest build-week --season 2026 --week 10 --rehearsal`, not pushed | 17:05:14.91 |
| r2 | the same command again, not pushed | 17:07:36.74 |
| fixed | `build_week(..., clock=r0's as_of)` in a scratch sandbox, not pushed | 14:49:18.98 |

Before r1, r2 and fixed, the 2026 w09 walk-forward part was moved aside, so each build recomputed it (as each counted
week's build computes the week before). The sandbox files a rebuild overwrites (the local commitment, REGISTRY.json,
LATEST, the w09 part) were backed up and restored byte-identical; the pushed commitment verifies as intact afterwards.

## Every file the commitment lists (SHA-256, first 12 hex)

| file | r0 pushed 14:49 | r1 17:05 | r2 17:07 | fixed (clock = r0) | fixed = r0 | r1 = r2 | r0 = r1 | note |
| --- | --- | --- | --- | --- | --- | --- | --- | --- |
| model/calibration_state.json | a519afd9fe69 | 08a9395ee565 | 24241853dde6 | 8bc6fdcd26f0 | no | no | no | metadata: `meta.as_of`; in `fixed`, `meta.sources_digest` (folder paths) |
| model/chain_v2.json | 71ffbfb1e14f | 71ffbfb1e14f | 71ffbfb1e14f | 71ffbfb1e14f | yes | yes | yes |  |
| model/features.json | 59847bfddbb2 | 59847bfddbb2 | 59847bfddbb2 | 59847bfddbb2 | yes | yes | yes |  |
| model/lgbm/availability.txt | 78fd5b3a81b7 | 78fd5b3a81b7 | 78fd5b3a81b7 | 78fd5b3a81b7 | yes | yes | yes |  |
| model/lgbm/dst_fum_rec.txt | cca3c55deb42 | cca3c55deb42 | cca3c55deb42 | cca3c55deb42 | yes | yes | yes |  |
| model/lgbm/dst_int.txt | 5b33af7c77c0 | 5b33af7c77c0 | 5b33af7c77c0 | 5b33af7c77c0 | yes | yes | yes |  |
| model/lgbm/dst_sacks.txt | 4c2a100a8987 | 4c2a100a8987 | 4c2a100a8987 | 4c2a100a8987 | yes | yes | yes |  |
| model/lgbm/eff_catch_rate.txt | 7e906a39ff28 | 7e906a39ff28 | 7e906a39ff28 | 7e906a39ff28 | yes | yes | yes |  |
| model/lgbm/eff_comp_rate.txt | 9c7a07f11f34 | 9c7a07f11f34 | 9c7a07f11f34 | 9c7a07f11f34 | yes | yes | yes |  |
| model/lgbm/eff_sack_rate.txt | ff7fb08564f3 | ff7fb08564f3 | ff7fb08564f3 | ff7fb08564f3 | yes | yes | yes |  |
| model/lgbm/eff_scramble_rate.txt | 3f859fbc2102 | 3f859fbc2102 | 3f859fbc2102 | 3f859fbc2102 | yes | yes | yes |  |
| model/lgbm/eff_ypc.txt | 14df581c645f | 14df581c645f | 14df581c645f | 14df581c645f | yes | yes | yes |  |
| model/lgbm/eff_ypr.txt | 6dd8dfafbabd | 6dd8dfafbabd | 6dd8dfafbabd | 6dd8dfafbabd | yes | yes | yes |  |
| model/lgbm/share_dropback.txt | 3ab47f22420c | 3ab47f22420c | 3ab47f22420c | 3ab47f22420c | yes | yes | yes |  |
| model/lgbm/share_gl_carry.txt | 24950158fba2 | 24950158fba2 | 24950158fba2 | 24950158fba2 | yes | yes | yes |  |
| model/lgbm/share_rz_target.txt | 9b2d9ef5388d | 9b2d9ef5388d | 9b2d9ef5388d | 9b2d9ef5388d | yes | yes | yes |  |
| model/lgbm/share_target.txt | 113449629502 | 113449629502 | 113449629502 | 113449629502 | yes | yes | yes |  |
| model/lgbm/td_pass_share.txt | 9be53b010600 | 9be53b010600 | 9be53b010600 | 9be53b010600 | yes | yes | yes |  |
| model/lgbm/td_rush_share.txt | 12e5325cd7a3 | 12e5325cd7a3 | 12e5325cd7a3 | 12e5325cd7a3 | yes | yes | yes |  |
| model/lgbm/team_pass_rate.txt | c2d3474ee314 | c2d3474ee314 | c2d3474ee314 | c2d3474ee314 | yes | yes | yes |  |
| model/lgbm/team_plays.txt | 85d5069ac424 | 85d5069ac424 | 85d5069ac424 | 85d5069ac424 | yes | yes | yes |  |
| model/lgbm/team_two_pt_att.txt | ec1e54c9a703 | ec1e54c9a703 | ec1e54c9a703 | ec1e54c9a703 | yes | yes | yes |  |
| model/params.json | 24bf6bd7dc13 | 24bf6bd7dc13 | 24bf6bd7dc13 | 24bf6bd7dc13 | yes | yes | yes |  |
| model/selection.json | 6985d099342f | 6985d099342f | 6985d099342f | 6985d099342f | yes | yes | yes |  |
| model/train_manifest.json | a0450869186f | a11563fae727 | 4f2a769d2e98 | 37194eafdbaf | no | no | no | metadata: timings, git sha, as_of, version |
| predictions/_manifest.json | fed31aed3d9a | 41e06ad9502c | 6c172ca92960 | 05b78c9211ef | no | no | no | metadata: paths, timings, written_at, file hashes |
| predictions/player_sims.parquet | 0e9802fbcf8f | 3dd0c3d4a02c | 3dd0c3d4a02c | 0e9802fbcf8f | yes | yes | no | values change with the build time (depth chart age) |
| predictions/player_summary.parquet | 5886903e46ed | 8e5827d0a401 | 0192bf1f71c6 | 162acab8ecc1 | no | no | no | label column `calibration_version` (hash of the state file); values equal at a fixed time |
| predictions/player_summary_raw.parquet | 153445a2e4c9 | f7485081a232 | f7485081a232 | 153445a2e4c9 | yes | yes | no | values change with the build time (depth chart age) |
| predictions/team_env.parquet | 3c8343af9e41 | 4331926a6c78 | 4331926a6c78 | 3c8343af9e41 | yes | yes | no | values change with the build time (depth chart age) |
| walk-forward/2026_w09.json | 01e364e7e24e | 7daf8c63c844 | 3b2a925c4498 | 87a9725c28f0 | no | no | no | metadata: created_at, timings, git sha |
| walk-forward/2026_w09.parquet | d7219c609616 | d7219c609616 | d7219c609616 | d7219c609616 | yes | yes | yes |  |

## Findings

1. **At a fixed build time the build is exact.** `fixed` reproduces r0 byte for byte in every projection file
   (`player_sims`, `player_summary_raw`, `team_env`), every model file and the recomputed walk-forward part.
   `player_summary` has identical values and row order; only the label column `calibration_version` differs.
   That label is `walk_forward:` plus the first 12 hex of the calibrator state file's hash, and the state file
   records the build time and the paths of its sources. The other four differing files are metadata
   (timings, git sha, paths, write times).
2. **At different build times the projections differ, by design.** The live feature rows are built as of the build
   moment (`asof_ts = now`, `ffml/sim/predict.py` step 4). Diffing the 2026 w10 rows built at r0's and r1's times
   (same snapshots) shows exactly one model input that changes: `depth__chart_age_days` (`asof_ts` minus the
   chart's `snapshot_ts`; FEATURES.md), in all 458 player rows (0.325 days at 14:49). Over the 2 h 16 min from r0
   to r1, every one of the 1,458 `pred_mean` values moved: mean absolute change 0.096 points, largest 0.66 (a
   quarterback), against a median `pred_mean` of 5.05. Two minutes apart (r1, r2) the projections are identical.
3. **No run-to-run randomness.** Seeds are fixed per season and week (`seeds` in the commitment); the 23 model files
   are byte-identical in all four builds; four independent refits of the w09 walk-forward part are byte-identical.
   The D-108 thread effect does not appear on this machine.

## What this means for the live test

- A commitment is a deterministic function of the code, the data snapshots and the build time, and it records all
  three (`code.commit`, `snapshots`, `as_of` / `rows_as_of`). With those, the build is reproducible exactly.
- The verdict scores only the committed projections, sealed weekly. Nothing is rebuilt: a failed build is retried,
  and a built but unpushed commitment is pushed as it is. So the build-time dependence cannot make a scored
  projection differ from the committed one.
- D-108 covers the production path for thread effects (4 threads, 8 cores). It does not cover the build-time
  dependence, which is a property of the frozen feature design, not nondeterminism.
- Making projections independent of the build time would change frozen code (`ffml/features/`, `ffml/sim/`) and
  the model's inputs. It is not needed for reproducibility and is not done.
- For the Evaluator: training rows measure the chart age at `cutoff_ts - 1 hour`; live rows measure it at the build
  moment, about 24 hours before the first kickoff under D-106. That follows from committing a day ahead; it is
  listed here so the freeze review sees it.
