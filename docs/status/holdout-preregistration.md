# Holdout pre-registration (EV-3.4, D-091 to D-096)

Written 2026-11-04 before the 2025 holdout season was opened. Nothing in this
file may change after the holdout session starts. The JSON block below is the
machine-readable record that `ffml/eval/report_holdout.py` checks before it
computes any metric; any mismatch makes the verdict `INVALID`.

```json
{
  "candidate": "chain_v1-primary-c2024w19-20261102T222634Z",
  "focus_model": "chain_v1",
  "commit": "3ecf61069020d6fcba1ddeca0fdb742afd09d794",
  "holdout_season": 2025,
  "forward": {"season": 2026, "weeks": [1, 2, 3, 4, 5, 6, 7]},
  "frozen": {
    "path": "artifacts/calibration/FROZEN.json",
    "sha256": "901fafbd8e63f3c747d62bd072e2c978c6a075a9e7004a5c3a9ce44e5f9f1bbf"
  },
  "calibrators": {
    "rolling_avg_4": {"path": "artifacts/calibration/rolling_avg_4/calibration.json", "sha256": "c36a02601b1485419cca6600072241d6ea9dc39d33fa720ec043091be2cb91f7"},
    "rolling_avg_3": {"path": "artifacts/calibration/rolling_avg_3/calibration.json", "sha256": "effde8fc29285e3f2686c973d2305d4918a1983d1a63e1d28a692ba0236f89af"},
    "rolling_ew_035": {"path": "artifacts/calibration/rolling_ew_035/calibration.json", "sha256": "38bad46211d3902d0a39e491adf790c7d9e0d2a1807cc8cacbf1b1bab8813059"},
    "prior_season": {"path": "artifacts/calibration/prior_season/calibration.json", "sha256": "384bb4a872e210f988d42c9ffb68c558712133e9aa2966551a47aab7338b9f41"},
    "rolling_median_8": {"path": "artifacts/calibration/rolling_median_8/calibration.json", "sha256": "09ffdbd0190f21572371ca0da0e61310e1a74f8a2410c37f0b81e23c46f3c4ba"},
    "direct_lgbm_ppr": {"path": "artifacts/calibration/direct_lgbm_ppr/calibration.json", "sha256": "7f98423eabf021d47efabb0e35554b842c7e5d6b33a7713e5120b452b8d1a013"},
    "direct_lgbm_half": {"path": "artifacts/calibration/direct_lgbm_half/calibration.json", "sha256": "5416d7e6da072ac3a3ece0fc9df3f9e584ee3b4c8ea8e4a473b44b02a586317e"},
    "direct_lgbm_std": {"path": "artifacts/calibration/direct_lgbm_std/calibration.json", "sha256": "fbb6bccf3e1e7c69a6bf620aaaad47b4c76460f91798be3ece7ae3c6b65b3860"},
    "chain_v1": {"path": "artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z/calibration.json", "sha256": "3c77725c4e6cf21cd2fc26acaeeea2b3aff22991087614bf35dcd9da280bcd39"},
    "chain_v1_final_report": {"path": "artifacts/calibration/chain_v1_final_report/calibration.json", "sha256": "914fa8d8cd8145df06fb19bdc812c5766bc9e538c62773d5475304f9fc94a45e"}
  },
  "calibration_mode": "frozen",
  "snapshots": {
    "processed": {
      "fact_player_game": "20261102T205628Z",
      "fact_dst_game": "20261102T204833Z",
      "dim_game": "20261102T205627Z",
      "dim_player": "20261102T205627Z"
    },
    "features": {
      "player_week": "20261102T213907Z",
      "team_week": "20261102T213908Z",
      "dst_week": "20261102T213908Z"
    }
  },
  "n_boot": 500,
  "seed": 0,
  "metric_seed": 0,
  "lines_sha256": "ea706e17578e6b5caa85eb9db3fbffa6758a6bc85dae2169a707b425b91de2ad"
}
```

## What is frozen

| Item | Value |
| --- | --- |
| Code commit | `3ecf61069020d6fcba1ddeca0fdb742afd09d794`. The session may run only there or at a descendant whose sole difference is this file |
| Candidate | `chain_v1` primary configuration, registry version `chain_v1-primary-c2024w19-20261102T222634Z`, status candidate (not approved) |
| Stage choices | `ffml/models/chain/selection.json`, SHA-256 `d6c1891828657b76de9cb5aab51a314cd5d85c014ad4a2ba4546b9ad2e82e0da` (LightGBM for 19 of 30 stages) |
| Features | version 0.2.0; manifest pin `tests/fixtures/features/feature_manifest_0.2.0.json`, SHA-256 `b119f85cdfdc3546b3fb053ca480da6b5e543349940080a3bd19ae692f2c42f6` |
| Data and feature snapshots | as in the JSON block; identical to the validated backtest `default_20261102` (predictions SHA-256 `58d782bfb3336b569974ef1704a0482f883b4e91b2e758c6cc9ee245a52b82d7`) |
| Consensus snapshot (restricted, private) | `fact_ecr_weekly` `20261102T204836Z` |
| Calibration mode | **fixed**. Every model's calibrator was frozen on its 2022-2024 rows (v3 quantile method, v2 per-position boom/bust, seed 0) and is applied unchanged to every 2025 and 2026 week; nothing updates from earlier holdout weeks (D-095) |
| Seeds | metric (randomized PIT) 0; bootstrap 500 week-clustered paired draws, seed 0; calibrator seed 0; chain simulation seed 20261101 (per week `seed + 100*season + week`), 2,000 simulations; challenger seed 20261102 |
| Runs | `holdout_2025`: 2025 weeks 1-18, weekly refits (each week trains on all earlier rows), purpose `holdout_eval`. `forward_2026`: 2026 weeks 1-7 (D-096), same protocol |

## Approval rules (22 lines, all must pass for `Verdict: APPROVED`)

Calibrated forms, `chain_v1+cal` against each comparator's calibrated form,
95% week-clustered paired bootstrap intervals (D-078).

- L01-L12: at QB, RB, WR and TE, the chain beats each of `rolling_avg_4`,
  `prior_season` and `rolling_median_8` on all four of MAE, pinball q10,
  pinball q50 and pinball q90 (every interval of chain minus comparator lies
  entirely below 0).
- L13-L16: at QB, RB, WR and TE, start/sit accuracy on the D-077 common pair
  set is not worse than the FantasyPros consensus (the interval of chain minus
  consensus is not entirely below 0).
- L17-L22: tie-aware coverage of the 80% interval (randomized PIT in
  (0.1, 0.9), metric seed 0) lies in [0.75, 0.85] at QB, RB, WR, TE, K and DST
  (D-085).

The same 22 lines are applied to the 2026 forward check, which prints
`Forward check: PASS` or `Forward check: FAIL` and never changes the 2025
verdict line.

## Owner rules for the session (D-092 to D-094)

1. If the candidate fails on 2025, it is not approved. Nothing is tuned on
   2025, the session is not rerun for a better result, and no Week 9
   projections are published as approved.
2. The only allowed rerun is after an infrastructure failure that stops the
   session before any metric is read. It uses the same frozen inputs, and the
   reason is logged in the session log and the evaluator's report.
3. If 2025 passes but the 2026 forward check misses the same rules, the
   orchestrator stops before publishing projections and shows the owner the
   results.
4. Nothing above is relaxed to make the Week 9 kickoff (D-021).

## Known facts recorded before opening 2025

- Validation (2022-2024, report v3): all 16 accuracy and start/sit lines pass;
  tie-aware coverage passes at all six positions (QB 0.779, RB 0.785, WR 0.786,
  TE 0.793, K 0.807, DST 0.820). The holdout has 18 weeks instead of 51, so
  intervals are wider and a "beats" line is harder to pass.
- K and DST boom/bust probabilities are low confidence (D-089); they are not
  among the 22 lines and are reported as information.
- The coverage rule and the calibration method were changed after the
  2022-2024 results were seen (D-085, D-086, D-088) and before 2025 was opened.
- Correction (owner request): from v2 to v3 the raw predictions were unchanged
  (identical raw-row hash), but calibrated quantiles, coverage, and 15
  calibrated verdict cells changed. The v2 TE tie-aware coverage was 0.791.
