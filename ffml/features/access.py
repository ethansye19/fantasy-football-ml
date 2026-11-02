"""The only sanctioned way for model, evaluation and backtest code to read features.

Every read goes through :func:`ffml.holdout.assert_seasons_allowed`, so the
2025 holdout cannot be loaded unless ``FFML_HOLDOUT_UNLOCK`` is set, and each
unlocked access is logged (DECISIONS D-005).
"""

from __future__ import annotations

from collections.abc import Iterable

import polars as pl

from ffml.features.manifest import columns, feature_names
from ffml.features.spec import DATASETS
from ffml.holdout import assert_seasons_allowed


def _store():
    from ffml.config import get_settings
    from ffml.data.store import SnapshotStore

    return SnapshotStore(get_settings().features_dir)


def load_features(
    dataset: str,
    seasons: Iterable[int],
    purpose: str,
    *,
    columns_: list[str] | None = None,
    snapshot_id: str | None = None,
) -> pl.DataFrame:
    """Read ``dataset`` rows for ``seasons`` after the holdout guard has approved them."""
    if dataset not in DATASETS:
        raise ValueError(f"unknown feature dataset {dataset!r}; expected one of {DATASETS}")
    wanted = sorted({int(s) for s in seasons})
    if not wanted:
        raise ValueError("seasons is empty")
    assert_seasons_allowed(wanted, purpose)
    return _store().read(dataset, seasons=wanted, snapshot_id=snapshot_id, columns=columns_)


def training_frame(
    dataset: str,
    seasons: Iterable[int],
    purpose: str,
    regular_season_only: bool = True,
    played_only: bool | None = None,
    config: str = "primary",
) -> pl.DataFrame:
    """Keys, model inputs for ``config`` and targets, filtered for model fitting.

    ``played_only`` defaults to True for player_week (stage 2-4 models are
    conditional on the player being active) and is ignored for team-level
    datasets. ``config`` selects the model inputs: ``primary`` (no same-week
    injury inputs), ``final_report`` (adds ``avail__`` columns) or
    ``sensitivity`` (adds actual weather). Meta columns are dropped.
    """
    df = load_features(dataset, seasons, purpose)
    if regular_season_only:
        df = df.filter(pl.col("game_type") == "REG")
    if played_only is None:
        played_only = dataset == "player_week"
    if played_only and dataset == "player_week":
        df = df.filter(pl.col("y_played") == 1.0)
    inputs = set(feature_names(dataset, config))
    keep = [c.name for c in columns(dataset) if c.kind in ("key", "target") or c.name in inputs]
    return df.select([c for c in keep if c in df.columns])


__all__ = ["load_features", "training_frame"]
