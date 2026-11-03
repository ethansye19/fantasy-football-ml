"""Tests for ffml.eval.metrics on synthetic frames with known answers."""

from __future__ import annotations

import math

import numpy as np
import polars as pl
import pytest
from scipy import stats

from ffml.eval import metrics as m

POS_COUNTS = {"QB": 24, "RB": 48, "WR": 72, "TE": 24, "K": 16, "DST": 16}


def synth_actuals(seasons=(2022,), weeks=range(1, 11), seed=0) -> pl.DataFrame:
    """One row per entity-week with a continuous actual (no ties) and a talent level."""
    rng = np.random.default_rng(seed)
    parts = []
    for season in seasons:
        for week in weeks:
            for pos, n in POS_COUNTS.items():
                talent = rng.gamma(4.0, 3.0, n)
                actual = talent + rng.normal(0.0, 4.0, n)
                parts.append(
                    pl.DataFrame(
                        {
                            "entity_id": [f"{pos}{i:03d}" for i in range(n)],
                            "position": [pos] * n,
                            "season": [season] * n,
                            "week": [week] * n,
                            "talent": talent,
                            "actual": actual,
                        }
                    )
                )
    return pl.concat(parts)


def with_preds(base: pl.DataFrame, q50, q10=None, q90=None, model="m", active=True) -> pl.DataFrame:
    q50 = np.asarray(q50, dtype=float)
    q10 = q50 if q10 is None else np.asarray(q10, dtype=float)
    q90 = q50 if q90 is None else np.asarray(q90, dtype=float)
    return base.with_columns(
        pl.Series("pred_mean", q50),
        pl.Series("pred_q50", q50),
        pl.Series("pred_q10", q10),
        pl.Series("pred_q90", q90),
        pl.lit(active).alias("active"),
        pl.lit(model).alias("model"),
    ).drop("talent", strict=False)


def row(entity, pos, pred, actual, active=True, week=1, season=2022, **extra):
    return {
        "entity_id": entity,
        "position": pos,
        "season": season,
        "week": week,
        "pred_mean": pred,
        "pred_q10": pred,
        "pred_q50": pred,
        "pred_q90": pred,
        "actual": actual,
        "active": active,
        "model": "m",
        **extra,
    }


# --------------------------------------------------------------------------- #
# Perfect, shuffled and monotone predictions
# --------------------------------------------------------------------------- #


def test_perfect_predictions():
    base = synth_actuals()
    frame = with_preds(base, base["actual"].to_numpy())
    assert m.mae(frame) == 0.0
    assert m.interval_coverage(frame) == 1.0
    assert m.interval_width(frame) == 0.0
    for q in (0.1, 0.5, 0.9):
        assert m.pinball_loss(frame, q) == 0.0
    assert m.spearman_within(frame) == pytest.approx(1.0)
    ss = m.start_sit_accuracy(frame)
    assert ss.accuracy == 1.0 and ss.weighted_accuracy == 1.0
    assert m.lineup_efficiency(frame).mean == pytest.approx(1.0)


def test_shuffled_predictions_are_uninformative():
    base = synth_actuals(seasons=(2022, 2023), weeks=range(1, 19), seed=1)
    rng = np.random.default_rng(2)
    frame = with_preds(base, rng.gamma(4.0, 3.0, base.height))
    assert abs(m.spearman_within(frame)) < 0.08
    ss = m.start_sit_accuracy(frame)
    assert ss.n_pairs > 10_000
    assert abs(ss.accuracy - 0.5) < 0.03
    assert abs(ss.weighted_accuracy - 0.5) < 0.04


def test_monotone_predictions_give_perfect_start_sit():
    base = synth_actuals(seed=3)
    actual = base["actual"].to_numpy()
    frame = with_preds(base, 2.0 * actual + 3.0)  # strictly increasing in actual
    ss = m.start_sit_accuracy(frame)
    assert ss.accuracy == 1.0
    assert ss.weighted_accuracy == 1.0
    assert m.spearman_within(frame) == pytest.approx(1.0)
    # gap bins partition the pairs and each non-empty bin is perfect
    assert int(ss.by_gap["n_pairs"].sum()) == ss.n_pairs
    assert ss.by_gap.filter(pl.col("n_pairs") > 0)["accuracy"].to_list() == [1.0] * int(
        (ss.by_gap["n_pairs"] > 0).sum()
    )


def test_calibrated_normal_coverage_near_nominal():
    rng = np.random.default_rng(4)
    n = 20_000
    mu = rng.uniform(5, 20, n)
    sigma = rng.uniform(2, 8, n)
    actual = mu + sigma * rng.standard_normal(n)
    z = stats.norm.ppf(0.9)
    frame = pl.DataFrame(
        {
            "entity_id": [f"p{i}" for i in range(n)],
            "position": ["WR"] * n,
            "season": [2022] * n,
            "week": (np.arange(n) % 17 + 1).tolist(),
            "pred_mean": mu,
            "pred_q10": mu - z * sigma,
            "pred_q50": mu,
            "pred_q90": mu + z * sigma,
            "actual": actual,
            "active": [True] * n,
            "model": ["normal"] * n,
            "pred_cdf_at_actual": stats.norm.cdf(actual, mu, sigma),
        }
    )
    assert m.interval_coverage(frame) == pytest.approx(0.8, abs=0.015)
    assert m.interval_width(frame) == pytest.approx(float(np.mean(2 * z * sigma)))
    # pinball at the median is half the absolute error
    assert m.pinball_loss(frame, 0.5) == pytest.approx(0.5 * m.mae(frame))
    pit = m.pit_histogram(frame)
    assert pit["n"].sum() == n
    assert m.pit_max_deviation(frame) < 0.015


# --------------------------------------------------------------------------- #
# Hand-computed values
# --------------------------------------------------------------------------- #


def test_pinball_by_hand():
    frame = pl.DataFrame(
        [
            row("a", "WR", 10.0, 14.0) | {"pred_q10": 6.0, "pred_q90": 12.0},
            row("b", "WR", 10.0, 4.0) | {"pred_q10": 6.0, "pred_q90": 12.0},
        ]
    )
    # q10: a under-predicted by 8 -> 0.1*8 = 0.8; b over by 2 -> 0.9*2 = 1.8; mean 1.3
    assert m.pinball_loss(frame, 0.1) == pytest.approx(1.3)
    # q90: a under by 2 -> 0.9*2 = 1.8; b over by 8 -> 0.1*8 = 0.8; mean 1.3
    assert m.pinball_loss(frame, 0.9) == pytest.approx(1.3)
    assert m.pinball_loss(frame, 0.5) == pytest.approx(0.5 * 5.0)
    assert m.interval_coverage(frame) == 0.0
    with pytest.raises(ValueError):
        m.pinball_loss(frame, 0.25)


def test_population_excludes_inactive_and_missing():
    frame = pl.DataFrame(
        [
            row("a", "WR", 10.0, 10.0),
            row("b", "WR", 10.0, None, active=False),
            row("c", "WR", 10.0, 0.0, active=False),
            row("d", "WR", None, 5.0),
        ]
    )
    assert m.population(frame).height == 1
    assert m.mae(frame) == 0.0
    inactive = m.population(frame, include_inactive=True)
    assert inactive.height == 3  # b's null actual becomes zero; d has no projection
    assert m.mae(inactive) == pytest.approx(20.0 / 3)


def test_lineup_efficiency_hand_built_pool():
    week1 = [
        ("QB1", "QB", 20, 15), ("QB2", "QB", 18, 22),
        ("RB1", "RB", 15, 5), ("RB2", "RB", 12, 20), ("RB3", "RB", 8, 9),
        ("WR1", "WR", 14, 18), ("WR2", "WR", 13, None), ("WR3", "WR", 6, 11),
        ("TE1", "TE", 10, 7),
        ("DST1", "DST", 8, 3), ("DST2", "DST", 6, 12),
    ]
    rows = [row(e, p, float(pr), None if a is None else float(a), active=a is not None)
            for e, p, pr, a in week1]
    week2 = [("QB1", "QB", 20, 30), ("RB1", "RB", 15, 10), ("WR1", "WR", 14, 12),
             ("TE1", "TE", 10, 9), ("DST1", "DST", 8, 4)]
    rows += [row(e, p, float(pr), float(a), week=2) for e, p, pr, a in week2]
    frame = pl.DataFrame(rows)
    slots = ("QB", "RB", "WR", "FLEX", "DEF")
    result = m.lineup_efficiency(frame, slots)
    pw = result.per_week.sort("week")
    # week 1, by projection: QB1 15 + RB1 5 + WR1 18 + FLEX WR2 (did not play) 0 + DST1 3 = 41
    # optimal: QB2 22 + RB2 20 + WR1 18 + FLEX WR3 11 + DST2 12 = 83
    assert pw["chosen_points"].to_list() == [41.0, 65.0]
    assert pw["optimal_points"].to_list() == [83.0, 65.0]
    assert result.mean == pytest.approx((41 / 83 + 1.0) / 2)


def test_best_lineup_superflex_and_unknown_slot():
    positions = ["QB", "QB", "RB", "WR"]
    values = [30.0, 25.0, 10.0, 8.0]
    chosen = m.best_lineup(positions, values, ("QB", "SUPER_FLEX", "RB", "BN"))
    assert chosen == [0, 1, 2]
    assert m.best_lineup(positions, values, ("K",)) == []
    with pytest.raises(ValueError):
        m.best_lineup(positions, values, ("XFLEX",))


# --------------------------------------------------------------------------- #
# Replacement level, startable sets and the ordering rule
# --------------------------------------------------------------------------- #


def test_replacement_level_filter():
    # 30 QBs: projections strictly decreasing; the top 12 are ordered correctly
    # by actual, the rest are ordered in reverse.
    n = 30
    preds = np.arange(n, 0, -1, dtype=float)
    actual = np.concatenate([np.arange(100, 88, -1), np.arange(1, n - 11)]).astype(float)
    frame = pl.DataFrame(
        [row(f"q{i}", "QB", preds[i], actual[i]) for i in range(n)]
    )
    kept = m.replacement_filter(frame)
    assert kept.height == 12
    assert sorted(kept["pred_rank"].to_list()) == list(range(1, 13))
    assert set(kept["entity_id"]) == {f"q{i}" for i in range(12)}
    assert m.spearman_within(frame) == pytest.approx(1.0)
    unfiltered = m.spearman_within(frame, levels=None)
    assert unfiltered < 0.9
    # custom level
    assert m.replacement_filter(frame, {"QB": 5}).height == 5


def test_spearman_min_entities_and_scipy_agreement():
    rng = np.random.default_rng(5)
    n = 30
    pred = np.round(rng.normal(10, 3, n))  # rounded -> ties
    act = rng.normal(10, 5, n)
    frame = pl.DataFrame([row(f"w{i}", "WR", pred[i], act[i]) for i in range(n)])
    table = m.spearman_by_group(frame, levels=None)
    assert table.height == 1
    assert table["rho"][0] == pytest.approx(stats.spearmanr(pred, act).statistic)
    # fewer than min_entities rows -> no group
    small = frame.head(7)
    assert m.spearman_by_group(small, levels=None).height == 0
    assert math.isnan(m.spearman_within(small, levels=None))


def test_start_sit_startable_and_active_rules():
    # 40 WRs ordered by projection; actual equals projection except WR 0 who
    # was inactive and WR 37 (outside top 36) who scored the most.
    rows = []
    for i in range(40):
        pred = 40.0 - i
        actual = pred
        active = True
        if i == 0:
            actual, active = None, False
        if i == 37:
            actual = 100.0
        rows.append(row(f"w{i}", "WR", pred, actual, active=active))
    frame = pl.DataFrame(rows)
    pop = m.startable_population(frame)
    # top 36 by projection include the inactive WR 0; 35 remain after the active filter
    assert pop.height == 35
    assert "w37" not in set(pop["entity_id"])
    ss = m.start_sit_accuracy(frame)
    assert ss.n_pairs == 35 * 34 // 2
    assert ss.accuracy == 1.0
    # K and DST are not part of the default startable sets
    kd = pl.DataFrame([row(f"k{i}", "K", float(10 - i), float(i)) for i in range(10)])
    assert m.start_sit_accuracy(kd).n_pairs == 0


def test_rank_pred_breaks_projection_ties():
    # A step-shaped projection (as from a rank-to-points curve) with rank_pred.
    preds = [10.0, 10.0, 8.0, 8.0]
    actual = [20.0, 15.0, 12.0, 9.0]
    rank_pred = [1.0, 2.0, 3.0, 4.0]
    with_rank = pl.DataFrame(
        [row(f"r{i}", "RB", preds[i], actual[i], rank_pred=rank_pred[i]) for i in range(4)]
    )
    without = with_rank.drop("rank_pred")
    full = m.start_sit_accuracy(with_rank)
    assert full.n_pairs == 6 and full.accuracy == 1.0
    partial = m.start_sit_accuracy(without)
    assert partial.n_pairs == 4  # the two tied pairs carry no preference
    # tied pairs have zero gap, so the weighted version is the same either way
    assert full.weighted_accuracy == pytest.approx(partial.weighted_accuracy)
    reversed_rank = with_rank.with_columns((5.0 - pl.col("rank_pred")).alias("rank_pred"))
    assert m.start_sit_accuracy(reversed_rank).accuracy == pytest.approx(4 / 6)


# --------------------------------------------------------------------------- #
# Calibration of probabilities
# --------------------------------------------------------------------------- #


def _prob_frame(p, y):
    n = len(p)
    return pl.DataFrame(
        [row(f"e{i}", "WR", 1.0, 1.0, week=i % 17 + 1) | {"boom_prob": float(p[i]), "boom_actual": float(y[i])}
         for i in range(n)]
    )


def test_brier_and_reliability_by_hand():
    p = [0.05, 0.15, 0.95, 1.0]
    y = [0, 1, 1, 1]
    frame = _prob_frame(p, y)
    expected = np.mean((np.array(p) - np.array(y)) ** 2)
    assert m.brier(frame, "boom_prob", "boom_actual") == pytest.approx(expected)
    table = m.reliability_table(frame, "boom_prob", "boom_actual")
    assert table.height == 10
    assert table.filter(pl.col("bin") == 0)["n"][0] == 1
    assert table.filter(pl.col("bin") == 9)["n"][0] == 2  # 0.95 and 1.0
    assert table.filter(pl.col("bin") == 9)["outcome_rate"][0] == 1.0
    assert int(table["n"].sum()) == 4


def test_calibration_slope_detects_overconfidence():
    rng = np.random.default_rng(6)
    n = 20_000
    p = rng.uniform(0.02, 0.98, n)
    y = (rng.uniform(size=n) < p).astype(float)
    calibrated = _prob_frame(p, y)
    assert m.calibration_slope(calibrated, "boom_prob", "boom_actual") == pytest.approx(1.0, abs=0.08)
    # push probabilities away from 0.5 on the logit scale (over-confident) -> slope below 1
    sharp = 1 / (1 + np.exp(-2.0 * np.log(p / (1 - p))))
    over = _prob_frame(sharp, y)
    assert m.calibration_slope(over, "boom_prob", "boom_actual") == pytest.approx(0.5, abs=0.06)


# --------------------------------------------------------------------------- #
# Bootstrap
# --------------------------------------------------------------------------- #


def _noisy_frame(seed=7, scale=4.0, model="noisy", seasons=(2022,), weeks=range(1, 13)):
    base = synth_actuals(seasons=seasons, weeks=weeks, seed=0)
    rng = np.random.default_rng(seed)
    q50 = base["talent"].to_numpy() + rng.normal(0, scale, base.height)
    return with_preds(base, q50, q50 - 6, q50 + 6, model=model)


def test_bootstrap_ci_contains_point_and_is_deterministic():
    frame = _noisy_frame()
    for fn in (m.mae, m.interval_coverage, m.spearman_within, m.start_sit):
        ci = m.bootstrap_ci(fn, frame, n_boot=200, seed=0)
        assert ci.lo <= ci.point <= ci.hi, fn
        assert ci.n_boot == 200
    a = m.bootstrap_ci(m.mae, frame, n_boot=50, seed=3)
    b = m.bootstrap_ci(m.mae, frame, n_boot=50, seed=3)
    assert a == b
    assert m.bootstrap_ci(m.mae, frame, n_boot=0).lo != m.bootstrap_ci(m.mae, frame, n_boot=0).lo  # NaN


def test_bootstrap_duplicated_weeks_stay_separate_groups():
    # one season-week with 12 QBs; any resample draws that week once, so
    # replicate tagging must keep Spearman defined (12 entities, not 24 merged rows)
    base = synth_actuals(weeks=[1], seed=8).filter(pl.col("position") == "QB")
    frame = with_preds(base, base["actual"].to_numpy() * 0.5 + 1.0)
    ci = m.bootstrap_ci(m.spearman_within, frame, n_boot=20)
    assert ci.point == pytest.approx(1.0) and ci.lo == pytest.approx(1.0)
    two = pl.concat([frame, frame.with_columns(pl.lit(2, dtype=frame.schema["week"]).alias("week"))])
    boots = m.paired_bootstrap({"x": two}, m.start_sit, n_boot=30)
    assert np.all(boots["x"] == 1.0)


def test_paired_bootstrap_identical_frames_have_zero_delta():
    frame = _noisy_frame()
    boots = m.paired_bootstrap({"a": frame, "b": frame}, m.mae, n_boot=40)
    assert np.allclose(boots["a"], boots["b"])


# --------------------------------------------------------------------------- #
# evaluate, compare, markdown
# --------------------------------------------------------------------------- #


def test_validate_frame_errors():
    frame = _noisy_frame()
    with pytest.raises(ValueError, match="missing"):
        m.validate_frame(frame.drop("pred_q90"))
    with pytest.raises(ValueError, match="duplicate"):
        m.validate_frame(pl.concat([frame, frame.head(1)]))
    with pytest.raises(ValueError, match="null"):
        m.validate_frame(frame.with_columns(pl.lit(None, dtype=pl.String).alias("entity_id")))
    nan = frame.with_columns(pl.when(pl.col("week") == 1).then(float("nan")).otherwise(pl.col("pred_q50")).alias("pred_q50"))
    assert m.validate_frame(nan)["pred_q50"].null_count() > 0


def test_evaluate_tables_and_markdown():
    frame = _noisy_frame(seasons=(2022, 2023), weeks=range(1, 7))
    rng = np.random.default_rng(9)
    frame = frame.with_columns(
        pl.Series("boom_prob", rng.uniform(0, 1, frame.height)),
        pl.Series("boom_actual", (rng.uniform(0, 1, frame.height) < 0.3).astype(float)),
    )
    result = m.evaluate(frame, by=("position",), n_boot=20)
    s = result.summary
    assert {"model", "position", "metric", "value", "ci_lo", "ci_hi", "n"} <= set(s.columns)
    metrics = set(s["metric"])
    assert {"mae", "pinball_q10", "pinball_q50", "pinball_q90", "coverage_80", "interval_width",
            "spearman", "start_sit", "start_sit_weighted", "brier_boom", "lineup_efficiency"} <= metrics
    le = s.filter(pl.col("metric") == "lineup_efficiency")
    assert le.height == 1 and le["position"][0] == "ALL"
    mae_wr = s.filter((pl.col("metric") == "mae") & (pl.col("position") == "WR"))
    wr = frame.filter(pl.col("position") == "WR")
    assert mae_wr["value"][0] == pytest.approx(m.mae(wr))
    assert (mae_wr["ci_lo"][0] <= mae_wr["value"][0] <= mae_wr["ci_hi"][0])
    assert result.per_week is not None
    assert result.per_week.filter(pl.col("metric") == "mae").height == 6 * 12
    assert result.lineup.height == 12
    assert result.reliability is not None
    md = m.to_markdown(result)
    assert "## Summary" in md and "lineup_efficiency" in md and "[" in md

    # by an integer column next to position (lineup rows keep the season value)
    by_season = m.evaluate(frame, by=("position", "season"), n_boot=0, per_week=False)
    le2 = by_season.summary.filter(pl.col("metric") == "lineup_efficiency")
    assert sorted(le2["season"].to_list()) == [2022, 2023]
    assert set(le2["position"]) == {"ALL"}
    assert by_season.per_week is None


def test_compare_paired_delta_and_alignment():
    noisy = _noisy_frame(seed=10, scale=6.0)
    good = _noisy_frame(seed=11, scale=1.0)
    # the baseline lacks some rows (like Thursday players for the consensus baseline)
    partial = noisy.filter(~((pl.col("position") == "WR") & (pl.col("entity_id").str.ends_with("0"))))
    table = m.compare({"baseline": partial, "model": good}, n_boot=40)
    row_ = table.filter((pl.col("model") == "model") & (pl.col("position") == "WR") & (pl.col("metric") == "mae"))
    assert row_["delta"][0] < 0
    assert row_["delta_hi"][0] < 0
    assert row_["better"][0] is True
    n_base = table.filter((pl.col("model") == "baseline") & (pl.col("position") == "WR") & (pl.col("metric") == "mae"))["n"][0]
    assert row_["n"][0] == n_base  # aligned population
    base_rows = table.filter(pl.col("model") == "baseline")
    assert base_rows["better"].null_count() == base_rows.height
    assert table.filter(pl.col("metric") == "lineup_efficiency").height == 2
    md = m.to_markdown(table)
    assert "delta model" in md
    aligned, dropped = m.align_frames({"baseline": partial, "model": good})
    assert dropped["baseline"] == 0 and dropped["model"] == good.height - partial.height


def test_bundle_matches_individual_metric_functions():
    """The single-pass bundle returns exactly what the individual functions return."""
    rng = np.random.default_rng(11)
    base = synth_actuals(weeks=range(1, 5), seed=3)
    n = base.height
    q50 = np.round(base["talent"].to_numpy() + rng.normal(0, 3, n))  # rounding creates ties
    frame = with_preds(base, q50, q50 - 4, q50 + 4).with_columns(
        pl.Series("active", rng.random(n) > 0.15),
        pl.Series("pred_mean", q50 + 0.5),
        pl.Series("rank_pred", rng.permutation(n).astype(float)),
        pl.Series("boom_prob", rng.uniform(0, 1, n)),
        pl.Series("boom_actual", (rng.random(n) > 0.7).astype(float)),
        pl.Series("pred_cdf_at_actual", rng.uniform(0, 1, n)),
    )
    frame = frame.with_columns(
        pl.when(pl.col("entity_id").str.ends_with("3")).then(None).otherwise(pl.col("pred_q10")).alias("pred_q10"),
        pl.when(pl.col("entity_id").str.ends_with("5")).then(None).otherwise(pl.col("actual")).alias("actual"),
    )
    other = frame.with_columns((pl.col("pred_q50") + pl.Series(rng.normal(0, 5, n))).alias("pred_q50"))
    frames = m.with_reference_rank({"a": m.validate_frame(frame), "b": m.validate_frame(other)})
    f = frames["a"]
    # a replicate-tagged resample (a week drawn twice) must agree too
    idx = m._cluster_index(f, m.CLUSTER)
    keys = list(idx)
    resampled = m._resample(f, idx, keys, np.array([0, 0, 2, 3]))
    for sample in (f, resampled):
        got = m.metric_bundle(sample.columns)(sample)
        want = {
            "mae": m.mae(sample),
            "mae_mean": m.mae_mean(sample),
            "pinball_q10": m.pinball_loss(sample, 0.1),
            "pinball_q50": m.pinball_loss(sample, 0.5),
            "pinball_q90": m.pinball_loss(sample, 0.9),
            "coverage_80": m.interval_coverage(sample),
            "interval_width": m.interval_width(sample),
            "spearman": m.spearman_within(sample),
            "start_sit": m.start_sit_accuracy(sample).accuracy,
            "start_sit_weighted": m.start_sit_accuracy(sample).weighted_accuracy,
            "spearman_common": m.spearman_within(sample, common=True),
            "start_sit_common": m.start_sit_accuracy(sample, common=True).accuracy,
            "start_sit_common_weighted": m.start_sit_accuracy(sample, common=True).weighted_accuracy,
            "brier_boom": m.brier(sample, "boom_prob", "boom_actual"),
            "calib_slope_boom": m.calibration_slope(sample, "boom_prob", "boom_actual"),
            "pit_max_dev": m.pit_max_deviation(sample),
        }
        for key, value in want.items():
            assert got[key] == pytest.approx(value, rel=1e-12, abs=1e-12, nan_ok=True), key
