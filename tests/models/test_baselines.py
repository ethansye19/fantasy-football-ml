"""Transparent baselines on synthetic stat lines (no processed data needed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.holdout import HoldoutLockedError
from ffml.models.baselines import (
    PREDICTION_COLUMNS,
    QUANTILE_COLUMNS,
    PriorSeasonBaseline,
    RollingAverageBaseline,
    RollingMedianBaseline,
    default_baselines,
    week_cutoffs,
)
from ffml.models.protocol import WeeklyModel, validate_predictions
from ffml.scoring import ScoringRules
from ffml.scoring.statline import ensure_stat_columns

# Points equal receptions, so expected values are easy to write down.
REC_ONLY = ScoringRules.from_dict({"rec": 1}, name="rec")
TAILS = (0.05, 0.10, 0.25, 0.75, 0.90, 0.95)
TAIL_COLS = ("pred_q05", "pred_q10", "pred_q25", "pred_q75", "pred_q90", "pred_q95")


def kickoff(season: int, week: int, day_offset: float = 0.0) -> datetime:
    """Sunday 17:00 UTC of a synthetic week (week 1 = first Sunday of September)."""
    base = datetime(season, 9, 7, 17, 0, tzinfo=UTC)
    return base + timedelta(days=7 * (week - 1) + day_offset)


def actuals(rows: list[dict]) -> pl.DataFrame:
    """Stat-line frame from dicts with entity_id, season, week, rec and optional overrides."""
    records = []
    for r in rows:
        records.append(
            {
                "entity_id": r["entity_id"],
                "position": r.get("position", "WR"),
                "season": r["season"],
                "week": r["week"],
                "game_id": f"{r['season']}_{r['week']:02d}_{r['entity_id']}",
                "team": "AAA",
                "opponent": "BBB",
                "game_type": "REG",
                "kickoff_utc": r.get("kickoff_utc") or kickoff(r["season"], r["week"]),
                "played": r.get("played", True),
                "rec": r.get("rec", 0),
            }
        )
    frame = pl.DataFrame(records).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    return ensure_stat_columns(frame)


def series(entity: str, season: int, values: list[float], position: str = "WR", start: int = 1):
    return [
        {"entity_id": entity, "position": position, "season": season, "week": start + i, "rec": v}
        for i, v in enumerate(values)
    ]


def pred(frame: pl.DataFrame, entity: str, season: int, week: int) -> dict:
    rows = frame.filter(
        (pl.col("entity_id") == entity) & (pl.col("season") == season) & (pl.col("week") == week)
    )
    assert rows.height == 1, f"expected one prediction for {entity} {season}-{week}"
    return rows.row(0, named=True)


def all_models():
    return [
        RollingAverageBaseline(window=4),
        RollingAverageBaseline(window=3),
        RollingAverageBaseline(ew_alpha=0.35),
        PriorSeasonBaseline(),
        RollingMedianBaseline(window=8),
    ]


def random_world(seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for e in range(12):
        pos = ("WR", "RB", "TE")[e % 3]
        for season in (2019, 2020, 2021):
            n = int(rng.integers(6, 17))
            values = rng.poisson(lam=3 + e % 5, size=n).tolist()
            rows += series(f"P{e}", season, values, position=pos)
    return actuals(rows)


# --------------------------------------------------------------------------- #
# Names, contract, protocol
# --------------------------------------------------------------------------- #


def test_names_and_contract():
    names = [m.name for m in default_baselines()]
    assert names == [
        "rolling_avg_4",
        "rolling_avg_3",
        "rolling_ew_035",
        "prior_season",
        "rolling_median_8",
    ]
    data = random_world()
    for model in all_models():
        assert isinstance(model, WeeklyModel)
        out = model.predict_from_actuals(data, [2021], REC_ONLY)
        assert out.columns == list(PREDICTION_COLUMNS)
        validate_predictions(out)
        assert set(out["scoring"]) == {"rec"}
        assert set(out["model"]) == {model.name}
        # D-053: the point estimate is the median
        assert out["pred_mean"].to_list() == out["pred_q50"].to_list()


def test_rolling_mean_uses_only_earlier_games():
    data = actuals(series("A", 2021, [10, 20, 30, 40, 50, 60]))
    out = RollingAverageBaseline(window=4).predict_from_actuals(data, [2021], REC_ONLY)
    assert pred(out, "A", 2021, 5)["pred_mean"] == pytest.approx(25.0)
    assert pred(out, "A", 2021, 6)["pred_mean"] == pytest.approx(35.0)
    assert pred(out, "A", 2021, 2)["pred_mean"] == pytest.approx(10.0)
    assert set(out["model"]) == {"rolling_avg_4"}


@pytest.mark.parametrize("week", [3, 6, 9])
def test_current_and_later_games_never_reach_a_prediction(week):
    """Perturbing week w and every later game leaves the week-w predictions unchanged."""
    base = random_world(seed=week)
    later = (pl.col("season") == 2021) & (pl.col("week") >= week)
    bumped = base.with_columns(
        pl.when(later).then(pl.col("rec") * 7 + 13).otherwise(pl.col("rec")).alias("rec")
    )
    for model in all_models():
        a = model.predict_from_actuals(base, [2021], REC_ONLY, diagnostics=True)
        b = model.predict_from_actuals(bumped, [2021], REC_ONLY, diagnostics=True)
        upto = pl.col("week") <= week
        assert a.filter(upto).equals(b.filter(upto)), model
        if not isinstance(model, PriorSeasonBaseline):
            nxt = pl.col("week") == week + 1
            assert not a.filter(nxt).equals(b.filter(nxt)), model


def test_window_three_exponential_and_median_variants():
    data = actuals(series("A", 2021, [10, 20, 30, 40, 50]))
    l3 = RollingAverageBaseline(window=3).predict_from_actuals(data, [2021], REC_ONLY)
    assert pred(l3, "A", 2021, 5)["pred_mean"] == pytest.approx(30.0)
    ew = RollingAverageBaseline(ew_alpha=0.35).predict_from_actuals(data, [2021], REC_ONLY)
    w = np.array([0.65**2, 0.65, 1.0])
    assert pred(ew, "A", 2021, 4)["pred_mean"] == pytest.approx(
        float(np.dot(w, [10, 20, 30]) / w.sum())
    )
    assert set(ew["model"]) == {"rolling_ew_035"}


def test_median_baseline_is_the_median_of_the_last_eight_games():
    values = [0, 30, 2, 2, 2, 9, 9, 9, 1, 50, 4]
    data = actuals(series("A", 2021, values))
    out = RollingMedianBaseline().predict_from_actuals(data, [2021], REC_ONLY)
    assert set(out["model"]) == {"rolling_median_8"}
    # week 11: last 8 games are weeks 3-10
    assert pred(out, "A", 2021, 11)["pred_mean"] == pytest.approx(float(np.median(values[2:10])))
    # week 4: only three games, median of those
    assert pred(out, "A", 2021, 4)["pred_q50"] == pytest.approx(2.0)
    # an outlier moves the mean, not the median
    l4 = RollingAverageBaseline(window=4).predict_from_actuals(data, [2021], REC_ONLY)
    assert pred(l4, "A", 2021, 11)["pred_mean"] > pred(out, "A", 2021, 11)["pred_mean"]


def test_history_crosses_season_boundary():
    data = actuals(series("A", 2020, [8, 8, 8, 8, 8, 8]) + series("A", 2021, [20, 20]))
    out = RollingAverageBaseline(window=4).predict_from_actuals(data, [2021], REC_ONLY)
    assert pred(out, "A", 2021, 1)["pred_mean"] == pytest.approx(8.0)
    assert pred(out, "A", 2021, 2)["pred_mean"] == pytest.approx((8 * 3 + 20) / 4)


def test_cutoff_is_first_kickoff_of_the_week():
    rows = series("A", 2021, [10, 10, 10]) + series("B", 2021, [1, 2, 3, 4, 5])
    # A's week-4 game is postponed to the Tuesday after week 5's Thursday opener,
    # and B opens week 5 on Thursday night.
    rows.append(
        {
            "entity_id": "A",
            "season": 2021,
            "week": 4,
            "rec": 90,
            "kickoff_utc": kickoff(2021, 5, day_offset=2),
        }
    )
    rows.append({"entity_id": "A", "season": 2021, "week": 5, "rec": 0})
    rows = [
        dict(r, kickoff_utc=kickoff(2021, 5, day_offset=-3))
        if (r["entity_id"] == "B" and r["week"] == 5)
        else r
        for r in rows
    ]
    out = RollingAverageBaseline(window=4).predict_from_actuals(
        actuals(rows), [2021], REC_ONLY, diagnostics=True
    )
    a5 = pred(out, "A", 2021, 5)
    # the postponed game kicked off after the week-5 cutoff, so it is not used
    assert a5["n_hist"] == 3
    assert a5["pred_mean"] == pytest.approx(10.0)
    b5 = pred(out, "B", 2021, 5)
    assert b5["n_hist"] == 4 and b5["pred_mean"] == pytest.approx(2.5)


def test_every_rostered_row_in_requested_seasons_is_predicted():
    rows = series("A", 2020, [5, 6]) + series("A", 2021, [7, 8])
    rows.append({"entity_id": "A", "season": 2021, "week": 3, "rec": 0, "played": False})
    out = RollingAverageBaseline().predict_from_actuals(actuals(rows), [2021], REC_ONLY)
    assert sorted(out["week"].to_list()) == [1, 2, 3]
    assert set(out["season"]) == {2021}
    # the unplayed week-3 row is predicted from the played games before it
    assert pred(out, "A", 2021, 3)["pred_mean"] == pytest.approx((5 + 6 + 7 + 8) / 4)


# --------------------------------------------------------------------------- #
# Residual quantiles (D-053)
# --------------------------------------------------------------------------- #


def test_entity_residual_quantiles_from_last_eight_games():
    values = [3, 9, 1, 7, 12, 4, 6, 10, 2, 8, 5]
    data = actuals(series("A", 2021, values))
    out = RollingAverageBaseline(window=4).predict_from_actuals(
        data, [2021], REC_ONLY, diagnostics=True
    )
    # estimates issued for weeks 2..11 and the residuals of weeks 2..10
    est = {w: float(np.mean(values[max(0, w - 5) : w - 1])) for w in range(2, 12)}
    resid = [values[w - 1] - est[w] for w in range(2, 11)]
    row = pred(out, "A", 2021, 11)
    assert row["quantile_source"] == "entity" and row["n_resid"] == 8
    expected = est[11] + np.quantile(resid[-8:], TAILS)
    expected[:3] = np.minimum(expected[:3], est[11])
    expected[3:] = np.maximum(expected[3:], est[11])
    floor = 0.0  # no prior season: no position minimum, so no clipping beyond the estimate
    expected = np.maximum(expected, floor)
    assert [row[c] for c in TAIL_COLS] == pytest.approx(list(expected))
    assert row["pred_q50"] == pytest.approx(est[11])
    # week 6 has four residuals (weeks 2-5): entity quantiles start there
    assert pred(out, "A", 2021, 6)["quantile_source"] == "entity"
    assert pred(out, "A", 2021, 5)["n_resid"] == 3


def test_position_residual_fallback_and_minimum_clip():
    # prior season: one WR with known residuals against its own rolling mean
    prior = series("W1", 2020, [0, 10, 0, 10, 0, 10, 0, 10]) + series("T1", 2020, [50] * 6, "TE")
    current = series("A", 2021, [7, 7, 7, 7])
    data = actuals(prior + current)
    model = RollingAverageBaseline(window=4)
    out = model.predict_from_actuals(data, [2021], REC_ONLY, diagnostics=True)
    row = pred(out, "A", 2021, 4)  # three earlier games, two residuals
    assert row["quantile_source"] == "position"
    vals = [0, 10, 0, 10, 0, 10, 0, 10]
    w1_resid = [vals[w - 1] - np.mean(vals[max(0, w - 5) : w - 1]) for w in range(2, 9)]
    expected = 7.0 + np.quantile(w1_resid, TAILS)
    expected[:3] = np.minimum(expected[:3], 7.0)
    expected[3:] = np.maximum(expected[3:], 7.0)
    expected = np.maximum(expected, 0.0)  # WR minimum observed in 2020 is 0
    assert [row[c] for c in TAIL_COLS] == pytest.approx(list(expected))
    assert row["pred_q05"] >= 0.0
    # TE residuals never enter the WR fallback
    assert row["pred_q95"] < 50


@pytest.mark.parametrize("seed", [0, 1, 2])
def test_residual_quantiles_are_monotone(seed):
    data = random_world(seed)
    for model in all_models():
        out = model.predict_from_actuals(data, [2020, 2021], REC_ONLY)
        # rows of the first data seasons can lack an estimate or prior residuals
        out = out.drop_nulls(QUANTILE_COLUMNS)
        assert out.height > 100
        q = out.select(QUANTILE_COLUMNS).to_numpy()
        assert np.all(np.diff(q, axis=1) >= -1e-9), model
        validate_predictions(out)
        lower = out.select(pl.min_horizontal(QUANTILE_COLUMNS)).to_series()
        assert (lower >= -1e-9).all()  # receptions are never negative


# --------------------------------------------------------------------------- #
# Prior season and rookie fallback
# --------------------------------------------------------------------------- #


def _rookie_world() -> tuple[pl.DataFrame, pl.DataFrame]:
    rows = []
    rows += series("V1", 2019, [10] * 10)  # first data season: excluded from the rookie class
    rows += series("V1", 2020, [12, 14, 16, 18])
    rows += series("R1a", 2020, [4] * 10)  # 2020 rookies, round 1
    rows += series("R1b", 2020, [8] * 10)
    rows += series("R5", 2020, [1] * 5)  # 2020 rookie, round 5 (cell too small)
    rows += series("V1", 2021, [30, 30])
    rows += series("N1", 2021, [50, 50])  # 2021 rookie, round 1
    rows += series("N3", 2021, [50, 50])  # 2021 rookie, round 3 (no cell)
    rows += series("U0", 2021, [50, 50])  # undrafted with no history
    players = pl.DataFrame(
        {
            "player_id": ["V1", "R1a", "R1b", "R5", "N1", "N3", "U0"],
            "draft_round": [2, 1, 1, 5, 1, 3, None],
        },
        schema={"player_id": pl.String, "draft_round": pl.Int32},
    )
    return actuals(rows), players


def test_prior_season_average():
    data, players = _rookie_world()
    out = PriorSeasonBaseline().predict_from_actuals(
        data, [2021], REC_ONLY, players=players, diagnostics=True
    )
    v1 = pred(out, "V1", 2021, 2)
    assert v1["pred_mean"] == pytest.approx(15.0)
    assert v1["mean_source"] == "history"
    assert set(out["model"]) == {"prior_season"}


def test_rookie_fallback_uses_position_and_draft_round():
    data, players = _rookie_world()
    out = PriorSeasonBaseline().predict_from_actuals(
        data, [2021], REC_ONLY, players=players, diagnostics=True
    )
    n1 = pred(out, "N1", 2021, 1)
    assert n1["mean_source"] == "fallback"
    assert n1["pred_mean"] == pytest.approx(6.0)  # 20 round-1 rookie games averaging 6
    # a round with fewer than 20 rookie games uses the position-level rookie average
    level = (10 * 4 + 10 * 8 + 5 * 1) / 25
    assert pred(out, "N3", 2021, 1)["pred_mean"] == pytest.approx(level)
    # undrafted (null round = 0) has no cell either
    assert pred(out, "U0", 2021, 1)["pred_mean"] == pytest.approx(level)
    # the rookie's own 2021 games never enter its prior-season prediction
    assert pred(out, "N1", 2021, 2)["pred_mean"] == pytest.approx(6.0)


def test_rolling_baseline_falls_back_for_players_without_games():
    data, players = _rookie_world()
    for model in (RollingAverageBaseline(), RollingMedianBaseline()):
        out = model.predict_from_actuals(
            data, [2021], REC_ONLY, players=players, diagnostics=True
        )
        n1 = pred(out, "N1", 2021, 1)
        assert n1["n_hist"] == 0 and n1["mean_source"] == "fallback"
        assert n1["pred_mean"] == pytest.approx(6.0)
        assert pred(out, "N1", 2021, 2)["pred_mean"] == pytest.approx(50.0)


def test_season_after_first_data_season_uses_position_average():
    rows = series("V1", 2019, [10] * 4) + series("V2", 2019, [20] * 4) + series("N1", 2020, [5])
    out = PriorSeasonBaseline().predict_from_actuals(actuals(rows), [2020], REC_ONLY)
    assert pred(out, "N1", 2020, 1)["pred_mean"] == pytest.approx(15.0)


# --------------------------------------------------------------------------- #
# WeeklyModel adapter and loader path
# --------------------------------------------------------------------------- #


def test_weekly_protocol_returns_one_week_per_scoring(monkeypatch):
    data = random_world()
    model = RollingAverageBaseline(window=4)

    def fake_load(season, purpose, store=None):
        return {"actuals": data, "players": None, "cutoffs": week_cutoffs(data), "max_season": 2021}

    monkeypatch.setattr(model, "_load", fake_load)
    half = ScoringRules.from_dict({"rec": 0.5}, name="half")
    scorings = {"rec": REC_ONLY, "half": half}
    model.fit((2021, 4))
    frame = model.predict(2021, 4, scorings)
    validate_predictions(frame)
    assert set(frame["week"]) == {4} and set(frame["season"]) == {2021}
    assert sorted(set(frame["scoring"])) == ["half", "rec"]
    batch = model.predict_from_actuals(data, [2021], REC_ONLY)
    want = batch.filter(pl.col("week") == 4).sort("entity_id")
    got = frame.filter(pl.col("scoring") == "rec").sort("entity_id")
    assert got.equals(want)
    rec = got.select("entity_id", "pred_mean")
    hf = frame.filter(pl.col("scoring") == "half").sort("entity_id").select("pred_mean")
    assert np.allclose(hf.to_series().to_numpy(), 0.5 * rec["pred_mean"].to_numpy())
    with pytest.raises(ValueError):
        model.predict(2021, 3, scorings)  # before the fit cutoff


def test_predict_applies_the_holdout_guard(tmp_settings):
    with pytest.raises(HoldoutLockedError):
        RollingAverageBaseline().predict_seasons([2025], REC_ONLY)
    with pytest.raises(HoldoutLockedError):
        PriorSeasonBaseline().predict_seasons([2026], REC_ONLY)  # 2026 lags read 2025 outcomes
    with pytest.raises(HoldoutLockedError):
        RollingMedianBaseline().fit((2025, 1)).predict(2025, 1, {"rec": REC_ONLY})


def test_invalid_parameters():
    with pytest.raises(ValueError):
        RollingAverageBaseline(window=0)
    with pytest.raises(ValueError):
        RollingAverageBaseline(ew_alpha=1.5)
    with pytest.raises(ValueError):
        RollingMedianBaseline(window=0)
