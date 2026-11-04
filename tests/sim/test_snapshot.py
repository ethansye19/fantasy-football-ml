"""Prediction snapshot write and read round trip."""

from __future__ import annotations

import json

import polars as pl
import pytest
from simtoy import toy_dst_params, toy_player_params, toy_team_params

from ffml.scoring import ScoringRules
from ffml.sim import read_latest_predictions, simulate_full, summarize, write_predictions_snapshot
from ffml.sim.snapshot import LATEST_NAME, MANIFEST_NAME, read_predictions, run_dir

META = {
    "model_version": "baseline-chain-0",
    "seeds": [42],
    "n_sims": 200,
    "inputs": {"dim_game": "x"},
}


@pytest.fixture(scope="module")
def run():
    res = simulate_full(
        toy_team_params(), toy_player_params(), toy_dst_params(), n_sims=200, seed=42
    )
    summary = summarize(res.players, ScoringRules.from_preset("ppr"))
    team_env = res.teams.group_by(["team", "game_id"]).agg(pl.col("points").mean())
    return res.players, summary, team_env


def test_round_trip(tmp_path, run):
    sims, summary, team_env = run
    path = write_predictions_snapshot(
        2026, 9, "r1", sims, summary, team_env, {**META, "note": "t"}, root=tmp_path
    )
    assert path == tmp_path / "season=2026" / "week=09" / "run=r1"
    latest = json.loads((tmp_path / LATEST_NAME).read_text(encoding="utf-8"))
    assert latest["season"] == 2026 and latest["week"] == 9 and latest["run_id"] == "r1"
    assert latest["model_version"] == "baseline-chain-0"
    snap = read_latest_predictions(root=tmp_path)
    assert snap.player_sims.equals(sims)
    assert snap.summary.equals(summary)
    assert snap.team_env.equals(team_env)
    m = snap.manifest
    assert (m["model_version"], m["seeds"], m["n_sims"], m["inputs"]) == (
        "baseline-chain-0",
        [42],
        200,
        {"dim_game": "x"},
    )
    assert m["extra"] == {"note": "t"}
    assert m["files"]["player_sims.parquet"]["rows"] == sims.height
    assert (path / MANIFEST_NAME).exists()
    assert not any(p.name.startswith(".staging") for p in path.parent.iterdir())


def test_runs_are_immutable_and_latest_moves(tmp_path, run):
    sims, summary, _ = run
    write_predictions_snapshot(2026, 9, "r1", sims, summary, None, META, root=tmp_path)
    with pytest.raises(FileExistsError):
        write_predictions_snapshot(2026, 9, "r1", sims, summary, None, META, root=tmp_path)
    write_predictions_snapshot(2026, 10, "r2", sims, summary, None, META, root=tmp_path)
    assert read_latest_predictions(root=tmp_path, load_sims=False).run_id == "r2"
    write_predictions_snapshot(
        2026, 10, "r3", sims, summary, None, META, root=tmp_path, update_latest=False
    )
    snap = read_latest_predictions(root=tmp_path, load_sims=False)
    assert snap.run_id == "r2" and snap.player_sims is None and snap.team_env is None
    assert read_predictions(2026, 10, "r3", root=tmp_path).summary.equals(summary)


def test_bad_inputs(tmp_path, run):
    sims, summary, _ = run
    with pytest.raises(ValueError, match="missing"):
        write_predictions_snapshot(
            2026, 9, "r1", sims, summary, None, {"model_version": "v"}, root=tmp_path
        )
    with pytest.raises(ValueError, match="run_id"):
        write_predictions_snapshot(2026, 9, "../evil", sims, summary, None, META, root=tmp_path)
    with pytest.raises(FileNotFoundError):
        read_latest_predictions(root=tmp_path)


def test_default_root_is_the_predictions_dir(tmp_settings, run):
    sims, summary, _ = run
    path = write_predictions_snapshot(2026, 9, "r1", sims, summary, None, META)
    assert path == run_dir(2026, 9, "r1", tmp_settings.predictions_dir)
    assert read_latest_predictions().run_id == "r1"


def test_approval_labels_are_top_level_in_the_manifest_and_latest(tmp_path, run):
    sims, summary, _ = run
    labels = {"environment": "private_test", "approval_status": "not approved"}
    meta = {**META, **labels, "label": "not approved", "mode": "private_test"}
    write_predictions_snapshot(2026, 10, "p1", sims, summary, None, meta, root=tmp_path)
    m = read_predictions(2026, 10, "p1", root=tmp_path, load_sims=False).manifest
    assert m["environment"] == "private_test" and m["approval_status"] == "not approved"
    assert m["label"] == "not approved"
    assert m["extra"] == {"mode": "private_test"}  # labels are not repeated under extra
    latest = json.loads((tmp_path / LATEST_NAME).read_text(encoding="utf-8"))
    assert latest["environment"] == "private_test" and latest["label"] == "not approved"
    assert latest["approval_status"] == "not approved" and latest["run_id"] == "p1"
    # without labels in meta the manifest and LATEST keep their old shape
    write_predictions_snapshot(2026, 10, "p2", sims, summary, None, META, root=tmp_path)
    latest = json.loads((tmp_path / LATEST_NAME).read_text(encoding="utf-8"))
    assert not {"environment", "approval_status", "label"} & set(latest)
