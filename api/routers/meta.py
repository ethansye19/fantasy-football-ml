"""``/meta/*``: health, approval status and app information (BE-4.8, docs/API.md section 5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from api import __version__

router = APIRouter(prefix="/meta", tags=["meta"])


@router.get("/health")
def health(request: Request) -> dict[str, Any]:
    """Liveness and basic checks. Always 200 while the process runs."""
    checks: dict[str, str] = {}
    db = getattr(request.app.state, "db", None)
    if db is not None:
        checks["database"] = "ok" if db.ping() else "error"
    state = request.app.state.gate.state()
    checks["projections"] = "serving" if state.serving else "off"
    status = "ok" if all(v != "error" for v in checks.values()) else "degraded"
    return {
        "status": status,
        "version": __version__,
        "environment": request.app.state.settings.app_env,
        "checks": checks,
        "time": datetime.now(UTC).isoformat(),
    }


@router.get("/approval")
def approval(request: Request) -> dict[str, Any]:
    """Whether projection features are on, and why (D-099)."""
    return request.app.state.gate.state().to_public()
