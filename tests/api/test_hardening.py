"""BE-4.10: fixes for the QA findings in docs/SECURITY.md section 1 (backend-owned tests)."""

from __future__ import annotations

import pytest
import respx
from sleeper_mock import USERNAME, directory, mock_sleeper, no_sleep_factory

from api.middleware import Limit, route_family

BASE_LEAGUE = {
    "name": "Hardening league",
    "season": 2026,
    "num_teams": 2,
    "scoring": {"preset": "ppr"},
    "roster_positions": ["QB", "RB", "WR", "TE", "FLEX", "K", "DEF", "BN"],
}


# --------------------------------------------------------------------------- QA-F02 body size


def test_oversized_declared_body_is_413(make_client):
    client = make_client()
    big = b'{"name": "' + b"x" * (300 * 1024) + b'"}'
    r = client.post("/leagues", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "payload_too_large"
    assert r.headers["X-Content-Type-Options"] == "nosniff"  # headers middleware wraps it
    assert client.get("/leagues").json()["leagues"] == []


def test_oversized_streamed_body_is_413(make_client):
    client = make_client()

    def chunks():
        for _ in range(30):  # 300 KiB, chunked (no Content-Length)
            yield b"x" * 10240

    r = client.post("/leagues", content=chunks(), headers={"content-type": "application/json"})
    assert r.status_code == 413, r.text
    assert r.json()["error"]["code"] == "payload_too_large"


def test_body_limit_is_configurable(make_client):
    small = make_client(body_max_bytes=100)
    r = small.post("/leagues", json={**BASE_LEAGUE, "name": "x" * 50})
    assert r.status_code == 413
    off = make_client(body_max_bytes=0)
    big = {**BASE_LEAGUE, "name": "y" * 50, "settings": {"status": "s"}}
    assert off.post("/leagues", json=big).status_code == 201
    normal = make_client()
    assert normal.post("/leagues", json=BASE_LEAGUE).status_code == 201


@pytest.mark.parametrize(
    "settings,status",
    [
        ({"playoff_week_start": 15, "status": "in_season", "best_ball": False}, 201),
        ({"waiver_budget": 100.5, "trade_deadline": None}, 201),
        ({"playoff_week_start": "x" * 10_000}, 422),
        ({"notes": "free-form keys are refused"}, 422),
        ({"playoff_week_start": 10**9}, 422),
        ({"playoff_week_start": [15]}, 422),
        ({"status": "s" * 101}, 422),
    ],
)
def test_league_settings_are_bounded(make_client, settings, status):
    client = make_client()
    r = client.post("/leagues", json={**BASE_LEAGUE, "settings": settings})
    assert r.status_code == status, r.text
    if status == 201:
        assert r.json()["settings"] == settings
    else:
        assert r.json()["error"]["code"] == "validation_error"
        assert len(r.text) < 4000  # no echo of large inputs


def test_settings_update_is_bounded_too(make_client):
    client = make_client()
    lg = client.post("/leagues", json=BASE_LEAGUE).json()
    bad = client.put(f"/leagues/{lg['id']}/settings", json={"settings": {"x": 1}})
    assert bad.status_code == 422
    ok = client.put(f"/leagues/{lg['id']}/settings", json={"settings": {"playoff_week_start": 16}})
    assert ok.status_code == 200 and ok.json()["settings"]["playoff_week_start"] == 16


# --------------------------------------------------------------------------- QA-F04 shared Sleeper client


def test_sleeper_client_is_process_wide_and_closed_at_shutdown(make_client):
    with respx.mock(assert_all_called=False) as router:
        mock_sleeper(router)
        client = make_client()
        client.app.state.players.set(directory())
        client.app.state.sleeper_factory = no_sleep_factory
        for _ in range(3):
            r = client.get(f"/sleeper/users/{USERNAME}/leagues", params={"season": 2026})
            assert r.status_code == 200, r.text
        holder = client.app.state.sleeper
        shared = holder.get(no_sleep_factory)
        assert holder.get(lambda: None) is shared  # built once; later factories are ignored
        http = shared._client
        assert not http.is_closed
        client.__exit__(None, None, None)  # lifespan shutdown closes it
        assert http.is_closed
        client.__enter__()  # the fixture tears the client down again


# --------------------------------------------------------------------------- QA-F03 rate limits


def _limited(make_client, **overrides):
    client = make_client(
        rate_limit_enabled=True,
        rate_limit_meta="60/2",
        rate_limit_read="60/2",
        rate_limit_sleeper="60/1",
        rate_limit_write="60/2",
        rate_limit_heavy="60/1",
        **overrides,
    )
    now = [1000.0]
    client.app.state.rate_limiter.clock = lambda: now[0]
    return client, now


def test_rate_limit_429_with_retry_after_and_refill(make_client):
    client, now = _limited(make_client)
    assert client.get("/meta/health").status_code == 200
    assert client.get("/meta/health").status_code == 200
    r = client.get("/meta/health")
    assert r.status_code == 429, r.text
    assert r.headers["Retry-After"] == "1"  # 60 per minute: one token per second
    err = r.json()["error"]
    assert err["code"] == "rate_limited" and err["details"]["family"] == "meta"
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"
    now[0] += 1.0
    assert client.get("/meta/health").status_code == 200
    assert client.get("/meta/health").status_code == 429


def test_rate_limit_families_are_independent_and_run_before_the_gate(make_client):
    client, _ = _limited(make_client)
    for _ in range(2):
        client.get("/meta/health")
    assert client.get("/meta/health").status_code == 429
    assert client.get("/players", params={"search": "ab"}).status_code == 200  # read family
    assert client.get("/projections").status_code == 503  # gate, counted as read
    assert client.get("/projections").status_code == 429  # read bucket empty before the gate
    assert client.post("/leagues/import/sleeper", json={}).status_code == 422  # sleeper family
    assert client.post("/leagues/import/sleeper", json={}).status_code == 429
    assert client.post("/leagues", json={}).status_code == 422  # write family, still open
    r = client.options(
        "/leagues",
        headers={"Origin": "http://localhost:3000", "Access-Control-Request-Method": "POST"},
    )
    assert r.status_code != 429  # preflights are not counted


def test_rate_limit_is_per_client_behind_a_trusted_proxy(make_client):
    client, _ = _limited(make_client, trusted_proxies=["testclient"])
    a = {"X-Forwarded-For": "203.0.113.1, 10.0.0.2"}
    b = {"X-Forwarded-For": "203.0.113.2"}
    assert [client.get("/meta/health", headers=a).status_code for _ in range(3)] == [200, 200, 429]
    assert client.get("/meta/health", headers=b).status_code == 200
    assert client.get("/meta/health").status_code == 200  # the proxy itself is a client too
    untrusted, _ = _limited(make_client)  # no trusted proxies: the header is ignored
    codes = [
        untrusted.get("/meta/health", headers={"X-Forwarded-For": f"203.0.113.{i}"}).status_code
        for i in range(3)
    ]
    assert codes == [200, 200, 429]


def test_rate_limits_are_off_in_tests_and_on_by_default(make_client):
    client = make_client()
    assert not client.app.state.rate_limiter.enabled
    assert all(client.get("/meta/health").status_code == 200 for _ in range(70))
    from api.settings import ApiSettings

    defaults = ApiSettings(app_env="production")
    assert defaults.rate_limit_enabled and defaults.rate_limit_sleeper == "10/5"


def test_limit_parsing_and_route_families():
    assert Limit.parse("10/5") == Limit(10.0, 5)
    assert Limit.parse("12") == Limit(12.0, 2)  # burst defaults to a tenth of a minute
    assert not Limit.parse("0").enabled and not Limit.parse("").enabled
    cases = {
        ("POST", "/leagues/import/sleeper"): "sleeper",
        ("POST", "/leagues/abc/sync"): "sleeper",
        ("GET", "/sleeper/users/x/leagues"): "sleeper",
        ("POST", "/leagues"): "write",
        ("PUT", "/leagues/abc/settings"): "write",
        ("DELETE", "/leagues/abc"): "write",
        ("GET", "/leagues/abc/waivers"): "heavy",
        ("POST", "/leagues/abc/trades/evaluate"): "heavy",
        ("GET", "/leagues/abc/projections"): "read",
        ("POST", "/leagues/abc/start-sit"): "read",
        ("GET", "/leagues/abc/ros"): "read",
        ("GET", "/leagues"): "read",
        ("GET", "/projections"): "read",
        ("GET", "/players"): "read",
        ("GET", "/meta/health"): "meta",
        ("GET", "/docs"): "meta",
    }
    assert {k: route_family(*k) for k in cases} == cases
