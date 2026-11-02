"""Shared context and helpers for processed-table builders.

Every builder module exposes one function ``build_<group>(ctx: ProcessContext)``
returning ``{table_name: {partition_key: DataFrame}}`` where ``partition_key``
is an ``int`` season for seasonal tables or the literal ``"all"``.

Canonical team codes are the current nflverse abbreviations (``LA`` for the
Rams, ``LV`` for the Raiders, ``LAC`` for the Chargers, ``JAX`` for the
Jaguars, ``WAS`` for Washington). Sleeper uses ``LAR`` for the Rams, and some
sources use Pro Football Reference style codes; ``canonical_team`` maps all
of them.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import polars as pl

from ffml.config import Settings, get_settings
from ffml.data.store import SnapshotStore

log = logging.getLogger(__name__)

PartitionKey = int | str

TEAM_ALIASES: dict[str, str] = {
    "OAK": "LV",
    "SD": "LAC",
    "STL": "LA",
    "SL": "LA",
    "LAR": "LA",
    "JAC": "JAX",
    "WSH": "WAS",
    "HST": "HOU",
    "BLT": "BAL",
    "CLV": "CLE",
    "ARZ": "ARI",
}

# Tables that must never be read by the API or the web app (DECISIONS D-007).
RESTRICTED_TABLES: frozenset[str] = frozenset({"fact_ecr_weekly"})

SKILL_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K")


def canonical_team(column: str) -> pl.Expr:
    """Expression mapping historical or third-party team codes to canonical codes.

    The column is cast to String first so an all-null column (Null dtype) maps cleanly.
    """
    return pl.col(column).cast(pl.String).replace(TEAM_ALIASES).alias(column)


def canonical_team_value(code: str | None) -> str | None:
    if code is None:
        return None
    return TEAM_ALIASES.get(code, code)


@dataclass
class ProcessContext:
    """State shared by builders within one ``ffml process`` run."""

    seasons: list[int]
    raw: SnapshotStore
    processed: SnapshotStore
    processed_restricted: SnapshotStore
    settings: Settings
    tables: dict[str, dict[PartitionKey, pl.DataFrame]] = field(default_factory=dict)
    raw_snapshot_ids: dict[str, str] = field(default_factory=dict)

    @classmethod
    def from_settings(cls, seasons: list[int], settings: Settings | None = None) -> ProcessContext:
        settings = settings or get_settings()
        return cls(
            seasons=sorted(seasons),
            raw=SnapshotStore(settings.raw_dir),
            processed=SnapshotStore(settings.processed_dir),
            processed_restricted=SnapshotStore(settings.processed_dir / "restricted"),
            settings=settings,
        )

    # ----- raw access -------------------------------------------------------------------

    def read_raw(
        self,
        dataset: str,
        seasons: list[PartitionKey] | None = None,
        columns: list[str] | None = None,
    ) -> pl.DataFrame:
        """Read the LATEST snapshot of a raw dataset and record its id for lineage."""
        snapshot_id = self.raw.latest_id(dataset)
        if snapshot_id is None:
            raise FileNotFoundError(f"raw dataset {dataset!r} has no snapshot under {self.raw.root}")
        self.raw_snapshot_ids[dataset] = snapshot_id
        return self.raw.read(dataset, seasons=seasons, snapshot_id=snapshot_id, columns=columns)

    # ----- processed access -------------------------------------------------------------

    def table(self, name: str, seasons: list[PartitionKey] | None = None) -> pl.DataFrame:
        """Return a processed table built earlier in this run, else its LATEST snapshot."""
        if name in self.tables:
            frames = self.tables[name]
            keys = list(frames) if seasons is None else [k for k in frames if k in set(seasons)]
            parts = [frames[k] for k in keys]
            if not parts:
                raise KeyError(f"table {name!r} has no partitions for {seasons}")
            return pl.concat(parts, how="vertical_relaxed")
        store = self.processed_restricted if name in RESTRICTED_TABLES else self.processed
        return store.read(name, seasons=seasons)

    def store_for(self, table: str) -> SnapshotStore:
        return self.processed_restricted if table in RESTRICTED_TABLES else self.processed


def split_by_season(df: pl.DataFrame, season_col: str = "season") -> dict[PartitionKey, pl.DataFrame]:
    """Split a frame into ``{season: frame}`` partitions (helper for builders)."""
    out: dict[PartitionKey, pl.DataFrame] = {}
    for (season,), part in df.partition_by(season_col, as_dict=True, maintain_order=True).items():
        out[int(season)] = part
    return out


def assert_unique(df: pl.DataFrame, keys: list[str], table: str) -> None:
    """Raise if ``keys`` are not unique; builders call this before returning."""
    dup = df.select(keys).is_duplicated().sum()
    if dup:
        sample = df.filter(df.select(keys).is_duplicated()).select(keys).head(5)
        raise ValueError(f"{table}: {dup} duplicate rows on {keys}; sample:\n{sample}")


# ----- additive helpers shared by the core-table builders ---------------------------------

#: Raw positions folded into a fantasy position (``position_raw`` keeps the source value).
FANTASY_POSITION_MAP: dict[str, str] = {"FB": "RB"}

#: Historical codes grouped by the canonical code they map to (``dim_team.aliases``).
TEAM_ALIAS_GROUPS: dict[str, list[str]] = {}
for _alias, _canonical in TEAM_ALIASES.items():
    TEAM_ALIAS_GROUPS.setdefault(_canonical, []).append(_alias)


def fantasy_position(column: str) -> pl.Expr:
    """Expression mapping a raw position to the fantasy position (FB to RB, others unchanged)."""
    return pl.col(column).replace(FANTASY_POSITION_MAP).alias(column)


def read_raw_or_none(
    ctx: ProcessContext,
    dataset: str,
    seasons: list[PartitionKey] | None = None,
    columns: list[str] | None = None,
) -> pl.DataFrame | None:
    """``ctx.read_raw`` that returns ``None`` (and logs) when the partition does not exist."""
    try:
        return ctx.read_raw(dataset, seasons=seasons, columns=columns)
    except FileNotFoundError as exc:
        log.warning("raw %s has no partition for %s (%s)", dataset, seasons, exc)
        return None


def team_game_map(dim_game: pl.DataFrame) -> pl.DataFrame:
    """Long form of ``dim_game``: one row per (season, week, team) with the game context.

    Columns: ``season, week, team, game_id, opponent, home, game_type, kickoff_utc``
    plus ``points`` (the team's score) and ``points_allowed`` (the opponent's score).
    """
    base_cols = ["game_id", "season", "week", "game_type", "kickoff_utc"]
    home = dim_game.select(
        *base_cols,
        pl.col("home_team").alias("team"),
        pl.col("away_team").alias("opponent"),
        pl.lit(True).alias("home"),
        pl.col("home_score").alias("points"),
        pl.col("away_score").alias("points_allowed"),
    )
    away = dim_game.select(
        *base_cols,
        pl.col("away_team").alias("team"),
        pl.col("home_team").alias("opponent"),
        pl.lit(False).alias("home"),
        pl.col("away_score").alias("points"),
        pl.col("home_score").alias("points_allowed"),
    )
    return pl.concat([home, away]).select(
        "season",
        "week",
        "team",
        "game_id",
        "opponent",
        "home",
        "game_type",
        "kickoff_utc",
        "points",
        "points_allowed",
    )


def clean_id(column: str) -> pl.Expr:
    """Cast an external id to a stripped string, turning blanks into nulls (D-023)."""
    text = pl.col(column).cast(pl.String).str.strip_chars()
    return pl.when(text == "").then(None).otherwise(text).alias(column)
