"""Driver attribution: signed point contributions relative to a position-average player.

Method (sequential substitution on an analytic expected-points function)
-------------------------------------------------------------------------
For QB, RB, WR and TE an analytic function ``F`` maps the engine inputs to an
expected stat line (expected plays and pass rate give dropbacks and designed
runs; shares give targets, carries and touchdowns; efficiency gives
receptions, yards, interceptions, sacks, scrambles and fumbles; QB passing
totals are the team's expected receiving totals per attempt, D-057) and then
to points with the league's linear scoring weights (yardage and points-allowed
bands are not linear and are left out). Parameters fall into four groups:

- environment: team plays, pass rate, offensive touchdowns, the team's
  attempts per dropback and kneel-downs;
- opportunity: dropback, target, carry and touchdown shares;
- efficiency: catch rate, yards per reception and carry, sack, scramble,
  interception and fumble rates, and for QBs the receiving corps' completions
  and yards per attempt;
- matchup: the opponent's effect, i.e. the change from "neutral opponent"
  inputs (LightGBM stages re-predicted with the defense features set to their
  training means; structural stages have no opponent input) to the actual
  inputs, plus the engine's blend of sack, interception and fumble rates with
  the opposing defense's rates.

Starting from the reference player (environment = mean team of the week,
other groups = mean neutral-opponent inputs of the week's players at the
position with P(active) >= 0.5), the groups are switched to the player's own
values in the fixed order environment, opportunity, efficiency, matchup; each
contribution is the change in ``F``. The four sum to ``F(player) -
F(reference)``. Availability is ``(P(active) - 1) * F(player)``, the expected
points lost to inactivity (unconditional minus conditional). Kickers use the
same scheme (environment = team field-goal and touchdown expectations,
opportunity = distance mix, efficiency = make probabilities). DSTs are split
from the simulation itself: environment = points- and yards-allowed band
points, efficiency = sacks, takeaways, forced fumbles, touchdowns, safeties
and blocks, each relative to the week's mean DST.

``F`` is an approximation of the simulated mean (no bands, no two-point
conversions, no truncation), so contributions explain the projection's shape
rather than reproduce ``pred_mean`` exactly; the JSON carries the reference
value for context.
"""

from __future__ import annotations

import json
from typing import Any

import numpy as np
import polars as pl

from ffml.scoring.rules import ScoringRules
from ffml.scoring.statline import FG_BUCKETS

METHOD = "sequential-substitution-v1"
GROUPS = ("environment", "opportunity", "efficiency", "matchup")
ENV = ("P", "r", "TD", "kappa", "kn")
OPP = ("ds", "ts", "cs", "ptd", "rtd")
EFF = ("cr", "ypr", "ypc", "sr", "scr", "ir", "fr", "cT", "yT")


def _col(df: pl.DataFrame, name: str) -> np.ndarray:
    return df[name].cast(pl.Float64).fill_null(0.0).to_numpy()


def _team_passing(pp: pl.DataFrame, tp: pl.DataFrame) -> pl.DataFrame:
    """Per team: completions and yards per attempt of the receiving corps, attempts per dropback."""
    b = tp.select(
        "game_id",
        "team",
        pl.col("unmodeled_tgt_share").fill_null(0.0).alias("b"),
        "unmodeled_catch_rate",
        "unmodeled_yards_per_rec",
    )
    p = pp.join(b, on=["game_id", "team"], how="left").with_columns(w=pl.col("availability_prob"))
    agg = p.group_by(["game_id", "team"]).agg(
        (pl.col("w") * pl.col("target_share") * pl.col("catch_rate")).sum().alias("rec_pa"),
        (pl.col("w") * pl.col("target_share") * pl.col("catch_rate") * pl.col("yards_per_rec"))
        .sum()
        .alias("yds_pa"),
        (
            pl.col("w")
            * pl.col("dropback_share")
            * (1 - pl.col("sack_rate"))
            * (1 - pl.col("scramble_rate"))
        )
        .sum()
        .alias("kappa"),
        pl.col("b").first(),
        pl.col("unmodeled_catch_rate").first(),
        pl.col("unmodeled_yards_per_rec").first(),
    )
    return agg.with_columns(
        cT=pl.col("rec_pa") + pl.col("b") * pl.col("unmodeled_catch_rate"),
        yT=pl.col("yds_pa")
        + pl.col("b") * pl.col("unmodeled_catch_rate") * pl.col("unmodeled_yards_per_rec"),
    ).select("game_id", "team", "cT", "yT", "kappa")


def offense_inputs(
    pp: pl.DataFrame, tp: pl.DataFrame, dp: pl.DataFrame | None, blend: bool
) -> pl.DataFrame:
    """One row per offensive player with the analytic inputs (``blend``: apply DST rates)."""
    env = tp.select(
        "game_id",
        "team",
        "opponent",
        pl.col("exp_plays").alias("P"),
        pl.col("exp_pass_rate").alias("r"),
        pl.col("exp_off_td").alias("TD"),
        pl.col("exp_kneels").alias("kn"),
    )
    out = (
        pp.filter(pl.col("position").is_in(["QB", "RB", "WR", "TE"]))
        .join(env, on=["game_id", "team"], how="left")
        .join(_team_passing(pp, tp), on=["game_id", "team"], how="left")
        .select(
            "entity_id",
            "game_id",
            "team",
            "opponent",
            "position",
            "availability_prob",
            "P",
            "r",
            "TD",
            "kappa",
            "kn",
            pl.col("dropback_share").alias("ds"),
            pl.col("target_share").alias("ts"),
            pl.col("carry_share").alias("cs"),
            pl.col("pass_td_share").fill_null(0.0).alias("ptd"),
            pl.col("rush_td_share").fill_null(0.0).alias("rtd"),
            pl.col("catch_rate").alias("cr"),
            pl.col("yards_per_rec").alias("ypr"),
            pl.col("yards_per_carry").alias("ypc"),
            pl.col("sack_rate").alias("sr"),
            pl.col("scramble_rate").alias("scr"),
            pl.col("int_rate").alias("ir"),
            pl.col("fumble_rate").alias("fr"),
            "cT",
            "yT",
        )
    )
    if blend and dp is not None and dp.height:
        d = dp.select(
            "game_id",
            pl.col("team").alias("opponent"),
            pl.col("sack_rate").alias("d_sr"),
            pl.col("int_rate").alias("d_ir"),
            pl.col("fum_rec_rate").alias("d_fr"),
        )
        out = (
            out.join(d, on=["game_id", "opponent"], how="left")
            .with_columns(
                [
                    pl.when(pl.col(f"d_{c}").is_not_null())
                    .then((pl.col(c) * pl.col(f"d_{c}")).sqrt())
                    .otherwise(pl.col(c))
                    .alias(c)
                    for c in ("sr", "ir", "fr")
                ]
            )
            .drop("d_sr", "d_ir", "d_fr")
        )
    return out


def expected_points(
    v: dict[str, np.ndarray], position: np.ndarray, rules: ScoringRules
) -> np.ndarray:
    """Analytic expected points (linear scoring terms only)."""
    w = rules.column_weights()
    off = w.get("off", {})
    db = v["P"] * v["r"] * v["ds"]
    att = db * (1 - v["sr"]) * (1 - v["scr"])
    team_att = v["P"] * v["r"] * v["kappa"]
    tgt = team_att * v["ts"]
    rec = tgt * v["cr"]
    rec_yds = rec * v["ypr"]
    rec_td = v["TD"] * v["r"] * v["ptd"]
    kneel = v["kn"] * v["ds"]
    carries = v["cs"] * v["P"] * (1 - v["r"]) + db * (1 - v["sr"]) * v["scr"] + kneel
    rush_yds = (carries - kneel) * v["ypc"] - kneel
    rush_td = v["TD"] * (1 - v["r"]) * v["rtd"]
    share = np.divide(att, team_att, out=np.zeros_like(att), where=team_att > 0)
    stats = {
        "pass_att": att,
        "pass_cmp": att * v["cT"],
        "pass_yds": att * v["yT"],
        "pass_td": v["TD"] * v["r"] * share,
        "pass_int": att * v["ir"],
        "rush_att": carries,
        "rush_yds": rush_yds,
        "rush_td": rush_td,
        "targets": tgt,
        "rec": rec,
        "rec_yds": rec_yds,
        "rec_td": rec_td,
        "fum_lost": (db + carries + rec) * v["fr"],
    }
    pts = np.zeros(position.size)
    for c, val in stats.items():
        pts += off.get(c, 0.0) * val
    for mask, label in (("te", "TE"), ("rb", "RB"), ("wr", "WR")):
        bonus = w.get(mask, {}).get("rec", 0.0)
        if bonus:
            pts += np.where(position == label, bonus * rec, 0.0)
    return pts


def _arrays(frame: pl.DataFrame, names: tuple[str, ...]) -> dict[str, np.ndarray]:
    return {n: _col(frame, n) for n in names}


def offense_drivers(
    actual: pl.DataFrame,
    neutral: pl.DataFrame,
    rules: ScoringRules,
    min_avail: float = 0.5,
) -> pl.DataFrame:
    """Driver contributions for offensive players; ``actual``/``neutral`` from offense_inputs."""
    a = actual.sort("entity_id", "game_id")
    n = neutral.join(
        a.select("entity_id", "game_id"), on=["entity_id", "game_id"], how="semi"
    ).sort("entity_id", "game_id")
    pos = np.asarray(a["position"].to_list(), dtype=object)
    avail = _col(a, "availability_prob")
    allp = ENV + OPP + EFF
    A = _arrays(a, allp)
    N = _arrays(n, allp)
    R: dict[str, np.ndarray] = {}
    teams = a.unique(["game_id", "team"])
    for name in ENV:
        R[name] = np.full(a.height, float(np.nanmean(_col(teams, name))))
    for name in OPP + EFF:
        ref = np.zeros(a.height)
        for p in np.unique(pos):
            pool = (pos == p) & (avail >= min_avail)
            if not pool.any():
                pool = pos == p
            ref[pos == p] = float(np.mean(N[name][pool]))
        R[name] = ref
    if "QB" in set(pos):  # team-level passing inputs: league means across teams
        for name in ("cT", "yT"):
            R[name] = np.full(a.height, float(np.nanmean(_col(teams, name))))

    def mix(env, opp, eff) -> dict[str, np.ndarray]:
        out = {k: env[k] for k in ENV}
        out.update({k: opp[k] for k in OPP})
        out.update({k: eff[k] for k in EFF})
        return out

    f0 = expected_points(mix(R, R, R), pos, rules)
    f1 = expected_points(mix(A, R, R), pos, rules)
    f2 = expected_points(mix(A, N, R), pos, rules)
    f3 = expected_points(mix(A, N, N), pos, rules)
    f4 = expected_points(mix(A, A, A), pos, rules)
    return pl.DataFrame(
        {
            "entity_id": a["entity_id"],
            "game_id": a["game_id"],
            "environment": f1 - f0,
            "opportunity": f2 - f1,
            "efficiency": f3 - f2,
            "matchup": f4 - f3,
            "availability": (avail - 1.0) * f4,
            "reference": f0,
        }
    )


def kicker_drivers(pp: pl.DataFrame, tp: pl.DataFrame, rules: ScoringRules) -> pl.DataFrame:
    k = pp.filter(pl.col("position") == "K").join(
        tp.select("game_id", "team", "exp_fg_att", "exp_off_td", "exp_two_pt_att"),
        on=["game_id", "team"],
        how="left",
    )
    if k.height == 0:
        return pl.DataFrame()
    wts = rules.weights
    fg_w = np.array([wts.get("fgm", 0.0) + wts.get(f"fgm_{b}", 0.0) for b in FG_BUCKETS])
    fg_w[4] += wts.get("fgm_50p", 0.0)
    fg_w[5] += wts.get("fgm_50p", 0.0)
    miss_w, xpm_w, xpmiss_w = wts.get("fgmiss", 0.0), wts.get("xpm", 0.0), wts.get("xpmiss", 0.0)
    env = np.column_stack(
        [_col(k, "exp_fg_att"), _col(k, "exp_off_td") - _col(k, "exp_two_pt_att")]
    )
    shares = np.column_stack([_col(k, f"fg_att_share_{b}") for b in FG_BUCKETS])
    makes = np.column_stack([_col(k, f"fg_make_prob_{b}") for b in FG_BUCKETS])
    xp = _col(k, "xp_make_prob")

    def f(e, s, m, x):
        made = (s * m * fg_w).sum(axis=1)
        miss = 1 - (s * m).sum(axis=1)
        return e[:, 0] * (made + miss * miss_w) + e[:, 1] * (x * xpm_w + (1 - x) * xpmiss_w)

    tp_env = np.column_stack(
        [_col(tp, "exp_fg_att"), _col(tp, "exp_off_td") - _col(tp, "exp_two_pt_att")]
    ).mean(axis=0)
    e0 = np.broadcast_to(tp_env, env.shape)
    s0 = np.broadcast_to(shares.mean(axis=0), shares.shape)
    m0 = np.broadcast_to(makes.mean(axis=0), makes.shape)
    x0 = np.full(k.height, xp.mean())
    f0, f1 = f(e0, s0, m0, x0), f(env, s0, m0, x0)
    f2, f3 = f(env, shares, m0, x0), f(env, shares, makes, xp)
    avail = _col(k, "availability_prob")
    return pl.DataFrame(
        {
            "entity_id": k["entity_id"],
            "game_id": k["game_id"],
            "environment": f1 - f0,
            "opportunity": f2 - f1,
            "efficiency": f3 - f2,
            "matchup": np.zeros(k.height),
            "availability": (avail - 1.0) * f3,
            "reference": f0,
        }
    )


def dst_drivers(dst_scored: pl.DataFrame, rules: ScoringRules) -> pl.DataFrame:
    """``dst_scored``: DST sims with stat columns and ``points`` (one scoring)."""
    if dst_scored.height == 0:
        return pl.DataFrame()
    lin = rules.column_weights().get("dst", {})
    linear = pl.sum_horizontal(
        [pl.col(c).cast(pl.Float64) * w for c, w in lin.items() if c in dst_scored.columns]
        or [pl.lit(0.0)]
    )
    per = dst_scored.group_by(["entity_id", "game_id"]).agg(
        linear.mean().alias("events"), pl.col("points").mean().alias("total")
    )
    per = per.with_columns(bands=pl.col("total") - pl.col("events"))
    ev, bd = per["events"].mean(), per["bands"].mean()
    return per.select(
        "entity_id",
        "game_id",
        (pl.col("bands") - bd).alias("environment"),
        pl.lit(0.0).alias("opportunity"),
        (pl.col("events") - ev).alias("efficiency"),
        pl.lit(0.0).alias("matchup"),
        pl.lit(0.0).alias("availability"),
        pl.lit(float(ev + bd)).alias("reference"),
    )


def to_json(frame: pl.DataFrame) -> pl.DataFrame:
    """Collapse contribution columns into the ``drivers`` JSON string."""
    keys = ("opportunity", "efficiency", "matchup", "environment", "availability", "reference")

    def one(row: dict[str, Any]) -> str:
        payload = {k: round(float(row[k]), 2) for k in keys}
        payload["method"] = METHOD
        return json.dumps(payload, separators=(",", ":"))

    return frame.select(
        "entity_id",
        "game_id",
        pl.Series("drivers", [one(r) for r in frame.iter_rows(named=True)], dtype=pl.String),
    )


__all__ = [
    "METHOD",
    "dst_drivers",
    "expected_points",
    "kicker_drivers",
    "offense_drivers",
    "offense_inputs",
    "to_json",
]
