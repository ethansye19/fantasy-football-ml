# Phase 3 / Independent Evaluator / EV-3.1 (plus scaffolding for EV-3.2 to EV-3.4)
Date: 2026-11-02
Scope: metrics module with bootstrap intervals, the restricted consensus
baseline reader (baseline 3), and the holdout and leakage audit tooling. All
consensus figures below are aggregates; no ranking rows appear in any
committed file (D-007).

Task list (from the brief):
1. Review and finish the inherited, untested `ffml/eval/metrics.py`; tests with known answers.
2. `ffml/eval/ecr_baseline.py`: consensus prediction frame, prior-season rank-to-points curve, coverage aggregates, output-path guard; synthetic tests.
3. `ffml/eval/audits/`: holdout audit, leakage audit, report writer; synthetic tests.
4. This note, with consensus coverage per season 2020-2024 and data findings.

Done:
- `ffml/eval/metrics.py` (commit 6abf642): prediction-frame contract in the module docstring; `mae`, `pinball_loss` (q 0.1/0.5/0.9), `interval_coverage`, `interval_width`, `reliability_table`, `brier`, `calibration_slope`, `pit_histogram`, `spearman_within` (groups of at least 8, replacement level QB 12, RB 24, WR 36, TE 12, K 12, DST 12), `start_sit_accuracy` (plain, gap-weighted, by gap bin; startable QB 12, RB 24, WR 36, TE 12, both active), `lineup_efficiency` (exact assignment solver, per week and mean), `bootstrap_ci` (cluster bootstrap over season-weeks), `evaluate` -> `EvalResult`, `compare` (paired bootstrap, deltas, acceptance flag), `to_markdown`.
- `ffml/eval/ecr_baseline.py` (commit 01500bf): `consensus_predictions(seasons, scoring="ppr", purpose="backtest")`, `consensus_with_diagnostics`, `consensus_coverage`, the pure builders (`page_ranks`, `latest_valid`, `join_actuals`, `fit_rank_curve`, `build_consensus`), evaluator-local reference scoring (PPR identical to nflverse `fantasy_points_ppr` on every played QB/RB/WR/TE row 2022-2024; Sleeper default K and DST), `check_output_path` / `write_restricted_frame` (refuse any path inside the repository outside `data/`). Guard: one `assert_seasons_allowed` call per build, covering the evaluated seasons and the prior seasons used for the curve.
- `ffml/eval/audits/` (commit 962a352): `report.py` (`AuditReport`, `AuditItem`, `write_report`, `combined_status`), `holdout_audit.py` (`audit_holdout`), `leakage_audit.py` (`audit_leakage`), and `python -m ffml.eval.audits [--write NAME] [--release] [--no-suite]` (exit 1 on any FAIL; `ffml/cli.py` is outside my paths).
- Tests: `tests/eval/test_metrics.py` (20), `tests/eval/test_ecr_baseline.py` (11), `tests/eval/test_audits.py` (18). All fixtures are synthetic; no real consensus rows.
- Dry-run audit report: `reports/evaluator_audit_2026-11-02_wave1.md`.

What I changed in the inherited metrics.py (kept: contract docstring, population rule, pinball, reliability table, assignment-based lineup solver, replicate tagging in the bootstrap):
- `rank_pred` was documented but never used. A step-shaped curve (the consensus baseline) then produced tied `pred_q50` values that silently dropped start/sit pairs. Added one ordering rule used everywhere (ranks, replacement filter, startable sets, Spearman, start/sit, lineups): `pred_q50` descending, then `rank_pred`, then `entity_id`. Only exact ties on both carry no preference.
- `STARTABLE` included K and DST; the spec has QB/RB/WR/TE only. Fixed.
- Lineup rows set every `by` key to the string "ALL", which breaks frame construction when `by` holds an integer column such as season. Lineups are now grouped by the non-position `by` columns, with position "ALL".
- `calibration_slope` was an OLS slope of outcome on probability. It is now the logistic (Cox) slope, fitted by Newton-Raphson, with NaN on separation.
- Bootstrap resampling concatenated frames once per metric. Now it gathers row indices once per replicate and evaluates every metric on that replicate (`bootstrap_bundle`). Real-data cost: about 15 s per model for 100 draws over 3 seasons and 6 positions.
- `validate_frame` now rejects null keys and duplicate `(model, entity_id, season, week)` rows, casts optional columns, and turns NaN into null.
- `compare` now aligns all models to their common keys by default (`align_frames`), so paired deltas use one population (the consensus baseline has no Thursday players). Its output adds `higher_is_better`, `better` (delta interval excludes zero in the good direction) and `n`. Lineup efficiency is now part of `compare`.
- Added the `DEF` slot alias and Sleeper flex slots (`SUPER_FLEX`, `REC_FLEX`, `WRRB_FLEX`); bench, IR and taxi slots are ignored. Added an optional `pool` grouping for per-roster lineups, the `coverage_gap` metric, and a compare-table markdown renderer.

Evidence:
- `.venv/Scripts/python -m pytest tests/eval -q` -> `49 passed in 12.90s`
- `.venv/Scripts/python -m pytest -q` -> `226 passed, 1 warning in 27.83s` (the warning is from `tests/scoring`)
- `.venv/Scripts/ruff check .` -> `All checks passed!`
- `python -m ffml.eval.audits` on the working tree at 962a352 (exit 0):
  - Holdout audit: PASS. Access log absent. No literal holdout season in code; three docstring mentions are WARN only: `ffml/features/access.py:4`, `ffml/features/context.py:187`, `ffml/models/baselines.py:378`, all explanatory. No unlock assignments outside tests and the guard (53 files scanned). `.env` absent; unlock not set in the environment.
  - Leakage audit: SKIP on all three checks. `tests/features/test_leakage.py` is not present yet (the Feature Engineer has `test_windows.py` and `conftest.py` so far), and no `data/features/player_week` dataset exists. The checks themselves were exercised on synthetic frames in the tests.
- Consensus coverage: share of played regular-season QB/RB/WR/TE rows with a valid consensus rank on the matching page (`consensus_coverage`):

  | season | played rows | covered | coverage | weeks with a scrape | coverage within those weeks |
  | --- | ---: | ---: | ---: | ---: | ---: |
  | 2020 | 6,308 | 5,118 | 0.811 | 16 | 0.866 |
  | 2021 | 6,685 | 5,383 | 0.805 | 17 | 0.856 |
  | 2022 | 6,611 | 5,120 | 0.774 | 16 | 0.879 |
  | 2023 | 6,669 | 5,128 | 0.769 | 16 | 0.871 |
  | 2024 | 6,618 | 4,391 | 0.663 | 14 | 0.866 |

  By position (all weeks): QB 0.800 / 0.764 / 0.734 / 0.704 / 0.594, RB 0.807 / 0.824 / 0.774 / 0.772 / 0.668, WR 0.820 / 0.809 / 0.783 / 0.779 / 0.670, TE 0.807 / 0.796 / 0.777 / 0.777 / 0.676 for 2020-2024.
- Consensus build diagnostics (rows): dropped for no id 0 / 8 / 13 / 11 / 44; dropped as cross-listed (page position differs from the player's position) 90 / 83 / 75 / 71 / 30; not `valid_for_game` 903 / 622 / 631 / 630 / 605; rows out 7,253 / 7,715 / 7,341 / 7,090 / 6,038 for 2020-2024. Curve training rows: 327 for 2020 (only one 2019 scrape exists), 6,344 for 2021, then 12,663, 18,657 and 24,658.
- Consensus smoke check on 2022-2024 (`evaluate`, 100 draws; a pipeline check, not for citation; EV-3.3 supersedes it): start/sit QB 0.586 [0.557, 0.623], RB 0.576 [0.556, 0.601], WR 0.590 [0.574, 0.606], TE 0.601 [0.570, 0.631]; 80% interval coverage 0.80-0.88 by position.

Data findings (aggregates only):
- DE: MODELING section 3 says consensus coverage starts at 2020 week 6. The processed table has dated scrapes for 2020 weeks 1-16, and week 1 includes a preseason (August) scrape. 2020 scrapes fall on Thursdays, so same-day Thursday games are correctly invalid. The 2019 partition is a single week-17 scrape. No season has week 18. 2022 and 2023 start at week 2, and 2024 at week 4.
- DE: `fact_ecr_weekly` still carries IDP pages (`dl`, `db`, 17 rows in 2021-2024, `valid_for_game` null) and cross-listed rows (an RB on the WR page, a QB on the TE page). The reader filters both and counts them.
- DE / ML: `fact_player_game.fantasy_points_ppr_ref` for kickers excludes kicking points (nflverse definition), so it is not a kicker reference. Any code using it as K actuals is wrong. The consensus reader uses its own kicking formula.
- DE: `played` follows snaps or kicks. 2020-2024 regular season has 1,000 `player_stats` rows with `played = false` (8 score points, for example return touchdowns without offensive snaps) and 4,436 roster rows with `played = true` and zero points. With `active = played`, those 8 scoring rows fall outside the evaluation population. Minor; consistent with D-043.

Not done / blocked:
- The leakage audit has not run on real inputs. It waits for FE-3.1 (feature dataset) and FE-3.2 (`tests/features/test_leakage.py`); rerun `python -m ffml.eval.audits --write evaluator_audit_<date>` when they land.
- No evaluation of model output yet; EV-3.3 waits for the ML Engineer's prediction frames.

Decisions proposed:
- D-049 proposal: the holdout guard writes one log line per `assert_seasons_allowed` call, so one release run writes several lines. "Holdout touched exactly once" (Gate 3) means one session: log lines with the same purpose, git sha and user less than 2 hours apart, all with purpose `holdout_eval`. `audit_holdout(release_run_allowed=True)` implements this. Alternative: the guard deduplicates per process.
- D-050 proposal: evaluation conventions. The ordering rule is `pred_q50`, then `rank_pred`, then `entity_id`. A start/sit tie in actual points counts 0.5. Startable sets exclude K and DST. The lineup pool is every projected entity in the season-week (or per `pool` id) with default slots QB, RB, RB, WR, WR, TE, FLEX, FLEX, K, DEF. `compare` aligns models to common keys, so the consensus comparison excludes Thursday players for every model and the report says so.
- D-051 proposal: baseline 3 lives only in `ffml/eval/ecr_baseline.py`. PLAN ML-3.2 also lists a "baseline 3 loader"; that item is met by this module, and model, feature, simulation and API code never read `data/processed/restricted`. Today no code outside `ffml/data` and `ffml/eval` references the restricted table.
- D-052 proposal: the consensus curve uses page ranks over the whole page, the latest valid scrape per entity, windowed prior-season quantiles made non-increasing by isotonic regression, and active rows only. The evaluator keeps its own PPR reference points and cross-checks them against `ffml/scoring` in EV-3.3.

Next:
- EV-3.2: run the audits with `--write` once the feature dataset and the leakage suite exist. Add a D-007 scan that fails on any restricted-table read outside `ffml/data` and `ffml/eval`, as Gate 3 requires.
- EV-3.3: consume the ML Engineer's prediction frames with `compare` against baselines 1-3 by position and week; cross-check reference PPR against `ffml/scoring`.
- Findings for other roles are the data findings above; none is blocking.
