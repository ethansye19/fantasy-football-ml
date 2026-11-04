"""STUB of ``ffml livetest build-week`` for testing the live-test runner.

It honours the interface the ML Engineer is building, and nothing else:

    python scripts/livetest_stub_build.py livetest build-week --season S --week W [--rehearsal]

* writes ``<data>/livetest/<S>w<WW>/commitment.json`` (``<data>/livetest/rehearsal/...``
  with ``--rehearsal``) listing its output files with SHA-256, fake model and
  calibrator hashes, the current ``LATEST`` snapshot ids, the code commit
  (``git rev-parse HEAD`` of the working directory), ``created_at``, the
  week's first kickoff from ``dim_game`` and a top-level digest;
* exit 0 on success, 1 on failure, 2 when blocked (week not in the
  schedule), 3 when the current time is at or after the week's first kickoff
  (not for rehearsals), 4 when the week's commitment already exists (not for
  rehearsals). The format follows ``ffml.sim.livetest``: ``outputs`` with
  ``path``, ``role``, ``sha256`` and ``bytes``; ``first_kickoff``;
  ``code.commit`` and ``code.clean``; ``digest.value`` over the canonical JSON.

It contains NO model output. Its only data file is a small table of
placeholder rows, written under the commitment directory (never into a
predictions store), and every file is labeled STUB and "not approved".

Test hooks (environment): ``FFML_LIVETEST_NOW`` clock override;
``FFML_STUB_EXIT`` exit with this code before doing anything;
``FFML_STUB_CALLS`` append one line per call to this file.
"""

from __future__ import annotations

import argparse
import os
import subprocess
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import livetest_core as core  # noqa: E402

STUB_LABEL = "STUB output for runner tests - not a model projection - not approved"


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="stub of ffml livetest build-week")
    parser.add_argument("group", choices=["livetest"])
    parser.add_argument("command", choices=["build-week"])
    parser.add_argument("--season", type=int, required=True)
    parser.add_argument("--week", type=int, required=True)
    parser.add_argument("--rehearsal", action="store_true")
    args = parser.parse_args(argv)

    calls = os.environ.get("FFML_STUB_CALLS")
    if calls:
        with open(calls, "a", encoding="utf-8") as handle:
            handle.write(f"{args.season} {args.week} {args.rehearsal}\n")
    forced = os.environ.get("FFML_STUB_EXIT")
    if forced:
        print(f"stub: forced exit {forced}")
        return int(forced)

    import polars as pl

    from ffml.config import get_settings

    data_dir = Path(get_settings().data_dir)
    now = core.parse_time(os.environ.get("FFML_LIVETEST_NOW")) or core.utcnow()
    plan = core.week_plan(core.load_dim_game(data_dir, [args.season]), args.season)
    wk = core.find_week(plan, args.season, args.week)
    if wk is None:
        print(f"stub: blocked: {args.season} week {args.week} is not in dim_game")
        return 2
    if now >= wk.first_kickoff_utc and not args.rehearsal:
        print(f"stub: refused: now {core.iso(now)} is at or after the first kickoff {core.iso(wk.first_kickoff_utc)}")
        return 3

    base = data_dir / "livetest" / ("rehearsal" if args.rehearsal else "")
    out_dir = base / core.week_key(args.season, args.week)
    commitment_path = out_dir / core.COMMITMENT_NAME
    if commitment_path.exists() and not args.rehearsal:
        print(f"stub: already committed: {commitment_path}")
        return 4
    stub_dir = out_dir / "stub_outputs"
    stub_dir.mkdir(parents=True, exist_ok=True)
    summary = pl.DataFrame(
        {
            "entity_id": [f"STUB{i:03d}" for i in range(12)],
            "season": [args.season] * 12,
            "week": [args.week] * 12,
            "position": ["QB", "RB", "WR", "TE"] * 3,
            "note": ["STUB placeholder row, not a projection"] * 12,
            "approval_status": ["not approved"] * 12,
        }
    )
    summary_path = stub_dir / core.SUMMARY_NAME
    summary.write_parquet(summary_path)
    readme = stub_dir / "STUB_README.txt"
    readme.write_text(STUB_LABEL + "\n", encoding="utf-8", newline="\n")

    from ffml.config import PROJECT_ROOT

    head = subprocess.run(["git", "rev-parse", "HEAD"], capture_output=True, text=True, cwd=PROJECT_ROOT)
    status = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=PROJECT_ROOT)
    code = {
        "commit": head.stdout.strip() if head.returncode == 0 else None,
        "clean": status.returncode == 0 and not status.stdout.strip(),
    }

    def rel(path: Path) -> str:
        try:
            return path.resolve().relative_to(PROJECT_ROOT.resolve()).as_posix()
        except ValueError:
            return path.resolve().as_posix()

    outputs = [
        {"path": rel(path), "role": "stub", "sha256": core.sha256_file(path), "bytes": path.stat().st_size}
        for path in (summary_path, readme)
    ]
    latest = core.latest_ids(data_dir, tiers=("processed", "features"))
    commitment = {
        "format": "ffml.livetest.commitment/1",
        "stub": True,
        "label": STUB_LABEL,
        "approval_status": "not approved",
        "season": args.season,
        "week": args.week,
        "rehearsal": args.rehearsal,
        "model_version": "stub-not-a-model",
        "model_hash": core.sha256_bytes(b"stub model: no model was fitted"),
        "calibrator_hash": core.sha256_bytes(b"stub calibrator: no calibrator was fitted"),
        "snapshots": {k: v for k, v in latest.items() if k.split("/")[-1] in (
            "dim_game", "fact_player_game", "player_week", "team_week", "dst_week")},
        "code": code,
        "created_at": now.isoformat(),
        "first_kickoff": wk.first_kickoff_utc.isoformat(),
        "outputs": outputs,
    }
    commitment["digest"] = {"algorithm": "sha256", "of": "canonical JSON of every other top-level field",
                            "value": core.canonical_digest(commitment)}
    core.write_json(commitment_path, commitment)
    print(f"stub: wrote {commitment_path}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
