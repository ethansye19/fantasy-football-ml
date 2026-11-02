"""``sleeper_players``: the daily Sleeper players map as a processed table.

The map is read through :class:`ffml.data.sleeper.SleeperPlayersCache`, which
fetches ``/players/nfl`` at most once per UTC day and keeps the raw dump under
``data/raw/sleeper_players/snapshot=<YYYYMMDD>/players.parquet`` (D-026).
The processed table is the documented subset of columns
(docs/DATA_SCHEMA.md, ``sleeper_players``) with ``gsis_id`` stripped of the
leading space Sleeper pads it with, every external id as a string (D-023),
team codes canonicalised (Sleeper's ``LAR`` -> ``LA``) and a
``snapshot_date``. It is written as a single ``"all"`` partition.

If the fetch fails and no cached snapshot exists the builder logs a warning
and returns ``{}``; the runner tolerates an empty result.
"""

from __future__ import annotations

import logging

import polars as pl

from ffml.data.process.base import PartitionKey, ProcessContext, assert_unique, canonical_team
from ffml.data.process.context import id_as_string
from ffml.data.sleeper import PLAYERS_DATASET, PLAYERS_SCHEMA, SleeperPlayersCache

log = logging.getLogger(__name__)

DOCUMENTED_COLUMNS = [
    "sleeper_id",
    "full_name",
    "position",
    "fantasy_positions",
    "team",
    "status",
    "injury_status",
    "injury_body_part",
    "gsis_id",
    "espn_id",
    "yahoo_id",
    "age",
    "years_exp",
    "depth_chart_order",
    "snapshot_date",
]
ID_COLUMNS: tuple[str, ...] = ("sleeper_id", "gsis_id", "espn_id", "yahoo_id")
INT_COLUMNS: tuple[str, ...] = ("age", "years_exp", "depth_chart_order")


def normalize_sleeper_players(frame: pl.DataFrame) -> pl.DataFrame:
    """Select and type the documented columns; one row per ``sleeper_id``."""
    exprs: list[pl.Expr] = []
    for column in DOCUMENTED_COLUMNS:
        if column not in frame.columns:
            exprs.append(pl.lit(None, dtype=PLAYERS_SCHEMA.get(column, pl.String)).alias(column))
        elif column in ID_COLUMNS:
            exprs.append(id_as_string(column).str.strip_chars())
        elif column in INT_COLUMNS:
            exprs.append(pl.col(column).cast(pl.Int32, strict=False))
        elif column == "snapshot_date":
            exprs.append(pl.col(column).cast(pl.Date))
        elif column == "fantasy_positions":
            exprs.append(pl.col(column))
        else:
            exprs.append(pl.col(column).cast(pl.String))
    out = frame.select(exprs).with_columns(
        *[
            pl.when(pl.col(c) == "")
            .then(pl.lit(None, dtype=pl.String))
            .otherwise(pl.col(c))
            .alias(c)
            for c in ID_COLUMNS
        ]
    )
    out = out.with_columns(canonical_team("team"))
    out = out.filter(pl.col("sleeper_id").is_not_null()).unique(
        subset=["sleeper_id"], keep="first", maintain_order=True
    )
    out = out.sort("sleeper_id")
    assert_unique(out, ["sleeper_id"], "sleeper_players")
    return out


def build_sleeper_players(ctx: ProcessContext) -> dict[str, dict[PartitionKey, pl.DataFrame]]:
    """Fetch (or reuse today's cached) Sleeper players map and normalise it."""
    cache = SleeperPlayersCache(root=ctx.settings.raw_dir / PLAYERS_DATASET)
    try:
        frame = cache.get()
    except Exception as exc:  # network, HTTP, or parsing failure
        latest = cache.latest()
        if latest is None:
            log.warning(
                "sleeper_players: fetch failed (%s: %s) and no cached snapshot exists; skipping",
                type(exc).__name__,
                exc,
            )
            return {}
        log.warning(
            "sleeper_players: fetch failed (%s: %s); using cached snapshot %s",
            type(exc).__name__,
            exc,
            latest,
        )
        frame = cache.read(latest)

    out = normalize_sleeper_players(frame)
    day = out["snapshot_date"].max() if out.height else cache.latest()
    if day is not None:
        ctx.raw_snapshot_ids[PLAYERS_DATASET] = f"{day:%Y%m%d}"
    log.info("sleeper_players: %d rows, snapshot_date=%s", out.height, day)
    return {"sleeper_players": {"all": out}}


__all__ = ["DOCUMENTED_COLUMNS", "build_sleeper_players", "normalize_sleeper_players"]
