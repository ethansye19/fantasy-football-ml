import json

import pytest

from ffml.config import get_settings, reset_settings_cache
from ffml.holdout import (
    HoldoutLockedError,
    allowed_seasons,
    assert_seasons_allowed,
    is_released,
    release_holdout,
    release_info,
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
        release_holdout("v1", "reports/evaluator_release_v1.md", approved=False)
    assert release_info() is None


def test_release_unlocks_permanently(unlocked, monkeypatch):
    release_holdout("v1", "reports/evaluator_release_v1.md", approved=False)
    with pytest.raises(HoldoutLockedError):
        release_holdout("v2", "again", approved=False)  # only once
    monkeypatch.delenv("FFML_HOLDOUT_UNLOCK")
    reset_settings_cache()
    assert is_released()
    assert_seasons_allowed([2025, 2026], purpose="predict")
    assert allowed_seasons([2024, 2025], purpose="train") == [2024, 2025]
    text = (get_settings().artifacts_dir / "holdout_access.log").read_text(encoding="utf-8")
    assert "purpose=predict" in text and "released=true" in text


def test_release_payload_records_the_verdict_and_note(unlocked):
    note = "released as training data for chain_v2 (D-098); not an approval"
    path = release_holdout("v1", "reports/evaluator_release_v1.md", approved=False, note=note)
    payload = json.loads(path.read_text(encoding="utf-8"))
    assert payload["approved"] is False and payload["note"] == note
    assert payload["model_version"] == "v1" and payload["season"] == 2025
    assert payload["evaluator_report"] == "reports/evaluator_release_v1.md"
    assert release_info() == payload
    # a release does not touch the registry: approval lives only there
    assert not (unlocked.models_dir / "REGISTRY.json").exists()


def test_release_note_defaults_to_empty(unlocked):
    payload = json.loads(release_holdout("v1", "r.md", approved=True).read_text(encoding="utf-8"))
    assert payload["approved"] is True and payload["note"] == ""


def test_release_needs_an_explicit_keyword_verdict(unlocked):
    with pytest.raises(TypeError):
        release_holdout("v1", "reports/evaluator_release_v1.md")  # approved is required
    with pytest.raises(TypeError):
        release_holdout("v1", "reports/evaluator_release_v1.md", False)  # keyword-only
    with pytest.raises(TypeError, match="approved must be"):
        release_holdout("v1", "reports/evaluator_release_v1.md", approved="no")
    assert release_info() is None and not is_released()
