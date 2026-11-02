"""Data layer: snapshot store, nflverse ingestion, and (later) processed tables.

Heavy modules (``nflverse``, ``ingest``) import nflreadpy and are not imported
here; use ``from ffml.data.ingest import run_ingest`` etc. directly.
"""

from ffml.data.store import SnapshotStore, SnapshotWriter, schema_hash, schema_items

__all__ = ["SnapshotStore", "SnapshotWriter", "schema_hash", "schema_items"]
