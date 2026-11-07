# Model card: ffml football-first projection chain

Last updated 2026-11-07 (OR-6.2). Every number in this card is copied from a
committed report or decision, and the source is named next to it. No metric
was computed for this card.

> **Status: no approved model.** The first candidate, chain_v1, was
> NOT APPROVED on its one-time 2025 holdout test. Its quarterback 80% ranges
> were too narrow: tie-aware coverage was 0.739, outside the required
> [0.75, 0.85]. The other 21 of 22 pre-registered lines passed (D-097). The
> second version, chain_v2, is under a pre-registered live test over 2026
> weeks 10-18 and 2027 weeks 1-9. The single verdict is expected about
> 2027-11-12 (D-110, D-111, D-119). Until then the app shows "no approved
> model". Projections, start/sit, waivers, trades and rest-of-season rankings
> are off, except in private test environments labeled "not approved" (D-099).

## 1. Model details

| Item | Value | Source |
| --- | --- | --- |
| Model family | Football-first Monte Carlo chain. It predicts stat-line distributions, and fantasy points are computed afterwards under each league's scoring. | D-003, docs/MODELING.md |
| chain_v1 | Registry candidate `chain_v1-primary-c2024w19-20261102T222634Z`. Primary configuration, features 0.2.0, 2,000 simulations per week, frozen calibrator `v3-tie-aware-quantile`. Not approved. | reports/backtest_2026-11-03_v3.md section 1; D-097 |
| chain_v2 | chain_v1 with two changes and no others: (a) 2025 is added to training, released as data and not as an approval; (b) the calibrator updates walk-forward each week. Stage choices (LightGBM in 19 of 30 stages), hyperparameters, features 0.2.0, calibrator settings and seeds are unchanged. | D-098; docs/status/livetest-preregistration-2026.md |
| chain_v2 versions | One registry candidate per live-test week, `chain_v2-primary-c{season}w{WW}-<UTC stamp>`, labeled "not approved", in a private store only. | docs/status/livetest-preregistration-2026.md |
| Seeds | Chain seed 20261101; week seed `seed + 100*season + week`; calibrator seed 0; metric seed 0; bootstrap 500 week-clustered paired draws, seed 0. | docs/status/livetest-preregistration-2026.md |
| Positions | Full chain for QB, RB, WR and TE; simplified chains for K and DST; no IDP. | D-010 |
| Not shipped | The direct challenger, a pooled LightGBM quantile model on fantasy points, is used for comparison only. | D-073 |

## 2. Intended use

**Intended use.**
- Personal, non-commercial fantasy football decisions (D-013).
- The chain produces, under any league's scoring:
  - weekly distributions with floor (q10), median and ceiling (q90);
  - boom and bust probabilities;
  - start/sit comparisons;
  - waiver and trade valuations;
  - rest-of-season rankings.
- Scoring is applied to simulated stat lines at request time, so it needs no retraining (D-003).
- Public use waits until a model passes its pre-registered test. Until then, outputs are research and private-test material only, labeled "not approved" (D-099, D-100).

**Out of scope.**
- Do not use these outputs for sports betting, wagering, player props or paid-contest entries. Nothing here is betting advice, and no outcome is promised. A projection is a probability, not a promise.
- Commercial use is out of scope. Sleeper's API terms are free for non-commercial use only (D-013).
- Do not read the ranges the app computes under a league's scoring as calibrated. Request-time calibration is not built. The API serves raw simulation quantiles under league scoring and says so in each response (D-103; Backend proposal P-2 in docs/status/phase4-backend.md).
- Do not treat the QB, K and DST boom/bust probabilities as reliable. They are low confidence (D-089, D-104; section 8).
- Not covered: IDP leagues, ESPN or Yahoo import, live drafts, and decisions about players' health or careers.

## 3. Data and attribution

| Source | What is used | License and attribution |
| --- | --- | --- |
| nflverse via nflreadpy 0.1.5 | Play-by-play, weekly player and team stats, schedules (results, kickoff times, point spreads and totals), weekly rosters, injury reports, depth charts, snap counts, Next Gen Stats, player tables. The player id crosswalk is DynastyProcess data via nflverse ("Player id crosswalk from DynastyProcess via nflverse."). | CC-BY-4.0. "Data from nflverse (github.com/nflverse/nflverse-data), CC-BY-4.0. Modified: aggregated and modeled by this project." Next Gen Stats come from NFL Next Gen Stats, and snap counts from Pro Football Reference. Both are redistributed via nflverse. |
| ffverse ffopportunity via nflverse | Expected-points regressors (not displayed) | "Expected fantasy points from ffverse/ffopportunity via nflverse." The data license is not stated on the reference page; the package code is MIT. |
| FTN charting via nflverse | Not used. The FTN features are not built in features 0.2.0 and sit behind a flag that is off by default (D-009; docs/FEATURES.md). | CC-BY-SA-4.0. If the flag is ever turned on, the attribution page must show "FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0." Share-alike then applies to derived data we publish. |
| Sleeper API | League settings, rosters and player status. Read only, public data only (D-020). | "League and player data from Sleeper." Non-commercial use. |
| Public expert consensus rankings | Private evaluation only. Never a model feature, never loaded by the API or the web app, never displayed (D-007, D-051, D-082). | The data license is not confirmed, so this card gives only qualitative comparisons (section 6.4). |

The full wording is in docs/DATA_SOURCES.md and on the app's attribution page
(`/about/attribution`, `GET /meta/attribution`).

**Data range and storage.**
- History runs from 2016. The chain refits on every final regular-season row before the predicted week (docs/status/livetest-preregistration-2026.md, "refit").
- Raw and processed data are versioned Parquet snapshots with manifests and hashes (D-016).
- Every run records its input snapshot ids (reports/backtest_2026-11-03_v3.md section 1).

**Known data caveats.**
- Historical point spreads and totals are closing lines. They are slightly optimistic for a projection made midweek (D-008).
- The injury dataset has no timestamps. Same-week designations are lagged in the model, and the latest report is applied at prediction time (D-006).
- The game-day inactive list is excluded from backtests, because in history it identifies who did not play (D-065).

## 4. The model chain

```
team plays -> pass/run mix -> player opportunity share -> efficiency -> touchdowns -> stat lines -> fantasy points
```

- **Team level.** Stage 1a predicts team plays, 1b the pass rate, and 1c team touchdowns and field-goal attempts.
- **Player level.** Stage 2 predicts opportunity shares (targets, carries, dropbacks), stage 3 efficiency, and stage 4 touchdown shares.
- **Availability.** A separate layer gives each player's chance of being active.
- **Model choice per stage.** Every stage has a transparent structural baseline. LightGBM replaces the baseline only where the 95% interval of its loss minus the baseline's loss, over 2022-2024 walk-forward weeks, lies entirely below zero. That was the case for 19 of 30 stages (reports/ml_stage_selection_2026-11-03.md; docs/MODELING.md sections 4 and 8; `ffml/models/chain/selection.json`).
- **Simulation.**
  - Each week runs 2,000 simulations.
  - Game total and margin are drawn first, centred on the point spread and total.
  - Team volume and touchdowns follow, then Dirichlet opportunity shares among active players, then per-touch outcomes.
  - The draws keep the correlations: a quarterback and his receivers move together, teammates compete for volume, and opponents share game script (docs/MODELING.md section 5; docs/ARCHITECTURE.md section 4).
- **Kickers and defenses.** These use team scoring and red-zone expectations, kicker accuracy by distance, and opponent turnover, sack and points-allowed distributions (D-010).
- **Rest of season.** Later weeks replace the week's lines with a team-strength model and use wider uncertainty (docs/MODELING.md section 5).

## 5. Training and validation protocol

- **Walk-forward validation by season and week.** For each validation week, the model is fit on all earlier rows and predicts that week. It is refit every week. Random train/test splits are never used, including inside hyperparameter search (docs/MODELING.md section 2).
- **Seasons.**
  - Training data starts in 2016.
  - Walk-forward validation covers 2022-2024, with 2021 as the calibration warm-up.
  - 2025 is the final holdout.
  - 2026 weeks 1-7 are the forward check (D-005, D-096).
- **Point-in-time features.**
  - The feature cutoff is the first kickoff of the season-week.
  - Off-field snapshots (depth charts, rosters, injuries) are taken as of one hour before that cutoff (D-046).
  - Same-week usage, efficiency and injury information is lagged (D-006).
  - The leakage suite and the Evaluator's leakage, holdout and release-candidate audits passed (reports/backtest_2026-11-03_v3.md section 11).
- **Holdout used once.**
  - 2025 stayed behind a loader guard until one evaluator session, run under a pre-registration that fixed the commit, hashes, seeds, calibration mode and the 22 lines (D-005, D-058, D-091).
  - The access log shows one `holdout_eval` session (docs/status/gate-3.md).
  - One infrastructure rerun was used before any metric was read: a memory-pressure kill, logged in the evaluator's addendum (D-093).
  - Nothing was tuned on 2025, and the session was not rerun for a better result (D-092).
- **Scoring of record.**
  - The PPR, half-PPR and standard presets of `ffml/scoring` (D-079).
  - The Evaluator's independent PPR reference matches them on 29,191 of 29,192 rows. The one difference is a position player who kicked (reports/backtest_2026-11-03_v3.md section 2).
- **Population.** Headline numbers cover active players with a projection, and projections are conditional on being active (reports/backtest_2026-11-03_v3.md section 1).
- **Comparison rule.**
  - Calibrated forms are compared with calibrated forms.
  - Intervals come from a 95% week-clustered paired bootstrap with 500 draws and seed 0.
  - "Beats" means the interval for the chain minus the comparator lies entirely on the chain's side of zero (D-078).
  - Start/sit and Spearman use one model-agnostic pair set (D-074, D-077).

## 6. Metrics

All tables use PPR scoring. "Calibrated" means the `+cal` form of each model.

### 6.1 Validation, 2022-2024 pooled (reports/backtest_2026-11-03_v3.md sections 3 and 12)

MAE of the median projection (lower is better):

| Position | n | chain_v1 | rolling_avg_4 | prior_season | rolling_median_8 | direct challenger |
| --- | --- | --- | --- | --- | --- | --- |
| QB | 2,009 | 5.693 | 6.266 | 6.746 | 6.314 | 5.750 |
| RB | 5,066 | 3.901 | 4.348 | 4.421 | 4.243 | 3.939 |
| WR | 7,938 | 4.068 | 4.527 | 4.485 | 4.376 | 4.085 |
| TE | 4,885 | 2.692 | 3.026 | 2.979 | 2.869 | 2.721 |
| K | 1,613 | 3.685 | 4.057 | 3.772 | 3.945 | 3.662 |
| DST | 1,630 | 4.217 | 4.824 | 4.492 | 4.625 | 4.193 |

Other metrics for calibrated chain_v1:
- Pinball loss at q10, q50 and q90.
- Tie-aware coverage of the 80% range, where the acceptance band is [0.75, 0.85].
- Mean width of the 80% range in points.
- Spearman rank correlation above replacement.
- Start/sit accuracy on the common pair set. Start/sit is not defined for K and DST.

| Position | pinball q10 | pinball q50 | pinball q90 | tie-aware coverage | 80% width | Spearman | start/sit |
| --- | --- | --- | --- | --- | --- | --- | --- |
| QB | 1.136 | 2.846 | 1.412 | 0.779 | 19.47 | 0.330 | 0.623 |
| RB | 0.616 | 1.950 | 1.165 | 0.785 | 13.09 | 0.222 | 0.576 |
| WR | 0.621 | 2.034 | 1.234 | 0.786 | 13.12 | 0.260 | 0.590 |
| TE | 0.380 | 1.346 | 0.876 | 0.793 | 8.30 | 0.301 | 0.614 |
| K | 0.704 | 1.843 | 0.984 | 0.807 | 11.33 | 0.139 | n/a |
| DST | 0.790 | 2.108 | 1.147 | 0.820 | 13.67 | 0.256 | n/a |

MAE difference, chain minus baseline, with the 95% interval (negative favours the chain):

| Position | vs rolling_avg_4 | vs prior_season | vs rolling_median_8 |
| --- | --- | --- | --- |
| QB | -0.573 [-0.786, -0.402] | -1.054 [-1.251, -0.849] | -0.621 [-0.807, -0.438] |
| RB | -0.447 [-0.521, -0.373] | -0.521 [-0.579, -0.468] | -0.342 [-0.409, -0.285] |
| WR | -0.459 [-0.513, -0.408] | -0.417 [-0.467, -0.372] | -0.309 [-0.368, -0.251] |
| TE | -0.334 [-0.398, -0.276] | -0.287 [-0.332, -0.250] | -0.177 [-0.235, -0.126] |
| K | -0.372 [-0.463, -0.276] | -0.087 [-0.151, -0.026] | -0.260 [-0.347, -0.158] |
| DST | -0.607 [-0.744, -0.463] | -0.275 [-0.350, -0.190] | -0.408 [-0.503, -0.302] |

**What 2022-2024 shows.**
- Against the three transparent baselines, the chain beats each one on MAE and on pinball loss at q10, q50 and q90 at all six positions.
- It also beats them on Spearman and start/sit, with one exception: K Spearman against prior_season shows no difference.
- In raw form, the chain's pooled MAE is below rolling_median_8 in every week of the season, by 0.176 to 0.551 points (section 4 of the report).

### 6.2 Holdout 2025 and forward check 2026 weeks 1-7 (reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md)

The 22 pre-registered lines:

| Lines | What they test | 2025 | 2026 weeks 1-7 |
| --- | --- | --- | --- |
| L01-L12 | The chain beats rolling_avg_4, prior_season and rolling_median_8 at QB, RB, WR and TE on MAE and pinball q10, q50 and q90. | 12 of 12 pass | 12 of 12 pass |
| L13-L16 | Start/sit at QB, RB, WR and TE is not worse than the public expert consensus. | 4 of 4 pass | 4 of 4 pass |
| L17-L22 | Tie-aware coverage of the 80% range lies in [0.75, 0.85] at QB, RB, WR, TE, K and DST. | 5 of 6 pass. **QB fails at 0.739.** | 6 of 6 pass |

Verdict: NOT APPROVED. Forward check: PASS. The forward check never changes
the holdout verdict (D-094).

Tie-aware coverage of the calibrated chain:

| Position | 2022-2024 validation | 2025 holdout | 2026 weeks 1-7 |
| --- | --- | --- | --- |
| QB | 0.779 | **0.739 (fail)** | 0.774 |
| RB | 0.785 | 0.774 | 0.782 |
| WR | 0.786 | 0.793 | 0.783 |
| TE | 0.793 | 0.826 | 0.807 |
| K | 0.807 | 0.798 | 0.776 |
| DST | 0.820 | 0.800 | 0.827 |

The QB ranges were too narrow on 2025. 13.5% of outcomes fell below q10 and
11.6% above q90, against 10% each. Raw QB coverage was 0.693 (D-097).

2025 MAE difference, chain minus comparator (calibrated, 95% interval):

| Position | vs rolling_avg_4 | vs prior_season | vs rolling_median_8 | vs direct challenger |
| --- | --- | --- | --- | --- |
| QB | -0.907 [-1.125, -0.672] | -1.151 [-1.414, -0.843] | -0.853 [-1.074, -0.647] | +0.022 [-0.118, +0.153] |
| RB | -0.370 [-0.496, -0.253] | -0.445 [-0.582, -0.311] | -0.268 [-0.345, -0.195] | +0.007 [-0.051, +0.063] |
| WR | -0.477 [-0.610, -0.353] | -0.422 [-0.525, -0.327] | -0.328 [-0.471, -0.201] | -0.044 [-0.081, -0.010] |
| TE | -0.414 [-0.505, -0.337] | -0.401 [-0.482, -0.317] | -0.311 [-0.391, -0.208] | -0.057 [-0.093, -0.020] |
| K | -0.265 [-0.505, -0.042] | -0.033 [-0.165, +0.103] | -0.181 [-0.376, -0.006] | +0.090 [-0.009, +0.205] |
| DST | -0.674 [-0.899, -0.473] | -0.261 [-0.418, -0.073] | -0.541 [-0.701, -0.365] | +0.019 [-0.033, +0.062] |

### 6.3 Where the chain does not beat the transparent baselines

On 2022-2024:
- **Calibration.** The chain is no better calibrated than the calibrated baselines. The coverage-gap comparison shows no difference at any position.
- **K rank metric.** K Spearman against prior_season shows no difference.

On 2025 (calibrated verdict grid of the holdout report):
- **K.**
  - MAE and pinball q50 against prior_season show no difference: -0.033 [-0.165, +0.103] and -0.017 [-0.082, +0.051].
  - Pinball q90 shows no difference against any of the three baselines.
  - Spearman shows no difference against any of the three baselines.
  - The chain does beat rolling_avg_4 and rolling_median_8 on K MAE.
- **DST.**
  - The chain beats all three baselines on MAE, on all three pinball losses and on Spearman.
  - The coverage gap shows no difference.
- **K and DST are outside the accuracy lines.** L01-L12 test only QB, RB, WR and TE. K and DST enter the approval rules only through the coverage lines L21-L22.
- **QB coverage.** The QB coverage gap is worse than all three baselines. Their calibrated tie-aware QB coverage was 0.809 to 0.820, against the chain's 0.739. The failing line, L17, comes from this.
- **QB and RB rank metrics.**
  - Spearman and start/sit show no difference against rolling_avg_4 and rolling_median_8.
  - The chain beats prior_season on both.
- **WR and TE rank metrics.** The chain beats all three baselines on both Spearman and start/sit.

### 6.4 Against a public expert consensus (qualitative)

**How it is compared.**
- The comparison uses identical player-weeks (D-081).
- Players whose game came before the weekly consensus snapshot are excluded, mostly Thursday games (D-036). The chain's results on those player-weeks are reported separately in the private reports.
- The consensus rankings are converted to points with a rank-to-points curve fitted on prior seasons only (D-052).

**Results.**
- **The chain did not beat the public expert consensus on MAE at any position on 2025, and it was worse at QB and RB** (D-097).
  - On 2025 there was no detectable MAE difference at WR, TE, K and DST.
  - It was also worse on QB and RB pinball q50 and on TE pinball q90.
  - It was better on the RB and WR coverage gap and on TE Spearman.
- **On 2022-2024 there was no detectable MAE difference at any position.**
  - It was worse on QB pinball q10.
  - It was better on WR pinball q90 and on the RB and WR coverage gap.
- **Start/sit was not worse than the consensus at any position, in either period.** On 2025 it tied at QB, RB and WR and was better at TE (D-097).
- **Parity, not superiority.** The approval lines ask only that start/sit is not worse than the consensus (L13-L16). No claim is made that the chain is more accurate than expert consensus.

**Where the figures are.**
- The consensus figures are in the private evaluator reports:
  - reports/backtest_2026-11-03_v3.md, sections 8 and 12;
  - reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md;
  - docs/status/gate-3.md.
- They stay private under D-007 and are not reproduced in this card or anywhere in the app.

### 6.5 Against the direct challenger

**2022-2024.**
- The chain has lower calibrated MAE than the direct challenger at RB and TE.
- There is no difference at QB, WR, K and DST.
- It is worse at K pinball q90: +0.069 [+0.027, +0.116] (reports/backtest_2026-11-03_v3.md section 3).

**2025.**
- The chain has lower MAE at WR and TE.
- There is no difference at QB, RB, K and DST.
- It is worse at K pinball q90 and on the QB coverage gap (holdout report, calibrated grid).

**Why the challenger is not shipped.** It predicts points under fixed scorings. The chain is kept because it produces stat lines that any league scoring can use (docs/MODELING.md sections 4 and 8).

### 6.6 Sensitivities (reports/backtest_2026-11-03_v3.md section 10)

- **Point spreads and totals.** Removing them makes the chain worse at QB, RB, K and DST. In raw form, chain minus the variant without lines is -0.150 [-0.221, -0.087] at QB, -0.021 at RB, -0.047 at K and -0.093 at DST. Because history uses closing lines, these validation results are slightly optimistic for a midweek projection (D-008).
- **Same-week injury report.** On 2022-2024, the primary chain's calibrated MAE shows no difference from the final-report injury configuration at QB, RB, WR and DST. It is slightly worse at TE (+0.030 [+0.016, +0.042]) and K (+0.040 [+0.012, +0.071]). The final-report configuration is a labelled sensitivity, not the headline configuration (D-006). Switching to it after seeing its 2025 QB coverage (0.758) is ruled out (D-097).

## 7. Calibration, with disclosures

**Method for both versions.**
- Quantiles use a per-position tie-aware conformal step: `OnlinePITQuantile`, seed 0, `min_count=50`, `atoms="nearest"`.
- Boom and bust use a per-position isotonic step: `OnlineIsotonic`, `min_count=200` (docs/status/livetest-preregistration-2026.md).

**How the versions differ.**
- **chain_v1.** The calibrator was frozen on 2022-2024 for the holdout and the forward check, and never updated from 2025 or 2026 (D-095).
- **chain_v2.**
  - The same calibrator updates walk-forward every week, from earlier weeks only (D-098).
  - This is the validation run's own method, so the 2022-2024 coverage in section 6.1 describes it.
  - Moving from frozen to walk-forward calibration was decided after the 2025 result was known. For that reason it is tested only on unseen live weeks.

**Changes made after validation, before 2025 was opened.** These are disclosed as the owner required.

1. **The coverage rule was changed after the 2022-2024 results were seen (D-085).**
   - The closed-interval rule counted every scoreless game as covered whenever q10 was exactly 0. That is common for RB, WR and TE: 15.7%, 24.6% and 40.1% of rows were tied at q10.
   - Acceptance now uses tie-aware coverage: the share of rows whose randomized PIT, drawn with seed 0, lies in (0.1, 0.9).
   - The 75-85% band is unchanged, and the same rule applies to every model.
   - Under the superseded closed-interval rule, the calibrated chain would fail at WR (0.858) and TE (0.873) on 2022-2024 (reports/backtest_2026-11-03_v3.md section 12).
2. **The boom/bust calibrator was changed from pooled to per position (D-086).**
   - The pooled calibrator gave kicker slopes of 0.14-0.26.
   - The per-position calibrator is fitted walk-forward on earlier weeks only, and the report shows before and after for every position.
3. **The conformal calibrator was aligned with the tie-aware rule (D-088).**
   - Report v2 showed K failing at 0.740 calibrated, because the calibrator still targeted the closed-interval definition. In v3, K is at 0.807.
   - The raw predictions did not change between v2 and v3. The calibrated quantiles and coverage did change, and 15 calibrated verdict cells moved. It is not accurate to say accuracy was unchanged (D-090, correction).
   - This was the last calibration change before the holdout.

**Seed sensitivity.** Metric seeds 0-4 and calibrator seeds 0-4 change no
PASS/FAIL result at any position (reports/backtest_2026-11-03_v3.md section 5.3).

## 8. Boom and bust, and the low-confidence positions

**Definitions.**
- Boom is the chance of a top positional finish that week: QB 6, RB 12, WR 12, TE 6, K 6, DST 6.
- Bust is the chance of finishing outside the startable range: QB 18, RB 36, WR 48, TE 18, K 20, DST 20.
- Both are computed after league scoring and are conditional on the player being active (D-017, D-067; docs/MODELING.md section 5).

**Low-confidence rule.** The rule was fixed before the results were seen
(D-086). A probability is low confidence when its calibrated slope lies
outside [0.8, 1.2], or when the upper end of the slope's 95% interval is
below 0.8.

Calibration slopes, with 1.0 meaning calibrated:

| Position | boom 2022-2024 | bust 2022-2024 | boom 2025 | bust 2025 | Status |
| --- | --- | --- | --- | --- | --- |
| QB | 0.923 | 0.961 | 0.563 [0.418, 0.743] | 0.769 [0.539, 0.982] | **low confidence** (D-104) |
| RB | 0.966 | 1.016 | 0.908 | 0.901 | within the rule |
| WR | 0.946 | 1.006 | 0.972 | 0.903 | within the rule |
| TE | 0.930 | 0.949 | 0.885 | 0.929 | within the rule |
| K | 0.118 [-0.042, 0.962] | 0.456 [0.116, 0.861] | 1.140 | 0.735 | **low confidence** (D-089) |
| DST | 0.774 [0.245, 1.285] | 0.289 [0.134, 0.839] | 0.226 | 0.528 | **low confidence** (D-089) |

Sources: reports/backtest_2026-11-03_v3.md section 5.2 (after the
per-position calibrator) and the holdout report's boom/bust table.

**Effect on the app.**
- QB, K and DST boom/bust are hidden or marked low confidence. The set is read from the run manifest, with {QB, K, DST} as the default (D-104).
- K boom passed the rule on 2025, but under D-089 it stays flagged without a further post-hoc choice.
- Boom and bust are not among the 22 approval lines. The live test therefore cannot remove these flags on its own.

## 9. Approval status and the live-test design

**History.**
- chain_v1 was NOT APPROVED on 2025, and Gate 3 failed (D-097; docs/status/gate-3.md).
- None of the following is allowed, because each would select or adjust on the holdout:
  - accepting 0.739 as close enough;
  - switching to the final-report variant, the direct challenger or a baseline chosen after seeing 2025;
  - recalibrating on 2025.
- No Week 9 projections were published (D-092).

**chain_v2 live test.** The sources are docs/status/livetest-preregistration-2026.md and D-098 to D-111 and D-116 to D-119.

- **What is tested.** The unchanged 22 lines of the holdout pre-registration, applied once over 18 weeks: 2026 weeks 10-18 and 2027 weeks 1-9.
  - A resized nine-week design was considered.
  - No candidate design met the pre-set limits: a good model passing at least 70% of the time and a no-edge model at most 10%. The best reached 0.614.
  - The fallback in D-105 item 3 therefore applies, and there is no provisional-approval tier (D-110).
- **Weekly commitments.**
  - Each week, the frozen chain_v2 code builds the projections before the week's official first kickoff. The run starts at least 24 hours early and retries every 30 minutes. The projections are hashed and pushed to a dedicated git branch, and GitHub's push record is the external timestamp (D-106, D-109).
  - Weeks are scored only after their games are final.
- **Scoring.**
  - Weekly scoring runs on Fridays.
  - It uses a fixed data cutoff: 12:00 UTC on the first Friday at least 72 hours after the week's last kickoff.
  - Stat corrections after the cutoff are ignored, and the snapshot hash is sealed with the week (D-116, D-119).
- **Sealed interim results.**
  - Interim results stay sealed until the verdict.
  - They are never used to tune chain_v2, to choose between weeks or to stop early (D-111).
- **Verdict rule.**
  - The binding rule is the 500-draw, seed-0 week-clustered bootstrap.
  - A 5,000-draw check is reported but can never change the verdict (D-111).
  - The verdict is a pass only if all 22 lines pass over the scored weeks.
- **Missed weeks.** Up to two missed weeks are excluded without extending the window. A third miss makes the test INVALID, not failed (D-111).
- **Frozen code.** The model, feature, simulation, scoring and scorer code is frozen during the window. Allowed changes are logged before first use (docs/status/livetest-changelog-2026.md).
- **Expected verdict date.** About 2027-11-12, the Friday data cutoff after 2027 week 9 (D-119). The 2027 schedule is not yet published, so the date could fall between about 2027-11-05 and 2027-11-19.

**Chance of passing, stated plainly.** Even a good model has only about a 56%
chance of passing the 22 rules over the 18 weeks. Here a good model means one
with the validated 2022-2024 accuracy and exact calibration.

| Weeks scored | Good model passes (variance-corrected) | No-edge model passes |
| --- | --- | --- |
| 18 | 0.557 | 0 of 5,000 (95% upper bound 0.0006) |
| 17 (one miss) | 0.518 | 0 of 5,000 (95% upper bound 0.0006) |
| 16 (two misses) | 0.500 | 0 of 5,000 (95% upper bound 0.0006) |

Source: docs/status/livetest-preregistration-2026.md. Season-wide shifts were
not simulated, such as the 2025 QB coverage drop from 0.779 to 0.739.

A failure would not by itself show that the chain is poor, because even a
good model often fails this test. A pass would show that the chain met the
pre-registered rules on unseen weeks, and a model with no edge passed in none
of the 5,000 simulated tests. It does not show that the
chain beats expert consensus, and it does not test K and DST accuracy or
boom/bust calibration.

## 10. Ethical and usage considerations

- **Not betting advice.** The project places no bets, and no pick is presented as certain. Do not use these outputs for wagering of any kind (PROMPT_1.md).
- **No public projections without a passing test.** Labels in the app follow the API's `approval_status`. Private-test views carry a "not approved" label (D-099, D-100, D-114).
- **Honest reporting.** If the chain does not beat a baseline or the consensus, the reports say so, and the verdict rules are not changed after results are seen (D-082). This card states every comparison where the chain is not better (sections 6.3 to 6.5).
- **Restricted data.**
  - Consensus rankings stay in private evaluation.
  - The API container never mounts the restricted directories (D-007, D-113).
- **Data about real people.**
  - The inputs are public game statistics and public injury reports about professional players.
  - The model makes no statement about a player's health beyond the public report, and its availability estimates are probabilities.
  - League data comes from public Sleeper endpoints, read only (D-020).
- **Single-user scope.** The API has no authentication. It is for one person on localhost until the D-011 session work is done (QA-F14, Gate 6).
- **Non-commercial.** Use follows Sleeper's terms and the nflverse CC-BY-4.0 attribution (D-013).

## 11. Limitations

The prioritized list of known limitations and planned improvements is
docs/LIMITATIONS.md. The most important points:
- there is no approved model;
- the QB ranges were too narrow on 2025;
- the chain is on a par with expert consensus, not better;
- QB, K and DST boom/bust are low confidence;
- the app's ranges under league scoring are raw simulation quantiles, because request-time calibration is not built;
- the live test is small and runs on one laptop.

## 12. Sources

- reports/backtest_2026-11-03_v3.md: the 2022-2024 validation report, sections 1-5, 10-13.
- reports/evaluator_release_chain_v1-primary-c2024w19-20261102T222634Z.md and its addendum: the 2025 holdout and the 2026 weeks 1-7 forward check.
- docs/status/gate-3.md: the Gate 3 result.
- docs/status/livetest-preregistration-2026.md: the chain_v2 live-test pre-registration.
- reports/ml_stage_selection_2026-11-03.md: stage selection, structural baselines against LightGBM.
- docs/DECISIONS.md: D-003 to D-013, D-017, D-036, D-046, D-052, D-058, D-065, D-067, D-073 to D-119.
- docs/MODELING.md, docs/ARCHITECTURE.md and docs/DATA_SOURCES.md.
