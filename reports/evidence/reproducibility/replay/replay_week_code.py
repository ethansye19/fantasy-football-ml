"""Replay a recorded build at its recorded code commit and compare every output with the recorded hashes.

usage: replay_week_code.py <recorded commitment.json> <clean worktree at the recorded commit> <new out dir> [<label>]

Builds before 7075790 store paths relative to PROJECT_ROOT (the folder that holds the imported ffml), so the
worktree's own data/ and artifacts/ (both gitignored there) are the replay stores: a stored "data/..." path then
names the same file as in the recorded build. The stores are copies; nothing under the real data/ or artifacts/
is written, and the real stores are fingerprinted before and after. The interpreter is the pinned venv (the
dependencies are the same at the recorded commit: pyproject and lock files have no diff).
"""
from __future__ import annotations

import hashlib
import json
import os
import shutil
import subprocess
import sys
import time
from pathlib import Path

REPO = Path(r"C:\Users\Ethan\fantasy-football-ml")
DATA, ARTS = REPO / "data", REPO / "artifacts"
PY = Path(r"C:\Users\Ethan\ffml-livetest-code") / ".venv" / "Scripts" / "python.exe"
DRIVER = Path(__file__).with_name("replay_driver_code.py")

rec_path, W, out = Path(sys.argv[1]), Path(sys.argv[2]), Path(sys.argv[3])
label = sys.argv[4] if len(sys.argv) > 4 else out.name
rec = json.loads(rec_path.read_text(encoding="utf-8"))
root = W / "data"


def sha(p: Path) -> str:
    return hashlib.sha256(p.read_bytes()).hexdigest()


def fingerprint(base: Path) -> str:
    h = hashlib.sha256()
    for p in sorted(x for x in base.rglob("*") if x.is_file()):
        st = p.stat()
        h.update(f"{p.relative_to(base).as_posix()}|{st.st_size}|{st.st_mtime_ns}\n".encode())
    return h.hexdigest()


def stored_to_path(stored: str, base: Path) -> Path:
    assert stored.startswith("data/"), stored
    return base / stored[len("data/"):]


def git(*a: str) -> str:
    return subprocess.run(["git", "-C", str(W), *a], capture_output=True, text=True, check=True).stdout.strip()


def free_gb() -> float:
    import ctypes

    class MS(ctypes.Structure):
        _fields_ = [("dwLength", ctypes.c_ulong), ("dwMemoryLoad", ctypes.c_ulong),
                    ("ullTotalPhys", ctypes.c_ulonglong), ("ullAvailPhys", ctypes.c_ulonglong),
                    ("ullTotalPageFile", ctypes.c_ulonglong), ("ullAvailPageFile", ctypes.c_ulonglong),
                    ("ullTotalVirtual", ctypes.c_ulonglong), ("ullAvailVirtual", ctypes.c_ulonglong),
                    ("ullAvailExtendedVirtual", ctypes.c_ulonglong)]
    m = MS()
    m.dwLength = ctypes.sizeof(MS)
    ctypes.windll.kernel32.GlobalMemoryStatusEx(ctypes.byref(m))
    return m.ullAvailPhys / 2**30


t0 = time.time()
if free_gb() < 8.0:
    print(f"[{label}] not started: {free_gb():.1f} GB free, below 8 GB (clean stop)")
    sys.exit(3)
assert git("rev-parse", "HEAD") == rec["code"]["commit"], "worktree is not at the recorded commit"
assert git("status", "--porcelain", "--untracked-files=all") == "", "worktree is not clean"
assert sorted(os.listdir(root)) == ["README.md"] and sorted(os.listdir(W / "artifacts")) == ["README.md"], \
    "worktree data/ or artifacts/ already holds files; use a new worktree"
assert not out.exists(), f"{out} exists; use a new folder"
out.mkdir(parents=True)
guard = {d: fingerprint(DATA / d) for d in ("processed", "features", "raw", "livetest")}
guard["artifacts"] = fingerprint(ARTS)
keep_readme = lambda src, names: ["README.md"] if Path(src) in (DATA, ARTS) else []  # noqa: E731
for d in ("features", "schema_pins"):
    shutil.copytree(DATA / d, root / d)
# the restricted (consensus archive) store is never copied: the build does not read it
shutil.copytree(DATA / "processed", root / "processed",
                ignore=lambda src, names: ["restricted"] if Path(src) == DATA / "processed" else [])
shutil.copytree(DATA / "backtests", root / "backtests",
                ignore=lambda src, names: ["livetest_comparators"] if Path(src) == DATA / "backtests" else [])
shutil.copytree(DATA / "raw" / "schedules", root / "raw" / "schedules")
shutil.copytree(ARTS, W / "artifacts", ignore=keep_readme, dirs_exist_ok=True)
# walk-forward parts that existed before the recorded build (sources that are not outputs of this build)
written = {o["path"] for o in rec["outputs"] if o["role"] == "walkforward_part"}
pre = [s for s in rec["calibration_history"]["sources"] if s["origin"] == "walkforward" and s["path"] not in written]
for s in pre:
    src, dst = stored_to_path(s["path"], DATA), stored_to_path(s["path"], root)
    dst.parent.mkdir(parents=True, exist_ok=True)
    shutil.copy2(src, dst)
    if src.with_suffix(".json").exists():
        shutil.copy2(src.with_suffix(".json"), dst.with_suffix(".json"))
# every calibration source the recorded build read, other than its own outputs, must be byte-identical now
src_check = [(s["path"], sha(stored_to_path(s["path"], root)) == s["sha256"])
             for s in rec["calibration_history"]["sources"] if s["path"] not in written]
bad_sources = [p for p, ok in src_check if not ok]
assert git("status", "--porcelain", "--untracked-files=all") == "", "copies made the worktree dirty"
print(f"[{label}] replay stores ready in {time.time() - t0:.0f}s; walk-forward parts copied: {len(pre)}; "
      f"calibration sources checked: {len(src_check)}, changed since the build: {len(bad_sources)}", flush=True)

pins = {"features": rec["snapshots"]["features"],
        "processed": {**rec["snapshots"]["processed_at_start"], **rec["snapshots"].get("processed_live_rows", {})}}
assert all(rec["snapshots"]["processed_at_start"].get(k, v) == v
           for k, v in rec["snapshots"].get("processed_live_rows", {}).items()), "live rows read other ids than the start"
spec = {"season": rec["season"], "week": rec["week"], "as_of": rec["as_of"], "pins": pins, "root": str(root),
        "pinned_worktree": str(W), "code_path": str(W), "result": str(out / "replay_result.json")}
(out / "replay_spec.json").write_text(json.dumps(spec, indent=1), encoding="utf-8")
env = {k: v for k, v in os.environ.items() if k not in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV", "FFML_HOLDOUT_UNLOCK")}
env.update(FFML_DATA_DIR=str(root), FFML_ARTIFACTS_DIR=str(W / "artifacts"), FFML_REPORTS_DIR=str(out / "_reports"),
           PYTHONUTF8="1", PYTHONIOENCODING="utf-8")
proc = subprocess.run([str(PY), "-I", str(DRIVER), str(out / "replay_spec.json")], cwd=str(W), env=env,
                      capture_output=True, text=True, encoding="utf-8", errors="replace")
(out / "replay_stdout.txt").write_text(proc.stdout + "\n--- stderr ---\n" + proc.stderr, encoding="utf-8")
print(f"[{label}] driver exit {proc.returncode} in {time.time() - t0:.0f}s: {proc.stdout.strip().splitlines()[-1:]}", flush=True)
if proc.returncode != 0:
    print(proc.stderr[-3000:])
    sys.exit(proc.returncode)

new = json.loads((root / "livetest" / "rehearsal" / f"{rec['season']}w{rec['week']:02d}" / "commitment.json").read_text(encoding="utf-8"))
result = json.loads((out / "replay_result.json").read_text(encoding="utf-8"))
rec_out = {o["path"]: o for o in rec["outputs"]}
new_out = {o["path"]: o for o in new["outputs"]}
same = [p for p in rec_out if p in new_out and new_out[p]["sha256"] == rec_out[p]["sha256"]]
diff = [p for p in rec_out if p in new_out and new_out[p]["sha256"] != rec_out[p]["sha256"]]
only = sorted(set(rec_out) ^ set(new_out))


def jdiff(a, b, path=""):
    if isinstance(a, dict) and isinstance(b, dict):
        return [d for k in sorted(set(a) | set(b)) for d in jdiff(a.get(k), b.get(k), f"{path}.{k}" if path else k)]
    if isinstance(a, list) and isinstance(b, list) and len(a) == len(b):
        return [d for i, (x, y) in enumerate(zip(a, b, strict=True)) for d in jdiff(x, y, f"{path}[{i}]")]
    return [] if a == b else [(path, str(a)[:90], str(b)[:90])]


report = {"label": label, "recorded": str(rec_path), "code": rec["code"]["commit"], "files": len(rec_out),
          "identical": len(same), "differ": [], "only_in_one": only, "calibration_sources_changed": bad_sources,
          "reads": result["reads"]}
for p in diff:
    entry = {"path": p, "role": rec_out[p]["role"]}
    a, b = stored_to_path(p, DATA), stored_to_path(p, root)
    if p.endswith(".json") and a.exists():
        entry["fields"] = jdiff(json.loads(a.read_text(encoding="utf-8")), json.loads(b.read_text(encoding="utf-8")))[:15]
    elif p.endswith(".parquet") and a.exists():
        import polars as pl
        fa, fb = pl.read_parquet(a), pl.read_parquet(b)
        entry["frames_equal"] = fa.equals(fb)
        if not entry["frames_equal"] and fa.columns == fb.columns and fa.height == fb.height:
            entry["columns_differ"] = [c for c in fa.columns if not fa[c].equals(fb[c])]
    report["differ"].append(entry)
top = jdiff({k: v for k, v in rec.items() if k != "outputs"}, {k: v for k, v in new.items() if k != "outputs"})
report["commitment_fields_differ"] = sorted({t[0].split(".")[0].split("[")[0] for t in top})
report["commitment_field_examples"] = [t for t in top if not t[0].startswith(("build.", "timings"))][:25]
report["real_stores_unchanged"] = {d: fingerprint(ARTS if d == "artifacts" else DATA / d) == v for d, v in guard.items()}
report["worktree_clean_after"] = git("status", "--porcelain", "--untracked-files=all") == ""
(out / "replay_report.json").write_text(json.dumps(report, indent=1), encoding="utf-8")
print(json.dumps({k: report[k] for k in ("label", "code", "files", "identical", "only_in_one",
                                         "calibration_sources_changed", "real_stores_unchanged", "worktree_clean_after")}))
for e in report["differ"]:
    print("DIFFERS:", json.dumps(e)[:700])
print("commitment fields that differ:", report["commitment_fields_differ"])
for t in report["commitment_field_examples"]:
    print("   ", t)
print("snapshot reads:")
for r in result["reads"]:
    print("   ", r)
