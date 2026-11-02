"""Slow integration test: build 2024 from the local processed snapshots (skipped without data)."""

from __future__ import annotations

import polars as pl
import pytest

from ffml.config import get_settings
from ffml.features.build import BUILDERS
from ffml.features.data import FeatureContext
from ffml.features.manifest import columns
from ffml.features.spec import POSITIONS
from ffml.features.targets import historical_targets

PROCESSED = get_settings().processed_dir
HAVE_DATA = (PROCESSED / "fact_player_game" / "LATEST").exists()
MAX_NULL_SHARE = 0.60
SEASON = 2024
FACT_ATTR = {"player_week": "player_game", "team_week": "team_game", "dst_week": "dst_game"}


@pytest.mark.slow
@pytest.mark.skipif(not HAVE_DATA, reason="data/processed not present")
def test_build_2024_from_processed_tables():
    ctx = FeatureContext.from_store([SEASON])
    targets = historical_targets(ctx, [SEASON])
    for name, fn in BUILDERS.items():
        df = fn(ctx, targets[name])
        fact = getattr(ctx, FACT_ATTR[name])
        fact_rows = fact.filter(pl.col("season") == SEASON).height
        assert df.height == fact_rows, name
        assert (df["cutoff_ts"] <= df["kickoff_utc"]).all(), name
        assert df.columns == [c.name for c in columns(name)]

        scope = df.filter(pl.col("y_played") == 1) if name == "player_week" else df
        too_sparse = []
        for c in columns(name):
            if c.kind != "feature" or c.sparse:
                continue  # sparse = documented NGS, prior-team carry-over and similar columns
            sub = scope
            if name == "player_week" and c.positions and set(c.positions) != set(POSITIONS):
                sub = scope.filter(pl.col("position").is_in(list(c.positions)))
            share = sub[c.name].null_count() / max(sub.height, 1)
            if share > MAX_NULL_SHARE:
                too_sparse.append((c.name, round(share, 3)))
        assert too_sparse == [], f"{name}: {too_sparse}"
