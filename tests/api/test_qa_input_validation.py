"""QA-4.1 input validation on every route (docs/SECURITY.md section 2).

The app runs in private_test with a fixture snapshot, so the gated routes
parse their input too (in production the gate answers 503 before parsing,
tests/api/test_gate.py). Checks:

- every route answers unknown or injection-like ids with a 4xx error
  envelope, never a 500, never a stack trace, and the database survives;
- bad types, unknown fields, malformed JSON and out-of-range query values
  give 422 ``validation_error`` (400 for bodies the JSON parser refuses);
- oversized lists and strings are refused;
- injection-like Sleeper usernames and league ids never reach Sleeper, and
  accepted ones reach exactly ``https://api.sleeper.app/v1/user/<name>``;
- cross-site "simple" requests (text/plain, form, no content type) cannot
  create leagues.

Known gaps are strict xfails with finding ids (remove the marker with the fix).
"""

from __future__ import annotations

import json
import re
from urllib.parse import quote

import pytest
import respx
from api_factory import PRIVATE_LABELS, player_ids, write_snapshot
from sleeper_mock import BASE, LEAGUE_ID, USERNAME, directory, mock_sleeper, no_sleep_factory

UNKNOWN_UUID = "6f1c0000-0000-4000-8000-000000000000"
#: path values a client might send as a league or team id
HOSTILE_IDS = [
    UNKNOWN_UUID,
    "x",
    "' OR '1'='1",
    "1; DROP TABLE leagues;--",
    '"; SELECT pg_sleep(5);--',
    "<script>alert(1)</script>",
    "${jndi:ldap://example.invalid/a}",
    "{{7*7}}",
    "%00",
    "..%2F..%2Fetc%2Fpasswd",
    "١٢٣",
    "a" * 2000,
    "00000000-0000-0000-0000-00000000000g",
    "null",
]
#: Sleeper usernames that must be refused before any request to Sleeper
HOSTILE_USERNAMES = [
    "",
    " ",
    "a" * 41,
    "user/../../league/1",
    "user?x=1",
    "user#frag",
    "user name",
    "user%2F..",
    "user;rm -rf",
    "<script>",
    "' OR 1=1--",
    "user\nX-Injected: 1",
    "us\r\ner",
    "user\x00",
    "..",
    "user.name",
    "us-er",
]
HOSTILE_LEAGUE_IDS = ["", "abc", "123abc", "1" * 25, "-1", "1.5", "1e9", "123/../456", "12 34",
                      "١٢٣", "0x1f", "123\n"]

ENTS = player_ids()
MINE = [e for e, _p, t in ENTS if t == "KC"][:6]
THEIRS = [e for e, _p, t in ENTS if t == "DET"][:6]
LEAGUE = {
    "name": "QA league",
    "season": 2024,
    "num_teams": 2,
    "scoring": {"preset": "ppr"},
    "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF", "BN", "BN"],
    "teams": [{"name": "Mine", "is_me": True, "players": MINE},
              {"name": "Theirs", "players": THEIRS}],
}
VALID_BODIES = {
    ("PUT", "/leagues/{league_id}/settings"): {"name": "Renamed"},
    ("PUT", "/leagues/{league_id}/my-team"): {"league_team_id": UNKNOWN_UUID},
    ("POST", "/leagues/{league_id}/start-sit"): {"player_ids": MINE[:2]},
    ("POST", "/leagues/{league_id}/trades/evaluate"): {"give": MINE[:1], "receive": THEIRS[:1]},
    ("POST", "/leagues"): LEAGUE,
    ("POST", "/leagues/import/sleeper"): {"league_id": LEAGUE_ID},
}


@pytest.fixture
def sleeper():
    with respx.mock(assert_all_called=False) as router:
        mock_sleeper(router)
        yield router


@pytest.fixture
def client(make_client, api_dirs, sleeper):
    write_snapshot(api_dirs["fixtures"], labels=PRIVATE_LABELS, n_sims=200)
    c = make_client(app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"])
    c.app.state.players.set(directory())
    c.app.state.sleeper_factory = no_sleep_factory
    return c


def _api_routes(client) -> list[tuple[str, str]]:
    """``(method, path template)`` of every API operation, from the OpenAPI schema."""
    paths = client.get("/openapi.json").json()["paths"]
    return sorted((m.upper(), path) for path, ops in paths.items() for m in ops
                  if m.upper() in ("GET", "POST", "PUT", "DELETE", "PATCH"))


def _send(client, method: str, path: str, body=None, **kw):
    if method in ("POST", "PUT"):
        return client.request(method, path, json=body if body is not None else {}, **kw)
    return client.request(method, path, **kw)


def _assert_clean_error(r, allowed: set[int]) -> None:
    assert r.status_code in allowed, (r.status_code, r.text[:300])
    assert "Traceback" not in r.text and 'File "' not in r.text
    body = r.json()
    assert set(body) == {"error"}, body
    assert set(body["error"]) == {"code", "message", "details"}, body


NO_BODY = {("POST", "/leagues/{league_id}/sync")}


def test_every_body_route_is_in_the_matrix(client):
    """A new POST or PUT route needs a valid body here so the malformed-body checks reach it."""
    routes = _api_routes(client)
    assert len(routes) >= 20
    body_routes = {(m, p) for m, p in routes if m in ("POST", "PUT")}
    assert body_routes - set(VALID_BODIES) - NO_BODY == set()


def test_unknown_and_hostile_ids_on_every_league_route(client):
    league = client.post("/leagues", json=LEAGUE)
    assert league.status_code == 201, league.text
    before = client.get("/leagues").json()["leagues"]
    problems = []
    for method, template in _api_routes(client):
        if "{league_id}" not in template:
            continue
        body = VALID_BODIES.get((method, template))
        for value in HOSTILE_IDS:
            path = template.replace("{league_id}", quote(value, safe=""))
            r = _send(client, method, path, body)
            if r.status_code >= 500 or r.status_code < 400:
                problems.append(f"{method} {template} id={value[:30]!r}: {r.status_code}")
                continue
            if r.status_code != 405:
                _assert_clean_error(r, {404, 422})
    assert problems == []
    after = client.get("/leagues").json()["leagues"]
    assert [lg["id"] for lg in after] == [lg["id"] for lg in before]


def test_hostile_team_ids_are_404(client):
    league = client.post("/leagues", json=LEAGUE).json()
    lid = league["id"]
    for value in HOSTILE_IDS:
        r = client.put(f"/leagues/{lid}/my-team", json={"league_team_id": value[:64]})
        _assert_clean_error(r, {404, 422})
        r = client.get(f"/leagues/{lid}/waivers", params={"league_team_id": value[:64]})
        _assert_clean_error(r, {404, 409, 422})


BAD_BODIES = [
    b"{not json",
    b"",
    b"null",
    b"[]",
    b'"text"',
    b"123",
    b'{"a": 1}',
    b"\xff\xfe\x00",
    json.dumps({"x": "y" * 10}).encode()[:-1],
    b"[" * 5000 + b"]" * 5000,
]


def test_malformed_and_wrong_shape_bodies_on_every_body_route(client):
    lid = client.post("/leagues", json=LEAGUE).json()["id"]
    problems = []
    for method, template in _api_routes(client):
        if method not in ("POST", "PUT") or (method, template) not in VALID_BODIES:
            continue
        path = template.replace("{league_id}", lid)
        for raw in BAD_BODIES:
            r = client.request(method, path, content=raw,
                               headers={"content-type": "application/json"})
            if r.status_code not in (400, 422):
                problems.append(f"{method} {template} {raw[:20]!r}: {r.status_code} {r.text[:100]}")
            else:
                _assert_clean_error(r, {400, 422})
    assert problems == []


WRONG_TYPES = {
    ("POST", "/leagues"): [
        {**LEAGUE, "name": 5}, {**LEAGUE, "name": ""}, {**LEAGUE, "name": "x" * 101},
        {**LEAGUE, "season": "two"}, {**LEAGUE, "season": 1900}, {**LEAGUE, "season": 2101},
        {**LEAGUE, "num_teams": 1}, {**LEAGUE, "num_teams": 33}, {**LEAGUE, "num_teams": 2.5},
        {**LEAGUE, "scoring": "ppr"}, {**LEAGUE, "scoring": {"preset": "superflex"}},
        {**LEAGUE, "scoring": {"preset": "ppr", "overrides": {"pass_td": "six"}}},
        {**LEAGUE, "scoring": {"preset": "ppr", "overrides": {"pass_td": [4]}}},
        {**LEAGUE, "scoring": {"settings": {"pass_td": 4}, "preset": "ppr"}},
        {**LEAGUE, "scoring": {}},
        {**LEAGUE, "roster_positions": []}, {**LEAGUE, "roster_positions": "QB"},
        {**LEAGUE, "roster_positions": ["QB", "WIZARD"]}, {**LEAGUE, "roster_positions": [1]},
        {**LEAGUE, "roster_positions": ["BN", "IR"]},
        {**LEAGUE, "roster_positions": ["QB"] * 41},
        {**LEAGUE, "teams": [{"name": "a", "players": ["../etc"]}]},
        {**LEAGUE, "teams": [{"name": "a", "players": ["x" * 33]}]},
        {**LEAGUE, "teams": [{"name": "a", "players": ["00-1"] * 61}]},
        {**LEAGUE, "teams": [{"name": "a", "is_me": True}, {"name": "b", "is_me": True}]},
        {**LEAGUE, "teams": [{"name": f"t{i}"} for i in range(3)]},
        {**LEAGUE, "teams": [{"name": "a", "players": [MINE[0]]}, {"name": "b", "players": [MINE[0]]}]},
        {**LEAGUE, "settings": {f"k{i}": 1 for i in range(51)}},
        {**LEAGUE, "unknown_field": 1},
    ],
    ("PUT", "/leagues/{league_id}/settings"): [
        {"name": ""}, {"name": None, "extra": 1}, {"roster_positions": []},
        {"scoring": {"preset": "x"}}, {"settings": "x"},
    ],
    ("PUT", "/leagues/{league_id}/my-team"): [
        {}, {"league_team_id": ""}, {"league_team_id": "x" * 65}, {"league_team_id": 5},
    ],
    ("POST", "/leagues/{league_id}/start-sit"): [
        {}, {"player_ids": []}, {"player_ids": [MINE[0]]}, {"player_ids": MINE[:5]},
        {"player_ids": [MINE[0], MINE[0]]}, {"player_ids": "a,b"}, {"player_ids": [1, 2]},
        {"player_ids": ["<script>", "x y"]}, {"player_ids": MINE[:2], "week": 0},
        {"player_ids": MINE[:2], "week": 19}, {"player_ids": MINE[:2], "week": "ten"},
        {"player_ids": ["a"] * 10_000},
    ],
    ("POST", "/leagues/{league_id}/trades/evaluate"): [
        {}, {"give": [], "receive": THEIRS[:1]}, {"give": MINE[:1]},
        {"give": MINE[:1] * 2, "receive": THEIRS[:1]}, {"give": MINE[:1], "receive": MINE[:1]},
        {"give": ["x"] * 9, "receive": THEIRS[:1]},
        {"give": MINE[:1], "receive": THEIRS[:1], "league_team_id": "x" * 65},
    ],
    ("POST", "/leagues/import/sleeper"): [
        {}, {"league_id": 123}, {"username": 5}, {"season": "now", "username": USERNAME},
        {"league_id": LEAGUE_ID, "extra": True},
    ],
}


def test_wrong_types_and_ranges_are_422(client, sleeper):
    lid = client.post("/leagues", json=LEAGUE).json()["id"]
    calls_before = len(sleeper.calls)
    problems = []
    for (method, template), bodies in WRONG_TYPES.items():
        path = template.replace("{league_id}", lid)
        for body in bodies:
            r = client.request(method, path, json=body)
            if r.status_code != 422:
                problems.append(f"{method} {template} {json.dumps(body)[:80]}: {r.status_code}")
            else:
                _assert_clean_error(r, {422})
    assert problems == []
    assert len(sleeper.calls) == calls_before  # nothing invalid reached Sleeper


BAD_QUERIES = {
    "/projections": [{"week": "abc"}, {"week": 0}, {"week": 19}, {"limit": 0}, {"limit": 501},
                     {"limit": -1}, {"offset": -1}, {"offset": 10_001}, {"position": "XX"},
                     {"position": "qb'--"}, {"sort": "drop"}, {"scoring": "evil"},
                     {"search": "x" * 61}, {"limit": "1e3"}],
    "/ros": [{"include_current_week": "maybe"}, {"limit": 501}, {"position": "FLEX"}],
    "/leagues/{league_id}/projections": [{"availability": "everyone"}, {"sort": "name"},
                                          {"week": "10;"}, {"limit": 10**12}],
    "/leagues/{league_id}/waivers": [{"limit": 101}, {"limit": 0}, {"position": "BN"},
                                      {"league_team_id": "x" * 65}],
    "/leagues/{league_id}/ros": [{"availability": "x"}, {"offset": -5}],
    "/sleeper/users/{username}/leagues": [{"season": 2016}, {"season": "now"}],
    "/players": [{}, {"search": "x"}, {"search": "x" * 61}, {"search": "ma", "limit": 51},
                 {"search": "ma", "limit": 0}, {"search": "ma", "position": "FLEX"}],
}


def test_bad_query_values_are_422(client):
    lid = client.post("/leagues", json=LEAGUE).json()["id"]
    present = {p for _m, p in _api_routes(client)}
    problems = []
    for template, queries in BAD_QUERIES.items():
        if template not in present:  # a route added later (GET /players) is checked once it exists
            continue
        path = template.replace("{league_id}", lid).replace("{username}", USERNAME)
        for params in queries:
            r = client.get(path, params=params)
            if r.status_code != 422:
                problems.append(f"GET {template} {params}: {r.status_code} {r.text[:80]}")
            else:
                _assert_clean_error(r, {422})
    assert problems == []


def test_unknown_query_parameters_are_ignored_not_errors(client):
    r = client.get("/projections", params={"utm_source": "x", "__proto__": "y"})
    assert r.status_code == 200


# --------------------------------------------------------------------------- Sleeper inputs


def _sleeper_calls_to(sleeper) -> list[str]:
    return [str(c.request.url) for c in sleeper.calls]


def test_hostile_usernames_never_reach_sleeper(client, sleeper):
    problems = []
    for name in HOSTILE_USERNAMES:
        before = len(sleeper.calls)
        r = client.post("/leagues/import/sleeper", json={"username": name})
        if r.status_code != 422 or len(sleeper.calls) != before:
            problems.append(f"import username={name!r}: {r.status_code} calls={_sleeper_calls_to(sleeper)[before:]}")
        r = client.get(f"/sleeper/users/{quote(name, safe='')}/leagues")
        if r.status_code not in (404, 405, 422) or len(sleeper.calls) != before:
            problems.append(f"lookup username={name!r}: {r.status_code} calls={_sleeper_calls_to(sleeper)[before:]}")
    assert problems == []


def test_hostile_league_ids_never_reach_sleeper(client, sleeper):
    problems = []
    for value in HOSTILE_LEAGUE_IDS:
        before = len(sleeper.calls)
        r = client.post("/leagues/import/sleeper", json={"league_id": value})
        if r.status_code != 422 or len(sleeper.calls) != before:
            problems.append(f"league_id={value!r}: {r.status_code} {_sleeper_calls_to(sleeper)[before:]}")
    assert problems == []


def test_surrounding_whitespace_is_stripped_before_sleeper_is_called(client, sleeper):
    before = len(sleeper.calls)
    client.post("/leagues/import/sleeper", json={"username": f" {USERNAME}\r\n"})
    urls = _sleeper_calls_to(sleeper)[before:]
    assert urls and urls[0] == f"{BASE}/user/{USERNAME}", urls


def test_accepted_inputs_reach_exactly_the_expected_sleeper_urls(client, sleeper):
    r = client.post("/leagues/import/sleeper", json={"league_id": LEAGUE_ID})
    assert r.status_code == 201, r.text
    r = client.get(f"/sleeper/users/{USERNAME}/leagues", params={"season": 2026})
    assert r.status_code == 200, r.text
    allowed = re.compile(
        rf"^{re.escape(BASE)}/(?:state/nfl|user/(?:{USERNAME}|\d+)(?:/leagues/nfl/\d{{4}})?"
        rf"|league/{LEAGUE_ID}(?:/rosters|/users)?)$"
    )
    urls = _sleeper_calls_to(sleeper)
    assert urls and all(allowed.match(u) for u in urls), urls
    assert all(c.request.method == "GET" for c in sleeper.calls)  # read-only (D-020)


@pytest.mark.parametrize("name", ["١٢٣", "café", "Ａdmin"])
def test_lookup_refuses_non_ascii_usernames(client, sleeper, name):
    before = len(sleeper.calls)
    r = client.get(f"/sleeper/users/{quote(name, safe='')}/leagues")
    assert r.status_code == 422 and len(sleeper.calls) == before, (r.status_code, r.text)


# --------------------------------------------------------------------------- cross-site requests


@pytest.mark.parametrize(
    "headers",
    [{"content-type": "text/plain"}, {"content-type": "application/x-www-form-urlencoded"},
     {"content-type": "multipart/form-data; boundary=x"}, {}],
)
def test_simple_cross_site_bodies_cannot_create_leagues(client, headers):
    """A cross-site form or no-cors fetch carries no JSON content type; the API refuses it."""
    req = client.build_request("POST", "/leagues", content=json.dumps(LEAGUE).encode(),
                               headers={**headers, "origin": "https://evil.example"})
    if not headers:
        req.headers.pop("content-type", None)
    r = client.send(req)
    assert r.status_code == 422, r.text
    assert client.get("/leagues").json()["leagues"] == []


# --------------------------------------------------------------------------- size limits


def test_long_lists_and_strings_are_refused(client):
    r = client.post("/leagues", json={**LEAGUE, "teams": [{"name": "t"}] * 33})
    _assert_clean_error(r, {422})
    r = client.post("/leagues", json={**LEAGUE, "roster_positions": ["Q" * 100_000]})
    _assert_clean_error(r, {422})


def test_oversized_body_is_refused(client):
    body = {**LEAGUE, "settings": {"notes": "x" * 2_000_000}}
    r = client.post("/leagues", json=body)
    assert r.status_code in (413, 422), r.status_code
    assert client.get("/leagues").json()["leagues"] == []


def test_free_form_setting_values_are_bounded(client):
    r = client.post("/leagues", json={**LEAGUE, "settings": {"playoff_week_start": "x" * 10_000}})
    assert r.status_code == 422, r.status_code


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "1e309"])
def test_non_finite_scoring_weights_are_refused(client, literal):
    raw = json.dumps({**LEAGUE, "scoring": {"preset": "ppr", "overrides": {"pass_td": 0.5}}})
    raw = raw.replace('"pass_td": 0.5', f'"pass_td": {literal}')
    r = client.post("/leagues", content=raw.encode(), headers={"content-type": "application/json"})
    assert r.status_code == 422, (r.status_code, r.text[:200])


def test_nan_scoring_weight_is_refused(client):
    raw = json.dumps({**LEAGUE, "scoring": {"preset": "ppr", "overrides": {"pass_td": 0.5}}})
    raw = raw.replace('"pass_td": 0.5', '"pass_td": NaN')
    r = client.post("/leagues", content=raw.encode(), headers={"content-type": "application/json"})
    _assert_clean_error(r, {422})


def test_internal_errors_are_generic(client, monkeypatch):
    def boom() -> dict:
        raise RuntimeError("secret detail /etc/ffml password=hunter2")

    monkeypatch.setattr(client.app.state.season, "describe", boom)
    from fastapi.testclient import TestClient

    quiet = TestClient(client.app, raise_server_exceptions=False)
    r = quiet.get("/meta/season")
    assert r.status_code == 500
    assert r.json() == {"error": {"code": "internal_error",
                                  "message": "Something went wrong on the server.",
                                  "details": None}}
    assert "hunter2" not in r.text and "Traceback" not in r.text
