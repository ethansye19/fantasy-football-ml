# Modeling Plan

## 1. Principle

Model football outcomes first, then apply fantasy scoring:

team plays -> pass/run mix -> player opportunity share -> efficiency -> touchdowns -> stat lines -> fantasy points

Every stage outputs a distribution. Fantasy points are computed at request
time from simulated stat lines, so any league scoring works without
retraining. Transparent baselines come first and gradient boosting is adopted
per stage only where it beats them on the validation seasons.

## 2. Data split and validation protocol

| Role | Seasons | Use |
| --- | --- | --- |
| Training | 2016-2021 (expanding as validation proceeds) | fitting |
| Walk-forward validation | 2022, 2023, 2024 | model selection, hyperparameters, shrinkage, calibration |
| **Final holdout** | **2025 (weeks 1-18)** | evaluated exactly once by the Independent Evaluator after selection; untouched before then (enforced by a guard in the data loader that refuses season 2025 unless `FFML_HOLDOUT_UNLOCK` is set and logged) |
| Forward test | 2026 weeks 1-8 (and onward) | live paper tracking, reported with caveats about sample size |

Walk-forward mechanics: for each validation season s and week w (2 to 18),
fit on all rows with season < s plus season s weeks < w, predict week w.
Models are refit every week in the backtest (LightGBM fits in seconds on
about 110k player-weeks). No random train/test splits anywhere, including
inside hyperparameter search (which uses the same walk-forward folds).

Regular season only for training and evaluation (postseason rows are kept in
the lake but excluded because fantasy seasons end at week 17 or 18).

## 3. Baselines (reported for every position and week)

1. **Rolling player average**: mean fantasy points over the player's last 4
   games (also last 3 and EW variants), computed under the league scoring in
   question. Distribution: empirical quantiles of the player's last 8 games,
   falling back to position quantiles.
2. **Prior-season average**: prior season per-game average; rookies and
   players without a prior season use the position-and-draft-round average.
3. **Public consensus**: FantasyPros weekly expert consensus rankings from
   the DynastyProcess archive (`ecr_type = "wp"`). The archive is scraped on
   Fridays, so a row is used only where `valid_for_game` holds (the scrape
   date precedes the date of that player's own game); Thursday-night players
   are therefore excluded from this comparison and the report says so.
   Weekly pages exist only as PPR for RB, WR, TE and as scoring-free pages for
   QB, K, DST, so the comparison is run under PPR scoring; coverage is
   2020 week 6 through 2026, with 2024 starting in week 4. Because ECR is a
   rank, the comparison uses rank correlation and start/sit accuracy; a
   points comparison uses a rank-to-points curve fitted on prior seasons
   only. Licensing is unverified, so this baseline lives in the private
   backtest report only, as aggregate metrics (DECISIONS D-007).

Structural baselines also exist per stage: team plays = L5 mean, pass rate =
EW mean adjusted by spread via a one-parameter linear fit, shares = EW share,
efficiency = shrunk EW rate, TD shares = shrunk EW share. The simulation can
run end to end on these baselines, which is the first release candidate.

## 4. Stage models

| Stage | Baseline | Candidate model | Output distribution |
| --- | --- | --- | --- |
| 1a team plays | L5 mean | LightGBM regression (features A-C) | Normal with residual sd by week bucket, truncated at 40-90 |
| 1b pass rate | EW neutral pass rate + spread adjustment | LightGBM regression | Beta with fitted concentration |
| 1c team TDs and FG attempts | implied total / 7 heuristic | LightGBM Poisson on offensive TDs; FG attempts from rz trips and stalled-drive rate | Poisson or negative binomial |
| 2 opportunity shares | shrunk EW share | LightGBM regression on logit share, then renormalized within team across active players | Dirichlet around predicted shares with concentration tuned per position |
| 3 efficiency | shrunk EW rate | LightGBM regression with monotone constraints where sensible (opponent strength) | per-touch outcomes: catch as Bernoulli, yards as Gamma with position-specific shape, QB completions Binomial |
| 4 TD shares | shrunk EW share of team TDs | LightGBM regression on logit share | Multinomial allocation of team TDs to players |
| Availability | rules | rules plus a small logistic model for Questionable outcomes if validation data supports it | Bernoulli active flag |

Direct challenger (not shipped): LightGBM quantile regression on PPR points
with the same features, used to quantify what the structural chain costs or
gains. If the challenger clearly beats the chain, the report says so.

## 5. Simulation

For each week, N = 2,000 (configurable) simulations per game:

1. Game level: draw total and margin from a bivariate Normal centred on the
   Vegas total and spread (sd fitted on history, about 13.5 points for the
   margin). Derive each team's points and a game-script shift.
2. Team level: draw plays from stage 1a conditioned on the game-script shift,
   pass rate from 1b, offensive TDs and FG attempts from 1c conditioned on
   drawn team points.
3. Availability: draw the active flag per player; renormalize predicted shares
   among active players with absorption factors.
4. Opportunity: draw target and carry shares from a Dirichlet; multiply by
   team attempts to get targets, carries, and dropbacks per player.
5. Efficiency: per player, draw receptions (Binomial with catch rate), yards
   (Gamma sum), QB completions and yards; sacks and interceptions from rates.
6. Touchdowns: allocate team pass and rush TDs to players with a Multinomial
   over TD shares; QB receives the passing TDs.
7. Turnovers and bonuses: fumbles lost from per-touch rates; two-point
   conversions from team two-point attempts.
8. Kickers and DST: FG attempts split across distance buckets with make
   probabilities; DST events from opponent rates.

Correlations preserved: QB with receivers (shared pass volume and TDs),
teammates negatively (shares), opponents (points and script), stacks across a
game (total). Seeds are stored in the run manifest.

Outputs per player: floor = 10th percentile, median, ceiling = 90th
percentile under the league scoring; boom = probability of a top-N positional
finish this week among all simulated players (QB top 6, RB top 12, WR top 12,
TE top 6, K and DST top 6); bust = probability of finishing outside the
startable range (QB 18, RB 36, WR 48, TE 18, K and DST 20). Thresholds are
configurable and documented in the model card.

Rest-of-season: the chain runs for every remaining week with opponent and
schedule context from `dim_game`; Vegas lines beyond the next week are
replaced by a team-strength model (rolling EPA ratings converted to spread and
total), with wider uncertainty. ROS totals come from summing per-week samples
with persistent player-level draws for availability so that injury risk
compounds.

## 6. Uncertainty and calibration

- Interval target: 80% central interval (q10-q90), plus q25-q75 reported.
- Calibration: on the validation seasons, compute per-position conformal
  offsets for q10 and q90 (split-conformal on walk-forward residuals) and
  reliability curves for boom and bust probabilities. Apply the offsets to the
  holdout and report coverage before and after.
- Simulation dispersion (Dirichlet concentration, Gamma shape, play sd) is
  tuned on the validation seasons to hit nominal coverage and pinball loss,
  never on the holdout.

## 7. Metrics (by position and by week, with bootstrap confidence intervals clustered by week)

| Metric | Definition |
| --- | --- |
| MAE | mean absolute error of the median projection vs. actual points, under PPR, half-PPR and standard |
| Pinball loss | at q10, q50, q90 |
| Interval coverage | share of actuals inside the 80% interval, and mean interval width |
| Calibration | reliability of boom and bust probabilities (Brier score and calibration slope), PIT histogram of the full distribution |
| Rank correlation | Spearman between projected median and actual points within position-week among players with projected median above the positional replacement level |
| Start/sit decision accuracy | among same-position pairs where both players are startable (QB top 12, RB top 24, WR top 36, TE top 12 by projected median) and both active, the share of pairs where the higher-projected player scored more; also weighted by the absolute projection gap, and lineup efficiency (points of the lineup chosen by projections divided by the optimal lineup) |

Evaluation population: players who were active that week with a projection;
a secondary table includes inactive players to measure the availability layer.

## 8. Acceptance rules

- A model stage replaces its baseline only if it improves the validation
  metric with a confidence interval excluding zero.
- The shipped chain must beat baselines 1 and 2 on MAE and pinball loss for
  RB, WR, TE, and QB on the holdout, and must not be worse than baseline 3 on
  start/sit accuracy by more than the confidence interval. Where it does not,
  the model card and the UI say so plainly, and the app still ships with the
  structural chain because scoring flexibility and distributions are required.
- Coverage of the 80% interval on the holdout must be within 75-85% per
  position after calibration.
- No metric claims appear in the UI unless they appear in the evaluator-signed
  report with the exact numbers.

## 9. Injury information policy (two labelled configurations)

| Configuration | Same-week injury report | Used for |
| --- | --- | --- |
| primary | not used as model features; availability layer applied with the latest report at prediction time | all headline metrics |
| final-report | adds report_status and teammates-out features to stages 2 and 4 | sensitivity section of the report |

Live predictions always use the latest report for availability because the
report is public before kickoff; the backtest primary configuration follows
the brief and lags injury information.

## 10. Model registry and retraining

- `artifacts/models/REGISTRY.json` tracks versions with status candidate,
  approved (evaluator signature file present), live.
- Weekly retrain runs Thursday 09:00 UTC after stat corrections. A retrain is
  promoted only when the retraining check passes: rolling 4-week MAE and
  coverage on the newest completed weeks are not worse than the live model by
  more than the historical week-to-week noise, data-quality checks passed, and
  feature schema is unchanged. Otherwise the live model is kept and an alert
  is raised.

## 11. Reports

- `reports/backtest_<date>.md`: metrics tables by position and week for the
  chain, the challenger and the three baselines; calibration plots; ablations
  (no Vegas lines, no NGS, final-report injuries).
- `reports/evaluator_<candidate>.md`: the Independent Evaluator's audit:
  leakage checks run, holdout results, approval or rejection with reasons.
- `reports/MODEL_CARD.md`: intended use, data, methods, metrics with
  intervals, limitations, and the explicit statement of where baselines were
  not beaten.
