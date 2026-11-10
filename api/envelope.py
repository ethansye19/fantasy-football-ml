"""Common top-level fields of every projection-derived response (docs/API.md section 3.3)."""

from __future__ import annotations

from typing import Any

from api.approval import NOT_APPROVED, NOT_APPROVED_NOTICE, ApprovalState
from api.settings import PRIVATE_TEST

NO_GUARANTEES = (
    "No guarantees: these are probabilities from simulations of possible outcomes, not "
    "predictions of what will happen. Use them as one input to your own decision."
)


def notices(state: ApprovalState) -> list[str]:
    out: list[str] = []
    src = state.source
    label = src.test_data_label if src is not None else None
    if label:  # replays and fixtures: "TEST DATA: ..." first (D-124)
        out.append(label)
    if state.environment == PRIVATE_TEST:
        out.append(NOT_APPROVED_NOTICE)
    if src is not None and src.is_replay:
        out.append(
            f"Historical replay of {src.season} week {src.week}: the model only saw information "
            f"available before that week. Rosters are today's; player teams and games are from "
            f"{src.season}."
        )
    if src is not None and src.kind == "fixture":
        out.append("Fixture data for testing; not real projections.")
    return out


def envelope(state: ApprovalState, **payload: Any) -> dict[str, Any]:
    """``approval_status``, ``label``, ``environment``, ``run`` and ``notices`` plus ``payload``."""
    status = state.approval_status
    if state.environment == PRIVATE_TEST:
        status = NOT_APPROVED  # never anything else in private_test (D-099)
    out: dict[str, Any] = {
        "approval_status": status,
        "label": status,
        "environment": state.environment,
        "run": state.source.describe() if state.source is not None else None,
        "notices": notices(state),
    }
    out.update(payload)
    return out


__all__ = ["NO_GUARANTEES", "envelope", "notices"]
