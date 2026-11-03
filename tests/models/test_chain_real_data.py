"""Slow end-to-end check of the chain on the real feature datasets (2023 week 10).

Skipped unless ``data/features`` holds the three datasets. Fits at cutoff
(2023, 10) with the committed stage selection, predicts 2023 week 10 and
checks coverage of active skill players and plausible medians.
"""

from __future__ import annotations

import os
import time

import polars as pl
import pytest

from ffml.config import get_settings
from ffml.models.chain import ChainModel
from ffml.models.protocol import validate_predictions
from ffml.scoring import ScoringRules
from ffml.scoring.actuals import actual_statlines

pytestmark = pytest.mark.slow


def _features_available() -> bool:
    root = get_settings().features_dir
    return all((root / d / "LATEST").exists() for d in ("player_week", "team_week", "dst_week"))


@pytest.mark.skipif(not _features_available(), reason="data/features not built")
def test_chain_2023_week10_end_to_end():
    ppr = ScoringRules.from_preset("ppr")
    t0 = time.perf_counter()
    model = ChainModel(n_sims=2000).fit((2023, 10))
    fit_s = time.perf_counter() - t0
    t1 = time.perf_counter()
    out = validate_predictions(model.predict(2023, 10, {"ppr": ppr}))
    predict_s = time.perf_counter() - t1
    print(f"fit {fit_s:.1f}s predict {predict_s:.1f}s")
    # The performance budget is enforced only on an otherwise idle machine
    # (FFML_ENFORCE_TIMING=1); concurrent backtests make wall time meaningless.
    if os.environ.get("FFML_ENFORCE_TIMING") == "1":
        assert fit_s < 90 and predict_s < 20

    act = actual_statlines([2023], "validate").filter(pl.col("week") == 10)
    active = act.filter(pl.col("played") & pl.col("position").is_in(["QB", "RB", "WR", "TE"]))
    missing = active.join(out, on="entity_id", how="anti")
    assert missing.height == 0, missing.select("entity_id", "position", "team").head(10)

    med = (
        out.join(active.select("entity_id"), on="entity_id", how="semi")
        .group_by("position")
        .agg(pl.col("pred_q50").median().alias("median"))
    )
    m = dict(zip(med["position"], med["median"], strict=True))
    print(m)
    assert 8 <= m["QB"] <= 25
    assert 2 <= m["RB"] <= 20
    assert 2 <= m["WR"] <= 20
    assert 1 <= m["TE"] <= 15
