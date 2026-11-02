"""Data validation suite: ``ffml validate`` (DATA_SCHEMA.md section 6; DE-2.5, QA-2.1).

Twelve checks run against the ``LATEST`` snapshots of the raw lake and the
processed tables. Each returns a :class:`CheckResult` with a status of
``PASS``, ``WARN``, ``FAIL`` or ``SKIP``; a check that raises is reported as
``FAIL`` with the traceback tail instead of aborting the run. The run writes a
markdown report and :func:`run_validation` returns ``True`` only when nothing
failed, which is what the CLI turns into its exit code.

Raw tier
    1. ``schema_pins``: every ingested dataset's LATEST manifest schema against
       ``ffml/data/schemas/expected_schemas.json`` (reuses the ingest comparison,
       so removed or retyped columns fail and added columns warn).
    2. ``duplicates``: uniqueness of the registry ``key_columns`` per dataset.
    3. ``game_completeness``: every final game in ``schedules`` has at least
       100 play-by-play rows and player_stats rows for both teams; gaps older
       than three days fail, fresher gaps warn (games may still be loading).
    4. ``weekly_volume``: for the latest season, player_stats and pbp rows per
       final game of each completed week against the median of the previous
       completed weeks (25 percent tolerance; the most recent completed week
       only warns). Rows are normalised per game so bye weeks do not alarm.
    5. ``null_rates``: key-column null shares in player_stats, pbp and schedules.
    6. ``restricted_hygiene``: restricted sources live only under a path that
       says so, their manifests say so, and no non-restricted processed path
       holds ``fact_ecr_weekly`` (DECISIONS D-007).

Processed tier (a check whose table is absent reports ``SKIP``)
    7. ``dim_player``: required columns, unique ``player_id``, unique
       non-null ``sleeper_id``.
    8. ``id_crosswalk``: per season, the share of active players (at least one
       target, carry or pass attempt) whose ``dim_player`` row has a
       ``sleeper_id``; fails below 0.98, warns below 1.0; unmatched names and
       espn/yahoo coverage are reported.
    9. ``fact_keys``: fact_player_game and fact_team_game keys unique, two
       fact_team_game rows per game, every final dim_game game present.
    10. ``team_changes``: every played (player, season, week, team) in
        fact_player_game has a fact_roster_week row with that team in the same
        week or the next two (trades show up with a lag); fails above 0.5
        percent of rows. Also counts players with more than one team per season.
    11. ``point_in_time``: fact_depth_chart.snapshot_ts never null and never
        later than the raw depth_charts manifest ``fetched_at``;
        fact_injury_report.report_modified_at (where present) likewise.
    12. ``partial_week``: no player_stats rows for games of the projection
        week whose dim_game status is still ``scheduled``.
"""

from __future__ import annotations

import copy
import json
import logging
import statistics
import traceback
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import polars as pl

from ffml.config import get_settings
from ffml.data.ingest import SCHEMA_PIN_PATH, SchemaDriftError, check_schema, load_pins
from ffml.data.nflverse import DATASETS, RESTRICTED_PREFIX
from ffml.data.process.base import RESTRICTED_TABLES, SKILL_POSITIONS, TEAM_ALIASES
from ffml.data.store import ALL_KEY, MANIFEST_NAME, SnapshotStore

log = logging.getLogger(__name__)

PASS = "PASS"
WARN = "WARN"
FAIL = "FAIL"
SKIP = "SKIP"
STATUSES = (PASS, WARN, FAIL, SKIP)
RAW = "raw"
PROCESSED = "processed"
TIERS = (RAW, PROCESSED)

MIN_PLAYS_PER_GAME = 100
STALE_GAME_DAYS = 3
VOLUME_TOLERANCE = 0.25
PBP_NULL_SHARE_MAX = 0.01
CROSSWALK_FAIL_BELOW = 0.98
CROSSWALK_WARN_BELOW = 1.0
TEAM_CHANGE_FAIL_SHARE = 0.005
TEAM_CHANGE_LAG_WEEKS = 2
EXAMPLE_LIMIT = 10
UNMATCHED_LIMIT = 50

PLAYER_STATS_KEYS_NO_NULLS = ("player_id", "team", "opponent_team", "week")
DIM_PLAYER_REQUIRED = (
    "player_id",
    "full_name",
    "position",
    "sleeper_id",
    "espn_id",
    "yahoo_id",
    "crosswalk_source",
    "latest_team",
)
FACT_PLAYER_GAME_KEY = ("player_id", "season", "week")
FACT_TEAM_GAME_KEY = ("team", "season", "week")
# play-by-play rows that legitimately carry no possession team
_PBP_ADMIN_DESC = r"(?i)timeout|two-minute warning|^end |^game$"


# --------------------------------------------------------------------------- #
# Results and context
# --------------------------------------------------------------------------- #


@dataclass
class CheckResult:
    """Outcome of one check."""

    name: str
    tier: str
    status: str
    message: str
    metrics: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        if self.status not in STATUSES:
            raise ValueError(f"status must be one of {STATUSES}, got {self.status!r}")
        if self.tier not in TIERS:
            raise ValueError(f"tier must be one of {TIERS}, got {self.tier!r}")

    @property
    def failed(self) -> bool:
        return self.status == FAIL


@dataclass
class SuiteResult:
    """Everything one validation run produced."""

    results: list[CheckResult]
    report_path: Path
    seasons: list[int]
    tiers: tuple[str, ...]
    generated_at: datetime

    @property
    def ok(self) -> bool:
        return not any(r.failed for r in self.results)

    @property
    def counts(self) -> dict[str, int]:
        return {status: sum(r.status == status for r in self.results) for status in STATUSES}

    @property
    def summary_line(self) -> str:
        c = self.counts
        return f"{c[PASS]} passed, {c[WARN]} warned, {c[FAIL]} failed, {c[SKIP]} skipped"

    def by_name(self) -> dict[str, CheckResult]:
        return {r.name: r for r in self.results}


@dataclass
class ValidationContext:
    """Stores, seasons, pins and the clock shared by the checks (frames are cached)."""

    seasons: list[int]
    raw: SnapshotStore
    processed: SnapshotStore
    pin_path: Path
    now: datetime
    _cache: dict[tuple[Any, ...], pl.DataFrame | None] = field(default_factory=dict, repr=False)

    @property
    def processed_restricted(self) -> SnapshotStore:
        return SnapshotStore(self.processed.root / RESTRICTED_PREFIX)

    # -- raw ----------------------------------------------------------------

    def raw_exists(self, dataset: str) -> bool:
        return self.raw.latest_id(dataset) is not None

    def raw_manifest(self, dataset: str) -> dict[str, Any] | None:
        if not self.raw_exists(dataset):
            return None
        return self.raw.read_manifest(dataset)

    def raw_frame(self, dataset: str, columns: Sequence[str] | None = None) -> pl.DataFrame | None:
        """LATEST snapshot of a raw dataset restricted to the requested seasons.

        Non-seasonal datasets (partition ``all``) are read whole. Returns
        ``None`` when the dataset or the requested partitions are absent.
        """
        key = (RAW, dataset, tuple(columns) if columns else None)
        if key not in self._cache:
            self._cache[key] = self._read(self.raw, dataset, columns)
        return self._cache[key]

    # -- processed ----------------------------------------------------------

    def processed_store(self, table: str) -> SnapshotStore:
        return self.processed_restricted if table in RESTRICTED_TABLES else self.processed

    def processed_exists(self, table: str) -> bool:
        return self.processed_store(table).latest_id(table) is not None

    def processed_frame(
        self, table: str, columns: Sequence[str] | None = None
    ) -> pl.DataFrame | None:
        """LATEST snapshot of a processed table (requested seasons plus ``all`` partitions)."""
        key = (PROCESSED, table, tuple(columns) if columns else None)
        if key not in self._cache:
            frame = self._read(self.processed_store(table), table, columns)
            self._cache[key] = None if frame is None else self.season_filter(frame)
        return self._cache[key]

    # -- helpers ------------------------------------------------------------

    def season_filter(self, df: pl.DataFrame) -> pl.DataFrame:
        if "season" not in df.columns:
            return df
        return df.filter(pl.col("season").cast(pl.Int32).is_in(self.seasons))

    def _read(
        self, store: SnapshotStore, dataset: str, columns: Sequence[str] | None
    ) -> pl.DataFrame | None:
        sid = store.latest_id(dataset)
        if sid is None:
            return None
        try:
            keys = store.partition_keys(dataset, sid)
        except FileNotFoundError:
            return None
        wanted = [k for k in keys if k == ALL_KEY or k in self.seasons]
        if not wanted:
            return None
        use: list[str] | None = None
        if columns:
            available = set(store.read_manifest(dataset, sid).get("columns", {}))
            use = [c for c in columns if c in available] or None
        return store.read(dataset, seasons=wanted, snapshot_id=sid, columns=use)


CheckFn = Callable[[ValidationContext], CheckResult]


# --------------------------------------------------------------------------- #
# Small helpers
# --------------------------------------------------------------------------- #


def _canon(column: str) -> pl.Expr:
    return pl.col(column).cast(pl.String).replace(TEAM_ALIASES).alias(column)


def _int_keys(df: pl.DataFrame, columns: Iterable[str] = ("season", "week")) -> pl.DataFrame:
    casts = [pl.col(c).cast(pl.Int32) for c in columns if c in df.columns]
    return df.with_columns(casts) if casts else df


def _str_keys(df: pl.DataFrame, columns: Iterable[str]) -> pl.DataFrame:
    casts = [pl.col(c).cast(pl.String) for c in columns if c in df.columns]
    return df.with_columns(casts) if casts else df


def _examples(df: pl.DataFrame, limit: int = EXAMPLE_LIMIT) -> list[dict[str, Any]]:
    return [
        {k: (v.isoformat() if isinstance(v, datetime) else v) for k, v in row.items()}
        for row in df.head(limit).rows(named=True)
    ]


def _duplicate_rows(df: pl.DataFrame, keys: Sequence[str]) -> tuple[int, list[dict[str, Any]]]:
    """``(extra rows, examples)`` for duplicated ``keys``."""
    subset = df.select(keys)
    extra = subset.height - subset.n_unique()
    if extra == 0:
        return 0, []
    mask = subset.is_duplicated()
    return extra, _examples(subset.filter(mask).unique(maintain_order=True), 3)


def _parse_iso(text: str | None) -> datetime | None:
    if not text:
        return None
    value = datetime.fromisoformat(text.replace("Z", "+00:00"))
    return value if value.tzinfo is not None else value.replace(tzinfo=UTC)


def _to_utc(df: pl.DataFrame, column: str) -> pl.DataFrame:
    """Return ``df`` with ``column`` as ``Datetime[us, UTC]`` (strings, naive or aware)."""
    dtype = df.schema[column]
    expr = pl.col(column)
    if dtype == pl.String:
        expr = (
            expr.str.strip_chars()
            .str.replace(r"(Z|\+00:00)$", "")
            .str.to_datetime(time_unit="us", strict=False)
            .dt.replace_time_zone("UTC")
        )
    elif isinstance(dtype, pl.Datetime):
        expr = (
            expr.dt.convert_time_zone("UTC")
            if dtype.time_zone is not None
            else expr.dt.replace_time_zone("UTC")
        )
    elif dtype == pl.Date:
        expr = expr.cast(pl.Datetime("us")).dt.replace_time_zone("UTC")
    else:
        raise TypeError(f"{column}: cannot interpret {dtype} as a timestamp")
    return df.with_columns(expr.alias(column))


def _gameday_as_date(df: pl.DataFrame) -> pl.DataFrame:
    dtype = df.schema["gameday"]
    if dtype == pl.String:
        return df.with_columns(pl.col("gameday").str.strip_chars().str.to_date("%Y-%m-%d"))
    if isinstance(dtype, pl.Datetime):
        return df.with_columns(pl.col("gameday").dt.date())
    return df.with_columns(pl.col("gameday").cast(pl.Date))


def _final_games(ctx: ValidationContext) -> pl.DataFrame | None:
    schedules = ctx.raw_frame(
        "schedules",
        ["game_id", "season", "game_type", "week", "gameday", "home_team", "away_team", "result"],
    )
    if schedules is None:
        return None
    schedules = _int_keys(ctx.season_filter(schedules))
    return schedules.with_columns(_canon("home_team"), _canon("away_team"))


def _season_label(seasons: Sequence[int]) -> str:
    if not seasons:
        return "-"
    if len(seasons) == 1:
        return str(seasons[0])
    contiguous = seasons[-1] - seasons[0] + 1 == len(seasons)
    return f"{seasons[0]}-{seasons[-1]}" if contiguous else ",".join(map(str, seasons))


# --------------------------------------------------------------------------- #
# Raw tier
# --------------------------------------------------------------------------- #


def check_schema_pins(ctx: ValidationContext) -> CheckResult:
    """1. LATEST manifest schema of every ingested dataset against the pins."""
    name = "schema_pins"
    pins = copy.deepcopy(load_pins(ctx.pin_path))
    datasets = ctx.raw.list_datasets()
    if not datasets:
        return CheckResult(name, RAW, SKIP, f"no datasets under {ctx.raw.root}")
    if not pins:
        return CheckResult(
            name, RAW, FAIL, f"no schema pins at {ctx.pin_path}; run `ffml ingest` to pin"
        )

    matched: list[str] = []
    added: dict[str, list[str]] = {}
    reordered: list[str] = []
    unpinned: list[str] = []
    unmanaged: list[str] = []
    drifted: dict[str, str] = {}
    seen: set[str] = set()
    for dataset in datasets:
        manifest = ctx.raw.read_manifest(dataset)
        if not manifest.get("schema_hash") or not isinstance(manifest.get("columns"), dict):
            # not written by the SnapshotStore (the Sleeper players cache keeps its
            # schema in code and its own manifest format); nothing to pin against
            unmanaged.append(dataset)
            continue
        pin_name = (manifest.get("extra") or {}).get("dataset_name") or dataset.rsplit("/", 1)[-1]
        seen.add(pin_name)
        if pin_name not in pins:
            unpinned.append(pin_name)
            continue
        partition = str(manifest.get("schema_partition", ""))
        season = int(partition) if partition.isdigit() else None
        try:
            report = check_schema(pin_name, dict(manifest["columns"]), pins, season=season)
        except SchemaDriftError as exc:
            drifted[pin_name] = str(exc)
            continue
        if report.added:
            added[pin_name] = list(report.added)
        elif report.status == "reordered":
            reordered.append(pin_name)
        else:
            matched.append(pin_name)
    not_ingested = sorted(set(pins) - seen)

    metrics: dict[str, Any] = {
        "datasets": len(datasets),
        "matched": sorted(matched),
        "added_columns": added,
        "reordered": reordered,
        "unpinned": unpinned,
        "unmanaged": unmanaged,
        "not_ingested": not_ingested,
        "drift": drifted,
        "pin_path": str(ctx.pin_path),
    }
    pinned_total = len(datasets) - len(unmanaged)
    info = f" (schema kept in code, not pinned: {unmanaged})" if unmanaged else ""
    if drifted:
        detail = "; ".join(f"{k}: {v}" for k, v in drifted.items())
        return CheckResult(
            name, RAW, FAIL, f"schema drift in {len(drifted)} dataset(s): {detail}", metrics
        )
    notes = []
    if added:
        notes.append("added columns in " + ", ".join(f"{k} {v}" for k, v in added.items()))
    if reordered:
        notes.append(f"columns reordered in {reordered}")
    if unpinned:
        notes.append(f"no pin for {unpinned}")
    if not_ingested:
        notes.append(f"pinned but not ingested: {not_ingested}")
    if notes:
        return CheckResult(
            name,
            RAW,
            WARN,
            f"{len(matched)} of {pinned_total} match; " + "; ".join(notes) + info,
            metrics,
        )
    return CheckResult(
        name,
        RAW,
        PASS,
        f"all {pinned_total} pinned datasets match their schema pins{info}",
        metrics,
    )


def check_duplicates(ctx: ValidationContext) -> CheckResult:
    """2. Registry ``key_columns`` are unique within the LATEST snapshot."""
    name = "duplicates"
    per_dataset: dict[str, Any] = {}
    skipped: list[str] = []
    failures: list[str] = []
    for spec in DATASETS.values():
        if not spec.key_columns:
            continue
        df = ctx.raw_frame(spec.store_path, spec.key_columns)
        if df is None:
            skipped.append(spec.name)
            continue
        missing = [c for c in spec.key_columns if c not in df.columns]
        if missing:
            per_dataset[spec.name] = {"rows": df.height, "missing_key_columns": missing}
            failures.append(f"{spec.name}: key columns {missing} missing")
            continue
        extra, examples = _duplicate_rows(df, spec.key_columns)
        per_dataset[spec.name] = {
            "rows": df.height,
            "keys": list(spec.key_columns),
            "duplicate_rows": extra,
            "examples": examples,
        }
        if extra:
            failures.append(
                f"{spec.name}: {extra} duplicate row(s) on {spec.key_columns}, e.g. {examples}"
            )
    metrics = {"datasets": per_dataset, "not_ingested": skipped}
    if not per_dataset:
        return CheckResult(name, RAW, SKIP, "no keyed dataset is ingested", metrics)
    if failures:
        return CheckResult(name, RAW, FAIL, "; ".join(failures), metrics)
    checked = ", ".join(per_dataset)
    return CheckResult(name, RAW, PASS, f"no duplicate keys in {checked}", metrics)


def check_game_completeness(ctx: ValidationContext) -> CheckResult:
    """3. Every final game has >= 100 pbp rows and player_stats rows for both teams."""
    name = "game_completeness"
    schedules = _final_games(ctx)
    if schedules is None:
        return CheckResult(name, RAW, SKIP, "schedules not ingested for the requested seasons")
    finals = _gameday_as_date(schedules.filter(pl.col("result").is_not_null()))
    if finals.is_empty():
        return CheckResult(name, RAW, SKIP, "no final games in the requested seasons")

    pbp = ctx.raw_frame("pbp", ["game_id", "season", "week"])
    plays = (
        pbp.group_by("game_id").len().rename({"len": "plays"})
        if pbp is not None
        else pl.DataFrame(schema={"game_id": pl.String, "plays": pl.UInt32})
    )
    stats = ctx.raw_frame("player_stats", ["season", "week", "team"])
    if stats is not None:
        team_rows = (
            _int_keys(stats)
            .with_columns(_canon("team"))
            .group_by(["season", "week", "team"])
            .len()
            .rename({"len": "rows"})
        )
    else:
        team_rows = pl.DataFrame(
            schema={"season": pl.Int32, "week": pl.Int32, "team": pl.String, "rows": pl.UInt32}
        )

    joined = (
        finals.join(plays, on="game_id", how="left")
        .join(
            team_rows.rename({"team": "home_team", "rows": "home_rows"}),
            on=["season", "week", "home_team"],
            how="left",
        )
        .join(
            team_rows.rename({"team": "away_team", "rows": "away_rows"}),
            on=["season", "week", "away_team"],
            how="left",
        )
        .with_columns(
            pl.col("plays").fill_null(0).cast(pl.Int64),
            pl.col("home_rows").fill_null(0).cast(pl.Int64),
            pl.col("away_rows").fill_null(0).cast(pl.Int64),
        )
    )
    stale_before = ctx.now.date() - timedelta(days=STALE_GAME_DAYS)
    gaps = joined.filter(
        (pl.col("plays") < MIN_PLAYS_PER_GAME)
        | (pl.col("home_rows") == 0)
        | (pl.col("away_rows") == 0)
    ).with_columns((pl.col("gameday") < pl.lit(stale_before)).alias("stale"))
    gaps = gaps.sort(["season", "week", "game_id"])

    def describe(row: dict[str, Any]) -> dict[str, Any]:
        reasons = []
        if row["plays"] < MIN_PLAYS_PER_GAME:
            reasons.append(f"{row['plays']} plays")
        if row["home_rows"] == 0:
            reasons.append(f"no stats for {row['home_team']}")
        if row["away_rows"] == 0:
            reasons.append(f"no stats for {row['away_team']}")
        return {
            "game_id": row["game_id"],
            "gameday": row["gameday"].isoformat(),
            "stale": row["stale"],
            "reasons": reasons,
        }

    gap_list = [describe(r) for r in gaps.head(50).rows(named=True)]
    stale = int(gaps["stale"].sum()) if not gaps.is_empty() else 0
    fresh = gaps.height - stale
    metrics = {
        "final_games": finals.height,
        "games_with_gaps": gaps.height,
        "stale_gaps": stale,
        "fresh_gaps": fresh,
        "stale_before": stale_before.isoformat(),
        "pbp_ingested": pbp is not None,
        "player_stats_ingested": stats is not None,
        "gaps": gap_list,
    }
    if gaps.is_empty():
        return CheckResult(
            name,
            RAW,
            PASS,
            f"all {finals.height} final games have >= {MIN_PLAYS_PER_GAME} plays and stats for both teams",
            metrics,
        )
    shown = "; ".join(f"{g['game_id']} ({', '.join(g['reasons'])})" for g in gap_list[:8])
    more = f" and {gaps.height - 8} more" if gaps.height > 8 else ""
    if stale:
        return CheckResult(
            name,
            RAW,
            FAIL,
            f"{stale} game(s) older than {STALE_GAME_DAYS} days lack data: {shown}{more}",
            metrics,
        )
    return CheckResult(
        name, RAW, WARN, f"{fresh} recent game(s) still loading: {shown}{more}", metrics
    )


def check_weekly_volume(ctx: ValidationContext) -> CheckResult:
    """4. Rows per final game of each completed week within 25% of the trailing median."""
    name = "weekly_volume"
    schedules = _final_games(ctx)
    if schedules is None:
        return CheckResult(name, RAW, SKIP, "schedules not ingested for the requested seasons")
    season = int(schedules["season"].max())
    reg = schedules.filter((pl.col("season") == season) & (pl.col("game_type") == "REG"))
    by_week = (
        reg.group_by("week")
        .agg(pl.len().alias("games"), pl.col("result").is_not_null().sum().alias("finals"))
        .sort("week")
    )
    completed = by_week.filter(pl.col("finals") == pl.col("games"))
    weeks = [int(w) for w in completed["week"].to_list()]
    games = dict(zip(weeks, (int(g) for g in completed["games"].to_list()), strict=True))
    if len(weeks) < 2:
        return CheckResult(
            name,
            RAW,
            SKIP,
            f"season {season} has {len(weeks)} completed week(s); need two for a comparison",
            {"season": season, "completed_weeks": weeks},
        )

    series: dict[str, dict[int, float]] = {}
    for dataset in ("player_stats", "pbp"):
        df = ctx.raw_frame(dataset, ["season", "week"])
        if df is None:
            continue
        counts = (
            _int_keys(df)
            .filter((pl.col("season") == season) & pl.col("week").is_in(weeks))
            .group_by("week")
            .len()
        )
        rows = dict(zip(counts["week"].to_list(), counts["len"].to_list(), strict=True))
        series[dataset] = {w: rows.get(w, 0) / games[w] for w in weeks}
    if not series:
        return CheckResult(name, RAW, SKIP, "neither player_stats nor pbp is ingested")

    violations: list[dict[str, Any]] = []
    for dataset, per_game in series.items():
        for i, week in enumerate(weeks[1:], start=1):
            previous = [per_game[w] for w in weeks[:i]]
            median = statistics.median(previous)
            value = per_game[week]
            deviation = abs(value - median) / median if median else float("inf")
            if deviation > VOLUME_TOLERANCE:
                violations.append(
                    {
                        "dataset": dataset,
                        "week": week,
                        "rows_per_game": round(value, 1),
                        "median_previous": round(median, 1),
                        "deviation": round(deviation, 3),
                        "latest_completed_week": week == weeks[-1],
                    }
                )
    metrics = {
        "season": season,
        "completed_weeks": weeks,
        "games_per_week": games,
        "rows_per_game": {d: {w: round(v, 1) for w, v in s.items()} for d, s in series.items()},
        "violations": violations,
    }
    if not violations:
        return CheckResult(
            name,
            RAW,
            PASS,
            f"season {season}: rows per game stable across completed weeks {weeks[0]}-{weeks[-1]} "
            f"for {', '.join(series)}",
            metrics,
        )
    text = "; ".join(
        f"{v['dataset']} week {v['week']}: {v['rows_per_game']} rows/game vs median {v['median_previous']}"
        for v in violations
    )
    if all(v["latest_completed_week"] for v in violations):
        return CheckResult(
            name, RAW, WARN, f"season {season}: latest completed week deviates: {text}", metrics
        )
    return CheckResult(name, RAW, FAIL, f"season {season}: volume anomaly: {text}", metrics)


def check_null_rates(ctx: ValidationContext) -> CheckResult:
    """5. Null shares on key columns of player_stats, pbp and schedules."""
    name = "null_rates"
    metrics: dict[str, Any] = {}
    failures: list[str] = []
    checked = 0

    stats = ctx.raw_frame("player_stats", list(PLAYER_STATS_KEYS_NO_NULLS))
    if stats is not None:
        checked += 1
        shares = {}
        for column in PLAYER_STATS_KEYS_NO_NULLS:
            if column not in stats.columns:
                failures.append(f"player_stats: column {column} missing")
                continue
            nulls = stats[column].null_count()
            shares[column] = {
                "nulls": nulls,
                "share": nulls / stats.height if stats.height else 0.0,
            }
            if nulls:
                failures.append(f"player_stats.{column}: {nulls} null(s)")
        metrics["player_stats"] = {"rows": stats.height, "columns": shares}

    pbp = ctx.raw_frame("pbp", ["game_id", "posteam", "play_type", "desc", "timeout"])
    if pbp is not None:
        checked += 1
        if "game_id" not in pbp.columns or "posteam" not in pbp.columns:
            failures.append("pbp: game_id or posteam column missing")
        else:
            admin = pl.col("play_type").is_null() | (pl.col("play_type") == "kickoff")
            if "timeout" in pbp.columns:
                admin = admin | (pl.col("timeout").fill_null(0) == 1)
            if "desc" in pbp.columns:
                admin = admin | pl.col("desc").fill_null("").str.contains(_PBP_ADMIN_DESC)
            plays = pbp.filter(~admin)
            game_id_share = pbp["game_id"].null_count() / pbp.height if pbp.height else 0.0
            posteam_nulls = plays["posteam"].null_count()
            posteam_share = posteam_nulls / plays.height if plays.height else 0.0
            metrics["pbp"] = {
                "rows": pbp.height,
                "plays_after_exclusions": plays.height,
                "game_id_null_share": round(game_id_share, 5),
                "posteam_nulls": posteam_nulls,
                "posteam_null_share": round(posteam_share, 5),
            }
            if game_id_share >= PBP_NULL_SHARE_MAX:
                failures.append(f"pbp.game_id null share {game_id_share:.3%}")
            if posteam_share >= PBP_NULL_SHARE_MAX:
                failures.append(f"pbp.posteam null share {posteam_share:.3%} on real plays")

    schedules = ctx.raw_frame("schedules", ["season", "game_id", "gameday"])
    if schedules is not None:
        checked += 1
        schedules = ctx.season_filter(schedules)
        nulls = {
            c: schedules[c].null_count() for c in ("game_id", "gameday") if c in schedules.columns
        }
        metrics["schedules"] = {"rows": schedules.height, "nulls": nulls}
        for column, count in nulls.items():
            if count:
                failures.append(f"schedules.{column}: {count} null(s)")
        for column in ("game_id", "gameday"):
            if column not in schedules.columns:
                failures.append(f"schedules: column {column} missing")

    if checked == 0:
        return CheckResult(name, RAW, SKIP, "player_stats, pbp and schedules are not ingested")
    if failures:
        return CheckResult(name, RAW, FAIL, "; ".join(failures), metrics)
    return CheckResult(name, RAW, PASS, "key columns have no unexpected nulls", metrics)


def check_restricted_hygiene(ctx: ValidationContext) -> CheckResult:
    """6. Restricted sources stay under restricted paths; fact_ecr_weekly never leaks."""
    name = "restricted_hygiene"
    problems: list[str] = []
    notes: list[str] = []
    datasets = ctx.raw.list_datasets()
    restricted_names = {spec.name for spec in DATASETS.values() if spec.restricted}

    for dataset in datasets:
        parts = dataset.split("/")
        manifest = ctx.raw.read_manifest(dataset)
        in_restricted_path = RESTRICTED_PREFIX in parts
        flagged = bool(manifest.get("restricted"))
        if parts[-1] in restricted_names and not in_restricted_path:
            problems.append(f"raw dataset {dataset} is restricted but not under a restricted path")
        if flagged and not in_restricted_path:
            problems.append(f"raw dataset {dataset} has restricted=true outside a restricted path")
        if in_restricted_path and not flagged:
            problems.append(
                f"raw dataset {dataset} lives under restricted/ but its manifest says restricted=false"
            )
    found = {d.split("/")[-1] for d in datasets}
    for missing in sorted(restricted_names - found):
        notes.append(f"{missing} not ingested")

    leaks: list[str] = []
    root = ctx.processed.root
    if root.exists():
        for path in root.rglob("*"):
            rel = path.relative_to(root).parts
            if RESTRICTED_PREFIX in rel:
                continue
            if any(part in RESTRICTED_TABLES for part in rel):
                leaks.append(path.relative_to(root).as_posix())
                continue
            if path.name == MANIFEST_NAME:
                try:
                    manifest = json.loads(path.read_text(encoding="utf-8"))
                except (OSError, ValueError):
                    continue
                if manifest.get("dataset") in RESTRICTED_TABLES or manifest.get("restricted"):
                    leaks.append(path.relative_to(root).as_posix())
    if leaks:
        problems.append(f"restricted tables outside {root / RESTRICTED_PREFIX}: {leaks[:10]}")

    metrics = {
        "raw_datasets": len(datasets),
        "restricted_datasets": sorted(d for d in datasets if RESTRICTED_PREFIX in d.split("/")),
        "processed_root": str(root),
        "processed_root_exists": root.exists(),
        "leaks": leaks,
        "notes": notes,
    }
    if not datasets and not root.exists():
        return CheckResult(name, RAW, SKIP, "no raw datasets and no processed directory", metrics)
    if problems:
        return CheckResult(name, RAW, FAIL, "; ".join(problems), metrics)
    message = "restricted data only under restricted paths; no fact_ecr_weekly outside restricted/"
    if notes:
        message += f" ({'; '.join(notes)})"
    return CheckResult(name, RAW, PASS, message, metrics)


# --------------------------------------------------------------------------- #
# Processed tier
# --------------------------------------------------------------------------- #


def _skip(name: str, table: str) -> CheckResult:
    return CheckResult(name, PROCESSED, SKIP, f"{table} not built yet")


def check_dim_player(ctx: ValidationContext) -> CheckResult:
    """7. dim_player: required columns, unique player_id, unique non-null sleeper_id."""
    name = "dim_player"
    dp = ctx.processed_frame("dim_player")
    if dp is None:
        return _skip(name, "dim_player")
    problems: list[str] = []
    missing = [c for c in DIM_PLAYER_REQUIRED if c not in dp.columns]
    metrics: dict[str, Any] = {"rows": dp.height, "missing_columns": missing}
    if missing:
        problems.append(f"missing columns {missing}")
    if "player_id" in dp.columns:
        null_ids = dp["player_id"].null_count()
        extra, examples = _duplicate_rows(dp, ["player_id"])
        metrics["player_id_nulls"] = null_ids
        metrics["player_id_duplicates"] = extra
        if null_ids:
            problems.append(f"{null_ids} null player_id")
        if extra:
            problems.append(f"{extra} duplicate player_id row(s), e.g. {examples}")
    if "sleeper_id" in dp.columns:
        with_sleeper = dp.filter(
            pl.col("sleeper_id").is_not_null() & (pl.col("sleeper_id").cast(pl.String) != "")
        )
        extra, examples = _duplicate_rows(with_sleeper, ["sleeper_id"])
        metrics["players_with_sleeper_id"] = with_sleeper.height
        metrics["sleeper_id_duplicates"] = extra
        if extra:
            problems.append(f"{extra} duplicate sleeper_id row(s), e.g. {examples}")
    if problems:
        return CheckResult(name, PROCESSED, FAIL, "; ".join(problems), metrics)
    return CheckResult(
        name,
        PROCESSED,
        PASS,
        f"{dp.height} players; player_id unique; {metrics.get('players_with_sleeper_id', 0)} unique sleeper_ids",
        metrics,
    )


def _active_players(ctx: ValidationContext) -> tuple[pl.DataFrame | None, str]:
    """``(season, player_id, name)`` of players with a target, carry or pass attempt."""
    fpg = ctx.processed_frame(
        "fact_player_game",
        ["player_id", "season", "position", "targets", "carries", "pass_att", "attempts"],
    )
    source = "fact_player_game"
    if fpg is None:
        fpg = ctx.raw_frame(
            "player_stats",
            [
                "player_id",
                "season",
                "position",
                "player_display_name",
                "player_name",
                "targets",
                "carries",
                "attempts",
            ],
        )
        source = "player_stats"
        if fpg is None:
            return None, source
    df = _int_keys(fpg, ["season"])
    if "position" in df.columns:
        df = df.filter(pl.col("position").is_in(list(SKILL_POSITIONS)))
    usage = [c for c in ("targets", "carries", "pass_att", "attempts") if c in df.columns]
    if not usage:
        raise ValueError(f"{source} has none of targets, carries, pass_att, attempts")
    name_col = next((c for c in ("player_display_name", "player_name") if c in df.columns), None)
    df = df.with_columns(pl.sum_horizontal([pl.col(c).fill_null(0) for c in usage]).alias("usage"))
    active = df.filter(pl.col("usage") > 0)
    active = active.with_columns(
        (pl.col(name_col) if name_col else pl.lit(None, dtype=pl.String)).alias("source_name")
    )
    return (
        active.group_by(["season", "player_id"]).agg(pl.col("source_name").drop_nulls().first()),
        source,
    )


def check_id_crosswalk(ctx: ValidationContext) -> CheckResult:
    """8. Share of active players per season whose dim_player row has a sleeper_id."""
    name = "id_crosswalk"
    dp = ctx.processed_frame(
        "dim_player", ["player_id", "full_name", "sleeper_id", "espn_id", "yahoo_id"]
    )
    if dp is None:
        return _skip(name, "dim_player")
    active, source = _active_players(ctx)
    if active is None:
        return CheckResult(
            name, PROCESSED, SKIP, "neither fact_player_game nor raw player_stats available"
        )

    def present(column: str) -> pl.Expr:
        if column not in dp.columns:
            return pl.lit(False)
        return pl.col(column).is_not_null() & (pl.col(column).cast(pl.String) != "")

    dp = _str_keys(dp, ["player_id"]).unique(subset=["player_id"], keep="first")
    joined = _str_keys(active, ["player_id"]).join(
        dp.with_columns(
            present("sleeper_id").alias("has_sleeper"),
            present("espn_id").alias("has_espn"),
            present("yahoo_id").alias("has_yahoo"),
            pl.lit(True).alias("in_dim_player"),
        ),
        on="player_id",
        how="left",
    )
    joined = joined.with_columns(
        pl.col("has_sleeper").fill_null(False),
        pl.col("has_espn").fill_null(False),
        pl.col("has_yahoo").fill_null(False),
        pl.col("in_dim_player").fill_null(False),
        pl.coalesce(
            [pl.col(c) for c in ("full_name", "source_name") if c in joined.columns]
            + [pl.col("player_id")]
        ).alias("name"),
    )
    per_season: dict[int, dict[str, Any]] = {}
    unmatched: dict[int, list[str]] = {}
    worst = 1.0
    for (season,), part in sorted(joined.partition_by("season", as_dict=True).items()):
        n = part.height
        matched = int(part["has_sleeper"].sum())
        share = matched / n if n else 1.0
        worst = min(worst, share)
        missing = part.filter(~pl.col("has_sleeper")).sort("name")
        per_season[int(season)] = {
            "active_players": n,
            "sleeper_matched": matched,
            "sleeper_share": round(share, 4),
            "espn_share": round(int(part["has_espn"].sum()) / n, 4) if n else 1.0,
            "yahoo_share": round(int(part["has_yahoo"].sum()) / n, 4) if n else 1.0,
            "not_in_dim_player": int((~part["in_dim_player"]).sum()),
        }
        unmatched[int(season)] = missing["name"].head(UNMATCHED_LIMIT).to_list()
    metrics = {
        "activity_source": source,
        "per_season": per_season,
        "unmatched": {s: names for s, names in unmatched.items() if names},
        "fail_below": CROSSWALK_FAIL_BELOW,
    }
    if not per_season:
        return CheckResult(
            name, PROCESSED, SKIP, "no active players in the requested seasons", metrics
        )
    latest = max(per_season)
    summary = ", ".join(
        f"{s}: {m['sleeper_share']:.1%} ({m['sleeper_matched']}/{m['active_players']})"
        for s, m in per_season.items()
    )
    shown = unmatched.get(latest) or next((v for v in unmatched.values() if v), [])
    tail = f"; unmatched ({latest}): {shown[:15]}" if shown else ""
    if worst < CROSSWALK_FAIL_BELOW:
        return CheckResult(
            name,
            PROCESSED,
            FAIL,
            f"sleeper_id coverage below {CROSSWALK_FAIL_BELOW:.0%}: {summary}{tail}",
            metrics,
        )
    if worst < CROSSWALK_WARN_BELOW:
        return CheckResult(
            name, PROCESSED, WARN, f"sleeper_id coverage below 100%: {summary}{tail}", metrics
        )
    return CheckResult(name, PROCESSED, PASS, f"sleeper_id coverage complete: {summary}", metrics)


def check_fact_keys(ctx: ValidationContext) -> CheckResult:
    """9. Fact keys unique; two team rows per game; every final game has team rows."""
    name = "fact_keys"
    fpg = ctx.processed_frame("fact_player_game", list(FACT_PLAYER_GAME_KEY))
    ftg = ctx.processed_frame("fact_team_game", [*FACT_TEAM_GAME_KEY, "game_id"])
    if fpg is None and ftg is None:
        return _skip(name, "fact_player_game and fact_team_game")
    problems: list[str] = []
    notes: list[str] = []
    metrics: dict[str, Any] = {}

    if fpg is None:
        notes.append("fact_player_game not built yet")
    else:
        missing = [c for c in FACT_PLAYER_GAME_KEY if c not in fpg.columns]
        if missing:
            problems.append(f"fact_player_game lacks key columns {missing}")
        else:
            extra, examples = _duplicate_rows(fpg, list(FACT_PLAYER_GAME_KEY))
            metrics["fact_player_game"] = {
                "rows": fpg.height,
                "duplicate_rows": extra,
                "examples": examples,
            }
            if extra:
                problems.append(
                    f"fact_player_game: {extra} duplicate row(s) on {list(FACT_PLAYER_GAME_KEY)}, e.g. {examples}"
                )

    if ftg is None:
        notes.append("fact_team_game not built yet")
    else:
        missing = [c for c in (*FACT_TEAM_GAME_KEY, "game_id") if c not in ftg.columns]
        if missing:
            problems.append(f"fact_team_game lacks columns {missing}")
        else:
            extra, examples = _duplicate_rows(ftg, list(FACT_TEAM_GAME_KEY))
            per_game = ftg.group_by("game_id").len()
            odd = per_game.filter(pl.col("len") != 2).sort("game_id")
            metrics["fact_team_game"] = {
                "rows": ftg.height,
                "games": per_game.height,
                "duplicate_rows": extra,
                "games_without_two_rows": odd.height,
                "examples": examples or _examples(odd),
            }
            if extra:
                problems.append(
                    f"fact_team_game: {extra} duplicate row(s) on {list(FACT_TEAM_GAME_KEY)}, e.g. {examples}"
                )
            if odd.height:
                problems.append(
                    f"fact_team_game: {odd.height} game(s) without exactly two rows, e.g. {_examples(odd, 5)}"
                )
            dg = ctx.processed_frame("dim_game", ["game_id", "season", "week", "status"])
            if dg is None:
                notes.append("dim_game not built yet; completed-game coverage not checked")
            elif "status" not in dg.columns:
                problems.append("dim_game lacks a status column")
            else:
                final = dg.filter(pl.col("status") == "final")
                absent = final.join(per_game, on="game_id", how="anti").sort(
                    ["season", "week", "game_id"]
                )
                metrics["dim_game"] = {
                    "final_games": final.height,
                    "without_team_rows": absent.height,
                    "examples": _examples(absent),
                }
                if absent.height:
                    problems.append(
                        f"{absent.height} final game(s) in dim_game lack fact_team_game rows, e.g. {_examples(absent, 5)}"
                    )

    if problems:
        return CheckResult(name, PROCESSED, FAIL, "; ".join(problems + notes), metrics)
    message = "fact keys unique; every game has two team rows; final games covered"
    if notes:
        message += f" ({'; '.join(notes)})"
    return CheckResult(name, PROCESSED, PASS, message, metrics)


def check_team_changes(ctx: ValidationContext) -> CheckResult:
    """10. Played player-weeks agree with fact_roster_week within a two-week lag."""
    name = "team_changes"
    fpg = ctx.processed_frame("fact_player_game", ["player_id", "season", "week", "team", "played"])
    frw = ctx.processed_frame("fact_roster_week", ["player_id", "season", "week", "team"])
    if fpg is None or frw is None:
        absent = [t for t, f in (("fact_player_game", fpg), ("fact_roster_week", frw)) if f is None]
        return _skip(name, " and ".join(absent))
    keys = ["player_id", "season", "week", "team"]
    missing = [c for c in keys if c not in fpg.columns] + [
        f"roster.{c}" for c in keys if c not in frw.columns
    ]
    if missing:
        return CheckResult(name, PROCESSED, FAIL, f"missing columns {missing}")

    games = _str_keys(_int_keys(fpg), ["player_id", "team"])
    played = games.filter(pl.col("played").fill_null(True)) if "played" in games.columns else games
    rows = played.select(keys).unique()
    roster = _str_keys(_int_keys(frw), ["player_id", "team"]).select(keys).unique()
    lagged = pl.concat(
        [
            roster.with_columns((pl.col("week") - k).alias("week"))
            for k in range(TEAM_CHANGE_LAG_WEEKS + 1)
        ]
    ).unique()
    violations = rows.join(lagged, on=keys, how="anti")
    same_week = roster.group_by(["player_id", "season", "week"]).agg(
        pl.col("team").alias("roster_teams")
    )
    violations = violations.join(same_week, on=["player_id", "season", "week"], how="left").sort(
        ["season", "week", "player_id"]
    )
    share = violations.height / rows.height if rows.height else 0.0

    multi = (
        played.group_by(["season", "player_id"])
        .agg(pl.col("team").n_unique().alias("teams"))
        .filter(pl.col("teams") > 1)
        .group_by("season")
        .len()
        .sort("season")
    )
    multi_team = {
        int(s): int(n)
        for s, n in zip(multi["season"].to_list(), multi["len"].to_list(), strict=True)
    }
    for season in ctx.seasons:
        if season in set(played["season"].to_list()):
            multi_team.setdefault(season, 0)

    metrics = {
        "player_weeks": rows.height,
        "violations": violations.height,
        "violation_share": round(share, 5),
        "fail_above": TEAM_CHANGE_FAIL_SHARE,
        "lag_weeks": TEAM_CHANGE_LAG_WEEKS,
        "multi_team_players": dict(sorted(multi_team.items())),
        "examples": _examples(violations),
    }
    multi_text = ", ".join(f"{s}: {n}" for s, n in sorted(multi_team.items()))
    if share > TEAM_CHANGE_FAIL_SHARE:
        return CheckResult(
            name,
            PROCESSED,
            FAIL,
            f"{violations.height} of {rows.height} played player-weeks ({share:.2%}) have no roster row with that "
            f"team within {TEAM_CHANGE_LAG_WEEKS} weeks, e.g. {_examples(violations, 3)}; multi-team players {multi_text}",
            metrics,
        )
    if violations.height:
        return CheckResult(
            name,
            PROCESSED,
            WARN,
            f"{violations.height} of {rows.height} played player-weeks ({share:.2%}) lack a matching roster row; "
            f"multi-team players {multi_text}",
            metrics,
        )
    return CheckResult(
        name,
        PROCESSED,
        PASS,
        f"all {rows.height} played player-weeks match a roster row; multi-team players {multi_text}",
        metrics,
    )


def check_point_in_time(ctx: ValidationContext) -> CheckResult:
    """11. Snapshot timestamps are never null and never later than the raw fetch time."""
    name = "point_in_time"
    fdc = ctx.processed_frame("fact_depth_chart", ["snapshot_ts", "season"])
    fir = ctx.processed_frame("fact_injury_report", ["snapshot_ts", "report_modified_at", "season"])
    if fdc is None and fir is None:
        return _skip(name, "fact_depth_chart and fact_injury_report")
    problems: list[str] = []
    notes: list[str] = []
    metrics: dict[str, Any] = {}

    def bound_for(dataset: str) -> tuple[datetime, str]:
        manifest = ctx.raw_manifest(dataset)
        fetched = _parse_iso(manifest.get("fetched_at")) if manifest else None
        if fetched is None:
            return ctx.now, "now (raw manifest unavailable)"
        return fetched, f"raw {dataset} fetched_at {manifest['fetched_at']}"

    def audit(
        table: str, df: pl.DataFrame, column: str, bound: datetime, *, allow_null: bool
    ) -> None:
        if column not in df.columns:
            if allow_null:
                notes.append(f"{table} lacks {column}")
            else:
                problems.append(f"{table} lacks {column}")
            return
        nulls = int(df[column].null_count())
        frame = _to_utc(df.filter(pl.col(column).is_not_null()), column)
        late = (
            int(frame.select((pl.col(column) > pl.lit(bound)).sum()).item()) if frame.height else 0
        )
        latest = frame[column].max() if frame.height else None
        metrics[f"{table}.{column}"] = {
            "rows": df.height,
            "nulls": nulls,
            "later_than_fetch": late,
            "max": latest.isoformat() if isinstance(latest, datetime) else latest,
            "bound": bound.isoformat(),
        }
        if nulls and not allow_null:
            problems.append(f"{table}.{column}: {nulls} null(s)")
        if late:
            problems.append(
                f"{table}.{column}: {late} row(s) later than the raw fetch ({bound.isoformat()})"
            )

    if fdc is not None:
        bound, label = bound_for("depth_charts")
        notes.append(f"depth charts bounded by {label}")
        audit("fact_depth_chart", fdc, "snapshot_ts", bound, allow_null=False)
    else:
        notes.append("fact_depth_chart not built yet")
    if fir is not None:
        bound, label = bound_for("injuries")
        notes.append(f"injuries bounded by {label}")
        audit("fact_injury_report", fir, "snapshot_ts", bound, allow_null=False)
        audit("fact_injury_report", fir, "report_modified_at", bound, allow_null=True)
    else:
        notes.append("fact_injury_report not built yet")

    if problems:
        return CheckResult(name, PROCESSED, FAIL, "; ".join(problems), metrics | {"notes": notes})
    return CheckResult(
        name,
        PROCESSED,
        PASS,
        "snapshot timestamps present and not later than the raw fetch; " + "; ".join(notes),
        metrics | {"notes": notes},
    )


def check_partial_week(ctx: ValidationContext) -> CheckResult:
    """12. No player_stats rows for projection-week games that are still scheduled."""
    name = "partial_week"
    dg = ctx.processed_frame(
        "dim_game", ["game_id", "season", "week", "home_team", "away_team", "status"]
    )
    if dg is None:
        return _skip(name, "dim_game")
    if not ctx.raw_exists("schedules"):
        return CheckResult(name, PROCESSED, SKIP, "schedules not ingested; projection week unknown")
    from ffml.season import current_state

    state = current_state(now_utc=ctx.now, store=ctx.raw)
    metrics: dict[str, Any] = {
        "season": state.season,
        "mode": state.mode,
        "projection_week": state.projection_week,
    }
    if state.mode != "regular" or state.projection_week is None:
        return CheckResult(name, PROCESSED, SKIP, f"{state.mode} mode: no projection week", metrics)
    if state.season not in ctx.seasons:
        return CheckResult(
            name,
            PROCESSED,
            SKIP,
            f"projection season {state.season} not in the requested seasons",
            metrics,
        )
    stats = ctx.raw_frame(
        "player_stats", ["player_id", "player_display_name", "season", "week", "team"]
    )
    if stats is None:
        return CheckResult(name, PROCESSED, SKIP, "player_stats not ingested", metrics)

    scheduled = (
        _int_keys(dg)
        .filter(
            (pl.col("season") == state.season)
            & (pl.col("week") == state.projection_week)
            & (pl.col("status") == "scheduled")
        )
        .with_columns(_canon("home_team"), _canon("away_team"))
    )
    teams = pl.concat(
        [
            scheduled.select(pl.col("home_team").alias("team")),
            scheduled.select(pl.col("away_team").alias("team")),
        ]
    ).unique()
    offending = (
        _int_keys(stats)
        .with_columns(_canon("team"))
        .filter((pl.col("season") == state.season) & (pl.col("week") == state.projection_week))
        .join(teams, on="team", how="semi")
    )
    metrics |= {
        "scheduled_games": scheduled.height,
        "scheduled_teams": sorted(teams["team"].to_list()),
        "offending_rows": offending.height,
        "examples": _examples(offending.sort(["team", "player_id"]), 5),
    }
    if offending.height:
        return CheckResult(
            name,
            PROCESSED,
            FAIL,
            f"{offending.height} player_stats row(s) for week {state.projection_week} games still scheduled "
            f"({', '.join(metrics['scheduled_teams'])})",
            metrics,
        )
    return CheckResult(
        name,
        PROCESSED,
        PASS,
        f"season {state.season} week {state.projection_week}: {scheduled.height} scheduled game(s), no premature stats rows",
        metrics,
    )


# --------------------------------------------------------------------------- #
# Registry and runner
# --------------------------------------------------------------------------- #

CHECKS: list[tuple[str, str, CheckFn]] = [
    ("schema_pins", RAW, check_schema_pins),
    ("duplicates", RAW, check_duplicates),
    ("game_completeness", RAW, check_game_completeness),
    ("weekly_volume", RAW, check_weekly_volume),
    ("null_rates", RAW, check_null_rates),
    ("restricted_hygiene", RAW, check_restricted_hygiene),
    ("dim_player", PROCESSED, check_dim_player),
    ("id_crosswalk", PROCESSED, check_id_crosswalk),
    ("fact_keys", PROCESSED, check_fact_keys),
    ("team_changes", PROCESSED, check_team_changes),
    ("point_in_time", PROCESSED, check_point_in_time),
    ("partial_week", PROCESSED, check_partial_week),
]


def run_check(name: str, tier: str, fn: CheckFn, ctx: ValidationContext) -> CheckResult:
    """Run one check; an exception becomes a FAIL carrying the traceback tail."""
    try:
        result = fn(ctx)
    except Exception as exc:  # noqa: BLE001 - every failure must land in the report
        tail = "".join(traceback.format_exception(exc)).strip().splitlines()[-6:]
        log.error("check %s crashed: %s", name, exc)
        return CheckResult(
            name, tier, FAIL, f"check crashed: {type(exc).__name__}: {exc}", {"traceback": tail}
        )
    result.name = name
    result.tier = tier
    return result


def _md_cell(text: str, limit: int = 400) -> str:
    text = " ".join(str(text).split()).replace("|", "\\|")
    return text if len(text) <= limit else text[: limit - 3] + "..."


def render_report(suite: SuiteResult, ctx: ValidationContext) -> str:
    lines = [
        "# Data validation report",
        "",
        f"- Generated: {suite.generated_at.replace(microsecond=0).isoformat().replace('+00:00', 'Z')}",
        f"- Seasons: {_season_label(suite.seasons)}",
        f"- Tiers: {', '.join(suite.tiers)}",
        f"- Raw store: `{ctx.raw.root}` ({len(ctx.raw.list_datasets())} datasets)",
        f"- Processed store: `{ctx.processed.root}` ({len(ctx.processed.list_datasets())} tables)",
        f"- Schema pins: `{ctx.pin_path}`",
        "",
        "## Summary",
        "",
        "| # | check | tier | status | message |",
        "| --- | --- | --- | --- | --- |",
    ]
    for i, r in enumerate(suite.results, start=1):
        lines.append(f"| {i} | {r.name} | {r.tier} | {r.status} | {_md_cell(r.message)} |")
    lines += [
        "",
        f"**{suite.summary_line}**",
        "",
        f"Overall verdict: **{'PASS' if suite.ok else 'FAIL'}**",
        "",
        "## Details",
    ]
    for i, r in enumerate(suite.results, start=1):
        lines += ["", f"### {i}. {r.name} ({r.tier}): {r.status}", "", r.message]
        if r.metrics:
            lines += ["", "```json", json.dumps(r.metrics, indent=2, default=str), "```"]
    return "\n".join(lines) + "\n"


def format_table(results: Sequence[CheckResult]) -> str:
    width = max((len(r.name) for r in results), default=10)
    rows = [f"{'check':<{width}}  {'tier':<9} {'status':<6} message"]
    for r in results:
        rows.append(f"{r.name:<{width}}  {r.tier:<9} {r.status:<6} {_md_cell(r.message, 110)}")
    return "\n".join(rows)


def run_suite(
    seasons: Iterable[int],
    report_path: str | Path | None = None,
    tiers: Iterable[str] | None = None,
    raw_root: str | Path | None = None,
    processed_root: str | Path | None = None,
    *,
    pin_path: str | Path | None = None,
    now_utc: datetime | None = None,
) -> SuiteResult:
    """Run the suite and write the markdown report; returns every check result."""
    season_list = sorted({int(s) for s in seasons})
    if not season_list:
        raise ValueError("seasons is empty")
    tier_tuple = tuple(t for t in TIERS if t in set(tiers or TIERS))
    unknown = set(tiers or ()) - set(TIERS)
    if unknown:
        raise ValueError(f"unknown tier(s) {sorted(unknown)}; choose from {TIERS}")
    settings = get_settings()
    now = now_utc.astimezone(UTC) if now_utc is not None else datetime.now(UTC)
    ctx = ValidationContext(
        seasons=season_list,
        raw=SnapshotStore(Path(raw_root) if raw_root is not None else settings.raw_dir),
        processed=SnapshotStore(
            Path(processed_root) if processed_root is not None else settings.processed_dir
        ),
        pin_path=Path(pin_path) if pin_path is not None else SCHEMA_PIN_PATH,
        now=now,
    )
    log.info(
        "validate: seasons %s, tiers %s, raw %s, processed %s",
        _season_label(season_list),
        ", ".join(tier_tuple),
        ctx.raw.root,
        ctx.processed.root,
    )
    results = [run_check(name, tier, fn, ctx) for name, tier, fn in CHECKS if tier in tier_tuple]
    path = (
        Path(report_path)
        if report_path
        else settings.reports_dir / f"validation_{now:%Y%m%dT%H%M%SZ}.md"
    )
    suite = SuiteResult(results, path, season_list, tier_tuple, now)
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(render_report(suite, ctx), encoding="utf-8", newline="\n")
    log.info(
        "validation summary\n%s\n%s; report %s", format_table(results), suite.summary_line, path
    )
    return suite


def run_validation(
    seasons: Iterable[int],
    report_path: str | Path | None = None,
    tiers: Iterable[str] | None = None,
    raw_root: str | Path | None = None,
    processed_root: str | Path | None = None,
    *,
    pin_path: str | Path | None = None,
    now_utc: datetime | None = None,
) -> bool:
    """CLI entry point: run the suite, write the report, return ``True`` when nothing failed."""
    suite = run_suite(
        seasons,
        report_path,
        tiers,
        raw_root,
        processed_root,
        pin_path=pin_path,
        now_utc=now_utc,
    )
    verdict = "PASS" if suite.ok else "FAIL"
    log.log(
        logging.INFO if suite.ok else logging.ERROR,
        "validation %s: %s",
        verdict,
        suite.summary_line,
    )
    return suite.ok


__all__ = [
    "CHECKS",
    "FAIL",
    "PASS",
    "PROCESSED",
    "RAW",
    "SKIP",
    "STATUSES",
    "TIERS",
    "WARN",
    "CheckResult",
    "SuiteResult",
    "ValidationContext",
    "format_table",
    "render_report",
    "run_check",
    "run_suite",
    "run_validation",
]
