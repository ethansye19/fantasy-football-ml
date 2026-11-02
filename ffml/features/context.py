"""Game context (group A), depth chart as of ``asof_ts``, lagged injury history, ``avail__`` inputs.

Timing rules:

* Game context comes from the row's own scheduled game in dim_game (lines are
  the schedule snapshot's closing lines, D-008). ``temp`` and ``wind`` are
  actual game values in history and are flagged as sensitivity-only columns.
* Depth charts: for each row the latest fact_depth_chart snapshot of the row's
  team with ``snapshot_ts <= asof_ts`` (D-046). Legacy weekly charts carry the
  synthetic time "first kickoff of the week minus 48 h" (D-037), so for
  2016-2024 the chart labelled with the row's own week is the one selected.
* Lagged injury features use reports of earlier weeks only: the report for the
  team's previous game week, weeks since the last ``Out`` designation, and
  games missed among the team's last five games of the season.
* Same-week injury designations, practice status, roster status and
  depth-chart presence go to ``avail__`` columns (D-006, D-047).
"""

from __future__ import annotations

import polars as pl

from ffml.features.data import FeatureContext
from ffml.features.spec import DEPTH_VARIANTS, FEATURE_DTYPE, PARAMS, Col, point_col

# --------------------------------------------------------------------------- #
# A. Game context
# --------------------------------------------------------------------------- #

TURF = ("matrixturf", "astroturf", "fieldturf", "a_turf", "sportturf")
REPORT_ORD = {"Out": 3, "Doubtful": 2, "Questionable": 1}

CONTEXT_FEATURES: list[str] = [
    "week",
    "week_of_season_frac",
    "postseason",
    "home",
    "spread",
    "total_line",
    "implied_team_total",
    "implied_opp_total",
    "favorite_flag",
    "roof_dome",
    "roof_outdoors",
    "surface_turf",
    "rest_days",
    "opp_rest_days",
    "short_week",
    "off_bye",
    "div_game",
    "primetime",
]
CONTEXT_SENSITIVITY: list[str] = ["temp", "wind"]


def context_cols() -> list[Col]:
    cols = [point_col("ctx", c) for c in CONTEXT_FEATURES]
    cols += [
        point_col(
            "ctx", c, kind="sensitivity", sparse=True, note="actual game value; sensitivity only"
        )
        for c in CONTEXT_SENSITIVITY
    ]
    return cols


def game_context(rows: pl.DataFrame, ctx: FeatureContext) -> pl.DataFrame:
    """Add ``ctx__`` columns from the row's game (team perspective via ``home``)."""
    g = ctx.dim_game.select(
        "game_id",
        "spread_line",
        "total_line",
        "roof",
        "surface",
        "home_rest",
        "away_rest",
        "div_game",
        "temp",
        "wind",
        pl.col("kickoff_utc").alias("_g_kickoff"),
    )
    cal = ctx.calendar.select("season", "week", "n_reg_weeks")
    df = rows.join(g, on="game_id", how="left").join(cal, on=["season", "week"], how="left")
    home = pl.col("home")
    spread = pl.when(home).then(-pl.col("spread_line")).otherwise(pl.col("spread_line"))
    rest = pl.when(home).then(pl.col("home_rest")).otherwise(pl.col("away_rest")).cast(pl.Float64)
    opp_rest = (
        pl.when(home).then(pl.col("away_rest")).otherwise(pl.col("home_rest")).cast(pl.Float64)
    )
    surface = pl.col("surface").str.strip_chars().str.to_lowercase()
    roof = pl.col("roof")
    kick_et_hour = pl.col("_g_kickoff").dt.convert_time_zone("America/New_York").dt.hour()

    def flag(expr: pl.Expr) -> pl.Expr:
        return expr.cast(pl.Float64)

    exprs = {
        "week": pl.col("week").cast(pl.Float64),
        "week_of_season_frac": pl.col("week") / pl.col("n_reg_weeks"),
        "postseason": flag(pl.col("game_type") != "REG"),
        "home": flag(home),
        "spread": spread,
        "total_line": pl.col("total_line"),
        "implied_team_total": pl.col("total_line") / 2 - spread / 2,
        "implied_opp_total": pl.col("total_line") / 2 + spread / 2,
        "favorite_flag": pl.when(spread.is_null()).then(None).otherwise(flag(spread < 0)),
        "roof_dome": pl.when(roof.is_null())
        .then(None)
        .otherwise(flag(roof.is_in(["dome", "closed"]))),
        "roof_outdoors": pl.when(roof.is_null())
        .then(None)
        .otherwise(flag(roof.is_in(["outdoors", "open"]))),
        "surface_turf": pl.when(surface.is_null() | (surface == ""))
        .then(None)
        .otherwise(flag(surface.is_in(list(TURF)))),
        "rest_days": rest,
        "opp_rest_days": opp_rest,
        "short_week": flag(rest <= PARAMS["short_week_max_rest"]),
        "off_bye": flag(rest >= PARAMS["off_bye_min_rest"]),
        "div_game": pl.col("div_game").cast(pl.Float64),
        "primetime": flag(kick_et_hour >= PARAMS["primetime_hour_et"]),
        "temp": pl.col("temp"),
        "wind": pl.col("wind"),
    }
    out = df.with_columns([e.alias(f"ctx__{k}") for k, e in exprs.items()])
    return out.drop(
        "spread_line",
        "total_line",
        "roof",
        "surface",
        "home_rest",
        "away_rest",
        "div_game",
        "temp",
        "wind",
        "_g_kickoff",
        "n_reg_weeks",
    )


# --------------------------------------------------------------------------- #
# Depth chart as of asof_ts
# --------------------------------------------------------------------------- #

DEPTH_FEATURES: list[str] = [
    "rank",
    "players_ahead",
    "starter_flag",
    "rank_change",
    "same_pos_count",
    "chart_age_days",
]


def depth_cols() -> list[Col]:
    cols = [point_col("depth", c) for c in DEPTH_FEATURES]
    cols[DEPTH_FEATURES.index("rank_change")] = point_col(
        "depth", "rank_change", sparse=True, note="null in week 1 and when unranked in either week"
    )
    cols[DEPTH_FEATURES.index("chart_age_days")] = point_col(
        "depth",
        "chart_age_days",
        note="asof_ts minus the chart's snapshot_ts (legacy charts: synthetic time, D-037)",
    )
    return cols


def _snapshot_index(ctx: FeatureContext, variant: str) -> pl.DataFrame:
    """Distinct team snapshots with the time from which each may be used.

    Timestamped charts (2025+) are usable from their ``snapshot_ts``. Legacy
    weekly charts (2016-2024) carry only a synthetic time (D-037). Under the
    ``primary`` variant (D-059, FEATURES.md ground rule 1) the chart labelled
    week w becomes usable at the next calendar week's historical as-of time,
    so week w rows read the week w-1 chart and week 1 reads the previous
    season's last chart. Under ``same_week`` it is usable from its synthetic
    ``snapshot_ts``, i.e. for its own week.
    """
    if variant not in DEPTH_VARIANTS:
        raise ValueError(f"unknown depth variant {variant!r}; expected one of {DEPTH_VARIANTS}")
    key = f"depth_index_{variant}"
    if key in ctx.cache:
        return ctx.cache[key]
    snaps = ctx.depth.select("team", "snapshot_ts", "snapshot_kind", "season", "week").unique(
        subset=["team", "snapshot_ts"], keep="first", maintain_order=True
    )
    legacy = snaps.filter(pl.col("snapshot_kind") == "weekly")
    stamped = snaps.filter(pl.col("snapshot_kind") != "weekly").with_columns(
        pl.col("snapshot_ts").alias("_avail_ts")
    )
    if variant == "primary":
        cal = ctx.calendar.select("season", "week", "cal_idx")
        nxt = ctx.calendar.select(
            (pl.col("cal_idx") - 1).alias("cal_idx"), pl.col("asof_hist_ts").alias("_avail_ts")
        )
        legacy = (
            legacy.join(cal, on=["season", "week"], how="left")
            .join(nxt, on="cal_idx", how="left")
            .drop("cal_idx")
        )
    else:
        legacy = legacy.with_columns(pl.col("snapshot_ts").alias("_avail_ts"))
    out = pl.concat([stamped, legacy], how="vertical_relaxed").select(
        "team", "snapshot_ts", "snapshot_kind", "_avail_ts"
    )
    ctx.cache[key] = out
    return out


def select_snapshots(
    ctx: FeatureContext, pairs: pl.DataFrame, asof_col: str, variant: str = "primary"
) -> pl.DataFrame:
    """For each ``(team, asof)`` pair the depth chart to use.

    The latest timestamped snapshot with ``snapshot_ts <= asof`` when one
    exists, otherwise the latest legacy chart usable at ``asof`` under
    ``variant`` (see :func:`_snapshot_index`).
    """
    idx = _snapshot_index(ctx, variant)
    out = pairs.select("team", asof_col).unique().drop_nulls()
    for legacy, tag in ((False, "_st"), (True, "_lg")):
        right = (
            idx.filter((pl.col("snapshot_kind") == "weekly") == legacy)
            .select(
                "team",
                pl.col("snapshot_ts").alias(f"{tag}_snap"),
                pl.col("_avail_ts").alias(f"{tag}_key"),
            )
            .drop_nulls()
            .sort(f"{tag}_key")
        )
        out = out.sort(asof_col).join_asof(
            right,
            left_on=asof_col,
            right_on=f"{tag}_key",
            by="team",
            strategy="backward",
            allow_exact_matches=True,
            check_sortedness=False,
        )
    return out.select(
        "team", asof_col, pl.coalesce("_st_snap", "_lg_snap").alias("depth_snapshot_ts")
    )


def depth_ranks(ctx: FeatureContext, selected: pl.DataFrame) -> pl.DataFrame:
    """Harmonised ranks within each selected team snapshot and position group.

    ``depth_rank`` = 1 + number of same-group teammates with a strictly better
    listed rank (legacy charts list several WRs at rank 1; 2025+ charts number
    WRs 1..n). ``same_pos_count`` = players listed in the group.
    """
    snaps = (
        selected.select("team", pl.col("depth_snapshot_ts").alias("snapshot_ts"))
        .unique()
        .drop_nulls()
    )
    d = ctx.depth.join(snaps, on=["team", "snapshot_ts"], how="semi")
    best = d.group_by("team", "snapshot_ts", "player_id", "pos_group").agg(
        pl.col("pos_rank").min().alias("best_rank")
    )
    part = ["team", "snapshot_ts", "pos_group"]
    return best.with_columns(
        pl.col("best_rank").rank("min").over(part).cast(pl.Int32).alias("depth_rank"),
        pl.len().over(part).cast(pl.Int32).alias("same_pos_count"),
    ).rename({"snapshot_ts": "depth_snapshot_ts"})


def qb1_at(
    ctx: FeatureContext, pairs: pl.DataFrame, asof_col: str, variant: str = "primary"
) -> pl.DataFrame:
    """Depth-chart QB1 (lowest player_id among rank-1 QBs) per ``(team, asof)``."""
    sel = select_snapshots(ctx, pairs, asof_col, variant)
    ranks = depth_ranks(ctx, sel).filter(pl.col("pos_group") == "QB", pl.col("depth_rank") == 1)
    qb1 = (
        ranks.sort("player_id")
        .unique(subset=["team", "depth_snapshot_ts"], keep="first", maintain_order=True)
        .select("team", "depth_snapshot_ts", pl.col("player_id").alias("qb1_id"))
    )
    return sel.join(qb1, on=["team", "depth_snapshot_ts"], how="left").select(
        "team", asof_col, "qb1_id", "depth_snapshot_ts"
    )


def _rank_lookup(
    ctx: FeatureContext, rows: pl.DataFrame, asof_col: str, variant: str
) -> pl.DataFrame:
    """Per row (``_rid``): selected snapshot, rank in the player's position group, presence."""
    sel = select_snapshots(ctx, rows, asof_col, variant)
    ranks = depth_ranks(ctx, sel)
    present = (
        ranks.select("team", "depth_snapshot_ts", "player_id")
        .unique()
        .with_columns(pl.lit(1.0).alias("_present"))
    )
    group_size = ranks.select("team", "depth_snapshot_ts", "pos_group", "same_pos_count").unique()
    df = rows.select("_rid", "team", "player_id", "position", asof_col).join(
        sel, on=["team", asof_col], how="left"
    )
    df = df.join(
        ranks.select("team", "depth_snapshot_ts", "player_id", "pos_group", "depth_rank"),
        left_on=["team", "depth_snapshot_ts", "player_id", "position"],
        right_on=["team", "depth_snapshot_ts", "player_id", "pos_group"],
        how="left",
    )
    df = df.join(
        group_size,
        left_on=["team", "depth_snapshot_ts", "position"],
        right_on=["team", "depth_snapshot_ts", "pos_group"],
        how="left",
    )
    df = df.join(present, on=["team", "depth_snapshot_ts", "player_id"], how="left")
    return df.select(
        "_rid",
        "depth_snapshot_ts",
        "depth_rank",
        "same_pos_count",
        pl.when(pl.col("depth_snapshot_ts").is_null())
        .then(None)
        .otherwise(pl.col("_present").fill_null(0.0))
        .alias("_present"),
    )


def depth_features(
    rows: pl.DataFrame, ctx: FeatureContext, variant: str = "primary"
) -> pl.DataFrame:
    """Add ``depth__`` columns, ``avail__depth_chart_present`` and ``meta__depth_snapshot_ts``.

    ``rows`` needs ``_rid, team, player_id, position, season, week, asof_ts``.
    ``depth__rank_change`` compares with the chart selected at the previous
    week's historical as-of time (same season; positive means moved down).
    ``variant`` selects the legacy-chart timing (D-059).
    """
    n_start = PARAMS["n_starters"]
    now = _rank_lookup(ctx, rows, "asof_ts", variant)
    cal = ctx.calendar.select("season", "week", "asof_hist_ts").sort("season", "week")
    prev_cal = cal.with_columns(
        pl.col("asof_hist_ts").shift(1).over("season").alias("_prev_asof")
    ).select("season", "week", "_prev_asof")
    prev_rows = rows.select("_rid", "team", "player_id", "position", "season", "week").join(
        prev_cal, on=["season", "week"], how="left"
    )
    prev = _rank_lookup(ctx, prev_rows, "_prev_asof", variant).select(
        "_rid", pl.col("depth_rank").alias("_prev_rank")
    )
    starters = pl.col("position").replace_strict(n_start, default=1, return_dtype=pl.Int32)
    df = rows.join(now, on="_rid", how="left").join(prev, on="_rid", how="left")
    rank = pl.col("depth_rank").cast(pl.Float64)
    df = df.with_columns(
        rank.alias("depth__rank"),
        (rank - 1.0).alias("depth__players_ahead"),
        pl.when(rank.is_null())
        .then(pl.when(pl.col("depth_snapshot_ts").is_null()).then(None).otherwise(0.0))
        .otherwise((rank <= starters).cast(pl.Float64))
        .alias("depth__starter_flag"),
        (rank - pl.col("_prev_rank").cast(pl.Float64)).alias("depth__rank_change"),
        pl.col("same_pos_count").cast(pl.Float64).alias("depth__same_pos_count"),
        ((pl.col("asof_ts") - pl.col("depth_snapshot_ts")).dt.total_seconds() / 86400.0).alias(
            "depth__chart_age_days"
        ),
        pl.col("_present").alias("avail__depth_chart_present"),
        pl.col("depth_snapshot_ts").alias("meta__depth_snapshot_ts"),
    )
    return df.drop("depth_snapshot_ts", "depth_rank", "same_pos_count", "_present", "_prev_rank")


# --------------------------------------------------------------------------- #
# Lagged injury history (regular features) and same-week avail__ inputs
# --------------------------------------------------------------------------- #

INJURY_FEATURES: list[str] = ["inj_prev_status", "inj_weeks_since_out", "games_missed"]


def injury_cols() -> list[Col]:
    return [
        point_col(
            "attr",
            "inj_prev_status",
            note="report of the team's previous game week: 0 none, 1 Q, 2 D, 3 Out",
        ),
        point_col("attr", "inj_weeks_since_out", note=f"capped at {PARAMS['weeks_since_out_cap']}"),
        point_col(
            "attr",
            "games_missed",
            window="l5",
            note="team games this season before cutoff, of the last 5",
        ),
    ]


def _status_ord() -> pl.Expr:
    return pl.col("report_status").replace_strict(REPORT_ORD, default=0, return_dtype=pl.Int32)


def _injury_weekly(ctx: FeatureContext) -> pl.DataFrame:
    cal = ctx.calendar.select("season", "week", "cal_idx", "week_end_ts")
    inj = ctx.injury.join(cal, on=["season", "week"], how="left").with_columns(
        _status_ord().alias("_ord"),
        pl.coalesce("report_modified_at", "week_end_ts").alias("_info_ts"),
    )
    return inj.group_by("player_id", "season", "week", "cal_idx").agg(
        pl.col("_ord").max(), pl.col("_info_ts").max()
    )


def injury_history(rows: pl.DataFrame, ctx: FeatureContext) -> pl.DataFrame:
    """Add lagged injury features and ``meta__inj_info_ts`` (latest report time used)."""
    weekly = _injury_weekly(ctx)
    cal = ctx.calendar.select("season", "week", "cal_idx")
    base = rows.select("_rid", "player_id", "team", "season", "week", "cutoff_ts").join(
        cal, on=["season", "week"], how="left"
    )

    # previous game week of the row's team within the season
    tg = (
        ctx.team_game.select(
            "team",
            pl.col("season").alias("_pg_season"),
            pl.col("week").alias("_pg_week"),
            "kickoff_utc",
        )
        .with_columns(pl.col("kickoff_utc").alias("_pg_key"))
        .sort("_pg_key")
    )
    prev = base.sort("cutoff_ts").join_asof(
        tg.drop("kickoff_utc"),
        left_on="cutoff_ts",
        right_on="_pg_key",
        by="team",
        strategy="backward",
        allow_exact_matches=False,
        check_sortedness=False,
    )
    prev = prev.join(
        weekly.select(
            "player_id",
            pl.col("season").alias("_pg_season"),
            pl.col("week").alias("_pg_week"),
            pl.col("_ord").alias("_prev_ord"),
            pl.col("_info_ts").alias("_prev_info"),
        ),
        on=["player_id", "_pg_season", "_pg_week"],
        how="left",
    )
    same_season = (pl.col("_pg_season") == pl.col("season")).fill_null(False)
    prev = prev.select(
        "_rid",
        pl.when(same_season)
        .then(pl.col("_prev_ord").fill_null(0).cast(pl.Float64))
        .otherwise(None)
        .alias("attr__inj_prev_status"),
        pl.when(same_season).then(pl.col("_prev_info")).otherwise(None).alias("_prev_info"),
    )

    # weeks since the last Out designation (earlier weeks only)
    outs = (
        weekly.filter(pl.col("_ord") == 3)
        .select(
            "player_id", pl.col("cal_idx").alias("_out_idx"), pl.col("_info_ts").alias("_out_info")
        )
        .with_columns(pl.col("_out_idx").alias("_out_key"))
        .sort("_out_key")
    )
    cap = float(PARAMS["weeks_since_out_cap"])
    since = (
        base.sort("cal_idx")
        .join_asof(
            outs,
            left_on="cal_idx",
            right_on="_out_key",
            by="player_id",
            strategy="backward",
            allow_exact_matches=False,
            check_sortedness=False,
        )
        .select(
            "_rid",
            pl.min_horizontal(
                (pl.col("cal_idx") - pl.col("_out_idx")).cast(pl.Float64), pl.lit(cap)
            )
            .fill_null(cap)
            .alias("attr__inj_weeks_since_out"),
            "_out_info",
        )
    )

    missed = games_missed(rows, ctx)
    out = (
        rows.join(prev, on="_rid", how="left")
        .join(since, on="_rid", how="left")
        .join(missed, on="_rid", how="left")
    )
    return out.with_columns(
        pl.max_horizontal("_prev_info", "_out_info").alias("meta__inj_info_ts")
    ).drop("_prev_info", "_out_info")


def games_missed(rows: pl.DataFrame, ctx: FeatureContext) -> pl.DataFrame:
    """Of the row team's last five games this season before cutoff, games the player did not play.

    A game counts as played when the player has ``played = true`` for any team
    in that season-week (a traded player is not counted as missing).
    """
    window = int(PARAMS["games_missed_window"])
    tg = ctx.team_game.select("team", "season", "week", "kickoff_utc").sort(
        "team", "season", "kickoff_utc"
    )
    tg = tg.with_columns(
        pl.col("kickoff_utc").cum_count().over("team", "season").cast(pl.Int32).alias("_tg_idx")
    )
    last = (
        rows.select("_rid", "player_id", "team", "season", "cutoff_ts")
        .sort("cutoff_ts")
        .join_asof(
            tg.select(
                "team",
                pl.col("season").alias("_l_season"),
                "_tg_idx",
                pl.col("kickoff_utc").alias("_tg_key"),
            ).sort("_tg_key"),
            left_on="cutoff_ts",
            right_on="_tg_key",
            by="team",
            strategy="backward",
            allow_exact_matches=False,
            check_sortedness=False,
        )
        .with_columns(
            pl.when(pl.col("_l_season") == pl.col("season"))
            .then(pl.col("_tg_idx"))
            .otherwise(0)
            .fill_null(0)
            .alias("_n")
        )
    )
    offsets = pl.DataFrame({"_off": list(range(window))}, schema={"_off": pl.Int32})
    exp = last.select("_rid", "player_id", "team", "season", "_n").join(offsets, how="cross")
    exp = exp.with_columns((pl.col("_n") - pl.col("_off")).alias("_tg_idx")).filter(
        pl.col("_tg_idx") >= 1
    )
    exp = exp.join(
        tg.select("team", "season", "_tg_idx", "week"), on=["team", "season", "_tg_idx"], how="left"
    )
    played = (
        ctx.player_game.filter(pl.col("played"))
        .select("player_id", "season", "week")
        .unique()
        .with_columns(pl.lit(1).alias("_pl"))
    )
    exp = exp.join(played, on=["player_id", "season", "week"], how="left")
    agg = exp.group_by("_rid").agg(
        (pl.col("_pl").is_null()).sum().cast(pl.Float64).alias("attr__games_missed__l5")
    )
    return (
        rows.select("_rid")
        .join(agg, on="_rid", how="left")
        .with_columns(pl.col("attr__games_missed__l5").fill_null(0.0))
    )


AVAIL_STRING = ["report_status", "practice_status", "roster_status"]
AVAIL_NUMERIC = ["report_status_ord", "on_injury_report", "depth_chart_present"]


def avail_cols() -> list[Col]:
    cols = [
        point_col(
            "avail",
            c,
            kind="avail",
            dtype="String",
            sparse=True,
            note="same-week; not a primary feature",
        )
        for c in AVAIL_STRING
    ]
    cols += [
        point_col(
            "avail", c, kind="avail", dtype=FEATURE_DTYPE, note="same-week; not a primary feature"
        )
        for c in AVAIL_NUMERIC
    ]
    return cols


def avail_columns(rows: pl.DataFrame, ctx: FeatureContext) -> pl.DataFrame:
    """Same-week injury report and roster status for the row's player and team."""
    inj = ctx.injury.select(
        "season",
        "week",
        "team",
        "player_id",
        pl.col("report_status").alias("avail__report_status"),
        pl.col("practice_status")
        .str.strip_chars()
        .replace("", None)
        .alias("avail__practice_status"),
        _status_ord().cast(pl.Float64).alias("avail__report_status_ord"),
        pl.lit(1.0).alias("avail__on_injury_report"),
    ).unique(subset=["season", "week", "team", "player_id"], keep="first", maintain_order=True)
    ros = ctx.roster.select(
        "season", "week", "team", "player_id", pl.col("status").alias("avail__roster_status")
    ).unique(subset=["season", "week", "team", "player_id"], keep="first", maintain_order=True)
    keys = ["season", "week", "team", "player_id"]
    out = rows.join(inj, on=keys, how="left").join(ros, on=keys, how="left")
    return out.with_columns(
        pl.col("avail__report_status_ord").fill_null(0.0),
        pl.col("avail__on_injury_report").fill_null(0.0),
    )


__all__ = [
    "avail_cols",
    "avail_columns",
    "context_cols",
    "depth_cols",
    "depth_features",
    "game_context",
    "games_missed",
    "injury_cols",
    "injury_history",
    "qb1_at",
    "select_snapshots",
]
