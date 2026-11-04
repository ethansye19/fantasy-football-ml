"""Model registry state transitions (temporary artifacts and reports directories)."""

from __future__ import annotations

import json

import pytest

from ffml.config import get_settings, reset_settings_cache
from ffml.models import registry as reg
from ffml.models.registry import RegistryError


@pytest.fixture
def env(tmp_settings, tmp_path, monkeypatch):
    monkeypatch.setenv("FFML_REPORTS_DIR", str(tmp_path / "reports"))
    reset_settings_cache()
    settings = get_settings()
    settings.reports_dir.mkdir(parents=True)
    yield settings
    reset_settings_cache()


def report(settings, name: str, verdict: str = "Verdict: APPROVED"):
    path = settings.reports_dir / name
    path.write_text(f"# Evaluator report\n\nMetrics ...\n\n  {verdict}  \n", encoding="utf-8")
    return path


def test_full_lifecycle(env):
    a = reg.register_candidate(
        "chain", "chain_v1", env.models_dir / "chain_v1", backtest_run_id="default_x"
    )
    assert a["status"] == "candidate" and a["evaluator_report"] is None
    assert a["backtest_run_id"] == "default_x" and a["git"]
    assert reg.registry_path().exists()
    assert reg.get_live() is None

    path = report(env, "evaluator_chain_v1.md")
    approved = reg.approve("chain_v1", path)
    assert approved["status"] == "approved"
    assert approved["evaluator_report"].endswith("evaluator_chain_v1.md")
    live = reg.set_live("chain_v1")
    assert live["status"] == "live" and reg.get_live()["version"] == "chain_v1"

    reg.register_candidate("chain", "chain_v2", env.models_dir / "chain_v2")
    reg.approve("chain_v2", report(env, "evaluator_chain_v2.md"))
    reg.set_live("chain_v2")
    assert reg.get_live()["version"] == "chain_v2"
    assert reg.get("chain_v1")["status"] == "retired"
    history = [h["status"] for h in reg.get("chain_v1")["history"]]
    assert history == ["candidate", "approved", "live", "retired"]
    assert [m["version"] for m in reg.list_models("retired")] == ["chain_v1"]

    stored = json.loads(reg.registry_path().read_text(encoding="utf-8"))
    assert stored["format"] == "ffml.registry/1"
    entry = stored["models"][1]
    for field in (
        "name",
        "version",
        "path",
        "status",
        "created_at",
        "git",
        "backtest_run_id",
        "evaluator_report",
    ):
        assert field in entry


def test_approval_requires_a_report_with_the_verdict_line(env, tmp_path):
    reg.register_candidate("chain", "v1", tmp_path / "v1")
    with pytest.raises(RegistryError, match="does not exist"):
        reg.approve("v1", env.reports_dir / "missing.md")
    with pytest.raises(RegistryError, match="Verdict"):
        reg.approve("v1", report(env, "rejected.md", verdict="Verdict: REJECTED"))
    with pytest.raises(RegistryError, match="Verdict"):
        reg.approve("v1", report(env, "inline.md", verdict="The Verdict: APPROVED is pending"))
    outside = tmp_path / "evaluator_v1.md"
    outside.write_text("Verdict: APPROVED\n", encoding="utf-8")
    with pytest.raises(RegistryError, match="not under"):
        reg.approve("v1", outside)
    assert reg.get("v1")["status"] == "candidate"
    # a report name relative to the reports directory works
    report(env, "evaluator_v1.md")
    assert reg.approve("v1", "evaluator_v1.md")["status"] == "approved"


def test_invalid_transitions(env, tmp_path):
    reg.register_candidate("challenger", "c1", tmp_path / "c1")
    with pytest.raises(RegistryError, match="already registered"):
        reg.register_candidate("challenger", "c1", tmp_path / "c1")
    with pytest.raises(RegistryError, match="only approved"):
        reg.set_live("c1")
    with pytest.raises(RegistryError, match="not registered"):
        reg.approve("nope", report(env, "x.md"))
    reg.approve("c1", report(env, "c1.md"))
    with pytest.raises(RegistryError, match="only candidates"):
        reg.approve("c1", report(env, "c1.md"))
    reg.set_live("c1")
    with pytest.raises(RegistryError, match="only approved"):
        reg.set_live("c1")  # already live
    reg.retire("c1")
    assert reg.get_live() is None
    with pytest.raises(RegistryError, match="already retired"):
        reg.retire("c1")
    with pytest.raises(RegistryError, match="unknown status"):
        reg.list_models("deleted")


def test_candidate_records_its_frozen_calibration(env, tmp_path):
    import hashlib

    artifact = tmp_path / "chain_v1"
    artifact.mkdir()
    cal = artifact / "calibration.json"
    cal.write_text(
        json.dumps({"format": "ffml.calibration/2", "meta": {"calibration_version": "v2-x"}}),
        encoding="utf-8",
    )
    entry = reg.register_candidate("chain", "c-cal", artifact, "run_1", calibration=cal)
    assert entry["calibration"]["sha256"] == hashlib.sha256(cal.read_bytes()).hexdigest()
    assert entry["calibration"]["calibration_version"] == "v2-x"
    assert entry["status"] == "candidate" and entry["evaluator_report"] is None
    with pytest.raises(RegistryError, match="does not exist"):
        reg.register_candidate("chain", "c-missing", artifact, calibration=artifact / "nope.json")


def test_candidate_calibration_can_be_replaced_until_approved(env, tmp_path):
    import hashlib

    artifact = tmp_path / "chain_v1"
    artifact.mkdir()
    old = artifact / "calibration_v2.json"
    new = artifact / "calibration.json"
    old.write_text(json.dumps({"meta": {"calibration_version": "v2"}}), encoding="utf-8")
    new.write_text(json.dumps({"meta": {"calibration_version": "v3"}}), encoding="utf-8")
    reg.register_candidate("chain", "c1", artifact, "run_1", calibration=old)
    entry = reg.update_calibration("c1", new)
    assert entry["calibration"]["sha256"] == hashlib.sha256(new.read_bytes()).hexdigest()
    assert entry["calibration"]["calibration_version"] == "v3"
    assert [h["calibration_version"] for h in entry["calibration_history"]] == ["v2"]
    assert reg.get("c1")["calibration"]["path"].endswith("calibration.json")
    reg.approve("c1", report(env, "c1.md"))
    with pytest.raises(RegistryError, match="only a candidate"):
        reg.update_calibration("c1", old)
