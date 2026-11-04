"""``/meta/*``: health, approval status and app information (BE-4.8, docs/API.md section 5)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import APIRouter, Request

from api import __version__
from api.approval import manifest_field
from api.services import meta_content

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


@router.get("/attribution")
def attribution(request: Request) -> dict[str, Any]:
    """Every data source and license, FTN wording included (D-009); no consensus data (D-007)."""
    return meta_content.attribution(request.app.state.settings.ftn_features_enabled)


@router.get("/model-card")
def model_card(request: Request) -> dict[str, Any]:
    state = request.app.state.gate.state()
    card: dict[str, Any] = dict(meta_content.MODEL_CARD)
    card["environment"] = state.environment
    card["approval_status"] = state.approval_status
    card["label"] = state.label
    card["serving"] = state.serving
    if state.environment == "production":
        card["models"] = registry_summary()
    else:
        card["models"] = []
        card["notice"] = (
            "Private test environment: numbers shown here are labeled not approved, whatever "
            "the model registry says."
        )
    return card


@router.get("/run-info")
def run_info(request: Request) -> dict[str, Any]:
    state = request.app.state.gate.state()
    run = None
    if state.serving and state.source is not None:
        m = state.source.manifest
        ros = manifest_field(m, "rest_of_season")
        run = {
            **state.source.describe(),
            "seeds": m.get("seeds"),
            "as_of": manifest_field(m, "as_of"),
            "mode": manifest_field(m, "mode"),
            "availability_config": manifest_field(m, "availability_config"),
            "rest_of_season_weeks": ros.get("weeks") if isinstance(ros, dict) else None,
        }
    return {
        "environment": state.environment,
        "serving": state.serving,
        "approval_status": state.approval_status,
        "label": state.label,
        "run": run,
        "message": state.message,
    }


@router.get("/season")
def season(request: Request) -> dict[str, Any]:
    """Season mode for the offseason banner (resolved from the schedule, not the calendar)."""
    return request.app.state.season.describe()


def registry_summary() -> list[dict[str, Any]]:
    """Registry entries for the model card (production only)."""
    try:
        from ffml.models.registry import list_models

        models = list_models()
    except Exception:  # noqa: BLE001 - the card still renders without the registry
        return []
    return [
        {
            "name": m.get("name"),
            "version": m.get("version"),
            "registry_status": m.get("status"),
            "created_at": m.get("created_at"),
        }
        for m in models
    ]
