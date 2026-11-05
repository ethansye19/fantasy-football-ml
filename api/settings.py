"""API settings (docs/API.md section 2).

All values come from ``FFML_*`` environment variables (or the repository
``.env``), the same prefix as :class:`ffml.config.Settings`. Paths of the
Parquet lake and the model registry are taken from :mod:`ffml.config`, so the
API and the pipeline always agree on where predictions live.

``FFML_APP_ENV`` selects the serving rules for projection-based features:

``production`` (default)
    Projection routes serve data only for an approved, live model whose
    published snapshot says so (:mod:`api.approval`). Otherwise they return
    HTTP 503 ``no_approved_model``.
``private_test``
    Projection routes may serve private-test snapshots, historical replays or
    fixtures, always labeled "not approved" (D-099).

Any other value (including typos) is treated as ``production``, the safe side.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path
from typing import Literal

from pydantic import field_validator
from pydantic_settings import BaseSettings, SettingsConfigDict

from ffml.config import PROJECT_ROOT, get_settings

log = logging.getLogger(__name__)

PRODUCTION = "production"
PRIVATE_TEST = "private_test"
AppEnv = Literal["production", "private_test"]


class ApiSettings(BaseSettings):
    """Runtime configuration of the API. Override with ``FFML_*`` environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="FFML_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    app_env: AppEnv = PRODUCTION
    database_url: str | None = None
    db_auto_migrate: bool = True
    cors_origins: list[str] = ["http://localhost:3000", "http://127.0.0.1:3000"]

    # private_test only: optional extra sources (never read in production)
    api_fixture_predictions_dir: Path | None = None
    api_replay_run: str | None = None  # "<season>/<week>/<run_id>" pins one replay run

    # largest accepted request body in bytes (413 above it; QA-F02); 0 disables the check
    body_max_bytes: int = 256 * 1024

    # inbound rate limits per client and route family (QA-F03, docs/SECURITY.md section 5):
    # "<per_minute>/<burst>"; "0" disables one family, rate_limit_enabled=False disables all
    rate_limit_enabled: bool = True
    rate_limit_sleeper: str = "10/5"
    rate_limit_write: str = "30/10"
    rate_limit_heavy: str = "30/10"
    rate_limit_read: str = "120/30"
    rate_limit_meta: str = "300/60"
    # peers whose X-Forwarded-For first entry identifies the client (the web proxy)
    trusted_proxies: list[str] = []

    # seconds an approval/source resolution is reused (0 disables the cache)
    approval_ttl_seconds: float = 5.0
    # in-memory scored-run cache entries (run x scoring hash)
    scoring_cache_size: int = 8
    # FTN charting features (D-009); attribution lists FTN either way
    ftn_features_enabled: bool = False

    @field_validator("app_env", mode="before")
    @classmethod
    def _safe_env(cls, value: object) -> str:
        text = str(value or "").strip().lower().replace("-", "_")
        if text not in (PRODUCTION, PRIVATE_TEST):
            if text:
                log.warning("FFML_APP_ENV=%r is not recognised; using production", value)
            return PRODUCTION
        return text

    @property
    def is_private_test(self) -> bool:
        return self.app_env == PRIVATE_TEST

    # -- paths shared with the pipeline -------------------------------------- #

    @property
    def data_dir(self) -> Path:
        return get_settings().data_dir

    @property
    def predictions_dir(self) -> Path:
        return get_settings().predictions_dir

    @property
    def private_predictions_dir(self) -> Path:
        """``ffml.config.Settings.private_predictions_dir`` when present, else the default."""
        settings = get_settings()
        value = getattr(settings, "private_predictions_dir", None)
        return Path(value) if value is not None else settings.data_dir / "predictions_private"

    @property
    def processed_dir(self) -> Path:
        return get_settings().processed_dir

    @property
    def raw_dir(self) -> Path:
        return get_settings().raw_dir

    @property
    def resolved_database_url(self) -> str:
        if self.database_url:
            return self.database_url
        return f"sqlite:///{(self.data_dir / 'app.db').as_posix()}"


@lru_cache(maxsize=1)
def get_api_settings() -> ApiSettings:
    return ApiSettings()


def reset_api_settings_cache() -> None:
    get_api_settings.cache_clear()


__all__ = [
    "PRIVATE_TEST",
    "PRODUCTION",
    "ApiSettings",
    "get_api_settings",
    "reset_api_settings_cache",
]
