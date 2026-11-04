"""Recommendation views over a scored run (BE-4.3 to BE-4.7, docs/API.md section 7).

Every function returns plain dicts; the router wraps them in the approval
envelope. Lineup values are each player's expected points per week
(unconditional on playing), so availability risk lowers them.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from typing import Any

import numpy as np
import polars as pl

from api.db.models import League
from api.envelope import NO_GUARANTEES
from api.errors import ApiError
from api.services import explain
from api.services.lineup import ELIGIBLE, eligible_positions, lineup_slots, season_total
from api.services.players import PlayerDirectory
from api.services.projection import Scored, drivers_raw

ACTIVE_SLOTS = ("starter", "bench")
SORTS = {
    "median": ("median", True),
    "mean": ("mean", True),
    "ceiling": ("ceiling", True),
    "floor": ("floor", True),
    "boom": ("boom_prob", True),
    "bust": ("bust_prob", False),
}
FA_CANDIDATES_PER_POSITION = 25
DROP_CHOICES = 3


# --------------------------------------------------------------------------- rosters


@dataclass
class Rosters:
    """Who holds whom in a league (player ids are gsis ids or team codes)."""

    owner: dict[str, dict[str, Any]]  # player_id -> {league_team_id, team_name, is_mine, slot}
    teams: dict[str, dict[str, Any]]  # league_team_id -> {name, players: [(pid, slot)]}
    my_team_id: str | None

    @classmethod
    def of(cls, league: League | None) -> Rosters:
        owner: dict[str, dict[str, Any]] = {}
        teams: dict[str, dict[str, Any]] = {}
        mine = None
        for team in league.teams if league is not None else []:
            tid = str(team.id)
            if team.is_me:
                mine = tid
            players = []
            for s in team.slots:
                if not s.player_id:
                    continue
                players.append((s.player_id, s.slot, s.position))
                owner[s.player_id] = {
                    "league_team_id": tid,
                    "team_name": team.team_name,
                    "is_mine": bool(team.is_me),
                    "slot": s.slot,
                }
            teams[tid] = {"name": team.team_name, "players": players}
        return cls(owner=owner, teams=teams, my_team_id=mine)

    def team(self, team_id: str | None, code: str = "no_team_selected") -> str:
        tid = team_id or self.my_team_id
        if tid is None:
            raise ApiError(
                409, code, "Choose your team first (PUT /leagues/{id}/my-team) or pass league_team_id."
            )
        if tid not in self.teams:
            raise ApiError(404, "team_not_found", f"No team {tid} in this league.")
        return tid

    def lineup_pool(self, team_id: str) -> list[str]:
        return [p for p, slot, _ in self.teams[team_id]["players"] if slot in ACTIVE_SLOTS]


# --------------------------------------------------------------------------- rows


def _display(directory: PlayerDirectory, pid: str) -> dict[str, Any]:
    info = directory.info(pid)
    return {
        "name": info.name if info and info.name else pid,
        "sleeper_id": info.sleeper_id if info else None,
        "injury_status": info.injury_status if info else None,
    }


def player_row(
    scored: Scored,
    row: dict[str, Any],
    directory: PlayerDirectory,
    rosters: Rosters | None,
) -> dict[str, Any]:
    pid = row["player_id"]
    shown = _display(directory, pid)
    owner = rosters.owner.get(pid) if rosters is not None else None
    raw = drivers_raw(scored.run, pid, scored.preset_like)
    return {
        "player_id": pid,
        "sleeper_id": shown["sleeper_id"],
        "name": shown["name"],
        "position": row["position"],
        "team": row["team"],
        "opponent": row.get("opponent"),
        "game_id": row.get("game_id"),
        "floor": row["floor"],
        "median": row["median"],
        "ceiling": row["ceiling"],
        "mean": row["mean"],
        "expected": row["expected"],
        "boom_prob": row["boom_prob"],
        "bust_prob": row["bust_prob"],
        "active_prob": row["active_prob"],
        "boom_bust_confidence": row["boom_bust_confidence"],
        "low_sample": bool(row["low_sample"]),
        "position_rank": row["position_rank"],
        "drivers": explain.driver_list(raw, row["position"]),
        "injury_status": shown["injury_status"],
        "roster": (
            {k: owner[k] for k in ("league_team_id", "team_name", "is_mine")} if owner else None
        ),
    }


def _filter(
    frame: pl.DataFrame,
    *,
    position: str | None,
    availability: str,
    rosters: Rosters | None,
    search: str | None,
    directory: PlayerDirectory,
) -> pl.DataFrame:
    if position:
        frame = frame.filter(pl.col("position") == position)
    if rosters is not None and availability != "all":
        rostered = list(rosters.owner)
        mine = [p for p, o in rosters.owner.items() if o["is_mine"]]
        if availability == "rostered":
            frame = frame.filter(pl.col("player_id").is_in(rostered))
        elif availability == "free_agents":
            frame = frame.filter(~pl.col("player_id").is_in(rostered))
        elif availability == "mine":
            frame = frame.filter(pl.col("player_id").is_in(mine))
    if search:
        needle = search.strip().lower()
        keep = [
            pid
            for pid in frame["player_id"].to_list()
            if needle in (_display(directory, pid)["name"] or "").lower() or needle in pid.lower()
        ]
        frame = frame.filter(pl.col("player_id").is_in(keep))
    return frame


def projections(
    scored: Scored,
    directory: PlayerDirectory,
    rosters: Rosters | None,
    *,
    position: str | None,
    availability: str,
    search: str | None,
    sort: str,
    limit: int,
    offset: int,
) -> dict[str, Any]:
    column, desc = SORTS[sort]
    frame = _filter(
        scored.table,
        position=position,
        availability=availability,
        rosters=rosters,
        search=search,
        directory=directory,
    ).sort([column, "player_id"], descending=[desc, False], nulls_last=True)
    page = frame.slice(offset, limit)
    return {
        "season": scored.run.season,
        "week": scored.run.week,
        "total": frame.height,
        "players": [player_row(scored, r, directory, rosters) for r in page.iter_rows(named=True)],
    }


# --------------------------------------------------------------------------- start/sit


def start_sit(
    scored: Scored, directory: PlayerDirectory, rosters: Rosters | None, ids: list[str]
) -> dict[str, Any]:
    run = scored.run
    missing, idx = [], []
    for pid in ids:
        i = run.index.get(pid)
        if i is None or not run.in_week[i]:
            missing.append(pid)
        else:
            idx.append(i)
    if missing:
        raise ApiError(
            404,
            "player_not_found",
            "No projection this week for some players (unknown id, or no game this week).",
            {"player_ids": missing, "week": run.week},
        )
    pts = scored.points[idx].astype(np.float64)  # paired by simulation index
    k = len(ids)
    pairwise = []
    p_ab = np.zeros((k, k))
    for a in range(k):
        for b in range(k):
            if a != b:
                p_ab[a, b] = float(np.mean(pts[a] > pts[b]) + 0.5 * np.mean(pts[a] == pts[b]))
    for a in range(k):
        for b in range(a + 1, k):
            pairwise.append({"a": ids[a], "b": ids[b], "p_a_beats_b": round(p_ab[a, b], 4)})
    best = pts.max(axis=0)
    winners = pts == best
    share = winners / winners.sum(axis=0)
    p_high = share.mean(axis=1)
    order = list(np.argsort(-p_high, kind="stable"))
    top, second = order[0], order[1]
    edge = p_ab[top, second]
    strength = "toss-up" if edge < 0.55 else "lean" if edge <= 0.70 else "clear"
    rows = {r["player_id"]: r for r in scored.table.filter(pl.col("player_id").is_in(ids)).iter_rows(named=True)}
    players = [player_row(scored, rows[pid], directory, rosters) for pid in ids]
    name = {p["player_id"]: p["name"] for p in players}
    a_id, b_id = ids[top], ids[second]
    verb = {"toss-up": "Toss-up", "lean": "Lean", "clear": "Start"}[strength]
    text = (
        f"{verb} {name[a_id]}: he outscores {name[b_id]} in {edge:.0%} of simulations."
        if strength != "toss-up"
        else f"Toss-up between {name[a_id]} and {name[b_id]}: {name[a_id]} comes out ahead in "
        f"{edge:.0%} of simulations."
    )
    explanation: list[str] = []
    cmp = explain.compare(
        name[a_id],
        drivers_raw(run, a_id, scored.preset_like),
        name[b_id],
        drivers_raw(run, b_id, scored.preset_like),
    )
    if cmp:
        explanation.append(cmp)
    ra, rb = rows[a_id], rows[b_id]
    if ra["ceiling"] is not None and rb["ceiling"] is not None and rb["ceiling"] > ra["ceiling"]:
        explanation.append(
            f"{name[b_id]} has the higher ceiling ({rb['ceiling']:.1f} vs {ra['ceiling']:.1f}), "
            f"so he is the riskier, higher-upside choice."
        )
    if ra["floor"] is not None and rb["floor"] is not None and ra["floor"] > rb["floor"]:
        explanation.append(
            f"{name[a_id]} also has the safer floor ({ra['floor']:.1f} vs {rb['floor']:.1f})."
        )
    for pid in (a_id, b_id):
        ap = rows[pid]["active_prob"]
        if ap is not None and ap < 0.9:
            explanation.append(f"{name[pid]} plays in only {ap:.0%} of simulations (injury risk).")
    return {
        "week": run.week,
        "players": players,
        "pairwise": pairwise,
        "p_highest": {pid: round(float(p_high[i]), 4) for i, pid in enumerate(ids)},
        "recommendation": {"player_id": a_id, "strength": strength, "text": text},
        "explanation": explanation,
        "disclaimer": NO_GUARANTEES,
    }


# --------------------------------------------------------------------------- values


def _positions(scored: Scored, rosters: Rosters | None) -> dict[str, str]:
    out = dict(zip(scored.run.entities, scored.run.positions, strict=True))
    if rosters is not None:
        for team in rosters.teams.values():
            for pid, _slot, pos in team["players"]:
                if pid not in out and pos:
                    out[pid] = pos
    return out


def replacement_levels(
    scored: Scored, league: League, rosters: Rosters, ros: pl.DataFrame
) -> dict[str, float]:
    """Best free agent's ROS mean per position; rank fallback without roster data."""
    slots = lineup_slots(league.roster_positions or [])
    positions = sorted(eligible_positions(slots))
    rostered = set(rosters.owner)
    share: dict[str, float] = {p: 0.0 for p in positions}
    for slot in slots:
        elig = [p for p in ELIGIBLE[slot] if p in share]
        for p in elig:
            share[p] += 1.0 / len(elig)
    out: dict[str, float] = {}
    for pos in positions:
        part = ros.filter(pl.col("position") == pos).sort("ros_mean", descending=True)
        if part.height == 0:
            out[pos] = 0.0
            continue
        fa = part.filter(~pl.col("player_id").is_in(list(rostered)))
        has_roster_data = any(p in rostered for p in part["player_id"].to_list())
        if has_roster_data and fa.height:
            out[pos] = round(float(fa["ros_mean"][0]), 2)
        else:
            rank = int(round(league.num_teams * share.get(pos, 0.0)))
            out[pos] = round(float(part["ros_mean"][min(rank, part.height - 1)]), 2)
    return out


def ros_rankings(
    scored: Scored,
    directory: PlayerDirectory,
    league: League | None,
    rosters: Rosters | None,
    *,
    position: str | None,
    availability: str,
    limit: int,
    offset: int,
    include_current: bool,
) -> dict[str, Any]:
    ros = scored.ros_table(include_current)
    if league is not None and rosters is not None:
        repl = replacement_levels(scored, league, rosters, ros)
    else:
        repl = _preset_replacement(ros)
    ros = ros.with_columns(
        (pl.col("ros_mean") - pl.col("position").replace_strict(repl, default=0.0))
        .round(2)
        .alias("value_over_replacement")
    ).with_columns(
        pl.col("value_over_replacement")
        .rank(method="ordinal", descending=True)
        .cast(pl.Int32)
        .alias("overall_rank"),
        pl.col("ros_mean")
        .rank(method="ordinal", descending=True)
        .over("position")
        .cast(pl.Int32)
        .alias("position_rank"),
    )
    frame = _filter(
        ros, position=position, availability=availability, rosters=rosters, search=None,
        directory=directory,
    ).sort(["overall_rank"])
    page = frame.slice(offset, limit)
    players = []
    for r in page.iter_rows(named=True):
        owner = rosters.owner.get(r["player_id"]) if rosters is not None else None
        shown = _display(directory, r["player_id"])
        players.append(
            {
                "player_id": r["player_id"],
                "sleeper_id": shown["sleeper_id"],
                "name": shown["name"],
                "position": r["position"],
                "team": r["team"],
                "ros_mean": round(r["ros_mean"], 2),
                "ros_floor": round(r["ros_floor"], 2),
                "ros_median": round(r["ros_median"], 2),
                "ros_ceiling": round(r["ros_ceiling"], 2),
                "games_remaining": r["games_remaining"],
                "bye_weeks": r["bye_weeks"],
                "value_over_replacement": r["value_over_replacement"],
                "overall_rank": r["overall_rank"],
                "position_rank": r["position_rank"],
                "roster": (
                    {k: owner[k] for k in ("league_team_id", "team_name", "is_mine")}
                    if owner
                    else None
                ),
            }
        )
    weeks = scored.weeks(include_current)
    return {
        "first_week": min(weeks) if weeks else scored.run.week,
        "last_week": max(weeks) if weeks else scored.run.week,
        "replacement_level": repl,
        "total": frame.height,
        "players": players,
    }


def _preset_replacement(ros: pl.DataFrame) -> dict[str, float]:
    """Preset routes: a 12-team 1QB/2RB/2WR/1TE/1FLEX/K/DST league."""
    ranks = {"QB": 12, "RB": 30, "WR": 30, "TE": 14, "K": 12, "DST": 12}
    out = {}
    for pos, rank in ranks.items():
        part = ros.filter(pl.col("position") == pos).sort("ros_mean", descending=True)
        out[pos] = round(float(part["ros_mean"][min(rank, part.height - 1)]), 2) if part.height else 0.0
    return out


# --------------------------------------------------------------------------- waivers


def _brief(directory: PlayerDirectory, pid: str, pos: str | None, team: str | None) -> dict[str, Any]:
    return {"player_id": pid, "name": _display(directory, pid)["name"], "position": pos, "team": team}


def waivers(
    scored: Scored,
    directory: PlayerDirectory,
    league: League,
    rosters: Rosters,
    *,
    team_id: str | None,
    position: str | None,
    limit: int,
) -> dict[str, Any]:
    tid = rosters.team(team_id)
    slots = lineup_slots(league.roster_positions or [])
    eligible = eligible_positions(slots)
    positions = _positions(scored, rosters)
    weeks = scored.weeks(True)
    weekly = {w: scored.weekly.get(w, {}) for w in weeks}
    week = scored.run.week
    pool = rosters.lineup_pool(tid)
    capacity = sum(1 for s in league.roster_positions or [] if s not in ("IR", "TAXI"))
    capacity -= sum(1 for s in league.roster_positions or [] if s in ("DL", "LB", "DB", "IDP_FLEX"))
    open_spot = len(pool) < capacity

    base_total, base_weeks, _ = season_total(slots, pool, positions, weekly)
    losses = []
    for pid in pool:
        rest = [p for p in pool if p != pid]
        total, _, _ = season_total(slots, rest, positions, weekly)
        losses.append((base_total - total, pid))
    losses.sort()
    drops = [pid for _, pid in losses[:DROP_CHOICES]]
    loss_of = {pid: loss for loss, pid in losses}

    ros = scored.ros_table(True)
    rostered = list(rosters.owner)
    fa = ros.filter(~pl.col("player_id").is_in(rostered) & pl.col("position").is_in(list(eligible)))
    if position:
        fa = fa.filter(pl.col("position") == position)
    fa = (
        fa.sort("ros_mean", descending=True)
        .group_by("position", maintain_order=True)
        .head(FA_CANDIDATES_PER_POSITION)
    )
    table = {r["player_id"]: r for r in scored.table.iter_rows(named=True)}
    options = []
    for cand in fa.iter_rows(named=True):
        add = cand["player_id"]
        choices: list[str | None] = [None] if open_spot else list(drops)
        best: tuple[float, float, str | None, dict[int, float], dict[str, int]] | None = None
        for drop in choices:
            roster = [p for p in pool if p != drop] + [add]
            total, per_week, starts = season_total(slots, roster, positions, weekly)
            gain = total - base_total
            if best is None or gain > best[0]:
                best = (gain, per_week.get(week, 0.0) - base_weeks.get(week, 0.0), drop, per_week, starts)
        assert best is not None
        gain, gain_next, drop, _, starts = best
        if gain <= 0.05:
            continue
        n_start = starts.get(add, 0)
        row = table.get(add)
        options.append(
            {
                "add": (
                    player_row(scored, row, directory, rosters)
                    if row is not None
                    else _brief(directory, add, cand["position"], cand["team"])
                ),
                "drop": (
                    {
                        **_brief(directory, drop, positions.get(drop), None),
                        "lineup_loss": round(loss_of.get(drop, 0.0), 2),
                    }
                    if drop
                    else None
                ),
                "gain_next_week": round(gain_next, 2),
                "gain_rest_of_season": round(gain, 2),
                "score": round(gain + gain_next, 2),
                "starts": n_start,
                "reason": (
                    f"Would start in {n_start} of {len(weeks)} remaining weeks; adds "
                    f"{gain:.1f} expected lineup points"
                    + (f" ({gain_next:+.1f} this week)." if abs(gain_next) >= 0.05 else ".")
                ),
            }
        )
    options.sort(key=lambda o: (-o["score"], o["add"]["player_id"]))
    return {
        "league_team_id": tid,
        "week": week,
        "weeks": weeks,
        "open_roster_spot": open_spot,
        "options": options[:limit],
        "drop_candidates": [
            {**_brief(directory, pid, positions.get(pid), None), "lineup_loss": round(loss, 2)}
            for loss, pid in losses[:DROP_CHOICES]
        ],
        "disclaimer": NO_GUARANTEES,
    }


# --------------------------------------------------------------------------- trades


def _fairness(net: float, give: float, receive: float) -> dict[str, Any]:
    scale = max(abs(give), abs(receive), 10.0)
    size = abs(net)
    if size < 5.0 or size <= 0.10 * scale:
        band = "fair"
        text = f"Roughly even: the sides differ by about {size:.0f} rest-of-season points over replacement."
    else:
        lopsided = size > 0.35 * scale
        who = "you" if net > 0 else "partner"
        band = f"{'lopsided_for' if lopsided else 'favors'}_{who}"
        side = "you" if net > 0 else "your trade partner"
        word = "strongly favors" if lopsided else "favors"
        text = (
            f"This trade {word} {side} by about {size:.0f} rest-of-season points over replacement."
        )
    return {"band": band, "difference": round(net, 2), "text": text}


def _owned_by(rosters: Rosters, ids: Iterable[str], team_id: str) -> list[str]:
    held = {p for p, _s, _pos in rosters.teams[team_id]["players"]}
    return [p for p in ids if p not in held]


def trade(
    scored: Scored,
    directory: PlayerDirectory,
    league: League,
    rosters: Rosters,
    *,
    give: list[str],
    receive: list[str],
    team_id: str | None,
    partner_id: str | None,
) -> dict[str, Any]:
    mine = rosters.team(team_id)
    if partner_id is None:
        owners = {rosters.owner.get(p, {}).get("league_team_id") for p in receive}
        if len(owners) != 1 or None in owners:
            raise ApiError(
                422,
                "invalid_trade",
                "The players you receive must all be on one other roster (or pass partner_team_id).",
            )
        partner_id = owners.pop()
    partner = rosters.team(partner_id, code="team_not_found")
    if partner == mine:
        raise ApiError(422, "invalid_trade", "A trade needs two different teams.")
    bad_give = _owned_by(rosters, give, mine)
    bad_recv = _owned_by(rosters, receive, partner)
    if bad_give or bad_recv:
        raise ApiError(
            422,
            "invalid_trade",
            "Some players are not on the expected roster.",
            {"not_on_your_roster": bad_give, "not_on_partner_roster": bad_recv},
        )
    slots = lineup_slots(league.roster_positions or [])
    positions = _positions(scored, rosters)
    weeks = scored.weeks(True)
    weekly = {w: scored.weekly.get(w, {}) for w in weeks}
    ros = scored.ros_table(True)
    repl = replacement_levels(scored, league, rosters, ros)
    ros_of = {r["player_id"]: r for r in ros.iter_rows(named=True)}
    table = {r["player_id"]: r for r in scored.table.iter_rows(named=True)}

    def side(ids: list[str]) -> tuple[list[dict[str, Any]], float]:
        out, total = [], 0.0
        for pid in ids:
            r = ros_of.get(pid)
            pos = positions.get(pid)
            mean = float(r["ros_mean"]) if r else 0.0
            vor = mean - repl.get(pos or "", 0.0)
            total += max(vor, 0.0)  # a player below replacement can be replaced for free
            row = table.get(pid)
            out.append(
                {
                    "player": (
                        player_row(scored, row, directory, rosters)
                        if row is not None
                        else _brief(directory, pid, pos, r["team"] if r else None)
                    ),
                    "ros_mean": round(mean, 2),
                    "value_over_replacement": round(vor, 2),
                    "trade_value": round(max(vor, 0.0), 2),
                }
            )
        return out, total

    give_rows, give_vor = side(give)
    recv_rows, recv_vor = side(receive)

    def fit(team: str, out_ids: list[str], in_ids: list[str]) -> dict[str, float]:
        pool = rosters.lineup_pool(team)
        before, _, _ = season_total(slots, pool, positions, weekly)
        after_pool = [p for p in pool if p not in out_ids] + list(in_ids)
        after, _, _ = season_total(slots, after_pool, positions, weekly)
        return {"before": round(before, 2), "after": round(after, 2), "change": round(after - before, 2)}

    net = recv_vor - give_vor
    fairness = _fairness(net, give_vor, recv_vor)
    my_fit = fit(mine, give, receive)
    verdict = fairness["text"] + (
        f" Your best weekly lineups would gain {my_fit['change']:.1f} points over weeks "
        f"{weeks[0]}-{weeks[-1]}."
        if my_fit["change"] >= 0
        else f" Your best weekly lineups would lose {-my_fit['change']:.1f} points over weeks "
        f"{weeks[0]}-{weeks[-1]}."
    )
    return {
        "league_team_id": mine,
        "partner_team_id": partner,
        "weeks": weeks,
        "give": give_rows,
        "receive": recv_rows,
        "value": {"give": round(give_vor, 2), "receive": round(recv_vor, 2), "net": round(net, 2)},
        "roster_fit": {"mine": my_fit, "partner": fit(partner, receive, give)},
        "replacement_level": repl,
        "fairness": fairness,
        "verdict_text": verdict,
        "disclaimer": NO_GUARANTEES,
    }


__all__ = [
    "Rosters",
    "projections",
    "replacement_levels",
    "ros_rankings",
    "start_sit",
    "trade",
    "waivers",
]
