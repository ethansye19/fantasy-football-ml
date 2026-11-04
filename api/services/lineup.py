"""Optimal weekly lineups under a league's roster slots (exact assignment, scipy).

Slots follow Sleeper names; IDP slots are ignored in the MVP (DATA_SCHEMA.md
section 5). A player fills at most one slot; empty slots score zero. The
assignment is solved exactly with :func:`scipy.optimize.linear_sum_assignment`
(dedicated slots and every flex combination, including WRRB_FLEX and
REC_FLEX together, are handled without greedy ordering issues).
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence

import numpy as np
from scipy.optimize import linear_sum_assignment

ELIGIBLE: dict[str, frozenset[str]] = {
    "QB": frozenset({"QB"}),
    "RB": frozenset({"RB"}),
    "WR": frozenset({"WR"}),
    "TE": frozenset({"TE"}),
    "K": frozenset({"K"}),
    "DEF": frozenset({"DST"}),
    "FLEX": frozenset({"RB", "WR", "TE"}),
    "SUPER_FLEX": frozenset({"QB", "RB", "WR", "TE"}),
    "WRRB_FLEX": frozenset({"WR", "RB"}),
    "REC_FLEX": frozenset({"WR", "TE"}),
}
_NEG = -1e9


def lineup_slots(roster_positions: Sequence[str]) -> list[str]:
    """Starting slots the optimizer fills (bench, IR, taxi and IDP slots dropped)."""
    return [s for s in roster_positions if s in ELIGIBLE]


def eligible_positions(slots: Sequence[str]) -> frozenset[str]:
    out: set[str] = set()
    for s in slots:
        out |= ELIGIBLE[s]
    return frozenset(out)


def optimal(
    slots: Sequence[str],
    players: Sequence[str],
    positions: Mapping[str, str],
    values: Mapping[str, float],
) -> tuple[float, dict[str, str]]:
    """Best total and ``{player_id: slot}`` for one week.

    ``values`` are expected points (missing means 0, e.g. a bye); negative
    values are treated as 0 because a slot can stay empty.
    """
    if not slots or not players:
        return 0.0, {}
    n_s, n_p = len(slots), len(players)
    m = np.full((n_s, n_p + n_s), 0.0)  # n_s dummy "empty" columns worth 0
    for j, pid in enumerate(players):
        pos = positions.get(pid)
        v = max(float(values.get(pid, 0.0)), 0.0)
        for i, slot in enumerate(slots):
            m[i, j] = v if pos in ELIGIBLE[slot] else _NEG
    rows, cols = linear_sum_assignment(m, maximize=True)
    total = 0.0
    chosen: dict[str, str] = {}
    for i, j in zip(rows, cols, strict=True):
        if j < n_p and m[i, j] > _NEG / 2 and m[i, j] > 0:
            total += m[i, j]
            chosen[players[j]] = slots[i]
    return total, chosen


def season_total(
    slots: Sequence[str],
    players: Sequence[str],
    positions: Mapping[str, str],
    weekly: Mapping[int, Mapping[str, float]],
) -> tuple[float, dict[int, float], dict[str, int]]:
    """Sum of optimal lineups over weeks; per-week totals and starts per player."""
    totals: dict[int, float] = {}
    starts: dict[str, int] = {}
    for week, values in weekly.items():
        total, chosen = optimal(slots, players, positions, values)
        totals[week] = total
        for pid in chosen:
            starts[pid] = starts.get(pid, 0) + 1
    return sum(totals.values()), totals, starts


__all__ = ["ELIGIBLE", "eligible_positions", "lineup_slots", "optimal", "season_total"]
