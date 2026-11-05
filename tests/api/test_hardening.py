"""BE-4.10: fixes for the QA findings in docs/SECURITY.md section 1 (backend-owned tests)."""

from __future__ import annotations

import pytest

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
