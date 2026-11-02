import os
from pathlib import Path

import pytest

os.environ.setdefault("PYTHONUTF8", "1")

FIXTURES = Path(__file__).parent / "fixtures"


def pytest_collection_modifyitems(config, items):
    if os.environ.get("FFML_OFFLINE") == "1":
        skip = pytest.mark.skip(reason="FFML_OFFLINE=1")
        for item in items:
            if "network" in item.keywords:
                item.add_marker(skip)


@pytest.fixture
def fixtures_dir() -> Path:
    return FIXTURES


@pytest.fixture
def tmp_settings(tmp_path, monkeypatch):
    """Settings pointed at a temporary data directory."""
    from ffml.config import get_settings, reset_settings_cache

    monkeypatch.setenv("FFML_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FFML_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()
