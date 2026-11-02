"""Player opportunity (group D), efficiency (group E), attributes (group F), kicker inputs (H).

Events are the player's games with ``played = true`` (roster-only and
inactive rows never count as games). Shares are computed against team-game
denominators (team targets, carries, red-zone and goal-line totals, air
yards, dropbacks, team passing and rushing TDs).

Share statistics (``reset=True``) restart their L1/L3/L5/EW/STD windows when
the player changes team: windows run within a team stint, and when the row's
team differs from the team of the player's last game they are null, their
shrunk variants fall back to the position prior, and the old team's EW value
moves to ``opp__prev_team_<share>__ew``. PS and CAR windows are player level
(any team). Efficiency rates carry ``ew/std/car`` shrunk variants toward
position priors computed from all seasons strictly before the row's season.
"""

from __future__ import annotations

import polars as pl

from ffml.features.data import FeatureContext
from ffml.features.spec import (
    FEATURE_DTYPE,
    PARAMS,
    SKILL,
    WINDOWS,
    Col,
    Stat,
    fname,
    point_col,
    stat_cols,
)
from ffml.features.targets import team_denominators
from ffml.features.windows import (
    asof_join,
    compute_state,
    finalize,
    join_priors,
    join_ps,
    ordered_sum,
    priors,
    season_totals,
    ts_col,
)

QB = ("QB",)
RB = ("RB",)
K = ("K",)
RUSHERS = ("RB", "QB", "WR")
NON_K = ("QB", "RB", "WR", "TE")
W5 = ("l3", "l5", "ew", "std", "ps")
E5 = ("l5", "ew", "std", "ps", "car")
SHR = ("ew", "std", "car")


def _share(name: str, num: str, den: str | None, positions, windows=WINDOWS, **kw) -> Stat:
    return Stat(name, num=num, den=den, windows=windows, reset=True, positions=positions, **kw)


def _vol(name: str, col: str, positions, windows=WINDOWS) -> Stat:
    return Stat(name, num=col, den=None, windows=windows, positions=positions)


def _eff(name: str, num: str, den: str, k: str, positions, n: bool = False) -> Stat:
    return Stat(
        name,
        num=num,
        den=den,
        windows=E5,
        shrink=SHR,
        k=k,
        n_windows=("std", "car") if n else (),
        positions=positions,
    )


def _ngs(name: str, col: str, positions, den: str | None = None) -> Stat:
    return Stat(name, num=col, den=den, windows=("ew", "std"), positions=positions, sparse=True)


OPP_STATS: list[Stat] = [
    _share("target_share", "targets", "team_targets", SKILL),
    _share("air_yards_share", "rec_air_yards", "team_air_yards", SKILL, windows=W5),
    _share(
        "rz_target_share",
        "rz_targets",
        "team_rz_targets",
        SKILL,
        shrink=("ew",),
        k="share_rz_targets",
    ),
    _share("ez_target_share", "ez_targets", "team_ez_targets", SKILL, windows=W5),
    _share("rush_share", "carries", "team_carries", RUSHERS),
    _share(
        "gl_carry_share",
        "gl_carries",
        "team_gl_carries",
        ("RB", "QB"),
        shrink=("ew",),
        k="share_gl_carries",
    ),
    _share("rz_carry_share", "rz_carries", "team_rz_carries", ("RB", "QB"), windows=W5),
    _share("snap_share", "offense_pct", None, NON_K),
    _share("route_proxy", "route_num", None, SKILL, windows=W5),
    _share("dropback_share", "dropbacks", "team_dropbacks", QB),
    _share(
        "pass_td_share",
        "rec_td",
        "team_pass_td",
        SKILL,
        windows=W5,
        shrink=("ew",),
        k="share_pass_td",
    ),
    _share(
        "rush_td_share",
        "rush_td",
        "team_rush_td",
        RUSHERS,
        windows=W5,
        shrink=("ew",),
        k="share_rush_td",
    ),
    _vol("targets", "targets", SKILL),
    _vol("carries", "carries", RUSHERS),
    _vol("touches", "touches", SKILL),
    _vol("dropbacks", "dropbacks", QB),
    _vol("pass_att", "pass_att", QB),
    _vol("fg_att", "fg_att", K, windows=("l3", "l5", "ew", "std", "ps", "car")),
    _vol("pat_att", "pat_att", K, windows=("l5", "ew", "std", "ps")),
    Stat("adot", "rec_air_yards", "targets", windows=E5, positions=SKILL),
    Stat(
        "designed_rush_rate",
        "designed_rush",
        "designed_rush_den",
        windows=("ew", "std", "ps", "car"),
        positions=QB,
    ),
    Stat(
        "scramble_rate", "scrambles", "dropbacks", windows=("ew", "std", "ps", "car"), positions=QB
    ),
]

PREV_TEAM_SHARES: list[str] = ["target_share", "rush_share", "snap_share"]

EFF_STATS: list[Stat] = [
    _eff("catch_rate", "receptions", "targets", "targets", SKILL, n=True),
    _eff("yards_per_target", "rec_yds", "targets", "targets", SKILL),
    _eff("yards_per_reception", "rec_yds", "receptions", "receptions", SKILL, n=True),
    _eff("yac_per_rec", "rec_yac", "receptions", "receptions", SKILL),
    _eff("yards_per_carry", "rush_yds", "carries", "carries", RUSHERS, n=True),
    _eff("rush_first_down_rate", "rush_first_downs", "carries", "carries", RUSHERS),
    _eff("yards_per_attempt", "pass_yds", "pass_att", "attempts", QB, n=True),
    _eff("completion_rate", "completions", "pass_att", "attempts", QB),
    _eff("int_rate", "interceptions", "pass_att", "td", QB),
    _eff("sack_rate", "sacks", "dropbacks", "dropbacks", QB, n=True),
    _eff("cpoe", "cpoe_num", "cpoe_att", "attempts", QB),
    _eff("td_per_target", "rec_td", "targets", "td", SKILL),
    _eff("td_per_carry", "rush_td", "carries", "td", RUSHERS),
    _eff("td_per_attempt", "pass_td", "pass_att", "td", QB),
    _eff("epa_per_target", "rec_epa", "targets", "targets", SKILL),
    _eff("epa_per_carry", "rush_epa", "carries", "carries", RUSHERS),
    _eff("epa_per_dropback", "pass_epa", "dropbacks", "dropbacks", QB),
    _eff("exp_rec_yds_per_target", "rec_yards_exp", "targets", "targets", SKILL),
    _eff("exp_rec_td_per_target", "rec_td_exp", "targets", "td", SKILL),
    _eff("exp_rush_yds_per_carry", "rush_yards_exp", "carries", "carries", RUSHERS),
    _eff("exp_rush_td_per_carry", "rush_td_exp", "carries", "td", RUSHERS),
    _eff("fg_pct", "fg_made", "fg_att", "fg_att", K, n=True),
    _eff("pat_pct", "pat_made", "pat_att", "pat_att", K, n=True),
    Stat(
        "actual_minus_expected_rec_yds",
        "ame_rec_yds",
        None,
        windows=("ew", "std", "car"),
        positions=SKILL,
    ),
    Stat("actual_minus_expected_td", "ame_td", None, windows=("ew", "std", "car"), positions=NON_K),
    Stat(
        "fg_made_share_40_49", "fg_made_40_49", "fg_made", windows=("ew", "std", "car"), positions=K
    ),
    Stat("fg_made_share_50p", "fg_made_50p", "fg_made", windows=("ew", "std", "car"), positions=K),
    _ngs("ngs_avg_separation", "avg_separation", ("WR", "TE", "RB")),
    _ngs("ngs_avg_cushion", "avg_cushion", ("WR", "TE")),
    _ngs("ngs_yac_above_expectation", "yac_above_expectation", ("WR", "TE", "RB")),
    _ngs("ngs_intended_air_yards", "avg_intended_air_yards", ("WR", "TE", "QB")),
    _ngs("ngs_ryoe_per_att", "rush_yards_over_expected", RB, den="carries"),
    _ngs("ngs_rush_efficiency", "efficiency", RB),
    _ngs("ngs_time_to_throw", "time_to_throw", QB),
    _ngs("ngs_aggressiveness", "aggressiveness", QB),
]

GAMES_STAT = Stat("games", num="_one", den=None, windows=(), n_windows=("std", "ps", "car"))

ATTR_POINT: list[str] = [
    "age",
    "years_exp",
    "rookie_flag",
    "draft_round",
    "draft_pick",
    "undrafted_flag",
    "height",
    "weight",
    "weeks_since_last_game",
    "new_team_flag",
    "weeks_on_team",
]

TEAMMATES_OUT: list[str] = [
    "teammates_out_same_pos",
    "teammates_out_target_share_ew",
    "teammates_out_rush_share_ew",
]
TEAM_OUT: list[str] = ["out_target_share_ew", "out_rush_share_ew", "out_dropback_share_ew"]
OUT_STATUSES = ("Out", "Doubtful")


def opp_cols() -> list[Col]:
    cols = stat_cols("opp", OPP_STATS)
    cols.append(
        Col(
            name=fname("opp", "wopr", "ew"),
            dtype=FEATURE_DTYPE,
            kind="feature",
            group="opp",
            stat="wopr",
            window="ew",
            variant="derived",
            positions=SKILL,
            note="1.5 * target_share__ew + 0.7 * air_yards_share__ew",
        )
    )
    for s in PREV_TEAM_SHARES:
        cols.append(
            Col(
                name=fname("opp", f"prev_team_{s}", "ew"),
                dtype=FEATURE_DTYPE,
                kind="feature",
                group="opp",
                stat=f"prev_team_{s}",
                window="ew",
                variant="carry",
                sparse=True,
                note="EW share with the previous team (null without a team change in the data)",
            )
        )
    return cols


def eff_cols() -> list[Col]:
    return stat_cols("eff", EFF_STATS)


def attr_cols() -> list[Col]:
    cols = stat_cols("attr", [GAMES_STAT])
    sparse = {"new_team_flag": "null before the player's first game in the data"}
    for c in ATTR_POINT:
        cols.append(point_col("attr", c, sparse=c in sparse, note=sparse.get(c)))
    return cols


def teammates_out_cols() -> list[Col]:
    return [
        point_col(
            "avail", c, kind="avail", note="same-week injury report (Out or Doubtful teammates)"
        )
        for c in TEAMMATES_OUT
    ]


def team_out_cols() -> list[Col]:
    return [
        point_col(
            "avail", c, kind="avail", note="same-week injury report (Out or Doubtful players)"
        )
        for c in TEAM_OUT
    ]


# --------------------------------------------------------------------------- #
# Events and states
# --------------------------------------------------------------------------- #


def player_events(ctx: FeatureContext) -> pl.DataFrame:
    """Played player-games with team denominators and derived numerators, plus team stint ids."""
    if "player_events" in ctx.cache:
        return ctx.cache["player_events"]
    pg = ctx.player_game.filter(pl.col("played"))
    drop = [c for c in pg.columns if c.startswith("team_")]
    den = team_denominators(ctx)
    f = pl.Float64
    ev = (
        pg.drop(drop)
        .join(den, on=["team", "season", "week"], how="left")
        .sort("player_id", "kickoff_utc")
        .with_columns(
            (pl.col("carries") + pl.col("receptions")).alias("touches"),
            (pl.col("offense_pct") * pl.col("team_pass_rate_game")).alias("route_num"),
            (pl.col("dropbacks") + pl.col("designed_rush")).alias("designed_rush_den"),
            (pl.col("cpoe") * pl.col("pass_att").cast(f)).alias("cpoe_num"),
            pl.when(pl.col("cpoe").is_not_null())
            .then(pl.col("pass_att"))
            .otherwise(None)
            .alias("cpoe_att"),
            (pl.col("rec_yds").cast(f) - pl.col("rec_yards_exp")).alias("ame_rec_yds"),
            (
                (pl.col("rec_td") + pl.col("rush_td")).cast(f)
                - pl.col("rec_td_exp")
                - pl.col("rush_td_exp")
            ).alias("ame_td"),
            pl.lit(1.0).alias("_one"),
            (pl.col("team") != pl.col("team").shift(1).over("player_id"))
            .fill_null(True)
            .cast(pl.Int32)
            .cum_sum()
            .over("player_id")
            .alias("_stint"),
        )
    )
    ctx.cache["player_events"] = ev
    return ev


def _player_states(ctx: FeatureContext):
    if "player_states" in ctx.cache:
        return ctx.cache["player_states"]
    ev = player_events(ctx)
    carry = [
        pl.col("team").alias("_pop_team"),
        pl.col("_stint").alias("_pop_stint"),
        pl.col("week").alias("_pop_week"),
    ]
    opp_state = compute_state(
        ev, ["player_id"], OPP_STATS, "opp", "pop", reset_by=["_stint"], carry=carry
    )
    # previous stint's last EW share, broadcast to every state row of the next stint
    ew_names = {s: fname("opp", s, "ew") for s in PREV_TEAM_SHARES}
    last = opp_state.select("player_id", "_pop_stint", *ew_names.values()).unique(
        subset=["player_id", "_pop_stint"], keep="last", maintain_order=True
    )
    prev = last.select(
        "player_id",
        "_pop_stint",
        *[
            pl.col(c).shift(1).over("player_id").alias(f"_pop_prev_{s}")
            for s, c in ew_names.items()
        ],
    )
    opp_state = opp_state.join(prev, on=["player_id", "_pop_stint"], how="left")
    opp_ps = season_totals(ev, ["player_id"], OPP_STATS, "pop")
    opp_pr = priors(ev, ["position"], OPP_STATS, "pop", order=["kickoff_utc", "player_id"])

    eff_stats = [*EFF_STATS, GAMES_STAT]
    eff_state = compute_state(ev, ["player_id"], eff_stats, "eff", "pef")
    eff_ps = season_totals(ev, ["player_id"], eff_stats, "pef")
    eff_pr = priors(ev, ["position"], eff_stats, "pef", order=["kickoff_utc", "player_id"])
    ctx.cache["player_states"] = (opp_state, opp_ps, opp_pr, eff_state, eff_ps, eff_pr)
    return ctx.cache["player_states"]


def attach_player_windows(rows: pl.DataFrame, ctx: FeatureContext) -> pl.DataFrame:
    """Add ``opp__``, ``eff__`` and ``attr__games`` windows plus team-change features.

    ``rows`` needs ``_rid, player_id, team, position, season, week, cutoff_ts``.
    """
    opp_state, opp_ps, opp_pr, eff_state, eff_ps, eff_pr = _player_states(ctx)
    cal = ctx.calendar.select("season", "week", "cal_idx")

    df = asof_join(rows, opp_state, ["player_id"], ["player_id"], "pop")
    df = join_ps(df, opp_ps, ["player_id"], ["player_id"])
    df = join_priors(df, opp_pr, ["position"], "pop")
    same_team = pl.col("_pop_team") == pl.col("team")
    prev_exprs = [
        pl.when(pl.col("_pop_team").is_null())
        .then(None)
        .when(same_team)
        .then(pl.col(f"_pop_prev_{s}"))
        .otherwise(pl.col(fname("opp", s, "ew")))
        .alias(fname("opp", f"prev_team_{s}", "ew"))
        for s in PREV_TEAM_SHARES
    ]
    df = df.with_columns(
        *prev_exprs,
        pl.when(pl.col("_pop_team").is_null())
        .then(None)
        .otherwise((~same_team).cast(pl.Float64))
        .alias("attr__new_team_flag"),
    )
    # weeks since the last game played (calendar weeks)
    df = df.join(
        cal.rename({"season": "_pop_season", "week": "_pop_week", "cal_idx": "_pop_cal"}),
        on=["_pop_season", "_pop_week"],
        how="left",
    ).join(cal.rename({"cal_idx": "_row_cal"}), on=["season", "week"], how="left")
    df = df.with_columns(
        (pl.col("_row_cal") - pl.col("_pop_cal"))
        .cast(pl.Float64)
        .alias("attr__weeks_since_last_game")
    ).drop("_row_cal")
    df = finalize(df, [("opp", OPP_STATS)], "pop", keep=same_team)
    df = df.rename({ts_col("pop"): "meta__last_game_kickoff"})
    df = df.with_columns(
        (
            1.5 * pl.col(fname("opp", "target_share", "ew"))
            + 0.7 * pl.col(fname("opp", "air_yards_share", "ew"))
        ).alias(fname("opp", "wopr", "ew"))
    )

    df = asof_join(df, eff_state, ["player_id"], ["player_id"], "pef")
    df = join_ps(df, eff_ps, ["player_id"], ["player_id"])
    df = join_priors(df, eff_pr, ["position"], "pef")
    df = finalize(df, [("eff", EFF_STATS), ("attr", [GAMES_STAT])], "pef")
    return df.drop(ts_col("pef"))


def attach_attributes(rows: pl.DataFrame, ctx: FeatureContext) -> pl.DataFrame:
    """Static attributes from dim_player and ``attr__weeks_on_team`` from roster history."""
    dp = ctx.dim_player.select(
        "player_id", "birth_date", "rookie_season", "draft_round", "draft_pick", "height", "weight"
    ).unique("player_id", keep="first", maintain_order=True)
    df = rows.join(dp, on="player_id", how="left")
    f = pl.Float64
    years = (pl.col("season") - pl.col("rookie_season")).cast(f)
    undrafted = pl.col("draft_pick").is_null()
    known = pl.col("rookie_season").is_not_null()
    df = df.with_columns(
        (
            (pl.col("kickoff_utc").dt.date() - pl.col("birth_date")).dt.total_days().cast(f)
            / 365.25
        ).alias("attr__age"),
        years.alias("attr__years_exp"),
        (years == 0).cast(f).alias("attr__rookie_flag"),
        pl.when(undrafted & known)
        .then(float(PARAMS["undrafted_round"]))
        .otherwise(pl.col("draft_round").cast(f))
        .alias("attr__draft_round"),
        pl.when(undrafted & known)
        .then(float(PARAMS["undrafted_pick"]))
        .otherwise(pl.col("draft_pick").cast(f))
        .alias("attr__draft_pick"),
        pl.when(known).then(undrafted.cast(f)).otherwise(None).alias("attr__undrafted_flag"),
        pl.col("height").cast(f).alias("attr__height"),
        pl.col("weight").cast(f).alias("attr__weight"),
    ).drop("birth_date", "rookie_season", "draft_round", "draft_pick", "height", "weight")

    # consecutive team games on the roster (any row source) before the cutoff
    hist = (
        ctx.player_game.select("player_id", "team", "kickoff_utc")
        .sort("player_id", "kickoff_utc")
        .with_columns(
            (pl.col("team") != pl.col("team").shift(1).over("player_id"))
            .fill_null(True)
            .cast(pl.Int32)
            .cum_sum()
            .over("player_id")
            .alias("_run")
        )
        .with_columns(pl.col("kickoff_utc").cum_count().over("player_id", "_run").alias("_run_len"))
        .select(
            pl.col("player_id").alias("_wot_pid"),
            pl.col("team").alias("_wot_team"),
            "_run_len",
            pl.col("kickoff_utc").alias("_wot_key"),
        )
        .sort("_wot_key")
    )
    last = (
        df.select("_rid", "player_id", "team", "cutoff_ts")
        .sort("cutoff_ts")
        .join_asof(
            hist,
            left_on="cutoff_ts",
            right_on="_wot_key",
            by_left=["player_id"],
            by_right=["_wot_pid"],
            strategy="backward",
            allow_exact_matches=False,
            check_sortedness=False,
        )
        .select(
            "_rid",
            pl.when(pl.col("_wot_team") == pl.col("team"))
            .then(pl.col("_run_len").cast(f))
            .otherwise(0.0)
            .alias("attr__weeks_on_team"),
        )
    )
    return df.join(last, on="_rid", how="left")


# --------------------------------------------------------------------------- #
# Same-week teammates out (avail__, final-report variant and availability layer)
# --------------------------------------------------------------------------- #


def out_shares(ctx: FeatureContext) -> pl.DataFrame:
    """Per Out/Doubtful player-week: EW target, rush and dropback share with that team before cutoff."""
    if "out_shares" in ctx.cache:
        return ctx.cache["out_shares"]
    opp_state = _player_states(ctx)[0]
    pos = ctx.dim_player.select("player_id", "position").unique(
        "player_id", keep="first", maintain_order=True
    )
    inj = (
        ctx.injury.filter(pl.col("report_status").is_in(list(OUT_STATUSES)))
        .select("season", "week", "team", "player_id")
        .unique(subset=["season", "week", "team", "player_id"], keep="first", maintain_order=True)
        .join(pos, on="player_id", how="left")
        .join(ctx.calendar.select("season", "week", "cutoff_ts"), on=["season", "week"], how="left")
        .filter(pl.col("cutoff_ts").is_not_null())
    )
    cols = {
        "target_share": "_o_tgt",
        "rush_share": "_o_rush",
        "dropback_share": "_o_db",
    }
    st = opp_state.select(
        "player_id",
        ts_col("pop"),
        season_col_name(),
        "_pop_team",
        *[fname("opp", s, "ew") for s in cols],
    )
    j = asof_join(inj, st, ["player_id"], ["player_id"], "pop")
    same = (pl.col("_pop_team") == pl.col("team")).fill_null(False)
    out = j.select(
        "season",
        "week",
        "team",
        "player_id",
        "position",
        *[
            pl.when(same).then(pl.col(fname("opp", s, "ew"))).otherwise(0.0).fill_null(0.0).alias(c)
            for s, c in cols.items()
        ],
    )
    ctx.cache["out_shares"] = out
    return out


def season_col_name() -> str:
    return "_pop_season"


def team_out_totals(ctx: FeatureContext) -> pl.DataFrame:
    """Per team-week sums of the Out/Doubtful players' EW shares (ordered sums)."""
    out = out_shares(ctx)
    return ordered_sum(
        out, ["season", "week", "team"], ["player_id"], ["_o_tgt", "_o_rush", "_o_db"]
    )


def teammates_out(rows: pl.DataFrame, ctx: FeatureContext) -> pl.DataFrame:
    """``avail__teammates_out_*``: Out/Doubtful teammates this week, excluding the player himself."""
    out = out_shares(ctx)
    totals = team_out_totals(ctx)
    pos_counts = out.group_by("season", "week", "team", "position").agg(
        pl.len().cast(pl.Float64).alias("_o_pos_n")
    )
    own = (
        out.select("season", "week", "team", "player_id", "_o_tgt", "_o_rush")
        .rename({"_o_tgt": "_own_tgt", "_o_rush": "_own_rush"})
        .with_columns(pl.lit(1.0).alias("_own_out"))
    )
    keys = ["season", "week", "team"]
    df = (
        rows.join(totals.select(*keys, "_o_tgt", "_o_rush"), on=keys, how="left")
        .join(pos_counts, on=[*keys, "position"], how="left")
        .join(own, on=[*keys, "player_id"], how="left")
    )
    df = df.with_columns(
        (pl.col("_o_pos_n").fill_null(0.0) - pl.col("_own_out").fill_null(0.0)).alias(
            "avail__teammates_out_same_pos"
        ),
        (pl.col("_o_tgt").fill_null(0.0) - pl.col("_own_tgt").fill_null(0.0)).alias(
            "avail__teammates_out_target_share_ew"
        ),
        (pl.col("_o_rush").fill_null(0.0) - pl.col("_own_rush").fill_null(0.0)).alias(
            "avail__teammates_out_rush_share_ew"
        ),
    )
    return df.drop("_o_tgt", "_o_rush", "_o_pos_n", "_own_tgt", "_own_rush", "_own_out")


def team_out(rows: pl.DataFrame, ctx: FeatureContext, by: str = "team") -> pl.DataFrame:
    """``avail__out_*_share_ew`` for team-level rows (team identified by column ``by``)."""
    totals = team_out_totals(ctx).rename(
        {
            "team": by,
            "_o_tgt": "avail__out_target_share_ew",
            "_o_rush": "avail__out_rush_share_ew",
            "_o_db": "avail__out_dropback_share_ew",
        }
    )
    df = rows.join(totals, on=["season", "week", by], how="left")
    return df.with_columns([pl.col(c).fill_null(0.0) for c in [f"avail__{c}" for c in TEAM_OUT]])


__all__ = [
    "EFF_STATS",
    "GAMES_STAT",
    "OPP_STATS",
    "attach_attributes",
    "attach_player_windows",
    "attr_cols",
    "eff_cols",
    "opp_cols",
    "player_events",
    "team_out",
    "team_out_cols",
    "teammates_out",
    "teammates_out_cols",
]
