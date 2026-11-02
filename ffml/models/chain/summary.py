"""Prediction-frame summaries of simulated stat lines (protocol columns, D-017).

For one scoring, every simulated stat line is scored. Positional ranks are
computed within each simulation among all simulated entities of the position
(inactive draws score zero and are ranked like any other draw; ``min`` rank
for boom, ``max`` rank for bust, as in :func:`ffml.sim.summarize.summarize`).
The protocol's ``pred_*`` columns, ``boom_prob`` and ``bust_prob`` are then
taken over the simulations in which the entity is active (conditional on
active); ``active_prob`` is the share of active simulations and
``pred_mean_uncond`` / ``pred_q50_uncond`` use every simulation with inactive
draws at zero points.

Entities with fewer than ``MIN_ACTIVE_SIMS`` active simulations take their
conditional columns from a second simulation in which they are forced active
(other players unchanged), so that every row has a stable conditional
distribution.
"""

from __future__ import annotations

import polars as pl

from ffml.models.protocol import QUANTILE_LEVELS
from ffml.scoring.rules import ScoringRules
from ffml.sim.summarize import BOOM_BUST_THRESHOLDS, score_sims

MIN_ACTIVE_SIMS = 100
QCOLS: tuple[str, ...] = tuple(f"pred_q{int(round(q * 100)):02d}" for q in QUANTILE_LEVELS)


def ranked(sims: pl.DataFrame, rules: ScoringRules) -> pl.DataFrame:
    """Scored sims with boom and bust flags per simulation."""
    scored = score_sims(sims, rules)
    th = pl.DataFrame(
        {
            "position": list(BOOM_BUST_THRESHOLDS),
            "boom_top": [v[0] for v in BOOM_BUST_THRESHOLDS.values()],
            "bust_outside": [v[1] for v in BOOM_BUST_THRESHOLDS.values()],
        }
    )
    over = ["season", "week", "sim", "position"]
    return (
        scored.with_columns(
            pl.col("points").rank(method="min", descending=True).over(over).alias("rk_min"),
            pl.col("points").rank(method="max", descending=True).over(over).alias("rk_max"),
        )
        .join(th, on="position", how="left")
        .with_columns(
            (pl.col("rk_min") <= pl.col("boom_top")).cast(pl.Float64).alias("boom"),
            (pl.col("rk_max") > pl.col("bust_outside")).cast(pl.Float64).alias("bust"),
        )
    )


def conditional(scored: pl.DataFrame) -> pl.DataFrame:
    """Columns conditional on active, per entity."""
    act = scored.filter(pl.col("active"))
    return act.group_by(["entity_id", "game_id"]).agg(
        pl.col("points").mean().alias("pred_mean"),
        *[
            pl.col("points").quantile(q, interpolation="linear").alias(c)
            for q, c in zip(QUANTILE_LEVELS, QCOLS, strict=True)
        ],
        pl.col("boom").mean().alias("boom_prob"),
        pl.col("bust").mean().alias("bust_prob"),
        pl.len().alias("n_active"),
    )


def unconditional(scored: pl.DataFrame) -> pl.DataFrame:
    return scored.group_by(["entity_id", "game_id"]).agg(
        pl.col("position").first(),
        pl.col("team").first(),
        pl.col("season").first(),
        pl.col("week").first(),
        pl.col("active").mean().alias("active_prob"),
        pl.col("points").mean().alias("pred_mean_uncond"),
        pl.col("points").quantile(0.5, interpolation="linear").alias("pred_q50_uncond"),
    )


def summarize_chain(
    sims: pl.DataFrame,
    rules: ScoringRules,
    forced_sims: pl.DataFrame | None = None,
    forced_ids: list[str] | None = None,
) -> tuple[pl.DataFrame, pl.DataFrame]:
    """Protocol columns per entity (without labels) and the scored main sims."""
    scored = ranked(sims, rules)
    unc = unconditional(scored)
    cond = conditional(scored)
    if forced_sims is not None and forced_ids:
        fc = conditional(ranked(forced_sims, rules)).filter(pl.col("entity_id").is_in(forced_ids))
        cond = pl.concat([cond.filter(~pl.col("entity_id").is_in(forced_ids)), fc])
    out = unc.join(cond, on=["entity_id", "game_id"], how="left")
    out = out.with_columns(
        pl.when(pl.col("position").is_in(list(BOOM_BUST_THRESHOLDS)))
        .then(pl.col(c))
        .otherwise(None)
        .alias(c)
        for c in ("boom_prob", "bust_prob")
    )
    return out, scored


__all__ = ["MIN_ACTIVE_SIMS", "QCOLS", "conditional", "ranked", "summarize_chain", "unconditional"]
