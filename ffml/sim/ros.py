"""Rest-of-season simulation (MODELING.md section 5, ROS paragraph).

The chain predicts the projection week from point-in-time features. For the
weeks after it there are no features yet and usually no lines, so each
remaining week is simulated from the **current** information set:

Parameters
    Every team, player and DST keeps its projection-week stage predictions
    (opportunity, efficiency, play volume, pass rate). Teams without a
    projection-week row (bye, or a game already final in a week in progress)
    take their rows from their next scheduled week (live) or their latest
    earlier week (replay); :mod:`ffml.sim.predict` supplies them. Only the
    schedule changes: the opponent, home field, and the game environment.

Game environment: team-strength model (:class:`StrengthModel`)
    Fitted on the model's training window (team_week rows before the cutoff):
    ``points = c0 + c_off * team__points__ew + c_def * def__points_allowed__ew
    + c_home * home`` by least squares (prior-season windows, then the league
    mean, fill nulls). For a future game the offense rating is the team's own
    EW points and the defense rating the opponent's EW points allowed, both as
    of the projection week. The implied totals give the total and spread
    (``ctx__`` conventions); team touchdowns and two-point attempts are scaled
    by ``implied_new / implied_base`` and field-goal attempts by
    ``0.5 + 0.5 * implied_new / implied_base``, where ``implied_base`` is the
    projection-week implied total of the base row.

Game sds k weeks ahead (documented rule)
    ``total_sd_k = sqrt(total_sd_0^2 + 2 * k * d)`` and
    ``margin_sd_k = sqrt(margin_sd_0^2 + 2 * k * d)``. ``total_sd_0`` and
    ``margin_sd_0`` are the residual sds of game totals and margins around the
    strength line in the training window (one row per game). ``d`` is the
    per-week drift of a team's points around a rating that is ``k`` weeks old:
    the training residual variance with ratings from ``k = 1..8`` weeks
    earlier is regressed on ``k`` through the same-week variance (slope
    through the origin, floored at 0); a game has two teams, hence ``2 d``.

Availability persistence (documented rule)
    Each player's projection-week cap ``c`` (final-report rules: report,
    practice, roster, depth chart, game-day INA and the Sleeper tie-breaker
    live) is a probability ``m_0 = 1 - c`` of being unavailable for an injury
    or roster reason. ``k`` weeks later it decays to ``m_k = m_0 * rho^k`` with
    ``rho = 0.35`` for a single-week absence and ``rho = 0.70`` when the player
    is Out or Doubtful and already missed a team game in the last five (a
    multi-week injury). Roster statuses RES, PUP, NFI, SUS and CUT (and
    Sleeper IR, PUP, Sus) mean the player stays out for three more weeks (the
    four-game reserve minimum), then ``m_k = 0.6^(k - 3)``. Draws are
    persistent: one uniform ``u`` per player and simulation is shared by all
    weeks, and the player is unavailable in week ``k`` when ``u < m_k`` (so,
    with ``m_k`` decreasing, once back he stays back). On top of that a fresh
    weekly participation draw uses the primary participation probability, or,
    for players under a cap, the same model evaluated with the injury lags
    reset (``p_healthy``), so the injury is not counted twice. The engine's
    passer fallback still applies.

Outputs
    ``ros_sims``: simulated stat lines for every remaining week (Int16, the
    D-048 vocabulary, ``ros_sims`` draws per entity and week, simulation index
    shared across weeks). ``ros_summary``: per entity and scoring preset the
    distribution of the summed points over the remaining weeks (mean, q10, q50,
    q90), games remaining and bye weeks.
"""

from __future__ import annotations

import logging
from collections.abc import Mapping
from dataclasses import asdict, dataclass, field
from typing import Any

import numpy as np
import polars as pl

from ffml.scoring.rules import ScoringRules
from ffml.sim.engine import simulate_full
from ffml.sim.summarize import score_sims

log = logging.getLogger(__name__)

LAST_REG_WEEK = 18
DRIFT_WEEKS = 8
RHO_SINGLE = 0.35
RHO_MULTI = 0.70
RHO_RESERVE = 0.6
RESERVE_WEEKS = 3
RESERVE_ROSTER = ("RES", "PUP", "NFI", "SUS", "CUT")
RESERVE_SLEEPER = ("IR", "PUP", "Sus")
OFF_COLS = ("team__points__ew", "team__points__ps")
DEF_COLS = ("def__points_allowed__ew", "def__points_allowed__ps")


# --------------------------------------------------------------------------- #
# Team-strength model
# --------------------------------------------------------------------------- #


def _coalesce(frame: pl.DataFrame, names: tuple[str, ...], default: float) -> np.ndarray:
    out = np.full(frame.height, np.nan)
    for n in names:
        if n in frame.columns:
            v = frame[n].cast(pl.Float64).fill_null(np.nan).to_numpy()
            out = np.where(np.isnan(out), v, out)
    return np.where(np.isnan(out), default, out)


@dataclass
class StrengthModel:
    """Points from lagged offense and defense ratings (fitted in the training window)."""

    c0: float = 1.5
    c_off: float = 0.5
    c_def: float = 0.5
    c_home: float = 1.5
    points_mean: float = 22.6
    total_sd0: float = 13.9
    margin_sd0: float = 13.6
    drift: float = 0.0
    source: str = "default"
    n_games: int = 0
    drift_by_k: dict[str, float] = field(default_factory=dict)

    def implied(self, off: np.ndarray, def_opp: np.ndarray, home: np.ndarray) -> np.ndarray:
        return self.c0 + self.c_off * off + self.c_def * def_opp + self.c_home * home

    def sds(self, weeks_ahead: int) -> tuple[float, float]:
        k = max(int(weeks_ahead), 0)
        return (
            float(np.sqrt(self.total_sd0**2 + 2 * k * self.drift)),
            float(np.sqrt(self.margin_sd0**2 + 2 * k * self.drift)),
        )

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)

    @classmethod
    def fit(cls, team_rows: pl.DataFrame) -> StrengthModel:
        t = team_rows.filter(pl.col("y_points").is_not_null())
        if t.height < 200:
            return cls(source="default (too few training rows)")
        mean = float(t["y_points"].mean())
        off = _coalesce(t, OFF_COLS, mean)
        dfn = _coalesce(t, DEF_COLS, mean)
        home = t["home"].cast(pl.Float64).to_numpy()
        y = t["y_points"].to_numpy().astype(float)
        X = np.column_stack([np.ones(t.height), off, dfn, home])
        coef, *_ = np.linalg.lstsq(X, y, rcond=None)
        model = cls(
            c0=float(coef[0]),
            c_off=float(coef[1]),
            c_def=float(coef[2]),
            c_home=float(coef[3]),
            points_mean=mean,
            source="fitted",
        )
        pred = X @ coef
        r = t.select(
            "game_id", "team", "opponent", "home", "season", "week", "y_points"
        ).with_columns(pl.Series("pred", pred))
        opp = r.select("game_id", pl.col("team").alias("opponent"), "y_points", "pred")
        g = r.join(opp, on=["game_id", "opponent"], suffix="_o").filter(pl.col("home"))
        if g.height >= 100:
            total = (g["y_points"] + g["y_points_o"] - g["pred"] - g["pred_o"]).to_numpy()
            margin = (g["y_points"] - g["y_points_o"] - g["pred"] + g["pred_o"]).to_numpy()
            model.total_sd0 = float(np.std(total))
            model.margin_sd0 = float(np.std(margin))
            model.n_games = g.height
        model.drift, model.drift_by_k = _drift(t, model, off, dfn)
        return model


def _drift(
    t: pl.DataFrame, m: StrengthModel, off: np.ndarray, dfn: np.ndarray
) -> tuple[float, dict[str, float]]:
    """Per-week growth of a team's points variance when ratings are k weeks old."""
    base = t.select("season", "week", "team", "opponent", "home", "y_points").with_columns(
        pl.Series("off", off), pl.Series("dfn", dfn)
    )
    # ratings at week w0: offense from the team's own row, defense of each opponent
    # from the row of the team that faced it (def__ describes the defense on the field)
    off_at = base.select("season", pl.col("week").alias("w0"), "team", "off")
    def_at = base.select(
        "season", pl.col("week").alias("w0"), pl.col("opponent").alias("team"), "dfn"
    )
    v0 = float(
        np.var(
            base["y_points"].to_numpy()
            - m.implied(off, dfn, base["home"].cast(pl.Float64).to_numpy())
        )
    )
    ks, vs = [], []
    by_k: dict[str, float] = {"0": v0}
    for k in range(1, DRIFT_WEEKS + 1):
        g = (
            base.with_columns((pl.col("week") - k).alias("w0"))
            .join(off_at, on=["season", "w0", "team"], how="inner", suffix="_r")
            .join(
                def_at.rename({"team": "opponent", "dfn": "dfn_r"}),
                on=["season", "w0", "opponent"],
                how="inner",
            )
        )
        if g.height < 200:
            continue
        pred = m.implied(
            g["off_r"].to_numpy(), g["dfn_r"].to_numpy(), g["home"].cast(pl.Float64).to_numpy()
        )
        v = float(np.var(g["y_points"].to_numpy() - pred))
        ks.append(k)
        vs.append(v)
        by_k[str(k)] = v
    if not ks:
        return 0.0, by_k
    k_arr, v_arr = np.asarray(ks, float), np.asarray(vs)
    slope = float(np.sum(k_arr * (v_arr - v0)) / np.sum(k_arr**2))
    return max(slope, 0.0), by_k


def ratings(team_rows: pl.DataFrame, dst_rows: pl.DataFrame, points_mean: float) -> pl.DataFrame:
    """Offense rating per team (own EW points) and defense rating (own EW points allowed)."""
    off = team_rows.select(
        "team", pl.Series("off", _coalesce(team_rows, OFF_COLS, points_mean))
    ).unique("team", keep="first", maintain_order=True)
    dfn = dst_rows.select(
        "team", pl.Series("dfn", _coalesce(dst_rows, DEF_COLS, points_mean))
    ).unique("team", keep="first", maintain_order=True)
    return off.join(dfn, on="team", how="full", coalesce=True).with_columns(
        pl.col("off").fill_null(points_mean), pl.col("dfn").fill_null(points_mean)
    )


def strength_game_lines(
    games: pl.DataFrame, rating: pl.DataFrame, model: StrengthModel
) -> pl.DataFrame:
    """``games`` (team, opponent, home) with strength-implied ctx line columns."""
    g = games.join(rating.select("team", "off"), on="team", how="left").join(
        rating.select(pl.col("team").alias("opponent"), "dfn"), on="opponent", how="left"
    )
    g = g.with_columns(
        pl.col("off").fill_null(model.points_mean), pl.col("dfn").fill_null(model.points_mean)
    )
    it = model.implied(
        g["off"].to_numpy(), g["dfn"].to_numpy(), g["home"].cast(pl.Float64).to_numpy()
    )
    g = g.with_columns(pl.Series("_it", np.maximum(it, 3.0)))
    io = g.select("game_id", pl.col("team").alias("opponent"), pl.col("_it").alias("_io"))
    g = g.join(io, on=["game_id", "opponent"], how="left").with_columns(
        pl.col("_io").fill_null(model.points_mean)
    )
    return g.with_columns(
        pl.col("_it").alias("ctx__implied_team_total"),
        pl.col("_io").alias("ctx__implied_opp_total"),
        (pl.col("_it") + pl.col("_io")).alias("ctx__total_line"),
        (pl.col("_io") - pl.col("_it")).alias("ctx__spread"),
    ).drop("off", "dfn", "_it", "_io")


# --------------------------------------------------------------------------- #
# Availability persistence
# --------------------------------------------------------------------------- #


def _col(frame: pl.DataFrame, name: str, default: Any = None) -> list[Any]:
    return frame[name].to_list() if name in frame.columns else [default] * frame.height


def unavailable_paths(players: pl.DataFrame, weeks_ahead: list[int]) -> np.ndarray:
    """``m_k`` per player (rows) and week ahead (columns), see the module docstring."""
    cap = np.asarray(_col(players, "avail_cap", 1.0), dtype=float)
    m0 = np.clip(1.0 - np.nan_to_num(cap, nan=1.0), 0.0, 1.0)
    roster = _col(players, "avail__roster_status")
    sleeper = _col(players, "sleeper_injury_status")
    status = _col(players, "avail__report_status")
    missed = np.nan_to_num(np.asarray(_col(players, "attr__games_missed__l5", 0.0), dtype=float))
    out = np.zeros((players.height, len(weeks_ahead)))
    for i in range(players.height):
        reserve = roster[i] in RESERVE_ROSTER or sleeper[i] in RESERVE_SLEEPER
        multi = status[i] in ("Out", "Doubtful") and missed[i] >= 1
        for j, k in enumerate(weeks_ahead):
            if reserve:
                out[i, j] = 1.0 if k <= RESERVE_WEEKS else RHO_RESERVE ** (k - RESERVE_WEEKS)
            else:
                out[i, j] = m0[i] * (RHO_MULTI if multi else RHO_SINGLE) ** k
    return out


def active_paths(
    players: pl.DataFrame, weeks_ahead: list[int], n_sims: int, rng: np.random.Generator
) -> dict[int, dict[str, np.ndarray]]:
    """Persistent active flags per week ahead: ``{k: {entity_id: (n_sims,) bool}}``."""
    m = unavailable_paths(players, weeks_ahead)
    cap = np.nan_to_num(np.asarray(_col(players, "avail_cap", 1.0), dtype=float), nan=1.0)
    p_primary = np.asarray(_col(players, "p_primary", 0.9), dtype=float)
    p_healthy = np.asarray(_col(players, "p_healthy", 0.9), dtype=float)
    p_part = np.clip(np.where(cap < 1.0, p_healthy, p_primary), 0.0, 1.0)
    ids = players["player_id"].to_list()
    u = rng.random((players.height, n_sims))
    out: dict[int, dict[str, np.ndarray]] = {}
    for j, k in enumerate(weeks_ahead):
        part = rng.random((players.height, n_sims)) < p_part[:, None]
        active = (u >= m[:, j : j + 1]) & part
        out[k] = {e: active[i] for i, e in enumerate(ids)}
    return out


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #


@dataclass
class RosResult:
    sims: pl.DataFrame
    summary: pl.DataFrame
    weeks: list[int]
    info: dict[str, Any]


def _scale_team(team: pl.DataFrame, base_implied: np.ndarray) -> pl.DataFrame:
    new = team["ctx__implied_team_total"].cast(pl.Float64).to_numpy()
    ratio = np.clip(new / np.maximum(base_implied, 3.0), 0.3, 3.0)
    out = team
    for c, factor in (
        ("p_team_off_td", ratio),
        ("p_team_two_pt_att", ratio),
        ("p_team_fg_att", 0.5 + 0.5 * ratio),
    ):
        if c in out.columns:
            out = out.with_columns(pl.Series(c, out[c].cast(pl.Float64).to_numpy() * factor))
    return out


def simulate_ros(
    model: Any,
    base_team: pl.DataFrame,
    base_players: pl.DataFrame,
    base_dst: pl.DataFrame,
    schedule: pl.DataFrame,
    strength: StrengthModel,
    start_week: int,
    n_sims: int,
    seed: int,
    scorings: Mapping[str, ScoringRules],
) -> RosResult:
    """Simulate weeks ``start_week + 1 .. 18`` from projection-week rows (module docstring).

    ``base_*`` hold one row per team, player and defense with the chain's
    ``p_<stage>`` columns (players also ``avail_cap``, ``p_primary``,
    ``p_healthy``); ``schedule`` has ``season, week, game_id, team, opponent,
    home`` for the remaining regular-season games.
    """
    weeks = list(range(start_week + 1, LAST_REG_WEEK + 1))
    sched = schedule.filter(pl.col("week").is_in(weeks))
    weeks = sorted(int(w) for w in sched["week"].unique().to_list())
    rng = np.random.default_rng(seed)
    rating = ratings(base_team, base_dst, strength.points_mean)
    base_implied = dict(
        zip(
            base_team["team"].to_list(),
            _coalesce(base_team, ("ctx__implied_team_total",), strength.points_mean),
            strict=True,
        )
    )
    paths = active_paths(base_players, [w - start_week for w in weeks], n_sims, rng)
    key_cols = ("game_id", "week", "opponent", "home")
    frames: list[pl.DataFrame] = []
    covered: dict[int, set[str]] = {}
    for w in weeks:
        k = w - start_week
        games = sched.filter(pl.col("week") == w).select(
            "season", "week", "game_id", "team", "opponent", "home"
        )
        lines = strength_game_lines(games, rating, strength)
        team = base_team.drop(
            [c for c in (*key_cols, *lines.columns[6:]) if c in base_team.columns]
        ).join(lines.drop("season"), on="team", how="inner")
        team = _scale_team(
            team,
            np.array([base_implied.get(t, strength.points_mean) for t in team["team"].to_list()]),
        )
        slots = games.select("team", "game_id", "week", "opponent")
        players = base_players.drop(
            [c for c in ("game_id", "week", "opponent") if c in base_players.columns]
        ).join(slots, on="team", how="inner")
        dst = base_dst.drop(
            [c for c in ("game_id", "week", "opponent") if c in base_dst.columns]
        ).join(slots, on="team", how="inner")
        tp, pp, dp = model.params_from_predictions(team, players, dst)
        if tp.height == 0:
            continue
        total_sd, margin_sd = strength.sds(k)
        tp = tp.with_columns(
            pl.lit(total_sd).alias("total_sd"), pl.lit(margin_sd).alias("margin_sd")
        )
        override = {e: v for e, v in paths[k].items() if e in set(pp["entity_id"].to_list())}
        res = simulate_full(
            tp, pp, dp, n_sims=n_sims, seed=seed + 1000 * k, active_override=override
        )
        frames.append(res.players)
        covered[w] = set(tp["team"].to_list())
    sims = pl.concat(frames, how="vertical") if frames else pl.DataFrame()
    summary = (
        ros_summary(sims, base_players, base_dst, weeks, covered, scorings)
        if frames
        else pl.DataFrame()
    )
    info = {
        "weeks": weeks,
        "n_sims": n_sims,
        "seed": seed,
        "strength": strength.to_dict(),
        "sd_rule": "sd_k = sqrt(sd_0^2 + 2 k d), d = per-week drift of team points variance",
        "availability_rule": {
            "rho_single": RHO_SINGLE,
            "rho_multi": RHO_MULTI,
            "reserve_weeks": RESERVE_WEEKS,
            "rho_reserve": RHO_RESERVE,
        },
        "rows": sims.height,
    }
    return RosResult(sims=sims, summary=summary, weeks=weeks, info=info)


def ros_summary(
    sims: pl.DataFrame,
    base_players: pl.DataFrame,
    base_dst: pl.DataFrame,
    weeks: list[int],
    covered: dict[int, set[str]],
    scorings: Mapping[str, ScoringRules],
) -> pl.DataFrame:
    """Summed points over the remaining weeks per entity and preset, with games left and byes."""
    entities = pl.concat(
        [
            base_players.select(pl.col("player_id").alias("entity_id"), "position", "team"),
            base_dst.select(
                pl.col("team").alias("entity_id"), pl.lit("DST").alias("position"), "team"
            ),
        ]
    ).unique("entity_id", keep="first", maintain_order=True)
    byes = {
        t: [w for w in weeks if t not in covered.get(w, set())]
        for t in entities["team"].unique().to_list()
    }
    n_sims = int(sims["sim"].max()) + 1
    out = []
    for label, rules in scorings.items():
        scored = score_sims(sims, rules)
        totals = scored.group_by(["entity_id", "sim"]).agg(pl.col("points").sum())
        # every simulation index exists for every entity with at least one game
        stats = totals.group_by("entity_id").agg(
            pl.col("points").mean().alias("ros_mean"),
            pl.col("points").quantile(0.1, interpolation="linear").alias("ros_q10"),
            pl.col("points").quantile(0.5, interpolation="linear").alias("ros_q50"),
            pl.col("points").quantile(0.9, interpolation="linear").alias("ros_q90"),
        )
        games = scored.group_by("entity_id").agg(
            (pl.col("week").n_unique()).alias("games_remaining")
        )
        f = (
            entities.join(stats, on="entity_id", how="left")
            .join(games, on="entity_id", how="left")
            .with_columns(
                [pl.col(c).fill_null(0.0) for c in ("ros_mean", "ros_q10", "ros_q50", "ros_q90")]
                + [pl.col("games_remaining").fill_null(0).cast(pl.Int32)]
            )
            .with_columns(
                pl.col("team")
                .map_elements(lambda t: byes.get(t, weeks), return_dtype=pl.List(pl.Int32))
                .alias("bye_weeks"),
                pl.lit(label).alias("scoring"),
                pl.lit(min(weeks)).cast(pl.Int32).alias("first_week"),
                pl.lit(max(weeks)).cast(pl.Int32).alias("last_week"),
                pl.lit(n_sims).cast(pl.Int32).alias("n_sims"),
            )
        )
        out.append(f)
    return pl.concat(out).sort(["scoring", "position", "ros_mean"], descending=[False, False, True])


__all__ = [
    "RosResult",
    "StrengthModel",
    "active_paths",
    "ratings",
    "ros_summary",
    "simulate_ros",
    "strength_game_lines",
    "unavailable_paths",
]
