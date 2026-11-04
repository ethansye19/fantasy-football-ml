"""Weekly chain_v2 live-test build (D-098): ``ffml livetest build-week``.

Interface (the scheduler and the push step depend on it; keep it stable)
------------------------------------------------------------------------
- Command: ``ffml livetest build-week --season S --week W [--rehearsal]``.
- Commitment: ``data/livetest/{S}w{W:02d}/commitment.json``; a rehearsal
  writes ``data/livetest/rehearsal/{S}w{W:02d}/commitment.json`` and every
  other rehearsal output under ``data/livetest/rehearsal/``.
- Exit codes:

  - 0: built and committed;
  - 1: failure (traceback printed; no commitment written);
  - 2: blocked: refused by the approval gate of ``ffml predict`` (D-100), or
    the week has no regular-season game in ``dim_game``;
  - 3: deadline: the current UTC time is at or after the week's first
    kickoff (``dim_game``) and ``--rehearsal`` was not given. Checked before
    anything is written, again before the projections are written, and
    again before the commitment is written;
  - 4: a commitment for that week already exists (one real build per week;
    nothing is written). Rehearsals are never refused for this.

Steps of :func:`build_week` (point-in-time as of the build, D-046)
------------------------------------------------------------------
1. **Deadline** (exit 3) and the chain_v2 pins
   (:func:`ffml.models.chain_v2.check_pins`).
2. **Raw calibration history.** (raw prediction, outcome) pairs of every
   regular-season week from 2021 week 1 to week ``W - 1``: stored raw
   walk-forward chain rows (:data:`STORED_SOURCES`: 2021-2024 from
   ``default_20261102``, 2025 from ``holdout_2025``, 2026 weeks 1-7 from
   ``forward_2026``) and, for the weeks after those, rows computed by the
   same walk-forward (fit at the week's cutoff, predict the week from the
   stored feature rows, ``ffml.eval.harness.check_frame``), cached once in
   ``data/livetest/walkforward/parts/chain_v1/`` (rehearsals:
   ``data/livetest/rehearsal/walkforward/``). Outcomes, PIT bounds and week
   cutoffs come from the harness functions on the latest processed data.
3. **Refit.** chain_v2's raw chain is fitted at the cutoff ``(S, W)``
   (training rows through week ``W - 1``, 2025 included) and saved as
   ``artifacts/models/chain_v2/<version>/`` with ``train_manifest.json``;
   version ``chain_v2-primary-c{S}w{WW}-<UTC stamp>``.
4. **Calibrator state** for week ``W`` from the history
   (:func:`ffml.models.chain_v2.calibrator_state`), saved in the artifact as
   ``calibration_state.json`` and hashed; the version is then registered as
   a registry **candidate** with that calibration file.
5. **Predict** week ``W`` in private-test mode (D-100): ``run_predict`` with
   the candidate, labeled "not approved", ``player_summary`` calibrated
   with the state (raw rows in ``player_summary_raw``), written to the
   private store (rehearsals: the sandbox store and registry of
   :func:`ffml.sim.predict.rehearsal_dir`). Rest of season is not built.
6. **Commitment** (:func:`write_commitment`): every output file with its
   SHA-256, the model artifact and calibrator-state hashes, the calibration
   inputs with hashes, data and feature snapshot ids, the code commit with
   clean-tree status, as-of time, ``created_at`` (UTC), the first kickoff,
   run time and peak memory, and a top-level digest: SHA-256 of the
   canonical JSON (sorted keys, no whitespace) of everything except
   ``digest`` (:func:`commitment_digest`; :func:`verify_commitment` checks
   it and every file).

Git commit and push of the commitment are the scheduler's (not done here).
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import subprocess
import sys
import time
from collections.abc import Callable, Iterable, Sequence
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ffml.config import PROJECT_ROOT, get_settings
from ffml.models import chain_v2

log = logging.getLogger(__name__)

EXIT_OK = 0
EXIT_FAILED = 1
EXIT_REFUSED = 2
EXIT_DEADLINE = 3
EXIT_ALREADY_COMMITTED = 4
COMMITMENT_FILE = "commitment.json"
COMMITMENT_FORMAT = "ffml.livetest.commitment/1"
PURPOSE = "livetest"
RAW_KEY = "chain_v1"  # part directory and model label of raw chain rows


class DeadlinePassed(RuntimeError):
    """The week's first kickoff has passed (exit code 3); nothing more is written."""


class WeekNotScheduled(ValueError):
    """The week has no regular-season game in ``dim_game`` (exit code 2); nothing is written."""


class AlreadyCommitted(RuntimeError):
    """A commitment for this week exists already (exit code 4); nothing is written."""


@dataclass(frozen=True)
class RawSource:
    """Stored raw walk-forward rows of one season (``<run>/parts/<key>/<season>_wNN.parquet``)."""

    run_id: str
    season: int
    weeks: tuple[int, ...]
    key: str = RAW_KEY


STORED_SOURCES: tuple[RawSource, ...] = (
    *(RawSource("default_20261102", s, tuple(range(1, 19))) for s in (2021, 2022, 2023, 2024)),
    RawSource("holdout_2025", 2025, tuple(range(1, 19))),
    RawSource("forward_2026", 2026, tuple(range(1, 8))),
)


@dataclass
class BuildResult:
    season: int
    week: int
    rehearsal: bool
    model_version: str
    commitment: Path
    digest: str
    run_dir: Path
    seconds: float
    peak_memory_mb: float | None
    info: dict[str, Any] = field(default_factory=dict)


# --------------------------------------------------------------------------- #
# Paths and small helpers
# --------------------------------------------------------------------------- #


def livetest_root() -> Path:
    return get_settings().data_dir / "livetest"


def sandbox_root() -> Path:
    from ffml.sim.predict import rehearsal_dir

    return rehearsal_dir()


def base_dir(rehearsal: bool) -> Path:
    return sandbox_root() if rehearsal else livetest_root()


def week_dir(season: int, week: int, rehearsal: bool = False) -> Path:
    return base_dir(rehearsal) / f"{int(season)}w{int(week):02d}"


def commitment_path(season: int, week: int, rehearsal: bool = False) -> Path:
    return week_dir(season, week, rehearsal) / COMMITMENT_FILE


def walkforward_dir(rehearsal: bool = False) -> Path:
    return base_dir(rehearsal) / "walkforward"


def artifact_root(rehearsal: bool = False) -> Path:
    if rehearsal:
        return sandbox_root() / "models" / chain_v2.MODEL_NAME
    return get_settings().models_dir / chain_v2.MODEL_NAME


def registry_file(rehearsal: bool = False) -> Path | None:
    from ffml.sim.predict import private_registry

    return private_registry(rehearsal)


def sha256_file(path: Path | str) -> str:
    h = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    return h.hexdigest()


def rel(path: Path | str) -> str:
    """Path relative to the repository root when inside it, else absolute (POSIX)."""
    p = Path(path).resolve()
    try:
        return p.relative_to(PROJECT_ROOT.resolve()).as_posix()
    except ValueError:
        return p.as_posix()


def resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else PROJECT_ROOT / p


def _utc(now: datetime) -> datetime:
    return now if now.tzinfo else now.replace(tzinfo=UTC)


def _iso(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return _utc(value).astimezone(UTC).isoformat()
    return str(value)


def _write_bytes_atomic(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_bytes(data)
    os.replace(tmp, path)


def _json_bytes(payload: Any) -> bytes:
    return (json.dumps(payload, indent=2, default=str) + "\n").encode("utf-8")


def peak_memory_mb() -> float | None:
    """Peak resident memory of this process in MiB (Windows peak working set, else ru_maxrss)."""
    try:
        if sys.platform == "win32":
            import ctypes
            from ctypes import wintypes

            class Counters(ctypes.Structure):
                _fields_ = [
                    ("cb", wintypes.DWORD),
                    ("PageFaultCount", wintypes.DWORD),
                    ("PeakWorkingSetSize", ctypes.c_size_t),
                    ("WorkingSetSize", ctypes.c_size_t),
                    ("QuotaPeakPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t),
                    ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                    ("PagefileUsage", ctypes.c_size_t),
                    ("PeakPagefileUsage", ctypes.c_size_t),
                ]

            counters = Counters()
            counters.cb = ctypes.sizeof(Counters)
            psapi = ctypes.WinDLL("psapi")
            kernel = ctypes.WinDLL("kernel32")
            kernel.GetCurrentProcess.restype = wintypes.HANDLE
            psapi.GetProcessMemoryInfo.argtypes = [
                wintypes.HANDLE,
                ctypes.POINTER(Counters),
                wintypes.DWORD,
            ]
            handle = kernel.GetCurrentProcess()
            if not psapi.GetProcessMemoryInfo(handle, ctypes.byref(counters), counters.cb):
                return None
            return round(counters.PeakWorkingSetSize / 2**20, 1)
        import resource

        peak = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
        return round(peak / (2**20 if sys.platform == "darwin" else 2**10), 1)
    except Exception:  # noqa: BLE001 - informational only
        return None


def code_state() -> dict[str, Any]:
    """Commit and clean-tree status of the working tree (``git status --porcelain``)."""

    def git(*args: str) -> str | None:
        try:
            out = subprocess.run(
                ["git", *args],
                cwd=PROJECT_ROOT,
                capture_output=True,
                text=True,
                timeout=30,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        return out.stdout if out.returncode == 0 else None

    head = (git("rev-parse", "HEAD") or "").strip() or None
    status = git("status", "--porcelain", "--untracked-files=normal")
    lines = [ln for ln in (status or "").splitlines() if ln.strip()]
    tracked = [ln for ln in lines if not ln.startswith("??")]
    code_prefixes = ("ffml/", "pyproject.toml")
    code = [ln for ln in lines if ln[3:].strip('"').startswith(code_prefixes)]
    return {
        "commit": head,
        "short": head[:7] if head else None,
        "clean": status is not None and not lines,
        "tracked_clean": status is not None and not tracked,
        "code_clean": status is not None and not code,
        "status": lines,
        "code_changes": code,
    }


# --------------------------------------------------------------------------- #
# Schedule and deadline
# --------------------------------------------------------------------------- #


def first_kickoff(season: int, week: int, store: Any = None) -> datetime:
    """First regular-season kickoff of ``(season, week)`` from the latest ``dim_game`` (UTC)."""
    from ffml.data.store import SnapshotStore

    store = store or SnapshotStore(get_settings().processed_dir)
    games = store.read("dim_game", seasons=[int(season)], columns=None)
    games = games.filter(
        (pl.col("season") == int(season))
        & (pl.col("week") == int(week))
        & (pl.col("game_type") == "REG")
    )
    if games.height == 0 or games["kickoff_utc"].drop_nulls().len() == 0:
        raise WeekNotScheduled(f"dim_game has no regular-season kickoff for {season} week {week}")
    return _utc(games["kickoff_utc"].min())


def check_deadline(now: datetime, kickoff: datetime, rehearsal: bool, stage: str) -> None:
    """Raise :class:`DeadlinePassed` when ``now >= kickoff`` (never for a rehearsal)."""
    if rehearsal:
        return
    if _utc(now) >= _utc(kickoff):
        raise DeadlinePassed(
            f"{stage}: now {_iso(now)} is at or after the first kickoff {_iso(kickoff)}; "
            "projections must be saved before kickoff (use --rehearsal to rehearse)"
        )


# --------------------------------------------------------------------------- #
# Raw walk-forward rows
# --------------------------------------------------------------------------- #


def backtests_dir() -> Path:
    return get_settings().data_dir / "backtests"


def stored_source(season: int, week: int) -> RawSource | None:
    for src in STORED_SOURCES:
        if src.season == season and week in src.weeks:
            return src
    return None


def stored_part(season: int, week: int) -> Path:
    src = stored_source(season, week)
    if src is None:
        raise KeyError(f"no stored raw rows for {season} week {week}")
    return backtests_dir() / src.run_id / "parts" / src.key / f"{season}_w{week:02d}.parquet"


def scheduled_weeks(
    first_season: int, season: int, week: int, store: Any = None
) -> list[tuple[int, int]]:
    """Regular-season weeks from ``first_season`` week 1 up to (not including) ``(season, week)``."""
    from ffml.data.store import SnapshotStore

    store = store or SnapshotStore(get_settings().processed_dir)
    games = store.read(
        "dim_game", seasons=list(range(first_season, season + 1)), columns=None
    ).filter(pl.col("game_type") == "REG")
    keys = games.select("season", "week").unique().sort("season", "week")
    return [
        (int(s), int(w))
        for s, w in keys.iter_rows()
        if (int(s), int(w)) < (int(season), int(week)) and int(s) >= first_season
    ]


def recompute_week(
    data: Any, season: int, week: int, scorings: Sequence[str] = chain_v2.SCORINGS
) -> tuple[pl.DataFrame, dict[str, Any]]:
    """One walk-forward week exactly as the harness runs it: fit at ``(season, week)``, predict it."""
    from ffml.eval.harness import check_frame, resolve_scorings

    rules = resolve_scorings(scorings)
    model = chain_v2.new_model(data)
    t0 = time.perf_counter()
    model.fit((season, week))
    fit_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    frame = model.predict(season, week, rules)
    predict_s = time.perf_counter() - t1
    frame = check_frame(frame, model, season, week, rules)
    info = {
        "season": season,
        "week": week,
        "cutoff": [season, week],
        "fit_seconds": round(fit_s, 2),
        "predict_seconds": round(predict_s, 2),
        "rows": frame.height,
        "features": data.snapshot_ids,
    }
    return frame, info


def walkforward_part(
    data: Any, season: int, week: int, cache: Path, scorings: Sequence[str] = chain_v2.SCORINGS
) -> tuple[Path, bool]:
    """The cached fresh walk-forward part of a week; computed (once) when missing. ``(path, new)``."""
    path = cache / "parts" / RAW_KEY / f"{season}_w{week:02d}.parquet"
    if path.exists():
        return path, False
    frame, info = recompute_week(data, season, week, scorings)
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(path.name + ".tmp")
    frame.write_parquet(tmp)
    os.replace(tmp, path)
    from ffml.holdout import _git_sha

    info.update(
        sha256=sha256_file(path),
        created_at=datetime.now(UTC).isoformat(),
        git=_git_sha(),
        protocol="ffml.eval.harness walk-forward, refit_every 1, check_frame",
    )
    _write_bytes_atomic(path.with_suffix(".json"), _json_bytes(info))
    log.info("walk-forward %d w%02d: %d rows -> %s", season, week, frame.height, path)
    return path, True


def raw_history_parts(
    season: int,
    week: int,
    data: Any,
    *,
    rehearsal: bool = False,
    store: Any = None,
    scorings: Sequence[str] = chain_v2.SCORINGS,
) -> tuple[list[dict[str, Any]], list[Path]]:
    """Every raw part from the warm-up season through the week before ``(season, week)``.

    Returns the source records (path, SHA-256, origin) and the parts written
    by this call. A stored week whose part file is missing is an error.
    """
    sources, written = [], []
    for s, w in scheduled_weeks(chain_v2.WARMUP_SEASON, season, week, store):
        src = stored_source(s, w)
        if src is not None:
            path = stored_part(s, w)
            if not path.exists():
                raise FileNotFoundError(f"stored raw part missing: {path}")
            origin = f"stored:{src.run_id}"
        else:
            path, new = walkforward_part(data, s, w, walkforward_dir(rehearsal), scorings)
            if new:
                written.append(path)
                written.append(path.with_suffix(".json"))
            origin = "walkforward"
        sources.append(
            {
                "season": s,
                "week": w,
                "origin": origin,
                "path": rel(path),
                "sha256": sha256_file(path),
            }
        )
    return sources, written


def read_parts(paths: Iterable[Path | str]) -> pl.DataFrame:
    frames = [pl.read_parquet(resolve(str(p))).drop("drivers", strict=False) for p in paths]
    if not frames:
        raise ValueError("no raw parts")
    return pl.concat(frames, how="diagonal_relaxed")


def with_outcomes(
    raw: pl.DataFrame, actuals: pl.DataFrame, scorings: Sequence[str] = chain_v2.SCORINGS
) -> pl.DataFrame:
    """Raw rows plus the harness outcome columns (exactly the combine step of ``run_walk_forward``)."""
    from ffml.eval.harness import (
        fill_bounds,
        join_outcomes,
        outcome_table,
        position_bounds,
        resolve_scorings,
        week_cutoffs,
    )

    rules = resolve_scorings(scorings)
    seasons = sorted({int(s) for s in raw["season"].unique().to_list()})
    outcomes = outcome_table(actuals, seasons, rules)
    cutoffs = week_cutoffs(actuals.filter(pl.col("season").is_in(seasons)))
    joined = join_outcomes(raw, outcomes, cutoffs)
    return fill_bounds(joined, position_bounds(actuals, seasons, rules))


def target_bounds(
    actuals: pl.DataFrame, season: int, scorings: Sequence[str] = chain_v2.SCORINGS
) -> pl.DataFrame:
    """PIT bounds of the target season's rows: position extremes of every earlier season."""
    from ffml.eval.harness import position_bounds, resolve_scorings

    b = position_bounds(actuals, [season], resolve_scorings(scorings))
    return b.filter(pl.col("season") == season).drop("season")


def calibration_history(
    season: int,
    week: int,
    data: Any,
    *,
    rehearsal: bool = False,
    store: Any = None,
) -> dict[str, Any]:
    """Raw rows with outcomes for the target week's calibrator, their sources and target bounds."""
    from ffml.scoring.actuals import actual_statlines

    sources, written = raw_history_parts(season, week, data, rehearsal=rehearsal, store=store)
    raw = read_parts(s["path"] for s in sources)
    actuals = actual_statlines(range(get_settings().first_season, season + 1), PURPOSE, store=store)
    history = with_outcomes(raw, actuals)
    return {
        "history": history,
        "bounds": target_bounds(actuals, season),
        "sources": sources,
        "written": written,
    }


# --------------------------------------------------------------------------- #
# Commitment
# --------------------------------------------------------------------------- #


def canonical(payload: Any) -> bytes:
    return json.dumps(
        payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False, default=str
    ).encode("utf-8")


def commitment_digest(payload: dict[str, Any]) -> str:
    """SHA-256 of the canonical JSON of the commitment without its ``digest`` field."""
    body = {k: v for k, v in payload.items() if k != "digest"}
    return hashlib.sha256(canonical(body)).hexdigest()


def file_records(paths: Iterable[Path], role: str) -> list[dict[str, Any]]:
    out = []
    for p in sorted({Path(p) for p in paths}, key=lambda x: x.as_posix()):
        if p.is_file():
            out.append(
                {"path": rel(p), "role": role, "sha256": sha256_file(p), "bytes": p.stat().st_size}
            )
    return out


def tree_files(root: Path) -> list[Path]:
    return sorted(p for p in Path(root).rglob("*") if p.is_file())


def digest_of(records: Sequence[dict[str, Any]]) -> str:
    """SHA-256 over ``path sha256`` lines (sorted by path): one hash for a set of files."""
    lines = sorted(f"{r['path']} {r['sha256']}" for r in records)
    return hashlib.sha256("\n".join(lines).encode("utf-8")).hexdigest()


def write_commitment(path: Path, payload: dict[str, Any]) -> str:
    """Add the digest and write the commitment atomically (LF, UTF-8); returns the digest."""
    body = {k: v for k, v in payload.items() if k != "digest"}
    digest = commitment_digest(body)
    body["digest"] = {
        "algorithm": "sha256",
        "of": "canonical JSON of every other top-level field (sorted keys, separators , :)",
        "value": digest,
    }
    _write_bytes_atomic(path, _json_bytes(body))
    return digest


def verify_commitment(path: Path | str) -> list[str]:
    """Problems with a commitment: digest mismatch, missing or changed files (empty = intact)."""
    payload = json.loads(Path(path).read_text(encoding="utf-8"))
    problems = []
    recorded = (payload.get("digest") or {}).get("value")
    if recorded != commitment_digest(payload):
        problems.append("digest does not match the commitment body")
    records = list(payload.get("outputs", []))
    records += list((payload.get("calibration_history") or {}).get("sources", []))
    for rec in records:
        file = resolve(rec["path"])
        if not file.is_file():
            problems.append(f"missing {rec['path']}")
        elif sha256_file(file) != rec["sha256"]:
            problems.append(f"changed {rec['path']}")
    return problems


# --------------------------------------------------------------------------- #
# Build
# --------------------------------------------------------------------------- #


def _processed_ids(store: Any = None) -> dict[str, str | None]:
    from ffml.data.store import SnapshotStore

    store = store or SnapshotStore(get_settings().processed_dir)
    names = (
        "dim_game",
        "dim_player",
        "fact_player_game",
        "fact_dst_game",
        "fact_team_game",
        "fact_injury_report",
        "fact_depth_chart",
        "fact_roster_week",
        "sleeper_players",
    )
    out = {}
    for name in names:
        try:
            out[name] = store.latest_id(name)
        except Exception:  # noqa: BLE001 - informational
            out[name] = None
    return out


def build_week(
    season: int,
    week: int,
    *,
    rehearsal: bool = False,
    clock: Callable[[], datetime] | None = None,
    kickoff: datetime | None = None,
    data: Any = None,
    ctx: Any = None,
    store: Any = None,
    history_fn: Callable[..., dict[str, Any]] | None = None,
    n_sims: int = chain_v2.N_SIMS,
) -> BuildResult:
    """Build, predict and commit chain_v2 for ``(season, week)``; see the module docstring.

    Keyword arguments other than ``rehearsal`` are for tests: ``clock``
    (UTC now), ``kickoff`` (instead of ``dim_game``), ``data`` (a ChainData),
    ``ctx`` (a FeatureContext for the live rows), ``store`` (processed
    snapshot store), ``history_fn`` (replaces :func:`calibration_history`)
    and ``n_sims``.
    """
    from ffml.holdout import release_info
    from ffml.models import registry
    from ffml.sim.predict import run_predict

    t_start = time.perf_counter()
    clock = clock or (lambda: datetime.now(UTC))
    season, week = int(season), int(week)
    as_of = _utc(clock())
    kickoff = _utc(kickoff) if kickoff is not None else first_kickoff(season, week, store)
    check_deadline(as_of, kickoff, rehearsal, "start")
    commit_file = commitment_path(season, week, rehearsal)
    if commit_file.exists() and not rehearsal:
        raise AlreadyCommitted(
            f"{commit_file} exists: {season} week {week} was already built and committed"
        )
    timings: dict[str, float] = {}
    pins = chain_v2.check_pins()
    code = code_state()
    processed_at_start = _processed_ids(store)

    # ---- data, raw history, calibrator inputs -------------------------------- #
    t0 = time.perf_counter()
    if data is None:
        from ffml.models.chain.data import ChainData

        data = ChainData(purpose="train")
    data.ensure(season)
    chain_v2.check_pins(data)
    timings["load_features"] = time.perf_counter() - t0
    t0 = time.perf_counter()
    hist = (history_fn or calibration_history)(season, week, data, rehearsal=rehearsal, store=store)
    timings["calibration_history"] = time.perf_counter() - t0

    # ---- refit ---------------------------------------------------------------- #
    t0 = time.perf_counter()
    model = chain_v2.new_model(data)
    model.fit((season, week))
    fit_seconds = time.perf_counter() - t0
    timings["fit"] = fit_seconds
    version = chain_v2.model_version((season, week), as_of)
    artifact = artifact_root(rehearsal) / version
    chain_v2.save_artifact(
        model,
        artifact,
        version,
        fit_seconds=fit_seconds,
        extra={
            "livetest": {
                "season": season,
                "week": week,
                "rehearsal": rehearsal,
                "as_of": as_of.isoformat(),
            },
            "holdout_release": release_info(),
        },
    )

    # ---- calibrator state ---------------------------------------------------- #
    t0 = time.perf_counter()
    state = chain_v2.calibrator_state(
        hist["history"],
        (season, week),
        kickoff,
        bounds=hist["bounds"],
        meta={"sources_digest": digest_of(hist["sources"]), "as_of": as_of.isoformat()},
    )
    state_file = artifact / chain_v2.STATE_FILE
    _write_bytes_atomic(state_file, chain_v2.state_payload(state))
    state_sha = sha256_file(state_file)
    if state_sha != chain_v2.state_sha256(state):
        raise RuntimeError("calibrator state file does not hash to its canonical bytes")
    timings["calibrator_state"] = time.perf_counter() - t0
    del hist["history"]
    entry = registry.register_candidate(
        chain_v2.MODEL_NAME,
        version,
        artifact,
        backtest_run_id=f"livetest_{season}w{week:02d}" + ("_rehearsal" if rehearsal else ""),
        calibration=state_file,
        registry=registry_file(rehearsal),
    )
    del model

    # ---- predict (private test, calibrated) ---------------------------------- #
    check_deadline(clock(), kickoff, rehearsal, "before writing projections")
    t0 = time.perf_counter()
    info = {
        "method": "walk_forward",
        "calibration_version": state.meta["calibration_version"],
        "state_path": rel(state_file),
        "state_sha256": state_sha,
        "history_rows": state.meta["rows"],
        "history_first": state.meta["history_first"],
        "history_last": state.meta["history_last"],
    }
    result = run_predict(
        season=season,
        week=week,
        rest_of_season=False,
        n_sims=n_sims,
        now=as_of,
        data=data,
        ctx=ctx,
        private_test=True,
        model_version=version,
        rehearsal=rehearsal,
        calibrator=lambda frame: chain_v2.calibrate(state, frame),
        calibration_info=info,
    )
    if result is None:
        raise RuntimeError(f"no games left to predict in {season} week {week}")
    timings["predict"] = time.perf_counter() - t0
    run_manifest = json.loads((result.path / "_manifest.json").read_text(encoding="utf-8"))

    # ---- commitment ----------------------------------------------------------- #
    check_deadline(clock(), kickoff, rehearsal, "before writing the commitment")
    model_files = file_records(tree_files(artifact), "model_artifact")
    pred_files = file_records(tree_files(result.path), "predictions")
    wf_files = file_records(hist["written"], "walkforward_part")
    seconds = time.perf_counter() - t_start
    peak = peak_memory_mb()
    created_at = _utc(clock())
    payload = {
        "format": COMMITMENT_FORMAT,
        "model": chain_v2.MODEL_NAME,
        "season": season,
        "week": week,
        "rehearsal": rehearsal,
        "environment": "private_test",
        "approval_status": "not approved",
        "label": "not approved",
        "decision": "D-098",
        "as_of": as_of.isoformat(),
        "created_at": created_at.isoformat(),
        "first_kickoff": kickoff.isoformat(),
        "seconds_before_kickoff": round((kickoff - created_at).total_seconds(), 1),
        "model_version": version,
        "registry": {
            "path": rel(registry_file(rehearsal) or registry.registry_path()),
            "status": entry["status"],
            "name": entry["name"],
            "calibration": entry.get("calibration"),
        },
        "model_artifact": {
            "path": rel(artifact),
            "sha256": digest_of(model_files),
            "files": {r["path"]: r["sha256"] for r in model_files},
            "cutoff": [season, week],
            "train_manifest_sha256": sha256_file(artifact / "train_manifest.json"),
        },
        "calibrator_state": {
            "path": rel(state_file),
            "sha256": state_sha,
            "calibration_version": state.meta["calibration_version"],
            "quantile_calibrator": state.meta["quantile_calibrator"],
            "isotonic": state.meta["isotonic"],
            "rows": state.meta["rows"],
            "history_first": state.meta["history_first"],
            "history_last": state.meta["history_last"],
            "cutoff": state.meta["cutoff"],
        },
        "calibration_history": {
            "sha256": digest_of(hist["sources"]),
            "weeks": len(hist["sources"]),
            "sources": hist["sources"],
        },
        "predictions": {
            "path": rel(result.path),
            "run_id": result.run_id,
            "manifest_sha256": sha256_file(result.path / "_manifest.json"),
            "rows": result.rows,
            "skipped_final_games": result.skipped_final_games,
        },
        "outputs": [*pred_files, *model_files, *wf_files],
        "snapshots": {
            "features": data.snapshot_ids,
            "processed_live_rows": (run_manifest.get("inputs") or {}).get("processed"),
            "processed_at_start": processed_at_start,
        },
        "seeds": run_manifest.get("seeds"),
        "n_sims": n_sims,
        "pins": pins,
        "code": code,
        "holdout_release": release_info(),
        "build": {
            "seconds": round(seconds, 1),
            "peak_memory_mb": peak,
            "timings": {k: round(v, 1) for k, v in timings.items()},
            "python": sys.version.split()[0],
        },
    }
    digest = write_commitment(commit_file, payload)
    log.info("committed %s %d w%02d: %s (%s)", version, season, week, commit_file, digest)
    return BuildResult(
        season=season,
        week=week,
        rehearsal=rehearsal,
        model_version=version,
        commitment=commit_file,
        digest=digest,
        run_dir=result.path,
        seconds=seconds,
        peak_memory_mb=peak,
        info={"timings": timings},
    )


# --------------------------------------------------------------------------- #
# Identity checks (stored rows against a recomputation)
# --------------------------------------------------------------------------- #


def verify_raw_identity(weeks: Sequence[tuple[int, int]], data: Any = None) -> list[dict[str, Any]]:
    """Recompute stored walk-forward weeks and compare every column of the raw rows exactly."""
    if data is None:
        from ffml.models.chain.data import ChainData

        data = ChainData(purpose=PURPOSE)
    out = []
    for season, week in weeks:
        stored = pl.read_parquet(stored_part(season, week))
        fresh, info = recompute_week(data, season, week)
        keys = ["scoring", "entity_id", "game_id"]
        cmp = chain_v2.frames_equal(fresh, stored, list(stored.columns), keys)
        out.append(
            {
                "season": season,
                "week": week,
                "source": stored_source(season, week).run_id,
                "identical_frame": fresh.equals(stored),
                "columns_equal": cmp["equal"],
                "rows": stored.height,
                "diff": {c: v for c, v in cmp["columns"].items() if not v["equal"]},
                "fit_seconds": info["fit_seconds"],
                "predict_seconds": info["predict_seconds"],
                "features": info["features"],
            }
        )
    return out


CAL_COMPARE: tuple[str, ...] = (
    "pred_q05",
    "pred_q10",
    "pred_q25",
    "pred_q50",
    "pred_q75",
    "pred_q90",
    "pred_q95",
    "boom_prob",
    "bust_prob",
    "cal_q_source",
    "cal_n",
    "cal_boom_source",
    "cal_bust_source",
)


def verify_calibrator_identity(
    targets: Sequence[tuple[int, int]], run: str = "default_20261102"
) -> list[dict[str, Any]]:
    """State from stored raw rows of earlier weeks, applied to a week, against the run's ``+cal`` rows."""
    frame = pl.read_parquet(backtests_dir() / run / "predictions.parquet")
    raw = frame.filter(pl.col("model") == RAW_KEY)
    cal = frame.filter(pl.col("model") == f"{RAW_KEY}+cal")
    out = []
    for season, week in targets:
        rows = raw.filter((pl.col("season") == season) & (pl.col("week") == week))
        cutoff = rows["week_cutoff"].drop_nulls().min()
        state = chain_v2.calibrator_state(raw, (season, week), cutoff)
        applied = state.apply(rows, lower_bound="pos_min", upper_bound="pos_max")
        stored = cal.filter((pl.col("season") == season) & (pl.col("week") == week))
        cmp = chain_v2.frames_equal(
            applied.select("scoring", "entity_id", *CAL_COMPARE),
            stored.select("scoring", "entity_id", *CAL_COMPARE),
            list(CAL_COMPARE),
            ["scoring", "entity_id"],
        )
        out.append(
            {
                "season": season,
                "week": week,
                "rows": stored.height,
                "equal": cmp["equal"],
                "diff": {c: v for c, v in cmp["columns"].items() if not v["equal"]},
                "state_rows": state.meta["rows"],
            }
        )
    return out


__all__ = [
    "COMMITMENT_FILE",
    "EXIT_ALREADY_COMMITTED",
    "EXIT_DEADLINE",
    "EXIT_FAILED",
    "EXIT_OK",
    "EXIT_REFUSED",
    "STORED_SOURCES",
    "AlreadyCommitted",
    "BuildResult",
    "DeadlinePassed",
    "WeekNotScheduled",
    "build_week",
    "calibration_history",
    "check_deadline",
    "code_state",
    "commitment_digest",
    "commitment_path",
    "first_kickoff",
    "peak_memory_mb",
    "recompute_week",
    "verify_calibrator_identity",
    "verify_commitment",
    "verify_raw_identity",
    "with_outcomes",
    "write_commitment",
]
