"""Error shapes (docs/API.md section 4).

Every error except the projection gate's 503 uses one envelope::

    {"error": {"code": "league_not_found", "message": "...", "details": null}}

The projection gate's 503 body is fixed by the owner (D-099)::

    {"status": "no_approved_model",
     "message": "No approved model. Projections are off until a model passes its pre-registered test."}
"""

from __future__ import annotations

import logging
from typing import Any

from fastapi import FastAPI, Request
from fastapi.encoders import jsonable_encoder
from fastapi.exceptions import RequestValidationError
from fastapi.responses import JSONResponse
from starlette.exceptions import HTTPException as StarletteHTTPException

log = logging.getLogger(__name__)


class ApiError(Exception):
    """An error with an HTTP status, a stable machine code and a readable message."""

    def __init__(
        self, status_code: int, code: str, message: str, details: Any = None
    ) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.code = code
        self.message = message
        self.details = details


MAX_MESSAGE = 500
MAX_DETAIL_TEXT = 200
MAX_DETAIL_ITEMS = 50


def trim(value: Any, text: int = MAX_DETAIL_TEXT, items: int = MAX_DETAIL_ITEMS) -> Any:
    """Shorten strings and lists inside an error payload (QA-F07: no echo amplification)."""
    if isinstance(value, str):
        return value if len(value) <= text else value[: text - 3] + "..."
    if isinstance(value, dict):
        return {str(k)[:64]: trim(v, text, items) for k, v in list(value.items())[:items]}
    if isinstance(value, list | tuple):
        out = [trim(v, text, items) for v in value[:items]]
        if len(value) > items:
            out.append(f"... {len(value) - items} more")
        return out
    return value


def error_body(code: str, message: str, details: Any = None) -> dict[str, Any]:
    return {"error": {"code": code, "message": trim(message, MAX_MESSAGE), "details": trim(details)}}


_HTTP_CODES = {
    400: "bad_request",
    401: "unauthorized",
    403: "forbidden",
    404: "not_found",
    405: "method_not_allowed",
    409: "conflict",
    413: "payload_too_large",
    415: "unsupported_media_type",
    422: "validation_error",
    429: "rate_limited",
    500: "internal_error",
    502: "upstream_error",
    503: "unavailable",
}


def render_api_error(exc: ApiError) -> JSONResponse:
    return JSONResponse(
        status_code=exc.status_code,
        content=jsonable_encoder(error_body(exc.code, exc.message, exc.details)),
    )


def render_validation_error(exc: RequestValidationError) -> JSONResponse:
    details = [
        {
            "loc": list(err.get("loc", ())),
            "msg": err.get("msg"),
            "type": err.get("type"),
        }
        for err in exc.errors()
    ]
    return JSONResponse(
        status_code=422,
        content=jsonable_encoder(
            error_body("validation_error", "The request is not valid.", details)
        ),
    )


def install_error_handlers(app: FastAPI) -> None:
    @app.exception_handler(ApiError)
    async def _api_error(_: Request, exc: ApiError) -> JSONResponse:
        return render_api_error(exc)

    @app.exception_handler(RequestValidationError)
    async def _validation(_: Request, exc: RequestValidationError) -> JSONResponse:
        return render_validation_error(exc)

    @app.exception_handler(StarletteHTTPException)
    async def _http(_: Request, exc: StarletteHTTPException) -> JSONResponse:
        code = _HTTP_CODES.get(exc.status_code, "error")
        message = exc.detail if isinstance(exc.detail, str) else code.replace("_", " ")
        return JSONResponse(
            status_code=exc.status_code,
            content=error_body(code, message),
            headers=getattr(exc, "headers", None),
        )

    @app.exception_handler(Exception)
    async def _unhandled(_: Request, exc: Exception) -> JSONResponse:
        log.exception("unhandled error: %r", exc)
        return JSONResponse(
            status_code=500,
            content=error_body("internal_error", "Something went wrong on the server."),
        )


__all__ = [
    "ApiError",
    "error_body",
    "install_error_handlers",
    "render_api_error",
    "render_validation_error",
]
