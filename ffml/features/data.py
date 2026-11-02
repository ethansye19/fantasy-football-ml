"""Input frames for the feature builders and the week calendar (cutoff rules).

:class:`FeatureContext` holds the processed tables the builders need, already
column-pruned. It is built from the processed snapshot store for real runs and
from a directory of Parquet files for the offline leakage tests.

Calendar (D-046): for every season-week, ``cutoff_ts`` is the first kickoff of
that week across the league and ``asof_ts = cutoff_ts - 1 hour`` for
historical rows. Weeks are strictly ordered in time in dim_game 2016-2026 (no
game of week w kicks off at or after the first kickoff of week w + 1).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field, replace
from datetime import datetime, timedelta
from functools import cached_property
from pathlib import Path

import polars as pl

from ffml.features.spec import PARAMS

log = logging.getLogger(__name__)

TABLES: tuple[str, ...] = (
    "dim_game",
    "dim_player",
    "fact_player_game",
    "fact_team_game",
    "fact_dst_game",
    "fact_injury_report",
    "fact_depth_chart",
    "fact_roster_week",
)

#: depth-chart position abbreviations mapped to fantasy position groups; other
#: abbreviations (offensive line, defense, returners) are not read.
DEPTH_POS_GROUP: dict[str, str] = {
    "QB": "QB",
    "RB": "RB",
    "HB": "RB",
    "FB": "FB",
    "WR": "WR",
    "LWR": "WR",
    "RWR": "WR",
    "SWR": "WR",
    "TE": "TE",
    "K": "K",
    "PK": "K",
}

COLUMNS: dict[str, list[str] | None] = {
    "dim_game": None,
    "dim_player": [
        "player_id",
        "position",
        "birth_date",
        "rookie_season",
        "draft_year",
        "draft_round",
        "draft_pick",
        "height",
        "weight",
    ],
    "fact_player_game": None,
    "fact_team_game": None,
    "fact_dst_game": None,
    "fact_injury_report": [
        "season",
        "week",
        "team",
        "player_id",
        "position",
        "report_status",
        "practice_status",
        "report_modified_at",
    ],
    "fact_depth_chart": [
        "snapshot_ts",
        "season",
        "week",
        "team",
        "player_id",
        "pos_abb",
        "pos_rank",
    ],
    "fact_roster_week": ["season", "week", "team", "player_id", "position", "status"],
}

SEASONAL = {t for t in TABLES if t not in ("dim_player",)}


def build_calendar(dim_game: pl.DataFrame) -> pl.DataFrame:
    """One row per season-week: cutoff, as-of, week end, ordinal and regular-season length."""
    offset = timedelta(hours=int(PARAMS["asof_offset_hours"]))
    cal = (
        dim_game.group_by("season", "week")
        .agg(
            pl.col("kickoff_utc").min().alias("cutoff_ts"),
            pl.col("kickoff_utc").max().alias("week_end_ts"),
            (pl.col("game_type") == "REG").all().alias("is_reg"),
        )
        .sort("cutoff_ts", "season", "week")
        .with_columns(
            (pl.col("cutoff_ts") - offset).alias("asof_hist_ts"),
            pl.int_range(pl.len(), dtype=pl.Int32).alias("cal_idx"),
        )
    )
    reg_weeks = (
        cal.filter(pl.col("is_reg"))
        .group_by("season")
        .agg(pl.col("week").max().alias("n_reg_weeks"))
    )
    return cal.join(reg_weeks, on="season", how="left").with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


@dataclass
class FeatureContext:
    """Processed inputs for the builders (column-pruned, depth chart filtered to skill slots)."""

    dim_game: pl.DataFrame
    dim_player: pl.DataFrame
    player_game: pl.DataFrame
    team_game: pl.DataFrame
    dst_game: pl.DataFrame
    injury: pl.DataFrame
    depth: pl.DataFrame
    roster: pl.DataFrame
    snapshot_ids: dict[str, str] = field(default_factory=dict)
    cache: dict = field(default_factory=dict, repr=False, compare=False)

    @cached_property
    def calendar(self) -> pl.DataFrame:
        return build_calendar(self.dim_game)

    @cached_property
    def team_games(self) -> pl.DataFrame:
        """Long-form schedule from dim_game: one row per team and game (all game types)."""
        g = self.dim_game
        common = ["game_id", "season", "week", "game_type", "kickoff_utc"]
        home = g.select(
            *common,
            pl.col("home_team").alias("team"),
            pl.col("away_team").alias("opponent"),
            pl.lit(True).alias("home"),
        )
        away = g.select(
            *common,
            pl.col("away_team").alias("team"),
            pl.col("home_team").alias("opponent"),
            pl.lit(False).alias("home"),
        )
        return pl.concat([home, away]).sort("kickoff_utc", "team")

    # -- constructors ------------------------------------------------------ #

    @classmethod
    def from_frames(
        cls, frames: dict[str, pl.DataFrame], snapshot_ids: dict[str, str] | None = None
    ) -> FeatureContext:
        missing = [t for t in TABLES if t not in frames]
        if missing:
            raise ValueError(f"missing input tables {missing}")
        prepared = {t: _prepare(t, frames[t]) for t in TABLES}
        kick = prepared["dim_game"].select("game_id", "kickoff_utc")
        for t in ("fact_team_game", "fact_dst_game"):
            prepared[t] = (
                prepared[t].drop("kickoff_utc", strict=False).join(kick, on="game_id", how="left")
            )
        return cls(
            dim_game=prepared["dim_game"],
            dim_player=prepared["dim_player"],
            player_game=prepared["fact_player_game"],
            team_game=prepared["fact_team_game"],
            dst_game=prepared["fact_dst_game"],
            injury=prepared["fact_injury_report"],
            depth=prepared["fact_depth_chart"],
            roster=prepared["fact_roster_week"],
            snapshot_ids=dict(snapshot_ids or {}),
        )

    @classmethod
    def from_store(cls, seasons: list[int] | None = None, store=None) -> FeatureContext:
        """Load LATEST processed snapshots for ``first_season .. max(seasons)``."""
        from ffml.config import get_settings
        from ffml.data.store import SnapshotStore

        settings = get_settings()
        store = store or SnapshotStore(settings.processed_dir)
        through = max(seasons) if seasons else None
        wanted = None
        if through is not None:
            wanted = list(range(settings.first_season, through + 1))
        frames: dict[str, pl.DataFrame] = {}
        ids: dict[str, str] = {}
        for table in TABLES:
            sid = store.latest_id(table)
            if sid is None:
                raise FileNotFoundError(
                    f"processed table {table!r} has no snapshot; run `ffml process`"
                )
            ids[table] = sid
            frames[table] = store.read(
                table,
                seasons=wanted if table in SEASONAL and table != "dim_game" else None,
                snapshot_id=sid,
                columns=COLUMNS[table],
            )
        return cls.from_frames(frames, ids)

    @classmethod
    def from_dir(cls, path: Path | str) -> FeatureContext:
        """Load ``<table>.parquet`` files (the offline test fixtures)."""
        path = Path(path)
        frames = {t: pl.read_parquet(path / f"{t}.parquet") for t in TABLES}
        return cls.from_frames(frames, {t: "fixture" for t in TABLES})

    # -- derived contexts for tests ---------------------------------------- #

    def truncate(self, before: datetime) -> FeatureContext:
        """Drop every game-derived and off-field record at or after ``before``.

        Game facts are kept when their kickoff precedes ``before``; depth-chart
        snapshots when ``snapshot_ts < before``; injury and roster weeks when
        the week's cutoff precedes ``before``. The schedule is kept whole
        (it is published in advance).
        """
        cal = self.calendar.select("season", "week", "cutoff_ts")
        weeks_before = cal.filter(pl.col("cutoff_ts") < before).select("season", "week")
        return replace(
            self,
            player_game=self.player_game.filter(pl.col("kickoff_utc") < before),
            team_game=self.team_game.filter(pl.col("kickoff_utc") < before),
            dst_game=self.dst_game.filter(pl.col("kickoff_utc") < before),
            injury=self.injury.join(weeks_before, on=["season", "week"], how="semi"),
            roster=self.roster.join(weeks_before, on=["season", "week"], how="semi"),
            depth=self.depth.filter(pl.col("snapshot_ts") < before),
            cache={},
        )

    def with_tables(self, **frames: pl.DataFrame) -> FeatureContext:
        """Copy with some tables replaced (tests use this to scramble a week)."""
        mapping = {
            "fact_player_game": "player_game",
            "fact_team_game": "team_game",
            "fact_dst_game": "dst_game",
            "fact_injury_report": "injury",
            "fact_depth_chart": "depth",
            "fact_roster_week": "roster",
        }
        kwargs = {mapping.get(k, k): v for k, v in frames.items()}
        return replace(self, **kwargs, cache={})


def _prepare(table: str, df: pl.DataFrame) -> pl.DataFrame:
    cols = COLUMNS.get(table)
    if cols is not None:
        df = df.select([c for c in cols if c in df.columns])
    for c in ("season", "week"):
        if c in df.columns:
            df = df.with_columns(pl.col(c).cast(pl.Int32))
    if table == "fact_depth_chart":
        df = df.filter(pl.col("pos_abb").is_in(list(DEPTH_POS_GROUP))).with_columns(
            pl.col("pos_abb").replace_strict(DEPTH_POS_GROUP, default=None).alias("pos_group")
        )
    return df


__all__ = [
    "COLUMNS",
    "DEPTH_POS_GROUP",
    "TABLES",
    "FeatureContext",
    "build_calendar",
]
