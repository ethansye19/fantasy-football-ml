# chain_v2 live-test pre-registration (2026 weeks 10-18 and 2027 weeks 1-9; D-098, D-105 to D-110)

Final draft, 2026-11-05, ML Engineer; the Evaluator reviews it next and the
orchestrator fills the frozen commit at the freeze, before the Week 10
kickoff (2026-11-13 01:15 UTC). After the freeze nothing in this file may
change except the commit field. Companion file:
`docs/status/livetest-changelog-2026.md` (every allowed change during the
window, see "Frozen code policy").

The JSON block is the machine-readable record. An evaluator-side check in the
style of `ffml/eval/report_holdout.py` (`load_prereg` reads the first fenced
`json` block) verifies it against each week's commitment before any metric
is computed; any mismatch makes that week `INVALID`. `rules_sha256` is the
SHA-256 of the canonical JSON of the `rules` object without the
`rules_sha256` key: `json.dumps(obj, sort_keys=True, separators=(",", ":"),
ensure_ascii=False)` encoded as UTF-8 (`rules.rules_sha256_definition`).

```json
{
  "test": "livetest_2026",
  "design": "fallback (D-105 item 3, D-110): the unchanged 22 lines, applied once over the window",
  "focus_model": "chain_v2",
  "raw_model_label": "chain_v1",
  "candidate_scheme": "chain_v2-primary-c{season}w{week:02d}-{UTC stamp YYYYMMDDTHHMMSSZ}",
  "commit": "<frozen commit>",
  "frozen_commit_must_include": [
    "1d0d2b6",
    "2872d13",
    "6e29122",
    "a2f2af0",
    "a30cd08",
    "b22973f",
    "bd3bf21",
    "d24a43a"
  ],
  "commit_relation": "a build, a refresh and the scorer are valid at the frozen commit X, or at a descendant Y whose `git diff --name-only X Y` touches only paths under allowed_paths (any other path is frozen); a frozen-path change invalidates the test unless the owner rules otherwise; the scorer records X, Y and the diff for every week",
  "window": {
    "seasons": [
      2026,
      2027
    ],
    "weeks": {
      "2026": [
        10,
        11,
        12,
        13,
        14,
        15,
        16,
        17,
        18
      ],
      "2027": [
        1,
        2,
        3,
        4,
        5,
        6,
        7,
        8,
        9
      ]
    },
    "weeks_total": 18,
    "verdict_after": "after every window week is final; at the earliest after 2027 week 9's games are final in dim_game, also when 2027 week 9 is the excluded miss; the scorer refuses the final before that",
    "weekly_scoring": "on the Friday job at or after the week's data cutoff: a verification-only report plus sealed metric parts; no weekly output shows a metric or a Verdict line"
  },
  "rules": {
    "design": "fallback (D-105 item 3, D-110): no candidate qualified (best good-model pass 0.614 < 0.70)",
    "lines": "the 22 lines of docs/status/holdout-preregistration.md, unchanged: L01-L12 beats rolling_avg_4, prior_season and rolling_median_8 at QB, RB, WR, TE on MAE and pinball q10, q50, q90; L13-L16 start/sit on the D-077 pair set not worse than the FantasyPros consensus at QB, RB, WR, TE; L17-L22 tie-aware coverage of the 80% interval in [0.75, 0.85] at QB, RB, WR, TE, K, DST",
    "lines_sha256": "ea706e17578e6b5caa85eb9db3fbffa6758a6bc85dae2169a707b425b91de2ad",
    "lines_source": "ffml.eval.report_holdout.prereg_lines (lines_spec_sha256)",
    "forms": "calibrated chain_v2 rows against each comparator's calibrated form on identical player-weeks; 95% week-clustered paired bootstrap (D-078); metric seed 0",
    "applied": "once, over every scored week of the window (18 weeks, or 17 with one excluded miss)",
    "verdict": "Verdict: APPROVED only if all 22 lines pass over the scored weeks, else Verdict: NOT APPROVED; INVALID on a third miss or any failed integrity check; computed once by the Evaluator after every window week is final, at the earliest after 2027 week 9's games are final in dim_game; a failed GitHub fetch or ls-remote check at the final records no verdict (exit 75) and the final is retried (D-116)",
    "provisional_tier": "none (D-110); the app shows no approved model for the rest of 2026",
    "rules_sha256": "8c4212e3d77614c7f34a923a2b5905b6aa05173e4439aaaace3d0f28a0ba7574",
    "rules_sha256_definition": "SHA-256 of the canonical JSON of this rules object without the rules_sha256 key: json.dumps(obj, sort_keys=True, separators=(',', ':'), ensure_ascii=False) encoded as UTF-8",
    "comparators": {
      "run": "ffml.eval.harness run of the config livetest_comparators (ffml/eval/backtest_configs.py): the five transparent baselines plus the direct challenger (information only), no chain; online v3 calibration warmed from 2021 exactly as the default config; seasons through 2027; purpose livetest_score; resumable parts; run incrementally at scoring time at the frozen commit",
      "rows_used": "each comparator's calibrated rows (rolling_avg_4+cal, prior_season+cal, rolling_median_8+cal; rolling_avg_3 and rolling_ew_035 reported) for the scored week",
      "reference_ranking": "D-077 reference set from the same frame by ffml.eval.report_backtest.reference_models and ffml.eval.metrics.with_reference_rank, as in the holdout: chain_v2 raw, the five baselines and direct_lgbm, plus consensus_ecr in the matched population",
      "consensus": "FantasyPros weekly consensus for 2026-2027 from the DynastyProcess archive via nflreadpy, matched with the holdout's rules (valid_for_game, same player-weeks); weeks without a consensus scrape are excluded from L13-L16 only, as week 18 was in validation, and the final report lists them"
    }
  },
  "deadline_rule": "the binding deadline of a week is the earlier of (a) the first regular-season kickoff as scheduled in dim_game at build time, recorded as commitment.first_kickoff, and (b) the actual first kickoff per the final schedule; created_at and the GitHub push timestamp must precede both (D-106); while a week's kickoff times are tentative (every game at one identical time) the runner's deadline is that time minus 24 h (D-109, scripts/livetest_core.py TENTATIVE_LEAD)",
  "deadlines_utc_as_scheduled_2026-11-04": {
    "2026w10": "2026-11-13T01:15:00Z",
    "2026w11": "2026-11-20T01:15:00Z",
    "2026w12": "2026-11-26T01:00:00Z",
    "2026w13": "2026-12-04T01:15:00Z",
    "2026w14": "2026-12-11T01:15:00Z",
    "2026w15": "2026-12-18T01:15:00Z",
    "2026w16": "2026-12-25T01:15:00Z",
    "2026w17": "2027-01-01T01:15:00Z",
    "2026w18": "2027-01-10T18:00:00Z (tentative: every game at one time)",
    "2027w01-09": "unknown until the 2027 schedule is published (May 2027); read from dim_game at build time"
  },
  "build": {
    "command": "ffml livetest build-week --season {season} --week {week}",
    "timing": "D-109: window opens K-25h, first run K-24h15m, retries every 30 min, build cutoff K-30m, push deadline K-5m; K = the deadline above",
    "rows_as_of": "min(build start time, first kickoff)",
    "cutoff": "the week's first kickoff (game-derived features); training and calibration use only weeks before W whose games are final",
    "exit_codes": {
      "0": "committed",
      "1": "failure",
      "2": "blocked (approval gate, or week not in dim_game)",
      "3": "at or after first kickoff",
      "4": "week already committed"
    },
    "commitment": "data/livetest/{season}w{week:02d}/commitment.json",
    "digest": "SHA-256 of the canonical JSON (sorted keys, separators , and :) of every top-level field except digest",
    "branch": "livetest-2026",
    "pushed_files": [
      "{season}w{WW}/commitment.json",
      "{season}w{WW}/player_summary.parquet (copy, when under 5 MB)"
    ],
    "external_timestamp": "GitHub push record: repos/{owner}/{repo}/activity?ref=refs/heads/livetest-2026, push_activity.timestamp",
    "one_commitment_per_week": true,
    "code_worktree": "a worktree pinned at the frozen commit with FFML_DATA_DIR and FFML_ARTIFACTS_DIR set to the main checkout; data refresh runs the pinned ffml.data.refresh module (a2f2af0)"
  },
  "frozen_paths": [
    "ffml/models/",
    "ffml/models/calibration.py",
    "ffml/models/chain_v2.py",
    "ffml/models/chain/selection.json",
    "ffml/features/",
    "ffml/sim/",
    "ffml/scoring/",
    "ffml/eval/livetest_score.py",
    "ffml/eval/harness.py",
    "ffml/eval/backtest_configs.py",
    "ffml/eval/ecr_baseline.py",
    "ffml/eval/metrics.py",
    "ffml/eval/report_backtest.py",
    "ffml/eval/report_holdout.py",
    "ffml/holdout.py",
    "ffml/cli.py",
    "ffml/config.py",
    "ffml/season.py",
    "pyproject.toml",
    "requirements.lock.txt",
    "tests/fixtures/features/feature_manifest_0.2.0.json"
  ],
  "allowed_paths": {
    "ffml/data/": "logged in docs/status/livetest-changelog-2026.md before first use; upstream nflverse or Sleeper schema or source fixes only; the recomputed feature rows for every already-committed week must be byte-identical to the stored ones, verified by the Evaluator before first use",
    "ffml/eval/power.py": "logged in docs/status/livetest-changelog-2026.md before first use; Evaluator power estimates; no effect on commitments or scoring",
    "ffml/eval/audits/": "logged in docs/status/livetest-changelog-2026.md before first use; Evaluator audits; no effect on commitments or scoring",
    "tests/": "logged in docs/status/livetest-changelog-2026.md before first use; tests only (the pinned manifest file under tests/fixtures/features/ is frozen)",
    ".github/": "logged in docs/status/livetest-changelog-2026.md before first use; CI and watchdog workflows; no effect on commitments",
    "scripts/": "logged in docs/status/livetest-changelog-2026.md before first use; runner, scheduler, watchdog, alerts; no effect on commitments",
    "api/": "logged in docs/status/livetest-changelog-2026.md before first use; application; no effect on commitments",
    "web/": "logged in docs/status/livetest-changelog-2026.md before first use; application; no effect on commitments",
    "docs/": "logged in docs/status/livetest-changelog-2026.md before first use; documentation; the pre-registration itself changes only in its commit field at the freeze",
    "reports/": "logged in docs/status/livetest-changelog-2026.md before first use; evaluator reports",
    "README.md": "logged in docs/status/livetest-changelog-2026.md before first use; documentation",
    ".env.example": "logged in docs/status/livetest-changelog-2026.md before first use; documentation of settings; no effect on commitments",
    ".dockerignore": "logged in docs/status/livetest-changelog-2026.md before first use; packaging; no effect on commitments",
    "docker-compose.yml": "logged in docs/status/livetest-changelog-2026.md before first use; deployment; no effect on commitments",
    "Dockerfile.api": "logged in docs/status/livetest-changelog-2026.md before first use; deployment; no effect on commitments",
    "Dockerfile.web": "logged in docs/status/livetest-changelog-2026.md before first use; deployment; no effect on commitments"
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
    "lightgbm_num_threads_note": "D-108: 8 through validation, holdout and forward runs; 4 since 6e29122 with byte-identical rows on the four identity weeks, the 2026 w8 part and the Week 9 state",
    "seed": 20261101,
    "week_seed": "seed + 100*season + week",
    "forced_pass_seed": "week_seed + 7",
    "n_sims": 2000,
    "availability_config": "final_report",
    "refit": "fit((season, week)) every week: every final regular-season row before the week, 2016 through the week before",
    "training_data_note": "2025 released as training data and history on 2026-11-04 with approved=false (artifacts/holdout_release.json); not an approval"
  },
  "calibration": {
    "mode": "walk_forward",
    "version": "v3-tie-aware-quantile",
    "quantiles": "OnlinePITQuantile(levels=(0.05, 0.1, 0.25, 0.5, 0.75, 0.9, 0.95), by=('position',), min_count=50, seed=0, atoms='nearest')",
    "isotonic": "OnlineIsotonic(pairs=(('boom_prob', 'boom_actual'), ('bust_prob', 'bust_actual')), by=('position',), min_count=200)",
    "history": "every regular-season week from 2021 week 1 through the week before, games that kicked off before the week's first kickoff; PIT bounds pos_min/pos_max = position extremes of earlier seasons",
    "state": "ffml.models.chain_v2.calibrator_state, saved as calibration_state.json in the week's artifact, SHA-256 in commitment.calibrator_state.sha256 and in the registry entry",
    "applied_to": "player_summary.parquet (model chain_v2, calibration_version walk_forward:<sha12>); raw rows kept in player_summary_raw.parquet (model chain_v1)",
    "identity": "equal to ffml.eval.harness.calibrated_rows on default_20261102 (2021 w1, 2021 w3, 2022 w1, 2023 w9, 2024 w18) and on real 2026 w8 history"
  },
  "history_sources": {
    "stored_runs": {
      "default_20261102": {
        "seasons": [
          2021,
          2022,
          2023,
          2024
        ],
        "weeks": "1-18",
        "predictions_sha256": "58d782bfb3336b569974ef1704a0482f883b4e91b2e758c6cc9ee245a52b82d7",
        "git": "82b4f6f"
      },
      "holdout_2025": {
        "seasons": [
          2025
        ],
        "weeks": "1-18",
        "predictions_sha256": "181c22b6407cee10342485ee568e653efee00fb0a15c45b10d7db2abc7a1609e",
        "git": "294aac0"
      },
      "forward_2026": {
        "seasons": [
          2026
        ],
        "weeks": "1-7",
        "predictions_sha256": "409896a4ca6622bdd26641cd4bf8292fb0cbfccfd465ca109264cf02f5664902",
        "git": "294aac0"
      }
    },
    "stored_parts": {
      "paths": "data/backtests/<run>/parts/chain_v1/<season>_wNN.parquet",
      "count": 97,
      "digest": "309ca749e087c9facd0beec9a63fba23f5d1596ea039ce33853c9a67369ffa54",
      "digest_rule": "SHA-256 over sorted 'path sha256' lines (ffml.sim.livetest.digest_of)"
    },
    "walkforward_cache": {
      "path": "data/livetest/walkforward/parts/chain_v1/<season>_wNN.parquet",
      "rule": "append-only; each week from 2026 w8 on, and every 2027 week, is computed once by the same walk-forward (fit((S, w)) on final earlier rows, predict w from the stored feature rows, ffml.eval.harness.check_frame) and never recomputed; a JSON sidecar records sha256, feature snapshot ids, git and timings",
      "listed_in": "commitment.calibration_history.sources (every part with origin and sha256) and commitment.calibration_history.sha256",
      "rehearsal_2026w08_sha256": "15c4b3feb81005e6addc279a9fcff0fff716fb6824567b3b098fd2b83f9ab3a5"
    },
    "outcomes": "ffml.eval.harness outcome_table, position_bounds, week_cutoffs on the latest processed data at build time"
  },
  "labels": {
    "environment": "private_test",
    "approval_status": "not approved",
    "label": "not approved",
    "store": "data/predictions_private (never the public store; never shown in the app, D-099)"
  },
  "missed_weeks": {
    "allowed": 2,
    "first_and_second_miss": "excluded from scoring; the window is not extended; the pass rate of a 17- or 16-week test is reported",
    "third_miss": "the test is INVALID (not failed)",
    "pass_rates_good_model_variance_corrected": {
      "18_weeks_scored": "0.557 (MC SE 0.007)",
      "17_weeks_scored": "0.518 (MC SE 0.007)",
      "16_weeks_scored": "0.500 (MC SE 0.007)",
      "meaning": "even a good model (the validated 2022-2024 accuracy, exactly calibrated) has only about a 56% chance of passing the 22 lines over the 18-week window under the frozen bootstrap; about 52% with one miss and 50% with two"
    },
    "pass_rates_no_edge_model": {
      "18_weeks_scored": "0 passes in 5,000 replicates: estimate 0.000, 95% upper bound 0.0006",
      "17_weeks_scored": "0 passes in 5,000 replicates: estimate 0.000, 95% upper bound 0.0006",
      "16_weeks_scored": "0 passes in 5,000 replicates: estimate 0.000, 95% upper bound 0.0006"
    },
    "late_commitments": "built before kickoff but pushed after it go to {season}w{WW}/late/; projections that exist are never withheld; not part of the test",
    "decision": "D-106 (revisited 2026-11-05)"
  },
  "n_boot": 500,
  "seed": 0,
  "metric_seed": 0,
  "scoring": {
    "when": "each week on the Friday job at or after the week's data cutoff, once every game of the week is final; the final after every window week is scored or missed (D-116)",
    "verdict": "after every window week is final; at the earliest after 2027 week 9's games are final in dim_game, also when 2027 week 9 is the excluded miss; the scorer refuses the final before that",
    "reads": [
      "livetest-2026:{season}w{WW}/commitment.json",
      "livetest-2026:{season}w{WW}/player_summary.parquet when present, else the local file the commitment lists (same SHA-256)",
      "actuals from ffml.scoring.actuals on the latest processed data"
    ],
    "verifies": [
      "digest",
      "every listed file hash (outputs and calibration_history.sources)",
      "created_at before the binding deadline (the earlier of commitment.first_kickoff and the final schedule's actual first kickoff)",
      "GitHub push_activity.timestamp before the binding deadline",
      "code.commit at the frozen commit or an allowed descendant (X, Y and the diff recorded)",
      "pins equal this block",
      "model_version matches the scheme and the week",
      "calibrator_state.history_last == the week before",
      "labels not approved / private_test",
      "the change log covers every descendant commit used"
    ],
    "pit_bounds": "player_summary.parquet carries no pos_min/pos_max; outcomes and PIT bounds are attached with the ffml.eval.harness functions (outcome_table, position_bounds, week_cutoffs) on the processed snapshot current at scoring time, as in the holdout (position extremes of earlier seasons; for 2027 weeks those include 2026); the weekly scoring JSON records that snapshot id",
    "comparators": "rules.comparators: the livetest_comparators run at the frozen commit, reference_models and with_reference_rank for the D-077 set, the consensus archive with the holdout's matching rules",
    "sensitivity_check": {
      "what": "report-only: every bootstrap interval behind the 22 lines is recomputed with 5,000 week-clustered paired draws (seed 0) alongside the binding 500-draw interval",
      "binding": "the 500-draw, seed-0 bootstrap of D-078 is the only verdict rule; the 5,000-draw check can never change a line's PASS/FAIL or the verdict",
      "disclosure": "any line whose 5,000-draw interval would give a different PASS/FAIL than the binding interval is listed in the final report with both intervals; the weekly sealed parts store both (the runner's Friday job calls the scorer with --weekly-sensitivity)",
      "decision": "owner, 2026-11-05 (D-111)"
    },
    "sealed_branch_path": "scoring/{season}w{WW}.sha256 on livetest-2026: the scorer's sealed file pushed verbatim by scripts/livetest_week.py seal (with {season}w{WW}/sealed_hashes.json as the runner's record); the final refuses unless every sealed file matches it",
    "data_cutoff": "12:00 UTC on the first Friday at least 72 hours after the last kickoff of the week in dim_game (`python -m ffml.eval.livetest_score --data-cutoff <week>`); before it `--week` exits 4 (retry later)",
    "snapshot_pins": "for each processed dataset it reads, weekly scoring uses the first snapshot taken at or after the cutoff; the snapshot ids and a SHA-256 of the outcome rows used (`outcome_rows_sha256`) are sealed with the week and recorded in scoring/weeks/<week>.json; a rescore reuses them; corrections after the cutoff are ignored; the final reads only sealed parts, including each week's sealed consensus rows; processed snapshots are never pruned during the window (D-118)",
    "prebuild_check": "before every build the runner runs `--prebuild-check --from <last build's code commit> --to <pinned HEAD>`: every commit in between needs a change-log entry and none may touch a frozen path; a block (exit 5) alerts the owner and retries; it is a miss only if no valid commitment is pushed by the deadline; the final also checks, for every scored week, that each commit between X and that week's build commit was in the change log as it stood at the build commit (D-116 B1, A1, A2)",
    "final_fetch": "`--final` reads the branch only from the fetched GitHub ref, checked against `git ls-remote`, and compares each scored week's commitment and summary with the hashes recorded at weekly scoring; if the fetch or the check fails it exits 75 before computing or writing anything: no Verdict line, no report, no sealed file (D-116 A, A4)",
    "committed_weeks": "a week with a commitment or a sealed record on the branch is always scored and never excluded as a miss; a MISSED marker for such a week is flagged in the final report with its week, the GitHub commit time of the commit that added it and its created_at, and does not change the verdict (QA-F28, D-116 A, A5)",
    "bootstrap_frames": "lines: only the frames the 22 lines read are bootstrapped; identical by construction (one week-clustered draw matrix) and shown identical to the full computation at 500 and 5,000 draws on the rehearsal worlds (reports/livetest_subset_proof_2026-11-07.json; D-116 B2, D-118)"
  },
  "path_policy": "any path not listed in allowed_paths is frozen (the scorer's path_allowed returns false for it); entries are prefixes (a directory entry ends with /); a frozen prefix takes precedence over an allowed one, so frozen_paths names the frozen set explicitly and never contains a prefix of an allowed entry (everything under ffml/ is frozen except ffml/data/, ffml/eval/power.py and ffml/eval/audits/)",
  "miss_rule": "D-106 as revisited by the owner on 2026-11-05: up to two missed weeks are excluded from scoring and the window is not extended; a third miss makes the test INVALID (not failed); a miss is a week with no valid commitment on livetest-2026 at its deadline, for a real technical failure or an offline machine, logged in {season}w{WW}/MISSED.json with the reason",
  "expected_verdict": {
    "when": "after every window week is final; at the earliest after 2027 week 9's games are final in dim_game",
    "expected_date": "about 2027-11-12 (the Friday data cutoff after 2027 week 9; the 2027 schedule is not published, so between about 2027-11-05 and 2027-11-19)",
    "app_until_then": "the real app shows 'no approved model' with projections, start/sit, waivers, trades and rest-of-season rankings off, through the rest of 2026, the 2027 offseason and draft, and 2027 weeks 1-9 (D-099, D-110); v2 outputs exist only in private test, labeled not approved"
  },
  "sealed_interim": {
    "policy": "interim scored results are sealed until the verdict",
    "mechanism": "weekly scoring writes only (a) verification status (digest, hashes, push timestamp, week final) and (b) sealed metric parts under data/livetest/scoring/sealed/ whose SHA-256 is pushed to livetest-2026; no metric, line result or ranking is rendered, printed or committed before the final evaluation",
    "use": "sealed results are not used to tune chain_v2, to choose between weeks, or to stop the test early; the only early end is a documented technical failure or a frozen-path breach, which makes the test INVALID, never a performance reason",
    "unsealing": "only by the Evaluator at the final evaluation, logged in the change log with the sealed hashes verified"
  },
  "outside_changes": {
    "covers": [
      "nflverse schema or source changes",
      "dependency breaks (wheels, upstream packages, Python)",
      "Sleeper API changes",
      "replacing or rebuilding the laptop"
    ],
    "rule": "only logged compatibility fixes that are proven not to change model behaviour, by replay checks against earlier committed weeks: the raw predictions and the calibrator state of the four identity weeks (2022 w6, 2024 w15, 2025 w9, 2026 w4) and of the three most recent committed live-test weeks are rebuilt and must be byte-identical; the recomputed feature rows of every committed week must be byte-identical; the change is logged in docs/status/livetest-changelog-2026.md with the replay evidence and verified by the Evaluator before first use",
    "if_not_provable": "stop; no further commitment is built until the owner rules; the stopped weeks count as misses only if the owner rules so",
    "laptop_replacement": "a new machine gets the pinned worktree at the frozen commit from requirements.lock.txt; before its first live build the replay checks above must pass on it; the lock file is frozen, so a wheel that no longer installs is a dependency break under this rule",
    "sleeper_outage": "the Sleeper tie-breaker is used only when its snapshot is fresh (ffml/sim/predict.py); an outage means a build without it, which is existing frozen behaviour and is recorded in the commitment"
  },
  "v3_policy": {
    "allowed": true,
    "conditions": [
      "developed on a separate branch (v3-dev) that is not merged into main during the window, or under a top-level directory outside every frozen path; it never changes a frozen path",
      "it does not read the sealed scoring outputs, the live-test commitments, or any derived file under data/livetest/scoring/",
      "it may use any data that is final at its own cutoffs, under the same point-in-time rules",
      "it needs its own pre-registered test on weeks unseen by it, approved by the owner before that test opens; passing the v2 test confers nothing on v3",
      "the pinned worktree for v2 never contains v3 code"
    ]
  },
  "estimate": {
    "simulator": "ffml/eval/power.py (Evaluator), fast path shown equal to the real scoring functions on 58 of 58 validated report cells and within 1.8e-15 on thinned test windows",
    "replicates": {
      "18_17_16_week_tables": 5000,
      "candidate_report": 5000,
      "first_report": 10000
    },
    "replicate": "for each week number of the window one 2022-2024 slate of that week number is drawn at random (whole weeks: within-week and within-game correlation kept); 2026 weeks 10-18 are thinned to the 2026 game counts by dropping whole games (immaterial: full slates give 0.568); 2027 weeks 1-9 use full slates because the 2027 schedule is unknown; missed weeks are drawn at random",
    "good_model": "chain_v1+cal's validated 2022-2024 accuracy with coverage made exactly calibrated (rank-based PIT over all rows of a position)",
    "variance_corrected": "week-level differences stretched by sqrt(3/2) about their week-number means, because a three-season pool understates season-to-season variation by about a third in variance; the pre-registration uses the corrected figures",
    "no_edge_model": "every chain-minus-comparator difference centred to zero per week number with its noise kept, start/sit centred to the consensus, coverage as in the good model; its pass rate is the false-approval rate",
    "not_simulated": [
      "season-wide shifts such as the 2025 QB coverage drop (0.779 to 0.739)",
      "schedule changes",
      "misses that are not random",
      "any change in the chain's edge over time"
    ],
    "reconciliation_2026-11-05": {
      "first_report_18_weeks": "0.767 uncorrected / 0.628 corrected (10,000 replicates) was the reference window of weeks 1-18 in calendar order with full slates",
      "fallback_window": "0.722 uncorrected / 0.557 corrected (5,000 replicates) is 2026 weeks 10-18 then 2027 weeks 1-9, the order the test will score in",
      "cause": "the frozen bootstrap (500 week-clustered draws, seed 0, D-078) is a fixed weight matrix; which calendar position gets which weights changes the good-model pass rate by about 5 points between the two orders (same weeks, same game counts: 271.7 vs 269.7; full slates in the fallback order give 0.568). Swapping the seed index of the slate draws leaves the gap (0.610 vs 0.573); other bootstrap seeds give 0.587-0.610 for both orders",
      "which_number": "0.557 is the estimate for the test as it will be run (frozen bootstrap, chronological order); the 0.618-0.628 figure applies to an order the test will not use; 0.59-0.61 is the average over bootstrap seeds. The bootstrap stays as pre-registered; no rule is changed on this evidence",
      "evidence": "docs/status/livetest-power-reconciliation.md; data/reports/live_test_power_reconcile.json and live_test_power_reconcile2.json"
    }
  }
}
```

## What is frozen

| Item | Value |
| --- | --- |
| Code commit | `<frozen commit>`, filled by the orchestrator at the freeze. It must contain a2f2af0 (`ffml refresh` as one module the runner calls pinned), 6e29122 (LightGBM 4 threads, D-108), b22973f (LF-normalized pins, `rows_as_of` cap) and d24a43a (`ffml livetest build-week`). Builds, refreshes and the scorer run only at that commit or at an allowed descendant (next section); every commitment records `code.commit`, `commit_at_end`, `head_moved` and the clean-tree flags |
| Candidate | one registry **candidate** per week, `chain_v2-primary-c{season}w{WW}-<UTC stamp>`, registered by the build with its calibrator state as the calibration file; never approved or set live during the window |
| Raw chain | chain_v1's primary configuration, unchanged: `ffml/models/chain`, stage choices `selection.json` (LightGBM for 19 of 30 stages), features 0.2.0, every hyperparameter |
| Pins | selection.json SHA-256 `75477b2b97bc7d9bef935c2cfd470158d5b809b62f531fe898de2f7d2da84601` (LF bytes; the holdout pre-registration quoted the CRLF working-copy form `d6c1891...` of the same file); feature manifest pin `b119f85cdfdc3546b3fb053ca480da6b5e543349940080a3bd19ae692f2c42f6`; calibrator reprs as in the JSON block. `check_pins` runs at every build and the values are copied into `commitment.pins` |
| Seeds, sims | chain seed 20261101, week seed `seed + 100*season + week`, forced pass `+7`, 2,000 simulations; calibrator seed 0; metric (randomized PIT) seed 0; bootstrap 500 week-clustered paired draws, seed 0 |
| LightGBM threads | 4 (D-108, 6e29122). The validated and stored rows were produced at 8; at 4 the four identity weeks, the 2026 w8 part and the Week 9 state are byte-identical (docs/status/phase4-ml-engineer-chain-v2.md, section 7) |
| Availability | live layer `final_report` (D-065): the chain's primary P(active) capped by the section G rules, game-day `INA`, Sleeper statuses as tie-breaker |
| Training | (a) `fit((season, W))` every week on every final regular-season row before week W: 2016-2024, 2025 (released as data, not approved), 2026 and, in 2027, the 2026 season and 2027 weeks 1..W-1 |
| Calibration | (b) walk-forward, `v3-tie-aware-quantile`: per-position `OnlinePITQuantile(seed=0, min_count=50, atoms="nearest")` and per-position `OnlineIsotonic(min_count=200)`, fitted for week W on 2021 w1..W-1 (games before W's first kickoff). The state is saved and hashed per week (`calibration_state.json`; `commitment.calibrator_state.sha256`) and applied to `player_summary.parquet`; raw rows stay in `player_summary_raw.parquet` |
| History sources | the three stored runs' raw chain parts (97 files, digest `309ca749...fa54`), then the append-only walk-forward cache from 2026 w8 on, every part listed with its SHA-256 in the commitment |
| Outputs | private test only: `environment: private_test`, `approval_status: not approved`, `label: not approved` in the manifest, `LATEST` of the private store and the summary column; nothing in the app (D-099) |

## Frozen code policy for the window (2026-11 to 2027-11)

**Any path not listed as allowed is frozen.** The lists in the JSON block are
the rule; this section explains them.

Frozen (no change at all): everything under `ffml/` except the allowed
entries below (`frozen_paths` lists the frozen set file by file rather than
as `ffml/`, because the scorer gives a frozen prefix precedence over an
allowed one), in particular `ffml/models/` (with `calibration.py`,
`chain_v2.py` and `chain/selection.json`, where the calibrator settings,
pins and seeds live), `ffml/features/`, `ffml/sim/`, `ffml/scoring/`,
`ffml/holdout.py`, `ffml/cli.py`, `ffml/config.py`, `ffml/season.py`, and
in `ffml/eval/` the scorer `livetest_score.py`, `harness.py`,
`backtest_configs.py`, `ecr_baseline.py`, `metrics.py`,
`report_backtest.py` and `report_holdout.py`; plus `pyproject.toml`,
`requirements.lock.txt` and the features manifest pin
`tests/fixtures/features/feature_manifest_0.2.0.json`. A change on a frozen
path invalidates the test unless the owner rules otherwise.

Allowed, one path per entry, each change logged in
`docs/status/livetest-changelog-2026.md` (date, commit, paths, reason,
verification, who verified, first week that uses it) before its first use:

- `ffml/data/`: upstream nflverse or Sleeper schema or source fixes only,
  and only if the recomputed feature rows for every already-committed week
  are byte-identical to the stored ones, verified by the Evaluator before
  first use;
- `ffml/eval/power.py` and `ffml/eval/audits/`: Evaluator estimates and
  audits; no effect on commitments or scoring;
- `tests/`, `.github/`, `scripts/`, `api/`, `web/`, `docs/`, `reports/`,
  `README.md`, `.env.example`, `.dockerignore`, `docker-compose.yml`,
  `Dockerfile.api`, `Dockerfile.web`: no effect on commitments; the
  pre-registration itself changes only in its commit field at the freeze.

### Outside changes during the window

This covers nflverse schema or source changes, dependency breaks (a wheel or
upstream package that no longer installs or imports, a Python change),
Sleeper API changes, and replacing or rebuilding the laptop. Only logged
compatibility fixes that are **proven not to change model behaviour** are
allowed, and the proof is a replay check against earlier weeks: the raw
predictions and the calibrator state of the four identity weeks (2022 w6,
2024 w15, 2025 w9, 2026 w4) and of the three most recent committed live-test
weeks are rebuilt with the fix in place and must be byte-identical to the
stored files, and the recomputed feature rows of every committed week must be
byte-identical. The change, the replay evidence and the Evaluator's
verification go in `docs/status/livetest-changelog-2026.md` before the first
build that uses it. If identity cannot be proven, the test stops: no further
commitment is built until the owner rules, and the stopped weeks count as
misses only if the owner rules so. A replacement machine gets the pinned
worktree at the frozen commit from `requirements.lock.txt` and must pass the
same replay check before its first live build; the lock file is frozen, so a
wheel that no longer installs is a dependency break under this rule. A
Sleeper outage needs no fix: the tie-breaker is used only when its snapshot
is fresh, a build without it is existing frozen behaviour, and the
commitment records it.

Commit relation, as for the holdout: a build, a data refresh or the scorer
is valid at the frozen commit X or at a descendant Y whose
`git diff --name-only X Y` touches only allowed paths. The scorer records
X, Y and the diff for every week; a diff that reaches any other path makes
the week INVALID and stops the test pending the owner's ruling.

## Candidate per week

`ffml livetest build-week --season {season} --week {week}` (interface in
`ffml/sim/livetest.py`), under the D-109 timing: the window opens 25 h
before the deadline, the first run at K-24h15m, retries every 30 minutes,
build cutoff K-30m, push deadline K-5m:

1. deadline check (exit 3 at or after the first kickoff), one commitment per
   week (exit 4), pins;
2. calibration history: stored parts plus cached walk-forward weeks;
3. refit at `(season, W)`, artifact `artifacts/models/chain_v2/<version>/`;
4. calibrator state for week W, registered as a candidate;
5. private-test prediction of week W, `rows_as_of = min(now, first kickoff)`
   (depth charts, rosters, injuries, Sleeper statuses as of the build;
   game-derived features at the week's cutoff, the first kickoff); no rest
   of season;
6. `data/livetest/{season}w{WW}/commitment.json` with every output file's
   SHA-256, the artifact and state hashes, the history sources, snapshot
   ids, code commit, `as_of`, `rows_as_of`, `cutoff`, `created_at`,
   `first_kickoff`, pins, run time, peak memory and the digest.

The runner (`scripts/livetest_week.py`) pushes `{season}w{WW}/commitment.json`
and, when under 5 MB, `{season}w{WW}/player_summary.parquet` to the orphan
branch `livetest-2026`; GitHub's push record is the external timestamp.
Running early never changes the cutoff; it only means fewer late roster and
injury updates.

## Rules (fallback design, D-110)

No candidate rule set met the D-105 limits (the best good-model pass rate
was 0.614 against the 0.70 floor), so under D-105 item 3 the limits are not
weakened and the fallback applies: the **unchanged 22 lines** of
docs/status/holdout-preregistration.md (`lines_sha256` ea706e17...), applied
**once** over 2026 weeks 10-18 plus 2027 weeks 1-9 (18 weeks), with the
D-106 miss rule (up to two excluded weeks, no extension; a third miss
makes the test INVALID). There is no provisional-approval tier; the app shows
"no approved model" for the rest of 2026.

Calibrated forms (`chain_v2`) against each comparator's calibrated form on
identical player-weeks, 95% week-clustered paired bootstrap intervals
(D-078), metric seed 0, bootstrap seed 0, 500 draws. Weekly scoring after a
week's games are final is information only and never prints a verdict line.
`Verdict: APPROVED` (all 22 lines pass over the scored weeks) or
`Verdict: NOT APPROVED` is computed once, by the Evaluator, after every
window week is final, at the earliest after 2027 week 9's games are final in
`dim_game` (also when 2027 week 9 is the excluded miss; the scorer refuses
the final before that); `Verdict: INVALID (<reason>)` on a third miss or a
failed integrity check. Recorded before the freeze: the Evaluator's estimate
for this design is a good-model pass rate of 0.557 (variance-corrected;
0.722 uncorrected) and under 0.06% for a model with no edge, below the 70% floor
that D-105 set for candidates; D-105 item 3 was fixed in advance and is
followed as written.

**Comparators.** The baselines' calibrated rows for each scored week
(`rolling_avg_4+cal`, `prior_season+cal`, `rolling_median_8+cal` for
L01-L12; `rolling_avg_3` and `rolling_ew_035` reported) come from a harness
run at the frozen commit with the config `livetest_comparators`
(`ffml/eval/backtest_configs.py`): the five transparent baselines plus the
direct challenger for information, no chain, online v3 calibration warmed
from 2021 exactly as `default`, seasons through 2027, purpose
`livetest_score`, resumable parts, run incrementally at scoring time. The
D-077 reference ranking for L13-L16 is computed from the same frame by
`report_backtest.reference_models` and `metrics.with_reference_rank`, as in
the holdout (chain_v2 raw, the five baselines and `direct_lgbm`, plus
`consensus_ecr` in the matched population). The consensus for 2026-2027
comes from the DynastyProcess archive via nflreadpy with the holdout's
matching rules (`valid_for_game`, identical player-weeks); weeks without a
consensus scrape are excluded from L13-L16 only, as week 18 was in
validation, and the final report lists them.

## Weekly scoring, the pre-build check and the final (D-116, D-118)

- **Friday data cutoff.** Each week is scored on the runner's Friday job at
  or after its data cutoff: 12:00 UTC on the first Friday at least 72 hours
  after the week's last kickoff. Scoring uses, for each processed dataset it
  reads, the first snapshot taken at or after the cutoff. The snapshot ids
  and a SHA-256 of the outcome rows used are sealed with the week; a rescore
  reuses them; stat corrections after the cutoff are ignored. The final reads
  only sealed parts, including each week's sealed consensus rows. Processed
  snapshots are never pruned during the window.
- **Pre-build check.** Before every build the runner checks every commit
  since the last build's code commit: each needs a change-log entry, and none
  may touch a frozen path. A block alerts the owner and retries every 30
  minutes; a block fixed before the deadline is not a miss. The final also
  checks that each commit used by a week's build was in the change log as it
  stood at that build.
- **The final.** It reads the branch only from the fetched GitHub ref,
  checked against `git ls-remote`, and compares every scored week with the
  hashes recorded at weekly scoring. If the fetch or the check fails it stops
  before computing anything and records no verdict (exit 75), so it can be
  retried.
- **Committed weeks.** A week with a commitment or a sealed record on the
  branch is always scored and never excluded as a miss. A missed-week marker
  for such a week is listed in the final report with its week, the time of
  the commit that added it and its `created_at`, and does not change the
  verdict.
- **Bootstrap frames.** Only the frames the 22 lines read are bootstrapped.
  This is identical by construction (one week-clustered draw matrix) and was
  shown identical to the full computation at 500 and 5,000 draws
  (reports/livetest_subset_proof_2026-11-07.json).

## Expected verdict date and the app until then

The single verdict is computed after every window week is final, at the
earliest after 2027 week 9's games are final in `dim_game`. The 2027
schedule is not published; by recent seasons' calendars that is **about
2027-11-12** (the Friday data cutoff after 2027 week 9), and no earlier
than about 2027-11-05 nor later than about 2027-11-19. Until then the real app stays on
"no approved model": projections, start/sit, waivers, trades and
rest-of-season rankings are off through the rest of 2026, the 2027 offseason
and draft, and 2027 weeks 1-9 (D-099, D-110). chain_v2 outputs exist only in
private test environments, labeled "not approved". No interim result changes
this.

## Sealed interim results

Interim scored results are sealed until the verdict. Weekly scoring writes
only (a) verification status (digest, file hashes, GitHub push timestamp
before the binding deadline, week final) and (b) sealed metric parts under
`data/livetest/scoring/sealed/`, whose SHA-256 is pushed to `livetest-2026`
so they cannot be altered later. No metric, line result or ranking is
rendered, printed or committed before the final evaluation. Sealed results
are not used to tune chain_v2, to choose between weeks, or to stop the test
early; the only early end is a documented technical failure or a frozen-path
breach, which makes the test INVALID, never a performance reason. Unsealing
happens once, by the Evaluator at the final evaluation, with the sealed
hashes verified and logged.

## A separate v3 during the test

Allowed, under these conditions: v3 is developed on a separate branch
(`v3-dev`) that is not merged into `main` during the window, or under a
top-level directory outside every frozen path, and never changes a frozen
path; it does not read the sealed scoring outputs, the live-test commitments
or anything under `data/livetest/scoring/`; it may use any data that is final
at its own point-in-time cutoffs; it needs its own pre-registered test on
weeks it has not seen, approved by the owner before that test opens, and a
v2 pass confers nothing on v3; the pinned v2 worktree never contains v3 code.

## Missed weeks (D-106, revisited 2026-11-05 for the 18-week window)

Up to two misses are allowed: the first and second missed weeks are excluded
from scoring and the window is not extended; a third miss makes the test
INVALID (not failed). A week is missed when no valid commitment is on
`livetest-2026` at its deadline, for a real technical failure or an offline
machine; the reason is logged in `{season}w{WW}/MISSED.json`. A commitment
built before kickoff but pushed after it goes to `{season}w{WW}/late/` and is
not part of the test; projections that exist are never withheld. The frozen
rules are unchanged whatever the number of scored weeks.

Pass-rate estimate for this design, stated plainly: **even a good model
has only about a 56% chance of passing** the 22 lines over the 18-week window
under the frozen bootstrap; about 52% with one excluded week and 50% with
two. A model with no edge passed in none of 5,000 replicates (estimate 0.0%;
95% upper bound 0.06%).

| Weeks scored | Good model passes (variance-corrected; MC SE 0.007) | No-edge model passes |
| --- | --- | --- |
| 18 | 0.557 | 0 of 5,000 (upper bound 0.0006) |
| 17 (one miss) | 0.518 | 0 of 5,000 (upper bound 0.0006) |
| 16 (two misses) | 0.500 | 0 of 5,000 (upper bound 0.0006) |

**Report-only sensitivity check (owner, 2026-11-05, D-111).** Because the
frozen 500-draw bootstrap moves the estimated pass rate by about 5 points
depending on the calendar order of the clusters, the final report also
recomputes every interval behind the 22 lines with 5,000 week-clustered
paired draws (seed 0). The 500-draw, seed-0 bootstrap of D-078 remains the
only binding verdict rule; the 5,000-draw check can never change a line's
PASS/FAIL or the verdict. Any line whose 5,000-draw interval would give a
different PASS/FAIL is listed in the final report with both intervals, and
the weekly sealed parts store both (the runner's Friday job calls the
scorer with `--weekly-sensitivity`).

### How the estimate is made, and the reconciliation with the first report

- **Simulator:** `ffml/eval/power.py` (Evaluator). Its fast path reproduces
  the real scoring functions on 58 of 58 validated report cells and to within
  1.8e-15 on thinned test windows. 5,000 replicates per row above (10,000 in
  the first report).
- **One replicate:** for each week number of the window, one 2022-2024 slate
  of that week number is drawn at random as a whole week, so within-week and
  within-game correlation is kept; 2026 weeks 10-18 are thinned to the 2026
  game counts by dropping whole games (immaterial: full slates give 0.568);
  2027 weeks 1-9 use full slates because the 2027 schedule is unknown; missed
  weeks are drawn at random.
- **Good model:** chain_v1+cal's validated 2022-2024 accuracy with coverage
  made exactly calibrated. **Variance-corrected:** week-level differences are
  stretched by sqrt(3/2) about their week-number means, because a
  three-season pool understates season-to-season variation by about a third
  in variance; the corrected figures are the ones used here. **No-edge
  model:** every chain-minus-comparator difference centred to zero per week
  number with its noise kept, start/sit centred to the consensus, coverage as
  in the good model; its pass rate is the false-approval rate.
- **Not simulated:** season-wide shifts such as the 2025 QB coverage drop
  (0.779 to 0.739), schedule changes, misses that are not random, any change
  in the chain's edge over time.
- **Reconciliation (2026-11-05).** The first report's "about 77%, or 63%
  corrected, over 18 weeks" (0.767 / 0.628, 10,000 replicates) was the
  reference window of weeks 1-18 in calendar order with full slates. The
  fallback window is 2026 weeks 10-18 followed by 2027 weeks 1-9, the order
  the test will actually score in, and gives 0.722 / 0.557. Same week
  numbers, same game counts (271.7 vs 269.7; full slates in the fallback
  order give 0.568), so neither thinning nor the slate draws explain it. The
  cause is the frozen bootstrap itself: 500 week-clustered draws with seed 0
  (D-078) are a fixed weight matrix, and which calendar position gets which
  weights moves the good-model pass rate by about 5 points between the two
  orders. Swapping the seed index of the slate draws leaves the gap (0.610 vs
  0.573); other bootstrap seeds give 0.587-0.610 for both orders. **0.557 is
  the number for the test as it will be run**; 0.618-0.628 applies to an
  order the test will not use; 0.59-0.61 is the average over bootstrap seeds.
  The bootstrap stays as pre-registered and no rule is changed on this
  evidence. Details: docs/status/livetest-power-reconciliation.md.

## 2027 weeks 1-9

The same frozen code, data refresh and machinery continue into 2027 with no
re-freeze: the pinned worktree, `ffml refresh` through the pinned module,
`build-week` with `fit((2027, W))` on everything final before the week
(2016 through 2026 and 2027 weeks 1..W-1), the walk-forward calibrator with
history through 2026 week 18 and 2027 weeks 1..W-1, the append-only cache
for every 2027 week, the same branch `livetest-2026`. The 2027 schedule is
unknown until the league publishes it (usually May); deadlines are read from
`dim_game` at build time under the same rule. Weeks whose kickoff times are
still tentative in `dim_game` (every game of the week at one identical time,
as 2026 week 18 is today, and as early 2027 weeks may be) use that time
minus 24 hours as the runner's deadline (D-109); `build-week` itself refuses
at the schedule time it reads, and the commitment's `first_kickoff` is the
schedule time at build. The 2027 preseason and any offseason data changes
pass through the frozen-code policy above; a 2027 feature schema change
upstream that cannot be absorbed under `ffml/data/` with byte-identical
recomputed rows stops the test for the owner's ruling.

## Evaluator-side scoring (stub specification)

The scorer is `ffml/eval/livetest_score.py` (frozen with the code); this
fixes what it reads and checks.

- **When.** A week is scored only after every regular-season game of that
  week is final in `dim_game` (after the refresh that brings those finals);
  never before, and never from projections that are not on the branch.
  Weekly results are information only; no weekly report prints a `Verdict:`
  line. The final is computed once, after every window week is final, at
  the earliest after 2027 week 9's games are final in `dim_game`, also when
  2027 week 9 is the excluded miss; the scorer refuses the final before that.
- **Reads.** From `livetest-2026`: `{season}w{WW}/commitment.json`, and
  `{season}w{WW}/player_summary.parquet` when present (else the local file
  the commitment lists, which must hash to the listed SHA-256). Actuals from
  `ffml.scoring.actuals.actual_statlines` on the processed snapshot current
  at scoring time, scored under the `ppr`, `half` and `std` presets
  (`ffml/scoring`, the scoring of record, D-079). `player_summary.parquet`
  carries no `pos_min` / `pos_max`: outcomes, week cutoffs and PIT bounds are
  attached with the `ffml.eval.harness` functions (`outcome_table`,
  `position_bounds`, `week_cutoffs`) on that same snapshot, as in the
  holdout (position extremes of earlier seasons; for 2027 weeks those include
  2026), and the weekly scoring JSON records the snapshot id. Comparators and
  the consensus as under "Rules".
- **Verifies before any metric** (any failure: `INVALID` for the week, with
  the reason): the commitment digest (canonical JSON of every other field);
  the SHA-256 of every file in `outputs` and in
  `calibration_history.sources`; `created_at` and the GitHub push record
  `push_activity.timestamp` of the commit that added the file
  (`repos/{owner}/{repo}/activity?ref=refs/heads/livetest-2026`) both before
  the binding deadline, the earlier of the commitment's `first_kickoff`
  (schedule at build time) and the actual first kickoff per the final
  schedule, both reported; `code.commit` at the frozen commit or an allowed
  descendant (X, Y and the diff recorded; the change log covers every
  descendant used); `pins` equal this block; `model_version` matches the
  scheme and the week; `calibrator_state.history_last` is the week before
  and `calibrator_state.calibration_version == v3-tie-aware-quantile`; the
  stored parts listed under `stored:` origins hash as in the stored runs
  (digest `309ca749...fa54` over those 97); labels `private_test` /
  `not approved`; the file is immutable (same blob at scoring time as at
  push time).
- **Computes.** The 22 lines on the calibrated rows (`model == chain_v2`),
  with `metric_seed 0`, `n_boot 500`, `seed 0`, plus the report-only
  5,000-draw intervals, cumulatively over the scored weeks after each week,
  and once over the whole window for the final. Weekly output is sealed:
  `reports/livetest/{season}w{WW}_verification.md` holds only the
  verification status (digest, hashes, binding deadline, push timestamp,
  games final, snapshot ids, sealed-file hashes); the metric parts go to
  `data/livetest/scoring/sealed/{season}w{WW}.parquet` and `.json`, whose
  hashes (`{season}w{WW}.sha256`) the runner pushes verbatim to
  `scoring/{season}w{WW}.sha256` on `livetest-2026`. No weekly output shows
  a metric or a `Verdict:` line. The final verifies every sealed file
  against the branch record before reading it, and only the final report
  carries the single `Verdict:` line. An approval is recorded through
  `ffml.models.registry.approve` only by the Evaluator and only with the
  `Verdict: APPROVED` line.

## Known facts recorded before the freeze

- chain_v1 was not approved on 2025 (D-097): 21 of 22 lines passed, QB
  tie-aware coverage 0.739. 2025 is training data and history since
  2026-11-04 (`approved=false`).
- The walk-forward calibrator is the validation run's own method; on
  2022-2024 its tie-aware coverage was QB 0.779, RB 0.785, WR 0.786, TE
  0.793, K 0.807, DST 0.820 (report v3). Nothing was tuned on 2025.
- D-105 selection (reports/evaluator_live_test_power_candidates.md, 5,000
  replicates, variance-corrected): R0 0.044, R1 0.614, R2 0.124, R3 0.614
  good-model pass rates on nine weeks, no-edge 0.000 for all; none reaches
  0.70, so the fallback applies (D-110). Over the 18-week window the
  unchanged 22 lines pass a good model 0.557 (corrected; 0.722 uncorrected)
  and a no-edge model 0.000.
- Rehearsal of the machinery: Week 9, built 2026-11-04 23:36 UTC (25.6 h
  before kickoff), commitment digest
  `a69e91379d1f23a88f858b0d5e221ea4a36f70d30f33cd94b30583bf3fe640c2`, 386 s,
  4.01 GB; not part of the test.
- QB, K and DST boom/bust are low confidence (D-089, D-104) and are not among
  the lines.
- Deadlines above are the schedule as of `dim_game 20261104T233034Z`; the
  binding deadline is the official first kickoff at build time, including
  Thanksgiving (Week 12) and other early games, with the tentative-time rule.
