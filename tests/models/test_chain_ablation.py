"""Ablation variants of ChainModel: excluded columns never reach a stage; defaults unchanged."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

import ffml.models.chain.lgbm as lgbm_mod
from ffml.features.manifest import columns
from ffml.models.chain import ChainModel
from ffml.models.chain.ablation import (
    ABLATIONS,
    LINE_COLUMNS,
    drop_excluded,
    is_excluded,
    strength_lines,
)
from ffml.models.chain.availability import AvailabilityLogit
from ffml.models.chain.data import ChainData
from ffml.models.chain.lgbm import stage_features
from ffml.models.chain.stages import STAGES
from ffml.models.chain.structural import StructuralStage
from ffml.models.protocol import validate_predictions
from ffml.scoring import ScoringRules

CUTOFF = (2017, 10)
PPR = {"ppr": ScoringRules.from_preset("ppr")}
NGS_COL = "eff__ngs_avg_separation__ew"


@pytest.fixture(scope="module")
def frames():
    from ffml.models.chain.synthetic import synthetic_frames

    fr = synthetic_frames()
    rng = np.random.default_rng(0)
    pw = fr["player_week"]
    fr["player_week"] = pw.with_columns(pl.Series(NGS_COL, rng.normal(3, 0.5, pw.height)))
    return fr


def _record_inputs(monkeypatch) -> list[set[str]]:
    """Columns of every frame any stage is fitted on, and every LightGBM matrix."""
    seen: list[set[str]] = []
    real_matrix = lgbm_mod._matrix
    real_struct, real_avail = StructuralStage.fit, AvailabilityLogit.fit

    def matrix(df, features):
        seen.append(set(features))
        return real_matrix(df, features)

    def struct_fit(self, df):
        seen.append(set(df.columns))
        return real_struct(self, df)

    def avail_fit(self, df):
        seen.append(set(df.columns))
        return real_avail(self, df)

    monkeypatch.setattr(lgbm_mod, "_matrix", matrix)
    monkeypatch.setattr(StructuralStage, "fit", struct_fit)
    monkeypatch.setattr(AvailabilityLogit, "fit", avail_fit)
    return seen


@pytest.mark.parametrize("ablation", ["no_vegas", "no_ngs"])
def test_excluded_columns_never_reach_a_stage(frames, monkeypatch, ablation):
    seen = _record_inputs(monkeypatch)
    m = ChainModel(
        stage_choice="lgbm", n_sims=150, seed=2, ablation=ablation, data=ChainData(frames=frames)
    ).fit(CUTOFF)
    patterns = ABLATIONS[ablation]
    assert seen and all(not is_excluded(c, patterns) for cols in seen for c in cols)
    for stage in m.lgbm.values():
        assert not [c for c in stage.features if is_excluded(c, patterns)]
    validate_predictions(m.predict(*CUTOFF, PPR))
    assert m.name == f"chain_v1-primary-{ablation}"


def test_default_feature_lists_are_unchanged(frames, monkeypatch):
    """No exclusions: every stage sees exactly the lists the stage specs give today."""
    # D-108 fallback, for this test only: one LightGBM thread, so the two fits compared below
    # are reproducible on CI runners with fewer cores than num_threads (D-133); the model keeps 4
    monkeypatch.setitem(lgbm_mod.BASE_PARAMS, "num_threads", 1)
    default = ChainModel(stage_choice="lgbm", n_sims=150, seed=2, data=ChainData(frames=frames))
    assert default.exclusions == () and default.name == "chain_v1"
    default.fit(CUTOFF)
    for name, stage in default.lgbm.items():
        rows = default.stage_rows(stage.spec, default.data.train_rows(stage.spec.dataset, CUTOFF))
        assert stage.features == stage_features(stage.spec, rows.columns, "primary"), name
    assert NGS_COL in default.lgbm["eff_catch_rate"].features
    assert "ctx__implied_team_total" in default.lgbm["team_off_td"].features
    assert default.structural["team_off_td"].params.get("source") is None
    # on the real manifest, an empty exclusion list leaves every column in place
    for ds in ("team_week", "player_week", "dst_week"):
        names = [c.name for c in columns(ds)]
        frame = pl.DataFrame(schema={n: pl.Float64 for n in names})
        assert drop_excluded(frame, ()).columns == names
    for spec in STAGES:
        names = [c.name for c in columns(spec.dataset)]
        assert stage_features(spec, names) == [
            c for c in dict.fromkeys(spec.features) if c in set(names)
        ]
    # explicit "none" is the default model
    explicit = ChainModel(
        stage_choice="lgbm", n_sims=150, seed=2, ablation="none", data=default.data
    ).fit(CUTOFF)
    assert explicit.predict(*CUTOFF, PPR).equals(default.predict(*CUTOFF, PPR))


def test_no_vegas_fallbacks_and_strength_lines(frames):
    m = ChainModel(
        stage_choice="structural",
        n_sims=150,
        seed=2,
        ablation="no_vegas",
        data=ChainData(frames=frames),
    ).fit(CUTOFF)
    td = m.structural["team_off_td"].params
    assert td["source"] == "team__points__l5|team__points__ps"
    assert m.structural["team_pass_rate"].params["b"] == 0.0  # no spread term
    st = m.consts["strength"]
    assert st["total_sd"] > 0 and st["margin_sd"] > 0
    tp, _, _ = m.build_params(*CUTOFF)
    assert set(tp["total_sd"]) == {st["total_sd"]} and set(tp["margin_sd"]) == {st["margin_sd"]}
    # lines come from lagged points, not from the schedule
    week = m.data.week_rows("team_week", *CUTOFF)
    lines = strength_lines(week, st["points_mean"]).sort("game_id", "team")
    t = tp.sort("game_id", "team")
    np.testing.assert_allclose(t["spread_line"].to_numpy(), -lines["ctx__spread"].to_numpy())
    np.testing.assert_allclose(t["total_line"].to_numpy(), lines["ctx__total_line"].to_numpy())
    vegas = week.sort("game_id", "team")["ctx__total_line"].to_numpy()
    assert not np.allclose(t["total_line"].to_numpy(), vegas)
    validate_predictions(m.predict(*CUTOFF, PPR))


def test_variant_names_data_and_persistence(frames, tmp_path):
    same_week = ChainData(player_dataset="player_week_depth_same_week", frames=frames)
    m = ChainModel(
        stage_choice="structural",
        n_sims=150,
        player_features_dataset="player_week_depth_same_week",
        data=same_week,
    )
    assert m.name == "chain_v1-primary-depth_same_week"
    assert ChainModel(
        config="final_report", ablation="no_ngs", data=ChainData(frames=frames)
    ).name == ("chain_v1-final_report-no_ngs")
    custom = ChainModel(
        exclude_features=("opp__snap_share__", "team"), data=ChainData(frames=frames)
    )
    assert (
        custom.name.startswith("chain_v1-primary-excl")
        and len(custom.name) == len("chain_v1-primary-excl") + 8
    )
    kept = drop_excluded(frames["player_week"], custom.exclusions)
    assert "team" in kept.columns and "player_id" in kept.columns  # keys are never dropped
    assert not [c for c in kept.columns if c.startswith(("opp__snap_share__", "team__"))]
    with pytest.raises(ValueError, match="unknown ablation"):
        ChainModel(ablation="no_weather")
    with pytest.raises(ValueError, match="model wants"):
        ChainModel(
            player_features_dataset="player_week_depth_same_week", data=ChainData(frames=frames)
        )
    m.fit(CUTOFF)
    out = m.predict(*CUTOFF, PPR)
    assert set(out["model"]) == {"chain_v1-primary-depth_same_week"}
    m.save(tmp_path / "v")
    back = ChainModel.load(tmp_path / "v", data=same_week)
    assert back.name == m.name and back.player_features_dataset == "player_week_depth_same_week"
    assert back.predict(*CUTOFF, PPR).equals(out)
    assert set(LINE_COLUMNS) == set(ABLATIONS["no_vegas"])
