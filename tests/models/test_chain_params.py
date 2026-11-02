"""Parameter builder: share renormalisation, dtypes and ranges, carries, kickers, DST; drivers."""

from __future__ import annotations

import json

import numpy as np
import polars as pl
import pytest

from ffml.models.chain import drivers as drv
from ffml.models.chain import params_builder as pb
from ffml.models.chain.dispersion import DEFAULTS
from ffml.scoring import ScoringRules
from ffml.sim.params import DstGameParams, PlayerGameParams, TeamGameParams

GAME = "2023_10_AAA_BBB"
DISP = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
CONSTS = {
    "unmodeled_tgt_share": 0.04,
    "kneels": 0.7,
    "touches_per_play": 1.38,
    "ff_per_fum_rec": 0.84,
}


def team_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "team": ["AAA", "BBB"],
            "opponent": ["BBB", "AAA"],
            "game_id": [GAME, GAME],
            "season": [2023, 2023],
            "week": [10, 10],
            "home": [False, True],
            "ctx__spread": [3.0, -3.0],
            "ctx__total_line": [44.0, 44.0],
            "ctx__implied_team_total": [20.5, 23.5],
            "p_team_plays": [63.0, 65.0],
            "p_team_pass_rate": [0.62, 0.58],
            "p_team_off_td": [2.2, 2.6],
            "p_team_fg_att": [1.8, 2.0],
            "p_team_two_pt_att": [0.2, 0.2],
        }
    )


def player_rows() -> pl.DataFrame:
    rows = []
    spec = [
        ("QB1", "QB", 0.95, 0.0, 0.10, 0.97),
        ("QB2", "QB", 0.15, 0.0, 0.01, 0.30),
        ("RB1", "RB", 0.90, 0.10, 0.55, 0.0),
        ("RB2", "RB", 0.85, 0.05, 0.35, 0.0),
        ("WR1", "WR", 0.95, 0.27, 0.01, 0.0),
        ("WR2", "WR", 0.40, 0.20, 0.0, 0.0),
        ("TE1", "TE", 0.95, 0.15, 0.0, 0.0),
        ("K1", "K", 0.97, 0.0, 0.0, 0.0),
    ]
    for team in ("AAA", "BBB"):
        for suffix, pos, avail, tgt, car, dbk in spec:
            rows.append(
                {
                    "player_id": f"{team}-{suffix}",
                    "game_id": GAME,
                    "team": team,
                    "opponent": "BBB" if team == "AAA" else "AAA",
                    "position": pos,
                    "season": 2023,
                    "week": 10,
                    "p_availability": avail,
                    "p_share_target": tgt,
                    "p_share_carry": car,
                    "p_share_dropback": dbk,
                    "p_share_gl_carry": car,
                    "p_share_rz_target": tgt,
                    "p_td_pass_share": tgt,
                    "p_td_rush_share": car,
                    "p_eff_catch_rate": 0.66,
                    "p_eff_ypr": 10.5,
                    "p_eff_ypc": 4.4,
                    "p_eff_comp_rate": 0.65,
                    "p_eff_int_rate": 0.022,
                    "p_eff_sack_rate": 0.07,
                    "p_eff_scramble_rate": 0.05,
                    "p_eff_fumble_rate": 0.006,
                    "p_k_fg_pct": 0.9,
                    "opp__fg_att_share_50_59__car_shrunk": 0.3 if pos == "K" else None,
                    "eff__fg_pct_40_49__car_shrunk": 0.8 if pos == "K" else None,
                }
            )
    return pl.DataFrame(rows, infer_schema_length=None)


def dst_rows() -> pl.DataFrame:
    return pl.DataFrame(
        {
            "team": ["AAA", "BBB"],
            "opponent": ["BBB", "AAA"],
            "game_id": [GAME, GAME],
            "def__dst_sack_rate__ew": [0.08, None],
            "def__dst_int_rate__ew": [0.03, 0.02],
            "p_dst_sacks": [3.0, 2.0],
            "p_dst_int": [1.0, 0.7],
            "p_dst_fum_rec": [0.5, 0.6],
            "p_dst_ff": [1.0, 0.7],
            "p_dst_def_td": [0.15, 0.1],
            "p_dst_ret_td": [0.03, 0.03],
            "p_dst_safety": [0.03, 0.03],
            "p_dst_blocks": [0.07, 0.07],
        }
    )


@pytest.fixture
def built():
    tp = pb.build_team_params(team_rows(), DISP, CONSTS)
    pp = pb.build_player_params(player_rows(), tp, DISP, CONSTS, {"k_fg_pct": "lgbm"})
    dp = pb.build_dst_params(dst_rows(), tp, pp, CONSTS, {"dst_sacks": "lgbm"})
    return tp, pp, dp


def test_renormalise_sums_to_one_among_available_players():
    rng = np.random.default_rng(0)
    team = np.repeat(["a", "b", "c"], 6)
    shares = rng.uniform(0, 0.4, team.size)
    avail = rng.uniform(0, 1, team.size)
    avail[team == "c"] = 0.0  # nobody available: zeros, no division by zero
    out = pb.renormalise(shares, avail, team, 1.0)
    for t in ("a", "b"):
        m = team == t
        assert np.sum(avail[m] * out[m]) == pytest.approx(1.0)
        # relative shares are preserved
        np.testing.assert_allclose(out[m] / out[m].sum(), shares[m] / shares[m].sum())
    assert np.all(out[team == "c"] == 0)
    bucket = pb.renormalise(shares, avail, team, 1 - 0.04)
    m = team == "a"
    assert np.sum(avail[m] * bucket[m]) + 0.04 == pytest.approx(1.0)


def test_builder_dtypes_ranges_and_contracts(built):
    tp, pp, dp = built
    for frame, contract in ((tp, TeamGameParams), (pp, PlayerGameParams), (dp, DstGameParams)):
        assert frame.columns == list(contract.COLUMNS)
        for name, col in contract.COLUMNS.items():
            assert frame[name].dtype == col.dtype, name
        contract.validate(frame)  # ranges and keys
    assert tp["spread_line"].to_list() == [-3.0, 3.0]  # positive = team favoured
    assert tp["sd_plays"].to_list() == [DISP["sd_plays"]["late"]] * 2
    shares = ["target_share", "carry_share", "dropback_share", "pass_td_share", "rush_td_share"]
    assert pp.select([(pl.col(c).is_between(0, 1)).all() for c in shares]).row(0) == (True,) * 5
    assert pp.filter(pl.col("position") != "QB")["dropback_share"].max() == 0.0
    assert pp.filter(pl.col("position").is_in(["QB", "K"]))["target_share"].max() == 0.0


def test_team_shares_sum_to_one_over_expected_available_players(built):
    tp, pp, _ = built
    sums = pp.group_by("team").agg(
        (pl.col("availability_prob") * pl.col("target_share")).sum().alias("tgt"),
        (pl.col("availability_prob") * pl.col("dropback_share")).sum().alias("dbk"),
        (pl.col("availability_prob") * pl.col("carry_share")).sum().alias("car"),
        (pl.col("availability_prob") * pl.col("pass_td_share")).sum().alias("ptd"),
    )
    np.testing.assert_allclose(sums["tgt"], 1 - CONSTS["unmodeled_tgt_share"])
    # a starter's conditional dropback share cannot exceed 1 and is clipped there
    assert ((sums["dbk"] > 0.99) & (sums["dbk"] <= 1.0 + 1e-12)).all()
    np.testing.assert_allclose(sums["car"], 1.0)
    np.testing.assert_allclose(sums["ptd"], 1.0)
    assert tp["unmodeled_tgt_share"].to_list() == [0.04, 0.04]


def test_qb_designed_run_share_leaves_room_for_scrambles_and_kneels(built):
    _, pp, _ = built
    a = pp.filter(pl.col("team") == "AAA")
    qb = a.filter(pl.col("entity_id") == "AAA-QB1").row(0, named=True)
    rb = a.filter(pl.col("entity_id") == "AAA-RB1").row(0, named=True)
    # stage 2 says QB1 takes 0.10 / (sum of shares) of all carries; part of it are scrambles
    assert qb["scramble_rate"] == pytest.approx(0.05 / (1 - 0.07))
    assert qb["carry_share"] / rb["carry_share"] < 0.10 / 0.55


def test_kicker_logit_shift_matches_lgbm_fg_pct(built):
    _, pp, _ = built
    k = pp.filter(pl.col("position") == "K").row(0, named=True)
    shares = np.array(
        [k[f"fg_att_share_{b}"] for b in ("0_19", "20_29", "30_39", "40_49", "50_59", "60p")]
    )
    makes = np.array(
        [k[f"fg_make_prob_{b}"] for b in ("0_19", "20_29", "30_39", "40_49", "50_59", "60p")]
    )
    assert shares.sum() == pytest.approx(1.0)
    assert float(shares @ makes) == pytest.approx(0.9, abs=1e-4)
    assert np.all(np.diff(makes) <= 1e-9)  # longer kicks are never easier here


def test_dst_lgbm_rate_reproduces_the_predicted_count(built):
    tp, pp, dp = built
    aaa = dp.filter(pl.col("team") == "AAA").row(0, named=True)
    opp_qb = pp.filter(pl.col("team") == "BBB", pl.col("position") == "QB")
    w = (opp_qb["dropback_share"] * opp_qb["availability_prob"]).to_numpy()
    r_off = float((opp_qb["sack_rate"].to_numpy() * w).sum() / w.sum())
    opp = tp.filter(pl.col("team") == "BBB").row(0, named=True)
    target = 3.0 / (opp["exp_plays"] * opp["exp_pass_rate"])
    assert np.sqrt(aaa["sack_rate"] * r_off) == pytest.approx(target, rel=1e-6)
    bbb = dp.filter(pl.col("team") == "BBB").row(0, named=True)
    assert bbb["int_rate"] == pytest.approx(0.02)  # structural: own EW rate
    assert aaa["ff_rate"] == 1.0 and aaa["ff_per_fum_rec"] == 0.84


def test_incomplete_games_are_dropped():
    one = team_rows().head(1)
    assert pb.build_team_params(one, DISP, CONSTS).height == 0


def test_drivers_add_up_and_are_json(built):
    tp, pp, dp = built
    rules = ScoringRules.from_preset("ppr")
    act = drv.offense_inputs(pp, tp, dp, blend=True)
    neu = drv.offense_inputs(pp, tp, dp, blend=False)
    out = drv.offense_drivers(act, neu, rules).sort("entity_id")
    a = act.sort("entity_id")
    pos = np.asarray(a["position"].to_list(), dtype=object)
    vals = {c: a[c].to_numpy().astype(float) for c in (*drv.ENV, *drv.OPP, *drv.EFF)}
    full = drv.expected_points(vals, pos, rules)
    groups = out.select(list(drv.GROUPS)).to_numpy().sum(axis=1)
    np.testing.assert_allclose(groups + out["reference"].to_numpy(), full, atol=1e-9)
    np.testing.assert_allclose(
        out["availability"].to_numpy(), (a["availability_prob"].to_numpy() - 1) * full
    )
    # the same inputs with and without the DST blend differ only in matchup
    assert out["matchup"].abs().max() > 0
    k = drv.kicker_drivers(pp, tp, rules)
    assert k.height == 2
    js = drv.to_json(pl.concat([out, k], how="vertical_relaxed"))
    payload = json.loads(js["drivers"][0])
    assert set(payload) == {
        "opportunity",
        "efficiency",
        "matchup",
        "environment",
        "availability",
        "reference",
        "method",
    }
