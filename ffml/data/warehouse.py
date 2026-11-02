"""DuckDB warehouse: views over the LATEST snapshots of the Parquet lake.

``rebuild_warehouse`` creates (or replaces) the single file at
``settings.warehouse_path`` (``data/warehouse.duckdb``, disposable; safe to
delete) with

* one view ``raw_<dataset>`` per non-restricted raw dataset over the
  partitions of its LATEST snapshot;
* one view ``<table>`` per non-restricted processed table over its LATEST
  snapshot;
* a table ``meta_snapshots(kind, name, snapshot_id, fetched_at, rows)``
  listing what each view points at.

RESTRICTED DATA (DECISIONS D-007): nothing under a ``restricted`` directory
(``data/raw/restricted/...``, ``data/processed/restricted/...``) ever gets a
view, and it is left out of ``meta_snapshots`` too. The warehouse is the
ad-hoc analysis entry point, so the restricted FantasyPros archive and the
``fact_ecr_weekly`` table derived from it must not be reachable through it.

Seasonal snapshots are read with DuckDB hive partitioning so that the
``season=<YYYY>`` directory supplies a ``season`` column where the files lack
one (timestamped depth charts); DuckDB merges it with an existing ``season``
column otherwise. ``season=all`` snapshots are read as plain file lists.
``union_by_name`` tolerates per-season column differences.

The build writes to a temporary file next to the target and renames it into
place, so readers never see a half-built warehouse, and the connection is
closed before the function returns (nothing holds the file open).
"""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path, PurePosixPath

import duckdb
import polars as pl

from ffml.config import Settings, get_settings
from ffml.data.store import SEASON_PREFIX, SnapshotStore

log = logging.getLogger(__name__)

RESTRICTED_DIR = "restricted"
RAW_VIEW_PREFIX = "raw_"
META_TABLE = "meta_snapshots"


def is_restricted_dataset(dataset: str) -> bool:
    """True when any path component of the dataset key is ``restricted`` (D-007)."""
    return RESTRICTED_DIR in PurePosixPath(dataset).parts


def view_name_for(dataset: str, prefix: str = "") -> str:
    return prefix + dataset.replace("/", "_")


def _sql_string(text: str) -> str:
    return "'" + text.replace("'", "''") + "'"


def _sql_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def snapshot_parquet_files(
    store: SnapshotStore, dataset: str, snapshot_id: str
) -> tuple[list[str], bool]:
    """Absolute forward-slash paths of a snapshot's parquet files and whether to hive-partition.

    Hive partitioning is used only when every file sits in an integer
    ``season=<YYYY>`` directory; ``season=all`` and flat layouts (the Sleeper
    players cache) are read as plain file lists.
    """
    snapshot_dir = store.snapshot_dir(dataset, snapshot_id)
    files = sorted(p for p in snapshot_dir.rglob("*.parquet") if not p.name.startswith("."))
    hive = bool(files) and all(
        p.parent.name.startswith(SEASON_PREFIX) and p.parent.name[len(SEASON_PREFIX) :].isdigit()
        for p in files
    )
    return [p.resolve().as_posix() for p in files], hive


def read_parquet_sql(files: list[str], hive: bool) -> str:
    file_list = "[" + ", ".join(_sql_string(f) for f in files) + "]"
    flag = "true" if hive else "false"
    return f"read_parquet({file_list}, union_by_name=true, hive_partitioning={flag})"


def _manifest_summary(
    store: SnapshotStore, dataset: str, snapshot_id: str
) -> tuple[str | None, int | None]:
    try:
        manifest = store.read_manifest(dataset, snapshot_id)
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None, None
    rows = manifest.get("rows")
    if isinstance(rows, dict):
        rows = rows.get("total")
    fetched = manifest.get("fetched_at") or manifest.get("written_at")
    return (str(fetched) if fetched else None), (int(rows) if rows is not None else None)


def _unlink_quietly(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def rebuild_warehouse(path: Path | str | None = None, *, settings: Settings | None = None) -> Path:
    """Create or replace the warehouse file; returns its path. Idempotent."""
    settings = settings or get_settings()
    target = Path(path) if path is not None else settings.warehouse_path
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(target.name + ".tmp")
    _unlink_quietly(tmp)
    _unlink_quietly(tmp.with_name(tmp.name + ".wal"))

    stores = (
        ("raw", SnapshotStore(settings.raw_dir), RAW_VIEW_PREFIX),
        ("processed", SnapshotStore(settings.processed_dir), ""),
    )
    meta: list[tuple[str, str, str, str | None, int | None]] = []
    views: list[str] = []
    con = duckdb.connect(str(tmp))
    try:
        for kind, store, prefix in stores:
            for dataset in store.list_datasets():
                if is_restricted_dataset(dataset):
                    # D-007: restricted data never gets a view and is not listed.
                    log.debug("warehouse: skipping restricted %s dataset %s", kind, dataset)
                    continue
                snapshot_id = store.latest_id(dataset)
                if snapshot_id is None:
                    continue
                files, hive = snapshot_parquet_files(store, dataset, snapshot_id)
                if not files:
                    log.warning(
                        "warehouse: %s %s snapshot %s has no parquet files",
                        kind,
                        dataset,
                        snapshot_id,
                    )
                    continue
                view = view_name_for(dataset, prefix)
                con.execute(
                    f"CREATE OR REPLACE VIEW {_sql_ident(view)} AS SELECT * FROM {read_parquet_sql(files, hive)}"
                )
                fetched_at, rows = _manifest_summary(store, dataset, snapshot_id)
                meta.append((kind, dataset, snapshot_id, fetched_at, rows))
                views.append(view)

        meta_df = pl.DataFrame(
            meta,
            schema={
                "kind": pl.String,
                "name": pl.String,
                "snapshot_id": pl.String,
                "fetched_at": pl.String,
                "rows": pl.Int64,
            },
            orient="row",
        )
        con.register("_meta_src", meta_df.to_arrow())
        con.execute(
            f"CREATE OR REPLACE TABLE {_sql_ident(META_TABLE)} AS SELECT * FROM _meta_src ORDER BY kind, name"
        )
        con.unregister("_meta_src")
        con.execute("CHECKPOINT")
    finally:
        con.close()

    _unlink_quietly(target.with_name(target.name + ".wal"))
    os.replace(tmp, target)
    log.info("warehouse: wrote %s with %d views (%s)", target, len(views), ", ".join(views))
    return target


def list_views(path: Path | str | None = None, *, settings: Settings | None = None) -> list[str]:
    """Names of the user views in the warehouse (read-only connection, closed on return)."""
    settings = settings or get_settings()
    target = Path(path) if path is not None else settings.warehouse_path
    con = duckdb.connect(str(target), read_only=True)
    try:
        rows = con.execute(
            "SELECT view_name FROM duckdb_views() WHERE NOT internal ORDER BY view_name"
        ).fetchall()
    finally:
        con.close()
    return [r[0] for r in rows]


__all__ = [
    "META_TABLE",
    "RAW_VIEW_PREFIX",
    "is_restricted_dataset",
    "list_views",
    "read_parquet_sql",
    "rebuild_warehouse",
    "snapshot_parquet_files",
    "view_name_for",
]
