"""Model registry ``artifacts/models/REGISTRY.json`` (MODELING.md section 10).

Every model version moves through ``candidate -> approved -> live -> retired``:

- :func:`register_candidate` adds a version as ``candidate`` with its
  artifact path, git sha and the backtest run that produced its numbers.
- :func:`approve` records the Independent Evaluator's report. It is allowed
  only for a candidate, only when the report file exists under the reports
  directory (``reports/`` by default) and only when the report contains the
  line ``Verdict: APPROVED`` (exact text on its own line, surrounding spaces
  ignored).
- :func:`set_live` promotes an approved version; the previously live version
  becomes ``retired``. Exactly one version is live at a time.
- :func:`get_live` returns the live entry (or ``None``).

Entry fields: ``name, version, path, status, created_at, git, backtest_run_id,
evaluator_report`` (null until approved) and ``history`` (one
``{status, at}`` record per transition). Versions are unique across names.
Writes go through a temporary file and an atomic rename.
"""

from __future__ import annotations

import json
import os
import subprocess
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ffml.config import PROJECT_ROOT, get_settings

REGISTRY_FILE = "REGISTRY.json"
REGISTRY_FORMAT = "ffml.registry/1"
STATUSES: tuple[str, ...] = ("candidate", "approved", "live", "retired")
VERDICT_LINE = "Verdict: APPROVED"


class RegistryError(ValueError):
    """An invalid registry transition or input."""


def registry_path() -> Path:
    return get_settings().models_dir / REGISTRY_FILE


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _git_sha() -> str:
    try:
        out = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            cwd=PROJECT_ROOT,
            capture_output=True,
            text=True,
            timeout=10,
            check=False,
        )
        return out.stdout.strip() or "unknown"
    except (OSError, subprocess.SubprocessError):
        return "unknown"


def _relative(path: Path | str) -> str:
    p = Path(path)
    try:
        return p.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def load_registry(path: Path | str | None = None) -> dict[str, Any]:
    """The registry document (an empty one when the file does not exist yet)."""
    path = Path(path) if path is not None else registry_path()
    if not path.exists():
        return {"format": REGISTRY_FORMAT, "models": []}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("format") != REGISTRY_FORMAT:
        raise RegistryError(f"{path} is not a model registry (format={payload.get('format')!r})")
    return payload


def _save(payload: dict[str, Any], path: Path | str | None = None) -> Path:
    path = Path(path) if path is not None else registry_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    payload["updated_at"] = _now()
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    os.replace(tmp, path)
    return path


def _find(payload: dict[str, Any], version: str) -> dict[str, Any]:
    for entry in payload["models"]:
        if entry["version"] == version:
            return entry
    raise RegistryError(f"version {version!r} is not registered")


def _transition(entry: dict[str, Any], status: str) -> None:
    entry["status"] = status
    entry.setdefault("history", []).append({"status": status, "at": _now()})


def list_models(
    status: str | None = None, *, path: Path | str | None = None
) -> list[dict[str, Any]]:
    if status is not None and status not in STATUSES:
        raise RegistryError(f"unknown status {status!r}; expected one of {STATUSES}")
    models = load_registry(path)["models"]
    return [m for m in models if status is None or m["status"] == status]


def get(version: str, *, path: Path | str | None = None) -> dict[str, Any]:
    return _find(load_registry(path), version)


def register_candidate(
    name: str,
    version: str,
    path: Path | str,
    backtest_run_id: str | None = None,
    *,
    git: str | None = None,
    registry: Path | str | None = None,
) -> dict[str, Any]:
    """Add ``version`` of model ``name`` as a candidate."""
    if not name or not version:
        raise RegistryError("name and version are required")
    payload = load_registry(registry)
    if any(m["version"] == version for m in payload["models"]):
        raise RegistryError(f"version {version!r} is already registered")
    entry: dict[str, Any] = {
        "name": name,
        "version": version,
        "path": _relative(path),
        "status": "candidate",
        "created_at": _now(),
        "git": git or _git_sha(),
        "backtest_run_id": backtest_run_id,
        "evaluator_report": None,
        "history": [],
    }
    _transition(entry, "candidate")
    payload["models"].append(entry)
    _save(payload, registry)
    return entry


def check_report(evaluator_report: Path | str) -> Path:
    """The report path if it exists under the reports directory and carries the approval line."""
    reports = get_settings().reports_dir.resolve()
    report = Path(evaluator_report)
    if not report.is_absolute():
        candidate = PROJECT_ROOT / report
        report = candidate if candidate.exists() else reports / report
    report = report.resolve()
    try:
        report.relative_to(reports)
    except ValueError as exc:
        raise RegistryError(f"evaluator report {report} is not under {reports}") from exc
    if not report.is_file():
        raise RegistryError(f"evaluator report {report} does not exist")
    lines = report.read_text(encoding="utf-8").splitlines()
    if not any(line.strip() == VERDICT_LINE for line in lines):
        raise RegistryError(f"evaluator report {report} has no line {VERDICT_LINE!r}")
    return report


def approve(
    version: str, evaluator_report: Path | str, *, registry: Path | str | None = None
) -> dict[str, Any]:
    """Mark a candidate approved by the evaluator's report (see module docstring)."""
    payload = load_registry(registry)
    entry = _find(payload, version)
    if entry["status"] != "candidate":
        raise RegistryError(f"only candidates can be approved; {version!r} is {entry['status']}")
    report = check_report(evaluator_report)
    entry["evaluator_report"] = _relative(report)
    entry["approved_at"] = _now()
    _transition(entry, "approved")
    _save(payload, registry)
    return entry


def set_live(version: str, *, registry: Path | str | None = None) -> dict[str, Any]:
    """Promote an approved version to live and retire the previously live one."""
    payload = load_registry(registry)
    entry = _find(payload, version)
    if entry["status"] != "approved":
        raise RegistryError(f"only approved versions can go live; {version!r} is {entry['status']}")
    if not entry.get("evaluator_report"):
        raise RegistryError(f"{version!r} has no evaluator report")
    for other in payload["models"]:
        if other["status"] == "live":
            other["retired_at"] = _now()
            _transition(other, "retired")
    entry["live_since"] = _now()
    _transition(entry, "live")
    _save(payload, registry)
    return entry


def retire(version: str, *, registry: Path | str | None = None) -> dict[str, Any]:
    """Retire any version (a live version leaves the app without a live model)."""
    payload = load_registry(registry)
    entry = _find(payload, version)
    if entry["status"] == "retired":
        raise RegistryError(f"{version!r} is already retired")
    entry["retired_at"] = _now()
    _transition(entry, "retired")
    _save(payload, registry)
    return entry


def get_live(*, registry: Path | str | None = None) -> dict[str, Any] | None:
    live = [m for m in load_registry(registry)["models"] if m["status"] == "live"]
    if len(live) > 1:
        raise RegistryError(f"registry has {len(live)} live versions")
    return live[0] if live else None


__all__ = [
    "STATUSES",
    "VERDICT_LINE",
    "RegistryError",
    "approve",
    "check_report",
    "get",
    "get_live",
    "list_models",
    "load_registry",
    "register_candidate",
    "registry_path",
    "retire",
    "set_live",
]
