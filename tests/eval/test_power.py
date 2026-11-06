"""Fast-path equivalence for the live-test pass-probability simulation (ffml/eval/power.py).

Synthetic frames only (no real or restricted rows). The fast path (per-game
sums, bootstrap count matrices, re-ranked start/sit pairs, redrawn PIT) must
give the same 22-line statistics as the real report functions
(``run_bootstrap``, ``with_randomized_pit``, ``with_reference_rank``,
``evaluate_lines``) on windows with dropped games.
"""

from __future__ import annotations

import math

import numpy as np
import polars as pl
import pytest

from ffml.eval import metrics as M
from ffml.eval import power as PW
from ffml.eval import report_backtest as RB

TEAMS = ("AAA", "BBB", "CCC", "DDD", "EEE", "FFF", "GGG", "HHH")
ROSTER = {"QB": 3, "RB": 3, "WR": 4, "TE": 3, "K": 1, "DST": 1}
WEEKS = (1, 2, 3)
N_BOOT = 30
Z = {0.05: -1.645, 0.10: -1.2816, 0.25: -0.6745, 0.50: 0.0, 0.75: 0.6745, 0.90: 1.2816, 0.95: 1.645}


def _truth(seed: int = 0) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    rows = []
    for season in PW.POOL_SEASONS:
        for week in WEEKS:
            order = rng.permutation(TEAMS)
            for gi in range(len(TEAMS) // 2):
                home, away = order[2 * gi], order[2 * gi + 1]
                gid = f"{season}_{week:02d}_{away}_{home}"
                shock = rng.normal(0.0, 2.0)  # a game-level shock: rows of one game are correlated
                for team in (home, away):
                    for pos, n in ROSTER.items():
                        for i in range(n):
                            eid = team if pos == "DST" else f"{team}-{pos}{i}"
                            mu = 3.0 + 12.0 * (n - i) / n + (3.0 if pos == "QB" else 0.0)
                            active = pos == "DST" or bool(rng.random() > 0.12)
                            # integer actuals give ties in actual points (start/sit 0.5) and at knots
                            actual = float(round(max(0.0, rng.normal(mu + shock, 5.0)))) if active else 0.0
                            rows.append({"entity_id": eid, "position": pos, "season": season, "week": week,
                                         "team": team, "game_id": gid, "mu": mu, "actual": actual,
                                         "active": active})
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


def _model(base: pl.DataFrame, name: str, noise: float, width: float, seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    # q50 on a 0.5 grid: true ties in the model's own ordering
    q50 = np.round((base["mu"].to_numpy() + rng.normal(0.0, noise, base.height)) * 2) / 2
    knots = {f"pred_q{int(round(q * 100)):02d}": np.maximum(0.0, q50 + z * width) for q, z in Z.items()}
    return base.drop("mu").with_columns(
        pl.lit(name).alias("model"), pl.Series("pred_mean", q50 + 0.2),
        *[pl.Series(c, v) for c, v in knots.items()],
        pl.lit(-4.0).alias("pos_min"), pl.lit(60.0).alias("pos_max"),
    )


def _ppr() -> pl.DataFrame:
    base = _truth()
    specs = [("chain_v1", 2.0, 4.0, 1), ("rolling_avg_4", 4.0, 5.0, 2), ("prior_season", 4.5, 5.5, 3),
             ("rolling_median_8", 3.5, 5.0, 4), ("direct_lgbm", 2.5, 4.5, 5)]
    frames = []
    for name, noise, width, seed in specs:
        frames.append(_model(base, name, noise, width * 1.2, seed))
        frames.append(_model(base, name + RB.CAL, noise, width, seed))
    return pl.concat(frames, how="vertical_relaxed")


def _consensus() -> pl.DataFrame:
    base = _truth()
    cons = _model(base, RB.CONSENSUS, 2.2, 4.0, 9).select(
        "entity_id", "position", "season", "week", "team", "game_id", "pred_mean", "pred_q10", "pred_q50",
        "pred_q90", "actual", "active", "model")
    cons = cons.with_columns(
        pl.col("pred_q50").rank("ordinal", descending=True).over(["position", "season", "week"])
        .cast(pl.Float64).alias("rank_pred"))
    # the first game of each week is an "early game" (no consensus row); 2023 week 3 has no scrape at all
    first = cons.group_by("season", "week").agg(pl.col("game_id").min().alias("first"))
    cons = cons.join(first, on=["season", "week"]).filter(pl.col("game_id") != pl.col("first")).drop("first")
    return cons.filter(~((pl.col("season") == 2023) & (pl.col("week") == 3)))


@pytest.fixture(scope="module")
def built():
    src = PW.source_frames(_ppr(), _consensus())
    return src, PW.build_power_data(src, max_rows=20_000)


TEST_WINDOW = PW.Window("t", "test", WEEKS, (3, None, 2))


def _picks(data, seed):
    return PW.draw_window(data, TEST_WINDOW, np.random.default_rng(seed))


def test_rng_prefix_matches_the_live_draw():
    """The live test draws rng(0).random(n) once; a prefix of a longer stream is the same numbers."""
    long = np.random.default_rng(0).random(5000)
    for n in (1, 17, 999, 5000):
        assert np.array_equal(np.random.default_rng(0).random(n), long[:n])


def test_boot_counts_match_the_paired_bootstrap_draws():
    draws = np.random.default_rng(0).integers(0, 9, size=(500, 9))
    counts = PW.boot_counts(9)
    assert counts.shape == (500, 9) and np.array_equal(counts.sum(1), np.full(500, 9))
    assert np.array_equal(counts[3], np.bincount(draws[3], minlength=9))


def test_windows_and_lines():
    assert PW.WINDOWS["i"].weeks == tuple(range(10, 19)) and sum(PW.WINDOWS["i"].games) == 136
    assert PW.WINDOWS["ii"].weeks == tuple(range(11, 19)) and PW.WINDOWS["iii"].games == (None,) * 18
    assert len(PW.LINES) == 22 and PW.LINE_IDS[0] == "L01" and PW.LINE_IDS[-1] == "L22"


def test_draw_window_drops_whole_games_to_the_target(built):
    _, data = built
    for seed in range(5):
        picks = _picks(data, seed)
        assert picks[0].keep is not None and int(picks[0].keep.sum()) == 3
        assert picks[1].keep is None
        assert picks[2].keep is not None and int(picks[2].keep.sum()) == 2
        agg = PW.aggregate(data, picks)
        assert list(agg.games) == [3, 4, 2]
        # a DST row per team: 2 per kept game
        dst = PW.POSITIONS.index("DST")
        assert list(agg.main_rows[:, dst]) == [6, 8, 4]


@pytest.mark.parametrize("coverage", ["B", "A", "B_fixed"])
@pytest.mark.parametrize("seed", [0, 1])
def test_fast_path_equals_real_functions_on_thinned_windows(built, coverage, seed):
    src, data = built
    picks = _picks(data, seed)
    real = PW.real_window_lines(src, picks, data, coverage=coverage, n_boot=N_BOOT)
    fast = PW.line_stats(PW.aggregate(data, picks), coverage, data, n_boot=N_BOOT)
    cmp = PW.compare_stats(fast, real)
    assert cmp["max_abs_diff"] < 1e-9, cmp
    assert cmp["verdicts_equal"]


def test_fast_path_equals_real_functions_on_all_slates(built):
    """Clusters are season-weeks when the rows keep their seasons (the validation-report setting)."""
    src, data = built
    picks = PW.full_picks(data)
    real = PW.real_window_lines(src, picks, data, coverage="B_fixed", n_boot=N_BOOT, relabel=False)
    fast = PW.line_stats(PW.aggregate(data, picks), "B_fixed", data, n_boot=N_BOOT)
    assert PW.compare_stats(fast, real)["max_abs_diff"] < 1e-9


def test_dropping_games_reranks_the_common_pair_set(built):
    """With fewer games, players ranked below the cut on the full slate enter the startable set."""
    _, data = built
    slate = data.slates[(2022, 1)]
    group = slate.ss["QB"]  # 12 startable of 18 consensus-population QBs (3 games x 2 teams x 3)
    keep = np.ones(slate.n_games, dtype=bool)
    keep[group.game[0]] = False  # drop the game of the top-ranked QB
    rows = np.flatnonzero(keep[group.game])[: group.cut]
    assert rows.max() >= group.cut  # a row outside the full-slate top 12 is now startable
    conc, pairs = PW._ss_sums(group, keep)
    m = int(group.in_pop[rows].sum())
    assert pairs == m * (m - 1) / 2 and 0 <= conc[PW.FOCUS_CAL] <= pairs


def test_scenario_a_is_calibrated_on_the_pool(built):
    _, data = built
    agg = PW.aggregate(data, PW.full_picks(data))
    cov = agg.cov["A"].sum(0) / agg.cov_n.sum(0)
    n = agg.cov_n.sum(0)
    # ranks r with 0.1 < (r - 0.5) / n < 0.9
    expected = np.array([sum(1 for r in range(1, int(k) + 1) if 0.1 < (r - 0.5) / k < 0.9) / k for k in n])
    assert np.allclose(cov, expected, atol=1e-12)


def test_scale_scenario_cannot_change_a_verdict_and_shift_with_k1_is_a(built):
    _, data = built
    for seed in range(4):
        agg = PW.aggregate(data, _picks(data, seed))
        a = PW.line_stats(agg, "A", data, n_boot=N_BOOT)
        for k in (0.75, 0.5):
            sc = PW.Scenario("s", "s", "A", "scale", k)
            s = PW.line_stats(agg, sc, data, n_boot=N_BOOT)
            assert np.array_equal(s.passed, a.passed)
            assert np.allclose(s.acc_hi, k * a.acc_hi)
        one = PW.line_stats(agg, PW.Scenario("s", "s", "A", "shift", 1.0), data, n_boot=N_BOOT)
        assert np.array_equal(one.acc_hi, a.acc_hi)
        half = PW.line_stats(agg, PW.Scenario("s", "s", "A", "shift", 0.5), data, n_boot=N_BOOT)
        assert np.allclose(half.acc_hi - a.acc_hi, -0.5 * data.pooled_delta)


def test_stretch_one_is_identity_and_keeps_week_means(built):
    _, data = built
    agg = PW.aggregate(data, _picks(data, 0))
    a = PW.line_stats(agg, "A", data, n_boot=N_BOOT)
    s1 = PW.line_stats(agg, PW.Scenario("s", "s", "A", stretch=1.0), data, n_boot=N_BOOT)
    assert np.array_equal(a.acc_hi, s1.acc_hi)
    # stretching a full slate whose ratio equals the week-number mean leaves it unchanged
    w = 2
    picks = [PW.Pick(s, w, None) for s in PW.POOL_SEASONS]
    full = PW.aggregate(data, picks)
    st = PW._stretch(full, data, 2.0)
    assert np.allclose(st.loss_sum.sum(0), full.loss_sum.sum(0))


def test_design_effect_formula():
    # identical cluster proportions: no between-cluster variance
    out = PW._deff(np.array([8.0, 16.0, 4.0]), np.array([10.0, 20.0, 5.0]), "week")
    assert out["deff_week"] == pytest.approx(0.0, abs=1e-12) and out["coverage"] == pytest.approx(0.8)
    # binomial-like spread gives a design effect near 1
    rng = np.random.default_rng(0)
    n = np.full(400, 50.0)
    y = rng.binomial(50, 0.8, 400).astype(float)
    assert 0.8 < PW._deff(y, n, "week")["deff_week"] < 1.2


def test_analytic_pass_is_monotone_in_rows_and_deff():
    assert PW.analytic_pass(300, 1.0) < PW.analytic_pass(600, 1.0)
    assert PW.analytic_pass(300, 2.0) < PW.analytic_pass(300, 1.0)
    assert math.isclose(PW.analytic_pass(1e9, 1.0), 1.0)


def test_parse_v3_cells_reads_the_printed_cells():
    text = (
        "### Differences: focus model minus each comparator [x]\n\n#### QB\n\n"
        "| comparator | form | MAE | MAE (mean) | pinball q10 | pinball q50 | pinball q90 | a | b | c |\n"
        "| `rolling_avg_4` | calibrated | -0.573 [-0.786, -0.402] B | x | -0.368 [-0.446, -0.300] B | "
        "-0.287 [-0.393, -0.201] B | -0.268 [-0.342, -0.199] B | y | z | w |\n\n## 4. By week\n"
        "- Start/sit not worse than the consensus beyond the interval, QB: **PASS** (delta +0.012 [-0.012, +0.037])\n"
        "The same coverage rule applied to every calibrated model\n\n| model | QB | RB | WR | TE | K | DST |\n"
        "| `chain_v1+cal` | 0.779 PASS | 0.785 PASS | 0.786 PASS | 0.793 PASS | 0.807 PASS | 0.820 PASS |\n"
    )
    cells = PW.parse_v3_cells(text)
    assert cells["QB|rolling_avg_4|mae"] == "-0.573 [-0.786, -0.402]"
    assert cells["QB|rolling_avg_4|pinball_q90"] == "-0.268 [-0.342, -0.199]"
    assert cells["QB|consensus|start_sit_common"] == "+0.012 [-0.012, +0.037]"
    assert cells["DST|coverage"] == "0.820" and cells["QB|coverage"] == "0.779"


def test_simulate_shapes_and_summary(built):
    _, data = built
    res = PW.simulate(data, TEST_WINDOW, 6, scenarios=(PW.SCENARIO_BY_KEY["A"], PW.SCENARIO_BY_KEY["B"]))
    assert res.passed["A"].shape == (6, 22) and res.drop_weeks[:, 0].all() and not res.drop_weeks[:, 1].any()
    s = PW.summary(res, "A")
    assert 0.0 <= s["all22"] <= s["cov6"] <= 1.0 and s["all22"] <= s["acc16"]
    assert len(s["per_line"]) == 22 and s["lift_if_waived"].min() >= 0.0
    assert M.RAND_PIT == "pit_rand"


# --------------------------------------------------------------------------- #
# D-105 candidate rule sets: pooled lines, the no-edge null, rule evaluation, selection
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("seed", [0, 1])
def test_pooled_fast_path_equals_real_bundle_on_thinned_windows(built, seed):
    src, data = built
    picks = _picks(data, seed)
    real = PW.real_pooled_lines(src, picks, data, n_boot=N_BOOT)
    fast = PW.line_stats(PW.aggregate(data, picks), "A", data, n_boot=N_BOOT)
    assert PW.compare_pooled(fast, real) < 1e-9


def test_rulesets_sizes_and_r0_matches_the_22_lines(built):
    _, data = built
    sizes = {k: len(v) for k, v in PW.RULESETS.items()}
    assert sizes == {"R0": 22, "R1": 28, "R2": 31, "R3": 34}
    assert [ln.id for ln in PW.RULESETS["R0"]] == list(PW.LINE_IDS)
    for seed in range(3):
        st = PW.line_stats(PW.aggregate(data, _picks(data, seed)), "A", data, n_boot=N_BOOT)
        assert np.array_equal(PW.evaluate_rules(st, PW.RULESETS["R0"]), st.passed)
        # beats implies not worse: R1's per-position lines pass whenever R0's do
        r1 = PW.evaluate_rules(st, PW.RULESETS["R1"])
        assert np.all(r1[:12] | ~st.passed[:12])


def test_null_shift_centres_every_week_number(built):
    _, data = built
    assert PW.null_residual(data) < 1e-9
    agg = PW.aggregate(data, PW.full_picks(data))
    st = PW.line_stats(agg, "N0", data, n_boot=N_BOOT)
    # on the whole pool every point delta is 0: per position, pooled and start/sit
    assert np.abs(st.acc_delta).max() < 1e-9 and np.abs(st.pool_delta).max() < 1e-9
    assert np.nanmax(np.abs(st.ss_delta)) < 1e-9
    a = PW.line_stats(agg, "A", data, n_boot=N_BOOT)
    assert np.array_equal(st.cov, a.cov)  # coverage is scenario A's
    sv = PW.line_stats(agg, "N0_vc", data, n_boot=N_BOOT)
    assert np.abs(sv.acc_delta).max() < 1e-9  # the stretch keeps the centring


def test_one_miss_window_scores_one_week_fewer(built):
    _, data = built
    w = PW.Window("t8", "t", WEEKS, (3, None, 2), drop_one=True)
    for seed in range(5):
        picks = PW.draw_window(data, w, np.random.default_rng(seed))
        assert len(picks) == len(WEEKS) - 1 and len({p.week for p in picks}) == len(picks)


def test_select_d105_limits_and_tie_break():
    rows = [
        {"key": "R0", "n_lines": 22, "fa": 0.001, "fa_se": 0.0004, "good": 0.04, "good_se": 0.003},
        {"key": "R1", "n_lines": 28, "fa": 0.030, "fa_se": 0.0024, "good": 0.80, "good_se": 0.006},
        {"key": "R2", "n_lines": 31, "fa": 0.028, "fa_se": 0.0023, "good": 0.75, "good_se": 0.006},
        {"key": "R3", "n_lines": 34, "fa": 0.010, "fa_se": 0.0014, "good": 0.60, "good_se": 0.007},
    ]
    sel = PW.select_d105(rows)
    assert sel["qualifying"] == ["R1", "R2"] and sel["close"] == ["R1", "R2"] and sel["pick"] == "R1"
    rows[2]["fa"] = 0.010
    assert PW.select_d105(rows)["pick"] == "R2"
    assert PW.select_d105(rows[:1])["pick"] is None


def test_simulate_rules_shapes(built):
    _, data = built
    res = PW.simulate_rules(data, TEST_WINDOW, 4)
    assert res.passed[("A", "R1")].shape == (4, 28) and res.passed[("N0_vc", "R3")].shape == (4, 34)
    assert (res.weeks == 3).all()


def test_drop_n_windows_exclude_distinct_weeks(built):
    """drop_n (1476ac6): n weeks of the window, drawn without replacement, are excluded; drop_one still means 1."""
    _, data = built
    base = PW.Window("t", "t", WEEKS, (3, None, 2))
    for n in (0, 1, 2):
        w = PW.Window("tn", "t", WEEKS, (3, None, 2), drop_n=n)
        for seed in range(6):
            picks = PW.draw_window(data, w, np.random.default_rng(seed))
            assert len(picks) == len(WEEKS) - n and len({p.week for p in picks}) == len(picks)
    one = PW.Window("t1", "t", WEEKS, (3, None, 2), drop_one=True)
    assert all(len(PW.draw_window(data, one, np.random.default_rng(s))) == len(WEEKS) - 1 for s in range(4))
    # the slate and game draws before the drop are the same stream as the full window
    full = PW.draw_window(data, base, np.random.default_rng(3))
    dropped = PW.draw_window(data, PW.Window("t2", "t", WEEKS, (3, None, 2), drop_n=2), np.random.default_rng(3))
    assert all(any(p.season == q.season and p.week == q.week for q in full) for p in dropped)
    assert set(PW.MISS_WINDOWS) == {"ext18", "ext18_one_miss", "ext18_two_miss"}
    assert PW.MISS_WINDOWS["ext18_two_miss"].drop_n == 2 and PW.WINDOW_SEED_INDEX["ext18_two_miss"] == 6

