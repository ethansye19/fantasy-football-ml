"""Transparent baselines on synthetic stat lines (no processed data needed)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.holdout import HoldoutLockedError
from ffml.models.baselines import (
    PREDICTION_COLUMNS,
    PriorSeasonBaseline,
    RollingAverageBaseline,
)
from ffml.scoring import ScoringRules
from ffml.scoring.statline import ensure_stat_columns

# Points equal receptions, so expected values are easy to write down.
REC_ONLY = ScoringRules.from_dict({"rec": 1})


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


# --------------------------------------------------------------------------- #
# Rolling average
# --------------------------------------------------------------------------- #


def test_rolling_mean_uses_only_earlier_games():
    data = actuals(series("A", 2021, [10, 20, 30, 40, 50, 60]))
    out = RollingAverageBaseline(window=4).predict_from_actuals(data, [2021], REC_ONLY)
    assert out.columns == list(PREDICTION_COLUMNS)
    assert pred(out, "A", 2021, 5)["pred_mean"] == pytest.approx(25.0)
    assert pred(out, "A", 2021, 6)["pred_mean"] == pytest.approx(35.0)
    assert pred(out, "A", 2021, 2)["pred_mean"] == pytest.approx(10.0)
    assert set(out["model"]) == {"rolling_l4"}


def test_current_game_never_leaks_into_its_prediction():
    base = series("A", 2021, [10, 20, 30, 40, 50, 60]) + series("B", 2021, [5, 5, 5, 5, 5, 5])
    bumped = [dict(r, rec=999) if (r["entity_id"] == "A" and r["week"] == 5) else r for r in base]
    for model in (
        RollingAverageBaseline(window=4),
        RollingAverageBaseline(ew_alpha=0.35),
        PriorSeasonBaseline(),
    ):
        a = model.predict_from_actuals(actuals(base), [2021], REC_ONLY, diagnostics=True)
        b = model.predict_from_actuals(actuals(bumped), [2021], REC_ONLY, diagnostics=True)
        # predictions up to and including week 5 are identical; week 6 sees the change
        upto5 = pl.col("week") <= 5
        assert a.filter(upto5).equals(b.filter(upto5)), model
        if isinstance(model, RollingAverageBaseline):
            assert pred(a, "A", 2021, 6)["pred_mean"] != pred(b, "A", 2021, 6)["pred_mean"]


def test_window_three_and_exponential_variants():
    data = actuals(series("A", 2021, [10, 20, 30, 40, 50]))
    l3 = RollingAverageBaseline(window=3).predict_from_actuals(data, [2021], REC_ONLY)
    assert pred(l3, "A", 2021, 5)["pred_mean"] == pytest.approx(30.0)
    ew = RollingAverageBaseline(ew_alpha=0.35).predict_from_actuals(data, [2021], REC_ONLY)
    w = np.array([0.65**2, 0.65, 1.0])
    assert pred(ew, "A", 2021, 4)["pred_mean"] == pytest.approx(
        float(np.dot(w, [10, 20, 30]) / w.sum())
    )
    assert set(ew["model"]) == {"rolling_ew0.35"}


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


def test_only_played_rows_in_requested_seasons_are_predicted():
    rows = series("A", 2020, [5, 6]) + series("A", 2021, [7, 8])
    rows.append({"entity_id": "A", "season": 2021, "week": 3, "rec": 0, "played": False})
    out = RollingAverageBaseline().predict_from_actuals(actuals(rows), [2021], REC_ONLY)
    assert sorted(out["week"].to_list()) == [1, 2]
    assert set(out["season"]) == {2021}


# --------------------------------------------------------------------------- #
# Quantiles
# --------------------------------------------------------------------------- #


def test_entity_quantiles_from_last_eight_games():
    values = [1, 2, 3, 4, 5, 6, 7, 8, 9, 10, 11]
    data = actuals(series("A", 2021, values))
    out = RollingAverageBaseline().predict_from_actuals(data, [2021], REC_ONLY, diagnostics=True)
    row = pred(out, "A", 2021, 11)  # history = weeks 1-10, last 8 = 3..10
    expected = np.quantile(np.arange(3, 11), [0.1, 0.5, 0.9])
    assert [row["pred_q10"], row["pred_q50"], row["pred_q90"]] == pytest.approx(list(expected))
    assert row["quantile_source"] == "entity"
    row5 = pred(out, "A", 2021, 5)  # four games available
    assert row5["quantile_source"] == "entity"
    assert row5["pred_q50"] == pytest.approx(2.5)


def test_shifted_quantiles_recentre_the_empirical_shape_on_the_mean():
    data = actuals(series("A", 2021, list(range(1, 12))))
    emp = RollingAverageBaseline().predict_from_actuals(data, [2021], REC_ONLY)
    shf = RollingAverageBaseline(quantile_mode="shifted").predict_from_actuals(
        data, [2021], REC_ONLY
    )
    assert set(shf["model"]) == {"rolling_l4_shifted"}
    e, s = pred(emp, "A", 2021, 11), pred(shf, "A", 2021, 11)
    assert s["pred_mean"] == pytest.approx(8.5) == e["pred_mean"]
    # last 8 games are 3..10 with mean 6.5: quantiles move by 8.5 - 6.5
    for q in ("pred_q10", "pred_q50", "pred_q90"):
        assert s[q] == pytest.approx(e[q] + 2.0)
    with pytest.raises(ValueError):
        PriorSeasonBaseline(quantile_mode="normal")


def test_quantile_fallback_to_prior_season_position_distribution():
    prior = series("W1", 2020, [0, 10, 20, 30, 40]) + series("T1", 2020, [100, 100], "TE")
    current = series("A", 2021, [7, 7, 7, 7])
    out = RollingAverageBaseline().predict_from_actuals(
        actuals(prior + current), [2021], REC_ONLY, diagnostics=True
    )
    row = pred(out, "A", 2021, 4)  # three earlier games: position fallback
    assert row["quantile_source"] == "position"
    expected = np.quantile([0, 10, 20, 30, 40], [0.1, 0.5, 0.9])
    assert [row["pred_q10"], row["pred_q50"], row["pred_q90"]] == pytest.approx(list(expected))
    assert row["pred_mean"] == pytest.approx(7.0)  # the mean still uses the entity history
    # the current season's own games never feed the position distribution
    assert pred(out, "A", 2021, 1)["pred_q50"] == pytest.approx(20.0)


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
    out = RollingAverageBaseline().predict_from_actuals(
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
# Loader path
# --------------------------------------------------------------------------- #


def test_predict_applies_the_holdout_guard(tmp_settings):
    with pytest.raises(HoldoutLockedError):
        RollingAverageBaseline().predict([2025], REC_ONLY)
    with pytest.raises(HoldoutLockedError):
        PriorSeasonBaseline().predict([2026], REC_ONLY)  # 2026 lags read 2025 outcomes


def test_invalid_parameters():
    with pytest.raises(ValueError):
        RollingAverageBaseline(window=0)
    with pytest.raises(ValueError):
        RollingAverageBaseline(ew_alpha=1.5)
