"""Tests for ``ffml refresh`` / ``ffml.data.refresh`` (DE-5.4)."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import subprocess
import sys
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from ffml.data import ingest as ingest_mod
from ffml.data import refresh
from ffml.data.ingest import SCHEMA_PIN_PATH, load_pins
from ffml.data.nflverse import DatasetSpec

HELP_STEP_NAMES = ("all", "data", "injuries", "retrain-check", "predict")  # ffml refresh --help


@pytest.fixture
def cli():
    """Run ``ffml`` through Typer's test runner; the root logging setup is restored afterwards."""
    from typer.testing import CliRunner

    from ffml.cli import app

    root = logging.getLogger()
    handlers, level = root.handlers[:], root.level
    runner = CliRunner()
    env = {"NO_COLOR": "1", "TERM": "dumb", "COLUMNS": "200"}  # plain help output on any terminal
    yield lambda args: runner.invoke(app, args, env=env)
    root.handlers[:] = handlers
    root.setLevel(level)


ANSI = re.compile(r"\x1b\[[0-9;?]*[A-Za-z]")


def plain(text: str) -> str:
    return ANSI.sub("", text)


@pytest.fixture
def fake_registry(monkeypatch) -> dict[str, DatasetSpec]:
    """One offline dataset standing in for the nflverse registry."""

    def loader(season=None, **kwargs):
        return pl.DataFrame(
            {
                "player_id": ["00-001", "00-002"],
                "season": [season, season],
                "week": [1.0, 2.0],
                "yards": [10.0, 5.0],
            }
        )

    spec = DatasetSpec(
        name="fake_weekly",
        loader=loader,
        min_season=2024,
        key_columns=["player_id", "season", "week"],
        casts={"week": pl.Int32},
        drop_null_keys=["player_id"],
    )
    reg = {"fake_weekly": spec}
    monkeypatch.setattr(ingest_mod, "DATASETS", reg)
    return reg


def test_cli_help_resolves(cli):
    result = cli(["refresh", "--help"])
    assert result.exit_code == 0, result.output
    assert "--step" in plain(result.output)


def test_cli_refused_and_unknown_steps_exit_1_with_a_message(cli):
    nonsense = cli(["refresh", "--step", "nonsense"])
    assert nonsense.exit_code == 1 and "unknown step" in plain(nonsense.output)
    predict = cli(["refresh", "--step", "predict"])
    assert predict.exit_code == 1 and "refused" in plain(predict.output)
    assert "Traceback" not in predict.output


def test_cli_ingest_update_schema_pin_option(cli, monkeypatch):
    calls: list[dict] = []
    monkeypatch.setattr(ingest_mod, "run_ingest", lambda **kw: calls.append(kw) or {})
    assert cli(["ingest", "--seasons", "2026", "--datasets", "teams"]).exit_code == 0
    assert cli(["ingest", "--seasons", "2026", "--datasets", "teams", "--update-schema-pin"]).exit_code == 0
    assert [c["update_schema_pin"] for c in calls] == [None, True]
    assert calls[0]["seasons"] == [2026] and calls[0]["datasets"] == ["teams"]


def test_every_step_name_resolves_or_is_refused_clearly():
    for name in refresh.STEPS:
        assert refresh.resolve_steps(name) == [refresh.Step(name)]
    assert [s.name for s in refresh.resolve_steps("all")] == list(refresh.STEPS)
    assert refresh.resolve_steps("data") == refresh.resolve_steps("ALL")
    injuries = refresh.resolve_steps("injuries")
    assert [s.name for s in injuries] == ["ingest", "process"]
    assert injuries[0].datasets == refresh.INJURY_DATASETS
    for name in ("retrain-check", "predict"):
        with pytest.raises(refresh.RefreshError, match="refused"):
            refresh.resolve_steps(name)
    with pytest.raises(refresh.RefreshError, match="unknown step"):
        refresh.resolve_steps("nope")
    for name in HELP_STEP_NAMES:  # the names the CLI help advertises are all handled
        try:
            refresh.resolve_steps(name)
        except refresh.RefreshError as exc:
            assert "refused" in str(exc)


def test_single_step_runs_in_process_with_the_working_pin_copy(tmp_settings, fake_registry):
    tracked_before = hashlib.sha256(SCHEMA_PIN_PATH.read_bytes()).hexdigest()
    (results,) = refresh.run_refresh("ingest", seasons=[2024, 2025])
    assert results.ok and results.step == "ingest", results.error
    assert results.snapshots["fake_weekly"]
    working = tmp_settings.schema_pin_path
    assert Path(results.info["pin_path"]) == working
    assert working == tmp_settings.data_dir / "schema_pins" / "expected_schemas.json"
    pins = load_pins(working)
    assert "fake_weekly" in pins and "schedules" in pins  # new pin plus the seeded tracked ones
    assert hashlib.sha256(SCHEMA_PIN_PATH.read_bytes()).hexdigest() == tracked_before
    assert results.info["ffml"] == str(Path(ingest_mod.__file__).resolve().parents[1])


def test_refresh_ingest_succeeds_when_the_newest_season_is_not_published(tmp_settings, fake_registry):
    """D-122: the runner's refresh for a 2027 week before nflverse publishes the 2027 file."""
    loader = fake_registry["fake_weekly"].loader

    def loader_2027(season=None, **kwargs):
        if season == 2027:
            raise ConnectionError("Failed to download https://x/2027.parquet: 404 Client Error")
        return loader(season=season, **kwargs)

    import dataclasses

    fake_registry["fake_weekly"] = dataclasses.replace(fake_registry["fake_weekly"], loader=loader_2027)
    fake_registry["schedules"] = _schedule_spec(final={2025, 2026})  # no 2027 game final yet (D-126)
    pins = tmp_settings.data_dir / "pins.json"  # fresh pins: the fake schedule is not the real one
    (result,) = refresh.run_refresh("ingest", seasons=[2025, 2026, 2027], pin_path=pins)
    assert result.ok, result.error
    assert result.info["not_yet_published"] == {"fake_weekly": [2027]}


def _schedule_spec(final: set[int]) -> DatasetSpec:
    def loader(season=None, **kwargs):
        return pl.DataFrame({
            "game_id": [f"{season}_01_A_B"], "season": [season], "week": [1], "game_type": ["REG"],
            "result": [3 if season in final else None],
        }, schema_overrides={"result": pl.Int64})

    return DatasetSpec(name="schedules", loader=loader, min_season=2016, key_columns=["game_id"], casts={},
                       drop_null_keys=["game_id"])


def test_refresh_stops_before_process_when_a_final_season_file_is_missing(tmp_settings, fake_registry):
    """D-126: 2027 has a final game but no stats file: the ingest step fails as a gap and process never runs."""
    import contextlib
    import dataclasses
    import io

    loader = fake_registry["fake_weekly"].loader

    def loader_2027(season=None, **kwargs):
        if season == 2027:
            raise ConnectionError("Failed to download https://x/2027.parquet: 404 Client Error")
        return loader(season=season, **kwargs)

    fake_registry["fake_weekly"] = dataclasses.replace(fake_registry["fake_weekly"], loader=loader_2027)
    fake_registry["schedules"] = _schedule_spec(final={2025, 2026, 2027})
    steps: list[str] = []

    def in_process(argv, **kwargs):  # runs each step here, against the fake registry
        steps.append(argv[argv.index("--step") + 1])
        out = io.StringIO()
        with contextlib.redirect_stdout(out):
            code = refresh.main(argv[3:])
        return subprocess.CompletedProcess(argv, code, out.getvalue(), "")

    with pytest.raises(refresh.RefreshError, match="gap") as excinfo:
        refresh.run_refresh("all", seasons=[2025, 2026, 2027], runner=in_process,
                            pin_path=tmp_settings.data_dir / "pins.json")
    assert steps == ["ingest"]  # stopped before process: no processed snapshot is written
    assert "a regular-season game of that season is final (D-126)" in str(excinfo.value)
    assert "schema drift" not in str(excinfo.value)
    assert [r.step for r in excinfo.value.results] == ["ingest"]
    assert not (tmp_settings.processed_dir / "dim_game").exists()


def test_all_runs_each_step_in_its_own_process_and_stops_at_a_failure(tmp_settings):
    calls: list[list[str]] = []

    def fake_run(argv, **kwargs):
        calls.append(list(argv))
        step = argv[argv.index("--step") + 1]
        ok = step != "process"
        line = {"step": step, "ok": ok, "seconds": 0.1, "snapshots": {}, "error": None if ok else "boom"}
        return subprocess.CompletedProcess(argv, 0 if ok else 1, "noise\n" + refresh.RESULT_PREFIX + json.dumps(line) + "\n", "")

    with pytest.raises(refresh.RefreshError, match="process: boom") as info:
        refresh.run_refresh("all", seasons=[2026], runner=fake_run)
    assert [c[c.index("--step") + 1] for c in calls] == ["ingest", "sleeper", "process"]
    assert all(c[0] == sys.executable and c[1:3] == ["-m", "ffml.data.refresh"] for c in calls)
    assert [r.ok for r in info.value.results] == [True, True, False]

    calls.clear()

    def good_run(argv, **kwargs):
        calls.append(list(argv))
        step = argv[argv.index("--step") + 1]
        line = {"step": step, "ok": True, "seconds": 0.1, "snapshots": {step: "s1"}, "error": None}
        return subprocess.CompletedProcess(argv, 0, refresh.RESULT_PREFIX + json.dumps(line), "")

    results = refresh.run_refresh("injuries", seasons=[2026], report=tmp_settings.data_dir / "r.md", runner=good_run)
    assert [r.step for r in results] == ["ingest", "process"]
    assert calls[0][calls[0].index("--datasets") + 1] == ",".join(refresh.INJURY_DATASETS)
    assert "--datasets" not in calls[1] and "--report" in calls[1]

    def silent_run(argv, **kwargs):
        return subprocess.CompletedProcess(argv, 3, "no result line here", "")

    with pytest.raises(refresh.RefreshError, match="exited 3 without"):
        refresh.run_refresh("all", seasons=[2026], runner=silent_run)


def test_default_seasons_follow_the_calendar_without_a_schedule_snapshot(tmp_settings, monkeypatch):
    monkeypatch.setattr(refresh, "_now", lambda: datetime(2027, 3, 1, tzinfo=UTC))
    assert refresh.default_seasons() == list(range(2016, 2027))  # 2027 is not published yet
    monkeypatch.setattr(refresh, "_now", lambda: datetime(2027, 9, 15, tzinfo=UTC))
    assert refresh.default_seasons() == list(range(2016, 2028))
    assert refresh.parse_seasons("2024,2026-2027") == [2024, 2026, 2027]


def test_module_main_prints_a_result_line(tmp_path):
    env = {
        **os.environ,
        "FFML_DATA_DIR": str(tmp_path / "data"),
        "FFML_ARTIFACTS_DIR": str(tmp_path / "artifacts"),
        "PYTHONUTF8": "1",
    }
    env.pop("FFML_HOLDOUT_UNLOCK", None)
    cmd = [sys.executable, "-m", "ffml.data.refresh", "--step", "validate", "--seasons", "2026",
           "--report", str(tmp_path / "validation.md")]
    proc = subprocess.run(cmd, env=env, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=600)
    assert proc.stdout.strip().splitlines()[-1].startswith(refresh.RESULT_PREFIX), proc.stderr[-800:]
    result = refresh.parse_result_line(proc.stdout)
    assert result is not None and result.step == "validate"
    assert (proc.returncode == 0) == result.ok
    assert Path(result.info["pin_path"]) == tmp_path / "data" / "schema_pins" / "expected_schemas.json"
    assert Path(result.info["pin_path"]).exists()  # seeded; the tracked file is not touched
