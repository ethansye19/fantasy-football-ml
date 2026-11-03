"""Tests for the walk-forward validation report (EV-3.3) on tiny synthetic backtest frames.

No real or restricted rows: every frame is generated here.
"""

from __future__ import annotations

import json
import math

import numpy as np
import polars as pl
import pytest

from ffml.eval import metrics as M
from ffml.eval import report_backtest as R

POS_COUNTS = {"QB": 16, "RB": 28, "WR": 40, "TE": 16, "K": 12, "DST": 12}


def truth(seasons=(2022,), weeks=(1, 2, 3, 4), seed=0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for season in seasons:
        for week in weeks:
            for pos, n in POS_COUNTS.items():
                for i in range(n):
                    mu = 4.0 + 16.0 * (n - i) / n
                    active = bool(rng.random() > 0.1)
                    actual = float(max(0.0, rng.normal(mu, 5.0))) if active else 0.0
                    rows.append({"entity_id": f"{pos}{i:02d}", "position": pos, "season": season, "week": week,
                                 "mu": mu, "actual": actual, "active": active})
    return pl.DataFrame(rows)


def model_rows(base: pl.DataFrame, name: str, noise: float, seed: int, probs: bool = False,
               width: float = 6.0, scoring: str = "ppr") -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    q50 = base["mu"].to_numpy() + rng.normal(0.0, noise, base.height)
    out = base.with_columns(
        pl.lit(name).alias("model"),
        pl.lit(scoring).alias("scoring"),
        pl.Series("pred_q50", q50),
        pl.Series("pred_mean", q50 + 0.3),
        pl.Series("pred_q10", q50 - width),
        pl.Series("pred_q90", q50 + width),
        pl.Series("pred_cdf_at_actual", rng.uniform(0, 1, base.height)),
        pl.lit(None, dtype=pl.String).alias("team"),
        pl.lit(None, dtype=pl.String).alias("game_id"),
    )
    out = out.with_columns(
        pl.when(pl.col("active")).then(pl.col("mu") > 15).otherwise(None).cast(pl.Float64).alias("boom_actual"),
        pl.when(pl.col("active")).then(pl.col("mu") < 8).otherwise(None).cast(pl.Float64).alias("bust_actual"),
    )
    if probs:
        out = out.with_columns(
            (pl.col("pred_q50") / 25).clip(0.01, 0.99).alias("boom_prob"),
            (1 - pl.col("pred_q50") / 25).clip(0.01, 0.99).alias("bust_prob"),
            pl.lit(0.9).alias("active_prob"),
            (pl.col("pred_q50") * 0.9).alias("pred_q50_uncond"),
            (pl.col("pred_mean") * 0.9).alias("pred_mean_uncond"),
        )
    return out.drop("mu")


def write_run(tmp_path, with_chain: bool = True, scorings=("ppr", "half")):
    base = truth()
    specs = [("rolling_avg_4", 4.0, 1), ("prior_season", 4.5, 2), ("rolling_median_8", 3.5, 3),
             ("direct_lgbm", 2.0, 4)]
    if with_chain:
        specs = [("chain_v1", 1.0, 5), ("chain_v1_final_report", 1.1, 6), *specs]
    frames = []
    for scoring in scorings:
        for name, noise, seed in specs:
            probs = name.startswith("chain")
            frames.append(model_rows(base, name, noise, seed, probs=probs, width=9.0, scoring=scoring))
            frames.append(model_rows(base, name + "+cal", noise, seed, probs=probs, width=6.5, scoring=scoring))
    pred = pl.concat(frames, how="diagonal_relaxed")
    run = tmp_path / "run"
    run.mkdir()
    pred.write_parquet(run / "predictions.parquet")
    manifest = {"run_id": "synthetic", "config": "test", "git": "abc1234", "git_sessions": ["abc1234"],
                "seasons": [2022], "weeks": [1, 2, 3, 4], "schedule_weeks": 4, "report_seasons": [2022],
                "warmup_seasons": [], "refit_every": 1, "scorings": {s: "hash" for s in scorings},
                "feature_version": "test", "input_snapshots": {}, "models": [], "files": {}}
    (run / "_manifest.json").write_text(json.dumps(manifest), encoding="utf-8")
    return run


# --------------------------------------------------------------------------- #
# D-074 pair set
# --------------------------------------------------------------------------- #


def _frames():
    base = truth()
    return {name: M.validate_frame(model_rows(base, name, noise, seed))
            for name, noise, seed in (("a", 1.0, 1), ("b", 5.0, 2), ("c", 8.0, 3))}


def test_pair_set_is_model_agnostic():
    frames = M.with_reference_rank(_frames())
    sets = {}
    counts = {}
    for name, f in frames.items():
        pop = M.startable_population(f, common=True)
        sets[name] = set(pop.select("entity_id", "season", "week").iter_rows())
        counts[name] = M.start_sit_pairs(f, common=True)[0].size
    assert sets["a"] == sets["b"] == sets["c"] and sets["a"]
    assert counts["a"] == counts["b"] == counts["c"] > 0
    # the own top-N sets differ (the reason for D-074)
    own = {n: set(M.startable_population(f).select("entity_id", "season", "week").iter_rows())
           for n, f in frames.items()}
    assert own["a"] != own["c"]


def test_reference_rank_ignores_actuals_and_keeps_tied_models_on_the_set():
    raw = _frames()
    ref1 = M.reference_ranks(raw)
    shuffled = {n: f.with_columns(pl.col("actual").shuffle(seed=7)) for n, f in raw.items()}
    ref2 = M.reference_ranks(shuffled)
    keys = ["entity_id", "position", "season", "week"]
    assert ref1.sort(keys).equals(ref2.sort(keys))
    # a model with no preference (all predictions equal) keeps the same pairs, scored as coin flips
    flat = raw["a"].with_columns(pl.lit(10.0).alias("pred_q50"))
    frames = M.with_reference_rank({"a": raw["a"], "b": raw["b"], "flat": flat}, reference=["a", "b"])
    gap_a, _ = M.start_sit_pairs(frames["a"], common=True)
    gap_f, conc_f = M.start_sit_pairs(frames["flat"], common=True)
    assert gap_f.size == gap_a.size
    assert np.allclose(conc_f, 0.5)
    # the common metrics enter the bundle only when ref_rank is present
    assert "start_sit_common" in M.metric_bundle(frames["a"].columns)(frames["a"])
    assert "start_sit_common" not in M.metric_bundle(raw["a"].columns)(raw["a"])
    with pytest.raises(ValueError, match="ref_rank"):
        M.start_sit_pairs(raw["a"], common=True)


# --------------------------------------------------------------------------- #
# CI sign convention
# --------------------------------------------------------------------------- #


def test_ci_status_rule():
    assert R.ci_status(-0.5, -0.1, False) == R.STATUS_BETTER  # MAE lower, interval below zero
    assert R.ci_status(0.1, 0.5, False) == R.STATUS_WORSE
    assert R.ci_status(-0.1, 0.2, False) == R.STATUS_NONE
    assert R.ci_status(0.01, 0.05, True) == R.STATUS_BETTER  # start/sit higher
    assert R.ci_status(-0.05, -0.01, True) == R.STATUS_WORSE
    assert R.ci_status(math.nan, 0.1, True) == R.STATUS_NA
    assert R.ci_status(-1.0, -0.5, None) == R.STATUS_NA  # coverage has no direction


def test_delta_is_focus_minus_other_with_paired_interval():
    rng = np.random.default_rng(0)
    common = rng.normal(0, 0.3, 200)
    boot = R.BootResult(
        points={"QB": {"f": {"mae": 4.0, "start_sit": 0.60}, "o": {"mae": 5.0, "start_sit": 0.55}}},
        boots={"QB": {"f": {"mae": 4.0 + common + rng.normal(0, 0.01, 200), "start_sit": 0.60 + common},
                      "o": {"mae": 5.0 + common, "start_sit": 0.55 + common + rng.normal(0, 0.01, 200)}}},
        n_boot=200,
    )
    d = boot.delta("QB", "f", "o", "mae")
    assert d.delta == pytest.approx(-1.0) and d.lo < d.hi < 0 and d.status == R.STATUS_BETTER
    rev = boot.delta("QB", "o", "f", "mae")
    assert rev.delta == pytest.approx(1.0) and 0 < rev.lo and rev.status == R.STATUS_WORSE
    ss = boot.delta("QB", "f", "o", "start_sit")
    assert ss.delta == pytest.approx(0.05) and ss.lo > 0 and ss.status == R.STATUS_BETTER
    # paired: the shared noise (sd 0.3) cancels, so the interval is narrow
    assert d.hi - d.lo < 0.1
    assert "B" in d.cell() and d.cell().startswith("-1.000")


def test_run_bootstrap_sign_on_frames_and_chunks_pair():
    base = truth(weeks=tuple(range(1, 9)))
    frames = {"good": model_rows(base, "good", 0.5, 1), "bad": model_rows(base, "bad", 6.0, 2),
              "mid": model_rows(base, "mid", 3.0, 3)}
    frames = M.with_reference_rank(M.align_frames(frames)[0])
    one = R.run_bootstrap(frames, ("WR",), lineup=False, n_boot=25, seed=1, chunk=3)
    split = R.run_bootstrap(frames, ("WR",), lineup=False, n_boot=25, seed=1, chunk=1)
    d1 = one.delta("WR", "good", "bad", "mae")
    d2 = split.delta("WR", "good", "bad", "mae")
    assert d1.delta < 0 and d1.status == R.STATUS_BETTER
    assert (d1.lo, d1.hi) == pytest.approx((d2.lo, d2.hi))  # chunked replicates pair exactly


# --------------------------------------------------------------------------- #
# Verdict rendering
# --------------------------------------------------------------------------- #


def _delta(metric, status, d=-0.1):
    lo, hi = {"beats": (-0.2, -0.05), "worse": (0.05, 0.2), "no difference": (-0.1, 0.1)}[status]
    if M.HIGHER_IS_BETTER.get(metric):
        lo, hi = -hi, -lo
    return R.Delta(metric, "chain_v1", "x", 1.0, 1.1, d, lo, hi, status)


def test_verdict_lists_every_non_beat_cell_and_coverage():
    models = R.ModelSet("chain_v1", None, "direct_lgbm", ["rolling_avg_4", "prior_season", "rolling_median_8"])
    cells = []
    statuses = iter(["beats", "no difference", "worse", "beats"] * 100)
    for form in ("calibrated", "raw"):
        for pos in ("QB", "K"):
            for metric in R.VERDICT_METRICS:
                if metric.startswith("start_sit") and pos == "K":
                    continue
                for comp in models.verdict_comparators(True):
                    cells.append(R.VerdictCell(form, pos, metric, comp, _delta(metric, next(statuses))))
    main = R.BootResult(points={
        "QB": {"chain_v1": {"coverage_80": 0.70}, "chain_v1+cal": {"coverage_80": 0.80}},
        "K": {"chain_v1": {"coverage_80": 0.86}, "chain_v1+cal": {"coverage_80": 0.87}},
    })
    acc = R.acceptance(cells, main, models, positions=("QB", "K"))
    text = R.render_verdict(cells, acc, models, positions=("QB", "K"))
    non_beat = [c for c in cells if c.status != R.STATUS_BETTER]
    sentences = R.verdict_sentences(cells, "chain_v1")
    # complete: every non-beat cell appears exactly once (one metric phrase per cell)
    phrases = sum(s.count(" [") for s in sentences)
    assert phrases == len(non_beat)
    for s in sentences:
        assert s in text
    assert "is worse on" in text and "does not beat it on" in text
    assert "| QB | 0.700 | 0.800 | PASS |" in text
    assert "| K | 0.860 | 0.870 | FAIL |" in text
    assert "outside it: K (0.870)" in text
    assert "MAE and pinball vs `rolling_avg_4`, QB: **FAIL**" in text
    assert "### Verdict grid, calibrated" in text and "### Verdict grid, raw" in text
    # all-beats grid renders the explicit empty list
    all_beat = [R.VerdictCell(c.form, c.position, c.metric, c.comparator, _delta(c.metric, "beats")) for c in cells]
    text2 = R.render_verdict(all_beat, R.acceptance(all_beat, main, models, ("QB", "K")), models, ("QB", "K"))
    assert "none: every cell of both grids is `beats`" in text2
    assert "MAE and pinball vs `rolling_avg_4`, QB: **PASS**" in text2


# --------------------------------------------------------------------------- #
# Cross-check and the end-to-end build
# --------------------------------------------------------------------------- #


def test_scoring_cross_check_classifies_emergency_kicker():
    harness = pl.DataFrame({
        "entity_id": ["a", "b", "c", "d"], "position": ["RB", "WR", "K", "QB"], "season": [2023] * 4,
        "week": [9] * 4, "actual": [3.0, 10.0, 7.0, 12.0], "active": [True] * 4,
    })
    reference = pl.DataFrame({
        "entity_id": ["a", "b", "c", "d"], "season": [2023] * 4, "week": [9] * 4,
        "actual": [0.0, 10.0, 7.0, 11.0], "active": [True] * 4, "kicking_points": [3.0, 0.0, 7.0, 0.0],
    })
    res = R.scoring_cross_check(harness, reference)
    assert res.rows == 4 and res.matched == 2
    assert res.explained == {"emergency_kicker": 1, "unexplained": 1}
    assert res.active_mismatch == 0 and res.missing_reference == 0


def _synthetic_consensus(seasons):
    base = truth(seasons=tuple(seasons))
    return (
        model_rows(base, R.CONSENSUS, 3.0, 9)
        .drop("pred_cdf_at_actual", "boom_actual", "bust_actual", "scoring")
        .with_columns(pl.col("pred_q50").rank("ordinal", descending=True).over(["position", "season", "week"])
                      .cast(pl.Float64).alias("rank_pred"))
        .filter(pl.col("entity_id").str.ends_with("1").not_())
    )


def _synthetic_reference(seasons):
    base = truth(seasons=tuple(seasons))
    # one emergency-kicker row: the harness scores a non-kicker's field goal, the reference does not
    return base.select("entity_id", "position", "season", "week", "actual", "active").with_columns(
        pl.when(pl.col("entity_id") == "RB00").then(pl.col("actual") - 3.0).otherwise(pl.col("actual")).alias("actual"),
        pl.when(pl.col("entity_id") == "RB00").then(3.0).otherwise(0.0).alias("kicking_points"),
    )


def test_build_report_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_cons_frame", _synthetic_consensus)
    monkeypatch.setattr(R, "_reference", _synthetic_reference)
    run = write_run(tmp_path)
    reports, data = tmp_path / "reports", tmp_path / "data"
    reports.mkdir()
    (reports / "evaluator_audit_2026-11-03_wave2.md").write_text(
        "# Evaluator audit\n\nOverall: **PASS**\n\n- Holdout audit: **PASS**\n\n## Holdout audit\n\n"
        "Overall: **PASS** (PASS 4, WARN 1).\n\n| check | status | detail |\n| --- | --- | --- |\n"
        "| literal | WARN | docstrings only |\n",
        encoding="utf-8",
    )
    kw = dict(n_boot=6, n_jobs=1, include_consensus=True, cross_check=True, reports_dir=reports,
              data_dir=data, positions=("QB", "WR", "K"))
    path = R.build_backtest_report(run, "backtest_test", **kw)
    text = path.read_text(encoding="utf-8")
    for heading in ("## 1. Setup", "## 3. Headline metrics", "## 4. By week", "## 5. Calibration",
                    "## 6. Secondary population", "## 7. Lineup efficiency", "## 9. Half-PPR and standard",
                    "## 10. Ablations", "## 11. Leakage and holdout audit", "## 12. Verdict"):
        assert heading in text
    assert "DRAFT" not in text
    assert "identical for every model" in text
    assert "Injury-information sensitivity" in text and "`chain_v1_final_report`" in text
    assert "## 8. Consensus baseline" in text and "Deltas `focus - consensus_ecr`" in text
    assert "vs consensus_ecr" in text  # the verdict grid has the consensus column
    assert "difference(s) equal the row's own kicking points" in text  # cross-check classified RB00
    assert "No unexplained differences." in text
    assert "Reliability of `boom_prob`" in text
    assert "- Holdout audit: **PASS** (PASS 4, WARN 1)." in text
    assert "  - WARN: literal: docstrings only" in text
    per_week = pl.read_csv(data / "backtest_test_per_week.csv")
    assert {"model", "position", "season", "week", "metric", "value"} <= set(per_week.columns)
    deltas = pl.read_csv(data / "backtest_test_deltas.csv")
    mae = deltas.filter((pl.col("population") == "headline") & (pl.col("metric") == "mae")
                        & (pl.col("position") == "WR") & (pl.col("other") == "rolling_avg_4"))
    assert mae.height == 1 and mae["delta"][0] < 0  # chain (noise 1) beats rolling_avg_4 (noise 4)
    # reproducible
    again = R.build_backtest_report(run, "backtest_test", **kw)
    assert again.read_text(encoding="utf-8") == text


def test_build_report_draft_without_chain(tmp_path):
    run = write_run(tmp_path, with_chain=False, scorings=("ppr",))
    path = R.build_backtest_report(run, "backtest_draft", n_boot=0, n_jobs=1, include_consensus=False,
                                   cross_check=False, reports_dir=tmp_path / "r", data_dir=tmp_path / "d",
                                   positions=("QB", "K"))
    text = path.read_text(encoding="utf-8")
    assert "DRAFT: the structural chain is not in this run yet" in text
    assert "Format check only" in text
    assert R.resolve_models({"chain_v1", "direct_lgbm"}).focus == "chain_v1"
    with pytest.raises(ValueError):
        R.resolve_models({"rolling_avg_4"})


def test_bootstrap_pads_missing_columns_and_consensus_population():
    base = truth()
    chain = model_rows(base, "chain_v1", 1.0, 5, probs=True)
    baseline = model_rows(base, "rolling_avg_4", 4.0, 1)  # no boom_prob column at all
    # a synthetic consensus frame: rank-based projection, no PIT, own (wrong) actuals that must be replaced
    cons = (
        model_rows(base, R.CONSENSUS, 3.0, 9)
        .drop("pred_cdf_at_actual", "boom_actual", "bust_actual", "scoring")
        .with_columns(pl.lit(-99.0).alias("actual"),
                      pl.col("pred_q50").rank("ordinal", descending=True).over(["position", "season", "week"])
                      .cast(pl.Float64).alias("rank_pred"))
        .filter(pl.col("entity_id").str.ends_with("1").not_())  # consensus covers fewer rows
    )
    aligned = M.with_reference_rank({"chain_v1": chain, "chain_v1+cal": chain.with_columns(
        pl.lit("chain_v1+cal").alias("model")), "rolling_avg_4": baseline})
    frames = R.consensus_population(cons, aligned, ["chain_v1", "rolling_avg_4"])
    assert frames[R.CONSENSUS].height == frames["chain_v1"].height < chain.height
    assert (frames[R.CONSENSUS]["actual"] >= 0).all()  # harness outcomes, not the consensus file's
    boot = R.run_bootstrap({m: frames[m] for m in ("chain_v1", "chain_v1+cal", R.CONSENSUS)}, ("WR", "K"),
                           lineup=False, n_boot=5, seed=0)
    assert not math.isnan(boot.value("WR", "chain_v1", "brier_boom"))
    assert math.isnan(boot.value("WR", R.CONSENSUS, "brier_boom"))
    d = boot.delta("WR", "chain_v1", R.CONSENSUS, "start_sit_common")
    assert not math.isnan(d.delta)
    mixed = R.run_bootstrap({"chain_v1": chain, "rolling_avg_4": baseline}, ("QB",), lineup=False, n_boot=3)
    assert math.isnan(mixed.value("QB", "rolling_avg_4", "brier_boom"))


def test_reference_excludes_final_report_variant():
    models = R.resolve_models({"chain_v1", "chain_v1_final_report", "direct_lgbm", "rolling_avg_4"})
    assert models.final_report == "chain_v1_final_report"
    ref = R.reference_models(models, {"chain_v1", "chain_v1_final_report", "direct_lgbm", "rolling_avg_4"})
    assert ref == ["chain_v1", "direct_lgbm", "rolling_avg_4"]
    assert "chain_v1_final_report" in models.comparators()
