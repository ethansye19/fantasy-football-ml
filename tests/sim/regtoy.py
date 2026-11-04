"""Temporary-registry helpers for the prediction mode tests (never the real registry)."""

from __future__ import annotations

import json
from pathlib import Path

from ffml.models import registry as reg

SYNTHETIC_VERSION = "chain_v1-synthetic-test"


def write_report(settings, name: str, verdict: str = "Verdict: APPROVED") -> Path:
    path = settings.reports_dir / name
    path.write_text(f"# Evaluator report\n\n{verdict}\n", encoding="utf-8")
    return path


def add_candidate(version: str, path: Path | str) -> dict:
    return reg.register_candidate("chain_v1", version, path, backtest_run_id="test")


def add_live(settings, version: str, path: Path | str) -> dict:
    add_candidate(version, path)
    reg.approve(version, write_report(settings, f"evaluator_{version}.md"))
    return reg.set_live(version)


def add_with_status(settings, version: str, path: Path | str, status: str) -> dict:
    """A registry entry in ``status`` (candidate, approved, live, retired or rejected)."""
    if status == "live":
        return add_live(settings, version, path)
    entry = add_candidate(version, path)
    if status == "approved":
        entry = reg.approve(version, write_report(settings, f"evaluator_{version}.md"))
    elif status == "rejected":
        report = write_report(settings, f"release_{version}.md", "Verdict: NOT APPROVED")
        entry = reg.reject(version, report)
    elif status == "retired":
        entry = reg.retire(version)
    assert entry["status"] == status
    return entry


def write_release_marker(settings) -> Path:
    """A fake holdout release in the temporary artifacts dir (the guard then logs every access)."""
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = settings.artifacts_dir / "holdout_release.json"
    payload = {"season": settings.holdout_season, "approved": False, "note": "test"}
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


def tree(root: Path) -> dict[str, bytes]:
    """Every file under ``root`` with its bytes (empty when ``root`` does not exist)."""
    if not root.exists():
        return {}
    return {p.relative_to(root).as_posix(): p.read_bytes() for p in root.rglob("*") if p.is_file()}
