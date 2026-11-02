"""Unit tests for the point-in-time window helpers on small synthetic frames."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from ffml.features.data import build_calendar
from ffml.features.spec import PARAMS, Stat, blend_weight
from ffml.features.windows import (
    asof_join,
    compute_state,
    finalize,
    join_priors,
    join_ps,
    priors,
    season_totals,
)

T0 = datetime(2024, 9, 8, 17, 0, tzinfo=UTC)
ALPHA = PARAMS["ew_alpha"]


def _events(values, *, season=2024, start=T0, entity="a", team="A", weeks=None) -> pl.DataFrame:
    n = len(values)
    weeks = weeks or list(range(1, n + 1))
    return pl.DataFrame(
        {
            "pid": [entity] * n,
            "season": [season] * n,
            "week": weeks,
            "kickoff_utc": [start + timedelta(days=7 * (w - weeks[0])) for w in weeks],
            "team": [team] * n,
            "x": [float(v) for v in values],
            "one": [1.0] * n,
        },
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "kickoff_utc": pl.Datetime("us", "UTC"),
        },
    )


def _features(events, rows, stats, *, keep=None, carry=(), reset_by=(), prior_group=None):
    state = compute_state(events, ["pid"], stats, "opp", "t", reset_by=reset_by, carry=carry)
    df = asof_join(rows, state, ["pid"], ["pid"], "t")
    df = join_ps(df, season_totals(events, ["pid"], stats, "t"), ["pid"], ["pid"])
    if prior_group is not None:
        pr = priors(events, [prior_group], stats, "t", order=["kickoff_utc", "pid"])
        df = join_priors(df, pr, [prior_group], "t")
    return finalize(df, [("opp", stats)], "t", keep=keep).sort("season", "week")


def _rows_at_own_kickoff(events: pl.DataFrame) -> pl.DataFrame:
    return events.select("pid", "season", "week", "team", pl.col("kickoff_utc").alias("cutoff_ts"))


def test_window_shift_excludes_current_game():
    ev = _events([10, 20, 30, 40, 50])
    stat = Stat("x", num="x", windows=("l1", "l3", "l5", "std", "car"))
    out = _features(ev, _rows_at_own_kickoff(ev), [stat])
    assert out["opp__x__l1"].to_list() == [None, 10.0, 20.0, 30.0, 40.0]
    assert out["opp__x__l3"].to_list() == [None, 10.0, 15.0, 20.0, 30.0]
    assert out["opp__x__l5"].to_list() == [None, 10.0, 15.0, 20.0, 25.0]
    assert out["opp__x__car"].to_list() == [None, 10.0, 15.0, 20.0, 25.0]
    assert out["opp__x__std"].to_list() == [None, 10.0, 15.0, 20.0, 25.0]
    # the state that was joined is always strictly before the row's cutoff
    assert (out["_t_ts"].drop_nulls() < out["cutoff_ts"].filter(out["_t_ts"].is_not_null())).all()


def test_ew_weights_and_unnormalised_sums():
    vals = [10.0, 20.0, 30.0]
    ev = _events(vals + [0.0])
    stat = Stat("x", num="x", windows=("ew",))
    out = _features(ev, _rows_at_own_kickoff(ev), [stat])
    w = [(1 - ALPHA) ** i for i in range(3)]
    expected = sum(wi * v for wi, v in zip(w, reversed(vals), strict=True)) / sum(w)
    assert out["opp__x__ew"][3] == pytest.approx(expected, rel=1e-12)
    # EW ratio of sums: games with a zero denominator do not move the ratio
    ev2 = ev.with_columns(
        pl.Series("den", [2.0, 0.0, 4.0, 1.0]), pl.Series("num", [1.0, 0.0, 3.0, 0.0])
    )
    rate = Stat("r", num="num", den="den", windows=("ew",), shrink=("ew",), k="targets")
    ev2 = ev2.with_columns(pl.lit("WR").alias("position"))
    rows = _rows_at_own_kickoff(ev2).with_columns(pl.lit("WR").alias("position"))
    out2 = _features(ev2, rows, [rate], prior_group="position")
    num = 3.0 + (1 - ALPHA) ** 2 * 1.0
    den = 4.0 + (1 - ALPHA) ** 2 * 2.0
    assert out2["opp__r__ew"][3] == pytest.approx(num / den, rel=1e-12)
    # single season, so there is no earlier-season prior and the shrunk value is null
    assert out2["opp__r__ew_shrunk"][3] is None


def test_cross_season_blend():
    prior = _events([1, 2, 3], season=2023, start=T0 - timedelta(days=365))
    cur = _events([10] * 10, season=2024)
    ev = pl.concat([prior, cur])
    stat = Stat("x", num="x", windows=("std", "ps"))
    out = _features(ev, _rows_at_own_kickoff(cur), [stat])
    by_week = {r["week"]: r for r in out.iter_rows(named=True)}
    assert by_week[1]["opp__x__ps"] == pytest.approx(2.0)
    assert by_week[1]["opp__x__std"] == pytest.approx(2.0)  # no 2024 games yet: prior season only
    w3 = blend_weight(3)
    assert w3 == pytest.approx(0.5 - 0.0625 * 2)
    assert by_week[3]["opp__x__std"] == pytest.approx(w3 * 2.0 + (1 - w3) * 10.0)
    assert blend_weight(9) == 0.0
    assert by_week[10]["opp__x__std"] == pytest.approx(10.0)


def test_team_change_resets_share_windows():
    a = _events([1, 2, 3], team="A")
    b = _events([10, 20], team="B", start=T0 + timedelta(days=21), weeks=[4, 5])
    ev = pl.concat([a, b]).with_columns(
        (pl.col("team") != pl.col("team").shift(1).over("pid"))
        .fill_null(True)
        .cast(pl.Int32)
        .cum_sum()
        .over("pid")
        .alias("stint")
    )
    share = Stat("s", num="x", windows=("l1", "l3", "ew", "std", "car"), reset=True)
    later = T0 + timedelta(days=35)
    rows = pl.DataFrame(
        {
            "pid": ["a", "a"],
            "season": [2024, 2024],
            "week": [6, 6],
            "team": ["B", "C"],
            "cutoff_ts": [later, later],
        },
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "cutoff_ts": pl.Datetime("us", "UTC"),
        },
    )
    out = _features(
        ev,
        rows,
        [share],
        keep=pl.col("_t_team") == pl.col("team"),
        carry=[pl.col("team").alias("_t_team")],
        reset_by=["stint"],
    )
    same, moved = (
        out.filter(pl.col("team") == "B").row(0, named=True),
        out.filter(pl.col("team") == "C").row(0, named=True),
    )
    assert same["opp__s__l1"] == 20.0
    assert same["opp__s__l3"] == pytest.approx(15.0)  # only the two games with team B
    assert same["opp__s__std"] == pytest.approx(15.0)
    assert same["opp__s__car"] == pytest.approx(36.0 / 5)  # career is player level
    for w in ("l1", "l3", "ew", "std"):
        assert moved[f"opp__s__{w}"] is None
    assert moved["opp__s__car"] == pytest.approx(36.0 / 5)


def test_shrinkage_formula_and_prior_from_earlier_seasons_only():
    def ev_for(season, pos, nums, dens, entity, start):
        e = _events([0] * len(nums), season=season, entity=entity, start=start)
        return e.with_columns(
            pl.Series("num", nums), pl.Series("den", dens), pl.lit(pos).alias("position")
        )

    s20 = T0 - timedelta(days=4 * 365)
    s21 = T0 - timedelta(days=3 * 365)
    ev = pl.concat(
        [
            ev_for(2020, "WR", [6.0, 4.0], [10.0, 10.0], "a", s20),
            ev_for(2020, "RB", [1.0], [10.0], "b", s20),
            ev_for(2021, "WR", [9.0, 9.0], [10.0, 10.0], "a", s21),
        ]
    )
    stat = Stat("r", num="num", den="den", windows=("std",), shrink=("std", "car"), k="targets")
    rows = ev.filter(pl.col("season") == 2021).select(
        "pid", "season", "week", "position", pl.col("kickoff_utc").alias("cutoff_ts")
    )
    out = _features(ev, rows, [stat], prior_group="position")
    k = PARAMS["shrink_k"]["targets"]
    prior_wr_2020 = 10.0 / 20.0
    # week 1 of 2021: no same-season data, shrunk STD equals the prior from 2020
    assert out["opp__r__std_shrunk"][0] == pytest.approx(prior_wr_2020)
    # week 2 of 2021: one game (9/10) shrunk toward the 2020 prior
    assert out["opp__r__std_shrunk"][1] == pytest.approx((9.0 + k * prior_wr_2020) / (10.0 + k))
    # career shrinkage uses career sums before the cutoff
    assert out["opp__r__car_shrunk"][1] == pytest.approx((19.0 + k * prior_wr_2020) / (30.0 + k))
    # changing 2021 outcomes must not move the 2021 prior (no same-season information)
    ev_alt = ev.with_columns(
        pl.when(pl.col("season") == 2021).then(0.0).otherwise(pl.col("num")).alias("num")
    )
    out_alt = _features(ev_alt, rows, [stat], prior_group="position")
    assert out_alt["opp__r__std_shrunk"][0] == pytest.approx(prior_wr_2020)
    # 2020 rows have no earlier season: shrunk is null rather than using 2020 itself
    rows20 = ev.filter(pl.col("season") == 2020, pl.col("pid") == "a").select(
        "pid", "season", "week", "position", pl.col("kickoff_utc").alias("cutoff_ts")
    )
    out20 = _features(ev, rows20, [stat], prior_group="position")
    assert out20["opp__r__std_shrunk"].null_count() == out20.height


def test_cutoff_is_first_kickoff_of_week_and_thursday_game_is_excluded():
    thursday = datetime(2024, 9, 13, 0, 15, tzinfo=UTC)
    sunday = thursday + timedelta(days=3)
    games = pl.DataFrame(
        {
            "game_id": ["w1", "w2_thu", "w2_sun"],
            "season": [2024, 2024, 2024],
            "week": [1, 2, 2],
            "game_type": ["REG"] * 3,
            "kickoff_utc": [T0, thursday, sunday],
        },
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "kickoff_utc": pl.Datetime("us", "UTC"),
        },
    )
    cal = build_calendar(games)
    w2 = cal.filter(pl.col("week") == 2).row(0, named=True)
    assert w2["cutoff_ts"] == thursday
    assert w2["asof_hist_ts"] == thursday - timedelta(hours=1)
    # entity history: week 1 game (value 5) and a Thursday game of week 2 (value 100)
    ev = pl.DataFrame(
        {
            "pid": ["a", "a"],
            "season": [2024, 2024],
            "week": [1, 2],
            "kickoff_utc": [T0, thursday],
            "x": [5.0, 100.0],
        },
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "kickoff_utc": pl.Datetime("us", "UTC"),
        },
    )
    # a Sunday row of week 2 gets the week's cutoff (the Thursday kickoff), not its own kickoff
    rows = pl.DataFrame(
        {"pid": ["a"], "season": [2024], "week": [2], "kickoff_utc": [sunday]},
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "kickoff_utc": pl.Datetime("us", "UTC"),
        },
    ).join(cal.select("season", "week", "cutoff_ts"), on=["season", "week"])
    stat = Stat("x", num="x", windows=("l1", "car"))
    out = _features(ev, rows, [stat])
    assert out["opp__x__l1"][0] == 5.0
    assert out["opp__x__car"][0] == 5.0
    # with the row's own kickoff as cutoff the Thursday game would have leaked in
    leaky = _features(ev, rows.with_columns(pl.col("kickoff_utc").alias("cutoff_ts")), [stat])
    assert leaky["opp__x__l1"][0] == 100.0
