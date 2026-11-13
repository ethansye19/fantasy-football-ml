"""Replay one recorded build (runs inside the pinned interpreter, FFML_DATA_DIR = the replay root).

Every snapshot read is pinned: a dataset the commitment records gets the recorded id; any other dataset gets
the newest snapshot created at or before the recorded build time (snapshot ids are creation stamps and are
never pruned). The clock is the recorded build time. No frozen code is changed: build_week's own test hooks
and a process-local wrapper around SnapshotStore.latest_id do the pinning.
"""
from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path

spec = json.loads(Path(sys.argv[1]).read_text(encoding="utf-8"))
as_of = datetime.fromisoformat(spec["as_of"])
stamp = as_of.strftime("%Y%m%dT%H%M%SZ")

import ffml  # noqa: E402
from ffml.config import get_settings  # noqa: E402
from ffml.data.store import SnapshotStore  # noqa: E402

S = get_settings()
assert Path(S.data_dir).resolve() == Path(spec["root"]).resolve(), (S.data_dir, spec["root"])
assert Path(spec["pinned_worktree"]).resolve() in Path(ffml.__file__).resolve().parents, ffml.__file__
kinds = {Path(S.processed_dir).resolve(): "processed", Path(S.features_dir).resolve(): "features",
         Path(S.raw_dir).resolve(): "raw"}
reads: list[dict] = []
_orig = SnapshotStore.latest_id


def pinned_latest_id(self, dataset):
    kind = kinds.get(Path(self.root).resolve(), str(self.root))
    rec = (spec["pins"].get(kind) or {}).get(dataset)
    if rec:
        sid, how = rec, "recorded"
    else:
        d = self.dataset_dir(dataset)
        ids = sorted(p.name.split("=", 1)[1] for p in d.iterdir() if p.is_dir() and p.name.startswith("snapshot=")) \
            if d.exists() else []
        before = [i for i in ids if i[:16] <= stamp]
        sid, how = (before[-1] if before else None), "newest at or before as_of"
    reads.append({"store": kind, "dataset": dataset, "snapshot": sid, "how": how, "latest_now": _orig(self, dataset)})
    return sid


SnapshotStore.latest_id = pinned_latest_id

from ffml.sim.livetest import build_week  # noqa: E402

res = build_week(int(spec["season"]), int(spec["week"]), rehearsal=True, clock=lambda: as_of)
uniq = {(r["store"], r["dataset"], r["snapshot"], r["how"], r["latest_now"]) for r in reads}
out = {"model_version": res.model_version, "commitment": str(res.commitment), "digest": res.digest,
       "reads": [dict(zip(("store", "dataset", "snapshot", "how", "latest_now"), u, strict=True)) for u in sorted(uniq)]}
Path(spec["result"]).write_text(json.dumps(out, indent=1), encoding="utf-8")
print("replayed", res.model_version, "digest", res.digest)
