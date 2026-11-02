"""Canonical stat-line vocabulary (DECISIONS D-048, implements D-003).

One set of column names is shared by simulated samples (``ffml.sim``), actual
outcomes (:mod:`ffml.scoring.actuals`) and the scoring engine
(:mod:`ffml.scoring.rules`), so any league's rules apply to either.

Every stat-line frame carries the key columns

=============  =====  ==========================================================
column         type   meaning
=============  =====  ==========================================================
``entity_id``  str    ``player_id`` (gsis id) for QB/RB/WR/TE/K, team code for DST
``position``   str    one of :data:`POSITIONS`
``season``     int
``week``       int
=============  =====  ==========================================================

plus the stat columns in :data:`STAT_COLUMNS`. Offense and kicker rows carry
zeros in the DST columns and DST rows carry zeros in the offense and kicking
columns, so one frame can hold every position.

Offense (QB, RB, WR, TE; kickers carry zeros):

- ``pass_att``, ``pass_cmp``, ``pass_yds``, ``pass_td``, ``pass_int``
- ``rush_att``, ``rush_yds``, ``rush_td``
- ``targets``, ``rec``, ``rec_yds``, ``rec_td``
- ``two_pt``: successful two-point conversions of any kind (passing, rushing,
  receiving), matching ``fact_player_game.two_pt_conversions``
- ``fum_lost``: fumbles lost (rushing, receiving and sack fumbles)
- ``ret_td``: special-teams return touchdowns scored by the player
  (``fact_player_game.return_tds``)

Kicking (K):

- ``fg_made_0_19`` ... ``fg_made_60p``: made field goals by distance bucket
- ``fg_missed`` (blocked attempts included), ``xp_made``, ``xp_missed``

Team defense and special teams (DST):

- ``sacks``, ``def_int``, ``fum_rec``, ``def_td`` (interception and fumble
  return touchdowns), ``st_td`` (kick and punt return touchdowns),
  ``safeties``, ``blocked_kicks``, ``pts_allowed`` (opponent's final score,
  D-045), ``yds_allowed`` (opponent's offensive yards)
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K", "DST")
OFFENSE_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE")

KEY_COLUMNS: tuple[str, ...] = ("entity_id", "position", "season", "week")

OFFENSE_COLUMNS: tuple[str, ...] = (
    "pass_att",
    "pass_cmp",
    "pass_yds",
    "pass_td",
    "pass_int",
    "rush_att",
    "rush_yds",
    "rush_td",
    "targets",
    "rec",
    "rec_yds",
    "rec_td",
    "two_pt",
    "fum_lost",
    "ret_td",
)

FG_BUCKETS: tuple[str, ...] = ("0_19", "20_29", "30_39", "40_49", "50_59", "60p")
FG_MADE_COLUMNS: tuple[str, ...] = tuple(f"fg_made_{b}" for b in FG_BUCKETS)

KICKING_COLUMNS: tuple[str, ...] = (
    *FG_MADE_COLUMNS,
    "fg_missed",
    "xp_made",
    "xp_missed",
)

DST_COLUMNS: tuple[str, ...] = (
    "sacks",
    "def_int",
    "fum_rec",
    "def_td",
    "st_td",
    "safeties",
    "blocked_kicks",
    "pts_allowed",
    "yds_allowed",
)

STAT_COLUMNS: tuple[str, ...] = (*OFFENSE_COLUMNS, *KICKING_COLUMNS, *DST_COLUMNS)

# Storage type of stat columns in simulated samples (all stats are integral;
# Int16 covers every realistic value including yardage and points allowed).
SIM_STAT_DTYPE = pl.Int16
# Storage type of stat columns in actual outcomes.
ACTUAL_STAT_DTYPE = pl.Int32


def missing_stat_columns(columns: Iterable[str]) -> list[str]:
    """Stat columns from :data:`STAT_COLUMNS` that are absent from ``columns``."""
    have = set(columns)
    return [c for c in STAT_COLUMNS if c not in have]


def ensure_stat_columns(
    frame: pl.DataFrame, dtype: pl.DataType = ACTUAL_STAT_DTYPE
) -> pl.DataFrame:
    """Add any missing stat columns as zeros and fill nulls in existing ones with 0."""
    missing = missing_stat_columns(frame.columns)
    out = frame.with_columns(pl.lit(0, dtype=dtype).alias(c) for c in missing)
    return out.with_columns(pl.col(c).fill_null(0) for c in STAT_COLUMNS)


def empty_statline_frame(dtype: pl.DataType = ACTUAL_STAT_DTYPE) -> pl.DataFrame:
    """An empty frame with the key columns and every stat column."""
    schema: dict[str, pl.DataType] = {
        "entity_id": pl.String,
        "position": pl.String,
        "season": pl.Int32,
        "week": pl.Int32,
    }
    schema.update({c: dtype for c in STAT_COLUMNS})
    return pl.DataFrame(schema=schema)


__all__ = [
    "ACTUAL_STAT_DTYPE",
    "DST_COLUMNS",
    "FG_BUCKETS",
    "FG_MADE_COLUMNS",
    "KEY_COLUMNS",
    "KICKING_COLUMNS",
    "OFFENSE_COLUMNS",
    "OFFENSE_POSITIONS",
    "POSITIONS",
    "SIM_STAT_DTYPE",
    "STAT_COLUMNS",
    "empty_statline_frame",
    "ensure_stat_columns",
    "missing_stat_columns",
]
