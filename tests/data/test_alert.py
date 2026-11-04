"""Tests for scripts/alert.py: local log, webhook (local HTTP server), toast hook, stale check."""

from __future__ import annotations

import json
import sys
import threading
from datetime import UTC, datetime
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

import polars as pl
import pytest

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import alert as alerts  # noqa: E402

FIXTURE = Path(__file__).parent / "fixtures" / "livetest_dim_game_2026.csv"


class Hook:
    """A local webhook receiver standing in for Slack, Discord or ntfy."""

    def __init__(self, status: int = 200) -> None:
        self.requests: list[dict] = []
        hook = self

        class Handler(BaseHTTPRequestHandler):
            def do_POST(self):  # noqa: N802
                body = self.rfile.read(int(self.headers.get("Content-Length", 0)))
                hook.requests.append({"path": self.path, "type": self.headers.get("Content-Type"), "body": body})
                self.send_response(status)
                self.end_headers()

            def log_message(self, *args):
                pass

        self.server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.server.server_address[1]}/hook"
        threading.Thread(target=self.server.serve_forever, daemon=True).start()

    def close(self) -> None:
        self.server.shutdown()
        self.server.server_close()


@pytest.fixture
def alert_env(tmp_path, monkeypatch):
    monkeypatch.setenv("FFML_DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("FFML_ARTIFACTS_DIR", str(tmp_path / "artifacts"))
    monkeypatch.setenv("FFML_ALERT_LOG", str(tmp_path / "alerts.jsonl"))
    monkeypatch.setenv("FFML_ALERT_TOAST", "0")
    monkeypatch.delenv("FFML_ALERT_WEBHOOK", raising=False)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    monkeypatch.setattr(alerts, "_dotenv", lambda: {})
    hook = Hook()
    yield tmp_path, hook
    hook.close()


def log_lines(tmp_path: Path) -> list[dict]:
    return [json.loads(line) for line in (tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()]


def test_alert_goes_to_log_and_webhook(alert_env, monkeypatch):
    tmp_path, hook = alert_env
    monkeypatch.setenv("FFML_ALERT_WEBHOOK", hook.url)
    result = alerts.send_alert("build_failure", "2026w10: build-week failed", details={"exit": 1})
    assert result.webhook == "sent (200)" and not result.suppressed
    assert len(hook.requests) == 1
    body = json.loads(hook.requests[0]["body"])
    assert body["text"].startswith("[ffml ERROR] build_failure: 2026w10")
    assert body["alert"]["details"] == {"exit": 1}
    (line,) = log_lines(tmp_path)
    assert line["kind"] == "build_failure" and line["channels"]["webhook"] == "sent (200)"


def test_no_webhook_configured_still_logs(alert_env):
    tmp_path, hook = alert_env
    result = alerts.send_alert("refresh_failure", "ingest failed")
    assert result.webhook == "not configured" and hook.requests == []
    assert log_lines(tmp_path)[0]["kind"] == "refresh_failure"


def test_webhook_failure_is_recorded_not_raised(alert_env, monkeypatch):
    tmp_path, _ = alert_env
    monkeypatch.setenv("FFML_ALERT_WEBHOOK", "http://127.0.0.1:9/nothing-listens-here")
    result = alerts.send_alert("push_failure", "git push failed")
    assert result.webhook.startswith("failed:")
    assert log_lines(tmp_path)[0]["channels"]["webhook"].startswith("failed:")


def test_repeats_inside_the_cooldown_are_logged_but_not_sent(alert_env, monkeypatch):
    tmp_path, hook = alert_env
    monkeypatch.setenv("FFML_ALERT_WEBHOOK", hook.url)
    t0 = datetime(2026, 11, 12, 16, 15, tzinfo=UTC)
    first = alerts.send_alert("push_failure", "x", dedupe_key="k", cooldown_minutes=120, now=t0)
    second = alerts.send_alert("push_failure", "x", dedupe_key="k", cooldown_minutes=120,
                               now=t0.replace(hour=17))
    third = alerts.send_alert("push_failure", "x", dedupe_key="k", cooldown_minutes=120,
                              now=t0.replace(hour=18, minute=30))
    assert not first.suppressed and second.suppressed and not third.suppressed
    assert len(hook.requests) == 2 and len(log_lines(tmp_path)) == 3


def test_toast_channel_is_called_when_enabled(alert_env, monkeypatch):
    calls = []
    monkeypatch.setattr(alerts, "show_toast", lambda title, message: calls.append((title, message)) or "shown")
    assert alerts.send_alert("missed_commitment", "no push", toast=True).toast == "shown"
    monkeypatch.setenv("FFML_ALERT_TOAST", "0")
    assert alerts.send_alert("missed_commitment", "no push").toast == "disabled"
    assert calls == [("ffml error: missed_commitment", "no push")]


def test_payload_formats():
    alert = {"severity": "error", "kind": "test", "message": "hello"}
    body, ctype = alerts.webhook_payload(alert, "https://hooks.slack.com/services/x")
    assert json.loads(body) == {"text": "[ffml ERROR] test: hello"}
    body, _ = alerts.webhook_payload(alert, "https://discord.com/api/webhooks/1/abc")
    assert json.loads(body) == {"content": "[ffml ERROR] test: hello"}
    body, ctype = alerts.webhook_payload(alert, "https://ntfy.sh/topic")
    assert body == b"[ffml ERROR] test: hello" and ctype.startswith("text/plain")


def test_unknown_kind_is_rejected(alert_env):
    with pytest.raises(ValueError):
        alerts.send_alert("not_a_kind", "x")


def test_cli_test_command(alert_env, monkeypatch, capsys):
    tmp_path, hook = alert_env
    monkeypatch.setenv("FFML_ALERT_WEBHOOK", hook.url)
    assert alerts.main(["test", "--no-toast"]) == 0
    assert json.loads(hook.requests[0]["body"])["alert"]["kind"] == "test"
    assert '"webhook": "sent (200)"' in capsys.readouterr().out


def write_schedule(data_dir: Path) -> None:
    from ffml.data.store import SnapshotStore

    frame = pl.read_csv(FIXTURE).with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("kickoff_utc").str.to_datetime("%Y-%m-%dT%H:%M:%SZ", time_zone="UTC"),
    )
    SnapshotStore(data_dir / "processed").write_snapshot("dim_game", {2026: frame})


def write_latest(root: Path, written_at: str) -> None:
    root.mkdir(parents=True, exist_ok=True)
    (root / "LATEST").write_text(json.dumps({"run_id": "r1", "written_at": written_at}), encoding="utf-8")


def test_stale_predictions_with_clock_override(alert_env, monkeypatch, capsys):
    tmp_path, _ = alert_env
    data = tmp_path / "data"
    write_schedule(data)
    write_latest(data / "predictions_private", "2026-11-12T16:30:00Z")
    fresh = alerts.check_stale(datetime(2026, 11, 19, 12, 0, tzinfo=UTC))
    assert fresh == []  # 6.8 days old
    stale = alerts.check_stale(datetime(2026, 11, 21, 12, 0, tzinfo=UTC))
    assert [f["store"] for f in stale] == ["private"] and "8.8 days" in stale[0]["problem"]
    assert alerts.check_stale(datetime(2027, 3, 1, tzinfo=UTC)) == []  # offseason
    assert alerts.main(["check-stale", "--now", "2026-11-21T12:00:00Z", "--no-toast"]) == 1
    assert log_lines(tmp_path)[-1]["kind"] == "stale_predictions"
    assert alerts.main(["check-stale", "--now", "2026-11-19T12:00:00Z", "--no-toast"]) == 0
    capsys.readouterr()


def test_public_store_is_checked_only_with_a_live_model(alert_env):
    tmp_path, _ = alert_env
    data = tmp_path / "data"
    write_schedule(data)
    now = datetime(2026, 11, 21, 12, 0, tzinfo=UTC)
    assert alerts.check_stale(now) == []  # no live model (D-099): no public store expected
    registry = tmp_path / "artifacts" / "models" / "REGISTRY.json"
    registry.parent.mkdir(parents=True)
    registry.write_text(json.dumps({"models": [{"version": "v2", "status": "live"}]}), encoding="utf-8")
    findings = alerts.check_stale(now)
    assert [(f["store"], f["problem"]) for f in findings] == [("public", "no LATEST pointer")]
