# Phase 4 / Independent Evaluator / review of the chain_v2 live-test pre-registration (EV-4.3 task 2, EV-4.4 task 2)
Date: 2026-11-05 (re-review after ML-4.4; again after D-111 at 86c43b1)
Scope: evaluator review of `docs/status/livetest-preregistration-2026.md`,
first at 7899bb5 and again at e94a555 (file SHA-256 `9c6209b5c7800ace...`),
before the freeze. Checked: that the JSON block is machine-checkable, the
pinned hashes (LF-normalised for tracked text), `lines_sha256` and
`rules_sha256`, the window, the one-miss rule, the frozen and allowed paths,
the comparators, the deadline rule, the PIT bounds, and that nothing in the
document depends on 2026 week 10 or later outcomes. I did not edit the
pre-registration.

## Re-review at 86c43b1 (D-111 version; EV-4.5 task 3)

File SHA-256 `44f0c30d29d68c00...`. New since e94a555: `expected_verdict`,
`sealed_interim`, `v3_policy`, `outside_changes`, `missed_weeks.allowed = 2`
with `miss_rule`, `estimate.reconciliation_2026-11-05`,
`scoring.sensitivity_check`, `path_policy`, and the matching markdown
sections. The scorer at 1d0d2b6 implements the sealed weekly mode, the
miss rule read from the file, the binding deadline, the pushed-blob
immutability and stored-parts digest checks, and the report-only
5,000-draw sensitivity check; its tests cover each.

### Ready to freeze: yes, conditional on R1 (the commit field and `frozen_commit_must_include`); R3 to R5 are wording and coordination items to fix in the same edit

| id | weight | finding | suggested fix |
| --- | --- | --- | --- |
| R1 | at the freeze | `commit` is still `<frozen commit>` and `frozen_commit_must_include` lists a2f2af0, 6e29122, b22973f, d24a43a only. `frozen_paths` names `ffml/eval/livetest_score.py` and `ffml/eval/backtest_configs.py`, so the frozen commit must contain `1d0d2b6` (which contains 1013534, 7c5b6e2, eebb0d8, 94e4545). | Fill `commit` with a descendant of `1d0d2b6`; add `1d0d2b6` to `frozen_commit_must_include`. |
| R2 | wording (open from the last review) | `rules.comparators.reference_ranking` still cites `report_backtest.ref_rank`. | Name `report_backtest.reference_models` and `metrics.with_reference_rank`; list the set: `chain_v2` raw rows, the five baselines, `direct_lgbm`, plus `consensus_ecr` in the matched population. |
| R3 | coordination, before the first week is scored (2026 week 10 final, about 2026-11-17) | `sealed_interim.mechanism` says the sealed files' SHA-256 is pushed to `livetest-2026` but names no path. The scorer writes `data/livetest/scoring/sealed/{season}w{WW}.sha256` (one `<sha256>  <name>` line per sealed file) and the final reads `scoring/{season}w{WW}.sha256` from the branch; without that file the final is refused. | State the branch path (`scoring/{season}w{WW}.sha256`) in the JSON and the runbook; the Data Engineer's push step must exist before 2026 week 10 is scored. |
| R4 | wording or default | `scoring.sensitivity_check.disclosure` says "the weekly sealed parts store both". Weekly intervals are cumulative over the sealed weeks; at 5,000 draws the weekly job grows to about an hour late in the window. The scorer stores the 500-draw cumulative lines every week and both in the final's `sealed/final_lines.json`; `--weekly-sensitivity` stores both weekly. The verdict is unaffected either way. | Either amend to "the final's sealed parts store both; the weekly sealed parts store the 500-draw cumulative lines" or require `--weekly-sensitivity` in the runbook. |
| R5 | wording | The "Evaluator-side scoring (stub specification)" section predates sealing: its "Computes" paragraph sends weekly line results to `reports/evaluator_livetest_2026_{season}w{WW}.md` and a running report, which `sealed_interim` forbids, and it says any failed check makes the week INVALID. The scorer refuses to score (nothing sealed) on the refusal-class failures (week not final, digest, file hashes, push record, deadline, rehearsal) and records every other failure in the sealed JSON and the verification report; a recorded failure makes the final INVALID. | Replace the paragraph: weekly `reports/livetest/{season}w{WW}_verification.md` (verification only) plus the sealed parts; final `reports/livetest/final.md` with the single `Verdict:` line and `data/livetest/scoring/sealed/final_lines.json` holding the binding and the 5,000-draw results. |

Verified in this pass:

- `missed_weeks.allowed = 2`, `third_miss` INVALID and `miss_rule` are read by the scorer (`missed weeks (miss rule)` check); the 17- and 16-week pass rates quoted (0.518, 0.500) come from `MISS_WINDOWS` runs; 0.518 matches the candidates report, 0.500 is the orchestrator's `ext18_two_miss` run.
- `estimate.reconciliation_2026-11-05`: confirmed by an independent run (docs/status/livetest-power-reconciliation.md, "Evaluator verification"): with the frozen 500-draw matrix the two orders differ by 0.037 (z = 3.0) on the same replicates; with a fresh seed per replicate or 5,000 draws they agree (gaps -0.015 and -0.003, within noise), at 0.59-0.61. Two run labels in the isolation table were wrong; corrected in the note, conclusion unchanged.
- `expected_verdict.when` matches the scorer's refusal rule (every window week final in `dim_game`, 2027 week 9 included even as the excluded miss).
- `path_policy`: the scorer's `path_allowed` is the rule described; `frozen_paths` contains no prefix of an allowed entry.
- `outside_changes` and `v3_policy` need nothing from the scorer; the change-log coverage check enforces that every descendant commit used is logged; the replay proof itself is recorded in the log's Verification column, not machine-checked.
- `lines_sha256` equals `lines_spec_sha256()`; `rules_sha256` recomputes under its stated rule; `n_boot`, `seed`, `metric_seed` equal the pinned spec; pins unchanged; no dependence on 2026 week 10+ outcomes (the new text adds decision records, estimate figures and schedule data only).

## Earlier re-review at e94a555 (superseded by the section above)

Ready to freeze then: yes, conditional on two mechanical items at the freeze

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
