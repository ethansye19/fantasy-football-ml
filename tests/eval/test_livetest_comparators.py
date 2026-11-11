"""The live-test comparators configuration and its week-by-week extension (EV-4.4).

Toy fixtures only: the harness's synthetic actuals and FakeModel, a fake dim_game, a fake runner.
"""

from __future__ import annotations

import polars as pl
import pytest
from test_harness import SEASONS, WEEKS, FakeModel, run, synthetic_actuals  # sibling test module

from ffml.eval import livetest_score as LS
from ffml.eval.backtest_configs import LIVETEST_REPORT_SEASONS, LIVETEST_SEASONS, get_config
from ffml.models.calibration import CAL_SUFFIX


def test_livetest_comparators_config_is_as_pre_registered():
    cfg = get_config("livetest_comparators")
    assert cfg.seasons == LIVETEST_SEASONS and cfg.seasons[0] == 2021 and len(cfg.seasons) == 7
    assert cfg.report_seasons == LIVETEST_REPORT_SEASONS and len(cfg.report_seasons) == 2
    assert cfg.report_seasons[0] == cfg.seasons[-2] and cfg.report_seasons[1] == cfg.seasons[-1]
    assert cfg.weeks == tuple(range(1, 19)) and cfg.refit_every == 1 and cfg.n_jobs == 1
    assert cfg.include_baselines and cfg.include_challenger and not cfg.include_chain
    assert cfg.calibration == "online" and cfg.purpose == "livetest_score" and cfg.strict_models
    assert set(cfg.report_seasons) <= set(cfg.seasons)


def _dg(season, weeks, partial=None):
    rows = []
    for w in weeks:
        for g in range(2):
            status = "scheduled" if partial is not None and (w, g) in partial else "final"
            rows.append({"season": season, "week": w, "game_type": "REG", "game_id": f"{season}_{w}_{g}",
                         "status": status})
    return pl.DataFrame(rows)


def test_final_week_filter_keeps_history_and_stops_at_the_scored_week():
    actuals = pl.DataFrame({"season": [2024] * 2 + [2026] * 4, "week": [17, 18, 8, 9, 10, 11], "x": range(6)})
    dg = _dg(2026, [8, 9, 10, 11], partial={(9, 1), (11, 0)})  # week 9 and 11 partly final
    out = LS.final_week_filter(actuals, dg, (2026, 10))
    assert out.select("season", "week").rows() == [(2024, 17), (2024, 18), (2026, 8), (2026, 10)]
    assert LS.earlier_weeks_not_final(dg, 2026, 10) == ["2026w09"]
    assert LS.earlier_weeks_not_final(dg, 2026, 9) == []


def test_ensure_comparators_extends_once_and_refuses_on_a_partial_earlier_week(tmp_path):
    run_dir = tmp_path / "cmp"
    run_dir.mkdir()
    calls = []

    def fake_runner(actuals, run_id):
        calls.append((sorted(actuals.select("season", "week").unique().rows()), run_id))
        rows = [{"entity_id": f"e{i}", "position": "QB", "season": 2026, "week": 10, "scoring": "ppr",
                 "model": m, "pred_q50": float(i)} for i in range(3) for m in LS.COMPARATOR_LABELS + ("rolling_avg_4",)]
        pl.DataFrame(rows).write_parquet(run_dir / "predictions.parquet")
        return run_dir

    actuals = pl.DataFrame({"season": [2026] * 3, "week": [8, 9, 10], "x": [1, 2, 3]})
    dg = _dg(2026, [8, 9, 10, 11], partial={(11, 0)})
    assert not LS.comparators_available(run_dir, 2026, 10)
    rows = LS.ensure_comparators(2026, 10, dg, run=run_dir, runner=fake_runner, actuals_fn=lambda s: actuals)
    assert calls == [([(2026, 8), (2026, 9), (2026, 10)], str(run_dir))]
    assert set(rows["model"].unique()) == set(LS.COMPARATOR_LABELS) | {"rolling_avg_4"}
    # a second call finds the rows and does not run again
    LS.ensure_comparators(2026, 10, dg, run=run_dir, runner=fake_runner, actuals_fn=lambda s: actuals)
    assert len(calls) == 1
    # week 11 cannot be extended: it is not final; week 12 cannot either while 11 is partial
    with pytest.raises(LS.ScoreRefused, match="not final"):
        LS.ensure_comparators(2026, 11, dg, run=run_dir, runner=fake_runner, actuals_fn=lambda s: actuals)
    dg2 = _dg(2026, [8, 9, 10, 11, 12], partial={(11, 0)})
    with pytest.raises(LS.ScoreRefused, match="earlier weeks not final"):
        LS.ensure_comparators(2026, 12, dg2, run=run_dir, runner=fake_runner, actuals_fn=lambda s: actuals)


def test_extending_the_run_by_one_week_keeps_earlier_calibrated_rows(tmp_settings, tmp_path):
    """Online calibration recomputed at each combine gives the same rows for the weeks already present."""
    actuals = synthetic_actuals()
    last = SEASONS[-1]
    cut = WEEKS[-1]
    upto_prev = actuals.filter((pl.col("season") < last) | (pl.col("week") < cut))
    model = FakeModel(actuals)
    run(tmp_path, [model], run_id="extend", seasons=SEASONS, actuals=upto_prev)
    first = pl.read_parquet(tmp_path / "backtests" / "extend" / "predictions.parquet")
    assert (last, cut) not in set(first.select("season", "week").unique().rows())
    run(tmp_path, [FakeModel(actuals)], run_id="extend", seasons=SEASONS, actuals=actuals)
    second = pl.read_parquet(tmp_path / "backtests" / "extend" / "predictions.parquet")
    assert (last, cut) in set(second.select("season", "week").unique().rows())
    keys = ["model", "entity_id", "season", "week"]
    cols = [c for c in first.columns if c in second.columns]
    a = first.sort(keys).select(cols)
    b = second.filter(~((pl.col("season") == last) & (pl.col("week") == cut))).sort(keys).select(cols)
    assert a.height == b.height and a.height > 0
    cal = [m for m in a["model"].unique().to_list() if m.endswith(CAL_SUFFIX)]
    assert cal, "the run has calibrated rows"
    for col in [c for c in ("pred_q10", "pred_q50", "pred_q90", "boom_prob", "pred_cdf_at_actual") if c in a.columns]:
        assert a[col].fill_null(-1.0).to_list() == b[col].fill_null(-1.0).to_list(), col


# --------------------------------------------------------------------------- #
# QA-F31: the comparators extension follows the D-121 exclusion
# --------------------------------------------------------------------------- #

OPEN = "2026_10_0"  # never final; teams H0 and A0


def _dg_kick(season, weeks, open_games=()):
    from datetime import UTC, datetime, timedelta

    base = datetime(2026, 11, 13, 1, 15, tzinfo=UTC)
    rows = []
    for w in weeks:
        for g in range(2):
            gid = f"{season}_{w}_{g}"
            rows.append({"season": season, "week": w, "game_type": "REG", "game_id": gid,
                         "home_team": f"H{g}", "away_team": f"A{g}",
                         "kickoff_utc": base + timedelta(days=7 * (w - 10), hours=g),
                         "status": "scheduled" if gid in open_games else "final"})
    return pl.DataFrame(rows)


def _actuals(season, weeks):
    rows = [{"season": season, "week": w, "game_id": f"{season}_{w}_{g}", "team": team, "entity_id": f"{team}-p{w}",
             "position": "WR", "x": 1}
            for w in weeks for g in range(2) for team in (f"H{g}", f"A{g}")]
    return pl.DataFrame(rows, schema_overrides={"season": pl.Int32, "week": pl.Int32})


def _fake_cmp(run_dir, calls):
    def runner(actuals, run_id):
        calls.append(actuals)
        s, w = max(actuals.select("season", "week").unique().rows())
        rows = [{"entity_id": f"e{i}", "position": "QB", "season": s, "week": w, "scoring": "ppr", "model": m,
                 "pred_q50": float(i)} for i in range(3) for m in LS.COMPARATOR_LABELS]
        path = run_dir / "predictions.parquet"
        frame = pl.DataFrame(rows)
        if path.is_file():
            frame = pl.concat([pl.read_parquet(path), frame], how="diagonal_relaxed")
        frame.write_parquet(path)
        return run_dir
    return runner


def test_comparators_extension_follows_the_d121_exclusion(tmp_path):
    import json
    from datetime import timedelta

    run_dir, root = tmp_path / "cmp", tmp_path / "scoring"
    run_dir.mkdir()
    calls = []
    runner = _fake_cmp(run_dir, calls)
    dg = _dg_kick(2026, [9, 10, 11, 12], open_games={OPEN})
    actuals = _actuals(2026, [9, 10, 11, 12])
    cut10 = LS.data_cutoff(dg, 2026, 10)
    after10 = LS.exclusion_after(cut10)
    kw = dict(run=run_dir, runner=runner, actuals_fn=lambda s: actuals, root=root)

    # the week itself: refused while not final, extended without exactly its excluded game (as plan_week decided)
    with pytest.raises(LS.ScoreRefused, match="2026w10 is not final"):
        LS.ensure_comparators(2026, 10, dg, now=after10, **kw)
    with pytest.raises(LS.ScoreRefused, match="2026w10 is not final"):
        LS.ensure_comparators(2026, 10, dg, excluded_games=("2026_10_1",), now=after10, **kw)
    rows = LS.ensure_comparators(2026, 10, dg, excluded_games=(OPEN,), now=after10, **kw)
    assert set(rows["model"].unique()) == set(LS.COMPARATOR_LABELS)
    sent = calls[-1]
    assert sorted(sent.select("season", "week").unique().rows()) == [(2026, 9), (2026, 10)]
    wk10 = sent.filter(pl.col("week") == 10)
    assert wk10.filter(pl.col("game_id") == OPEN).height == 0 and wk10.filter(pl.col("team").is_in(["H0", "A0"])).height == 0
    assert wk10.height == 2 and sent.filter((pl.col("week") == 9) & pl.col("team").is_in(["H0", "A0"])).height == 2

    # a later week: week 10 never sealed and before its cutoff plus 14 days still holds it
    with pytest.raises(LS.ScoreRefused, match=r"earlier weeks not final in dim_game: \['2026w10'\]"):
        LS.ensure_comparators(2026, 11, dg, now=after10 - timedelta(hours=1), **kw)
    # week 10 sealed with its D-121 exclusion: done, and its excluded game stays out of the history
    rp = LS.record_path(root, 2026, 10)
    rp.parent.mkdir(parents=True, exist_ok=True)
    rp.write_text(json.dumps({"excluded_games": [{"game_id": OPEN, "reason": LS.EXCLUSION_REASON}]}), encoding="utf-8")
    LS.ensure_comparators(2026, 11, dg, now=after10 - timedelta(hours=1), **kw)
    sent = calls[-1]
    assert sorted(sent.select("season", "week").unique().rows()) == [(2026, 9), (2026, 10), (2026, 11)]
    assert sent.filter((pl.col("week") == 10) & (pl.col("game_id") == OPEN)).height == 0
    # a sealed week whose record does not cover the open game is not done
    rp.write_text(json.dumps({"excluded_games": []}), encoding="utf-8")
    with pytest.raises(LS.ScoreRefused, match="2026w10"):
        LS.ensure_comparators(2026, 12, dg, now=after10 + timedelta(days=1), **kw)
    # never sealed (a missed week, or one before the window): done once past its own cutoff plus 14 days
    rp.unlink()
    LS.ensure_comparators(2026, 12, dg, now=after10 + timedelta(hours=1), **kw)
    sent = calls[-1]
    assert sorted(sent.select("season", "week").unique().rows())[-1] == (2026, 12)
    assert sent.filter((pl.col("week") == 10) & pl.col("team").is_in(["H0", "A0"])).height == 0
    assert LS.comparator_exclusions(2026, 12, dg, now=after10 + timedelta(hours=1), root=root) == {(2026, 10): (OPEN,)}
    # an all-final season: no exclusion, the filter keeps every row (unchanged behaviour)
    done = _dg_kick(2026, [9, 10, 11])
    assert LS.comparator_exclusions(2026, 11, done, root=root) == {}
    assert LS.final_week_filter(actuals, done, (2026, 11), {}).equals(LS.final_week_filter(actuals, done, (2026, 11)))
