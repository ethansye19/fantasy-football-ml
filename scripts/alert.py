"""Failure alerts (QA-5.1 core): local alert log, optional webhook, Windows toast.

Every alert is appended to a local JSON-lines log first, so nothing is lost
when the network or the desktop session is unavailable. Then, if configured,
it is posted to a webhook and shown as a Windows toast notification. No new
dependencies: the webhook uses ``urllib`` and the toast uses the WinRT API
that ships with Windows PowerShell 5.1.

Usage::

    python scripts/alert.py test [--no-toast] [--no-webhook]
    python scripts/alert.py send --kind build_failure --message "..." [--severity error]
    python scripts/alert.py check-stale [--now 2026-12-01T12:00:00Z] [--max-age-days 8]
    python scripts/alert.py tail [-n 20]

Environment:

    FFML_ALERT_WEBHOOK         webhook URL (fallback: ALERT_WEBHOOK_URL, as in .env.example)
    FFML_ALERT_WEBHOOK_FORMAT  auto (default), json, slack, discord or text
    FFML_ALERT_TOAST           1 or 0 (default 1 on Windows, 0 elsewhere)
    FFML_ALERT_LOG             alert log path (default <data_dir>/alerts/alerts.jsonl)
    FFML_DATA_DIR              data directory (default <repo>/data)

The variables are read from the process environment and from ``<repo>/.env``.
This module does not import ``ffml`` at load time, so it still works when the
pipeline environment is broken (the stale check imports it lazily).
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
import sys
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]

KINDS: dict[str, str] = {
    "refresh_failure": "data refresh failed",
    "data_quality": "data-quality check failed",
    "build_failure": "live-test build failed or was blocked",
    "push_failure": "commitment push failed",
    "missed_commitment": "no pushed commitment before kickoff",
    "stale_predictions": "predictions are stale",
    "integrity": "integrity check failed",
    "schedule_drift": "schedule changed since the tasks were registered",
    "test": "test alert",
    "info": "information",
}
SEVERITIES = ("info", "warning", "error", "critical")
DEFAULT_STALE_DAYS = 8.0


# --------------------------------------------------------------------------- #
# Configuration
# --------------------------------------------------------------------------- #


def _dotenv() -> dict[str, str]:
    path = REPO_ROOT / ".env"
    if not path.exists():
        return {}
    try:
        from dotenv import dotenv_values

        return {k: v for k, v in dotenv_values(path).items() if v is not None}
    except Exception:  # pragma: no cover - python-dotenv is a core dependency
        return {}


def _env(name: str, default: str | None = None) -> str | None:
    value = os.environ.get(name)
    if value is None:
        value = _dotenv().get(name)
    return value if value not in (None, "") else default


def data_dir() -> Path:
    value = _env("FFML_DATA_DIR")
    return Path(value).resolve() if value else REPO_ROOT / "data"


def artifacts_dir() -> Path:
    value = _env("FFML_ARTIFACTS_DIR")
    return Path(value).resolve() if value else REPO_ROOT / "artifacts"


def alert_log_path() -> Path:
    value = _env("FFML_ALERT_LOG")
    return Path(value) if value else data_dir() / "alerts" / "alerts.jsonl"


def _state_path() -> Path:
    return alert_log_path().with_name("alert_state.json")


def webhook_url() -> str | None:
    return _env("FFML_ALERT_WEBHOOK") or _env("ALERT_WEBHOOK_URL")


def toast_enabled() -> bool:
    value = _env("FFML_ALERT_TOAST")
    if value is None:
        return os.name == "nt"
    return value.strip().lower() not in ("0", "false", "no", "off")


def _iso(ts: datetime) -> str:
    return ts.astimezone(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def parse_time(text: str) -> datetime:
    """Parse an ISO time; a missing zone means UTC."""
    ts = datetime.fromisoformat(text.strip().replace("Z", "+00:00"))
    return ts if ts.tzinfo else ts.replace(tzinfo=UTC)


# --------------------------------------------------------------------------- #
# Channels
# --------------------------------------------------------------------------- #


def _summary(alert: dict[str, Any]) -> str:
    return f"[ffml {alert['severity'].upper()}] {alert['kind']}: {alert['message']}"


def webhook_payload(alert: dict[str, Any], url: str, fmt: str | None = None) -> tuple[bytes, str]:
    """Body and content type for ``url``; ``auto`` picks Slack, Discord or ntfy by host."""
    fmt = (fmt or _env("FFML_ALERT_WEBHOOK_FORMAT") or "auto").lower()
    if fmt == "auto":
        host = urllib.parse.urlparse(url).netloc.lower()
        if "hooks.slack.com" in host:
            fmt = "slack"
        elif "discord.com" in host or "discordapp.com" in host:
            fmt = "discord"
        elif "ntfy" in host:
            fmt = "text"
        else:
            fmt = "json"
    text = _summary(alert)
    if fmt == "slack":
        body: Any = {"text": text}
    elif fmt == "discord":
        body = {"content": text[:1900]}
    elif fmt == "text":
        return text.encode("utf-8"), "text/plain; charset=utf-8"
    else:
        body = {"text": text, "alert": alert}
    return json.dumps(body, default=str).encode("utf-8"), "application/json"


def post_webhook(url: str, body: bytes, content_type: str, timeout: float = 10.0) -> int:
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": content_type, "User-Agent": "ffml-alert/1"},
    )
    with urllib.request.urlopen(request, timeout=timeout) as response:  # noqa: S310 - configured URL
        return int(response.status)


_TOAST_PS = r"""
$ErrorActionPreference = 'Stop'
[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType = WindowsRuntime] | Out-Null
[Windows.Data.Xml.Dom.XmlDocument, Windows.Data.Xml.Dom.XmlDocument, ContentType = WindowsRuntime] | Out-Null
$title = [Security.SecurityElement]::Escape($env:FFML_TOAST_TITLE)
$body = [Security.SecurityElement]::Escape($env:FFML_TOAST_BODY)
$xml = New-Object Windows.Data.Xml.Dom.XmlDocument
$xml.LoadXml("<toast><visual><binding template='ToastGeneric'><text>$title</text><text>$body</text></binding></visual></toast>")
$appId = '{1AC14E77-02E7-4E5D-B744-2EB1AE5198B7}\WindowsPowerShell\v1.0\powershell.exe'
[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier($appId).Show([Windows.UI.Notifications.ToastNotification]::new($xml))
"""


def show_toast(title: str, message: str, timeout: float = 20.0) -> str:
    """Show a Windows toast through Windows PowerShell; returns a status string."""
    if os.name != "nt":
        return "unavailable (not Windows)"
    env = dict(os.environ, FFML_TOAST_TITLE=title[:120], FFML_TOAST_BODY=message[:400])
    try:
        proc = subprocess.run(
            ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _TOAST_PS],
            env=env,
            capture_output=True,
            text=True,
            timeout=timeout,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        return f"failed: {type(exc).__name__}: {exc}"
    if proc.returncode != 0:
        return f"failed: {(proc.stderr or proc.stdout).strip()[:300]}"
    return "shown"


# --------------------------------------------------------------------------- #
# Dispatch
# --------------------------------------------------------------------------- #


@dataclass
class AlertResult:
    alert: dict[str, Any]
    log_path: str
    webhook: str = "not configured"
    toast: str = "disabled"
    suppressed: bool = False
    channels: dict[str, str] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


def _load_state() -> dict[str, str]:
    try:
        return json.loads(_state_path().read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return {}


def _save_state(state: dict[str, str]) -> None:
    path = _state_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(state, indent=1, sort_keys=True) + "\n", encoding="utf-8")
    os.replace(tmp, path)


def _append_log(record: dict[str, Any]) -> Path:
    path = alert_log_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8", newline="\n") as handle:
        handle.write(json.dumps(record, default=str, sort_keys=True) + "\n")
    return path


def send_alert(
    kind: str,
    message: str,
    *,
    severity: str = "error",
    details: dict[str, Any] | None = None,
    dedupe_key: str | None = None,
    cooldown_minutes: float = 0.0,
    webhook: str | None | bool = True,
    toast: bool | None = None,
    now: datetime | None = None,
) -> AlertResult:
    """Log an alert locally, then post it to the webhook and show a toast.

    ``webhook``: True uses the configured URL, a string overrides it, False or
    None disables it. ``toast``: None follows ``FFML_ALERT_TOAST``.
    ``dedupe_key`` with ``cooldown_minutes`` > 0 suppresses the webhook and
    toast (never the log line) for repeats inside the cooldown, so a failure
    retried every 30 minutes does not page every 30 minutes.
    Channel failures are recorded, never raised.
    """
    if kind not in KINDS:
        raise ValueError(f"unknown alert kind {kind!r}; known: {sorted(KINDS)}")
    if severity not in SEVERITIES:
        raise ValueError(f"severity must be one of {SEVERITIES}")
    now = now or datetime.now(UTC)
    alert = {
        "ts": _iso(now),
        "kind": kind,
        "severity": severity,
        "message": message,
        "details": details or {},
        "host": os.environ.get("COMPUTERNAME") or os.environ.get("HOSTNAME") or "",
    }
    result = AlertResult(alert=alert, log_path=str(alert_log_path()))

    if dedupe_key and cooldown_minutes > 0:
        state = _load_state()
        last = state.get(dedupe_key)
        if last and now - parse_time(last) < timedelta(minutes=cooldown_minutes):
            result.suppressed = True
            result.webhook = result.toast = f"suppressed (repeat of {last})"
        else:
            state[dedupe_key] = _iso(now)
            _save_state(state)

    if not result.suppressed:
        url = webhook_url() if webhook is True else (webhook or None)
        if url:
            try:
                body, ctype = webhook_payload(alert, url)
                result.webhook = f"sent ({post_webhook(url, body, ctype)})"
            except (urllib.error.URLError, OSError, ValueError) as exc:
                result.webhook = f"failed: {type(exc).__name__}: {exc}"
        if toast if toast is not None else toast_enabled():
            result.toast = show_toast(f"ffml {severity}: {kind}", message)

    result.channels = {"log": "written", "webhook": result.webhook, "toast": result.toast}
    _append_log({**alert, "channels": result.channels, "suppressed": result.suppressed})
    return result


# --------------------------------------------------------------------------- #
# Stale predictions
# --------------------------------------------------------------------------- #


def _reg_season_window(now: datetime) -> tuple[int, datetime, datetime] | None:
    """(season, first REG kickoff, last REG kickoff) of the season containing ``now``."""
    import polars as pl

    from ffml.data.store import SnapshotStore

    games = SnapshotStore(data_dir() / "processed").read(
        "dim_game", columns=["season", "game_type", "kickoff_utc"]
    )
    spans = (
        games.filter(pl.col("game_type") == "REG")
        .group_by("season")
        .agg(pl.col("kickoff_utc").min().alias("first"), pl.col("kickoff_utc").max().alias("last"))
        .sort("season")
    )
    for season, first, last in spans.iter_rows():
        if first <= now <= last + timedelta(hours=6):
            return int(season), first, last
    return None


def _live_version() -> str | None:
    path = artifacts_dir() / "models" / "REGISTRY.json"
    try:
        models = json.loads(path.read_text(encoding="utf-8")).get("models", [])
    except (OSError, ValueError):
        return None
    live = [m.get("version") for m in models if m.get("status") == "live"]
    return live[0] if live else None


def check_stale(
    now: datetime | None = None, max_age_days: float = DEFAULT_STALE_DAYS
) -> list[dict[str, Any]]:
    """Stale-prediction findings for the public and private stores (empty when fine).

    Only checked during the regular season. The public store is expected only
    when the registry has a live (approved) version; under D-099 there is none
    and the check reports "not applicable". The private store is checked when
    it has a ``LATEST`` pointer; a missed live-test week is the watchdog's job.
    """
    now = now or datetime.now(UTC)
    window = _reg_season_window(now)
    if window is None:
        return []
    season = window[0]
    findings: list[dict[str, Any]] = []
    stores = {
        "public": (data_dir() / "predictions", _live_version() is not None),
        "private": (data_dir() / "predictions_private", False),
    }
    for name, (root, required) in stores.items():
        pointer = root / "LATEST"
        if not pointer.exists():
            if required:
                findings.append(
                    {"store": name, "season": season, "problem": "no LATEST pointer", "path": str(pointer)}
                )
            continue
        try:
            latest = json.loads(pointer.read_text(encoding="utf-8"))
            written = parse_time(str(latest["written_at"]))
        except (OSError, ValueError, KeyError) as exc:
            findings.append({"store": name, "problem": f"unreadable LATEST: {exc}", "path": str(pointer)})
            continue
        age = now - written
        if age > timedelta(days=max_age_days):
            findings.append(
                {
                    "store": name,
                    "season": season,
                    "problem": f"newest run is {age.total_seconds() / 86400:.1f} days old "
                    f"(limit {max_age_days:g})",
                    "written_at": _iso(written),
                    "run_id": latest.get("run_id"),
                    "path": str(pointer),
                }
            )
    return findings


# --------------------------------------------------------------------------- #
# CLI
# --------------------------------------------------------------------------- #


def _print(obj: Any) -> None:
    print(json.dumps(obj, indent=2, default=str))


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    sub = parser.add_subparsers(dest="command", required=True)

    p_test = sub.add_parser("test", help="send a test alert through every configured channel")
    p_test.add_argument("--no-toast", action="store_true")
    p_test.add_argument("--no-webhook", action="store_true")

    p_send = sub.add_parser("send", help="send one alert")
    p_send.add_argument("--kind", required=True, choices=sorted(KINDS))
    p_send.add_argument("--message", required=True)
    p_send.add_argument("--severity", default="error", choices=SEVERITIES)
    p_send.add_argument("--details", default=None, help="JSON object")
    p_send.add_argument("--no-toast", action="store_true")

    p_stale = sub.add_parser("check-stale", help="alert when predictions are older than N days in season")
    p_stale.add_argument("--now", default=None, help="clock override (ISO time, UTC)")
    p_stale.add_argument("--max-age-days", type=float, default=DEFAULT_STALE_DAYS)
    p_stale.add_argument("--no-toast", action="store_true")

    p_tail = sub.add_parser("tail", help="print the newest alerts from the local log")
    p_tail.add_argument("-n", type=int, default=20)

    args = parser.parse_args(argv)
    if args.command == "test":
        result = send_alert(
            "test",
            "test alert from scripts/alert.py; no action needed",
            severity="info",
            webhook=False if args.no_webhook else True,
            toast=False if args.no_toast else None,
        )
        _print(result.to_dict())
        return 0 if not result.webhook.startswith("failed") else 1
    if args.command == "send":
        details = json.loads(args.details) if args.details else None
        result = send_alert(
            args.kind,
            args.message,
            severity=args.severity,
            details=details,
            toast=False if args.no_toast else None,
        )
        _print(result.to_dict())
        return 0
    if args.command == "check-stale":
        now = parse_time(args.now) if args.now else None
        findings = check_stale(now, args.max_age_days)
        if findings:
            message = "; ".join(f"{f['store']}: {f['problem']}" for f in findings)
            result = send_alert(
                "stale_predictions",
                message,
                severity="warning",
                details={"findings": findings, "clock_override": args.now},
                dedupe_key="stale_predictions",
                cooldown_minutes=0 if args.now else 12 * 60,
                toast=False if args.no_toast else None,
                now=now,
            )
            _print({"stale": True, "findings": findings, "alert": result.to_dict()})
            return 1
        _print({"stale": False, "checked_at": _iso(now or datetime.now(UTC))})
        return 0
    if args.command == "tail":
        path = alert_log_path()
        lines = path.read_text(encoding="utf-8").splitlines() if path.exists() else []
        for line in lines[-args.n :]:
            print(line)
        return 0
    return 2  # pragma: no cover


if __name__ == "__main__":
    sys.exit(main())
