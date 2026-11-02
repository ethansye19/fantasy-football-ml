"""Final-holdout guard (docs/MODELING.md section 2, DECISIONS D-005).

The holdout season (2025 by default) must stay untouched until the model is
selected. Every code path that loads feature rows or outcomes for training,
validation, or evaluation calls :func:`assert_seasons_allowed`. Building raw
and processed tables for the holdout season is allowed (it is mechanical and
outcome-blind), so the data layer does not call this guard.

When ``FFML_HOLDOUT_UNLOCK=true`` is set, access is permitted and every access
is appended to ``<artifacts_dir>/holdout_access.log`` so the Independent
Evaluator can verify that the holdout was evaluated exactly once.
"""

from __future__ import annotations

import logging
import os
import subprocess
from collections.abc import Iterable
from datetime import UTC, datetime

from ffml.config import PROJECT_ROOT, get_settings

log = logging.getLogger(__name__)


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


def is_unlocked() -> bool:
    return bool(get_settings().holdout_unlock)


def assert_seasons_allowed(seasons: Iterable[int], purpose: str) -> None:
    """Raise :class:`HoldoutLockedError` if ``seasons`` touch the locked holdout.

    ``purpose`` is a short label such as ``"train"``, ``"validate"``,
    ``"backtest"`` or ``"holdout_eval"``; it is written to the access log.
    """
    settings = get_settings()
    wanted = {int(s) for s in seasons}
    if settings.holdout_season not in wanted:
        return
    if not settings.holdout_unlock:
        raise HoldoutLockedError(
            f"season {settings.holdout_season} is the final holdout and is locked for "
            f"purpose={purpose!r}; set FFML_HOLDOUT_UNLOCK=true only for the evaluator's release run"
        )
    settings.artifacts_dir.mkdir(parents=True, exist_ok=True)
    line = (
        f"{datetime.now(UTC).isoformat()}\tpurpose={purpose}\tseasons={sorted(wanted)}"
        f"\tgit={_git_sha()}\tuser={os.environ.get('USERNAME') or os.environ.get('USER') or '?'}\n"
    )
    with (settings.artifacts_dir / "holdout_access.log").open("a", encoding="utf-8") as fh:
        fh.write(line)
    log.warning("HOLDOUT ACCESS %s", line.strip())


def allowed_seasons(seasons: Iterable[int], purpose: str) -> list[int]:
    """Return ``seasons`` without the holdout when locked (logs a warning), else all."""
    settings = get_settings()
    out = sorted({int(s) for s in seasons})
    if settings.holdout_season in out and not settings.holdout_unlock:
        log.warning(
            "dropping locked holdout season %s from %s request", settings.holdout_season, purpose
        )
        return [s for s in out if s != settings.holdout_season]
    if settings.holdout_season in out:
        assert_seasons_allowed(out, purpose)
    return out
