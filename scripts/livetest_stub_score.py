"""STUB of the Evaluator's scorer CLI (``python -m ffml.eval.livetest_score``), for runner tests.

It honours exactly the interface the runner relies on (D-116, assumptions A1
and A3), and nothing else:

    livetest_stub_score.py --prebuild-check --from <commit or ""> --to <commit>
        prints a JSON object {"ok", "from", "to", "uncovered", "frozen"};
        exit 0 = the build may run, exit 5 = blocked.
    livetest_stub_score.py --data-cutoff <season>w<WW>
        prints a JSON object {"week", "data_cutoff_utc", "last_kickoff_utc", "rule"}:
        12:00 UTC on the first Friday at least 72 hours after the week's last
        kickoff in dim_game (A3); exit 0.
    livetest_stub_score.py --week <season>w<WW> [--weekly-sensitivity]
        exit 0: writes data/livetest/scoring/sealed/<week>.sha256 (one placeholder
        part) and data/livetest/scoring/weeks/<week>.json with the dim_game and
        processed snapshot ids; exit 4 = not yet (the runner retries, no alert).

No metric is computed: the part is a placeholder labeled STUB.

Test hooks (environment): ``FFML_STUB_SCORE_CALLS`` append one JSON line per
call; ``FFML_STUB_PREBUILD_EXIT`` (default 0), ``FFML_STUB_PREBUILD_UNCOVERED``
and ``FFML_STUB_PREBUILD_FROZEN`` (comma-separated commits);
``FFML_STUB_CUTOFF`` (ISO, overrides the computed cutoff);
``FFML_STUB_SCORE_EXIT`` (exit code for ``--week``, default 0).
"""

from __future__ import annotations

import argparse
import json
import os
import sys
from datetime import timedelta
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

import livetest_core as core  # noqa: E402

FRIDAY = 4


def data_cutoff(last_kickoff):
    """12:00 UTC on the first Friday at or after last kickoff + 72 h (A3)."""
    earliest = core.as_utc(last_kickoff) + timedelta(hours=72)
    day = earliest.replace(hour=12, minute=0, second=0, microsecond=0)
    while day.weekday() != FRIDAY or day < earliest:
        day = (day + timedelta(days=1)).replace(hour=12, minute=0, second=0, microsecond=0)
    return day


def _split(name: str) -> list[str]:
    return [x.strip() for x in os.environ.get(name, "").split(",") if x.strip()]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="stub of python -m ffml.eval.livetest_score")
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--prebuild-check", action="store_true")
    mode.add_argument("--data-cutoff", metavar="WEEK")
    mode.add_argument("--week", metavar="WEEK")
    parser.add_argument("--from", dest="from_commit", default=None)
    parser.add_argument("--to", dest="to_commit", default=None)
    parser.add_argument("--weekly-sensitivity", action="store_true")
    args = parser.parse_args(argv)

    calls = os.environ.get("FFML_STUB_SCORE_CALLS")
    if calls:
        with open(calls, "a", encoding="utf-8") as handle:
            handle.write(json.dumps({"argv": sys.argv[1:], "cwd": os.getcwd()}) + "\n")

    if args.prebuild_check:
        if args.from_commit is None or args.to_commit is None:
            print("--prebuild-check needs --from and --to", file=sys.stderr)
            return 2
        code = int(os.environ.get("FFML_STUB_PREBUILD_EXIT") or 0)
        result = {"ok": code == 0, "from": args.from_commit, "to": args.to_commit,
                  "uncovered": _split("FFML_STUB_PREBUILD_UNCOVERED"), "frozen": _split("FFML_STUB_PREBUILD_FROZEN"),
                  "stub": True}
        print(json.dumps(result))
        return code

    from ffml.config import get_settings

    data_dir = Path(get_settings().data_dir)
    key = args.data_cutoff or args.week
    season, week = int(key[:4]), int(key[5:])
    if args.data_cutoff:
        override = os.environ.get("FFML_STUB_CUTOFF")
        wk = core.find_week(core.week_plan(core.load_dim_game(data_dir, [season]), season), season, week)
        if wk is None and not override:
            print(f"{key} is not in dim_game", file=sys.stderr)
            return 2
        cutoff = core.parse_time(override) if override else data_cutoff(wk.last_kickoff_utc)
        print(json.dumps({"week": key, "data_cutoff_utc": core.iso(cutoff),
                          "last_kickoff_utc": core.iso(wk.last_kickoff_utc) if wk else None,
                          "rule": "12:00 UTC on the first Friday at least 72 h after the last kickoff (A3)"}))
        return 0

    code = int(os.environ.get("FFML_STUB_SCORE_EXIT") or 0)
    if code:
        print(f"stub: exit {code}")
        return code
    sealed_dir = data_dir / "livetest" / "scoring" / "sealed"
    weeks_dir = data_dir / "livetest" / "scoring" / "weeks"
    sealed_dir.mkdir(parents=True, exist_ok=True)
    weeks_dir.mkdir(parents=True, exist_ok=True)
    part = sealed_dir / f"{season}_w{week:02d}_stub.parquet"
    part.write_bytes(f"STUB sealed part for {key}; no metric\n".encode())
    (sealed_dir / f"{key}.sha256").write_text(f"{core.sha256_file(part)}  {part.name}\n", encoding="utf-8")
    snapshots = core.latest_ids(data_dir, tiers=("processed",))
    core.write_json(weeks_dir / f"{key}.json", {
        "week": key, "stub": True,
        "dim_game_snapshot": snapshots.get("processed/dim_game"),
        "processed_snapshots": {k.split("/", 1)[1]: v for k, v in snapshots.items()},
    })
    print(f"stub: scored {key}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
