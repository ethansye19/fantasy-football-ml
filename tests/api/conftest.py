"""Fixtures for API tests: isolated data, artifacts and database per test (SQLite)."""

from __future__ import annotations

from collections.abc import Callable, Iterator
from pathlib import Path
from typing import Any

import pytest
from fastapi.testclient import TestClient

from api.settings import ApiSettings, reset_api_settings_cache


@pytest.fixture
def api_dirs(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[dict[str, Path]]:
    """Point ffml settings at empty temporary data, artifacts and reports directories."""
    from ffml.config import reset_settings_cache

    dirs = {
        "data": tmp_path / "data",
        "artifacts": tmp_path / "artifacts",
        "reports": tmp_path / "reports",
    }
    for d in dirs.values():
        d.mkdir(parents=True)
    monkeypatch.setenv("FFML_DATA_DIR", str(dirs["data"]))
    monkeypatch.setenv("FFML_ARTIFACTS_DIR", str(dirs["artifacts"]))
    monkeypatch.setenv("FFML_REPORTS_DIR", str(dirs["reports"]))
    for name in ("FFML_APP_ENV", "FFML_API_FIXTURE_PREDICTIONS_DIR", "FFML_API_REPLAY_RUN"):
        monkeypatch.delenv(name, raising=False)
    reset_settings_cache()
    reset_api_settings_cache()
    dirs["predictions"] = dirs["data"] / "predictions"
    dirs["private"] = dirs["data"] / "predictions_private"
    dirs["fixtures"] = tmp_path / "fixture_predictions"
    yield dirs
    reset_settings_cache()
    reset_api_settings_cache()


@pytest.fixture
def make_client(api_dirs: dict[str, Path]) -> Iterator[Callable[..., TestClient]]:
    """``make_client(app_env="production", **settings)`` -> a TestClient on a fresh app."""
    from api.main import create_app

    clients: list[TestClient] = []

    def factory(app_env: str = "production", **overrides: Any) -> TestClient:
        values: dict[str, Any] = {
            "app_env": app_env,
            "approval_ttl_seconds": 0,
            "database_url": f"sqlite:///{(api_dirs['data'] / 'app.db').as_posix()}",
        }
        values.update(overrides)
        client = TestClient(create_app(ApiSettings(**values)))
        client.__enter__()
        clients.append(client)
        return client

    yield factory
    for client in clients:
        client.__exit__(None, None, None)
