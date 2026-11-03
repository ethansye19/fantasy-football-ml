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
    others = [c for c in cells if c.comparator != R.CONSENSUS]
    cons = [c for c in cells if c.comparator == R.CONSENSUS]
    # complete for the other comparators: one plain sentence per cell not beaten
    sentences = R.verdict_sentences(others, "The chain")
    assert len(sentences) == sum(1 for c in others if c.status != R.STATUS_BETTER)
    for s in sentences:
        assert s in text
    # complete for the consensus: every calibrated cell (beaten or not), then raw cells that differ
    cons_lines = [x for x in R.consensus_sentences(cells, "The chain") if x.startswith("- ")]
    cal = {(c.position, c.metric): c.status for c in cons if c.form == "calibrated"}
    differ = [c for c in cons if c.form == "raw" and cal[(c.position, c.metric)] != c.status]
    assert len(cons_lines) == len(cal) + len(differ)
    for s in cons_lines:
        assert s in text
    assert "The chain beats the FantasyPros consensus on" in text
    assert "The chain does not beat the FantasyPros consensus on" in text
    assert ": it is worse (" in text
    assert "| QB | 0.700 | FAIL | 0.800 | PASS |" in text
    assert "| K | 0.860 | FAIL | 0.870 | FAIL |" in text
    assert "- K: **FAIL**. The chain's calibrated 80% interval covers 0.870" in text
    assert "outside it: K (0.870)" in text
    assert "MAE and pinball vs `rolling_avg_4`, QB: **FAIL**" in text
    assert "### Verdict grid, calibrated" in text and "### Verdict grid, raw" in text
    # all-beats grid renders the explicit empty list for the other comparators
    all_beat = [R.VerdictCell(c.form, c.position, c.metric, c.comparator, _delta(c.metric, "beats")) for c in cells]
    text2 = R.render_verdict(all_beat, R.acceptance(all_beat, main, models, ("QB", "K")), models, ("QB", "K"))
    assert "none: every cell of both grids is `beats`" in text2
    assert "MAE and pinball vs `rolling_avg_4`, QB: **PASS**" in text2
    assert "every one of these cells has the same result" in text2


def test_d082_sentence_wording():
    d = R.Delta("mae", "chain_v1+cal", R.CONSENSUS, 4.2, 4.08, 0.12, -0.05, 0.29, R.STATUS_NONE)
    cell = R.VerdictCell("calibrated", "RB", "mae", R.CONSENSUS, d)
    assert R.cell_sentence(cell, "The chain") == (
        "The chain does not beat the FantasyPros consensus on RB MAE "
        "(difference +0.12, 95% interval -0.05 to +0.29; calibrated)."
    )
    worse = R.VerdictCell("raw", "QB", "pinball_q10", "prior_season",
                          R.Delta("pinball_q10", "chain_v1", "prior_season", 1.2, 1.1, 0.06, 0.02, 0.10, R.STATUS_WORSE))
    assert R.cell_sentence(worse, "The chain") == (
        "The chain does not beat the prior-season average (`prior_season`) on QB pinball loss at q10: "
        "it is worse (difference +0.06, 95% interval +0.02 to +0.10; raw forms)."
    )
    ss = R.VerdictCell("calibrated", "WR", "start_sit_common", R.CONSENSUS,
                       R.Delta("start_sit_common", "a", "b", 0.6, 0.59, 0.012, 0.004, 0.02, R.STATUS_BETTER))
    assert R.cell_sentence(ss, "The chain") == (
        "The chain beats the FantasyPros consensus on WR start/sit accuracy "
        "(difference +0.012, 95% interval +0.004 to +0.020; calibrated)."
    )


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


def _synthetic_exclusions(keys, seasons):
    # entities ending in 1 have no consensus row: week 1 no scrape, weeks 2-3 early games, later weeks no match
    reason = (
        pl.when(pl.col("week") == 1).then(pl.lit("no_scrape_week"))
        .when(pl.col("week").is_in([2, 3])).then(pl.lit("early_game"))
        .otherwise(pl.lit("no_ecr_match"))
    )
    return keys.select(list(M.KEY_COLUMNS)).with_columns(reason.alias("reason"))


def _fixed_git(*args):
    return "abc1234" if args and args[0] == "rev-parse" else ""


def test_report_text_depends_on_repository_state_only_through_git(tmp_path, monkeypatch):
    """The flake's cause: a HEAD change between two builds changes only the git line of the report."""
    monkeypatch.setattr(R, "_registry_rows", lambda focus: [])
    run = write_run(tmp_path, scorings=("ppr",))
    kw = dict(n_boot=0, n_jobs=1, include_consensus=False, cross_check=False, reports_dir=tmp_path / "r",
              data_dir=tmp_path / "d", positions=("QB",))
    monkeypatch.setattr(R, "_git", _fixed_git)
    first = R.build_backtest_report(run, "a", **kw).read_text(encoding="utf-8")
    monkeypatch.setattr(R, "_git", lambda *a: "def5678" if a and a[0] == "rev-parse" else " M ffml/eval/harness.py")
    second = R.build_backtest_report(run, "a", **kw).read_text(encoding="utf-8")
    diff = [(x, y) for x, y in zip(first.splitlines(), second.splitlines(), strict=True) if x != y]
    assert len(diff) == 1 and "git sha of this report" in diff[0][0]


def _synthetic_bounds(seasons):
    return pl.DataFrame({"position": list(POS_COUNTS), "pos_min": [-4.0] * 6, "pos_max": [60.0] * 6,
                         "season": [2022] * 6}, schema_overrides={"season": pl.Int32})


def test_build_report_end_to_end(tmp_path, monkeypatch):
    monkeypatch.setattr(R, "_cons_frame", _synthetic_consensus)
    monkeypatch.setattr(R, "_reference", _synthetic_reference)
    monkeypatch.setattr(R, "_cons_exclusions", _synthetic_exclusions)
    monkeypatch.setattr(R, "_bounds", _synthetic_bounds)
    # the report embeds HEAD and whether ffml/eval is dirty; other agents committing or editing ffml/eval
    # between the two builds below made the byte-for-byte reproducibility check flaky, so pin them
    monkeypatch.setattr(R, "_git", _fixed_git)
    monkeypatch.setattr(R, "_registry_rows", lambda focus: [["registry", "synthetic (not read)"]])
    run = write_run(tmp_path)
    reports, data = tmp_path / "reports", tmp_path / "data"
    reports.mkdir()
    (reports / "evaluator_audit_2026-11-03_wave2.md").write_text(
        "# Evaluator audit\n\nOverall: **PASS**\n\n- Holdout audit: **PASS**\n\n## Holdout audit\n\n"
        "Overall: **PASS** (PASS 4, WARN 1).\n\n| check | status | detail |\n| --- | --- | --- |\n"
        "| literal | WARN | docstrings only |\n",
        encoding="utf-8",
    )
    # a v1 report and an earlier-calibration file for the v2 sections (D-085, D-086)
    (reports / "backtest_v1.md").write_text(
        "# v1\n\n| item | value |\n| --- | --- |\n| predictions file sha256 | `abcdef0123456789...` (matches) |\n",
        encoding="utf-8")
    pred = pl.read_parquet(run / "predictions.parquet")
    before = pred.with_columns(
        pl.when(pl.col("model") == "chain_v1+cal").then((pl.col("boom_prob") * 0.5).clip(0.01, 0.99))
        .otherwise(pl.col("boom_prob")).alias("boom_prob"))
    before.write_parquet(run / "predictions_cal_v1_pooled_isotonic.parquet")
    kw = dict(n_boot=6, n_jobs=1, include_consensus=True, cross_check=True, reports_dir=reports,
              data_dir=data, positions=("QB", "WR", "K"), calibration_before="predictions_cal_v1_pooled_isotonic.parquet",
              previous_report="backtest_v1")
    path = R.build_backtest_report(run, "backtest_test", **kw)
    text = path.read_text(encoding="utf-8")
    for heading in ("## 1. Setup", "## 3. Headline metrics", "## 4. By week", "## 5. Calibration",
                    "## 6. Secondary population", "## 7. Lineup efficiency", "## 9. Half-PPR and standard",
                    "## 10. Ablations", "## 11. Leakage and holdout audit", "## 12. Verdict"):
        assert heading in text
    assert "DRAFT" not in text
    assert "identical for every model" in text
    assert "Injury-information sensitivity" in text and "`chain_v1_final_report`" in text
    assert "## 8. Against the FantasyPros consensus on identical player-weeks" in text
    assert "Differences `chain_v1 - consensus_ecr` on these identical player-weeks" in text
    assert "vs consensus_ecr" in text  # the verdict grid has the consensus column
    assert "difference(s) equal the row's own kicking points" in text  # cross-check classified RB00
    assert "No unexplained differences." in text
    assert text.index("Holdout session pending") < text.index("## 1. Setup")
    assert "Row keys identical for every model in this table: **yes**" in text
    assert "### 8.1 The Thursday effect" in text and "MISMATCH" not in text
    assert "on early-game player-weeks and" in text
    assert "Against the FantasyPros consensus: every position and metric" in text
    assert "### 5.1 Interval coverage detail" in text and "tie-aware (acceptance, D-085)" in text
    # v2: changes section, rule-change statement, the same rule for every model, boom/bust before and after
    assert text.index("## Changes from v1") < text.index("## 1. Setup")
    assert "(v1: `abcdef0123456789...`)" in text and "Quantile columns of `chain_v1+cal`" in text
    assert "unchanged on" in text  # only boom_prob differs in the before file
    assert "**Rule change (D-085).**" in text
    assert "changed after the 2022-2024 results were seen and before the" in text
    assert "`reports/backtest_2026-11-03.md`, which stays committed unchanged" in text
    assert "The same coverage rule applied to every calibrated model (tie-aware" in text
    assert "### 5.2 Boom and bust calibration per position, before and after (D-086)" in text
    assert "low confidence after" in text and "Reliability of `boom_prob` by position" in text
    assert (data / "backtest_test_boom_bust.csv").exists()
    assert "tie-aware coverage gap" in text
    assert "calibrated result (acceptance, D-085)" in text and "v1 rule result (superseded)" in text
    assert (data / "backtest_test_coverage.csv").exists()
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
    # a stand-in focus has no final-report sensitivity
    assert R.resolve_models({"chain_v1", "chain_v1_final_report", "direct_lgbm"}, focus="direct_lgbm").final_report is None


# --------------------------------------------------------------------------- #
# D-081: matched population and the Thursday split
# --------------------------------------------------------------------------- #


def test_matched_population_has_identical_keys_for_every_model():
    base = truth()
    frames = {
        "chain_v1": model_rows(base, "chain_v1", 1.0, 5, probs=True),
        "chain_v1+cal": model_rows(base, "chain_v1+cal", 1.0, 5, probs=True),
        "direct_lgbm": model_rows(base, "direct_lgbm", 2.0, 4),
        "rolling_median_8+cal": model_rows(base, "rolling_median_8+cal", 3.5, 3),
    }
    aligned = M.with_reference_rank(frames)
    cons = _synthetic_consensus((2022,))  # no entity ending in 1
    no_curve = (pl.col("entity_id") == "WR02") & (pl.col("week") == 2)  # a consensus row without a projection
    cons = cons.with_columns([pl.when(no_curve).then(None).otherwise(pl.col(c)).alias(c)
                              for c in ("pred_q10", "pred_q50", "pred_q90", "pred_mean")])
    joint = R.consensus_population(cons, aligned, ["chain_v1", "direct_lgbm"])
    assert set(joint) == {*frames, R.CONSENSUS}
    assert R.population_keys_identical(joint)
    keys = [R.population_keys(f) for f in joint.values()]
    assert all(k.equals(keys[0]) for k in keys)
    k0 = keys[0]
    assert k0.filter(pl.col("entity_id").str.ends_with("1")).is_empty()
    assert k0.filter((pl.col("entity_id") == "WR02") & (pl.col("week") == 2)).is_empty()
    expected = base.filter(pl.col("active") & ~pl.col("entity_id").str.ends_with("1")
                           & ~((pl.col("entity_id") == "WR02") & (pl.col("week") == 2))).height
    assert k0.height == expected
    # every model is scored against the same actuals (the consensus file's own are replaced)
    acts = [M.population(M.validate_frame(R._slim(f))).sort(list(M.KEY_COLUMNS))["actual"] for f in joint.values()]
    assert all(a.equals(acts[0]) for a in acts)
    broken = {**joint, "x": joint["chain_v1"].head(10)}
    assert not R.population_keys_identical(broken)


ECR_SCHEMA = {
    "season": pl.Int32, "week": pl.Int32, "game_type": pl.String, "scrape_date": pl.Date, "fantasypros_id": pl.String,
    "player_id": pl.String, "team": pl.String, "ecr": pl.Float64, "sd": pl.Float64, "valid_for_game": pl.Boolean,
    "fp_page": pl.String,
}


def _ecr(week, page, fpid, player_id, team, ecr, scrape, game):
    return {"season": 2023, "week": week, "game_type": "REG", "scrape_date": scrape, "fantasypros_id": fpid,
            "player_id": player_id, "team": team, "ecr": float(ecr), "sd": 1.0,
            "valid_for_game": scrape < game, "fp_page": page}


def test_thursday_split_counts():
    from datetime import date

    from ffml.eval import ecr_baseline as eb

    fri, sun, thu = date(2023, 10, 6), date(2023, 10, 8), date(2023, 10, 5)
    ecr = pl.DataFrame([
        _ecr(5, "ppr-wr", "1", "A", "AAA", 1, fri, sun),            # valid
        _ecr(5, "ppr-wr", "2", "B", "BBB", 2, fri, thu),            # Thursday game: early
        _ecr(5, "ppr-wr", "5", "E", "CCC", 3, fri, sun),            # an RB listed on the WR page only
        _ecr(5, "ppr-rb", "6", "F", "CCC", 1, fri, sun),            # valid, but no projection later
        _ecr(5, "dst", "7", None, "GGG", 1, fri, thu),              # DST keyed by team, early
        _ecr(5, "qb", "8", "H", "HHH", 1, date(2023, 10, 4), thu),  # an earlier scrape is valid
        _ecr(5, "qb", "8", "H", "HHH", 1, fri, thu),
    ], schema=ECR_SCHEMA)
    keys = pl.DataFrame({
        "entity_id": ["A", "B", "C", "D", "E", "F", "GGG", "H"],
        "position": ["WR", "WR", "WR", "WR", "RB", "RB", "DST", "QB"],
        "season": [2023] * 8, "week": [5, 5, 5, 6, 5, 5, 5, 5],
    }, schema_overrides={"season": pl.Int32, "week": pl.Int32})
    reasons = dict(eb.exclusion_reasons(ecr, keys).select("entity_id", "reason").iter_rows())
    assert reasons == {"A": "valid", "B": "early_game", "C": "no_ecr_match", "D": "no_scrape_week",
                       "E": "no_ecr_match", "F": "valid", "GGG": "early_game", "H": "valid"}
    matched = keys.filter(pl.col("entity_id").is_in(["A", "H"]))
    status = R.consensus_status(keys, matched, eb.exclusion_reasons(ecr, keys))
    by_key = dict(status.select("entity_id", "status").iter_rows())
    assert by_key == {"A": "matched", "B": "early_game", "C": "no_ecr_match", "D": "no_scrape_week",
                      "E": "no_ecr_match", "F": "valid_no_projection", "GGG": "early_game", "H": "matched"}
    counts = {r["position"]: r for r in R.status_counts(status).iter_rows(named=True)}
    assert counts["ALL"] == {"position": "ALL", "matched": 2, "early_game": 2, "no_ecr_match": 2,
                             "no_scrape_week": 1, "valid_no_projection": 1, "all": 8}
    assert counts["WR"]["matched"] == counts["WR"]["early_game"] == counts["WR"]["no_scrape_week"] == 1
    assert sum(counts["RB"][s] for s in R.STATUS_ORDER) == counts["RB"]["all"] == 2
    # the populations hold exactly those keys
    frame = keys.with_columns(pl.lit("m").alias("model"), pl.lit(1.0).alias("pred_q50"))
    pops = R.split_populations({"m": frame}, status)
    assert sorted(pops[R.POP_EARLY]["m"]["entity_id"].to_list()) == ["B", "GGG"]
    assert sorted(pops[R.POP_MATCHED]["m"]["entity_id"].to_list()) == ["A", "H"]
    assert pops[R.POP_ALL]["m"].height == 8


def test_split_mae_deltas_and_sentence():
    base = truth(weeks=tuple(range(1, 9)))
    frames = M.with_reference_rank({"chain_v1+cal": model_rows(base, "chain_v1+cal", 1.0, 5),
                                    "rolling_median_8+cal": model_rows(base, "rolling_median_8+cal", 4.0, 3)})
    keys = R.population_keys(frames["chain_v1+cal"])
    status = keys.with_columns(
        pl.when(pl.col("entity_id").str.ends_with("0")).then(pl.lit(R.STATUS_EARLY))
        .otherwise(pl.lit(R.STATUS_MATCHED)).alias("status"))
    pops = R.split_populations(frames, status)
    split = R.split_mae_deltas(pops, "chain_v1+cal", "rolling_median_8+cal", ("WR",), n_boot=30)
    groups = {s.group: s for s in split}
    assert set(groups) == {"WR", "ALL"}
    for pop in (R.POP_MATCHED, R.POP_EARLY, R.POP_ALL):
        d, lo, hi = groups["ALL"].deltas[pop]
        assert d < 0 and lo <= d <= hi
    text = R.thursday_sentence(groups["ALL"], "The chain", "rolling_median_8+cal")
    assert "on early-game player-weeks" in text and "The difference between the two is" in text


# --------------------------------------------------------------------------- #
# Coverage detail: tails, ties, randomized PIT
# --------------------------------------------------------------------------- #


def test_tail_decomposition_and_tie_aware_coverage():
    # a point mass at 0: q05 = q10 = 0 for every row; half the actuals are exactly 0
    n = 400
    rng = np.random.default_rng(1)
    y = np.where(np.arange(n) % 2 == 0, 0.0, rng.uniform(0.5, 9.5, n))
    frame = pl.DataFrame({
        "entity_id": [f"RB{i:03d}" for i in range(n)], "position": ["RB"] * n, "season": [2022] * n,
        "week": [1 + i % 4 for i in range(n)], "model": ["m"] * n, "actual": y, "active": [True] * n,
        "pred_mean": [4.0] * n, "pred_q05": [0.0] * n, "pred_q10": [0.0] * n, "pred_q25": [2.0] * n,
        "pred_q50": [4.0] * n, "pred_q75": [6.0] * n, "pred_q90": [8.0] * n, "pred_q95": [9.0] * n,
    })
    tails = M.tail_decomposition(M.validate_frame(frame))
    assert tails["tied_q10"] == pytest.approx(0.5) and tails["below_q10"] == 0.0
    assert sum(tails[k] for k in ("below_q10", "tied_q10", "inside", "tied_q90", "above_q90")) == pytest.approx(1.0)
    closed = M.interval_coverage(M.validate_frame(frame))
    assert closed == pytest.approx(tails["tied_q10"] + tails["inside"] + tails["tied_q90"])
    # F(0-) = 0.05 (lower bound below 0) and F(0) = 0.10, so a tied row's randomized PIT is never in (0.1, 0.9)
    left, right = M.cdf_limits(np.array([0.0]), np.array([[0, 0, 2, 4, 6, 8, 9]], dtype=float),
                               np.array([-2.0]), np.array([30.0]))
    assert left[0] == pytest.approx(0.05) and right[0] == pytest.approx(0.10)
    bounds = pl.DataFrame({"position": ["RB"], "pos_min": [-2.0], "pos_max": [30.0], "season": [2022]},
                          schema_overrides={"season": pl.Int32})
    tie_aware = R.tie_aware_coverage(frame, bounds, seed=0)
    assert tie_aware < closed - 0.4  # the ties no longer count as covered
    assert tie_aware == R.tie_aware_coverage(frame, bounds, seed=0)  # seeded
    # without ties the diagnostic is close to the closed coverage
    smooth = frame.with_columns(pl.Series("actual", rng.uniform(0.5, 9.5, n)))
    assert abs(R.tie_aware_coverage(smooth, bounds) - M.interval_coverage(M.validate_frame(smooth))) < 0.06


def _knot_frame(y, n_weeks=4):
    n = len(y)
    return pl.DataFrame({
        "entity_id": [f"WR{i:03d}" for i in range(n)], "position": ["WR"] * n, "season": [2022] * n,
        "week": [1 + i % n_weeks for i in range(n)], "model": ["m"] * n, "actual": np.asarray(y, dtype=float),
        "active": [True] * n, "pred_mean": [4.0] * n, "pred_q05": [0.0] * n, "pred_q10": [0.0] * n,
        "pred_q25": [2.0] * n, "pred_q50": [4.0] * n, "pred_q75": [6.0] * n, "pred_q90": [8.0] * n,
        "pred_q95": [9.0] * n,
    })


def test_tie_aware_coverage_is_seeded_and_equals_closed_without_ties():
    rng = np.random.default_rng(3)
    bounds = pl.DataFrame({"position": ["WR"], "pos_min": [-2.0], "pos_max": [40.0], "season": [2022]},
                          schema_overrides={"season": pl.Int32})
    # no actual equals a knot: tie-aware coverage is exactly the closed-interval coverage
    smooth = _knot_frame(rng.uniform(0.25, 12.0, 500))
    assert R.tie_aware_coverage(smooth, bounds) == M.interval_coverage(M.validate_frame(smooth))
    # with ties: deterministic for seed 0, and the per-row column is reused by every caller
    tied = _knot_frame(np.where(np.arange(500) % 3 == 0, 0.0, rng.uniform(0.25, 12.0, 500)))
    a = R.with_randomized_pit(tied, bounds, seed=0)
    b = R.with_randomized_pit(tied.sample(fraction=1.0, shuffle=True, seed=9), bounds, seed=0)
    key = ["entity_id", "season", "week"]
    assert a.sort(key)[M.RAND_PIT].equals(b.sort(key)[M.RAND_PIT])  # row order does not matter
    assert R.tie_aware_coverage(a) == R.tie_aware_coverage(tied, bounds, seed=0)
    assert not a[M.RAND_PIT].equals(R.with_randomized_pit(tied, bounds, seed=1)[M.RAND_PIT])  # the seed matters
    assert R.tie_aware_coverage(a) < M.interval_coverage(M.validate_frame(tied)) - 0.25
    # the bundle reads the same column (and the inactive rows get no draw)
    bundle = M.metric_bundle(a.columns)(M.validate_frame(R._slim(a)))
    assert bundle["coverage_tie_aware"] == R.tie_aware_coverage(a)
    assert bundle["coverage_gap_tie_aware"] == pytest.approx(abs(bundle["coverage_tie_aware"] - 0.8))
    inactive = R.with_randomized_pit(tied.with_columns(pl.lit(False).alias("active")), bounds)
    assert inactive[M.RAND_PIT].null_count() == inactive.height
    # a model with only q10/q50/q90 (the consensus) gets the same rule from its own knots
    three = smooth.drop("pred_q05", "pred_q25", "pred_q75", "pred_q95")
    assert R.knot_columns(three) == ([0.1, 0.5, 0.9], ["pred_q10", "pred_q50", "pred_q90"])
    assert R.tie_aware_coverage(three, bounds) == M.interval_coverage(M.validate_frame(three))


def test_low_confidence_flag_rule():
    assert not R.low_confidence(1.0, 1.3)
    assert not R.low_confidence(0.8, 0.95) and not R.low_confidence(1.2, 1.5)  # band edges are inside
    assert R.low_confidence(0.79, 0.95) and R.low_confidence(1.21, 1.4)  # outside [0.8, 1.2]
    assert R.low_confidence(0.85, 0.79)  # interval wholly below 0.8
    assert R.low_confidence(math.nan, math.nan)  # cannot be estimated
    assert not R.low_confidence(0.9, math.nan)  # no interval: the point rule decides


def test_boom_bust_calibration_before_after():
    base = truth(weeks=tuple(range(1, 9)))
    after = model_rows(base, "chain_v1+cal", 1.0, 5, probs=True)
    rng = np.random.default_rng(4)
    # calibrated probabilities: draw outcomes from them; "before" squashes them towards 0.5 (under-confident)
    p = rng.uniform(0.02, 0.98, after.height)
    y = (rng.random(after.height) < p).astype(float)
    after = after.with_columns(pl.Series("boom_prob", p), pl.Series("boom_actual", y),
                               pl.Series("bust_prob", 1 - p), pl.Series("bust_actual", 1 - y))
    before = after.with_columns((0.5 + (pl.col("boom_prob") - 0.5) * 0.3).alias("boom_prob"))
    stats, rel = R.boom_bust_calibration(before, after, ("WR", "RB"), n_boot=40)
    s = {(r["position"], r["kind"], r["version"]): r for r in stats.iter_rows(named=True)}
    assert s[("WR", "boom", "after")]["low_confidence"] is False
    assert 0.8 <= s[("WR", "boom", "after")]["slope"] <= 1.2
    assert s[("WR", "boom", "before")]["slope"] > 1.2 and s[("WR", "boom", "before")]["low_confidence"] is True
    r = s[("WR", "boom", "after")]
    assert r["slope_lo"] <= r["slope"] <= r["slope_hi"] and abs(r["intercept"]) < 0.5
    assert set(rel["version"].unique()) == {"before", "after"} and set(rel["kind"].unique()) == {"boom", "bust"}


def test_coverage_paragraph_is_neutral_and_names_failing_positions():
    models = R.ModelSet("chain_v1", None, "direct_lgbm", ["rolling_median_8"])
    detail = pl.DataFrame([{"model": "chain_v1+cal", "position": "RB", "n": 100, "coverage_closed": 0.863,
                            "below_q10": 0.053, "tied_q10": 0.156, "inside": 0.70, "tied_q90": 0.007,
                            "above_q90": 0.084, "coverage_tie_aware": 0.79}])
    text = R.coverage_paragraph(detail, [("RB", 0.88, 0.863, False), ("QB", 0.8, 0.8, True)], models)
    assert "15.6% tie exactly at q10" in text and "0.790" in text
    assert "(1) keep the rule as written" in text and "the chain fails coverage acceptance at RB" in text
    assert "(2) adopt a tie-aware coverage definition" in text and "(3) change the calibrator" in text
    assert "does not recommend" in text
    assert R.coverage_paragraph(detail, [("RB", 0.8, 0.8, True)], models) == ""
