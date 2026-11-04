"""Route class and dependencies that enforce the approval gate (api.approval).

:class:`GatedRoute` checks the gate **before** FastAPI parses the body or
validates parameters, so in production every projection route answers 503
``no_approved_model`` for any request, valid or not, until an approved live
model is published. When the gate allows serving, the route handler runs and
the response gets the ``X-FFML-Approval`` header.
"""

from __future__ import annotations

from collections.abc import Callable, Coroutine
from typing import Annotated, Any

from fastapi import Depends, Request, Response
from fastapi.responses import JSONResponse
from fastapi.routing import APIRoute

from api.approval import HEADER, ApprovalGate, ApprovalState


def gate_of(request: Request) -> ApprovalGate:
    return request.app.state.gate


def blocked_response(state: ApprovalState) -> JSONResponse:
    return JSONResponse(
        status_code=503,
        content=state.blocked_body(),
        headers={HEADER: state.header_value, "Cache-Control": "no-store"},
    )


class GatedRoute(APIRoute):
    """An APIRoute whose handler refuses to run unless the approval gate serves."""

    def get_route_handler(self) -> Callable[[Request], Coroutine[Any, Any, Response]]:
        original = super().get_route_handler()

        async def handler(request: Request) -> Response:
            state = gate_of(request).state()
            if not state.serving or state.source is None:
                return blocked_response(state)
            request.state.approval = state
            response = await original(request)
            response.headers[HEADER] = state.header_value
            return response

        return handler


def approval_state(request: Request) -> ApprovalState:
    """Dependency: the gate state the GatedRoute stored for this request."""
    state = getattr(request.state, "approval", None)
    if state is None:  # pragma: no cover - only reachable if a route forgets GatedRoute
        state = gate_of(request).state()
        if not state.serving:
            raise RuntimeError("projection route without GatedRoute")
    return state


#: Endpoint parameter type: ``state: Approval``.
Approval = Annotated[ApprovalState, Depends(approval_state)]

__all__ = ["Approval", "GatedRoute", "approval_state", "blocked_response", "gate_of"]
