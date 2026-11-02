"""Versioned Parquet snapshot store for the raw and processed data tiers.

Layout (docs/DATA_SCHEMA.md section 1)::

    <root>/<dataset>/snapshot=<YYYYMMDDTHHMMSSZ>/season=<key>/part-0.parquet
    <root>/<dataset>/snapshot=<...>/_manifest.json
    <root>/<dataset>/LATEST            text file holding the newest snapshot id

``<dataset>`` may contain a slash (``restricted/<name>``) so that restricted
sources live under a path that visibly says so. ``<key>`` is a season (int) or
the literal ``all`` for non-seasonal datasets.

Write protocol: every partition and the manifest are written into a hidden
staging directory next to the snapshots, the staging directory is renamed into
place in one step, and only then is ``LATEST`` replaced through a temp file. A
crash at any point therefore leaves either the previous ``LATEST`` intact or a
complete new snapshot; a half-written snapshot is never pointed to.

Snapshot ids are UTC timestamps at second resolution. When two snapshots of
the same dataset are created within one second (or the clock steps backwards)
a zero-padded counter suffix (``-001``) keeps ids strictly increasing and
lexicographically sortable.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import re
import shutil
from collections.abc import Iterable, Mapping, Sequence
from datetime import UTC, datetime
from importlib import metadata
from pathlib import Path
from typing import Any

import polars as pl

log = logging.getLogger(__name__)

MANIFEST_NAME = "_manifest.json"
LATEST_NAME = "LATEST"
PART_NAME = "part-0.parquet"
SNAPSHOT_PREFIX = "snapshot="
SEASON_PREFIX = "season="
ALL_KEY = "all"
PARQUET_COMPRESSION = "zstd"

_STAGING_PREFIX = ".staging-"
_ID_FORMAT = "%Y%m%dT%H%M%SZ"
_SNAPSHOT_ID_RE = re.compile(r"^(\d{8}T\d{6}Z)(?:-(\d{3,}))?$")
_MAX_COUNTER = 999

PartitionKey = int | str
SchemaLike = pl.DataFrame | pl.LazyFrame | pl.Schema | Mapping[str, Any]


# --------------------------------------------------------------------------- #
# Schema helpers
# --------------------------------------------------------------------------- #


def schema_items(schema: SchemaLike) -> list[tuple[str, str]]:
    """Ordered ``(column name, dtype string)`` pairs for a frame, schema, or mapping."""
    if isinstance(schema, pl.DataFrame):
        schema = schema.schema
    elif isinstance(schema, pl.LazyFrame):
        schema = schema.collect_schema()
    return [(str(name), str(dtype)) for name, dtype in schema.items()]


def schema_hash(schema: SchemaLike) -> str:
    """SHA-256 over the ordered list of ``(column name, dtype string)`` pairs.

    The hash is order-sensitive on purpose: a reordered column set is a schema
    change that the drift check reports (as a warning, not a failure).
    """
    payload = json.dumps(schema_items(schema), separators=(",", ":")).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _sha256_file(path: Path, chunk_size: int = 1 << 20) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(chunk_size), b""):
            digest.update(block)
    return digest.hexdigest()


def _nflreadpy_version() -> str | None:
    try:
        return metadata.version("nflreadpy")
    except metadata.PackageNotFoundError:  # pragma: no cover - always installed here
        return None


def _utc_now() -> datetime:
    return datetime.now(UTC)


def _utc_now_iso() -> str:
    return _utc_now().replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_text_atomic(path: Path, text: str) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(text, encoding="utf-8")
    os.replace(tmp, path)


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    _write_text_atomic(path, json.dumps(payload, indent=2, sort_keys=False, default=str) + "\n")


# --------------------------------------------------------------------------- #
# Partition keys and snapshot ids
# --------------------------------------------------------------------------- #


def partition_key_to_str(key: PartitionKey) -> str:
    """Render a partition key the way it appears in the directory name."""
    if isinstance(key, bool):
        raise TypeError("partition key must be an int season or 'all', not bool")
    if isinstance(key, int):
        return str(key)
    if key == ALL_KEY:
        return ALL_KEY
    raise ValueError(f"partition key must be an int season or {ALL_KEY!r}, got {key!r}")


def parse_partition_key(text: str) -> PartitionKey:
    return int(text) if text.isdigit() else text


def _partition_sort_key(key: PartitionKey) -> tuple[int, Any]:
    return (0, key) if isinstance(key, int) else (1, str(key))


def split_snapshot_id(snapshot_id: str) -> tuple[str, int]:
    """Return ``(timestamp part, counter)`` for a snapshot id; counter is 0 when absent."""
    match = _SNAPSHOT_ID_RE.match(snapshot_id)
    if match is None:
        raise ValueError(f"malformed snapshot id: {snapshot_id!r}")
    return match.group(1), int(match.group(2) or 0)


def is_snapshot_id(text: str) -> bool:
    return _SNAPSHOT_ID_RE.match(text) is not None


# --------------------------------------------------------------------------- #
# Writer
# --------------------------------------------------------------------------- #


class SnapshotWriter:
    """Incrementally writes the partitions of one snapshot.

    Nothing is visible to readers until :meth:`commit`; :meth:`abort` discards
    the staging directory. Obtain one via :meth:`SnapshotStore.begin_snapshot`.
    """

    def __init__(self, store: SnapshotStore, dataset: str) -> None:
        self.store = store
        self.dataset = dataset
        self.snapshot_id = store._new_snapshot_id(dataset)
        self.staging_dir = store.dataset_dir(dataset) / f"{_STAGING_PREFIX}{self.snapshot_id}"
        self.final_dir = store.snapshot_dir(dataset, self.snapshot_id)
        self._partitions: dict[PartitionKey, dict[str, Any]] = {}
        self._committed = False
        if self.staging_dir.exists():
            shutil.rmtree(self.staging_dir)
        self.staging_dir.mkdir(parents=True, exist_ok=False)

    @property
    def partitions(self) -> dict[PartitionKey, dict[str, Any]]:
        return dict(self._partitions)

    @property
    def committed(self) -> bool:
        return self._committed

    def write_partition(self, key: PartitionKey, df: pl.DataFrame) -> dict[str, Any]:
        """Write one partition (a season or ``"all"``) into the staging directory."""
        if self._committed:
            raise RuntimeError("snapshot already committed")
        if key in self._partitions:
            raise ValueError(f"partition {key!r} already written for this snapshot")
        key_str = partition_key_to_str(key)
        part_dir = self.staging_dir / f"{SEASON_PREFIX}{key_str}"
        part_dir.mkdir(parents=True, exist_ok=False)
        path = part_dir / PART_NAME
        df.write_parquet(path, compression=PARQUET_COMPRESSION)
        info = {
            "key": key_str,
            "rows": df.height,
            "schema": schema_items(df),
            "schema_hash": schema_hash(df),
            "file": f"{SEASON_PREFIX}{key_str}/{PART_NAME}",
            "file_sha256": _sha256_file(path),
            "bytes": path.stat().st_size,
        }
        self._partitions[key] = info
        return info

    def build_manifest(self, meta: Mapping[str, Any] | None = None) -> dict[str, Any]:
        """Assemble the manifest from the written partitions plus caller metadata.

        Reserved keys in ``meta`` (``loader``, ``loader_kwargs``, ``fetched_at``,
        ``nflreadpy_version``, ``source_license``, ``restricted``) fill the
        documented manifest fields; everything else is kept under ``extra``.
        """
        if not self._partitions:
            raise ValueError("no partitions written")
        extra = dict(meta or {})
        keys = sorted(self._partitions, key=_partition_sort_key)
        ordered = [self._partitions[k] for k in keys]
        reference = ordered[-1]  # newest season, or the single "all" partition
        hashes = {p["key"]: p["schema_hash"] for p in ordered}
        return {
            "dataset": self.dataset,
            "snapshot_id": self.snapshot_id,
            "loader": extra.pop("loader", None),
            "loader_kwargs": extra.pop("loader_kwargs", {}),
            "seasons": [k for k in keys if isinstance(k, int)],
            "partitions": [p["key"] for p in ordered],
            "fetched_at": extra.pop("fetched_at", None) or _utc_now_iso(),
            "written_at": _utc_now_iso(),
            "nflreadpy_version": extra.pop("nflreadpy_version", _nflreadpy_version()),
            "rows": {
                "total": sum(p["rows"] for p in ordered),
                "per_season": {p["key"]: p["rows"] for p in ordered},
            },
            "columns": dict(reference["schema"]),
            "schema_hash": reference["schema_hash"],
            "schema_partition": reference["key"],
            "schema_uniform": len(set(hashes.values())) == 1,
            "partition_schema_hashes": hashes,
            "file_sha256": {p["key"]: p["file_sha256"] for p in ordered},
            "files": {p["key"]: p["file"] for p in ordered},
            "bytes": {"total": sum(p["bytes"] for p in ordered)},
            "source_license": extra.pop("source_license", None),
            "restricted": bool(extra.pop("restricted", False)),
            "extra": extra,
        }

    def commit(self, meta: Mapping[str, Any] | None = None, *, update_latest: bool = True) -> str:
        """Write the manifest, move the snapshot into place, then advance ``LATEST``.

        ``update_latest=False`` keeps the snapshot readable by id without making
        it the default (used when the schema drift check fails).
        """
        if self._committed:
            raise RuntimeError("snapshot already committed")
        if not self._partitions:
            self.abort()
            raise ValueError("no partitions written; snapshot aborted")
        try:
            manifest = self.build_manifest(meta)
            self.store._write_manifest(self.staging_dir / MANIFEST_NAME, manifest)
            os.replace(self.staging_dir, self.final_dir)
        except BaseException:
            self.abort()
            raise
        self._committed = True
        if update_latest:
            self.store._write_latest(self.dataset, self.snapshot_id)
        return self.snapshot_id

    def abort(self) -> None:
        if not self._committed:
            shutil.rmtree(self.staging_dir, ignore_errors=True)


# --------------------------------------------------------------------------- #
# Store
# --------------------------------------------------------------------------- #


class SnapshotStore:
    """Snapshot store rooted at ``root`` (defaults to ``settings.raw_dir``).

    The same class serves ``processed_dir``; pass that path as ``root``.
    """

    def __init__(self, root: Path | str | None = None) -> None:
        if root is None:
            from ffml.config import get_settings

            root = get_settings().raw_dir
        self.root = Path(root)

    def __repr__(self) -> str:
        return f"SnapshotStore({str(self.root)!r})"

    # -- paths ------------------------------------------------------------- #

    def dataset_dir(self, dataset: str) -> Path:
        return self.root / dataset

    def snapshot_dir(self, dataset: str, snapshot_id: str) -> Path:
        return self.dataset_dir(dataset) / f"{SNAPSHOT_PREFIX}{snapshot_id}"

    def partition_path(self, dataset: str, snapshot_id: str, key: PartitionKey) -> Path:
        return (
            self.snapshot_dir(dataset, snapshot_id)
            / f"{SEASON_PREFIX}{partition_key_to_str(key)}"
            / PART_NAME
        )

    # -- discovery --------------------------------------------------------- #

    def list_datasets(self) -> list[str]:
        """Dataset keys (relative POSIX paths) that have a ``LATEST`` pointer."""
        if not self.root.exists():
            return []
        return sorted(
            p.parent.relative_to(self.root).as_posix() for p in self.root.rglob(LATEST_NAME)
        )

    def list_snapshots(self, dataset: str) -> list[str]:
        """Committed snapshot ids, oldest first (lexicographic order is chronological)."""
        ds_dir = self.dataset_dir(dataset)
        if not ds_dir.exists():
            return []
        ids = []
        for child in ds_dir.iterdir():
            if child.is_dir() and child.name.startswith(SNAPSHOT_PREFIX):
                sid = child.name[len(SNAPSHOT_PREFIX) :]
                if is_snapshot_id(sid):
                    ids.append(sid)
        return sorted(ids)

    def _staging_ids(self, dataset: str) -> list[str]:
        ds_dir = self.dataset_dir(dataset)
        if not ds_dir.exists():
            return []
        return [
            child.name[len(_STAGING_PREFIX) :]
            for child in ds_dir.iterdir()
            if child.is_dir()
            and child.name.startswith(_STAGING_PREFIX)
            and is_snapshot_id(child.name[len(_STAGING_PREFIX) :])
        ]

    def latest_id(self, dataset: str) -> str | None:
        path = self.dataset_dir(dataset) / LATEST_NAME
        if not path.exists():
            return None
        text = path.read_text(encoding="utf-8").strip()
        return text or None

    def resolve_snapshot_id(self, dataset: str, snapshot_id: str | None = None) -> str:
        sid = snapshot_id or self.latest_id(dataset)
        if sid is None:
            raise FileNotFoundError(f"no snapshot of {dataset!r} under {self.root}")
        if not self.snapshot_dir(dataset, sid).is_dir():
            raise FileNotFoundError(f"snapshot {sid} of {dataset!r} not found under {self.root}")
        return sid

    def partition_keys(self, dataset: str, snapshot_id: str | None = None) -> list[PartitionKey]:
        sid = self.resolve_snapshot_id(dataset, snapshot_id)
        keys = []
        for child in self.snapshot_dir(dataset, sid).iterdir():
            if child.is_dir() and child.name.startswith(SEASON_PREFIX):
                keys.append(parse_partition_key(child.name[len(SEASON_PREFIX) :]))
        return sorted(keys, key=_partition_sort_key)

    # -- reading ----------------------------------------------------------- #

    def read_manifest(self, dataset: str, snapshot_id: str | None = None) -> dict[str, Any]:
        sid = self.resolve_snapshot_id(dataset, snapshot_id)
        path = self.snapshot_dir(dataset, sid) / MANIFEST_NAME
        return json.loads(path.read_text(encoding="utf-8"))

    def read(
        self,
        dataset: str,
        seasons: Iterable[PartitionKey] | None = None,
        snapshot_id: str | None = None,
        *,
        columns: Sequence[str] | None = None,
        how: str = "vertical_relaxed",
    ) -> pl.DataFrame:
        """Read and concatenate partitions of a snapshot (``None`` means ``LATEST``).

        ``seasons`` filters by partition key; requested seasons that the snapshot
        lacks are skipped (debug-logged) and an error is raised only when nothing
        matches. Partitions are concatenated with ``how`` (default
        ``vertical_relaxed``); when their column sets differ (depth charts before
        and after 2025) the read falls back to ``diagonal_relaxed`` with a warning.
        """
        sid = self.resolve_snapshot_id(dataset, snapshot_id)
        available = self.partition_keys(dataset, sid)
        if seasons is None:
            wanted = available
        else:
            requested = list(seasons)
            wanted = [k for k in available if k in requested]
            missing = [k for k in requested if k not in available]
            if missing:
                log.debug("%s snapshot %s has no partitions for %s", dataset, sid, missing)
        if not wanted:
            raise FileNotFoundError(
                f"no matching partitions for {dataset!r} snapshot {sid} (requested {seasons!r})"
            )
        frames = [
            pl.read_parquet(self.partition_path(dataset, sid, key), columns=columns)
            for key in wanted
        ]
        if len(frames) == 1:
            return frames[0]
        try:
            return pl.concat(frames, how=how)
        except pl.exceptions.PolarsError as exc:
            if how.startswith("diagonal"):
                raise
            log.warning(
                "%s snapshot %s: partitions have different columns (%s); using diagonal_relaxed",
                dataset,
                sid,
                str(exc).splitlines()[0],
            )
            return pl.concat(frames, how="diagonal_relaxed")

    # -- writing ----------------------------------------------------------- #

    def begin_snapshot(self, dataset: str) -> SnapshotWriter:
        """Start a snapshot that is written partition by partition (see SnapshotWriter)."""
        return SnapshotWriter(self, dataset)

    def write_snapshot(
        self,
        dataset: str,
        frames: Mapping[PartitionKey, pl.DataFrame],
        meta: Mapping[str, Any] | None = None,
    ) -> str:
        """Write all ``frames`` (keyed by season or ``"all"``) as one snapshot.

        Returns the new snapshot id. ``LATEST`` is updated last.
        """
        if not frames:
            raise ValueError("frames is empty")
        writer = self.begin_snapshot(dataset)
        try:
            for key in sorted(frames, key=_partition_sort_key):
                writer.write_partition(key, frames[key])
        except BaseException:
            writer.abort()
            raise
        return writer.commit(meta)

    def prune(self, dataset: str, keep: int = 8) -> list[str]:
        """Delete all but the newest ``keep`` snapshots; never deletes the LATEST one.

        Returns the ids that were removed.
        """
        if keep < 1:
            raise ValueError("keep must be at least 1")
        ids = self.list_snapshots(dataset)
        protected = set(ids[-keep:])
        latest = self.latest_id(dataset)
        if latest is not None:
            protected.add(latest)
        removed = []
        for sid in ids:
            if sid in protected:
                continue
            shutil.rmtree(self.snapshot_dir(dataset, sid))
            removed.append(sid)
        if removed:
            log.info("pruned %d snapshot(s) of %s: %s", len(removed), dataset, ", ".join(removed))
        return removed

    # -- internals --------------------------------------------------------- #

    def _new_snapshot_id(self, dataset: str) -> str:
        candidate = _utc_now().strftime(_ID_FORMAT)
        existing = self.list_snapshots(dataset) + self._staging_ids(dataset)
        if not existing:
            return candidate
        newest = max(existing)
        if candidate > newest:
            return candidate
        base, counter = split_snapshot_id(newest)
        if counter >= _MAX_COUNTER:
            raise RuntimeError(f"too many snapshots of {dataset!r} within one second")
        return f"{base}-{counter + 1:03d}"

    def _write_manifest(self, path: Path, manifest: Mapping[str, Any]) -> None:
        _write_json_atomic(path, manifest)

    def _write_latest(self, dataset: str, snapshot_id: str) -> None:
        ds_dir = self.dataset_dir(dataset)
        ds_dir.mkdir(parents=True, exist_ok=True)
        _write_text_atomic(ds_dir / LATEST_NAME, snapshot_id + "\n")


__all__ = [
    "ALL_KEY",
    "LATEST_NAME",
    "MANIFEST_NAME",
    "PART_NAME",
    "PartitionKey",
    "SnapshotStore",
    "SnapshotWriter",
    "is_snapshot_id",
    "parse_partition_key",
    "partition_key_to_str",
    "schema_hash",
    "schema_items",
    "split_snapshot_id",
]
