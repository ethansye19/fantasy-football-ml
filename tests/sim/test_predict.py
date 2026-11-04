"""Live prediction pipeline: modes and approval gate, holdout block, model source, replay."""

from __future__ import annotations

import json
import os
from dataclasses import replace
from datetime import UTC, datetime
from pathlib import Path

import polars as pl
import pytest
from regtoy import (
    SYNTHETIC_VERSION,
    add_candidate,
    add_live,
    add_with_status,
    tree,
    write_release_marker,
)

from ffml.config import get_settings
from ffml.models.protocol import validate_predictions
from ffml.season import SeasonState
from ffml.sim import predict as predict_mod
from ffml.sim.predict import (
    NO_APPROVED_MODEL_MESSAGE,
    LivePredictionBlocked,
    NoApprovedModel,
    PredictionRefused,
    resolve_model_dir,
    run_predict,
)
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


def _week5_in_progress_ctx():
    """Fixture context with 2023 week 5 in progress: KC final, DET still to play."""
    from ffml.features.data import FeatureContext

    ctx = FeatureContext.from_dir(FIXTURES)
    g = ctx.dim_game.with_columns(
        pl.when((pl.col("season") == 2023) & (pl.col("week") >= 5))
        .then(pl.lit("scheduled"))
        .otherwise(pl.col("status"))
        .alias("status")
    )
    g = g.with_columns(
        pl.when(pl.col("game_id") == "2023_05_KC_MIN")
        .then(pl.lit("final"))
        .otherwise(pl.col("status"))
        .alias("status")
    )
    return replace(ctx, dim_game=g, cache={})


def _no_state(now=None, store=None):
    raise AssertionError("the schedule was read before the approval gate")


# --------------------------------------------------------------------------- #
# Live mode: the approval gate
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("status", [None, "candidate", "rejected", "retired", "approved"])
def test_live_without_a_live_model_stops_before_reading_anything(registry_env, monkeypatch, status):
    import ffml.season

    if status is not None:
        add_with_status(registry_env, "chain_v1-x", registry_env.models_dir / "x", status)
    write_release_marker(registry_env)  # released: the holdout guard would log any access
    monkeypatch.setattr(ffml.season, "current_state", _no_state)
    before = tree(registry_env.artifacts_dir)
    for kwargs in ({"season": 2026, "week": 9}, {}):
        with pytest.raises(NoApprovedModel) as err:
            run_predict(**kwargs)
        assert str(err.value) == NO_APPROVED_MODEL_MESSAGE
        assert isinstance(err.value, LivePredictionBlocked)
    assert tree(registry_env.artifacts_dir) == before  # no access-log line, registry unchanged
    assert not (registry_env.artifacts_dir / "holdout_access.log").exists()
    assert tree(registry_env.data_dir) == {}  # neither the public nor the private store


def test_a_hand_edited_live_entry_without_a_report_is_not_approved(registry_env):
    from ffml.models import registry as reg

    add_candidate("chain_v1-x", registry_env.models_dir / "x")
    payload = reg.load_registry()
    payload["models"][0]["status"] = "live"  # bypasses approve and set_live
    reg.registry_path().write_text(json.dumps(payload), encoding="utf-8")
    assert reg.get_live()["version"] == "chain_v1-x"
    with pytest.raises(NoApprovedModel):
        run_predict(season=2026, week=9)
    assert tree(registry_env.data_dir) == {}


def test_cli_without_a_live_model_exits_2_with_the_message(registry_env, monkeypatch, cli):
    import ffml.season

    monkeypatch.setattr(ffml.season, "current_state", _no_state)
    for args in (["predict", "--season", "2026", "--week", "9"], ["predict"]):
        result = cli(args)
        assert result.exit_code == 2, result.output
        assert NO_APPROVED_MODEL_MESSAGE in " ".join(result.output.split())
    assert tree(registry_env.data_dir) == {}
    assert not (registry_env.artifacts_dir / "holdout_access.log").exists()


def test_cli_modes_and_refusals(registry_env, monkeypatch, cli):
    result = cli(["predict", "--season", "2026", "--week", "9", "--model-dir", "somewhere"])
    assert result.exit_code == 2 and "prediction refused" in result.output
    assert "model_dir" in result.output
    result = cli(["predict", "--private-test"])
    assert result.exit_code == 2 and "needs model_version" in result.output
    result = cli(["predict", "--private-test", "--model-version", "nope"])
    assert result.exit_code == 2 and "not registered" in result.output
    seen = {}
    monkeypatch.setattr(predict_mod, "run_predict", lambda **kw: seen.update(kw))
    args = ["predict", "--private-test", "--model-version", "v2", "--model-dir", "d"]
    assert cli([*args, "--season", "2026", "--week", "10", "--no-ros"]).exit_code == 0
    assert seen == {
        "season": 2026,
        "week": 10,
        "rest_of_season": False,
        "model_dir": "d",
        "private_test": True,
        "model_version": "v2",
    }


@pytest.mark.parametrize("mode", ["offseason", "postseason"])
def test_offseason_and_postseason_exit_without_writing(registry_env, monkeypatch, capsys, mode):
    import ffml.season

    add_live(registry_env, "chain_v1-live", registry_env.models_dir / "live")
    monkeypatch.setattr(ffml.season, "current_state", lambda now=None, store=None: _state(mode))
    out = run_predict(now=NOW)
    assert out is None
    assert mode in capsys.readouterr().out
    assert tree(registry_env.data_dir) == {}


def test_live_2026_is_blocked_by_the_holdout_with_instructions(registry_env):
    add_live(registry_env, "chain_v1-live", registry_env.models_dir / "live")
    with pytest.raises(LivePredictionBlocked) as err:
        run_predict(season=2026, week=9)
    assert not isinstance(err.value, NoApprovedModel)
    text = str(err.value)
    assert "D-058" in text and "release_holdout" in text and "replay=True" in text
    assert "FFML_HOLDOUT_UNLOCK" in text  # says not to use it
    assert tree(registry_env.data_dir) == {}
    assert not get_settings().holdout_unlock


def test_live_mode_refuses_overrides(registry_env, synthetic_model_dir):
    from ffml.models.chain import ChainModel

    model_dir, data = synthetic_model_dir
    add_live(registry_env, SYNTHETIC_VERSION, model_dir)
    base = {"season": 2023, "week": 5, "data": data}
    for override in (
        {"model_dir": model_dir},
        {"model": ChainModel.load(model_dir, data=data)},
        {"model_version": SYNTHETIC_VERSION},
        {"chain_root": model_dir.parent},
    ):
        with pytest.raises(PredictionRefused, match="live mode refuses"):
            run_predict(**base, **override)
    assert tree(registry_env.data_dir) == {}


def test_live_run_uses_only_the_live_version_and_is_labeled_approved(
    registry_env, synthetic_model_dir
):
    model_dir, data = synthetic_model_dir
    add_candidate("chain_v1-newer-candidate", registry_env.models_dir / "newer")
    add_live(registry_env, SYNTHETIC_VERSION, model_dir)
    res = run_predict(
        season=2023,
        week=5,
        data=data,
        ctx=_week5_in_progress_ctx(),
        n_sims=200,
        ros_sims=40,
        now=NOW,
    )
    public = registry_env.predictions_dir
    assert res is not None and res.mode == "live"
    assert (res.environment, res.approval_status) == ("live", "approved")
    assert res.path.is_relative_to(public)
    assert res.skipped_final_games == ["2023_05_KC_MIN"]
    snap = read_predictions(2023, 5, res.run_id, root=public)
    summary = validate_predictions(snap.summary)
    assert set(summary["scoring"]) == {"ppr", "half", "std"}
    assert "approval_status" not in summary.columns
    assert "2023_05_KC_MIN" not in set(summary["game_id"]) | set(snap.team_env["game_id"])
    assert "2023_05_CAR_DET" in set(summary["game_id"])
    assert summary.filter(pl.col("position") != "DST")["team"].unique().to_list() == ["DET"]
    m = snap.manifest
    assert m["approval_status"] == "approved" and m["environment"] == "live"
    assert "label" not in m and m["model_version"] == SYNTHETIC_VERSION
    extra = m["extra"]
    assert extra["model_source"]["kind"] == "registry_live"
    assert extra["model_source"]["version"] == SYNTHETIC_VERSION
    assert Path(extra["model_source"]["path"]) == model_dir
    assert extra["availability_config"] == "final_report" and extra["mode"] == "live"
    assert extra["availability"]["ina_applied"] is True
    assert extra["skipped_final_games"] == ["2023_05_KC_MIN"]
    # live runs move the public LATEST, labeled; the private store is untouched
    latest = json.loads((public / "LATEST").read_text(encoding="utf-8"))
    assert latest["run_id"] == res.run_id and latest["approval_status"] == "approved"
    assert latest["environment"] == "live"
    assert not registry_env.private_predictions_dir.exists()
    # the rest of the season starts the next week and KC (final this week) is filled in
    assert res.ros_weeks == list(range(6, 19))
    ros = pl.read_parquet(snap.path / "ros_sims.parquet")
    assert "KC" in set(ros["team"]) and ros["week"].min() == 6
    assert all(ros[c].dtype == pl.Int16 for c in ("pass_yds", "rec", "sacks"))
    rs = pl.read_parquet(snap.path / "ros_summary.parquet")
    assert "approval_status" not in rs.columns
    kc = rs.filter((pl.col("entity_id") == "KC") & (pl.col("scoring") == "ppr")).row(0, named=True)
    assert kc["games_remaining"] == 13 - len(kc["bye_weeks"]) and len(kc["bye_weeks"]) == 1


def test_live_refuses_an_artifact_built_as_another_version(registry_env, synthetic_model_dir):
    model_dir, data = synthetic_model_dir
    add_live(registry_env, "chain_v1-other", model_dir)  # path of a different build
    with pytest.raises(PredictionRefused, match="built as"):
        run_predict(season=2023, week=5, data=data, ctx=_week5_in_progress_ctx(), now=NOW)
    assert tree(registry_env.data_dir) == {}


# --------------------------------------------------------------------------- #
# Private test mode
# --------------------------------------------------------------------------- #


def test_private_test_of_a_candidate_writes_only_to_the_private_store(
    registry_env, synthetic_model_dir
):
    model_dir, data = synthetic_model_dir
    add_candidate(SYNTHETIC_VERSION, model_dir)
    public = registry_env.predictions_dir
    public.mkdir(parents=True)
    (public / "LATEST").write_text('{"run_id": "approved-run"}\n', encoding="utf-8")
    public_before = tree(public)
    res = run_predict(
        season=2023,
        week=5,
        private_test=True,
        model_version=SYNTHETIC_VERSION,
        data=data,
        ctx=_week5_in_progress_ctx(),
        n_sims=200,
        ros_sims=40,
        now=NOW,
    )
    private = registry_env.private_predictions_dir
    assert private == registry_env.data_dir / "predictions_private"
    assert tree(public) == public_before  # public LATEST and store untouched
    assert res.path.is_relative_to(private) and res.run_id.endswith("-private")
    assert (res.environment, res.approval_status, res.mode) == (
        "private_test",
        "not approved",
        "private_test",
    )
    snap = read_predictions(2023, 5, res.run_id, root=private)
    m = snap.manifest
    assert m["environment"] == "private_test" and m["approval_status"] == "not approved"
    assert m["label"] == "not approved" and m["model_version"] == SYNTHETIC_VERSION
    assert m["extra"]["model_source"]["kind"] == "registry_candidate"
    assert m["extra"]["model_source"]["registry_status"] == "candidate"
    summary = validate_predictions(snap.summary)
    assert summary["approval_status"].unique().to_list() == ["not approved"]
    rs = pl.read_parquet(snap.path / "ros_summary.parquet")
    assert rs.height and rs["approval_status"].unique().to_list() == ["not approved"]
    latest = json.loads((private / "LATEST").read_text(encoding="utf-8"))
    assert latest["run_id"] == res.run_id and latest["label"] == "not approved"
    assert latest["approval_status"] == "not approved"


@pytest.mark.parametrize("status", ["rejected", "retired", "approved", "live", "unknown"])
def test_private_test_refuses_versions_that_are_not_candidates(registry_env, status):
    if status != "unknown":
        add_with_status(registry_env, "chain_v1-x", registry_env.models_dir / "x", status)
    with pytest.raises(PredictionRefused) as err:
        run_predict(season=2023, week=5, private_test=True, model_version="chain_v1-x")
    text = str(err.value)
    assert ("not registered" if status == "unknown" else status) in text
    if status in ("approved", "live"):
        assert "live mode" in text
    assert tree(registry_env.data_dir) == {}


def test_private_test_argument_checks(registry_env, synthetic_model_dir):
    from ffml.models.chain import ChainModel

    model_dir, data = synthetic_model_dir
    add_candidate(SYNTHETIC_VERSION, model_dir)
    ok = {"season": 2023, "week": 5, "private_test": True, "model_version": SYNTHETIC_VERSION}
    with pytest.raises(PredictionRefused, match="needs model_version"):
        run_predict(season=2023, week=5, private_test=True)
    with pytest.raises(PredictionRefused, match="one mode"):
        run_predict(**ok, replay=True)
    with pytest.raises(PredictionRefused, match="private_test mode refuses model"):
        run_predict(**ok, model=ChainModel.load(model_dir, data=data))
    with pytest.raises(PredictionRefused, match="not the registry path"):
        run_predict(**ok, model_dir=model_dir.parent)
    with pytest.raises(PredictionRefused, match="private_predictions_dir"):
        run_predict(**ok, root=registry_env.predictions_dir)
    assert tree(registry_env.data_dir) == {}
    # the matching model_dir passes the gate (the week check runs next)
    with pytest.raises(ValueError, match="regular-season week"):
        run_predict(**{**ok, "week": 20}, model_dir=model_dir)


# --------------------------------------------------------------------------- #
# Replay mode and model resolution
# --------------------------------------------------------------------------- #


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


def _replay(**kw):
    return run_predict(season=2017, week=12, replay=True, rest_of_season=False, n_sims=100, **kw)


def test_replay_is_labeled_not_approved_and_never_moves_latest(registry_env, synthetic_model_dir):
    model_dir, data = synthetic_model_dir
    add_candidate(SYNTHETIC_VERSION, model_dir)  # a candidate is not approved
    res = _replay(model_dir=model_dir, data=data)
    assert (res.environment, res.approval_status, res.mode) == ("replay", "not approved", "replay")
    m = read_predictions(2017, 12, res.run_id, root=registry_env.predictions_dir).manifest
    assert m["environment"] == "replay" and m["approval_status"] == "not approved"
    assert "label" not in m and m["extra"]["mode"] == "replay"
    assert not (registry_env.predictions_dir / "LATEST").exists()
    assert not registry_env.private_predictions_dir.exists()
    with pytest.raises(PredictionRefused, match="never move LATEST"):
        _replay(model_dir=model_dir, data=data, update_latest=True)
    with pytest.raises(PredictionRefused, match="model_version"):
        _replay(model_dir=model_dir, data=data, model_version=SYNTHETIC_VERSION)


def test_replay_of_the_live_version_is_labeled_approved(registry_env, synthetic_model_dir):
    from ffml.models.chain import ChainModel

    model_dir, data = synthetic_model_dir
    add_live(registry_env, SYNTHETIC_VERSION, model_dir)
    assert _replay(model_dir=model_dir, data=data, run_id="dir").approval_status == "approved"
    assert _replay(data=data, run_id="registry").approval_status == "approved"
    obj = ChainModel.load(model_dir, data=data)
    assert _replay(model=obj, run_id="object").approval_status == "not approved"
    for run_id, status in (("dir", "approved"), ("object", "not approved")):
        m = read_predictions(2017, 12, run_id, root=registry_env.predictions_dir).manifest
        assert m["approval_status"] == status and m["environment"] == "replay"
    assert not (registry_env.predictions_dir / "LATEST").exists()


def test_infeasible_requests_are_refused(registry_env, synthetic_model_dir):
    model_dir, data = synthetic_model_dir
    add_live(registry_env, SYNTHETIC_VERSION, model_dir)
    with pytest.raises(ValueError, match="replay needs"):
        run_predict(replay=True)
    with pytest.raises(ValueError, match="regular-season week"):
        run_predict(season=2023, week=20)
    with pytest.raises(ValueError, match="cutoff"):
        run_predict(season=2016, week=3, replay=True, model_dir=model_dir, data=data)
    assert tree(registry_env.data_dir) == {}


def test_blocked_message_names_the_holdout_and_the_fix():
    text = predict_mod.blocked_message(2026)
    assert "2025" in text and "evaluator" in text.lower()


def _features_available() -> bool:
    root = get_settings().features_dir
    return all((root / d / "LATEST").exists() for d in ("player_week", "team_week", "dst_week"))


@pytest.mark.slow
@pytest.mark.skipif(not _features_available(), reason="data/features not built")
def test_replay_2024_week10_writes_a_valid_snapshot(tmp_path, monkeypatch):
    import ffml.models.registry as registry
    from ffml.models.chain import ChainModel

    monkeypatch.setattr(registry, "get_live", lambda registry=None: None)  # not the real one
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
    assert snap.manifest["environment"] == "replay"
    assert snap.manifest["approval_status"] == "not approved"
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
