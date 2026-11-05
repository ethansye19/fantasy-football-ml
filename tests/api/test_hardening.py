"""BE-4.10: fixes for the QA findings in docs/SECURITY.md section 1 (backend-owned tests)."""

from __future__ import annotations

import json

import pytest
import respx
from api_factory import APPROVED_LABELS, live_entry, write_registry, write_snapshot
from pydantic import ValidationError
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


# --------------------------------------------------------------------------- QA-F11, F12, F05, F06, F21


def _approved_world(api_dirs, labels):
    write_registry(api_dirs["artifacts"], [live_entry()])
    return write_snapshot(api_dirs["predictions"], labels=labels)


@pytest.mark.parametrize(
    "labels,reason",
    [
        ({**APPROVED_LABELS, "approval_status": "Approved"}, "snapshot_not_approved"),
        ({**APPROVED_LABELS, "approval_status": " approved "}, "snapshot_not_approved"),
        ({**APPROVED_LABELS, "environment": None}, "snapshot_not_live"),
        ({**APPROVED_LABELS, "environment": "Live"}, "snapshot_not_live"),
    ],
)
def test_labels_are_compared_exactly(make_client, api_dirs, labels, reason):
    _approved_world(api_dirs, labels)
    meta = make_client().get("/meta/approval").json()
    assert (meta["serving"], meta["reason"]) == (False, reason)


def test_exact_labels_still_serve(make_client, api_dirs):
    _approved_world(api_dirs, APPROVED_LABELS)
    assert make_client().get("/meta/approval").json()["serving"] is True


@pytest.mark.parametrize("models", ["live", [None], [1], [[]], ["live"], [{"status": "live"}]])
def test_model_card_skips_malformed_registry_entries(make_client, api_dirs, models):
    path = api_dirs["artifacts"] / "models" / "REGISTRY.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps({"format": "ffml.registry/1", "models": models}), encoding="utf-8")
    r = make_client().get("/meta/model-card")
    assert r.status_code == 200, r.text
    assert all(isinstance(m, dict) and "registry_status" in m for m in r.json()["models"])


@pytest.mark.parametrize("literal", ["Infinity", "-Infinity", "NaN", "1e309", "101", "-100.5"])
def test_scoring_weights_must_be_finite_and_bounded(make_client, literal):
    client = make_client()
    raw = json.dumps({**BASE_LEAGUE, "scoring": {"preset": "ppr", "overrides": {"pass_td": 0.5}}})
    raw = raw.replace('"pass_td": 0.5', f'"pass_td": {literal}')
    r = client.post("/leagues", content=raw.encode(), headers={"content-type": "application/json"})
    assert r.status_code == 422, (literal, r.text[:200])
    ok = client.post("/leagues", json={**BASE_LEAGUE, "scoring": {"preset": "ppr", "overrides": {"pass_td": 6}}})
    assert ok.status_code == 201


@pytest.mark.parametrize("name", ["caf%C3%A9", "%D9%A1%D9%A2%D9%A3", "a%20b", "x" * 41, "a.b"])
def test_username_lookup_is_ascii_only(make_client, name):
    client = make_client()
    r = client.get(f"/sleeper/users/{name}/leagues")
    assert r.status_code == 422, (name, r.status_code)
    assert r.json()["error"]["code"] == "validation_error"


def test_production_refuses_wildcard_and_odd_cors_origins():
    from api.settings import ApiSettings

    for bad in (["*"], ["http://localhost:3000", "*"], ["ftp://x"], ["localhost:3000"], ["http://a/b"]):
        with pytest.raises(ValidationError):
            ApiSettings(app_env="production", cors_origins=bad)
    ok = ApiSettings(app_env="production", cors_origins=["http://localhost:3000", "https://ffml.example"])
    assert len(ok.cors_origins) == 2
    assert ApiSettings(app_env="private_test", cors_origins=["*"]).cors_origins == ["*"]


# --------------------------------------------------------------------------- QA-F08 headers and docs pages


def test_docs_pages_are_off_in_production_and_on_in_private_test(make_client):
    prod = make_client()
    for path in ("/docs", "/redoc", "/openapi.json"):
        assert prod.get(path).status_code == 404, path
    private = make_client(app_env="private_test")
    assert private.get("/openapi.json").status_code == 200
    page = private.get("/docs")
    assert page.status_code == 200
    assert page.headers["Content-Security-Policy"] == "frame-ancestors 'none'"
    assert page.headers["X-Frame-Options"] == "DENY"
    forced_on = make_client(docs_enabled=True)
    assert forced_on.get("/openapi.json").status_code == 200
    forced_off = make_client(app_env="private_test", docs_enabled=False)
    assert forced_off.get("/docs").status_code == 404


def test_json_responses_carry_frame_referrer_and_csp_headers(make_client):
    client = make_client()
    responses = [
        client.get("/meta/health"),
        client.get("/projections"),  # 503 gate
        client.get("/leagues/nope"),  # 404 envelope
        client.post("/leagues", json={}),  # 422
        client.get("/no/such/route"),  # router 404
    ]
    for r in responses:
        assert r.headers["Referrer-Policy"] == "no-referrer", r.url
        assert r.headers["X-Frame-Options"] == "DENY", r.url
        assert r.headers["Content-Security-Policy"] == "default-src 'none'; frame-ancestors 'none'"
        assert r.headers["X-Content-Type-Options"] == "nosniff"
