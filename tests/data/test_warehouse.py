"""Warehouse views and data dictionary over a tiny temporary lake."""

from __future__ import annotations

import json
from datetime import UTC, datetime

import duckdb
import polars as pl

from ffml.data.dictionary import render_data_dictionary, write_data_dictionary
from ffml.data.store import SnapshotStore
from ffml.data.warehouse import is_restricted_dataset, list_views, rebuild_warehouse

FETCHED = "2026-11-01T12:00:00Z"


def _stats(season: int, n: int) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [season] * n,
            "week": list(range(1, n + 1)),
            "player_id": [f"00-{i:04d}" for i in range(n)],
            "targets": [float(i) for i in range(n)],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32},
    )


def make_tiny_lake(settings) -> tuple[SnapshotStore, SnapshotStore]:
    raw = SnapshotStore(settings.raw_dir)
    processed = SnapshotStore(settings.processed_dir)
    raw.write_snapshot(
        "player_stats",
        {2024: _stats(2024, 3), 2025: _stats(2025, 4)},
        meta={
            "loader": "nflreadpy.load_player_stats",
            "fetched_at": FETCHED,
            "source_license": "CC-BY-4.0 (nflverse)",
        },
    )
    # timestamped layout without a season column: the hive directory supplies it
    raw.write_snapshot(
        "depth_charts",
        {
            2024: pl.DataFrame({"season": [2024], "club_code": ["KC"], "gsis_id": ["00-A"]}),
            2025: pl.DataFrame(
                {"dt": ["2025-09-09T07:00:00Z"], "team": ["KC"], "gsis_id": ["00-A"]}
            ),
        },
        meta={"loader": "nflreadpy.load_depth_charts", "fetched_at": FETCHED},
    )
    raw.write_snapshot(
        "teams",
        {"all": pl.DataFrame({"team_abbr": ["KC", "BUF"], "team_name": ["Chiefs", "Bills"]})},
        meta={
            "loader": "nflreadpy.load_teams",
            "fetched_at": FETCHED,
            "source_license": "CC-BY-4.0",
        },
    )
    raw.write_snapshot(
        "restricted/ff_rankings_all",
        {"all": pl.DataFrame({"player": ["Secret"], "ecr": [1.0]})},
        meta={"loader": "nflreadpy.load_ff_rankings", "restricted": True, "fetched_at": FETCHED},
    )
    # Sleeper players cache layout: snapshot=<YYYYMMDD>/players.parquet + LATEST
    sleeper_dir = settings.raw_dir / "sleeper_players" / "snapshot=20261101"
    sleeper_dir.mkdir(parents=True)
    pl.DataFrame({"sleeper_id": ["4046"], "full_name": ["Patrick Mahomes"]}).write_parquet(
        sleeper_dir / "players.parquet"
    )
    (sleeper_dir / "_manifest.json").write_text(
        json.dumps(
            {"fetched_at": FETCHED, "rows": 1, "source": "Sleeper API", "terms": "non-commercial"}
        ),
        encoding="utf-8",
    )
    (settings.raw_dir / "sleeper_players" / "LATEST").write_text("20261101\n", encoding="utf-8")

    processed.write_snapshot(
        "fact_injury_report",
        {
            2024: pl.DataFrame(
                {
                    "season": [2024, 2024],
                    "week": [1, 2],
                    "player_id": ["00-A", "00-B"],
                    "report_status": ["Out", None],
                    "snapshot_ts": [datetime(2026, 11, 1, tzinfo=UTC)] * 2,
                },
                schema_overrides={"season": pl.Int32, "week": pl.Int32},
            )
        },
        meta={"inputs": {"injuries": raw.latest_id("player_stats")}, "fetched_at": FETCHED},
    )
    processed.write_snapshot(
        "restricted/fact_ecr_weekly",
        {2024: pl.DataFrame({"season": [2024], "ecr": [1.0], "player_id": ["00-A"]})},
        meta={"restricted": True, "fetched_at": FETCHED},
    )
    return raw, processed


def test_is_restricted_dataset():
    assert is_restricted_dataset("restricted/ff_rankings_all")
    assert is_restricted_dataset("restricted/fact_ecr_weekly")
    assert not is_restricted_dataset("player_stats")
    assert not is_restricted_dataset("unrestricted_data")


def test_rebuild_warehouse_views_and_meta(tmp_settings):
    make_tiny_lake(tmp_settings)
    path = tmp_settings.data_dir / "warehouse.duckdb"
    assert rebuild_warehouse(path, settings=tmp_settings) == path
    assert list_views(path) == [
        "fact_injury_report",
        "raw_depth_charts",
        "raw_player_stats",
        "raw_sleeper_players",
        "raw_teams",
    ]
    con = duckdb.connect(str(path), read_only=True)
    try:
        sqls = [
            r[0]
            for r in con.execute("SELECT sql FROM duckdb_views() WHERE NOT internal").fetchall()
        ]
        assert sqls and all("restricted" not in sql for sql in sqls)
        assert con.execute("SELECT count(*) FROM raw_player_stats").fetchone()[0] == 7
        seasons = con.execute("SELECT DISTINCT season FROM raw_player_stats ORDER BY 1").fetchall()
        assert [r[0] for r in seasons] == [2024, 2025]
        # hive season fills the partition that has no season column
        assert con.execute("SELECT count(season) FROM raw_depth_charts").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM raw_teams").fetchone()[0] == 2
        assert con.execute("SELECT count(*) FROM raw_sleeper_players").fetchone()[0] == 1
        assert con.execute("SELECT count(*) FROM fact_injury_report").fetchone()[0] == 2
        meta = con.execute(
            "SELECT kind, name, snapshot_id, fetched_at, rows FROM meta_snapshots ORDER BY kind, name"
        ).fetchall()
    finally:
        con.close()
    kinds = {row[0] for row in meta}
    names = {row[1] for row in meta}
    assert kinds == {"raw", "processed"}
    assert names == {
        "player_stats",
        "depth_charts",
        "teams",
        "sleeper_players",
        "fact_injury_report",
    }
    by_name = {row[1]: row for row in meta}
    assert by_name["player_stats"][4] == 7 and by_name["player_stats"][3] == FETCHED
    assert by_name["sleeper_players"][2] == "20261101" and by_name["sleeper_players"][4] == 1


def test_rebuild_warehouse_is_idempotent_and_closes_file(tmp_settings):
    make_tiny_lake(tmp_settings)
    path = tmp_settings.data_dir / "warehouse.duckdb"
    rebuild_warehouse(path, settings=tmp_settings)
    first = list_views(path)
    rebuild_warehouse(path, settings=tmp_settings)
    assert list_views(path) == first
    leftovers = sorted(p.name for p in path.parent.iterdir() if p.name.startswith("warehouse"))
    assert leftovers == ["warehouse.duckdb"]
    path.unlink()  # would fail on Windows if a connection were still open
    assert not path.exists()


def test_data_dictionary_sections(tmp_settings):
    raw, processed = make_tiny_lake(tmp_settings)
    text = render_data_dictionary(raw, processed, generated_at=datetime(2026, 11, 2, tzinfo=UTC))
    assert text.startswith("# Data Dictionary")
    assert "## Processed tables" in text
    assert "### fact_injury_report" in text
    assert "| `player_id` | String | 0.0% | `00-A` |" in text
    assert "| `report_status` | String | 50.0% | `Out` |" in text
    assert "## Raw datasets" in text
    assert (
        "| `player_stats` | `nflreadpy.load_player_stats` | 2024-2025 | 7 | CC-BY-4.0 (nflverse) |"
        in text
    )
    assert "| `teams` | `nflreadpy.load_teams` | all | 2 |" in text
    assert (
        "| `sleeper_players` | `Sleeper API` | daily (" not in text
    )  # manifest has no snapshot_date
    assert "| `sleeper_players` | `Sleeper API` |" in text
    # restricted data: one line, no column sections, no raw-table row
    assert "### fact_ecr_weekly" not in text
    assert "| `restricted/ff_rankings_all` |" not in text
    assert "## Restricted data (D-007)" in text
    assert "`restricted/fact_ecr_weekly`" in text and "`restricted/ff_rankings_all`" in text

    out = write_data_dictionary(
        tmp_settings.data_dir / "docs" / "DATA_DICTIONARY.md", settings=tmp_settings
    )
    body = out.read_bytes()
    assert b"\r\n" not in body
    assert b"### fact_injury_report" in body
