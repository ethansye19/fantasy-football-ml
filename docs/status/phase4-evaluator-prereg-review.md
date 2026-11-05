# Phase 4 / Independent Evaluator / review of the chain_v2 live-test pre-registration (EV-4.3 task 2, EV-4.4 task 2)
Date: 2026-11-05 (re-review after ML-4.4)
Scope: evaluator review of `docs/status/livetest-preregistration-2026.md`,
first at 7899bb5 and again at e94a555 (file SHA-256 `9c6209b5c7800ace...`),
before the freeze. Checked: that the JSON block is machine-checkable, the
pinned hashes (LF-normalised for tracked text), `lines_sha256` and
`rules_sha256`, the window, the one-miss rule, the frozen and allowed paths,
the comparators, the deadline rule, the PIT bounds, and that nothing in the
document depends on 2026 week 10 or later outcomes. I did not edit the
pre-registration.

## Ready to freeze: yes, conditional on two mechanical items at the freeze

1. Fill `commit` with a commit that contains the evaluator's scorer and
   comparators work (`7c5b6e2` contains `eebb0d8`, `94e4545`, `1013534`),
   and add `7c5b6e2` to `frozen_commit_must_include` (R1 below). Without it,
   the frozen commit would not hold `ffml/eval/livetest_score.py` and the
   `livetest_comparators` configuration that the document names.
2. Non-blocking wording (R2): the reference-ranking sentence names a function
   that does not exist; see below.

Everything else in the first review is closed or verified (table 2).

## Re-review at e94a555: remaining findings

| id | weight | finding | suggested fix |
| --- | --- | --- | --- |
| R1 | at the freeze | `frozen_commit_must_include` lists a2f2af0, 6e29122, b22973f, d24a43a but not the scorer and comparators commits; `frozen_paths` names `ffml/eval/livetest_score.py` and `ffml/eval/backtest_configs.py`, so the frozen commit must contain them. | Add `7c5b6e2` (which contains the others) and fill `commit` with a descendant of it. |
| R2 | wording | `rules.comparators.reference_ranking` says "computed ... by ffml.eval.report_backtest.ref_rank". The functions are `report_backtest.reference_models` and `metrics.with_reference_rank`; on the pre-registered frame the D-077 reference set is the raw models present: `chain_v2` (the raw rows), `rolling_avg_4`, `rolling_avg_3`, `rolling_ew_035`, `prior_season`, `rolling_median_8`, `direct_lgbm`, plus `consensus_ecr` in the matched population, as in the holdout. | Name the functions and list the set. |

## Status of the first review's findings

| id | first review | status at e94a555 |
| --- | --- | --- |
| F1 | `commit` placeholder | open by design; filled at the freeze (R1) |
| F2 | `rules_sha256` rule unwritten | closed: `rules_sha256_definition` added; recomputed `1decb853...` equals the field; `lines_sha256` equals `lines_spec_sha256()` at HEAD |
| F3 | path policy gap | closed: "any path not under allowed_paths is frozen"; explicit `frozen_paths` (the scorer, `backtest_configs.py`, `harness.py`, `ecr_baseline.py`, `metrics.py`, `report_backtest.py`, `report_holdout.py`, `holdout.py`, `cli.py`, `config.py`, `season.py`, `pyproject.toml`, `requirements.lock.txt`, models, features, sim, scoring, the pinned manifest); explicit `allowed_paths` with conditions, one path per entry (`tests/`, `.github/`, `ffml/data/`, `ffml/eval/power.py`, `ffml/eval/audits/`, `scripts/`, `api/`, `web/`, `docs/`, `reports/`, Docker files, `README.md`). The scorer parses 16 allowed and 19 frozen entries; `path_allowed` is false for the scorer, `cli.py`, `config.py`, the lock file and `metrics.py`, true for `tests/`, `.github/`, `power.py`, `ffml/data/`, `scripts/`, `docs/` |
| F4 | comparators and reference set | closed, except the R2 wording: the `livetest_comparators` configuration (committed at 1013534) with rows used and the consensus rule |
| F5 | consensus for 2027 | closed: weeks without a scrape leave L13-L16 only and are listed |
| F6 | pin hashes | verified again (unchanged) |
| F7 | `verdict_after` | closed: after every window week is final, at the earliest after 2027 week 9's games, also when it is the excluded miss; the scorer refuses the final before that |
| F8 | deadline when the schedule changes | closed: the binding deadline is the earlier of the committed first kickoff and the actual first kickoff; `created_at` and the GitHub push timestamp must precede it. The scorer implements exactly this at 7c5b6e2 (both checks refuse scoring) |
| F9 | PIT bounds | closed: harness functions on the processed snapshot current at scoring time, snapshot id recorded. The scorer records the LATEST ids of `fact_player_game`, `fact_dst_game`, `dim_game`, `dim_player` with every scored week (7c5b6e2) |
| F10 | no dependence on week 10+ data | still none: the new text adds schedule data, hashes of frozen files and commit shas only |
| F11 | prose in `frozen_paths` | closed: paths only |
| F12 | one commitment per week | verified |
| new | change log | `docs/status/livetest-changelog-2026.md` exists with a `Commit` column; the scorer's final checks that every commit between the frozen commit and each week's build commit, and up to the evaluating HEAD, has an entry (7c5b6e2) |

## Evidence

- `load_prereg` parses the block at e94a555; `prereg_window` gives 18 weeks,
  `(2026, 10)` to `(2027, 9)`; `rules_hash(rules) == rules["rules_sha256"]`;
  `rules["lines_sha256"] == lines_spec_sha256()`; `n_boot`, `seed`,
  `metric_seed` equal the pinned spec.
- Hashes: `ffml/models/chain/selection.json` bytes `d6c1891828657b76...`,
  LF `75477b2b97bc7d9b...`; `tests/fixtures/features/feature_manifest_0.2.0.json`
  `b119f85cdfdc3546...` in both forms.
- Holdout guard for the scorer's purpose: `assert_seasons_allowed(2021..2027,
  "livetest_score")` passes with the release marker
  (`artifacts/holdout_release.json`, approved=false) and no unlock; the access
  log records `purpose=livetest_score ... released=true`.
- Tests: `tests/eval/test_livetest_score.py` (10) and
  `tests/eval/test_livetest_comparators.py` (4) pass; the audit's eval-code
  check passes.

## Decisions proposed

- None beyond R1 and R2. The evaluator's earlier proposal to freeze the
  scorer is adopted in the document (`frozen_paths`).

## Next

- At the freeze: confirm the `commit` field and `frozen_commit_must_include`
  (R1), then record "frozen at <sha>" here.
