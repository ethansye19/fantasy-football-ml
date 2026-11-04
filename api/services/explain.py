"""Plain-language explanations of the chain's driver contributions (BE-4.4).

The snapshot's ``player_summary.parquet`` carries, per player and reference
scoring, signed point contributions relative to a position-average player
(``ffml.models.chain.drivers``: environment, opportunity, efficiency, matchup,
availability). The API picks the reference scoring closest to the league
(``preset_like``) and turns the largest contributions into short sentences.
The numbers explain the projection's shape; they do not add up to it exactly.
"""

from __future__ import annotations

import json
from typing import Any

GROUPS = ("opportunity", "efficiency", "matchup", "environment", "availability")
MIN_POINTS = 0.25
MAX_DRIVERS = 3

_VOLUME = {
    "QB": "dropbacks and designed runs",
    "RB": "carries and targets",
    "WR": "targets",
    "TE": "targets",
    "K": "field-goal and extra-point chances",
}


def parse_drivers(raw: Any) -> dict[str, float]:
    """``{group: points}`` from the JSON column (missing or malformed gives ``{}``)."""
    if raw is None:
        return {}
    try:
        data = json.loads(raw) if isinstance(raw, str) else dict(raw)
    except (TypeError, ValueError):
        return {}
    out: dict[str, float] = {}
    for g in GROUPS:
        v = data.get(g)
        if isinstance(v, int | float) and v == v:
            out[g] = float(v)
    return out


def sentence(group: str, points: float, position: str | None) -> str:
    pos = position or "player"
    who = "an average defense" if pos == "DST" else f"an average {pos}"
    amount = f"{points:+.1f} pts vs {who}"
    up = points > 0
    if pos == "DST":
        texts = {
            "environment": (
                "The opponent is expected to score and gain little"
                if up
                else "The opponent is expected to score and move the ball well"
            ),
            "efficiency": (
                "Projected for more sacks, takeaways and return scores"
                if up
                else "Projected for fewer sacks, takeaways and return scores"
            ),
            "matchup": "Favorable matchup" if up else "Tough matchup",
            "opportunity": "More chances to score" if up else "Fewer chances to score",
            "availability": "Availability risk",
        }
    else:
        volume = _VOLUME.get(pos, "touches")
        texts = {
            "opportunity": (
                f"Gets a larger share of his team's {volume}"
                if up
                else f"Gets a smaller share of his team's {volume}"
            ),
            "efficiency": (
                "Does more with each opportunity" if up else "Does less with each opportunity"
            ),
            "matchup": "Favorable opponent" if up else "Tough opponent",
            "environment": (
                "His team is expected to run more plays and score more"
                if up
                else "His team is expected to run fewer plays and score less"
            ),
            "availability": "Some risk that he does not play",
        }
    if group == "availability":
        return f"{texts['availability']} ({points:+.1f} expected pts)."
    return f"{texts.get(group, group.capitalize())} ({amount})."


def driver_list(raw: Any, position: str | None) -> list[dict[str, Any]]:
    """The largest contributions, largest first, as ``{group, points, text}``."""
    values = parse_drivers(raw)
    items = sorted(
        ((g, v) for g, v in values.items() if abs(v) >= MIN_POINTS),
        key=lambda gv: abs(gv[1]),
        reverse=True,
    )[:MAX_DRIVERS]
    return [
        {"group": g, "points": round(v, 2), "text": sentence(g, v, position)} for g, v in items
    ]


def compare(a_name: str, a_raw: Any, b_name: str, b_raw: Any) -> str | None:
    """One sentence on where A's edge over B comes from (largest driver difference)."""
    a, b = parse_drivers(a_raw), parse_drivers(b_raw)
    diffs = {g: a.get(g, 0.0) - b.get(g, 0.0) for g in GROUPS if g in a or g in b}
    if not diffs:
        return None
    group, diff = max(diffs.items(), key=lambda gv: gv[1])
    if diff < MIN_POINTS:
        return None
    label = {
        "opportunity": "opportunity (a bigger share of the work)",
        "efficiency": "efficiency (more per opportunity)",
        "matchup": "the matchup",
        "environment": "his team's expected volume and scoring",
        "availability": "a better chance of playing",
    }[group]
    return f"{a_name}'s edge over {b_name} comes mostly from {label}: {diff:+.1f} pts."


__all__ = ["GROUPS", "compare", "driver_list", "parse_drivers", "sentence"]
