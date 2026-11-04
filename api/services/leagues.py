"""League logic: Sleeper import and sync, manual leagues, settings, serialization (BE-4.2).

Scoring is stored as the normalized Sleeper key map (zero entries removed,
D-024) and turned into :class:`ffml.scoring.rules.ScoringRules` at request
time; roster slots are validated with
:func:`ffml.data.sleeper.normalize_roster_positions`. Nothing is written to
Sleeper (D-020).
"""

from __future__ import annotations

import logging
import uuid
from collections import Counter
from collections.abc import Callable, Iterable
from datetime import UTC, datetime
from typing import Any

import httpx
from sqlalchemy import select
from sqlalchemy.orm import Session, selectinload

from api.db.models import League, LeagueRosterSlot, LeagueTeam
from api.errors import ApiError
from api.schemas import ManualLeagueRequest, ScoringInput
from api.services.players import PlayerDirectory, PlayerInfo
from ffml.data.sleeper import (
    SleeperClient,
    SleeperError,
    SleeperLeague,
    SleeperNotFound,
    normalize_roster_positions,
)
from ffml.scoring.rules import PRESETS, ScoringRules

log = logging.getLogger(__name__)

NON_STARTING = frozenset({"BN", "IR", "TAXI"})
IDP_SLOTS = frozenset({"DL", "LB", "DB", "IDP_FLEX"})
#: League settings copied from Sleeper (others are not used by the app).
SLEEPER_SETTINGS_KEYS = (
    "num_teams",
    "playoff_week_start",
    "playoff_teams",
    "start_week",
    "leg",
    "last_scored_leg",
    "trade_deadline",
    "waiver_type",
    "waiver_budget",
    "reserve_slots",
    "taxi_slots",
    "best_ball",
    "type",
)
SLOT_KINDS = ("starter", "bench", "ir", "taxi")


# --------------------------------------------------------------------------- scoring


def rules_for(league: League) -> ScoringRules:
    return ScoringRules.from_sleeper(league.scoring_settings or {}, name=str(league.id))


def preset_like(rules: ScoringRules) -> str:
    rec = rules.weights.get("rec", 0.0)
    return "ppr" if rec >= 0.75 else "half" if rec >= 0.25 else "std"


def scoring_from_input(scoring: ScoringInput) -> ScoringRules:
    """``{"preset", "overrides"}`` or ``{"settings"}``; 422 ``invalid_scoring`` otherwise."""
    if scoring.settings is not None and (scoring.preset or scoring.overrides):
        raise ApiError(422, "invalid_scoring", "Give either settings or a preset with overrides.")
    if scoring.settings is not None:
        base: dict[str, float] = dict(scoring.settings)
    elif scoring.preset:
        base = {**PRESETS[scoring.preset], **scoring.overrides}
    else:
        raise ApiError(422, "invalid_scoring", "Scoring needs a preset or settings.")
    try:
        return ScoringRules.from_dict(base)
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "invalid_scoring", str(exc)) from exc


def roster_positions_from_input(positions: Iterable[str]) -> list[str]:
    try:
        cleaned = normalize_roster_positions(list(positions))
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "invalid_roster_positions", str(exc)) from exc
    if not any(p not in NON_STARTING and p not in IDP_SLOTS for p in cleaned):
        raise ApiError(422, "invalid_roster_positions", "The roster has no starting slot.")
    return cleaned


def starting_slots(roster_positions: Iterable[str]) -> dict[str, int]:
    """Starting slot counts in roster order (bench, IR, taxi and IDP slots excluded)."""
    counts = Counter(p for p in roster_positions if p not in NON_STARTING and p not in IDP_SLOTS)
    order = [p for p in dict.fromkeys(roster_positions) if p in counts]
    return {p: counts[p] for p in order}


def scoring_payload(rules: ScoringRules) -> dict[str, Any]:
    return {
        "settings": rules.weights,
        "hash": rules.scoring_hash,
        "unsupported_keys": list(rules.unsupported_keys),
        "preset_like": preset_like(rules),
    }


# --------------------------------------------------------------------------- serialization


def serialize_league(league: League, *, with_teams: bool = True) -> dict[str, Any]:
    rules = rules_for(league)
    mine = next((t for t in league.teams if t.is_me), None)
    out: dict[str, Any] = {
        "id": str(league.id),
        "source": league.source,
        "external_id": league.external_id,
        "name": league.name,
        "season": league.season,
        "num_teams": league.num_teams,
        "scoring": scoring_payload(rules),
        "roster_positions": list(league.roster_positions or []),
        "starting_slots": starting_slots(league.roster_positions or []),
        "settings": dict(league.settings or {}),
        "my_team_id": str(mine.id) if mine else None,
        "created_at": league.created_at,
        "updated_at": league.updated_at,
        "last_synced_at": league.last_synced_at,
    }
    if with_teams:
        out["teams"] = [serialize_team(t) for t in league.teams]
    return out


def serialize_team(team: LeagueTeam) -> dict[str, Any]:
    order = {k: i for i, k in enumerate(SLOT_KINDS)}
    slots = sorted(team.slots, key=lambda s: (order.get(s.slot, 9), s.position or "", s.name or ""))
    return {
        "id": str(team.id),
        "external_roster_id": team.external_roster_id,
        "team_name": team.team_name,
        "owner_name": team.owner_name,
        "owner_external_id": team.owner_external_id,
        "is_me": bool(team.is_me),
        "players": [
            {
                "player_id": s.player_id,
                "sleeper_id": s.sleeper_id,
                "name": s.name,
                "position": s.position,
                "team": s.nfl_team,
                "slot": s.slot,
            }
            for s in slots
        ],
    }


# --------------------------------------------------------------------------- queries


def parse_uuid(value: str, code: str = "league_not_found") -> uuid.UUID:
    try:
        return uuid.UUID(str(value))
    except (TypeError, ValueError) as exc:
        raise ApiError(404, code, f"No such id: {value!r}.") from exc


def get_league(db: Session, league_id: str) -> League:
    league = db.scalars(
        select(League)
        .where(League.id == parse_uuid(league_id))
        .options(selectinload(League.teams).selectinload(LeagueTeam.slots))
    ).first()
    if league is None:
        raise ApiError(404, "league_not_found", f"No league with id {league_id}.")
    return league


def list_leagues(db: Session) -> list[League]:
    return list(
        db.scalars(
            select(League).options(selectinload(League.teams)).order_by(League.created_at.desc())
        )
    )


def team_by_id(league: League, team_id: str) -> LeagueTeam:
    wanted = parse_uuid(team_id, "team_not_found")
    for team in league.teams:
        if team.id == wanted:
            return team
    raise ApiError(404, "team_not_found", f"No team {team_id} in this league.")


def set_my_team(league: League, team_id: str) -> None:
    chosen = team_by_id(league, team_id)
    for team in league.teams:
        team.is_me = team.id == chosen.id


def _slot(info: PlayerInfo, kind: str, now: datetime) -> LeagueRosterSlot:
    return LeagueRosterSlot(
        player_id=info.player_id,
        sleeper_id=info.sleeper_id,
        position=info.position,
        name=info.name,
        nfl_team=info.team,
        slot=kind,
        synced_at=now,
    )


# --------------------------------------------------------------------------- Sleeper


def sleeper_call(fn: Callable[[], Any]) -> Any:
    """Run a Sleeper call, mapping failures to API errors."""
    try:
        return fn()
    except SleeperNotFound as exc:
        raise ApiError(404, "sleeper_not_found", "Sleeper has no such user or league.") from exc
    except (SleeperError, httpx.HTTPError) as exc:
        log.warning("sleeper call failed: %r", exc)
        raise ApiError(
            502, "sleeper_unavailable", "Sleeper could not be reached. Try again shortly."
        ) from exc


def league_brief(lg: SleeperLeague) -> dict[str, Any]:
    try:
        like = preset_like(ScoringRules.from_sleeper(lg.scoring_settings))
    except (TypeError, ValueError):
        like = None
    return {
        "league_id": lg.league_id,
        "name": lg.name,
        "season": lg.season,
        "num_teams": lg.num_teams,
        "status": lg.status,
        "preset_like": like,
    }


def current_sleeper_season(client: SleeperClient) -> int:
    state = sleeper_call(client.get_state)
    return int(state.league_season or state.season)


def user_leagues(
    client: SleeperClient, username: str, season: int | None
) -> tuple[Any, int, list[SleeperLeague]]:
    user = sleeper_call(lambda: client.get_user(username))
    season = season or current_sleeper_season(client)
    leagues = sleeper_call(lambda: client.get_user_leagues(user.user_id, season))
    return user, season, leagues


def _sleeper_settings(lg: SleeperLeague) -> dict[str, Any]:
    out = {k: lg.settings[k] for k in SLEEPER_SETTINGS_KEYS if k in lg.settings}
    out["status"] = lg.status
    return out


def import_sleeper(
    db: Session,
    client: SleeperClient,
    directory: PlayerDirectory,
    *,
    league_id: str | None,
    username: str | None,
    season: int | None,
) -> tuple[League, bool]:
    """Import (or re-sync) a Sleeper league. Returns ``(league, created)``."""
    user_id: str | None = None
    if username:
        user, season_used, leagues = user_leagues(client, username, season)
        user_id = user.user_id
        if league_id is None:
            if not leagues:
                raise ApiError(
                    404, "sleeper_not_found", f"{username} has no Sleeper leagues in {season_used}."
                )
            if len(leagues) > 1:
                raise ApiError(
                    409,
                    "multiple_leagues",
                    f"{username} has {len(leagues)} leagues in {season_used}; choose one.",
                    {"leagues": [league_brief(lg) for lg in leagues]},
                )
            league_id = leagues[0].league_id
    if league_id is None:
        raise ApiError(422, "validation_error", "Give a Sleeper league_id or username.")
    lg = sleeper_call(lambda: client.get_league(league_id))
    rosters = sleeper_call(lambda: client.get_rosters(league_id))
    users = sleeper_call(lambda: client.get_league_users(league_id))

    try:
        rules = ScoringRules.from_sleeper(lg.scoring_settings)
        positions = normalize_roster_positions(lg.roster_positions)
    except (TypeError, ValueError) as exc:
        raise ApiError(422, "invalid_scoring", f"Sleeper league settings: {exc}") from exc

    existing = db.scalars(
        select(League)
        .where(
            League.source == "sleeper",
            League.external_id == lg.league_id,
            League.season == lg.season,
        )
        .options(selectinload(League.teams).selectinload(LeagueTeam.slots))
    ).first()
    created = existing is None
    league = existing or League(source="sleeper", external_id=lg.league_id, season=lg.season)
    now = datetime.now(UTC)
    league.name = lg.name
    league.num_teams = lg.num_teams
    if created:
        # an existing league keeps any custom scoring the user saved (PUT /settings)
        league.scoring_settings = rules.weights
        league.roster_positions = positions
    league.settings = {**(league.settings or {}), **_sleeper_settings(lg)}
    league.last_synced_at = now

    users_by_id = {u.user_id: u for u in users}
    previous_me = {t.external_roster_id for t in league.teams if t.is_me}
    teams_by_roster = {t.external_roster_id: t for t in league.teams}
    seen: set[str] = set()
    for roster in sorted(rosters, key=lambda r: r.roster_id):
        rid = str(roster.roster_id)
        seen.add(rid)
        team = teams_by_roster.get(rid)
        if team is None:
            team = LeagueTeam(external_roster_id=rid, is_me=False)
            league.teams.append(team)
        owner = users_by_id.get(roster.owner_id or "")
        team.owner_external_id = roster.owner_id
        team.owner_name = owner.display_name if owner else None
        team.team_name = (owner.team_name if owner else None) or (
            f"Team {owner.display_name}" if owner and owner.display_name else f"Roster {rid}"
        )
        owners = {roster.owner_id, *roster.co_owners} - {None}
        if user_id is not None:
            team.is_me = user_id in owners
        else:
            team.is_me = rid in previous_me
        team.slots.clear()
        starters = {p for p in roster.starters if p and p != "0"}
        reserve, taxi = set(roster.reserve), set(roster.taxi)
        for pid in roster.players:
            kind = (
                "starter"
                if pid in starters
                else "ir"
                if pid in reserve
                else "taxi"
                if pid in taxi
                else "bench"
            )
            team.slots.append(_slot(directory.from_sleeper(pid), kind, now))
    for team in list(league.teams):
        if team.external_roster_id not in seen:
            league.teams.remove(team)
    if created:
        db.add(league)
    db.flush()
    return league, created


def sync_league(db: Session, client: SleeperClient, directory: PlayerDirectory, league: League):
    if league.source != "sleeper" or not league.external_id:
        raise ApiError(409, "not_a_sleeper_league", "Only Sleeper leagues can be synced.")
    return import_sleeper(
        db, client, directory, league_id=league.external_id, username=None, season=None
    )[0]


# --------------------------------------------------------------------------- manual


def resolve_players(directory: PlayerDirectory, ids: Iterable[str]) -> list[PlayerInfo]:
    out, unknown = [], []
    for pid in ids:
        info = directory.resolve(pid)
        if info is None:
            unknown.append(pid)
        else:
            out.append(info)
    if unknown:
        raise ApiError(
            422, "player_not_found", "Unknown player ids.", {"player_ids": unknown}
        )
    return out


def create_manual(db: Session, body: ManualLeagueRequest, directory: PlayerDirectory) -> League:
    rules = scoring_from_input(body.scoring)
    positions = roster_positions_from_input(body.roster_positions)
    if len(body.teams) > body.num_teams:
        raise ApiError(422, "validation_error", "More teams than num_teams.")
    if sum(t.is_me for t in body.teams) > 1:
        raise ApiError(422, "validation_error", "Only one team can be marked is_me.")
    now = datetime.now(UTC)
    league = League(
        source="manual",
        external_id=None,
        name=body.name,
        season=body.season,
        num_teams=body.num_teams,
        scoring_settings=rules.weights,
        roster_positions=positions,
        settings=dict(body.settings),
    )
    taken: set[str] = set()
    for i in range(body.num_teams):
        given = body.teams[i] if i < len(body.teams) else None
        team = LeagueTeam(
            external_roster_id=str(i + 1),
            team_name=given.name if given else f"Team {i + 1}",
            is_me=bool(given and given.is_me),
        )
        for info in resolve_players(directory, given.players if given else []):
            key = info.player_id or info.sleeper_id or ""
            if key in taken:
                raise ApiError(
                    422, "validation_error", f"Player {key} is on more than one roster."
                )
            taken.add(key)
            team.slots.append(_slot(info, "bench", now))
        league.teams.append(team)
    db.add(league)
    db.flush()
    return league


def update_settings(league: League, *, name, scoring, roster_positions, settings) -> None:
    if name is not None:
        league.name = name
    if scoring is not None:
        league.scoring_settings = scoring_from_input(scoring).weights
    if roster_positions is not None:
        league.roster_positions = roster_positions_from_input(roster_positions)
    if settings is not None:
        league.settings = {**(league.settings or {}), **settings}
    league.updated_at = datetime.now(UTC)


__all__ = [
    "create_manual",
    "get_league",
    "import_sleeper",
    "league_brief",
    "list_leagues",
    "preset_like",
    "rules_for",
    "scoring_from_input",
    "serialize_league",
    "set_my_team",
    "starting_slots",
    "sync_league",
    "update_settings",
    "user_leagues",
]
