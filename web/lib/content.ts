/**
 * Built-in copy for the information pages, used when the API's /meta
 * endpoints cannot be reached. Attribution wording follows
 * docs/DATA_SOURCES.md exactly; the model summary follows the decision log
 * (D-089, D-097, D-098) as of 2026-11-04.
 */
import type { AttributionResponse, ModelCardResponse } from "./api/types";

export const CONTENT_AS_OF = "2026-11-04";

export const ATTRIBUTION_FALLBACK: AttributionResponse = {
  sources: [
    {
      id: "nflverse",
      name: "nflverse",
      license: "CC-BY-4.0",
      url: "https://github.com/nflverse/nflverse-data",
      in_use: true,
      text: "Data from nflverse (github.com/nflverse/nflverse-data), CC-BY-4.0. Modified: aggregated and modeled by this project.",
      used_for:
        "Play-by-play, player and team statistics, schedules, rosters, injuries, depth charts, Next Gen Stats and Pro Football Reference snap counts, loaded with nflreadpy, the nflverse Python client.",
      notes: [
        "Next Gen Stats originate from NFL Next Gen Stats and snap counts from Pro Football Reference, both redistributed via nflverse.",
      ],
    },
    {
      id: "ffopportunity",
      name: "ffverse ffopportunity",
      license: "Package code MIT; data license not stated on the reference page",
      url: "https://github.com/ffverse/ffopportunity",
      in_use: true,
      text: "Expected fantasy points from ffverse/ffopportunity via nflverse.",
      used_for: "Expected-points inputs to the model.",
      notes: [],
    },
    {
      id: "sleeper",
      name: "Sleeper",
      license: "Sleeper API terms: free for non-commercial use",
      url: "https://docs.sleeper.com",
      in_use: true,
      text: "League and player data from Sleeper.",
      used_for: "League settings, rosters and player status (read only).",
      notes: [],
    },
    {
      id: "ftn",
      name: "FTN charting",
      license: "CC-BY-SA-4.0",
      url: "https://ftndata.com",
      in_use: false,
      text: "FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0.",
      used_for:
        "Optional charting features. Off by default (D-009) because share-alike applies to derived datasets that are published.",
      notes: [],
    },
  ],
  usage: "Personal, non-commercial use (D-013). Commercial use would need Sleeper's permission.",
  not_displayed: "Expert ranking data used only in private model evaluation is never shown in this app.",
  independence:
    "An original project. Not affiliated with or endorsed by the NFL, nflverse, FTN, Sleeper or any fantasy football company.",
};

export const MODEL_CARD_FALLBACK: ModelCardResponse = {
  title: "Football-first projection chain",
  what_it_predicts:
    "Distributions of player stat lines for each NFL week, simulated 2,000 times. Fantasy points are computed afterwards with each league's own scoring, so any scoring works without retraining.",
  method: [
    "Team plays and pass/run mix, then each player's share of targets, carries and dropbacks, then efficiency, then touchdowns, then fantasy points.",
    "Transparent baselines first (rolling average, prior-season average, rolling median); gradient-boosted stages are kept only where they beat them on walk-forward folds.",
    "Monte Carlo simulation with shared game draws, so a quarterback and his receivers move together and teammates compete for volume.",
    "Only information available before each week's first kickoff is used; validation is walk-forward by season and week.",
  ],
  status_summary:
    "No model is approved. The first candidate (chain_v1) did not pass its one-time 2025 holdout test: its quarterback 80% ranges were too narrow (tie-aware coverage 0.739, required 0.75 to 0.85); the other 21 of 22 pre-registered checks passed. A second version (chain_v2) adds 2025 to training and updates its calibration weekly; it must pass a pre-registered live test on 2026 weeks before any projections are published.",
  definitions: DEFINITIONS_FALLBACK(),
  limitations: [
    "Kicker and defense boom/bust probabilities are low confidence (D-089).",
    "Quarterback boom/bust probabilities were miscalibrated on the 2025 holdout (D-097).",
    "Historical betting lines are closing lines, slightly optimistic for midweek projections (D-008).",
    "Injury designations have no timestamps in the source data; the model lags them and the latest report caps availability at prediction time (D-006).",
    "Projections are probabilities, not promises.",
  ],
  documentation: ["docs/MODELING.md", "docs/DECISIONS.md (D-097 to D-099)"],
};

function DEFINITIONS_FALLBACK(): Record<string, string> {
  return {
    floor: "10th percentile of simulated points under your scoring.",
    median: "50th percentile.",
    ceiling: "90th percentile.",
    boom: "Chance of a top finish at the position that week (QB 6, RB 12, WR 12, TE 6, K 6, DST 6).",
    bust: "Chance of finishing outside the startable range (QB 18, RB 36, WR 48, TE 18, K 20, DST 20).",
  };
}

export const DEFINITIONS = DEFINITIONS_FALLBACK();

/** Limitations of the app itself (the model's come from /meta/model-card). */
export const APP_LIMITATIONS: { title: string; text: string }[] = [
  {
    title: "Projections are off until a model passes its test",
    text: "The real app shows no projections, start/sit, waivers, trades or rankings until a model passes its pre-registered test (D-097, D-099). Private test environments show labeled, not-approved numbers only.",
  },
  {
    title: "Ranges under custom scoring are raw simulation quantiles",
    text: "The model's calibration is fitted on reference scorings and is not re-fitted for each league, so floors and ceilings under unusual scoring may be a little too narrow or too wide.",
  },
  {
    title: "Few simulations for some players",
    text: "Players who are active in fewer than 100 of the simulations are marked low sample. Below 20 active simulations their ranges are not shown.",
  },
  {
    title: "Historical replays",
    text: "A replay of a past week uses the information that was available before that week, but rosters are today's league rosters.",
  },
  {
    title: "League sources",
    text: "Leagues can be imported from Sleeper or entered by hand. ESPN and Yahoo import are not available. Defensive player (IDP) slots are accepted and ignored, and some scoring keys are not supported; each league lists the ones that are ignored.",
  },
  {
    title: "No accounts",
    text: "This browser remembers which league you picked. Leagues are stored by the app's API, not tied to a login.",
  },
  {
    title: "Offseason",
    text: "When no regular-season games remain, weekly features pause and rankings become season-level.",
  },
  {
    title: "Use",
    text: "For personal, non-commercial use. Nothing here is betting advice, and no outcome is promised.",
  },
];
