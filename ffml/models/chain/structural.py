"""Structural (transparent) baselines per stage, MODELING.md section 3.

Each predictor has at most one or two fitted numbers, all estimated on the
training window, and a documented fallback chain for rows whose primary
window is null (new players, new teams, the first weeks of the data):

=====================  ======================================================
method                 prediction
=====================  ======================================================
``l5_mean``            first non-null of the listed windows (L5, prior season),
                       else the training mean
``ew_spread``          EW rate + ``b * margin`` with ``margin = -ctx__spread``
                       (expected margin of the row's team); ``b`` by least
                       squares on the training residuals (one parameter)
``implied_ratio``      ``a * ctx__implied_team_total`` with ``a`` = sum(y) /
                       sum(implied) (the "implied total / k" heuristic)
``ew_blend``           ``w * x + (1 - w) * mean`` with ``x`` the first non-null
                       window and ``w`` by least squares, clipped to [0, 1]
``share_ew``           first non-null share window; else a prior: the mean
                       target of training rows **without** that history, by
                       position and depth rank (1, 2, 3, 4+, unknown), with
                       cells under 30 rows falling back to the position
``rate_shrunk``        the Feature Engineer's shrunk window (EW, else career);
                       else the weighted position rate
``position_prior``     weighted position rate
``dst_blend``          ``c * sqrt(own rate * opponent rate) * opponent volume``
                       (league means for nulls, ``c`` = sum(y) / sum(raw))
``direct``             first non-null listed column, else the training mean
=====================  ======================================================

Predictions are clipped to the stage's range (shares and rates inside (0, 1),
counts at least 1e-3).
"""

from __future__ import annotations

from typing import Any

import numpy as np
import polars as pl

from ffml.models.chain.stages import StageSpec

DEPTH_BUCKETS = (1, 2, 3, 4)
MIN_PRIOR_ROWS = 30


def _num(df: pl.DataFrame, name: str) -> np.ndarray:
    if name not in df.columns:
        return np.full(df.height, np.nan)
    return df[name].cast(pl.Float64).fill_null(np.nan).to_numpy()


def _coalesce(df: pl.DataFrame, names: tuple[str, ...]) -> np.ndarray:
    out = np.full(df.height, np.nan)
    for n in names:
        v = _num(df, n)
        out = np.where(np.isnan(out), v, out)
    return out


def depth_bucket(df: pl.DataFrame) -> np.ndarray:
    r = _num(df, "depth__rank")
    return np.where(np.isnan(r), 0, np.clip(np.nan_to_num(r, nan=0), 1, 4)).astype(int)


def clip_for(spec: StageSpec, pred: np.ndarray) -> np.ndarray:
    if spec.kind in ("share", "rate", "binary"):
        return np.clip(pred, 1e-4, 1 - 1e-4)
    if spec.kind == "count":
        return np.maximum(pred, 1e-3)
    if spec.clip is not None:
        return np.clip(pred, *spec.clip)
    return pred


class StructuralStage:
    """One structural predictor; ``params`` is JSON-serialisable."""

    def __init__(self, spec: StageSpec, params: dict[str, Any] | None = None) -> None:
        self.spec = spec
        self.method = spec.structural
        self.args = spec.structural_args
        self.params: dict[str, Any] = dict(params or {})

    # -- helpers ---------------------------------------------------------- #

    def _y_w(self, df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray]:
        y = _num(df, self.spec.target)
        w = _num(df, self.spec.weight) if self.spec.weight else np.ones(df.height)
        w = np.where(np.isnan(w), 0.0, w)
        return y, w

    @staticmethod
    def _wmean(y: np.ndarray, w: np.ndarray) -> float:
        ok = ~np.isnan(y) & (w > 0)
        return float(np.sum(y[ok] * w[ok]) / np.sum(w[ok])) if ok.any() else float("nan")

    def _positions(self, df: pl.DataFrame) -> np.ndarray:
        if "position" in df.columns:
            return np.asarray(df["position"].to_list(), dtype=object)
        return np.full(df.height, "ALL", dtype=object)

    # -- fit -------------------------------------------------------------- #

    def fit(self, df: pl.DataFrame) -> StructuralStage:
        y, w = self._y_w(df)
        m = self.method
        cols = tuple(self.args.get("columns", ()))
        ok = ~np.isnan(y)
        self.params["mean"] = self._wmean(y, w)
        if m in ("l5_mean", "direct"):
            pass
        elif m == "ew_spread":
            x = _coalesce(df, cols)
            x = np.where(np.isnan(x), self.params["mean"], x)
            s = -_num(df, "ctx__spread")
            good = ok & ~np.isnan(s)
            denom = float(np.sum(s[good] ** 2))
            self.params["b"] = (
                float(np.sum((y[good] - x[good]) * s[good]) / denom) if denom else 0.0
            )
        elif m == "implied_ratio":
            imp = _num(df, "ctx__implied_team_total")
            good = ok & ~np.isnan(imp)
            self.params["a"] = float(np.sum(y[good]) / np.sum(imp[good])) if good.any() else 0.0
        elif m == "ew_blend":
            x = _coalesce(df, cols)
            mu = self.params["mean"]
            good = ok & ~np.isnan(x)
            dx = x[good] - mu
            denom = float(np.sum(dx**2))
            wgt = float(np.sum((y[good] - mu) * dx) / denom) if denom else 0.0
            self.params["w"] = float(np.clip(wgt, 0.0, 1.0))
        elif m == "share_ew":
            x = _coalesce(df, cols)
            pos = self._positions(df)
            dep = depth_bucket(df)
            nohist = ok & np.isnan(x)
            priors: dict[str, float] = {}
            for p in np.unique(pos):
                in_pos = nohist & (pos == p)
                pos_mean = (
                    float(np.mean(y[in_pos]))
                    if in_pos.sum() >= MIN_PRIOR_ROWS
                    else float(np.nanmean(y[ok & (pos == p)]))
                    if (ok & (pos == p)).any()
                    else self.params["mean"]
                )
                priors[f"{p}|all"] = pos_mean
                for d in (0, *DEPTH_BUCKETS):
                    cell = in_pos & (dep == d)
                    priors[f"{p}|{d}"] = (
                        float(np.mean(y[cell])) if cell.sum() >= MIN_PRIOR_ROWS else pos_mean
                    )
            self.params["priors"] = priors
        elif m in ("rate_shrunk", "position_prior"):
            pos = self._positions(df)
            self.params["priors"] = {
                str(p): self._wmean(y[pos == p], w[pos == p]) for p in np.unique(pos)
            }
        elif m == "dst_blend":
            own = self._own_rate(df)
            opp = _num(df, self.args["opp"])
            vol = _num(df, self.args["volume"])
            self.params["own_mean"] = float(np.nanmean(own))
            self.params["opp_mean"] = float(np.nanmean(opp))
            self.params["vol_mean"] = float(np.nanmean(vol))
            raw = self._dst_raw(df)
            self.params["c"] = float(np.sum(y[ok]) / np.sum(raw[ok])) if ok.any() else 1.0
        else:
            raise ValueError(f"unknown structural method {m!r}")
        return self

    def _own_rate(self, df: pl.DataFrame) -> np.ndarray:
        if "own" in self.args:
            return _num(df, self.args["own"])
        num = _num(df, self.args["own_count"])
        den = _num(df, self.args["own_den"])
        return np.divide(num, den, out=np.full(df.height, np.nan), where=den > 0)

    def _dst_raw(self, df: pl.DataFrame) -> np.ndarray:
        own = self._own_rate(df)
        own = np.where(np.isnan(own), self.params["own_mean"], own)
        opp = _num(df, self.args["opp"])
        opp = np.where(np.isnan(opp), self.params["opp_mean"], opp)
        vol = _num(df, self.args["volume"])
        vol = np.where(np.isnan(vol), self.params["vol_mean"], vol)
        return np.sqrt(np.maximum(own, 0) * np.maximum(opp, 0)) * vol

    # -- predict ---------------------------------------------------------- #

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        m = self.method
        cols = tuple(self.args.get("columns", ()))
        mu = self.params["mean"]
        if m in ("l5_mean", "direct"):
            x = _coalesce(df, cols)
            pred = np.where(np.isnan(x), mu, x)
        elif m == "ew_spread":
            x = _coalesce(df, cols)
            x = np.where(np.isnan(x), mu, x)
            s = np.nan_to_num(-_num(df, "ctx__spread"), nan=0.0)
            pred = x + self.params["b"] * s
        elif m == "implied_ratio":
            imp = _num(df, "ctx__implied_team_total")
            pred = np.where(np.isnan(imp), mu, self.params["a"] * imp)
        elif m == "ew_blend":
            x = _coalesce(df, cols)
            pred = np.where(np.isnan(x), mu, self.params["w"] * x + (1 - self.params["w"]) * mu)
        elif m == "share_ew":
            x = _coalesce(df, cols)
            pos = self._positions(df)
            dep = depth_bucket(df)
            pri = self.params["priors"]
            prior = np.array(
                [
                    pri.get(f"{p}|{d}", pri.get(f"{p}|all", mu))
                    for p, d in zip(pos, dep, strict=True)
                ],
                dtype=float,
            )
            pred = np.where(np.isnan(x), prior, x)
        elif m == "rate_shrunk":
            x = _coalesce(df, cols)
            pos = self._positions(df)
            pri = self.params["priors"]
            prior = np.array([pri.get(str(p), mu) for p in pos], dtype=float)
            pred = np.where(np.isnan(x), prior, x)
        elif m == "position_prior":
            pos = self._positions(df)
            pri = self.params["priors"]
            pred = np.array([pri.get(str(p), mu) for p in pos], dtype=float)
        elif m == "dst_blend":
            pred = self.params["c"] * self._dst_raw(df)
        else:  # pragma: no cover - checked in fit
            raise ValueError(m)
        pred = np.where(np.isnan(pred), mu, pred)
        return clip_for(self.spec, pred)

    def to_dict(self) -> dict[str, Any]:
        return {"stage": self.spec.name, "method": self.method, "params": self.params}


__all__ = ["StructuralStage", "clip_for", "depth_bucket"]
