import pytest

from ffml.config import get_settings, reset_settings_cache
from ffml.holdout import HoldoutLockedError, allowed_seasons, assert_seasons_allowed


@pytest.fixture
def locked(tmp_settings, monkeypatch):
    monkeypatch.delenv("FFML_HOLDOUT_UNLOCK", raising=False)
    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


@pytest.fixture
def unlocked(tmp_settings, monkeypatch):
    monkeypatch.setenv("FFML_HOLDOUT_UNLOCK", "true")
    reset_settings_cache()
    yield get_settings()
    reset_settings_cache()


def test_locked_raises_for_holdout(locked):
    assert_seasons_allowed([2022, 2023], purpose="train")  # fine
    with pytest.raises(HoldoutLockedError):
        assert_seasons_allowed([2024, 2025], purpose="train")


def test_allowed_seasons_drops_holdout_when_locked(locked):
    assert allowed_seasons([2023, 2024, 2025], purpose="backtest") == [2023, 2024]


def test_unlocked_logs_access(unlocked):
    assert_seasons_allowed([2025], purpose="holdout_eval")
    log_path = unlocked.artifacts_dir / "holdout_access.log"
    text = log_path.read_text(encoding="utf-8")
    assert "purpose=holdout_eval" in text and "2025" in text
