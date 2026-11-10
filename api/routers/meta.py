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
        card["models"] = registry_summary(state)
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
        "test_data_label": state.test_data_label,
        "run": run,
        "message": state.message,
    }


@router.get("/season")
def season(request: Request) -> dict[str, Any]:
    """Current season, week and season type: Sleeper, cross-checked with the schedule (D-124)."""
    return request.app.state.season.describe()


#: Registry status -> model-card word. The bare word "approved" is never emitted: an
#: approved or live registry entry is "awaiting_release" unless the gate is serving it.
REGISTRY_STATUS_WORDS = {
    "candidate": "candidate",
    "rejected": "rejected",
    "retired": "retired",
    "approved": "awaiting_release",
    "live": "awaiting_release",
}


def registry_status_word(entry: dict[str, Any], state: Any) -> str:
    raw = entry.get("status")
    if raw == "live" and state.serving and state.live_model_version == entry.get("version"):
        return "live"
    return REGISTRY_STATUS_WORDS.get(str(raw), "unknown")


def registry_summary(state: Any) -> list[dict[str, Any]]:
    """Registry entries for the model card (production only)."""
    try:
        from ffml.models.registry import load_registry

        models = load_registry().get("models")
    except Exception:  # noqa: BLE001 - the card still renders without the registry
        return []
    if not isinstance(models, list):
        return []
    out = []
    for m in models:
        if not isinstance(m, dict):  # QA-F12: hand-edited registries with non-object entries
            continue
        word = registry_status_word(m, state)
        out.append(
            {
                "name": m.get("name"),
                "version": m.get("version"),
                "registry_status": word,
                "serving": word == "live",
                "created_at": m.get("created_at"),
            }
        )
    return out
