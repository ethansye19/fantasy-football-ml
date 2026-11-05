"""``ffml refresh``: the data refresh, one step per process (DE-5.1 core, DE-5.4).

Steps, in order: ``ingest`` (every nflverse dataset, all seasons from
``Settings.first_season`` through the current one), ``sleeper`` (the players
map, at most once per UTC day, never forced), ``process``, ``validate`` (fails
when a check FAILS) and ``features``. Each step only adds snapshots and moves
``LATEST``; none rewrites or prunes a snapshot.

Why all seasons: a raw snapshot holds only the seasons it was asked for, and
``LATEST`` points at one snapshot, so ``ffml ingest --seasons 2026`` would hide
2016-2025 from ``process`` and ``features`` (D-031 correction, status note
phase5-data-engineer-livetest). Memory stays bounded because the ingest loads
one season at a time.

Entry points
------------
* :func:`run_refresh` (``ffml refresh --step <name>``). A single step runs in
  this process. ``all`` (alias ``data``) runs the five steps, each in its own
  child process (``python -m ffml.data.refresh --step <name>``), and stops at
  the first failure. ``injuries`` is the small mid-week refresh: ingest of
  injuries, depth charts and weekly rosters, then process. ``retrain-check``
  and ``predict`` are refused with a message: predictions are gated (D-099,
  D-100) and the live test has its own runner (scripts/livetest_week.py).
* ``python -m ffml.data.refresh --step <name> [...]``: one step, then a last
  stdout line ``REFRESH_RESULT {json}`` with ``step``, ``ok``, ``seconds``,
  ``snapshots``, ``error`` and context (``data_dir``, ``ffml``, ``pin_path``);
  exit 0 or 1. The live-test runner calls this with the pinned interpreter, so
  the data the frozen model sees are produced by the frozen code.

Schema pins are read from and written to ``Settings.schema_pin_path`` (the
working copy under the data directory) unless ``update_schema_pin`` asks for
the tracked package file; see :func:`ffml.data.ingest.resolve_pin_path`.
"""

from __future__ import annotations

import argparse
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

log = logging.getLogger(__name__)

STEPS: tuple[str, ...] = ("ingest", "sleeper", "process", "validate", "features")
INJURY_DATASETS: tuple[str, ...] = ("injuries", "depth_charts", "rosters_weekly")
ALIASES: dict[str, str] = {
    "all": "every step in order, each in its own process",
    "data": "same as all",
    "injuries": "ingest of injuries, depth charts and weekly rosters, then process",
}
REFUSED: dict[str, str] = {
    "retrain-check": "not part of the data refresh; retraining checks belong to the model harness",
    "predict": "live predictions need an approved model (D-099, D-100); the live test uses "
    "scripts/livetest_week.py and `ffml livetest build-week`",
}
RESULT_PREFIX = "REFRESH_RESULT "
SEASON_START_MONTH = 9  # nflverse publishes a season's data from September


class RefreshError(RuntimeError):
    """A step failed, or the step name is unknown or refused."""

    def __init__(self, message: str, results: list[StepResult] | None = None) -> None:
        super().__init__(message)
        self.results = results or []


@dataclass(frozen=True)
class Step:
    name: str
    datasets: tuple[str, ...] | None = None  # ingest only; None means every dataset


@dataclass
class StepResult:
    step: str
    ok: bool = False
    seconds: float = 0.0
    snapshots: dict[str, Any] = field(default_factory=dict)
    error: str | None = None
    info: dict[str, Any] = field(default_factory=dict)

    def flat(self) -> dict[str, Any]:
        """One flat JSON object: the fixed keys first, then the step's context."""
        return {
            "step": self.step,
            "ok": self.ok,
            "seconds": self.seconds,
            "snapshots": self.snapshots,
            "error": self.error,
            **self.info,
        }

    @classmethod
    def from_flat(cls, data: dict[str, Any]) -> StepResult:
        fixed = ("step", "ok", "seconds", "snapshots", "error")
        return cls(
            step=str(data.get("step", "?")),
            ok=bool(data.get("ok", False)),
            seconds=float(data.get("seconds") or 0.0),
            snapshots=dict(data.get("snapshots") or {}),
            error=data.get("error"),
            info={k: v for k, v in data.items() if k not in fixed},
        )


# --------------------------------------------------------------------------- #
# Step names and seasons
# --------------------------------------------------------------------------- #


def resolve_steps(step: str) -> list[Step]:
    """The steps a name stands for, in order; unknown and refused names raise."""
    name = (step or "").strip().lower()
    if name in STEPS:
        return [Step(name)]
    if name in ("all", "data"):
        return [Step(s) for s in STEPS]
    if name == "injuries":
        return [Step("ingest", INJURY_DATASETS), Step("process")]
    if name in REFUSED:
        raise RefreshError(f"step {name!r} is refused: {REFUSED[name]}")
    raise RefreshError(
        f"unknown step {step!r}; choose from {', '.join(STEPS)}, {', '.join(ALIASES)}"
    )


def parse_seasons(spec: str) -> list[int]:
    """'2016-2026', '2024,2025' or '2026' into a sorted list."""
    out: set[int] = set()
    for part in str(spec).split(","):
        part = part.strip()
        if not part:
            continue
        if "-" in part:
            lo, hi = part.split("-", 1)
            out.update(range(int(lo), int(hi) + 1))
        else:
            out.add(int(part))
    if not out:
        raise ValueError(f"no seasons in {spec!r}")
    return sorted(out)


def _now() -> datetime:
    return datetime.now(UTC)


def default_seasons() -> list[int]:
    """``Settings.first_season`` through the current season.

    The current season is the later of the schedule snapshot's season
    (:func:`ffml.season.current_state`, when a snapshot exists) and the
    calendar's: a year counts as a season from September, when nflverse starts
    publishing it. Asking for a season that is not published yet would fail
    the ingest as a gap (D-028), and never asking for the new one would leave
    the schedule snapshot stuck in the old season.
    """
    from ffml.config import get_settings

    first = int(get_settings().first_season)
    now = _now()
    calendar = now.year if now.month >= SEASON_START_MONTH else now.year - 1
    season = calendar
    try:
        from ffml.season import current_state

        season = max(calendar, int(current_state(now).season))
    except Exception as exc:  # no schedule snapshot yet, or an unreadable one
        log.debug("default_seasons: schedule state unavailable (%s); using the calendar", exc)
    return list(range(first, max(first, season) + 1))


# --------------------------------------------------------------------------- #
# Steps
# --------------------------------------------------------------------------- #


def _latest(root: Path, names: Iterable[str]) -> dict[str, str | None]:
    from ffml.data.store import SnapshotStore

    store = SnapshotStore(root)
    return {name: store.latest_id(name) for name in names}


def step_ingest(
    seasons: list[int],
    datasets: tuple[str, ...] | None,
    pin_path: Path | None,
    update_schema_pin: bool | None,
) -> dict[str, Any]:
    from ffml.data.ingest import resolve_pin_path, run_ingest

    results = run_ingest(
        seasons=seasons,
        datasets=list(datasets) if datasets else None,
        pin_path=pin_path,
        update_schema_pin=update_schema_pin,
    )
    return {
        "snapshots": {name: r.snapshot_id for name, r in results.items()},
        "latest_advanced": {name: r.latest_updated for name, r in results.items()},
        "pin_path": str(resolve_pin_path(pin_path, update_tracked=update_schema_pin)),
    }


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
    return {
        "snapshots": _latest(get_settings().processed_dir, sorted(ctx.tables)),
        "inputs": dict(ctx.raw_snapshot_ids),
    }


def step_validate(
    seasons: list[int], pin_path: Path | None, report: Path | None, update_schema_pin: bool | None
) -> dict[str, Any]:
    from ffml.data.ingest import resolve_pin_path
    from ffml.data.validate import run_validation

    resolved = resolve_pin_path(pin_path, update_tracked=update_schema_pin)
    ok = run_validation(seasons=seasons, report_path=report, pin_path=resolved)
    return {"passed": ok, "report": str(report) if report else None, "pin_path": str(resolved)}


def step_features(seasons: list[int]) -> dict[str, Any]:
    from ffml.features.build import run_features

    return {"snapshots": run_features(seasons=seasons)}


def run_step(
    step: Step,
    *,
    seasons: Iterable[int] | None = None,
    pin_path: Path | str | None = None,
    report: Path | str | None = None,
    update_schema_pin: bool | None = None,
) -> StepResult:
    """Run one step in this process; failures are reported, not raised."""
    from ffml.config import get_settings

    season_list = sorted({int(s) for s in seasons}) if seasons else default_seasons()
    pin = Path(pin_path) if pin_path is not None else None
    rep = Path(report) if report is not None else None
    t0 = time.perf_counter()
    result = StepResult(step=step.name)
    result.info = {
        "seasons": [season_list[0], season_list[-1]],
        "data_dir": str(get_settings().data_dir),
    }
    try:
        import ffml

        result.info["ffml"] = str(Path(ffml.__file__).resolve().parent)
        if step.name == "ingest":
            info = step_ingest(season_list, step.datasets, pin, update_schema_pin)
        elif step.name == "sleeper":
            info = step_sleeper()
        elif step.name == "process":
            info = step_process(season_list)
        elif step.name == "validate":
            info = step_validate(season_list, pin, rep, update_schema_pin)
        elif step.name == "features":
            info = step_features(season_list)
        else:  # pragma: no cover - resolve_steps only produces the names above
            raise RefreshError(f"unknown step {step.name!r}")
        result.snapshots = dict(info.pop("snapshots", {}) or {})
        result.ok = bool(info.get("passed", True))
        if not result.ok:
            result.error = "validation FAILED; see the report"
        result.info.update(info)
    except Exception as exc:  # reported to the caller, which alerts and retries
        result.error = f"{type(exc).__name__}: {exc}"
        log.error("refresh %s failed: %s", step.name, exc, exc_info=True)
    result.seconds = round(time.perf_counter() - t0, 1)
    return result


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def child_argv(
    step: Step,
    *,
    seasons: list[int],
    pin_path: Path | str | None,
    report: Path | str | None,
    update_schema_pin: bool | None,
) -> list[str]:
    """Arguments after the interpreter for one child process."""
    argv = ["-m", "ffml.data.refresh", "--step", step.name, "--seasons", ",".join(map(str, seasons))]
    if step.datasets:
        argv += ["--datasets", ",".join(step.datasets)]
    if pin_path is not None:
        argv += ["--pin-path", str(pin_path)]
    if report is not None:
        argv += ["--report", str(report)]
    if update_schema_pin:
        argv.append("--update-schema-pin")
    return argv


def parse_result_line(stdout: str | None) -> StepResult | None:
    """The last ``REFRESH_RESULT`` line of a child's stdout, or ``None``."""
    found = None
    for line in (stdout or "").splitlines():
        if line.startswith(RESULT_PREFIX):
            try:
                found = StepResult.from_flat(json.loads(line[len(RESULT_PREFIX) :]))
            except ValueError:
                continue
    return found


def run_refresh(
    step: str = "all",
    *,
    seasons: Iterable[int] | None = None,
    datasets: Iterable[str] | None = None,
    pin_path: Path | str | None = None,
    report: Path | str | None = None,
    update_schema_pin: bool | None = None,
    python: Path | str | None = None,
    runner: Callable[..., subprocess.CompletedProcess] = subprocess.run,
) -> list[StepResult]:
    """Run the steps ``step`` names (see the module docstring).

    One step runs in this process. Several run one per child process with
    ``python`` (default: this interpreter), stopping at the first failure.
    Raises :class:`RefreshError` (with ``.results``) when any step failed.
    """
    plan = resolve_steps(step)
    if datasets:
        wanted = tuple(datasets)
        plan = [Step(s.name, wanted) if s.name == "ingest" else s for s in plan]
    season_list = sorted({int(s) for s in seasons}) if seasons else default_seasons()
    results: list[StepResult] = []
    if len(plan) == 1:
        results.append(
            run_step(
                plan[0],
                seasons=season_list,
                pin_path=pin_path,
                report=report,
                update_schema_pin=update_schema_pin,
            )
        )
    else:
        py = str(python or sys.executable)
        env = {**os.environ, "PYTHONUTF8": "1", "PYTHONIOENCODING": "utf-8"}
        for item in plan:
            argv = [
                py,
                *child_argv(
                    item,
                    seasons=season_list,
                    pin_path=pin_path,
                    report=report,
                    update_schema_pin=update_schema_pin,
                ),
            ]
            log.info("refresh: %s", " ".join(argv))
            t0 = time.perf_counter()
            proc = runner(
                argv, stdout=subprocess.PIPE, text=True, encoding="utf-8", errors="replace", env=env
            )
            res = parse_result_line(proc.stdout) or StepResult(
                step=item.name,
                error=f"child exited {proc.returncode} without a {RESULT_PREFIX.strip()} line",
                seconds=round(time.perf_counter() - t0, 1),
            )
            results.append(res)
            if not res.ok:
                break
    _log_summary(results)
    failed = [r for r in results if not r.ok]
    if failed:
        raise RefreshError(
            "; ".join(f"{r.step}: {r.error or 'failed'}" for r in failed), results
        )
    return results


def _log_summary(results: list[StepResult]) -> None:
    lines = [f"{'step':<10} {'ok':<5} {'secs':>7}  snapshots / error"]
    for r in results:
        tail = r.error if r.error else ", ".join(f"{k}={v}" for k, v in r.snapshots.items()) or "-"
        lines.append(f"{r.step:<10} {str(r.ok):<5} {r.seconds:>7}  {tail}")
    log.info("refresh summary\n%s", "\n".join(lines))


# --------------------------------------------------------------------------- #
# ``python -m ffml.data.refresh``
# --------------------------------------------------------------------------- #


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m ffml.data.refresh", description="one data-refresh step in this process"
    )
    parser.add_argument("--step", required=True, choices=STEPS)
    parser.add_argument(
        "--seasons", default=None, help="e.g. 2016-2026 (default: first_season through the current season)"
    )
    parser.add_argument("--datasets", default="", help="ingest only: comma-separated dataset names")
    parser.add_argument("--pin-path", type=Path, default=None, help="schema pins (default: the working copy)")
    parser.add_argument("--report", type=Path, default=None, help="validate: markdown report path")
    parser.add_argument(
        "--update-schema-pin", action="store_true", help="write new columns to the tracked pin file"
    )
    args = parser.parse_args(argv)

    from ffml.config import ensure_utf8

    ensure_utf8()
    if not logging.getLogger().handlers:
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s %(levelname)s %(name)s: %(message)s",
            stream=sys.stderr,
            force=True,
        )
    datasets = tuple(d.strip() for d in args.datasets.split(",") if d.strip()) or None
    result = run_step(
        Step(args.step, datasets),
        seasons=parse_seasons(args.seasons) if args.seasons else None,
        pin_path=args.pin_path,
        report=args.report,
        update_schema_pin=True if args.update_schema_pin else None,
    )
    print(RESULT_PREFIX + json.dumps(result.flat(), default=str), flush=True)
    return 0 if result.ok else 1


__all__ = [
    "ALIASES",
    "INJURY_DATASETS",
    "REFUSED",
    "RESULT_PREFIX",
    "STEPS",
    "RefreshError",
    "Step",
    "StepResult",
    "child_argv",
    "default_seasons",
    "main",
    "parse_result_line",
    "parse_seasons",
    "resolve_steps",
    "run_refresh",
    "run_step",
]


if __name__ == "__main__":
    sys.exit(main())
