"""Project settings, paths, and process-wide environment fixes.

All paths are resolved relative to the repository root unless overridden with
environment variables prefixed ``FFML_`` (or a ``.env`` file at the root).
"""

from __future__ import annotations

import os
import sys
from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict

PROJECT_ROOT = Path(__file__).resolve().parents[1]


def ensure_utf8() -> None:
    """Force UTF-8 on Windows consoles (cp1252 breaks Polars table output)."""
    os.environ.setdefault("PYTHONUTF8", "1")
    os.environ.setdefault("PYTHONIOENCODING", "utf-8")
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, "reconfigure", None)
        if reconfigure is not None:
            try:
                reconfigure(encoding="utf-8")
            except (ValueError, OSError):
                pass


class Settings(BaseSettings):
    """Runtime configuration. Override with FFML_* environment variables."""

    model_config = SettingsConfigDict(
        env_prefix="FFML_", env_file=PROJECT_ROOT / ".env", extra="ignore"
    )

    data_dir: Path = PROJECT_ROOT / "data"
    artifacts_dir: Path = PROJECT_ROOT / "artifacts"
    reports_dir: Path = PROJECT_ROOT / "reports"

    # Seasons
    first_season: int = 2016
    holdout_season: int = 2025
    holdout_unlock: bool = False  # must be True (and is logged) to read the holdout season

    # External services
    sleeper_base_url: str = "https://api.sleeper.app/v1"
    http_timeout_seconds: float = 60.0
    http_user_agent: str = "ffml/0.1 (+https://github.com/ethansye19/fantasy-football-ml)"

    # Simulation defaults
    n_sims: int = 2000
    random_seed: int = 20261101

    log_level: str = "INFO"

    @property
    def raw_dir(self) -> Path:
        return self.data_dir / "raw"

    @property
    def processed_dir(self) -> Path:
        return self.data_dir / "processed"

    @property
    def features_dir(self) -> Path:
        return self.data_dir / "features"

    @property
    def predictions_dir(self) -> Path:
        return self.data_dir / "predictions"

    @property
    def warehouse_path(self) -> Path:
        return self.data_dir / "warehouse.duckdb"

    @property
    def models_dir(self) -> Path:
        return self.artifacts_dir / "models"


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    return Settings()


def reset_settings_cache() -> None:
    """Clear the cached settings (used by tests that change environment variables)."""
    get_settings.cache_clear()
