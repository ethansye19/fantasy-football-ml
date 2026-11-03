"""Rest-of-season rules: strength model, sd widening, persistent availability, engine override."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest
from simtoy import toy_dst_params, toy_player_params, toy_team_params

from ffml.models.chain.synthetic import synthetic_frames
from ffml.sim.engine import simulate_full
from ffml.sim.ros import (
    RHO_MULTI,
    RHO_SINGLE,
    StrengthModel,
    active_paths,
    strength_game_lines,
    unavailable_paths,
)


def test_strength_model_fits_and_sds_widen_with_weeks_ahead():
    tw = synthetic_frames()["team_week"].with_columns(
        pl.col("team__points__ew").alias("team__points__ew"),
        (22 - pl.col("def__points_allowed__ew") + 22).alias("def__points_allowed__ew"),
    )
    m = StrengthModel.fit(tw)
    assert m.source == "fitted" and m.n_games > 100
    assert m.c_off > 0 and m.total_sd0 > 0 and m.margin_sd0 > 0 and m.drift >= 0
    sds = [m.sds(k) for k in range(0, 9)]
    assert all(b[0] >= a[0] and b[1] >= a[1] for a, b in zip(sds, sds[1:], strict=False))
    widened = StrengthModel(total_sd0=13.0, margin_sd0=12.0, drift=1.0)
    assert widened.sds(8) == pytest.approx((np.sqrt(169 + 16), np.sqrt(144 + 16)))
    assert StrengthModel.fit(tw.head(10)).source.startswith("default")


def test_strength_lines_follow_the_ctx_conventions():
    m = StrengthModel(c0=0.0, c_off=0.5, c_def=0.5, c_home=2.0)
    games = pl.DataFrame(
        {
            "game_id": ["g", "g"],
            "team": ["A", "B"],
            "opponent": ["B", "A"],
            "home": [True, False],
        }
    )
    rating = pl.DataFrame({"team": ["A", "B"], "off": [30.0, 20.0], "dfn": [20.0, 24.0]})
    out = strength_game_lines(games, rating, m).sort("team")
    a, b = out.row(0, named=True), out.row(1, named=True)
    assert a["ctx__implied_team_total"] == pytest.approx(0.5 * 30 + 0.5 * 24 + 2.0)
    assert b["ctx__implied_team_total"] == pytest.approx(0.5 * 20 + 0.5 * 20)
    assert a["ctx__total_line"] == b["ctx__total_line"]
    assert (
        a["ctx__spread"] == pytest.approx(-b["ctx__spread"]) and a["ctx__spread"] < 0
    )  # favourite


def _players() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "player_id": ["out1", "outmulti", "ir", "healthy", "q"],
            "avail_cap": [0.0, 0.0, 0.0, 1.0, 0.7],
            "avail__report_status": ["Out", "Out", None, None, "Questionable"],
            "avail__roster_status": ["ACT", "ACT", "RES", "ACT", "ACT"],
            "attr__games_missed__l5": [0.0, 2.0, 3.0, 0.0, 0.0],
            "p_primary": [0.3, 0.2, 0.0, 0.95, 0.9],
            "p_healthy": [0.95, 0.95, 0.95, 0.95, 0.95],
        }
    )


def test_unavailable_paths_follow_the_documented_rules():
    m = unavailable_paths(_players(), [1, 2, 3, 4, 6])
    np.testing.assert_allclose(m[0], [RHO_SINGLE**k for k in (1, 2, 3, 4, 6)])
    np.testing.assert_allclose(m[1], [RHO_MULTI**k for k in (1, 2, 3, 4, 6)])
    np.testing.assert_allclose(m[2], [1, 1, 1, 0.6, 0.6**3])  # reserve: out three more weeks
    np.testing.assert_allclose(m[3], 0.0)
    np.testing.assert_allclose(m[4], [0.3 * RHO_SINGLE**k for k in (1, 2, 3, 4, 6)])
    assert np.all(np.diff(m, axis=1) <= 1e-12)  # decaying


def test_active_paths_are_persistent_and_match_the_rates():
    players = _players().with_columns(
        pl.lit(1.0).alias("p_healthy"), pl.lit(1.0).alias("p_primary")
    )
    ks = [1, 2, 3, 4, 5, 6]
    paths = active_paths(players, ks, 20000, np.random.default_rng(0))
    for pid in ("out1", "outmulti", "ir"):
        flags = np.vstack([paths[k][pid] for k in ks])
        # once back, a player stays back (no flapping between weeks)
        assert np.all(np.diff(flags.astype(int), axis=0) >= 0)
    expect = 1 - unavailable_paths(players, ks)
    got = np.array([[paths[k][p].mean() for k in ks] for p in players["player_id"]])
    np.testing.assert_allclose(got, expect, atol=0.015)
    # participation draws are fresh each week for players without a cap
    part = _players().with_columns(pl.lit(0.5).alias("p_primary"))
    p2 = active_paths(part, [1, 2], 20000, np.random.default_rng(1))
    both = (p2[1]["healthy"] & p2[2]["healthy"]).mean()
    assert both == pytest.approx(0.25, abs=0.02)


def test_engine_active_override_replaces_draws():
    tp, pp, dp = toy_team_params(), toy_player_params(), toy_dst_params()
    S = 400
    flags = np.zeros(S, dtype=bool)
    flags[:100] = True
    res = simulate_full(tp, pp, dp, n_sims=S, seed=3, active_override={"HOM-WR1": flags})
    wr = res.players.filter(pl.col("entity_id") == "HOM-WR1").sort("sim")
    np.testing.assert_array_equal(wr["active"].to_numpy(), flags)
    assert wr.filter(~pl.col("active"))["targets"].max() == 0
    # without an override the draws are the engine's own
    base = simulate_full(tp, pp, dp, n_sims=S, seed=3)
    assert base.players.filter(pl.col("entity_id") == "HOM-WR1")["active"].mean() == 1.0
