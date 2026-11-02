import pytest

from ffml.config import get_settings, reset_settings_cache
from ffml.holdout import (
    HoldoutLockedError,
    allowed_seasons,
    assert_seasons_allowed,
    is_released,
    release_holdout,
)


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
    assert "purpose=holdout_eval" in text and "2025" in text and "released=false" in text


def test_release_requires_unlock(locked):
    with pytest.raises(HoldoutLockedError):
        release_holdout("v1", "reports/evaluator_release_v1.md")


def test_release_unlocks_permanently(unlocked, monkeypatch):
    release_holdout("v1", "reports/evaluator_release_v1.md")
    with pytest.raises(HoldoutLockedError):
        release_holdout("v2", "again")  # only once
    monkeypatch.delenv("FFML_HOLDOUT_UNLOCK")
    reset_settings_cache()
    assert is_released()
    assert_seasons_allowed([2025, 2026], purpose="predict")
    assert allowed_seasons([2024, 2025], purpose="train") == [2024, 2025]
    text = (get_settings().artifacts_dir / "holdout_access.log").read_text(encoding="utf-8")
    assert "purpose=predict" in text and "released=true" in text
