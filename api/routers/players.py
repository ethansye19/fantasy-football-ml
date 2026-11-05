"""Ungated player search for league setup (docs/API.md section 6).

Returns identity only: id, name, position and team. Nothing projection-derived
and nothing restricted (D-007). Results are cacheable: they depend only on the
processed player tables.
"""

from __future__ import annotations

from typing import Annotated, Any, Literal

from fastapi import APIRouter, Query, Response

from api.deps import Directory

router = APIRouter(tags=["players"])

Position = Literal["QB", "RB", "WR", "TE", "K", "DST"]


@router.get("/players")
def search_players(
    directory: Directory,
    response: Response,
    search: Annotated[str, Query(min_length=2, max_length=60)],
    position: Position | None = None,
    limit: Annotated[int, Query(ge=1, le=50)] = 50,
) -> dict[str, Any]:
    rows = directory.search(search, position, limit)
    response.headers["Cache-Control"] = "public, max-age=300"
    return {
        "query": search.strip(),
        "position": position,
        "players": [
            {"player_id": i.player_id, "name": i.name, "position": i.position, "team": i.team}
            for i in rows
        ],
    }
