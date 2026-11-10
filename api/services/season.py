"""Current season, week and season type for the app (D-124; ARCHITECTURE.md section 8).

Primary source: Sleeper's state endpoint (``GET /state/nfl``), read through the
app's shared :class:`ffml.data.sleeper.SleeperClient` and its rate limiter
(D-025). Cross-check: the nflverse schedule, :func:`ffml.season.current_state`
on the latest raw ``schedules`` snapshot (the earliest regular-season week with
a game that is not final). The calendar is never used.

Rules (``GET /meta/season``):

* both answer and agree: Sleeper's answer, ``source: "sleeper"``,
  ``cross_check.status: "agree"``;
* both answer and disagree: the **schedule's** answer, ``source: "schedule"``,
  ``cross_check.status: "disagree"`` with the differing fields; the
  disagreement is logged (once per distinct disagreement);
* Sleeper unreachable, failing or disabled: the schedule's answer,
  ``source: "schedule"``, ``cross_check.status: "sleeper_unavailable"``;
* no schedule snapshot: Sleeper's answer, not cross-checked,
  ``cross_check.status: "schedule_unavailable"``;
* neither: ``mode: "unknown"``, ``source: "none"``.

Sleeper's ``pre`` season type agrees with a schedule whose next week is week 1
and has no final game. Weeks are compared only when both say regular season.

The answer is cached for ``ttl_seconds`` (a few minutes); an answer with a
source missing is kept for at most ``degraded_ttl_seconds`` so it recovers
quickly. The Sleeper call has its own deadline, so a hung connection cannot
hold the endpoint for the client's full retry budget.
"""

from __future__ import annotations

import logging
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from api.services.meta_content import SEASON_BANNERS

log = logging.getLogger(__name__)

TTL_SECONDS = 180.0
DEGRADED_TTL_SECONDS = 60.0
SLEEPER_TIMEOUT_SECONDS = 5.0

SOURCE_SLEEPER = "sleeper"
SOURCE_SCHEDULE = "schedule"
SOURCE_NONE = "none"

AGREE = "agree"
DISAGREE = "disagree"
SLEEPER_UNAVAILABLE = "sleeper_unavailable"
SCHEDULE_UNAVAILABLE = "schedule_unavailable"
UNAVAILABLE = "unavailable"

#: Sleeper ``season_type`` -> the app's season type.
SLEEPER_SEASON_TYPES = {
    "pre": "preseason",
    "regular": "regular",
    "post": "postseason",
    "off": "offseason",
}
#: The app's season type -> ``mode`` (the schedule's vocabulary; preseason is week 1 ahead).
MODE_OF_TYPE = {
    "preseason": "regular",
    "regular": "regular",
    "postseason": "postseason",
    "offseason": "offseason",
}


@dataclass(frozen=True)
class View:
    """One source's answer, in comparable form."""

    season: int
    season_type: str
    week: int | None

    def to_dict(self) -> dict[str, Any]:
        return {"season": self.season, "season_type": self.season_type, "week": self.week}


def sleeper_view(state: Any) -> View:
    raw = str(getattr(state, "season_type", "") or "").strip().lower()
    season_type = SLEEPER_SEASON_TYPES.get(raw, raw or "unknown")
    week = getattr(state, "week", None)
    return View(
        season=int(state.season),
        season_type=season_type,
        week=int(week) if season_type == "regular" and week is not None else None,
    )


def schedule_view(state: Any) -> View:
    mode = str(state.mode)
    return View(
        season=int(state.season),
        season_type=mode if mode in ("regular", "postseason", "offseason") else "unknown",
        week=int(state.projection_week) if mode == "regular" and state.projection_week else None,
    )


def _preseason_matches(sleeper: View, schedule: View, schedule_state: Any) -> bool:
    """Sleeper's preseason against a schedule whose next week is week 1, nothing final yet."""
    return (
        sleeper.season_type == "preseason"
        and schedule.season_type == "regular"
        and schedule.week == 1
        and int(getattr(schedule_state, "games_final", 0) or 0) == 0
    )


def differences(sleeper: View, schedule: View, schedule_state: Any) -> list[str]:
    """Fields on which the two answers disagree (empty when they agree)."""
    out: list[str] = []
    if sleeper.season != schedule.season:
        out.append("season")
    types_match = sleeper.season_type == schedule.season_type or _preseason_matches(
        sleeper, schedule, schedule_state
    )
    if not types_match:
        out.append("season_type")
    if (
        sleeper.season_type == "regular"
        and schedule.season_type == "regular"
        and sleeper.week != schedule.week
    ):
        out.append("week")
    return out


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    return value.isoformat() if isinstance(value, datetime) else str(value)


def _call_with_deadline(fn: Callable[[], Any], seconds: float, busy: threading.Event) -> Any:
    """Run ``fn`` in a daemon thread and wait at most ``seconds``; one call in flight at a time."""
    if busy.is_set():
        raise TimeoutError("an earlier Sleeper state request is still pending")
    box: dict[str, Any] = {}
    done = threading.Event()

    def run() -> None:
        try:
            box["value"] = fn()
        except BaseException as exc:  # noqa: BLE001 - re-raised in the caller
            box["error"] = exc
        finally:
            busy.clear()
            done.set()

    busy.set()
    threading.Thread(target=run, name="ffml-season-sleeper", daemon=True).start()
    if not done.wait(seconds):
        raise TimeoutError(f"Sleeper did not answer within {seconds:g} s")
    if "error" in box:
        raise box["error"]
    return box["value"]


class SeasonService:
    """Resolves, cross-checks and caches the current season and week.

    ``resolver(now)`` returns the schedule's :class:`ffml.season.SeasonState`
    (default: the latest raw ``schedules`` snapshot under ``raw_dir``).
    ``sleeper_state()`` returns Sleeper's :class:`ffml.data.sleeper.SleeperState`;
    ``None`` means Sleeper is not used (the schedule answers alone).
    """

    def __init__(
        self,
        raw_dir: Path,
        resolver: Callable[[datetime], Any] | None = None,
        sleeper_state: Callable[[], Any] | None = None,
        *,
        ttl_seconds: float = TTL_SECONDS,
        degraded_ttl_seconds: float = DEGRADED_TTL_SECONDS,
        sleeper_timeout_seconds: float = SLEEPER_TIMEOUT_SECONDS,
        snapshot_id: Callable[[], str | None] | None = None,
    ):
        self.raw_dir = raw_dir
        self.resolver = resolver
        self.sleeper_state = sleeper_state
        self.ttl_seconds = float(ttl_seconds)
        self.degraded_ttl_seconds = float(degraded_ttl_seconds)
        self.sleeper_timeout_seconds = float(sleeper_timeout_seconds)
        self._snapshot_id = snapshot_id
        self._lock = threading.Lock()
        self._resolve_lock = threading.Lock()
        self._sleeper_busy = threading.Event()
        self._cached: tuple[float, float, dict[str, Any]] | None = None  # (at, ttl, body)
        self._last_disagreement: tuple[Any, ...] | None = None

    # -- sources --------------------------------------------------------------

    def _schedule(self, now: datetime) -> tuple[Any, str | None]:
        if self.resolver is not None:
            state = self.resolver(now)
            snap = self._snapshot_id() if self._snapshot_id is not None else None
            return state, snap
        from ffml.data.store import SnapshotStore
        from ffml.season import current_state

        store = SnapshotStore(self.raw_dir)
        snap = store.latest_id("schedules")
        return current_state(now, store=store), snap

    def _sleeper(self) -> Any:
        if self.sleeper_state is None:
            raise LookupError("Sleeper is not used for the season (FFML_SEASON_SLEEPER_ENABLED)")
        return _call_with_deadline(
            self.sleeper_state, self.sleeper_timeout_seconds, self._sleeper_busy
        )

    # -- public ---------------------------------------------------------------

    def describe(self, now: datetime | None = None) -> dict[str, Any]:
        """The ``/meta/season`` body. ``now`` (tests) bypasses the cache."""
        if now is not None:
            return self._compute(now)
        cached = self._fresh()
        if cached is not None:
            return cached
        with self._resolve_lock:  # one resolution at a time; the others reuse it
            cached = self._fresh()
            if cached is not None:
                return cached
            body = self._compute(datetime.now(UTC))
            ttl = self.ttl_seconds
            if body["cross_check"]["status"] not in (AGREE, DISAGREE):
                ttl = min(ttl, self.degraded_ttl_seconds)
            with self._lock:
                self._cached = (time.monotonic(), ttl, body)
            return body

    def clear(self) -> None:
        with self._lock:
            self._cached = None

    def _fresh(self) -> dict[str, Any] | None:
        with self._lock:
            if self._cached is None:
                return None
            at, ttl, body = self._cached
            if ttl > 0 and time.monotonic() - at < ttl:
                return body
            return None

    # -- resolution -----------------------------------------------------------

    def _compute(self, now: datetime) -> dict[str, Any]:
        schedule_state = schedule_snapshot = schedule_error = None
        try:
            schedule_state, schedule_snapshot = self._schedule(now)
        except Exception as exc:  # noqa: BLE001 - no schedule snapshot yet, or unreadable
            schedule_error = exc
        sleeper_state = sleeper_error = None
        try:
            sleeper_state = self._sleeper()
        except Exception as exc:  # noqa: BLE001 - network, HTTP, parse, timeout, disabled
            sleeper_error = exc

        sched = schedule_view(schedule_state) if schedule_state is not None else None
        sleep: View | None = None
        if sleeper_state is not None:
            try:
                sleep = sleeper_view(sleeper_state)
            except (TypeError, ValueError, AttributeError) as exc:
                sleeper_error, sleeper_state = exc, None

        cross: dict[str, Any] = {
            "status": UNAVAILABLE,
            "differences": [],
            "message": "",
            "sleeper": None,
            "schedule": None,
        }
        if sleep is not None:
            cross["sleeper"] = {
                **sleep.to_dict(),
                "display_week": getattr(sleeper_state, "display_week", None),
                "raw_season_type": getattr(sleeper_state, "season_type", None),
            }
        elif sleeper_error is not None:
            cross["sleeper_error"] = self._error_text(sleeper_error)
        if sched is not None:
            cross["schedule"] = {
                **sched.to_dict(),
                "week_in_progress": bool(schedule_state.week_in_progress),
                "last_completed_week": schedule_state.last_completed_week,
                "games_final": getattr(schedule_state, "games_final", None),
                "games_total": getattr(schedule_state, "games_total", None),
                "snapshot_id": schedule_snapshot,
            }

        if sleep is not None and sched is not None:
            diff = differences(sleep, sched, schedule_state)
            if diff:
                cross.update(status=DISAGREE, differences=diff)
                cross["message"] = self._disagreement_text(diff, sleep, sched)
                self._log_disagreement(diff, sleep, sched, schedule_snapshot)
                return self._body(now, SOURCE_SCHEDULE, sched, schedule_state, cross)
            cross.update(status=AGREE)
            cross["message"] = "Sleeper and the nflverse schedule agree."
            return self._body(now, SOURCE_SLEEPER, sleep, schedule_state, cross)
        if sched is not None:
            cross.update(status=SLEEPER_UNAVAILABLE)
            cross["message"] = (
                "Sleeper could not be reached, so the season and week come from the nflverse "
                "schedule."
                if self.sleeper_state is not None
                else "Sleeper is not used here, so the season and week come from the nflverse "
                "schedule."
            )
            if self.sleeper_state is not None:
                log.warning("season: Sleeper state unavailable (%s); using the nflverse schedule",
                            cross.get("sleeper_error"))
            return self._body(now, SOURCE_SCHEDULE, sched, schedule_state, cross)
        if sleep is not None:
            cross.update(status=SCHEDULE_UNAVAILABLE)
            cross["message"] = (
                "No nflverse schedule snapshot is loaded, so Sleeper's answer is not cross-checked."
            )
            log.warning("season: no schedule snapshot (%r); Sleeper's answer is not cross-checked",
                        schedule_error)
            return self._body(now, SOURCE_SLEEPER, sleep, None, cross)
        cross["message"] = "Neither Sleeper nor the nflverse schedule is available."
        return self._unknown(now, cross)

    @staticmethod
    def _error_text(exc: BaseException) -> str:
        text = str(exc).strip()
        name = type(exc).__name__
        return f"{name}: {text[:160]}" if text else name

    @staticmethod
    def _disagreement_text(diff: list[str], sleeper: View, schedule: View) -> str:
        parts = []
        for field in diff:
            parts.append(
                f"{field.replace('_', ' ')}: Sleeper {getattr(sleeper, field)}, "
                f"schedule {getattr(schedule, field)}"
            )
        return (
            "Sleeper and the nflverse schedule disagree (" + "; ".join(parts) + "); the "
            "schedule's answer is shown."
        )

    def _log_disagreement(
        self, diff: list[str], sleeper: View, schedule: View, snapshot: str | None
    ) -> None:
        key = (tuple(diff), sleeper, schedule, snapshot)
        if key == self._last_disagreement:
            return
        self._last_disagreement = key
        log.warning(
            "season cross-check: Sleeper and the nflverse schedule disagree on %s "
            "(Sleeper %s, schedule %s, schedules snapshot %s); showing the schedule's answer",
            ", ".join(diff),
            sleeper.to_dict(),
            schedule.to_dict(),
            snapshot,
        )

    def _body(
        self,
        now: datetime,
        source: str,
        view: View,
        schedule_state: Any,
        cross: dict[str, Any],
    ) -> dict[str, Any]:
        mode = MODE_OF_TYPE.get(view.season_type, "unknown")
        if schedule_state is not None and source == SOURCE_SCHEDULE:
            mode = str(schedule_state.mode)
        regular = mode == "regular"
        if schedule_state is not None:
            projection_week = schedule_state.projection_week if regular else None
            week_in_progress = bool(schedule_state.week_in_progress) if regular else False
            last_completed = schedule_state.last_completed_week
            next_kickoff = _iso(schedule_state.next_kickoff_utc)
        else:
            projection_week = view.week if regular else None
            week_in_progress = False
            last_completed = None
            next_kickoff = None
        week = view.week if view.week is not None else (1 if view.season_type == "preseason" else None)
        if regular and week is None:
            week = projection_week
        return {
            "season": view.season,
            "week": week,
            "season_type": view.season_type,
            "mode": mode,
            "projection_week": projection_week,
            "week_in_progress": week_in_progress,
            "last_completed_week": last_completed,
            "next_kickoff_utc": next_kickoff,
            "offseason": mode in ("postseason", "offseason") if mode != "unknown" else None,
            "banner": SEASON_BANNERS.get(mode),
            "source": source,
            "as_of": now.isoformat(),
            "cross_check": cross,
        }

    @staticmethod
    def _unknown(now: datetime, cross: dict[str, Any]) -> dict[str, Any]:
        return {
            "season": None,
            "week": None,
            "season_type": "unknown",
            "mode": "unknown",
            "projection_week": None,
            "week_in_progress": False,
            "last_completed_week": None,
            "next_kickoff_utc": None,
            "offseason": None,  # unknown, not offseason
            "banner": SEASON_BANNERS["unknown"],
            "source": SOURCE_NONE,
            "as_of": now.isoformat(),
            "cross_check": cross,
        }


__all__ = [
    "AGREE",
    "DISAGREE",
    "SCHEDULE_UNAVAILABLE",
    "SLEEPER_UNAVAILABLE",
    "SeasonService",
    "View",
    "differences",
    "schedule_view",
    "sleeper_view",
]
