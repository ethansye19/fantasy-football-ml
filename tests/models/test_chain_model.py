"""ChainModel on a small synthetic league: protocol, determinism, persistence, configurations."""

from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest

from ffml.models.chain import ChainModel, resolve_choices
from ffml.models.chain.data import ChainData
from ffml.models.chain.selection import bootstrap_diff, row_loss, summarise
from ffml.models.chain.stages import STAGES, stage
from ffml.models.chain.summary import QCOLS
from ffml.models.protocol import (
    OPTIONAL_COLUMNS,
    PREDICTION_COLUMNS,
    WeeklyModel,
    validate_predictions,
)
from ffml.scoring import ScoringRules

SCORINGS = {"ppr": ScoringRules.from_preset("ppr"), "std": ScoringRules.from_preset("std")}
CUTOFF = (2017, 10)


@pytest.fixture(scope="module")
def frames():
    from ffml.models.chain.synthetic import synthetic_frames

    return synthetic_frames()


@pytest.fixture(scope="module")
def structural(frames):
    return ChainModel(
        stage_choice="structural", n_sims=300, seed=5, data=ChainData(frames=frames)
    ).fit(CUTOFF)


@pytest.fixture(scope="module")
def structural_pred(structural):
    return structural.predict(*CUTOFF, SCORINGS)


def test_protocol_frame(structural, structural_pred, frames):
    out = structural_pred
    assert isinstance(structural, WeeklyModel)
    validate_predictions(out)
    for c in (*PREDICTION_COLUMNS, *OPTIONAL_COLUMNS):
        assert c in out.columns, c
    assert out["season"].dtype == pl.Int32 and out["week"].dtype == pl.Int32
    assert all(out[c].dtype == pl.Float64 for c in ("pred_mean", *QCOLS, "active_prob"))
    assert set(out["scoring"]) == {"ppr", "std"}
    assert set(out["model"]) == {"chain_v1"}
    # every rostered player and every DST of the week has one row per scoring
    week = frames["player_week"].filter((pl.col("season") == 2017) & (pl.col("week") == 10))
    ppr = out.filter(pl.col("scoring") == "ppr")
    assert set(ppr.filter(pl.col("position") != "DST")["entity_id"]) == set(week["player_id"])
    assert ppr.filter(pl.col("position") == "DST").height == 8
    assert out.filter(pl.col("position") == "DST")["active_prob"].min() == 1.0


def test_quantiles_monotone_and_ranges(structural_pred):
    q = structural_pred.select(QCOLS).to_numpy()
    assert np.all(np.diff(q, axis=1) >= -1e-12)
    for c in ("boom_prob", "bust_prob", "active_prob"):
        v = structural_pred[c].drop_nulls()
        assert v.min() >= 0 and v.max() <= 1
    assert structural_pred["pred_mean"].null_count() == 0
    # unconditional mean is the conditional mean scaled by availability (simulation noise aside)
    off = structural_pred.filter(pl.col("active_prob") > 0.3)
    ratio = off["pred_mean_uncond"] / (off["active_prob"] * off["pred_mean"])
    assert ratio.drop_nans().drop_nulls().is_between(0.8, 1.2).mean() > 0.9
    # PPR never scores below standard for the same player
    j = structural_pred.filter(pl.col("scoring") == "ppr").join(
        structural_pred.filter(pl.col("scoring") == "std"), on="entity_id", suffix="_std"
    )
    assert (j["pred_mean"] >= j["pred_mean_std"] - 1e-9).all()


def test_drivers_are_json_with_five_groups(structural_pred):
    for text in structural_pred["drivers"].head(50):
        payload = json.loads(text)
        for k in ("opportunity", "efficiency", "matchup", "environment", "availability"):
            assert isinstance(payload[k], float)


def test_seed_makes_predict_deterministic(structural, structural_pred):
    again = structural.predict(*CUTOFF, SCORINGS)
    assert again.equals(structural_pred)
    other = ChainModel(stage_choice="structural", n_sims=300, seed=6, data=structural.data).fit(
        CUTOFF
    )
    assert not other.predict(*CUTOFF, SCORINGS).equals(structural_pred)


def test_save_and_load_round_trip(structural, structural_pred, tmp_path):
    structural.save(tmp_path / "m")
    loaded = ChainModel.load(tmp_path / "m", data=structural.data)
    assert loaded.cutoff == CUTOFF and loaded.choices == structural.choices
    assert loaded.predict(*CUTOFF, SCORINGS).equals(structural_pred)


def test_lgbm_chain_fits_saves_and_loads(frames, tmp_path):
    choice = {
        s.name: "lgbm" for s in STAGES if s.dataset != "player_week" or s.name.startswith("share")
    }
    m = ChainModel(stage_choice=choice, n_sims=200, seed=1, data=ChainData(frames=frames)).fit(
        CUTOFF
    )
    assert set(m.lgbm) == {k for k, v in m.choices.items() if v == "lgbm"}
    assert m.lgbm["share_target"].best_iteration >= 10
    assert m.lgbm["share_target"].es_frame is not None
    out = validate_predictions(m.predict(*CUTOFF, {"ppr": SCORINGS["ppr"]}))
    m.save(tmp_path / "lg")
    back = ChainModel.load(tmp_path / "lg", data=m.data)
    assert back.predict(*CUTOFF, {"ppr": SCORINGS["ppr"]}).equals(out)


def test_primary_ignores_same_week_reports_final_report_uses_them(frames):
    out_frames = dict(frames)
    out_frames["player_week"] = frames["player_week"].with_columns(
        pl.lit("Out").alias("avail__report_status"), pl.lit(3.0).alias("avail__report_status_ord")
    )
    base = ChainModel(
        stage_choice="structural", n_sims=200, seed=3, data=ChainData(frames=frames)
    ).fit(CUTOFF)
    scrambled = ChainModel(
        stage_choice="structural", n_sims=200, seed=3, data=ChainData(frames=out_frames)
    ).fit(CUTOFF)
    one = {"ppr": SCORINGS["ppr"]}
    assert base.predict(*CUTOFF, one).equals(scrambled.predict(*CUTOFF, one))
    fr = ChainModel(
        config="final_report",
        stage_choice="structural",
        n_sims=200,
        seed=3,
        data=ChainData(frames=out_frames),
    ).fit(CUTOFF)
    pred = fr.predict(*CUTOFF, one)
    assert fr.name == "chain_v1_final_report"
    off = pred.filter(pl.col("position") != "DST")
    assert off["active_prob"].max() == 0.0  # everyone ruled out
    validate_predictions(pred)  # conditional columns still exist (forced-active pass)
    assert off["pred_mean"].null_count() == 0


def test_run_train_writes_a_loadable_artifact(frames, tmp_path):
    from ffml.models.train import resolve_config, run_train

    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"cutoff": [2017, 10], "n_sims": 200, "seed": 4}), encoding="utf-8")
    data = ChainData(frames=frames)
    directory = run_train(str(cfg), out_root=tmp_path / "models", data=data)
    for name in ("params.json", "features.json", "selection.json", "train_manifest.json"):
        assert (directory / name).exists(), name
    manifest = json.loads((directory / "train_manifest.json").read_text(encoding="utf-8"))
    assert manifest["cutoff"] == [2017, 10] and manifest["seeds"]["model"] == 4
    assert "git" in manifest and "inputs" in manifest
    feats = json.loads((directory / "features.json").read_text(encoding="utf-8"))
    assert len(feats["fingerprint_sha256"]) == 64
    model = ChainModel.load(directory, data=data)
    validate_predictions(model.predict(2017, 10, {"ppr": SCORINGS["ppr"]}))
    assert resolve_config("default")["cutoff"] == [2024, 19]
    with pytest.raises(ValueError):
        resolve_config("nope")
    bad = tmp_path / "bad.json"
    bad.write_text(json.dumps({"cutoff": [2025, 3]}), encoding="utf-8")
    with pytest.raises(ValueError, match="holdout"):
        run_train(str(bad), out_root=tmp_path / "models", data=data)


def test_resolve_choices():
    assert set(resolve_choices("structural").values()) == {"structural"}
    lg = resolve_choices("lgbm")
    assert all(v == "lgbm" for k, v in lg.items() if stage(k).lgbm)
    assert resolve_choices({"team_plays": "lgbm"})["team_plays"] == "lgbm"
    assert resolve_choices({"team_plays": "lgbm"})["share_target"] == "structural"
    with pytest.raises(ValueError):
        resolve_choices("bogus")


def test_selection_rule_and_losses():
    spec = stage("dst_sacks")
    assert row_loss(spec, np.array([0.0, 2.0]), np.array([1.0, 2.0])) == pytest.approx([2.0, 0.0])
    ll = row_loss(stage("availability"), np.array([1.0, 0.0]), np.array([0.5, 0.5]))
    assert ll == pytest.approx([np.log(2), np.log(2)])
    rng = np.random.default_rng(0)
    weekly = pl.DataFrame(
        {"loss_structural": rng.uniform(9, 11, 40), "w": np.full(40, 10.0)}
    ).with_columns((pl.col("loss_structural") - 1.0).alias("loss_lgbm"))
    point, lo, hi = bootstrap_diff(weekly, rng)
    assert point == pytest.approx(-0.1) and hi < 0
    recs = [
        {
            "stage": "team_plays",
            "season": 2022,
            "week": w,
            "n": 30,
            "w": 30.0,
            "loss_structural": 100.0,
            "loss_lgbm": 100.0 + rng.normal(0, 5),
        }
        for w in range(2, 19)
    ]
    out = summarise(recs)
    assert out["team_plays"]["choice"] == "structural"  # no clear improvement
