"""Projection-based routes (BE-4.3 to BE-4.7), all behind the approval gate (D-099).

Every route here uses :class:`api.gate.GatedRoute`: in production they answer
HTTP 503 ``no_approved_model`` until an approved live model is published; in
private_test they serve labeled "not approved" data. League scoring is applied
to the simulated stat lines at request time (D-003).
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Request

from api.approval import ApprovalState
from api.deps import DbSession, Directory
from api.envelope import envelope
from api.errors import ApiError
from api.gate import Approval, GatedRoute
from api.schemas import StartSitRequest, TradeRequest
from api.services import leagues as league_svc
from api.services import recommend
from api.services.projection import Scored
from ffml.scoring.rules import ScoringRules

router = APIRouter(route_class=GatedRoute, tags=["projections"])

#: (method, path template) of every gated route; tests iterate over this list.
PROJECTION_ROUTES: tuple[tuple[str, str], ...] = (
    ("GET", "/projections"),
    ("GET", "/ros"),
    ("GET", "/leagues/{league_id}/projections"),
    ("POST", "/leagues/{league_id}/start-sit"),
    ("GET", "/leagues/{league_id}/waivers"),
    ("POST", "/leagues/{league_id}/trades/evaluate"),
    ("GET", "/leagues/{league_id}/ros"),
)

Position = Literal["QB", "RB", "WR", "TE", "K", "DST"]
Availability = Literal["all", "rostered", "free_agents", "mine"]
Sort = Literal["median", "mean", "ceiling", "floor", "boom", "bust"]
Preset = Literal["ppr", "half", "std"]
QUANTILE_NOTE = (
    "Raw simulation quantiles under this scoring; the model's calibration is fitted on "
    "reference scorings and is not applied at request time."
)


def _scored(request: Request, state: ApprovalState, rules: ScoringRules) -> Scored:
    assert state.source is not None
    return request.app.state.projections.scored(state.source, rules)


def _scoring_block(rules: ScoringRules, scored: Scored) -> dict[str, Any]:
    return {
        "hash": rules.scoring_hash,
        "unsupported_keys": list(rules.unsupported_keys),
        "preset_like": scored.preset_like,
        "drivers_reference": scored.preset_like,
        "quantiles": QUANTILE_NOTE,
    }


def _check_week(scored: Scored, week: int | None) -> None:
    if week is not None and week != scored.run.week:
        raise ApiError(
            404,
            "week_not_available",
            f"No projections for week {week} in the current run.",
            {"available_weeks": [scored.run.week]},
        )


def _check_season(request: Request, state: ApprovalState, what: str) -> None:
    """Offseason mode (live runs only; replays and fixtures are historical by definition)."""
    src = state.source
    if src is None or src.is_replay or src.kind == "fixture":
        return
    season = request.app.state.season.describe()
    if season.get("mode") in ("postseason", "offseason"):
        raise ApiError(409, "offseason", f"{what} is paused: {season.get('banner')}")


# --------------------------------------------------------------------------- preset routes


@router.get("/projections")
def preset_projections(
    request: Request,
    state: Approval,
    directory: Directory,
    scoring: Preset = "ppr",
    week: Annotated[int | None, Query(ge=1, le=18)] = None,
    position: Position | None = None,
    search: Annotated[str | None, Query(max_length=60)] = None,
    sort: Sort = "median",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> Any:
    _check_season(request, state, "Weekly projections")
    rules = ScoringRules.from_preset(scoring)
    scored = _scored(request, state, rules)
    _check_week(scored, week)
    body = recommend.projections(
        scored, directory, None, position=position, availability="all", search=search,
        sort=sort, limit=limit, offset=offset,
    )
    return envelope(state, scoring=_scoring_block(rules, scored), **body)


@router.get("/ros")
def preset_ros(
    request: Request,
    state: Approval,
    directory: Directory,
    scoring: Preset = "ppr",
    position: Position | None = None,
    include_current_week: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> Any:
    rules = ScoringRules.from_preset(scoring)
    scored = _scored(request, state, rules)
    body = recommend.ros_rankings(
        scored, directory, None, None, position=position, availability="all", limit=limit,
        offset=offset, include_current=include_current_week,
    )
    return envelope(state, scoring=_scoring_block(rules, scored), **body)


# --------------------------------------------------------------------------- league routes


def _league(db, league_id: str):  # type: ignore[no-untyped-def]
    league = league_svc.get_league(db, league_id)
    return league, league_svc.rules_for(league), recommend.Rosters.of(league)


@router.get("/leagues/{league_id}/projections")
def league_projections(
    league_id: str,
    request: Request,
    state: Approval,
    db: DbSession,
    directory: Directory,
    week: Annotated[int | None, Query(ge=1, le=18)] = None,
    position: Position | None = None,
    search: Annotated[str | None, Query(max_length=60)] = None,
    availability: Availability = "all",
    sort: Sort = "median",
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> Any:
    league, rules, rosters = _league(db, league_id)
    _check_season(request, state, "Weekly projections")
    scored = _scored(request, state, rules)
    _check_week(scored, week)
    body = recommend.projections(
        scored, directory, rosters, position=position, availability=availability,
        search=search, sort=sort, limit=limit, offset=offset,
    )
    return envelope(
        state, league_id=str(league.id), scoring=_scoring_block(rules, scored), **body
    )


@router.post("/leagues/{league_id}/start-sit")
def start_sit(
    league_id: str,
    body: StartSitRequest,
    request: Request,
    state: Approval,
    db: DbSession,
    directory: Directory,
) -> Any:
    league, rules, rosters = _league(db, league_id)
    _check_season(request, state, "Start/sit")
    scored = _scored(request, state, rules)
    _check_week(scored, body.week)
    result = recommend.start_sit(scored, directory, rosters, list(body.player_ids))
    return envelope(
        state, league_id=str(league.id), scoring=_scoring_block(rules, scored), **result
    )


@router.get("/leagues/{league_id}/waivers")
def waivers(
    league_id: str,
    request: Request,
    state: Approval,
    db: DbSession,
    directory: Directory,
    league_team_id: Annotated[str | None, Query(max_length=64)] = None,
    position: Position | None = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 25,
) -> Any:
    league, rules, rosters = _league(db, league_id)
    scored = _scored(request, state, rules)
    result = recommend.waivers(
        scored, directory, league, rosters, team_id=league_team_id, position=position,
        limit=limit,
    )
    return envelope(
        state, league_id=str(league.id), scoring=_scoring_block(rules, scored), **result
    )


@router.post("/leagues/{league_id}/trades/evaluate")
def trade_evaluate(
    league_id: str,
    body: TradeRequest,
    request: Request,
    state: Approval,
    db: DbSession,
    directory: Directory,
) -> Any:
    league, rules, rosters = _league(db, league_id)
    if set(body.give) & set(body.receive):
        raise ApiError(422, "invalid_trade", "A player cannot be on both sides of a trade.")
    scored = _scored(request, state, rules)
    result = recommend.trade(
        scored, directory, league, rosters, give=list(body.give), receive=list(body.receive),
        team_id=body.league_team_id, partner_id=body.partner_team_id,
    )
    return envelope(
        state, league_id=str(league.id), scoring=_scoring_block(rules, scored), **result
    )


@router.get("/leagues/{league_id}/ros")
def league_ros(
    league_id: str,
    request: Request,
    state: Approval,
    db: DbSession,
    directory: Directory,
    position: Position | None = None,
    availability: Availability = "all",
    include_current_week: bool = True,
    limit: Annotated[int, Query(ge=1, le=500)] = 100,
    offset: Annotated[int, Query(ge=0, le=10_000)] = 0,
) -> Any:
    league, rules, rosters = _league(db, league_id)
    scored = _scored(request, state, rules)
    body = recommend.ros_rankings(
        scored, directory, league, rosters, position=position, availability=availability,
        limit=limit, offset=offset, include_current=include_current_week,
    )
    return envelope(
        state, league_id=str(league.id), scoring=_scoring_block(rules, scored), **body
    )
