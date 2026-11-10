"""D-124: the current season and week come from Sleeper, cross-checked with the schedule.

Recorded fixtures, no network:

* ``fixtures/sleeper_state_2026w09.json``: ``GET https://api.sleeper.app/v1/state/nfl``
  recorded 2026-11-10 02:35 UTC (2026 regular season, week 9);
* ``tests/fixtures/sleeper/state.json``: the same endpoint recorded 2026-11-02 (week 8);
* ``fixtures/schedules_2026_w09.parquet``: the 2026 rows of the raw ``schedules``
  snapshot 20261110T023724Z (weeks 1-8 final, week 9 with 14 of 15 games final).
"""

from __future__ import annotations

import json
import logging
import threading
from datetime import UTC, datetime
from pathlib import Path

import httpx
import polars as pl
import pytest
import respx

from api.main import report_test_sources
from api.services.season import SeasonService
from api.settings import ApiSettings
from ffml.data.sleeper import SleeperState
from ffml.season import resolve_state
from tests.api.api_factory import PRIVATE_LABELS, write_snapshot
from tests.api.sleeper_mock import BASE, no_sleep_factory

FIXTURES = Path(__file__).resolve().parent / "fixtures"
SCHEDULES = FIXTURES / "schedules_2026_w09.parquet"
SLEEPER_W09 = FIXTURES / "sleeper_state_2026w09.json"
SLEEPER_W08 = Path(__file__).resolve().parents[1] / "fixtures" / "sleeper" / "state.json"
NOW = datetime(2026, 11, 10, 2, 40, tzinfo=UTC)  # Monday night of week 9
SNAPSHOT = "20261110T023724Z"
REPLAY_LABEL = "TEST DATA: 2024 week 11 replay, not approved, not a recommendation"


def state_json(path: Path, **changes) -> dict:
    return {**json.loads(path.read_text(encoding="utf-8")), **changes}


def sleeper(path: Path = SLEEPER_W09, **changes) -> SleeperState:
    return SleeperState.from_api(state_json(path, **changes))


def schedules() -> pl.DataFrame:
    return pl.read_parquet(SCHEDULES)


def service(sleeper_state=None, frame: pl.DataFrame | None = None, **kwargs) -> SeasonService:
    frame = schedules() if frame is None else frame
    return SeasonService(
        Path("."),
        resolver=lambda now: resolve_state(frame, now),
        sleeper_state=sleeper_state,
        snapshot_id=lambda: SNAPSHOT,
        **kwargs,
    )


def no_schedule(_now):
    raise FileNotFoundError("no schedules snapshot")


# --------------------------------------------------------------------------- rules


def test_agreement_uses_sleeper_and_reports_the_cross_check():
    body = service(lambda: sleeper()).describe(NOW)
    assert (body["season"], body["week"], body["season_type"], body["mode"]) == (
        2026, 9, "regular", "regular"
    )
    assert body["source"] == "sleeper"
    assert body["projection_week"] == 9 and body["week_in_progress"] is True
    assert body["last_completed_week"] == 8 and body["offseason"] is False
    assert body["banner"] is None and body["as_of"] == NOW.isoformat()
    cross = body["cross_check"]
    assert cross["status"] == "agree" and cross["differences"] == []
    assert cross["sleeper"]["week"] == 9 and cross["sleeper"]["raw_season_type"] == "regular"
    assert cross["schedule"]["week"] == 9 and cross["schedule"]["snapshot_id"] == SNAPSHOT
    assert (cross["schedule"]["games_final"], cross["schedule"]["games_total"]) == (135, 272)


def test_disagreement_shows_the_schedule_and_is_logged_once(caplog):
    svc = service(lambda: sleeper(SLEEPER_W08))  # Sleeper says week 8, the schedule week 9
    with caplog.at_level(logging.WARNING, logger="api.services.season"):
        first = svc.describe(NOW)
        svc.describe(NOW)
    assert first["source"] == "schedule"
    assert (first["season"], first["week"], first["mode"]) == (2026, 9, "regular")
    cross = first["cross_check"]
    assert cross["status"] == "disagree" and cross["differences"] == ["week"]
    assert "Sleeper 8, schedule 9" in cross["message"]
    logged = [r for r in caplog.records if "disagree" in r.getMessage()]
    assert len(logged) == 1, [r.getMessage() for r in caplog.records]
    assert "week" in logged[0].getMessage() and SNAPSHOT in logged[0].getMessage()


def test_season_mismatch_is_a_disagreement():
    """February: Sleeper may already say the next season; the schedule's answer wins."""
    final = schedules().with_columns(pl.col("result").fill_null(3))
    svc = service(lambda: sleeper(season="2027", season_type="off"), frame=final)
    body = svc.describe(datetime(2027, 2, 20, tzinfo=UTC))
    assert body["source"] == "schedule" and body["season"] == 2026
    assert body["mode"] == "offseason" and body["season_type"] == "offseason"
    assert body["week"] is None and body["offseason"] is True and "Offseason" in body["banner"]
    assert body["cross_check"]["differences"] == ["season"]


def test_preseason_agrees_with_week_one_before_any_game():
    frame = schedules().with_columns(pl.lit(None, dtype=pl.Int32).alias("result"))
    svc = service(lambda: sleeper(season_type="pre", week="1", display_week="1"), frame=frame)
    body = svc.describe(datetime(2026, 8, 20, tzinfo=UTC))
    assert body["cross_check"]["status"] == "agree"
    assert (body["source"], body["season_type"], body["mode"], body["week"]) == (
        "sleeper", "preseason", "regular", 1
    )


def test_sleeper_unreachable_uses_the_schedule_and_says_so(caplog):
    def down():
        raise httpx.ConnectError("connection refused")

    with caplog.at_level(logging.WARNING, logger="api.services.season"):
        body = service(down).describe(NOW)
    assert body["source"] == "schedule" and (body["season"], body["week"]) == (2026, 9)
    cross = body["cross_check"]
    assert cross["status"] == "sleeper_unavailable" and cross["sleeper"] is None
    assert "ConnectError" in cross["sleeper_error"]
    assert "Sleeper could not be reached" in cross["message"]
    assert any("Sleeper state unavailable" in r.getMessage() for r in caplog.records)


def test_a_hung_sleeper_call_is_cut_off_by_the_deadline():
    release = threading.Event()
    calls = []

    def hang():
        calls.append(1)
        release.wait(5)
        return sleeper()

    svc = service(hang, sleeper_timeout_seconds=0.2)
    try:
        body = svc.describe(NOW)
        assert body["source"] == "schedule"
        assert "within 0.2 s" in body["cross_check"]["sleeper_error"]
        again = svc.describe(NOW)  # the first call is still pending: no second request
        assert "still pending" in again["cross_check"]["sleeper_error"]
        assert len(calls) == 1
    finally:
        release.set()


def test_no_schedule_uses_sleeper_without_cross_check():
    svc = SeasonService(Path("."), resolver=no_schedule, sleeper_state=lambda: sleeper())
    body = svc.describe(NOW)
    assert (body["source"], body["season"], body["week"], body["mode"]) == (
        "sleeper", 2026, 9, "regular"
    )
    assert body["projection_week"] == 9 and body["week_in_progress"] is False
    assert body["cross_check"]["status"] == "schedule_unavailable"
    assert body["cross_check"]["schedule"] is None


def test_neither_source_is_unknown():
    def down():
        raise httpx.ReadTimeout("timeout")

    body = SeasonService(Path("."), resolver=no_schedule, sleeper_state=down).describe(NOW)
    assert (body["mode"], body["season"], body["week"], body["source"]) == (
        "unknown", None, None, "none"
    )
    assert body["offseason"] is None and body["banner"]
    assert body["cross_check"]["status"] == "unavailable"


def test_sleeper_disabled_is_said_plainly():
    body = service(None).describe(NOW)
    assert body["source"] == "schedule"
    assert body["cross_check"]["status"] == "sleeper_unavailable"
    assert "not used" in body["cross_check"]["message"]


def test_answers_are_cached_briefly():
    calls = []

    def counted():
        calls.append(1)
        return sleeper()

    svc = service(counted, ttl_seconds=180)
    first = svc.describe()
    assert svc.describe() is first and svc.describe() is first
    assert len(calls) == 1
    svc.clear()
    svc.describe()
    assert len(calls) == 2


def test_a_degraded_answer_is_kept_for_a_shorter_time(monkeypatch):
    clock = [1000.0]
    monkeypatch.setattr("api.services.season.time.monotonic", lambda: clock[0])

    def down():
        raise httpx.ConnectError("refused")

    calls = []

    def flaky():
        calls.append(1)
        return down() if len(calls) == 1 else sleeper()

    svc = service(flaky, ttl_seconds=180, degraded_ttl_seconds=60)
    assert svc.describe()["cross_check"]["status"] == "sleeper_unavailable"
    clock[0] += 30
    assert svc.describe()["cross_check"]["status"] == "sleeper_unavailable"  # cached
    clock[0] += 31
    assert svc.describe()["cross_check"]["status"] == "agree"  # retried after 60 s
    clock[0] += 170
    svc.describe()
    assert len(calls) == 2  # a full answer is kept for 180 s


# --------------------------------------------------------------------------- the app


def _schedule_snapshot(api_dirs) -> str:
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(api_dirs["data"] / "raw")
    return store.write_snapshot("schedules", {2026: schedules()})


def test_meta_season_reads_sleeper_through_the_shared_client(make_client, api_dirs):
    snap = _schedule_snapshot(api_dirs)
    client = make_client(season_sleeper_enabled=True)
    client.app.state.sleeper_factory = no_sleep_factory
    with respx.mock(assert_all_called=True) as router:
        route = router.get(f"{BASE}/state/nfl").respond(json=state_json(SLEEPER_W09))
        body = client.get("/meta/season").json()
        client.get("/meta/season")  # cached: one Sleeper request
    assert route.call_count == 1
    assert (body["season"], body["week"], body["season_type"]) == (2026, 9, "regular")
    assert body["source"] == "sleeper" and body["cross_check"]["status"] == "agree"
    assert body["cross_check"]["schedule"]["snapshot_id"] == snap
    # the season client is the app's one Sleeper client (D-025 rate limiter, QA-F04)
    assert client.app.state.sleeper.get(client.app.state.sleeper_factory).request_count == 1


def test_meta_season_falls_back_when_sleeper_fails(make_client, api_dirs):
    _schedule_snapshot(api_dirs)
    client = make_client(season_sleeper_enabled=True)
    client.app.state.sleeper_factory = no_sleep_factory
    with respx.mock(assert_all_called=True) as router:
        router.get(f"{BASE}/state/nfl").respond(503)
        body = client.get("/meta/season").json()
    assert body["source"] == "schedule" and (body["season"], body["week"]) == (2026, 9)
    assert body["cross_check"]["status"] == "sleeper_unavailable"
    assert "SleeperServerError" in body["cross_check"]["sleeper_error"]


def test_tests_never_call_sleeper_unless_they_opt_in(make_client, api_dirs):
    _schedule_snapshot(api_dirs)
    with respx.mock(assert_all_mocked=True, assert_all_called=False):
        body = make_client().get("/meta/season").json()  # any real request would raise
    assert body["source"] == "schedule" and body["season"] == 2026


def test_production_season_ignores_replays_and_test_settings(make_client, api_dirs):
    """A 2024 replay anywhere, and the replay settings, never change the season (D-124)."""
    _schedule_snapshot(api_dirs)
    write_snapshot(api_dirs["test_replays"], week=11, mode="replay", update_latest=False)
    write_snapshot(api_dirs["predictions"], week=10, mode="replay", update_latest=False)
    client = make_client(api_replay_run="2024/11/2024w11-test-replay",
                         api_fixture_predictions_dir=api_dirs["fixtures"])
    season = client.get("/meta/season").json()
    assert (season["season"], season["week"]) == (2026, 9)
    info = client.get("/meta/run-info").json()
    assert info["run"] is None and info["test_data_label"] is None
    for path in ("/meta/season", "/meta/run-info", "/meta/approval", "/meta/model-card"):
        assert "2024" not in client.get(path).text, path


def test_startup_reports_ignored_test_settings_and_public_replays(api_dirs):
    write_snapshot(api_dirs["predictions"], week=10, run_id="left-replay", mode="replay",
                   update_latest=False)
    notes = report_test_sources(ApiSettings(api_replay_run="2024/11/x-replay"))
    assert any("FFML_API_REPLAY_RUN is set but ignored in production" in n for n in notes)
    assert any("left-replay" in n and "never served" in n for n in notes)
    private = report_test_sources(ApiSettings(app_env="private_test", api_replay_run="2024/9/x"))
    assert not any("ignored in production" in n for n in private)
    assert any("left-replay" in n for n in private)


# --------------------------------------------------------------------------- test-data label


def test_private_test_replay_is_labeled_test_data(make_client, api_dirs):
    write_snapshot(api_dirs["test_replays"], week=11, mode="replay", update_latest=False)
    client = make_client(app_env="private_test")
    approval = client.get("/meta/approval").json()
    assert approval["run"]["source"] == "replay"
    assert approval["test_data_label"] == REPLAY_LABEL
    assert approval["run"]["test_data_label"] == REPLAY_LABEL
    assert approval["label"] == "not approved"
    assert client.get("/meta/run-info").json()["test_data_label"] == REPLAY_LABEL
    body = client.get("/projections", params={"scoring": "ppr"}).json()
    assert body["notices"][0] == REPLAY_LABEL
    assert body["label"] == "not approved" and body["run"]["test_data_label"] == REPLAY_LABEL
    ros = client.get("/ros", params={"scoring": "ppr"}).json()
    assert ros["notices"][0] == REPLAY_LABEL


def test_fixture_runs_are_labeled_but_private_runs_are_not(make_client, api_dirs):
    write_snapshot(api_dirs["fixtures"], run_id="fixture-run")
    client = make_client(app_env="private_test", api_fixture_predictions_dir=api_dirs["fixtures"])
    assert client.get("/meta/approval").json()["test_data_label"] == (
        "TEST DATA: fixture data, not approved, not a recommendation"
    )
    write_snapshot(api_dirs["private"], season=2026, week=10, labels=PRIVATE_LABELS)
    approval = client.get("/meta/approval").json()
    assert approval["run"]["source"] == "private_test"
    assert approval["test_data_label"] is None and approval["label"] == "not approved"


@pytest.mark.parametrize("app_env", ["production", "private_test"])
def test_season_body_shape(make_client, api_dirs, app_env):
    _schedule_snapshot(api_dirs)
    body = make_client(app_env=app_env).get("/meta/season").json()
    assert set(body) >= {
        "season", "week", "season_type", "mode", "projection_week", "week_in_progress",
        "last_completed_week", "next_kickoff_utc", "offseason", "banner", "source", "as_of",
        "cross_check",
    }
    assert set(body["cross_check"]) >= {"status", "differences", "message", "sleeper", "schedule"}
