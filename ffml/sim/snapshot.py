"""Prediction snapshots on disk (DATA_SCHEMA.md section 4).

Layout::

    data/predictions/season=<YYYY>/week=<WW>/run=<run_id>/
        player_sims.parquet      simulated stat lines (ffml.sim.engine output)
        player_summary.parquet   per-entity summary (ffml.sim.summarize output)
        team_env.parquet         team environment (optional)
        ros_sims.parquet         rest-of-season simulated stat lines (optional, ffml.sim.ros)
        ros_summary.parquet      rest-of-season totals per scoring preset (optional)
        _manifest.json           model_version, seeds, n_sims, inputs, file hashes
    data/predictions/LATEST      json: season, week, run_id, model_version, path

A run directory is written into a hidden staging directory and renamed into
place in one step, then ``LATEST`` is replaced through a temporary file, so a
reader never sees a half-written run. Runs are immutable: writing an existing
``(season, week, run_id)`` raises :class:`FileExistsError`.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
from collections.abc import Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ffml.config import get_settings

MANIFEST_NAME = "_manifest.json"
LATEST_NAME = "LATEST"
SIMS_FILE = "player_sims.parquet"
SUMMARY_FILE = "player_summary.parquet"
TEAM_ENV_FILE = "team_env.parquet"
REQUIRED_META: tuple[str, ...] = ("model_version", "seeds", "n_sims", "inputs")
_RUN_ID_RE = re.compile(r"^[A-Za-z0-9][A-Za-z0-9._-]{0,127}$")
_EXTRA_RE = re.compile(r"^[a-z0-9][a-z0-9_]{0,63}\.parquet$")
ROS_SIMS_FILE = "ros_sims.parquet"
ROS_SUMMARY_FILE = "ros_summary.parquet"


@dataclass(frozen=True)
class PredictionsSnapshot:
    season: int
    week: int
    run_id: str
    model_version: str
    path: Path
    manifest: dict[str, Any]
    player_sims: pl.DataFrame | None
    summary: pl.DataFrame
    team_env: pl.DataFrame | None


def _root(root: Path | str | None) -> Path:
    return Path(root) if root is not None else get_settings().predictions_dir


def run_dir(season: int, week: int, run_id: str, root: Path | str | None = None) -> Path:
    return _root(root) / f"season={int(season):04d}" / f"week={int(week):02d}" / f"run={run_id}"


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for block in iter(lambda: handle.read(1 << 20), b""):
            digest.update(block)
    return digest.hexdigest()


def _now() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def _write_json_atomic(path: Path, payload: Mapping[str, Any]) -> None:
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(
        json.dumps(payload, indent=2, default=str) + "\n", encoding="utf-8", newline="\n"
    )
    os.replace(tmp, path)


def write_predictions_snapshot(
    season: int,
    week: int,
    run_id: str,
    player_sims: pl.DataFrame,
    summary: pl.DataFrame,
    team_env: pl.DataFrame | None,
    meta: Mapping[str, Any],
    *,
    root: Path | str | None = None,
    update_latest: bool = True,
    extra_frames: Mapping[str, pl.DataFrame] | None = None,
) -> Path:
    """Write one immutable prediction run and (by default) point ``LATEST`` at it.

    ``meta`` must contain ``model_version``, ``seeds``, ``n_sims`` and
    ``inputs`` (for example input snapshot ids); other keys are kept under
    ``extra``. ``extra_frames`` maps further file names (``<name>.parquet``,
    for example the rest-of-season ``ros_sims.parquet``) to frames written
    into the same run with hashes in the manifest. Returns the run directory.
    """
    missing = [k for k in REQUIRED_META if k not in meta]
    if missing:
        raise ValueError(f"meta is missing {missing}")
    if not _RUN_ID_RE.match(run_id):
        raise ValueError(f"run_id {run_id!r} must match {_RUN_ID_RE.pattern}")
    for name in extra_frames or {}:
        if not _EXTRA_RE.match(name) or name in (SIMS_FILE, SUMMARY_FILE, TEAM_ENV_FILE):
            raise ValueError(f"extra frame name {name!r} must be a new <name>.parquet")
    base = _root(root)
    final = run_dir(season, week, run_id, base)
    if final.exists():
        raise FileExistsError(f"prediction run already exists: {final}")
    staging = final.with_name(f".staging-{final.name}-{os.getpid()}")
    if staging.exists():
        shutil.rmtree(staging)
    staging.mkdir(parents=True)
    try:
        files: dict[str, dict[str, Any]] = {}
        frames = {SIMS_FILE: player_sims, SUMMARY_FILE: summary}
        if team_env is not None:
            frames[TEAM_ENV_FILE] = team_env
        frames.update(extra_frames or {})
        for name, frame in frames.items():
            path = staging / name
            frame.write_parquet(path, compression="zstd")
            files[name] = {
                "rows": frame.height,
                "columns": frame.columns,
                "bytes": path.stat().st_size,
                "sha256": _sha256(path),
            }
        extra = {k: v for k, v in meta.items() if k not in REQUIRED_META}
        manifest = {
            "season": int(season),
            "week": int(week),
            "run_id": run_id,
            "model_version": str(meta["model_version"]),
            "seeds": meta["seeds"],
            "n_sims": int(meta["n_sims"]),
            "inputs": meta["inputs"],
            "written_at": _now(),
            "files": files,
            "extra": extra,
        }
        _write_json_atomic(staging / MANIFEST_NAME, manifest)
        os.replace(staging, final)
    except BaseException:
        shutil.rmtree(staging, ignore_errors=True)
        raise
    if update_latest:
        latest = {
            "season": int(season),
            "week": int(week),
            "run_id": run_id,
            "model_version": str(meta["model_version"]),
            "path": final.relative_to(base).as_posix(),
            "written_at": manifest["written_at"],
        }
        base.mkdir(parents=True, exist_ok=True)
        _write_json_atomic(base / LATEST_NAME, latest)
    return final


def read_predictions(
    season: int,
    week: int,
    run_id: str,
    *,
    root: Path | str | None = None,
    load_sims: bool = True,
) -> PredictionsSnapshot:
    """Read one prediction run; ``load_sims=False`` skips the large sample file."""
    path = run_dir(season, week, run_id, root)
    manifest_path = path / MANIFEST_NAME
    if not manifest_path.exists():
        raise FileNotFoundError(f"no prediction run at {path}")
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    sims = pl.read_parquet(path / SIMS_FILE) if load_sims else None
    team_path = path / TEAM_ENV_FILE
    return PredictionsSnapshot(
        season=int(manifest["season"]),
        week=int(manifest["week"]),
        run_id=manifest["run_id"],
        model_version=manifest["model_version"],
        path=path,
        manifest=manifest,
        player_sims=sims,
        summary=pl.read_parquet(path / SUMMARY_FILE),
        team_env=pl.read_parquet(team_path) if team_path.exists() else None,
    )


def read_latest_predictions(
    *, root: Path | str | None = None, load_sims: bool = True
) -> PredictionsSnapshot:
    """The run that ``LATEST`` points to."""
    base = _root(root)
    pointer = base / LATEST_NAME
    if not pointer.exists():
        raise FileNotFoundError(f"no {LATEST_NAME} under {base}")
    latest = json.loads(pointer.read_text(encoding="utf-8"))
    return read_predictions(
        latest["season"], latest["week"], latest["run_id"], root=base, load_sims=load_sims
    )


__all__ = [
    "LATEST_NAME",
    "MANIFEST_NAME",
    "ROS_SIMS_FILE",
    "ROS_SUMMARY_FILE",
    "PredictionsSnapshot",
    "read_latest_predictions",
    "read_predictions",
    "run_dir",
    "write_predictions_snapshot",
]
