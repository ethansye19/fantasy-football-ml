"""Distribution summaries, boom/bust and start/sit probabilities."""

from __future__ import annotations

import numpy as np
import polars as pl
import pytest

from ffml.scoring import ScoringRules
from ffml.sim import prob_beats, simulate, summarize, synthetic_slate
from ffml.sim.summarize import SUMMARY_COLUMNS

PPR = ScoringRules.from_preset("ppr")
STAR = "H00-WR06"
DEEP = "H00-WR13"


@pytest.fixture(scope="module")
def slate_sims() -> pl.DataFrame:
    """Eight teams (64 WRs); one WR made dominant."""
    tp, pp, dp = synthetic_slate(n_games=4, seed=1)
    pp = pp.with_columns(
        pl.when(pl.col("entity_id") == STAR)
        .then(pl.lit(1.0))
        .otherwise(pl.col("availability_prob"))
        .alias("availability_prob"),
        pl.when(pl.col("entity_id") == STAR)
        .then(pl.lit(45.0))
        .otherwise(pl.col("yards_per_rec"))
        .alias("yards_per_rec"),
        pl.when(pl.col("entity_id") == STAR)
        .then(pl.lit(0.95))
        .otherwise(pl.col("catch_rate"))
        .alias("catch_rate"),
    )
    return simulate(tp, pp, dp, n_sims=1000, seed=13)


def test_summary_columns_and_quantiles(slate_sims):
    out = summarize(slate_sims, PPR)
    assert out.columns[: len(SUMMARY_COLUMNS)] == list(SUMMARY_COLUMNS)
    assert {"season", "week", "team", "game_id", "active_prob", "n_sims"} <= set(out.columns)
    n_entities = slate_sims.select("entity_id").n_unique()
    assert out.height == n_entities
    row = out.filter(pl.col("entity_id") == "A01-QB00").row(0, named=True)
    pts = PPR.score(slate_sims.filter(pl.col("entity_id") == "A01-QB00")).to_numpy()
    assert row["mean"] == pytest.approx(pts.mean())
    assert [row["q10"], row["q50"], row["q90"]] == pytest.approx(
        list(np.quantile(pts, [0.1, 0.5, 0.9]))
    )
    assert (out["q10"] <= out["q50"]).all() and (out["q50"] <= out["q90"]).all()
    assert out["boom_prob"].is_between(0, 1).all() and out["bust_prob"].is_between(0, 1).all()


def test_dominant_player_booms_and_deep_backup_busts(slate_sims):
    out = summarize(slate_sims, PPR)
    star = out.filter(pl.col("entity_id") == STAR).row(0, named=True)
    deep = out.filter(pl.col("entity_id") == DEEP).row(0, named=True)
    assert star["boom_prob"] > 0.95 and star["bust_prob"] < 0.01
    assert deep["boom_prob"] < 0.05 and deep["bust_prob"] > 0.5
    # per sim, the top-N ranks boom about N players (ties at the line can add a few)
    wr = out.filter(pl.col("position") == "WR")
    assert wr["boom_prob"].sum() == pytest.approx(12, abs=1.0)
    # a player who never plays always busts (his zero ties straddle the line)
    tp, pp, dp = synthetic_slate(n_games=4, seed=1)
    pp = pp.with_columns(
        pl.when(pl.col("entity_id") == DEEP)
        .then(0.0)
        .otherwise(pl.col("availability_prob"))
        .alias("availability_prob")
    )
    out0 = summarize(simulate(tp, pp, dp, n_sims=300, seed=2), PPR)
    benched = out0.filter(pl.col("entity_id") == DEEP).row(0, named=True)
    assert benched["bust_prob"] == 1.0 and benched["boom_prob"] == 0.0 and benched["mean"] == 0.0


def test_thresholds_are_configurable(slate_sims):
    out = summarize(slate_sims, PPR, thresholds={"WR": (1, 2)})
    assert out.filter(pl.col("position") != "WR")["boom_prob"].is_null().all()
    wr = out.filter(pl.col("position") == "WR")
    assert wr["boom_prob"].sum() == pytest.approx(1.0, abs=0.05)
    assert out.filter(pl.col("entity_id") == STAR)["boom_prob"].item() > 0.5


def test_scoring_changes_the_summary(slate_sims):
    ppr = summarize(slate_sims, PPR).filter(pl.col("position") == "TE")
    std = summarize(slate_sims, ScoringRules.from_preset("std")).filter(pl.col("position") == "TE")
    j = ppr.join(std, on="entity_id", suffix="_std")
    assert (j["mean"] >= j["mean_std"]).all()
    assert (j["mean"] > j["mean_std"]).any()


def test_prob_beats(slate_sims):
    assert prob_beats(slate_sims, PPR, STAR, DEEP) > 0.95
    assert prob_beats(slate_sims, PPR, STAR, STAR) == pytest.approx(0.5)
    a, b = "H00-WR07", "A00-WR07"
    assert prob_beats(slate_sims, PPR, a, b) + prob_beats(slate_sims, PPR, b, a) == pytest.approx(
        1.0
    )
    with pytest.raises(KeyError):
        prob_beats(slate_sims, PPR, STAR, "nobody")
