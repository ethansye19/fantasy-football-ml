"""GitHub Actions watchdog for the live test (D-107): alerts 6 h and 1 h before kickoff.

Runs hourly from ``.github/workflows/livetest_watchdog.yml`` on ``main``, so it
does not depend on the laptop. It reads the tracked schedule
``scripts/livetest_schedule_2026.json`` (exported with
``scripts/livetest_week.py plan --export``), takes the next test week, and when
the run falls inside an alert window checks the commitments branch for
``<week>/commitment.json`` through the GitHub contents API. Missing inside a
window: exit 1 (the workflow run fails, and GitHub mails the owner) and, when
``FFML_ALERT_WEBHOOK`` is set (a repository secret), an alert through
``scripts/alert.py`` (log plus webhook; no toast on a runner).

Windows, in hours before the official first kickoff: ``[6.5, 5.5)`` and
``[1.5, 0)``. An hourly cron at :00 lands 6 h to 6 h 15 min and 1 h to
1 h 15 min before the usual 01:00Z and 01:15Z kickoffs; a run delayed by up to
30 minutes still falls inside, and the last run before kickoff alerts as well.
The check is stateless: every run inside a window with nothing pushed alerts.

    python scripts/livetest_watchdog_gh.py --schedule scripts/livetest_schedule_2026.json
        [--branch livetest-2026] [--repo owner/name] [--now 2026-11-12T19:00:00Z]
        [--force-window] [--json]

Standard library only, so it runs on a bare ``ubuntu-latest`` Python. The token
comes from ``GITHUB_TOKEN`` (or ``GH_TOKEN``); the repository from ``--repo`` or
``GITHUB_REPOSITORY``.

Exit codes: 0 nothing to do, or the commitment is there; 1 inside a window with
no commitment (alert raised); 2 configuration or API error.
"""

from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

DEFAULT_BRANCH = "livetest-2026"
DEFAULT_SCHEDULE = SCRIPTS_DIR / "livetest_schedule_2026.json"
# (label, start, end) in hours before kickoff; a run with start > hours_left >= end is inside
WINDOWS: tuple[tuple[str, float, float], ...] = (("6h", 6.5, 5.5), ("1h", 1.5, 0.0))
COMMITMENT = "commitment.json"
MISSED = "MISSED.json"
LATE = "late/commitment.json"
EXIT_OK, EXIT_ALERT, EXIT_ERROR = 0, 1, 2


def parse_time(text: str) -> datetime:
    ts = datetime.fromisoformat(str(text).strip().replace("Z", "+00:00"))
    return ts.replace(tzinfo=UTC) if ts.tzinfo is None else ts.astimezone(UTC)


def iso(ts: datetime) -> str:
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def load_schedule(path: Path | str) -> dict[str, Any]:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    if not isinstance(data, dict) or not isinstance(data.get("weeks"), list) or not data["weeks"]:
        raise ValueError(f"{path} has no 'weeks' list")
    return data


def pick_week(schedule: dict[str, Any], now: datetime) -> dict[str, Any] | None:
    """The test week whose first kickoff is next (strictly after ``now``)."""
    test_weeks = set(schedule.get("test_weeks") or [])
    upcoming = [
        w for w in schedule["weeks"]
        if (not test_weeks or int(w["week"]) in test_weeks) and parse_time(w["first_kickoff_utc"]) > now
    ]
    return min(upcoming, key=lambda w: parse_time(w["first_kickoff_utc"])) if upcoming else None


def window_for(now: datetime, kickoff: datetime) -> tuple[str, float] | None:
    """``(label, hours_left)`` when ``now`` is inside an alert window before ``kickoff``."""
    hours_left = (kickoff - now).total_seconds() / 3600.0
    for label, start, end in WINDOWS:
        if hours_left > 0 and start > hours_left >= end:
            return label, hours_left
    return None


class GitHubContents:
    """Existence checks on a branch through the GitHub contents API (read only)."""

    def __init__(self, repo: str, branch: str, token: str | None = None, api: str = "https://api.github.com") -> None:
        if not repo or "/" not in repo:
            raise ValueError(f"repository must be owner/name, got {repo!r}")
        self.repo, self.branch, self.token, self.api = repo, branch, token, api.rstrip("/")

    def exists(self, path: str) -> bool:
        url = (f"{self.api}/repos/{self.repo}/contents/{urllib.parse.quote(path, safe='/')}"
               f"?ref={urllib.parse.quote(self.branch, safe='')}")
        headers = {"Accept": "application/vnd.github+json", "User-Agent": "ffml-livetest-watchdog/1",
                   "X-GitHub-Api-Version": "2022-11-28"}
        if self.token:
            headers["Authorization"] = f"Bearer {self.token}"
        request = urllib.request.Request(url, headers=headers, method="GET")
        try:
            with urllib.request.urlopen(request, timeout=30) as response:  # noqa: S310 - fixed API host
                return 200 <= int(response.status) < 300
        except urllib.error.HTTPError as exc:
            if exc.code == 404:
                return False
            raise RuntimeError(f"GitHub API {exc.code} for {path} on {self.branch}: {exc.reason}") from exc


def decide(schedule: dict[str, Any], now: datetime, exists: Callable[[str], bool], *,
           force_window: bool = False) -> dict[str, Any]:
    """What this run concludes; pure apart from the ``exists`` calls."""
    result: dict[str, Any] = {
        "now": iso(now), "week": None, "kickoff": None, "hours_to_kickoff": None, "window": None,
        "checked": False, "commitment": None, "missed": None, "late": None, "alert": False,
        "message": "", "exit_code": EXIT_OK,
    }
    week = pick_week(schedule, now)
    if week is None:
        result["message"] = "no upcoming test week in the schedule; nothing to check"
        return result
    kickoff = parse_time(week["first_kickoff_utc"])
    hours = (kickoff - now).total_seconds() / 3600.0
    window = window_for(now, kickoff)
    result.update(week=week["key"], kickoff=iso(kickoff), hours_to_kickoff=round(hours, 2),
                  window=window[0] if window else None)
    if window is None and not force_window:
        result["message"] = f"{week['key']}: {hours:.1f} h to kickoff; outside the alert windows"
        return result
    result["checked"] = True
    result["commitment"] = bool(exists(f"{week['key']}/{COMMITMENT}"))
    if result["commitment"]:
        result["message"] = f"{week['key']}: commitment is on the branch ({hours:.1f} h before kickoff)"
        return result
    result["missed"] = bool(exists(f"{week['key']}/{MISSED}"))
    result["late"] = bool(exists(f"{week['key']}/{LATE}"))
    result["alert"] = True
    result["exit_code"] = EXIT_ALERT
    label = window[0] if window else "forced"
    result["message"] = (
        f"{week['key']}: NO commitment on the branch {hours:.1f} h before the first kickoff {iso(kickoff)} "
        f"(window {label}); run scripts/livetest_week.py run on the laptop now (docs/RUNBOOK.md section 7)"
    )
    return result


def dispatch_alert(result: dict[str, Any], branch: str) -> dict[str, Any]:
    """Log plus webhook through scripts/alert.py; never raises."""
    try:
        import alert as alerts

        sent = alerts.send_alert(
            "missed_commitment", result["message"], severity="critical",
            details={"source": "github watchdog", "branch": branch, **{k: result[k] for k in (
                "week", "kickoff", "hours_to_kickoff", "window", "missed", "late")}},
            toast=False,
        )
        return dict(sent.channels)
    except Exception as exc:  # the workflow failure is the alert of last resort
        return {"error": f"{type(exc).__name__}: {exc}"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="GitHub Actions watchdog for the live-test commitments branch")
    parser.add_argument("--schedule", type=Path, default=DEFAULT_SCHEDULE)
    parser.add_argument("--branch", default=DEFAULT_BRANCH)
    parser.add_argument("--repo", default=os.environ.get("GITHUB_REPOSITORY"), help="owner/name")
    parser.add_argument("--now", default=None, help="clock override, ISO UTC")
    parser.add_argument("--force-window", action="store_true", help="check even outside the alert windows")
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args(argv)
    try:
        schedule = load_schedule(args.schedule)
        now = parse_time(args.now) if args.now else datetime.now(UTC)
        token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN") or None
        client = GitHubContents(args.repo or "", args.branch, token)
        result = decide(schedule, now, client.exists, force_window=args.force_window)
    except (OSError, ValueError, RuntimeError) as exc:
        print(f"watchdog error: {type(exc).__name__}: {exc}", file=sys.stderr)
        return EXIT_ERROR
    result["branch"] = args.branch
    if result["alert"]:
        result["alert_channels"] = dispatch_alert(result, args.branch)
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(result["message"])
    if result["alert"]:
        print(f"::error title=livetest watchdog::{result['message']}")
    return int(result["exit_code"])


if __name__ == "__main__":
    sys.exit(main())
