"""Tests for the GitHub Actions watchdog (scripts/livetest_watchdog_gh.py): windows, API, webhook."""

from __future__ import annotations

import io
import json
import sys
import urllib.error
from datetime import UTC, datetime, timedelta
from pathlib import Path

SCRIPTS = Path(__file__).resolve().parents[2] / "scripts"
if str(SCRIPTS) not in sys.path:
    sys.path.insert(0, str(SCRIPTS))

import livetest_watchdog_gh as wd  # noqa: E402

K10 = datetime(2026, 11, 13, 1, 15, tzinfo=UTC)
K11 = datetime(2026, 11, 20, 1, 15, tzinfo=UTC)
K12 = datetime(2026, 11, 26, 1, 0, tzinfo=UTC)  # Thanksgiving eve, Wednesday 20:00 ET
K13 = datetime(2026, 12, 4, 1, 15, tzinfo=UTC)


def schedule() -> dict:
    return {
        "format": "ffml.livetest.schedule/1",
        "season": 2026,
        "test_weeks": [10, 11, 12, 13],
        "weeks": [
            {"key": f"2026w{w:02d}", "season": 2026, "week": w, "first_kickoff_utc": k.strftime("%Y-%m-%dT%H:%M:%SZ"),
             "tentative": False, "games": 14}
            for w, k in ((9, K10 - timedelta(days=7)), (10, K10), (11, K11), (12, K12), (13, K13))
        ],
    }


def test_next_test_week_and_alert_windows_around_the_thanksgiving_kickoff():
    s = schedule()
    assert wd.pick_week(s, K10 - timedelta(days=5))["week"] == 10  # week 9 is not a test week
    assert wd.pick_week(s, K12 - timedelta(days=3))["week"] == 12
    assert wd.pick_week(s, K12)["week"] == 13  # at kickoff the next week is next
    assert wd.pick_week(s, datetime(2027, 2, 1, tzinfo=UTC)) is None
    assert wd.window_for(K12 - timedelta(hours=6), K12)[0] == "6h"  # the 19:00Z cron slot
    assert wd.window_for(K12 - timedelta(hours=5, minutes=35), K12)[0] == "6h"  # that slot, 25 min late
    assert wd.window_for(K12 - timedelta(hours=5, minutes=20), K12) is None
    assert wd.window_for(K12 - timedelta(hours=3), K12) is None
    assert wd.window_for(K12 - timedelta(hours=1), K12)[0] == "1h"  # the 00:00Z slot
    assert wd.window_for(K12 - timedelta(minutes=10), K12)[0] == "1h"  # the last slot before kickoff
    assert wd.window_for(K12, K12) is None
    assert wd.window_for(K10 - timedelta(hours=6, minutes=15), K10)[0] == "6h"  # 01:15Z kickoffs, 19:00Z slot


def test_decide_alerts_only_inside_a_window_without_a_commitment():
    s = schedule()
    present = {"2026w12/commitment.json"}
    r = wd.decide(s, K12 - timedelta(hours=6), lambda p: p in present)
    assert r["checked"] and r["commitment"] and not r["alert"] and r["exit_code"] == 0
    r = wd.decide(s, K12 - timedelta(hours=3), lambda p: False)
    assert not r["checked"] and r["exit_code"] == 0 and "outside" in r["message"]
    r = wd.decide(s, K12 - timedelta(hours=1), lambda p: False)
    assert r["alert"] and r["exit_code"] == 1 and r["week"] == "2026w12" and r["window"] == "1h"
    assert "NO commitment" in r["message"] and "2026-11-26T01:00:00Z" in r["message"]
    assert r["missed"] is False and r["late"] is False
    r = wd.decide(s, K12 - timedelta(hours=3), lambda p: False, force_window=True)
    assert r["alert"] and r["window"] is None and r["exit_code"] == 1
    r = wd.decide(s, datetime(2027, 3, 1, tzinfo=UTC), lambda p: False)
    assert r["week"] is None and r["exit_code"] == 0 and not r["checked"]


class _Response:
    status = 200

    def __enter__(self):
        return self

    def __exit__(self, *exc):
        return False


def test_main_uses_the_contents_api_and_dispatches_the_alert(tmp_path, monkeypatch):
    path = tmp_path / "schedule.json"
    path.write_text(json.dumps(schedule()), encoding="utf-8")
    requested: list[tuple[str, str | None]] = []

    def fake_urlopen(request, timeout=0):
        requested.append((request.full_url, request.get_header("Authorization")))
        if "2026w10/" in request.full_url:
            raise urllib.error.HTTPError(request.full_url, 404, "Not Found", {}, io.BytesIO(b"{}"))
        return _Response()

    sent: list[dict] = []
    monkeypatch.setattr(wd.urllib.request, "urlopen", fake_urlopen)
    monkeypatch.setattr(wd, "dispatch_alert", lambda result, branch: sent.append(result) or {"webhook": "sent (200)"})
    monkeypatch.setenv("GITHUB_TOKEN", "t0k")
    args = ["--schedule", str(path), "--repo", "o/r", "--branch", "livetest-2026", "--json"]
    assert wd.main([*args, "--now", "2026-11-12T19:00:00Z"]) == 1  # week 10, 6 h window, nothing on the branch
    assert len(sent) == 1 and sent[0]["week"] == "2026w10" and sent[0]["alert_channels"]["webhook"] == "sent (200)"
    assert requested[0][0] == "https://api.github.com/repos/o/r/contents/2026w10/commitment.json?ref=livetest-2026"
    assert requested[0][1] == "Bearer t0k"
    assert wd.main([*args, "--now", "2026-11-12T12:00:00Z"]) == 0  # outside the windows: no API call
    assert len(requested) == 3  # commitment, MISSED and late for the first run only
    assert wd.main([*args, "--now", "2026-11-19T19:15:00Z"]) == 0  # week 11: the commitment is there (200)
    assert len(sent) == 1
    assert wd.main(["--schedule", str(tmp_path / "missing.json"), "--repo", "o/r"]) == 2
    assert wd.main([*args[:2], "--repo", "not-a-repo", "--now", "2026-11-12T19:00:00Z"]) == 2


def test_dispatch_alert_logs_locally_without_a_webhook(tmp_path, monkeypatch):
    monkeypatch.setenv("FFML_ALERT_LOG", str(tmp_path / "alerts.jsonl"))
    monkeypatch.setenv("FFML_ALERT_TOAST", "0")
    monkeypatch.delenv("FFML_ALERT_WEBHOOK", raising=False)
    monkeypatch.delenv("ALERT_WEBHOOK_URL", raising=False)
    import alert as alerts

    monkeypatch.setattr(alerts, "_dotenv", lambda: {})
    result = wd.decide(schedule(), K10 - timedelta(hours=1), lambda p: False)
    channels = wd.dispatch_alert(result, "livetest-2026")
    assert channels["log"] == "written" and channels["webhook"] == "not configured"
    line = json.loads((tmp_path / "alerts.jsonl").read_text(encoding="utf-8").splitlines()[-1])
    assert line["kind"] == "missed_commitment" and line["details"]["source"] == "github watchdog"
