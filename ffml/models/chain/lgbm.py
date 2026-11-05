"""LightGBM stage candidates with fixed, documented hyperparameters.

Hyperparameters are fixed (no search): learning rate 0.05, at most 600
rounds, 31 leaves and at least 100 rows per leaf for player stages (15 leaves
and 40 rows for team and DST stages, which have about 5,000 rows), feature
fraction 0.7 (0.5 team/DST), bagging 0.8 every round, L2 penalty 1.0, 127 bins,
deterministic training with a fixed seed and 4 threads.

Threads (D-108): ``num_threads`` was 8 through the validation, holdout and
forward runs. With 8 OpenMP threads on Linux (libgomp) the weighted
cross-entropy stages were not reproducible run to run (9 of 10 repeats on a
4-vCPU CI runner, 6 of 8 at 8 vCPUs), while 4 or 1 threads were (0 of 32).
It is 4 since 2026-11-05: on the development machine the four stored
identity weeks (2022 w6, 2024 w15, 2025 w9, 2026 w4) and the 2026 week 8
walk-forward part recomputed at 4 threads are byte-identical to the rows
produced at 8, and the Week 9 calibrator state is unchanged
(docs/status/phase4-ml-engineer-chain-v2.md). A determinism fix, not a
model change.

Early stopping uses only rows inside the training window: the last
``ES_WEEKS`` regular-season weeks before the cutoff are held out, the model is
trained on the earlier rows with 50-round patience, and then refitted on the
whole window with the best number of rounds. The held-out predictions of the
first model are kept (``es_frame``) because they are out-of-sample for the
fitted trees; the chain estimates dispersion parameters from them.

Objectives by stage kind: L2 regression (continuous and positive targets,
weighted by the denominator for yards per touch), cross-entropy on the share
or rate with the denominator as weight (the GLM reading of "regression on
logit share"), Poisson for counts, binary log loss for availability.
Monotone constraints come from the stage specification (``basic`` method).
"""

from __future__ import annotations

import logging
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from ffml.models.chain.stages import StageSpec
from ffml.models.chain.structural import clip_for

log = logging.getLogger(__name__)

ES_WEEKS = 8
MAX_ROUNDS = 600
PATIENCE = 50
BASE_PARAMS: dict[str, Any] = {
    "learning_rate": 0.05,
    "num_leaves": 31,
    "min_data_in_leaf": 100,
    "feature_fraction": 0.7,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "max_bin": 127,
    "num_threads": 4,
    "verbosity": -1,
    "deterministic": True,
    "force_col_wise": True,
    "monotone_constraints_method": "basic",
}
SMALL_DATA_PARAMS: dict[str, Any] = {
    "num_leaves": 15,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.5,
}


def stage_features(spec: StageSpec, columns: list[str], config: str = "primary") -> list[str]:
    """The stage's LightGBM inputs present in ``columns`` (avail__ only in final_report)."""
    names = list(spec.features)
    if config == "final_report":
        names += list(spec.final_report_features)
    have = set(columns)
    out = [c for c in dict.fromkeys(names) if c in have]
    if config != "final_report":
        out = [c for c in out if not c.startswith("avail__")]
    return out


def lgbm_params(spec: StageSpec, seed: int) -> dict[str, Any]:
    params = dict(BASE_PARAMS)
    if spec.dataset != "player_week":
        params.update(SMALL_DATA_PARAMS)
    params["objective"] = spec.objective
    params["seed"] = int(seed)
    return params


def _matrix(df: pl.DataFrame, features: list[str]) -> np.ndarray:
    if not features:
        return np.zeros((df.height, 0))
    return df.select([pl.col(c).cast(pl.Float64) for c in features]).to_numpy()


class LGBMStage:
    """One LightGBM candidate (fit with early stopping inside the window, then refit)."""

    def __init__(self, spec: StageSpec, config: str = "primary", seed: int = 0) -> None:
        self.spec = spec
        self.config = config
        self.seed = seed
        self.features: list[str] = []
        self.booster: lgb.Booster | None = None
        self.best_iteration: int = 0
        self.es_frame: pl.DataFrame | None = None

    def _xyw(self, df: pl.DataFrame) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
        X = _matrix(df, self.features)
        y = df[self.spec.target].cast(pl.Float64).to_numpy()
        if self.spec.objective in ("cross_entropy", "binary"):
            y = np.clip(y, 0.0, 1.0)  # shares and rates are proportions
        if self.spec.weight:
            w = df[self.spec.weight].cast(pl.Float64).fill_null(0.0).to_numpy()
        else:
            w = np.ones(df.height)
        return X, y, w

    def fit(self, df: pl.DataFrame) -> LGBMStage:
        self.features = stage_features(self.spec, df.columns, self.config)
        params = lgbm_params(self.spec, self.seed)
        mono = [int(self.spec.monotone.get(c, 0)) for c in self.features]
        if any(mono):
            params["monotone_constraints"] = mono
        weeks = np.sort(df["wk_key"].unique().to_numpy())
        valid_keys = weeks[-ES_WEEKS:] if weeks.size > ES_WEEKS + 4 else weeks[:0]
        is_valid = df["wk_key"].is_in(valid_keys.tolist())
        tr, va = df.filter(~is_valid), df.filter(is_valid)
        Xt, yt, wt = self._xyw(tr)
        ds_t = lgb.Dataset(Xt, yt, weight=wt, free_raw_data=True)
        if va.height:
            Xv, yv, wv = self._xyw(va)
            ds_v = lgb.Dataset(Xv, yv, weight=wv, reference=ds_t)
            first = lgb.train(
                params,
                ds_t,
                num_boost_round=MAX_ROUNDS,
                valid_sets=[ds_v],
                callbacks=[lgb.early_stopping(PATIENCE, verbose=False)],
            )
            self.best_iteration = int(first.best_iteration or MAX_ROUNDS)
            self.es_frame = va.with_columns(
                pl.Series(
                    "pred",
                    clip_for(self.spec, first.predict(Xv, num_iteration=self.best_iteration)),
                )
            )
        else:
            self.best_iteration = 200
        X, y, w = self._xyw(df)
        self.booster = lgb.train(
            params, lgb.Dataset(X, y, weight=w), num_boost_round=max(self.best_iteration, 10)
        )
        return self

    def predict(self, df: pl.DataFrame) -> np.ndarray:
        if self.booster is None:
            raise RuntimeError(f"{self.spec.name}: LightGBM stage is not fitted")
        X = _matrix(df, self.features)
        return clip_for(self.spec, self.booster.predict(X))

    # -- persistence ------------------------------------------------------ #

    def to_dict(self) -> dict[str, Any]:
        return {
            "stage": self.spec.name,
            "config": self.config,
            "features": self.features,
            "best_iteration": self.best_iteration,
            "seed": self.seed,
        }

    def model_string(self) -> str:
        assert self.booster is not None
        return self.booster.model_to_string()

    @classmethod
    def from_saved(cls, spec: StageSpec, meta: dict[str, Any], model_str: str) -> LGBMStage:
        out = cls(spec, config=meta.get("config", "primary"), seed=int(meta.get("seed", 0)))
        out.features = list(meta["features"])
        out.best_iteration = int(meta["best_iteration"])
        out.booster = lgb.Booster(model_str=model_str)
        return out


__all__ = ["BASE_PARAMS", "ES_WEEKS", "LGBMStage", "MAX_ROUNDS", "lgbm_params", "stage_features"]
