"""Team offense environment (group B) and defense (group C), lagged by the cutoff rule.

Convention used in all three datasets: ``team__`` columns describe the
**offense on the field** in the row's matchup and ``def__`` columns the
**defense on the field**. For player_week and team_week rows the offense is
the row's own team and the defense is the opponent; for dst_week rows the
offense is the opponent and the defense is the row's team.

Team windows: L3, L5, EW, STD (blended with PS early in the season), PS. Rate
statistics with an explicit denominator in fact_team_game are ratios of sums;
pace, PROE and per-play EPA splits are means of the per-game values. Defense
rates also carry a ``std_shrunk`` variant shrunk toward the league rate of all
earlier seasons (no same-season information).
"""

from __future__ import annotations

import polars as pl

from ffml.features.context import qb1_at
from ffml.features.data import FeatureContext
from ffml.features.spec import Col, Stat, point_col, stat_cols
from ffml.features.targets import team_denominators
from ffml.features.windows import (
    asof_join,
    compute_state,
    finalize,
    join_priors,
    join_ps,
    priors,
    season_totals,
    ts_col,
)

TEAM_WINDOWS = ("l3", "l5", "ew", "std", "ps")


def _mean(name: str, col: str, **kw) -> Stat:
    return Stat(name, num=col, den=None, windows=TEAM_WINDOWS, **kw)


def _rate(name: str, num: str, den: str, **kw) -> Stat:
    return Stat(name, num=num, den=den, windows=TEAM_WINDOWS, **kw)


TEAM_STATS: list[Stat] = [
    _mean("plays", "plays"),
    _mean("sec_per_play", "sec_per_play"),
    _mean("neutral_sec_per_play", "neutral_sec_per_play"),
    _rate("pass_rate", "pass_plays", "plays"),
    _rate("neutral_pass_rate", "neutral_pass_plays", "neutral_plays"),
    _mean("proe", "proe"),
    _mean("early_down_pass_rate", "early_down_pass_rate"),
    _rate("epa_per_play", "epa_total", "plays"),
    _mean("pass_epa_per_play", "pass_epa_per_play"),
    _mean("rush_epa_per_play", "rush_epa_per_play"),
    _rate("success_rate", "successes", "plays"),
    _mean("points", "points"),
    _mean("pass_td", "pass_td"),
    _mean("rush_td", "rush_td"),
    _mean("rz_trips", "rz_trips"),
    _rate("rz_td_rate", "rz_tds", "rz_trips"),
    _mean("gl_plays", "gl_plays"),
    _rate("sack_rate_allowed", "sacks", "dropbacks"),
    _rate("int_rate", "interceptions", "pass_att"),
    _rate("fumble_rate", "fumbles_lost", "plays"),
    _mean("team_targets", "team_targets"),
    _mean("team_carries", "rush_att"),
    _mean("pass_att", "pass_att"),
    _mean("dropbacks", "dropbacks"),
    _mean("field_goal_att", "field_goal_att"),
]

DEF_STATS: list[Stat] = [
    _mean("plays_faced", "allowed_plays"),
    _mean("sec_per_play_faced", "allowed_sec_per_play"),
    _rate("pass_rate_faced", "allowed_pass_plays", "allowed_plays"),
    _mean("proe_faced", "allowed_proe"),
    _rate("epa_per_play_allowed", "allowed_epa_total", "allowed_plays"),
    _mean("pass_epa_allowed", "allowed_pass_epa_per_play"),
    _mean("rush_epa_allowed", "allowed_rush_epa_per_play"),
    _rate("success_rate_allowed", "allowed_successes", "allowed_plays"),
    _rate(
        "ypt_allowed_rb",
        "allowed_rb_rec_yds",
        "allowed_rb_targets",
        shrink=("std",),
        k="def_targets",
    ),
    _rate(
        "ypt_allowed_wr",
        "allowed_wr_rec_yds",
        "allowed_wr_targets",
        shrink=("std",),
        k="def_targets",
    ),
    _rate(
        "ypt_allowed_te",
        "allowed_te_rec_yds",
        "allowed_te_targets",
        shrink=("std",),
        k="def_targets",
    ),
    _rate("tgt_share_allowed_rb", "allowed_rb_targets", "allowed_skill_targets"),
    _rate("tgt_share_allowed_wr", "allowed_wr_targets", "allowed_skill_targets"),
    _rate("tgt_share_allowed_te", "allowed_te_targets", "allowed_skill_targets"),
    _rate(
        "ypc_allowed",
        "allowed_rb_rush_yds",
        "allowed_rb_rush_att",
        shrink=("std",),
        k="def_carries",
    ),
    _rate(
        "rush_td_rate_allowed", "allowed_rush_td", "allowed_rush_att", shrink=("std",), k="def_td"
    ),
    _rate(
        "rec_td_rate_allowed", "allowed_pass_td", "allowed_pass_att", shrink=("std",), k="def_td"
    ),
    _rate("sack_rate", "allowed_sacks", "allowed_dropbacks", shrink=("std",), k="def_dropbacks"),
    _rate("int_rate", "allowed_interceptions", "allowed_pass_att", shrink=("std",), k="def_td"),
    _mean("points_allowed", "allowed_points"),
    _rate(
        "rz_td_rate_allowed",
        "allowed_rz_tds",
        "allowed_rz_trips",
        shrink=("std",),
        k="def_rz_trips",
    ),
    _mean("qb_pass_yds_allowed", "allowed_qb_pass_yds"),
    _mean("rb_rush_yds_allowed", "allowed_rb_rush_yds"),
]


def team_cols() -> list[Col]:
    cols = stat_cols("team", TEAM_STATS)
    cols.append(
        point_col(
            "team",
            "qb_change_flag",
            note="depth-chart QB1 at asof differs from the passer with most attempts in the last game",
        )
    )
    return cols


def def_cols() -> list[Col]:
    return stat_cols("def", DEF_STATS)


def team_events(ctx: FeatureContext) -> pl.DataFrame:
    """fact_team_game rows (all game types) with reconstructed numerators."""
    if "team_events" in ctx.cache:
        return ctx.cache["team_events"]
    tg = ctx.team_game
    den = team_denominators(ctx).select("team", "season", "week", "team_targets")
    f = pl.Float64
    ev = tg.join(den, on=["team", "season", "week"], how="left").with_columns(
        (pl.col("pass_rate") * pl.col("plays")).round(0).alias("pass_plays"),
        (pl.col("neutral_pass_rate") * pl.col("neutral_plays"))
        .round(0)
        .alias("neutral_pass_plays"),
        (pl.col("epa_per_play") * pl.col("plays")).alias("epa_total"),
        (pl.col("success_rate") * pl.col("plays")).round(0).alias("successes"),
        (pl.col("rz_td_rate").fill_null(0.0) * pl.col("rz_trips")).round(0).alias("rz_tds"),
        (pl.col("allowed_pass_rate") * pl.col("allowed_plays"))
        .round(0)
        .alias("allowed_pass_plays"),
        (pl.col("allowed_epa_per_play") * pl.col("allowed_plays")).alias("allowed_epa_total"),
        (pl.col("allowed_success_rate") * pl.col("allowed_plays"))
        .round(0)
        .alias("allowed_successes"),
        (pl.col("allowed_rz_td_rate").fill_null(0.0) * pl.col("allowed_rz_trips"))
        .round(0)
        .alias("allowed_rz_tds"),
        (
            pl.col("allowed_rb_targets").cast(f)
            + pl.col("allowed_wr_targets").cast(f)
            + pl.col("allowed_te_targets").cast(f)
        ).alias("allowed_skill_targets"),
        pl.lit(1, dtype=pl.Int8).alias("_all"),
    )
    ctx.cache["team_events"] = ev
    return ev


def _states(ctx: FeatureContext, kind: str):
    key = f"state_{kind}"
    if key in ctx.cache:
        return ctx.cache[key]
    ev = team_events(ctx)
    stats, group, tag = (TEAM_STATS, "team", "tof") if kind == "off" else (DEF_STATS, "def", "tdf")
    state = compute_state(ev, ["team"], stats, group, tag)
    ps = season_totals(ev, ["team"], stats, tag)
    pr = priors(ev, ["_all"], stats, tag, order=["kickoff_utc", "team"])
    ctx.cache[key] = (state, ps, pr)
    return ctx.cache[key]


def attach_offense(rows: pl.DataFrame, ctx: FeatureContext, by: str = "team") -> pl.DataFrame:
    """Add ``team__`` windows of the offense identified by column ``by``."""
    state, ps, _ = _states(ctx, "off")
    df = asof_join(rows, state, [by], ["team"], "tof")
    df = join_ps(df, ps, [by], ["team"])
    df = finalize(df, [("team", TEAM_STATS)], "tof")
    return df.rename({ts_col("tof"): "meta__team_last_game_kickoff"})


def attach_defense(rows: pl.DataFrame, ctx: FeatureContext, by: str = "opponent") -> pl.DataFrame:
    """Add ``def__`` windows of the defense identified by column ``by``."""
    state, ps, pr = _states(ctx, "def")
    df = asof_join(rows, state, [by], ["team"], "tdf")
    df = join_ps(df, ps, [by], ["team"])
    df = join_priors(df.with_columns(pl.lit(1, dtype=pl.Int8).alias("_all")), pr, ["_all"], "tdf")
    df = finalize(df, [("def", DEF_STATS)], "tdf").drop("_all")
    return df.rename({ts_col("tdf"): "meta__def_last_game_kickoff"})


def qb_change_flag(rows: pl.DataFrame, ctx: FeatureContext, by: str = "team") -> pl.DataFrame:
    """``team__qb_change_flag``: depth QB1 at ``asof_ts`` vs the last game's top passer.

    The top passer is the team's player with the most pass attempts in its last
    game before ``cutoff_ts`` (ties: lowest player_id). Null when either side is
    unknown.
    """
    pg = ctx.player_game.filter(pl.col("pass_att") > 0)
    top = (
        pg.sort(
            ["team", "kickoff_utc", "pass_att", "player_id"], descending=[False, False, True, False]
        )
        .unique(subset=["team", "kickoff_utc"], keep="first", maintain_order=True)
        .select(
            pl.col("team").alias("_qb_team"),
            pl.col("player_id").alias("_top_passer"),
            pl.col("kickoff_utc").alias("_qb_key"),
        )
        .sort("_qb_key")
    )
    base = rows.select("_rid", by, "cutoff_ts", "asof_ts")
    last = base.sort("cutoff_ts").join_asof(
        top,
        left_on="cutoff_ts",
        right_on="_qb_key",
        by_left=[by],
        by_right=["_qb_team"],
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    pairs = base.select(pl.col(by).alias("team"), "asof_ts")
    qb1 = qb1_at(ctx, pairs, "asof_ts").rename({"team": by})
    last = last.join(qb1, on=[by, "asof_ts"], how="left")
    flag = (
        pl.when(pl.col("qb1_id").is_null() | pl.col("_top_passer").is_null())
        .then(None)
        .otherwise((pl.col("qb1_id") != pl.col("_top_passer")).cast(pl.Float64))
    )
    out = last.select(
        "_rid",
        flag.alias("team__qb_change_flag"),
        pl.col("depth_snapshot_ts").alias("meta__qb_depth_snapshot_ts"),
    )
    return rows.join(out, on="_rid", how="left")


__all__ = [
    "DEF_STATS",
    "TEAM_STATS",
    "attach_defense",
    "attach_offense",
    "def_cols",
    "qb_change_flag",
    "team_cols",
    "team_events",
]
