"""Ablation variants of the chain (MODELING.md section 11: no Vegas lines, no NGS, depth timing).

``ChainModel(ablation=..., exclude_features=..., player_features_dataset=...)``
removes feature columns from every frame the chain fits or predicts on, so no
structural baseline or LightGBM matrix can see them. The defaults
(``ablation="none"``, no exclusions, ``player_week``) change nothing.

Exclusion patterns are column-name prefixes (an exact name is its own prefix)
and only apply to feature-style columns, whose names contain ``__``: keys,
targets and the chain's derived columns are never removed.

Presets
-------
``no_vegas``
    :data:`LINE_COLUMNS`, the only feature columns built from the schedule's
    spread and total (``ffml/features/context.py``). Lagged neutral-script
    windows use the play-by-play ``wp`` (the non-Vegas win probability) and stay.
    Fallbacks: the team-touchdown and two-point structural baselines use the
    team's L5 points (then prior-season points) in place of the implied total;
    the pass-rate spread term drops out (``b = 0``); and the simulation's game
    draw is centred on a lagged team-strength line,
    ``implied = (team__points__ew + def__points_allowed__ew) / 2`` for each side
    (prior season, then the league mean, when null), with total and margin sds
    re-estimated from the training window's residuals against that line.
``no_ngs``
    :data:`NGS_PREFIXES`: every Next Gen Stats feature (``eff__ngs_*``, 16
    columns in feature version 0.2.0). No structural baseline reads them.
"""

from __future__ import annotations

import hashlib
from collections.abc import Iterable

import numpy as np
import polars as pl

LINE_COLUMNS: tuple[str, ...] = (
    "ctx__spread",
    "ctx__total_line",
    "ctx__implied_team_total",
    "ctx__implied_opp_total",
    "ctx__favorite_flag",
)
NGS_PREFIXES: tuple[str, ...] = ("eff__ngs_",)
ABLATIONS: dict[str, tuple[str, ...]] = {
    "none": (),
    "no_vegas": LINE_COLUMNS,
    "no_ngs": NGS_PREFIXES,
}
DEFAULT_PLAYER_DATASET = "player_week"
STRENGTH_SOURCES = (
    ("team__points__ew", "team__points__ps"),
    ("def__points_allowed__ew", "def__points_allowed__ps"),
)


def resolve_exclusions(ablation: str, exclude_features: Iterable[str] = ()) -> tuple[str, ...]:
    if ablation not in ABLATIONS:
        raise ValueError(f"unknown ablation {ablation!r}; choose from {sorted(ABLATIONS)}")
    return tuple(dict.fromkeys((*ABLATIONS[ablation], *exclude_features)))


def is_excluded(name: str, patterns: tuple[str, ...]) -> bool:
    return "__" in name and any(name.startswith(p) for p in patterns)


def drop_excluded(frame: pl.DataFrame, patterns: tuple[str, ...]) -> pl.DataFrame:
    """``frame`` without the excluded feature columns (the same object when nothing matches)."""
    if not patterns:
        return frame
    gone = [c for c in frame.columns if is_excluded(c, patterns)]
    return frame.drop(gone) if gone else frame


def lines_excluded(patterns: tuple[str, ...]) -> bool:
    """True when any column the simulation's game draw reads is excluded."""
    return any(is_excluded(c, patterns) for c in LINE_COLUMNS[:3])


def variant_tokens(
    ablation: str, exclude_features: Iterable[str], player_dataset: str
) -> list[str]:
    tokens = [] if ablation == "none" else [ablation]
    extra = sorted(set(exclude_features))
    if extra:
        tokens.append("excl" + hashlib.sha1("|".join(extra).encode()).hexdigest()[:8])
    if player_dataset != DEFAULT_PLAYER_DATASET:
        tokens.append(player_dataset.removeprefix("player_week_"))
    return tokens


def _coalesce(frame: pl.DataFrame, names: tuple[str, ...], default: float) -> pl.Expr:
    present = [pl.col(n).cast(pl.Float64) for n in names if n in frame.columns]
    return pl.coalesce([*present, pl.lit(default)])


def strength_lines(team_rows: pl.DataFrame, points_mean: float) -> pl.DataFrame:
    """Team rows with line columns rebuilt from lagged points (``no_vegas`` game draw).

    Same conventions as the schedule features: ``ctx__spread`` is positive for
    the underdog, ``ctx__implied_team_total = total / 2 - spread / 2``.
    """
    own = 0.5 * (
        _coalesce(team_rows, STRENGTH_SOURCES[0], points_mean)
        + _coalesce(team_rows, STRENGTH_SOURCES[1], points_mean)
    )
    t = team_rows.with_columns(own.alias("_it"))
    opp = t.select("game_id", pl.col("team").alias("opponent"), pl.col("_it").alias("_io"))
    t = t.join(opp, on=["game_id", "opponent"], how="left").with_columns(
        pl.col("_io").fill_null(points_mean)
    )
    return t.with_columns(
        pl.col("_it").alias("ctx__implied_team_total"),
        pl.col("_io").alias("ctx__implied_opp_total"),
        (pl.col("_it") + pl.col("_io")).alias("ctx__total_line"),
        (pl.col("_io") - pl.col("_it")).alias("ctx__spread"),
    ).drop("_it", "_io")


def strength_residual_sds(train_team_rows: pl.DataFrame, points_mean: float) -> tuple[float, float]:
    """(total sd, margin sd) of actual scores around the strength line, one row per game."""
    t = strength_lines(train_team_rows, points_mean)
    pts = t.select(
        "game_id", pl.col("team").alias("opponent"), pl.col("y_points").alias("_opp_pts")
    )
    g = (
        t.join(pts, on=["game_id", "opponent"], how="inner")
        .filter(pl.col("home").cast(pl.Boolean))
        .drop_nulls(["y_points", "_opp_pts"])
    )
    if g.height < 50:
        return 13.2, 12.8
    total = (g["y_points"] + g["_opp_pts"] - g["ctx__total_line"]).to_numpy()
    margin = (g["y_points"] - g["_opp_pts"] + g["ctx__spread"]).to_numpy()
    return float(np.std(total)), float(np.std(margin))


__all__ = [
    "ABLATIONS",
    "LINE_COLUMNS",
    "NGS_PREFIXES",
    "drop_excluded",
    "is_excluded",
    "lines_excluded",
    "resolve_exclusions",
    "strength_lines",
    "strength_residual_sds",
    "variant_tokens",
]
