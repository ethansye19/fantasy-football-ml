"""Projection-based routes (BE-4.3 to BE-4.7), all behind the approval gate (D-099).

Every route here uses :class:`api.gate.GatedRoute`: in production they answer
HTTP 503 ``no_approved_model`` until an approved live model is published; in
private_test they serve labeled "not approved" data.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from fastapi.responses import JSONResponse

from api.approval import ApprovalState
from api.envelope import envelope
from api.gate import Approval, GatedRoute
from api.schemas import StartSitRequest, TradeRequest

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


def _not_implemented(state: ApprovalState, what: str) -> JSONResponse:
    body: dict[str, Any] = envelope(
        state,
        error={"code": "not_implemented", "message": f"{what} is not implemented yet."},
    )
    return JSONResponse(status_code=501, content=body)


@router.get("/projections")
def preset_projections(state: Approval) -> Any:
    return _not_implemented(state, "Preset projections")


@router.get("/ros")
def preset_ros(state: Approval) -> Any:
    return _not_implemented(state, "Preset rest-of-season rankings")


@router.get("/leagues/{league_id}/projections")
def league_projections(league_id: str, state: Approval) -> Any:
    return _not_implemented(state, "League projections")


@router.post("/leagues/{league_id}/start-sit")
def start_sit(
    league_id: str, body: StartSitRequest, state: Approval
) -> Any:
    return _not_implemented(state, "Start/sit")


@router.get("/leagues/{league_id}/waivers")
def waivers(league_id: str, state: Approval) -> Any:
    return _not_implemented(state, "Waivers")


@router.post("/leagues/{league_id}/trades/evaluate")
def trade_evaluate(
    league_id: str, body: TradeRequest, state: Approval
) -> Any:
    return _not_implemented(state, "Trade analyzer")


@router.get("/leagues/{league_id}/ros")
def league_ros(league_id: str, state: Approval) -> Any:
    return _not_implemented(state, "Rest-of-season rankings")
