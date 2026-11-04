"""BE-4.2: Sleeper import (recorded fixtures), manual leagues, settings, my team, sync."""

from __future__ import annotations

import pytest
import respx
from api_helpers import assert_never_approved
from sleeper_mock import (
    LEAGUE_ID,
    USER_ID,
    USERNAME,
    directory,
    mock_sleeper,
    no_sleep_factory,
    server_error,
)


@pytest.fixture
def sleeper_router():
    with respx.mock(assert_all_called=False) as router:
        yield mock_sleeper(router)


@pytest.fixture
def client(make_client, sleeper_router):
    c = make_client()
    c.app.state.players.set(directory())
    c.app.state.sleeper_factory = no_sleep_factory
    return c


def _import(client, **body):
    return client.post("/leagues/import/sleeper", json=body or {"league_id": LEAGUE_ID})


MANUAL = {
    "name": "Office league",
    "season": 2026,
    "num_teams": 4,
    "scoring": {"preset": "half", "overrides": {"pass_td": 6}},
    "roster_positions": ["QB", "RB", "RB", "WR", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"],
    "teams": [{"name": "Mine", "is_me": True, "players": ["00-0036322", "KC", "4217"]}],
}


# --------------------------------------------------------------------------- Sleeper import


def test_import_by_league_id(client):
    r = _import(client)
    assert r.status_code == 201, r.text
    lg = r.json()
    assert (lg["source"], lg["external_id"], lg["season"], lg["num_teams"]) == (
        "sleeper",
        LEAGUE_ID,
        2026,
        8,
    )
    assert lg["name"] == "Bgr fantasy leuge"
    assert len(lg["teams"]) == 8
    assert lg["my_team_id"] is None  # no username given
    # scoring: 41 non-zero keys kept, three unsupported keys reported, PPR-like
    assert len(lg["scoring"]["settings"]) == 41
    assert set(lg["scoring"]["unsupported_keys"]) == {"def_st_fum_rec", "st_ff", "st_fum_rec"}
    assert lg["scoring"]["preset_like"] == "ppr"
    assert len(lg["scoring"]["hash"]) == 16
    assert lg["roster_positions"][:3] == ["QB", "RB", "RB"]
    assert lg["starting_slots"] == {
        "QB": 1, "RB": 2, "WR": 2, "TE": 1, "FLEX": 2, "K": 1, "DEF": 1
    }
    assert lg["settings"]["playoff_week_start"] == 15
    players = [p for t in lg["teams"] for p in t["players"]]
    assert len(players) == 119
    assert sum(p["slot"] == "starter" for p in players) == 80
    # crosswalk: every rostered Sleeper id maps to a gsis id or a team code (D-022)
    assert all(p["player_id"] for p in players)
    dst = {p["sleeper_id"]: p["player_id"] for p in players if p["position"] == "DST"}
    assert len(dst) == 9 and dst["LAR"] == "LA"
    assert all(p["player_id"].startswith("00-") for p in players if p["position"] != "DST")


def test_import_by_username_marks_my_team(client):
    r = _import(client, username=USERNAME)
    assert r.status_code == 201, r.text
    lg = r.json()
    mine = [t for t in lg["teams"] if t["is_me"]]
    assert len(mine) == 1 and mine[0]["owner_external_id"] == USER_ID
    assert lg["my_team_id"] == mine[0]["id"]
    assert mine[0]["owner_name"] == USERNAME


def test_reimport_updates_in_place(client):
    first = _import(client, username=USERNAME).json()
    again = _import(client)
    assert again.status_code == 200
    lg = again.json()
    assert lg["id"] == first["id"]
    assert len(lg["teams"]) == 8
    assert lg["my_team_id"] == first["my_team_id"]  # kept without a username
    assert len(client.get("/leagues").json()["leagues"]) == 1


def test_username_with_several_leagues_needs_a_choice(client):
    r = _import(client, username=USERNAME, season=2025)
    assert r.status_code == 409
    err = r.json()["error"]
    assert err["code"] == "multiple_leagues"
    assert len(err["details"]["leagues"]) == 3


def test_unknown_user_and_league(client):
    r = client.post("/leagues/import/sleeper", json={"username": "nobody_here"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "sleeper_not_found"
    r = client.post("/leagues/import/sleeper", json={"league_id": "1"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "sleeper_not_found"


def test_sleeper_outage_is_502(make_client):
    with respx.mock(assert_all_called=False) as router:
        server_error(router)
        c = make_client()
        c.app.state.players.set(directory())
        c.app.state.sleeper_factory = no_sleep_factory
        r = _import(c)
    assert r.status_code == 502
    assert r.json()["error"]["code"] == "sleeper_unavailable"


def test_import_validation(client):
    for body in ({}, {"league_id": "abc"}, {"username": "bad name!"}, {"league_id": "1", "x": 1}):
        r = client.post("/leagues/import/sleeper", json=body)
        assert r.status_code == 422, body
        assert r.json()["error"]["code"] == "validation_error"


def test_user_league_listing(client):
    r = client.get(f"/sleeper/users/{USERNAME}/leagues", params={"season": 2026})
    assert r.status_code == 200
    body = r.json()
    assert body["user"]["user_id"] == USER_ID
    assert [lg["league_id"] for lg in body["leagues"]] == [LEAGUE_ID]
    default = client.get(f"/sleeper/users/{USERNAME}/leagues").json()
    assert default["season"] == 2026  # from Sleeper state


# --------------------------------------------------------------------------- settings, my team, sync


def test_custom_scoring_survives_sync(client):
    lg = _import(client).json()
    old_hash = lg["scoring"]["hash"]
    r = client.put(
        f"/leagues/{lg['id']}/settings",
        json={"scoring": {"preset": "std", "overrides": {"pass_td": 6}}, "name": "Renamed"},
    )
    assert r.status_code == 200, r.text
    new = r.json()
    assert new["name"] == "Renamed"
    assert new["scoring"]["hash"] != old_hash
    assert new["scoring"]["preset_like"] == "std"
    assert new["scoring"]["settings"]["pass_td"] == 6
    synced = client.post(f"/leagues/{lg['id']}/sync").json()
    assert synced["scoring"]["hash"] == new["scoring"]["hash"]
    assert synced["name"] == "Bgr fantasy leuge"  # names follow Sleeper


def test_my_team_selection(client):
    lg = _import(client).json()
    team = lg["teams"][3]["id"]
    r = client.put(f"/leagues/{lg['id']}/my-team", json={"league_team_id": team})
    assert r.status_code == 200
    assert r.json()["my_team_id"] == team
    assert sum(t["is_me"] for t in r.json()["teams"]) == 1
    other = lg["teams"][0]["id"]
    r = client.put(f"/leagues/{lg['id']}/my-team", json={"league_team_id": other})
    assert r.json()["my_team_id"] == other
    r = client.put(f"/leagues/{lg['id']}/my-team", json={"league_team_id": "nope"})
    assert r.status_code == 404 and r.json()["error"]["code"] == "team_not_found"


def test_roster_positions_update_validated(client):
    lg = _import(client).json()
    bad = client.put(f"/leagues/{lg['id']}/settings", json={"roster_positions": ["QB", "XX"]})
    assert bad.status_code == 422 and bad.json()["error"]["code"] == "invalid_roster_positions"
    ok = client.put(
        f"/leagues/{lg['id']}/settings",
        json={"roster_positions": ["QB", "RB", "WR", "TE", "SUPER_FLEX", "BN"]},
    )
    assert ok.json()["starting_slots"] == {"QB": 1, "RB": 1, "WR": 1, "TE": 1, "SUPER_FLEX": 1}


# --------------------------------------------------------------------------- manual leagues


def test_manual_league(client):
    r = client.post("/leagues", json=MANUAL)
    assert r.status_code == 201, r.text
    lg = r.json()
    assert lg["source"] == "manual" and lg["external_id"] is None
    assert len(lg["teams"]) == 4
    assert lg["scoring"]["settings"]["pass_td"] == 6
    assert lg["scoring"]["settings"]["rec"] == 0.5
    mine = next(t for t in lg["teams"] if t["is_me"])
    assert lg["my_team_id"] == mine["id"]
    ids = {p["player_id"] for p in mine["players"]}
    assert "KC" in ids and "00-0036322" in ids
    assert len(ids) == 3  # Sleeper id 4217 resolved through the crosswalk
    assert client.get(f"/leagues/{lg['id']}").json()["id"] == lg["id"]


@pytest.mark.parametrize(
    "change,code",
    [
        ({"roster_positions": ["QB", "ZZ"]}, "invalid_roster_positions"),
        ({"roster_positions": ["BN", "BN"]}, "invalid_roster_positions"),
        ({"scoring": {"settings": {"pass_td": 4}, "preset": "ppr"}}, "invalid_scoring"),
        ({"scoring": {}}, "invalid_scoring"),
        ({"scoring": {"preset": "superflex"}}, "validation_error"),
        ({"num_teams": 1}, "validation_error"),
        ({"teams": [{"name": "A", "players": ["00-9999999x"]}]}, "player_not_found"),
        ({"teams": [{"name": "A", "players": ["bad id!"]}]}, "validation_error"),
        ({"teams": [{"name": "A", "players": ["99999999"]}]}, "player_not_found"),
        (
            {"teams": [{"name": "A", "players": ["KC"]}, {"name": "B", "players": ["KC"]}]},
            "validation_error",
        ),
        ({"name": ""}, "validation_error"),
        ({"unexpected": True}, "validation_error"),
    ],
)
def test_manual_league_validation(client, change, code):
    r = client.post("/leagues", json={**MANUAL, **change})
    assert r.status_code == 422, r.text
    assert r.json()["error"]["code"] == code


def test_sync_manual_league_refused(client):
    lg = client.post("/leagues", json=MANUAL).json()
    r = client.post(f"/leagues/{lg['id']}/sync")
    assert r.status_code == 409 and r.json()["error"]["code"] == "not_a_sleeper_league"


def test_delete_league(client):
    lg = client.post("/leagues", json=MANUAL).json()
    assert client.delete(f"/leagues/{lg['id']}").status_code == 204
    r = client.get(f"/leagues/{lg['id']}")
    assert r.status_code == 404 and r.json()["error"]["code"] == "league_not_found"
    assert client.get("/leagues/not-a-uuid").status_code == 404


def test_league_routes_work_in_private_test_with_label_header(make_client, sleeper_router):
    c = make_client(app_env="private_test")
    c.app.state.players.set(directory())
    c.app.state.sleeper_factory = no_sleep_factory
    r = _import(c)
    assert r.status_code == 201
    assert_never_approved(r)
