"""QA-4.1/4.2 rate limits (docs/SECURITY.md section 5).

Inbound limiting is ``api.middleware.RateLimitMiddleware`` (QA-F03, f988e60),
per client and route family, off in the test factory unless a test opts in
with ``rate_limit_enabled=True``. The Backend's own tests
(``tests/api/test_hardening.py``) cover 429 and refill, family independence,
running before the gate, the trusted proxy and the defaults. These tests add
the contract details QA relies on:

- the 429 is the error envelope, with ``Retry-After`` in whole seconds, and
  carries the security headers (and the private_test label);
- CORS preflights are never counted or limited;
- a spoofed ``X-Forwarded-For`` cannot evade a limit unless the peer is a
  trusted proxy;
- the limit runs before validation and before the body limit (cheap check
  first), so a flood of invalid or oversized requests still gets 429;
- a disabled family is unlimited while the others limit;
- after ``Retry-After`` seconds the client is served again (injected clock);
- every OpenAPI operation maps to the family SECURITY.md assigns it (a new
  route must be classified on purpose).

Outbound: a league import makes a bounded number of Sleeper calls and the
Sleeper client and token bucket are process-wide (QA-F04, fbc6fa3).
"""

from __future__ import annotations

import json
import re

import pytest
import respx
from api_factory import PRIVATE_LABELS, write_snapshot
from sleeper_mock import LEAGUE_ID, USERNAME, directory, mock_sleeper

from api.middleware import FAMILIES, route_family
from ffml.data import sleeper as sleeper_module

ALLOWED_ORIGIN = "http://localhost:3000"


@pytest.fixture
def sleeper():
    with respx.mock(assert_all_called=False) as router:
        mock_sleeper(router)
        yield router


@pytest.fixture
def client(make_client, sleeper):
    c = make_client()
    c.app.state.players.set(directory())
    return c  # the app's own Sleeper client, as in production


# --------------------------------------------------------------------------- outbound


def test_one_import_makes_a_bounded_number_of_sleeper_calls(client, sleeper):
    r = client.post("/leagues/import/sleeper", json={"league_id": LEAGUE_ID})
    assert r.status_code == 201, r.text
    urls = [str(c.request.url) for c in sleeper.calls]
    assert 1 <= len(urls) <= 6, urls
    assert not any(u.endswith("/players/nfl") for u in urls)  # no 14.7 MB map per request


def test_one_lookup_makes_at_most_two_sleeper_calls(client, sleeper):
    r = client.get(f"/sleeper/users/{USERNAME}/leagues", params={"season": 2026})
    assert r.status_code == 200, r.text
    assert 1 <= len(sleeper.calls) <= 2


def test_sleeper_budget_is_shared_across_requests(client, sleeper, monkeypatch):
    """D-025: one token bucket per process (QA-F04)."""
    buckets: list[object] = []  # the objects themselves, so no id() is reused after GC
    original = sleeper_module.TokenBucket.acquire

    def acquire(self, *args, **kwargs):
        buckets.append(self)
        return original(self, *args, **kwargs)

    monkeypatch.setattr(sleeper_module.TokenBucket, "acquire", acquire)
    for _ in range(3):
        r = client.get(f"/sleeper/users/{USERNAME}/leagues", params={"season": 2026})
        assert r.status_code == 200
    assert len(buckets) >= 3
    assert len({id(b) for b in buckets}) == 1


# --------------------------------------------------------------------------- inbound (QA-F03)


def _limited(make_client, **overrides):
    values = {
        "rate_limit_enabled": True,
        "rate_limit_sleeper": "0",
        "rate_limit_write": "0",
        "rate_limit_heavy": "0",
        "rate_limit_read": "0",
        "rate_limit_meta": "0",
    }
    values.update(overrides)
    return make_client(**values)


def _assert_429(r, family: str) -> int:
    assert r.status_code == 429, (r.status_code, r.text[:200])
    body = r.json()
    retry = body["error"]["details"]["retry_after_seconds"]
    assert body == {
        "error": {
            "code": "rate_limited",
            "message": f"Too many {family} requests. Try again in {retry} s.",
            "details": {"family": family, "retry_after_seconds": retry},
        }
    }
    assert re.fullmatch(r"[1-9][0-9]*", r.headers["Retry-After"])
    assert int(r.headers["Retry-After"]) == retry
    assert r.headers["X-Content-Type-Options"] == "nosniff"
    assert r.headers["Cache-Control"] == "no-store"
    assert r.headers["Content-Type"].startswith("application/json")
    assert "Traceback" not in r.text
    return retry


def test_429_is_the_error_envelope_with_security_headers(make_client):
    client = _limited(make_client, rate_limit_read="60/2")
    assert client.get("/projections").status_code == 503  # gate, bucket 2 -> 1
    assert client.get("/projections").status_code == 503  # bucket 1 -> 0
    retry = _assert_429(client.get("/projections"), "read")
    assert retry >= 1
    assert client.get("/meta/health").status_code == 200  # meta family unlimited here


def test_429_in_private_test_is_labeled_not_approved(make_client, api_dirs):
    write_snapshot(api_dirs["fixtures"], labels=PRIVATE_LABELS, n_sims=100)
    client = _limited(
        make_client, app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"],
        rate_limit_read="60/1",
    )
    assert client.get("/projections").status_code == 200
    r = client.get("/projections")
    _assert_429(r, "read")
    assert r.headers["X-FFML-Approval"] == "not-approved"
    assert "approved" not in json.dumps(r.json()).replace("not-approved", "")


def test_cors_preflights_are_never_counted_or_limited(make_client):
    client = _limited(make_client, rate_limit_read="60/1")
    for _ in range(5):
        r = client.options("/projections", headers={"Origin": ALLOWED_ORIGIN,
                                                    "Access-Control-Request-Method": "GET"})
        assert r.status_code == 200, r.text
        assert r.headers["access-control-allow-origin"] == ALLOWED_ORIGIN
    assert client.get("/projections").status_code == 503  # the single token is still there
    _assert_429(client.get("/projections"), "read")
    r = client.options("/projections", headers={"Origin": ALLOWED_ORIGIN,
                                                "Access-Control-Request-Method": "GET"})
    assert r.status_code == 200  # preflights keep working while the client is limited


def test_spoofed_x_forwarded_for_cannot_evade_the_limit(make_client):
    client = _limited(make_client, rate_limit_read="60/1")  # no trusted proxies
    assert client.get("/projections").status_code == 503
    for forwarded in ("203.0.113.9", "10.0.0.1, 203.0.113.9", "", "evil"):
        _assert_429(client.get("/projections", headers={"X-Forwarded-For": forwarded}), "read")


def test_trusted_proxy_separates_clients_and_ignores_later_hops(make_client):
    client = _limited(make_client, rate_limit_read="60/1", trusted_proxies=["testclient"])
    a = {"X-Forwarded-For": "203.0.113.9, 10.0.0.1"}
    b = {"X-Forwarded-For": "203.0.113.10, 10.0.0.1"}
    assert client.get("/projections", headers=a).status_code == 503
    _assert_429(client.get("/projections", headers=a), "read")
    assert client.get("/projections", headers=b).status_code == 503  # another client
    assert client.get("/projections").status_code == 503  # the proxy itself, without the header


def test_limit_runs_before_validation_and_before_the_body_limit(make_client):
    client = _limited(make_client, rate_limit_write="60/1", body_max_bytes=1024)
    assert client.post("/leagues", json={}).status_code == 422  # consumes the token
    _assert_429(client.post("/leagues", json={}), "write")  # not 422
    big = b"{" + b" " * 4096 + b"}"
    r = client.post("/leagues", content=big, headers={"content-type": "application/json"})
    _assert_429(r, "write")  # not 413: the cheap check comes first
    client.app.state.rate_limiter.reset()
    r = client.post("/leagues", content=big, headers={"content-type": "application/json"})
    assert r.status_code == 413  # with a token, the body limit applies


def test_disabled_family_is_unlimited_while_others_limit(make_client):
    client = _limited(make_client, rate_limit_read="60/1", rate_limit_meta="0")
    for _ in range(40):
        assert client.get("/meta/health").status_code == 200
    assert client.get("/projections").status_code == 503
    _assert_429(client.get("/projections"), "read")
    assert client.get("/meta/approval").status_code == 200  # still unlimited


def test_retry_after_is_honoured(make_client):
    client = _limited(make_client, rate_limit_read="30/1")  # one token, refill every 2 s
    now = [1000.0]
    client.app.state.rate_limiter.clock = lambda: now[0]
    assert client.get("/projections").status_code == 503
    retry = _assert_429(client.get("/projections"), "read")
    assert retry == 2
    now[0] += retry - 0.5
    _assert_429(client.get("/projections"), "read")  # too early
    now[0] += 0.5
    assert client.get("/projections").status_code == 503  # served again
    _assert_429(client.get("/projections"), "read")


def test_rate_limiting_is_on_by_default_with_the_documented_limits():
    from api.middleware import Limit
    from api.settings import ApiSettings

    settings = ApiSettings(app_env="production")
    assert settings.rate_limit_enabled is True
    assert settings.trusted_proxies == []
    assert {f: Limit.parse(getattr(settings, f"rate_limit_{f}")) for f in FAMILIES} == {
        "sleeper": Limit(10.0, 5),
        "write": Limit(30.0, 10),
        "heavy": Limit(30.0, 10),
        "read": Limit(120.0, 30),
        "meta": Limit(300.0, 60),
    }


def _expected_family(method: str, path: str) -> str:
    if path.startswith("/meta"):
        return "meta"
    if path.startswith("/sleeper/") or path == "/leagues/import/sleeper" or path.endswith("/sync"):
        return "sleeper"
    if path.endswith(("/waivers", "/trades/evaluate")):
        return "heavy"
    if path.endswith(("/projections", "/ros", "/start-sit")) or path == "/players":
        return "read"
    if path.startswith("/leagues") and method in ("POST", "PUT", "DELETE", "PATCH"):
        return "write"
    return "read"


def test_every_operation_maps_to_its_documented_family(make_client):
    """A new route lands in a family on purpose (docs/SECURITY.md section 5)."""
    client = make_client()
    ops = [(m.upper(), p) for p, methods in client.app.openapi()["paths"].items() for m in methods]
    assert len(ops) >= 20
    wrong = []
    for method, template in ops:
        path = (template.replace("{league_id}", "6f1c0000-0000-4000-8000-000000000000")
                .replace("{username}", "someone"))
        got = route_family(method, path)
        if got != _expected_family(method, template):
            wrong.append(f"{method} {template}: {got} (expected {_expected_family(method, template)})")
    assert wrong == []
    assert route_family("GET", "/no/such/route") == "read"
    assert route_family("GET", "/docs") == "meta"
