"""``fact_player_game``: one row per skill player per game, including inactive weeks.

Base rows come from nflverse weekly ``player_stats`` (REG and POST) restricted to
QB, RB, WR, TE and K after the FB-to-RB mapping in ``dim_player``. Play-by-play
adds situational counts and team context, ``snap_counts`` adds participation,
``ff_opportunity`` adds expected values and the Next Gen Stats files add the
nullable tracking metrics. Rostered skill players (status ACT or INA) whose team
played but who have no stats row are appended with ``played = false`` and zeroed
counting stats so that availability can be modelled.

Primary key ``(player_id, season, week)``; a player with two stats rows in one
week (traded) keeps the row with more touches.
"""

from __future__ import annotations

import logging

import polars as pl

from ffml.data.process.base import (
    SKILL_POSITIONS,
    PartitionKey,
    ProcessContext,
    assert_unique,
    canonical_team,
    fantasy_position,
    read_raw_or_none,
    team_game_map,
)
from ffml.data.process.team_game import prepare_pbp

log = logging.getLogger(__name__)

STATS_COLUMNS: list[str] = [
    "player_id",
    "position",
    "season",
    "week",
    "season_type",
    "game_id",
    "team",
    "opponent_team",
    "completions",
    "attempts",
    "passing_yards",
    "passing_tds",
    "passing_interceptions",
    "sacks_suffered",
    "sack_yards_lost",
    "sack_fumbles_lost",
    "passing_air_yards",
    "passing_yards_after_catch",
    "passing_epa",
    "passing_cpoe",
    "passing_2pt_conversions",
    "carries",
    "rushing_yards",
    "rushing_tds",
    "rushing_fumbles_lost",
    "rushing_first_downs",
    "rushing_epa",
    "rushing_2pt_conversions",
    "receptions",
    "targets",
    "receiving_yards",
    "receiving_tds",
    "receiving_fumbles_lost",
    "receiving_air_yards",
    "receiving_yards_after_catch",
    "receiving_epa",
    "receiving_2pt_conversions",
    "target_share",
    "air_yards_share",
    "wopr",
    "special_teams_tds",
    "fg_made",
    "fg_att",
    "fg_missed",
    "fg_blocked",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_40_49",
    "fg_made_50_59",
    "fg_made_60_",
    "pat_made",
    "pat_att",
    "fantasy_points",
    "fantasy_points_ppr",
]

STATS_RENAMES: dict[str, str] = {
    "attempts": "pass_att",
    "passing_yards": "pass_yds",
    "passing_tds": "pass_td",
    "passing_interceptions": "interceptions",
    "sacks_suffered": "sacks",
    "sack_yards_lost": "sack_yards",
    "passing_air_yards": "pass_air_yards",
    "passing_yards_after_catch": "pass_yac",
    "passing_epa": "pass_epa",
    "passing_cpoe": "cpoe",
    "rushing_yards": "rush_yds",
    "rushing_tds": "rush_td",
    "rushing_first_downs": "rush_first_downs",
    "rushing_epa": "rush_epa",
    "receiving_yards": "rec_yds",
    "receiving_tds": "rec_td",
    "receiving_air_yards": "rec_air_yards",
    "receiving_yards_after_catch": "rec_yac",
    "receiving_epa": "rec_epa",
    "special_teams_tds": "return_tds",
    "fantasy_points_ppr": "fantasy_points_ppr_ref",
    "fantasy_points": "fantasy_points_std_ref",
}

PBP_PLAYER_COLUMNS: list[str] = [
    "game_id",
    "play_id",
    "posteam",
    "play_type",
    "pass",
    "rush",
    "qb_dropback",
    "qb_scramble",
    "sack",
    "pass_attempt",
    "rush_attempt",
    "two_point_attempt",
    "down",
    "qtr",
    "wp",
    "yardline_100",
    "air_yards",
    "half_seconds_remaining",
    "receiver_player_id",
    "rusher_player_id",
    "passer_id",
]
SNAP_COLUMNS = ["game_id", "pfr_player_id", "offense_snaps", "offense_pct"]
FFOPP_COLUMNS = [
    "player_id",
    "season",
    "week",
    "receptions_exp",
    "rec_yards_gained_exp",
    "rec_touchdown_exp",
    "rush_yards_gained_exp",
    "rush_touchdown_exp",
    "pass_yards_gained_exp",
    "pass_touchdown_exp",
    "total_fantasy_points_exp",
]
FFOPP_RENAMES: dict[str, str] = {
    "rec_yards_gained_exp": "rec_yards_exp",
    "rec_touchdown_exp": "rec_td_exp",
    "rush_yards_gained_exp": "rush_yards_exp",
    "rush_touchdown_exp": "rush_td_exp",
    "pass_yards_gained_exp": "pass_yards_exp",
    "pass_touchdown_exp": "pass_td_exp",
    "total_fantasy_points_exp": "total_fp_exp",
}
NGS_KEYS = ["player_gsis_id", "season", "season_type", "week"]
NGS_COLUMNS: dict[str, dict[str, str]] = {
    "nextgen_passing": {
        "avg_time_to_throw": "time_to_throw",
        "aggressiveness": "aggressiveness",
        "avg_intended_air_yards": "pass_intended_air_yards",
    },
    "nextgen_rushing": {
        "efficiency": "efficiency",
        "rush_yards_over_expected": "rush_yards_over_expected",
    },
    "nextgen_receiving": {
        "avg_separation": "avg_separation",
        "avg_cushion": "avg_cushion",
        "avg_yac_above_expectation": "yac_above_expectation",
        "avg_intended_air_yards": "rec_intended_air_yards",
    },
}
ROSTER_COLUMNS = ["season", "week", "team", "gsis_id", "status", "position"]
ROSTER_STATUSES = ("ACT", "INA")

KEY_COLUMNS = [
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
COUNT_COLUMNS: list[str] = [
    "pass_att",
    "completions",
    "pass_yds",
    "pass_td",
    "interceptions",
    "sacks",
    "sack_yards",
    "pass_air_yards",
    "pass_yac",
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
    "third_down_targets",
    "two_min_targets",
    "fg_att",
    "fg_made",
    "fg_made_0_39",
    "fg_made_40_49",
    "fg_made_50p",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_50_59",
    "fg_made_60p",
    "fg_missed",
    "fg_blocked",
    "pat_att",
    "pat_made",
    "fumbles_lost",
    "two_pt_conversions",
    "return_tds",
]
RATE_COLUMNS: list[str] = [
    "pass_epa",
    "cpoe",
    "rush_epa",
    "rec_epa",
    "adot",
    "target_share",
    "air_yards_share",
    "wopr",
]
TEAM_CONTEXT_COLUMNS: list[str] = [
    "team_pass_att",
    "team_rush_att",
    "team_targets",
    "team_rz_targets",
    "team_rz_carries",
    "team_gl_carries",
    "team_pass_td",
    "team_rush_td",
    "team_plays",
]
EXPECTED_COLUMNS: list[str] = [
    "rec_yards_exp",
    "rec_td_exp",
    "receptions_exp",
    "rush_yards_exp",
    "rush_td_exp",
    "pass_yards_exp",
    "pass_td_exp",
    "total_fp_exp",
]
NEXTGEN_COLUMNS: list[str] = [
    "avg_separation",
    "avg_cushion",
    "yac_above_expectation",
    "avg_intended_air_yards",
    "time_to_throw",
    "aggressiveness",
    "rush_yards_over_expected",
    "efficiency",
]
OUTPUT_COLUMNS: list[str] = [
    *KEY_COLUMNS,
    "played",
    "offense_snaps",
    "offense_pct",
    "pass_att",
    "completions",
    "pass_yds",
    "pass_td",
    "interceptions",
    "sacks",
    "sack_yards",
    "pass_air_yards",
    "pass_yac",
    "pass_epa",
    "cpoe",
    "dropbacks",
    "carries",
    "rush_yds",
    "rush_td",
    "rush_first_downs",
    "rush_epa",
    "designed_rush",
    "scrambles",
    "targets",
    "receptions",
    "rec_yds",
    "rec_td",
    "rec_air_yards",
    "rec_yac",
    "rec_epa",
    "adot",
    "target_share",
    "air_yards_share",
    "wopr",
    "rz_targets",
    "rz_carries",
    "gl_carries",
    "ez_targets",
    "third_down_targets",
    "two_min_targets",
    *TEAM_CONTEXT_COLUMNS,
    *EXPECTED_COLUMNS,
    *NEXTGEN_COLUMNS,
    "fg_att",
    "fg_made",
    "fg_made_0_39",
    "fg_made_40_49",
    "fg_made_50p",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_50_59",
    "fg_made_60p",
    "fg_missed",
    "fg_blocked",
    "pat_att",
    "pat_made",
    "fumbles_lost",
    "two_pt_conversions",
    "return_tds",
    "fantasy_points_ppr_ref",
    "fantasy_points_std_ref",
    "row_source",
]


# --------------------------------------------------------------------------- #
# Component frames
# --------------------------------------------------------------------------- #


def _int(name: str) -> pl.Expr:
    return pl.col(name).cast(pl.Int64, strict=False).fill_null(0)


def base_rows(stats: pl.DataFrame, dim_player: pl.DataFrame) -> pl.DataFrame:
    """Stats rows for skill players, renamed and deduplicated on the primary key."""
    df = stats.filter(pl.col("player_id").is_not_null()).with_columns(
        canonical_team("team"),
        canonical_team("opponent_team"),
        fantasy_position("position"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
    )
    df = df.join(
        dim_player.select("player_id", pl.col("position").alias("dim_position")),
        on="player_id",
        how="left",
    ).with_columns(pl.coalesce([pl.col("dim_position"), pl.col("position")]).alias("position"))
    df = df.filter(pl.col("position").is_in(list(SKILL_POSITIONS))).drop("dim_position")

    touches = (
        _int("attempts") + _int("carries") + _int("targets") + _int("fg_att") + _int("pat_att")
    )
    before = df.height
    df = (
        df.with_columns(touches.alias("_touches"))
        .sort(["player_id", "season", "week", "_touches"], descending=[False, False, False, True])
        .unique(subset=["player_id", "season", "week"], keep="first", maintain_order=True)
        .drop("_touches")
    )
    if df.height != before:
        log.info(
            "fact_player_game: dropped %d duplicate (player, season, week) stats rows, kept the row with more touches",
            before - df.height,
        )

    out = df.rename(STATS_RENAMES).with_columns(
        (_int("fg_made_0_19") + _int("fg_made_20_29") + _int("fg_made_30_39")).alias(
            "fg_made_0_39"
        ),
        (_int("fg_made_50_59") + _int("fg_made_60_")).alias("fg_made_50p"),
        # Fine distance buckets are kept as well because league scoring (Sleeper
        # fgm_0_19 ... fgm_60p) distinguishes them.
        _int("fg_made_0_19").alias("fg_made_0_19"),
        _int("fg_made_20_29").alias("fg_made_20_29"),
        _int("fg_made_30_39").alias("fg_made_30_39"),
        _int("fg_made_50_59").alias("fg_made_50_59"),
        _int("fg_made_60_").alias("fg_made_60p"),
        (
            _int("sack_fumbles_lost")
            + _int("rushing_fumbles_lost")
            + _int("receiving_fumbles_lost")
        ).alias("fumbles_lost"),
        (
            _int("passing_2pt_conversions")
            + _int("rushing_2pt_conversions")
            + _int("receiving_2pt_conversions")
        ).alias("two_pt_conversions"),
        pl.lit("player_stats").alias("row_source"),
    )
    return out.drop(
        [
            c
            for c in (
                "fg_made_0_19",
                "fg_made_20_29",
                "fg_made_30_39",
                "fg_made_50_59",
                "fg_made_60_",
                "sack_fumbles_lost",
                "rushing_fumbles_lost",
                "receiving_fumbles_lost",
                "passing_2pt_conversions",
                "rushing_2pt_conversions",
                "receiving_2pt_conversions",
                "game_id",
                "opponent_team",
                "season_type",
            )
            if c in out.columns
        ]
    )


def pbp_player_counts(pbp: pl.DataFrame) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Situational counts per (game_id, player_id) and team context per (game_id, team)."""
    df = pbp.filter(pl.col("posteam").is_not_null() & pl.col("not_no_play"))
    target, rush = pl.col("is_target"), pl.col("is_rush_att")
    rz = pl.col("yardline_100") <= 20
    gl = pl.col("yardline_100") <= 5
    ez = pl.col("air_yards") >= pl.col("yardline_100")
    receivers = (
        df.filter(target)
        .group_by(["game_id", "receiver_player_id"])
        .agg(
            (rz).sum().alias("rz_targets"),
            ez.fill_null(False).sum().alias("ez_targets"),
            (pl.col("down") == 3).fill_null(False).sum().alias("third_down_targets"),
            (pl.col("half_seconds_remaining") <= 120)
            .fill_null(False)
            .sum()
            .alias("two_min_targets"),
        )
        .rename({"receiver_player_id": "player_id"})
    )
    rushers = (
        df.filter(rush)
        .group_by(["game_id", "rusher_player_id"])
        .agg(
            rz.sum().alias("rz_carries"),
            gl.sum().alias("gl_carries"),
            (~pl.col("qb_scramble")).sum().alias("designed_rush"),
            pl.col("qb_scramble").sum().alias("scrambles"),
        )
        .rename({"rusher_player_id": "player_id"})
    )
    passers = (
        df.filter(pl.col("is_play") & pl.col("qb_dropback") & pl.col("passer_id").is_not_null())
        .group_by(["game_id", "passer_id"])
        .agg(pl.len().alias("dropbacks"))
        .rename({"passer_id": "player_id"})
    )
    players = receivers.join(rushers, on=["game_id", "player_id"], how="full", coalesce=True).join(
        passers, on=["game_id", "player_id"], how="full", coalesce=True
    )
    team = (
        df.group_by(["game_id", "posteam"])
        .agg(
            target.sum().alias("team_targets"),
            (target & rz).sum().alias("team_rz_targets"),
            (rush & rz).sum().alias("team_rz_carries"),
            (rush & gl).sum().alias("team_gl_carries"),
        )
        .rename({"posteam": "team"})
    )
    return players, team


def snap_rows(snaps: pl.DataFrame | None, dim_player: pl.DataFrame) -> pl.DataFrame:
    schema = {
        "game_id": pl.String,
        "player_id": pl.String,
        "offense_snaps": pl.Int32,
        "offense_pct": pl.Float64,
    }
    if snaps is None or snaps.is_empty():
        return pl.DataFrame(schema=schema)
    pfr = (
        dim_player.filter(pl.col("pfr_id").is_not_null())
        .select("pfr_id", "player_id")
        .unique(subset=["pfr_id"], keep="first")
    )
    return (
        snaps.filter(pl.col("pfr_player_id").is_not_null())
        .join(pfr, left_on="pfr_player_id", right_on="pfr_id", how="inner")
        .sort("offense_snaps", descending=True, nulls_last=True)
        .unique(subset=["game_id", "player_id"], keep="first", maintain_order=True)
        .select(
            "game_id",
            "player_id",
            pl.col("offense_snaps").cast(pl.Int32, strict=False),
            pl.col("offense_pct").cast(pl.Float64, strict=False),
        )
    )


def expected_rows(ffopp: pl.DataFrame | None) -> pl.DataFrame:
    schema = {
        "player_id": pl.String,
        "season": pl.Int32,
        "week": pl.Int32,
        **{c: pl.Float64 for c in EXPECTED_COLUMNS},
    }
    if ffopp is None or ffopp.is_empty():
        return pl.DataFrame(schema=schema)
    return (
        ffopp.filter(pl.col("player_id").is_not_null())
        .rename(FFOPP_RENAMES)
        .with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))
        .sort("total_fp_exp", descending=True, nulls_last=True)
        .unique(subset=["player_id", "season", "week"], keep="first", maintain_order=True)
        .select(list(schema))
    )


def _remap_post_weeks(ngs: pl.DataFrame, post_weeks: list[int]) -> pl.DataFrame:
    """NGS numbers the Super Bowl one week later than nflverse; map POST weeks by rank."""
    if "season_type" not in ngs.columns or not post_weeks:
        return ngs
    ngs_post = sorted(ngs.filter(pl.col("season_type") == "POST")["week"].unique().to_list())
    mapping = {int(src): int(dst) for src, dst in zip(ngs_post, sorted(post_weeks), strict=False)}
    if not mapping or all(k == v for k, v in mapping.items()):
        return ngs
    return ngs.with_columns(
        pl.when(pl.col("season_type") == "POST")
        .then(pl.col("week").replace(mapping))
        .otherwise(pl.col("week"))
        .alias("week")
    )


def nextgen_rows(frames: dict[str, pl.DataFrame | None], post_weeks: list[int]) -> pl.DataFrame:
    """Join the three NGS files on (player, season, week) and name the documented columns."""
    schema = {"player_id": pl.String, "season": pl.Int32, "week": pl.Int32}
    out: pl.DataFrame | None = None
    for dataset, columns in NGS_COLUMNS.items():
        ngs = frames.get(dataset)
        if ngs is None or ngs.is_empty():
            continue
        part = (
            _remap_post_weeks(ngs.filter(pl.col("week") > 0), post_weeks)
            .select(
                pl.col("player_gsis_id").alias("player_id"),
                pl.col("season").cast(pl.Int32),
                pl.col("week").cast(pl.Int32),
                *[
                    pl.col(src).cast(pl.Float64, strict=False).alias(dst)
                    for src, dst in columns.items()
                    if src in ngs.columns
                ],
            )
            .filter(pl.col("player_id").is_not_null())
            .unique(subset=["player_id", "season", "week"], keep="first", maintain_order=True)
        )
        out = (
            part
            if out is None
            else out.join(part, on=["player_id", "season", "week"], how="full", coalesce=True)
        )
    if out is None:
        return pl.DataFrame(schema={**schema, **{c: pl.Float64 for c in NEXTGEN_COLUMNS}})
    for name in ("pass_intended_air_yards", "rec_intended_air_yards", *NEXTGEN_COLUMNS):
        if name not in out.columns and name != "avg_intended_air_yards":
            out = out.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    out = out.with_columns(
        pl.coalesce([pl.col("rec_intended_air_yards"), pl.col("pass_intended_air_yards")]).alias(
            "avg_intended_air_yards"
        )
    )
    return out.select(*schema, *NEXTGEN_COLUMNS)


def roster_rows(
    rosters: pl.DataFrame | None,
    dim_player: pl.DataFrame,
    games: pl.DataFrame,
    existing: pl.DataFrame,
) -> pl.DataFrame:
    """Rostered (ACT or INA) skill players whose team played but who have no stats row."""
    schema = {
        "player_id": pl.String,
        "season": pl.Int32,
        "week": pl.Int32,
        "team": pl.String,
        "position": pl.String,
    }
    if rosters is None or rosters.is_empty():
        return pl.DataFrame(schema=schema)
    df = (
        rosters.filter(
            pl.col("gsis_id").is_not_null() & pl.col("status").is_in(list(ROSTER_STATUSES))
        )
        .select(
            pl.col("gsis_id").str.strip_chars().alias("player_id"),
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "team",
            "status",
            pl.col("position").alias("roster_position"),
        )
        .with_columns(canonical_team("team"), fantasy_position("roster_position"))
        .join(dim_player.select("player_id", "position"), on="player_id", how="left")
        .with_columns(
            pl.coalesce([pl.col("position"), pl.col("roster_position")]).alias("position")
        )
        .filter(pl.col("position").is_in(list(SKILL_POSITIONS)))
        .join(
            games.select("season", "week", "team").unique(),
            on=["season", "week", "team"],
            how="semi",
        )
        .join(
            existing.select("player_id", "season", "week"),
            on=["player_id", "season", "week"],
            how="anti",
        )
        .with_columns((pl.col("status") == "ACT").alias("_act"))
        .sort(["player_id", "season", "week", "_act"], descending=[False, False, False, True])
        .unique(subset=["player_id", "season", "week"], keep="first", maintain_order=True)
    )
    return df.select(list(schema))


# --------------------------------------------------------------------------- #
# Season assembly
# --------------------------------------------------------------------------- #


def player_game_season(
    stats: pl.DataFrame,
    pbp: pl.DataFrame,
    dim_game: pl.DataFrame,
    dim_player: pl.DataFrame,
    team_game: pl.DataFrame,
    snaps: pl.DataFrame | None = None,
    ffopp: pl.DataFrame | None = None,
    nextgen: dict[str, pl.DataFrame | None] | None = None,
    rosters: pl.DataFrame | None = None,
) -> pl.DataFrame:
    """Assemble one season of ``fact_player_game`` from prepared inputs (``pbp`` already prepared)."""
    games = team_game_map(dim_game).drop(["points", "points_allowed"])
    base = base_rows(stats, dim_player)
    extra = roster_rows(rosters, dim_player, games, base)
    if extra.height:
        log.info(
            "fact_player_game %s: added %d rostered rows without a stats row",
            dim_game["season"].min(),
            extra.height,
        )
    extra = extra.with_columns(pl.lit("roster").alias("row_source"))
    rows = pl.concat([base, extra], how="diagonal_relaxed")

    rows = rows.join(games, on=["season", "week", "team"], how="left")
    no_game = rows.filter(pl.col("game_id").is_null())
    if no_game.height:
        log.warning(
            "fact_player_game: %d rows have no dim_game match on (season, week, team) and are dropped; sample %s",
            no_game.height,
            no_game.select("player_id", "season", "week", "team").head(3).rows(),
        )
        rows = rows.filter(pl.col("game_id").is_not_null())

    situational, team_ctx = pbp_player_counts(pbp)
    rows = rows.join(situational, on=["game_id", "player_id"], how="left")
    rows = rows.join(team_ctx, on=["game_id", "team"], how="left")
    rows = rows.join(
        team_game.select(
            "game_id",
            "team",
            pl.col("pass_att").alias("team_pass_att"),
            pl.col("rush_att").alias("team_rush_att"),
            pl.col("plays").alias("team_plays"),
            pl.col("pass_td").alias("team_pass_td"),
            pl.col("rush_td").alias("team_rush_td"),
        ),
        on=["game_id", "team"],
        how="left",
    )
    rows = rows.join(snap_rows(snaps, dim_player), on=["game_id", "player_id"], how="left")
    rows = rows.join(expected_rows(ffopp), on=["player_id", "season", "week"], how="left")
    post_weeks = dim_game.filter(pl.col("game_type") != "REG")["week"].unique().to_list()
    rows = rows.join(
        nextgen_rows(nextgen or {}, post_weeks), on=["player_id", "season", "week"], how="left"
    )

    for name in COUNT_COLUMNS:
        if name not in rows.columns:
            rows = rows.with_columns(pl.lit(0).alias(name))
    for name in (*RATE_COLUMNS, *EXPECTED_COLUMNS, *NEXTGEN_COLUMNS, "offense_pct"):
        if name not in rows.columns:
            rows = rows.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))
    if "offense_snaps" not in rows.columns:
        rows = rows.with_columns(pl.lit(None, dtype=pl.Int32).alias("offense_snaps"))
    for name in ("fantasy_points_ppr_ref", "fantasy_points_std_ref"):
        if name not in rows.columns:
            rows = rows.with_columns(pl.lit(None, dtype=pl.Float64).alias(name))

    rows = rows.with_columns(
        [pl.col(c).cast(pl.Int64, strict=False).fill_null(0).cast(pl.Int32) for c in COUNT_COLUMNS]
        + [
            pl.col(c).cast(pl.Int64, strict=False).fill_null(0).cast(pl.Int32)
            for c in TEAM_CONTEXT_COLUMNS
        ]
        + [
            pl.col(c).cast(pl.Float64, strict=False)
            for c in (*RATE_COLUMNS, *EXPECTED_COLUMNS, *NEXTGEN_COLUMNS, "offense_pct")
        ]
        + [
            pl.col("fantasy_points_ppr_ref").cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col("fantasy_points_std_ref").cast(pl.Float64, strict=False).fill_null(0.0),
            pl.col("offense_snaps").cast(pl.Int32, strict=False),
        ]
    )
    rows = rows.with_columns(
        pl.when(pl.col("targets") > 0)
        .then(pl.col("rec_air_yards").cast(pl.Float64) / pl.col("targets"))
        .otherwise(None)
        .alias("adot"),
        (
            (pl.col("offense_snaps").fill_null(0) > 0)
            | (pl.col("pass_att") > 0)
            | (pl.col("carries") > 0)
            | (pl.col("targets") > 0)
            | (pl.col("fg_att") > 0)
            | (pl.col("pat_att") > 0)
        ).alias("played"),
    )
    out = rows.select(OUTPUT_COLUMNS).sort(["season", "week", "team", "player_id"])
    assert_unique(out, ["player_id", "season", "week"], "fact_player_game")
    return out


def build_player_game(ctx: ProcessContext) -> dict[str, dict[PartitionKey, pl.DataFrame]]:
    """Build ``fact_player_game`` for every requested season."""
    dim_game = ctx.table("dim_game")
    dim_player = ctx.table("dim_player").select("player_id", "position", "pfr_id")
    frames: dict[PartitionKey, pl.DataFrame] = {}
    for season in ctx.seasons:
        stats = read_raw_or_none(ctx, "player_stats", seasons=[season], columns=STATS_COLUMNS)
        raw_pbp = read_raw_or_none(ctx, "pbp", seasons=[season], columns=PBP_PLAYER_COLUMNS)
        if stats is None or stats.is_empty() or raw_pbp is None or raw_pbp.is_empty():
            log.warning("fact_player_game: no player_stats or pbp for %d", season)
            continue
        try:
            team_game = ctx.table("fact_team_game", seasons=[season])
        except (KeyError, FileNotFoundError):
            log.warning(
                "fact_player_game: fact_team_game missing for %d; team context from team-game will be null",
                season,
            )
            team_game = pl.DataFrame(
                schema={
                    "game_id": pl.String,
                    "team": pl.String,
                    "pass_att": pl.Int32,
                    "rush_att": pl.Int32,
                    "plays": pl.Int32,
                    "pass_td": pl.Int32,
                    "rush_td": pl.Int32,
                }
            )
        frame = player_game_season(
            stats,
            prepare_pbp(raw_pbp),
            dim_game.filter(pl.col("season") == season),
            dim_player,
            team_game,
            snaps=read_raw_or_none(ctx, "snap_counts", seasons=[season], columns=SNAP_COLUMNS),
            ffopp=read_raw_or_none(ctx, "ff_opportunity", seasons=[season], columns=FFOPP_COLUMNS),
            nextgen={
                name: read_raw_or_none(ctx, name, seasons=[season], columns=[*NGS_KEYS, *cols])
                for name, cols in NGS_COLUMNS.items()
            },
            rosters=read_raw_or_none(
                ctx, "rosters_weekly", seasons=[season], columns=ROSTER_COLUMNS
            ),
        )
        log.info(
            "fact_player_game %d: %d rows (%d played, %d from rosters)",
            season,
            frame.height,
            int(frame["played"].sum()),
            int((frame["row_source"] == "roster").sum()),
        )
        frames[season] = frame
    return {"fact_player_game": frames}


__all__ = [
    "COUNT_COLUMNS",
    "OUTPUT_COLUMNS",
    "PBP_PLAYER_COLUMNS",
    "STATS_COLUMNS",
    "base_rows",
    "build_player_game",
    "pbp_player_counts",
    "player_game_season",
]
