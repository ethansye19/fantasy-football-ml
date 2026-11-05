"""BE-4.9 item 4: ungated GET /players?search=&position= (identity only)."""

from __future__ import annotations

import polars as pl
import pytest
from api_helpers import assert_never_approved, strings
from sleeper_mock import directory

from api.services.players import PlayerDirectory

IDENTITY_KEYS = {"player_id", "name", "position", "team"}


@pytest.fixture
def client(make_client):
    c = make_client()  # production, no model: the route is not gated
    c.app.state.players.set(directory())
    return c


def test_search_returns_identity_only(client):
    r = client.get("/players", params={"search": "jeff"})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["query"] == "jeff" and body["position"] is None
    names = {p["name"] for p in body["players"]}
    assert "Justin Jefferson" in names
    for p in body["players"]:
        assert set(p) == IDENTITY_KEYS
    jj = next(p for p in body["players"] if p["name"] == "Justin Jefferson")
    assert (jj["player_id"], jj["position"], jj["team"]) == ("00-0036322", "WR", "MIN")
    assert r.headers["Cache-Control"] == "public, max-age=300"


def test_search_is_case_insensitive_and_matches_ids_and_defenses(client):
    assert client.get("/players", params={"search": "JEFFERSON"}).json()["players"]
    by_id = client.get("/players", params={"search": "00-0036322"}).json()["players"]
    assert [p["player_id"] for p in by_id] == ["00-0036322"]
    dst = client.get("/players", params={"search": "kc"}).json()["players"]
    assert {"player_id": "KC", "name": "KC D/ST", "position": "DST", "team": "KC"} in dst


def test_search_position_filter_and_limit(client):
    assert client.get("/players", params={"search": "a", "position": "RB"}).status_code == 422
    rb = client.get("/players", params={"search": "ja", "position": "RB"}).json()
    assert rb["players"] and {p["position"] for p in rb["players"]} == {"RB"}
    few = client.get("/players", params={"search": "ja", "limit": 2}).json()
    assert len(few["players"]) == 2
    assert client.get("/players", params={"search": "ja", "limit": 51}).status_code == 422
    assert client.get("/players", params={"search": "ja", "position": "OL"}).status_code == 422
    assert client.get("/players").status_code == 422


def test_search_no_match_and_no_model_needed(client):
    body = client.get("/players", params={"search": "zzzzqq"}).json()
    assert body["players"] == []
    assert client.get("/meta/approval").json()["serving"] is False  # still answered above


def test_search_never_returns_projection_fields_or_approved(make_client):
    c = make_client(app_env="private_test")
    c.app.state.players.set(directory())
    r = c.get("/players", params={"search": "jeff"})
    assert r.status_code == 200
    assert_never_approved(r)
    words = set(" ".join(strings(r.json())).lower().split())
    assert not {"median", "floor", "ceiling", "boom", "approved"} & words


def test_search_ordering_prefers_word_starts_then_current_team():
    dim = pl.DataFrame(
        {
            "player_id": ["00-0000001", "00-0000002", "00-0000003", "00-0000004"],
            "sleeper_id": ["1", "2", "3", "4"],
            "full_name": ["Justin Jefferson", "Al Sonny", "Bo Sonder", "Cy Olson"],
            "position": ["WR", "WR", "WR", "WR"],
            "latest_team": ["MIN", None, "DET", "KC"],
        }
    )
    d = PlayerDirectory.from_frames(dim, None)
    names = [i.name for i in d.search("son")]
    # word-start matches first (with a team before without), then substring matches
    assert names == ["Bo Sonder", "Al Sonny", "Cy Olson", "Justin Jefferson"]
    assert d.search("s") == []
    assert [i.name for i in d.search("son", position="QB")] == []
