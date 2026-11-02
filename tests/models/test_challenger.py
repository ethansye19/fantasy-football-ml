"""Direct LightGBM quantile challenger on tiny synthetic frames (no feature store needed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.features.manifest import feature_names
from ffml.models.challenger import OBJECTIVES, PLAYER_POSITIONS, DirectQuantileModel
from ffml.models.protocol import PREDICTION_COLUMNS, WeeklyModel, validate_predictions
from ffml.scoring.rules import ScoringRules

QCOLS = ["pred_q05", "pred_q10", "pred_q25", "pred_q50", "pred_q75", "pred_q90", "pred_q95"]
PPR = ScoringRules.from_preset("ppr")
FEATURES = ["f_signal", "f_noise"]


def kick(season: int, week: int) -> datetime:
    return datetime(season, 9, 7, 17, 0, tzinfo=UTC) + timedelta(days=7 * (week - 1))


def player_frame(
    seed: int = 0, seasons=(2019, 2020, 2021), weeks=range(1, 11), n: int = 60
) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for week in weeks:
            signal = rng.uniform(0, 4, size=n)
            noise = rng.normal(size=n)
            points = 4 + 3 * signal + rng.normal(0, 2, size=n)
            for i in range(n):
                pos = PLAYER_POSITIONS[i % len(PLAYER_POSITIONS)]
                rows.append(
                    {
                        "player_id": f"P{i:03d}",
                        "season": season,
                        "week": week,
                        "game_id": f"{season}_{week:02d}_G{i % 8}",
                        "team": f"T{i % 8}",
                        "position": pos,
                        "kickoff_utc": kick(season, week),
                        "points": float(points[i]),
                        "train_ok": bool(i % 10 != 0),  # every tenth player did not play
                        "f_signal": float(signal[i]),
                        "f_noise": float(noise[i]),
                        "pos_code": float(PLAYER_POSITIONS.index(pos)),
                    }
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def dst_frame(seed: int = 1, seasons=(2019, 2020, 2021), weeks=range(1, 11)) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for week in weeks:
            for t in range(8):
                signal = float(rng.uniform(0, 2))
                rows.append(
                    {
                        "team": f"T{t}",
                        "season": season,
                        "week": week,
                        "game_id": f"{season}_{week:02d}_G{t}",
                        "position": "DST",
                        "kickoff_utc": kick(season, week),
                        "points": 5 + 4 * signal + float(rng.normal(0, 3)),
                        "train_ok": True,
                        "f_signal": signal,
                        "f_noise": float(rng.normal()),
                    }
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def model(frames: dict[str, pl.DataFrame] | None = None, **kwargs) -> DirectQuantileModel:
    m = DirectQuantileModel("ppr", max_rounds=40, num_threads=2, **kwargs)
    frames = frames or {"player": player_frame(), "dst": dst_frame()}
    return m.use_frames(frames, {g: FEATURES for g in frames})


def test_fit_predict_contract():
    m = model()
    assert isinstance(m, WeeklyModel)
    assert m.name == "direct_lgbm" and m.key == "direct_lgbm_ppr"
    m.fit((2021, 4))
    out = m.predict(2021, 4, {"ppr": PPR, "half": ScoringRules.from_preset("half")})
    validate_predictions(out)
    assert out.columns == list(PREDICTION_COLUMNS)
    assert set(out["scoring"]) == {"ppr"} and set(out["model"]) == {"direct_lgbm"}
    assert out.height == 60 + 8  # every rostered player (played or not) and every DST
    assert set(out["position"]) == {*PLAYER_POSITIONS, "DST"}
    q = out.select(QCOLS).to_numpy()
    assert np.all(np.diff(q, axis=1) >= 0)
    # the model learned the signal
    players = out.filter(pl.col("position") != "DST")
    feats = player_frame().filter((pl.col("season") == 2021) & (pl.col("week") == 4))
    joined = players.join(feats, left_on="entity_id", right_on="player_id")
    assert np.corrcoef(joined["pred_q50"], joined["f_signal"])[0, 1] > 0.8
    # quantiles are clipped at the position minimum of the training rows
    assert (players["pred_q05"] >= -10).all()
    rounds = m.params["rounds"]
    assert set(rounds) == {"player_2021", "dst_2021"}
    assert set(rounds["player_2021"]) == set(OBJECTIVES)


def test_other_scorings_and_mismatched_rules():
    m = model().fit((2021, 4))
    empty = m.predict(2021, 4, {"half": ScoringRules.from_preset("half")})
    assert empty.height == 0 and empty.columns == list(PREDICTION_COLUMNS)
    with pytest.raises(ValueError):
        m.predict(2021, 4, {"ppr": ScoringRules.from_preset("std")})
    with pytest.raises(ValueError):
        m.predict(2021, 3, {"ppr": PPR})  # before the cutoff
    with pytest.raises(RuntimeError):
        model().predict(2021, 4, {"ppr": PPR})  # not fitted


def test_rows_at_or_after_the_cutoff_never_reach_the_fit():
    base = {"player": player_frame(), "dst": dst_frame()}
    later = (pl.col("season") == 2021) & (pl.col("week") >= 5)
    bumped = {
        g: f.with_columns(
            pl.when(later)
            .then(pl.col("points") * 5 + 50)
            .otherwise(pl.col("points"))
            .alias("points")
        )
        for g, f in base.items()
    }
    a = model(base).fit((2021, 5)).predict(2021, 5, {"ppr": PPR})
    b = model(bumped).fit((2021, 5)).predict(2021, 5, {"ppr": PPR})
    assert a.equals(b)
    # a game of week 4 played after week 5's first kickoff is excluded too
    moved = {
        g: f.with_columns(
            pl.when((pl.col("season") == 2021) & (pl.col("week") == 4))
            .then(pl.lit(kick(2021, 5) + timedelta(days=1)))
            .otherwise(pl.col("kickoff_utc"))
            .alias("kickoff_utc")
        )
        for g, f in base.items()
    }
    moved_bumped = {
        g: f.with_columns(
            pl.when((pl.col("season") == 2021) & (pl.col("week") == 4))
            .then(pl.col("points") + 100)
            .otherwise(pl.col("points"))
            .alias("points")
        )
        for g, f in moved.items()
    }
    c = model(moved).fit((2021, 5)).predict(2021, 5, {"ppr": PPR})
    d = model(moved_bumped).fit((2021, 5)).predict(2021, 5, {"ppr": PPR})
    assert c.equals(d)


def test_rounds_are_tuned_once_per_season_on_earlier_seasons():
    m = model()
    m.fit((2021, 3))
    first = dict(m.params["rounds"]["player_2021"])
    m.fit((2021, 6))
    assert m.params["rounds"]["player_2021"] == first
    # changing 2021 outcomes cannot change the 2021 rounds
    bumped = {
        "player": player_frame().with_columns(
            pl.when(pl.col("season") == 2021)
            .then(pl.col("points") * 3)
            .otherwise(pl.col("points"))
            .alias("points")
        ),
        "dst": dst_frame(),
    }
    other = model(bumped)
    other.fit((2021, 3))
    assert other.params["rounds"]["player_2021"] == first


def test_primary_features_exclude_injury_inputs_and_targets():
    for dataset in ("player_week", "dst_week"):
        names = feature_names(dataset, "primary")
        assert names
        assert not [c for c in names if c.startswith(("avail__", "y_"))]


def test_without_dst():
    m = model({"player": player_frame()}, include_dst=False).fit((2021, 2))
    out = m.predict(2021, 2, {"ppr": PPR})
    assert "DST" not in set(out["position"])
    validate_predictions(out)
