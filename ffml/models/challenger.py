"""Direct challenger: LightGBM quantile regression on fantasy points (MODELING.md section 4).

Not shipped. It quantifies what the structural chain costs or gains against a
model that learns points directly from the same primary-configuration
features. One instance per scoring: ``DirectQuantileModel("ppr")`` trains on
points under that scoring (``actual_statlines`` scored with the preset rules,
or the rules passed in) and returns rows only for that scoring.

Model
=====
- **Groups.** QB, RB, WR, TE and K share one pooled model on ``player_week``
  rows with the position as a categorical feature; DST has its own smaller
  model on ``dst_week`` rows (``include_dst=False`` omits DST).
- **Targets.** One booster per quantile level in
  :data:`ffml.models.protocol.QUANTILE_LEVELS` (objective ``quantile``) plus a
  mean booster (objective ``regression``, L2). Predictions are conditional on
  the entity being active (trained on played rows only), so there is no
  ``active_prob``.
- **Training rows.** Regular-season rows before the cutoff ``(season, week)``
  whose game kicked off before the first kickoff of the cutoff week (D-046),
  played (``y_played == 1``) for players, with points from the stat lines.
- **Features.** ``ffml.features.manifest.feature_names(dataset, "primary")``:
  no ``avail__`` (same-week injury and roster) and no ``y_`` columns, read
  through :mod:`ffml.features.access` (holdout guard). The feature list is
  fixed at the first load.
- **Hyperparameters.** Fixed (:data:`PLAYER_PARAMS`, :data:`DST_PARAMS`);
  only the number of boosting rounds is chosen by early stopping, inside the
  training window: at the first fit of each season the rows of earlier seasons
  are split into the last :data:`VALIDATION_WEEKS` played weeks (validation)
  and everything before (training); each booster stops after
  :data:`EARLY_STOPPING_ROUNDS` rounds without improvement of its own loss
  (pinball at its level, L2 for the mean), at most ``max_rounds``. Every weekly
  fit of that season then trains on all rows before its cutoff with those
  rounds. Rounds therefore depend only on data before the season's first week,
  and a resumed backtest uses the same rounds.
- **Output.** Quantiles are made monotone by sorting each row, then every
  prediction is clipped below at the position's minimum observed score in the
  training rows.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Mapping
from typing import Any

import lightgbm as lgb
import numpy as np
import polars as pl

from ffml.config import get_settings
from ffml.data.store import SnapshotStore
from ffml.features.access import load_features
from ffml.features.manifest import feature_names
from ffml.holdout import assert_seasons_allowed
from ffml.models.protocol import PREDICTION_COLUMNS, QUANTILE_LEVELS
from ffml.scoring.actuals import actual_statlines
from ffml.scoring.rules import ScoringRules

log = logging.getLogger(__name__)

QUANTILE_COLUMNS: tuple[str, ...] = tuple(
    f"pred_q{int(round(q * 100)):02d}" for q in QUANTILE_LEVELS
)
OBJECTIVES: tuple[str, ...] = (*(f"q{int(round(q * 100)):02d}" for q in QUANTILE_LEVELS), "mean")
PLAYER_POSITIONS: tuple[str, ...] = ("QB", "RB", "WR", "TE", "K")

#: pooled QB/RB/WR/TE/K model
PLAYER_PARAMS: dict[str, Any] = {
    "learning_rate": 0.1,
    "num_leaves": 15,
    "min_data_in_leaf": 200,
    "feature_fraction": 0.3,
    "bagging_fraction": 0.7,
    "bagging_freq": 1,
    "lambda_l2": 1.0,
    "max_bin": 31,
}
#: DST model (about 540 rows per season)
DST_PARAMS: dict[str, Any] = {
    "learning_rate": 0.05,
    "num_leaves": 7,
    "min_data_in_leaf": 40,
    "feature_fraction": 0.3,
    "bagging_fraction": 0.8,
    "bagging_freq": 1,
    "lambda_l2": 5.0,
    "max_bin": 31,
}
MAX_ROUNDS = 500
EARLY_STOPPING_ROUNDS = 30
VALIDATION_WEEKS = 8
MIN_ROUNDS = 20
DEFAULT_ROUNDS = 150  # only when no earlier season exists to tune on
DATASET_PARAMS: tuple[str, ...] = ("max_bin",)

_GROUPS: dict[str, dict[str, Any]] = {
    "player": {"dataset": "player_week", "entity": "player_id", "params": PLAYER_PARAMS},
    "dst": {"dataset": "dst_week", "entity": "team", "params": DST_PARAMS},
}
_KEYS: dict[str, list[str]] = {
    "player": [
        "player_id",
        "season",
        "week",
        "game_id",
        "team",
        "position",
        "game_type",
        "kickoff_utc",
    ],
    "dst": ["team", "season", "week", "game_id", "game_type", "kickoff_utc"],
}


def _objective_params(base: Mapping[str, Any], objective: str) -> dict[str, Any]:
    params = dict(base)
    if objective == "mean":
        params.update(objective="regression", metric="l2")
    else:
        params.update(objective="quantile", alpha=int(objective[1:]) / 100.0, metric="quantile")
    return params


class DirectQuantileModel:
    """LightGBM quantile regression on points under one scoring (see module docstring)."""

    heavy = True  # the harness schedules heavy models first in parallel runs

    def __init__(
        self,
        scoring_name: str = "ppr",
        *,
        rules: ScoringRules | None = None,
        include_dst: bool = True,
        max_rounds: int = MAX_ROUNDS,
        num_threads: int | None = None,
        seed: int = 20261102,
        purpose: str = "backtest",
        config: str = "primary",
        name: str = "direct_lgbm",
    ) -> None:
        self.scoring_name = scoring_name
        self.rules = rules or ScoringRules.from_preset(scoring_name)
        self.include_dst = include_dst
        self.max_rounds = int(max_rounds)
        self.num_threads = num_threads
        self.seed = int(seed)
        self.purpose = purpose
        self.config = config
        self.name = name
        self.key = f"{name}_{scoring_name}"
        self._groups = ["player", "dst"] if include_dst else ["player"]
        self._data: dict[str, dict[str, Any]] = {}
        self._loaded_through: int | None = None
        self._features: dict[str, list[str]] = {}
        self._rounds: dict[tuple[str, int], dict[str, int]] = {}
        self._boosters: dict[str, dict[str, lgb.Booster]] = {}
        self._pos_min: dict[str, dict[str, float]] = {}
        self._cutoff: tuple[int, int] | None = None

    def __repr__(self) -> str:
        return (
            f"DirectQuantileModel({self.scoring_name!r}, include_dst={self.include_dst}, "
            f"max_rounds={self.max_rounds}, config={self.config!r})"
        )

    @property
    def params(self) -> dict[str, Any]:
        """Hyperparameters and tuned rounds, for the backtest manifest."""
        return {
            "scoring": self.scoring_name,
            "scoring_hash": self.rules.scoring_hash,
            "player_params": PLAYER_PARAMS,
            "dst_params": DST_PARAMS if self.include_dst else None,
            "max_rounds": self.max_rounds,
            "early_stopping_rounds": EARLY_STOPPING_ROUNDS,
            "validation_weeks": VALIDATION_WEEKS,
            "seed": self.seed,
            "feature_config": self.config,
            "n_features": {g: len(f) for g, f in self._features.items()},
            "rounds": {f"{g}_{s}": r for (g, s), r in sorted(self._rounds.items())},
        }

    # -- data -------------------------------------------------------------- #

    def _threads(self) -> int:
        return int(self.num_threads or os.cpu_count() or 1)

    def _ensure_data(self, season: int) -> None:
        if self._loaded_through is not None and self._loaded_through >= season:
            return
        settings = get_settings()
        history = list(range(min(settings.first_season, season), season + 1))
        assert_seasons_allowed(history, self.purpose)
        actuals = actual_statlines(
            history, self.purpose, store=SnapshotStore(settings.processed_dir)
        )
        actuals = actuals.select(
            "entity_id",
            pl.col("season").cast(pl.Int32),
            pl.col("week").cast(pl.Int32),
            "game_id",
            "played",
            self.rules.score(actuals).alias("points"),
        )
        for group in self._groups:
            spec = _GROUPS[group]
            dataset = spec["dataset"]
            if group not in self._features:
                names = feature_names(dataset, self.config)
                bad = [c for c in names if c.startswith(("avail__", "y_"))]
                if self.config == "primary" and bad:
                    raise ValueError(f"primary features include excluded columns: {bad[:5]}")
                self._features[group] = names
            wanted = [*_KEYS[group], *self._features[group]]
            if group == "player":
                wanted.append("y_played")
            frame = load_features(dataset, history, self.purpose)
            present = set(frame.columns)
            missing = [c for c in self._features[group] if c not in present]
            if missing:
                log.warning(
                    "%s: %d features missing from %s; using the rest",
                    self.key,
                    len(missing),
                    dataset,
                )
                self._features[group] = [c for c in self._features[group] if c in present]
                wanted = [c for c in wanted if c in present]
            frame = frame.select(wanted).filter(pl.col("game_type") == "REG")
            ent = spec["entity"]
            frame = frame.join(
                actuals.rename({"entity_id": ent}),
                on=[ent, "season", "week", "game_id"],
                how="left",
            )
            if group == "player":
                frame = frame.with_columns(
                    pl.col("position")
                    .replace_strict(
                        {p: i for i, p in enumerate(PLAYER_POSITIONS)},
                        default=None,
                        return_dtype=pl.Float32,
                    )
                    .alias("pos_code"),
                    ((pl.col("y_played") == 1.0) & pl.col("points").is_not_null()).alias(
                        "train_ok"
                    ),
                )
            else:
                frame = frame.with_columns(
                    pl.lit("DST").alias("position"),
                    pl.col("points").is_not_null().alias("train_ok"),
                )
            self._install(group, frame)
        self._loaded_through = season

    def _install(self, group: str, frame: pl.DataFrame) -> None:
        ent = _GROUPS[group]["entity"]
        frame = frame.sort(["season", "week", ent])
        cols = self._matrix_columns(group)
        meta = [
            ent,
            "season",
            "week",
            "game_id",
            "team",
            "position",
            "kickoff_utc",
            "points",
            "train_ok",
        ]
        self._data[group] = {
            "frame": frame.select(list(dict.fromkeys(meta))),
            "X": frame.select(cols).cast(pl.Float32).to_numpy(),
            "categorical": [cols.index("pos_code")] if "pos_code" in cols else [],
        }

    def _matrix_columns(self, group: str) -> list[str]:
        cols = list(self._features[group])
        if group == "player":
            cols.append("pos_code")
        return cols

    # -- fitting ----------------------------------------------------------- #

    def _train(
        self,
        group: str,
        objective: str,
        dataset: lgb.Dataset,
        rounds: int,
        valid: lgb.Dataset | None = None,
    ) -> lgb.Booster:
        params = _objective_params(_GROUPS[group]["params"], objective)
        params.update(
            verbose=-1,
            seed=self.seed,
            num_threads=self._threads(),
            deterministic=True,
            force_col_wise=True,
        )
        callbacks = []
        valid_sets = None
        if valid is not None:
            callbacks.append(lgb.early_stopping(EARLY_STOPPING_ROUNDS, verbose=False))
            valid_sets = [valid]
        return lgb.train(
            params, dataset, num_boost_round=rounds, valid_sets=valid_sets, callbacks=callbacks
        )

    def _dataset(
        self, group: str, rows: np.ndarray, reference: lgb.Dataset | None = None
    ) -> lgb.Dataset:
        data = self._data[group]
        base = _GROUPS[group]["params"]
        ds_params = {k: base[k] for k in DATASET_PARAMS if k in base}
        ds_params["verbose"] = -1
        return lgb.Dataset(
            data["X"][rows],
            label=data["frame"]["points"].to_numpy()[rows],
            categorical_feature=data["categorical"] or "auto",
            params=ds_params,
            reference=reference,
            free_raw_data=True,
        )

    def _tune_rounds(self, group: str, season: int) -> dict[str, int]:
        key = (group, season)
        if key in self._rounds:
            return self._rounds[key]
        frame = self._data[group]["frame"]
        ok = frame["train_ok"].to_numpy() & (frame["season"].to_numpy() < season)
        if not ok.any():
            self._rounds[key] = dict.fromkeys(OBJECTIVES, DEFAULT_ROUNDS)
            return self._rounds[key]
        sw = frame.select((pl.col("season") * 100 + pl.col("week")).alias("sw"))["sw"].to_numpy()
        weeks = np.unique(sw[ok])
        valid_weeks = weeks[-VALIDATION_WEEKS:]
        is_valid = ok & np.isin(sw, valid_weeks)
        is_train = ok & ~np.isin(sw, valid_weeks)
        train = self._dataset(group, np.flatnonzero(is_train))
        valid = self._dataset(group, np.flatnonzero(is_valid), reference=train)
        rounds = {}
        for objective in OBJECTIVES:
            booster = self._train(group, objective, train, self.max_rounds, valid)
            best = booster.best_iteration or self.max_rounds
            rounds[objective] = int(max(MIN_ROUNDS, best))
        log.info("%s %s rounds for %d: %s", self.key, group, season, rounds)
        self._rounds[key] = rounds
        return rounds

    def fit(self, cutoff: tuple[int, int]) -> DirectQuantileModel:
        """Train every booster on rows before ``cutoff`` (rounds tuned once per season)."""
        season, week = int(cutoff[0]), int(cutoff[1])
        self._ensure_data(season)
        for group in self._groups:
            frame = self._data[group]["frame"]
            sw = (
                frame["season"].to_numpy().astype(np.int64) * 100 + frame["week"].to_numpy()
            ).astype(np.int64)
            before = sw < season * 100 + week
            in_week = sw == season * 100 + week
            if in_week.any():
                first_kick = frame.filter(pl.Series(in_week))["kickoff_utc"].min()
                before &= (frame["kickoff_utc"] < first_kick).fill_null(False).to_numpy()
            mask = before & frame["train_ok"].to_numpy()
            rows = np.flatnonzero(mask)
            if rows.size < 50:
                raise ValueError(
                    f"{self.key}: only {rows.size} {group} training rows before {cutoff}"
                )
            rounds = self._tune_rounds(group, season)
            dataset = self._dataset(group, rows)
            self._boosters[group] = {
                objective: self._train(group, objective, dataset, rounds[objective])
                for objective in OBJECTIVES
            }
            train = frame.filter(pl.Series(mask))
            self._pos_min[group] = dict(
                train.group_by("position").agg(pl.col("points").min()).iter_rows()
            )
        self._cutoff = (season, week)
        return self

    # -- prediction -------------------------------------------------------- #

    def _empty(self) -> pl.DataFrame:
        schema: dict[str, pl.DataType] = {
            c: pl.String() for c in ("entity_id", "position", "team", "game_id", "scoring", "model")
        }
        schema.update({"season": pl.Int32(), "week": pl.Int32()})
        schema.update({c: pl.Float64() for c in ("pred_mean", *QUANTILE_COLUMNS)})
        return pl.DataFrame(schema=schema).select(PREDICTION_COLUMNS)

    def predict(self, season: int, week: int, scorings: Mapping[str, ScoringRules]) -> pl.DataFrame:
        """Rows for every regular-season feature row of ``(season, week)``, this model's scoring only."""
        season, week = int(season), int(week)
        if self.scoring_name not in scorings:
            return self._empty()
        given = scorings[self.scoring_name]
        if given.scoring_hash != self.rules.scoring_hash:
            raise ValueError(
                f"{self.key} was trained on {self.rules!r}, asked to predict {given!r}"
            )
        if self._cutoff is None:
            raise RuntimeError(f"{self.key}: fit before predict")
        if (season, week) < self._cutoff:
            raise ValueError(
                f"cannot predict {(season, week)} before the fit cutoff {self._cutoff}"
            )
        self._ensure_data(season)
        frames = []
        for group in self._groups:
            data = self._data[group]
            frame = data["frame"]
            mask = ((frame["season"] == season) & (frame["week"] == week)).to_numpy()
            if not mask.any():
                continue
            rows = frame.filter(pl.Series(mask))
            X = data["X"][mask]
            boosters = self._boosters[group]
            q = np.column_stack([boosters[o].predict(X) for o in OBJECTIVES[:-1]])
            q = np.sort(q, axis=1)
            mean = boosters["mean"].predict(X)
            floor = (
                rows["position"]
                .replace_strict(self._pos_min[group], default=None, return_dtype=pl.Float64)
                .to_numpy()
            )
            floor = np.where(np.isnan(floor), -np.inf, floor)
            q = np.maximum(q, floor[:, None])
            mean = np.maximum(mean, floor)
            ent = _GROUPS[group]["entity"]
            out = rows.select(
                pl.col(ent).alias("entity_id"),
                "position",
                "team",
                "game_id",
                pl.col("season").cast(pl.Int32),
                pl.col("week").cast(pl.Int32),
            ).with_columns(
                pl.lit(self.scoring_name).alias("scoring"),
                pl.lit(self.name).alias("model"),
                pl.Series("pred_mean", mean, dtype=pl.Float64),
                *[pl.Series(c, q[:, i], dtype=pl.Float64) for i, c in enumerate(QUANTILE_COLUMNS)],
            )
            frames.append(out.select(PREDICTION_COLUMNS))
        if not frames:
            return self._empty()
        return pl.concat(frames, how="vertical_relaxed").unique(
            ["entity_id", "season", "week"], keep="first", maintain_order=True
        )

    # -- testing hook ------------------------------------------------------ #

    def use_frames(
        self, frames: Mapping[str, pl.DataFrame], features: Mapping[str, list[str]]
    ) -> DirectQuantileModel:
        """Install in-memory training frames instead of the feature store (tests).

        Each frame needs the group's key columns, ``points``, ``train_ok``,
        ``position``, the listed features and, for ``player``, ``pos_code``.
        """
        for group, frame in frames.items():
            self._features[group] = list(features[group])
            self._install(group, frame)
        self._groups = list(frames)
        self._loaded_through = 10**6
        return self


__all__ = ["DST_PARAMS", "DirectQuantileModel", "OBJECTIVES", "PLAYER_PARAMS"]
