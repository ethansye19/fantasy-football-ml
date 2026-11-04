"""Season mode for the offseason behaviour (ffml.season; ARCHITECTURE.md section 8)."""

from __future__ import annotations

import threading
import time
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api.services.meta_content import SEASON_BANNERS

TTL_SECONDS = 300.0


class SeasonService:
    """Resolves and caches :class:`ffml.season.SeasonState`; ``resolver`` is injectable."""

    def __init__(self, raw_dir: Path, resolver: Callable[[datetime], Any] | None = None):
        self.raw_dir = raw_dir
        self.resolver = resolver
        self._lock = threading.Lock()
        self._cached: tuple[float, dict[str, Any]] | None = None

    def _resolve(self, now: datetime) -> Any:
        if self.resolver is not None:
            return self.resolver(now)
        from ffml.data.store import SnapshotStore
        from ffml.season import current_state

        return current_state(now, store=SnapshotStore(self.raw_dir))

    def describe(self, now: datetime | None = None) -> dict[str, Any]:
        with self._lock:
            if now is None and self._cached and time.monotonic() - self._cached[0] < TTL_SECONDS:
                return self._cached[1]
        at = now or datetime.now(UTC)
        try:
            state = self._resolve(at)
            out: dict[str, Any] = {
                "season": state.season,
                "mode": state.mode,
                "projection_week": state.projection_week,
                "week_in_progress": state.week_in_progress,
                "last_completed_week": state.last_completed_week,
                "next_kickoff_utc": state.next_kickoff_utc,
                "offseason": state.mode != "regular",
                "banner": SEASON_BANNERS.get(state.mode),
                "as_of": at.isoformat(),
            }
        except Exception:  # noqa: BLE001 - no schedule snapshot yet
            out = {
                "season": None,
                "mode": "unknown",
                "projection_week": None,
                "week_in_progress": False,
                "last_completed_week": None,
                "next_kickoff_utc": None,
                "offseason": False,
                "banner": SEASON_BANNERS["unknown"],
                "as_of": at.isoformat(),
            }
        if now is None:
            with self._lock:
                self._cached = (time.monotonic(), out)
        return out

    def clear(self) -> None:
        with self._lock:
            self._cached = None


__all__ = ["SeasonService"]
