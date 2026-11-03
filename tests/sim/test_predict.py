"""Live prediction pipeline: modes, holdout block, model source, week in progress, replay."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest

from ffml.config import get_settings
from ffml.models.protocol import validate_predictions
from ffml.season import SeasonState
from ffml.sim import predict as predict_mod
from ffml.sim.predict import LivePredictionBlocked, resolve_model_dir, run_predict
from ffml.sim.snapshot import read_predictions

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "features"
NOW = datetime(2023, 10, 5, 12, tzinfo=UTC)


def _state(mode: str) -> SeasonState:
    return SeasonState(
        season=2026,
        mode=mode,
        projection_week=None,
        week_in_progress=False,
        completed_weeks=tuple(range(1, 19)),
        last_completed_week=18,
        next_kickoff_utc=None,
        last_final_kickoff_utc=None,
        as_of_utc=NOW,
        games_total=285,
        games_final=285,
    )


@pytest.fixture(scope="module")
def synthetic_model_dir(tmp_path_factory):
    from ffml.models.chain import ChainModel
    from ffml.models.chain.data import ChainData
    from ffml.models.chain.synthetic import synthetic_frames

    data = ChainData(frames=synthetic_frames())
    model = ChainModel(stage_choice="structural", n_sims=200, seed=4, data=data).fit((2017, 10))
    path = tmp_path_factory.mktemp("chain") / "chain_v1-synthetic"
    model.save(path)
    (path / "train_manifest.json").write_text(
        json.dumps({"model_version": "chain_v1-synthetic-test"}), encoding="utf-8"
    )
    return path, data


@pytest.mark.parametrize("mode", ["offseason", "postseason"])
def test_offseason_and_postseason_exit_without_writing(tmp_path, monkeypatch, capsys, mode):
    import ffml.season

    monkeypatch.setattr(ffml.season, "current_state", lambda now=None, store=None: _state(mode))
    out = run_predict(now=NOW, root=tmp_path / "pred")
    assert out is None
    assert mode in capsys.readouterr().out
    assert not (tmp_path / "pred").exists()


def test_live_2026_is_blocked_by_the_holdout_with_instructions(tmp_settings, tmp_path):
    with pytest.raises(LivePredictionBlocked) as err:
        run_predict(season=2026, week=9, root=tmp_path / "pred")
    text = str(err.value)
    assert "D-058" in text and "release_holdout" in text and "replay=True" in text
    assert "FFML_HOLDOUT_UNLOCK" in text  # says not to use it
    assert not (tmp_path / "pred").exists()
    assert not get_settings().holdout_unlock


def test_model_source_resolution(tmp_path, monkeypatch):
    import ffml.models.registry as registry

    root = tmp_path / "chain"
    for name in ("chain_v1-a", "chain_v1-b"):
        (root / name).mkdir(parents=True)
        (root / name / "params.json").write_text("{}", encoding="utf-8")
    os.utime(root / "chain_v1-a" / "params.json", (1, 1))  # b is newest
    monkeypatch.setattr(registry, "get_live", lambda registry=None: None)
    path, src = resolve_model_dir(chain_root=root)
    assert path.name == "chain_v1-b" and src["kind"] == "newest_artifact"
    path, src = resolve_model_dir(model_dir=root / "chain_v1-a", chain_root=root)
    assert src == {"kind": "model_dir", "path": str(root / "chain_v1-a")}
    entry = {"version": "chain_v1-a", "path": str(root / "chain_v1-a")}
    monkeypatch.setattr(registry, "get_live", lambda registry=None: entry)
    path, src = resolve_model_dir(chain_root=root)
    assert src["kind"] == "registry_live" and src["version"] == "chain_v1-a"
    monkeypatch.setattr(registry, "get_live", lambda registry=None: None)
    with pytest.raises(FileNotFoundError, match="ffml train"):
        resolve_model_dir(chain_root=tmp_path / "empty")


def test_week_in_progress_predicts_only_games_not_final(tmp_path, synthetic_model_dir):
    from ffml.features.data import FeatureContext

    model_dir, data = synthetic_model_dir
    ctx = FeatureContext.from_dir(FIXTURES)
    g = ctx.dim_game.with_columns(
        pl.when((pl.col("season") == 2023) & (pl.col("week") >= 5))
        .then(pl.lit("scheduled"))
        .otherwise(pl.col("status"))
        .alias("status")
    )
    # week 5 in progress: the KC game is final, the DET game is still to play
    g = g.with_columns(
        pl.when(pl.col("game_id") == "2023_05_KC_MIN")
        .then(pl.lit("final"))
        .otherwise(pl.col("status"))
        .alias("status")
    )
    ctx = replace(ctx, dim_game=g, cache={})
    res = run_predict(
        season=2023,
        week=5,
        model_dir=model_dir,
        data=data,
        ctx=ctx,
        root=tmp_path / "pred",
        n_sims=200,
        ros_sims=40,
        now=NOW,
    )
    assert res is not None and res.mode == "live"
    assert res.skipped_final_games == ["2023_05_KC_MIN"]
    snap = read_predictions(2023, 5, res.run_id, root=tmp_path / "pred")
    summary = validate_predictions(snap.summary)
    assert set(summary["scoring"]) == {"ppr", "half", "std"}
    assert "2023_05_KC_MIN" not in set(summary["game_id"]) | set(snap.team_env["game_id"])
    assert "2023_05_CAR_DET" in set(summary["game_id"])
    assert summary.filter(pl.col("position") != "DST")["team"].unique().to_list() == ["DET"]
    extra = snap.manifest["extra"]
    assert extra["model_source"]["kind"] == "model_dir"
    assert extra["model_source"]["path"] == str(model_dir)
    assert snap.manifest["model_version"] == "chain_v1-synthetic-test"
    assert extra["availability_config"] == "final_report" and extra["mode"] == "live"
    assert extra["availability"]["ina_applied"] is True
    assert extra["skipped_final_games"] == ["2023_05_KC_MIN"]
    # live runs move LATEST
    latest = json.loads((tmp_path / "pred" / "LATEST").read_text(encoding="utf-8"))
    assert latest["run_id"] == res.run_id
    # the rest of the season starts the next week and KC (final this week) is filled in
    assert res.ros_weeks == list(range(6, 19))
    ros = pl.read_parquet(snap.path / "ros_sims.parquet")
    assert "KC" in set(ros["team"]) and ros["week"].min() == 6
    assert all(ros[c].dtype == pl.Int16 for c in ("pass_yds", "rec", "sacks"))
    rs = pl.read_parquet(snap.path / "ros_summary.parquet")
    kc = rs.filter((pl.col("entity_id") == "KC") & (pl.col("scoring") == "ppr")).row(0, named=True)
    assert kc["games_remaining"] == 13 - len(kc["bye_weeks"]) and len(kc["bye_weeks"]) == 1


def test_infeasible_requests_are_refused(tmp_path, synthetic_model_dir):
    model_dir, data = synthetic_model_dir
    with pytest.raises(ValueError, match="replay needs"):
        run_predict(replay=True, root=tmp_path)
    with pytest.raises(ValueError, match="regular-season week"):
        run_predict(season=2023, week=20, root=tmp_path)
    with pytest.raises(ValueError, match="cutoff"):
        run_predict(season=2016, week=3, replay=True, model_dir=model_dir, data=data, root=tmp_path)


def test_blocked_message_names_the_holdout_and_the_fix():
    text = predict_mod.blocked_message(2026)
    assert "2025" in text and "evaluator" in text.lower()


def _features_available() -> bool:
    root = get_settings().features_dir
    return all((root / d / "LATEST").exists() for d in ("player_week", "team_week", "dst_week"))


@pytest.mark.slow
@pytest.mark.skipif(not _features_available(), reason="data/features not built")
def test_replay_2024_week10_writes_a_valid_snapshot(tmp_path):
    from ffml.models.chain import ChainModel

    model = ChainModel(stage_choice="structural", n_sims=300).fit((2024, 10))
    model.save(tmp_path / "model")
    res = run_predict(
        season=2024,
        week=10,
        replay=True,
        model_dir=tmp_path / "model",
        root=tmp_path / "pred",
        n_sims=300,
        ros_sims=100,
    )
    print(res.timings)
    snap = read_predictions(2024, 10, res.run_id, root=tmp_path / "pred")
    summary = validate_predictions(snap.summary)
    assert set(summary["scoring"]) == {"ppr", "half", "std"}
    assert summary["drivers"].null_count() == 0
    assert snap.player_sims["sim"].max() == 299
    assert not (tmp_path / "pred" / "LATEST").exists()  # replays never move LATEST
    extra = snap.manifest["extra"]
    assert extra["mode"] == "replay" and extra["model_source"]["kind"] == "model_dir"
    assert extra["model_source"]["cutoff"] == [2024, 10]
    assert extra["availability"]["ina_applied"] is False
    assert res.ros_weeks == list(range(11, 19))
    ros = pl.read_parquet(snap.path / "ros_sims.parquet")
    assert sorted(ros["week"].unique().to_list()) == list(range(11, 19))
    rs = pl.read_parquet(snap.path / "ros_summary.parquet").filter(pl.col("scoring") == "ppr")
    # 2024 byes after week 10 (weeks 11, 12 and 14): those teams play 7 of 8 weeks
    byes = rs.filter(pl.col("position") == "DST").select("team", "bye_weeks", "games_remaining")
    with_bye = byes.filter(pl.col("bye_weeks").list.len() == 1)
    assert with_bye.height >= 8 and (with_bye["games_remaining"] == 7).all()
    for row in with_bye.head(3).iter_rows(named=True):
        week = row["bye_weeks"][0]
        assert ros.filter((pl.col("week") == week) & (pl.col("team") == row["team"])).height == 0
    # teams on bye in week 10 are carried into the rest of the season
    week10 = set(summary["team"])
    assert set(rs["team"]) - week10
    assert rs.filter(pl.col("games_remaining") > 0)["ros_mean"].min() >= 0
    assert (rs["ros_q10"] <= rs["ros_q90"]).all()
