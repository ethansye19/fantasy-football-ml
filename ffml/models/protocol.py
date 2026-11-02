"""Interface every weekly model implements (orchestrator contract for Phase 3 wave two).

The walk-forward harness (``ffml/eval/harness.py``) drives models only through
this protocol, so the structural chain, the transparent baselines, and the
direct challenger are evaluated identically.

Walk-forward semantics
----------------------
``fit(cutoff)`` trains on every regular-season row strictly before the cutoff
``(season, week)``: all rows of earlier seasons plus rows of ``season`` with a
smaller week. Feature rows are read only through ``ffml.features.access`` so the
holdout guard applies. ``predict(season, week, scorings)`` then predicts that
single week. The harness calls ``fit`` again before each predicted week (or
every ``refit_every`` weeks; the reported backtest uses 1).

Prediction frame
----------------
``predict`` returns one row per entity per scoring with the columns in
``PREDICTION_COLUMNS``; optional columns are listed in ``OPTIONAL_COLUMNS``.
The harness joins actual outcomes, active flags, and boom/bust outcomes; models
never see actuals for the predicted week. ``entity_id`` is the gsis player id,
or the team code for DST. Quantile columns must be monotone non-decreasing.
"""

from __future__ import annotations

from collections.abc import Mapping
from typing import Protocol, runtime_checkable

import polars as pl

from ffml.scoring.rules import ScoringRules

#: Required prediction columns (dtypes: strings, Int32 season/week, Float64 predictions).
PREDICTION_COLUMNS: tuple[str, ...] = (
    "entity_id",
    "position",
    "team",
    "game_id",
    "season",
    "week",
    "scoring",
    "model",
    "pred_mean",
    "pred_q05",
    "pred_q10",
    "pred_q25",
    "pred_q50",
    "pred_q75",
    "pred_q90",
    "pred_q95",
)

#: Optional columns a model may add (the chain adds all of them).
OPTIONAL_COLUMNS: tuple[str, ...] = (
    "boom_prob",  # P(top-N positional finish this week), D-017
    "bust_prob",  # P(finish outside the startable range)
    "active_prob",  # P(player is active), from the availability layer
    "pred_mean_if_active",
    "pred_q50_if_active",
    "drivers",  # JSON string with signed point contributions
)

QUANTILE_LEVELS: tuple[float, ...] = (0.05, 0.10, 0.25, 0.50, 0.75, 0.90, 0.95)


@runtime_checkable
class WeeklyModel(Protocol):
    """A model the walk-forward harness can fit and query week by week."""

    #: Stable model name used in reports (for example ``"chain_v1"``).
    name: str

    def fit(self, cutoff: tuple[int, int]) -> WeeklyModel:
        """Train on all regular-season rows strictly before ``cutoff = (season, week)``."""
        ...

    def predict(
        self, season: int, week: int, scorings: Mapping[str, ScoringRules]
    ) -> pl.DataFrame:
        """Predict every entity with a scheduled game in ``(season, week)``."""
        ...


def validate_predictions(frame: pl.DataFrame) -> pl.DataFrame:
    """Check a prediction frame against the contract; returns it unchanged or raises."""
    missing = [c for c in PREDICTION_COLUMNS if c not in frame.columns]
    if missing:
        raise ValueError(f"prediction frame is missing columns: {missing}")
    qcols = [f"pred_q{int(round(q * 100)):02d}" for q in QUANTILE_LEVELS]
    bad = frame.filter(
        pl.any_horizontal(
            [pl.col(a) > pl.col(b) + 1e-9 for a, b in zip(qcols, qcols[1:], strict=False)]
        )
    )
    if bad.height:
        raise ValueError(f"{bad.height} prediction rows have non-monotone quantiles")
    dup = frame.select(["entity_id", "season", "week", "scoring", "model"]).is_duplicated().sum()
    if dup:
        raise ValueError(f"{dup} duplicate (entity, season, week, scoring, model) rows")
    return frame
