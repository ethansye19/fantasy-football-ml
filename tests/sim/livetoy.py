"""Toy inputs for the live-test build tests: calibration history, bounds, fixture context."""

from __future__ import annotations

import hashlib
from dataclasses import replace
from datetime import UTC, datetime, timedelta
from pathlib import Path

import numpy as np
import polars as pl

FIXTURES = Path(__file__).resolve().parents[1] / "fixtures" / "features"
POSITIONS = {"QB": 10, "RB": 14, "WR": 18, "TE": 6, "K": 4, "DST": 4}
QCOLS = ["pred_q05", "pred_q10", "pred_q25", "pred_q50", "pred_q75", "pred_q90", "pred_q95"]
MULT = np.array([0.1, 0.3, 0.6, 1.0, 1.4, 1.8, 2.1])
SCORINGS = ("ppr", "half", "std")


def toy_history(seasons=(2021, 2022, 2023), last=(2023, 4), seed=0) -> pl.DataFrame:
    """Raw chain rows (label chain_v1) with the harness outcome columns."""
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for week in range(1, 19):
            if (season, week) > last:
                break
            first = datetime(season, 9, 4, 0, 15, tzinfo=UTC) + timedelta(days=7 * (week - 1))
            for pos, n in POSITIONS.items():
                for i in range(n):
                    loc = rng.gamma(2.0, 4.0)
                    active = bool(rng.random() < 0.9)
                    for scoring in SCORINGS:
                        m = {"ppr": 1.0, "half": 0.9, "std": 0.8}[scoring]
                        q = np.round(np.maximum(0.0, loc * m * MULT + rng.normal(0, 0.3, 7)), 1)
                        q = np.maximum.accumulate(np.where(q < 0.6, 0.0, q))
                        boom, bust = float(rng.random() * 0.6), float(rng.random())
                        actual = float(np.round(max(0.0, rng.gamma(2.0, loc * m / 2) - 1.0)))
                        rows.append(
                            {
                                "entity_id": f"{pos}{i:03d}",
                                "position": pos,
                                "team": f"T{i % 8}",
                                "game_id": f"{season}_{week:02d}_G{i % 8}",
                                "season": season,
                                "week": week,
                                "scoring": scoring,
                                "model": "chain_v1",
                                "pred_mean": float(q[3]),
                                **{c: float(v) for c, v in zip(QCOLS, q, strict=True)},
                                "boom_prob": boom,
                                "bust_prob": bust,
                                "actual": actual if active else 0.0,
                                "active": active,
                                "boom_actual": float(rng.random() < boom) if active else None,
                                "bust_actual": float(rng.random() < bust) if active else None,
                                "kickoff_utc": first + timedelta(hours=0 if i % 4 == 0 else 72),
                                "week_cutoff": first,
                                "pos_min": -2.0,
                                "pos_max": 60.0,
                            }
                        )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def toy_bounds() -> pl.DataFrame:
    return pl.DataFrame(
        [
            {"scoring": s, "position": p, "pos_min": -2.0, "pos_max": 60.0}
            for s in SCORINGS
            for p in POSITIONS
        ]
    )


def history_fn(tmp_path: Path):
    """A ``build_week(history_fn=...)`` that serves the toy history from one hashed file."""
    calls: list[tuple[int, int]] = []

    def fn(season, week, data, *, rehearsal=False, store=None):
        calls.append((season, week))
        hist = toy_history(last=(season, week - 1))
        path = tmp_path / f"toy_history_{season}w{week:02d}.parquet"
        hist.write_parquet(path)
        sha = hashlib.sha256(path.read_bytes()).hexdigest()
        return {
            "history": hist,
            "bounds": toy_bounds(),
            "sources": [
                {"season": 0, "week": 0, "origin": "toy", "path": str(path), "sha256": sha}
            ],
            "written": [],
        }

    fn.calls = calls  # type: ignore[attr-defined]
    return fn


def week5_ctx():
    """Fixture feature context with every 2023 week 5+ game still to play."""
    from ffml.features.data import FeatureContext

    ctx = FeatureContext.from_dir(FIXTURES)
    g = ctx.dim_game.with_columns(
        pl.when((pl.col("season") == 2023) & (pl.col("week") >= 5))
        .then(pl.lit("scheduled"))
        .otherwise(pl.col("status"))
        .alias("status")
    )
    return replace(ctx, dim_game=g, cache={})
