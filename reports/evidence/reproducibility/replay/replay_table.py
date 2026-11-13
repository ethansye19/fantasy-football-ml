"""Markdown tables for the replay evidence report (reads the three replay reports and the recorded commitments)."""
import json
from pathlib import Path

S = Path(__file__).parent
DATA = Path(r"C:\Users\Ethan\fantasy-football-ml\data")
runs = {"A": S / "rp_w10" / "replay_report.json", "B": S / "rp_w09" / "replay_report.json",
        "C": S / "rp_w09_e43" / "replay_report.json"}
rep = {k: json.loads(p.read_text(encoding="utf-8")) for k, p in runs.items() if p.exists()}


def short(path: str) -> str:
    parts = path.split("/")
    if "lgbm" in parts:
        return "lgbm/" + parts[-1]
    return parts[-1]


def cell(r: dict, path: str) -> str:
    d = {e["path"]: e for e in r["differ"]}
    if path in r["only_in_one"]:
        return "missing"
    if path not in d:
        return "identical"
    e = d[path]
    if "fields" in e:
        return "differs: " + ", ".join(sorted({f[0] for f in e["fields"]}))
    return "differs"


for week, keys in (("2026w10", ["A"]), ("2026w09", ["B", "C"])):
    rec = json.loads((DATA / "livetest" / "rehearsal" / week / "commitment.json").read_text(encoding="utf-8"))
    keys = [k for k in keys if k in rep]
    print(f"\n### {week}: {len(rec['outputs'])} recorded outputs\n")
    print("| Output | Role | Recorded SHA-256 (first 16) | " + " | ".join(f"Replay {k}" for k in keys) + " |")
    print("| --- | --- | --- | " + " | ".join("---" for _ in keys) + " |")
    lg = [o for o in rec["outputs"] if "/lgbm/" in o["path"]]
    for o in rec["outputs"]:
        if "/lgbm/" in o["path"] and o is not lg[0]:
            continue
        name = f"lgbm/*.txt ({len(lg)} stage models)" if "/lgbm/" in o["path"] else short(o["path"])
        if "/lgbm/" in o["path"]:
            cells = []
            for k in keys:
                vals = {cell(rep[k], x["path"]) for x in lg}
                cells.append(f"{len(lg)} identical" if vals == {"identical"} else "; ".join(sorted(vals)) + f" ({sum(cell(rep[k], x['path']) != 'identical' for x in lg)} of {len(lg)})")
            sha16 = "(per file)"
        else:
            cells = [cell(rep[k], o["path"]) for k in keys]
            sha16 = "`" + o["sha256"][:16] + "`"
        print(f"| {name} | {o['role']} | {sha16} | " + " | ".join(cells) + " |")
    for k in keys:
        r = rep[k]
        print(f"\nReplay {k}: {r['identical']} of {r['files']} identical; real stores unchanged: {r['real_stores_unchanged']}"
              + (f"; worktree clean after: {r.get('worktree_clean_after')}" if "worktree_clean_after" in r else "")
              + (f"; calibration sources changed: {r.get('calibration_sources_changed')}" if "calibration_sources_changed" in r else ""))
        moved = sum(x["snapshot"] != x["latest_now"] for x in r["reads"])
        print(f"  snapshot reads: {len(r['reads'])}, all {sorted({x['how'] for x in r['reads']})}; "
              f"pinned id differs from today's LATEST for {moved}")
        print("  commitment fields that differ:", r["commitment_fields_differ"])
