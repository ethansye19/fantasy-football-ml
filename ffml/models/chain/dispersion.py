"""Simulation dispersion parameters estimated from training-window residuals only.

Each estimate is a method-of-moments match between the residual variance of
the chosen stage predictor and the variance the engine generates
(:mod:`ffml.sim.engine`), so the engine's own noise is not counted twice.
Residuals are in-sample for structural predictors (one or two fitted
numbers) and the early-stopping hold-out of the training window for LightGBM
(out-of-sample for the trees). All inputs come from rows before the cutoff.

- ``sd_plays`` by week bucket (weeks 1-4, 5+): the residual sd of plays (the
  engine's play draw has exactly that sd).
- ``pass_rate_conc``: residual variance of dropbacks per play minus the
  Binomial(plays) noise and the game-script shift
  ``(SCRIPT_PASS_COEF * mu * (1 - mu))^2``, matched to a Beta variance
  ``mu (1 - mu) / (conc + 1)``.
- ``share_conc`` per position (QB dropbacks, RB carries, WR and TE targets):
  ``sum (y - p)^2 = sum p (1 - p) / (conc + 1) + sum p (1 - p) / N`` with ``N``
  the team's attempts in that category (Multinomial noise).
- Gamma shapes per position for yards per reception and per carry: with
  ``n`` touches, ``Var(yards) = n m^2 / k + (n m s)^2`` where ``s`` is the
  team efficiency sd of the engine, so ``k = sum n m^2 / (sum r^2 - sum (n m
  s)^2)``.

Estimates are clipped to ranges the engine handles well and fall back to the
engine defaults when a cell has fewer than 200 rows.
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from ffml.sim.engine import PASS_EFF_SD, RUSH_EFF_SD, SCRIPT_PASS_COEF
from ffml.sim.params import DEFAULT_SHARE_CONC

MIN_ROWS = 200
SHARE_STAGE = {
    "QB": "share_dropback",
    "RB": "share_carry",
    "WR": "share_target",
    "TE": "share_target",
}
SHARE_DENOM = {
    "share_dropback": "tm_dropbacks",
    "share_carry": "tm_carries",
    "share_target": "tm_targets",
}
DEFAULTS: dict[str, Any] = {
    "sd_plays": {"early": 7.5, "late": 7.0},
    "pass_rate_conc": 60.0,
    "share_conc": dict(DEFAULT_SHARE_CONC),
    "ypr_shape": {"RB": 1.0, "WR": 1.0, "TE": 1.0},
    "ypc_shape": {"QB": 0.5, "RB": 0.5, "WR": 0.5},
}


def _arr(df: pl.DataFrame, name: str) -> np.ndarray:
    return df[name].cast(pl.Float64).fill_null(np.nan).to_numpy()


def plays_sd(frame: pl.DataFrame) -> dict[str, float]:
    out = dict(DEFAULTS["sd_plays"])
    r = _arr(frame, "y_plays") - _arr(frame, "pred")
    wk = _arr(frame, "week")
    for key, mask in (("early", wk <= 4), ("late", wk > 4)):
        ok = mask & ~np.isnan(r)
        if ok.sum() >= MIN_ROWS:
            out[key] = float(np.clip(np.sqrt(np.mean(r[ok] ** 2)), 3.0, 15.0))
    return out


def pass_rate_conc(frame: pl.DataFrame) -> float:
    y, p, n = _arr(frame, "y_pass_rate"), _arr(frame, "pred"), _arr(frame, "y_plays")
    ok = ~np.isnan(y) & ~np.isnan(p) & (n > 0)
    if ok.sum() < MIN_ROWS:
        return DEFAULTS["pass_rate_conc"]
    v = np.mean((y[ok] - p[ok]) ** 2)
    pq = np.mean(p[ok] * (1 - p[ok]))
    binom = np.mean(p[ok] * (1 - p[ok]) / n[ok])
    script = np.mean((SCRIPT_PASS_COEF * p[ok] * (1 - p[ok])) ** 2)
    beta_var = max(v - binom - script, pq / 2001.0)
    return float(np.clip(pq / beta_var - 1.0, 20.0, 2000.0))


def share_conc(frames: dict[str, pl.DataFrame]) -> dict[str, float]:
    out = dict(DEFAULTS["share_conc"])
    for pos, stage in SHARE_STAGE.items():
        f = frames.get(stage)
        if f is None:
            continue
        f = f.filter(pl.col("position") == pos)
        target = {"share_dropback": "y_dropback_share", "share_carry": "y_carry_share"}.get(
            stage, "y_target_share"
        )
        y, p, n = _arr(f, target), _arr(f, "pred"), _arr(f, SHARE_DENOM[stage])
        ok = ~np.isnan(y) & ~np.isnan(p) & (n > 0)
        if ok.sum() < MIN_ROWS:
            continue
        pq = np.sum(p[ok] * (1 - p[ok]))
        resid = np.sum((y[ok] - p[ok]) ** 2) - np.sum(p[ok] * (1 - p[ok]) / n[ok])
        inv = max(resid / pq, 1.0 / 501.0)
        out[pos] = float(np.clip(1.0 / inv - 1.0, 2.0, 500.0))
    return out


def gamma_shapes(
    frame: pl.DataFrame | None, yards: str, touches: str, eff_sd: float, positions: tuple[str, ...]
) -> dict[str, float]:
    default = DEFAULTS["ypr_shape" if yards == "y_rec_yds" else "ypc_shape"]
    out = {p: float(default.get(p, 1.0)) for p in positions}
    if frame is None:
        return out
    for pos in positions:
        f = frame.filter(pl.col("position") == pos)
        yd, n, m = _arr(f, yards), _arr(f, touches), _arr(f, "pred")
        ok = ~np.isnan(yd) & ~np.isnan(m) & (n > 0)
        if ok.sum() < MIN_ROWS:
            continue
        r2 = np.sum((yd[ok] - n[ok] * m[ok]) ** 2)
        eff = np.sum((n[ok] * m[ok] * eff_sd) ** 2)
        base = np.sum(n[ok] * m[ok] ** 2)
        k = base / max(r2 - eff, 0.05 * base)
        out[pos] = float(np.clip(k, 0.15, 5.0))
    return out


def estimate(residual_frames: dict[str, pl.DataFrame]) -> dict[str, Any]:
    """All dispersion parameters from per-stage frames with ``pred`` and targets."""
    out: dict[str, Any] = {k: (dict(v) if isinstance(v, dict) else v) for k, v in DEFAULTS.items()}
    if "team_plays" in residual_frames:
        out["sd_plays"] = plays_sd(residual_frames["team_plays"])
    if "team_pass_rate" in residual_frames:
        out["pass_rate_conc"] = pass_rate_conc(residual_frames["team_pass_rate"])
    out["share_conc"] = share_conc(residual_frames)
    out["ypr_shape"] = gamma_shapes(
        residual_frames.get("eff_ypr"), "y_rec_yds", "y_receptions", PASS_EFF_SD, ("RB", "WR", "TE")
    )
    out["ypc_shape"] = gamma_shapes(
        residual_frames.get("eff_ypc"), "y_rush_yds", "y_carries", RUSH_EFF_SD, ("QB", "RB", "WR")
    )
    return out


__all__ = ["DEFAULTS", "estimate"]
