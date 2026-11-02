"""Tests for the consensus baseline reader on synthetic frames (never real ECR rows)."""

from __future__ import annotations

from datetime import date, timedelta

import numpy as np
import polars as pl
import pytest

from ffml.config import PROJECT_ROOT, get_settings, reset_settings_cache
from ffml.data.store import SnapshotStore
from ffml.eval import ecr_baseline as eb
from ffml.eval import metrics as m
from ffml.holdout import HoldoutLockedError

PAGE_OF = {"QB": "qb", "RB": "ppr-rb", "WR": "ppr-wr", "TE": "ppr-te", "K": "k", "DST": "dst"}


def ecr_row(season, week, position, fpid, player_id, team, ecr, *, scrape, game, valid=None, page=None, sd=1.0):
    return {
        "season": season,
        "week": week,
        "game_type": "REG",
        "scrape_date": scrape,
        "scoring": "ppr" if position in ("RB", "WR", "TE") else "std",
        "position": position,
        "fantasypros_id": fpid,
        "player_id": player_id,
        "player_name": f"Synthetic {fpid}",
        "team": team,
        "ecr": float(ecr),
        "sd": sd,
        "best": float(ecr),
        "worst": float(ecr),
        "valid_for_week": False,
        "team_game_date": game,
        "valid_for_game": (scrape < game) if valid is None and game is not None else valid,
        "fp_page": page or PAGE_OF[position],
    }


ECR_SCHEMA = {
    "season": pl.Int32, "week": pl.Int32, "game_type": pl.String, "scrape_date": pl.Date,
    "scoring": pl.String, "position": pl.String, "fantasypros_id": pl.String,
    "player_id": pl.String, "player_name": pl.String, "team": pl.String, "ecr": pl.Float64,
    "sd": pl.Float64, "best": pl.Float64, "worst": pl.Float64, "valid_for_week": pl.Boolean,
    "team_game_date": pl.Date, "valid_for_game": pl.Boolean, "fp_page": pl.String,
}


def ecr_frame(rows):
    return pl.DataFrame(rows, schema=ECR_SCHEMA)


def pg_row(player_id, position, season, week, team="AAA", played=True, **stats):
    base = {
        "player_id": player_id, "season": season, "week": week, "game_id": f"{season}_{week:02d}_{team}",
        "team": team, "position": position, "game_type": "REG", "played": played,
    }
    for col in (*eb.PPR_WEIGHTS, "fg_made_0_39", "fg_made_40_49", "fg_made_50p", "fg_missed",
                "pat_att", "pat_made"):
        base[col] = stats.get(col, 0)
    return base


def dst_row(team, season, week, points_allowed, **stats):
    base = {"team": team, "season": season, "week": week, "game_id": f"{season}_{week:02d}_{team}",
            "game_type": "REG", "points_allowed": points_allowed}
    for col in eb.DST_WEIGHTS:
        base[col] = stats.get(col, 0)
    return base


# --------------------------------------------------------------------------- #
# Reference scoring
# --------------------------------------------------------------------------- #


def test_reference_scoring_by_hand():
    pg = pl.DataFrame([
        pg_row("p1", "WR", 2022, 1, receptions=5, rec_yds=83, rec_td=1, fumbles_lost=1),
        pg_row("p2", "QB", 2022, 1, pass_yds=250, pass_td=2, interceptions=1, rush_yds=12,
               two_pt_conversions=1),
        pg_row("k1", "K", 2022, 1, fg_made_0_39=2, fg_made_40_49=1, fg_made_50p=1, fg_missed=1,
               pat_att=3, pat_made=2),
        pg_row("p3", "RB", 2022, 1, played=False),
    ])
    dst = pl.DataFrame([
        dst_row("D0", 2022, 1, 0, sacks=3, interceptions=1),
        dst_row("D6", 2022, 1, 6),
        dst_row("D7", 2022, 1, 7, def_td=1),
        dst_row("D35", 2022, 1, 35, safeties=1, blocked_kicks=1, fumble_recoveries=1, return_td=1),
        dst_row("DN", 2022, 1, None),
    ], schema_overrides={"points_allowed": pl.Int32})
    act = {r["entity_id"]: r for r in eb.actual_points(pg, dst).iter_rows(named=True)}
    assert act["p1"]["actual"] == pytest.approx(5 + 8.3 + 6 - 2)
    assert act["p2"]["actual"] == pytest.approx(10 + 8 - 2 + 1.2 + 2)
    assert act["k1"]["actual"] == pytest.approx(6 + 4 + 5 - 1 + 2 - 1)
    assert act["p3"]["actual"] == 0.0 and act["p3"]["active"] is False
    assert act["D0"]["actual"] == pytest.approx(3 + 2 + 10)
    assert act["D6"]["actual"] == pytest.approx(7)
    assert act["D7"]["actual"] == pytest.approx(6 + 4)
    assert act["D35"]["actual"] == pytest.approx(2 + 2 + 2 + 6 - 4)
    assert act["DN"]["actual"] == 0.0
    assert act["D0"]["position"] == "DST" and act["D0"]["active"] is True


# --------------------------------------------------------------------------- #
# Selection: page ranks, valid_for_game, joins
# --------------------------------------------------------------------------- #


FRI = date(2022, 9, 16)
MON = date(2022, 9, 19)
THU_GAME = date(2022, 9, 15)
SUN_GAME = date(2022, 9, 18)


def test_valid_for_game_filtering_and_page_rank():
    rows = [
        # Friday scrape: the Thursday WR is ranked first but invalid
        ecr_row(2022, 2, "WR", "f1", "p1", "THU", 1.0, scrape=FRI, game=THU_GAME),
        ecr_row(2022, 2, "WR", "f2", "p2", "SUN", 2.0, scrape=FRI, game=SUN_GAME),
        ecr_row(2022, 2, "WR", "f3", "p3", "SUN", 3.0, scrape=FRI, game=SUN_GAME, sd=2.0),
        ecr_row(2022, 2, "WR", "f4", "p4", "SUN", 3.0, scrape=FRI, game=SUN_GAME, sd=1.0),
        ecr_row(2022, 2, "WR", "f5", "p5", None, 5.0, scrape=FRI, game=None, valid=None),
        # Monday scrape: after the Sunday games, invalid for everyone, reverses the order
        ecr_row(2022, 2, "WR", "f2", "p2", "SUN", 9.0, scrape=MON, game=SUN_GAME),
        ecr_row(2022, 2, "WR", "f3", "p3", "SUN", 1.0, scrape=MON, game=SUN_GAME),
        # an IDP page is ignored
        ecr_row(2022, 2, "WR", "f9", "p9", "SUN", 1.0, scrape=FRI, game=SUN_GAME, page="db"),
    ]
    ecr = ecr_frame(rows)
    ranked = eb.page_ranks(ecr)
    assert "db" not in set(ranked["fp_page"])
    fri = ranked.filter(pl.col("scrape_date") == FRI).sort("page_rank")
    # rank is over the whole page; ecr tie broken by sd
    assert fri["fantasypros_id"].to_list() == ["f1", "f2", "f4", "f3", "f5"]
    chosen = eb.latest_valid(ranked)
    assert sorted(chosen["fantasypros_id"].to_list()) == ["f2", "f3", "f4"]
    assert set(chosen["scrape_date"]) == {FRI}
    assert dict(zip(chosen["fantasypros_id"], chosen["page_rank"], strict=True)) == {"f2": 2, "f3": 4, "f4": 3}


def test_latest_valid_scrape_wins():
    early = date(2022, 9, 9)
    rows = [
        ecr_row(2022, 2, "RB", "r1", "p1", "SUN", 1.0, scrape=early, game=SUN_GAME),
        ecr_row(2022, 2, "RB", "r2", "p2", "SUN", 2.0, scrape=early, game=SUN_GAME),
        ecr_row(2022, 2, "RB", "r1", "p1", "SUN", 2.0, scrape=FRI, game=SUN_GAME),
        ecr_row(2022, 2, "RB", "r2", "p2", "SUN", 1.0, scrape=FRI, game=SUN_GAME),
    ]
    chosen = eb.latest_valid(eb.page_ranks(ecr_frame(rows))).sort("fantasypros_id")
    assert chosen["scrape_date"].to_list() == [FRI, FRI]
    assert chosen["page_rank"].to_list() == [2, 1]


def test_join_drops_missing_ids_and_cross_listed_rows():
    rows = [
        ecr_row(2022, 2, "WR", "f1", "p1", "SUN", 1.0, scrape=FRI, game=SUN_GAME),
        ecr_row(2022, 2, "WR", "f2", None, "SUN", 2.0, scrape=FRI, game=SUN_GAME),
        # a running back listed on the WR page as well as the RB page
        ecr_row(2022, 2, "RB", "f3", "p3", "SUN", 3.0, scrape=FRI, game=SUN_GAME, page="ppr-wr"),
        ecr_row(2022, 2, "RB", "f3", "p3", "SUN", 1.0, scrape=FRI, game=SUN_GAME),
        # no game row this week (e.g. not on a roster)
        ecr_row(2022, 2, "WR", "f4", "p4", "SUN", 4.0, scrape=FRI, game=SUN_GAME),
        ecr_row(2022, 2, "DST", "d1", None, "DDD", 1.0, scrape=FRI, game=SUN_GAME),
        ecr_row(2022, 2, "DST", "d2", None, None, 2.0, scrape=FRI, game=SUN_GAME),
    ]
    pg = pl.DataFrame([
        pg_row("p1", "WR", 2022, 2, receptions=4),
        pg_row("p3", "RB", 2022, 2, rush_yds=50),
    ])
    dst = pl.DataFrame([dst_row("DDD", 2022, 2, 10, sacks=2)])
    actuals = eb.actual_points(pg, dst)
    diag = eb.Diagnostics()
    out = eb.select_consensus(ecr_frame(rows), actuals, diag).sort("entity_id")
    counts = diag.counts[2022]
    assert counts["dropped_no_id"] == 2
    assert counts["dropped_position_mismatch"] == 1
    by_id = {r["entity_id"]: r for r in out.iter_rows(named=True)}
    assert set(by_id) == {"p1", "p3", "p4", "DDD"}
    assert by_id["p3"]["position"] == "RB" and by_id["p3"]["rank_pred"] == 1.0
    assert by_id["p1"]["actual"] == pytest.approx(4.0)
    assert by_id["p4"]["actual"] is None and by_id["p4"]["active"] is False
    assert by_id["DDD"]["actual"] == pytest.approx(2 + 4) and by_id["DDD"]["position"] == "DST"


# --------------------------------------------------------------------------- #
# Rank-to-points curve
# --------------------------------------------------------------------------- #


def test_rank_curve_fitting():
    rng = np.random.default_rng(0)
    ranks = np.repeat(np.arange(1, 61), 60).astype(float)
    actual = 30.0 - 0.4 * ranks + rng.normal(0, 3.0, ranks.size)
    curve = eb.fit_position_curve(ranks, actual)
    assert curve is not None and curve.max_rank == 60 and curve.n_train == ranks.size
    assert np.all(np.diff(curve.q50) <= 1e-12)
    assert np.all(np.diff(curve.mean) <= 1e-12)
    assert np.all(curve.q10 <= curve.q50) and np.all(curve.q50 <= curve.q90)
    mid = np.arange(10, 51)
    assert np.max(np.abs(curve.q50[mid - 1] - (30.0 - 0.4 * mid))) < 1.5
    width = curve.q90[mid - 1] - curve.q10[mid - 1]
    assert np.mean(width) == pytest.approx(2 * 1.2816 * 3.0, abs=1.2)
    pred = curve.predict(np.array([1.0, 30.0, 500.0]))
    assert pred["pred_q50"][2] == curve.q50[-1]  # deep ranks clamp to the last fitted rank
    assert eb.fit_position_curve(np.array([]), np.array([])) is None


def test_rank_curve_ignores_inactive_rows():
    rows = pl.DataFrame({
        "position": ["WR"] * 40,
        "season": [2021] * 40,
        "rank_pred": [float(r) for r in range(1, 21)] * 2,
        "actual": [10.0] * 20 + [0.0] * 20,
        "active": [True] * 20 + [False] * 20,
    })
    curve = eb.fit_rank_curve(rows)
    assert np.allclose(curve.positions["WR"].q50, 10.0)
    assert curve.seasons == (2021,)


def _season_rows(season, offset, weeks=range(2, 10), n=40, rng=None):
    """WR consensus rows and actuals where actual = offset - 0.3 * rank (+ noise)."""
    rng = rng or np.random.default_rng(season)
    ecr_rows, pg_rows = [], []
    for week in weeks:
        scrape = date(season, 9, 1) + timedelta(days=7 * week)
        game = scrape + timedelta(days=2)
        for r in range(1, n + 1):
            pid = f"{season}-{week}-{r}"
            ecr_rows.append(ecr_row(season, week, "WR", f"f{pid}", pid, "SUN", r, scrape=scrape, game=game))
            pts = offset - 0.3 * r + rng.normal(0, 1.0)
            pg_rows.append(pg_row(pid, "WR", season, week, receptions=0, rec_yds=round(pts * 10)))
    return ecr_rows, pg_rows


def test_curve_uses_prior_seasons_only():
    e21, p21 = _season_rows(2021, offset=20.0)
    e22, p22 = _season_rows(2022, offset=100.0)
    ecr = ecr_frame(e21 + e22)
    actuals = eb.actual_points(pl.DataFrame(p21 + p22))
    result = eb.build_consensus(ecr, actuals, seasons=[2021, 2022])
    assert result.curve_seasons == {2021: (), 2022: (2021,)}
    pred = result.predictions
    p21_pred = pred.filter(pl.col("season") == 2021)
    assert p21_pred["pred_q50"].null_count() == p21_pred.height  # no prior season, no curve
    p22_pred = pred.filter(pl.col("season") == 2022)
    assert p22_pred["pred_q50"].null_count() == 0
    # the 2022 curve reflects 2021 (offset 20), not 2022 (offset 100)
    top = p22_pred.filter(pl.col("rank_pred") == 10)["pred_q50"]
    assert top.max() < 25.0
    assert np.allclose(top.to_numpy(), 20.0 - 3.0, atol=1.5)
    diag = {r["season"]: r for r in result.diagnostics.iter_rows(named=True)}
    assert diag[2021]["rows_without_curve"] == p21_pred.height
    assert diag[2022]["curve_train_rows"] == len(p21)
    # evaluating 2022 alone still fits on 2021 when the frame holds it
    only = eb.build_consensus(ecr, actuals, seasons=[2022])
    assert only.predictions["season"].unique().to_list() == [2022]
    assert only.curve_seasons[2022] == (2021,)
    # the frame satisfies the metrics contract
    checked = m.validate_frame(only.predictions)
    assert set(checked["model"]) == {eb.MODEL_NAME}
    assert m.mae(checked) > 0


def test_coverage_from_frames():
    rows = [
        ecr_row(2022, 2, "WR", "f1", "p1", "THU", 1.0, scrape=FRI, game=THU_GAME),
        ecr_row(2022, 2, "WR", "f2", "p2", "SUN", 2.0, scrape=FRI, game=SUN_GAME),
        ecr_row(2022, 2, "WR", "f3", "p3", "SUN", 3.0, scrape=FRI, game=SUN_GAME),
        ecr_row(2022, 2, "RB", "f4", "p4", "SUN", 1.0, scrape=FRI, game=SUN_GAME),
    ]
    pg = pl.DataFrame([
        pg_row("p1", "WR", 2022, 2), pg_row("p2", "WR", 2022, 2), pg_row("p3", "WR", 2022, 2),
        pg_row("p4", "RB", 2022, 2), pg_row("p5", "RB", 2022, 2), pg_row("p6", "TE", 2022, 2),
        pg_row("p7", "TE", 2022, 2, played=False),
        pg_row("p8", "WR", 2022, 3),  # a week without any scrape
    ])
    cov = eb.coverage_from_frames(ecr_frame(rows), eb.actual_points(pg))
    by_pos = {r["position"]: r for r in cov.iter_rows(named=True)}
    assert by_pos["ALL"]["played_rows"] == 7 and by_pos["ALL"]["covered_rows"] == 3
    assert by_pos["ALL"]["coverage"] == pytest.approx(3 / 7)
    assert by_pos["WR"]["coverage"] == pytest.approx(2 / 4)
    assert by_pos["WR"]["coverage_in_scrape_weeks"] == pytest.approx(2 / 3)
    assert by_pos["TE"]["covered_rows"] == 0


# --------------------------------------------------------------------------- #
# Guards and the I/O path
# --------------------------------------------------------------------------- #


def test_output_guard(tmp_path):
    with pytest.raises(ValueError, match="D-007"):
        eb.check_output_path(PROJECT_ROOT / "reports" / "consensus.parquet")
    with pytest.raises(ValueError):
        eb.check_output_path(PROJECT_ROOT / "tests" / "fixtures" / "consensus.parquet")
    assert eb.check_output_path(PROJECT_ROOT / "data" / "eval" / "x.parquet").is_relative_to(PROJECT_ROOT / "data")
    frame = pl.DataFrame({"a": [1]})
    target = eb.write_restricted_frame(frame, tmp_path / "out" / "c.parquet")
    assert target.exists()
    # a synthetic repository root shows the rule is relative to the root
    fake_root = tmp_path / "repo"
    with pytest.raises(ValueError):
        eb.write_restricted_frame(frame, fake_root / "docs" / "c.parquet", root=fake_root)
    assert eb.write_restricted_frame(frame, fake_root / "data" / "c.parquet", root=fake_root).exists()


@pytest.fixture
def locked_store(tmp_settings, monkeypatch):
    monkeypatch.delenv("FFML_HOLDOUT_UNLOCK", raising=False)
    reset_settings_cache()
    settings = get_settings()
    e21, p21 = _season_rows(2021, offset=20.0, weeks=range(2, 5))
    e22, p22 = _season_rows(2022, offset=20.0, weeks=range(2, 5))
    ecr = ecr_frame(e21 + e22)
    restricted = SnapshotStore(settings.processed_dir / "restricted")
    restricted.write_snapshot(
        "fact_ecr_weekly", {s: ecr.filter(pl.col("season") == s) for s in (2021, 2022)}
    )
    pg = pl.DataFrame(p21 + p22)
    processed = SnapshotStore(settings.processed_dir)
    processed.write_snapshot(
        "fact_player_game", {s: pg.filter(pl.col("season") == s) for s in (2021, 2022)}
    )
    yield settings
    reset_settings_cache()


def test_consensus_predictions_end_to_end_on_synthetic_store(locked_store):
    pred = eb.consensus_predictions([2022])
    assert pred.height == 3 * 40
    assert pred["pred_q50"].null_count() == 0
    assert set(pred["position"]) == {"WR"}
    cov = eb.consensus_coverage([2022])
    assert cov.filter(pl.col("position") == "ALL")["coverage"][0] == pytest.approx(1.0)
    assert not (locked_store.artifacts_dir / "holdout_access.log").exists()


def test_holdout_and_scoring_guards(locked_store):
    holdout = locked_store.holdout_season
    with pytest.raises(HoldoutLockedError):
        eb.consensus_predictions([holdout])
    with pytest.raises(HoldoutLockedError):
        eb.consensus_coverage([holdout - 1, holdout])
    with pytest.raises(ValueError, match="scoring"):
        eb.consensus_predictions([2022], scoring="half")
