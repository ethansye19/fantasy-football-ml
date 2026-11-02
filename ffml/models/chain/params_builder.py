"""Stage predictions to the engine's parameter frames (:mod:`ffml.sim.params`).

Inputs are the week's feature rows with one ``p_<stage>`` column per stage
(see :mod:`ffml.models.chain.stages`), the dispersion estimates and the
training-window constants of :class:`ffml.models.chain.model.ChainModel`.

Share renormalisation (stage 2 and 4)
    Stage predictions are each player's share *conditional on being active*.
    Within a team the shares are scaled so that their availability-weighted
    sum is one (``sum_i P(active_i) * s_i = 1``; for targets ``1 - b`` with
    ``b`` the unmodeled-receivers bucket, D-057), i.e. over the players the
    simulation treats as available, in expectation. The engine then
    renormalises over the players actually active in each simulation.

Carries
    Stage 2 predicts the share of *all* team carries. The engine allocates
    designed runs by ``carry_share`` and adds QB scrambles and kneel-downs, so
    a non-QB's designed-run share is ``s_i * C / R`` (``C`` expected team
    carries, ``R`` expected designed runs) and a QB's is
    ``(s_i * C - E[scrambles_i] - E[kneels_i]) / R`` (floored at 0).

DST
    Own rates feed the engine's geometric-mean blend with the opposing
    offense. When a LightGBM count model is selected for sacks, interceptions
    or fumble recoveries, the rate passed is ``r*^2 / r_off`` with ``r*`` the
    predicted count over the opponent's expected volume and ``r_off`` the
    opposing offense's own rate, so that the blend reproduces ``r*``.

Kickers
    Distance-bucket attempt shares are the kicker's shrunk lagged mix
    (``opp__fg_att_share_<b>__car_shrunk``, renormalised), make probabilities
    the shrunk band accuracies (``eff__fg_pct_<b>__car_shrunk``); with a
    LightGBM FG% model the band probabilities get one logit shift so that the
    attempt-weighted make rate equals its prediction. Extra points come from
    the team's simulated touchdowns.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from ffml.scoring.statline import FG_BUCKETS
from ffml.sim.params import (
    DEFAULT_FG_ATT_SHARE,
    DEFAULT_FG_MAKE_PROB,
    DstGameParams,
    PlayerGameParams,
    TeamGameParams,
)

SKILL = ("RB", "WR", "TE")
OFFENSE = ("QB", "RB", "WR", "TE")


def _num(df: pl.DataFrame, name: str, default: float = np.nan) -> np.ndarray:
    if name not in df.columns:
        return np.full(df.height, default)
    out = df[name].cast(pl.Float64).fill_null(np.nan).to_numpy()
    return np.where(np.isnan(out), default, out)


def _logit(p: np.ndarray) -> np.ndarray:
    p = np.clip(p, 1e-6, 1 - 1e-6)
    return np.log(p) - np.log1p(-p)


def _expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def complete_games(team_rows: pl.DataFrame) -> pl.DataFrame:
    """Team rows of games that have both teams (the engine needs both)."""
    counts = team_rows.group_by("game_id").len()
    ok = counts.filter(pl.col("len") == 2)["game_id"]
    return team_rows.filter(pl.col("game_id").is_in(ok.implode()))


# --------------------------------------------------------------------------- #
# Team
# --------------------------------------------------------------------------- #


def build_team_params(
    team_rows: pl.DataFrame, disp: dict[str, Any], consts: dict[str, Any]
) -> pl.DataFrame:
    t = complete_games(team_rows)
    week = _num(t, "week", 9)
    sd = np.where(week <= 4, disp["sd_plays"]["early"], disp["sd_plays"]["late"])
    spread = _num(t, "ctx__spread", 0.0)
    total = _num(t, "ctx__total_line", consts.get("total_line", 45.0))
    implied = _num(t, "ctx__implied_team_total", np.nan)
    frame = pl.DataFrame(
        {
            "game_id": t["game_id"],
            "season": t["season"],
            "week": t["week"],
            "team": t["team"],
            "opponent": t["opponent"],
            "home": t["home"].cast(pl.Boolean),
            "exp_plays": np.clip(_num(t, "p_team_plays", 62.6), 45.0, 80.0),
            "sd_plays": sd,
            "exp_pass_rate": np.clip(_num(t, "p_team_pass_rate", 0.61), 0.35, 0.85),
            "pass_rate_conc": np.full(t.height, float(disp["pass_rate_conc"])),
            "exp_off_td": np.maximum(_num(t, "p_team_off_td", 2.4), 0.2),
            "exp_fg_att": np.maximum(_num(t, "p_team_fg_att", 1.9), 0.2),
            "exp_two_pt_att": np.maximum(_num(t, "p_team_two_pt_att", 0.2), 0.0),
            "exp_kneels": np.full(t.height, float(consts.get("kneels", 0.65))),
            "implied_total": pl.Series(implied, dtype=pl.Float64).fill_nan(None),
            "spread_line": -spread,
            "total_line": total,
            "unmodeled_tgt_share": np.full(
                t.height, float(consts.get("unmodeled_tgt_share", 0.035))
            ),
            "unmodeled_catch_rate": np.full(
                t.height, float(consts.get("unmodeled_catch_rate", 0.05))
            ),
            "unmodeled_yards_per_rec": np.full(
                t.height, float(consts.get("unmodeled_yards_per_rec", 9.0))
            ),
        },
        strict=False,
    )
    return TeamGameParams.validate(frame).sort(["game_id", "team"])


# --------------------------------------------------------------------------- #
# Players
# --------------------------------------------------------------------------- #


def renormalise(
    shares: np.ndarray, avail: np.ndarray, team: np.ndarray, target_sum: np.ndarray | float = 1.0
) -> np.ndarray:
    """Scale shares within each team so that ``sum_i avail_i * share_i = target_sum``.

    Teams whose weighted sum is zero keep zeros. ``target_sum`` may be a
    per-row array (the team's value repeated).
    """
    shares = np.maximum(np.nan_to_num(shares, nan=0.0), 0.0)
    target = np.broadcast_to(np.asarray(target_sum, dtype=float), shares.shape)
    out = np.zeros_like(shares)
    for t in np.unique(team):
        m = team == t
        s = float(np.sum(avail[m] * shares[m]))
        if s > 0:
            out[m] = shares[m] * target[m] / s
    return out


def _kicker_columns(
    k: pl.DataFrame, consts: dict[str, Any], lgbm_fg: bool
) -> dict[str, np.ndarray]:
    share_prior = consts.get("fg_share_prior", DEFAULT_FG_ATT_SHARE)
    make_prior = consts.get("fg_make_prior", DEFAULT_FG_MAKE_PROB)
    shares = np.column_stack(
        [_num(k, f"opp__fg_att_share_{b}__car_shrunk", share_prior[b]) for b in FG_BUCKETS]
    )
    shares = np.maximum(shares, 0.0)
    shares = shares / np.maximum(shares.sum(axis=1, keepdims=True), 1e-9)
    makes = np.column_stack(
        [_num(k, f"eff__fg_pct_{b}__car_shrunk", make_prior[b]) for b in FG_BUCKETS]
    )
    makes = np.clip(makes, 0.05, 0.995)
    if lgbm_fg and "p_k_fg_pct" in k.columns:
        target = np.clip(_num(k, "p_k_fg_pct", 0.85), 0.5, 0.99)
        lo, hi = np.full(k.height, -4.0), np.full(k.height, 4.0)
        base = _logit(makes)
        for _ in range(40):  # bisection on one logit shift per kicker
            mid = (lo + hi) / 2
            rate = np.sum(shares * _expit(base + mid[:, None]), axis=1)
            lo = np.where(rate < target, mid, lo)
            hi = np.where(rate >= target, mid, hi)
        makes = np.clip(_expit(base + ((lo + hi) / 2)[:, None]), 0.05, 0.995)
    out = {f"fg_att_share_{b}": shares[:, i] for i, b in enumerate(FG_BUCKETS)}
    out.update({f"fg_make_prob_{b}": makes[:, i] for i, b in enumerate(FG_BUCKETS)})
    out["xp_make_prob"] = np.clip(
        _num(k, "eff__pat_pct__car_shrunk", consts.get("xp_prior", 0.94)), 0.5, 0.999
    )
    return out


def build_player_params(
    players: pl.DataFrame,
    team_params: pl.DataFrame,
    disp: dict[str, Any],
    consts: dict[str, Any],
    choices: dict[str, str] | None = None,
) -> pl.DataFrame:
    """PlayerGameParams for the week's player rows (teams without a team row are dropped)."""
    choices = choices or {}
    tp = team_params.select(
        "game_id", "team", "exp_plays", "exp_pass_rate", "exp_kneels", "unmodeled_tgt_share"
    )
    # deterministic row order: every per-team sum below is then summed in a fixed order
    p = players.join(tp, on=["game_id", "team"], how="inner").sort(
        ["game_id", "team", "position", "player_id"]
    )
    p = p.filter(pl.col("position").is_in(["QB", "RB", "WR", "TE", "K"]))
    pos = np.asarray(p["position"].to_list(), dtype=object)
    team = np.asarray([f"{g}|{t}" for g, t in zip(p["game_id"], p["team"], strict=True)])
    avail = np.clip(_num(p, "p_availability", 0.8), 0.0, 1.0)
    is_skill = np.isin(pos, SKILL)
    is_qb = pos == "QB"
    is_off = np.isin(pos, OFFENSE)

    # stage 2 shares, renormalised over expected-available players
    bucket = _num(p, "unmodeled_tgt_share", 0.035)
    tgt = renormalise(
        np.where(is_skill, _num(p, "p_share_target", 0.0), 0.0), avail, team, 1 - bucket
    )
    dbk = renormalise(np.where(is_qb, _num(p, "p_share_dropback", 0.0), 0.0), avail, team, 1.0)
    car_all = renormalise(np.where(is_off, _num(p, "p_share_carry", 0.0), 0.0), avail, team, 1.0)
    plays = _num(p, "exp_plays", 62.6)
    prate = _num(p, "exp_pass_rate", 0.61)
    designed = plays * (1 - prate)
    dropbacks = plays * prate
    sack = np.where(is_qb, _num(p, "p_eff_sack_rate", 0.065), 0.065)
    scr_db = np.where(is_qb, _num(p, "p_eff_scramble_rate", 0.04), 0.0)  # per dropback
    scr_rate = np.clip(scr_db / np.maximum(1 - sack, 0.5), 0.0, 0.5)  # per non-sack dropback
    exp_scr = dropbacks * dbk * (1 - sack) * scr_rate
    kneels = _num(p, "exp_kneels", 0.65)
    team_scr = np.zeros(p.height)
    for t in np.unique(team):
        m = team == t
        team_scr[m] = np.sum(avail[m] * exp_scr[m])
    carries_total = designed + team_scr + kneels
    raw_design = np.where(
        is_qb,
        np.maximum(car_all * carries_total - exp_scr - kneels * dbk, 0.0),
        car_all * carries_total,
    ) / np.maximum(designed, 1.0)
    car = renormalise(raw_design, avail, team, 1.0)

    # stage 4 touchdown shares (engine weights; same expected-availability scaling)
    ptd = renormalise(np.where(is_skill, _num(p, "p_td_pass_share", 0.0), 0.0), avail, team, 1.0)
    rtd = renormalise(
        np.where(np.isin(pos, ("QB", "RB", "WR")), _num(p, "p_td_rush_share", 0.0), 0.0),
        avail,
        team,
        1.0,
    )
    conc = np.array([disp["share_conc"].get(x, 50.0) for x in pos], dtype=float)
    ypr_shape = np.array([disp["ypr_shape"].get(x, 1.0) for x in pos], dtype=float)
    ypc_shape = np.array([disp["ypc_shape"].get(x, 0.5) for x in pos], dtype=float)
    cols: dict[str, Any] = {
        "entity_id": p["player_id"],
        "game_id": p["game_id"],
        "team": p["team"],
        "position": p["position"],
        "availability_prob": avail,
        "target_share": np.clip(tgt, 0, 1),
        "carry_share": np.clip(car, 0, 1),
        "dropback_share": np.clip(dbk, 0, 1),
        "gl_carry_share": np.clip(
            np.where(np.isin(pos, ("QB", "RB", "WR")), _num(p, "p_share_gl_carry", 0.0), 0.0), 0, 1
        ),
        "rz_target_share": np.clip(
            np.where(is_skill, _num(p, "p_share_rz_target", 0.0), 0.0), 0, 1
        ),
        "pass_td_share": np.clip(ptd, 0, 1),
        "rush_td_share": np.clip(rtd, 0, 1),
        "share_conc": conc,
        "catch_rate": np.clip(
            np.where(is_skill, _num(p, "p_eff_catch_rate", 0.65), 0.6), 0.05, 0.99
        ),
        "yards_per_rec": np.clip(np.where(is_skill, _num(p, "p_eff_ypr", 10.0), 8.0), 1.0, 40.0),
        "yards_per_rec_shape": ypr_shape,
        "yards_per_carry": np.clip(
            np.where(np.isin(pos, ("QB", "RB", "WR")), _num(p, "p_eff_ypc", 4.2), 4.0), 0.5, 15.0
        ),
        "yards_per_carry_shape": ypc_shape,
        "comp_rate": np.clip(np.where(is_qb, _num(p, "p_eff_comp_rate", 0.64), 0.64), 0.05, 0.99),
        "int_rate": np.clip(np.where(is_qb, _num(p, "p_eff_int_rate", 0.023), 0.023), 0.0, 0.2),
        "sack_rate": np.clip(sack, 0.0, 0.3),
        "scramble_rate": np.where(is_qb, scr_rate, 0.0),
        "fumble_rate": np.clip(_num(p, "p_eff_fumble_rate", 0.005), 0.0, 0.1),
        "ret_td_rate": np.zeros(p.height),
    }
    k = p.filter(pl.col("position") == "K")
    kick = _kicker_columns(k, consts, choices.get("k_fg_pct") == "lgbm")
    is_k = pos == "K"
    for name, values in kick.items():
        full = np.full(p.height, np.nan)
        full[is_k] = values
        default = (
            DEFAULT_FG_ATT_SHARE.get(name.removeprefix("fg_att_share_"))
            if name.startswith("fg_att_share_")
            else DEFAULT_FG_MAKE_PROB.get(name.removeprefix("fg_make_prob_"))
            if name.startswith("fg_make_prob_")
            else 0.95
        )
        cols[name] = np.where(np.isnan(full), default, full)
    return PlayerGameParams.validate(pl.DataFrame(cols, strict=False)).sort(
        ["game_id", "team", "position", "entity_id"]
    )


# --------------------------------------------------------------------------- #
# DST
# --------------------------------------------------------------------------- #


def _offense_rates(player_params: pl.DataFrame) -> pl.DataFrame:
    """Per offense: dropback-weighted QB sack and INT rates, touch-weighted fumble rate."""
    p = player_params.with_columns(
        w_db=pl.col("dropback_share") * pl.col("availability_prob"),
        w_touch=(pl.col("dropback_share") + pl.col("target_share") + pl.col("carry_share"))
        * pl.col("availability_prob"),
    )
    p = p.sort(["game_id", "team", "position", "entity_id"])
    return p.group_by(["game_id", "team"], maintain_order=True).agg(
        ((pl.col("sack_rate") * pl.col("w_db")).sum() / pl.col("w_db").sum()).alias("off_sack"),
        ((pl.col("int_rate") * pl.col("w_db")).sum() / pl.col("w_db").sum()).alias("off_int"),
        ((pl.col("fumble_rate") * pl.col("w_touch")).sum() / pl.col("w_touch").sum()).alias(
            "off_fum"
        ),
    )


def build_dst_params(
    dst_rows: pl.DataFrame,
    team_params: pl.DataFrame,
    player_params: pl.DataFrame,
    consts: dict[str, Any],
    choices: dict[str, str] | None = None,
) -> pl.DataFrame:
    choices = choices or {}
    opp_env = team_params.select(
        "game_id",
        pl.col("team").alias("opponent"),
        (pl.col("exp_plays") * pl.col("exp_pass_rate")).alias("opp_db"),
        pl.col("exp_plays").alias("opp_plays"),
    )
    rates = _offense_rates(player_params).rename({"team": "opponent"})
    d = dst_rows.join(opp_env, on=["game_id", "opponent"], how="inner").join(
        rates, on=["game_id", "opponent"], how="left"
    )
    lg = consts.get("dst_league", {})
    own_sack = _num(d, "def__dst_sack_rate__ew", lg.get("sack_rate", 0.065))
    own_int = _num(d, "def__dst_int_rate__ew", lg.get("int_rate", 0.023))
    tpp = float(consts.get("touches_per_play", 1.35))
    own_fum = _num(d, "def__dst_fumble_recoveries__ew", np.nan) / np.maximum(
        _num(d, "def__plays_faced__ew", np.nan) * tpp, 1.0
    )
    own_fum = np.where(np.isnan(own_fum), lg.get("fum_rate", 0.0065), own_fum)
    opp_db = _num(d, "opp_db", 38.0)
    att = opp_db * 0.9
    touches = _num(d, "opp_plays", 62.6) * tpp

    def implied_rate(stage: str, volume: np.ndarray, off_rate: np.ndarray, own: np.ndarray):
        if choices.get(stage) != "lgbm" or f"p_{stage}" not in d.columns:
            return own
        target = _num(d, f"p_{stage}", np.nan) / np.maximum(volume, 1.0)
        off = np.where(np.isnan(off_rate) | (off_rate <= 0), target, off_rate)
        out = target**2 / np.maximum(off, 1e-4)
        return np.where(np.isnan(out), own, out)

    sack_rate = implied_rate("dst_sacks", opp_db, _num(d, "off_sack"), own_sack)
    int_rate = implied_rate("dst_int", att, _num(d, "off_int"), own_int)
    fum_rate = implied_rate("dst_fum_rec", touches, _num(d, "off_fum"), own_fum)
    takeaways = _num(d, "p_dst_int", 0.79) + _num(d, "p_dst_fum_rec", 0.52)
    frame = pl.DataFrame(
        {
            "team": d["team"],
            "game_id": d["game_id"],
            "sack_rate": np.clip(sack_rate, 0.001, 0.5),
            "int_rate": np.clip(int_rate, 0.0005, 0.3),
            "fum_rec_rate": np.clip(fum_rate, 0.0001, 0.1),
            "def_td_rate": np.clip(
                _num(d, "p_dst_def_td", 0.11) / np.maximum(takeaways, 0.3), 0, 0.5
            ),
            "st_td_rate": np.clip(_num(d, "p_dst_ret_td", 0.034), 0, 1),
            "safety_rate": np.clip(_num(d, "p_dst_safety", 0.03), 0, 1),
            "block_rate": np.clip(_num(d, "p_dst_blocks", 0.075), 0, 1),
            "ff_rate": np.clip(_num(d, "p_dst_ff", 0.82), 0.02, 5),
            "ff_per_fum_rec": np.full(d.height, float(consts.get("ff_per_fum_rec", 0.84))),
        }
    )
    return DstGameParams.validate(frame).sort(["game_id", "team"])


__all__ = [
    "build_dst_params",
    "build_player_params",
    "build_team_params",
    "complete_games",
    "renormalise",
]
