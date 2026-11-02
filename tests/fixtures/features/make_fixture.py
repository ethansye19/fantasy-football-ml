"""Regenerate the offline feature fixtures from the local processed snapshots.

Slice: Kansas City and Detroit (who met in the 2023 Thursday opener), 2022 in
full plus 2023 weeks 1-6, for the player-level tables; every team for the
team-game and DST tables of the same weeks so opponent-defense windows exist.
Depth charts keep only skill-position slots. Run from the repository root::

    .venv/Scripts/python tests/fixtures/features/make_fixture.py

Never reads anything under a ``restricted`` directory.
"""

from __future__ import annotations

from pathlib import Path

import polars as pl

from ffml.config import get_settings
from ffml.data.store import SnapshotStore
from ffml.features.data import COLUMNS, DEPTH_POS_GROUP, TABLES

HERE = Path(__file__).resolve().parent
TEAMS = ["KC", "DET"]
SEASONS = [2022, 2023]
LAST_WEEK_2023 = 6


def _in_window(df: pl.DataFrame) -> pl.DataFrame:
    return df.filter(
        (pl.col("season") == 2022)
        | ((pl.col("season") == 2023) & (pl.col("week") <= LAST_WEEK_2023))
    )


def main() -> None:
    store = SnapshotStore(get_settings().processed_dir)
    out: dict[str, pl.DataFrame] = {}
    out["dim_game"] = store.read("dim_game", seasons=SEASONS)
    pg = _in_window(store.read("fact_player_game", seasons=SEASONS)).filter(
        pl.col("team").is_in(TEAMS)
    )
    out["fact_player_game"] = pg
    out["fact_team_game"] = _in_window(store.read("fact_team_game", seasons=SEASONS))
    out["fact_dst_game"] = _in_window(store.read("fact_dst_game", seasons=SEASONS))
    out["fact_injury_report"] = _in_window(
        store.read("fact_injury_report", seasons=SEASONS, columns=COLUMNS["fact_injury_report"])
    ).filter(pl.col("team").is_in(TEAMS))
    out["fact_roster_week"] = _in_window(
        store.read("fact_roster_week", seasons=SEASONS, columns=COLUMNS["fact_roster_week"])
    ).filter(pl.col("team").is_in(TEAMS))
    out["fact_depth_chart"] = _in_window(
        store.read("fact_depth_chart", seasons=SEASONS, columns=COLUMNS["fact_depth_chart"])
    ).filter(pl.col("team").is_in(TEAMS), pl.col("pos_abb").is_in(list(DEPTH_POS_GROUP)))
    ids = pl.concat(
        [
            out["fact_player_game"].select("player_id"),
            out["fact_injury_report"].select("player_id"),
            out["fact_roster_week"].select("player_id"),
            out["fact_depth_chart"].select("player_id"),
        ]
    ).unique()
    out["dim_player"] = store.read("dim_player", columns=COLUMNS["dim_player"]).join(
        ids, on="player_id", how="semi"
    )
    total = 0
    for table in TABLES:
        path = HERE / f"{table}.parquet"
        out[table].write_parquet(path, compression="zstd", compression_level=19)
        size = path.stat().st_size
        total += size
        print(f"{table}: {out[table].height} rows, {size / 1024:.1f} KB")
    print(f"total {total / 1024:.1f} KB")


if __name__ == "__main__":
    main()
