"""One data-refresh step per process (DE-5.1 core), used by the live-test runner.

Run it with the interpreter whose ``ffml`` should do the work; the live-test
runner uses the pinned worktree's venv, so data are processed by the frozen
code. Each step only adds snapshots and moves ``LATEST``; it never rewrites or
prunes an existing snapshot (the runner fingerprints them to prove it).

    python scripts/refresh_data.py --step ingest   --seasons 2016-2026 --pin-path P
    python scripts/refresh_data.py --step sleeper
    python scripts/refresh_data.py --step process  --seasons 2016-2026
    python scripts/refresh_data.py --step validate --seasons 2016-2026 --pin-path P --report R
    python scripts/refresh_data.py --step features --seasons 2016-2026

Steps:

* ``ingest``: every nflverse dataset for all seasons. A full run, because a
  snapshot holds only the seasons it was asked for and ``LATEST`` must keep
  the history (a ``--seasons 2026`` snapshot would hide 2016-2025). Schema
  pins are read from and updated in ``--pin-path`` (seeded from the package
  copy on first use), so the code checkout is never modified.
* ``sleeper``: the Sleeper players map, at most once per UTC day (never forced).
* ``process``: processed tables for all seasons.
* ``validate``: the validation suite; exit 1 when a check FAILS.
* ``features``: point-in-time feature sets for all seasons.

The last stdout line is ``REFRESH_RESULT {json}`` with ``step``, ``ok``,
``seconds``, ``snapshots`` and ``error``. Exit 0 on success, 1 on failure.
"""

from __future__ import annotations

import argparse
import json
import logging
import shutil
import sys
import time
import traceback
from pathlib import Path
from typing import Any

STEPS = ("ingest", "sleeper", "process", "validate", "features")


def parse_seasons(spec: str) -> list[int]:
    out: set[int] = set()
    for part in spec.split(","):
        part = part.strip()
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        elif part:
            out.add(int(part))
    return sorted(out)


def _latest(root: Path, names: list[str]) -> dict[str, str | None]:
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(root)
    return {name: store.latest_id(name) for name in names}


def seed_pins(pin_path: Path) -> dict[str, Any]:
    from ffml.data.ingest import SCHEMA_PIN_PATH

    if pin_path.exists():
        return {"pin_path": str(pin_path), "seeded": False}
    pin_path.parent.mkdir(parents=True, exist_ok=True)
    shutil.copyfile(SCHEMA_PIN_PATH, pin_path)
    return {"pin_path": str(pin_path), "seeded": True, "seeded_from": str(SCHEMA_PIN_PATH)}


def step_ingest(seasons: list[int], pin_path: Path, datasets: list[str] | None) -> dict[str, Any]:
    from ffml.data.ingest import run_ingest

    info = seed_pins(pin_path)
    results = run_ingest(seasons=seasons, datasets=datasets, pin_path=pin_path)
    info["snapshots"] = {name: r.snapshot_id for name, r in results.items()}
    info["latest_advanced"] = {name: r.latest_updated for name, r in results.items()}
    return info


def step_sleeper() -> dict[str, Any]:
    from ffml.data.sleeper import SleeperPlayersCache

    cache = SleeperPlayersCache()
    before = cache.latest()
    frame = cache.get(max_age_hours=24.0, force=False)
    after = cache.latest()
    return {
        "snapshots": {"sleeper_players": f"{after:%Y%m%d}" if after else None},
        "fetched": after != before,
        "rows": frame.height,
    }


def step_process(seasons: list[int]) -> dict[str, Any]:
    from ffml.config import get_settings
    from ffml.data.process import run_process

    ctx = run_process(seasons=seasons)
    settings = get_settings()
    tables = sorted(ctx.tables)
    return {"snapshots": _latest(settings.processed_dir, tables), "inputs": dict(ctx.raw_snapshot_ids)}


def step_validate(seasons: list[int], pin_path: Path, report: Path | None) -> dict[str, Any]:
    from ffml.data.validate import run_validation

    seed_pins(pin_path)
    ok = run_validation(seasons=seasons, report_path=report, pin_path=pin_path)
    return {"passed": ok, "report": str(report) if report else None}


def step_features(seasons: list[int]) -> dict[str, Any]:
    from ffml.features.build import run_features

    return {"snapshots": run_features(seasons=seasons)}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.split("\n\n")[0])
    parser.add_argument("--step", required=True, choices=STEPS)
    parser.add_argument("--seasons", default="2016-2026")
    parser.add_argument("--datasets", default="", help="ingest only: comma-separated dataset names")
    parser.add_argument("--pin-path", type=Path, default=None)
    parser.add_argument("--report", type=Path, default=None)
    args = parser.parse_args(argv)

    from ffml.config import ensure_utf8, get_settings

    ensure_utf8()
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s",
                        stream=sys.stderr, force=True)
    settings = get_settings()
    seasons = parse_seasons(args.seasons)
    pin_path = args.pin_path or (settings.data_dir / "livetest" / "schema_pins" / "expected_schemas.json")
    t0 = time.perf_counter()
    result: dict[str, Any] = {"step": args.step, "ok": False, "seasons": [seasons[0], seasons[-1]],
                              "data_dir": str(settings.data_dir)}
    try:
        import ffml

        result["ffml"] = str(Path(ffml.__file__).resolve().parent)
        if args.step == "ingest":
            datasets = [d for d in args.datasets.split(",") if d.strip()] or None
            result.update(step_ingest(seasons, pin_path, datasets))
            result["ok"] = True
        elif args.step == "sleeper":
            result.update(step_sleeper())
            result["ok"] = True
        elif args.step == "process":
            result.update(step_process(seasons))
            result["ok"] = True
        elif args.step == "validate":
            result.update(step_validate(seasons, pin_path, args.report))
            result["ok"] = bool(result["passed"])
        elif args.step == "features":
            result.update(step_features(seasons))
            result["ok"] = True
    except Exception as exc:  # reported to the runner, which alerts and retries
        result["error"] = f"{type(exc).__name__}: {exc}"
        traceback.print_exc(file=sys.stderr)
    result["seconds"] = round(time.perf_counter() - t0, 1)
    print("REFRESH_RESULT " + json.dumps(result, default=str), flush=True)
    return 0 if result["ok"] else 1


if __name__ == "__main__":
    sys.exit(main())
