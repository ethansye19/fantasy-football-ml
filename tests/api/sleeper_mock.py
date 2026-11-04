"""respx routes serving the recorded Sleeper responses in tests/fixtures/sleeper (no network)."""

from __future__ import annotations

import json
from pathlib import Path

import httpx
import polars as pl
import respx

from api.services.players import PlayerDirectory
from ffml.data.sleeper import players_frame

SLEEPER = Path(__file__).resolve().parents[1] / "fixtures" / "sleeper"
API_FIXTURES = Path(__file__).resolve().parent / "fixtures"
BASE = "https://api.sleeper.app/v1"
LEAGUE_ID = "1389750342958223360"
USER_ID = "1257514331365462016"
USERNAME = "ethanye19"


def _json(name: str):
    return json.loads((SLEEPER / name).read_text(encoding="utf-8"))


def mock_sleeper(router: respx.MockRouter) -> respx.MockRouter:
    """Register every recorded endpoint on ``router``; unknown paths answer like Sleeper."""
    router.get(f"{BASE}/state/nfl").respond(json=_json("state.json"))
    router.get(f"{BASE}/user/{USERNAME}").respond(json=_json("user_ethanye19.json"))
    router.get(f"{BASE}/user/{USER_ID}").respond(json=_json("user_ethanye19.json"))
    router.get(f"{BASE}/user/{USER_ID}/leagues/nfl/2026").respond(
        json=_json("user_leagues_2026.json")
    )
    router.get(f"{BASE}/user/{USER_ID}/leagues/nfl/2025").respond(
        json=_json("user_leagues_2025.json")
    )
    router.get(f"{BASE}/league/{LEAGUE_ID}").respond(json=_json("league.json"))
    router.get(f"{BASE}/league/{LEAGUE_ID}/rosters").respond(json=_json("rosters.json"))
    router.get(f"{BASE}/league/{LEAGUE_ID}/users").respond(json=_json("users.json"))
    # Observed Sleeper behaviour (fixtures README): unknown user -> 200 null, unknown league -> 404
    router.get(url__regex=rf"{BASE}/user/[^/]+$").respond(200, content=b"null")
    router.get(url__regex=rf"{BASE}/league/.*").respond(404, content=b"null")
    return router


def directory() -> PlayerDirectory:
    """Crosswalk for the test league: real dim_player rows plus the recorded Sleeper map."""
    dim = pl.read_parquet(API_FIXTURES / "dim_player_league.parquet")
    sleeper = players_frame(_json("players_subset.json"))
    return PlayerDirectory.from_frames(dim, sleeper)


def no_sleep_factory():
    from ffml.data.sleeper import SleeperClient

    return SleeperClient(sleep=lambda _s: None)


def server_error(router: respx.MockRouter) -> None:
    router.get(url__regex=rf"{BASE}/.*").mock(return_value=httpx.Response(503))
