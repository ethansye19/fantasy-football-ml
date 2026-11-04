"""Pass probability of the 22 pre-registered lines in a live 2026 test (D-098 item 3).

The owner asked for "the chance that a well-calibrated model would pass all
22 rules over 2026 weeks 10-18, accounting for the number of games and
within-week correlation". This module estimates it by simulation from the
validated 2022-2024 rows of ``chain_v1+cal`` (``default_20261102``), which is
the chain_v2 model's calibration method on 2022-2024 (D-098 item 2b).

Method
======

One replicate of a test window (for example weeks 10-18):

1. For each week number ``w`` one validation slate is drawn uniformly from
   2022, 2023 and 2024 week ``w``. Whole slates keep the correlation within a
   week and within a game.
2. A slate with more games than the 2026 week is thinned by dropping whole
   games at random (every row of both teams, both DST rows, and therefore
   every start/sit pair involving them). A slate with fewer games is kept as
   is and counted.
3. The 22 lines are evaluated exactly as ``ffml/eval/report_holdout.py``
   would on that window relabelled as season 2026: 500 week-clustered paired
   bootstrap draws with seed 0 (D-078), calibrated forms, the D-077 common
   pair set re-ranked on the thinned slate, and tie-aware coverage in
   ``[0.75, 0.85]`` (D-085).

Fast path
=========

Every statistic behind the lines is a ratio of sums over rows (MAE, pinball
losses, coverage) or over pairs (start/sit), and the paired cluster bootstrap
resamples whole weeks. So per game (and per week) we keep sums and counts,
and a bootstrap replicate of a window is ``(C @ sums) / (C @ counts)`` where
``C[b, j]`` counts how often draw ``b`` picked week ``j``. ``C`` depends only
on the number of clusters and the seed, exactly as
:func:`ffml.eval.metrics._paired_bundle_bootstrap` draws them. The start/sit
pair set is recomputed for a thinned slate (the reference order is fixed per
row, so re-ranking is "the first N kept rows in reference order"), and the
seed-0 randomized PIT of scenario B is redrawn over the window's rows in the
live sort order. :func:`real_window_lines` evaluates the same window with the
real functions (``run_bootstrap``, ``with_randomized_pit``,
``with_reference_rank``, ``evaluate_lines``) so the two paths can be compared
(``tests/eval/test_power.py`` and the report's equivalence section).

Scenarios
=========

* ``A`` well calibrated: within each position each row's randomized PIT ``u``
  is replaced by its rank score ``(rank - 0.5) / n`` over all 2022-2024 rows
  of that position, so coverage is exactly 0.80 in expectation while the
  dependence between rows is kept. Accuracy and start/sit as validated.
* ``B`` as validated: the rows unchanged; the PIT is redrawn with seed 0 over
  the window as the live test will draw it.
* ``C`` accuracy sensitivity, on top of A. ``scale`` multiplies every per-row
  chain-minus-comparator difference by ``k``; the sign rule is invariant to a
  positive scale, so this leaves every L01-L12 verdict unchanged (shown, not
  assumed). ``shift`` moves every per-row difference by ``(1 - k)`` times its
  2022-2024 mean, so the expected edge is ``k`` times the validated one while
  the week-to-week noise is kept.

Only aggregate numbers leave this module (D-007); the consensus rows stay in
memory.
"""

from __future__ import annotations

import argparse
import json
import math
import sys
import time
from collections.abc import Mapping, Sequence
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import numpy as np
import polars as pl

from ffml.config import PROJECT_ROOT, ensure_utf8
from ffml.eval import metrics as M
from ffml.eval import report_backtest as RB
from ffml.eval import report_holdout as H

FOCUS = "chain_v1"
FOCUS_CAL = FOCUS + RB.CAL
COMPARATORS: tuple[str, ...] = tuple(b + RB.CAL for b in H.LINE_BASELINES)
MAIN_LABELS: tuple[str, ...] = (FOCUS_CAL, *COMPARATORS)
SS_LABELS: tuple[str, ...] = (FOCUS_CAL, RB.CONSENSUS)
POSITIONS: tuple[str, ...] = H.COVERAGE_POSITIONS
ACC_POSITIONS: tuple[str, ...] = RB.ACCEPT_POSITIONS
METRICS: tuple[str, ...] = H.BEATS_METRICS
POOL_SEASONS: tuple[int, ...] = (2022, 2023, 2024)
LIVE_SEASON = 2026
N_BOOT = H.SPEC_N_BOOT
BOOT_SEED = H.SPEC_SEED
METRIC_SEED = H.SPEC_METRIC_SEED
ALPHA = 0.05
BAND: tuple[float, float] = RB.COVERAGE_BAND
#: 2026 regular-season game counts (given in the brief; no 2026 data is read)
GAMES_2026: dict[int, int] = {10: 14, 11: 13, 12: 16, 13: 14, 14: 15, 15: 16, 16: 16, 17: 16, 18: 16}
#: simulation seed for slate and game draws (bootstrap and PIT seeds are the pre-registered ones)
SIM_SEED = 20261104
#: coverage levels for the coverage power curve (scenario A with the interval (a, 1 - a), a = (1 - c) / 2)
COVERAGE_LEVELS: tuple[float, ...] = (0.72, 0.74, 0.76, 0.78, 0.80, 0.82, 0.84, 0.86)
#: tie-aware coverage counts a PIT strictly inside (0.1, 0.9) (D-085)
BAND_PIT: tuple[float, float] = (0.1, 0.9)
#: the planned test weeks; scenario A_w and the coverage-level curve are calibrated on them
TEST_WEEKS: tuple[int, ...] = tuple(range(10, 19))
LINES: tuple[H.PreregLine, ...] = H.prereg_lines()
LINE_IDS: tuple[str, ...] = tuple(line.id for line in LINES)


@dataclass(frozen=True)
class Window:
    key: str
    label: str
    weeks: tuple[int, ...]
    games: tuple[int | None, ...]  # target game count per week; None keeps the full slate


WINDOWS: dict[str, Window] = {
    "i": Window("i", "weeks 10-18 (the plan)", tuple(range(10, 19)), tuple(GAMES_2026[w] for w in range(10, 19))),
    "ii": Window("ii", "weeks 11-18 (start slips a week)", tuple(range(11, 19)),
                 tuple(GAMES_2026[w] for w in range(11, 19))),
    "iii": Window("iii", "weeks 1-18, full slates (reference)", tuple(range(1, 19)), tuple([None] * 18)),
}


@dataclass(frozen=True)
class Scenario:
    key: str
    label: str
    coverage: str = "A"  # "A" rank-calibrated PIT, "B" seed-0 redraw over the window, "B_fixed" validation draws
    acc_mode: str = "none"  # "none", "scale" or "shift" (L01-L12 only)
    k: float = 1.0
    stretch: float = 1.0  # > 1: slate deviations from their week-number mean are stretched (variance correction)


SCENARIOS: tuple[Scenario, ...] = (
    Scenario("A", "A: well calibrated (rank-calibrated PIT), accuracy as validated", "A"),
    Scenario("B", "B: as validated (chain_v1+cal unchanged, PIT redrawn with seed 0)", "B"),
    Scenario("C_shift_0.75", "C: A with the chain's mean edge on L01-L12 shrunk to 0.75x (noise kept)", "A", "shift", 0.75),
    Scenario("C_shift_0.5", "C: A with the chain's mean edge on L01-L12 shrunk to 0.5x (noise kept)", "A", "shift", 0.5),
    Scenario("C_scale_0.75", "C literal: A with per-row differences scaled by 0.75", "A", "scale", 0.75),
    Scenario("C_scale_0.5", "C literal: A with per-row differences scaled by 0.5", "A", "scale", 0.5),
    Scenario("B_fixed", "B sensitivity: validation PIT draws kept (not redrawn)", "B_fixed"),
    Scenario("A_vc", "A sensitivity: variance-corrected slate draws (x sqrt(3/2))", "A", stretch=math.sqrt(1.5)),
    Scenario("A_w", "A calibrated on the test weeks: ranks over the 2022-2024 rows of weeks 10-18 only", "A10"),
)
SCENARIO_BY_KEY: dict[str, Scenario] = {s.key: s for s in SCENARIOS}


# --------------------------------------------------------------------------- #
# Inputs
# --------------------------------------------------------------------------- #


@dataclass
class SourceFrames:
    """The frames the real report functions build for 2022-2024 (kept for the real-path check)."""

    main: dict[str, pl.DataFrame]  # MAIN_LABELS, aligned, with the full-sample seed-0 ``pit_rand``
    cons: dict[str, pl.DataFrame]  # consensus population: reference models, focus, focus+cal, consensus
    cons_ref: list[str]  # D-077 reference labels in the consensus population (consensus last)
    bounds: pl.DataFrame | None  # prior-season position bounds (pos_min / pos_max)
    u_cal: pl.DataFrame  # key columns + ``u_cal`` (scenario A score of the focus+cal rows)
    u_cal10: pl.DataFrame | None = None  # the same score ranked over weeks 10-18 only (scenario A_w)


def source_frames(ppr: pl.DataFrame, consensus: pl.DataFrame, focus: str = FOCUS,
                  metric_seed: int = METRIC_SEED) -> SourceFrames:
    """Populations exactly as :func:`ffml.eval.report_holdout.compute_run` builds them."""
    present = set(ppr["model"].unique().to_list())
    models = RB.resolve_models(present, focus)
    raw = [m for m in models.raw if m in present]
    ref = RB.reference_models(models, present)
    aligned, _, _ = RB.headline_frames(ppr, raw, present, ref)
    bounds = H.bounds_from_run(ppr)
    main = {m: RB.with_randomized_pit(aligned[m], bounds, metric_seed) for m in MAIN_LABELS}
    cons_all = RB.consensus_population(consensus, aligned, raw, ref)
    cons_ref = [m for m in ref if m in cons_all] + [RB.CONSENSUS]
    keep = list(dict.fromkeys([*cons_ref, focus, FOCUS_CAL]))
    cons = {m: cons_all[m] for m in keep}
    return SourceFrames(main, cons, cons_ref, bounds, rank_calibrated_pit(main[FOCUS_CAL]),
                        rank_calibrated_pit(main[FOCUS_CAL], TEST_WEEKS))


def rank_calibrated_pit(frame: pl.DataFrame, weeks: Sequence[int] | None = None) -> pl.DataFrame:
    """Scenario A: per position, ``u' = (rank - 0.5) / n`` of the row's randomized PIT over all rows.

    With ``weeks`` the ranks are taken over the rows of those week numbers only
    (scenario A_w). Ties in ``u`` (rare) are broken by the canonical row order
    (season, week, position, entity).
    """
    pop = (M.population(M.validate_frame(RB._slim(frame))).filter(pl.col(M.RAND_PIT).is_not_null())
           .sort(list(RB.SORT_KEYS)))
    if weeks is not None:
        pop = pop.filter(pl.col("week").is_in(list(weeks)))
    return pop.select(
        *M.KEY_COLUMNS,
        ((pl.col(M.RAND_PIT).rank("ordinal").over("position").cast(pl.Float64) - 0.5)
         / pl.len().over("position").cast(pl.Float64)).alias("u_cal"),
    )


def load_source_frames(run: str = "default_20261102", purpose: str = "backtest") -> SourceFrames:  # pragma: no cover
    """Read the validated run (2022-2024 PPR rows only) and the restricted consensus, in memory."""
    from ffml.eval.ecr_baseline import consensus_predictions
    from ffml.eval.harness import read_manifest, resolve_run_dir
    from ffml.holdout import assert_seasons_allowed

    run_dir = resolve_run_dir(run)
    seasons = [int(s) for s in read_manifest(run_dir)["report_seasons"]]
    if sorted(seasons) != list(POOL_SEASONS):
        raise ValueError(f"run {run} reports {seasons}, expected {list(POOL_SEASONS)}")
    assert_seasons_allowed(seasons, purpose)
    cols = [c for c in (*RB.KEEP_COLUMNS, "pos_min", "pos_max") if c not in (M.REF_RANK, M.RAND_PIT)]
    lf = pl.scan_parquet(run_dir / "predictions.parquet")
    names = lf.collect_schema().names()
    ppr = (lf.filter(pl.col("scoring") == "ppr", pl.col("season").is_in(list(POOL_SEASONS)))
           .select([c for c in cols if c in names]).collect())
    consensus = consensus_predictions(list(POOL_SEASONS), "ppr", purpose=purpose)
    return source_frames(ppr, consensus)


# --------------------------------------------------------------------------- #
# Precomputed aggregates
# --------------------------------------------------------------------------- #


@dataclass
class SSGroup:
    """One slate-position of the consensus population, rows in D-077 reference order."""

    game: np.ndarray  # local game index per row
    in_pop: np.ndarray  # bool: active with an actual and a projection
    c2: dict[str, np.ndarray]  # 2 x concordance per row pair (int8, diagonal 0), per SS label
    cut: int
    full_conc: dict[str, float] = field(default_factory=dict)
    full_pairs: float = 0.0


@dataclass
class Slate:
    season: int
    week: int
    games: list[str]
    loss_sum: np.ndarray  # (G, P, len(MAIN_LABELS), len(METRICS))
    loss_n: np.ndarray
    cov: dict[str, np.ndarray]  # "A", "B_fixed", "L<i>" (level i of COVERAGE_LEVELS): covered counts (G, P)
    cov_n: np.ndarray  # (G, P) rows with a PIT
    main_rows: np.ndarray  # (G, P) all aligned rows (cluster presence)
    cons_rows: np.ndarray  # (G, len(ACC_POSITIONS)) all consensus-population rows
    pit_pos: np.ndarray  # eligible PIT rows in live order (position, entity): position index
    pit_game: np.ndarray
    pit_left: np.ndarray
    pit_right: np.ndarray
    ss: dict[str, SSGroup]

    @property
    def n_games(self) -> int:
        return len(self.games)


@dataclass
class PowerData:
    slates: dict[tuple[int, int], Slate]
    u_stream: np.ndarray  # default_rng(METRIC_SEED).random(n): the live test's PIT uniforms, in row order
    pooled_delta: np.ndarray  # (len(ACC_POSITIONS), len(COMPARATORS), len(METRICS)) 2022-2024 point deltas
    week_ratio: dict[int, dict[str, np.ndarray]]  # week number -> ratio of sums over the pool slates


def _num_array(frame: pl.DataFrame, col: str) -> np.ndarray:
    if col not in frame.columns:
        return np.full(frame.height, np.nan)
    return frame[col].cast(pl.Float64).to_numpy()


def _losses(pop: pl.DataFrame) -> np.ndarray:
    """Per-row MAE and pinball losses exactly as the metric bundle computes them (NaN where undefined)."""
    y = _num_array(pop, "actual")
    out = np.empty((pop.height, len(METRICS)))
    out[:, 0] = np.abs(_num_array(pop, "pred_q50") - y)
    for i, (q, col) in enumerate(((0.1, "pred_q10"), (0.5, "pred_q50"), (0.9, "pred_q90")), start=1):
        p = _num_array(pop, col)
        d = y - p
        out[:, i] = np.where(np.isnan(p), np.nan, np.maximum(q * d, (q - 1.0) * d))
    return out


def _bounds_for(bounds: pl.DataFrame | None, season: int) -> pl.DataFrame | None:
    """Bounds relabelled to ``season``; they must not differ between the pool seasons."""
    if bounds is None or bounds.is_empty():
        return bounds
    per = bounds.select("position", "pos_min", "pos_max").unique()
    if per.height != per["position"].n_unique():
        raise ValueError("position bounds differ between seasons; the window relabelling would change PITs")
    return per.with_columns(pl.lit(season, dtype=pl.Int32).alias("season"))


def build_power_data(src: SourceFrames, max_rows: int = 400_000) -> PowerData:
    """Per-game sums and counts, start/sit groups and PIT limits for every pool slate."""
    pos_idx = {p: i for i, p in enumerate(POSITIONS)}
    focus = M.validate_frame(RB._slim(src.main[FOCUS_CAL]))
    games = (focus.select("season", "week", "game_id").unique().drop_nulls()
             .sort("season", "week", "game_id"))
    if focus["game_id"].null_count():
        raise ValueError("aligned rows without a game id")
    games = games.with_columns(pl.int_range(0, pl.len()).over("season", "week").alias("g"))
    game_key = ["season", "week", "game_id"]

    def with_game(frame: pl.DataFrame) -> pl.DataFrame:
        keyed = frame.drop([c for c in ("game_id", "g") if c in frame.columns]).join(
            focus.select(*M.KEY_COLUMNS, "game_id"), on=list(M.KEY_COLUMNS), how="left")
        return keyed.join(games, on=game_key, how="left")

    slate_keys = games.select("season", "week").unique().sort("season", "week").rows()
    gcount = {(int(s), int(w)): int(n) for s, w, n in games.group_by("season", "week").len().rows()}
    gnames = {(int(s), int(w)): sub.sort("g")["game_id"].to_list()
              for (s, w), sub in games.group_by(["season", "week"], maintain_order=True)}

    def zeros(key, *shape):
        return np.zeros((gcount[key], *shape))

    loss_sum = {k: zeros(k, len(POSITIONS), len(MAIN_LABELS), len(METRICS)) for k in gcount}
    loss_n = {k: zeros(k, len(POSITIONS), len(MAIN_LABELS), len(METRICS)) for k in gcount}
    cov_keys = ["A", "A10", "B_fixed", *[f"L{i}" for i in range(len(COVERAGE_LEVELS))]]
    cov = {k: {c: zeros(k, len(POSITIONS)) for c in cov_keys} for k in gcount}
    cov_n = {k: zeros(k, len(POSITIONS)) for k in gcount}
    main_rows = {k: zeros(k, len(POSITIONS)) for k in gcount}
    cons_rows = {k: zeros(k, len(ACC_POSITIONS)) for k in gcount}

    def scatter(target: dict, frame: pl.DataFrame, values: np.ndarray, extra: tuple = ()) -> None:
        s = frame["season"].to_numpy()
        w = frame["week"].to_numpy()
        g = frame["g"].to_numpy()
        p = np.array([pos_idx[x] for x in frame["position"].to_list()], dtype=np.int64)
        for key in gcount:
            m = (s == key[0]) & (w == key[1])
            if m.any():
                np.add.at(target[key], (g[m], p[m], *extra), values[m])

    # accuracy sums (population rows of each model; NaN losses are masked as in the bundle)
    for mi, label in enumerate(MAIN_LABELS):
        pop = with_game(M.population(M.validate_frame(RB._slim(src.main[label]))))
        lo = _losses(pop)
        for xi in range(len(METRICS)):
            v = lo[:, xi]
            ok = ~np.isnan(v)
            scatter(loss_sum, pop.filter(pl.Series(ok)), v[ok], (mi, xi))
            scatter(loss_n, pop.filter(pl.Series(ok)), np.ones(int(ok.sum())), (mi, xi))
    allrows = with_game(focus)
    scatter(main_rows, allrows, np.ones(allrows.height))

    # coverage: validation draws (B_fixed), rank-calibrated (A) and the coverage-level variants
    pop = with_game(M.population(focus)).filter(pl.col(M.RAND_PIT).is_not_null())
    pop = pop.join(src.u_cal, on=list(M.KEY_COLUMNS), how="left")
    u10 = src.u_cal10 if src.u_cal10 is not None else src.u_cal.head(0)
    pop = pop.join(u10.rename({"u_cal": "u_cal10"}), on=list(M.KEY_COLUMNS), how="left")
    if pop["u_cal"].null_count():
        raise ValueError("rank-calibrated PIT missing for some rows")
    u = pop[M.RAND_PIT].to_numpy()
    ua = pop["u_cal"].to_numpy()
    # A_w: weeks 10-18 ranked among themselves; other weeks keep A (A_w is reported for windows i and ii)
    uw = pop["u_cal10"].fill_null(pop["u_cal"]).to_numpy()
    lo_b, hi_b = BAND_PIT
    scatter(cov_n, pop, np.ones(pop.height))
    _cov_scatter = {
        "A": ((ua > lo_b) & (ua < hi_b)).astype(float),
        "B_fixed": ((u > lo_b) & (u < hi_b)).astype(float),
    }
    for i, c in enumerate(COVERAGE_LEVELS):
        a = (1.0 - c) / 2.0
        _cov_scatter[f"L{i}"] = ((uw > a) & (uw < 1.0 - a)).astype(float)
    _cov_scatter["A10"] = ((uw > lo_b) & (uw < hi_b)).astype(float)
    for name, vals in _cov_scatter.items():
        tgt = {k: cov[k][name] for k in gcount}
        scatter(tgt, pop, vals)

    # PIT limits for the live redraw (scenario B), rows in the live order within each slate
    levels, kcols = RB.knot_columns(focus)
    elig = focus.filter(
        pl.col("active").fill_null(False) & pl.col("actual").is_not_null() & pl.col("pred_q50").is_not_null()
        & pl.all_horizontal([pl.col(c).is_not_null() & ~pl.col(c).cast(pl.Float64).is_nan() for c in kcols])
    )
    b = src.bounds
    if b is not None and not b.is_empty():
        elig = elig.join(b.select(pl.col("season").cast(elig.schema["season"]), "position",
                                  pl.col("pos_min").cast(pl.Float64), pl.col("pos_max").cast(pl.Float64)),
                         on=["season", "position"], how="left")
    else:
        elig = elig.with_columns(pl.lit(None, dtype=pl.Float64).alias("pos_min"),
                                 pl.lit(None, dtype=pl.Float64).alias("pos_max"))
    elig = with_game(elig).sort(list(RB.SORT_KEYS))
    left, right = M.cdf_limits(elig["actual"].cast(pl.Float64).to_numpy(),
                               elig.select(kcols).cast(pl.Float64).to_numpy(),
                               elig["pos_min"].to_numpy(), elig["pos_max"].to_numpy(), levels)
    elig = elig.with_columns(pl.Series("_left", left), pl.Series("_right", right))

    # start/sit groups on the consensus population (scores by each model's own ordering rule)
    prepared = RB.prepare_frames({m: src.cons[m] for m in (FOCUS, *SS_LABELS)})
    scores = {}
    for label in SS_LABELS:
        ranked = M.rank_within(prepared[label], M.GROUP)
        scores[label] = ranked.select(*M.KEY_COLUMNS, pl.col(M._SCORE).alias(f"_s{SS_LABELS.index(label)}"))
    ref_q50 = src.cons[FOCUS_CAL].select(*M.KEY_COLUMNS, "ref_q50").with_columns(
        [pl.col(c).cast(prepared[FOCUS_CAL].schema[c]) for c in M.KEY_COLUMNS])
    cbase = with_game(prepared[FOCUS_CAL].select(*M.KEY_COLUMNS, "actual", "active", "pred_q50", M.REF_RANK)
                      .join(ref_q50, on=list(M.KEY_COLUMNS), how="left"))
    for label in SS_LABELS:
        cbase = cbase.join(scores[label], on=list(M.KEY_COLUMNS), how="left")
    cons_pop_keys = {lab: M.population(prepared[lab]).select(M.KEY_COLUMNS) for lab in SS_LABELS}
    if not cons_pop_keys[SS_LABELS[0]].sort(list(M.KEY_COLUMNS)).equals(
            cons_pop_keys[SS_LABELS[1]].sort(list(M.KEY_COLUMNS))):
        raise ValueError("start/sit populations differ between the focus model and the consensus")
    cbase = cbase.with_columns(
        (pl.col("active").fill_null(False) & pl.col("actual").is_not_null() & pl.col("pred_q50").is_not_null())
        .alias("_pop"))
    acc_idx = {p: i for i, p in enumerate(ACC_POSITIONS)}
    cacc = cbase.filter(pl.col("position").is_in(list(ACC_POSITIONS)))
    if cacc["g"].null_count():
        raise ValueError("consensus rows without a game")
    s_ = cacc["season"].to_numpy()
    w_ = cacc["week"].to_numpy()
    for key in gcount:
        m = (s_ == key[0]) & (w_ == key[1])
        if m.any():
            sub = cacc.filter(pl.Series(m))
            pidx = np.array([acc_idx[x] for x in sub["position"].to_list()], dtype=np.int64)
            np.add.at(cons_rows[key], (sub["g"].to_numpy(), pidx), 1.0)

    slates: dict[tuple[int, int], Slate] = {}
    elig_season = elig["season"].to_numpy()
    elig_week = elig["week"].to_numpy()
    for key in [(int(s), int(w)) for s, w in slate_keys]:
        m = (elig_season == key[0]) & (elig_week == key[1])
        es = elig.filter(pl.Series(m))
        ss: dict[str, SSGroup] = {}
        cs = cacc.filter((pl.col("season") == key[0]) & (pl.col("week") == key[1]))
        for pos in ACC_POSITIONS:
            grp = cs.filter(pl.col("position") == pos).sort(["ref_q50", "entity_id"], descending=[True, False],
                                                             nulls_last=True)
            if grp.is_empty():
                continue
            if grp["ref_q50"].null_count():
                raise ValueError("consensus-population row without a reference projection")
            if not np.array_equal(grp[M.REF_RANK].to_numpy(), np.arange(1, grp.height + 1)):
                raise ValueError(f"reference order of {key} {pos} does not reproduce ref_rank")
            actual = grp["actual"].cast(pl.Float64).fill_null(0.0).to_numpy()
            c2 = {}
            for li, label in enumerate(SS_LABELS):
                sc = grp[f"_s{li}"].to_numpy()
                ds = sc[:, None] - sc[None, :]
                da = actual[:, None] - actual[None, :]
                conc = np.where((da == 0) | (ds == 0), 1, 2 * (np.sign(ds) == np.sign(da)).astype(np.int8))
                conc = conc.astype(np.int8)
                np.fill_diagonal(conc, 0)
                c2[label] = conc
            group = SSGroup(grp["g"].to_numpy().astype(np.int64), grp["_pop"].to_numpy().astype(bool), c2,
                            int(M.STARTABLE[pos]))
            conc_full, pairs_full = _ss_sums(group, None)
            group.full_conc, group.full_pairs = conc_full, pairs_full
            ss[pos] = group
        slates[key] = Slate(
            season=key[0], week=key[1], games=gnames[key],
            loss_sum=loss_sum[key], loss_n=loss_n[key], cov=cov[key], cov_n=cov_n[key],
            main_rows=main_rows[key], cons_rows=cons_rows[key],
            pit_pos=np.array([pos_idx[x] for x in es["position"].to_list()], dtype=np.int64),
            pit_game=es["g"].to_numpy().astype(np.int64),
            pit_left=es["_left"].to_numpy(), pit_right=es["_right"].to_numpy(), ss=ss,
        )
    u_stream = np.random.default_rng(METRIC_SEED).random(max_rows)
    data = PowerData(slates, u_stream, np.zeros((len(ACC_POSITIONS), len(COMPARATORS), len(METRICS))), {})
    full = aggregate(data, full_picks(data))
    s_tot, n_tot = full.loss_sum.sum(0), full.loss_n.sum(0)
    point = s_tot / n_tot
    for pi, pos in enumerate(ACC_POSITIONS):
        p = POSITIONS.index(pos)
        for ci in range(len(COMPARATORS)):
            data.pooled_delta[pi, ci] = point[p, 0] - point[p, ci + 1]
    data.week_ratio = _week_ratios(data)
    return data


def _ss_sums(group: SSGroup, keep: np.ndarray | None) -> tuple[dict[str, float], float]:
    """Concordance sums and the pair count of one start/sit group (games in ``keep`` only)."""
    if keep is None and group.full_conc:
        return group.full_conc, group.full_pairs
    rows = np.arange(group.game.size) if keep is None else np.flatnonzero(keep[group.game])
    top = rows[: group.cut]  # D-077 ref_rank <= cut among the kept rows (inactive rows count in the rank)
    sel = top[group.in_pop[top]]
    m = sel.size
    pairs = m * (m - 1) / 2.0
    conc = {lab: float(c[np.ix_(sel, sel)].sum(dtype=np.int64)) / 4.0 for lab, c in group.c2.items()}
    return conc, pairs


# --------------------------------------------------------------------------- #
# Windows
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class Pick:
    season: int
    week: int
    keep: np.ndarray | None  # games kept (bool per slate game); None = full slate
    short: bool = False  # the slate has fewer games than the 2026 week


def draw_window(data: PowerData, window: Window, rng: np.random.Generator) -> list[Pick]:
    picks = []
    for week, target in zip(window.weeks, window.games, strict=True):
        season = POOL_SEASONS[int(rng.integers(len(POOL_SEASONS)))]
        slate = data.slates[(season, week)]
        keep = None
        if target is not None and slate.n_games > target:
            keep = np.zeros(slate.n_games, dtype=bool)
            keep[rng.choice(slate.n_games, size=target, replace=False)] = True
        picks.append(Pick(season, week, keep, target is not None and slate.n_games < target))
    return picks


def full_picks(data: PowerData) -> list[Pick]:
    """All 2022-2024 slates in cluster order (reproduces the validation report)."""
    return [Pick(s, w, None) for s, w in sorted(data.slates)]


@dataclass
class WindowAgg:
    weeks: list[int]
    loss_sum: np.ndarray  # (K, P, MODELS, METRICS)
    loss_n: np.ndarray
    cov: dict[str, np.ndarray]  # (K, P)
    cov_n: np.ndarray
    cov_b: np.ndarray  # (P,) scenario B covered counts (seed-0 redraw over the window)
    cov_b_n: np.ndarray
    main_rows: np.ndarray  # (K, P)
    cons_rows: np.ndarray  # (K, len(ACC_POSITIONS))
    conc: np.ndarray  # (K, len(ACC_POSITIONS), len(SS_LABELS))
    pairs: np.ndarray  # (K, len(ACC_POSITIONS))
    games: np.ndarray  # (K,) games in the simulated week
    short: np.ndarray  # (K,) bool


def aggregate(data: PowerData, picks: Sequence[Pick]) -> WindowAgg:
    K = len(picks)
    P = len(POSITIONS)
    agg = WindowAgg(
        weeks=[p.week for p in picks],
        loss_sum=np.zeros((K, P, len(MAIN_LABELS), len(METRICS))), loss_n=np.zeros((K, P, len(MAIN_LABELS), len(METRICS))),
        cov={}, cov_n=np.zeros((K, P)), cov_b=np.zeros(P), cov_b_n=np.zeros(P),
        main_rows=np.zeros((K, P)), cons_rows=np.zeros((K, len(ACC_POSITIONS))),
        conc=np.zeros((K, len(ACC_POSITIONS), len(SS_LABELS))), pairs=np.zeros((K, len(ACC_POSITIONS))),
        games=np.zeros(K), short=np.zeros(K, dtype=bool),
    )
    first = data.slates[(picks[0].season, picks[0].week)]
    agg.cov = {c: np.zeros((K, P)) for c in first.cov}
    lefts, rights, poss = [], [], []
    for j, pick in enumerate(picks):
        sl = data.slates[(pick.season, pick.week)]
        if pick.keep is None:
            agg.loss_sum[j] = sl.loss_sum.sum(0)
            agg.loss_n[j] = sl.loss_n.sum(0)
            for c in sl.cov:
                agg.cov[c][j] = sl.cov[c].sum(0)
            agg.cov_n[j] = sl.cov_n.sum(0)
            agg.main_rows[j] = sl.main_rows.sum(0)
            agg.cons_rows[j] = sl.cons_rows.sum(0)
            rows = slice(None)
            agg.games[j] = sl.n_games
        else:
            kp = pick.keep
            agg.loss_sum[j] = sl.loss_sum[kp].sum(0)
            agg.loss_n[j] = sl.loss_n[kp].sum(0)
            for c in sl.cov:
                agg.cov[c][j] = sl.cov[c][kp].sum(0)
            agg.cov_n[j] = sl.cov_n[kp].sum(0)
            agg.main_rows[j] = sl.main_rows[kp].sum(0)
            agg.cons_rows[j] = sl.cons_rows[kp].sum(0)
            rows = kp[sl.pit_game]
            agg.games[j] = int(kp.sum())
        agg.short[j] = pick.short
        lefts.append(sl.pit_left[rows])
        rights.append(sl.pit_right[rows])
        poss.append(sl.pit_pos[rows])
        for pi, pos in enumerate(ACC_POSITIONS):
            group = sl.ss.get(pos)
            if group is None:
                continue
            conc, pairs = _ss_sums(group, pick.keep)
            agg.pairs[j, pi] = pairs
            for li, label in enumerate(SS_LABELS):
                agg.conc[j, pi, li] = conc[label]
    left = np.concatenate(lefts)
    right = np.concatenate(rights)
    pos = np.concatenate(poss)
    if left.size > data.u_stream.size:
        raise ValueError("u_stream too short for this window")
    u = left + (right - left) * data.u_stream[: left.size]
    inside = (u > BAND_PIT[0]) & (u < BAND_PIT[1])
    agg.cov_b = np.bincount(pos[inside], minlength=P).astype(float)
    agg.cov_b_n = np.bincount(pos, minlength=P).astype(float)
    return agg


# --------------------------------------------------------------------------- #
# Lines
# --------------------------------------------------------------------------- #

_COUNTS: dict[int, np.ndarray] = {}


def boot_counts(n_clusters: int, n_boot: int = N_BOOT, seed: int = BOOT_SEED) -> np.ndarray:
    """``C[b, j]``: times draw ``b`` picks cluster ``j`` (the draws of ``_paired_bundle_bootstrap``)."""
    key = (n_clusters, n_boot, seed)
    if key not in _COUNTS:
        draws = np.random.default_rng(seed).integers(0, n_clusters, size=(n_boot, n_clusters))
        counts = np.zeros((n_boot, n_clusters))
        for b in range(n_boot):
            counts[b] = np.bincount(draws[b], minlength=n_clusters)
        _COUNTS[key] = counts
    return _COUNTS[key]


def _interval(values: np.ndarray, alpha: float = ALPHA) -> tuple[np.ndarray, np.ndarray]:
    """Percentile interval along axis 0, NaN draws dropped (``metrics._percentile``)."""
    if not np.isnan(values).any():
        lo, hi = np.quantile(values, [alpha / 2, 1 - alpha / 2], axis=0)
        return lo, hi
    flat = values.reshape(values.shape[0], -1)
    lo = np.full(flat.shape[1], np.nan)
    hi = np.full(flat.shape[1], np.nan)
    for i in range(flat.shape[1]):
        v = flat[:, i][~np.isnan(flat[:, i])]
        if v.size:
            lo[i], hi[i] = np.quantile(v, [alpha / 2, 1 - alpha / 2])
    return lo.reshape(values.shape[1:]), hi.reshape(values.shape[1:])


@dataclass
class LineStats:
    acc_delta: np.ndarray  # (len(ACC_POSITIONS), len(COMPARATORS), len(METRICS))
    acc_lo: np.ndarray
    acc_hi: np.ndarray
    ss_delta: np.ndarray  # (len(ACC_POSITIONS),)
    ss_lo: np.ndarray
    ss_hi: np.ndarray
    cov: np.ndarray  # (len(POSITIONS),)
    passed: np.ndarray  # (22,) in LINE_IDS order

    def as_dict(self) -> dict[str, Any]:
        out: dict[str, Any] = {}
        for line in LINES:
            if line.kind == "beats_baseline":
                pi, ci = ACC_POSITIONS.index(line.position), H.LINE_BASELINES.index(line.comparator)
                out[line.id] = {m: (float(self.acc_delta[pi, ci, xi]), float(self.acc_lo[pi, ci, xi]),
                                    float(self.acc_hi[pi, ci, xi])) for xi, m in enumerate(METRICS)}
            elif line.kind == "startsit_consensus":
                pi = ACC_POSITIONS.index(line.position)
                out[line.id] = (float(self.ss_delta[pi]), float(self.ss_lo[pi]), float(self.ss_hi[pi]))
            else:
                out[line.id] = float(self.cov[POSITIONS.index(line.position)])
        return out


def _stretch(agg: WindowAgg, data: PowerData, lam: float) -> WindowAgg:
    """Scale each simulated week's deviation from its week-number mean by ``lam`` (sums only)."""
    if lam == 1.0:
        return agg
    out = WindowAgg(**{**agg.__dict__})
    ls = agg.loss_sum.copy()
    ca = agg.cov["A"].copy()
    conc = agg.conc.copy()
    for j, w in enumerate(agg.weeks):
        r = data.week_ratio[w]
        ls[j] = agg.loss_n[j] * r["loss"] + lam * (agg.loss_sum[j] - agg.loss_n[j] * r["loss"])
        ca[j] = agg.cov_n[j] * r["cov"] + lam * (agg.cov["A"][j] - agg.cov_n[j] * r["cov"])
        conc[j] = agg.pairs[j][:, None] * r["conc"] + lam * (agg.conc[j] - agg.pairs[j][:, None] * r["conc"])
    out.loss_sum = ls
    out.cov = {**agg.cov, "A": ca}
    out.conc = conc
    return out


def _week_ratios(data: PowerData) -> dict[int, dict[str, np.ndarray]]:
    out: dict[int, dict[str, np.ndarray]] = {}
    for w in sorted({k[1] for k in data.slates}):
        sl = [data.slates[(s, w)] for s in POOL_SEASONS if (s, w) in data.slates]
        loss = sum(x.loss_sum.sum(0) for x in sl) / np.maximum(sum(x.loss_n.sum(0) for x in sl), 1e-300)
        cov = sum(x.cov["A"].sum(0) for x in sl) / np.maximum(sum(x.cov_n.sum(0) for x in sl), 1e-300)
        conc = np.zeros((len(ACC_POSITIONS), len(SS_LABELS)))
        pairs = np.zeros(len(ACC_POSITIONS))
        for x in sl:
            for pi, pos in enumerate(ACC_POSITIONS):
                g = x.ss.get(pos)
                if g is not None:
                    pairs[pi] += g.full_pairs
                    conc[pi] += [g.full_conc[lab] for lab in SS_LABELS]
        conc = np.where(pairs[:, None] > 0, conc / np.maximum(pairs[:, None], 1e-300), 0.0)
        out[w] = {"loss": loss, "cov": cov, "conc": conc}
    return out


def line_stats(agg: WindowAgg, scenario: Scenario | str = "A", data: PowerData | None = None,
               cov_key: str | None = None, n_boot: int = N_BOOT) -> LineStats:
    """The 22 lines on one window (fast path). ``cov_key`` overrides the coverage source (e.g. ``L3``)."""
    sc = SCENARIO_BY_KEY[scenario] if isinstance(scenario, str) else scenario
    if sc.stretch != 1.0:
        if data is None:
            raise ValueError("a stretched scenario needs the PowerData week ratios")
        agg = _stretch(agg, data, sc.stretch)
    P = len(POSITIONS)
    # main bootstrap: universe = weeks with any aligned row at the position (same for every position here)
    acc_shape = (len(ACC_POSITIONS), len(COMPARATORS), len(METRICS))
    acc_delta, acc_lo, acc_hi = np.empty(acc_shape), np.empty(acc_shape), np.empty(acc_shape)
    for pi, pos in enumerate(ACC_POSITIONS):
        p = POSITIONS.index(pos)
        present = agg.main_rows[:, p] > 0
        C = boot_counts(int(present.sum()), n_boot)
        S = agg.loss_sum[present, p]  # (K', MODELS, METRICS)
        N = agg.loss_n[present, p]
        with np.errstate(invalid="ignore", divide="ignore"):
            boots = np.einsum("bk,kmx->bmx", C, S) / np.einsum("bk,kmx->bmx", C, N)
            point = S.sum(0) / N.sum(0)
        diff = boots[:, :1, :] - boots[:, 1:, :]  # (B, COMPARATORS, METRICS)
        d = point[0][None, :] - point[1:]
        if sc.acc_mode == "scale":
            diff, d = sc.k * diff, sc.k * d
        elif sc.acc_mode == "shift":
            if data is None:
                raise ValueError("the shift scenario needs the pooled deltas")
            shift = (1.0 - sc.k) * data.pooled_delta[pi]
            diff, d = diff - shift[None], d - shift
        lo, hi = _interval(diff)
        acc_delta[pi], acc_lo[pi], acc_hi[pi] = d, lo, hi
    # start/sit (consensus population; universe = weeks with consensus rows at the position)
    ss_delta, ss_lo, ss_hi = (np.full(len(ACC_POSITIONS), np.nan) for _ in range(3))
    for pi in range(len(ACC_POSITIONS)):
        present = agg.cons_rows[:, pi] > 0
        if not present.any():
            continue
        C = boot_counts(int(present.sum()), n_boot)
        conc = agg.conc[present, pi]  # (K', LABELS)
        pairs = agg.pairs[present, pi]
        with np.errstate(invalid="ignore", divide="ignore"):
            boots = (C @ conc) / (C @ pairs)[:, None]
            point = conc.sum(0) / pairs.sum()
        lo, hi = _interval((boots[:, 0] - boots[:, 1])[:, None])
        ss_delta[pi], ss_lo[pi], ss_hi[pi] = point[0] - point[1], lo[0], hi[0]
    # coverage (point value)
    key = cov_key or sc.coverage
    with np.errstate(invalid="ignore", divide="ignore"):
        if key == "B":
            cov = agg.cov_b / agg.cov_b_n
        else:
            cov = agg.cov[key].sum(0) / agg.cov_n.sum(0)
    passed = np.zeros(len(LINES), dtype=bool)
    for i, line in enumerate(LINES):
        if line.kind == "beats_baseline":
            pi, ci = ACC_POSITIONS.index(line.position), H.LINE_BASELINES.index(line.comparator)
            h = acc_hi[pi, ci]
            passed[i] = bool(np.all(~np.isnan(h) & (h < 0)))
        elif line.kind == "startsit_consensus":
            pi = ACC_POSITIONS.index(line.position)
            passed[i] = bool(not math.isnan(ss_lo[pi]) and not (ss_hi[pi] < 0))
        else:
            v = cov[POSITIONS.index(line.position)]
            passed[i] = bool(not math.isnan(v) and BAND[0] <= v <= BAND[1])
    assert P == cov.size
    return LineStats(acc_delta, acc_lo, acc_hi, ss_delta, ss_lo, ss_hi, cov, passed)


# --------------------------------------------------------------------------- #
# The real functions on the same window (equivalence check)
# --------------------------------------------------------------------------- #


def window_keys(src: SourceFrames, picks: Sequence[Pick], data: PowerData) -> pl.DataFrame:
    """Key columns of the aligned rows in the window's kept games."""
    rows = []
    for pick in picks:
        sl = data.slates[(pick.season, pick.week)]
        kept = sl.games if pick.keep is None else [g for g, k in zip(sl.games, pick.keep, strict=True) if k]
        rows += [(pick.season, pick.week, g) for g in kept]
    kept_games = pl.DataFrame(rows, schema={"season": pl.Int32, "week": pl.Int32, "game_id": pl.String}, orient="row")
    focus = src.main[FOCUS_CAL]
    return focus.join(kept_games, on=["season", "week", "game_id"], how="semi").select(M.KEY_COLUMNS)


def _relabel(frame: pl.DataFrame, keys: pl.DataFrame, season: int) -> pl.DataFrame:
    out = frame.join(keys, on=list(M.KEY_COLUMNS), how="semi")
    return out.with_columns(pl.lit(season, dtype=out.schema["season"]).alias("season"))


def real_window_lines(src: SourceFrames, picks: Sequence[Pick], data: PowerData, coverage: str = "B",
                      n_boot: int = N_BOOT, seed: int = BOOT_SEED, metric_seed: int = METRIC_SEED,
                      relabel: bool = True) -> dict[str, Any]:
    """The 22 lines with the real report functions on the window's rows (slow; for the check).

    ``coverage``: ``B`` redraws the PIT on the window with ``with_randomized_pit``
    (what the live test does), ``B_fixed`` keeps the validation draws, ``A``
    uses the rank-calibrated scores as ``pit_rand``. With ``relabel`` the
    window is season 2026 (one cluster per week, live sort order); without it
    the rows keep their seasons (used to reproduce the validation report).
    """
    keys = window_keys(src, picks, data)
    season = LIVE_SEASON if relabel else None

    def restrict(f: pl.DataFrame) -> pl.DataFrame:
        return _relabel(f, keys, season) if relabel else f.join(keys, on=list(M.KEY_COLUMNS), how="semi")

    main = {}
    for label in MAIN_LABELS:
        f = src.main[label]
        if coverage == "A" and label == FOCUS_CAL:
            f = f.drop(M.RAND_PIT).join(src.u_cal.rename({"u_cal": M.RAND_PIT}), on=list(M.KEY_COLUMNS),
                                        how="left")
        f = restrict(f)
        if coverage == "B":
            bounds = _bounds_for(src.bounds, LIVE_SEASON) if relabel else src.bounds
            f = RB.with_randomized_pit(f, bounds, metric_seed)
        main[label] = f
    mb = RB.run_bootstrap(main, POSITIONS, lineup=False, n_boot=n_boot, seed=seed)
    cons = {m: restrict(f.drop([c for c in (M.REF_RANK, "ref_q50") if c in f.columns]))
            for m, f in src.cons.items()}
    cons = M.with_reference_rank(cons, reference=src.cons_ref)
    cb = RB.run_bootstrap({m: cons[m] for m in (FOCUS, FOCUS_CAL, RB.CONSENSUS)}, ACC_POSITIONS, lineup=False,
                          n_boot=n_boot, seed=seed)
    results = H.evaluate_lines(mb, cb, FOCUS)
    out: dict[str, Any] = {"passed": [r.passed for r in results]}
    for line in LINES:
        if line.kind == "beats_baseline":
            other = line.comparator + RB.CAL
            out[line.id] = {}
            for m in METRICS:
                d = mb.delta(line.position, FOCUS_CAL, other, m)
                out[line.id][m] = (d.delta, d.lo, d.hi)
        elif line.kind == "startsit_consensus":
            d = cb.delta(line.position, FOCUS_CAL, RB.CONSENSUS, "start_sit_common")
            out[line.id] = (d.delta, d.lo, d.hi)
        else:
            out[line.id] = mb.value(line.position, FOCUS_CAL, "coverage_tie_aware")
    return out


def compare_stats(fast: LineStats, real: Mapping[str, Any]) -> dict[str, Any]:
    """Largest absolute difference between the fast path and the real functions, and verdict agreement."""
    f = fast.as_dict()
    worst = 0.0
    where = ""
    for lid in LINE_IDS:
        a, b = f[lid], real[lid]
        pairs = []
        if isinstance(a, dict):
            for m in METRICS:
                pairs += list(zip(a[m], b[m], strict=True))
        elif isinstance(a, tuple):
            pairs += list(zip(a, b, strict=True))
        else:
            pairs.append((a, b))
        for x, y in pairs:
            if math.isnan(x) and math.isnan(y):
                continue
            diff = abs(x - y) if not (math.isnan(x) or math.isnan(y)) else math.inf
            if diff > worst:
                worst, where = diff, lid
    same = [bool(x) == bool(y) for x, y in zip(fast.passed, real["passed"], strict=True)]
    return {"max_abs_diff": worst, "where": where, "verdicts_equal": all(same),
            "n_values": sum(12 if isinstance(f[k], dict) else 3 if isinstance(f[k], tuple) else 1 for k in LINE_IDS)}


# --------------------------------------------------------------------------- #
# Simulation
# --------------------------------------------------------------------------- #


@dataclass
class SimResult:
    window: Window
    n_rep: int
    passed: dict[str, np.ndarray]  # scenario -> (R, 22) bool
    cov: dict[str, np.ndarray]  # scenario coverage source -> (R, P) coverage values
    level_pass: np.ndarray  # (R, len(COVERAGE_LEVELS), P) coverage line passes at each true level
    rows: np.ndarray  # (R, P) focus+cal MAE rows in the window
    cov_rows: np.ndarray  # (R, P) rows with a PIT
    pairs: np.ndarray  # (R, len(ACC_POSITIONS)) start/sit pairs
    ss_weeks: np.ndarray  # (R, len(ACC_POSITIONS)) weeks with consensus rows
    games: np.ndarray  # (R,) games in the window
    short_weeks: np.ndarray  # (R, K) bool
    drop_weeks: np.ndarray  # (R, K) bool, games were dropped
    seconds: float = 0.0
    stats_a: dict[str, np.ndarray] = field(default_factory=dict)  # scenario A per-replicate deltas and intervals


#: line groups for the descriptive "what if this group could not fail" table
LINE_GROUPS: dict[str, tuple[str, ...]] = {
    "QB accuracy (L01, L05, L09)": ("L01", "L05", "L09"),
    "TE accuracy (L04, L08, L12)": ("L04", "L08", "L12"),
    "RB and WR accuracy (L02, L03, L06, L07, L10, L11)": ("L02", "L03", "L06", "L07", "L10", "L11"),
    "all 12 accuracy lines (L01-L12)": tuple(f"L{i:02d}" for i in range(1, 13)),
    "4 start/sit lines (L13-L16)": ("L13", "L14", "L15", "L16"),
    "6 coverage lines (L17-L22)": tuple(f"L{i:02d}" for i in range(17, 23)),
}


def simulate(data: PowerData, window: Window, n_rep: int, scenarios: Sequence[Scenario] = SCENARIOS,
             seed: int = SIM_SEED, progress: bool = False) -> SimResult:
    """``n_rep`` replicates of ``window``; every scenario sees the same slates and dropped games."""
    t0 = time.time()
    rng = np.random.default_rng([seed, list(WINDOWS).index(window.key) if window.key in WINDOWS else 99])
    K, P = len(window.weeks), len(POSITIONS)
    res = SimResult(
        window, n_rep, {s.key: np.zeros((n_rep, len(LINES)), dtype=bool) for s in scenarios},
        {c: np.zeros((n_rep, P)) for c in ("A", "A10", "B", "B_fixed")},
        np.zeros((n_rep, len(COVERAGE_LEVELS), P), dtype=bool), np.zeros((n_rep, P)), np.zeros((n_rep, P)),
        np.zeros((n_rep, len(ACC_POSITIONS))), np.zeros((n_rep, len(ACC_POSITIONS))), np.zeros(n_rep),
        np.zeros((n_rep, K), dtype=bool), np.zeros((n_rep, K), dtype=bool),
    )
    for r in range(n_rep):
        picks = draw_window(data, window, rng)
        agg = aggregate(data, picks)
        for s in scenarios:
            st = line_stats(agg, s, data)
            res.passed[s.key][r] = st.passed
            if s.key == "A":
                for name in ("acc_delta", "acc_lo", "acc_hi", "ss_delta", "ss_lo", "ss_hi"):
                    v = getattr(st, name)
                    if name not in res.stats_a:
                        res.stats_a[name] = np.zeros((n_rep, *v.shape))
                    res.stats_a[name][r] = v
        with np.errstate(invalid="ignore", divide="ignore"):
            res.cov["A"][r] = agg.cov["A"].sum(0) / agg.cov_n.sum(0)
            res.cov["B_fixed"][r] = agg.cov["B_fixed"].sum(0) / agg.cov_n.sum(0)
            res.cov["A10"][r] = agg.cov["A10"].sum(0) / agg.cov_n.sum(0)
            res.cov["B"][r] = agg.cov_b / agg.cov_b_n
            for li in range(len(COVERAGE_LEVELS)):
                v = agg.cov[f"L{li}"].sum(0) / agg.cov_n.sum(0)
                res.level_pass[r, li] = (v >= BAND[0]) & (v <= BAND[1])
        res.rows[r] = agg.loss_n[:, :, 0, 0].sum(0)
        res.cov_rows[r] = agg.cov_n.sum(0)
        res.pairs[r] = agg.pairs.sum(0)
        res.ss_weeks[r] = (agg.cons_rows > 0).sum(0)
        res.games[r] = agg.games.sum()
        res.short_weeks[r] = agg.short
        res.drop_weeks[r] = [p.keep is not None for p in picks]
        if progress and (r + 1) % 500 == 0:
            print(f"  window {window.key}: {r + 1}/{n_rep} replicates, {time.time() - t0:.0f}s", flush=True)
    res.seconds = time.time() - t0
    return res


def mc_se(p: float, n: int) -> float:
    return math.sqrt(max(p * (1 - p), 0.0) / n) if n else math.nan


def summary(res: SimResult, scenario: str) -> dict[str, Any]:
    passed = res.passed[scenario]
    n = res.n_rep
    kinds = np.array([line.kind for line in LINES])
    all22 = passed.all(1)
    cov6 = passed[:, kinds == "coverage"].all(1)
    acc16 = passed[:, kinds != "coverage"].all(1)
    out = {"all22": all22.mean(), "cov6": cov6.mean(), "acc16": acc16.mean(),
           "acc12": passed[:, kinds == "beats_baseline"].all(1).mean(),
           "ss4": passed[:, kinds == "startsit_consensus"].all(1).mean(),
           "per_line": passed.mean(0), "n": n}
    fails = ~passed
    out["fail_rate"] = fails.mean(0)
    out["sole_failure"] = (fails & (fails.sum(1) == 1)[:, None]).mean(0)
    # how much P(all 22) would rise if the line always passed
    out["lift_if_waived"] = np.array([np.delete(passed, i, axis=1).all(1).mean() - all22.mean()
                                      for i in range(len(LINES))])
    out["mean_failed_lines"] = fails.sum(1).mean()
    out["group_lift"] = {g: float(passed[:, [i for i, lid in enumerate(LINE_IDS) if lid not in ids]].all(1).mean()
                                  - all22.mean()) for g, ids in LINE_GROUPS.items()}
    return out


# --------------------------------------------------------------------------- #
# Design effect of coverage (within-week and within-game correlation)
# --------------------------------------------------------------------------- #


def design_effects(data: PowerData, cov_key: str = "A") -> pl.DataFrame:
    """Per position: variance of pooled coverage with week (and game) clusters over the binomial variance.

    ``deff = V_cluster / V_srs`` with the ratio-estimator cluster variance
    ``K / (K - 1) * sum_k (y_k - p n_k)^2 / n^2`` and ``V_srs = p (1 - p) / n``,
    over all 2022-2024 weeks (54 clusters) and games.
    """
    rows = []
    for p, pos in enumerate(POSITIONS):
        yw, nw, yg, ng = [], [], [], []
        for key in sorted(data.slates):
            sl = data.slates[key]
            y = sl.cov[cov_key][:, p]
            n = sl.cov_n[:, p]
            yw.append(y.sum())
            nw.append(n.sum())
            yg += list(y[n > 0])
            ng += list(n[n > 0])
        rows.append({"position": pos, **_deff(np.array(yw), np.array(nw), "week"),
                     **_deff(np.array(yg), np.array(ng), "game")})
    return pl.DataFrame(rows)


def _deff(y: np.ndarray, n: np.ndarray, tag: str) -> dict[str, float]:
    keep = n > 0
    y, n = y[keep], n[keep]
    k = y.size
    tot = n.sum()
    p = y.sum() / tot
    v_clu = k / (k - 1) * float(((y - p * n) ** 2).sum()) / tot**2
    v_srs = p * (1 - p) / tot
    out = {f"deff_{tag}": v_clu / v_srs, f"clusters_{tag}": float(k), f"rows_per_{tag}": float(tot / k)}
    if tag == "week":
        out["coverage"] = p
        out["rows"] = float(tot)
        m = tot / k
        out["icc_week"] = (v_clu / v_srs - 1) / (m - 1) if m > 1 else math.nan
    return out


def analytic_pass(n: float, deff: float, p: float = 0.8, band: tuple[float, float] = BAND) -> float:
    """Normal approximation of P(coverage in band) for ``n`` rows with design effect ``deff``."""
    sd = math.sqrt(deff * p * (1 - p) / n)
    from math import erf

    def phi(z: float) -> float:
        return 0.5 * (1 + erf(z / math.sqrt(2)))

    return phi((band[1] - p) / sd) - phi((band[0] - p) / sd)


# --------------------------------------------------------------------------- #
# Validation-report reproduction (fast path on all 54 slates vs the printed v3 cells)
# --------------------------------------------------------------------------- #


def parse_v3_cells(text: str) -> dict[str, str]:
    """The printed v3 cells behind the 22 lines (calibrated deltas, start/sit, tie-aware coverage)."""
    import re

    cells: dict[str, str] = {}
    sec = text.split("### Differences: focus model minus each comparator", 1)[1].split("## 4.", 1)[0]
    col = {"mae": 3, "pinball_q10": 5, "pinball_q50": 6, "pinball_q90": 7}
    for block in sec.split("#### ")[1:]:
        pos = block.split("\n", 1)[0].strip()
        if pos not in ACC_POSITIONS:
            continue
        for row in block.splitlines():
            parts = [c.strip() for c in row.split("|")]
            if len(parts) < 9 or parts[2] != "calibrated":
                continue
            comp = parts[1].strip("`")
            if comp in H.LINE_BASELINES:
                for m, i in col.items():
                    cells[f"{pos}|{comp}|{m}"] = parts[i].replace(" B", "").replace(" W", "").strip()
    for pos in ACC_POSITIONS:
        m = re.search(rf"Start/sit not worse than the consensus beyond the interval, {pos}: \*\*\w+\*\* \(delta "
                      r"([^)]*)\)", text)
        if m:
            cells[f"{pos}|consensus|start_sit_common"] = m.group(1).strip()
    m = re.search(r"\| `chain_v1\+cal` \| ([^\n]*)\|\n", text.split("The same coverage rule applied to every", 1)[1])
    if m:
        vals = [v.strip().split()[0] for v in m.group(1).split("|")]
        for pos, v in zip(POSITIONS, vals, strict=False):
            cells[f"{pos}|coverage"] = v
    return cells


def format_fast_cells(stats: LineStats) -> dict[str, str]:
    out: dict[str, str] = {}
    for pi, pos in enumerate(ACC_POSITIONS):
        for ci, comp in enumerate(H.LINE_BASELINES):
            for xi, m in enumerate(METRICS):
                out[f"{pos}|{comp}|{m}"] = (f"{RB._num(stats.acc_delta[pi, ci, xi], 3, True)} "
                                            f"[{RB._num(stats.acc_lo[pi, ci, xi], 3, True)}, "
                                            f"{RB._num(stats.acc_hi[pi, ci, xi], 3, True)}]")
        out[f"{pos}|consensus|start_sit_common"] = (f"{RB._num(stats.ss_delta[pi], 3, True)} "
                                                    f"[{RB._num(stats.ss_lo[pi], 3, True)}, "
                                                    f"{RB._num(stats.ss_hi[pi], 3, True)}]")
    for p, pos in enumerate(POSITIONS):
        out[f"{pos}|coverage"] = RB._num(stats.cov[p], 3)
    return out


# --------------------------------------------------------------------------- #
# Command line: run everything and write the aggregate results (JSON) for the report
# --------------------------------------------------------------------------- #


def _jsonable(x: Any) -> Any:
    if isinstance(x, np.ndarray):
        return x.tolist()
    if isinstance(x, (np.floating, np.integer)):
        return x.item()
    if isinstance(x, dict):
        return {str(k): _jsonable(v) for k, v in x.items()}
    if isinstance(x, (list, tuple)):
        return [_jsonable(v) for v in x]
    return x


def run_all(n_rep: int = 2000, equiv_windows: int = 4, full_real: bool = False,
            out_json: Path | None = None, progress: bool = True) -> dict[str, Any]:  # pragma: no cover
    """Load 2022-2024, check the fast path, simulate every scenario and window, return aggregates."""
    t0 = time.time()
    src = load_source_frames()
    data = build_power_data(src)
    log = {"seconds_load": time.time() - t0}
    if progress:
        print(f"inputs ready in {log['seconds_load']:.0f}s: {len(data.slates)} slates", flush=True)
    out: dict[str, Any] = {"n_rep": n_rep, "sim_seed": SIM_SEED, "n_boot": N_BOOT, "boot_seed": BOOT_SEED,
                           "metric_seed": METRIC_SEED, "line_ids": list(LINE_IDS)}
    # 1. validation report cells (fast path on all 54 slates, clusters = season-weeks)
    full = line_stats(aggregate(data, full_picks(data)), "B_fixed", data)
    v3 = parse_v3_cells((PROJECT_ROOT / "reports" / "backtest_2026-11-03_v3.md").read_text(encoding="utf-8"))
    fast_cells = format_fast_cells(full)
    out["v3_cells"] = {k: {"v3": v3.get(k), "fast": fast_cells[k], "equal": v3.get(k) == fast_cells[k]}
                       for k in fast_cells}
    out["full_fast_passed"] = full.passed.tolist()
    out["full_delta"] = {"acc": full.acc_delta, "ss": full.ss_delta, "acc_hi": full.acc_hi, "ss_lo": full.ss_lo,
                         "ss_hi": full.ss_hi}
    if full_real:
        t = time.time()
        real = real_window_lines(src, full_picks(data), data, coverage="B_fixed", relabel=False)
        out["full_real"] = compare_stats(full, real)
        out["full_real"]["seconds"] = time.time() - t
        if progress:
            print(f"full-data real check: {out['full_real']}", flush=True)
    # 2. sub-windows with the real functions (B redraw, A and B_fixed coverage)
    rng = np.random.default_rng([SIM_SEED, 7])
    checks = []
    specs = [("i", "B"), ("i", "A"), ("ii", "B"), ("ii", "B_fixed"), ("i", "B_fixed")][:equiv_windows]
    for wk, cov in specs:
        picks = draw_window(data, WINDOWS[wk], rng)
        t = time.time()
        real = real_window_lines(src, picks, data, coverage=cov)
        fast = line_stats(aggregate(data, picks), {"B": "B", "A": "A", "B_fixed": "B_fixed"}[cov], data)
        c = compare_stats(fast, real)
        c.update(window=wk, coverage=cov, seconds=time.time() - t,
                 slates=[f"{p.season}w{p.week}" + ("" if p.keep is None else f"-{int((~p.keep).sum())}g")
                         for p in picks],
                 n_pass_fast=int(fast.passed.sum()), n_pass_real=int(sum(real["passed"])))
        checks.append(c)
        if progress:
            print(f"real check window {wk} coverage {cov}: max diff {c['max_abs_diff']:.2e}, "
                  f"verdicts equal {c['verdicts_equal']} ({c['seconds']:.0f}s)", flush=True)
    out["window_checks"] = checks
    # 3. simulation
    out["windows"] = {}
    for wk, window in WINDOWS.items():
        res = simulate(data, window, n_rep, progress=progress)
        w: dict[str, Any] = {"label": window.label, "weeks": list(window.weeks), "games": list(window.games),
                             "seconds": res.seconds, "scenarios": {}}
        for s in SCENARIOS:
            w["scenarios"][s.key] = summary(res, s.key)
        w["coverage_mean"] = {c: res.cov[c].mean(0) for c in res.cov}
        w["coverage_sd"] = {c: res.cov[c].std(0, ddof=1) for c in res.cov}
        w["level_pass"] = res.level_pass.mean(0)
        w["level_pass_all6"] = res.level_pass.all(2).mean(0)
        w["rows_mean"] = res.rows.mean(0)
        w["cov_rows_mean"] = res.cov_rows.mean(0)
        w["pairs_mean"] = res.pairs.mean(0)
        w["ss_weeks_mean"] = res.ss_weeks.mean(0)
        w["games_mean"] = res.games.mean()
        w["short_week_rate"] = res.short_weeks.mean(0)
        w["any_short"] = res.short_weeks.any(1).mean()
        w["drop_week_rate"] = res.drop_weeks.mean(0)
        sim_deff = {}
        for c in ("A", "B"):
            pbar = res.cov[c].mean(0)
            nbar = res.cov_rows.mean(0)
            sim_deff[c] = res.cov[c].var(0, ddof=1) / (pbar * (1 - pbar) / nbar)
        w["sim_deff"] = sim_deff
        w["scale_identical"] = {s.key: bool(np.array_equal(res.passed[s.key], res.passed["A"]))
                                for s in SCENARIOS if s.acc_mode == "scale"}
        w["cov_quantiles"] = {c: np.quantile(res.cov[c], [0.05, 0.5, 0.95], axis=0) for c in ("A", "A10", "B")}
        if res.stats_a:
            sa = res.stats_a
            w["acc_cells"] = {"delta_mean": sa["acc_delta"].mean(0), "half_width": ((sa["acc_hi"] - sa["acc_lo"]) / 2).mean(0),
                              "below0": (sa["acc_hi"] < 0).mean(0), "delta_sd": sa["acc_delta"].std(0, ddof=1)}
            w["ss_cells"] = {"delta_mean": sa["ss_delta"].mean(0), "half_width": ((sa["ss_hi"] - sa["ss_lo"]) / 2).mean(0),
                             "not_below0": (~(sa["ss_hi"] < 0)).mean(0), "delta_sd": sa["ss_delta"].std(0, ddof=1)}
        pool = line_stats(aggregate(data, [Pick(s_, w_, None) for s_ in POOL_SEASONS for w_ in window.weeks]),
                          "A", data)
        w["pool_weeks_delta"] = {"acc": pool.acc_delta, "ss": pool.ss_delta}
        out["windows"][wk] = w
        if progress:
            print(f"window {wk}: {res.seconds:.0f}s; P(all 22) A={w['scenarios']['A']['all22']:.3f} "
                  f"B={w['scenarios']['B']['all22']:.3f}", flush=True)
    out["deff"] = {c: design_effects(data, c).to_dicts() for c in ("A", "B_fixed")}
    out["pooled_delta"] = data.pooled_delta
    out["meta"] = _meta()
    out["seconds_total"] = time.time() - t0
    out = _jsonable(out)
    if out_json is not None:
        out_json.parent.mkdir(parents=True, exist_ok=True)
        out_json.write_text(json.dumps(out, indent=1), encoding="utf-8")
    return out


def _meta(run: str = "default_20261102") -> dict[str, Any]:  # pragma: no cover - reads the repository
    import hashlib
    from datetime import UTC, datetime

    from ffml.eval.harness import read_manifest, resolve_run_dir

    run_dir = resolve_run_dir(run)
    h = hashlib.sha256()
    with (run_dir / "predictions.parquet").open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            h.update(chunk)
    manifest = read_manifest(run_dir)
    return {"run": run, "predictions_sha256": h.hexdigest(), "calibration_version": manifest.get("calibration_version"),
            "git": RB._git("rev-parse", "--short", "HEAD"), "written_at": datetime.now(UTC).isoformat(timespec="seconds")}


# --------------------------------------------------------------------------- #
# Report
# --------------------------------------------------------------------------- #

REPORT_NAME = "evaluator_live_test_power_2026w10-18"
_SHOW = ("A", "A_w", "A_vc", "B", "B_fixed", "C_shift_0.75", "C_shift_0.5")


def _p(p: float, n: int, digits: int = 3) -> str:
    return f"{p:.{digits}f} ({mc_se(p, n):.{digits}f})"


def _line_label(line: H.PreregLine) -> str:
    if line.kind == "beats_baseline":
        return f"{line.position} beats `{line.comparator}+cal` (MAE, pinball q10/q50/q90)"
    if line.kind == "startsit_consensus":
        return f"{line.position} start/sit not worse than consensus"
    return f"{line.position} tie-aware coverage in [0.75, 0.85]"


def render_report(out: Mapping[str, Any]) -> str:  # noqa: C901 - one linear document
    n = int(out["n_rep"])
    W = out["windows"]
    meta = out.get("meta", {})
    L: list[str] = []
    a = L.append
    a("# Live-test pass probability for the 22 lines, 2026 weeks 10-18 (D-098 item 3)")
    a("")
    a(f"Independent Evaluator, {meta.get('written_at', '')[:10]}. Generated by `python -m ffml.eval.power` "
      f"(git `{meta.get('git', '?')}`). Purpose `backtest`, 2022-2024 rows only; no 2025 or 2026 outcome was "
      "read and `FFML_HOLDOUT_UNLOCK` was not set. The FantasyPros consensus is used in aggregate only (D-007).")
    a("")
    a("This report estimates; it does not recommend. The owner decides the test design (D-098 item 3).")
    a("")
    # ------------------------------------------------------------------ summary
    a("## Summary")
    a("")
    wi = W["i"]["scenarios"]
    a(f"Monte Carlo: {n:,} replicates per window; every scenario sees the same slates and dropped games. "
      "Values are probabilities with the Monte Carlo standard error in parentheses.")
    a("")
    rows = []
    for key in ("A", "A_w", "A_vc", "B", "C_shift_0.75", "C_shift_0.5"):
        rows.append([SCENARIO_BY_KEY[key].label] + [_p(W[w]["scenarios"][key]["all22"], n) for w in ("i", "ii", "iii")])
    a(RB._md_table(["scenario", "P(all 22), weeks 10-18", "weeks 11-18", "weeks 1-18 (reference)"], rows))
    a("")
    drivers = _drivers(wi["A"], 4)
    a(f"- **Scenario A (the owner's question), weeks 10-18:** P(all 22 pass) = {_p(wi['A']['all22'], n)}. "
      f"P(all six coverage lines) = {_p(wi['A']['cov6'], n)}; P(all 16 accuracy and start/sit lines) = "
      f"{_p(wi['A']['acc16'], n)}.")
    a("- **Lines that fail most often in A, weeks 10-18:** " + "; ".join(
        f"{lid} ({_line_label(LINES[LINE_IDS.index(lid)])}) fails {fr:.1%}" for lid, fr in drivers) + ".")
    qa = W["i"]["coverage_mean"]["A"][0]
    a(f"- **Calibrated over the test weeks (A_w), weeks 10-18:** P(all 22) = {_p(wi['A_w']['all22'], n)}; coverage "
      f"six {_p(wi['A_w']['cov6'], n)}. Scenario A is calibrated over all weeks of 2022-2024, and the chain's "
      f"coverage drifts with the week of the season, so in weeks 10-18 A's QB coverage averages {qa:.3f}; A_w "
      "ranks the PIT over weeks 10-18 only, so it averages 0.80 there.")
    a(f"- **Variance-corrected (A_vc), weeks 10-18:** P(all 22) = {_p(wi['A_vc']['all22'], n)}. Each simulated week "
      "is one of only three real slates, which understates week-to-week noise by about a third in variance "
      "(section 4, item 1); A_vc corrects for this and is the more conservative reading of A.")
    a(f"- **As validated (B), weeks 10-18:** P(all 22) = {_p(wi['B']['all22'], n)}; coverage six "
      f"{_p(wi['B']['cov6'], n)}; accuracy and start/sit sixteen {_p(wi['B']['acc16'], n)}.")
    a(f"- **Smaller edge (C, mean edge shrunk, noise kept):** P(all 22) = {_p(wi['C_shift_0.75']['all22'], n)} at "
      f"0.75x and {_p(wi['C_shift_0.5']['all22'], n)} at 0.5x. Scaling every per-row difference by k (the literal "
      "C) cannot change any verdict, because an interval scaled by a positive k keeps its sign; the simulation "
      "confirms identical verdicts in every replicate (section 3.1).")
    dA0 = {r["position"]: r for r in out["deff"]["A"]}
    a("- **Within-week correlation (coverage design effect, week clusters, scenario A, 2022-2024):** "
      + ", ".join(f"{pos} {dA0[pos]['deff_week']:.2f}" for pos in POSITIONS)
      + ". Expected rows with a PIT over weeks 10-18: "
      + ", ".join(f"{pos} {W['i']['cov_rows_mean'][p]:.0f}" for p, pos in enumerate(POSITIONS))
      + " (section 3.6).")
    a(f"- **Reference, 18 weeks with full slates:** P(all 22) = {_p(W['iii']['scenarios']['A']['all22'], n)} in A "
      f"and {_p(W['iii']['scenarios']['B']['all22'], n)} in B. Weeks 11-18: "
      f"{_p(W['ii']['scenarios']['A']['all22'], n)} in A.")
    a("")
    # ------------------------------------------------------------------ method
    a("## 1. Method")
    a("")
    a("**Pool.** The validated walk-forward run `default_20261102` (predictions sha256 "
      f"`{str(meta.get('predictions_sha256', ''))[:16]}...`, calibration `{meta.get('calibration_version', '')}`), "
      "PPR, seasons 2022-2024, as in `reports/backtest_2026-11-03_v3.md`. The focus is `chain_v1+cal`, the chain "
      "with the walk-forward (online) calibrator, which is the chain_v2 calibration method (D-098 item 2b). The "
      "comparators are the calibrated forms of `rolling_avg_4`, `prior_season` and `rolling_median_8`; the "
      "start/sit comparator is the FantasyPros consensus on the D-077 common pair set. Populations, the pair set "
      "and the randomized PIT come from the real report functions (`headline_frames`, `consensus_population`, "
      "`with_randomized_pit`, `bounds_from_run`).")
    a("")
    a("**One replicate.**")
    a("")
    a("1. For each week number w of the window, one slate is drawn uniformly from 2022, 2023 and 2024 week w. "
      "Whole slates keep the correlation within a week and within a game.")
    a("2. If the slate has more games than 2026 week w (14, 13, 16, 14, 15, 16, 16, 16, 16 for weeks 10-18), "
      "whole games are dropped uniformly at random until the counts match: every row of both teams goes, both "
      "DST rows go, and the start/sit pair set is re-ranked on the remaining players, so pairs involving dropped "
      "players go and players below the cut on the full slate can enter it. Slates with fewer games are kept "
      "(frequency in section 3.5).")
    a("3. The window is relabelled season 2026 (one bootstrap cluster per week, the live sort order) and the 22 "
      "lines are evaluated as `ffml/eval/report_holdout.py` does: 500 week-clustered paired bootstrap draws, "
      "seed 0, percentile 95% interval (D-078); L01-L12 pass when all four intervals (MAE, pinball q10, q50, "
      "q90) of chain minus comparator lie entirely below 0; L13-L16 pass unless the start/sit interval of chain "
      "minus consensus lies entirely below 0; L17-L22 pass when tie-aware coverage (randomized PIT in (0.1, 0.9)) "
      "lies in [0.75, 0.85] (D-085).")
    a("")
    a("**Scenarios.**")
    a("")
    for s in SCENARIOS:
        a(f"- `{s.key}`: {s.label}.")
    a("")
    a("Details. A: within each position each row's validation PIT u (seed 0, the v3 draws) is replaced by its "
      "rank score u' = (rank - 0.5)/n over all 2022-2024 rows of the position, so pooled coverage is 0.800 at "
      "every position (to within 1/n) and the dependence between rows (same week, same game) is kept. B: rows "
      "unchanged; the PIT is redrawn on the window exactly as the live test will draw it (one seed-0 generator "
      "over the window's rows in season, week, position, entity order). B_fixed keeps the validation draws "
      "instead. C (shift): every per-row chain-minus-comparator loss difference behind L01-L12 is moved by "
      "(1 - k) times its 2022-2024 mean at that position, so the expected edge is k times the validated edge and "
      "the week-to-week noise is unchanged; start/sit and coverage as in A. C (scale, the literal brief): every "
      "per-row difference multiplied by k. A_w: as A, but the ranks are taken over the 2022-2024 rows of weeks "
      "10-18 only, so coverage is 0.80 over the test weeks rather than over the whole season (in window (ii) and "
      "(iii) weeks outside 10-18 keep A's scores). A_vc: as A, but each simulated week's sums are moved away from the "
      "mean of their week number by the factor sqrt(3/2) (section 4, item 1).")
    a("")
    a("**Windows.** (i) weeks 10-18 with the 2026 game counts; (ii) weeks 11-18 with the 2026 game counts; "
      "(iii) weeks 1-18 with full slates, for reference.")
    a("")
    a(f"**Seeds.** Bootstrap seed {out['boot_seed']} with {out['n_boot']} draws and metric seed "
      f"{out['metric_seed']} (the pre-registered values); slate and game draws seed {out['sim_seed']}.")
    a("")
    # ------------------------------------------------------------------ equivalence
    a("## 2. The fast path reproduces the real code")
    a("")
    a("Every statistic behind the 22 lines is a ratio of sums: over rows for MAE, pinball and coverage, over "
      "pairs for start/sit. The bootstrap resamples whole weeks, so a draw is (C @ sums) / (C @ counts), where "
      "C[b, j] counts how often draw b picks week j. C is built from the same generator calls as "
      "`metrics._paired_bundle_bootstrap` (`default_rng(0).integers(0, K, (500, K))`). Per game the fast path "
      "keeps loss sums and counts per model and metric, coverage counts and PIT limits F(y-), F(y); per "
      "slate-position it keeps the consensus-population rows in D-077 reference order with each model's "
      "pair concordances. A thinned slate's pair set is \"the first N kept rows in reference order\", which is "
      "what `with_reference_rank` gives on the thinned frame.")
    a("")
    cells = out["v3_cells"]
    n_eq = sum(1 for c in cells.values() if c["equal"])
    a(f"**2.1 Validation report cells.** On all 54 slates of 2022-2024 (clusters are season-weeks, as in the "
      f"report) the fast path reproduces **{n_eq} of {len(cells)}** printed cells of "
      "`reports/backtest_2026-11-03_v3.md` behind the 22 lines at the printed precision: the 48 calibrated "
      "delta cells with their intervals (12 comparator-position pairs, 4 metrics, section 3), the 4 start/sit "
      "deltas against the consensus (section 12) and the 6 tie-aware coverages (section 12). A selection:")
    a("")
    sel = ["QB|rolling_avg_4|mae", "QB|prior_season|pinball_q10", "RB|rolling_median_8|pinball_q90",
           "WR|rolling_median_8|pinball_q10", "TE|prior_season|pinball_q50", "QB|consensus|start_sit_common",
           "WR|consensus|start_sit_common", "TE|consensus|start_sit_common", "QB|coverage", "TE|coverage",
           "DST|coverage"]
    a(RB._md_table(["cell", "v3 report", "fast path", "equal"],
                   [[k.replace("|", " / "), cells[k]["v3"], cells[k]["fast"], "yes" if cells[k]["equal"] else "NO"]
                    for k in sel if k in cells]))
    a("")
    checks = out["window_checks"]
    a(f"**2.2 Simulated windows against the real functions.** {len(checks)} simulated windows (drawn with their "
      "own seed, games dropped) were evaluated twice: with the fast path, and with the real functions on the "
      "actual rows (`with_randomized_pit` on the relabelled window, `with_reference_rank` on the thinned "
      "consensus population, `run_bootstrap` with 500 draws and seed 0, `evaluate_lines`). Every compared value "
      "is a point delta, an interval end or a coverage (162 per window: 12 lines x 4 metrics x 3, 4 lines x 3, 6).")
    a("")
    crow = []
    for c in checks:
        crow.append([c["window"], c["coverage"], ", ".join(c["slates"]), f"{c['max_abs_diff']:.1e}",
                     "yes" if c["verdicts_equal"] else "NO", f"{c['n_pass_fast']} / {c['n_pass_real']}",
                     f"{c['seconds']:.0f}"])
    a(RB._md_table(["window", "coverage", "slates (-Ng: N games dropped)", "max abs difference", "22 verdicts equal",
                    "lines passed fast / real", "real-path seconds"], crow))
    a("")
    if "full_real" in out:
        fr = out["full_real"]
        a(f"All 54 slates with the real functions: max abs difference {fr['max_abs_diff']:.1e}, verdicts equal: "
          f"{'yes' if fr['verdicts_equal'] else 'NO'}.")
        a("")
    a("`tests/eval/test_power.py` repeats the comparison on synthetic slates (thinned windows, re-ranked pair "
      "sets, ties in projections and outcomes, a week without consensus rows, tied PIT knots) with tolerance "
      "1e-9, and checks the bootstrap count matrix, the PIT stream prefix, scenario A's calibration and the C "
      "invariance.")
    a("")
    # ------------------------------------------------------------------ results
    a("## 3. Results")
    a("")
    a("### 3.1 Joint pass probabilities, every scenario and window")
    a("")
    for wk in ("i", "ii", "iii"):
        w = W[wk]
        a(f"**Window ({wk}): {w['label']}.**")
        a("")
        rr = []
        for s in SCENARIOS:
            sm = w["scenarios"][s.key]
            rr.append([f"`{s.key}`", _p(sm["all22"], n), _p(sm["cov6"], n), _p(sm["acc16"], n), _p(sm["acc12"], n),
                       _p(sm["ss4"], n), f"{sm['mean_failed_lines']:.2f}"])
        a(RB._md_table(["scenario", "all 22", "6 coverage", "16 accuracy + start/sit", "12 accuracy", "4 start/sit",
                        "mean lines failed"], rr))
        ident = w.get("scale_identical", {})
        if ident:
            a("")
            a("Literal C (scale): verdicts identical to A in every replicate: " + ", ".join(
                f"`{k}` {'yes' if v else 'NO'}" for k, v in ident.items()) + ".")
        a("")
    a("### 3.2 Per-line pass probabilities")
    a("")
    a("Monte Carlo standard error of a line probability p is sqrt(p (1 - p) / n), at most "
      f"{mc_se(0.5, n):.3f}.")
    a("")
    for wk in ("i", "ii", "iii"):
        w = W[wk]
        a(f"**Window ({wk}): {w['label']}.**")
        a("")
        rr = []
        for i, line in enumerate(LINES):
            rr.append([line.id, _line_label(line)] + [f"{w['scenarios'][k]['per_line'][i]:.3f}" for k in _SHOW])
        a(RB._md_table(["line", "statement", *[f"`{k}`" for k in _SHOW]], rr))
        a("")
    a("### 3.3 What drives the failures")
    a("")
    a("For each line: how often it fails, how often it is the only failing line, and how much P(all 22) would "
      "rise if that line could not fail (the other 21 unchanged). The last column is descriptive, not a "
      "proposal.")
    a("")
    for wk, key in (("i", "A"), ("i", "B"), ("i", "C_shift_0.5")):
        sm = W[wk]["scenarios"][key]
        order = np.argsort(-np.array(sm["fail_rate"]))
        rr = [[LINE_IDS[i], _line_label(LINES[i]), f"{sm['fail_rate'][i]:.3f}", f"{sm['sole_failure'][i]:.3f}",
               f"+{sm['lift_if_waived'][i]:.3f}"] for i in order if sm["fail_rate"][i] > 0.005]
        a(f"**Window ({wk}), scenario `{key}`** (lines failing in more than 0.5% of replicates; P(all 22) = "
          f"{sm['all22']:.3f}):")
        a("")
        a(RB._md_table(["line", "statement", "fails", "only failing line", "P(all 22) rise if it could not fail"], rr))
        a("")
    a("Line groups: the rise in P(all 22) if every line of the group could not fail (descriptive):")
    a("")
    rr = []
    for g in LINE_GROUPS:
        rr.append([g] + [f"+{W[wk]['scenarios'][k]['group_lift'][g]:.3f}" for wk, k in
                         (("i", "A"), ("i", "B"), ("ii", "A"), ("iii", "A"))])
    a(RB._md_table(["group", "(i) A", "(i) B", "(ii) A", "(iii) A"], rr))
    a("")
    a("**Cell by cell, scenario A, window (i).** For each accuracy cell: the chain-minus-comparator delta on all "
      "54 weeks of 2022-2024 (the validation report), on the 27 pool slates of weeks 10-18, its mean and spread "
      "over simulated windows, the mean half-width of the window's 95% interval, and how often the interval lies "
      "entirely below 0. A line passes only when all four of its cells do.")
    a("")
    cells_w = W["i"].get("acc_cells")
    fd = out.get("full_delta")
    if cells_w and fd:
        rr = []
        for line in LINES:
            if line.kind != "beats_baseline":
                continue
            pi, ci = ACC_POSITIONS.index(line.position), H.LINE_BASELINES.index(line.comparator)
            for xi, m in enumerate(METRICS):
                rr.append([line.id, line.position, f"`{line.comparator}`", RB.METRIC_LABEL[m],
                           RB._num(fd["acc"][pi][ci][xi], 3, True),
                           RB._num(W["i"]["pool_weeks_delta"]["acc"][pi][ci][xi], 3, True),
                           f"{RB._num(cells_w['delta_mean'][pi][ci][xi], 3, True)} ({cells_w['delta_sd'][pi][ci][xi]:.3f})",
                           f"{cells_w['half_width'][pi][ci][xi]:.3f}", f"{cells_w['below0'][pi][ci][xi]:.3f}"])
        a(RB._md_table(["line", "position", "comparator", "metric", "delta, 2022-2024 all weeks",
                        "delta, weeks 10-18 pool", "window delta: mean (sd)", "window interval half-width",
                        "P(interval below 0)"], rr))
        a("")
        ssw = W["i"]["ss_cells"]
        rr = []
        for pi, pos in enumerate(ACC_POSITIONS):
            rr.append([LINE_IDS[12 + pi], pos, RB._num(fd["ss"][pi], 3, True),
                       RB._num(W["i"]["pool_weeks_delta"]["ss"][pi], 3, True),
                       f"{RB._num(ssw['delta_mean'][pi], 3, True)} ({ssw['delta_sd'][pi]:.3f})",
                       f"{ssw['half_width'][pi]:.3f}", f"{ssw['not_below0'][pi]:.3f}"])
        a("Start/sit (chain minus consensus; the line passes unless the interval lies entirely below 0):")
        a("")
        a(RB._md_table(["line", "position", "delta, 2022-2024", "delta, weeks 10-17 pool (no week 18 consensus)",
                        "window delta: mean (sd)", "window interval half-width", "P(not entirely below 0)"], rr))
        a("")
    a("### 3.4 Coverage lines against the true coverage level")
    a("")
    a("Scenario A_w with the interval (a, 1 - a), a = (1 - c)/2, so the true coverage over 2022-2024 weeks 10-18 "
      "is c at every position and the dependence between rows is kept. P(coverage line passes):")
    a("")
    for wk in ("i", "ii"):
        w = W[wk]
        rr = []
        for li, c in enumerate(COVERAGE_LEVELS):
            rr.append([f"{c:.2f}"] + [f"{w['level_pass'][li][p]:.3f}" for p in range(len(POSITIONS))]
                      + [f"{w['level_pass_all6'][li]:.3f}"])
        a(f"**Window ({wk}).**")
        a("")
        a(RB._md_table(["true coverage", *POSITIONS, "all six"], rr))
        a("")
    a("### 3.5 Expected rows, pairs and slates over the window")
    a("")
    rr = []
    for wk in ("i", "ii", "iii"):
        w = W[wk]
        for p, pos in enumerate(POSITIONS):
            pi = ACC_POSITIONS.index(pos) if pos in ACC_POSITIONS else None
            rr.append([wk, pos, f"{w['rows_mean'][p]:.0f}", f"{w['cov_rows_mean'][p]:.0f}",
                       f"{w['pairs_mean'][pi]:.0f}" if pi is not None else "n/a",
                       f"{w['ss_weeks_mean'][pi]:.2f}" if pi is not None else "n/a"])
    a(RB._md_table(["window", "position", "rows (headline population)", "rows with a PIT (coverage)",
                    "start/sit pairs (common set)", "weeks with consensus rows"], rr))
    a("")
    for wk in ("i", "ii"):
        w = W[wk]
        weeks = w["weeks"]
        a(f"Window ({wk}): {w['games_mean']:.1f} games on average (2026 schedule: "
          f"{sum(g for g in w['games'] if g)}). Share of replicates in which a week's slate had fewer games than "
          "2026 (kept as is) / had games dropped: " + "; ".join(
              f"w{wk_}: {s:.2f} / {d:.2f}" for wk_, s, d in zip(weeks, w["short_week_rate"], w["drop_week_rate"],
                                                                strict=True)) +
          f". At least one short week: {w['any_short']:.3f}.")
        a("")
    a("Week 18 has no consensus rows in 2022, 2023 or 2024 (and week 1 none, 2024 weeks 2-3 none), so the "
      "start/sit lines use the remaining weeks' clusters, exactly as the real code would on such a window.")
    a("")
    a("### 3.6 Within-week correlation: design effect of coverage")
    a("")
    a("The design effect is the variance of pooled coverage over the variance it would have with independent "
      "rows, deff = V_cluster / (p (1 - p) / n), with the ratio-estimator cluster variance "
      "K/(K - 1) sum_k (y_k - p n_k)^2 / n^2 over the 54 weeks (or about 800 games) of 2022-2024. A design "
      "effect d means the window's n rows carry the information of n/d independent rows.")
    a("")
    dA = {r["position"]: r for r in out["deff"]["A"]}
    dB = {r["position"]: r for r in out["deff"]["B_fixed"]}
    w = W["i"]
    rr = []
    for p, pos in enumerate(POSITIONS):
        nrow = w["cov_rows_mean"][p]
        rr.append([pos, f"{dA[pos]['rows_per_week']:.1f}", f"{dA[pos]['deff_week']:.2f}", f"{dA[pos]['icc_week']:.4f}",
                   f"{dA[pos]['deff_game']:.2f}", f"{dB[pos]['deff_week']:.2f}",
                   f"{w['sim_deff']['A'][p]:.2f}", f"{w['sim_deff']['B'][p]:.2f}",
                   f"{analytic_pass(nrow, 1.0):.3f}", f"{analytic_pass(nrow, dA[pos]['deff_week']):.3f}",
                   f"{w['scenarios']['A']['per_line'][16 + p]:.3f}", f"{w['scenarios']['A_w']['per_line'][16 + p]:.3f}",
                   f"{w['scenarios']['A_vc']['per_line'][16 + p]:.3f}"])
    a(RB._md_table(["position", "rows per week", "deff, week clusters (A)", "ICC within week (A)",
                    "deff, game clusters (A)", "deff, week clusters (B)", "simulated deff, window (i), A",
                    "simulated deff, window (i), B", "normal P(pass), independent rows, window (i) n",
                    "normal P(pass), with week deff", "simulated P(pass), A", "simulated P(pass), A_w",
                    "simulated P(pass), A_vc"], rr))
    a("")
    a("The simulated design effect is the variance of window coverage across replicates over the binomial "
      "variance at the replicates' mean rows; it is smaller than the 54-week estimate partly because each week "
      "is drawn from only three slates (section 4, item 1). The normal columns use p = 0.80 and the window's "
      "expected rows; they differ from the simulated A column mainly because A is centred on the whole-season "
      "coverage, not on 0.80 in weeks 10-18 (A_w is), and from A_w because the simulated spread is narrower "
      "than the binomial one (A_vc widens it).")
    a("")
    a("Coverage over window (i) across replicates (5th, 50th, 95th percentiles):")
    a("")
    rr = []
    for p, pos in enumerate(POSITIONS):
        qa = [w["cov_quantiles"]["A"][i][p] for i in range(3)]
        qb = [w["cov_quantiles"]["B"][i][p] for i in range(3)]
        qw = [w["cov_quantiles"]["A10"][i][p] for i in range(3)]
        rr.append([pos, " / ".join(f"{x:.3f}" for x in qa), f"{w['coverage_sd']['A'][p]:.4f}",
                   " / ".join(f"{x:.3f}" for x in qw), " / ".join(f"{x:.3f}" for x in qb),
                   f"{w['coverage_mean']['B'][p]:.3f}"])
    a(RB._md_table(["position", "A: 5% / 50% / 95%", "A: sd", "A_w: 5% / 50% / 95%", "B: 5% / 50% / 95%",
                    "B: mean"], rr))
    a("")
    # ------------------------------------------------------------------ assumptions
    a("## 4. Assumptions and approximations")
    a("")
    items = [
        "**Three slates per week number.** Each simulated week is one of only three real slates. The spread of a "
        "uniform pick among three values is the plug-in variance, which is 2/3 of the true slate-to-slate "
        "variance in expectation, so window-level noise is understated. `A_vc` stretches each simulated week's "
        "deviation from its week-number mean by sqrt(3/2) to undo this in expectation; compare A and A_vc for "
        "the size of the effect.",
        "**Season-level shifts are not simulated.** Weeks are drawn independently across 2022-2024, so a "
        "shift shared by every week of 2026 (as in 2025, when QB coverage of the frozen chain fell from 0.779 in "
        "validation to 0.739) is not represented; section 3.4 shows the coverage lines' pass probability as a "
        "function of the true coverage level instead.",
        "**chain_v1+cal stands in for chain_v2.** chain_v2 adds 2025 to training and uses the walk-forward "
        "calibrator; chain_v1+cal is that calibrator on chain_v1 (D-098 item 2b). The live test's calibrator "
        "starts from 2025 weeks rather than 2021, which is not simulated.",
        "**Scenario A keeps the validation PIT draws.** u' is a fixed function of each row's seed-0 validation "
        "PIT, as the brief defines it; it is not redrawn per window. Scenario B redraws exactly as the live test "
        "will; B_fixed shows that the redraw changes little.",
        "**The pair set.** The D-077 reference is the mean raw pred_q50 of the models in the validation run's "
        "primary configuration (chain_v1, direct_lgbm, the five baselines) plus the consensus. If the live test "
        "compares a different model set, its pair set differs slightly. Thinning re-ranks the pair set on the "
        "kept players, exactly as the real code would on a 2026 slate with fewer games.",
        "**Consensus availability.** The consensus population lacks players whose game precedes the Friday "
        "scrape (Thursday games), as it will in 2026, and the archive has no week 18 scrape in 2022-2024; the "
        "simulation keeps both. If 2026 week 18 has a consensus scrape, the start/sit lines get one more week.",
        "**Game counts.** Slates with fewer games than 2026 are kept, so window (i) has slightly fewer games on "
        "average than the 136 scheduled (section 3.5); dropped games are chosen uniformly (not by kickoff slot).",
        "**PIT bounds.** The run's own prior-season position bounds (`pos_min` / `pos_max`, identical in "
        "2022-2024) are used, as `report_holdout` does; they reproduce the v3 coverages exactly.",
        "**Literal C is invariant.** Multiplying every per-row difference by k > 0 multiplies the point delta and "
        "every bootstrap difference by k, so an interval entirely below 0 stays entirely below 0. The shift form "
        "is reported as the operative accuracy sensitivity; both are shown.",
        "**Data read.** Only 2022-2024 PPR rows of `default_20261102` and the consensus for 2022-2024 "
        "(`consensus_predictions(purpose='backtest')`, which, as in the v3 report, fits its rank-to-points curve "
        "on earlier archive seasons up to 2024). Nothing from 2025, 2026, `data/backtests/holdout_2025` or "
        "`data/backtests/forward_2026` was read.",
    ]
    for i, t in enumerate(items, start=1):
        a(f"{i}. {t}")
    a("")
    a("## 5. Reproduction")
    a("")
    flags = f"--reps {n} --equiv-windows {len(out['window_checks'])}" + (" --full-real" if "full_real" in out else "")
    a(f"`python -m ffml.eval.power {flags}` (about {out.get('seconds_total', 0) / 60:.0f} minutes, "
      "one process) writes the aggregates to `data/reports/live_test_power.json` (gitignored) and this report. "
      "Run time per window: " + ", ".join(f"({k}) {W[k]['seconds']:.0f}s" for k in W) + ".")
    a("")
    return "\n".join(L) + "\n"


def _drivers(sm: Mapping[str, Any], k: int) -> list[tuple[str, float]]:
    fr = np.array(sm["fail_rate"])
    order = np.argsort(-fr)[:k]
    return [(LINE_IDS[i], float(fr[i])) for i in order if fr[i] > 0]


def main(argv: list[str] | None = None) -> int:  # pragma: no cover
    ensure_utf8()
    ap = argparse.ArgumentParser(description=__doc__.split("\n", 1)[0])
    ap.add_argument("--reps", type=int, default=2000)
    ap.add_argument("--equiv-windows", type=int, default=4)
    ap.add_argument("--full-real", action="store_true", help="also check all 54 weeks with the real functions")
    ap.add_argument("--out", default=str(PROJECT_ROOT / "data" / "reports" / "live_test_power.json"))
    ap.add_argument("--render-only", action="store_true", help="re-render the report from an existing --out file")
    ap.add_argument("--report", default=str(PROJECT_ROOT / "reports" / f"{REPORT_NAME}.md"))
    args = ap.parse_args(argv)
    if args.render_only:
        out = json.loads(Path(args.out).read_text(encoding="utf-8"))
    else:
        out = run_all(args.reps, args.equiv_windows, args.full_real, Path(args.out))
    Path(args.report).write_text(render_report(out), encoding="utf-8")
    print(f"wrote {args.report}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(main())
