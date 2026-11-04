"""FastAPI application factory (docs/API.md).

Run locally::

    .venv/Scripts/uvicorn api.main:app --reload            # production rules (default)
    FFML_APP_ENV=private_test .venv/Scripts/uvicorn api.main:app

The process starts in either environment. In production the projection
routes answer 503 ``no_approved_model`` until an approved live model is
published (api.approval); league, meta and health routes work normally.
"""

from __future__ import annotations

import logging
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, Request, Response
from fastapi.middleware.cors import CORSMiddleware

from api import __version__
from api.approval import HEADER, HEADER_NOT_APPROVED, ApprovalGate
from api.db.session import Database
from api.errors import install_error_handlers
from api.services.players import DirectoryHolder
from api.services.season import SeasonService
from api.settings import ApiSettings, get_api_settings
from ffml.config import ensure_utf8
from ffml.data.sleeper import SleeperClient

log = logging.getLogger(__name__)


def create_app(settings: ApiSettings | None = None) -> FastAPI:
    """Build the app. Tests pass their own settings; production reads the environment."""
    ensure_utf8()
    settings = settings or get_api_settings()

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        log.info("ffml api %s starting in %s mode", __version__, settings.app_env)
        if settings.db_auto_migrate:
            app.state.db.migrate()
        try:
            yield
        finally:
            app.state.db.dispose()

    app = FastAPI(
        title="ffml API",
        version=__version__,
        description="League tools and projections. See docs/API.md.",
        lifespan=lifespan,
    )
    app.state.settings = settings
    app.state.gate = ApprovalGate(settings)
    app.state.db = Database(settings.resolved_database_url)
    app.state.players = DirectoryHolder(settings.processed_dir)
    app.state.sleeper_factory = SleeperClient
    app.state.season = SeasonService(settings.raw_dir)

    install_error_handlers(app)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=list(settings.cors_origins),
        allow_methods=["GET", "POST", "PUT", "DELETE"],
        allow_headers=["Content-Type"],
        expose_headers=[HEADER],
        allow_credentials=False,
    )

    @app.middleware("http")
    async def _headers(request: Request, call_next) -> Response:  # type: ignore[no-untyped-def]
        response = await call_next(request)
        response.headers.setdefault("X-Content-Type-Options", "nosniff")
        response.headers.setdefault("Cache-Control", "no-store")
        if settings.is_private_test:
            # every response in a private test environment is labeled (D-099)
            response.headers[HEADER] = HEADER_NOT_APPROVED
        return response

    from api.routers import leagues, meta, projections

    app.include_router(meta.router)
    app.include_router(leagues.router)
    app.include_router(projections.router)
    return app


def __getattr__(name: str):  # lazy module-level ``app`` for ``uvicorn api.main:app``
    if name == "app":
        return create_app()
    raise AttributeError(name)
