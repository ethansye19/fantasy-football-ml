"""Monte Carlo engine: determinism, conservation, correlations, availability, timing."""

from __future__ import annotations

import time

import numpy as np
import polars as pl
import pytest
from simtoy import GAME, toy_dst_params, toy_player_params, toy_team_params

from ffml.scoring import STAT_COLUMNS, ScoringRules
from ffml.sim import simulate, simulate_full, synthetic_slate
from ffml.sim.engine import KEY_OUTPUT_COLUMNS, _dirichlet_shares

PPR = ScoringRules.from_preset("ppr")


def points_by_sim(sims: pl.DataFrame, entity: str) -> np.ndarray:
    one = sims.filter(pl.col("entity_id") == entity).sort("sim")
    return PPR.score(one).to_numpy()


def stat_by_sim(sims: pl.DataFrame, entity: str, column: str) -> np.ndarray:
    return sims.filter(pl.col("entity_id") == entity).sort("sim")[column].to_numpy().astype(float)


# --------------------------------------------------------------------------- #
# Shape and determinism
# --------------------------------------------------------------------------- #


def test_output_schema(toy):
    tp, pp, dp = toy
    sims = simulate(tp, pp, dp, n_sims=50, seed=1)
    assert sims.columns == [*KEY_OUTPUT_COLUMNS, *STAT_COLUMNS]
    assert sims.height == (pp.height + dp.height) * 50
    assert sims["sim"].dtype == pl.Int16 and sims["active"].dtype == pl.Boolean
    assert all(sims[c].dtype == pl.Int16 for c in STAT_COLUMNS)
    dst = sims.filter(pl.col("position") == "DST")
    assert set(dst["entity_id"]) == {"HOM", "AWY"} and dst["active"].all()
    offense_cols = ["pass_yds", "rec", "rush_att", "fg_made_40_49", "xp_made"]
    assert dst.select(pl.sum_horizontal(offense_cols)).to_series().sum() == 0
    off = sims.filter(pl.col("position") != "DST")
    assert (
        off.select(pl.sum_horizontal(["sacks", "pts_allowed", "yds_allowed"])).to_series().sum()
        == 0
    )


def test_determinism_with_seed(toy):
    tp, pp, dp = toy
    a = simulate(tp, pp, dp, n_sims=300, seed=7)
    b = simulate(tp, pp, dp, n_sims=300, seed=7)
    c = simulate(tp, pp, dp, n_sims=300, seed=8)
    assert a.equals(b)
    assert not a.equals(c)
    # input row order does not change the draws
    shuffled = simulate(
        tp.reverse(), pp.sample(fraction=1.0, shuffle=True, seed=3), dp, n_sims=300, seed=7
    )
    assert shuffled.equals(a)


# --------------------------------------------------------------------------- #
# Conservation
# --------------------------------------------------------------------------- #


def test_dirichlet_shares_sum_to_one_over_active_players():
    rng = np.random.default_rng(0)
    base = np.broadcast_to(np.array([0.4, 0.3, 0.2, 0.1, 0.0]), (3, 400, 5)).copy()
    active = (rng.random((3, 400, 5)) < 0.7).astype(float)
    conc = np.full((3, 400, 5), 50.0)
    shares = _dirichlet_shares(rng, base, conc, active)
    eligible = (active * (base > 0)).sum(-1) > 0
    np.testing.assert_allclose(shares.sum(-1)[eligible], 1.0, atol=1e-12)
    assert np.all(shares.sum(-1)[~eligible] == 0)
    assert np.all(shares[active == 0] == 0)
    # the mean share of an always-active slot matches its renormalised base
    all_on = np.ones_like(active)
    m = _dirichlet_shares(rng, base, conc, all_on).mean(axis=(0, 1))
    np.testing.assert_allclose(m[:4], [0.4, 0.3, 0.2, 0.1], atol=0.01)


def _team_sums(sims: pl.DataFrame, teams: pl.DataFrame) -> pl.DataFrame:
    return (
        sims.filter(pl.col("position") != "DST")
        .group_by(["team", "sim"])
        .agg(
            pl.col("targets").cast(pl.Int64).sum().alias("targets_p"),
            pl.col("pass_att").cast(pl.Int64).sum().alias("pass_att_p"),
            pl.col("pass_cmp").cast(pl.Int64).sum().alias("pass_cmp_p"),
            pl.col("pass_yds").cast(pl.Int64).sum().alias("pass_yds_p"),
            pl.col("rush_att").cast(pl.Int64).sum().alias("rush_att_p"),
            pl.col("rec").cast(pl.Int64).sum().alias("rec_p"),
            pl.col("rec_yds").cast(pl.Int64).sum().alias("rec_yds_p"),
            pl.col("rec_td").cast(pl.Int64).sum().alias("rec_td_p"),
            pl.col("pass_td").cast(pl.Int64).sum().alias("pass_td_p"),
            pl.col("rush_td").cast(pl.Int64).sum().alias("rush_td_p"),
        )
        .join(teams, on=["team", "sim"])
    )


def test_team_totals_are_conserved():
    tp = toy_team_params()
    pp = toy_player_params(
        **{"HOM-WR1": {"availability_prob": 0.6}, "HOM-RB1": {"availability_prob": 0.7}}
    )
    res = simulate_full(tp, pp, toy_dst_params(), n_sims=1000, seed=11)
    sims, teams = res.players, res.teams
    per_team = _team_sums(sims, teams)
    assert per_team.height == 2 * 1000
    # receivers plus the unmodeled bucket take every attempt
    assert (per_team["targets_p"] + per_team["unmodeled_targets"] == per_team["pass_att"]).all()
    assert (per_team["targets_p"] == per_team["targets"]).all()
    assert (per_team["pass_att_p"] == per_team["pass_att"]).all()
    assert (per_team["rush_att_p"] == per_team["rush_att"]).all()
    assert (per_team["rec_td_p"] + per_team["unmodeled_rec_td"] == per_team["pass_td_p"]).all()
    assert (per_team["pass_td_p"] + per_team["rush_td_p"] == per_team["off_td"]).all()
    assert (per_team["rec_p"] <= per_team["targets_p"]).all()
    # D-057: passing totals are the receivers' totals in every simulation
    assert (per_team["pass_cmp_p"] == per_team["rec_p"] + per_team["unmodeled_rec"]).all()
    assert (per_team["pass_yds_p"] == per_team["rec_yds_p"] + per_team["unmodeled_rec_yds"]).all()
    assert (per_team["pass_yds_p"] == per_team["pass_yds"]).all()
    assert (per_team["pass_cmp_p"] == per_team["pass_cmp"]).all()
    # plays are dropbacks plus designed runs; carries add scrambles and kneel-downs
    assert (teams["dropbacks"] + teams["designed_runs"] == teams["plays"]).all()
    assert (
        teams["designed_runs"] + teams["scrambles"] + teams["kneels"] == teams["rush_att"]
    ).all()
    assert teams["plays"].min() >= 40 and teams["plays"].max() <= 90
    # every player: receptions <= targets, touchdowns <= touches
    off = sims.filter(pl.col("position") != "DST")
    assert (off["rec"] <= off["targets"]).all()
    assert (off["rec_td"] <= off["rec"]).all()
    assert (off["rush_td"] <= off["rush_att"]).all()
    assert (off["pass_cmp"] + off["pass_int"] <= off["pass_att"]).all()


def test_qb_passing_equals_receiving_with_bucket_and_two_passers():
    """D-057 with an explicit unmodeled bucket and two QBs sharing dropbacks."""
    tp = toy_team_params().with_columns(
        pl.lit(0.12).alias("unmodeled_tgt_share"),
        pl.lit(0.4).alias("unmodeled_catch_rate"),
        pl.lit(10.0).alias("unmodeled_yards_per_rec"),
    )
    pp = toy_player_params(**{"HOM-QB1": {"dropback_share": 0.6}})
    extra = pp.filter(pl.col("entity_id") == "HOM-QB1").with_columns(
        pl.lit("HOM-QB2").alias("entity_id"),
        pl.lit(0.4).alias("dropback_share"),
        pl.lit(0.0).alias("carry_share"),
        pl.lit(0.0).alias("rush_td_share"),
    )
    pp = pl.concat([pp, extra], how="diagonal_relaxed")
    res = simulate_full(tp, pp, toy_dst_params(), n_sims=800, seed=13)
    per_team = _team_sums(res.players, res.teams)
    assert (per_team["pass_yds_p"] == per_team["rec_yds_p"] + per_team["unmodeled_rec_yds"]).all()
    assert (per_team["pass_cmp_p"] == per_team["rec_p"] + per_team["unmodeled_rec"]).all()
    assert (per_team["pass_td_p"] == per_team["rec_td_p"] + per_team["unmodeled_rec_td"]).all()
    assert per_team["unmodeled_targets"].mean() > 0
    share = per_team["unmodeled_targets"].sum() / per_team["pass_att"].sum()
    assert abs(share - 0.12) < 0.02
    qbs = res.players.filter(pl.col("entity_id").is_in(["HOM-QB1", "HOM-QB2"]))
    assert (qbs["pass_cmp"] + qbs["pass_int"] <= qbs["pass_att"]).all()
    both = (
        qbs.group_by("sim").agg((pl.col("pass_att") > 0).sum().alias("n")).filter(pl.col("n") == 2)
    )
    assert both.height > 400  # both QBs throw in most simulations


def test_scrambles_and_kneels_are_qb_carries(toy):
    tp, pp, dp = toy
    res = simulate_full(tp, pp, dp, n_sims=1000, seed=17)
    qb = res.players.filter(pl.col("entity_id") == "HOM-QB1")
    team = res.teams.filter(pl.col("team") == "HOM")
    assert team["scrambles"].mean() > 0.5 and team["kneels"].mean() > 0.3
    # QB carries are his designed runs plus every scramble and kneel of the team
    rb = res.players.filter(pl.col("team") == "HOM", pl.col("position") == "RB")
    designed_rb = rb.group_by("sim").agg(pl.col("rush_att").cast(pl.Int64).sum()).sort("sim")
    t = team.sort("sim")
    qb_carries = qb.sort("sim")["rush_att"].cast(pl.Int64)
    assert (
        qb_carries + designed_rb["rush_att"]
        == t["designed_runs"].cast(pl.Int64) + t["scrambles"] + t["kneels"]
    ).all()


def test_passer_fallback_keeps_marginal_availability():
    tp = toy_team_params()
    pp = toy_player_params(**{"HOM-QB1": {"availability_prob": 0.9}})
    extra = pp.filter(pl.col("entity_id") == "HOM-QB1").with_columns(
        pl.lit("HOM-QB2").alias("entity_id"),
        pl.lit(0.3).alias("availability_prob"),
        pl.lit(0.2).alias("dropback_share"),
    )
    pp = pl.concat([pp, extra], how="diagonal_relaxed")
    sims = simulate(tp, pp, toy_dst_params(), n_sims=6000, seed=19)
    q = sims.filter(pl.col("entity_id").is_in(["HOM-QB1", "HOM-QB2"]))
    per_sim = q.group_by("sim").agg(pl.col("active").sum().alias("n"))
    assert per_sim["n"].min() >= 1  # a passer in every simulation
    rate = q.filter(pl.col("entity_id") == "HOM-QB2")["active"].mean()
    assert abs(rate - 0.3) < 0.02


def test_dst_forced_fumbles_follow_recoveries_and_rate(toy):
    tp, pp, _ = toy
    dp = pl.DataFrame({"team": ["HOM", "AWY"], "game_id": [GAME, GAME], "ff_rate": [1.5, 0.5]})
    sims = simulate(tp, pp, dp, n_sims=3000, seed=23)
    dst = sims.filter(pl.col("position") == "DST")
    hom = dst.filter(pl.col("team") == "HOM")
    awy = dst.filter(pl.col("team") == "AWY")
    assert abs(hom["forced_fumbles"].cast(pl.Float64).mean() - 1.5) < 0.1
    assert abs(awy["forced_fumbles"].cast(pl.Float64).mean() - 0.5) < 0.1
    corr = np.corrcoef(
        hom["forced_fumbles"].to_numpy().astype(float), hom["fum_rec"].to_numpy().astype(float)
    )[0, 1]
    assert corr > 0.2
    scored = ScoringRules.from_dict({"ff": 1.0}).score(hom)
    assert scored.mean() == pytest.approx(hom["forced_fumbles"].cast(pl.Float64).mean())


def test_defense_mirrors_the_opposing_offense(toy):
    tp, pp, dp = toy
    res = simulate_full(tp, pp, dp, n_sims=500, seed=5)
    sims, teams = res.players, res.teams
    offense = (
        sims.filter(pl.col("position") != "DST")
        .group_by(["team", "sim"])
        .agg(
            pl.col("pass_int").cast(pl.Int64).sum().alias("ints"),
            pl.col("fum_lost").cast(pl.Int64).sum().alias("fum"),
        )
    )
    dst = sims.filter(pl.col("position") == "DST").with_columns(
        pl.when(pl.col("team") == "HOM").then(pl.lit("AWY")).otherwise(pl.lit("HOM")).alias("opp")
    )
    j = dst.join(offense, left_on=["opp", "sim"], right_on=["team", "sim"]).join(
        teams.select("team", "sim", "points", "sacks"),
        left_on=["opp", "sim"],
        right_on=["team", "sim"],
    )
    assert (j["def_int"] == j["ints"]).all()
    assert (j["fum_rec"] == j["fum"]).all()
    assert (j["pts_allowed"] == j["points"]).all()
    assert (j["sacks"] == j["sacks_right"]).all()


# --------------------------------------------------------------------------- #
# Correlation structure
# --------------------------------------------------------------------------- #


def test_correlation_structure_on_a_two_team_game():
    tp = toy_team_params(total_sd=14.0, margin_sd=8.0)
    res = simulate_full(tp, toy_player_params(), toy_dst_params(), n_sims=4000, seed=21)
    sims, teams = res.players, res.teams
    # QB and his WR1 move together (shared attempts, touchdowns, efficiency)
    qb_wr = np.corrcoef(points_by_sim(sims, "HOM-QB1"), points_by_sim(sims, "HOM-WR1"))[0, 1]
    assert qb_wr > 0.2
    # teammates compete for the same targets: WR1 and WR2 target shares are negatively related
    att = teams.filter(pl.col("team") == "HOM").sort("sim")["pass_att"].to_numpy().astype(float)
    ok = att > 0
    s1 = stat_by_sim(sims, "HOM-WR1", "targets")[ok] / att[ok]
    s2 = stat_by_sim(sims, "HOM-WR2", "targets")[ok] / att[ok]
    assert np.corrcoef(s1, s2)[0, 1] < -0.1

    # opponents are linked through the shared total: with total_sd 14 and margin_sd 8
    # the drawn team points correlate at (14^2 - 8^2) / (14^2 + 8^2) = 0.51; touchdown
    # and kicking noise dilutes the realised points
    def team_col(team: str, col: str) -> np.ndarray:
        return teams.filter(pl.col("team") == team).sort("sim")[col].to_numpy().astype(float)

    drawn = np.corrcoef(team_col("HOM", "points_drawn"), team_col("AWY", "points_drawn"))[0, 1]
    assert abs(drawn - 0.51) < 0.05
    assert np.corrcoef(team_col("HOM", "points"), team_col("AWY", "points"))[0, 1] > 0.1
    qbs = np.corrcoef(points_by_sim(sims, "HOM-QB1"), points_by_sim(sims, "AWY-QB1"))[0, 1]
    assert qbs > 0.05
    # a defense moves against the offense it faces
    dst = points_by_sim(sims, "AWY")
    assert np.corrcoef(points_by_sim(sims, "HOM-QB1"), dst)[0, 1] < -0.1


# --------------------------------------------------------------------------- #
# Availability
# --------------------------------------------------------------------------- #


def test_unavailable_player_has_zero_lines_and_teammates_absorb(toy):
    tp, base_pp, dp = toy
    pp = toy_player_params(
        **{"HOM-WR1": {"availability_prob": 0.0}, "HOM-K1": {"availability_prob": 0.0}}
    )
    sims = simulate(tp, pp, dp, n_sims=500, seed=2)
    for entity in ("HOM-WR1", "HOM-K1"):
        out = sims.filter(pl.col("entity_id") == entity)
        assert not out["active"].any()
        assert out.select(pl.sum_horizontal(list(STAT_COLUMNS))).to_series().sum() == 0
    base = simulate(tp, base_pp, dp, n_sims=500, seed=2)
    wr2_out = stat_by_sim(sims, "HOM-WR2", "targets").mean()
    wr2_base = stat_by_sim(base, "HOM-WR2", "targets").mean()
    assert wr2_out > wr2_base * 1.3  # WR1's targets are redistributed
    # with no kicker active, nobody kicks but the team still scores touchdowns
    assert sims.filter(pl.col("team") == "HOM")["rec_td"].cast(pl.Int64).sum() > 0


def test_availability_probability_is_respected(toy):
    tp, _, dp = toy
    pp = toy_player_params(**{"AWY-RB1": {"availability_prob": 0.25}})
    sims = simulate(tp, pp, dp, n_sims=2000, seed=4)
    rate = sims.filter(pl.col("entity_id") == "AWY-RB1")["active"].mean()
    assert abs(rate - 0.25) < 0.03


def test_two_kickers_one_kicks_per_simulation(toy):
    tp, _, dp = toy
    pp = toy_player_params(**{"HOM-K1": {"availability_prob": 0.5}})
    extra = pp.filter(pl.col("entity_id") == "HOM-K1").with_columns(
        pl.lit("HOM-K2").alias("entity_id"), pl.lit(0.3).alias("availability_prob")
    )
    sims = simulate(tp, pl.concat([pp, extra], how="diagonal_relaxed"), dp, n_sims=1000, seed=9)
    kick_cols = [
        "fg_made_0_19",
        "fg_made_20_29",
        "fg_made_30_39",
        "fg_made_40_49",
        "fg_made_50_59",
        "fg_made_60p",
        "fg_missed",
        "xp_made",
        "xp_missed",
    ]
    k = (
        sims.filter(pl.col("entity_id").is_in(["HOM-K1", "HOM-K2"]))
        .with_columns(pl.sum_horizontal(kick_cols).alias("kicks"))
        .pivot(on="entity_id", index="sim", values="kicks")
    )
    assert ((k["HOM-K1"] > 0) & (k["HOM-K2"] > 0)).sum() == 0


# --------------------------------------------------------------------------- #
# Validation and timing
# --------------------------------------------------------------------------- #


def test_invalid_inputs_are_rejected(toy):
    tp, pp, dp = toy
    with pytest.raises(ValueError, match="exactly two team rows"):
        simulate(tp.head(1), pp.filter(pl.col("team") == "HOM"), None, n_sims=10)
    with pytest.raises(ValueError, match="no TeamGameParams row"):
        simulate(tp, pp.with_columns(pl.lit("XXX").alias("team")), dp, n_sims=10)
    with pytest.raises(ValueError, match="missing required columns"):
        simulate(tp.drop("exp_plays"), pp, dp, n_sims=10)
    with pytest.raises(ValueError, match="unsupported positions"):
        simulate(tp, pp.with_columns(pl.lit("LB").alias("position")), dp, n_sims=10)
    with pytest.raises(ValueError, match="duplicate keys"):
        simulate(tp, pl.concat([pp, pp.head(1)]), dp, n_sims=10)
    assert GAME in tp["game_id"].to_list()


def test_600_players_2000_sims_under_30_seconds():
    tp, pp, dp = synthetic_slate(n_games=16)
    assert pp.height >= 600
    start = time.perf_counter()
    sims = simulate(tp, pp, dp, n_sims=2000, seed=1)
    elapsed = time.perf_counter() - start
    print(f"simulate: {pp.height} players + {dp.height} DST x 2000 sims in {elapsed:.2f}s")
    assert sims.height == (pp.height + dp.height) * 2000
    assert elapsed < 30.0
