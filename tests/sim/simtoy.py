"""Toy slates for the simulation tests (helpers imported by the test modules)."""

from __future__ import annotations

import polars as pl

GAME = "2026_09_AWY_HOM"

ROSTER = (
    # suffix, position, target_share, carry_share, dropback_share
    ("QB1", "QB", 0.0, 0.10, 1.0),
    ("RB1", "RB", 0.10, 0.60, 0.0),
    ("RB2", "RB", 0.0, 0.30, 0.0),
    ("WR1", "WR", 0.35, 0.0, 0.0),
    ("WR2", "WR", 0.30, 0.0, 0.0),
    ("TE1", "TE", 0.25, 0.0, 0.0),
    ("K1", "K", 0.0, 0.0, 0.0),
)


def toy_team_params(total_sd: float = 10.0, margin_sd: float = 13.5) -> pl.DataFrame:
    rows = []
    for team, opp, home, spread in (("HOM", "AWY", True, 3.0), ("AWY", "HOM", False, -3.0)):
        rows.append(
            {
                "game_id": GAME,
                "season": 2026,
                "week": 9,
                "team": team,
                "opponent": opp,
                "home": home,
                "exp_plays": 64.0,
                "exp_pass_rate": 0.6,
                "exp_off_td": 2.6,
                "exp_fg_att": 1.8,
                "spread_line": spread,
                "total_line": 46.0,
                "total_sd": total_sd,
                "margin_sd": margin_sd,
            }
        )
    return pl.DataFrame(rows)


def toy_player_params(**overrides: dict) -> pl.DataFrame:
    """Both teams' players; ``overrides`` maps an entity_id to column overrides."""
    rows = []
    for team in ("HOM", "AWY"):
        for suffix, pos, tgt, car, dbk in ROSTER:
            entity = f"{team}-{suffix}"
            row = {
                "entity_id": entity,
                "game_id": GAME,
                "team": team,
                "position": pos,
                "target_share": tgt,
                "carry_share": car,
                "dropback_share": dbk,
                "pass_td_share": tgt,
                "rush_td_share": car,
            }
            row.update(overrides.get(entity, {}))
            rows.append(row)
    return pl.DataFrame(rows)


def toy_dst_params() -> pl.DataFrame:
    return pl.DataFrame({"team": ["HOM", "AWY"], "game_id": [GAME, GAME]})
