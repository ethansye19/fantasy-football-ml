"""Build and write the point-in-time feature datasets (FE-3.1).

``run_features(seasons)`` loads the LATEST processed snapshots, builds the
historical target frames for ``seasons`` and writes three datasets to the
feature snapshot store (``settings.features_dir``), partitioned by season:

* ``player_week``: one row per fact_player_game row (REG and POST, including
  roster-only rows with ``y_played = 0``);
* ``team_week``: one row per fact_team_game row;
* ``dst_week``: one row per fact_dst_game row.

Each ``build_*`` function takes an explicit target frame so the predictor can
pass future rows from :func:`ffml.features.targets.future_targets`. Building
the holdout season is allowed (it is mechanical); reading it for modelling goes
through :mod:`ffml.features.access`, which enforces the holdout guard.

Every group builder works on a narrow frame of keys and returns its own
columns; the pieces are aligned on a row id and stacked horizontally, then
cast and ordered exactly as the manifest says.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Callable
from datetime import UTC, datetime

import polars as pl

from ffml.features.context import avail_columns, depth_features, game_context, injury_history
from ffml.features.data import FeatureContext
from ffml.features.dst import attach_dst
from ffml.features.manifest import DTYPES, columns
from ffml.features.player import attach_attributes, attach_player_windows, team_out, teammates_out
from ffml.features.spec import FEATURE_VERSION, PARAMS
from ffml.features.targets import KEYS, TARGET_NAMES, TIME_COLS, historical_targets
from ffml.features.team import attach_defense, attach_offense, qb_change_flag

log = logging.getLogger(__name__)

Piece = Callable[[pl.DataFrame, FeatureContext], pl.DataFrame]


def _base(targets: pl.DataFrame, dataset: str) -> pl.DataFrame:
    keys = KEYS[dataset]
    missing = [c for c in [*keys, *TIME_COLS] if c not in targets.columns]
    if missing:
        raise ValueError(f"{dataset} targets frame is missing {missing}")
    base = targets.select(*keys, *TIME_COLS).with_row_index("_rid")
    dup = base.select(
        keys[:3] if dataset == "player_week" else ["team", "season", "week"]
    ).is_duplicated()
    if dup.any():
        raise ValueError(f"{dataset} targets frame has {int(dup.sum())} duplicated key rows")
    return base.with_columns(pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32))


def _assemble(
    base: pl.DataFrame, targets: pl.DataFrame, pieces: list[pl.DataFrame], dataset: str
) -> pl.DataFrame:
    base = base.sort("_rid")
    seen = set(base.columns)
    parts = [base]
    for piece in pieces:
        if piece.height != base.height:
            raise RuntimeError(
                f"{dataset}: a feature piece has {piece.height} rows, expected {base.height}"
            )
        new = [c for c in piece.columns if c not in seen]
        seen.update(new)
        parts.append(piece.sort("_rid").select(new))
    ys = targets.select(TARGET_NAMES[dataset])
    parts.append(ys.with_row_index("_rid").sort("_rid").drop("_rid"))
    df = pl.concat(parts, how="horizontal").with_columns(
        pl.lit(FEATURE_VERSION).alias("feature_version")
    )
    cols = columns(dataset)
    missing = [c.name for c in cols if c.name not in df.columns]
    if missing:
        raise RuntimeError(
            f"{dataset}: builder did not produce {missing[:10]} ({len(missing)} total)"
        )
    out = df.select([pl.col(c.name).cast(DTYPES[c.dtype]) for c in cols])
    return out


def _run_pieces(
    base: pl.DataFrame, ctx: FeatureContext, steps: list[tuple[str, Piece]], dataset: str
) -> list[pl.DataFrame]:
    pieces = []
    for name, fn in steps:
        t0 = time.perf_counter()
        pieces.append(fn(base, ctx))
        log.debug("%s: %s in %.2fs", dataset, name, time.perf_counter() - t0)
    return pieces


def build_player_week(ctx: FeatureContext, targets: pl.DataFrame) -> pl.DataFrame:
    """Player-week features for the rows of ``targets`` (historical or future)."""
    base = _base(targets, "player_week")
    steps: list[tuple[str, Piece]] = [
        ("context", game_context),
        ("team", lambda b, c: attach_offense(b, c, by="team")),
        ("def", lambda b, c: attach_defense(b, c, by="opponent")),
        ("qb_change", lambda b, c: qb_change_flag(b, c, by="team")),
        ("player_windows", attach_player_windows),
        ("attributes", attach_attributes),
        ("injury_history", injury_history),
        ("depth", depth_features),
        ("avail", avail_columns),
        ("teammates_out", teammates_out),
    ]
    return _assemble(base, targets, _run_pieces(base, ctx, steps, "player_week"), "player_week")


def build_team_week(ctx: FeatureContext, targets: pl.DataFrame) -> pl.DataFrame:
    """Team-week features (stage 1): own offense as ``team__``, opponent defense as ``def__``."""
    base = _base(targets, "team_week")
    steps: list[tuple[str, Piece]] = [
        ("context", game_context),
        ("team", lambda b, c: attach_offense(b, c, by="team")),
        ("def", lambda b, c: attach_defense(b, c, by="opponent")),
        ("qb_change", lambda b, c: qb_change_flag(b, c, by="team")),
        ("team_out", lambda b, c: team_out(b, c, by="team")),
    ]
    return _assemble(base, targets, _run_pieces(base, ctx, steps, "team_week"), "team_week")


def build_dst_week(ctx: FeatureContext, targets: pl.DataFrame) -> pl.DataFrame:
    """DST-week features: opponent offense as ``team__``, own defense and DST events as ``def__``."""
    base = _base(targets, "dst_week")
    steps: list[tuple[str, Piece]] = [
        ("context", game_context),
        ("team", lambda b, c: attach_offense(b, c, by="opponent")),
        ("def", lambda b, c: attach_defense(b, c, by="team")),
        ("dst", lambda b, c: attach_dst(b, c, by="team")),
        ("qb_change", lambda b, c: qb_change_flag(b, c, by="opponent")),
        ("team_out", lambda b, c: team_out(b, c, by="opponent")),
    ]
    return _assemble(base, targets, _run_pieces(base, ctx, steps, "dst_week"), "dst_week")


BUILDERS: dict[str, Callable[[FeatureContext, pl.DataFrame], pl.DataFrame]] = {
    "player_week": build_player_week,
    "team_week": build_team_week,
    "dst_week": build_dst_week,
}


def build_all(ctx: FeatureContext, seasons: list[int] | None = None) -> dict[str, pl.DataFrame]:
    """Historical feature frames for ``seasons`` (all seasons in ``ctx`` when None)."""
    targets = historical_targets(ctx, seasons)
    return {name: fn(ctx, targets[name]) for name, fn in BUILDERS.items()}


def run_features(
    seasons: list[int], *, ctx: FeatureContext | None = None, store=None
) -> dict[str, str]:
    """Build and write ``player_week``, ``team_week`` and ``dst_week`` for ``seasons``.

    Returns the new snapshot id per dataset.
    """
    from ffml.config import get_settings
    from ffml.data.store import SnapshotStore

    seasons = sorted({int(s) for s in seasons})
    t_all = time.perf_counter()
    ctx = ctx or FeatureContext.from_store(seasons)
    log.info("loaded processed inputs in %.1fs", time.perf_counter() - t_all)
    store = store or SnapshotStore(get_settings().features_dir)
    targets = historical_targets(ctx, seasons)
    ids: dict[str, str] = {}
    for name, fn in BUILDERS.items():
        t0 = time.perf_counter()
        df = fn(ctx, targets[name])
        present = sorted(int(s) for s in df["season"].unique().to_list())
        frames = {s: df.filter(pl.col("season") == s) for s in present}
        cols = columns(name)
        meta = {
            "loader": "ffml.features.build.run_features",
            "loader_kwargs": {"seasons": seasons},
            "feature_version": FEATURE_VERSION,
            "feature_columns": [
                [c.name, c.dtype, c.kind]
                for c in cols
                if c.kind in ("feature", "sensitivity", "avail")
            ],
            "n_features": sum(c.kind == "feature" for c in cols),
            "params": PARAMS,
            "inputs": dict(ctx.snapshot_ids),
            "built_at": datetime.now(UTC).replace(microsecond=0).isoformat(),
        }
        if not frames:
            log.warning("%s: no rows for seasons %s; nothing written", name, seasons)
            continue
        ids[name] = store.write_snapshot(name, frames, meta)
        log.info(
            "%s: %d rows x %d columns (%d features) in %.1fs -> snapshot %s",
            name,
            df.height,
            df.width,
            meta["n_features"],
            time.perf_counter() - t0,
            ids[name],
        )
    log.info("features built in %.1fs", time.perf_counter() - t_all)
    return ids


__all__ = [
    "BUILDERS",
    "build_all",
    "build_dst_week",
    "build_player_week",
    "build_team_week",
    "run_features",
]
