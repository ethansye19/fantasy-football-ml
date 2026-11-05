"""QA-4.1 CORS policy and security headers of the API (docs/SECURITY.md section 3).

- CORS: an allowlist of origins (``FFML_CORS_ORIGINS``), no wildcard, no
  credentials, only the methods and headers the app uses; disallowed origins
  get no ``Access-Control-Allow-Origin`` on preflight or simple requests.
- Headers on every response, errors included: ``X-Content-Type-Options:
  nosniff``, ``Cache-Control: no-store``, JSON content type; the approval
  header on projection routes; ``X-FFML-Approval: not-approved`` on every
  private_test response (D-099).
"""

from __future__ import annotations

import pytest
from api_factory import PRIVATE_LABELS, write_snapshot
from api_helpers import all_routes, call

from api.settings import ApiSettings

ALLOWED = "http://localhost:3000"
EVIL = ("https://evil.example", "null", "http://localhost:3000.evil.example",
        "http://127.0.0.1:3001", "https://localhost:3000")


def test_default_origins_are_local_only_and_no_wildcard():
    origins = ApiSettings.model_fields["cors_origins"].default
    assert origins and "*" not in origins
    assert all(o.startswith(("http://localhost:", "http://127.0.0.1:")) for o in origins)


@pytest.mark.parametrize("origin", EVIL)
@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_preflight_from_other_origins_is_refused(make_client, origin, method):
    client = make_client()
    r = client.options("/leagues", headers={"Origin": origin,
                                             "Access-Control-Request-Method": method})
    assert "access-control-allow-origin" not in r.headers
    assert r.status_code == 400


@pytest.mark.parametrize("method", ["GET", "POST", "PUT", "DELETE"])
def test_preflight_from_the_app_origin(make_client, method):
    client = make_client()
    r = client.options("/leagues", headers={"Origin": ALLOWED,
                                             "Access-Control-Request-Method": method,
                                             "Access-Control-Request-Headers": "content-type"})
    assert r.status_code == 200
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert "access-control-allow-credentials" not in r.headers
    allowed_methods = {m.strip() for m in r.headers["access-control-allow-methods"].split(",")}
    assert allowed_methods <= {"GET", "POST", "PUT", "DELETE"}
    allowed_headers = r.headers["access-control-allow-headers"].lower()
    assert "authorization" not in allowed_headers and "*" not in allowed_headers


@pytest.mark.parametrize("method", ["PATCH", "TRACE", "CONNECT"])
def test_preflight_for_unused_methods_is_refused(make_client, method):
    client = make_client()
    r = client.options("/leagues", headers={"Origin": ALLOWED,
                                             "Access-Control-Request-Method": method})
    assert r.status_code == 400


def test_preflight_with_credentials_or_custom_headers_is_refused(make_client):
    client = make_client()
    r = client.options("/leagues", headers={"Origin": ALLOWED,
                                             "Access-Control-Request-Method": "POST",
                                             "Access-Control-Request-Headers": "authorization"})
    assert r.status_code == 400


def test_simple_requests_from_other_origins_get_no_cors_grant(make_client):
    client = make_client()
    for origin in EVIL:
        r = client.get("/meta/health", headers={"Origin": origin})
        assert r.status_code == 200
        assert "access-control-allow-origin" not in r.headers
    r = client.get("/meta/health", headers={"Origin": ALLOWED})
    assert r.headers["access-control-allow-origin"] == ALLOWED
    assert "origin" in r.headers.get("vary", "").lower()
    assert "x-ffml-approval" in r.headers["access-control-expose-headers"].lower()


def test_wildcard_origin_setting_never_allows_credentials(make_client):
    """Production refuses ``["*"]`` (QA-F21); private_test still never adds credentials."""
    import pytest
    from pydantic import ValidationError

    from api.settings import ApiSettings

    with pytest.raises(ValidationError):
        ApiSettings(app_env="production", cors_origins=["*"])
    client = make_client(app_env="private_test", cors_origins=["*"])
    r = client.get("/meta/health", headers={"Origin": "https://evil.example"})
    assert r.headers.get("access-control-allow-origin") == "*"
    assert "access-control-allow-credentials" not in r.headers


# --------------------------------------------------------------------------- headers


def _sample_requests(client) -> list[tuple[str, str, dict | None]]:
    out: list[tuple[str, str, dict | None]] = [
        ("GET", "/meta/health", None),
        ("GET", "/meta/approval", None),
        ("GET", "/meta/attribution", None),
        ("GET", "/meta/model-card", None),
        ("GET", "/meta/run-info", None),
        ("GET", "/meta/season", None),
        ("GET", "/leagues", None),
        ("GET", "/leagues/not-a-uuid", None),  # 404 envelope
        ("GET", "/no/such/route", None),  # 404 from the router
        ("PATCH", "/leagues", None),  # 405
        ("POST", "/leagues", {"name": ""}),  # 422
    ]
    out += [(m, t.replace("{league_id}", "00000000-0000-0000-0000-000000000000"), None)
            for m, t in all_routes()]
    return out


def _send(client, method, path, body):
    if body is not None:
        return client.request(method, path, json=body)
    if method in ("POST", "PUT"):
        return client.request(method, path, json={})
    return client.request(method, path)


@pytest.mark.parametrize("app_env", ["production", "private_test"])
def test_every_response_carries_the_security_headers(make_client, api_dirs, app_env):
    kwargs = {}
    if app_env == "private_test":
        write_snapshot(api_dirs["fixtures"], labels=PRIVATE_LABELS, n_sims=100)
        kwargs["api_fixture_predictions_dir"] = api_dirs["fixtures"]
    client = make_client(app_env=app_env, **kwargs)
    problems = []
    for method, path, body in _sample_requests(client):
        r = _send(client, method, path, body)
        h = {k.lower(): v for k, v in r.headers.items()}
        where = f"{app_env} {method} {path} ({r.status_code})"
        if h.get("x-content-type-options") != "nosniff":
            problems.append(f"{where}: x-content-type-options {h.get('x-content-type-options')}")
        if h.get("cache-control") != "no-store":
            problems.append(f"{where}: cache-control {h.get('cache-control')}")
        if r.status_code != 204 and not h.get("content-type", "").startswith("application/json"):
            problems.append(f"{where}: content-type {h.get('content-type')}")
        if app_env == "private_test" and h.get("x-ffml-approval") != "not-approved":
            problems.append(f"{where}: x-ffml-approval {h.get('x-ffml-approval')}")
        if "server" in h and any(ch.isdigit() for ch in h["server"]):
            problems.append(f"{where}: server header leaks a version {h['server']}")
    assert problems == []


def test_gated_routes_label_their_responses_in_production(make_client):
    client = make_client()
    for method, template in all_routes():
        r = call(client, method, template)
        assert r.headers["X-FFML-Approval"] == "none"
