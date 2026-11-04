"""Static content of the meta endpoints: attribution (DATA_SOURCES.md) and the model card.

FantasyPros expert consensus rankings are used only inside private model
evaluation (D-007, D-051): they are not a displayed source, and nothing here
reports consensus comparisons.
"""

from __future__ import annotations

from typing import Any

NFLVERSE_TEXT = (
    "Data from nflverse (github.com/nflverse/nflverse-data), CC-BY-4.0. Modified: aggregated "
    "and modeled by this project."
)
FTN_TEXT = "FTN charting data: ftndata.com via nflverse, CC-BY-SA-4.0."
SLEEPER_TEXT = "League and player data from Sleeper."


def attribution(ftn_in_use: bool) -> dict[str, Any]:
    return {
        "sources": [
            {
                "id": "nflverse",
                "name": "nflverse",
                "license": "CC-BY-4.0",
                "url": "https://github.com/nflverse/nflverse-data",
                "in_use": True,
                "text": NFLVERSE_TEXT,
                "used_for": (
                    "Play-by-play, player and team stats, schedules and lines, rosters, "
                    "injuries, depth charts, snap counts, Next Gen Stats, player ids."
                ),
                "notes": [
                    "Next Gen Stats originate from NFL Next Gen Stats and are redistributed "
                    "via nflverse.",
                    "Snap counts and advanced stats originate from Pro Football Reference and "
                    "are redistributed via nflverse.",
                ],
            },
            {
                "id": "ffopportunity",
                "name": "ffverse ffopportunity",
                "license": "Not stated on the reference page; package code MIT",
                "url": "https://github.com/ffverse/ffopportunity",
                "in_use": True,
                "text": "Expected fantasy points from ffverse/ffopportunity via nflverse.",
                "used_for": "Expected-points regressors in the model (not displayed directly).",
                "notes": [],
            },
            {
                "id": "ff_playerids",
                "name": "DynastyProcess player ids",
                "license": "via nflverse",
                "url": "https://github.com/dynastyprocess/data",
                "in_use": True,
                "text": "Player id crosswalk from DynastyProcess via nflverse.",
                "used_for": "Matching Sleeper players to nflverse players.",
                "notes": [],
            },
            {
                "id": "sleeper",
                "name": "Sleeper",
                "license": "Sleeper API terms: free for non-commercial use",
                "url": "https://docs.sleeper.com",
                "in_use": True,
                "text": SLEEPER_TEXT,
                "used_for": "League settings, rosters and player status (read only).",
                "notes": ["Trending player data, if shown, is from Sleeper."],
            },
            {
                "id": "ftn",
                "name": "FTN charting",
                "license": "CC-BY-SA-4.0",
                "url": "https://ftndata.com",
                "in_use": bool(ftn_in_use),
                "text": FTN_TEXT,
                "used_for": (
                    "Optional charting features (motion, play action, RPO, screens). Off by "
                    "default (D-009); share-alike applies to derived data we publish."
                ),
                "notes": [],
            },
        ],
        "usage": (
            "Personal, non-commercial use. Commercial use would need Sleeper's permission "
            "(D-013)."
        ),
        "not_displayed": (
            "Expert consensus rankings are used only in private model evaluation and are never "
            "shown in this app."
        ),
        "independence": (
            "An original project. Not affiliated with or endorsed by the NFL, nflverse, FTN, "
            "Sleeper or any fantasy football company."
        ),
    }


MODEL_CARD: dict[str, Any] = {
    "title": "ffml football-first projection chain",
    "what_it_predicts": (
        "Distributions of player stat lines for each NFL week, simulated 2,000 times. "
        "Fantasy points are computed afterwards with each league's own scoring, so any "
        "scoring works without retraining (D-003)."
    ),
    "method": [
        "Team plays and pass/run mix, then each player's share of targets, carries and "
        "dropbacks, then efficiency, then touchdowns, then fantasy points.",
        "Transparent baselines first (rolling average, prior-season average, rolling median), "
        "LightGBM stages kept only where they beat them on walk-forward folds.",
        "Monte Carlo simulation with shared game draws, so a quarterback and his receivers "
        "move together and teammates compete for volume.",
        "Only information available before each week's first kickoff is used; validation is "
        "walk-forward by season and week.",
        "Rest of season: later weeks use a team-strength model instead of betting lines, with "
        "wider uncertainty and persistent injury draws.",
    ],
    "status_summary": (
        "No model is approved. The first candidate (chain_v1) was not approved on its one-time "
        "2025 holdout test: its quarterback 80% ranges were too narrow (tie-aware coverage "
        "0.739, required 0.75 to 0.85); the other 21 of 22 pre-registered checks passed (D-097). "
        "A second version (chain_v2) adds 2025 to training and updates its calibration weekly; "
        "it must pass a pre-registered live test on 2026 weeks before any projections are "
        "published (D-098)."
    ),
    "definitions": {
        "floor": "10th percentile of simulated points under your scoring.",
        "median": "50th percentile.",
        "ceiling": "90th percentile.",
        "boom": (
            "Chance of a top finish at the position that week (QB 6, RB 12, WR 12, TE 6, K 6, "
            "DST 6)."
        ),
        "bust": (
            "Chance of finishing outside the startable range (QB 18, RB 36, WR 48, TE 18, K 20, "
            "DST 20)."
        ),
    },
    "limitations": [
        "Kicker and defense boom/bust probabilities are low confidence (D-089).",
        "Quarterback boom/bust probabilities were miscalibrated on the 2025 holdout (D-097).",
        "Historical betting lines are closing lines, slightly optimistic for midweek "
        "projections (D-008).",
        "Injury designations have no timestamps in the source data; the model lags them and "
        "the latest report caps availability at prediction time (D-006).",
        "Projections are probabilities, not guarantees.",
    ],
    "documentation": ["docs/MODELING.md", "docs/DECISIONS.md (D-097 to D-099)"],
}


SEASON_BANNERS = {
    "postseason": (
        "The regular season is over. Weekly projections, start/sit and waivers are paused; "
        "rankings are season-level until next season."
    ),
    "offseason": (
        "Offseason: no regular-season games remain. Weekly features are paused and rankings are "
        "season-level until next season."
    ),
    "unknown": "The schedule is not available yet, so the current week cannot be determined.",
}


__all__ = ["MODEL_CARD", "SEASON_BANNERS", "attribution"]
