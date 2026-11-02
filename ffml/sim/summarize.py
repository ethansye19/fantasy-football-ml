"""Per-player distributions from simulated stat lines under any scoring (D-003, D-017).

:func:`summarize` scores every simulated stat line with a
:class:`~ffml.scoring.rules.ScoringRules`, then reports per entity the mean,
10th, 50th and 90th percentiles (linear interpolation) and the boom and bust
probabilities. Boom is a top-N finish at the position within the same
simulation (all simulated entities of that position in the same season-week
compete); bust is finishing outside the startable range. Ties are resolved in the
player's favour for boom (``min`` rank: a tie for 12th is a top-12 finish)
and against him for bust (``max`` rank: a tie that straddles the startable
line, such as a block of zero-point games, is a bust). Inactive draws score
zero and are ranked like any other draw.

Default thresholds (MODELING.md section 5, documented in the model card):
boom top 6 QB, 12 RB, 12 WR, 6 TE, 6 K, 6 DST; bust outside 18 QB, 36 RB,
48 WR, 18 TE, 20 K, 20 DST.
"""

from __future__ import annotations

from collections.abc import Mapping

import polars as pl

from ffml.scoring.rules import ScoringRules

BOOM_BUST_THRESHOLDS: dict[str, tuple[int, int]] = {
    "QB": (6, 18),
    "RB": (12, 36),
    "WR": (12, 48),
    "TE": (6, 18),
    "K": (6, 20),
    "DST": (6, 20),
}
GROUP_KEYS: tuple[str, ...] = ("season", "week")
SUMMARY_COLUMNS: tuple[str, ...] = (
    "entity_id",
    "position",
    "mean",
    "q10",
    "q50",
    "q90",
    "boom_prob",
    "bust_prob",
)


def score_sims(player_sims: pl.DataFrame, scoring: ScoringRules) -> pl.DataFrame:
    """``player_sims`` key columns plus ``points`` under ``scoring``."""
    keys = [
        c
        for c in ("entity_id", "position", "season", "week", "game_id", "team", "sim", "active")
        if c in player_sims.columns
    ]
    return player_sims.select(*keys, scoring.expression(player_sims.columns).alias("points"))


def _thresholds_frame(thresholds: Mapping[str, tuple[int, int]]) -> pl.DataFrame:
    return pl.DataFrame(
        {
            "position": list(thresholds),
            "boom_top": [int(v[0]) for v in thresholds.values()],
            "bust_outside": [int(v[1]) for v in thresholds.values()],
        },
        schema={"position": pl.String, "boom_top": pl.Int64, "bust_outside": pl.Int64},
    )


def summarize(
    player_sims: pl.DataFrame,
    scoring: ScoringRules,
    thresholds: Mapping[str, tuple[int, int]] = BOOM_BUST_THRESHOLDS,
) -> pl.DataFrame:
    """Distribution summary per entity and season-week.

    Columns: ``entity_id, position, mean, q10, q50, q90, boom_prob, bust_prob``
    followed by ``season, week, team, game_id, active_prob, n_sims`` when the
    inputs carry them. Positions without thresholds get null boom and bust.
    """
    scored = score_sims(player_sims, scoring)
    group = [c for c in GROUP_KEYS if c in scored.columns]
    rank_over = [*group, "sim", "position"]
    scored = scored.with_columns(
        pl.col("points").rank(method="min", descending=True).over(rank_over).alias("pos_rank"),
        pl.col("points").rank(method="max", descending=True).over(rank_over).alias("pos_rank_max"),
    ).join(_thresholds_frame(thresholds), on="position", how="left")
    by = ["entity_id", "position", *group]
    extra_keys = [c for c in ("team", "game_id") if c in scored.columns]
    aggs = [
        pl.col("points").mean().alias("mean"),
        *[
            pl.col("points").quantile(q, interpolation="linear").alias(name)
            for name, q in (("q10", 0.1), ("q50", 0.5), ("q90", 0.9))
        ],
        (pl.col("pos_rank") <= pl.col("boom_top")).mean().alias("boom_prob"),
        (pl.col("pos_rank_max") > pl.col("bust_outside")).mean().alias("bust_prob"),
        *[pl.col(c).first() for c in extra_keys],
        pl.len().alias("n_sims"),
    ]
    if "active" in scored.columns:
        aggs.append(pl.col("active").mean().alias("active_prob"))
    out = scored.group_by(by).agg(aggs)
    out = out.with_columns(
        pl.when(pl.col("position").is_in(list(thresholds))).then(pl.col(c)).otherwise(None).alias(c)
        for c in ("boom_prob", "bust_prob")
    )
    tail = [c for c in (*group, *extra_keys, "active_prob", "n_sims") if c in out.columns]
    return out.select(*SUMMARY_COLUMNS, *tail).sort(
        [*group, "position", "mean"],
        descending=[
            *([False] * len(group)),
            False,
            True,
        ],
    )


def prob_beats(player_sims: pl.DataFrame, scoring: ScoringRules, a: str, b: str) -> float:
    """P(points of ``a`` > points of ``b``) over paired simulations, ties counted half.

    Pairs share the simulation index, so teammates and opponents keep their
    simulated correlation. Raises ``KeyError`` when either entity is absent.
    """
    pair = player_sims.filter(pl.col("entity_id").is_in([a, b]))
    scored = score_sims(pair, scoring)
    pa = scored.filter(pl.col("entity_id") == a).select("sim", pl.col("points").alias("a"))
    pb = scored.filter(pl.col("entity_id") == b).select("sim", pl.col("points").alias("b"))
    if pa.height == 0 or pb.height == 0:
        missing = [e for e, f in ((a, pa), (b, pb)) if f.height == 0]
        raise KeyError(f"entities not in player_sims: {missing}")
    joined = pa.join(pb, on="sim", how="inner")
    wins = (joined["a"] > joined["b"]).sum() + 0.5 * (joined["a"] == joined["b"]).sum()
    return float(wins / joined.height)


__all__ = [
    "BOOM_BUST_THRESHOLDS",
    "SUMMARY_COLUMNS",
    "prob_beats",
    "score_sims",
    "summarize",
]
