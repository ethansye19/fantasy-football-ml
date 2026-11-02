"""Dimension tables: ``dim_team``, ``dim_game`` and the player crosswalk ``dim_player``.

``dim_player`` is keyed by the nflverse ``gsis_id`` (``player_id``). External ids
are filled from several sources in a fixed precedence (DECISIONS D-022) and are
always strings (D-023):

1. the manual override file ``ffml/data/crosswalk_overrides.csv`` (a human
   verified these, so they win over every automatic source);
2. raw ``players`` (base rows; ``espn_id``, ``pfr_id``, ``nfl_id``);
3. raw ``ff_playerids`` joined on ``gsis_id``;
4. raw ``rosters_weekly`` (most recent non-null value per player);
5. the Sleeper players map, through its (whitespace-stripped) ``gsis_id``, for
   ``sleeper_id`` only;
6. name matching (normalised name + fantasy position + team) against the
   Sleeper map for players with at least one opportunity that still lack a
   ``sleeper_id`` (``crosswalk_source = "name_match"``).

``crosswalk_source`` records where the ``sleeper_id`` came from. A ``sleeper_id``
may belong to one player only: on a conflict the player with the most recent
season of activity keeps it and the others are nulled (and logged).
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from pathlib import Path

import polars as pl

from ffml.data.process.base import (
    FANTASY_POSITION_MAP,
    SKILL_POSITIONS,
    TEAM_ALIAS_GROUPS,
    TEAM_ALIASES,
    PartitionKey,
    ProcessContext,
    assert_unique,
    canonical_team,
    clean_id,
    fantasy_position,
    read_raw_or_none,
    split_by_season,
)
from ffml.data.sleeper import SleeperPlayersCache
from ffml.season import with_kickoff_utc

log = logging.getLogger(__name__)

#: Manual crosswalk overrides: ``gsis_id, sleeper_id, espn_id, yahoo_id, note``.
OVERRIDES_PATH = Path(__file__).resolve().parents[1] / "crosswalk_overrides.csv"
#: A game whose kickoff passed less than this long ago, with no result yet, is in progress.
IN_PROGRESS_WINDOW = timedelta(hours=5)
#: Directory name of the Sleeper players cache under the raw root.
SLEEPER_PLAYERS_DATASET = "sleeper_players"
#: Share of players with opportunities that must carry a ``sleeper_id`` (DATA_SCHEMA.md 6).
COVERAGE_GATE = 0.98

TEAM_COLUMNS: dict[str, str] = {
    "team_abbr": "team",
    "team_name": "team_name",
    "team_nick": "team_nick",
    "team_conf": "conf",
    "team_division": "division",
    "team_color": "color",
    "team_color2": "color2",
    "team_color3": "color3",
    "team_color4": "color4",
    "team_logo_wikipedia": "logo_wikipedia",
    "team_logo_espn": "logo_espn",
    "team_logo_squared": "logo_squared",
    "team_wordmark": "wordmark",
}

GAME_COLUMNS: list[str] = [
    "game_id",
    "season",
    "week",
    "game_type",
    "kickoff_utc",
    "home_team",
    "away_team",
    "home_score",
    "away_score",
    "result",
    "total",
    "status",
    "spread_line",
    "total_line",
    "home_moneyline",
    "away_moneyline",
    "roof",
    "surface",
    "temp",
    "wind",
    "home_rest",
    "away_rest",
    "div_game",
    "stadium_id",
    "stadium",
    "home_qb_name",
    "away_qb_name",
]
_GAME_FLOAT_COLUMNS = (
    "spread_line",
    "total_line",
    "home_moneyline",
    "away_moneyline",
    "temp",
    "wind",
)
_GAME_INT_COLUMNS = (
    "home_score",
    "away_score",
    "result",
    "total",
    "home_rest",
    "away_rest",
    "div_game",
)

ROSTER_ID_COLUMNS: list[str] = [
    "season",
    "week",
    "gsis_id",
    "sleeper_id",
    "espn_id",
    "yahoo_id",
    "pfr_id",
]
STATS_ACTIVITY_COLUMNS: list[str] = [
    "player_id",
    "season",
    "week",
    "team",
    "position",
    "targets",
    "carries",
    "attempts",
    "fg_att",
    "pat_att",
]
OPPORTUNITY_COLUMNS: tuple[str, ...] = ("targets", "carries", "attempts", "fg_att", "pat_att")

ID_COLUMNS: tuple[str, ...] = (
    "sleeper_id",
    "espn_id",
    "yahoo_id",
    "pfr_id",
    "fantasypros_id",
    "mfl_id",
    "nfl_id",
)

PLAYER_COLUMNS: list[str] = [
    "player_id",
    "full_name",
    "first_name",
    "last_name",
    "position",
    "position_raw",
    "birth_date",
    "rookie_season",
    "draft_year",
    "draft_round",
    "draft_pick",
    *ID_COLUMNS,
    "crosswalk_source",
    "headshot_url",
    "latest_team",
    "status",
    "height",
    "weight",
    "college_name",
    "last_season",
    "last_active_season",
    "updated_at",
]

_SUFFIX_PATTERN = r"\s+(jr|sr|ii|iii|iv|v)\b"


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_dims(
    ctx: ProcessContext, now: datetime | None = None
) -> dict[str, dict[PartitionKey, pl.DataFrame]]:
    """Build ``dim_team`` (key ``all``), ``dim_game`` (by season) and ``dim_player`` (``all``)."""
    now = now or datetime.now(UTC)

    dim_team = build_dim_team(ctx.read_raw("teams"))
    log.info("dim_team: %d franchises", dim_team.height)

    schedules = ctx.read_raw("schedules", seasons=list(ctx.seasons))
    dim_game = build_dim_game(schedules, now=now)
    log.info(
        "dim_game: %d games, status counts %s",
        dim_game.height,
        dict(dim_game["status"].value_counts().iter_rows()),
    )

    players = ctx.read_raw("players")
    ff_ids = ctx.read_raw("ff_playerids")
    rosters = read_raw_or_none(
        ctx, "rosters_weekly", seasons=list(ctx.seasons), columns=ROSTER_ID_COLUMNS
    )
    stats = read_raw_or_none(
        ctx, "player_stats", seasons=list(ctx.seasons), columns=STATS_ACTIVITY_COLUMNS
    )
    sleeper = load_sleeper_players(ctx)
    overrides = load_overrides(OVERRIDES_PATH)
    dim_player = build_dim_player(
        players, ff_ids, rosters=rosters, sleeper=sleeper, stats=stats, overrides=overrides
    )
    log.info(
        "dim_player: %d players, crosswalk sources %s",
        dim_player.height,
        dict(dim_player["crosswalk_source"].value_counts().iter_rows()),
    )
    if stats is not None:
        coverage = crosswalk_coverage(dim_player, stats)
        for row in coverage.iter_rows(named=True):
            level = logging.INFO if row["coverage"] >= COVERAGE_GATE else logging.WARNING
            log.log(
                level,
                "crosswalk coverage %d: %d of %d players with opportunities have a sleeper_id (%.4f)%s",
                row["season"],
                row["matched"],
                row["players"],
                row["coverage"],
                f"; unmatched: {row['unmatched']}" if row["unmatched"] else "",
            )

    return {
        "dim_team": {"all": dim_team},
        "dim_game": split_by_season(dim_game),
        "dim_player": {"all": dim_player},
    }


# --------------------------------------------------------------------------- #
# dim_team
# --------------------------------------------------------------------------- #


def build_dim_team(teams: pl.DataFrame) -> pl.DataFrame:
    """The current franchises from raw ``teams`` with an ``aliases`` list of historical codes."""
    selected = [pl.col(src).alias(dst) for src, dst in TEAM_COLUMNS.items() if src in teams.columns]
    df = (
        teams.filter(~pl.col("team_abbr").is_in(list(TEAM_ALIASES)))
        .select(selected)
        .unique(subset=["team"], keep="first", maintain_order=True)
        .sort("team")
    )
    aliases = [TEAM_ALIAS_GROUPS.get(team, []) for team in df["team"].to_list()]
    df = df.with_columns(pl.Series("aliases", aliases, dtype=pl.List(pl.String)))
    assert_unique(df, ["team"], "dim_team")
    if df.height != 32:
        log.warning("dim_team has %d rows, expected 32", df.height)
    return df


# --------------------------------------------------------------------------- #
# dim_game
# --------------------------------------------------------------------------- #


def build_dim_game(schedules: pl.DataFrame, now: datetime) -> pl.DataFrame:
    """One row per scheduled game with ``kickoff_utc`` and a ``status`` relative to ``now``."""
    now = now.astimezone(UTC) if now.tzinfo else now.replace(tzinfo=UTC)
    df = with_kickoff_utc(schedules).with_columns(
        canonical_team("home_team"), canonical_team("away_team")
    )
    window_start = now - IN_PROGRESS_WINDOW
    status = (
        pl.when(pl.col("result").is_not_null())
        .then(pl.lit("final"))
        .when(
            (pl.col("kickoff_utc") <= pl.lit(now)) & (pl.col("kickoff_utc") > pl.lit(window_start))
        )
        .then(pl.lit("in_progress"))
        .otherwise(pl.lit("scheduled"))
    )
    df = df.with_columns(status.alias("status"))
    exprs: list[pl.Expr] = []
    for name in GAME_COLUMNS:
        if name not in df.columns:
            exprs.append(pl.lit(None, dtype=pl.String).alias(name))
        elif name in _GAME_FLOAT_COLUMNS:
            exprs.append(pl.col(name).cast(pl.Float64))
        elif name in _GAME_INT_COLUMNS:
            exprs.append(pl.col(name).cast(pl.Int32))
        else:
            exprs.append(pl.col(name))
    out = df.select(exprs).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    out = out.sort(["season", "week", "kickoff_utc", "game_id"])
    assert_unique(out, ["game_id"], "dim_game")
    return out


# --------------------------------------------------------------------------- #
# dim_player
# --------------------------------------------------------------------------- #


def load_sleeper_players(ctx: ProcessContext) -> pl.DataFrame | None:
    """Today's Sleeper players map (fetched at most once per UTC day) or ``None`` on failure."""
    try:
        cache = SleeperPlayersCache(root=ctx.raw.root / SLEEPER_PLAYERS_DATASET)
        frame = cache.get()
        latest = cache.latest()
        if latest is not None:
            ctx.raw_snapshot_ids[SLEEPER_PLAYERS_DATASET] = f"{latest:%Y%m%d}"
        log.info("sleeper players map: %d rows", frame.height)
        return frame
    except Exception as exc:  # noqa: BLE001 - the crosswalk degrades gracefully without Sleeper
        log.warning(
            "Sleeper players map unavailable (%s: %s); continuing without it",
            type(exc).__name__,
            exc,
        )
        return None


def load_overrides(path: Path) -> pl.DataFrame:
    """Read the manual override CSV (all columns as strings); empty frame when absent.

    Columns: ``gsis_id, sleeper_id, espn_id, yahoo_id, position, note``. A non-null
    ``position`` forces the fantasy position (two-way players such as a CB who
    plays WR are listed as defenders by nflverse).
    """
    schema = {
        "gsis_id": pl.String,
        "sleeper_id": pl.String,
        "espn_id": pl.String,
        "yahoo_id": pl.String,
        "position": pl.String,
        "note": pl.String,
    }
    if not path.exists():
        return pl.DataFrame(schema=schema)
    df = pl.read_csv(path, schema_overrides=schema, infer_schema_length=0)
    for name in schema:
        if name not in df.columns:
            df = df.with_columns(pl.lit(None, dtype=pl.String).alias(name))
    return df.select(list(schema))


def normalized_name(expr: pl.Expr) -> pl.Expr:
    """Lower case, no punctuation, no generational suffix, single spaces."""
    return (
        expr.str.to_lowercase()
        .str.replace_all(r"[^a-z0-9 ]", "")
        .str.replace_all(_SUFFIX_PATTERN, "")
        .str.replace_all(_SUFFIX_PATTERN, "")
        .str.replace_all(r"\s+", " ")
        .str.strip_chars()
    )


def _col_or_null(df: pl.DataFrame, name: str, dtype: pl.DataType) -> pl.Expr:
    if name in df.columns:
        return pl.col(name).cast(dtype, strict=False).alias(name)
    return pl.lit(None, dtype=dtype).alias(name)


def _player_base(players: pl.DataFrame) -> pl.DataFrame:
    birth = pl.col("birth_date")
    if "birth_date" not in players.columns:
        birth = pl.lit(None, dtype=pl.Date)
    elif players.schema["birth_date"] == pl.String:
        birth = birth.str.strip_chars().str.to_date("%Y-%m-%d", strict=False)
    elif players.schema["birth_date"] != pl.Date:
        birth = birth.cast(pl.Date, strict=False)
    return (
        players.filter(pl.col("gsis_id").is_not_null())
        .select(
            pl.col("gsis_id").alias("player_id"),
            _col_or_null(players, "display_name", pl.String).alias("full_name"),
            _col_or_null(players, "first_name", pl.String),
            _col_or_null(players, "last_name", pl.String),
            _col_or_null(players, "position", pl.String).alias("position_raw"),
            birth.alias("birth_date"),
            _col_or_null(players, "rookie_season", pl.Int32),
            _col_or_null(players, "draft_year", pl.Int32),
            _col_or_null(players, "draft_round", pl.Int32),
            _col_or_null(players, "draft_pick", pl.Int32),
            _col_or_null(players, "espn_id", pl.String).alias("espn_id__players"),
            _col_or_null(players, "pfr_id", pl.String).alias("pfr_id__players"),
            _col_or_null(players, "nfl_id", pl.String).alias("nfl_id"),
            _col_or_null(players, "headshot", pl.String).alias("headshot_url"),
            _col_or_null(players, "latest_team", pl.String),
            _col_or_null(players, "status", pl.String),
            _col_or_null(players, "height", pl.Int32),
            _col_or_null(players, "weight", pl.Int32),
            _col_or_null(players, "college_name", pl.String),
            _col_or_null(players, "last_season", pl.Int32),
        )
        .with_columns(
            clean_id("player_id"),
            clean_id("espn_id__players"),
            clean_id("pfr_id__players"),
            clean_id("nfl_id"),
            canonical_team("latest_team"),
        )
        .unique(subset=["player_id"], keep="first", maintain_order=True)
    )


def _ff_ids(ff_ids: pl.DataFrame) -> pl.DataFrame:
    cols = ("sleeper_id", "espn_id", "yahoo_id", "pfr_id", "fantasypros_id", "mfl_id")
    df = ff_ids.select(
        _col_or_null(ff_ids, "gsis_id", pl.String).alias("player_id"),
        *[_col_or_null(ff_ids, c, pl.String) for c in cols],
        _col_or_null(ff_ids, "db_season", pl.Int32),
    ).with_columns(clean_id("player_id"), *[clean_id(c) for c in cols])
    df = (
        df.filter(pl.col("player_id").is_not_null())
        .sort(
            [pl.col("sleeper_id").is_not_null(), pl.col("db_season")],
            descending=[True, True],
            nulls_last=True,
        )
        .unique(subset=["player_id"], keep="first", maintain_order=True)
    )
    return df.select("player_id", *[pl.col(c).alias(f"{c}__ff") for c in cols])


def _roster_ids(rosters: pl.DataFrame | None) -> pl.DataFrame:
    cols = ("sleeper_id", "espn_id", "yahoo_id", "pfr_id")
    if rosters is None or rosters.is_empty():
        return pl.DataFrame(
            schema={"player_id": pl.String, **{f"{c}__rw": pl.String for c in cols}}
        )
    df = rosters.select(
        _col_or_null(rosters, "gsis_id", pl.String).alias("player_id"),
        _col_or_null(rosters, "season", pl.Int32),
        _col_or_null(rosters, "week", pl.Int32),
        *[_col_or_null(rosters, c, pl.String) for c in cols],
    ).with_columns(clean_id("player_id"), *[clean_id(c) for c in cols])
    df = (
        df.filter(pl.col("player_id").is_not_null())
        .sort(["season", "week"], descending=True, nulls_last=True)
        .group_by("player_id", maintain_order=True)
        .agg([pl.col(c).drop_nulls().first().alias(f"{c}__rw") for c in cols])
    )
    return df


def _sleeper_prepared(sleeper: pl.DataFrame | None) -> pl.DataFrame | None:
    if sleeper is None or sleeper.is_empty():
        return None
    df = sleeper.select(
        _col_or_null(sleeper, "sleeper_id", pl.String),
        _col_or_null(sleeper, "gsis_id", pl.String),
        _col_or_null(sleeper, "full_name", pl.String),
        _col_or_null(sleeper, "position", pl.String),
        _col_or_null(sleeper, "team", pl.String),
        _col_or_null(sleeper, "active", pl.Boolean),
        _col_or_null(sleeper, "status", pl.String),
        _col_or_null(sleeper, "search_rank", pl.Int32),
    ).with_columns(
        clean_id("sleeper_id"),
        clean_id("gsis_id"),
        canonical_team("team"),
        fantasy_position("position"),
    )
    return df.filter(pl.col("sleeper_id").is_not_null())


def _sleeper_by_gsis(sleeper: pl.DataFrame) -> pl.DataFrame:
    return (
        sleeper.filter(pl.col("gsis_id").is_not_null())
        .sort(
            [
                pl.col("active").fill_null(False),
                pl.col("full_name").fill_null("") != "Duplicate Player",
                pl.col("status") == "Active",
                pl.col("search_rank"),
            ],
            descending=[True, True, True, False],
            nulls_last=True,
        )
        .unique(subset=["gsis_id"], keep="first", maintain_order=True)
        .select(pl.col("gsis_id").alias("player_id"), pl.col("sleeper_id").alias("sleeper_id__sp"))
    )


def _activity(stats: pl.DataFrame | None) -> pl.DataFrame:
    schema = {
        "player_id": pl.String,
        "last_active_season": pl.Int32,
        "last_stats_season": pl.Int32,
        "last_team": pl.String,
        "stats_position": pl.String,
        "opportunities": pl.Int64,
    }
    if stats is None or stats.is_empty():
        return pl.DataFrame(schema=schema)
    opp = pl.sum_horizontal(
        [_col_or_null(stats, c, pl.Int64).fill_null(0) for c in OPPORTUNITY_COLUMNS]
    ).alias("opportunities")
    df = stats.select(
        pl.col("player_id"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        _col_or_null(stats, "team", pl.String),
        _col_or_null(stats, "position", pl.String),
        opp,
    ).with_columns(clean_id("player_id"), canonical_team("team"), fantasy_position("position"))
    return (
        df.filter(pl.col("player_id").is_not_null())
        .sort(["season", "week"])
        .group_by("player_id", maintain_order=True)
        .agg(
            pl.col("season").filter(pl.col("opportunities") > 0).max().alias("last_active_season"),
            pl.col("season").max().alias("last_stats_season"),
            pl.col("team").last().alias("last_team"),
            pl.col("position").last().alias("stats_position"),
            pl.col("opportunities").sum().alias("opportunities"),
        )
    )


def _overrides_prepared(overrides: pl.DataFrame | None) -> pl.DataFrame:
    cols = ("sleeper_id", "espn_id", "yahoo_id", "position")
    schema = {"player_id": pl.String, **{f"{c}__man": pl.String for c in cols}}
    if overrides is None or overrides.is_empty():
        return pl.DataFrame(schema=schema)
    df = overrides.select(
        _col_or_null(overrides, "gsis_id", pl.String).alias("player_id"),
        *[_col_or_null(overrides, c, pl.String) for c in cols],
    ).with_columns(clean_id("player_id"), *[clean_id(c) for c in cols])
    df = df.filter(pl.col("player_id").is_not_null()).unique(
        subset=["player_id"], keep="last", maintain_order=True
    )
    return df.select("player_id", *[pl.col(c).alias(f"{c}__man") for c in cols])


def _name_match(df: pl.DataFrame, sleeper: pl.DataFrame) -> pl.DataFrame:
    """Match players with opportunities but no sleeper_id on (name, position, team)."""
    assigned = df.filter(pl.col("sleeper_id").is_not_null())["sleeper_id"]
    need = (
        df.filter(
            pl.col("sleeper_id").is_null()
            & (pl.col("opportunities").fill_null(0) > 0)
            & pl.col("position").is_in(list(SKILL_POSITIONS))
            & pl.col("full_name").is_not_null()
        )
        .with_columns(
            normalized_name(pl.col("full_name")).alias("key_name"),
            pl.coalesce([pl.col("last_team"), pl.col("latest_team")]).alias("key_team"),
        )
        .select("player_id", "key_name", "position", "key_team")
        .filter(pl.col("key_team").is_not_null() & (pl.col("key_name") != ""))
    )
    if need.is_empty():
        return df
    candidates = (
        sleeper.filter(
            pl.col("full_name").is_not_null()
            & pl.col("team").is_not_null()
            & ~pl.col("sleeper_id").is_in(assigned.to_list())
        )
        .with_columns(normalized_name(pl.col("full_name")).alias("key_name"))
        .group_by(["key_name", "position", "team"])
        .agg(pl.col("sleeper_id").n_unique().alias("n_ids"), pl.col("sleeper_id").first())
        .filter(pl.col("n_ids") == 1)
        .rename({"team": "key_team"})
        .select("key_name", "position", "key_team", "sleeper_id")
    )
    matched = need.join(candidates, on=["key_name", "position", "key_team"], how="inner")
    matched = matched.filter(~pl.col("sleeper_id").is_duplicated())
    if matched.is_empty():
        log.info("name match: no new sleeper ids for %d candidates", need.height)
        return df
    log.info("name match: %d of %d candidates matched", matched.height, need.height)
    df = df.join(
        matched.select("player_id", pl.col("sleeper_id").alias("sleeper_id__nm")),
        on="player_id",
        how="left",
    )
    return df.with_columns(
        pl.when(pl.col("sleeper_id").is_null() & pl.col("sleeper_id__nm").is_not_null())
        .then(pl.lit("name_match"))
        .otherwise(pl.col("crosswalk_source"))
        .alias("crosswalk_source"),
        pl.coalesce([pl.col("sleeper_id"), pl.col("sleeper_id__nm")]).alias("sleeper_id"),
    ).drop("sleeper_id__nm")


def _enforce_unique_sleeper(df: pl.DataFrame) -> pl.DataFrame:
    """Keep one player per sleeper_id (most recent activity wins); null the rest."""
    with_id = df.filter(pl.col("sleeper_id").is_not_null())
    dup_ids = with_id.filter(pl.col("sleeper_id").is_duplicated())
    if dup_ids.is_empty():
        return df
    ranked = dup_ids.sort(
        ["last_active_season", "last_stats_season", "last_season", "player_id"],
        descending=[True, True, True, False],
        nulls_last=True,
    ).with_columns(pl.int_range(pl.len()).over("sleeper_id").alias("_rank"))
    winners = ranked.filter(pl.col("_rank") == 0).select(
        "sleeper_id", pl.col("player_id").alias("winner"), pl.col("full_name").alias("winner_name")
    )
    losers = ranked.filter(pl.col("_rank") > 0).join(winners, on="sleeper_id", how="left")
    for row in losers.select(
        "sleeper_id", "player_id", "full_name", "winner", "winner_name", "last_active_season"
    ).iter_rows(named=True):
        log.warning(
            "sleeper_id %s claimed by %s (%s) and %s (%s); keeping %s, nulling %s (last active %s)",
            row["sleeper_id"],
            row["winner"],
            row["winner_name"],
            row["player_id"],
            row["full_name"],
            row["winner"],
            row["player_id"],
            row["last_active_season"],
        )
    loser_ids = losers["player_id"].to_list()
    return df.with_columns(
        pl.when(pl.col("player_id").is_in(loser_ids))
        .then(None)
        .otherwise(pl.col("sleeper_id"))
        .alias("sleeper_id"),
        pl.when(pl.col("player_id").is_in(loser_ids))
        .then(None)
        .otherwise(pl.col("crosswalk_source"))
        .alias("crosswalk_source"),
    )


def build_dim_player(
    players: pl.DataFrame,
    ff_ids: pl.DataFrame,
    rosters: pl.DataFrame | None = None,
    sleeper: pl.DataFrame | None = None,
    stats: pl.DataFrame | None = None,
    overrides: pl.DataFrame | None = None,
    updated_at: datetime | None = None,
) -> pl.DataFrame:
    """Assemble the player crosswalk (see the module docstring for the precedence)."""
    base = _player_base(players)
    sleeper_prepared = _sleeper_prepared(sleeper)
    df = (
        base.join(_ff_ids(ff_ids), on="player_id", how="left")
        .join(_roster_ids(rosters), on="player_id", how="left")
        .join(_overrides_prepared(overrides), on="player_id", how="left")
        .join(_activity(stats), on="player_id", how="left")
    )
    if sleeper_prepared is not None:
        df = df.join(_sleeper_by_gsis(sleeper_prepared), on="player_id", how="left")
    else:
        df = df.with_columns(pl.lit(None, dtype=pl.String).alias("sleeper_id__sp"))

    source = (
        pl.when(pl.col("sleeper_id__man").is_not_null())
        .then(pl.lit("manual"))
        .when(pl.col("sleeper_id__ff").is_not_null())
        .then(pl.lit("ff_playerids"))
        .when(pl.col("sleeper_id__rw").is_not_null())
        .then(pl.lit("rosters_weekly"))
        .when(pl.col("sleeper_id__sp").is_not_null())
        .then(pl.lit("sleeper_players"))
        .otherwise(pl.lit(None, dtype=pl.String))
    )
    df = df.with_columns(
        pl.coalesce(
            [
                pl.col("sleeper_id__man"),
                pl.col("sleeper_id__ff"),
                pl.col("sleeper_id__rw"),
                pl.col("sleeper_id__sp"),
            ]
        ).alias("sleeper_id"),
        source.alias("crosswalk_source"),
        pl.coalesce(
            [
                pl.col("espn_id__man"),
                pl.col("espn_id__players"),
                pl.col("espn_id__ff"),
                pl.col("espn_id__rw"),
            ]
        ).alias("espn_id"),
        pl.coalesce(
            [pl.col("yahoo_id__man"), pl.col("yahoo_id__ff"), pl.col("yahoo_id__rw")]
        ).alias("yahoo_id"),
        pl.coalesce([pl.col("pfr_id__players"), pl.col("pfr_id__ff"), pl.col("pfr_id__rw")]).alias(
            "pfr_id"
        ),
        pl.col("fantasypros_id__ff").alias("fantasypros_id"),
        pl.col("mfl_id__ff").alias("mfl_id"),
        pl.coalesce(
            [
                pl.col("position__man"),
                pl.coalesce([pl.col("position_raw"), pl.col("stats_position")]).replace(
                    FANTASY_POSITION_MAP
                ),
            ]
        ).alias("position"),
    )
    if sleeper_prepared is not None:
        df = _name_match(df, sleeper_prepared)
    df = _enforce_unique_sleeper(df)

    stamp = (updated_at or datetime.now(UTC)).astimezone(UTC).replace(tzinfo=None)
    out = (
        df.with_columns(pl.lit(stamp).dt.replace_time_zone("UTC").alias("updated_at"))
        .select(PLAYER_COLUMNS)
        .sort("player_id")
    )
    assert_unique(out, ["player_id"], "dim_player")
    assert_unique(
        out.filter(pl.col("sleeper_id").is_not_null()), ["sleeper_id"], "dim_player.sleeper_id"
    )
    return out


def crosswalk_coverage(dim_player: pl.DataFrame, stats: pl.DataFrame) -> pl.DataFrame:
    """Per season: skill players with at least one opportunity and the share with a ``sleeper_id``.

    Columns: ``season, players, matched, coverage, unmatched`` (list of
    ``"name (position, team)"`` strings for the players without a ``sleeper_id``).
    """
    opp = pl.sum_horizontal(
        [_col_or_null(stats, c, pl.Int64).fill_null(0) for c in OPPORTUNITY_COLUMNS]
    ).alias("opportunities")
    per_player = (
        stats.select(
            pl.col("player_id"),
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            _col_or_null(stats, "team", pl.String),
            opp,
        )
        .with_columns(clean_id("player_id"), canonical_team("team"))
        .filter(pl.col("player_id").is_not_null())
        .sort(["season", "week"])
        .group_by(["season", "player_id"], maintain_order=True)
        .agg(pl.col("opportunities").sum(), pl.col("team").last())
        .filter(pl.col("opportunities") > 0)
        .join(
            dim_player.select("player_id", "full_name", "position", "sleeper_id"),
            on="player_id",
            how="inner",
        )
        .filter(pl.col("position").is_in(list(SKILL_POSITIONS)))
    )
    label = pl.format("{} ({}, {})", pl.col("full_name"), pl.col("position"), pl.col("team"))
    return (
        per_player.group_by("season", maintain_order=True)
        .agg(
            pl.len().alias("players"),
            pl.col("sleeper_id").is_not_null().sum().alias("matched"),
            label.filter(pl.col("sleeper_id").is_null()).alias("unmatched"),
        )
        .with_columns((pl.col("matched") / pl.col("players")).alias("coverage"))
        .select("season", "players", "matched", "coverage", "unmatched")
        .sort("season")
    )


__all__ = [
    "COVERAGE_GATE",
    "OVERRIDES_PATH",
    "build_dim_game",
    "build_dim_player",
    "build_dim_team",
    "build_dims",
    "crosswalk_coverage",
    "load_overrides",
    "load_sleeper_players",
    "normalized_name",
]
