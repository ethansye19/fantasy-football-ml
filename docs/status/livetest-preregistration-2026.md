# chain_v2 live-test pre-registration (2026 weeks 10-18; D-098, D-105 to D-108)

DRAFT, written 2026-11-05 by the ML Engineer. Two fields stay open until the
freeze: the frozen code commit and the rule set that D-105's limits select
from the Evaluator's candidate report. Everything else is as built and
verified. Once frozen, nothing in this file may change except those two
fields, and the freeze happens before the Week 10 kickoff
(2026-11-13 01:15 UTC) or the test starts at the next full week.

The JSON block is the machine-readable record. An evaluator-side check in the
style of `ffml/eval/report_holdout.py` (`load_prereg` reads the first fenced
`json` block) verifies it against each week's commitment before any metric
is computed; any mismatch makes that week's result `INVALID`.

```json
{
  "test": "livetest_2026",
  "focus_model": "chain_v2",
  "raw_model_label": "chain_v1",
  "candidate_scheme": "chain_v2-primary-c{season}w{week:02d}-{UTC stamp YYYYMMDDTHHMMSSZ}",
  "commit": "<frozen commit, to be filled at freeze>",
  "allowed_changes_after_freeze": ["docs/status/livetest-preregistration-2026.md"],
  "season": 2026,
  "weeks": [10, 11, 12, 13, 14, 15, 16, 17, 18],
  "deadline_rule": "the official schedule's first regular-season kickoff of the week, read from dim_game at build time (D-106)",
  "deadlines_utc_as_scheduled_2026-11-04": {
    "10": "2026-11-13T01:15:00Z", "11": "2026-11-20T01:15:00Z", "12": "2026-11-26T01:00:00Z",
    "13": "2026-12-04T01:15:00Z", "14": "2026-12-11T01:15:00Z", "15": "2026-12-18T01:15:00Z",
    "16": "2026-12-25T01:15:00Z", "17": "2027-01-01T01:15:00Z", "18": "2027-01-10T18:00:00Z"
  },
  "build": {
    "command": "ffml livetest build-week --season 2026 --week {week}",
    "start": "at least 24 h before the deadline, retried every 30 min until a valid commitment is pushed (D-106, D-107)",
    "rows_as_of": "min(build start time, first kickoff)",
    "cutoff": "the week's first kickoff (game-derived features); training and calibration use only weeks before W whose games are final",
    "exit_codes": {"0": "committed", "1": "failure", "2": "blocked (approval gate, or week not in dim_game)", "3": "at or after first kickoff", "4": "week already committed"},
    "commitment": "data/livetest/2026w{week:02d}/commitment.json",
    "digest": "SHA-256 of the canonical JSON (sorted keys, separators , and :) of every top-level field except digest",
    "branch": "livetest-2026",
    "pushed_files": ["2026w{WW}/commitment.json", "2026w{WW}/player_summary.parquet (copy, when under 5 MB)"],
    "external_timestamp": "GitHub push record: repos/{owner}/{repo}/activity?ref=refs/heads/livetest-2026, push_activity.timestamp",
    "one_commitment_per_week": true,
    "code_worktree": "a worktree pinned at the frozen commit with FFML_DATA_DIR and FFML_ARTIFACTS_DIR set to the main checkout"
  },
  "pins": {
    "hash_rule": "tracked text files: SHA-256 of bytes with CRLF normalized to LF; artifact and data files: SHA-256 as they are",
    "selection": {
      "path": "ffml/models/chain/selection.json",
      "sha256": "75477b2b97bc7d9bef935c2cfd470158d5b809b62f531fe898de2f7d2da84601",
      "sha256_crlf_working_copy": "d6c1891828657b76de9cb5aab51a314cd5d85c014ad4a2ba4546b9ad2e82e0da"
    },
    "feature_manifest": {
      "path": "tests/fixtures/features/feature_manifest_0.2.0.json",
      "sha256": "b119f85cdfdc3546b3fb053ca480da6b5e543349940080a3bd19ae692f2c42f6"
    },
    "feature_version": "0.2.0",
    "checked_by": "ffml.models.chain_v2.check_pins at every build; the values are copied into commitment.pins"
  },
  "model": {
    "config": "primary",
    "stage_choices": "ffml/models/chain/selection.json (LightGBM for 19 of 30 stages)",
    "hyperparameters": "ffml/models/chain (unchanged since chain_v1)",
    "lightgbm_num_threads": 4,
    "lightgbm_num_threads_note": "D-108: 8 through validation, holdout and forward runs; 4 since 2026-11-05 with byte-identical rows on the four identity weeks and the 2026 w8 part",
    "seed": 20261101,
    "week_seed": "seed + 100*season + week",
    "forced_pass_seed": "week_seed + 7",
    "n_sims": 2000,
    "availability_config": "final_report",
    "refit": "fit((season, week)) every week: every final regular-season row before the week, 2016 through 2026 week W-1",
    "training_data_note": "2025 released as training data and history on 2026-11-04 with approved=false (artifacts/holdout_release.json); not an approval"
  },
  "calibration": {
    "mode": "walk_forward",
    "version": "v3-tie-aware-quantile",
    "quantiles": "OnlinePITQuantile(levels=(0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95), by=('position',), min_count=50, seed=0, atoms='nearest')",
    "isotonic": "OnlineIsotonic(pairs=(('boom_prob', 'boom_actual'), ('bust_prob', 'bust_actual')), by=('position',), min_count=200)",
    "history": "every regular-season week from 2021 week 1 through week W-1, games that kicked off before week W's first kickoff; PIT bounds pos_min/pos_max = position extremes of earlier seasons",
    "state": "ffml.models.chain_v2.calibrator_state, saved as calibration_state.json in the week's artifact, SHA-256 in commitment.calibrator_state.sha256 and in the registry entry",
    "applied_to": "player_summary.parquet (model chain_v2, calibration_version walk_forward:<sha12>); raw rows kept in player_summary_raw.parquet (model chain_v1)",
    "identity": "equal to ffml.eval.harness.calibrated_rows on default_20261102 (2021 w1, 2021 w3, 2022 w1, 2023 w9, 2024 w18) and on real 2026 w8 history"
  },
  "history_sources": {
    "stored_runs": {
      "default_20261102": {"seasons": [2021, 2022, 2023, 2024], "weeks": "1-18", "predictions_sha256": "58d782bfb3336b569974ef1704a0482f883b4e91b2e758c6cc9ee245a52b82d7", "git": "82b4f6f"},
      "holdout_2025": {"seasons": [2025], "weeks": "1-18", "predictions_sha256": "181c22b6407cee10342485ee568e653efee00fb0a15c45b10d7db2abc7a1609e", "git": "294aac0"},
      "forward_2026": {"seasons": [2026], "weeks": "1-7", "predictions_sha256": "409896a4ca6622bdd26641cd4bf8292fb0cbfccfd465ca109264cf02f5664902", "git": "294aac0"}
    },
    "stored_parts": {
      "paths": "data/backtests/<run>/parts/chain_v1/<season>_wNN.parquet",
      "count": 97,
      "digest": "309ca749e087c9facd0beec9a63fba23f5d1596ea039ce33853c9a67369ffa54",
      "digest_rule": "SHA-256 over sorted 'path sha256' lines (ffml.sim.livetest.digest_of)"
    },
    "walkforward_cache": {
      "path": "data/livetest/walkforward/parts/chain_v1/<season>_wNN.parquet",
      "rule": "append-only; each week from 2026 w8 on is computed once by the same walk-forward (fit((S, w)) on final earlier rows, predict w from the stored feature rows, ffml.eval.harness.check_frame) and never recomputed; a JSON sidecar records sha256, feature snapshot ids, git and timings",
      "listed_in": "commitment.calibration_history.sources (every part with origin and sha256) and commitment.calibration_history.sha256",
      "rehearsal_2026w08_sha256": "15c4b3feb81005e6addc279a9fcff0fff716fb6824567b3b098fd2b83f9ab3a5"
    },
    "outcomes": "ffml.eval.harness outcome_table, position_bounds, week_cutoffs on the latest processed data at build time"
  },
  "labels": {"environment": "private_test", "approval_status": "not approved", "label": "not approved", "store": "data/predictions_private (never the public store; never shown in the app, D-099)"},
  "missed_weeks": {
    "allowed": 1,
    "first_miss": "excluded from scoring; the window is not extended; the lower pass rate of an eight-week test is reported",
    "second_miss": "the test is INVALID (not failed)",
    "counts_as_miss": "no valid commitment on livetest-2026 at the first kickoff, for a real technical failure or an offline machine, logged with its reason in 2026w{WW}/MISSED.json",
    "late_commitments": "built before kickoff but pushed after it go to 2026w{WW}/late/; projections that exist are never withheld; not part of the test",
    "decision": "D-106"
  },
  "rules": {
    "selected_by": "D-105 from reports/evaluator_live_test_power_candidates.md",
    "selection": "<pending>",
    "unchanged": "coverage and start/sit lines, the metrics, the D-078 week-clustered paired bootstrap",
    "lines_sha256": "ea706e17578e6b5caa85eb9db3fbffa6758a6bc85dae2169a707b425b91de2ad",
    "lines_note": "the 22 lines of ffml.eval.report_holdout.prereg_lines (the holdout's set), reported unchanged over 2026 weeks 10-18 plus 2027 weeks 1-9",
    "provisional_approval": "D-105 item 4: a pass under the resized design is provisional approval, with claims limited to what the rules tested; if the 22-line window through 2027 week 9 fails, provisional approval is removed"
  },
  "n_boot": 500,
  "seed": 0,
  "metric_seed": 0,
  "scoring": {
    "when": "only after every regular-season game of the week is final in dim_game",
    "reads": ["livetest-2026:2026w{WW}/commitment.json", "livetest-2026:2026w{WW}/player_summary.parquet when present, else the local file the commitment lists (same SHA-256)", "actuals from ffml.scoring.actuals on the latest processed data"],
    "verifies": ["digest", "every listed file hash (outputs and calibration_history.sources)", "created_at before first_kickoff", "GitHub push_activity.timestamp before first_kickoff", "code.commit equals the frozen commit (or an allowed descendant)", "pins equal this block", "model_version matches the scheme and the week", "calibrator_state.history_last == [2026, W-1]", "labels not approved / private_test"]
  }
}
```

## What is frozen

| Item | Value |
| --- | --- |
| Code commit | `<frozen commit, to be filled at freeze>`. Builds run only from a worktree pinned there (D-107); every commitment records `code.commit`, `commit_at_end`, `head_moved` and the clean-tree flags. The only file a descendant may change is this one |
| Candidate | one registry **candidate** per week, `chain_v2-primary-c2026w{WW}-<UTC stamp>`, registered by the build with its calibrator state as the calibration file; never approved or set live during the test |
| Raw chain | chain_v1's primary configuration, unchanged: `ffml/models/chain`, stage choices `selection.json` (LightGBM for 19 of 30 stages), features 0.2.0, every hyperparameter |
| Pins | selection.json SHA-256 `75477b2b97bc7d9bef935c2cfd470158d5b809b62f531fe898de2f7d2da84601` (LF bytes; the holdout pre-registration quoted the CRLF working-copy form `d6c1891...` of the same file); feature manifest pin `b119f85cdfdc3546b3fb053ca480da6b5e543349940080a3bd19ae692f2c42f6`; calibrator reprs as in the JSON block. `check_pins` runs at every build and the values are copied into `commitment.pins` |
| Seeds, sims | chain seed 20261101, week seed `seed + 100*season + week`, forced pass `+7`, 2,000 simulations; calibrator seed 0; metric (randomized PIT) seed 0; bootstrap 500 week-clustered paired draws, seed 0 |
| LightGBM threads | 4 (D-108). The validated and stored rows were produced at 8; at 4 the four identity weeks, the 2026 w8 part and the Week 9 state are byte-identical (docs/status/phase4-ml-engineer-chain-v2.md, section 7) |
| Availability | live layer `final_report` (D-065): the chain's primary P(active) capped by the section G rules, game-day `INA`, Sleeper statuses as tie-breaker |
| Training | (a) `fit((2026, W))` every week on every final regular-season row before week W: 2016-2024, 2025 (released as data, not approved) and 2026 weeks 1..W-1 |
| Calibration | (b) walk-forward, `v3-tie-aware-quantile`: per-position `OnlinePITQuantile(seed=0, min_count=50, atoms="nearest")` and per-position `OnlineIsotonic(min_count=200)`, fitted for week W on 2021 w1..W-1 (games before W's first kickoff). The state is saved and hashed per week (`calibration_state.json`; `commitment.calibrator_state.sha256`) and applied to `player_summary.parquet`; raw rows stay in `player_summary_raw.parquet` |
| History sources | the three stored runs' raw chain parts (97 files, digest `309ca749...fa54`), then the append-only walk-forward cache from 2026 w8 on, every part listed with its SHA-256 in the commitment |
| Outputs | private test only: `environment: private_test`, `approval_status: not approved`, `label: not approved` in the manifest, `LATEST` of the private store and the summary column; nothing in the app (D-099) |

## Candidate per week

`ffml livetest build-week --season 2026 --week W` (interface in
`ffml/sim/livetest.py`), started at least 24 hours before the deadline and
retried every 30 minutes (D-106, D-107):

1. deadline check (exit 3 at or after the first kickoff), one commitment per
   week (exit 4), pins;
2. calibration history: stored parts plus cached walk-forward weeks;
3. refit at `(2026, W)`, artifact `artifacts/models/chain_v2/<version>/`;
4. calibrator state for week W, registered as a candidate;
5. private-test prediction of week W, `rows_as_of = min(now, first kickoff)`
   (depth charts, rosters, injuries, Sleeper statuses as of the build;
   game-derived features at the week's cutoff, the first kickoff); no rest
   of season;
6. `data/livetest/2026w{WW}/commitment.json` with every output file's
   SHA-256, the artifact and state hashes, the history sources, snapshot
   ids, code commit, `as_of`, `rows_as_of`, `cutoff`, `created_at`,
   `first_kickoff`, pins, run time, peak memory and the digest.

The runner (`scripts/livetest_week.py`) pushes `2026w{WW}/commitment.json`
and, when under 5 MB, `2026w{WW}/player_summary.parquet` to the orphan
branch `livetest-2026`; GitHub's push record is the external timestamp.
Running early never changes the cutoff; it only means fewer late roster and
injury updates.

## Rule set

Selected by D-105 from `reports/evaluator_live_test_power_candidates.md`:
**\<pending\>**. The accuracy lines are resized to the nine-week window;
coverage and start/sit lines, the metrics and the D-078 bootstrap are
unchanged. Calibrated forms (`chain_v2`) against each comparator's calibrated
form on identical player-weeks, 95% week-clustered paired bootstrap
intervals, metric seed 0.

The original 22 lines (`lines_sha256` above, the holdout's set: L01-L12
beats three baselines at QB-TE on MAE and pinball; L13-L16 start/sit not
worse than the consensus; L17-L22 tie-aware coverage in [0.75, 0.85] at six
positions) keep being computed and reported, unchanged, over 2026 weeks
10-18 plus 2027 weeks 1-9. A pass of the resized design means
**provisional approval** with claims limited to what those rules tested; if
the 22-line window through 2027 week 9 fails, provisional approval is removed
and the app returns to "no approved model" (D-105 item 4).

## Missed weeks (D-106)

One miss is allowed: the first missed week is excluded from scoring and the
window is not extended; a second miss makes the test INVALID (not failed).
A week is missed when no valid commitment is on `livetest-2026` at its first
kickoff, for a real technical failure or an offline machine; the reason is
logged in `2026w{WW}/MISSED.json`. A commitment built before kickoff but
pushed after it goes to `2026w{WW}/late/` and is not part of the test;
projections that exist are never withheld. With one week excluded, the frozen
rules are unchanged and the eight-week pass rate is reported.

## Evaluator-side scoring (stub specification)

The scorer is the Evaluator's; this fixes what it reads and checks.

- **When.** A week is scored only after every regular-season game of that
  week is final in `dim_game` (after the refresh that brings those finals).
  Never before, and never from projections that are not on the branch.
- **Reads.** From `livetest-2026`: `2026w{WW}/commitment.json`, and
  `2026w{WW}/player_summary.parquet` when present (else the local file the
  commitment lists, which must hash to the listed SHA-256). Actuals from
  `ffml.scoring.actuals.actual_statlines` on the latest processed data,
  scored under the `ppr`, `half` and `std` presets (`ffml/scoring`, the
  scoring of record, D-079); outcomes and PIT bounds from the
  `ffml.eval.harness` functions (`outcome_table`, `position_bounds`,
  `week_cutoffs`); comparators (the three transparent baselines and, for
  start/sit, the FantasyPros consensus under its validity rule) computed for
  the same week on the same population, calibrated with the same
  walk-forward method on their own history.
- **Verifies before any metric** (any failure: `INVALID` for the week, with
  the reason): the commitment digest (canonical JSON of every other field);
  the SHA-256 of every file in `outputs` and in
  `calibration_history.sources`; `created_at < first_kickoff`; the GitHub
  push record `push_activity.timestamp < first_kickoff` for the commit that
  added the file (`repos/{owner}/{repo}/activity?ref=refs/heads/livetest-2026`);
  `code.commit` equals the frozen commit (or a descendant whose only change is
  this file); `pins` equal this block; `model_version` matches the scheme
  and the week; `calibrator_state.history_last == [2026, W-1]` and
  `calibrator_state.calibration_version == v3-tie-aware-quantile`; the stored
  parts listed under `stored:` origins hash as in the stored runs (digest
  `309ca749...fa54` over those 97); labels `private_test` / `not approved`;
  the file is immutable (same blob at scoring time as at push time).
- **Computes.** The rule set above on the calibrated rows (`model ==
  chain_v2`), with `metric_seed 0`, `n_boot 500`, `seed 0`; the 22 lines
  as information every week; cumulative results after each week; the final
  verdict after Week 18 (or after the eighth scored week with one miss).
  Results go to `reports/evaluator_livetest_2026_w{WW}.md` and a running
  `reports/evaluator_livetest_2026.md`; a pass is recorded as provisional
  approval through `ffml.models.registry.approve` only by the Evaluator and
  only with the `Verdict: APPROVED` line.

## Known facts recorded before the freeze

- chain_v1 was not approved on 2025 (D-097): 21 of 22 lines passed, QB
  tie-aware coverage 0.739. 2025 is training data and history since
  2026-11-04 (`approved=false`).
- The walk-forward calibrator is the validation run's own method; on
  2022-2024 its tie-aware coverage was QB 0.779, RB 0.785, WR 0.786, TE
  0.793, K 0.807, DST 0.820 (report v3). Nothing was tuned on 2025.
- Under the unchanged 22 lines, a well-calibrated model with the validated
  accuracy passes nine weeks about 10% of the time (4% variance-corrected);
  this is why D-105 resizes the accuracy lines before the freeze.
- Rehearsal of the machinery: Week 9, built 2026-11-04 23:36 UTC (25.6 h
  before kickoff), commitment digest
  `a69e91379d1f23a88f858b0d5e221ea4a36f70d30f33cd94b30583bf3fe640c2`, 386 s,
  4.01 GB; not part of the test.
- QB, K and DST boom/bust are low confidence (D-089, D-104) and are not among
  the lines.
- Deadlines above are the schedule as of `dim_game 20261104T233034Z`; the
  binding deadline is the official first kickoff at build time, including
  Thanksgiving (Week 12) and other early games.
