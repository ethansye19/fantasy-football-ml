"""Online conformal offsets, online isotonic probabilities, and frozen calibration (synthetic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.models.calibration import (
    CAL_SUFFIX,
    CALIBRATION_V2,
    CALIBRATION_VERSION,
    FrozenCalibration,
    OnlineConformal,
    OnlineIsotonic,
    OnlinePITQuantile,
    calibration_path_for,
    pit_limits,
    quantile_at,
    save_next_to,
    sort_quantiles,
    source_column,
)
from ffml.models.protocol import QUANTILE_LEVELS

QCOLS = [f"pred_q{int(round(q * 100)):02d}" for q in QUANTILE_LEVELS]
Z = {0.05: -1.645, 0.10: -1.2816, 0.25: -0.6745, 0.5: 0.0, 0.75: 0.6745, 0.90: 1.2816, 0.95: 1.645}


def week_start(season: int, week: int) -> datetime:
    return datetime(season, 9, 7, 17, 0, tzinfo=UTC) + timedelta(days=7 * (week - 1))


def frame(
    seed: int = 0,
    seasons=(2021, 2022),
    weeks=range(1, 7),
    n: int = 80,
    width: float = 0.5,
    bias: float = 0.0,
    model: str = "m",
    scoring: str = "ppr",
) -> pl.DataFrame:
    """Rows whose predicted quantiles are too narrow (``width`` x true sd) and shifted by ``bias``."""
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for week in weeks:
            for pos, sd in (("WR", 6.0), ("RB", 4.0)):
                mu = rng.uniform(5, 15, size=n)
                actual = mu + rng.normal(0, sd, size=n)
                for i in range(n):
                    row = {
                        "entity_id": f"{pos}{i}",
                        "position": pos,
                        "season": season,
                        "week": week,
                        "scoring": scoring,
                        "model": model,
                        "pred_mean": float(mu[i] + bias),
                        "actual": float(actual[i]),
                        "active": True,
                        "kickoff_utc": week_start(season, week),
                        "week_cutoff": week_start(season, week),
                    }
                    for q in QUANTILE_LEVELS:
                        row[f"pred_q{int(round(q * 100)):02d}"] = float(
                            mu[i] + bias + Z[q] * sd * width
                        )
                    rows.append(row)
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def week_rows(f: pl.DataFrame, season: int, week: int) -> pl.DataFrame:
    return f.filter((pl.col("season") == season) & (pl.col("week") == week)).sort(
        ["position", "entity_id"]
    )


# --------------------------------------------------------------------------- #
# OnlineConformal
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("target", [(2021, 3), (2022, 1), (2022, 5)])
def test_offsets_for_week_use_only_earlier_weeks(target):
    base = frame()
    season, week = target
    later = (pl.col("season") > season) | ((pl.col("season") == season) & (pl.col("week") >= week))
    perturbed = base.with_columns(
        pl.when(later).then(pl.col("actual") * 3 + 40).otherwise(pl.col("actual")).alias("actual")
    )
    cal = OnlineConformal(min_count=20)
    a = cal.transform(base)
    b = cal.transform(perturbed)
    qa = week_rows(a, season, week).select(QCOLS, "cal_n")
    qb = week_rows(b, season, week).select(QCOLS, "cal_n")
    assert qa.equals(qb)
    # the perturbation does reach the next week's offsets
    nxt = (season, week + 1)
    assert not week_rows(a, *nxt).select(QCOLS).equals(week_rows(b, *nxt).select(QCOLS))


def test_first_week_is_unchanged_and_model_is_renamed():
    base = frame()
    out = OnlineConformal().transform(base)
    assert set(out["model"]) == {"m" + CAL_SUFFIX}
    first = week_rows(out, 2021, 1)
    assert first.select(QCOLS).equals(week_rows(base, 2021, 1).select(QCOLS))
    assert set(first["cal_n"]) == {0}
    assert out.height == base.height


def test_offsets_restore_nominal_coverage():
    base = frame(width=0.5, bias=2.0, n=150)
    out = OnlineConformal(min_count=50).transform(base)
    late = pl.col("season") == 2022

    def share_below(f: pl.DataFrame, col: str) -> float:
        g = f.filter(late)
        return float((g["actual"] <= g[col]).mean())

    for q, col in zip(QUANTILE_LEVELS, QCOLS, strict=True):
        raw = share_below(base, col)
        calibrated = share_below(out, col)
        assert abs(calibrated - q) < 0.03, (col, raw, calibrated)
        if q in (0.05, 0.95):
            assert abs(raw - q) > 0.05
    # the mean is not touched
    assert out.sort(["season", "week", "entity_id"])["pred_mean"].equals(
        base.sort(["season", "week", "entity_id"])["pred_mean"]
    )


def test_offsets_are_per_position_and_respect_min_count():
    base = frame(n=30)
    cal = OnlineConformal(min_count=100)
    out = cal.transform(base)
    # week 4 has 3 x 30 = 90 earlier rows per position: below min_count, no offset
    assert week_rows(out, 2021, 4).select(QCOLS).equals(week_rows(base, 2021, 4).select(QCOLS))
    w5 = week_rows(out, 2021, 5)
    assert set(w5["cal_n"]) == {120}
    off = cal.offsets(base.filter((pl.col("season") == 2021) & (pl.col("week") < 5)))
    assert set(off["position"]) == {"WR", "RB"}
    wr = off.filter(pl.col("position") == "WR")["off_q95"][0]
    rb = off.filter(pl.col("position") == "RB")["off_q95"][0]
    assert wr > rb > 0  # WR is wider in truth, so it needs the larger widening


def test_partitions_never_share_offsets():
    a = frame(seed=1, model="a", width=0.5)
    b = frame(seed=1, model="b", width=2.0)
    both = pl.concat([a, b])
    out = OnlineConformal(min_count=20).transform(both)
    alone = OnlineConformal(min_count=20).transform(a)
    got = out.filter(pl.col("model") == "a" + CAL_SUFFIX).sort(
        ["season", "week", "position", "entity_id"]
    )
    want = alone.sort(["season", "week", "position", "entity_id"])
    assert got.select(QCOLS).equals(want.select(QCOLS))


def test_games_after_the_week_cutoff_are_excluded():
    base = frame(n=60)
    # move week-2 games past week 3's first kickoff (a postponed slate)
    late = (pl.col("season") == 2021) & (pl.col("week") == 2)
    moved = base.with_columns(
        pl.when(late)
        .then(pl.lit(week_start(2021, 3) + timedelta(days=2)))
        .otherwise(pl.col("kickoff_utc"))
        .alias("kickoff_utc")
    )
    cal = OnlineConformal(min_count=10)
    out = cal.transform(moved)
    assert set(week_rows(out, 2021, 3)["cal_n"]) == {60}  # only week 1 counts for week 3
    assert set(week_rows(out, 2021, 4)["cal_n"]) == {180}


def test_quantiles_are_resorted_and_clipped():
    f = frame(n=40).with_columns(pl.lit(-1.0).alias("floor"))
    # the last week's medians sit far above their q95: offsets alone leave them crossed
    last = (pl.col("season") == 2022) & (pl.col("week") == 6)
    f = f.with_columns(
        pl.when(last).then(pl.col("pred_q95") + 30).otherwise(pl.col("pred_q50")).alias("pred_q50")
    )
    out = OnlineConformal(min_count=10).transform(f, lower_bound="floor")
    crossed = week_rows(out, 2022, 6)
    assert (crossed["pred_q95"] >= crossed["pred_q50"]).all()
    assert (crossed["pred_q95"] > week_rows(f, 2022, 6)["pred_q95"] + 20).all()
    q = out.select(QCOLS).to_numpy()
    assert np.all(np.diff(q, axis=1) >= -1e-12)
    assert np.all(q >= -1.0 - 1e-12)
    s = sort_quantiles(pl.DataFrame({"a": [3.0, None], "b": [1.0, 2.0]}), ["a", "b"])
    assert s.row(0) == (1.0, 3.0) and s.row(1) == (None, 2.0)


# --------------------------------------------------------------------------- #
# Frozen calibration
# --------------------------------------------------------------------------- #


def test_fit_final_round_trip_and_apply(tmp_path):
    base = frame(seed=3, n=100)
    cal = OnlineConformal(min_count=20)
    frozen = cal.fit_final(base, meta={"backtest_run_id": "unit"})
    expected = cal.offsets(base).sort("position")
    table = frozen.offset_table().sort("position")
    assert table["off_q90"].to_list() == pytest.approx(expected["off_q90"].to_list())
    path = save_next_to(frozen, tmp_path / "model_v1")
    assert path == tmp_path / "model_v1" / "calibration.json"
    assert calibration_path_for(tmp_path / "chain.pkl") == tmp_path / "chain.calibration.json"
    loaded = FrozenCalibration.load(path)
    assert loaded.meta["backtest_run_id"] == "unit"
    new = frame(seed=4, seasons=(2023,), weeks=range(1, 3), n=100)
    applied = loaded.apply(new, rename=True)
    assert set(applied["model"]) == {"m" + CAL_SUFFIX}
    wr = applied.filter(pl.col("position") == "WR").sort("entity_id")
    raw = new.filter(pl.col("position") == "WR").sort("entity_id")
    off = table.filter(pl.col("position") == "WR")["off_q90"][0]
    # the q90 offset is large enough that sorting never changes the q90 column here
    assert wr["pred_q90"].to_numpy() == pytest.approx(raw["pred_q90"].to_numpy() + off)
    # an unknown scoring gets no offsets unless mapped to a fitted one
    other = new.with_columns(pl.lit("league_x").alias("scoring"))
    assert loaded.apply(other).select(QCOLS).equals(other.select(QCOLS))
    mapped = loaded.apply(other, use_scoring="ppr")
    assert mapped.select(QCOLS).equals(loaded.apply(new).select(QCOLS))
    with pytest.raises(ValueError):
        cal.fit_final(pl.concat([base, base.with_columns(pl.lit("z").alias("model"))]))


# --------------------------------------------------------------------------- #
# OnlineIsotonic
# --------------------------------------------------------------------------- #


def prob_frame(seed: int = 0, n: int = 300) -> pl.DataFrame:
    """Over-confident boom probabilities: the true rate is prob**2."""
    rng = np.random.default_rng(seed)
    rows = []
    for season in (2021, 2022):
        for week in range(1, 6):
            p = rng.uniform(0, 1, size=n)
            y = (rng.uniform(0, 1, size=n) < p**2).astype(float)
            for i in range(n):
                rows.append(
                    {
                        "entity_id": f"E{i}",
                        "position": "WR",
                        "season": season,
                        "week": week,
                        "model": "m",
                        "scoring": "ppr",
                        "boom_prob": float(p[i]),
                        "boom_actual": float(y[i]),
                        "actual": 1.0,
                        "active": True,
                    }
                )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def test_isotonic_uses_only_earlier_weeks_and_improves_brier():
    base = prob_frame()
    iso = OnlineIsotonic(min_count=200)
    out = iso.transform(base)
    target = (2022, 2)
    later = (pl.col("season") == 2022) & (pl.col("week") >= 2)
    flipped = base.with_columns(
        pl.when(later)
        .then(1.0 - pl.col("boom_actual"))
        .otherwise(pl.col("boom_actual"))
        .alias("boom_actual")
    )
    out2 = iso.transform(flipped)
    a = week_rows(out, *target).select("entity_id", "boom_prob")
    b = week_rows(out2, *target).select("entity_id", "boom_prob")
    assert a.equals(b)
    # week 1 of 2021 has no history: unchanged
    assert week_rows(out, 2021, 1)["boom_prob"].equals(week_rows(base, 2021, 1)["boom_prob"])
    late = pl.col("season") == 2022

    def brier(f: pl.DataFrame) -> float:
        g = f.filter(late)
        return float(((g["boom_prob"] - g["boom_actual"]) ** 2).mean())

    assert brier(out) < brier(base) - 0.01
    frozen = OnlineConformal().fit_final(
        base.with_columns(*[pl.lit(1.0).alias(c) for c in QCOLS]), isotonic=iso
    )
    assert "boom_prob" in frozen.isotonic
    applied = frozen.apply(base.with_columns(*[pl.lit(1.0).alias(c) for c in QCOLS]))
    assert applied["boom_prob"].max() <= 1.0 and applied["boom_prob"].min() >= 0.0
    assert iso.transform(base.drop("boom_prob")).equals(base.drop("boom_prob"))


# --------------------------------------------------------------------------- #
# Per-position isotonic (calibration v2, D-086)
# --------------------------------------------------------------------------- #

#: true event rate as a function of the stated probability, per position
TRUTH = {"WR": lambda p: p**2, "K": lambda p: 0.15 + 0.1 * p, "TE": lambda p: p}


def position_prob_frame(seed: int = 0, sizes=None, weeks=range(1, 7), seasons=(2021, 2022)):
    """Boom and bust probabilities miscalibrated differently by position (K nearly flat)."""
    sizes = sizes or {"WR": 120, "K": 60, "TE": 15}
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for week in weeks:
            for pos, n in sizes.items():
                p = rng.uniform(0, 1, size=n)
                boom = (rng.uniform(size=n) < TRUTH[pos](p)).astype(float)
                bust = (rng.uniform(size=n) < TRUTH[pos](1 - p)).astype(float)
                for i in range(n):
                    rows.append(
                        {
                            "entity_id": f"{pos}{i}",
                            "position": pos,
                            "season": season,
                            "week": week,
                            "model": "m",
                            "scoring": "ppr",
                            "boom_prob": float(p[i]),
                            "boom_actual": float(boom[i]),
                            "bust_prob": float(1 - p[i]),
                            "bust_actual": float(bust[i]),
                            "actual": 1.0,
                            "active": True,
                            "kickoff_utc": week_start(season, week),
                            "week_cutoff": week_start(season, week),
                        }
                    )
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def by_pos(f: pl.DataFrame, pos: str, season: int, week: int) -> pl.DataFrame:
    return week_rows(f, season, week).filter(pl.col("position") == pos)


@pytest.mark.parametrize("target", [(2021, 4), (2022, 2)])
def test_per_position_maps_use_only_earlier_weeks(target):
    base = position_prob_frame()
    season, week = target
    later = (pl.col("season") > season) | ((pl.col("season") == season) & (pl.col("week") >= week))
    flipped = base.with_columns(
        pl.when(later & (pl.col("position") == "K"))
        .then(1.0 - pl.col("boom_actual"))
        .otherwise(pl.col("boom_actual"))
        .alias("boom_actual")
    )
    iso = OnlineIsotonic(min_count=100)
    a, b = iso.transform(base), iso.transform(flipped)
    for pos in ("WR", "K", "TE"):
        cols = ["entity_id", "boom_prob", "bust_prob", "cal_boom_source", "cal_bust_source"]
        assert (
            by_pos(a, pos, season, week)
            .select(cols)
            .equals(by_pos(b, pos, season, week).select(cols))
        )
    # the next week's kicker map sees the flipped outcomes
    nxt = (season, week + 1)
    assert not by_pos(a, "K", *nxt)["boom_prob"].equals(by_pos(b, "K", *nxt)["boom_prob"])
    # and a kicker-only change never moves the WR map (WR has its own rows)
    assert by_pos(a, "WR", *nxt)["boom_prob"].equals(by_pos(b, "WR", *nxt)["boom_prob"])


def test_per_position_fixes_what_pooling_cannot():
    base = position_prob_frame(sizes={"WR": 200, "K": 200, "TE": 15}, weeks=range(1, 9))
    late = (pl.col("season") == 2022) & (pl.col("position") == "K")
    per_pos = OnlineIsotonic(min_count=200).transform(base)
    pooled = OnlineIsotonic(by=(), min_count=200).transform(base)

    def brier(f):
        g = f.filter(late)
        return float(((g["boom_prob"] - g["boom_actual"]) ** 2).mean())

    truth = TRUTH["K"](base.filter(late)["boom_prob"])

    def error(f):
        """Mean distance of the calibrated kicker probability from the true rate."""
        return float((f.filter(late)["boom_prob"] - truth).abs().mean())

    assert brier(per_pos) < brier(pooled) - 0.005
    # the kicker truth is nearly flat (0.15-0.25): per-position maps track it, pooling cannot
    assert error(per_pos) < 0.5 * error(pooled)
    assert set(pooled.filter(late)["cal_boom_source"]) == {"pooled"}


def test_fallback_to_pooled_then_identity_with_sources():
    base = position_prob_frame(sizes={"WR": 120, "K": 60, "TE": 15})
    out = OnlineIsotonic(min_count=100).transform(base)
    src = source_column("boom_prob")
    assert src == "cal_boom_source"
    # week 1 has no history at all: identity, probabilities untouched
    w1 = week_rows(out, 2021, 1)
    assert set(w1[src]) == {"identity"} and set(w1["cal_bust_source"]) == {"identity"}
    assert w1["boom_prob"].equals(week_rows(base, 2021, 1)["boom_prob"])
    # week 2: WR (120 earlier rows) has its own map; K (60) and TE (15) use the pooled map (195)
    w2 = week_rows(out, 2021, 2)
    got = dict(w2.group_by("position").agg(pl.col(src).unique().first()).iter_rows())
    assert got == {"WR": "position", "K": "pooled", "TE": "pooled"}
    # the pooled map is the fit on all positions' earlier rows
    hist = base.filter((pl.col("season") == 2021) & (pl.col("week") == 1))
    pooled_only = OnlineIsotonic(by=(), min_count=100).transform(
        pl.concat([hist, week_rows(base, 2021, 2)])
    )
    te_pooled = by_pos(pooled_only, "TE", 2021, 2)["boom_prob"]
    assert by_pos(out, "TE", 2021, 2)["boom_prob"].to_list() == pytest.approx(te_pooled.to_list())
    # K reaches min_count at week 3 (two earlier weeks of 60) and switches to its own map
    w3 = dict(week_rows(out, 2021, 3).group_by("position").agg(pl.col(src).first()).iter_rows())
    assert w3 == {"WR": "position", "K": "position", "TE": "pooled"}
    # TE never reaches 100 rows in 2021 (5 x 15 = 75 before week 6)
    assert set(by_pos(out, "TE", 2021, 6)[src]) == {"pooled"}


def test_frozen_per_position_round_trip(tmp_path):
    base = position_prob_frame(sizes={"WR": 120, "K": 60, "TE": 5})
    qcols = {c: pl.lit(1.0) for c in QCOLS}
    frame = base.with_columns(**qcols)
    iso = OnlineIsotonic(min_count=100)
    frozen = OnlineConformal().fit_final(frame, isotonic=iso, meta={"run": "unit"})
    assert frozen.meta["calibration_version"] == CALIBRATION_V2  # offsets: v2
    spec = frozen.isotonic["boom_prob"]
    assert spec["keys"] == ["scoring", "position"] and spec["pooled_keys"] == ["scoring"]
    assert {e["group"]["position"] for e in spec["maps"]} == {"WR", "K"}  # TE has 60 rows
    assert [e["group"] for e in spec["pooled"]] == [{"scoring": "ppr"}]
    path = save_next_to(frozen, tmp_path / "artifact")
    loaded = FrozenCalibration.load(path)
    assert loaded.to_dict() == frozen.to_dict()

    new = position_prob_frame(
        seed=9,
        sizes={
            "WR": 20,
            "K": 20,
            "TE": 20,
        },
        seasons=(2023,),
        weeks=(1,),
    )
    new = new.with_columns(**qcols)
    out = loaded.apply(new)
    assert set(out["calibration_version"]) == {CALIBRATION_V2}
    sources = dict(out.group_by("position").agg(pl.col("cal_boom_source").first()).iter_rows())
    assert sources == {"WR": "position", "K": "position", "TE": "pooled"}
    # each position matches a direct isotonic fit on that position's rows
    from sklearn.isotonic import IsotonicRegression

    for pos in ("WR", "K"):
        train = frame.filter(pl.col("position") == pos)
        ref = IsotonicRegression(y_min=0, y_max=1, out_of_bounds="clip").fit(
            train["boom_prob"].to_numpy(), train["boom_actual"].to_numpy()
        )
        rows = new.filter(pl.col("position") == pos)
        got = out.filter(pl.col("position") == pos)["boom_prob"].to_numpy()
        assert got == pytest.approx(ref.predict(rows["boom_prob"].to_numpy()))
    # an unknown scoring is identity unless mapped
    other = new.with_columns(pl.lit("league_x").alias("scoring"))
    unmapped = loaded.apply(other)
    assert set(unmapped["cal_boom_source"]) == {"identity"}
    assert unmapped["boom_prob"].equals(other["boom_prob"])
    mapped = loaded.apply(other, use_scoring="ppr")
    assert mapped["boom_prob"].equals(out["boom_prob"])
    # v1 files (pooled maps without fallbacks) still load and apply
    legacy = frozen.to_dict()
    legacy["format"] = "ffml.calibration/1"
    legacy["isotonic"] = {
        "boom_prob": {
            "outcome": "boom_actual",
            "by": [],
            "maps": [dict(spec["pooled"][0], group={})],
        }
    }
    v1 = FrozenCalibration.from_dict(legacy).apply(new)
    assert set(v1["cal_boom_source"]) == {"pooled"}


# --------------------------------------------------------------------------- #
# Tie-aware quantile recalibration (calibration v3, D-088)
# --------------------------------------------------------------------------- #

LEVELS = np.array(QUANTILE_LEVELS)


def discrete_frame(
    seed: int = 0,
    shrink: float = 0.55,
    seasons=(2021, 2022),
    weeks=range(1, 9),
    sizes=None,
    shift: float = 0.0,
) -> pl.DataFrame:
    """Kicker-like integer scores with ties at the knots; quantiles too narrow by ``shrink``.

    ``y ~ Poisson(mu)``; the model's quantiles are the true Poisson quantiles
    pulled toward the median by ``shrink`` (1.0 = calibrated) and rounded to
    integers, so several knots share a value and actuals land exactly on them.
    """
    from scipy.stats import poisson

    sizes = sizes or {"K": 120, "DST": 60}
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for week in weeks:
            for pos, n in sizes.items():
                mu = rng.uniform(4, 10, size=n)
                y = rng.poisson(mu)
                true_q = poisson.ppf(LEVELS[None, :], mu[:, None])
                med = poisson.ppf(0.5, mu)[:, None]
                q = np.round(med + shrink * (true_q - med) + shift)
                q = np.maximum.accumulate(q, axis=1)
                for i in range(n):
                    row = {
                        "entity_id": f"{pos}{i:03d}",
                        "position": pos,
                        "season": season,
                        "week": week,
                        "model": "m",
                        "scoring": "ppr",
                        "pred_mean": float(mu[i]),
                        "actual": float(y[i]),
                        "active": True,
                        "pos_min": 0.0,
                        "pos_max": 30.0,
                        "kickoff_utc": week_start(season, week),
                        "week_cutoff": week_start(season, week),
                    }
                    row.update({c: float(q[i, j]) for j, c in enumerate(QCOLS)})
                    rows.append(row)
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def metric_pits(f: pl.DataFrame, seed: int = 0) -> np.ndarray:
    """The acceptance metric's randomized PIT (seed 0, rows in season-week-position-entity order)."""
    from ffml.eval.metrics import randomized_pit

    g = f.sort(["season", "week", "position", "entity_id"])
    return randomized_pit(
        g["actual"].to_numpy(),
        g.select(QCOLS).to_numpy(),
        g["pos_min"].to_numpy(),
        g["pos_max"].to_numpy(),
        seed=seed,
    )


def tie_aware(f: pl.DataFrame) -> float:
    u = metric_pits(f)
    return float(((u > 0.1) & (u < 0.9)).mean())


def zero_mass_frame(seed: int = 0, width: float = 0.5, weeks=range(1, 10)) -> pl.DataFrame:
    """RB/WR-like scores: 0.1-point resolution with a point mass at zero, quantiles too narrow.

    The truth is ``max(0, N(mu, 6))`` rounded to 0.1. The model's level-tau
    quantile is the true quantile at ``h(tau) = 0.5 + width * (tau - 0.5)``
    (too narrow in probability space), so its knots tie at 0 wherever
    ``h(tau)`` is below the zero mass: a tie the randomized PIT spreads.
    """
    from scipy.stats import norm

    rng = np.random.default_rng(seed)
    rows = []
    for season in (2021, 2022):
        for week in weeks:
            mu = rng.uniform(1, 14, size=200)
            y = np.round(np.maximum(0.0, rng.normal(mu, 6.0)), 1)
            h = 0.5 + width * (LEVELS - 0.5)
            q = np.maximum(0.0, mu[:, None] + 6.0 * norm.ppf(h)[None, :])
            for i in range(200):
                row = {
                    "entity_id": f"R{i:03d}",
                    "position": "RB",
                    "season": season,
                    "week": week,
                    "model": "m",
                    "scoring": "ppr",
                    "pred_mean": float(mu[i]),
                    "actual": float(y[i]),
                    "active": True,
                    "pos_min": 0.0,
                    "pos_max": 50.0,
                    "kickoff_utc": week_start(season, week),
                    "week_cutoff": week_start(season, week),
                }
                row.update({c: float(q[i, j]) for j, c in enumerate(QCOLS)})
                rows.append(row)
    return pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )


def test_v3_randomized_pits_become_uniform_with_a_point_mass_out_of_sample():
    # too wide in probability space, with a large point mass at zero (q05 = q10 = 0 in most rows)
    base = zero_mass_frame(width=1.1)
    later = pl.col("season") == 2022
    raw = base.filter(later)
    tied = raw.filter(pl.col("pred_q05") == pl.col("pred_q10"))
    assert tied.height / raw.height > 0.5 and (tied["actual"] == 0).mean() > 0.15
    cal = OnlinePITQuantile(min_count=100).transform(base).filter(later)
    assert set(cal["model"]) == {"m" + CAL_SUFFIX}
    raw_hist = np.histogram(metric_pits(raw), bins=10, range=(0, 1))[0] / raw.height
    cal_hist = np.histogram(metric_pits(cal), bins=10, range=(0, 1))[0] / cal.height
    assert np.abs(raw_hist - 0.1).max() > 0.03
    assert np.abs(cal_hist - 0.1).max() < 0.025, cal_hist
    assert tie_aware(raw) > 0.82 and abs(tie_aware(cal) - 0.8) < 0.015
    # quantiles stay monotone and floored at the bound, the mean is untouched
    q = cal.select(QCOLS).to_numpy()
    assert np.all(np.diff(q, axis=1) >= -1e-12) and q.min() >= 0.0
    key = ["entity_id", "week"]
    assert cal.sort(key)["pred_mean"].equals(raw.sort(key)["pred_mean"])


@pytest.mark.parametrize("shrink", [0.55, 1.6])
def test_v3_integer_scores_meet_the_rule_at_the_knot_levels(shrink):
    """Kicker-like integers: PITs pile up at knot levels (atoms); the atom rule handles them.

    With only seven knots an integer score cannot reach every PIT value, so the
    test checks what the acceptance rule measures: the outside shares
    ``U <= tau`` (lower levels) and ``U >= tau`` (upper levels) and the
    tie-aware coverage, against the plain ``G^-1``.
    """
    base = discrete_frame(shrink=shrink)
    later = pl.col("season") == 2022
    raw = base.filter(later)
    ties = (raw.select([(pl.col(c) == pl.col("actual")) for c in QCOLS]).to_numpy()).any(axis=1)
    assert ties.mean() > 0.3  # actuals sit exactly on a knot in many rows
    near = OnlinePITQuantile(min_count=100).transform(base).filter(later)
    plain = OnlinePITQuantile(min_count=100, atoms="plain").transform(base).filter(later)
    gap = {
        name: abs(tie_aware(f) - 0.8)
        for name, f in (("raw", raw), ("near", near), ("plain", plain))
    }
    assert gap["near"] < gap["plain"] and gap["near"] < gap["raw"]
    assert gap["near"] < 0.045  # bounded by about half an integer's probability mass
    if shrink == 0.55:
        u = metric_pits(near)
        lower = [abs((u <= t).mean() - t) for t in LEVELS[:3]]
        upper = [abs((u >= t).mean() - (1 - t)) for t in LEVELS[4:]]
        assert max(lower + upper) < 0.035 and gap["near"] < 0.02


@pytest.mark.parametrize("target", [(2021, 3), (2022, 1), (2022, 6)])
def test_v3_week_w_uses_only_earlier_weeks(target):
    base = discrete_frame(seed=2)
    season, week = target
    later = (pl.col("season") > season) | ((pl.col("season") == season) & (pl.col("week") >= week))
    bumped = base.with_columns(
        pl.when(later).then(pl.col("actual") + 7).otherwise(pl.col("actual")).alias("actual")
    )
    cal = OnlinePITQuantile(min_count=50)
    a, b = cal.transform(base), cal.transform(bumped)
    cols = ["entity_id", *QCOLS, "cal_n", "cal_q_source"]
    assert week_rows(a, season, week).select(cols).equals(week_rows(b, season, week).select(cols))
    nxt = (season, week + 1)
    assert not week_rows(a, *nxt).select(QCOLS).equals(week_rows(b, *nxt).select(QCOLS))


def test_v3_identity_when_already_calibrated_and_without_history():
    # continuous, exactly calibrated normal quantiles: G is close to the identity
    from scipy.stats import norm

    rng = np.random.default_rng(5)
    rows = []
    for week in range(1, 13):
        mu = rng.uniform(5, 15, size=300)
        y = rng.normal(mu, 3.0)
        for i in range(300):
            row = {
                "entity_id": f"W{i}",
                "position": "WR",
                "season": 2022,
                "week": week,
                "model": "m",
                "scoring": "ppr",
                "pred_mean": float(mu[i]),
                "actual": float(y[i]),
                "active": True,
                "pos_min": -20.0,
                "pos_max": 50.0,
                "kickoff_utc": week_start(2022, week),
                "week_cutoff": week_start(2022, week),
            }
            row.update(
                {c: float(mu[i] + 3.0 * norm.ppf(q)) for c, q in zip(QCOLS, LEVELS, strict=True)}
            )
            rows.append(row)
    base = pl.DataFrame(rows).with_columns(
        pl.col("season").cast(pl.Int32), pl.col("week").cast(pl.Int32)
    )
    out = OnlinePITQuantile(min_count=200).transform(base)
    # week 1: no history, identity exactly
    w1 = week_rows(out, 2022, 1)
    assert set(w1["cal_q_source"]) == {"identity"}
    assert w1.select(QCOLS).equals(week_rows(base, 2022, 1).select(QCOLS))
    # later weeks move the quantiles by a small fraction of the spread only
    late = out.filter(pl.col("week") >= 6).sort(["week", "entity_id"])
    raw = base.filter(pl.col("week") >= 6).sort(["week", "entity_id"])
    diff = np.abs(late.select(QCOLS).to_numpy() - raw.select(QCOLS).to_numpy())
    assert diff.max() < 0.5 and diff.mean() < 0.2  # sd is 3 points
    assert abs(tie_aware(late) - 0.8) < 0.03


def test_v3_fallbacks_and_sources():
    base = discrete_frame(sizes={"K": 120, "DST": 30}, weeks=range(1, 5), seasons=(2022,))
    out = OnlinePITQuantile(min_count=100).transform(base)
    src = dict(
        week_rows(out, 2022, 2).group_by("position").agg(pl.col("cal_q_source").first()).iter_rows()
    )
    assert src == {"K": "position", "DST": "pooled"}  # DST has 30 earlier rows, pooled 150
    assert set(week_rows(out, 2022, 1)["cal_q_source"]) == {"identity"}
    w4 = dict(
        week_rows(out, 2022, 4).group_by("position").agg(pl.col("cal_q_source").first()).iter_rows()
    )
    assert w4 == {"K": "position", "DST": "pooled"}  # 90 DST rows < 100


def test_v3_frozen_round_trip(tmp_path):
    base = discrete_frame(seed=7, sizes={"K": 120, "DST": 20})
    cal = OnlinePITQuantile(min_count=100)
    bounds = pl.DataFrame(
        {
            "scoring": ["ppr", "ppr"],
            "position": ["K", "DST"],
            "pos_min": [0.0, -2.0],
            "pos_max": [30.0, 35.0],
        }
    )
    frozen = cal.fit_final(base, isotonic=OnlineIsotonic(), meta={"run": "unit"}, bounds=bounds)
    assert frozen.quantile_method == "pit_quantile"
    assert frozen.meta["calibration_version"] == CALIBRATION_VERSION == "v3-tie-aware-quantile"
    groups = {(e["group"]["scoring"], e["group"]["position"]) for e in frozen.quantile_maps["maps"]}
    assert groups == {("ppr", "K"), ("ppr", "DST")}  # both have at least 100 rows (DST 16 x 20)
    path = save_next_to(frozen, tmp_path / "artifact")
    loaded = FrozenCalibration.load(path)
    assert loaded.to_dict() == frozen.to_dict()
    new = discrete_frame(seed=8, sizes={"K": 50, "DST": 50, "TE": 10}, seasons=(2023,), weeks=(1,))
    applied = loaded.apply(new, lower_bound="pos_min", upper_bound="pos_max")
    # each row's quantiles are Q_raw at the stored levels of its position
    k_levels = next(
        e["levels"] for e in loaded.quantile_maps["maps"] if e["group"]["position"] == "K"
    )
    k_rows = new.filter(pl.col("position") == "K")
    knots = np.column_stack(
        [
            np.minimum(k_rows["pos_min"].to_numpy(), k_rows["pred_q05"].to_numpy()),
            k_rows.select(QCOLS).to_numpy(),
            np.maximum(k_rows["pos_max"].to_numpy(), k_rows["pred_q95"].to_numpy()),
        ]
    )
    expect = np.sort(quantile_at(knots, QUANTILE_LEVELS, np.array(k_levels)), axis=1)
    got = applied.filter(pl.col("position") == "K").select(QCOLS).to_numpy()
    assert got == pytest.approx(expect)
    sources = dict(applied.group_by("position").agg(pl.col("cal_q_source").first()).iter_rows())
    assert sources["K"] == "position" and sources["TE"] == "pooled"
    assert set(applied["calibration_version"]) == {CALIBRATION_VERSION}
    # without bound columns the stored bounds are used (live and holdout frames)
    no_bounds = loaded.apply(new.drop("pos_min", "pos_max").filter(pl.col("position") == "K"))
    assert no_bounds.select(QCOLS).to_numpy() == pytest.approx(got)
    # an unknown scoring is identity unless mapped
    other = new.with_columns(pl.lit("league_x").alias("scoring"))
    ident = loaded.apply(other, lower_bound="pos_min", upper_bound="pos_max")
    assert set(ident["cal_q_source"]) == {"identity"}
    assert ident.select(QCOLS).to_numpy() == pytest.approx(
        np.sort(other.select(QCOLS).to_numpy(), axis=1)
    )
    mapped = loaded.apply(other, lower_bound="pos_min", upper_bound="pos_max", use_scoring="ppr")
    assert mapped.select(QCOLS).equals(applied.select(QCOLS))
    # a v2 file (offsets) still loads and applies offsets
    v2 = OnlineConformal(min_count=10).fit_final(base)
    assert FrozenCalibration.from_dict(v2.to_dict()).quantile_method == "conformal_offsets"


def test_pit_limits_match_the_metric():
    from ffml.eval.metrics import cdf_limits

    f = discrete_frame(seasons=(2022,), weeks=(1,))
    left, right = pit_limits(f)
    q = f.select(QCOLS).to_numpy()
    l2, r2 = cdf_limits(f["actual"].to_numpy(), q, f["pos_min"].to_numpy(), f["pos_max"].to_numpy())
    assert np.array_equal(left, l2) and np.array_equal(right, r2)
    assert (right - left > 0).mean() > 0.15  # point masses at the actual
