"""Actual outcomes in the stat-line vocabulary (D-048).

:func:`actual_statlines` maps ``fact_player_game`` (QB, RB, WR, TE, K) and
``fact_dst_game`` (DST, ``entity_id`` = team code) onto
:data:`ffml.scoring.statline.STAT_COLUMNS` so that league scoring can be
applied to outcomes exactly as it is applied to simulated samples.

Column mapping from ``fact_player_game``: ``pass_cmp = completions``,
``pass_int = interceptions``, ``rush_att = carries``, ``rec = receptions``,
``two_pt = two_pt_conversions``, ``fum_lost = fumbles_lost``,
``ret_td = return_tds``, ``fg_made_<bucket>`` from the fine distance buckets,
``fg_missed`` as is, ``xp_made = pat_made``, ``xp_missed = pat_att - pat_made``.

From ``fact_dst_game``: ``def_int = interceptions``,
``fum_rec = fumble_recoveries``, ``st_td = return_td``,
``pts_allowed = points_allowed``, ``yds_allowed = yards_allowed`` (rounded).

Reading outcomes for a season is an evaluation access, so the holdout guard
(:func:`ffml.holdout.assert_seasons_allowed`) runs first.
"""

from __future__ import annotations

from collections.abc import Iterable, Sequence

import polars as pl

from ffml.config import get_settings
from ffml.data.store import SnapshotStore
from ffml.holdout import assert_seasons_allowed
from ffml.scoring.statline import (
    ACTUAL_STAT_DTYPE,
    DST_COLUMNS,
    KICKING_COLUMNS,
    OFFENSE_COLUMNS,
    STAT_COLUMNS,
)

CONTEXT_COLUMNS: tuple[str, ...] = (
    "game_id",
    "team",
    "opponent",
    "game_type",
    "kickoff_utc",
    "played",
)
OUTPUT_COLUMNS: tuple[str, ...] = (
    "entity_id",
    "position",
    "season",
    "week",
    *CONTEXT_COLUMNS,
    *STAT_COLUMNS,
)

_PLAYER_SOURCE_COLUMNS: tuple[str, ...] = (
    "player_id",
    "season",
    "week",
    "game_id",
    "team",
    "opponent",
    "position",
    "game_type",
    "kickoff_utc",
    "played",
    "pass_att",
    "completions",
    "pass_yds",
    "pass_td",
    "interceptions",
    "carries",
    "rush_yds",
    "rush_td",
    "targets",
    "receptions",
    "rec_yds",
    "rec_td",
    "two_pt_conversions",
    "fumbles_lost",
    "return_tds",
    "fg_made_0_19",
    "fg_made_20_29",
    "fg_made_30_39",
    "fg_made_40_49",
    "fg_made_50_59",
    "fg_made_60p",
    "fg_missed",
    "pat_att",
    "pat_made",
)

_DST_SOURCE_COLUMNS: tuple[str, ...] = (
    "team",
    "season",
    "week",
    "game_id",
    "opponent",
    "game_type",
    "sacks",
    "interceptions",
    "fumble_recoveries",
    "def_td",
    "return_td",
    "safeties",
    "blocked_kicks",
    "points_allowed",
    "yards_allowed",
)


def _int(name: str) -> pl.Expr:
    return pl.col(name).fill_null(0).cast(ACTUAL_STAT_DTYPE)


def player_statlines(fact_player_game: pl.DataFrame) -> pl.DataFrame:
    """Offense and kicker rows of ``fact_player_game`` in the stat-line vocabulary."""
    f = fact_player_game
    offense = {
        "pass_att": _int("pass_att"),
        "pass_cmp": _int("completions"),
        "pass_yds": _int("pass_yds"),
        "pass_td": _int("pass_td"),
        "pass_int": _int("interceptions"),
        "rush_att": _int("carries"),
        "rush_yds": _int("rush_yds"),
        "rush_td": _int("rush_td"),
        "targets": _int("targets"),
        "rec": _int("receptions"),
        "rec_yds": _int("rec_yds"),
        "rec_td": _int("rec_td"),
        "two_pt": _int("two_pt_conversions"),
        "fum_lost": _int("fumbles_lost"),
        "ret_td": _int("return_tds"),
    }
    kicking = {c: _int(c) for c in KICKING_COLUMNS if c.startswith("fg_made_")}
    kicking["fg_missed"] = _int("fg_missed")
    kicking["xp_made"] = _int("pat_made")
    kicking["xp_missed"] = (_int("pat_att") - _int("pat_made")).clip(lower_bound=0)
    exprs = [offense[c].alias(c) for c in OFFENSE_COLUMNS]
    exprs += [kicking[c].alias(c) for c in KICKING_COLUMNS]
    exprs += [pl.lit(0, dtype=ACTUAL_STAT_DTYPE).alias(c) for c in DST_COLUMNS]
    return f.select(
        pl.col("player_id").alias("entity_id"),
        pl.col("position"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("game_id"),
        pl.col("team"),
        pl.col("opponent"),
        pl.col("game_type"),
        pl.col("kickoff_utc"),
        pl.col("played").fill_null(False),
        *exprs,
    )


def dst_statlines(
    fact_dst_game: pl.DataFrame, dim_game: pl.DataFrame | None = None
) -> pl.DataFrame:
    """DST rows of ``fact_dst_game`` in the stat-line vocabulary (``entity_id`` = team)."""
    d = fact_dst_game
    if "kickoff_utc" not in d.columns:
        if dim_game is None:
            raise ValueError("dst_statlines needs kickoff_utc or dim_game")
        d = d.join(dim_game.select("game_id", "kickoff_utc"), on="game_id", how="left")
    dst = {
        "sacks": _int("sacks"),
        "def_int": _int("interceptions"),
        "fum_rec": _int("fumble_recoveries"),
        "def_td": _int("def_td"),
        "st_td": _int("return_td"),
        "safeties": _int("safeties"),
        "blocked_kicks": _int("blocked_kicks"),
        "pts_allowed": _int("points_allowed"),
        "yds_allowed": pl.col("yards_allowed").fill_null(0).round(0).cast(ACTUAL_STAT_DTYPE),
    }
    zeros = [
        pl.lit(0, dtype=ACTUAL_STAT_DTYPE).alias(c) for c in (*OFFENSE_COLUMNS, *KICKING_COLUMNS)
    ]
    return d.select(
        pl.col("team").alias("entity_id"),
        pl.lit("DST").alias("position"),
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("game_id"),
        pl.col("team"),
        pl.col("opponent"),
        pl.col("game_type"),
        pl.col("kickoff_utc"),
        pl.lit(True).alias("played"),
        *zeros,
        *[dst[c].alias(c) for c in DST_COLUMNS],
    ).select(OUTPUT_COLUMNS)


def actual_statlines(
    seasons: Iterable[int],
    purpose: str,
    *,
    game_types: Sequence[str] | None = ("REG",),
    include_dst: bool = True,
    store: SnapshotStore | None = None,
) -> pl.DataFrame:
    """Actual stat lines for ``seasons`` (offense, kickers, and DST).

    One row per entity and game with the key columns, ``game_id``, ``team``,
    ``opponent``, ``game_type``, ``kickoff_utc``, ``played`` and every column
    of :data:`STAT_COLUMNS`. Rostered players who did not play are kept with
    ``played = false`` and zero stats. ``game_types=None`` keeps postseason
    rows; the default is the regular season only (MODELING.md section 2).

    Calls the holdout guard with ``purpose`` before reading anything.
    """
    wanted = sorted({int(s) for s in seasons})
    if not wanted:
        raise ValueError("seasons is empty")
    assert_seasons_allowed(wanted, purpose)
    store = store or SnapshotStore(get_settings().processed_dir)

    fpg = store.read("fact_player_game", seasons=wanted, columns=list(_PLAYER_SOURCE_COLUMNS))
    frames = [player_statlines(fpg).select(OUTPUT_COLUMNS)]
    if include_dst:
        dst = store.read("fact_dst_game", seasons=wanted, columns=list(_DST_SOURCE_COLUMNS))
        games = store.read("dim_game", seasons=wanted, columns=["game_id", "kickoff_utc"])
        frames.append(dst_statlines(dst, games))
    out = pl.concat(frames, how="vertical_relaxed")
    if game_types is not None:
        out = out.filter(pl.col("game_type").is_in(list(game_types)))
    return out.sort(["season", "week", "position", "entity_id"])


__all__ = [
    "CONTEXT_COLUMNS",
    "OUTPUT_COLUMNS",
    "actual_statlines",
    "dst_statlines",
    "player_statlines",
]
