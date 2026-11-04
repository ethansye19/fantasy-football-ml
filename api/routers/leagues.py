"""League import, manual leagues and settings (BE-4.2, docs/API.md section 6). Never gated."""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter, Query, Response
from fastapi.encoders import jsonable_encoder
from fastapi.responses import JSONResponse

from api.deps import DbSession, Directory, Sleeper
from api.errors import ApiError
from api.schemas import (
    LeagueSettingsUpdate,
    ManualLeagueRequest,
    MyTeamRequest,
    SleeperImportRequest,
)
from api.services import leagues as svc

router = APIRouter(tags=["leagues"])


@router.get("/sleeper/users/{username}/leagues")
def sleeper_user_leagues(
    username: str,
    client: Sleeper,
    season: int | None = Query(default=None, ge=2017, le=2100),
) -> dict[str, Any]:
    if not username.replace("_", "").isalnum() or len(username) > 40:
        raise ApiError(422, "validation_error", "Invalid Sleeper username.")
    user, season_used, leagues = svc.user_leagues(client, username, season)
    return {
        "user": {
            "user_id": user.user_id,
            "username": user.username,
            "display_name": user.display_name,
        },
        "season": season_used,
        "leagues": [svc.league_brief(lg) for lg in leagues],
    }


@router.post("/leagues/import/sleeper")
def import_sleeper(
    body: SleeperImportRequest, db: DbSession, client: Sleeper, directory: Directory
) -> JSONResponse:
    if not body.league_id and not body.username:
        raise ApiError(422, "validation_error", "Give a Sleeper league_id or username.")
    league, created = svc.import_sleeper(
        db,
        client,
        directory,
        league_id=body.league_id,
        username=body.username,
        season=body.season,
    )
    return JSONResponse(
        status_code=201 if created else 200,
        content=jsonable_encoder(svc.serialize_league(svc.get_league(db, str(league.id)))),
    )


@router.post("/leagues", status_code=201)
def create_league(body: ManualLeagueRequest, db: DbSession, directory: Directory) -> Any:
    league = svc.create_manual(db, body, directory)
    return svc.serialize_league(svc.get_league(db, str(league.id)))


@router.get("/leagues")
def list_leagues(db: DbSession) -> dict[str, Any]:
    return {"leagues": [svc.serialize_league(lg, with_teams=False) for lg in svc.list_leagues(db)]}


@router.get("/leagues/{league_id}")
def get_league(league_id: str, db: DbSession) -> Any:
    return svc.serialize_league(svc.get_league(db, league_id))


@router.put("/leagues/{league_id}/settings")
def update_settings(league_id: str, body: LeagueSettingsUpdate, db: DbSession) -> Any:
    league = svc.get_league(db, league_id)
    svc.update_settings(
        league,
        name=body.name,
        scoring=body.scoring,
        roster_positions=body.roster_positions,
        settings=body.settings,
    )
    db.flush()
    return svc.serialize_league(league)


@router.put("/leagues/{league_id}/my-team")
def set_my_team(league_id: str, body: MyTeamRequest, db: DbSession) -> Any:
    league = svc.get_league(db, league_id)
    svc.set_my_team(league, body.league_team_id)
    db.flush()
    return svc.serialize_league(league)


@router.post("/leagues/{league_id}/sync")
def sync_league(league_id: str, db: DbSession, client: Sleeper, directory: Directory) -> Any:
    league = svc.get_league(db, league_id)
    svc.sync_league(db, client, directory, league)
    db.expire_all()
    return svc.serialize_league(svc.get_league(db, league_id))


@router.delete("/leagues/{league_id}", status_code=204)
def delete_league(league_id: str, db: DbSession) -> Response:
    db.delete(svc.get_league(db, league_id))
    return Response(status_code=204)
