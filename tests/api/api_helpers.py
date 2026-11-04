"""Shared helpers for API tests."""

from __future__ import annotations

from typing import Any

from fastapi.testclient import TestClient

from api.routers.projections import PROJECTION_ROUTES

NO_MODEL_BODY = {
    "status": "no_approved_model",
    "message": "No approved model. Projections are off until a model passes its pre-registered test.",
}

DUMMY_LEAGUE = "00000000-0000-0000-0000-000000000000"
BODIES: dict[str, dict[str, Any]] = {
    "/leagues/{league_id}/start-sit": {"player_ids": ["00-0000001", "00-0000002"]},
    "/leagues/{league_id}/trades/evaluate": {"give": ["00-0000001"], "receive": ["00-0000002"]},
}


def call(client: TestClient, method: str, template: str, league_id: str = DUMMY_LEAGUE,
         body: dict[str, Any] | None = None, params: dict[str, Any] | None = None):
    path = template.replace("{league_id}", league_id)
    if method == "GET":
        return client.get(path, params=params)
    return client.post(path, json=body if body is not None else BODIES.get(template, {}))


def all_routes() -> list[tuple[str, str]]:
    return list(PROJECTION_ROUTES)


def strings(value: Any) -> list[str]:
    """Every string value (not key) inside a JSON document."""
    if isinstance(value, str):
        return [value]
    if isinstance(value, dict):
        return [s for v in value.values() for s in strings(v)]
    if isinstance(value, list):
        return [s for v in value for s in strings(v)]
    return []


def assert_never_approved(response) -> None:
    """private_test rule: no 'approved' status anywhere in the body or the header."""
    assert response.headers.get("X-FFML-Approval") == "not-approved"
    try:
        body = response.json()
    except ValueError:
        return
    for s in strings(body):
        assert s.strip().lower() != "approved", f"'approved' emitted in private_test: {body}"
    if isinstance(body, dict) and "approval_status" in body:
        assert body["approval_status"] == "not approved"


def assert_labeled(response) -> None:
    body = response.json()
    assert body["approval_status"] == "not approved"
    assert body["label"] == "not approved"
    assert_never_approved(response)
