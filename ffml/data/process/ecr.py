"""``fact_ecr_weekly``: FantasyPros weekly expert-consensus rankings (RESTRICTED).

D-007 RULE
----------
This table is a **private evaluation input only**. It is derived from the
restricted raw dataset ``restricted/ff_rankings_all`` (FantasyPros ECR
redistributed by DynastyProcess; data licence unverified, FantasyPros content
proprietary). The runner writes it under ``data/processed/restricted/``
because ``fact_ecr_weekly`` is listed in ``RESTRICTED_TABLES``. It must
**never be read by the API or the web app, never be committed, and never be
shown in public reports** (committed backtest reports and the model card may
carry aggregate comparison metrics only, no ranking rows). The evaluator
checks these constraints at Gate 3.

What the table holds
--------------------
Weekly positional pages (``ecr_type == "wp"``) for QB, RB, WR, TE, K and DST.
``scoring`` is derived from the page name (``ppr-*`` -> ``ppr``,
``half-point-ppr-*`` -> ``half``, anything else -> ``std``; QB, K and DST
pages are scoring-independent and therefore ``std``). ``fp_page`` is stored
as the normalised page name (``ppr-wr``) because the archive switched from
``/nfl/rankings/ppr-wr.php`` to bare names in later scrapes.

``season``, ``week`` and ``game_type`` come from the same schedule-window rule
as the depth charts (:func:`ffml.data.process.context.assign_week`): week
``w`` is the first week whose last kickoff is on or after the scrape date
(the scrape date is taken as midnight America/New_York). ``valid_for_week``
is true when the scrape date is strictly before the calendar date (ET) of
the first kickoff of that week. The archive scrapes mostly on Fridays, i.e.
after Thursday Night Football, so ``valid_for_week`` is false for almost
every week that starts on a Thursday. For that reason the table also carries
``team_game_date`` (ET date of the ranked team's game in that week, null on a
bye or when the team is unknown) and ``valid_for_game`` (scrape date strictly
before ``team_game_date``), which is the flag the evaluator should use for a
per-player comparison: a Friday scrape is a legitimate pre-game ranking for
every player whose game is on Sunday or Monday.

``player_id`` is the nflverse ``gsis_id`` obtained by mapping the FantasyPros
id through the raw ``ff_playerids`` crosswalk (read directly, not through
``dim_player``, so this builder has no dependency on the dimension tables).
Rows without a match are kept with a null ``player_id``; team defenses never
match because the crosswalk holds players only. Match rates are logged per
season.
"""

from __future__ import annotations

import logging

import polars as pl

from ffml.data.process.base import (
    PartitionKey,
    ProcessContext,
    assert_unique,
    canonical_team,
    split_by_season,
)
from ffml.data.process.context import assign_week, id_as_string, week_windows
from ffml.season import ET_ZONE, SCHEDULE_COLUMNS, with_kickoff_utc

log = logging.getLogger(__name__)

RANKINGS_DATASET = "restricted/ff_rankings_all"
WEEKLY_ECR_TYPE = "wp"
POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K", "DST")
POSITION_ALIASES: dict[str, str] = {"PK": "K", "DEF": "DST", "D/ST": "DST", "D": "DST"}
NO_TEAM_CODES: tuple[str, ...] = ("FA", "")

RAW_COLUMNS = [
    "fp_page",
    "player",
    "id",
    "pos",
    "team",
    "ecr",
    "sd",
    "best",
    "worst",
    "ecr_type",
    "scrape_date",
]
ECR_KEYS = ["scrape_date", "fp_page", "fantasypros_id"]
ECR_COLUMNS = [
    "season",
    "week",
    "game_type",
    "scrape_date",
    "scoring",
    "position",
    "fantasypros_id",
    "player_id",
    "player_name",
    "team",
    "ecr",
    "sd",
    "best",
    "worst",
    "valid_for_week",
    "team_game_date",
    "valid_for_game",
    "fp_page",
]


def page_name(expr: pl.Expr) -> pl.Expr:
    """``/nfl/rankings/ppr-wr.php`` -> ``ppr-wr``; bare names pass through."""
    return (
        expr.cast(pl.String)
        .str.strip_chars()
        .str.to_lowercase()
        .str.extract(r"([^/]+?)(?:\.php)?$", 1)
    )


def scoring_from_page(page: pl.Expr) -> pl.Expr:
    return (
        pl.when(page.str.starts_with("ppr-"))
        .then(pl.lit("ppr"))
        .when(page.str.starts_with("half-point-ppr-"))
        .then(pl.lit("half"))
        .otherwise(pl.lit("std"))
    )


def normalize_position(expr: pl.Expr) -> pl.Expr:
    return expr.cast(pl.String).str.strip_chars().str.to_uppercase().replace(POSITION_ALIASES)


def fantasypros_crosswalk(playerids: pl.DataFrame) -> pl.DataFrame:
    """``fantasypros_id`` (String) -> ``player_id`` (gsis_id), one row per FantasyPros id."""
    return (
        playerids.select(
            id_as_string("fantasypros_id"),
            pl.col("gsis_id").cast(pl.String).str.strip_chars().alias("player_id"),
        )
        .filter(
            pl.col("fantasypros_id").is_not_null()
            & pl.col("player_id").is_not_null()
            & (pl.col("player_id") != "")
        )
        .unique(subset=["fantasypros_id"], keep="first", maintain_order=True)
    )


def team_game_dates(schedules: pl.DataFrame) -> pl.DataFrame:
    """``(season, week, team) -> team_game_date`` (ET calendar date of the team's kickoff)."""
    games = schedules
    if "kickoff_utc" not in games.columns:
        games = with_kickoff_utc(games.select([c for c in SCHEDULE_COLUMNS if c in games.columns]))
    games = games.with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("kickoff_utc").dt.convert_time_zone(ET_ZONE).dt.date().alias("team_game_date"),
    )
    sides = [
        games.select("season", "week", pl.col(side).cast(pl.String).alias("team"), "team_game_date")
        for side in ("home_team", "away_team")
    ]
    return (
        pl.concat(sides, how="vertical")
        .with_columns(canonical_team("team"))
        .sort(["season", "week", "team", "team_game_date"])
        .unique(subset=["season", "week", "team"], keep="first", maintain_order=True)
    )


def build_ecr_frame(
    rankings: pl.DataFrame, schedules: pl.DataFrame, playerids: pl.DataFrame
) -> pl.DataFrame:
    """Pure transformation from the raw archive to the ``fact_ecr_weekly`` layout."""
    windows = week_windows(schedules)
    wp = rankings.filter(pl.col("ecr_type") == WEEKLY_ECR_TYPE).with_columns(
        page_name(pl.col("fp_page")).alias("fp_page"),
        normalize_position(pl.col("pos")).alias("position"),
    )
    wp = wp.filter(pl.col("position").is_in(POSITIONS))
    wp = wp.with_columns(
        scoring_from_page(pl.col("fp_page")).alias("scoring"),
        pl.col("scrape_date")
        .cast(pl.String)
        .str.strip_chars()
        .str.to_date("%Y-%m-%d", strict=False)
        .alias("scrape_date"),
        id_as_string("id", "fantasypros_id"),
        pl.col("player").cast(pl.String).alias("player_name"),
        pl.when(pl.col("team").cast(pl.String).str.to_uppercase().is_in(NO_TEAM_CODES))
        .then(pl.lit(None, dtype=pl.String))
        .otherwise(pl.col("team").cast(pl.String).str.to_uppercase())
        .alias("team"),
        *[pl.col(c).cast(pl.Float64) for c in ("ecr", "sd", "best", "worst")],
    ).with_columns(canonical_team("team"))

    bad_dates = wp.filter(
        pl.col("scrape_date").is_null() | pl.col("fantasypros_id").is_null()
    ).height
    if bad_dates:
        log.warning("fact_ecr_weekly: dropping %d rows without scrape_date or id", bad_dates)
        wp = wp.filter(pl.col("scrape_date").is_not_null() & pl.col("fantasypros_id").is_not_null())

    # Midnight ET of the scrape date, so "last kickoff on or after the scrape date" holds.
    wp = wp.with_columns(
        pl.col("scrape_date")
        .cast(pl.Datetime("us"))
        .dt.replace_time_zone(ET_ZONE)
        .dt.convert_time_zone("UTC")
        .alias("_ts")
    )
    wp = assign_week(wp, windows, "_ts")
    first = windows.select(
        "season",
        "week",
        pl.col("first_kickoff_utc")
        .dt.convert_time_zone(ET_ZONE)
        .dt.date()
        .alias("_first_kickoff_date"),
    )
    wp = wp.join(first, on=["season", "week"], how="left").with_columns(
        (pl.col("scrape_date") < pl.col("_first_kickoff_date"))
        .fill_null(False)
        .alias("valid_for_week")
    )
    wp = wp.join(
        team_game_dates(schedules), on=["season", "week", "team"], how="left"
    ).with_columns(
        pl.when(pl.col("team_game_date").is_null())
        .then(pl.lit(None, dtype=pl.Boolean))
        .otherwise(pl.col("scrape_date") < pl.col("team_game_date"))
        .alias("valid_for_game")
    )
    wp = wp.join(fantasypros_crosswalk(playerids), on="fantasypros_id", how="left")

    before = wp.height
    wp = wp.unique(subset=ECR_KEYS, keep="first", maintain_order=True)
    if before - wp.height:
        log.info("fact_ecr_weekly: removed %d duplicate rows on %s", before - wp.height, ECR_KEYS)

    out = wp.select(ECR_COLUMNS).sort(
        ["season", "week", "scrape_date", "scoring", "position", "ecr"]
    )
    assert_unique(out, ECR_KEYS, "fact_ecr_weekly")
    return out


def ecr_summary(df: pl.DataFrame) -> pl.DataFrame:
    """Per season: rows, player match rate (DST excluded), valid ``(week, scoring)`` combos."""
    if df.is_empty():
        return pl.DataFrame(
            schema={
                "season": pl.Int32,
                "rows": pl.UInt32,
                "scrape_dates": pl.UInt32,
                "weeks": pl.UInt32,
                "week_scoring_combos": pl.UInt32,
                "valid_combos": pl.UInt32,
                "game_valid_combos": pl.UInt32,
                "game_valid_share": pl.Float64,
                "player_rows": pl.UInt32,
                "match_rate": pl.Float64,
            }
        )
    combos = df.group_by("season").agg(
        pl.len().alias("rows"),
        pl.col("scrape_date").n_unique().alias("scrape_dates"),
        pl.col("week").filter(pl.col("week") > 0).n_unique().alias("weeks"),
        pl.struct("week", "scoring").n_unique().alias("week_scoring_combos"),
        pl.struct("week", "scoring")
        .filter(pl.col("valid_for_week"))
        .n_unique()
        .alias("valid_combos"),
        pl.struct("week", "scoring")
        .filter(pl.col("valid_for_game").fill_null(False))
        .n_unique()
        .alias("game_valid_combos"),
        pl.col("valid_for_game").fill_null(False).mean().alias("game_valid_share"),
    )
    match = (
        df.filter(pl.col("position") != "DST")
        .group_by("season")
        .agg(
            pl.len().alias("player_rows"),
            pl.col("player_id").is_not_null().mean().alias("match_rate"),
        )
    )
    return combos.join(match, on="season", how="left").sort("season")


def build_ecr(ctx: ProcessContext) -> dict[str, dict[PartitionKey, pl.DataFrame]]:
    """Build ``fact_ecr_weekly`` (restricted) for the seasons in ``ctx``."""
    rankings = ctx.read_raw(RANKINGS_DATASET, columns=RAW_COLUMNS)
    schedules = ctx.read_raw("schedules", columns=list(SCHEDULE_COLUMNS))
    playerids = ctx.read_raw("ff_playerids", columns=["fantasypros_id", "gsis_id"])
    df = build_ecr_frame(rankings, schedules, playerids)
    df = df.filter(pl.col("season").is_in(list(ctx.seasons)))
    for row in ecr_summary(df).iter_rows(named=True):
        log.info(
            "fact_ecr_weekly %s: rows=%d scrape_dates=%d weeks=%d week_scoring_combos=%d "
            "valid_for_week_combos=%d valid_for_game_combos=%d valid_for_game_share=%.3f "
            "player_match_rate=%.3f",
            row["season"],
            row["rows"],
            row["scrape_dates"],
            row["weeks"],
            row["week_scoring_combos"],
            row["valid_combos"],
            row["game_valid_combos"],
            row["game_valid_share"],
            row["match_rate"] if row["match_rate"] is not None else float("nan"),
        )
    if df.is_empty():
        log.warning("fact_ecr_weekly: no weekly rankings for seasons %s", ctx.seasons)
        return {}
    return {"fact_ecr_weekly": split_by_season(df)}


__all__ = [
    "ECR_COLUMNS",
    "ECR_KEYS",
    "POSITIONS",
    "RANKINGS_DATASET",
    "build_ecr",
    "build_ecr_frame",
    "ecr_summary",
    "fantasypros_crosswalk",
    "normalize_position",
    "page_name",
    "scoring_from_page",
    "team_game_dates",
]
