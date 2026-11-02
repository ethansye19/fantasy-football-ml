"""Structural baselines per stage and the availability layer, on hand-built synthetic rows."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from ffml.models.chain.availability import (
    AvailabilityLogit,
    final_report_availability,
    final_report_cap,
    questionable_rates,
    strip_avail,
)
from ffml.models.chain.stages import STAGES, expand, stage
from ffml.models.chain.structural import StructuralStage, depth_bucket


def fitted(name: str, df: pl.DataFrame) -> StructuralStage:
    return StructuralStage(stage(name)).fit(df)


def test_every_stage_has_a_structural_baseline_and_metric():
    assert len(STAGES) >= 25
    for s in STAGES:
        assert s.structural
        assert s.metric in ("mae", "poisson", "logloss")
        assert s.objective in ("regression", "cross_entropy", "poisson", "binary")
    assert expand(["a__{x,y}__{l1,l3}"]) == ("a__x__l1", "a__x__l3", "a__y__l1", "a__y__l3")


def test_team_plays_l5_mean_with_fallbacks():
    df = pl.DataFrame(
        {
            "team__plays__l5": [60.0, None, None],
            "team__plays__ps": [70.0, 66.0, None],
            "y_plays": [61.0, 64.0, 65.0],
        }
    )
    st = fitted("team_plays", df)
    assert st.params["mean"] == pytest.approx(190 / 3)
    np.testing.assert_allclose(st.predict(df), [60.0, 66.0, 190 / 3])


def test_pass_rate_one_parameter_spread_adjustment():
    rng = np.random.default_rng(0)
    n = 3000
    ew = rng.uniform(0.5, 0.7, n)
    spread = rng.normal(0, 6, n)  # ctx__spread: positive = underdog
    y = ew + (-0.006) * (-spread) + rng.normal(0, 0.01, n)
    df = pl.DataFrame(
        {"team__pass_rate__ew": ew, "ctx__spread": spread, "y_pass_rate": y, "y_plays": 60.0}
    )
    st = fitted("team_pass_rate", df)
    assert st.params["b"] == pytest.approx(-0.006, abs=5e-4)
    pred = st.predict(df)
    assert np.all((pred > 0) & (pred < 1))
    assert np.mean(np.abs(pred - y)) < 0.012


def test_team_touchdowns_implied_total_ratio():
    df = pl.DataFrame(
        {"ctx__implied_team_total": [20.0, 30.0, None], "y_offensive_td": [2.0, 3.0, 1.0]}
    )
    st = fitted("team_off_td", df)
    assert st.params["a"] == pytest.approx(5.0 / 50.0)
    np.testing.assert_allclose(st.predict(df), [2.0, 3.0, 2.0])


def test_ew_blend_weight_is_fitted_and_clipped():
    rng = np.random.default_rng(1)
    x = rng.uniform(1, 3, 4000)
    mean = 2.0
    y = 0.4 * x + 0.6 * mean + rng.normal(0, 0.05, x.size)
    df = pl.DataFrame({"team__field_goal_att__ew": x, "y_field_goal_att": y})
    st = fitted("team_fg_att", df)
    assert st.params["w"] == pytest.approx(0.4, abs=0.03)
    noise = df.with_columns(pl.Series("y_field_goal_att", rng.permutation(y)))
    assert 0.0 <= fitted("team_fg_att", noise).params["w"] <= 0.1


def test_share_ew_uses_history_then_position_depth_prior():
    rows = []
    for i in range(200):  # players without history: the prior comes from them
        rank = 1 + i % 4
        rows.append(
            {
                "position": "WR",
                "depth__rank": float(rank),
                "opp__target_share__ew": None,
                "y_target_share": {1: 0.20, 2: 0.12, 3: 0.08, 4: 0.03}[rank],
                "tm_targets": 30.0,
            }
        )
    rows.append(
        {
            "position": "WR",
            "depth__rank": 1.0,
            "opp__target_share__ew": 0.31,
            "y_target_share": 0.3,
            "tm_targets": 30.0,
        }
    )
    df = pl.DataFrame(rows, infer_schema_length=None)
    st = fitted("share_target", df)
    new = pl.DataFrame(
        {
            "position": ["WR", "WR", "WR", "WR"],
            "depth__rank": [1.0, 3.0, None, 2.0],
            "opp__target_share__ew": [None, None, None, 0.25],
        }
    )
    pred = st.predict(new)
    np.testing.assert_allclose(pred[:2], [0.20, 0.08], atol=1e-9)
    assert pred[2] == pytest.approx(st.params["priors"]["WR|all"])
    assert pred[3] == pytest.approx(0.25)
    np.testing.assert_array_equal(depth_bucket(new), [1, 3, 0, 2])


def test_rate_shrunk_and_position_prior_are_weighted():
    df = pl.DataFrame(
        {
            "position": ["RB", "RB", "WR"],
            "eff__catch_rate__ew_shrunk": [0.8, None, None],
            "y_catch_rate": [0.5, 1.0, 0.6],
            "y_targets": [1.0, 3.0, 10.0],
        }
    )
    st = fitted("eff_catch_rate", df)
    assert st.params["priors"]["RB"] == pytest.approx((0.5 + 3.0) / 4.0)
    np.testing.assert_allclose(st.predict(df), [0.8, 0.875, 0.6])
    fum = pl.DataFrame(
        {"position": ["RB", "RB"], "y_fumble_rate": [0.0, 0.02], "y_touches": [30.0, 10.0]}
    )
    fs = fitted("eff_fumble_rate", fum)
    np.testing.assert_allclose(fs.predict(fum), [0.005, 0.005])


def test_dst_blend_geometric_mean_times_volume():
    df = pl.DataFrame(
        {
            "def__dst_sack_rate__ew": [0.04, 0.09, None],
            "team__sack_rate_allowed__ew": [0.09, 0.04, 0.0625],
            "team__dropbacks__ew": [40.0, 40.0, 40.0],
            "y_sacks": [2.4, 2.4, 2.5],
        }
    )
    st = fitted("dst_sacks", df)
    raw = np.array([np.sqrt(0.04 * 0.09) * 40, np.sqrt(0.09 * 0.04) * 40])
    own_mean = (0.04 + 0.09) / 2
    raw = np.append(raw, np.sqrt(own_mean * 0.0625) * 40)
    assert st.params["c"] == pytest.approx(7.3 / raw.sum())
    np.testing.assert_allclose(st.predict(df), st.params["c"] * raw)


def test_direct_and_count_clipping():
    df = pl.DataFrame(
        {"def__dst_def_td__std_shrunk": [0.12, None, -1.0], "y_def_td": [0.0, 1.0, 0.0]}
    )
    st = fitted("dst_def_td", df)
    pred = st.predict(df)
    assert pred[0] == pytest.approx(0.12) and pred[1] == pytest.approx(1 / 3)
    assert pred[2] == pytest.approx(1e-3)  # counts are floored


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


def _availability_rows(n: int = 4000, seed: int = 2) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    missed = rng.integers(0, 6, n).astype(float)
    rank = rng.integers(1, 5, n).astype(float)
    logit = 2.0 - 0.8 * missed - 0.5 * (rank - 1)
    played = (rng.random(n) < 1 / (1 + np.exp(-logit))).astype(float)
    return pl.DataFrame(
        {
            "position": rng.choice(["QB", "RB", "WR", "TE", "K"], n),
            "attr__games_missed__l5": missed,
            "attr__weeks_since_last_game": rng.integers(1, 3, n).astype(float),
            "depth__rank": rank,
            "depth__starter_flag": (rank == 1).astype(float),
            "opp__snap_share__ew": rng.uniform(0, 1, n),
            "avail__report_status": rng.choice(["Out", "Questionable", None], n),
            "avail__report_status_ord": rng.integers(0, 4, n).astype(float),
            "y_played": played,
        }
    )


def test_primary_availability_never_reads_avail_columns():
    model = AvailabilityLogit()
    # column-name inspection: nothing the model reads is a same-week input
    assert not [c for c in model.input_columns if c.startswith("avail__")]
    avail_stage = stage("availability")
    assert not [c for c in avail_stage.features if c.startswith("avail__")]
    df = _availability_rows()
    model.fit(df)
    p1 = model.predict(df)
    # scrambling or dropping every avail__ column changes nothing
    scrambled = df.with_columns(
        pl.lit("Out").alias("avail__report_status"), pl.lit(3.0).alias("avail__report_status_ord")
    )
    np.testing.assert_array_equal(p1, model.predict(scrambled))
    np.testing.assert_array_equal(p1, model.predict(strip_avail(df)))
    assert not [c for c in strip_avail(df).columns if c.startswith("avail__")]
    assert np.all((p1 > 0) & (p1 < 1))
    # lagged information moves the probability the right way
    fresh = df.with_columns(pl.lit(0.0).alias("attr__games_missed__l5"))
    hurt = df.with_columns(pl.lit(5.0).alias("attr__games_missed__l5"))
    assert model.predict(fresh).mean() > model.predict(hurt).mean() + 0.2


def test_final_report_caps_follow_section_g_rules():
    df = pl.DataFrame(
        {
            "position": ["WR", "WR", "WR", "RB", "TE", "QB", "WR"],
            "avail__report_status": [
                "Out",
                "Doubtful",
                "Questionable",
                "Questionable",
                None,
                None,
                None,
            ],
            "avail__practice_status": [
                None,
                None,
                None,
                "Did Not Participate In Practice",
                None,
                None,
                None,
            ],
            "avail__roster_status": ["ACT", "ACT", "ACT", "ACT", "ACT", "RES", "ACT"],
            "avail__depth_chart_present": [1.0, 1.0, 1.0, 1.0, 1.0, 1.0, 0.0],
            "opp__snap_share__ew": [0.9, 0.9, 0.9, 0.9, 0.9, 0.9, 0.8],
        }
    )
    caps = final_report_cap(df, {"WR": 0.7, "RB": 0.8})
    np.testing.assert_allclose(caps, [0.0, 0.15, 0.7, 0.5, 1.0, 0.0, 0.5])
    prob = final_report_availability(df, np.full(df.height, 0.6), {"WR": 0.7, "RB": 0.8})
    np.testing.assert_allclose(prob, [0.0, 0.15, 0.6, 0.5, 0.6, 0.0, 0.5])


def test_questionable_base_rates_are_estimated_from_history():
    n = 200
    df = pl.DataFrame(
        {
            "position": ["WR"] * n,
            "avail__report_status": ["Questionable"] * n,
            "depth__starter_flag": [1.0] * n,
            "opp__snap_share__ew": [0.8] * n,
            "y_played": [1.0] * 150 + [0.0] * 50,
        }
    )
    assert questionable_rates(df) == {"WR": pytest.approx(0.75)}
