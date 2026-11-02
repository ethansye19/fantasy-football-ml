"""DST inputs (FEATURES.md section H) for dst_week rows.

A dst_week row is a team defense and special-teams unit in one game. Following
the "offense on the field / defense on the field" convention of ``team.py``:
``team__`` columns describe the **opponent's offense** (sack rate allowed,
interception rate, fumble rate, points, pace), ``def__`` columns the row
team's own defense (the same defensive windows as player_week's opponent
defense) plus ``def__dst_*`` scoring-event windows from fact_dst_game. Rare
events (defensive and return TDs, safeties, blocked kicks) carry
``std_shrunk`` and ``car_shrunk`` per-game rates shrunk toward the league rate
of earlier seasons with ``PARAMS["shrink_k"]["dst_games"]`` games.
"""

from __future__ import annotations

import polars as pl

from ffml.features.data import FeatureContext
from ffml.features.spec import Col, Stat, stat_cols
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

DST_WINDOWS = ("l5", "ew", "std", "ps", "car")


def _mean(name: str, col: str) -> Stat:
    return Stat(name, num=col, den=None, windows=DST_WINDOWS)


def _rare(name: str, col: str) -> Stat:
    return Stat(
        name, num=col, den="_one", windows=DST_WINDOWS, shrink=("std", "car"), k="dst_games"
    )


DST_STATS: list[Stat] = [
    _mean("dst_sacks", "sacks"),
    _mean("dst_interceptions", "interceptions"),
    _mean("dst_fumble_recoveries", "fumble_recoveries"),
    _rare("dst_def_td", "def_td"),
    _rare("dst_return_td", "return_td"),
    _rare("dst_safeties", "safeties"),
    _rare("dst_blocked_kicks", "blocked_kicks"),
    _mean("dst_points_allowed", "points_allowed"),
    _mean("dst_yards_allowed", "yards_allowed"),
    Stat("dst_sack_rate", "sacks", "allowed_dropbacks", windows=DST_WINDOWS),
    Stat("dst_int_rate", "interceptions", "allowed_pass_att", windows=DST_WINDOWS),
]


def dst_cols() -> list[Col]:
    return stat_cols("def", DST_STATS)


def dst_events(ctx: FeatureContext) -> pl.DataFrame:
    tg = ctx.team_game.select("team", "season", "week", "allowed_dropbacks", "allowed_pass_att")
    return ctx.dst_game.join(tg, on=["team", "season", "week"], how="left").with_columns(
        pl.lit(1.0).alias("_one"), pl.lit(1, dtype=pl.Int8).alias("_all")
    )


def attach_dst(rows: pl.DataFrame, ctx: FeatureContext, by: str = "team") -> pl.DataFrame:
    """Add ``def__dst_*`` windows of the unit identified by column ``by``."""
    if "dst_state" not in ctx.cache:
        ev = dst_events(ctx)
        ctx.cache["dst_state"] = (
            compute_state(ev, ["team"], DST_STATS, "def", "dst"),
            season_totals(ev, ["team"], DST_STATS, "dst"),
            priors(ev, ["_all"], DST_STATS, "dst", order=["kickoff_utc", "team"]),
        )
    state, ps, pr = ctx.cache["dst_state"]
    df = asof_join(rows, state, [by], ["team"], "dst")
    df = join_ps(df, ps, [by], ["team"])
    df = join_priors(df.with_columns(pl.lit(1, dtype=pl.Int8).alias("_all")), pr, ["_all"], "dst")
    df = finalize(df, [("def", DST_STATS)], "dst").drop("_all")
    return df.rename({ts_col("dst"): "meta__dst_last_game_kickoff"})


__all__ = ["DST_STATS", "attach_dst", "dst_cols", "dst_events"]
