"""Online conformal offsets, online isotonic probabilities, and frozen calibration (synthetic)."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.models.calibration import (
    CAL_SUFFIX,
    CALIBRATION_VERSION,
    FrozenCalibration,
    OnlineConformal,
    OnlineIsotonic,
    calibration_path_for,
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
    assert frozen.meta["calibration_version"] == CALIBRATION_VERSION
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
    assert set(out["calibration_version"]) == {CALIBRATION_VERSION}
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
