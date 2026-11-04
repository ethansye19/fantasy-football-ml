# Holdout release report: `chain_v1-primary-c2024w19-20261102T222634Z`

Verdict: NOT APPROVED

Forward check: PASS

The forward check never changes the verdict above (D-094). Under the owner's rule a forward FAIL means the orchestrator stops before publishing projections. A NOT APPROVED verdict is final: nothing is tuned on the holdout and the session is not rerun for a better result (D-092, D-093).

## Pre-registration

| item | value |
| --- | --- |
| file | `docs\status\holdout-preregistration.md`, sha256 `e40058ea4302bba2c31f1d201494a7b0df7b5ee2a8f39ee3b0332cd0e9bedd8b` |
| candidate | `chain_v1-primary-c2024w19-20261102T222634Z` (focus `chain_v1+cal`) |
| commit | `3ecf61069020d6fcba1ddeca0fdb742afd09d794`; report generated at `294aac0` |
| holdout season | 2025 |
| forward | {"season": 2026, "weeks": [1, 2, 3, 4, 5, 6, 7]} |
| calibration mode | frozen |
| bootstrap | 500 week-clustered paired draws, seed 0; metric seed 0 |
| approval lines | 22 lines, spec sha256 `ea706e17578e6b5caa85eb9db3fbffa6758a6bc85dae2169a707b425b91de2ad` |

## Holdout season 2025

### Integrity checks (before any metric was read)

| check | result | detail |
| --- | --- | --- |
| approval lines | PASS | the pre-registration pins the 22 lines of this code |
| bootstrap and seeds | PASS | pre-registered {'n_boot': 500, 'seed': 0, 'metric_seed': 0}, the pinned spec uses {'n_boot': 500, 'seed': 0, 'metric_seed': 0} |
| commit | PASS | pre-registered X 3ecf61069020d6fcba1ddeca0fdb742afd09d794; run sessions: 294aac0: Y 294aac054185 descends from X 3ecf61069020; diff ['docs/status/holdout-preregistration.md'] |
| report generation commit | PASS | generator HEAD 294aac054185: Y 294aac054185 descends from X 3ecf61069020; diff ['docs/status/holdout-preregistration.md']; working tree clean |
| seasons and weeks | PASS | run reports seasons [2025], expected [2025] |
| FROZEN.json | PASS | artifacts/calibration/FROZEN.json: sha256 901fafbd8e63f3c747d62bd072e2c978c6a075a9e7004a5c3a9ce44e5f9f1bbf |
| calibrator rolling_avg_4 | PASS | artifacts/calibration/rolling_avg_4/calibration.json: sha256 c36a02601b1485419cca6600072241d6ea9dc39d33fa720ec043091be2cb91f7 |
| calibrator rolling_avg_3 | PASS | artifacts/calibration/rolling_avg_3/calibration.json: sha256 effde8fc29285e3f2686c973d2305d4918a1983d1a63e1d28a692ba0236f89af |
| calibrator rolling_ew_035 | PASS | artifacts/calibration/rolling_ew_035/calibration.json: sha256 38bad46211d3902d0a39e491adf790c7d9e0d2a1807cc8cacbf1b1bab8813059 |
| calibrator prior_season | PASS | artifacts/calibration/prior_season/calibration.json: sha256 384bb4a872e210f988d42c9ffb68c558712133e9aa2966551a47aab7338b9f41 |
| calibrator rolling_median_8 | PASS | artifacts/calibration/rolling_median_8/calibration.json: sha256 09ffdbd0190f21572371ca0da0e61310e1a74f8a2410c37f0b81e23c46f3c4ba |
| calibrator direct_lgbm_ppr | PASS | artifacts/calibration/direct_lgbm_ppr/calibration.json: sha256 7f98423eabf021d47efabb0e35554b842c7e5d6b33a7713e5120b452b8d1a013 |
| calibrator direct_lgbm_half | PASS | artifacts/calibration/direct_lgbm_half/calibration.json: sha256 5416d7e6da072ac3a3ece0fc9df3f9e584ee3b4c8ea8e4a473b44b02a586317e |
| calibrator direct_lgbm_std | PASS | artifacts/calibration/direct_lgbm_std/calibration.json: sha256 fbb6bccf3e1e7c69a6bf620aaaad47b4c76460f91798be3ece7ae3c6b65b3860 |
| calibrator chain_v1 | PASS | artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z/calibration.json: sha256 3c77725c4e6cf21cd2fc26acaeeea2b3aff22991087614bf35dcd9da280bcd39 |
| calibrator chain_v1_final_report | PASS | artifacts/calibration/chain_v1_final_report/calibration.json: sha256 914fa8d8cd8145df06fb19bdc812c5766bc9e538c62773d5475304f9fc94a45e |
| calibration mode | PASS | run calibration mode 'frozen', pre-registered 'frozen' |
| calibrators used by the run | PASS | 11 recorded, all pre-registered with matching hashes |
| snapshot ids | PASS | run input snapshots equal the pre-registration |
| holdout access log | PASS | one holdout_eval session, 81 lines, 2026-11-04T17:07:08.747272+00:00 to 2026-11-04T18:10:58.568180+00:00; 294aac0: Y 294aac054185 descends from X 3ecf61069020; diff ['docs/status/holdout-preregistration.md'] |
| holdout access log after the reads | PASS | one holdout_eval session, 84 lines, 2026-11-04T17:07:08.747272+00:00 to 2026-11-04T18:32:01.457341+00:00; 294aac0: Y 294aac054185 descends from X 3ecf61069020; diff ['docs/status/holdout-preregistration.md'] |

### The 22 pre-registered lines (157,072 PPR rows read)

| line | statement | result | detail |
| --- | --- | --- | --- |
| L01 | `chain_v1+cal` beats `rolling_avg_4+cal` at QB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L02 | `chain_v1+cal` beats `rolling_avg_4+cal` at RB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L03 | `chain_v1+cal` beats `rolling_avg_4+cal` at WR on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L04 | `chain_v1+cal` beats `rolling_avg_4+cal` at TE on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L05 | `chain_v1+cal` beats `prior_season+cal` at QB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L06 | `chain_v1+cal` beats `prior_season+cal` at RB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L07 | `chain_v1+cal` beats `prior_season+cal` at WR on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L08 | `chain_v1+cal` beats `prior_season+cal` at TE on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L09 | `chain_v1+cal` beats `rolling_median_8+cal` at QB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L10 | `chain_v1+cal` beats `rolling_median_8+cal` at RB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L11 | `chain_v1+cal` beats `rolling_median_8+cal` at WR on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L12 | `chain_v1+cal` beats `rolling_median_8+cal` at TE on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L13 | `chain_v1+cal` start/sit at QB is not worse than the FantasyPros consensus (common pair set) | PASS | not worse (difference +0.001, 95% interval -0.038 to +0.044) |
| L14 | `chain_v1+cal` start/sit at RB is not worse than the FantasyPros consensus (common pair set) | PASS | not worse (difference -0.007, 95% interval -0.024 to +0.009) |
| L15 | `chain_v1+cal` start/sit at WR is not worse than the FantasyPros consensus (common pair set) | PASS | not worse (difference -0.018, 95% interval -0.044 to +0.005) |
| L16 | `chain_v1+cal` start/sit at TE is not worse than the FantasyPros consensus (common pair set) | PASS | not worse (difference +0.025, 95% interval +0.002 to +0.047) |
| L17 | `chain_v1+cal` tie-aware coverage at QB within [0.75, 0.85] | **FAIL** | tie-aware coverage 0.739: `chain_v1+cal` at QB is outside [0.75, 0.85] |
| L18 | `chain_v1+cal` tie-aware coverage at RB within [0.75, 0.85] | PASS | tie-aware coverage 0.774 |
| L19 | `chain_v1+cal` tie-aware coverage at WR within [0.75, 0.85] | PASS | tie-aware coverage 0.793 |
| L20 | `chain_v1+cal` tie-aware coverage at TE within [0.75, 0.85] | PASS | tie-aware coverage 0.826 |
| L21 | `chain_v1+cal` tie-aware coverage at K within [0.75, 0.85] | PASS | tie-aware coverage 0.798 |
| L22 | `chain_v1+cal` tie-aware coverage at DST within [0.75, 0.85] | PASS | tie-aware coverage 0.800 |

Failing lines in plain words:

- L17: tie-aware coverage 0.739: `chain_v1+cal` at QB is outside [0.75, 0.85].

### Information outside the 22 lines (not part of the verdict)


#### Verdict grid, calibrated (focus minus comparator, D-078)

| position | metric | vs rolling_avg_4 | vs prior_season | vs rolling_median_8 | vs consensus_ecr | vs direct_lgbm |
| --- | --- | --- | --- | --- | --- | --- |
| QB | MAE | beats (-0.907 [-1.125, -0.672] B) | beats (-1.151 [-1.414, -0.843] B) | beats (-0.853 [-1.074, -0.647] B) | worse (+0.263 [+0.099, +0.418] W) | no difference (+0.022 [-0.118, +0.153]) |
| QB | pinball q10 | beats (-0.405 [-0.470, -0.345] B) | beats (-0.311 [-0.397, -0.211] B) | beats (-0.333 [-0.428, -0.253] B) | no difference (-0.010 [-0.090, +0.069]) | no difference (-0.004 [-0.037, +0.027]) |
| QB | pinball q50 | beats (-0.454 [-0.563, -0.336] B) | beats (-0.575 [-0.707, -0.422] B) | beats (-0.426 [-0.537, -0.323] B) | worse (+0.131 [+0.049, +0.209] W) | no difference (+0.011 [-0.059, +0.076]) |
| QB | pinball q90 | beats (-0.382 [-0.472, -0.279] B) | beats (-0.328 [-0.459, -0.203] B) | beats (-0.339 [-0.456, -0.214] B) | no difference (+0.023 [-0.033, +0.081]) | no difference (-0.034 [-0.086, +0.016]) |
| QB | tie-aware coverage gap | worse (+0.041 [+0.002, +0.076] W) | worse (+0.053 [+0.013, +0.081] W) | worse (+0.048 [+0.012, +0.077] W) | no difference (+0.014 [-0.019, +0.035]) | worse (+0.031 [+0.010, +0.051] W) |
| QB | Spearman | no difference (+0.140 [-0.071, +0.361]) | beats (+0.209 [+0.016, +0.377] B) | no difference (+0.082 [-0.090, +0.232]) | no difference (+0.033 [-0.082, +0.156]) | no difference (+0.038 [-0.081, +0.149]) |
| QB | start/sit | no difference (+0.040 [-0.029, +0.119]) | beats (+0.074 [+0.009, +0.131] B) | no difference (+0.022 [-0.038, +0.075]) | no difference (+0.001 [-0.038, +0.044]) | no difference (+0.006 [-0.034, +0.043]) |
| RB | MAE | beats (-0.370 [-0.496, -0.253] B) | beats (-0.445 [-0.582, -0.311] B) | beats (-0.268 [-0.345, -0.195] B) | worse (+0.086 [+0.000, +0.172] W) | no difference (+0.007 [-0.051, +0.063]) |
| RB | pinball q10 | beats (-0.281 [-0.330, -0.243] B) | beats (-0.134 [-0.180, -0.090] B) | beats (-0.216 [-0.251, -0.179] B) | no difference (+0.007 [-0.003, +0.017]) | no difference (-0.001 [-0.008, +0.007]) |
| RB | pinball q50 | beats (-0.185 [-0.248, -0.126] B) | beats (-0.222 [-0.291, -0.156] B) | beats (-0.134 [-0.173, -0.098] B) | worse (+0.043 [+0.000, +0.086] W) | no difference (+0.003 [-0.025, +0.032]) |
| RB | pinball q90 | beats (-0.274 [-0.345, -0.216] B) | beats (-0.233 [-0.299, -0.176] B) | beats (-0.254 [-0.327, -0.197] B) | no difference (-0.031 [-0.086, +0.029]) | no difference (-0.029 [-0.069, +0.003]) |
| RB | tie-aware coverage gap | no difference (+0.013 [-0.009, +0.035]) | no difference (+0.020 [-0.007, +0.042]) | no difference (+0.020 [-0.004, +0.034]) | beats (-0.041 [-0.054, -0.023] B) | no difference (+0.020 [-0.017, +0.040]) |
| RB | Spearman | no difference (+0.087 [-0.008, +0.201]) | beats (+0.145 [+0.063, +0.225] B) | no difference (+0.071 [-0.010, +0.162]) | no difference (-0.024 [-0.073, +0.030]) | beats (+0.042 [+0.002, +0.079] B) |
| RB | start/sit | no difference (+0.027 [-0.008, +0.067]) | beats (+0.045 [+0.016, +0.075] B) | no difference (+0.021 [-0.008, +0.054]) | no difference (-0.007 [-0.024, +0.009]) | no difference (+0.015 [-0.002, +0.029]) |
| WR | MAE | beats (-0.477 [-0.610, -0.353] B) | beats (-0.422 [-0.525, -0.327] B) | beats (-0.328 [-0.471, -0.201] B) | no difference (-0.014 [-0.079, +0.071]) | beats (-0.044 [-0.081, -0.010] B) |
| WR | pinball q10 | beats (-0.303 [-0.345, -0.270] B) | beats (-0.178 [-0.252, -0.130] B) | beats (-0.242 [-0.284, -0.210] B) | no difference (-0.003 [-0.015, +0.009]) | beats (-0.008 [-0.013, -0.003] B) |
| WR | pinball q50 | beats (-0.238 [-0.305, -0.176] B) | beats (-0.211 [-0.262, -0.163] B) | beats (-0.164 [-0.236, -0.100] B) | no difference (-0.007 [-0.039, +0.035]) | beats (-0.022 [-0.040, -0.005] B) |
| WR | pinball q90 | beats (-0.280 [-0.326, -0.237] B) | beats (-0.242 [-0.307, -0.179] B) | beats (-0.272 [-0.321, -0.222] B) | no difference (-0.010 [-0.029, +0.008]) | beats (-0.026 [-0.045, -0.008] B) |
| WR | tie-aware coverage gap | no difference (-0.004 [-0.021, +0.011]) | no difference (-0.004 [-0.024, +0.011]) | no difference (+0.000 [-0.014, +0.013]) | beats (-0.036 [-0.048, -0.014] B) | no difference (+0.004 [-0.012, +0.015]) |
| WR | Spearman | beats (+0.125 [+0.045, +0.210] B) | beats (+0.070 [+0.026, +0.110] B) | beats (+0.080 [+0.016, +0.148] B) | no difference (-0.039 [-0.108, +0.023]) | beats (+0.059 [+0.024, +0.102] B) |
| WR | start/sit | beats (+0.044 [+0.015, +0.074] B) | beats (+0.026 [+0.009, +0.040] B) | beats (+0.030 [+0.008, +0.052] B) | no difference (-0.018 [-0.044, +0.005]) | beats (+0.020 [+0.006, +0.035] B) |
| TE | MAE | beats (-0.414 [-0.505, -0.337] B) | beats (-0.401 [-0.482, -0.317] B) | beats (-0.311 [-0.391, -0.208] B) | no difference (-0.025 [-0.066, +0.017]) | beats (-0.057 [-0.093, -0.020] B) |
| TE | pinball q10 | beats (-0.210 [-0.237, -0.184] B) | beats (-0.121 [-0.173, -0.070] B) | beats (-0.161 [-0.193, -0.131] B) | no difference (-0.001 [-0.010, +0.008]) | no difference (-0.002 [-0.008, +0.002]) |
| TE | pinball q50 | beats (-0.207 [-0.252, -0.168] B) | beats (-0.201 [-0.241, -0.158] B) | beats (-0.155 [-0.195, -0.104] B) | no difference (-0.012 [-0.033, +0.009]) | beats (-0.028 [-0.047, -0.010] B) |
| TE | pinball q90 | beats (-0.207 [-0.265, -0.150] B) | beats (-0.164 [-0.205, -0.121] B) | beats (-0.178 [-0.242, -0.113] B) | worse (+0.023 [+0.003, +0.040] W) | no difference (-0.009 [-0.032, +0.014]) |
| TE | tie-aware coverage gap | no difference (+0.018 [-0.014, +0.043]) | no difference (-0.032 [-0.072, +0.010]) | no difference (+0.016 [-0.020, +0.045]) | no difference (+0.007 [-0.019, +0.034]) | no difference (+0.010 [-0.009, +0.028]) |
| TE | Spearman | beats (+0.199 [+0.078, +0.324] B) | beats (+0.209 [+0.064, +0.355] B) | beats (+0.230 [+0.106, +0.343] B) | beats (+0.062 [+0.009, +0.110] B) | no difference (+0.025 [-0.062, +0.115]) |
| TE | start/sit | beats (+0.082 [+0.045, +0.123] B) | beats (+0.077 [+0.023, +0.132] B) | beats (+0.083 [+0.037, +0.124] B) | beats (+0.025 [+0.002, +0.047] B) | no difference (+0.016 [-0.019, +0.049]) |
| K | MAE | beats (-0.265 [-0.505, -0.042] B) | no difference (-0.033 [-0.165, +0.103]) | beats (-0.181 [-0.376, -0.006] B) | no difference (+0.050 [-0.034, +0.160]) | no difference (+0.090 [-0.009, +0.205]) |
| K | pinball q10 | beats (-0.216 [-0.304, -0.142] B) | beats (-0.091 [-0.157, -0.036] B) | beats (-0.133 [-0.190, -0.082] B) | no difference (-0.004 [-0.016, +0.009]) | no difference (-0.008 [-0.024, +0.009]) |
| K | pinball q50 | beats (-0.132 [-0.253, -0.021] B) | no difference (-0.017 [-0.082, +0.051]) | beats (-0.091 [-0.188, -0.003] B) | no difference (+0.025 [-0.017, +0.080]) | no difference (+0.045 [-0.004, +0.102]) |
| K | pinball q90 | no difference (-0.029 [-0.200, +0.150]) | no difference (+0.047 [-0.086, +0.197]) | no difference (+0.002 [-0.171, +0.187]) | worse (+0.118 [+0.019, +0.249] W) | worse (+0.132 [+0.027, +0.252] W) |
| K | tie-aware coverage gap | no difference (+0.000 [-0.026, +0.026]) | no difference (-0.013 [-0.034, +0.024]) | no difference (+0.000 [-0.024, +0.023]) | no difference (+0.009 [-0.023, +0.030]) | no difference (-0.011 [-0.023, +0.025]) |
| K | Spearman | no difference (+0.066 [-0.103, +0.231]) | no difference (+0.067 [-0.099, +0.221]) | no difference (+0.139 [-0.050, +0.312]) | no difference (-0.025 [-0.142, +0.096]) | no difference (+0.087 [-0.057, +0.223]) |
| DST | MAE | beats (-0.674 [-0.899, -0.473] B) | beats (-0.261 [-0.418, -0.073] B) | beats (-0.541 [-0.701, -0.365] B) | no difference (-0.021 [-0.108, +0.067]) | no difference (+0.019 [-0.033, +0.062]) |
| DST | pinball q10 | beats (-0.155 [-0.242, -0.071] B) | beats (-0.072 [-0.142, -0.015] B) | beats (-0.149 [-0.220, -0.082] B) | no difference (+0.027 [-0.007, +0.056]) | no difference (+0.006 [-0.012, +0.025]) |
| DST | pinball q50 | beats (-0.337 [-0.449, -0.236] B) | beats (-0.130 [-0.209, -0.036] B) | beats (-0.271 [-0.351, -0.183] B) | no difference (-0.010 [-0.054, +0.034]) | no difference (+0.009 [-0.016, +0.031]) |
| DST | pinball q90 | beats (-0.375 [-0.492, -0.263] B) | beats (-0.245 [-0.344, -0.160] B) | beats (-0.307 [-0.415, -0.210] B) | no difference (+0.023 [-0.011, +0.055]) | no difference (-0.003 [-0.018, +0.013]) |
| DST | tie-aware coverage gap | no difference (-0.011 [-0.026, +0.028]) | no difference (-0.006 [-0.025, +0.028]) | no difference (-0.018 [-0.040, +0.028]) | no difference (-0.032 [-0.050, +0.011]) | no difference (-0.029 [-0.040, +0.031]) |
| DST | Spearman | beats (+0.390 [+0.165, +0.616] B) | beats (+0.191 [+0.007, +0.406] B) | beats (+0.274 [+0.079, +0.485] B) | no difference (+0.008 [-0.080, +0.102]) | no difference (+0.013 [-0.064, +0.106]) |

#### Verdict grid, raw (focus minus comparator, D-078)

| position | metric | vs rolling_avg_4 | vs prior_season | vs rolling_median_8 | vs consensus_ecr | vs direct_lgbm |
| --- | --- | --- | --- | --- | --- | --- |
| QB | MAE | beats (-0.918 [-1.141, -0.674] B) | beats (-1.253 [-1.572, -0.903] B) | beats (-0.857 [-1.071, -0.638] B) | worse (+0.293 [+0.138, +0.442] W) | no difference (+0.032 [-0.127, +0.199]) |
| QB | pinball q10 | beats (-0.538 [-0.634, -0.449] B) | beats (-0.470 [-0.606, -0.322] B) | beats (-0.490 [-0.605, -0.383] B) | no difference (-0.013 [-0.062, +0.029]) | no difference (+0.002 [-0.036, +0.045]) |
| QB | pinball q50 | beats (-0.459 [-0.571, -0.337] B) | beats (-0.627 [-0.786, -0.451] B) | beats (-0.429 [-0.535, -0.319] B) | worse (+0.146 [+0.069, +0.221] W) | no difference (+0.016 [-0.063, +0.099]) |
| QB | pinball q90 | beats (-0.421 [-0.581, -0.254] B) | beats (-0.349 [-0.542, -0.179] B) | beats (-0.403 [-0.582, -0.220] B) | no difference (+0.051 [-0.012, +0.113]) | no difference (-0.016 [-0.073, +0.038]) |
| QB | tie-aware coverage gap | no difference (+0.007 [-0.036, +0.045]) | no difference (-0.027 [-0.061, +0.006]) | no difference (-0.010 [-0.041, +0.026]) | worse (+0.060 [+0.024, +0.084] W) | worse (+0.067 [+0.042, +0.090] W) |
| QB | Spearman | no difference (+0.137 [-0.076, +0.363]) | beats (+0.213 [+0.016, +0.397] B) | no difference (+0.093 [-0.077, +0.243]) | no difference (+0.033 [-0.081, +0.157]) | no difference (+0.035 [-0.088, +0.153]) |
| QB | start/sit | no difference (+0.032 [-0.037, +0.108]) | beats (+0.073 [+0.007, +0.137] B) | no difference (+0.025 [-0.036, +0.077]) | no difference (+0.001 [-0.038, +0.045]) | no difference (+0.003 [-0.040, +0.046]) |
| RB | MAE | beats (-0.431 [-0.566, -0.305] B) | beats (-0.774 [-0.920, -0.636] B) | beats (-0.284 [-0.371, -0.198] B) | worse (+0.085 [+0.012, +0.164] W) | no difference (-0.005 [-0.060, +0.045]) |
| RB | pinball q10 | beats (-0.393 [-0.469, -0.333] B) | beats (-0.203 [-0.275, -0.139] B) | beats (-0.287 [-0.339, -0.238] B) | no difference (+0.011 [-0.000, +0.021]) | no difference (-0.000 [-0.006, +0.007]) |
| RB | pinball q50 | beats (-0.215 [-0.283, -0.152] B) | beats (-0.387 [-0.460, -0.318] B) | beats (-0.142 [-0.186, -0.099] B) | worse (+0.043 [+0.006, +0.082] W) | no difference (-0.003 [-0.030, +0.022]) |
| RB | pinball q90 | beats (-0.313 [-0.404, -0.244] B) | beats (-0.231 [-0.317, -0.165] B) | beats (-0.291 [-0.388, -0.216] B) | no difference (-0.025 [-0.084, +0.035]) | no difference (-0.017 [-0.058, +0.019]) |
| RB | tie-aware coverage gap | beats (-0.107 [-0.144, -0.071] B) | beats (-0.105 [-0.139, -0.075] B) | beats (-0.096 [-0.139, -0.058] B) | no difference (-0.015 [-0.054, +0.019]) | no difference (+0.004 [-0.014, +0.020]) |
| RB | Spearman | no difference (+0.067 [-0.026, +0.177]) | beats (+0.170 [+0.079, +0.269] B) | no difference (+0.062 [-0.018, +0.154]) | no difference (-0.026 [-0.076, +0.027]) | no difference (+0.034 [-0.008, +0.074]) |
| RB | start/sit | no difference (+0.020 [-0.012, +0.058]) | beats (+0.058 [+0.026, +0.092] B) | no difference (+0.018 [-0.010, +0.050]) | no difference (-0.008 [-0.024, +0.007]) | no difference (+0.011 [-0.006, +0.027]) |
| WR | MAE | beats (-0.518 [-0.664, -0.392] B) | beats (-0.811 [-0.879, -0.745] B) | beats (-0.315 [-0.446, -0.192] B) | no difference (-0.008 [-0.067, +0.060]) | no difference (-0.023 [-0.057, +0.009]) |
| WR | pinball q10 | beats (-0.410 [-0.480, -0.357] B) | beats (-0.277 [-0.376, -0.204] B) | beats (-0.328 [-0.385, -0.287] B) | no difference (-0.004 [-0.015, +0.007]) | beats (-0.008 [-0.014, -0.003] B) |
| WR | pinball q50 | beats (-0.259 [-0.332, -0.196] B) | beats (-0.405 [-0.440, -0.373] B) | beats (-0.157 [-0.223, -0.096] B) | no difference (-0.004 [-0.034, +0.030]) | no difference (-0.011 [-0.029, +0.005]) |
| WR | pinball q90 | beats (-0.282 [-0.333, -0.224] B) | beats (-0.231 [-0.298, -0.166] B) | beats (-0.297 [-0.346, -0.244] B) | no difference (-0.010 [-0.029, +0.008]) | beats (-0.026 [-0.045, -0.008] B) |
| WR | tie-aware coverage gap | beats (-0.110 [-0.131, -0.089] B) | beats (-0.135 [-0.151, -0.118] B) | beats (-0.111 [-0.135, -0.089] B) | beats (-0.031 [-0.042, -0.018] B) | no difference (+0.009 [-0.008, +0.017]) |
| WR | Spearman | beats (+0.105 [+0.023, +0.183] B) | beats (+0.094 [+0.023, +0.151] B) | beats (+0.082 [+0.018, +0.152] B) | no difference (-0.037 [-0.103, +0.024]) | beats (+0.059 [+0.019, +0.106] B) |
| WR | start/sit | beats (+0.037 [+0.009, +0.065] B) | beats (+0.032 [+0.006, +0.054] B) | beats (+0.031 [+0.009, +0.054] B) | no difference (-0.017 [-0.043, +0.007]) | beats (+0.021 [+0.006, +0.037] B) |
| TE | MAE | beats (-0.452 [-0.546, -0.374] B) | beats (-0.616 [-0.710, -0.520] B) | beats (-0.318 [-0.397, -0.220] B) | no difference (-0.037 [-0.080, +0.010]) | beats (-0.064 [-0.106, -0.023] B) |
| TE | pinball q10 | beats (-0.290 [-0.327, -0.258] B) | beats (-0.155 [-0.212, -0.094] B) | beats (-0.215 [-0.261, -0.173] B) | no difference (-0.001 [-0.010, +0.008]) | no difference (-0.002 [-0.008, +0.003]) |
| TE | pinball q50 | beats (-0.226 [-0.273, -0.187] B) | beats (-0.308 [-0.355, -0.260] B) | beats (-0.159 [-0.198, -0.110] B) | no difference (-0.018 [-0.040, +0.005]) | beats (-0.032 [-0.053, -0.011] B) |
| TE | pinball q90 | beats (-0.226 [-0.288, -0.161] B) | beats (-0.176 [-0.232, -0.123] B) | beats (-0.211 [-0.281, -0.135] B) | worse (+0.025 [+0.005, +0.042] W) | no difference (-0.009 [-0.031, +0.014]) |
| TE | tie-aware coverage gap | beats (-0.099 [-0.127, -0.068] B) | beats (-0.110 [-0.154, -0.064] B) | beats (-0.101 [-0.139, -0.066] B) | no difference (+0.009 [-0.016, +0.034]) | no difference (+0.004 [-0.013, +0.022]) |
| TE | Spearman | beats (+0.205 [+0.056, +0.350] B) | beats (+0.226 [+0.076, +0.368] B) | beats (+0.232 [+0.109, +0.345] B) | beats (+0.061 [+0.005, +0.109] B) | no difference (+0.031 [-0.067, +0.124]) |
| TE | start/sit | beats (+0.081 [+0.032, +0.131] B) | beats (+0.089 [+0.027, +0.146] B) | beats (+0.085 [+0.039, +0.125] B) | beats (+0.026 [+0.002, +0.050] B) | no difference (+0.020 [-0.018, +0.055]) |
| K | MAE | no difference (-0.208 [-0.455, +0.014]) | no difference (-0.003 [-0.139, +0.139]) | no difference (-0.137 [-0.333, +0.042]) | worse (+0.095 [+0.004, +0.196] W) | worse (+0.126 [+0.035, +0.228] W) |
| K | pinball q10 | beats (-0.271 [-0.388, -0.164] B) | beats (-0.125 [-0.210, -0.044] B) | beats (-0.174 [-0.261, -0.083] B) | worse (+0.032 [+0.004, +0.057] W) | worse (+0.024 [+0.006, +0.043] W) |
| K | pinball q50 | no difference (-0.104 [-0.227, +0.007]) | no difference (-0.001 [-0.069, +0.070]) | no difference (-0.068 [-0.167, +0.021]) | worse (+0.048 [+0.002, +0.098] W) | worse (+0.063 [+0.017, +0.114] W) |
| K | pinball q90 | no difference (-0.082 [-0.257, +0.105]) | no difference (-0.027 [-0.164, +0.120]) | no difference (-0.073 [-0.235, +0.103]) | worse (+0.118 [+0.019, +0.249] W) | worse (+0.124 [+0.018, +0.258] W) |
| K | tie-aware coverage gap | beats (-0.108 [-0.154, -0.052] B) | beats (-0.150 [-0.202, -0.092] B) | beats (-0.121 [-0.181, -0.055] B) | no difference (+0.023 [-0.025, +0.044]) | no difference (+0.011 [-0.030, +0.033]) |
| K | Spearman | no difference (+0.047 [-0.119, +0.212]) | no difference (+0.032 [-0.117, +0.186]) | no difference (+0.123 [-0.057, +0.287]) | no difference (-0.059 [-0.192, +0.078]) | no difference (+0.049 [-0.082, +0.181]) |
| DST | MAE | beats (-0.654 [-0.846, -0.466] B) | beats (-0.324 [-0.483, -0.160] B) | beats (-0.489 [-0.660, -0.319] B) | no difference (+0.025 [-0.044, +0.099]) | no difference (+0.045 [-0.015, +0.105]) |
| DST | pinball q10 | beats (-0.295 [-0.385, -0.206] B) | beats (-0.152 [-0.232, -0.081] B) | beats (-0.250 [-0.334, -0.158] B) | no difference (+0.027 [-0.007, +0.056]) | no difference (+0.006 [-0.012, +0.024]) |
| DST | pinball q50 | beats (-0.327 [-0.423, -0.233] B) | beats (-0.162 [-0.242, -0.080] B) | beats (-0.244 [-0.330, -0.160] B) | no difference (+0.012 [-0.022, +0.050]) | no difference (+0.023 [-0.007, +0.053]) |
| DST | pinball q90 | beats (-0.468 [-0.611, -0.330] B) | beats (-0.311 [-0.453, -0.178] B) | beats (-0.399 [-0.558, -0.247] B) | no difference (+0.023 [-0.011, +0.055]) | no difference (-0.002 [-0.017, +0.014]) |
| DST | tie-aware coverage gap | beats (-0.086 [-0.121, -0.051] B) | beats (-0.075 [-0.107, -0.044] B) | beats (-0.064 [-0.093, -0.034] B) | worse (+0.030 [+0.011, +0.049] W) | worse (+0.042 [+0.017, +0.060] W) |
| DST | Spearman | beats (+0.387 [+0.167, +0.616] B) | beats (+0.187 [+0.019, +0.381] B) | beats (+0.252 [+0.067, +0.456] B) | no difference (-0.011 [-0.093, +0.075]) | no difference (+0.005 [-0.077, +0.102]) |

#### Closed coverage and tails

Exclusive shares of active rows relative to [q10, q90] (ties within 1e-9 points): below q10, tied at q10, strictly inside, tied at q90 (and not at q10), above q90. Closed coverage is tied q10 + inside + tied q90 and is reported for reference. The last column is the **acceptance definition (D-085)**: the share of rows whose randomized PIT lies in (0.1, 0.9), with the PIT drawn uniformly in [F(y-), F(y)] from the model's quantile knots and the prior-season position bounds (seed 0), so a point mass is spread over its probability instead of counting as inside.

| position | model | n | below q10 | tied q10 | inside | tied q90 | above q90 | closed coverage | tie-aware (acceptance, D-085) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QB | `chain_v1` | 674 | 0.156 | 0.024 | 0.682 | 0.000 | 0.138 | 0.706 | 0.693 |
| QB | `chain_v1+cal` | 674 | 0.135 | 0.024 | 0.726 | 0.000 | 0.116 | 0.749 | 0.739 |
| QB | `chain_v1_final_report` | 674 | 0.171 | 0.022 | 0.680 | 0.001 | 0.126 | 0.703 | 0.691 |
| QB | `chain_v1_final_report+cal` | 674 | 0.131 | 0.000 | 0.758 | 0.000 | 0.111 | 0.758 | 0.758 |
| QB | `direct_lgbm` | 674 | 0.126 | 0.000 | 0.760 | 0.000 | 0.114 | 0.760 | 0.760 |
| QB | `direct_lgbm+cal` | 674 | 0.126 | 0.000 | 0.770 | 0.000 | 0.104 | 0.770 | 0.770 |
| QB | `prior_season` | 674 | 0.187 | 0.001 | 0.666 | 0.000 | 0.145 | 0.668 | 0.666 |
| QB | `prior_season+cal` | 674 | 0.117 | 0.000 | 0.809 | 0.000 | 0.074 | 0.809 | 0.809 |
| QB | `rolling_avg_3` | 674 | 0.160 | 0.000 | 0.687 | 0.000 | 0.153 | 0.687 | 0.687 |
| QB | `rolling_avg_3+cal` | 674 | 0.091 | 0.000 | 0.829 | 0.000 | 0.080 | 0.829 | 0.829 |
| QB | `rolling_avg_4` | 674 | 0.154 | 0.000 | 0.700 | 0.000 | 0.145 | 0.700 | 0.700 |
| QB | `rolling_avg_4+cal` | 674 | 0.092 | 0.000 | 0.820 | 0.000 | 0.088 | 0.820 | 0.820 |
| QB | `rolling_ew_035` | 674 | 0.145 | 0.000 | 0.705 | 0.000 | 0.150 | 0.705 | 0.705 |
| QB | `rolling_ew_035+cal` | 674 | 0.105 | 0.000 | 0.820 | 0.000 | 0.074 | 0.820 | 0.820 |
| QB | `rolling_median_8` | 674 | 0.165 | 0.000 | 0.682 | 0.000 | 0.153 | 0.682 | 0.682 |
| QB | `rolling_median_8+cal` | 674 | 0.111 | 0.000 | 0.813 | 0.000 | 0.076 | 0.813 | 0.813 |
| RB | `chain_v1` | 1,630 | 0.048 | 0.147 | 0.713 | 0.004 | 0.087 | 0.863 | 0.825 |
| RB | `chain_v1+cal` | 1,630 | 0.077 | 0.125 | 0.693 | 0.000 | 0.106 | 0.817 | 0.774 |
| RB | `chain_v1_final_report` | 1,630 | 0.052 | 0.146 | 0.718 | 0.001 | 0.083 | 0.864 | 0.828 |
| RB | `chain_v1_final_report+cal` | 1,630 | 0.076 | 0.122 | 0.699 | 0.000 | 0.103 | 0.821 | 0.780 |
| RB | `direct_lgbm` | 1,630 | 0.077 | 0.000 | 0.821 | 0.000 | 0.102 | 0.821 | 0.821 |
| RB | `direct_lgbm+cal` | 1,630 | 0.082 | 0.000 | 0.807 | 0.000 | 0.112 | 0.807 | 0.807 |
| RB | `prior_season` | 1,630 | 0.158 | 0.055 | 0.627 | 0.005 | 0.155 | 0.687 | 0.671 |
| RB | `prior_season+cal` | 1,630 | 0.096 | 0.000 | 0.806 | 0.000 | 0.098 | 0.806 | 0.806 |
| RB | `rolling_avg_3` | 1,630 | 0.163 | 0.015 | 0.652 | 0.007 | 0.164 | 0.673 | 0.670 |
| RB | `rolling_avg_3+cal` | 1,630 | 0.108 | 0.000 | 0.785 | 0.000 | 0.107 | 0.785 | 0.785 |
| RB | `rolling_avg_4` | 1,630 | 0.164 | 0.017 | 0.648 | 0.006 | 0.164 | 0.671 | 0.668 |
| RB | `rolling_avg_4+cal` | 1,630 | 0.110 | 0.000 | 0.787 | 0.000 | 0.102 | 0.787 | 0.787 |
| RB | `rolling_ew_035` | 1,630 | 0.156 | 0.009 | 0.666 | 0.003 | 0.165 | 0.679 | 0.678 |
| RB | `rolling_ew_035+cal` | 1,630 | 0.103 | 0.000 | 0.789 | 0.000 | 0.108 | 0.789 | 0.789 |
| RB | `rolling_median_8` | 1,630 | 0.149 | 0.045 | 0.636 | 0.004 | 0.166 | 0.683 | 0.679 |
| RB | `rolling_median_8+cal` | 1,630 | 0.103 | 0.000 | 0.793 | 0.000 | 0.104 | 0.793 | 0.793 |
| WR | `chain_v1` | 2,638 | 0.042 | 0.255 | 0.613 | 0.001 | 0.089 | 0.869 | 0.789 |
| WR | `chain_v1+cal` | 2,638 | 0.038 | 0.255 | 0.617 | 0.001 | 0.089 | 0.873 | 0.793 |
| WR | `chain_v1_final_report` | 2,638 | 0.052 | 0.254 | 0.611 | 0.001 | 0.083 | 0.865 | 0.783 |
| WR | `chain_v1_final_report+cal` | 2,638 | 0.044 | 0.251 | 0.610 | 0.000 | 0.095 | 0.861 | 0.782 |
| WR | `direct_lgbm` | 2,638 | 0.106 | 0.004 | 0.798 | 0.000 | 0.092 | 0.802 | 0.798 |
| WR | `direct_lgbm+cal` | 2,638 | 0.106 | 0.000 | 0.803 | 0.000 | 0.092 | 0.803 | 0.803 |
| WR | `prior_season` | 2,638 | 0.159 | 0.101 | 0.590 | 0.002 | 0.148 | 0.693 | 0.655 |
| WR | `prior_season+cal` | 2,638 | 0.116 | 0.000 | 0.790 | 0.000 | 0.095 | 0.790 | 0.790 |
| WR | `rolling_avg_3` | 2,638 | 0.153 | 0.004 | 0.670 | 0.011 | 0.161 | 0.686 | 0.683 |
| WR | `rolling_avg_3+cal` | 2,638 | 0.109 | 0.000 | 0.797 | 0.000 | 0.094 | 0.797 | 0.797 |
| WR | `rolling_avg_4` | 2,638 | 0.159 | 0.006 | 0.665 | 0.011 | 0.158 | 0.683 | 0.679 |
| WR | `rolling_avg_4+cal` | 2,638 | 0.112 | 0.000 | 0.790 | 0.000 | 0.099 | 0.790 | 0.790 |
| WR | `rolling_ew_035` | 2,638 | 0.152 | 0.002 | 0.682 | 0.007 | 0.157 | 0.691 | 0.690 |
| WR | `rolling_ew_035+cal` | 2,638 | 0.103 | 0.000 | 0.799 | 0.000 | 0.098 | 0.799 | 0.799 |
| WR | `rolling_median_8` | 2,638 | 0.150 | 0.043 | 0.635 | 0.010 | 0.162 | 0.688 | 0.678 |
| WR | `rolling_median_8+cal` | 2,638 | 0.111 | 0.000 | 0.793 | 0.000 | 0.095 | 0.793 | 0.793 |
| TE | `chain_v1` | 1,667 | 0.031 | 0.373 | 0.509 | 0.002 | 0.085 | 0.883 | 0.827 |
| TE | `chain_v1+cal` | 1,667 | 0.031 | 0.371 | 0.507 | 0.000 | 0.091 | 0.878 | 0.826 |
| TE | `chain_v1_final_report` | 1,667 | 0.040 | 0.372 | 0.504 | 0.003 | 0.081 | 0.879 | 0.819 |
| TE | `chain_v1_final_report+cal` | 1,667 | 0.038 | 0.369 | 0.497 | 0.001 | 0.095 | 0.866 | 0.812 |
| TE | `direct_lgbm` | 1,667 | 0.086 | 0.000 | 0.823 | 0.000 | 0.091 | 0.823 | 0.823 |
| TE | `direct_lgbm+cal` | 1,667 | 0.089 | 0.000 | 0.816 | 0.000 | 0.095 | 0.816 | 0.816 |
| TE | `prior_season` | 1,667 | 0.128 | 0.169 | 0.522 | 0.023 | 0.158 | 0.713 | 0.663 |
| TE | `prior_season+cal` | 1,667 | 0.114 | 0.169 | 0.601 | 0.000 | 0.116 | 0.770 | 0.742 |
| TE | `rolling_avg_3` | 1,667 | 0.152 | 0.037 | 0.617 | 0.030 | 0.164 | 0.684 | 0.673 |
| TE | `rolling_avg_3+cal` | 1,667 | 0.104 | 0.000 | 0.791 | 0.000 | 0.105 | 0.791 | 0.791 |
| TE | `rolling_avg_4` | 1,667 | 0.151 | 0.038 | 0.614 | 0.034 | 0.163 | 0.686 | 0.674 |
| TE | `rolling_avg_4+cal` | 1,667 | 0.101 | 0.000 | 0.792 | 0.000 | 0.107 | 0.792 | 0.792 |
| TE | `rolling_ew_035` | 1,667 | 0.142 | 0.014 | 0.665 | 0.015 | 0.165 | 0.693 | 0.690 |
| TE | `rolling_ew_035+cal` | 1,667 | 0.101 | 0.000 | 0.796 | 0.000 | 0.103 | 0.796 | 0.796 |
| TE | `rolling_median_8` | 1,667 | 0.140 | 0.130 | 0.540 | 0.022 | 0.167 | 0.692 | 0.671 |
| TE | `rolling_median_8+cal` | 1,667 | 0.104 | 0.000 | 0.790 | 0.000 | 0.106 | 0.790 | 0.790 |
| K | `chain_v1` | 535 | 0.030 | 0.037 | 0.815 | 0.022 | 0.095 | 0.875 | 0.815 |
| K | `chain_v1+cal` | 535 | 0.084 | 0.000 | 0.798 | 0.022 | 0.095 | 0.821 | 0.798 |
| K | `chain_v1_final_report` | 535 | 0.028 | 0.041 | 0.824 | 0.019 | 0.088 | 0.884 | 0.824 |
| K | `chain_v1_final_report+cal` | 535 | 0.086 | 0.000 | 0.806 | 0.000 | 0.108 | 0.806 | 0.806 |
| K | `direct_lgbm` | 535 | 0.079 | 0.000 | 0.796 | 0.000 | 0.125 | 0.796 | 0.796 |
| K | `direct_lgbm+cal` | 535 | 0.084 | 0.000 | 0.813 | 0.000 | 0.103 | 0.813 | 0.813 |
| K | `prior_season` | 535 | 0.174 | 0.007 | 0.636 | 0.000 | 0.183 | 0.643 | 0.636 |
| K | `prior_season+cal` | 535 | 0.086 | 0.000 | 0.815 | 0.000 | 0.099 | 0.815 | 0.815 |
| K | `rolling_avg_3` | 535 | 0.166 | 0.002 | 0.667 | 0.000 | 0.164 | 0.669 | 0.669 |
| K | `rolling_avg_3+cal` | 535 | 0.114 | 0.000 | 0.789 | 0.000 | 0.097 | 0.789 | 0.789 |
| K | `rolling_avg_4` | 535 | 0.161 | 0.000 | 0.677 | 0.002 | 0.161 | 0.679 | 0.677 |
| K | `rolling_avg_4+cal` | 535 | 0.093 | 0.000 | 0.798 | 0.000 | 0.108 | 0.798 | 0.798 |
| K | `rolling_ew_035` | 535 | 0.164 | 0.000 | 0.675 | 0.000 | 0.161 | 0.675 | 0.675 |
| K | `rolling_ew_035+cal` | 535 | 0.095 | 0.000 | 0.802 | 0.000 | 0.103 | 0.802 | 0.802 |
| K | `rolling_median_8` | 535 | 0.166 | 0.002 | 0.662 | 0.006 | 0.164 | 0.669 | 0.664 |
| K | `rolling_median_8+cal` | 535 | 0.093 | 0.000 | 0.802 | 0.000 | 0.105 | 0.802 | 0.802 |
| DST | `chain_v1` | 544 | 0.108 | 0.048 | 0.735 | 0.017 | 0.092 | 0.800 | 0.735 |
| DST | `chain_v1+cal` | 544 | 0.108 | 0.000 | 0.800 | 0.000 | 0.092 | 0.800 | 0.800 |
| DST | `chain_v1_final_report` | 544 | 0.112 | 0.050 | 0.726 | 0.020 | 0.092 | 0.796 | 0.726 |
| DST | `chain_v1_final_report+cal` | 544 | 0.112 | 0.000 | 0.796 | 0.000 | 0.092 | 0.796 | 0.796 |
| DST | `direct_lgbm` | 544 | 0.121 | 0.000 | 0.778 | 0.000 | 0.101 | 0.778 | 0.778 |
| DST | `direct_lgbm+cal` | 544 | 0.121 | 0.000 | 0.770 | 0.000 | 0.108 | 0.770 | 0.770 |
| DST | `prior_season` | 544 | 0.162 | 0.007 | 0.658 | 0.004 | 0.169 | 0.669 | 0.660 |
| DST | `prior_season+cal` | 544 | 0.086 | 0.000 | 0.794 | 0.000 | 0.119 | 0.794 | 0.794 |
| DST | `rolling_avg_3` | 544 | 0.171 | 0.009 | 0.631 | 0.000 | 0.189 | 0.640 | 0.631 |
| DST | `rolling_avg_3+cal` | 544 | 0.101 | 0.006 | 0.779 | 0.000 | 0.114 | 0.785 | 0.779 |
| DST | `rolling_avg_4` | 544 | 0.171 | 0.006 | 0.647 | 0.002 | 0.175 | 0.654 | 0.649 |
| DST | `rolling_avg_4+cal` | 544 | 0.099 | 0.004 | 0.789 | 0.000 | 0.108 | 0.792 | 0.789 |
| DST | `rolling_ew_035` | 544 | 0.158 | 0.002 | 0.667 | 0.000 | 0.173 | 0.669 | 0.667 |
| DST | `rolling_ew_035+cal` | 544 | 0.096 | 0.004 | 0.798 | 0.000 | 0.103 | 0.801 | 0.798 |
| DST | `rolling_median_8` | 544 | 0.164 | 0.002 | 0.671 | 0.004 | 0.160 | 0.675 | 0.671 |
| DST | `rolling_median_8+cal` | 544 | 0.105 | 0.002 | 0.781 | 0.000 | 0.112 | 0.783 | 0.781 |

Pinball loss at q10 and q90 (the proper scoring rule for each quantile), calibrated forms; difference is `chain_v1+cal - comparator`, negative favours the focus model, B/W by the D-078 rule.

| position | comparator | pinball q10 `chain_v1+cal` | comparator | difference [95% CI] | pinball q90 `chain_v1+cal` | comparator | difference [95% CI] |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QB | `chain_v1_final_report+cal` | 1.147 | 1.177 | -0.031 [-0.050, -0.012] B | 1.489 | 1.490 | -0.001 [-0.027, +0.025] |
| QB | `direct_lgbm+cal` | 1.147 | 1.151 | -0.004 [-0.037, +0.027] | 1.489 | 1.522 | -0.034 [-0.086, +0.016] |
| QB | `rolling_avg_4+cal` | 1.147 | 1.552 | -0.405 [-0.470, -0.345] B | 1.489 | 1.870 | -0.382 [-0.472, -0.279] B |
| QB | `rolling_avg_3+cal` | 1.147 | 1.594 | -0.447 [-0.508, -0.392] B | 1.489 | 1.876 | -0.387 [-0.473, -0.294] B |
| QB | `rolling_ew_035+cal` | 1.147 | 1.546 | -0.399 [-0.474, -0.328] B | 1.489 | 1.829 | -0.340 [-0.444, -0.230] B |
| QB | `prior_season+cal` | 1.147 | 1.457 | -0.311 [-0.397, -0.211] B | 1.489 | 1.817 | -0.328 [-0.459, -0.203] B |
| QB | `rolling_median_8+cal` | 1.147 | 1.480 | -0.333 [-0.428, -0.253] B | 1.489 | 1.828 | -0.339 [-0.456, -0.214] B |
| RB | `chain_v1_final_report+cal` | 0.624 | 0.624 | +0.000 [-0.004, +0.004] | 1.190 | 1.180 | +0.010 [-0.014, +0.032] |
| RB | `direct_lgbm+cal` | 0.624 | 0.625 | -0.001 [-0.008, +0.007] | 1.190 | 1.219 | -0.029 [-0.069, +0.003] |
| RB | `rolling_avg_4+cal` | 0.624 | 0.905 | -0.281 [-0.330, -0.243] B | 1.190 | 1.463 | -0.274 [-0.345, -0.216] B |
| RB | `rolling_avg_3+cal` | 0.624 | 0.969 | -0.345 [-0.405, -0.293] B | 1.190 | 1.477 | -0.288 [-0.367, -0.223] B |
| RB | `rolling_ew_035+cal` | 0.624 | 0.910 | -0.286 [-0.336, -0.245] B | 1.190 | 1.446 | -0.257 [-0.338, -0.195] B |
| RB | `prior_season+cal` | 0.624 | 0.758 | -0.134 [-0.180, -0.090] B | 1.190 | 1.422 | -0.233 [-0.299, -0.176] B |
| RB | `rolling_median_8+cal` | 0.624 | 0.840 | -0.216 [-0.251, -0.179] B | 1.190 | 1.444 | -0.254 [-0.327, -0.197] B |
| WR | `chain_v1_final_report+cal` | 0.582 | 0.579 | +0.003 [-0.001, +0.007] | 1.154 | 1.137 | +0.017 [+0.004, +0.029] W |
| WR | `direct_lgbm+cal` | 0.582 | 0.590 | -0.008 [-0.013, -0.003] B | 1.154 | 1.180 | -0.026 [-0.045, -0.008] B |
| WR | `rolling_avg_4+cal` | 0.582 | 0.886 | -0.303 [-0.345, -0.270] B | 1.154 | 1.433 | -0.280 [-0.326, -0.237] B |
| WR | `rolling_avg_3+cal` | 0.582 | 0.930 | -0.347 [-0.384, -0.314] B | 1.154 | 1.438 | -0.284 [-0.334, -0.239] B |
| WR | `rolling_ew_035+cal` | 0.582 | 0.887 | -0.304 [-0.344, -0.270] B | 1.154 | 1.421 | -0.267 [-0.310, -0.226] B |
| WR | `prior_season+cal` | 0.582 | 0.761 | -0.178 [-0.252, -0.130] B | 1.154 | 1.396 | -0.242 [-0.307, -0.179] B |
| WR | `rolling_median_8+cal` | 0.582 | 0.824 | -0.242 [-0.284, -0.210] B | 1.154 | 1.425 | -0.272 [-0.321, -0.222] B |
| TE | `chain_v1_final_report+cal` | 0.399 | 0.403 | -0.004 [-0.009, +0.000] | 0.895 | 0.888 | +0.007 [-0.005, +0.020] |
| TE | `direct_lgbm+cal` | 0.399 | 0.402 | -0.002 [-0.008, +0.002] | 0.895 | 0.904 | -0.009 [-0.032, +0.014] |
| TE | `rolling_avg_4+cal` | 0.399 | 0.609 | -0.210 [-0.237, -0.184] B | 0.895 | 1.102 | -0.207 [-0.265, -0.150] B |
| TE | `rolling_avg_3+cal` | 0.399 | 0.635 | -0.236 [-0.267, -0.209] B | 0.895 | 1.106 | -0.211 [-0.272, -0.149] B |
| TE | `rolling_ew_035+cal` | 0.399 | 0.624 | -0.224 [-0.252, -0.200] B | 0.895 | 1.083 | -0.188 [-0.244, -0.131] B |
| TE | `prior_season+cal` | 0.399 | 0.520 | -0.121 [-0.173, -0.070] B | 0.895 | 1.058 | -0.164 [-0.205, -0.121] B |
| TE | `rolling_median_8+cal` | 0.399 | 0.560 | -0.161 [-0.193, -0.131] B | 0.895 | 1.073 | -0.178 [-0.242, -0.113] B |
| K | `chain_v1_final_report+cal` | 0.714 | 0.715 | -0.000 [-0.013, +0.010] | 1.058 | 0.959 | +0.099 [+0.011, +0.205] W |
| K | `direct_lgbm+cal` | 0.714 | 0.723 | -0.008 [-0.024, +0.009] | 1.058 | 0.926 | +0.132 [+0.027, +0.252] W |
| K | `rolling_avg_4+cal` | 0.714 | 0.930 | -0.216 [-0.304, -0.142] B | 1.058 | 1.087 | -0.029 [-0.200, +0.150] |
| K | `rolling_avg_3+cal` | 0.714 | 0.965 | -0.251 [-0.345, -0.174] B | 1.058 | 1.101 | -0.043 [-0.205, +0.131] |
| K | `rolling_ew_035+cal` | 0.714 | 0.908 | -0.193 [-0.278, -0.122] B | 1.058 | 1.081 | -0.023 [-0.188, +0.156] |
| K | `prior_season+cal` | 0.714 | 0.805 | -0.091 [-0.157, -0.036] B | 1.058 | 1.010 | +0.047 [-0.086, +0.197] |
| K | `rolling_median_8+cal` | 0.714 | 0.847 | -0.133 [-0.190, -0.082] B | 1.058 | 1.056 | +0.002 [-0.171, +0.187] |
| DST | `chain_v1_final_report+cal` | 0.804 | 0.807 | -0.003 [-0.014, +0.009] | 1.153 | 1.156 | -0.003 [-0.016, +0.009] |
| DST | `direct_lgbm+cal` | 0.804 | 0.797 | +0.006 [-0.012, +0.025] | 1.153 | 1.155 | -0.003 [-0.018, +0.013] |
| DST | `rolling_avg_4+cal` | 0.804 | 0.958 | -0.155 [-0.242, -0.071] B | 1.153 | 1.528 | -0.375 [-0.492, -0.263] B |
| DST | `rolling_avg_3+cal` | 0.804 | 1.020 | -0.217 [-0.305, -0.124] B | 1.153 | 1.542 | -0.390 [-0.495, -0.276] B |
| DST | `rolling_ew_035+cal` | 0.804 | 0.987 | -0.184 [-0.270, -0.091] B | 1.153 | 1.495 | -0.343 [-0.455, -0.233] B |
| DST | `prior_season+cal` | 0.804 | 0.876 | -0.072 [-0.142, -0.015] B | 1.153 | 1.398 | -0.245 [-0.344, -0.160] B |
| DST | `rolling_median_8+cal` | 0.804 | 0.952 | -0.149 [-0.220, -0.082] B | 1.153 | 1.460 | -0.307 [-0.415, -0.210] B |

#### Boom and bust calibration (frozen calibrator, D-086 rule, D-089 flags)

| position | kind | n | Brier | intercept | slope [95% CI] | D-086 rule |
| --- | --- | --- | --- | --- | --- | --- |
| QB | boom | 674 | 0.132 | -0.611 | 0.563 [0.418, 0.743] | **low confidence** |
| QB | bust | 674 | 0.194 | -0.039 | 0.769 [0.539, 0.982] | **low confidence** |
| RB | boom | 1,630 | 0.090 | -0.112 | 0.908 [0.807, 1.027] | within the rule |
| RB | bust | 1,630 | 0.142 | 0.057 | 0.901 [0.782, 1.042] | within the rule |
| WR | boom | 2,638 | 0.064 | -0.027 | 0.972 [0.883, 1.060] | within the rule |
| WR | bust | 2,638 | 0.151 | 0.037 | 0.903 [0.828, 0.974] | within the rule |
| TE | boom | 1,667 | 0.051 | -0.203 | 0.885 [0.728, 1.110] | within the rule |
| TE | bust | 1,667 | 0.108 | 0.069 | 0.929 [0.827, 1.054] | within the rule |
| K | boom | 535 | 0.167 | 0.097 | 1.140 [0.412, 1.844] | within the rule (D-089: stays flagged) |
| K | bust | 535 | 0.224 | -0.204 | 0.735 [0.495, 1.117] | **low confidence** (D-089: stays flagged) |
| DST | boom | 544 | 0.158 | -0.996 | 0.226 [0.004, 1.127] | **low confidence** (D-089: stays flagged) |
| DST | bust | 544 | 0.214 | -0.196 | 0.528 [0.038, 1.504] | **low confidence** (D-089: stays flagged) |

#### Thursday split (D-081)

Active player-weeks by consensus status (PPR, 2022-2024). Columns (a) and (b) are the populations compared below; the other exclusion reasons are counted separately and are not part of (b).

| position | (a) matched consensus population | (b) game on or before the scrape date | no ECR match (not ranked, no id, or another page) | no scrape that week | valid ECR row without a consensus projection | (c) all active | sum check |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QB | 516 | 44 | 36 | 78 | 0 | 674 | ok |
| RB | 1,211 | 120 | 105 | 194 | 0 | 1,630 | ok |
| WR | 2,035 | 196 | 101 | 306 | 0 | 2,638 | ok |
| TE | 1,267 | 122 | 87 | 191 | 0 | 1,667 | ok |
| K | 363 | 37 | 74 | 61 | 0 | 535 | ok |
| DST | 438 | 42 | 0 | 64 | 0 | 544 | ok |
| ALL | 5,830 | 561 | 403 | 894 | 0 | 7,688 | ok |

Calibrated forms of `chain_v1` and the best baseline `rolling_median_8` on the three populations. Spearman and start/sit use the headline startable set (D-077) restricted to each population; with only a few early-game players per position-week, they are often undefined there (n/a).

| position | population | model | n | MAE | pinball q10 | pinball q50 | pinball q90 | 80% coverage | Spearman | start/sit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QB | (a) matched | `chain_v1+cal` | 516 | 6.128 | 1.165 | 3.064 | 1.459 | 0.775 | 0.199 | 0.552 |
| QB | (a) matched | `rolling_median_8+cal` | 516 | 7.017 | 1.510 | 3.508 | 1.837 | 0.806 | 0.071 | 0.514 |
| QB | (b) early game | `chain_v1+cal` | 44 | 6.570 | 1.322 | 3.285 | 1.464 | 0.727 | n/a | 0.632 |
| QB | (b) early game | `rolling_median_8+cal` | 44 | 7.052 | 1.772 | 3.526 | 1.732 | 0.818 | n/a | 0.632 |
| QB | (c) all active | `chain_v1+cal` | 674 | 6.068 | 1.147 | 3.034 | 1.489 | 0.749 | 0.190 | 0.558 |
| QB | (c) all active | `rolling_median_8+cal` | 674 | 6.921 | 1.480 | 3.460 | 1.828 | 0.813 | 0.107 | 0.536 |
| RB | (a) matched | `chain_v1+cal` | 1,211 | 4.304 | 0.668 | 2.152 | 1.243 | 0.827 | 0.340 | 0.616 |
| RB | (a) matched | `rolling_median_8+cal` | 1,211 | 4.539 | 0.885 | 2.269 | 1.516 | 0.788 | 0.298 | 0.602 |
| RB | (b) early game | `chain_v1+cal` | 120 | 4.173 | 0.670 | 2.087 | 1.149 | 0.783 | n/a | 0.514 |
| RB | (b) early game | `rolling_median_8+cal` | 120 | 4.509 | 0.816 | 2.254 | 1.323 | 0.783 | n/a | 0.571 |
| RB | (c) all active | `chain_v1+cal` | 1,630 | 4.026 | 0.624 | 2.013 | 1.190 | 0.817 | 0.328 | 0.617 |
| RB | (c) all active | `rolling_median_8+cal` | 1,630 | 4.294 | 0.840 | 2.147 | 1.444 | 0.793 | 0.257 | 0.596 |
| WR | (a) matched | `chain_v1+cal` | 2,035 | 3.937 | 0.599 | 1.969 | 1.174 | 0.872 | 0.239 | 0.584 |
| WR | (a) matched | `rolling_median_8+cal` | 2,035 | 4.244 | 0.831 | 2.122 | 1.439 | 0.793 | 0.172 | 0.560 |
| WR | (b) early game | `chain_v1+cal` | 196 | 4.027 | 0.690 | 2.013 | 1.295 | 0.867 | -0.333 | 0.593 |
| WR | (b) early game | `rolling_median_8+cal` | 196 | 4.340 | 0.947 | 2.170 | 1.455 | 0.791 | 0.127 | 0.495 |
| WR | (c) all active | `chain_v1+cal` | 2,638 | 3.811 | 0.582 | 1.905 | 1.154 | 0.873 | 0.264 | 0.592 |
| WR | (c) all active | `rolling_median_8+cal` | 2,638 | 4.139 | 0.824 | 2.070 | 1.425 | 0.793 | 0.184 | 0.562 |
| TE | (a) matched | `chain_v1+cal` | 1,267 | 2.856 | 0.422 | 1.428 | 0.904 | 0.878 | 0.435 | 0.656 |
| TE | (a) matched | `rolling_median_8+cal` | 1,267 | 3.161 | 0.572 | 1.581 | 1.074 | 0.791 | 0.159 | 0.564 |
| TE | (b) early game | `chain_v1+cal` | 122 | 3.249 | 0.465 | 1.624 | 1.095 | 0.836 | n/a | 1.000 |
| TE | (b) early game | `rolling_median_8+cal` | 122 | 3.416 | 0.676 | 1.708 | 1.308 | 0.730 | n/a | 0.833 |
| TE | (c) all active | `chain_v1+cal` | 1,667 | 2.721 | 0.399 | 1.360 | 0.895 | 0.878 | 0.385 | 0.643 |
| TE | (c) all active | `rolling_median_8+cal` | 1,667 | 3.031 | 0.560 | 1.516 | 1.073 | 0.790 | 0.155 | 0.560 |
| K | (a) matched | `chain_v1+cal` | 363 | 3.719 | 0.706 | 1.860 | 1.014 | 0.832 | 0.108 | n/a |
| K | (a) matched | `rolling_median_8+cal` | 363 | 3.871 | 0.822 | 1.935 | 1.010 | 0.807 | 0.035 | n/a |
| K | (b) early game | `chain_v1+cal` | 37 | 4.584 | 0.764 | 2.292 | 1.180 | 0.757 | n/a | n/a |
| K | (b) early game | `rolling_median_8+cal` | 37 | 4.730 | 1.092 | 2.365 | 1.497 | 0.730 | n/a | n/a |
| K | (c) all active | `chain_v1+cal` | 535 | 3.864 | 0.714 | 1.932 | 1.058 | 0.821 | 0.135 | n/a |
| K | (c) all active | `rolling_median_8+cal` | 535 | 4.045 | 0.847 | 2.023 | 1.056 | 0.802 | -0.004 | n/a |
| DST | (a) matched | `chain_v1+cal` | 438 | 4.328 | 0.828 | 2.164 | 1.178 | 0.785 | 0.304 | n/a |
| DST | (a) matched | `rolling_median_8+cal` | 438 | 4.829 | 0.976 | 2.415 | 1.499 | 0.783 | -0.025 | n/a |
| DST | (b) early game | `chain_v1+cal` | 42 | 3.991 | 0.571 | 1.996 | 1.079 | 0.857 | n/a | n/a |
| DST | (b) early game | `rolling_median_8+cal` | 42 | 4.501 | 0.760 | 2.250 | 1.221 | 0.786 | n/a | n/a |
| DST | (c) all active | `chain_v1+cal` | 544 | 4.254 | 0.804 | 2.127 | 1.153 | 0.800 | 0.280 | n/a |
| DST | (c) all active | `rolling_median_8+cal` | 544 | 4.795 | 0.952 | 2.398 | 1.460 | 0.783 | 0.006 | n/a |

MAE of `chain_v1+cal` minus MAE of `rolling_median_8+cal` [95% paired week-clustered interval]; the last column is the early-game difference minus the matched difference (negative: the focus model's advantage is larger on early-game player-weeks).

| position | (a) matched | (b) early game | (c) all active | (b) - (a) |
| --- | --- | --- | --- | --- |
| QB | -0.889 [-1.244, -0.550] | -0.483 [-2.021, +0.691] | -0.853 [-1.098, -0.651] | +0.406 [-1.298, +1.743] |
| RB | -0.235 [-0.361, -0.127] | -0.336 [-0.860, +0.102] | -0.268 [-0.355, -0.195] | -0.100 [-0.670, +0.417] |
| WR | -0.306 [-0.408, -0.193] | -0.313 [-0.717, +0.036] | -0.328 [-0.475, -0.209] | -0.007 [-0.422, +0.352] |
| TE | -0.306 [-0.433, -0.203] | -0.168 [-0.393, +0.158] | -0.311 [-0.410, -0.222] | +0.138 [-0.101, +0.492] |
| K | -0.152 [-0.318, +0.015] | -0.146 [-0.712, +0.344] | -0.181 [-0.339, -0.005] | +0.006 [-0.564, +0.484] |
| DST | -0.501 [-0.694, -0.313] | -0.510 [-1.065, +0.010] | -0.541 [-0.706, -0.367] | -0.008 [-0.590, +0.528] |
| ALL | -0.348 [-0.419, -0.291] | -0.303 [-0.524, -0.126] | -0.362 [-0.431, -0.302] | +0.045 [-0.192, +0.228] |

The chain's MAE (all positions pooled, calibrated) is 4.12 on early-game player-weeks and 3.99 on the matched population; its MAE minus that of `rolling_median_8+cal` is -0.30 (95% interval -0.52 to -0.13) on early-game player-weeks and -0.35 (-0.42 to -0.29) on the matched population. The difference between the two is +0.04 (95% interval -0.19 to +0.23): it does neither clearly better nor worse on early-game player-weeks than on the matched population (the interval includes zero).

## Forward check (D-094)

### Integrity checks (before any metric was read)

| check | result | detail |
| --- | --- | --- |
| approval lines | PASS | the pre-registration pins the 22 lines of this code |
| bootstrap and seeds | PASS | pre-registered {'n_boot': 500, 'seed': 0, 'metric_seed': 0}, the pinned spec uses {'n_boot': 500, 'seed': 0, 'metric_seed': 0} |
| commit | PASS | pre-registered X 3ecf61069020d6fcba1ddeca0fdb742afd09d794; run sessions: 294aac0: Y 294aac054185 descends from X 3ecf61069020; diff ['docs/status/holdout-preregistration.md'] |
| report generation commit | PASS | generator HEAD 294aac054185: Y 294aac054185 descends from X 3ecf61069020; diff ['docs/status/holdout-preregistration.md']; working tree clean |
| seasons and weeks | PASS | run reports seasons [2026], expected [2026]; weeks [1, 2, 3, 4, 5, 6, 7], expected [1, 2, 3, 4, 5, 6, 7] |
| FROZEN.json | PASS | artifacts/calibration/FROZEN.json: sha256 901fafbd8e63f3c747d62bd072e2c978c6a075a9e7004a5c3a9ce44e5f9f1bbf |
| calibrator rolling_avg_4 | PASS | artifacts/calibration/rolling_avg_4/calibration.json: sha256 c36a02601b1485419cca6600072241d6ea9dc39d33fa720ec043091be2cb91f7 |
| calibrator rolling_avg_3 | PASS | artifacts/calibration/rolling_avg_3/calibration.json: sha256 effde8fc29285e3f2686c973d2305d4918a1983d1a63e1d28a692ba0236f89af |
| calibrator rolling_ew_035 | PASS | artifacts/calibration/rolling_ew_035/calibration.json: sha256 38bad46211d3902d0a39e491adf790c7d9e0d2a1807cc8cacbf1b1bab8813059 |
| calibrator prior_season | PASS | artifacts/calibration/prior_season/calibration.json: sha256 384bb4a872e210f988d42c9ffb68c558712133e9aa2966551a47aab7338b9f41 |
| calibrator rolling_median_8 | PASS | artifacts/calibration/rolling_median_8/calibration.json: sha256 09ffdbd0190f21572371ca0da0e61310e1a74f8a2410c37f0b81e23c46f3c4ba |
| calibrator direct_lgbm_ppr | PASS | artifacts/calibration/direct_lgbm_ppr/calibration.json: sha256 7f98423eabf021d47efabb0e35554b842c7e5d6b33a7713e5120b452b8d1a013 |
| calibrator direct_lgbm_half | PASS | artifacts/calibration/direct_lgbm_half/calibration.json: sha256 5416d7e6da072ac3a3ece0fc9df3f9e584ee3b4c8ea8e4a473b44b02a586317e |
| calibrator direct_lgbm_std | PASS | artifacts/calibration/direct_lgbm_std/calibration.json: sha256 fbb6bccf3e1e7c69a6bf620aaaad47b4c76460f91798be3ece7ae3c6b65b3860 |
| calibrator chain_v1 | PASS | artifacts/models/chain/chain_v1-primary-c2024w19-20261102T222634Z/calibration.json: sha256 3c77725c4e6cf21cd2fc26acaeeea2b3aff22991087614bf35dcd9da280bcd39 |
| calibrator chain_v1_final_report | PASS | artifacts/calibration/chain_v1_final_report/calibration.json: sha256 914fa8d8cd8145df06fb19bdc812c5766bc9e538c62773d5475304f9fc94a45e |
| calibration mode | PASS | run calibration mode 'frozen', pre-registered 'frozen' |
| calibrators used by the run | PASS | 11 recorded, all pre-registered with matching hashes |
| snapshot ids | PASS | run input snapshots equal the pre-registration |
| holdout access log after the forward reads | PASS | one holdout_eval session, 85 lines, 2026-11-04T17:07:08.747272+00:00 to 2026-11-04T18:37:59.088626+00:00; 294aac0: Y 294aac054185 descends from X 3ecf61069020; diff ['docs/status/holdout-preregistration.md'] |

### The 22 pre-registered lines (62,080 PPR rows read)

| line | statement | result | detail |
| --- | --- | --- | --- |
| L01 | `chain_v1+cal` beats `rolling_avg_4+cal` at QB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L02 | `chain_v1+cal` beats `rolling_avg_4+cal` at RB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L03 | `chain_v1+cal` beats `rolling_avg_4+cal` at WR on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L04 | `chain_v1+cal` beats `rolling_avg_4+cal` at TE on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L05 | `chain_v1+cal` beats `prior_season+cal` at QB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L06 | `chain_v1+cal` beats `prior_season+cal` at RB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L07 | `chain_v1+cal` beats `prior_season+cal` at WR on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L08 | `chain_v1+cal` beats `prior_season+cal` at TE on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L09 | `chain_v1+cal` beats `rolling_median_8+cal` at QB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L10 | `chain_v1+cal` beats `rolling_median_8+cal` at RB on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L11 | `chain_v1+cal` beats `rolling_median_8+cal` at WR on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L12 | `chain_v1+cal` beats `rolling_median_8+cal` at TE on MAE and pinball q10, q50 and q90 (every 95% interval entirely below 0) | PASS | all four intervals entirely below 0 |
| L13 | `chain_v1+cal` start/sit at QB is not worse than the FantasyPros consensus (common pair set) | PASS | not worse (difference +0.061, 95% interval +0.029 to +0.089) |
| L14 | `chain_v1+cal` start/sit at RB is not worse than the FantasyPros consensus (common pair set) | PASS | not worse (difference +0.001, 95% interval -0.011 to +0.013) |
| L15 | `chain_v1+cal` start/sit at WR is not worse than the FantasyPros consensus (common pair set) | PASS | not worse (difference +0.015, 95% interval -0.009 to +0.043) |
| L16 | `chain_v1+cal` start/sit at TE is not worse than the FantasyPros consensus (common pair set) | PASS | not worse (difference +0.022, 95% interval -0.014 to +0.057) |
| L17 | `chain_v1+cal` tie-aware coverage at QB within [0.75, 0.85] | PASS | tie-aware coverage 0.774 |
| L18 | `chain_v1+cal` tie-aware coverage at RB within [0.75, 0.85] | PASS | tie-aware coverage 0.782 |
| L19 | `chain_v1+cal` tie-aware coverage at WR within [0.75, 0.85] | PASS | tie-aware coverage 0.783 |
| L20 | `chain_v1+cal` tie-aware coverage at TE within [0.75, 0.85] | PASS | tie-aware coverage 0.807 |
| L21 | `chain_v1+cal` tie-aware coverage at K within [0.75, 0.85] | PASS | tie-aware coverage 0.776 |
| L22 | `chain_v1+cal` tie-aware coverage at DST within [0.75, 0.85] | PASS | tie-aware coverage 0.827 |

Failing lines in plain words:

- none: all 22 lines pass.

### Information outside the 22 lines (not part of the verdict)


#### Verdict grid, calibrated (focus minus comparator, D-078)

| position | metric | vs rolling_avg_4 | vs prior_season | vs rolling_median_8 | vs consensus_ecr | vs direct_lgbm |
| --- | --- | --- | --- | --- | --- | --- |
| QB | MAE | beats (-0.679 [-1.105, -0.343] B) | beats (-0.568 [-0.892, -0.250] B) | beats (-0.417 [-0.763, -0.073] B) | no difference (+0.157 [-0.289, +0.604]) | no difference (+0.013 [-0.253, +0.298]) |
| QB | pinball q10 | beats (-0.330 [-0.452, -0.216] B) | beats (-0.374 [-0.564, -0.179] B) | beats (-0.355 [-0.483, -0.218] B) | no difference (+0.065 [-0.013, +0.149]) | beats (-0.021 [-0.046, -0.004] B) |
| QB | pinball q50 | beats (-0.340 [-0.553, -0.172] B) | beats (-0.284 [-0.446, -0.125] B) | beats (-0.208 [-0.382, -0.037] B) | no difference (+0.078 [-0.145, +0.302]) | no difference (+0.007 [-0.127, +0.149]) |
| QB | pinball q90 | beats (-0.391 [-0.560, -0.214] B) | beats (-0.178 [-0.305, -0.061] B) | beats (-0.236 [-0.348, -0.122] B) | no difference (+0.052 [-0.079, +0.188]) | no difference (-0.002 [-0.053, +0.056]) |
| QB | tie-aware coverage gap | no difference (-0.001 [-0.047, +0.077]) | no difference (-0.009 [-0.063, +0.073]) | no difference (-0.017 [-0.083, +0.073]) | no difference (+0.018 [-0.036, +0.065]) | no difference (+0.012 [-0.016, +0.041]) |
| QB | Spearman | beats (+0.385 [+0.159, +0.605] B) | no difference (+0.210 [-0.001, +0.404]) | beats (+0.305 [+0.046, +0.573] B) | beats (+0.152 [+0.084, +0.214] B) | no difference (+0.125 [-0.006, +0.240]) |
| QB | start/sit | beats (+0.156 [+0.062, +0.248] B) | no difference (+0.076 [-0.011, +0.147]) | beats (+0.116 [+0.015, +0.224] B) | beats (+0.061 [+0.029, +0.089] B) | beats (+0.048 [+0.011, +0.086] B) |
| RB | MAE | beats (-0.550 [-0.682, -0.417] B) | beats (-0.567 [-0.678, -0.431] B) | beats (-0.361 [-0.503, -0.202] B) | no difference (+0.107 [-0.074, +0.274]) | no difference (-0.019 [-0.088, +0.060]) |
| RB | pinball q10 | beats (-0.240 [-0.310, -0.185] B) | beats (-0.192 [-0.280, -0.109] B) | beats (-0.215 [-0.279, -0.163] B) | no difference (+0.014 [-0.018, +0.046]) | beats (-0.021 [-0.035, -0.008] B) |
| RB | pinball q50 | beats (-0.275 [-0.341, -0.209] B) | beats (-0.283 [-0.339, -0.216] B) | beats (-0.180 [-0.251, -0.101] B) | no difference (+0.054 [-0.037, +0.137]) | no difference (-0.009 [-0.044, +0.030]) |
| RB | pinball q90 | beats (-0.331 [-0.417, -0.250] B) | beats (-0.371 [-0.515, -0.229] B) | beats (-0.367 [-0.490, -0.247] B) | no difference (-0.021 [-0.097, +0.051]) | no difference (-0.037 [-0.105, +0.027]) |
| RB | tie-aware coverage gap | no difference (+0.016 [-0.013, +0.031]) | no difference (-0.012 [-0.035, +0.013]) | no difference (+0.013 [-0.019, +0.040]) | beats (-0.040 [-0.050, -0.030] B) | no difference (+0.016 [-0.024, +0.027]) |
| RB | Spearman | no difference (+0.168 [-0.007, +0.376]) | beats (+0.226 [+0.004, +0.367] B) | beats (+0.201 [+0.028, +0.387] B) | no difference (+0.019 [-0.032, +0.077]) | no difference (-0.001 [-0.074, +0.062]) |
| RB | start/sit | no difference (+0.068 [-0.003, +0.137]) | beats (+0.083 [+0.016, +0.128] B) | beats (+0.082 [+0.013, +0.152] B) | no difference (+0.001 [-0.011, +0.013]) | no difference (+0.003 [-0.023, +0.022]) |
| WR | MAE | beats (-0.514 [-0.629, -0.411] B) | beats (-0.346 [-0.424, -0.257] B) | beats (-0.358 [-0.448, -0.270] B) | no difference (-0.034 [-0.197, +0.110]) | beats (-0.086 [-0.144, -0.028] B) |
| WR | pinball q10 | beats (-0.292 [-0.335, -0.262] B) | beats (-0.192 [-0.235, -0.152] B) | beats (-0.239 [-0.269, -0.211] B) | no difference (-0.001 [-0.011, +0.007]) | no difference (-0.009 [-0.018, +0.001]) |
| WR | pinball q50 | beats (-0.257 [-0.315, -0.206] B) | beats (-0.173 [-0.212, -0.128] B) | beats (-0.179 [-0.224, -0.135] B) | no difference (-0.017 [-0.098, +0.055]) | beats (-0.043 [-0.072, -0.014] B) |
| WR | pinball q90 | beats (-0.275 [-0.357, -0.189] B) | beats (-0.254 [-0.310, -0.189] B) | beats (-0.247 [-0.331, -0.173] B) | no difference (-0.008 [-0.061, +0.038]) | no difference (-0.029 [-0.078, +0.010]) |
| WR | tie-aware coverage gap | no difference (+0.012 [-0.011, +0.029]) | no difference (+0.003 [-0.021, +0.030]) | no difference (+0.014 [-0.022, +0.029]) | beats (-0.047 [-0.069, -0.021] B) | no difference (+0.010 [-0.016, +0.035]) |
| WR | Spearman | beats (+0.144 [+0.107, +0.175] B) | no difference (+0.036 [-0.121, +0.181]) | no difference (+0.054 [-0.081, +0.201]) | no difference (+0.034 [-0.032, +0.111]) | no difference (+0.046 [-0.007, +0.105]) |
| WR | start/sit | beats (+0.049 [+0.037, +0.062] B) | no difference (+0.015 [-0.043, +0.068]) | no difference (+0.024 [-0.025, +0.076]) | no difference (+0.015 [-0.009, +0.043]) | no difference (+0.017 [-0.003, +0.037]) |
| TE | MAE | beats (-0.514 [-0.664, -0.322] B) | beats (-0.258 [-0.383, -0.129] B) | beats (-0.242 [-0.335, -0.152] B) | no difference (+0.029 [-0.048, +0.108]) | no difference (-0.043 [-0.129, +0.039]) |
| TE | pinball q10 | beats (-0.242 [-0.282, -0.198] B) | beats (-0.229 [-0.343, -0.139] B) | beats (-0.208 [-0.254, -0.169] B) | no difference (-0.007 [-0.018, +0.006]) | beats (-0.014 [-0.019, -0.008] B) |
| TE | pinball q50 | beats (-0.257 [-0.332, -0.161] B) | beats (-0.129 [-0.192, -0.065] B) | beats (-0.121 [-0.167, -0.076] B) | no difference (+0.014 [-0.024, +0.054]) | no difference (-0.021 [-0.065, +0.019]) |
| TE | pinball q90 | beats (-0.229 [-0.319, -0.131] B) | beats (-0.218 [-0.285, -0.150] B) | beats (-0.212 [-0.289, -0.125] B) | no difference (+0.026 [-0.035, +0.086]) | beats (-0.030 [-0.045, -0.015] B) |
| TE | tie-aware coverage gap | no difference (-0.004 [-0.025, +0.029]) | no difference (-0.062 [-0.111, +0.002]) | no difference (+0.001 [-0.019, +0.019]) | no difference (-0.056 [-0.083, +0.012]) | no difference (-0.027 [-0.050, +0.017]) |
| TE | Spearman | beats (+0.236 [+0.010, +0.481] B) | no difference (-0.150 [-0.360, +0.072]) | no difference (+0.128 [-0.100, +0.337]) | no difference (+0.020 [-0.063, +0.108]) | no difference (+0.004 [-0.130, +0.165]) |
| TE | start/sit | beats (+0.086 [+0.009, +0.167] B) | no difference (-0.056 [-0.131, +0.022]) | no difference (+0.071 [-0.003, +0.139]) | no difference (+0.022 [-0.014, +0.057]) | no difference (+0.010 [-0.036, +0.068]) |
| K | MAE | beats (-0.437 [-0.668, -0.136] B) | no difference (-0.043 [-0.201, +0.152]) | no difference (-0.121 [-0.338, +0.084]) | no difference (+0.021 [-0.156, +0.201]) | no difference (+0.035 [-0.064, +0.156]) |
| K | pinball q10 | beats (-0.251 [-0.303, -0.208] B) | no difference (-0.062 [-0.150, +0.002]) | beats (-0.160 [-0.274, -0.080] B) | no difference (+0.008 [-0.027, +0.044]) | no difference (+0.012 [-0.010, +0.033]) |
| K | pinball q50 | beats (-0.218 [-0.334, -0.068] B) | no difference (-0.021 [-0.101, +0.076]) | no difference (-0.061 [-0.169, +0.042]) | no difference (+0.010 [-0.078, +0.100]) | no difference (+0.017 [-0.032, +0.078]) |
| K | pinball q90 | no difference (-0.065 [-0.187, +0.082]) | no difference (-0.013 [-0.196, +0.193]) | no difference (+0.033 [-0.096, +0.186]) | no difference (+0.055 [-0.090, +0.247]) | no difference (+0.056 [-0.015, +0.175]) |
| K | tie-aware coverage gap | no difference (+0.009 [-0.034, +0.060]) | no difference (+0.005 [-0.034, +0.056]) | no difference (+0.019 [-0.030, +0.048]) | no difference (-0.027 [-0.041, +0.033]) | no difference (+0.019 [-0.018, +0.040]) |
| K | Spearman | no difference (-0.028 [-0.273, +0.233]) | no difference (-0.293 [-0.567, +0.076]) | no difference (-0.293 [-0.606, +0.043]) | no difference (-0.158 [-0.423, +0.051]) | no difference (-0.311 [-0.608, +0.004]) |
| DST | MAE | beats (-0.560 [-1.091, -0.086] B) | no difference (-0.144 [-0.376, +0.108]) | beats (-0.349 [-0.761, -0.000] B) | no difference (-0.041 [-0.161, +0.114]) | no difference (-0.005 [-0.058, +0.047]) |
| DST | pinball q10 | beats (-0.190 [-0.272, -0.097] B) | beats (-0.155 [-0.221, -0.097] B) | beats (-0.142 [-0.210, -0.059] B) | no difference (-0.031 [-0.056, +0.006]) | no difference (+0.001 [-0.016, +0.022]) |
| DST | pinball q50 | beats (-0.280 [-0.546, -0.043] B) | no difference (-0.072 [-0.188, +0.054]) | beats (-0.174 [-0.380, -0.000] B) | no difference (-0.021 [-0.081, +0.057]) | no difference (-0.003 [-0.029, +0.024]) |
| DST | pinball q90 | beats (-0.242 [-0.295, -0.191] B) | beats (-0.185 [-0.341, -0.050] B) | beats (-0.210 [-0.312, -0.124] B) | no difference (-0.018 [-0.067, +0.029]) | no difference (+0.006 [-0.040, +0.053]) |
| DST | tie-aware coverage gap | no difference (+0.009 [-0.038, +0.058]) | no difference (+0.023 [-0.028, +0.069]) | no difference (+0.023 [-0.014, +0.082]) | no difference (-0.009 [-0.070, +0.056]) | no difference (+0.014 [-0.023, +0.024]) |
| DST | Spearman | beats (+0.371 [+0.031, +0.747] B) | no difference (+0.129 [-0.169, +0.454]) | no difference (+0.176 [-0.160, +0.510]) | no difference (-0.024 [-0.121, +0.074]) | no difference (+0.043 [-0.044, +0.125]) |

#### Verdict grid, raw (focus minus comparator, D-078)

| position | metric | vs rolling_avg_4 | vs prior_season | vs rolling_median_8 | vs consensus_ecr | vs direct_lgbm |
| --- | --- | --- | --- | --- | --- | --- |
| QB | MAE | beats (-0.652 [-1.112, -0.273] B) | beats (-0.560 [-0.942, -0.163] B) | beats (-0.392 [-0.754, -0.027] B) | no difference (+0.175 [-0.303, +0.645]) | no difference (+0.029 [-0.292, +0.361]) |
| QB | pinball q10 | beats (-0.410 [-0.552, -0.259] B) | beats (-0.572 [-0.843, -0.292] B) | beats (-0.451 [-0.662, -0.238] B) | no difference (-0.007 [-0.064, +0.058]) | beats (-0.079 [-0.118, -0.047] B) |
| QB | pinball q50 | beats (-0.326 [-0.556, -0.136] B) | beats (-0.280 [-0.471, -0.082] B) | beats (-0.196 [-0.377, -0.013] B) | no difference (+0.087 [-0.152, +0.322]) | no difference (+0.015 [-0.146, +0.181]) |
| QB | pinball q90 | beats (-0.479 [-0.694, -0.264] B) | beats (-0.202 [-0.354, -0.070] B) | beats (-0.245 [-0.358, -0.126] B) | no difference (+0.107 [-0.042, +0.273]) | no difference (+0.045 [-0.036, +0.135]) |
| QB | tie-aware coverage gap | no difference (-0.036 [-0.078, +0.000]) | beats (-0.052 [-0.103, -0.008] B) | no difference (-0.012 [-0.052, +0.016]) | no difference (+0.088 [-0.020, +0.131]) | worse (+0.089 [+0.007, +0.120] W) |
| QB | Spearman | beats (+0.346 [+0.089, +0.586] B) | no difference (+0.142 [-0.077, +0.348]) | beats (+0.273 [+0.013, +0.545] B) | beats (+0.153 [+0.077, +0.218] B) | no difference (+0.118 [-0.013, +0.229]) |
| QB | start/sit | beats (+0.136 [+0.027, +0.246] B) | no difference (+0.055 [-0.043, +0.134]) | no difference (+0.103 [-0.001, +0.214]) | beats (+0.058 [+0.029, +0.084] B) | beats (+0.045 [+0.010, +0.083] B) |
| RB | MAE | beats (-0.523 [-0.755, -0.256] B) | beats (-0.600 [-0.903, -0.189] B) | beats (-0.361 [-0.522, -0.180] B) | no difference (+0.106 [-0.066, +0.263]) | no difference (-0.010 [-0.104, +0.130]) |
| RB | pinball q10 | beats (-0.326 [-0.400, -0.252] B) | beats (-0.315 [-0.455, -0.167] B) | beats (-0.281 [-0.358, -0.198] B) | no difference (+0.016 [-0.017, +0.050]) | beats (-0.020 [-0.031, -0.009] B) |
| RB | pinball q50 | beats (-0.262 [-0.377, -0.128] B) | beats (-0.300 [-0.452, -0.094] B) | beats (-0.180 [-0.261, -0.090] B) | no difference (+0.053 [-0.033, +0.131]) | no difference (-0.005 [-0.052, +0.065]) |
| RB | pinball q90 | beats (-0.370 [-0.486, -0.260] B) | beats (-0.390 [-0.554, -0.249] B) | beats (-0.409 [-0.575, -0.260] B) | no difference (-0.010 [-0.098, +0.078]) | no difference (-0.026 [-0.095, +0.037]) |
| RB | tie-aware coverage gap | beats (-0.116 [-0.143, -0.089] B) | beats (-0.131 [-0.171, -0.083] B) | beats (-0.103 [-0.148, -0.058] B) | no difference (-0.035 [-0.074, +0.007]) | no difference (-0.003 [-0.019, +0.006]) |
| RB | Spearman | no difference (+0.127 [-0.052, +0.320]) | no difference (+0.140 [-0.083, +0.282]) | beats (+0.201 [+0.039, +0.379] B) | no difference (+0.014 [-0.036, +0.069]) | no difference (-0.000 [-0.073, +0.065]) |
| RB | start/sit | no difference (+0.058 [-0.014, +0.125]) | no difference (+0.052 [-0.014, +0.094]) | beats (+0.081 [+0.018, +0.146] B) | no difference (+0.000 [-0.013, +0.013]) | no difference (+0.006 [-0.021, +0.027]) |
| WR | MAE | beats (-0.567 [-0.649, -0.484] B) | beats (-0.577 [-0.671, -0.454] B) | beats (-0.353 [-0.421, -0.288] B) | no difference (-0.023 [-0.172, +0.132]) | no difference (-0.070 [-0.142, +0.002]) |
| WR | pinball q10 | beats (-0.392 [-0.441, -0.353] B) | beats (-0.251 [-0.311, -0.195] B) | beats (-0.316 [-0.357, -0.274] B) | no difference (-0.005 [-0.016, +0.004]) | beats (-0.012 [-0.023, -0.000] B) |
| WR | pinball q50 | beats (-0.283 [-0.324, -0.242] B) | beats (-0.289 [-0.335, -0.227] B) | beats (-0.177 [-0.211, -0.144] B) | no difference (-0.011 [-0.086, +0.066]) | no difference (-0.035 [-0.071, +0.001]) |
| WR | pinball q90 | beats (-0.302 [-0.378, -0.222] B) | beats (-0.301 [-0.345, -0.252] B) | beats (-0.313 [-0.385, -0.244] B) | no difference (-0.008 [-0.061, +0.038]) | no difference (-0.030 [-0.079, +0.009]) |
| WR | tie-aware coverage gap | beats (-0.100 [-0.128, -0.071] B) | beats (-0.123 [-0.160, -0.082] B) | beats (-0.102 [-0.134, -0.072] B) | beats (-0.047 [-0.069, -0.021] B) | no difference (+0.016 [-0.009, +0.032]) |
| WR | Spearman | beats (+0.108 [+0.052, +0.163] B) | no difference (+0.023 [-0.122, +0.155]) | no difference (+0.050 [-0.090, +0.201]) | no difference (+0.030 [-0.038, +0.108]) | no difference (+0.039 [-0.019, +0.098]) |
| WR | start/sit | beats (+0.037 [+0.015, +0.060] B) | no difference (+0.011 [-0.042, +0.057]) | no difference (+0.023 [-0.028, +0.075]) | no difference (+0.013 [-0.011, +0.043]) | no difference (+0.014 [-0.008, +0.034]) |
| TE | MAE | beats (-0.555 [-0.728, -0.355] B) | beats (-0.361 [-0.506, -0.199] B) | beats (-0.240 [-0.330, -0.140] B) | no difference (+0.028 [-0.063, +0.120]) | no difference (-0.037 [-0.133, +0.051]) |
| TE | pinball q10 | beats (-0.349 [-0.419, -0.278] B) | beats (-0.280 [-0.391, -0.181] B) | beats (-0.270 [-0.332, -0.207] B) | no difference (-0.007 [-0.018, +0.006]) | beats (-0.013 [-0.019, -0.008] B) |
| TE | pinball q50 | beats (-0.277 [-0.364, -0.178] B) | beats (-0.181 [-0.253, -0.100] B) | beats (-0.120 [-0.165, -0.070] B) | no difference (+0.014 [-0.032, +0.060]) | no difference (-0.019 [-0.066, +0.026]) |
| TE | pinball q90 | beats (-0.210 [-0.276, -0.147] B) | beats (-0.173 [-0.245, -0.113] B) | beats (-0.189 [-0.266, -0.108] B) | no difference (+0.025 [-0.036, +0.083]) | beats (-0.029 [-0.045, -0.015] B) |
| TE | tie-aware coverage gap | beats (-0.114 [-0.131, -0.075] B) | beats (-0.152 [-0.190, -0.091] B) | beats (-0.082 [-0.107, -0.034] B) | no difference (-0.057 [-0.084, +0.006]) | no difference (-0.017 [-0.037, +0.025]) |
| TE | Spearman | no difference (+0.195 [-0.009, +0.407]) | worse (-0.199 [-0.399, -0.015] W) | no difference (+0.117 [-0.117, +0.328]) | no difference (+0.017 [-0.070, +0.103]) | no difference (-0.024 [-0.182, +0.150]) |
| TE | start/sit | beats (+0.077 [+0.013, +0.144] B) | worse (-0.076 [-0.131, -0.016] W) | no difference (+0.068 [-0.004, +0.135]) | no difference (+0.022 [-0.015, +0.057]) | no difference (+0.003 [-0.047, +0.057]) |
| K | MAE | beats (-0.475 [-0.666, -0.230] B) | no difference (-0.109 [-0.262, +0.060]) | no difference (-0.173 [-0.361, +0.029]) | no difference (+0.012 [-0.111, +0.141]) | no difference (-0.004 [-0.095, +0.084]) |
| K | pinball q10 | beats (-0.353 [-0.416, -0.306] B) | beats (-0.161 [-0.277, -0.078] B) | beats (-0.221 [-0.400, -0.100] B) | no difference (+0.018 [-0.022, +0.056]) | worse (+0.033 [+0.022, +0.043] W) |
| K | pinball q50 | beats (-0.238 [-0.333, -0.115] B) | no difference (-0.054 [-0.131, +0.030]) | no difference (-0.086 [-0.180, +0.014]) | no difference (+0.006 [-0.056, +0.071]) | no difference (-0.002 [-0.047, +0.042]) |
| K | pinball q90 | no difference (-0.131 [-0.308, +0.050]) | no difference (-0.073 [-0.215, +0.097]) | no difference (-0.027 [-0.144, +0.118]) | no difference (+0.055 [-0.090, +0.247]) | no difference (+0.040 [-0.039, +0.155]) |
| K | tie-aware coverage gap | beats (-0.136 [-0.175, -0.047] B) | beats (-0.173 [-0.192, -0.096] B) | beats (-0.136 [-0.161, -0.060] B) | no difference (-0.040 [-0.060, +0.040]) | no difference (-0.019 [-0.032, +0.038]) |
| K | Spearman | no difference (+0.100 [-0.107, +0.323]) | no difference (-0.201 [-0.441, +0.108]) | no difference (-0.191 [-0.519, +0.139]) | no difference (-0.127 [-0.348, +0.049]) | no difference (-0.217 [-0.478, +0.060]) |
| DST | MAE | beats (-0.563 [-1.023, -0.131] B) | no difference (-0.184 [-0.341, +0.006]) | no difference (-0.285 [-0.677, +0.078]) | no difference (+0.027 [-0.083, +0.161]) | no difference (+0.060 [-0.035, +0.169]) |
| DST | pinball q10 | beats (-0.317 [-0.449, -0.170] B) | beats (-0.256 [-0.358, -0.173] B) | beats (-0.282 [-0.358, -0.210] B) | no difference (-0.031 [-0.056, +0.006]) | no difference (+0.000 [-0.016, +0.020]) |
| DST | pinball q50 | beats (-0.282 [-0.511, -0.065] B) | no difference (-0.092 [-0.171, +0.003]) | no difference (-0.143 [-0.339, +0.039]) | no difference (+0.014 [-0.042, +0.080]) | no difference (+0.030 [-0.017, +0.084]) |
| DST | pinball q90 | beats (-0.234 [-0.393, -0.095] B) | no difference (-0.182 [-0.455, +0.040]) | beats (-0.222 [-0.395, -0.048] B) | no difference (-0.018 [-0.067, +0.029]) | no difference (-0.004 [-0.055, +0.048]) |
| DST | tie-aware coverage gap | no difference (-0.075 [-0.149, +0.009]) | no difference (-0.061 [-0.146, +0.028]) | no difference (-0.075 [-0.136, +0.005]) | no difference (+0.035 [-0.026, +0.065]) | no difference (+0.053 [-0.061, +0.105]) |
| DST | Spearman | beats (+0.373 [+0.032, +0.740] B) | no difference (+0.102 [-0.179, +0.363]) | no difference (+0.183 [-0.159, +0.501]) | no difference (-0.034 [-0.127, +0.057]) | no difference (+0.032 [-0.031, +0.098]) |

#### Closed coverage and tails

Exclusive shares of active rows relative to [q10, q90] (ties within 1e-9 points): below q10, tied at q10, strictly inside, tied at q90 (and not at q10), above q90. Closed coverage is tied q10 + inside + tied q90 and is reported for reference. The last column is the **acceptance definition (D-085)**: the share of rows whose randomized PIT lies in (0.1, 0.9), with the PIT drawn uniformly in [F(y-), F(y)] from the model's quantile knots and the prior-season position bounds (seed 0), so a point mass is spread over its probability instead of counting as inside.

| position | model | n | below q10 | tied q10 | inside | tied q90 | above q90 | closed coverage | tie-aware (acceptance, D-085) |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QB | `chain_v1` | 248 | 0.113 | 0.016 | 0.685 | 0.000 | 0.185 | 0.702 | 0.694 |
| QB | `chain_v1+cal` | 248 | 0.081 | 0.016 | 0.762 | 0.000 | 0.141 | 0.778 | 0.774 |
| QB | `chain_v1_final_report` | 248 | 0.109 | 0.016 | 0.710 | 0.000 | 0.165 | 0.726 | 0.718 |
| QB | `chain_v1_final_report+cal` | 248 | 0.081 | 0.000 | 0.786 | 0.000 | 0.133 | 0.786 | 0.786 |
| QB | `direct_lgbm` | 248 | 0.093 | 0.000 | 0.782 | 0.000 | 0.125 | 0.782 | 0.782 |
| QB | `direct_lgbm+cal` | 248 | 0.093 | 0.000 | 0.786 | 0.000 | 0.121 | 0.786 | 0.786 |
| QB | `prior_season` | 248 | 0.177 | 0.000 | 0.641 | 0.000 | 0.181 | 0.641 | 0.641 |
| QB | `prior_season+cal` | 248 | 0.101 | 0.000 | 0.835 | 0.000 | 0.065 | 0.835 | 0.835 |
| QB | `rolling_avg_3` | 248 | 0.137 | 0.000 | 0.677 | 0.000 | 0.185 | 0.677 | 0.677 |
| QB | `rolling_avg_3+cal` | 248 | 0.081 | 0.000 | 0.815 | 0.000 | 0.105 | 0.815 | 0.815 |
| QB | `rolling_avg_4` | 248 | 0.137 | 0.000 | 0.657 | 0.000 | 0.206 | 0.657 | 0.657 |
| QB | `rolling_avg_4+cal` | 248 | 0.065 | 0.000 | 0.827 | 0.000 | 0.109 | 0.827 | 0.827 |
| QB | `rolling_ew_035` | 248 | 0.125 | 0.000 | 0.690 | 0.000 | 0.185 | 0.690 | 0.690 |
| QB | `rolling_ew_035+cal` | 248 | 0.085 | 0.000 | 0.831 | 0.000 | 0.085 | 0.831 | 0.831 |
| QB | `rolling_median_8` | 248 | 0.145 | 0.000 | 0.681 | 0.000 | 0.173 | 0.681 | 0.681 |
| QB | `rolling_median_8+cal` | 248 | 0.085 | 0.000 | 0.843 | 0.000 | 0.073 | 0.843 | 0.843 |
| RB | `chain_v1` | 642 | 0.055 | 0.156 | 0.704 | 0.002 | 0.084 | 0.861 | 0.819 |
| RB | `chain_v1+cal` | 642 | 0.084 | 0.132 | 0.692 | 0.000 | 0.092 | 0.824 | 0.782 |
| RB | `chain_v1_final_report` | 642 | 0.061 | 0.153 | 0.713 | 0.000 | 0.073 | 0.866 | 0.827 |
| RB | `chain_v1_final_report+cal` | 642 | 0.083 | 0.131 | 0.685 | 0.000 | 0.101 | 0.816 | 0.780 |
| RB | `direct_lgbm` | 642 | 0.084 | 0.000 | 0.822 | 0.000 | 0.093 | 0.822 | 0.822 |
| RB | `direct_lgbm+cal` | 642 | 0.095 | 0.000 | 0.802 | 0.000 | 0.103 | 0.802 | 0.802 |
| RB | `prior_season` | 642 | 0.157 | 0.039 | 0.623 | 0.000 | 0.181 | 0.662 | 0.650 |
| RB | `prior_season+cal` | 642 | 0.104 | 0.000 | 0.769 | 0.000 | 0.126 | 0.769 | 0.769 |
| RB | `rolling_avg_3` | 642 | 0.139 | 0.003 | 0.688 | 0.008 | 0.162 | 0.699 | 0.696 |
| RB | `rolling_avg_3+cal` | 642 | 0.090 | 0.000 | 0.808 | 0.000 | 0.101 | 0.808 | 0.808 |
| RB | `rolling_avg_4` | 642 | 0.164 | 0.002 | 0.657 | 0.008 | 0.170 | 0.667 | 0.665 |
| RB | `rolling_avg_4+cal` | 642 | 0.100 | 0.000 | 0.798 | 0.000 | 0.103 | 0.798 | 0.798 |
| RB | `rolling_ew_035` | 642 | 0.142 | 0.000 | 0.688 | 0.008 | 0.162 | 0.696 | 0.696 |
| RB | `rolling_ew_035+cal` | 642 | 0.092 | 0.000 | 0.802 | 0.000 | 0.106 | 0.802 | 0.802 |
| RB | `rolling_median_8` | 642 | 0.140 | 0.026 | 0.650 | 0.009 | 0.174 | 0.682 | 0.678 |
| RB | `rolling_median_8+cal` | 642 | 0.087 | 0.000 | 0.805 | 0.000 | 0.107 | 0.805 | 0.805 |
| WR | `chain_v1` | 1,025 | 0.029 | 0.262 | 0.600 | 0.002 | 0.106 | 0.863 | 0.782 |
| WR | `chain_v1+cal` | 1,025 | 0.029 | 0.261 | 0.601 | 0.002 | 0.106 | 0.864 | 0.783 |
| WR | `chain_v1_final_report` | 1,025 | 0.038 | 0.260 | 0.599 | 0.001 | 0.101 | 0.860 | 0.774 |
| WR | `chain_v1_final_report+cal` | 1,025 | 0.033 | 0.259 | 0.592 | 0.000 | 0.116 | 0.851 | 0.767 |
| WR | `direct_lgbm` | 1,025 | 0.075 | 0.010 | 0.798 | 0.000 | 0.117 | 0.806 | 0.798 |
| WR | `direct_lgbm+cal` | 1,025 | 0.075 | 0.004 | 0.804 | 0.000 | 0.117 | 0.808 | 0.807 |
| WR | `prior_season` | 1,025 | 0.140 | 0.031 | 0.643 | 0.009 | 0.178 | 0.683 | 0.660 |
| WR | `prior_season+cal` | 1,025 | 0.099 | 0.000 | 0.786 | 0.000 | 0.115 | 0.786 | 0.786 |
| WR | `rolling_avg_3` | 1,025 | 0.161 | 0.004 | 0.661 | 0.014 | 0.160 | 0.677 | 0.675 |
| WR | `rolling_avg_3+cal` | 1,025 | 0.102 | 0.000 | 0.795 | 0.000 | 0.102 | 0.795 | 0.795 |
| WR | `rolling_avg_4` | 1,025 | 0.154 | 0.003 | 0.667 | 0.015 | 0.161 | 0.684 | 0.682 |
| WR | `rolling_avg_4+cal` | 1,025 | 0.104 | 0.000 | 0.795 | 0.000 | 0.100 | 0.795 | 0.795 |
| WR | `rolling_ew_035` | 1,025 | 0.151 | 0.000 | 0.681 | 0.003 | 0.165 | 0.684 | 0.684 |
| WR | `rolling_ew_035+cal` | 1,025 | 0.105 | 0.000 | 0.789 | 0.000 | 0.105 | 0.789 | 0.789 |
| WR | `rolling_median_8` | 1,025 | 0.132 | 0.049 | 0.633 | 0.006 | 0.180 | 0.688 | 0.680 |
| WR | `rolling_median_8+cal` | 1,025 | 0.099 | 0.000 | 0.797 | 0.000 | 0.104 | 0.797 | 0.797 |
| TE | `chain_v1` | 701 | 0.033 | 0.412 | 0.456 | 0.001 | 0.097 | 0.870 | 0.806 |
| TE | `chain_v1+cal` | 701 | 0.033 | 0.407 | 0.458 | 0.000 | 0.103 | 0.864 | 0.807 |
| TE | `chain_v1_final_report` | 701 | 0.044 | 0.404 | 0.465 | 0.000 | 0.087 | 0.869 | 0.810 |
| TE | `chain_v1_final_report+cal` | 701 | 0.041 | 0.402 | 0.451 | 0.000 | 0.106 | 0.853 | 0.795 |
| TE | `direct_lgbm` | 701 | 0.100 | 0.020 | 0.775 | 0.000 | 0.106 | 0.795 | 0.777 |
| TE | `direct_lgbm+cal` | 701 | 0.116 | 0.013 | 0.763 | 0.000 | 0.108 | 0.766 | 0.766 |
| TE | `prior_season` | 701 | 0.187 | 0.084 | 0.568 | 0.027 | 0.134 | 0.679 | 0.642 |
| TE | `prior_season+cal` | 701 | 0.157 | 0.084 | 0.666 | 0.000 | 0.093 | 0.750 | 0.730 |
| TE | `rolling_avg_3` | 701 | 0.158 | 0.046 | 0.641 | 0.011 | 0.144 | 0.698 | 0.688 |
| TE | `rolling_avg_3+cal` | 701 | 0.121 | 0.000 | 0.773 | 0.000 | 0.106 | 0.773 | 0.773 |
| TE | `rolling_avg_4` | 701 | 0.157 | 0.053 | 0.626 | 0.016 | 0.148 | 0.695 | 0.680 |
| TE | `rolling_avg_4+cal` | 701 | 0.114 | 0.000 | 0.789 | 0.000 | 0.097 | 0.789 | 0.789 |
| TE | `rolling_ew_035` | 701 | 0.153 | 0.023 | 0.675 | 0.009 | 0.141 | 0.706 | 0.706 |
| TE | `rolling_ew_035+cal` | 701 | 0.106 | 0.000 | 0.799 | 0.000 | 0.096 | 0.799 | 0.799 |
| TE | `rolling_median_8` | 701 | 0.137 | 0.110 | 0.602 | 0.011 | 0.140 | 0.723 | 0.712 |
| TE | `rolling_median_8+cal` | 701 | 0.101 | 0.000 | 0.806 | 0.000 | 0.093 | 0.806 | 0.806 |
| K | `chain_v1` | 214 | 0.056 | 0.023 | 0.799 | 0.042 | 0.079 | 0.864 | 0.799 |
| K | `chain_v1+cal` | 214 | 0.103 | 0.000 | 0.776 | 0.042 | 0.079 | 0.818 | 0.776 |
| K | `chain_v1_final_report` | 214 | 0.056 | 0.019 | 0.808 | 0.042 | 0.075 | 0.869 | 0.808 |
| K | `chain_v1_final_report+cal` | 214 | 0.103 | 0.000 | 0.776 | 0.000 | 0.121 | 0.776 | 0.776 |
| K | `direct_lgbm` | 214 | 0.089 | 0.000 | 0.780 | 0.000 | 0.131 | 0.780 | 0.780 |
| K | `direct_lgbm+cal` | 214 | 0.093 | 0.000 | 0.794 | 0.000 | 0.112 | 0.794 | 0.794 |
| K | `prior_season` | 214 | 0.206 | 0.005 | 0.626 | 0.000 | 0.164 | 0.631 | 0.626 |
| K | `prior_season+cal` | 214 | 0.126 | 0.000 | 0.780 | 0.000 | 0.093 | 0.780 | 0.780 |
| K | `rolling_avg_3` | 214 | 0.173 | 0.000 | 0.664 | 0.000 | 0.164 | 0.664 | 0.664 |
| K | `rolling_avg_3+cal` | 214 | 0.107 | 0.000 | 0.780 | 0.000 | 0.112 | 0.780 | 0.780 |
| K | `rolling_avg_4` | 214 | 0.178 | 0.000 | 0.664 | 0.000 | 0.159 | 0.664 | 0.664 |
| K | `rolling_avg_4+cal` | 214 | 0.112 | 0.000 | 0.785 | 0.000 | 0.103 | 0.785 | 0.785 |
| K | `rolling_ew_035` | 214 | 0.178 | 0.000 | 0.654 | 0.000 | 0.168 | 0.654 | 0.654 |
| K | `rolling_ew_035+cal` | 214 | 0.112 | 0.000 | 0.785 | 0.000 | 0.103 | 0.785 | 0.785 |
| K | `rolling_median_8` | 214 | 0.173 | 0.000 | 0.664 | 0.005 | 0.159 | 0.664 | 0.664 |
| K | `rolling_median_8+cal` | 214 | 0.117 | 0.000 | 0.794 | 0.000 | 0.089 | 0.794 | 0.794 |
| DST | `chain_v1` | 214 | 0.103 | 0.079 | 0.738 | 0.009 | 0.070 | 0.827 | 0.738 |
| DST | `chain_v1+cal` | 214 | 0.103 | 0.000 | 0.827 | 0.000 | 0.070 | 0.827 | 0.827 |
| DST | `chain_v1_final_report` | 214 | 0.103 | 0.075 | 0.743 | 0.009 | 0.070 | 0.827 | 0.743 |
| DST | `chain_v1_final_report+cal` | 214 | 0.103 | 0.000 | 0.827 | 0.000 | 0.070 | 0.827 | 0.827 |
| DST | `direct_lgbm` | 214 | 0.126 | 0.000 | 0.808 | 0.000 | 0.065 | 0.808 | 0.808 |
| DST | `direct_lgbm+cal` | 214 | 0.121 | 0.000 | 0.813 | 0.000 | 0.065 | 0.813 | 0.813 |
| DST | `prior_season` | 214 | 0.192 | 0.000 | 0.678 | 0.000 | 0.131 | 0.678 | 0.678 |
| DST | `prior_season+cal` | 214 | 0.107 | 0.000 | 0.804 | 0.000 | 0.089 | 0.804 | 0.804 |
| DST | `rolling_avg_3` | 214 | 0.150 | 0.009 | 0.701 | 0.000 | 0.140 | 0.710 | 0.701 |
| DST | `rolling_avg_3+cal` | 214 | 0.093 | 0.009 | 0.822 | 0.000 | 0.075 | 0.832 | 0.822 |
| DST | `rolling_avg_4` | 214 | 0.178 | 0.005 | 0.664 | 0.000 | 0.154 | 0.668 | 0.664 |
| DST | `rolling_avg_4+cal` | 214 | 0.093 | 0.009 | 0.818 | 0.000 | 0.079 | 0.827 | 0.818 |
| DST | `rolling_ew_035` | 214 | 0.159 | 0.005 | 0.687 | 0.000 | 0.150 | 0.692 | 0.687 |
| DST | `rolling_ew_035+cal` | 214 | 0.112 | 0.005 | 0.785 | 0.000 | 0.098 | 0.790 | 0.785 |
| DST | `rolling_median_8` | 214 | 0.192 | 0.000 | 0.664 | 0.000 | 0.145 | 0.664 | 0.664 |
| DST | `rolling_median_8+cal` | 214 | 0.103 | 0.005 | 0.804 | 0.000 | 0.089 | 0.808 | 0.804 |

Pinball loss at q10 and q90 (the proper scoring rule for each quantile), calibrated forms; difference is `chain_v1+cal - comparator`, negative favours the focus model, B/W by the D-078 rule.

| position | comparator | pinball q10 `chain_v1+cal` | comparator | difference [95% CI] | pinball q90 `chain_v1+cal` | comparator | difference [95% CI] |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QB | `chain_v1_final_report+cal` | 1.187 | 1.208 | -0.021 [-0.060, +0.009] | 1.368 | 1.341 | +0.028 [-0.041, +0.117] |
| QB | `direct_lgbm+cal` | 1.187 | 1.208 | -0.021 [-0.046, -0.004] B | 1.368 | 1.371 | -0.002 [-0.053, +0.056] |
| QB | `rolling_avg_4+cal` | 1.187 | 1.516 | -0.330 [-0.452, -0.216] B | 1.368 | 1.759 | -0.391 [-0.560, -0.214] B |
| QB | `rolling_avg_3+cal` | 1.187 | 1.565 | -0.378 [-0.471, -0.284] B | 1.368 | 1.823 | -0.455 [-0.611, -0.289] B |
| QB | `rolling_ew_035+cal` | 1.187 | 1.547 | -0.361 [-0.488, -0.230] B | 1.368 | 1.689 | -0.321 [-0.476, -0.149] B |
| QB | `prior_season+cal` | 1.187 | 1.560 | -0.374 [-0.564, -0.179] B | 1.368 | 1.546 | -0.178 [-0.305, -0.061] B |
| QB | `rolling_median_8+cal` | 1.187 | 1.542 | -0.355 [-0.483, -0.218] B | 1.368 | 1.605 | -0.236 [-0.348, -0.122] B |
| RB | `chain_v1_final_report+cal` | 0.624 | 0.621 | +0.003 [-0.006, +0.013] | 1.093 | 1.094 | -0.001 [-0.024, +0.026] |
| RB | `direct_lgbm+cal` | 0.624 | 0.645 | -0.021 [-0.035, -0.008] B | 1.093 | 1.130 | -0.037 [-0.105, +0.027] |
| RB | `rolling_avg_4+cal` | 0.624 | 0.865 | -0.240 [-0.310, -0.185] B | 1.093 | 1.424 | -0.331 [-0.417, -0.250] B |
| RB | `rolling_avg_3+cal` | 0.624 | 0.901 | -0.276 [-0.342, -0.220] B | 1.093 | 1.410 | -0.317 [-0.396, -0.249] B |
| RB | `rolling_ew_035+cal` | 0.624 | 0.882 | -0.258 [-0.343, -0.192] B | 1.093 | 1.422 | -0.329 [-0.430, -0.237] B |
| RB | `prior_season+cal` | 0.624 | 0.817 | -0.192 [-0.280, -0.109] B | 1.093 | 1.464 | -0.371 [-0.515, -0.229] B |
| RB | `rolling_median_8+cal` | 0.624 | 0.840 | -0.215 [-0.279, -0.163] B | 1.093 | 1.460 | -0.367 [-0.490, -0.247] B |
| WR | `chain_v1_final_report+cal` | 0.598 | 0.599 | -0.001 [-0.004, +0.003] | 1.195 | 1.192 | +0.003 [-0.013, +0.018] |
| WR | `direct_lgbm+cal` | 0.598 | 0.606 | -0.009 [-0.018, +0.001] | 1.195 | 1.224 | -0.029 [-0.078, +0.010] |
| WR | `rolling_avg_4+cal` | 0.598 | 0.889 | -0.292 [-0.335, -0.262] B | 1.195 | 1.470 | -0.275 [-0.357, -0.189] B |
| WR | `rolling_avg_3+cal` | 0.598 | 0.924 | -0.326 [-0.397, -0.270] B | 1.195 | 1.493 | -0.298 [-0.374, -0.223] B |
| WR | `rolling_ew_035+cal` | 0.598 | 0.886 | -0.288 [-0.356, -0.246] B | 1.195 | 1.464 | -0.269 [-0.353, -0.182] B |
| WR | `prior_season+cal` | 0.598 | 0.790 | -0.192 [-0.235, -0.152] B | 1.195 | 1.448 | -0.254 [-0.310, -0.189] B |
| WR | `rolling_median_8+cal` | 0.598 | 0.837 | -0.239 [-0.269, -0.211] B | 1.195 | 1.442 | -0.247 [-0.331, -0.173] B |
| TE | `chain_v1_final_report+cal` | 0.408 | 0.416 | -0.008 [-0.019, -0.002] B | 0.955 | 0.947 | +0.008 [-0.006, +0.018] |
| TE | `direct_lgbm+cal` | 0.408 | 0.422 | -0.014 [-0.019, -0.008] B | 0.955 | 0.985 | -0.030 [-0.045, -0.015] B |
| TE | `rolling_avg_4+cal` | 0.408 | 0.650 | -0.242 [-0.282, -0.198] B | 0.955 | 1.184 | -0.229 [-0.319, -0.131] B |
| TE | `rolling_avg_3+cal` | 0.408 | 0.681 | -0.273 [-0.302, -0.248] B | 0.955 | 1.183 | -0.228 [-0.301, -0.150] B |
| TE | `rolling_ew_035+cal` | 0.408 | 0.694 | -0.286 [-0.327, -0.243] B | 0.955 | 1.157 | -0.202 [-0.273, -0.118] B |
| TE | `prior_season+cal` | 0.408 | 0.637 | -0.229 [-0.343, -0.139] B | 0.955 | 1.173 | -0.218 [-0.285, -0.150] B |
| TE | `rolling_median_8+cal` | 0.408 | 0.617 | -0.208 [-0.254, -0.169] B | 0.955 | 1.167 | -0.212 [-0.289, -0.125] B |
| K | `chain_v1_final_report+cal` | 0.729 | 0.724 | +0.004 [-0.005, +0.016] | 0.961 | 0.971 | -0.010 [-0.055, +0.043] |
| K | `direct_lgbm+cal` | 0.729 | 0.716 | +0.012 [-0.010, +0.033] | 0.961 | 0.906 | +0.056 [-0.015, +0.175] |
| K | `rolling_avg_4+cal` | 0.729 | 0.980 | -0.251 [-0.303, -0.208] B | 0.961 | 1.027 | -0.065 [-0.187, +0.082] |
| K | `rolling_avg_3+cal` | 0.729 | 0.993 | -0.265 [-0.321, -0.211] B | 0.961 | 1.022 | -0.061 [-0.188, +0.079] |
| K | `rolling_ew_035+cal` | 0.729 | 0.944 | -0.215 [-0.270, -0.158] B | 0.961 | 1.001 | -0.039 [-0.170, +0.118] |
| K | `prior_season+cal` | 0.729 | 0.790 | -0.062 [-0.150, +0.002] | 0.961 | 0.975 | -0.013 [-0.196, +0.193] |
| K | `rolling_median_8+cal` | 0.729 | 0.888 | -0.160 [-0.274, -0.080] B | 0.961 | 0.929 | +0.033 [-0.096, +0.186] |
| DST | `chain_v1_final_report+cal` | 0.696 | 0.689 | +0.006 [-0.003, +0.017] | 1.130 | 1.129 | +0.002 [-0.030, +0.032] |
| DST | `direct_lgbm+cal` | 0.696 | 0.695 | +0.001 [-0.016, +0.022] | 1.130 | 1.124 | +0.006 [-0.040, +0.053] |
| DST | `rolling_avg_4+cal` | 0.696 | 0.886 | -0.190 [-0.272, -0.097] B | 1.130 | 1.372 | -0.242 [-0.295, -0.191] B |
| DST | `rolling_avg_3+cal` | 0.696 | 0.914 | -0.218 [-0.312, -0.087] B | 1.130 | 1.358 | -0.227 [-0.292, -0.170] B |
| DST | `rolling_ew_035+cal` | 0.696 | 0.865 | -0.169 [-0.241, -0.068] B | 1.130 | 1.371 | -0.241 [-0.310, -0.182] B |
| DST | `prior_season+cal` | 0.696 | 0.851 | -0.155 [-0.221, -0.097] B | 1.130 | 1.315 | -0.185 [-0.341, -0.050] B |
| DST | `rolling_median_8+cal` | 0.696 | 0.838 | -0.142 [-0.210, -0.059] B | 1.130 | 1.340 | -0.210 [-0.312, -0.124] B |

#### Boom and bust calibration (frozen calibrator, D-086 rule, D-089 flags)

| position | kind | n | Brier | intercept | slope [95% CI] | D-086 rule |
| --- | --- | --- | --- | --- | --- | --- |
| QB | boom | 248 | 0.133 | -0.360 | 0.712 [0.360, 1.270] | **low confidence** |
| QB | bust | 248 | 0.206 | -0.063 | 0.852 [0.599, 1.155] | within the rule |
| RB | boom | 642 | 0.083 | -0.073 | 0.923 [0.718, 1.280] | within the rule |
| RB | bust | 642 | 0.142 | 0.021 | 0.939 [0.862, 1.058] | within the rule |
| WR | boom | 1,025 | 0.060 | 0.326 | 1.200 [0.981, 1.466] | **low confidence** |
| WR | bust | 1,025 | 0.155 | 0.063 | 0.883 [0.790, 1.023] | within the rule |
| TE | boom | 701 | 0.047 | -0.169 | 0.910 [0.678, 1.336] | within the rule |
| TE | bust | 701 | 0.109 | 0.095 | 0.907 [0.813, 0.995] | within the rule |
| K | boom | 214 | 0.173 | -1.032 | 0.190 [-2.192, 1.925] | **low confidence** (D-089: stays flagged) |
| K | bust | 214 | 0.241 | -0.375 | 0.165 [-0.840, 1.750] | **low confidence** (D-089: stays flagged) |
| DST | boom | 214 | 0.155 | -0.427 | 0.760 [0.251, 1.463] | **low confidence** (D-089: stays flagged) |
| DST | bust | 214 | 0.234 | -0.235 | 0.532 [0.061, 1.139] | **low confidence** (D-089: stays flagged) |

#### Thursday split (D-081)

Active player-weeks by consensus status (PPR, 2022-2024). Columns (a) and (b) are the populations compared below; the other exclusion reasons are counted separately and are not part of (b).

| position | (a) matched consensus population | (b) game on or before the scrape date | no ECR match (not ranked, no id, or another page) | no scrape that week | valid ECR row without a consensus projection | (c) all active | sum check |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QB | 227 | 18 | 3 | 0 | 0 | 248 | ok |
| RB | 547 | 46 | 49 | 0 | 0 | 642 | ok |
| WR | 922 | 74 | 29 | 0 | 0 | 1,025 | ok |
| TE | 623 | 53 | 25 | 0 | 0 | 701 | ok |
| K | 150 | 12 | 52 | 0 | 0 | 214 | ok |
| DST | 198 | 16 | 0 | 0 | 0 | 214 | ok |
| ALL | 2,667 | 219 | 158 | 0 | 0 | 3,044 | ok |

Calibrated forms of `chain_v1` and the best baseline `rolling_median_8` on the three populations. Spearman and start/sit use the headline startable set (D-077) restricted to each population; with only a few early-game players per position-week, they are often undefined there (n/a).

| position | population | model | n | MAE | pinball q10 | pinball q50 | pinball q90 | 80% coverage | Spearman | start/sit |
| --- | --- | --- | --- | --- | --- | --- | --- | --- | --- | --- |
| QB | (a) matched | `chain_v1+cal` | 227 | 5.824 | 1.178 | 2.912 | 1.350 | 0.784 | 0.282 | 0.595 |
| QB | (a) matched | `rolling_median_8+cal` | 227 | 6.249 | 1.535 | 3.124 | 1.606 | 0.850 | -0.021 | 0.485 |
| QB | (b) early game | `chain_v1+cal` | 18 | 7.626 | 1.373 | 3.813 | 1.625 | 0.722 | n/a | 0.250 |
| QB | (b) early game | `rolling_median_8+cal` | 18 | 7.703 | 1.590 | 3.852 | 1.526 | 0.778 | n/a | 0.250 |
| QB | (c) all active | `chain_v1+cal` | 248 | 5.942 | 1.187 | 2.971 | 1.368 | 0.778 | 0.266 | 0.599 |
| QB | (c) all active | `rolling_median_8+cal` | 248 | 6.359 | 1.542 | 3.180 | 1.605 | 0.843 | -0.039 | 0.484 |
| RB | (a) matched | `chain_v1+cal` | 547 | 4.221 | 0.673 | 2.110 | 1.152 | 0.823 | 0.406 | 0.645 |
| RB | (a) matched | `rolling_median_8+cal` | 547 | 4.653 | 0.907 | 2.327 | 1.512 | 0.784 | 0.183 | 0.565 |
| RB | (b) early game | `chain_v1+cal` | 46 | 3.675 | 0.637 | 1.838 | 1.094 | 0.826 | n/a | 0.500 |
| RB | (b) early game | `rolling_median_8+cal` | 46 | 3.314 | 0.708 | 1.657 | 1.538 | 0.891 | n/a | 0.500 |
| RB | (c) all active | `chain_v1+cal` | 642 | 3.915 | 0.624 | 1.958 | 1.093 | 0.824 | 0.392 | 0.647 |
| RB | (c) all active | `rolling_median_8+cal` | 642 | 4.276 | 0.840 | 2.138 | 1.460 | 0.805 | 0.191 | 0.565 |
| WR | (a) matched | `chain_v1+cal` | 922 | 4.042 | 0.600 | 2.021 | 1.232 | 0.866 | 0.345 | 0.622 |
| WR | (a) matched | `rolling_median_8+cal` | 922 | 4.420 | 0.845 | 2.210 | 1.467 | 0.795 | 0.280 | 0.594 |
| WR | (b) early game | `chain_v1+cal` | 74 | 4.629 | 0.701 | 2.315 | 0.988 | 0.851 | n/a | 0.583 |
| WR | (b) early game | `rolling_median_8+cal` | 74 | 4.622 | 0.844 | 2.311 | 1.246 | 0.784 | n/a | 0.417 |
| WR | (c) all active | `chain_v1+cal` | 1,025 | 4.028 | 0.598 | 2.014 | 1.195 | 0.864 | 0.341 | 0.619 |
| WR | (c) all active | `rolling_median_8+cal` | 1,025 | 4.386 | 0.837 | 2.193 | 1.442 | 0.797 | 0.287 | 0.595 |
| TE | (a) matched | `chain_v1+cal` | 623 | 3.049 | 0.427 | 1.525 | 1.002 | 0.859 | 0.257 | 0.608 |
| TE | (a) matched | `rolling_median_8+cal` | 623 | 3.292 | 0.630 | 1.646 | 1.201 | 0.796 | 0.226 | 0.561 |
| TE | (b) early game | `chain_v1+cal` | 53 | 2.531 | 0.332 | 1.266 | 0.631 | 0.906 | n/a | 0.750 |
| TE | (b) early game | `rolling_median_8+cal` | 53 | 2.851 | 0.618 | 1.426 | 0.975 | 0.849 | n/a | 0.250 |
| TE | (c) all active | `chain_v1+cal` | 701 | 2.943 | 0.408 | 1.471 | 0.955 | 0.864 | 0.290 | 0.607 |
| TE | (c) all active | `rolling_median_8+cal` | 701 | 3.185 | 0.617 | 1.593 | 1.167 | 0.806 | 0.162 | 0.537 |
| K | (a) matched | `chain_v1+cal` | 150 | 4.002 | 0.757 | 2.001 | 1.005 | 0.813 | -0.048 | n/a |
| K | (a) matched | `rolling_median_8+cal` | 150 | 3.907 | 0.898 | 1.954 | 0.941 | 0.793 | 0.196 | n/a |
| K | (b) early game | `chain_v1+cal` | 12 | 2.758 | 0.496 | 1.379 | 0.775 | 1.000 | n/a | n/a |
| K | (b) early game | `rolling_median_8+cal` | 12 | 3.500 | 0.656 | 1.750 | 0.837 | 0.833 | n/a | n/a |
| K | (c) all active | `chain_v1+cal` | 214 | 3.822 | 0.729 | 1.911 | 0.961 | 0.818 | -0.047 | n/a |
| K | (c) all active | `rolling_median_8+cal` | 214 | 3.944 | 0.888 | 1.972 | 0.929 | 0.794 | 0.246 | n/a |
| DST | (a) matched | `chain_v1+cal` | 198 | 3.899 | 0.697 | 1.949 | 1.152 | 0.823 | 0.236 | n/a |
| DST | (a) matched | `rolling_median_8+cal` | 198 | 4.298 | 0.857 | 2.149 | 1.365 | 0.798 | 0.112 | n/a |
| DST | (b) early game | `chain_v1+cal` | 16 | 3.645 | 0.682 | 1.822 | 0.856 | 0.875 | n/a | n/a |
| DST | (b) early game | `rolling_median_8+cal` | 16 | 3.363 | 0.602 | 1.681 | 1.026 | 0.938 | n/a | n/a |
| DST | (c) all active | `chain_v1+cal` | 214 | 3.880 | 0.696 | 1.940 | 1.130 | 0.827 | 0.260 | n/a |
| DST | (c) all active | `rolling_median_8+cal` | 214 | 4.228 | 0.838 | 2.114 | 1.340 | 0.808 | 0.085 | n/a |

MAE of `chain_v1+cal` minus MAE of `rolling_median_8+cal` [95% paired week-clustered interval]; the last column is the early-game difference minus the matched difference (negative: the focus model's advantage is larger on early-game player-weeks).

| position | (a) matched | (b) early game | (c) all active | (b) - (a) |
| --- | --- | --- | --- | --- |
| QB | -0.425 [-0.851, -0.009] | -0.077 [-1.431, +1.720] | -0.417 [-0.763, -0.073] | +0.347 [-1.096, +2.258] |
| RB | -0.433 [-0.586, -0.276] | +0.362 [-0.432, +1.529] | -0.361 [-0.503, -0.202] | +0.794 [-0.005, +1.972] |
| WR | -0.378 [-0.508, -0.267] | +0.007 [-0.377, +0.501] | -0.358 [-0.448, -0.270] | +0.385 [-0.045, +0.954] |
| TE | -0.243 [-0.345, -0.149] | -0.320 [-0.831, +0.288] | -0.242 [-0.335, -0.152] | -0.078 [-0.582, +0.539] |
| K | +0.095 [-0.125, +0.304] | -0.742 [-1.757, +0.068] | -0.121 [-0.338, +0.084] | -0.836 [-2.012, +0.136] |
| DST | -0.400 [-0.830, -0.017] | +0.282 [-0.692, +1.393] | -0.349 [-0.761, -0.000] | +0.681 [-0.469, +1.786] |
| ALL | -0.337 [-0.413, -0.257] | -0.026 [-0.398, +0.391] | -0.320 [-0.404, -0.240] | +0.311 [-0.034, +0.697] |

The chain's MAE (all positions pooled, calibrated) is 3.99 on early-game player-weeks and 3.99 on the matched population; its MAE minus that of `rolling_median_8+cal` is -0.03 (95% interval -0.40 to +0.39) on early-game player-weeks and -0.34 (-0.41 to -0.26) on the matched population. The difference between the two is +0.31 (95% interval -0.03 to +0.70): it does neither clearly better nor worse on early-game player-weeks than on the matched population (the interval includes zero).
