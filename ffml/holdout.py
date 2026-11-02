"""Final-holdout guard (docs/MODELING.md section 2, DECISIONS D-005).

The holdout season (2025 by default) must stay untouched until the model is
selected. Every code path that loads feature rows or outcomes for training,
validation, or evaluation calls :func:`assert_seasons_allowed`. Building raw
and processed tables for the holdout season is allowed (it is mechanical and
outcome-blind), so the data layer does not call this guard.

When ``FFML_HOLDOUT_UNLOCK=true`` is set, access is permitted and every access
is appended to ``<artifacts_dir>/holdout_access.log`` so the Independent
Evaluator can verify that the holdout was evaluated exactly once (one
``holdout_eval`` session, D-049).

After that session the evaluator calls :func:`release_holdout` with the
approved model version. The release marker ``<artifacts_dir>/holdout_release.json``
permanently unlocks the season for production refits and for 2026 features
that use 2025 as history (D-058). Accesses after release are still logged,
with ``released=true``.
"""

from __future__ import annotations

import json
import logging
import os
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ffml.config import PROJECT_ROOT, get_settings

log = logging.getLogger(__name__)

RELEASE_FILE = "holdout_release.json"


class HoldoutLockedError(RuntimeError):
    """Raised when code asks for the holdout season without the unlock flag."""


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


def holdout_season() -> int:
    return get_settings().holdout_season


def _release_path() -> Path:
    return get_settings().artifacts_dir / RELEASE_FILE


def release_info() -> dict[str, Any] | None:
    """The release marker written after the evaluator's holdout session, if any."""
    path = _release_path()
    if not path.exists():
        return None
    return json.loads(path.read_text(encoding="utf-8"))


def is_released() -> bool:
    info = release_info()
    return bool(info) and int(info.get("season", -1)) == get_settings().holdout_season


def is_unlocked() -> bool:
    return bool(get_settings().holdout_unlock) or is_released()


def release_holdout(model_version: str, evaluator_report: str) -> Path:
    """Permanently release the holdout after the single evaluation session (D-058).

    Only the Independent Evaluator calls this, at the end of its release run,
    and only while the unlock flag is set (so a release cannot happen silently).
    """
    settings = get_settings()
    if not settings.holdout_unlock:
        raise HoldoutLockedError("release_holdout requires FFML_HOLDOUT_UNLOCK=true (evaluator run)")
    if is_released():
        raise HoldoutLockedError(f"holdout already released: {release_info()}")
    payload = {
        "season": settings.holdout_season,
        "released_at": datetime.now(UTC).isoformat(),
        "model_version": model_version,
        "evaluator_report": evaluator_report,
        "git": _git_sha(),
    }
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    path = _release_path()
    path.write_text(json.dumps(payload, indent=2) + "\n", encoding="utf-8")
    log.warning("HOLDOUT RELEASED %s", payload)
    return path


def assert_seasons_allowed(seasons: Iterable[int], purpose: str) -> None:
    """Raise :class:`HoldoutLockedError` if ``seasons`` touch the locked holdout.

    ``purpose`` is a short label such as ``"train"``, ``"validate"``,
    ``"backtest"``, ``"holdout_eval"`` or ``"predict"``; it is written to the
    access log.
    """
    settings = get_settings()
    wanted = {int(s) for s in seasons}
    if settings.holdout_season not in wanted:
        return
    released = is_released()
    if not settings.holdout_unlock and not released:
        raise HoldoutLockedError(
            f"season {settings.holdout_season} is the final holdout and is locked for "
            f"purpose={purpose!r}; set FFML_HOLDOUT_UNLOCK=true only for the evaluator's release run"
        )
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    line = (
        f"{datetime.now(UTC).isoformat()}\tpurpose={purpose}\tseasons={sorted(wanted)}"
        f"\tgit={_git_sha()}\tuser={os.environ.get('USERNAME') or os.environ.get('USER') or '?'}"
        f"\treleased={str(released).lower()}\n"
    )
    with (settings.artifacts_dir / "holdout_access.log").open("a", encoding="utf-8") as fh:
        fh.write(line)
    log.warning("HOLDOUT ACCESS %s", line.strip())


def allowed_seasons(seasons: Iterable[int], purpose: str) -> list[int]:
    """Return ``seasons`` without the holdout when locked (logs a warning), else all."""
    settings = get_settings()
    out = sorted({int(s) for s in seasons})
    if settings.holdout_season in out and not is_unlocked():
        log.warning(
            "dropping locked holdout season %s from %s request", settings.holdout_season, purpose
        )
        return [s for s in out if s != settings.holdout_season]
    if settings.holdout_season in out:
        assert_seasons_allowed(out, purpose)
    return out
