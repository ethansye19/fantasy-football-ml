"""Context tables: injury reports, depth charts, and weekly rosters (DE-2.4).

``build_context`` produces three seasonal tables from the LATEST raw snapshots:

* ``fact_injury_report`` from ``injuries`` (both source layouts unified, one
  row per ``(season, week, player_id, team)``);
* ``fact_depth_chart`` from ``depth_charts`` (the 2016-2024 weekly format and
  the 2025+ timestamped format unified on one key
  ``(snapshot_ts, team, player_id, pos_abb)``);
* ``fact_roster_week`` from ``rosters_weekly`` (one row per
  ``(season, week, player_id, team)``; a traded player keeps both rows).

Kickoff times are read straight from the raw ``schedules`` snapshot through
:func:`ffml.season.with_kickoff_utc`. This module deliberately does **not**
depend on ``dim_game`` or any other ``dim_*`` table so that it can run before
(or without) the dimension builders.

Week assignment for timestamps
------------------------------
:func:`week_windows` turns the schedule into one window per ``(season, week)``
whose inclusive upper bound is the last kickoff of that week (any game type,
so postseason weeks are covered). A timestamp belongs to the first window
whose upper bound is at or after it, which means: week ``w`` covers the
interval after the last kickoff of week ``w-1`` up to and including the last
kickoff of week ``w``; timestamps before week 1 belong to week 1; timestamps
before the season's first kickoff minus 60 days belong to week 0 of that
season with ``game_type = "offseason"``. Timestamps after the last kickoff in
the schedule table fall back to the raw partition's season, week 0, offseason
(logged); a later rebuild with a newer schedule relabels them.

Legacy weekly depth-chart rows get ``snapshot_ts`` = first kickoff of their
``(season, week)`` minus 48 hours, so "latest snapshot at or before the
cutoff" selects the week-``w`` chart for a cutoff inside week ``w`` exactly as
it does for timestamped rows. Their ``game_type`` is taken from the schedule
for that week (the source labels the wild-card week ``REG`` in some seasons);
``SBBYE`` rows, which carry no week, are dropped and counted.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta
from typing import Any

import polars as pl

from ffml.data.process.base import (
    PartitionKey,
    ProcessContext,
    assert_unique,
    canonical_team,
    split_by_season,
)
from ffml.season import SCHEDULE_COLUMNS, with_kickoff_utc

log = logging.getLogger(__name__)

OFFSEASON = "offseason"
OFFSEASON_LEAD = timedelta(days=60)
LEGACY_SNAPSHOT_LEAD = timedelta(hours=48)
UTC_US = pl.Datetime("us", "UTC")

INJURY_KEYS = ["season", "week", "player_id", "team"]
DEPTH_CHART_KEYS = ["snapshot_ts", "team", "player_id", "pos_abb"]
ROSTER_KEYS = ["season", "week", "player_id", "team"]

#: Lower is more severe; statuses not listed (``Note``, null) sort last.
STATUS_SEVERITY: dict[str, int] = {"Out": 0, "Doubtful": 1, "Questionable": 2}

INJURY_COLUMNS = [
    "season",
    "week",
    "game_type",
    "team",
    "player_id",
    "position",
    "full_name",
    "report_status",
    "practice_status",
    "report_primary_injury",
    "report_secondary_injury",
    "practice_primary_injury",
    "practice_secondary_injury",
    "report_modified_at",
    "snapshot_ts",
]
DEPTH_CHART_COLUMNS = [
    "snapshot_ts",
    "snapshot_kind",
    "season",
    "week",
    "game_type",
    "team",
    "player_id",
    "player_name",
    "pos_grp",
    "pos_abb",
    "pos_rank",
    "formation",
]
ROSTER_COLUMNS = [
    "season",
    "week",
    "game_type",
    "team",
    "player_id",
    "position",
    "full_name",
    "status",
    "status_description",
    "depth_chart_position",
    "jersey_number",
    "height",
    "years_exp",
    "sleeper_id",
    "espn_id",
    "yahoo_id",
    "pfr_id",
    "snapshot_ts",
]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def parse_fetched_at(manifest: dict[str, Any]) -> datetime:
    """``fetched_at`` of a manifest as an aware UTC datetime (falls back to ``written_at``)."""
    stamp = manifest.get("fetched_at") or manifest.get("written_at")
    if not stamp:
        raise ValueError("manifest has neither fetched_at nor written_at")
    parsed = datetime.fromisoformat(str(stamp).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=UTC)
    return parsed.astimezone(UTC)


def manifest_snapshot_ts(ctx: ProcessContext, dataset: str) -> datetime:
    return parse_fetched_at(ctx.raw.read_manifest(dataset))


def id_as_string(column: str, alias: str | None = None) -> pl.Expr:
    """Cast an external id column to ``String`` without a trailing ``.0`` (D-023)."""
    expr = pl.col(column)
    return (
        pl.when(expr.cast(pl.String).str.ends_with(".0"))
        .then(expr.cast(pl.String).str.strip_suffix(".0"))
        .otherwise(expr.cast(pl.String))
        .alias(alias or column)
    )


def _col_or_null(df: pl.DataFrame, column: str, dtype: pl.DataType) -> pl.Expr:
    if column in df.columns:
        return pl.col(column)
    return pl.lit(None, dtype=dtype).alias(column)


def _utc_datetime(df: pl.DataFrame, column: str) -> pl.Expr:
    """Parse ``column`` (ISO string or datetime) into ``Datetime[us, UTC]``."""
    dtype = df.schema[column]
    if isinstance(dtype, pl.Datetime):
        expr = pl.col(column).cast(pl.Datetime("us", dtype.time_zone))
        if dtype.time_zone is None:
            return expr.dt.replace_time_zone("UTC")
        return expr.dt.convert_time_zone("UTC")
    return (
        pl.col(column)
        .cast(pl.String)
        .str.strip_chars()
        .str.replace(r"(Z|\+00:00)$", "")
        .str.to_datetime("%Y-%m-%dT%H:%M:%S%.f", time_unit="us", strict=False)
        .dt.replace_time_zone("UTC")
    )


def _read_seasons(ctx: ProcessContext, dataset: str) -> list[int]:
    available = [k for k in ctx.raw.partition_keys(dataset) if isinstance(k, int)]
    wanted = [s for s in available if s in set(ctx.seasons)]
    missing = sorted(set(ctx.seasons) - set(available))
    if missing:
        log.info("%s: no raw partition for seasons %s", dataset, missing)
    return wanted


def _read_raw_by_season(ctx: ProcessContext, dataset: str) -> list[tuple[int, pl.DataFrame]]:
    return [
        (season, ctx.read_raw(dataset, seasons=[season])) for season in _read_seasons(ctx, dataset)
    ]


# --------------------------------------------------------------------------- #
# Schedule windows
# --------------------------------------------------------------------------- #


def week_windows(schedules: pl.DataFrame) -> pl.DataFrame:
    """One row per ``(season, week)`` plus a week-0 offseason row per season.

    Columns: ``season`` (Int32), ``week`` (Int32), ``game_type``,
    ``first_kickoff_utc``, ``last_kickoff_utc``, ``window_end_utc``; sorted by
    ``window_end_utc``. ``window_end_utc`` is the inclusive upper bound of the
    week (its last kickoff). For the week-0 row it is the season's first
    kickoff minus 60 days minus one microsecond, so a timestamp strictly
    before that threshold is offseason.
    """
    games = schedules
    if "kickoff_utc" not in games.columns:
        games = with_kickoff_utc(games.select([c for c in SCHEDULE_COLUMNS if c in games.columns]))
    weeks = (
        games.filter(pl.col("kickoff_utc").is_not_null())
        .with_columns(
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            pl.col("kickoff_utc").cast(UTC_US),
        )
        .sort("kickoff_utc")
        .group_by("season", "week", maintain_order=True)
        .agg(
            pl.col("game_type").first(),
            pl.col("kickoff_utc").min().alias("first_kickoff_utc"),
            pl.col("kickoff_utc").max().alias("last_kickoff_utc"),
        )
        .with_columns(pl.col("last_kickoff_utc").alias("window_end_utc"))
    )
    offseason = (
        weeks.group_by("season")
        .agg(pl.col("first_kickoff_utc").min().alias("season_start"))
        .select(
            pl.col("season"),
            pl.lit(0, dtype=pl.Int32).alias("week"),
            pl.lit(OFFSEASON).alias("game_type"),
            pl.lit(None, dtype=UTC_US).alias("first_kickoff_utc"),
            pl.lit(None, dtype=UTC_US).alias("last_kickoff_utc"),
            (pl.col("season_start") - OFFSEASON_LEAD - timedelta(microseconds=1)).alias(
                "window_end_utc"
            ),
        )
    )
    return pl.concat([weeks, offseason.select(weeks.columns)], how="vertical").sort(
        "window_end_utc", "season", "week"
    )


def assign_week(
    frame: pl.DataFrame,
    windows: pl.DataFrame,
    ts_col: str,
    fallback_season_col: str | None = None,
) -> pl.DataFrame:
    """Add ``season``, ``week``, ``game_type`` to ``frame`` from the window rule.

    Rows whose timestamp lies after the last window (schedule not yet
    published that far) get ``fallback_season_col``'s value, week 0 and
    ``game_type = "offseason"``; without a fallback column they get the last
    season in ``windows``. The count of such rows is logged.
    """
    if frame.is_empty():
        return frame.with_columns(
            pl.lit(None, dtype=pl.Int32).alias("season"),
            pl.lit(None, dtype=pl.Int32).alias("week"),
            pl.lit(None, dtype=pl.String).alias("game_type"),
        )
    right = windows.select(
        pl.col("window_end_utc").cast(UTC_US),
        pl.col("season").alias("_w_season"),
        pl.col("week").alias("_w_week"),
        pl.col("game_type").alias("_w_game_type"),
    ).sort("window_end_utc")
    left = (
        frame.drop([c for c in ("season", "week", "game_type") if c in frame.columns])
        .with_columns(pl.col(ts_col).cast(UTC_US))
        .with_row_index("_row")
        .sort(ts_col)
    )
    joined = left.join_asof(right, left_on=ts_col, right_on="window_end_utc", strategy="forward")
    joined = joined.sort("_row").drop("_row")
    if "window_end_utc" in joined.columns and "window_end_utc" not in frame.columns:
        joined = joined.drop("window_end_utc")

    unmatched = int(joined["_w_season"].null_count())
    if unmatched:
        last_season = int(windows["season"].max())
        fallback = (
            pl.col(fallback_season_col).cast(pl.Int32)
            if fallback_season_col and fallback_season_col in joined.columns
            else pl.lit(last_season, dtype=pl.Int32)
        )
        log.warning(
            "%d rows have %s after the last scheduled kickoff; labelled week 0 (%s)",
            unmatched,
            ts_col,
            OFFSEASON,
        )
        joined = joined.with_columns(
            pl.coalesce(pl.col("_w_season"), fallback).alias("_w_season"),
            pl.col("_w_week").fill_null(0),
            pl.col("_w_game_type").fill_null(OFFSEASON),
        )
    return joined.rename({"_w_season": "season", "_w_week": "week", "_w_game_type": "game_type"})


def load_week_windows(ctx: ProcessContext) -> pl.DataFrame:
    """Windows for every season in the LATEST ``schedules`` snapshot (not only ``ctx.seasons``)."""
    schedules = ctx.read_raw("schedules", columns=list(SCHEDULE_COLUMNS))
    return week_windows(schedules)


# --------------------------------------------------------------------------- #
# fact_injury_report
# --------------------------------------------------------------------------- #


def build_injury_report(raw: pl.DataFrame, snapshot_ts: datetime) -> pl.DataFrame:
    """Unify both injuries layouts and deduplicate to one row per player-week-team.

    2016-2024 files carry ``date_modified`` (kept as ``report_modified_at``,
    D-029); 2025+ files have no timestamp and may name the season type
    ``season_type``. Duplicates keep the latest ``report_modified_at`` and then
    the most severe status (Out > Doubtful > Questionable > other).
    """
    df = raw
    game_type = _col_or_null(df, "game_type", pl.String)
    if "season_type" in df.columns:
        game_type = pl.coalesce(game_type, pl.col("season_type")).alias("game_type")
    modified = _col_or_null(df, "date_modified", UTC_US)
    if "date_modified" in df.columns:
        modified = _utc_datetime(df, "date_modified")

    out = df.select(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        game_type.alias("game_type"),
        pl.col("team").cast(pl.String),
        pl.col("gsis_id").cast(pl.String).str.strip_chars().alias("player_id"),
        _col_or_null(df, "position", pl.String).cast(pl.String),
        _col_or_null(df, "full_name", pl.String).cast(pl.String),
        _col_or_null(df, "report_status", pl.String).cast(pl.String),
        _col_or_null(df, "practice_status", pl.String).cast(pl.String),
        _col_or_null(df, "report_primary_injury", pl.String).cast(pl.String),
        _col_or_null(df, "report_secondary_injury", pl.String).cast(pl.String),
        _col_or_null(df, "practice_primary_injury", pl.String).cast(pl.String),
        _col_or_null(df, "practice_secondary_injury", pl.String).cast(pl.String),
        modified.alias("report_modified_at"),
        pl.lit(snapshot_ts.astimezone(UTC)).cast(UTC_US).alias("snapshot_ts"),
    ).with_columns(canonical_team("team"))

    null_ids = out.filter(pl.col("player_id").is_null() | (pl.col("player_id") == "")).height
    if null_ids:
        log.warning("fact_injury_report: dropping %d rows without gsis_id", null_ids)
        out = out.filter(pl.col("player_id").is_not_null() & (pl.col("player_id") != ""))

    out = out.with_columns(
        pl.col("report_status")
        .replace_strict(STATUS_SEVERITY, default=9, return_dtype=pl.Int8)
        .fill_null(9)
        .alias("_severity")
    )
    out = out.sort(
        [*INJURY_KEYS, "report_modified_at", "_severity"],
        descending=[False] * len(INJURY_KEYS) + [True, False],
        nulls_last=True,
    )
    before = out.height
    out = out.unique(subset=INJURY_KEYS, keep="first", maintain_order=True).drop("_severity")
    if before - out.height:
        log.info(
            "fact_injury_report: removed %d duplicate rows on %s", before - out.height, INJURY_KEYS
        )
    out = out.select(INJURY_COLUMNS)
    assert_unique(out, INJURY_KEYS, "fact_injury_report")
    return out


# --------------------------------------------------------------------------- #
# fact_depth_chart
# --------------------------------------------------------------------------- #


def is_timestamped_depth_chart(df: pl.DataFrame) -> bool:
    return "dt" in df.columns


def legacy_depth_chart(df: pl.DataFrame, windows: pl.DataFrame) -> pl.DataFrame:
    """2016-2024 weekly rows -> unified columns with a derived ``snapshot_ts``."""
    team_col = "club_code" if "club_code" in df.columns else "team"
    name_col = "full_name" if "full_name" in df.columns else "player_name"
    out = df.select(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col(team_col).cast(pl.String).alias("team"),
        pl.col("gsis_id").cast(pl.String).str.strip_chars().alias("player_id"),
        _col_or_null(df, name_col, pl.String).cast(pl.String).alias("player_name"),
        _col_or_null(df, "formation", pl.String).cast(pl.String).alias("pos_grp"),
        pl.col("depth_position").cast(pl.String).alias("pos_abb"),
        pl.col("depth_team").cast(pl.String).cast(pl.Int32, strict=False).alias("pos_rank"),
        _col_or_null(df, "formation", pl.String).cast(pl.String).alias("formation"),
    )
    first = windows.filter(pl.col("week") > 0).select(
        "season", "week", "game_type", "first_kickoff_utc"
    )
    out = out.join(first, on=["season", "week"], how="left")
    unmatched = out.filter(pl.col("first_kickoff_utc").is_null())
    if unmatched.height:
        log.warning(
            "fact_depth_chart: dropping %d legacy rows whose (season, week) has no scheduled game "
            "(null-week SBBYE rows or weeks missing from the schedule)",
            unmatched.height,
        )
        out = out.filter(pl.col("first_kickoff_utc").is_not_null())
    return out.with_columns(
        (pl.col("first_kickoff_utc") - LEGACY_SNAPSHOT_LEAD).alias("snapshot_ts"),
        pl.lit("weekly").alias("snapshot_kind"),
    ).select(DEPTH_CHART_COLUMNS)


def timestamped_depth_chart(
    df: pl.DataFrame, windows: pl.DataFrame, partition_season: int | None = None
) -> pl.DataFrame:
    """2025+ ``dt`` rows -> unified columns with ``(season, week)`` from the schedule."""
    out = df.select(
        _utc_datetime(df, "dt").alias("snapshot_ts"),
        pl.lit("timestamped").alias("snapshot_kind"),
        pl.col("team").cast(pl.String),
        pl.col("gsis_id").cast(pl.String).str.strip_chars().alias("player_id"),
        _col_or_null(df, "player_name", pl.String).cast(pl.String).alias("player_name"),
        _col_or_null(df, "pos_grp", pl.String).cast(pl.String),
        pl.col("pos_abb").cast(pl.String),
        pl.col("pos_rank").cast(pl.Int32, strict=False),
        _col_or_null(df, "pos_slot", pl.String).cast(pl.String).alias("formation"),
        pl.lit(partition_season, dtype=pl.Int32).alias("_partition_season"),
    )
    null_ts = out.filter(pl.col("snapshot_ts").is_null()).height
    if null_ts:
        log.warning("fact_depth_chart: dropping %d timestamped rows with unparseable dt", null_ts)
        out = out.filter(pl.col("snapshot_ts").is_not_null())
    out = assign_week(out, windows, "snapshot_ts", fallback_season_col="_partition_season")
    return out.select(DEPTH_CHART_COLUMNS)


def finalize_depth_chart(parts: list[pl.DataFrame]) -> pl.DataFrame:
    """Concatenate unified parts, canonicalise teams, drop id-less rows, deduplicate."""
    if not parts:
        return pl.DataFrame(schema={c: pl.Null for c in DEPTH_CHART_COLUMNS})
    out = pl.concat(parts, how="vertical_relaxed").with_columns(
        canonical_team("team"),
        *[
            pl.when(pl.col(c).str.strip_chars() == "")
            .then(pl.lit(None, dtype=pl.String))
            .otherwise(pl.col(c))
            .alias(c)
            for c in ("pos_abb", "pos_grp", "formation")
        ],
    )
    null_ids = out.filter(pl.col("player_id").is_null() | (pl.col("player_id") == "")).height
    if null_ids:
        log.warning(
            "fact_depth_chart: dropping %d rows without gsis_id (unsigned rookies; unusable for joins)",
            null_ids,
        )
        out = out.filter(pl.col("player_id").is_not_null() & (pl.col("player_id") != ""))
    out = out.sort([*DEPTH_CHART_KEYS, "pos_rank"], nulls_last=True)
    before = out.height
    out = out.unique(subset=DEPTH_CHART_KEYS, keep="first", maintain_order=True)
    removed = before - out.height
    log.info("fact_depth_chart: removed %d duplicate rows on %s", removed, DEPTH_CHART_KEYS)
    out = out.select(DEPTH_CHART_COLUMNS).sort(
        ["season", "week", "snapshot_ts", "team", "pos_abb", "pos_rank"]
    )
    assert_unique(out, DEPTH_CHART_KEYS, "fact_depth_chart")
    return out


def build_depth_chart(
    partitions: list[tuple[int, pl.DataFrame]], windows: pl.DataFrame
) -> pl.DataFrame:
    """Unify raw depth-chart partitions (``(partition season, frame)`` pairs)."""
    parts = []
    for season, df in partitions:
        if df.is_empty():
            continue
        if is_timestamped_depth_chart(df):
            parts.append(timestamped_depth_chart(df, windows, partition_season=season))
        else:
            parts.append(legacy_depth_chart(df, windows))
    return finalize_depth_chart(parts)


# --------------------------------------------------------------------------- #
# fact_roster_week
# --------------------------------------------------------------------------- #


def build_roster_week(raw: pl.DataFrame, snapshot_ts: datetime) -> pl.DataFrame:
    """Weekly rosters with string ids and a consistent ``height`` (inches, Int32)."""
    df = raw
    height = _col_or_null(df, "height", pl.Float64)
    if "height" in df.columns:
        height = pl.col("height").cast(pl.Float64).round(0).cast(pl.Int32)
    desc_col = (
        "status_description_abbr"
        if "status_description_abbr" in df.columns
        else "status_description"
    )
    out = df.select(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        _col_or_null(df, "game_type", pl.String).cast(pl.String),
        pl.col("team").cast(pl.String),
        pl.col("gsis_id").cast(pl.String).str.strip_chars().alias("player_id"),
        _col_or_null(df, "position", pl.String).cast(pl.String),
        _col_or_null(df, "full_name", pl.String).cast(pl.String),
        _col_or_null(df, "status", pl.String).cast(pl.String),
        _col_or_null(df, desc_col, pl.String).cast(pl.String).alias("status_description"),
        _col_or_null(df, "depth_chart_position", pl.String).cast(pl.String),
        _col_or_null(df, "jersey_number", pl.Int32).cast(pl.Int32, strict=False),
        height.cast(pl.Int32).alias("height"),
        _col_or_null(df, "years_exp", pl.Int32).cast(pl.Int32, strict=False),
        *[
            id_as_string(c) if c in df.columns else pl.lit(None, dtype=pl.String).alias(c)
            for c in ("sleeper_id", "espn_id", "yahoo_id", "pfr_id")
        ],
        pl.lit(snapshot_ts.astimezone(UTC)).cast(UTC_US).alias("snapshot_ts"),
    ).with_columns(canonical_team("team"))

    null_ids = out.filter(pl.col("player_id").is_null() | (pl.col("player_id") == "")).height
    if null_ids:
        log.warning("fact_roster_week: dropping %d rows without gsis_id", null_ids)
        out = out.filter(pl.col("player_id").is_not_null() & (pl.col("player_id") != ""))
    out = out.select(ROSTER_COLUMNS).sort(ROSTER_KEYS)
    assert_unique(out, ROSTER_KEYS, "fact_roster_week")
    return out


# --------------------------------------------------------------------------- #
# Entry point
# --------------------------------------------------------------------------- #


def build_context(ctx: ProcessContext) -> dict[str, dict[PartitionKey, pl.DataFrame]]:
    """Build ``fact_injury_report``, ``fact_depth_chart`` and ``fact_roster_week``."""
    windows = load_week_windows(ctx)
    result: dict[str, dict[PartitionKey, pl.DataFrame]] = {}

    injury_parts = [df for _, df in _read_raw_by_season(ctx, "injuries")]
    if injury_parts:
        injuries = build_injury_report(
            pl.concat(injury_parts, how="diagonal_relaxed"), manifest_snapshot_ts(ctx, "injuries")
        )
        result["fact_injury_report"] = split_by_season(injuries)
        log.info("fact_injury_report: %d rows", injuries.height)

    depth_parts = _read_raw_by_season(ctx, "depth_charts")
    if depth_parts:
        depth = build_depth_chart(depth_parts, windows)
        result["fact_depth_chart"] = split_by_season(depth)
        log.info(
            "fact_depth_chart: %d rows (%s)",
            depth.height,
            ", ".join(
                f"{kind}={n}"
                for kind, n in depth.group_by("snapshot_kind")
                .len()
                .sort("snapshot_kind")
                .iter_rows()
            ),
        )

    roster_parts = [df for _, df in _read_raw_by_season(ctx, "rosters_weekly")]
    if roster_parts:
        rosters = build_roster_week(
            pl.concat(roster_parts, how="diagonal_relaxed"),
            manifest_snapshot_ts(ctx, "rosters_weekly"),
        )
        result["fact_roster_week"] = split_by_season(rosters)
        log.info("fact_roster_week: %d rows", rosters.height)

    return result


__all__ = [
    "DEPTH_CHART_COLUMNS",
    "DEPTH_CHART_KEYS",
    "INJURY_COLUMNS",
    "INJURY_KEYS",
    "OFFSEASON",
    "ROSTER_COLUMNS",
    "ROSTER_KEYS",
    "assign_week",
    "build_context",
    "build_depth_chart",
    "build_injury_report",
    "build_roster_week",
    "id_as_string",
    "legacy_depth_chart",
    "load_week_windows",
    "manifest_snapshot_ts",
    "parse_fetched_at",
    "timestamped_depth_chart",
    "week_windows",
]
