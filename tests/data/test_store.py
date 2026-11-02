"""Tests for the Parquet snapshot store (DE-2.1)."""

from __future__ import annotations

import hashlib
import json
from datetime import datetime
from importlib import metadata

import polars as pl
import pytest
from freezegun import freeze_time

from ffml.data.store import (
    LATEST_NAME,
    MANIFEST_NAME,
    PART_NAME,
    SnapshotStore,
    schema_hash,
    split_snapshot_id,
)


def frame(season: int, n: int = 3) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "season": [season] * n,
            "x": list(range(n)),
            "name": [f"p{i}" for i in range(n)],
        }
    )


@pytest.fixture
def store(tmp_path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "raw")


def test_round_trip_and_layout(store: SnapshotStore) -> None:
    sid = store.write_snapshot("demo", {2024: frame(2024), 2025: frame(2025, 2)}, {"loader": "x"})

    base = store.root / "demo" / f"snapshot={sid}"
    assert (base / "season=2024" / PART_NAME).exists()
    assert (base / "season=2025" / PART_NAME).exists()
    assert (base / MANIFEST_NAME).exists()
    assert (store.root / "demo" / LATEST_NAME).read_text(encoding="utf-8").strip() == sid
    assert store.latest_id("demo") == sid
    assert store.list_snapshots("demo") == [sid]
    assert store.partition_keys("demo") == [2024, 2025]

    out = store.read("demo")
    assert out.height == 5
    assert out["season"].to_list() == [2024, 2024, 2024, 2025, 2025]
    assert out.columns == ["season", "x", "name"]


def test_read_with_season_filter(store: SnapshotStore) -> None:
    first = store.write_snapshot("demo", {2024: frame(2024)})
    second = store.write_snapshot("demo", {2024: frame(2024), 2025: frame(2025, 2)})

    assert store.read("demo", seasons=[2025]).height == 2
    # seasons the snapshot lacks are skipped as long as something matches
    assert store.read("demo", seasons=[2023, 2025]).height == 2
    with pytest.raises(FileNotFoundError):
        store.read("demo", seasons=[2023])
    # explicit older snapshot
    assert store.read("demo", snapshot_id=first).height == 3
    assert store.read("demo", snapshot_id=second).height == 5
    # column projection
    assert store.read("demo", columns=["x"]).columns == ["x"]


def test_non_seasonal_all_partition(store: SnapshotStore) -> None:
    sid = store.write_snapshot("dim", {"all": frame(0, 4)})
    assert (store.root / "dim" / f"snapshot={sid}" / "season=all" / PART_NAME).exists()
    manifest = store.read_manifest("dim")
    assert manifest["seasons"] == []
    assert manifest["partitions"] == ["all"]
    assert manifest["rows"] == {"total": 4, "per_season": {"all": 4}}
    assert store.read("dim", seasons=["all"]).height == 4


def test_manifest_contents(store: SnapshotStore) -> None:
    df24, df25 = frame(2024), frame(2025, 2)
    meta = {
        "loader": "nflreadpy.load_demo",
        "loader_kwargs": {"summary_level": "week"},
        "source_license": "CC-BY-4.0 (nflverse)",
        "fetched_at": "2026-11-01T12:00:00Z",
        "note": "extra field",
    }
    sid = store.write_snapshot("demo", {2025: df25, 2024: df24}, meta)
    manifest = store.read_manifest("demo")

    assert manifest["dataset"] == "demo"
    assert manifest["snapshot_id"] == sid
    assert manifest["loader"] == "nflreadpy.load_demo"
    assert manifest["loader_kwargs"] == {"summary_level": "week"}
    assert manifest["seasons"] == [2024, 2025]
    assert manifest["fetched_at"] == "2026-11-01T12:00:00Z"
    datetime.fromisoformat(manifest["written_at"].replace("Z", "+00:00"))
    assert manifest["nflreadpy_version"] == metadata.version("nflreadpy")
    assert manifest["rows"] == {"total": 5, "per_season": {"2024": 3, "2025": 2}}
    assert manifest["columns"] == {"season": "Int64", "x": "Int64", "name": "String"}
    assert manifest["schema_hash"] == schema_hash(df25)
    assert manifest["schema_uniform"] is True
    assert manifest["partition_schema_hashes"] == {
        "2024": schema_hash(df24),
        "2025": schema_hash(df25),
    }
    assert manifest["source_license"] == "CC-BY-4.0 (nflverse)"
    assert manifest["restricted"] is False
    assert manifest["extra"] == {"note": "extra field"}

    for key, rel in manifest["files"].items():
        path = store.root / "demo" / f"snapshot={sid}" / rel
        assert manifest["file_sha256"][key] == hashlib.sha256(path.read_bytes()).hexdigest()


def test_latest_pointer_is_updated_last(store: SnapshotStore, monkeypatch) -> None:
    sid1 = store.write_snapshot("demo", {2024: frame(2024)})

    # A crash while writing the manifest leaves LATEST untouched and no snapshot behind.
    def boom(self, path, manifest):
        raise OSError("disk full")

    monkeypatch.setattr(SnapshotStore, "_write_manifest", boom)
    with pytest.raises(OSError):
        store.write_snapshot("demo", {2024: frame(2024), 2025: frame(2025)})
    assert store.latest_id("demo") == sid1
    assert store.list_snapshots("demo") == [sid1]
    assert not [p for p in (store.root / "demo").iterdir() if p.name.startswith(".staging")]
    monkeypatch.undo()

    # When LATEST is written the final snapshot directory is already complete.
    seen: dict[str, object] = {}
    original = SnapshotStore._write_latest

    def spy(self, dataset, snapshot_id):
        final = self.snapshot_dir(dataset, snapshot_id)
        seen["manifest"] = (final / MANIFEST_NAME).exists()
        seen["partition"] = (final / "season=2025" / PART_NAME).exists()
        seen["latest_before"] = self.latest_id(dataset)
        return original(self, dataset, snapshot_id)

    monkeypatch.setattr(SnapshotStore, "_write_latest", spy)
    sid2 = store.write_snapshot("demo", {2024: frame(2024), 2025: frame(2025)})
    assert seen == {"manifest": True, "partition": True, "latest_before": sid1}
    assert store.latest_id("demo") == sid2


def test_snapshot_ids_are_strictly_increasing(store: SnapshotStore) -> None:
    with freeze_time("2026-11-01 12:00:00"):
        a = store.write_snapshot("demo", {2024: frame(2024)})
        b = store.write_snapshot("demo", {2024: frame(2024)})
        c = store.write_snapshot("demo", {2024: frame(2024)})
    assert a == "20261101T120000Z"
    assert b == "20261101T120000Z-001"
    assert c == "20261101T120000Z-002"
    with freeze_time("2026-11-01 12:00:01"):
        d = store.write_snapshot("demo", {2024: frame(2024)})
    assert d == "20261101T120001Z"
    # the clock stepping backwards still yields a larger id
    with freeze_time("2026-11-01 11:00:00"):
        e = store.write_snapshot("demo", {2024: frame(2024)})
    assert e == "20261101T120001Z-001"
    ids = [a, b, c, d, e]
    assert ids == sorted(ids)
    assert store.list_snapshots("demo") == ids
    assert store.latest_id("demo") == e
    assert split_snapshot_id(e) == ("20261101T120001Z", 1)


def test_prune_keeps_newest_and_latest(store: SnapshotStore) -> None:
    ids = [store.write_snapshot("demo", {2024: frame(2024)}) for _ in range(4)]
    # point LATEST at the oldest snapshot to prove prune never deletes it
    store._write_latest("demo", ids[0])

    removed = store.prune("demo", keep=2)
    assert removed == [ids[1]]
    assert store.list_snapshots("demo") == [ids[0], ids[2], ids[3]]
    assert store.latest_id("demo") == ids[0]
    assert store.read("demo").height == 3

    assert store.prune("demo") == []  # default keep=8 removes nothing
    with pytest.raises(ValueError):
        store.prune("demo", keep=0)


def test_schema_hash_stability() -> None:
    df = pl.DataFrame({"a": [1], "b": ["x"]})
    h = schema_hash(df)
    assert len(h) == 64
    assert h == schema_hash(pl.DataFrame({"a": [2, 3], "b": ["y", "z"]}))  # values irrelevant
    assert h == schema_hash(df.schema)
    assert h == schema_hash({"a": "Int64", "b": "String"})  # mapping form (pins, manifests)
    expected = hashlib.sha256(
        json.dumps([["a", "Int64"], ["b", "String"]], separators=(",", ":")).encode()
    ).hexdigest()
    assert h == expected
    assert h != schema_hash(df.select("b", "a"))  # order matters
    assert h != schema_hash(df.with_columns(pl.col("a").cast(pl.Int32)))  # dtype matters
    assert h != schema_hash(df.rename({"b": "c"}))  # names matter


def test_read_falls_back_to_diagonal_when_columns_differ(store: SnapshotStore, caplog) -> None:
    old = pl.DataFrame({"season": [2024], "a": [1]})
    new = pl.DataFrame({"season": [2025], "b": ["x"]})
    store.write_snapshot("depth", {2024: old, 2025: new})
    with caplog.at_level("WARNING"):
        out = store.read("depth")
    assert out.columns == ["season", "a", "b"]
    assert out.height == 2
    assert "diagonal_relaxed" in caplog.text
    assert store.read_manifest("depth")["schema_uniform"] is False


def test_restricted_dataset_path(store: SnapshotStore) -> None:
    sid = store.write_snapshot("restricted/ff", {"all": frame(0)}, {"restricted": True})
    assert "restricted" in str(store.snapshot_dir("restricted/ff", sid))
    assert store.read_manifest("restricted/ff")["restricted"] is True
    assert store.list_datasets() == ["restricted/ff"]


def test_errors(store: SnapshotStore) -> None:
    with pytest.raises(ValueError):
        store.write_snapshot("demo", {})
    with pytest.raises(FileNotFoundError):
        store.read("missing")
    assert store.latest_id("missing") is None
    assert store.list_snapshots("missing") == []
    store.write_snapshot("demo", {2024: frame(2024)})
    with pytest.raises(FileNotFoundError):
        store.read_manifest("demo", snapshot_id="20000101T000000Z")
    with pytest.raises(ValueError):
        store.write_snapshot("demo", {"week": frame(2024)})
