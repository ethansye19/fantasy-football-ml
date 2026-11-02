"""Target frames: one row per entity-game with keys, cutoff/as-of times and ``y_`` outcomes.

Historical targets come from the fact tables (REG and POST). Future targets
are built from the schedule and the latest weekly roster with every ``y_``
column null; the builders accept either, so the predictor can pass future
rows through exactly the same code (``build_player_week(ctx, targets)``).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

import polars as pl

from ffml.features.data import FeatureContext
from ffml.features.spec import POSITIONS
from ffml.features.windows import ratio

log = logging.getLogger(__name__)

PLAYER_KEYS: list[str] = [
    "player_id",
    "season",
    "week",
    "game_id",
    "team",
    "opponent",
    "position",
    "game_type",
    "home",
    "kickoff_utc",
]
TEAM_KEYS: list[str] = [
    "team",
    "season",
    "week",
    "game_id",
    "opponent",
    "game_type",
    "home",
    "kickoff_utc",
]
TIME_COLS: list[str] = ["cutoff_ts", "asof_ts"]

#: raw counting outcomes copied from fact_player_game as ``y_<name>``
PLAYER_COUNT_TARGETS: list[str] = [
    "pass_att",
    "completions",
    "pass_yds",
    "pass_td",
    "interceptions",
    "sacks",
    "sack_yards",
    "pass_air_yards",
    "dropbacks",
    "carries",
    "rush_yds",
    "rush_td",
    "rush_first_downs",
    "designed_rush",
    "scrambles",
    "targets",
    "receptions",
    "rec_yds",
    "rec_td",
    "rec_air_yards",
    "rec_yac",
    "rz_targets",
    "rz_carries",
    "gl_carries",
    "ez_targets",
    "fg_att",
    "fg_made",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_40_49",
    "fg_made_50_59",
    "fg_made_60p",
    "fg_made_0_39",
    "fg_made_50p",
    "fg_missed",
    "fg_blocked",
    "pat_att",
    "pat_made",
    "fumbles_lost",
    "two_pt_conversions",
    "return_tds",
]

#: (target name, numerator column, denominator column in the denominators frame)
PLAYER_SHARE_TARGETS: list[tuple[str, str, str]] = [
    ("target_share", "targets", "team_targets"),
    ("carry_share", "carries", "team_carries"),
    ("gl_carry_share", "gl_carries", "team_gl_carries"),
    ("rz_target_share", "rz_targets", "team_rz_targets"),
    ("pass_td_share", "rec_td", "team_pass_td"),
    ("rush_td_share", "rush_td", "team_rush_td"),
    ("dropback_share", "dropbacks", "team_dropbacks"),
]

#: stage 3 efficiency outcomes (numerator, denominator in fact_player_game)
PLAYER_RATE_TARGETS: list[tuple[str, str, str]] = [
    ("catch_rate", "receptions", "targets"),
    ("yards_per_target", "rec_yds", "targets"),
    ("yards_per_carry", "rush_yds", "carries"),
    ("yards_per_attempt", "pass_yds", "pass_att"),
    ("completion_rate", "completions", "pass_att"),
    ("int_per_attempt", "interceptions", "pass_att"),
]

PLAYER_OTHER_TARGETS: list[str] = [
    "played",
    "offense_snaps",
    "offense_pct",
    "fantasy_points_ppr_ref",
    "fantasy_points_std_ref",
]

TEAM_TARGETS: list[str] = [
    "plays",
    "pass_rate",
    "pass_att",
    "rush_att",
    "dropbacks",
    "sacks",
    "pass_td",
    "rush_td",
    "offensive_td",
    "points",
    "field_goal_att",
    "field_goal_made",
    "rz_trips",
    "interceptions",
    "fumbles_lost",
    "two_pt_att",
    "two_pt_conv",
]

DST_TARGETS: list[str] = [
    "sacks",
    "interceptions",
    "fumble_recoveries",
    "def_td",
    "return_td",
    "safeties",
    "blocked_kicks",
    "points_allowed",
    "yards_allowed",
]


def player_target_names() -> list[str]:
    names = [f"y_{c}" for c in PLAYER_OTHER_TARGETS]
    names += [f"y_{c}" for c in PLAYER_COUNT_TARGETS]
    names += [f"y_{n}" for n, _, _ in PLAYER_SHARE_TARGETS]
    names += [f"y_{n}" for n, _, _ in PLAYER_RATE_TARGETS]
    return names


def team_target_names() -> list[str]:
    return [f"y_{c}" for c in TEAM_TARGETS]


def dst_target_names() -> list[str]:
    return [f"y_{c}" for c in DST_TARGETS]


TARGET_NAMES: dict[str, list[str]] = {
    "player_week": player_target_names(),
    "team_week": team_target_names(),
    "dst_week": dst_target_names(),
}
KEYS: dict[str, list[str]] = {
    "player_week": PLAYER_KEYS,
    "team_week": TEAM_KEYS,
    "dst_week": TEAM_KEYS,
}


def team_denominators(ctx: FeatureContext) -> pl.DataFrame:
    """Team-game denominators for player shares (integer sums, exact).

    From fact_team_game: carries (official rush attempts), dropbacks, passing
    and rushing TDs. From fact_player_game: team targets and red-zone/goal-line
    totals as computed from pbp (max over the team's rows), and air yards and
    end-zone targets summed over the team's skill players.
    """
    tg = ctx.team_game.select(
        "team",
        "season",
        "week",
        pl.col("rush_att").cast(pl.Float64).alias("team_carries"),
        pl.col("dropbacks").cast(pl.Float64).alias("team_dropbacks"),
        pl.col("pass_td").cast(pl.Float64).alias("team_pass_td_tg"),
        pl.col("rush_td").cast(pl.Float64).alias("team_rush_td_tg"),
        pl.col("pass_rate").cast(pl.Float64).alias("team_pass_rate_game"),
    )
    pg = ctx.player_game.group_by("team", "season", "week").agg(
        pl.col("team_targets").max().cast(pl.Float64),
        pl.col("team_rz_targets").max().cast(pl.Float64),
        pl.col("team_rz_carries").max().cast(pl.Float64),
        pl.col("team_gl_carries").max().cast(pl.Float64),
        pl.col("team_pass_td").max().cast(pl.Float64),
        pl.col("team_rush_td").max().cast(pl.Float64),
        pl.col("rec_air_yards").cast(pl.Int64).sum().cast(pl.Float64).alias("team_air_yards"),
        pl.col("ez_targets").cast(pl.Int64).sum().cast(pl.Float64).alias("team_ez_targets"),
    )
    out = pg.join(tg, on=["team", "season", "week"], how="full", coalesce=True)
    return out.with_columns(
        pl.coalesce("team_pass_td_tg", "team_pass_td").alias("team_pass_td"),
        pl.coalesce("team_rush_td_tg", "team_rush_td").alias("team_rush_td"),
    ).drop("team_pass_td_tg", "team_rush_td_tg")


def _with_times(
    ctx: FeatureContext, df: pl.DataFrame, asof: datetime | None = None
) -> pl.DataFrame:
    cal = ctx.calendar.select("season", "week", "cutoff_ts", "asof_hist_ts")
    out = df.join(cal, on=["season", "week"], how="left")
    if asof is None:
        out = out.rename({"asof_hist_ts": "asof_ts"})
    else:
        out = out.drop("asof_hist_ts").with_columns(
            pl.lit(asof).cast(pl.Datetime("us", "UTC")).alias("asof_ts")
        )
    return out


def _f(expr: pl.Expr) -> pl.Expr:
    return expr.cast(pl.Float64)


def player_targets(ctx: FeatureContext, seasons: list[int] | None = None) -> pl.DataFrame:
    """Every fact_player_game row (REG and POST) with keys, times and ``y_`` outcomes."""
    pg = ctx.player_game
    if seasons is not None:
        pg = pg.filter(pl.col("season").is_in(seasons))
    den = team_denominators(ctx).select(
        "team", "season", "week", *{d for _, _, d in PLAYER_SHARE_TARGETS}
    )
    df = pg.join(den, on=["team", "season", "week"], how="left", suffix="_den")
    ys: list[pl.Expr] = [
        _f(pl.col("played")).alias("y_played"),
        _f(pl.col("offense_snaps")).alias("y_offense_snaps"),
        _f(pl.col("offense_pct")).alias("y_offense_pct"),
        _f(pl.col("fantasy_points_ppr_ref")).alias("y_fantasy_points_ppr_ref"),
        _f(pl.col("fantasy_points_std_ref")).alias("y_fantasy_points_std_ref"),
    ]
    ys += [_f(pl.col(c)).alias(f"y_{c}") for c in PLAYER_COUNT_TARGETS]
    for name, num, d in PLAYER_SHARE_TARGETS:
        dcol = f"{d}_den" if f"{d}_den" in df.columns else d
        ys.append(ratio(_f(pl.col(num)), _f(pl.col(dcol))).alias(f"y_{name}"))
    for name, num, d in PLAYER_RATE_TARGETS:
        ys.append(ratio(_f(pl.col(num)), _f(pl.col(d))).alias(f"y_{name}"))
    out = df.select(*PLAYER_KEYS, *ys)
    out = _with_times(ctx, out)
    return out.select(*PLAYER_KEYS, *TIME_COLS, *player_target_names()).sort(
        "season", "week", "team", "player_id"
    )


def team_targets(ctx: FeatureContext, seasons: list[int] | None = None) -> pl.DataFrame:
    """Every fact_team_game row with ``y_`` team outcomes (stage 1)."""
    tg = ctx.team_game
    if seasons is not None:
        tg = tg.filter(pl.col("season").is_in(seasons))
    tg = tg.with_columns((pl.col("pass_td") + pl.col("rush_td")).alias("offensive_td"))
    out = tg.select(*TEAM_KEYS, *[_f(pl.col(c)).alias(f"y_{c}") for c in TEAM_TARGETS])
    out = _with_times(ctx, out)
    return out.select(*TEAM_KEYS, *TIME_COLS, *team_target_names()).sort("season", "week", "team")


def dst_targets(ctx: FeatureContext, seasons: list[int] | None = None) -> pl.DataFrame:
    """Every fact_dst_game row with ``y_`` DST scoring inputs."""
    dst = ctx.dst_game
    if seasons is not None:
        dst = dst.filter(pl.col("season").is_in(seasons))
    out = dst.select(*TEAM_KEYS, *[_f(pl.col(c)).alias(f"y_{c}") for c in DST_TARGETS])
    out = _with_times(ctx, out)
    return out.select(*TEAM_KEYS, *TIME_COLS, *dst_target_names()).sort("season", "week", "team")


def historical_targets(
    ctx: FeatureContext, seasons: list[int] | None = None
) -> dict[str, pl.DataFrame]:
    return {
        "player_week": player_targets(ctx, seasons),
        "team_week": team_targets(ctx, seasons),
        "dst_week": dst_targets(ctx, seasons),
    }


def _null_targets(names: list[str]) -> list[pl.Expr]:
    return [pl.lit(None, dtype=pl.Float64).alias(n) for n in names]


def future_targets(
    season: int,
    week: int,
    ctx: FeatureContext | None = None,
    *,
    now: datetime | None = None,
    include_final: bool = False,
) -> dict[str, pl.DataFrame]:
    """Rows for the scheduled games of ``season``/``week`` with null ``y_`` columns.

    Teams come from dim_game (games not yet final unless ``include_final``);
    players from the latest fact_roster_week of the season at or before
    ``week`` with status ACT or INA and fantasy position QB/RB/WR/TE/K
    (dim_player position, roster position as fallback), crossed with their
    team's game. ``asof_ts`` is ``now`` (default: current UTC time) so depth
    charts and rosters are read as of the moment of prediction; ``cutoff_ts``
    stays the week's first kickoff.
    """
    if ctx is None:
        ctx = FeatureContext.from_store([season])
    now = now or datetime.now(UTC)
    games = ctx.team_games.filter(pl.col("season") == season, pl.col("week") == week)
    if not include_final:
        final = ctx.dim_game.filter(pl.col("status") == "final").select("game_id")
        games = games.join(final, on="game_id", how="anti")
    teams = games.select(*TEAM_KEYS).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )

    roster = ctx.roster.filter(pl.col("season") == season, pl.col("week") <= week)
    if roster.is_empty():
        log.warning("no fact_roster_week rows for %s up to week %s", season, week)
        players = pl.DataFrame(schema={k: teams.schema.get(k, pl.String) for k in PLAYER_KEYS})
    else:
        latest = int(roster["week"].max())
        roster = roster.filter(pl.col("week") == latest, pl.col("status").is_in(["ACT", "INA"]))
        pos = ctx.dim_player.select("player_id", pl.col("position").alias("dim_position"))
        roster = roster.join(pos, on="player_id", how="left").with_columns(
            pl.coalesce("dim_position", "position").alias("position")
        )
        roster = roster.filter(pl.col("position").is_in(list(POSITIONS)))
        players = roster.select("player_id", "team", "position").join(teams, on="team", how="inner")
        dup = players.filter(pl.col("player_id").is_duplicated())
        if not dup.is_empty():
            log.warning("%d future player rows on two teams; keeping the first team", dup.height)
            players = players.sort("player_id", "team").unique(
                "player_id", keep="first", maintain_order=True
            )
        players = players.select(*PLAYER_KEYS)
        log.info(
            "future_targets %s week %s: roster week %s, %d players",
            season,
            week,
            latest,
            players.height,
        )

    out = {
        "player_week": _with_times(ctx, players, asof=now)
        .with_columns(_null_targets(player_target_names()))
        .select(*PLAYER_KEYS, *TIME_COLS, *player_target_names())
        .sort("season", "week", "team", "player_id"),
        "team_week": _with_times(ctx, teams, asof=now)
        .with_columns(_null_targets(team_target_names()))
        .select(*TEAM_KEYS, *TIME_COLS, *team_target_names())
        .sort("season", "week", "team"),
        "dst_week": _with_times(ctx, teams, asof=now)
        .with_columns(_null_targets(dst_target_names()))
        .select(*TEAM_KEYS, *TIME_COLS, *dst_target_names())
        .sort("season", "week", "team"),
    }
    return out


__all__ = [
    "KEYS",
    "PLAYER_KEYS",
    "TARGET_NAMES",
    "TEAM_KEYS",
    "TIME_COLS",
    "dst_targets",
    "future_targets",
    "historical_targets",
    "player_targets",
    "team_denominators",
    "team_targets",
]
