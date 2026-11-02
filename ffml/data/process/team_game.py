"""``fact_team_game`` and ``fact_dst_game`` from play-by-play, one season at a time.

Definitions (checked against nflverse ``player_stats`` team totals for 2025):

* ``plays``: pass or rush plays (``pass == 1`` or ``rush == 1``) excluding
  ``no_play``, ``qb_kneel`` and ``qb_spike``; pace, EPA, success and the pass
  rates use this set.
* ``pass_att`` and ``rush_att`` follow the official definition so that they tie
  to the player totals: ``pass_attempt`` without a sack (spikes included),
  ``rush_attempt`` (kneels included), two-point tries and nullified plays excluded.
* a target is an official pass attempt with a ``receiver_player_id``.
* neutral script: ``0.2 <= wp <= 0.8`` and ``qtr <= 3``.
* seconds per play: difference in ``game_seconds_remaining`` between
  consecutive plays of the same offensive drive (``fixed_drive``); the first
  play of a drive and negative differences are ignored.
* red-zone trips: distinct drives with a scrimmage play (pass, run, kneel,
  spike, field goal) snapped at or inside the 20.

Every offensive metric is mirrored from the opponent's view as ``allowed_*``.
"""

from __future__ import annotations

import logging

import polars as pl

from ffml.data.process.base import (
    PartitionKey,
    ProcessContext,
    assert_unique,
    canonical_team,
    read_raw_or_none,
    team_game_map,
)

log = logging.getLogger(__name__)

#: Columns read from raw ``pbp`` (always pass ``columns=``; the file has 372).
PBP_COLUMNS: list[str] = [
    "game_id",
    "play_id",
    "season",
    "week",
    "posteam",
    "defteam",
    "play_type",
    "pass",
    "rush",
    "qb_dropback",
    "qb_scramble",
    "sack",
    "pass_attempt",
    "rush_attempt",
    "two_point_attempt",
    "two_point_conv_result",
    "down",
    "qtr",
    "wp",
    "game_seconds_remaining",
    "half_seconds_remaining",
    "fixed_drive",
    "yardline_100",
    "air_yards",
    "epa",
    "success",
    "yards_gained",
    "pass_oe",
    "pass_touchdown",
    "rush_touchdown",
    "touchdown",
    "return_touchdown",
    "td_team",
    "field_goal_attempt",
    "field_goal_result",
    "extra_point_result",
    "punt_blocked",
    "safety",
    "interception",
    "fumble",
    "fumble_lost",
    "fumbled_1_team",
    "fumble_recovery_1_team",
    "receiver_player_id",
    "rusher_player_id",
    "passer_player_id",
    "passer_id",
    "passing_yards",
    "complete_pass",
]

_FLAG_COLUMNS = (
    "pass",
    "rush",
    "qb_dropback",
    "qb_scramble",
    "sack",
    "pass_attempt",
    "rush_attempt",
    "two_point_attempt",
    "pass_touchdown",
    "rush_touchdown",
    "touchdown",
    "return_touchdown",
    "field_goal_attempt",
    "punt_blocked",
    "safety",
    "interception",
    "fumble",
    "fumble_lost",
    "complete_pass",
)
_FLOAT_COLUMNS = (
    "down",
    "qtr",
    "wp",
    "game_seconds_remaining",
    "half_seconds_remaining",
    "fixed_drive",
    "yardline_100",
    "air_yards",
    "epa",
    "success",
    "yards_gained",
    "pass_oe",
    "passing_yards",
    "play_id",
)
_TEAM_COLUMNS = ("posteam", "defteam", "td_team", "fumbled_1_team", "fumble_recovery_1_team")

EXCLUDED_PLAY_TYPES = ("no_play", "qb_kneel", "qb_spike")
RZ_PLAY_TYPES = ("pass", "run", "qb_kneel", "qb_spike", "field_goal")
RETURN_PLAY_TYPES = ("kickoff", "punt")

KEY_COLUMNS = ["team", "season", "week", "game_id", "opponent", "home", "game_type"]

#: Offensive metrics mirrored as ``allowed_*`` (``points`` is added from dim_game).
OFFENSE_METRICS: list[str] = [
    "plays",
    "pass_att",
    "rush_att",
    "dropbacks",
    "sacks",
    "neutral_plays",
    "sec_per_play",
    "neutral_sec_per_play",
    "pass_rate",
    "neutral_pass_rate",
    "proe",
    "early_down_pass_rate",
    "epa_per_play",
    "pass_epa_per_play",
    "rush_epa_per_play",
    "success_rate",
    "yards_per_play",
    "points",
    "pass_td",
    "rush_td",
    "rz_trips",
    "rz_td_rate",
    "gl_plays",
    "field_goal_att",
    "field_goal_made",
    "two_pt_att",
    "two_pt_conv",
    "interceptions",
    "fumbles_lost",
]
POSITION_METRICS: list[str] = [
    "rb_targets",
    "wr_targets",
    "te_targets",
    "rb_rec_yds",
    "wr_rec_yds",
    "te_rec_yds",
    "rb_rush_att",
    "rb_rush_yds",
    "rb_rush_td",
    "rec_td_rb",
    "rec_td_wr",
    "rec_td_te",
    "qb_pass_yds",
    "qb_pass_td",
    "qb_rush_yds",
]
_COUNT_METRICS = {
    "plays",
    "pass_att",
    "rush_att",
    "dropbacks",
    "sacks",
    "neutral_plays",
    "pass_td",
    "rush_td",
    "rz_trips",
    "gl_plays",
    "field_goal_att",
    "field_goal_made",
    "two_pt_att",
    "two_pt_conv",
    "interceptions",
    "fumbles_lost",
    "rb_targets",
    "wr_targets",
    "te_targets",
    "rb_rush_att",
    "rb_rush_td",
    "rec_td_rb",
    "rec_td_wr",
    "rec_td_te",
    "qb_pass_td",
}
_SUM_METRICS = {
    "rb_rec_yds",
    "wr_rec_yds",
    "te_rec_yds",
    "rb_rush_yds",
    "qb_pass_yds",
    "qb_rush_yds",
}

DST_METRICS: list[str] = [
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


# --------------------------------------------------------------------------- #
# Shared play preparation
# --------------------------------------------------------------------------- #


def prepare_pbp(pbp: pl.DataFrame) -> pl.DataFrame:
    """Cast the play-by-play columns and add the boolean play flags used by both builders."""
    exprs: list[pl.Expr] = []
    for name in _FLAG_COLUMNS:
        if name in pbp.columns:
            exprs.append(pl.col(name).cast(pl.Float64, strict=False).fill_null(0.0) == 1.0)
    for name in _FLOAT_COLUMNS:
        if name in pbp.columns:
            exprs.append(pl.col(name).cast(pl.Float64, strict=False))
    for name in _TEAM_COLUMNS:
        if name in pbp.columns:
            exprs.append(canonical_team(name))
    df = pbp.with_columns(exprs)
    play_type = pl.col("play_type")
    not_no_play = play_type.is_not_null() & (play_type != "no_play")
    is_play = (pl.col("pass") | pl.col("rush")) & ~play_type.is_in(
        list(EXCLUDED_PLAY_TYPES)
    ).fill_null(True)
    is_pass_att = (
        pl.col("pass_attempt") & ~pl.col("sack") & ~pl.col("two_point_attempt") & not_no_play
    )
    is_rush_att = pl.col("rush_attempt") & ~pl.col("two_point_attempt") & not_no_play
    flags = [
        not_no_play.alias("not_no_play"),
        is_play.alias("is_play"),
        is_pass_att.alias("is_pass_att"),
        is_rush_att.alias("is_rush_att"),
        (is_pass_att & pl.col("receiver_player_id").is_not_null()).alias("is_target"),
        (
            play_type.is_in(list(RZ_PLAY_TYPES)).fill_null(False)
            & ~pl.col("two_point_attempt")
            & (pl.col("yardline_100") <= 20)
        ).alias("is_rz_play"),
    ]
    if "wp" in df.columns and "qtr" in df.columns:
        is_neutral = is_play & pl.col("wp").is_between(0.2, 0.8) & (pl.col("qtr") <= 3)
        flags.append(is_neutral.alias("is_neutral"))
        flags.append((is_neutral & pl.col("down").is_in([1.0, 2.0])).alias("is_early"))
    return df.with_columns(flags)


def _positions(dim_player: pl.DataFrame, id_column: str, alias: str) -> pl.DataFrame:
    return dim_player.select(
        pl.col("player_id").alias(id_column), pl.col("position").alias(alias)
    ).unique(subset=[id_column], keep="first")


def _ratio(num: str, den: str) -> pl.Expr:
    return pl.when(pl.col(den) > 0).then(pl.col(num).cast(pl.Float64) / pl.col(den)).otherwise(None)


# --------------------------------------------------------------------------- #
# Offense metrics per (game, team)
# --------------------------------------------------------------------------- #


def offense_metrics(pbp: pl.DataFrame, dim_player: pl.DataFrame) -> pl.DataFrame:
    """Offensive metrics keyed by ``game_id, team`` from a prepared play frame."""
    df = (
        pbp.filter(pl.col("posteam").is_not_null())
        .join(
            _positions(dim_player, "receiver_player_id", "recv_pos"),
            on="receiver_player_id",
            how="left",
        )
        .join(
            _positions(dim_player, "rusher_player_id", "rush_pos"),
            on="rusher_player_id",
            how="left",
        )
        .join(
            _positions(dim_player, "passer_player_id", "pass_pos"),
            on="passer_player_id",
            how="left",
        )
    )
    keys = ["game_id", "posteam"]
    is_play, is_neutral, is_early = pl.col("is_play"), pl.col("is_neutral"), pl.col("is_early")
    dropback = pl.col("qb_dropback")
    td_pass = pl.col("pass_touchdown") & pl.col("not_no_play")
    td_rush = pl.col("rush_touchdown") & pl.col("not_no_play")
    scrimmage = pl.col("play_type").is_in(["pass", "run"]).fill_null(False)
    aggs = [
        is_play.sum().alias("plays"),
        pl.col("is_pass_att").sum().alias("pass_att"),
        pl.col("is_rush_att").sum().alias("rush_att"),
        (dropback & is_play).sum().alias("dropbacks"),
        (pl.col("sack") & is_play).sum().alias("sacks"),
        is_neutral.sum().alias("neutral_plays"),
        (dropback & is_neutral).sum().alias("neutral_dropbacks"),
        is_early.sum().alias("early_plays"),
        (dropback & is_early).sum().alias("early_dropbacks"),
        pl.col("pass_oe").filter(is_play).mean().alias("proe"),
        pl.col("epa").filter(is_play).mean().alias("epa_per_play"),
        pl.col("epa").filter(is_play & pl.col("pass")).mean().alias("pass_epa_per_play"),
        pl.col("epa").filter(is_play & pl.col("rush")).mean().alias("rush_epa_per_play"),
        pl.col("success").filter(is_play).mean().alias("success_rate"),
        pl.col("yards_gained").filter(is_play).mean().alias("yards_per_play"),
        td_pass.sum().alias("pass_td"),
        td_rush.sum().alias("rush_td"),
        (is_play & (pl.col("yardline_100") <= 5)).sum().alias("gl_plays"),
        (pl.col("field_goal_attempt") & pl.col("not_no_play")).sum().alias("field_goal_att"),
        (pl.col("field_goal_result") == "made").sum().alias("field_goal_made"),
        (pl.col("two_point_attempt") & pl.col("not_no_play")).sum().alias("two_pt_att"),
        (pl.col("two_point_conv_result") == "success").sum().alias("two_pt_conv"),
        (pl.col("interception") & scrimmage).sum().alias("interceptions"),
        (pl.col("fumble_lost") & scrimmage).sum().alias("fumbles_lost"),
    ]
    target, rush_att = pl.col("is_target"), pl.col("is_rush_att")
    for pos in ("RB", "WR", "TE"):
        p = pos.lower()
        recv = pl.col("recv_pos") == pos
        aggs += [
            (target & recv).sum().alias(f"{p}_targets"),
            pl.col("yards_gained")
            .filter(target & pl.col("complete_pass") & recv)
            .sum()
            .alias(f"{p}_rec_yds"),
            (td_pass & recv).sum().alias(f"rec_td_{p}"),
        ]
    rb_rush = rush_att & (pl.col("rush_pos") == "RB")
    qb_rush = rush_att & (pl.col("rush_pos") == "QB")
    qb_pass = pl.col("pass_pos") == "QB"
    aggs += [
        rb_rush.sum().alias("rb_rush_att"),
        pl.col("yards_gained").filter(rb_rush).sum().alias("rb_rush_yds"),
        (td_rush & (pl.col("rush_pos") == "RB")).sum().alias("rb_rush_td"),
        pl.col("passing_yards").filter(pl.col("is_pass_att") & qb_pass).sum().alias("qb_pass_yds"),
        (td_pass & qb_pass).sum().alias("qb_pass_td"),
        pl.col("yards_gained").filter(qb_rush).sum().alias("qb_rush_yds"),
    ]
    off = df.group_by(keys).agg(aggs)

    # Pace: seconds between consecutive plays of the same drive.
    plays = df.filter(is_play).sort(["game_id", "posteam", "fixed_drive", "play_id"])
    elapsed = pl.col("game_seconds_remaining").shift(1).over(
        ["game_id", "posteam", "fixed_drive"]
    ) - pl.col("game_seconds_remaining")
    plays = plays.with_columns(pl.when(elapsed >= 0).then(elapsed).otherwise(None).alias("elapsed"))
    pace = plays.group_by(keys).agg(
        pl.col("elapsed").mean().alias("sec_per_play"),
        pl.col("elapsed").filter(is_neutral).mean().alias("neutral_sec_per_play"),
    )

    # Red-zone trips: drives with a snap at or inside the 20; TD drives among them.
    drives = df.group_by(["game_id", "posteam", "fixed_drive"]).agg(
        pl.col("is_rz_play").any().alias("rz"),
        (td_pass | td_rush).any().alias("td"),
    )
    rz = (
        drives.filter(pl.col("rz"))
        .group_by(keys)
        .agg(pl.len().alias("rz_trips"), pl.col("td").sum().alias("rz_tds"))
    )

    out = off.join(pace, on=keys, how="left").join(rz, on=keys, how="left")
    out = out.with_columns(
        pl.col("rz_trips").fill_null(0),
        pl.col("rz_tds").fill_null(0),
    ).with_columns(
        _ratio("dropbacks", "plays").alias("pass_rate"),
        _ratio("neutral_dropbacks", "neutral_plays").alias("neutral_pass_rate"),
        _ratio("early_dropbacks", "early_plays").alias("early_down_pass_rate"),
        _ratio("rz_tds", "rz_trips").alias("rz_td_rate"),
    )
    return out.rename({"posteam": "team"}).drop(
        ["neutral_dropbacks", "early_plays", "early_dropbacks", "rz_tds"]
    )


# --------------------------------------------------------------------------- #
# Defense / special teams metrics per (game, team)
# --------------------------------------------------------------------------- #


def defense_metrics(pbp: pl.DataFrame) -> pl.DataFrame:
    """DST scoring inputs keyed by ``game_id, team`` (the defending team)."""
    keys = ["game_id", "defteam"]
    play_type = pl.col("play_type")
    by_def = (
        pbp.filter(pl.col("defteam").is_not_null())
        .group_by(keys)
        .agg(
            (pl.col("sack") & pl.col("is_play")).sum().alias("sacks"),
            (pl.col("interception") & pl.col("not_no_play")).sum().alias("interceptions"),
            (
                pl.col("touchdown")
                & (pl.col("td_team") == pl.col("defteam"))
                & ~play_type.is_in(list(RETURN_PLAY_TYPES)).fill_null(False)
            )
            .sum()
            .alias("def_td"),
            pl.col("safety").sum().alias("safeties"),
            (
                pl.col("punt_blocked").sum()
                + (pl.col("field_goal_result") == "blocked").sum()
                + (pl.col("extra_point_result") == "blocked").sum()
            ).alias("blocked_kicks"),
            pl.col("yards_gained").filter(pl.col("is_play")).sum().alias("yards_allowed"),
        )
        .rename({"defteam": "team"})
    )
    returns = (
        pbp.filter(
            play_type.is_in(list(RETURN_PLAY_TYPES))
            & pl.col("touchdown")
            & pl.col("td_team").is_not_null()
        )
        .group_by(["game_id", "td_team"])
        .agg(pl.len().alias("return_td"))
        .rename({"td_team": "team"})
    )
    recoveries = (
        pbp.filter(
            pl.col("fumble")
            & pl.col("fumble_recovery_1_team").is_not_null()
            & pl.col("fumbled_1_team").is_not_null()
            & (pl.col("fumble_recovery_1_team") != pl.col("fumbled_1_team"))
        )
        .group_by(["game_id", "fumble_recovery_1_team"])
        .agg(pl.len().alias("fumble_recoveries"))
        .rename({"fumble_recovery_1_team": "team"})
    )
    return by_def.join(returns, on=["game_id", "team"], how="left").join(
        recoveries, on=["game_id", "team"], how="left"
    )


# --------------------------------------------------------------------------- #
# Season assembly
# --------------------------------------------------------------------------- #


def _season_games(dim_game: pl.DataFrame, pbp: pl.DataFrame) -> pl.DataFrame:
    game_ids = pbp["game_id"].unique()
    games = team_game_map(dim_game.filter(pl.col("game_id").is_in(game_ids)))
    missing = set(game_ids.to_list()) - set(games["game_id"].unique().to_list())
    if missing:
        log.warning(
            "%d pbp games are not in dim_game and are skipped: %s",
            len(missing),
            sorted(missing)[:5],
        )
    return games


def team_game_season(
    pbp: pl.DataFrame, dim_game: pl.DataFrame, dim_player: pl.DataFrame
) -> pl.DataFrame:
    """``fact_team_game`` rows for the games present in ``pbp`` (already prepared)."""
    games = _season_games(dim_game, pbp)
    off = offense_metrics(pbp, dim_player)
    tg = games.join(off, on=["game_id", "team"], how="left")
    fills = [pl.col(c).fill_null(0).cast(pl.Int32) for c in _COUNT_METRICS if c in tg.columns]
    fills += [pl.col(c).fill_null(0.0).cast(pl.Float64) for c in _SUM_METRICS if c in tg.columns]
    tg = tg.with_columns(fills)
    mirror_cols = OFFENSE_METRICS + POSITION_METRICS
    opp = tg.select(
        "game_id",
        pl.col("team").alias("opponent"),
        *[pl.col(c).alias(f"allowed_{c}") for c in mirror_cols],
    )
    tg = tg.join(opp, on=["game_id", "opponent"], how="left")
    out = tg.select(
        *KEY_COLUMNS,
        *OFFENSE_METRICS,
        *[f"allowed_{c}" for c in OFFENSE_METRICS],
        *[f"allowed_{c}" for c in POSITION_METRICS],
    ).sort(["season", "week", "game_id", "team"])
    assert_unique(out, ["team", "season", "week"], "fact_team_game")
    return out


def dst_game_season(pbp: pl.DataFrame, dim_game: pl.DataFrame) -> pl.DataFrame:
    """``fact_dst_game`` rows for the games present in ``pbp`` (already prepared)."""
    games = _season_games(dim_game, pbp)
    dst = games.join(defense_metrics(pbp), on=["game_id", "team"], how="left")
    out = dst.with_columns(
        *[
            pl.col(c).fill_null(0).cast(pl.Int32)
            for c in DST_METRICS
            if c not in ("points_allowed", "yards_allowed")
        ],
        pl.col("points_allowed").cast(pl.Int32),
        pl.col("yards_allowed").fill_null(0.0).cast(pl.Float64),
    ).select(*KEY_COLUMNS, *DST_METRICS)
    out = out.sort(["season", "week", "game_id", "team"])
    assert_unique(out, ["team", "season", "week"], "fact_dst_game")
    return out


def build_team_game(ctx: ProcessContext) -> dict[str, dict[PartitionKey, pl.DataFrame]]:
    """Build ``fact_team_game`` and ``fact_dst_game`` for every requested season."""
    dim_game = ctx.table("dim_game")
    dim_player = ctx.table("dim_player").select("player_id", "position")
    team_frames: dict[PartitionKey, pl.DataFrame] = {}
    dst_frames: dict[PartitionKey, pl.DataFrame] = {}
    for season in ctx.seasons:
        raw = read_raw_or_none(ctx, "pbp", seasons=[season], columns=PBP_COLUMNS)
        if raw is None or raw.is_empty():
            log.warning("fact_team_game: no play-by-play for %d", season)
            continue
        pbp = prepare_pbp(raw)
        games = dim_game.filter(pl.col("season") == season)
        team = team_game_season(pbp, games, dim_player)
        dst = dst_game_season(pbp, games)
        log.info(
            "fact_team_game %d: %d team-games from %d plays (%d games); fact_dst_game %d rows",
            season,
            team.height,
            pbp.height,
            pbp["game_id"].n_unique(),
            dst.height,
        )
        team_frames[season] = team
        dst_frames[season] = dst
    return {"fact_team_game": team_frames, "fact_dst_game": dst_frames}


__all__ = [
    "DST_METRICS",
    "OFFENSE_METRICS",
    "PBP_COLUMNS",
    "POSITION_METRICS",
    "build_team_game",
    "defense_metrics",
    "dst_game_season",
    "offense_metrics",
    "prepare_pbp",
    "team_game_season",
]
