"""Monte Carlo stat-line engine (MODELING.md section 5, ARCHITECTURE.md section 4).

:func:`simulate` turns the three parameter frames of :mod:`ffml.sim.params`
into ``n_sims`` correlated stat lines per player and team defense, in the
vocabulary of :mod:`ffml.scoring.statline`. Everything is vectorised with
NumPy over ``(team, sim)`` and ``(player, sim)`` arrays; teams are laid out as
padded ``(team, sim, slot)`` blocks for the share draws.

Generative steps, per game and simulation:

1. Game: total ``T ~ N(total_line, total_sd)`` and margin
   ``M ~ N(spread, margin_sd)`` (independent draws, shared by both teams);
   team points ``(T +/- M) / 2`` floored at 0; the standardised total and
   margin deviations are the game-script signals.
2. Team: plays ``~ N(exp_plays, sd_plays)`` correlated with the total
   deviation (:data:`PLAYS_TOTAL_CORR`), truncated to 40-90; pass rate
   ``~ Beta`` around ``exp_pass_rate`` shifted on the logit scale against the
   margin deviation (trailing teams pass more, :data:`SCRIPT_PASS_COEF`);
   dropbacks ``~ Binomial(plays, pass rate)``, the rest are carries.
   Offensive touchdowns have mean ``exp_off_td * points / implied_total``
   and Binomial noise with ``n = ceil(2 * mean)`` (variance about half the
   mean, because the drawn points already carry most of the variance); each
   touchdown is a passing one with probability equal to the drawn pass rate.
   Field-goal attempts ``~ Poisson(exp_fg_att * (0.5 + 0.5 * points /
   implied))``; two-point attempts ``~ Binomial(touchdowns,
   exp_two_pt_att / exp_off_td)``, converted with :data:`TWO_PT_SUCCESS`.
3. Availability: ``active ~ Bernoulli(availability_prob)``; expected shares
   are renormalised over the active players of the team in every simulation
   (full absorption).
4. Opportunity: per-player shares ``g_i / sum g`` with
   ``g_i ~ Gamma(conc_i * p_i, 1 / conc_i)`` (a Dirichlet when all
   concentrations are equal; per-position concentration keeps the mean
   share), then ``Multinomial`` allocation of team dropbacks (QBs), targets
   (team pass attempts) and carries. Shares sum to 1 over active players,
   so player targets and carries add up to the team totals exactly.
5. Efficiency: sacks ``~ Binomial(dropbacks, sack_rate)``, attempts are the
   rest; completions ``~ Binomial(attempts, comp_rate)``; interceptions
   ``~ Binomial(attempts, int_rate)`` capped at the incompletions;
   receptions ``~ Binomial(targets, catch_rate)``; yardage is a sum of
   per-touch Gamma draws (``Gamma(n * shape, mean / shape)``), with a team
   passing-efficiency factor shared by the QB's yards per completion and the
   receivers' yards per reception (:data:`PASS_EFF_SD`) and a team rushing
   factor (:data:`RUSH_EFF_SD`).
6. Touchdowns: team passing touchdowns go to receptions with weights
   ``receptions_i * pass_td_share_i / (target_share_i * catch_rate_i)`` and
   rushing touchdowns to carries with weights
   ``carries_i * rush_td_share_i / carry_share_i`` (expected allocation equals
   the TD shares among active players; a player never scores more
   touchdowns than touches, any overflow is dropped). The QBs receive the
   team's realised passing touchdowns in proportion to their attempts.
7. Turnovers and bonuses: lost fumbles ``~ Binomial(touches, fumble_rate)``;
   converted two-pointers go to a scorer by ``two_pt_rate``; a conversion by
   a non-QB is a pass with probability ``target_share / (target_share +
   carry_share)`` and then also credits a QB (as in
   ``fact_player_game.two_pt_conversions``).
8. Kickers and DST: field-goal attempts split across distance buckets by a
   Multinomial, makes are Binomial per bucket; extra-point attempts are the
   touchdowns not followed by a two-point try plus the team's own defensive
   and return touchdowns. One kicker per team per simulation: the active
   kicker with the highest availability. A defense's sacks, interceptions
   and fumble recoveries are the opposing offense's simulated sacks taken,
   interceptions and lost fumbles; defensive touchdowns ``~ Binomial
   (takeaways, def_td_rate)``; return touchdowns ``~ Poisson(st_td_rate)``
   (credited to individual returners by ``ret_td_rate``), safeties and blocked
   kicks Poisson; points allowed is the opponent's realised score
   (touchdowns, kicks, conversions, its defense's touchdowns and safeties);
   yards allowed is the opponent's simulated passing plus rushing yards net
   of sack yardage.

Correlations that follow: QB with his receivers (shared attempts, TDs and
passing efficiency), teammates negatively (shares), opponents through the
shared total, a defense against the opposing offense (same events).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import numpy as np
import polars as pl

from ffml.config import get_settings
from ffml.scoring.statline import FG_BUCKETS, SIM_STAT_DTYPE, STAT_COLUMNS
from ffml.sim.params import (
    DEFAULT_FG_ATT_SHARE,
    DEFAULT_FG_MAKE_PROB,
    FG_ATT_SHARE_COLUMNS,
    FG_MAKE_PROB_COLUMNS,
    OFFENSE_POSITIONS,
    DstGameParams,
    PlayerGameParams,
    TeamGameParams,
)

log = logging.getLogger(__name__)

PLAYS_MIN, PLAYS_MAX = 40, 90
PLAYS_TOTAL_CORR = 0.3
SCRIPT_PASS_COEF = 0.25
PASS_EFF_SD = 0.12
RUSH_EFF_SD = 0.10
TWO_PT_SUCCESS = 0.48
SACK_YARDS = 7.0
DEFAULT_YDS_ALLOWED = (330.0, 70.0)
DEFAULT_DST = {
    "sack_rate": 0.065,
    "int_rate": 0.023,
    "def_td_rate": 0.09,
    "st_td_rate": 0.04,
    "safety_rate": 0.02,
    "block_rate": 0.05,
}

KEY_OUTPUT_COLUMNS: tuple[str, ...] = (
    "entity_id",
    "season",
    "week",
    "game_id",
    "team",
    "position",
    "sim",
    "active",
)
TEAM_SIM_COLUMNS: tuple[str, ...] = (
    "game_id",
    "season",
    "week",
    "team",
    "opponent",
    "sim",
    "points_drawn",
    "points",
    "plays",
    "dropbacks",
    "pass_att",
    "rush_att",
    "sacks",
    "off_td",
    "pass_td",
    "rush_td",
    "fg_att",
    "fg_made",
    "two_pt_att",
    "two_pt_conv",
    "pass_yds",
    "rush_yds",
)


@dataclass(frozen=True)
class SimulationResult:
    """Output of :func:`simulate_full`."""

    players: pl.DataFrame  # STAT_COLUMNS per player and DST per simulation
    teams: pl.DataFrame  # team-level draws per simulation (TEAM_SIM_COLUMNS)
    seed: int
    n_sims: int


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _logit(p: np.ndarray) -> np.ndarray:
    return np.log(p) - np.log1p(-p)


def _expit(x: np.ndarray) -> np.ndarray:
    return 1.0 / (1.0 + np.exp(-x))


def _blend(own: np.ndarray, other: np.ndarray) -> np.ndarray:
    """Geometric mean of two rates where ``other`` is not NaN, else ``own``."""
    return np.where(np.isnan(other), own, np.sqrt(own * np.nan_to_num(other)))


def _gamma_sum(
    rng: np.random.Generator, n: np.ndarray, shape: np.ndarray, mean: np.ndarray
) -> np.ndarray:
    """Sum of ``n`` iid Gamma draws with the given per-touch mean and shape (0 when n = 0)."""
    k = np.broadcast_to(shape, n.shape)
    scale = np.maximum(np.broadcast_to(mean, n.shape), 1e-6) / k
    return rng.gamma(n * k, scale)


class _Layout:
    """Padded ``(team, slot)`` layout of a subset of players."""

    def __init__(self, team_index: np.ndarray, n_teams: int) -> None:
        self.team = team_index.astype(np.int64)
        self.n_teams = n_teams
        self.slot = np.zeros(team_index.size, dtype=np.int64)
        counts = np.zeros(n_teams, dtype=np.int64)
        for i, t in enumerate(self.team):
            self.slot[i] = counts[t]
            counts[t] += 1
        self.counts = counts
        self.width = int(counts.max()) if counts.size and counts.max() > 0 else 1

    def gather(self, values: np.ndarray, n_sims: int) -> np.ndarray:
        """``(player, sim)`` or ``(player,)`` values into ``(team, sim, slot)``."""
        out = np.zeros((self.n_teams, n_sims, self.width), dtype=np.float64)
        if values.ndim == 1:
            values = np.broadcast_to(values[:, None], (values.size, n_sims))
        out[self.team, :, self.slot] = values
        return out

    def scatter(self, block: np.ndarray) -> np.ndarray:
        """``(team, sim, slot)`` back to ``(player, sim)``."""
        return block[self.team, :, self.slot]

    def team_sum(self, values: np.ndarray) -> np.ndarray:
        """Sum ``(player, sim)`` values per team into ``(team, sim)``."""
        out = np.zeros((self.n_teams, values.shape[1]), dtype=np.float64)
        np.add.at(out, self.team, values)
        return out


def _normalise(weights: np.ndarray) -> np.ndarray:
    """Normalise the last axis to sum 1; all-zero rows stay zero."""
    total = weights.sum(axis=-1, keepdims=True)
    return np.divide(weights, total, out=np.zeros_like(weights), where=total > 0)


def _allocate(rng: np.random.Generator, n: np.ndarray, weights: np.ndarray) -> np.ndarray:
    """Multinomial allocation of ``n`` (team, sim) over ``weights`` (team, sim, slot).

    Rows whose weights are all zero send their count to a discarded sink.
    """
    p = _normalise(weights)
    sink = np.clip(1.0 - p.sum(axis=-1, keepdims=True), 0.0, 1.0)
    pvals = np.concatenate([p, sink], axis=-1)
    pvals /= pvals.sum(axis=-1, keepdims=True)
    counts = rng.multinomial(n.astype(np.int64), pvals)
    return counts[..., :-1].astype(np.float64)


def _dirichlet_shares(
    rng: np.random.Generator, base: np.ndarray, conc: np.ndarray, active: np.ndarray
) -> np.ndarray:
    """Share draws (team, sim, slot) around ``base`` renormalised over active slots.

    ``g_i ~ Gamma(conc_i * p_i, 1 / conc_i)`` with ``p`` the active-normalised
    expected share, then ``g / sum(g)``: shares sum to 1 over active players
    with at least some expected share, and to 0 when none is active.
    """
    p = _normalise(base * active)
    g = rng.gamma(conc * p, 1.0 / conc)
    return _normalise(g)


# --------------------------------------------------------------------------- #
# Engine
# --------------------------------------------------------------------------- #


def _game_structure(tp: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, list[str]]:
    """Index pairs (first row, second row) per game and the opponent index per team row."""
    games = tp["game_id"].to_list()
    teams = tp["team"].to_list()
    opponents = tp["opponent"].to_list()
    by_game: dict[str, list[int]] = {}
    for i, g in enumerate(games):
        by_game.setdefault(g, []).append(i)
    game_ids = sorted(by_game)
    pairs = np.zeros((len(game_ids), 2), dtype=np.int64)
    opp = np.zeros(len(games), dtype=np.int64)
    for gi, g in enumerate(game_ids):
        rows = by_game[g]
        if len(rows) != 2:
            raise ValueError(
                f"TeamGameParams: game {g} needs exactly two team rows, has {len(rows)}"
            )
        a, b = rows
        if opponents[a] != teams[b] or opponents[b] != teams[a]:
            raise ValueError(f"TeamGameParams: game {g} team/opponent fields disagree")
        pairs[gi] = (a, b)
        opp[a], opp[b] = b, a
    return pairs, opp, game_ids


def simulate(
    team_params: pl.DataFrame,
    player_params: pl.DataFrame,
    dst_params: pl.DataFrame | None = None,
    n_sims: int | None = None,
    seed: int | None = None,
) -> pl.DataFrame:
    """Simulated stat lines: one row per player (and DST) per simulation.

    Columns: ``entity_id, season, week, game_id, team, position, sim (Int16),
    active (Boolean)`` and every column of
    :data:`ffml.scoring.statline.STAT_COLUMNS` as Int16. DST rows
    (``entity_id`` = team, ``position`` = DST) are emitted for the teams in
    ``dst_params``. Defaults: ``n_sims`` and ``seed`` from the settings.
    """
    return simulate_full(team_params, player_params, dst_params, n_sims=n_sims, seed=seed).players


def simulate_full(
    team_params: pl.DataFrame,
    player_params: pl.DataFrame,
    dst_params: pl.DataFrame | None = None,
    n_sims: int | None = None,
    seed: int | None = None,
) -> SimulationResult:
    """Like :func:`simulate` but also returns the team-level draws."""
    settings = get_settings()
    S = int(n_sims if n_sims is not None else settings.n_sims)
    seed = int(seed if seed is not None else settings.random_seed)
    if S < 1 or S > np.iinfo(np.int16).max:
        raise ValueError("n_sims must be between 1 and 32767")
    rng = np.random.default_rng(seed)

    tp = TeamGameParams.validate(team_params).sort(["game_id", "team"])
    pp = PlayerGameParams.validate(player_params).sort(["game_id", "team", "position", "entity_id"])
    dp = DstGameParams.validate(dst_params).sort(["game_id", "team"])
    T = tp.height
    if T == 0:
        raise ValueError("TeamGameParams is empty")
    pairs, opp, _ = _game_structure(tp)
    team_key = {(g, t): i for i, (g, t) in enumerate(zip(tp["game_id"], tp["team"], strict=True))}

    def team_index(frame: pl.DataFrame, label: str) -> np.ndarray:
        idx = []
        for g, t in zip(frame["game_id"], frame["team"], strict=True):
            if (g, t) not in team_key:
                raise ValueError(f"{label}: ({g}, {t}) has no TeamGameParams row")
            idx.append(team_key[(g, t)])
        return np.asarray(idx, dtype=np.int64)

    p_team = team_index(pp, "PlayerGameParams")
    d_team = team_index(dp, "DstGameParams")

    def tcol(name: str) -> np.ndarray:
        return tp[name].to_numpy().astype(np.float64)

    # ---- 1. game level --------------------------------------------------- #
    spread = tcol("spread_line")
    total_line = tcol("total_line")
    margin_sd = tcol("margin_sd")
    total_sd = tcol("total_sd")
    a_idx, b_idx = pairs[:, 0], pairs[:, 1]
    g_margin = (spread[a_idx] - spread[b_idx]) / 2.0  # expected margin of row a
    g_total = (total_line[a_idx] + total_line[b_idx]) / 2.0
    g_msd = (margin_sd[a_idx] + margin_sd[b_idx]) / 2.0
    g_tsd = (total_sd[a_idx] + total_sd[b_idx]) / 2.0
    G = pairs.shape[0]
    z_total_g = rng.standard_normal((G, S))
    z_margin_g = rng.standard_normal((G, S))
    total = g_total[:, None] + g_tsd[:, None] * z_total_g
    margin_a = g_margin[:, None] + g_msd[:, None] * z_margin_g
    points_drawn = np.zeros((T, S))
    z_total = np.zeros((T, S))
    z_margin = np.zeros((T, S))
    exp_margin = np.zeros(T)
    points_drawn[a_idx] = np.maximum((total + margin_a) / 2.0, 0.0)
    points_drawn[b_idx] = np.maximum((total - margin_a) / 2.0, 0.0)
    z_total[a_idx] = z_total_g
    z_total[b_idx] = z_total_g
    z_margin[a_idx] = z_margin_g
    z_margin[b_idx] = -z_margin_g
    exp_margin[a_idx] = g_margin
    exp_margin[b_idx] = -g_margin
    team_total = np.zeros(T)
    team_total[a_idx] = g_total
    team_total[b_idx] = g_total
    implied = tp["implied_total"].to_numpy().astype(np.float64)
    implied = np.where(np.isnan(implied), (team_total + exp_margin) / 2.0, implied)
    implied = np.maximum(implied, 1.0)
    ratio = points_drawn / implied[:, None]

    # ---- 2. team level --------------------------------------------------- #
    eps = rng.standard_normal((T, S))
    rho = PLAYS_TOTAL_CORR
    plays = tcol("exp_plays")[:, None] + tcol("sd_plays")[:, None] * (
        rho * z_total + np.sqrt(1 - rho**2) * eps
    )
    plays = np.rint(np.clip(plays, PLAYS_MIN, PLAYS_MAX)).astype(np.int64)
    mu = _expit(
        _logit(np.clip(tcol("exp_pass_rate"), 0.02, 0.98))[:, None] - SCRIPT_PASS_COEF * z_margin
    )
    conc = tcol("pass_rate_conc")[:, None]
    pass_rate = rng.beta(mu * conc, (1.0 - mu) * conc)
    dropbacks = rng.binomial(plays, pass_rate)
    rush_team = plays - dropbacks

    td_mean = tcol("exp_off_td")[:, None] * ratio
    td_n = np.ceil(2.0 * td_mean).astype(np.int64)
    td_p = np.divide(td_mean, td_n, out=np.zeros_like(td_mean), where=td_n > 0)
    off_td_drawn = rng.binomial(td_n, np.clip(td_p, 0.0, 1.0))
    pass_td_drawn = rng.binomial(off_td_drawn, pass_rate)
    rush_td_drawn = off_td_drawn - pass_td_drawn
    fg_att = rng.poisson(tcol("exp_fg_att")[:, None] * (0.5 + 0.5 * ratio))
    two_pt_rate = np.clip(tcol("exp_two_pt_att") / np.maximum(tcol("exp_off_td"), 0.1), 0.0, 0.5)

    # ---- 3. availability ------------------------------------------------- #
    P = pp.height
    position = np.asarray(pp["position"].to_list(), dtype=object)

    def pcol(name: str) -> np.ndarray:
        return pp[name].to_numpy().astype(np.float64)

    active = rng.random((P, S)) < pcol("availability_prob")[:, None]
    is_off = np.isin(position, OFFENSE_POSITIONS)
    off = np.flatnonzero(is_off)
    kick = np.flatnonzero(position == "K")
    lay = _Layout(p_team[off], T)
    act_o = active[off].astype(np.float64)
    act_blk = lay.gather(act_o, S)
    conc_blk = lay.gather(pcol("share_conc")[off], S)
    conc_blk = np.where(conc_blk > 0, conc_blk, 1.0)
    has_offense = lay.counts > 0

    def ocol(name: str) -> np.ndarray:
        return pcol(name)[off]

    opp_of_player = opp[p_team[off]]

    # DST parameter arrays by team row (NaN when the team has no DST row)
    def dst_arr(name: str, default: float | None = None) -> np.ndarray:
        out = np.full(T, np.nan)
        if dp.height:
            out[d_team] = dp[name].to_numpy().astype(np.float64)
        if default is not None:
            out = np.where(np.isnan(out), default, out)
        return out

    dst_sack = dst_arr("sack_rate")
    dst_int = dst_arr("int_rate")
    dst_fum = dst_arr("fum_rec_rate")

    # ---- 4/5. opportunity and efficiency --------------------------------- #
    db_shares = _dirichlet_shares(rng, lay.gather(ocol("dropback_share"), S), conc_blk, act_blk)
    db = lay.scatter(_allocate(rng, dropbacks, db_shares))
    sack_rate = _blend(ocol("sack_rate"), dst_sack[opp_of_player])
    sacks = rng.binomial(db.astype(np.int64), sack_rate[:, None]).astype(np.float64)
    att = db - sacks
    cmp_ = rng.binomial(att.astype(np.int64), ocol("comp_rate")[:, None]).astype(np.float64)
    int_rate = _blend(ocol("int_rate"), dst_int[opp_of_player])
    ints = np.minimum(rng.binomial(att.astype(np.int64), int_rate[:, None]), att - cmp_)
    pass_eff = np.exp(PASS_EFF_SD * rng.standard_normal((T, S)) - PASS_EFF_SD**2 / 2)
    rush_eff = np.exp(RUSH_EFF_SD * rng.standard_normal((T, S)) - RUSH_EFF_SD**2 / 2)
    team_of = p_team[off]
    pass_yds = _gamma_sum(
        rng,
        cmp_,
        ocol("yards_per_comp_shape")[:, None],
        ocol("yards_per_comp")[:, None] * pass_eff[team_of],
    )

    att_team = lay.team_sum(att)
    tgt_base = lay.gather(ocol("target_share"), S)
    tgt_shares = _dirichlet_shares(rng, tgt_base, conc_blk, act_blk)
    targets = lay.scatter(_allocate(rng, att_team, tgt_shares))
    rec = rng.binomial(targets.astype(np.int64), ocol("catch_rate")[:, None]).astype(np.float64)
    rec_yds = _gamma_sum(
        rng,
        rec,
        ocol("yards_per_rec_shape")[:, None],
        ocol("yards_per_rec")[:, None] * pass_eff[team_of],
    )

    car_base = lay.gather(ocol("carry_share"), S)
    car_shares = _dirichlet_shares(rng, car_base, conc_blk, act_blk)
    carries = lay.scatter(_allocate(rng, rush_team, car_shares))
    rush_yds = _gamma_sum(
        rng,
        carries,
        ocol("yards_per_carry_shape")[:, None],
        ocol("yards_per_carry")[:, None] * rush_eff[team_of],
    )

    # ---- 6. touchdowns --------------------------------------------------- #
    def td_weight(primary: str, fallback: str, opportunity: str) -> np.ndarray:
        w = pp.select(pl.coalesce(primary, fallback, opportunity)).to_series()
        return w.to_numpy().astype(np.float64)[off]

    exp_rec = ocol("target_share") * ocol("catch_rate")
    lam = np.divide(
        td_weight("pass_td_share", "rz_target_share", "target_share"),
        exp_rec,
        out=np.zeros(off.size),
        where=exp_rec > 0,
    )
    rec_td = lay.scatter(_allocate(rng, pass_td_drawn, lay.gather(rec * lam[:, None], S)))
    rec_td = np.minimum(rec_td, rec)
    car_share = ocol("carry_share")
    mu_r = np.divide(
        td_weight("rush_td_share", "gl_carry_share", "carry_share"),
        car_share,
        out=np.zeros(off.size),
        where=car_share > 0,
    )
    rush_td = lay.scatter(_allocate(rng, rush_td_drawn, lay.gather(carries * mu_r[:, None], S)))
    rush_td = np.minimum(rush_td, carries)
    pass_td_team = lay.team_sum(rec_td)
    pass_td = lay.scatter(_allocate(rng, pass_td_team, lay.gather(att, S)))
    rush_td_team = lay.team_sum(rush_td)
    off_td = np.where(has_offense[:, None], pass_td_team + rush_td_team, off_td_drawn)

    # ---- 7. fumbles ------------------------------------------------------ #
    touches = db + carries + rec
    fum_rate = _blend(ocol("fumble_rate"), dst_fum[opp_of_player])
    fum_lost = rng.binomial(touches.astype(np.int64), fum_rate[:, None]).astype(np.float64)

    # ---- 8a. defense events (a defense faces the opponent's offense) ------ #
    sacks_off = lay.team_sum(sacks)
    ints_off = lay.team_sum(ints)
    fum_off = lay.team_sum(fum_lost)
    no_off = ~has_offense
    if no_off.any():  # offenses without player rows: draw their events at team level
        rows = np.flatnonzero(no_off)
        opp_rows = opp[rows]
        s_rate = np.where(
            np.isnan(dst_sack[opp_rows]), DEFAULT_DST["sack_rate"], dst_sack[opp_rows]
        )
        i_rate = np.where(np.isnan(dst_int[opp_rows]), DEFAULT_DST["int_rate"], dst_int[opp_rows])
        sacks_off[rows] = rng.binomial(dropbacks[rows], s_rate[:, None])
        ints_off[rows] = rng.binomial(
            dropbacks[rows] - sacks_off[rows].astype(np.int64), i_rate[:, None]
        )
        fum_off[rows] = rng.poisson(0.6, size=(rows.size, S))
    def_sacks = sacks_off[opp]
    def_int = ints_off[opp]
    def_fum_rec = fum_off[opp]
    def_td = rng.binomial(
        (def_int + def_fum_rec).astype(np.int64),
        dst_arr("def_td_rate", DEFAULT_DST["def_td_rate"])[:, None],
    ).astype(np.float64)
    st_td = rng.poisson(dst_arr("st_td_rate", DEFAULT_DST["st_td_rate"])[:, None], size=(T, S))
    safeties = rng.poisson(dst_arr("safety_rate", DEFAULT_DST["safety_rate"])[:, None], size=(T, S))
    blocks = rng.poisson(dst_arr("block_rate", DEFAULT_DST["block_rate"])[:, None], size=(T, S))

    # return touchdowns credited to returners (the rest to unmodelled players)
    ret_rate = ocol("ret_td_rate")
    st_rate = dst_arr("st_td_rate", DEFAULT_DST["st_td_rate"])
    ret_sum = lay.team_sum(np.broadcast_to(ret_rate[:, None], (off.size, 1)).copy())[:, 0]
    denom = np.maximum(np.maximum(st_rate, ret_sum), 1e-12)
    ret_p = lay.gather(ret_rate / denom[team_of], S) * act_blk
    ret_pvals = np.concatenate([ret_p, np.clip(1 - ret_p.sum(-1, keepdims=True), 0, 1)], axis=-1)
    ret_pvals /= ret_pvals.sum(-1, keepdims=True)
    ret_td = lay.scatter(rng.multinomial(st_td, ret_pvals)[..., :-1].astype(np.float64))

    # ---- 7b. two-point conversions --------------------------------------- #
    two_att = rng.binomial(off_td.astype(np.int64), two_pt_rate[:, None])
    two_conv = rng.binomial(two_att, TWO_PT_SUCCESS).astype(np.float64)
    scorer_w = pp.select(
        pl.coalesce("two_pt_rate", pl.col("target_share") + pl.col("carry_share"))
    ).to_series()
    scorer_w = scorer_w.to_numpy().astype(np.float64)[off]
    scorers = lay.scatter(_allocate(rng, two_conv, lay.gather(scorer_w, S) * act_blk))
    tshare, cshare = ocol("target_share"), ocol("carry_share")
    pass_prob = np.divide(
        tshare, tshare + cshare, out=np.full(off.size, 0.5), where=(tshare + cshare) > 0
    )
    pass_prob = np.where(position[off] == "QB", 0.0, pass_prob)
    pass_conv = rng.binomial(scorers.astype(np.int64), pass_prob[:, None]).astype(np.float64)
    qb_credit = lay.scatter(_allocate(rng, lay.team_sum(pass_conv), lay.gather(att, S)))
    two_pt = scorers + qb_credit

    # ---- 8b. kickers ----------------------------------------------------- #
    nk = kick.size
    k_team = p_team[kick]
    k_active = active[kick]
    k_avail = pcol("availability_prob")[kick]
    chosen = np.full((T, S), -1, dtype=np.int64)
    for t in np.unique(k_team):
        cands = np.flatnonzero(k_team == t)
        order = cands[np.argsort(-k_avail[cands], kind="stable")]
        for k in order[::-1]:  # highest availability last so it wins
            chosen[t] = np.where(k_active[k], k, chosen[t])
    share_rows = np.vstack(
        [
            pp.select(FG_ATT_SHARE_COLUMNS).to_numpy()[kick],
            [DEFAULT_FG_ATT_SHARE[b] for b in FG_BUCKETS],
        ]
    ).astype(np.float64)
    prob_rows = np.vstack(
        [
            pp.select(FG_MAKE_PROB_COLUMNS).to_numpy()[kick],
            [DEFAULT_FG_MAKE_PROB[b] for b in FG_BUCKETS],
        ]
    ).astype(np.float64)
    xp_rows = np.append(pcol("xp_make_prob")[kick], 0.95)
    pick = np.where(chosen >= 0, chosen, nk)
    fg_att_b = rng.multinomial(fg_att, _normalise(share_rows[pick]))
    fg_made_b = rng.binomial(fg_att_b, prob_rows[pick])
    fg_made_team = fg_made_b.sum(axis=-1)
    xp_att = np.maximum(off_td - two_att, 0) + def_td + st_td
    xp_made = rng.binomial(xp_att.astype(np.int64), xp_rows[pick]).astype(np.float64)

    # ---- points and yards ------------------------------------------------ #
    points = (
        6.0 * (off_td + def_td + st_td)
        + xp_made
        + 2.0 * two_conv
        + 3.0 * fg_made_team
        + 2.0 * safeties
    )
    off_yds = lay.team_sum(pass_yds) + lay.team_sum(rush_yds) - SACK_YARDS * sacks_off
    if no_off.any():
        off_yds[no_off] = rng.normal(*DEFAULT_YDS_ALLOWED, size=(int(no_off.sum()), S))
    yds_allowed = np.rint(off_yds[opp])
    target_mean = dst_arr("yds_allowed_mean")
    target_sd = dst_arr("yds_allowed_sd")
    for t in np.flatnonzero(~np.isnan(target_mean)):
        y = yds_allowed[t]
        centre = y - y.mean()
        sd = y.std()
        if not np.isnan(target_sd[t]) and sd > 0:
            centre = centre * (target_sd[t] / sd)
        yds_allowed[t] = np.rint(centre + target_mean[t])

    # ---- assemble -------------------------------------------------------- #
    stats_p: dict[str, np.ndarray] = {c: np.zeros((P, S)) for c in STAT_COLUMNS}

    def put(name: str, values: np.ndarray) -> None:
        stats_p[name][off] = values

    put("pass_att", att)
    put("pass_cmp", cmp_)
    put("pass_yds", np.rint(pass_yds))
    put("pass_td", pass_td)
    put("pass_int", ints)
    put("rush_att", carries)
    put("rush_yds", np.rint(rush_yds))
    put("rush_td", rush_td)
    put("targets", targets)
    put("rec", rec)
    put("rec_yds", np.rint(rec_yds))
    put("rec_td", rec_td)
    put("two_pt", two_pt)
    put("fum_lost", fum_lost)
    put("ret_td", ret_td)
    if nk:
        sims = np.arange(S)
        for j, k in enumerate(kick):
            t = k_team[j]
            mine = chosen[t] == j
            for b_i, b in enumerate(FG_BUCKETS):
                stats_p[f"fg_made_{b}"][k] = np.where(mine, fg_made_b[t, sims, b_i], 0)
            stats_p["fg_missed"][k] = np.where(mine, fg_att[t] - fg_made_team[t], 0)
            stats_p["xp_made"][k] = np.where(mine, xp_made[t], 0)
            stats_p["xp_missed"][k] = np.where(mine, xp_att[t] - xp_made[t], 0)

    D = dp.height
    stats_d: dict[str, np.ndarray] = {c: np.zeros((D, S)) for c in STAT_COLUMNS}
    if D:
        stats_d["sacks"] = def_sacks[d_team]
        stats_d["def_int"] = def_int[d_team]
        stats_d["fum_rec"] = def_fum_rec[d_team]
        stats_d["def_td"] = def_td[d_team]
        stats_d["st_td"] = st_td[d_team].astype(np.float64)
        stats_d["safeties"] = safeties[d_team].astype(np.float64)
        stats_d["blocked_kicks"] = blocks[d_team].astype(np.float64)
        stats_d["pts_allowed"] = points[opp][d_team]
        stats_d["yds_allowed"] = yds_allowed[d_team]

    season = tp["season"].to_numpy()
    week = tp["week"].to_numpy()
    players_frame = _long_frame(
        entity=pp["entity_id"],
        position=pp["position"],
        team=pp["team"],
        game=pp["game_id"],
        season=season[p_team],
        week=week[p_team],
        active=active,
        stats=stats_p,
        n_sims=S,
    )
    frames = [players_frame]
    if D:
        frames.append(
            _long_frame(
                entity=dp["team"],
                position=pl.Series(["DST"] * D),
                team=dp["team"],
                game=dp["game_id"],
                season=season[d_team],
                week=week[d_team],
                active=np.ones((D, S), dtype=bool),
                stats=stats_d,
                n_sims=S,
            )
        )
    out = pl.concat(frames, how="vertical")

    teams = _team_frame(
        tp,
        S,
        points_drawn=points_drawn,
        points=points,
        plays=plays,
        dropbacks=dropbacks,
        pass_att=att_team,
        rush_att=rush_team,
        sacks=sacks_off,
        off_td=off_td,
        pass_td=np.where(has_offense[:, None], pass_td_team, pass_td_drawn),
        rush_td=np.where(has_offense[:, None], rush_td_team, rush_td_drawn),
        fg_att=fg_att,
        fg_made=fg_made_team,
        two_pt_att=two_att,
        two_pt_conv=two_conv,
        pass_yds=lay.team_sum(pass_yds),
        rush_yds=lay.team_sum(rush_yds),
    )
    return SimulationResult(players=out, teams=teams, seed=seed, n_sims=S)


def _long_frame(
    *,
    entity: pl.Series,
    position: pl.Series,
    team: pl.Series,
    game: pl.Series,
    season: np.ndarray,
    week: np.ndarray,
    active: np.ndarray,
    stats: dict[str, np.ndarray],
    n_sims: int,
) -> pl.DataFrame:
    n = entity.len()
    rep = pl.Series(np.repeat(np.arange(n, dtype=np.int64), n_sims))
    cols: dict[str, pl.Series] = {
        "entity_id": entity.gather(rep),
        "season": pl.Series(np.repeat(season.astype(np.int32), n_sims)),
        "week": pl.Series(np.repeat(week.astype(np.int32), n_sims)),
        "game_id": game.gather(rep),
        "team": team.gather(rep),
        "position": position.gather(rep),
        "sim": pl.Series(np.tile(np.arange(n_sims, dtype=np.int16), n)),
        "active": pl.Series(active.reshape(-1)),
    }
    lo, hi = np.iinfo(np.int16).min, np.iinfo(np.int16).max
    for c in STAT_COLUMNS:
        cols[c] = pl.Series(np.clip(np.rint(stats[c]), lo, hi).astype(np.int16).reshape(-1))
    frame = pl.DataFrame(cols)
    return frame.with_columns(pl.col(c).cast(SIM_STAT_DTYPE) for c in STAT_COLUMNS)


def _team_frame(tp: pl.DataFrame, n_sims: int, **arrays: np.ndarray) -> pl.DataFrame:
    T = tp.height
    rep = pl.Series(np.repeat(np.arange(T, dtype=np.int64), n_sims))
    cols: dict[str, pl.Series] = {
        "game_id": tp["game_id"].gather(rep),
        "season": tp["season"].gather(rep),
        "week": tp["week"].gather(rep),
        "team": tp["team"].gather(rep),
        "opponent": tp["opponent"].gather(rep),
        "sim": pl.Series(np.tile(np.arange(n_sims, dtype=np.int16), T)),
    }
    for name, values in arrays.items():
        dtype = np.float32 if name == "points_drawn" else np.int16
        data = np.asarray(values, dtype=np.float64).reshape(-1)
        if dtype is np.int16:
            data = np.rint(data)
        cols[name] = pl.Series(data.astype(dtype))
    return pl.DataFrame(cols).select(TEAM_SIM_COLUMNS)


__all__ = [
    "KEY_OUTPUT_COLUMNS",
    "TEAM_SIM_COLUMNS",
    "SimulationResult",
    "simulate",
    "simulate_full",
]
