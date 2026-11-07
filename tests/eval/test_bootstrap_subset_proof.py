"""D-116 B2 / A6: bootstrapping only the frames the 22 lines read equals the full computation.

By construction: ``run_bootstrap`` draws ``default_rng(seed).integers(0, K, (n_boot, K))`` per position from
the cluster universe of the frames it is given and computes each frame's metrics from that frame alone; the
aligned frames share their keys, so the universe, the draw matrix and every replicate of the line frames are
the same with or without the other frames (``livetest_score.line_frames`` checks the universes and falls back
to the full computation otherwise).

Demonstrated here by exact comparison (``==`` on floats, ``np.array_equal`` on replicate arrays) of every
replicate array and point value behind the lines, every interval, every line result and the verdict:

* always: the unit-test fixture weeks at 40 draws, and the fall-back when the universes differ;
* with ``FFML_SUBSET_PROOF=1``: QA's rehearsal worlds (``tests/rehearsal/_rehearsal_world.py``: the PASS world A,
  the FAIL worlds B, QB spread 20% too narrow, and C, ``prior_season+cal`` tied with the chain at WR), 18 weeks,
  at 500 draws (the binding rule) and 5,000 draws (the sensitivity check). Evidence and timings go to
  ``data/reports/livetest_subset_proof.json``.
"""

from __future__ import annotations

import json
import math
import os
import sys
import time
from pathlib import Path

import numpy as np
import polars as pl
import pytest
from test_livetest_score import _baselines, _consensus, _rows, fake_outcomes  # sibling test module

from ffml.eval import livetest_score as LS
from ffml.eval import report_backtest as RB
from ffml.eval import report_holdout as H

LINE_METRICS = (*H.BEATS_METRICS, "coverage_tie_aware")


def compare(full: LS.Scored, sub: LS.Scored) -> list[str]:
    """Every difference between the full and the line-frames-only computation that could reach a line."""
    diffs: list[str] = []
    if full.bootstrap_frames != "full" or sub.bootstrap_frames != "lines":
        diffs.append(f"bootstrap frames {full.bootstrap_frames} / {sub.bootstrap_frames}")
    for pos in LS.POSITIONS:
        for label in LS.LINE_FRAME_LABELS:
            if not full.main.has(pos, label):
                continue
            if not sub.main.has(pos, label):
                diffs.append(f"{pos} {label} missing from the subset")
                continue
            for m in LINE_METRICS:
                a, b = full.main.value(pos, label, m), sub.main.value(pos, label, m)
                if not (a == b or (math.isnan(a) and math.isnan(b))):
                    diffs.append(f"point {pos} {label} {m}: {a!r} != {b!r}")
                ra = full.main.boots.get(pos, {}).get(label, {}).get(m)
                rb = sub.main.boots.get(pos, {}).get(label, {}).get(m)
                if (ra is None) != (rb is None) or (ra is not None and not np.array_equal(ra, rb, equal_nan=True)):
                    diffs.append(f"replicates {pos} {label} {m} differ")
    f = LS.FOCUS + RB.CAL
    for line in H.prereg_lines():
        if line.kind == "beats_baseline":
            for m in H.BEATS_METRICS:
                da = full.main.delta(line.position, f, line.comparator + RB.CAL, m)
                db = sub.main.delta(line.position, f, line.comparator + RB.CAL, m)
                if (da.delta, da.lo, da.hi, da.status) != (db.delta, db.lo, db.hi, db.status) and not all(
                        (math.isnan(x) and math.isnan(y)) or x == y for x, y in
                        zip((da.delta, da.lo, da.hi), (db.delta, db.lo, db.hi), strict=True)):
                    diffs.append(f"{line.id} {m} interval {da} != {db}")
        elif line.kind == "startsit_consensus" and full.cons is not None:
            da = full.cons.delta(line.position, f, RB.CONSENSUS, "start_sit_common")
            db = sub.cons.delta(line.position, f, RB.CONSENSUS, "start_sit_common")
            if not all((math.isnan(x) and math.isnan(y)) or x == y
                       for x, y in zip((da.delta, da.lo, da.hi), (db.delta, db.lo, db.hi), strict=True)):
                diffs.append(f"{line.id} interval {da} != {db}")
        if LS.line_intervals(full, line) != LS.line_intervals(sub, line):
            diffs.append(f"{line.id} reported interval text differs")
    ra = [(r.line.id, r.passed, r.detail) for r in full.results]
    rb = [(r.line.id, r.passed, r.detail) for r in sub.results]
    if ra != rb:
        diffs.append("line results differ: " + str([x for x, y in zip(ra, rb, strict=True) if x != y][:3]))
    if H.verdict_text([], full.results) != H.verdict_text([], sub.results):
        diffs.append("verdict differs")
    return diffs


def _fixture_window():
    frames, cons = [], []
    for w in (10, 11, 12):
        cal = _rows(2026, w, "chain_v2+cal", 1).with_columns(pl.lit("walk_forward:abc").alias("calibration_version"))
        raw = _rows(2026, w, "chain_v2", 1)
        frames += [fake_outcomes(pl.concat([raw, cal], how="diagonal_relaxed"), 2026), _baselines(2026, w)]
        cons.append(_consensus(2026, w))
    return pl.concat(frames, how="diagonal_relaxed"), pl.concat(cons)


def test_subset_identical_on_fixture_weeks():
    frame, cons = _fixture_window()
    full = LS.score_frames(frame, cons, n_boot=40, subset=False)
    sub = LS.score_frames(frame, cons, n_boot=40, subset=True)
    assert compare(full, sub) == []
    assert set(sub.main.points["QB"]) == set(LS.LINE_FRAME_LABELS)  # the other frames were left out
    assert len(full.main.points["QB"]) > len(sub.main.points["QB"])


def test_line_frames_falls_back_when_universes_differ():
    frame, _ = _fixture_window()
    aligned = {m: frame.filter((pl.col("model") == m) & (pl.col("scoring") == "ppr"))
               for m in frame["model"].unique().to_list()}
    sub, used = LS.line_frames(aligned, LS.POSITIONS)
    assert used == "lines" and set(sub) == set(LS.LINE_FRAME_LABELS)
    # a frame outside the line set with an extra week changes the full universe: no subset
    extra = aligned["rolling_avg_4"].filter(pl.col("week") == 12).with_columns(pl.lit(13).cast(pl.Int32).alias("week"))
    aligned["rolling_avg_4"] = pl.concat([aligned["rolling_avg_4"], extra])
    sub, used = LS.line_frames(aligned, LS.POSITIONS)
    assert used == "full" and len(sub) == len(aligned)


# --------------------------------------------------------------------------- #
# The proof on QA's rehearsal worlds (FFML_SUBSET_PROOF=1)
# --------------------------------------------------------------------------- #

PROOF = os.environ.get("FFML_SUBSET_PROOF") == "1"
EVIDENCE = Path(__file__).resolve().parents[2] / "data" / "reports" / "livetest_subset_proof.json"
CASES = [("A_pass", 500), ("B_qb_narrow", 500), ("C_accuracy_tie", 500), ("A_pass", 5000), ("C_accuracy_tie", 5000)]


def _rehearsal():
    sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "rehearsal"))
    import _rehearsal_world as W

    return W


def rehearsal_world(name: str):
    """The rehearsal world's 18 window weeks as the scorer's sealed frames (chain, baselines, consensus), PPR.

    Outcomes are the world's true points (the PPR scoring of its stat lines); PIT bounds are the position
    extremes of the world's history weeks; the consensus follows the world's own consensus noise and half
    the matchup effect, as the rehearsal's ranks do.
    """
    W = _rehearsal()
    spec = {"A_pass": W.WorldSpec("A_pass"),
            "B_qb_narrow": W.WorldSpec("B_qb_narrow", qb_sigma_factor=0.8),
            "C_accuracy_tie": W.WorldSpec("C_accuracy_tie", tie_baseline=("prior_season", "WR"))}[name]
    truth = W.truth_frame(spec)
    window, cal, raw = W.chain_frames(spec, truth)
    base = W.baseline_frames(spec, truth, cal)
    in_window = pl.struct("season", "week").map_elements(lambda r: (r["season"], r["week"]) in set(W.WINDOW),
                                                         return_dtype=pl.Boolean)
    hist = truth.filter(~in_window)
    bounds = hist.group_by("position").agg(pl.col("points").min().alias("pos_min"),
                                           pl.col("points").max().alias("pos_max"))
    outcomes = window.select("entity_id", "season", "week", "position", pl.col("points").alias("actual"),
                             pl.lit(True).alias("active")).join(bounds, on="position").drop("position")
    preds = pl.concat([LS.relabel(raw, LS.FOCUS), LS.relabel(cal, LS.FOCUS + RB.CAL), base], how="diagonal_relaxed")
    frame = preds.filter(pl.col("scoring") == "ppr").join(outcomes, on=["entity_id", "season", "week"], how="left")
    c50 = (np.log(window["skill"].to_numpy()) + 0.5 * np.log(window["matchup"].to_numpy())
           + window["noise_consensus"].to_numpy())
    cons = window.select("entity_id", "position", "team", "game_id", "season", "week").with_columns(
        pl.lit(RB.CONSENSUS).alias("model"), pl.Series("pred_q50", np.exp(c50)), pl.Series("pred_mean", np.exp(c50)),
        pl.Series("pred_q10", 0.55 * np.exp(c50)), pl.Series("pred_q90", 1.7 * np.exp(c50)),
        pl.lit(0.0).alias("actual"), pl.lit(True).alias("active"))
    cons = cons.with_columns(pl.col("pred_q50").rank("ordinal", descending=True)
                             .over(["position", "season", "week"]).cast(pl.Float64).alias("rank_pred"))
    return frame, cons


@pytest.mark.skipif(not PROOF, reason="set FFML_SUBSET_PROOF=1 for the rehearsal-world proof (about an hour)")
@pytest.mark.parametrize(("world", "n_boot"), CASES)
def test_subset_identical_on_rehearsal_worlds(world, n_boot):
    frame, cons = rehearsal_world(world)
    t, c = time.perf_counter(), time.process_time()
    full = LS.score_frames(frame, cons, n_boot=n_boot, subset=False)
    t_full, c_full = time.perf_counter() - t, time.process_time() - c
    t, c = time.perf_counter(), time.process_time()
    sub = LS.score_frames(frame, cons, n_boot=n_boot, subset=True)
    t_sub, c_sub = time.perf_counter() - t, time.process_time() - c
    diffs = compare(full, sub)
    record = {"world": world, "n_boot": n_boot, "weeks": len(full.weeks), "rows": frame.height,
              "labels_full": len(full.main.points.get("QB", {})), "labels_subset": len(sub.main.points.get("QB", {})),
              "minutes_full": t_full / 60, "minutes_subset": t_sub / 60,
              "cpu_minutes_full": c_full / 60, "cpu_minutes_subset": c_sub / 60, "differences": diffs,
              "verdict_lines": [(r.line.id, r.passed) for r in full.results],
              "lines_passed": sum(r.passed for r in full.results),
              "verdict_if_all_checks_pass": H.verdict_text([], full.results)}
    EVIDENCE.parent.mkdir(parents=True, exist_ok=True)
    data = json.loads(EVIDENCE.read_text(encoding="utf-8")) if EVIDENCE.is_file() else {}
    data[f"{world}@{n_boot}"] = record
    EVIDENCE.write_text(json.dumps(data, indent=1), encoding="utf-8")
    assert diffs == []
    if world == "A_pass":
        assert record["verdict_if_all_checks_pass"] == "Verdict: APPROVED"
    else:
        assert record["verdict_if_all_checks_pass"] == "Verdict: NOT APPROVED"
