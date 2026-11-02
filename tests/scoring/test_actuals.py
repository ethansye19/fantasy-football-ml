"""Actual stat lines from the processed tables, on a synthetic snapshot store."""

from __future__ import annotations

from datetime import UTC, datetime

import polars as pl
import pytest

from ffml.data.store import SnapshotStore
from ffml.holdout import HoldoutLockedError
from ffml.scoring import STAT_COLUMNS, ScoringRules
from ffml.scoring.actuals import (
    _DST_SOURCE_COLUMNS,
    _PLAYER_SOURCE_COLUMNS,
    OUTPUT_COLUMNS,
    actual_statlines,
)

KICK = datetime(2024, 9, 8, 17, 0, tzinfo=UTC)


def _player_row(**values) -> dict:
    row = {c: 0 for c in _PLAYER_SOURCE_COLUMNS}
    row.update(
        player_id="P1",
        season=2024,
        week=1,
        game_id="2024_01_AAA_BBB",
        team="AAA",
        opponent="BBB",
        position="WR",
        game_type="REG",
        kickoff_utc=KICK,
        played=True,
    )
    row.update(values)
    return row


def _dst_row(**values) -> dict:
    row = {c: 0 for c in _DST_SOURCE_COLUMNS}
    row.update(
        team="AAA",
        season=2024,
        week=1,
        game_id="2024_01_AAA_BBB",
        opponent="BBB",
        game_type="REG",
        yards_allowed=287.0,
    )
    row.update(values)
    return row


@pytest.fixture
def store(tmp_path) -> SnapshotStore:
    s = SnapshotStore(tmp_path / "processed")
    fpg = pl.DataFrame(
        [
            _player_row(
                player_id="QB1",
                position="QB",
                pass_att=30,
                completions=20,
                pass_yds=250,
                pass_td=2,
                interceptions=1,
                carries=3,
                rush_yds=12,
                two_pt_conversions=1,
                fumbles_lost=1,
            ),
            _player_row(
                player_id="WR1", targets=9, receptions=6, rec_yds=88, rec_td=1, return_tds=1
            ),
            _player_row(
                player_id="K1",
                position="K",
                fg_made_0_19=1,
                fg_made_40_49=1,
                fg_made_60p=1,
                fg_missed=1,
                pat_att=4,
                pat_made=3,
            ),
            _player_row(player_id="WR2", played=False),
            _player_row(player_id="WR1", week=19, game_type="WC", receptions=2),
        ]
    )
    s.write_snapshot("fact_player_game", {2024: fpg})
    dst = pl.DataFrame(
        [
            _dst_row(
                sacks=3,
                interceptions=2,
                fumble_recoveries=1,
                forced_fumbles=2,
                def_td=1,
                return_td=1,
                safeties=1,
                blocked_kicks=1,
                points_allowed=13,
            )
        ]
    )
    s.write_snapshot("fact_dst_game", {2024: dst})
    games = pl.DataFrame({"game_id": ["2024_01_AAA_BBB"], "season": [2024], "kickoff_utc": [KICK]})
    s.write_snapshot("dim_game", {2024: games})
    return s


def test_actual_statlines_mapping(store, tmp_settings):
    out = actual_statlines([2024], "test", store=store)
    assert out.columns == list(OUTPUT_COLUMNS)
    assert out.height == 5  # postseason row dropped by default
    by = {r["entity_id"]: r for r in out.iter_rows(named=True)}
    qb = by["QB1"]
    assert (qb["pass_cmp"], qb["pass_int"], qb["rush_att"], qb["two_pt"], qb["fum_lost"]) == (
        20,
        1,
        3,
        1,
        1,
    )
    wr = by["WR1"]
    assert (wr["rec"], wr["targets"], wr["rec_yds"], wr["ret_td"]) == (6, 9, 88, 1)
    k = by["K1"]
    assert (k["fg_made_0_19"], k["fg_made_40_49"], k["fg_made_60p"], k["fg_missed"]) == (1, 1, 1, 1)
    assert (k["xp_made"], k["xp_missed"]) == (3, 1)
    d = by["AAA"]
    assert d["position"] == "DST" and d["played"] is True and d["kickoff_utc"] == KICK
    assert (d["sacks"], d["def_int"], d["fum_rec"], d["def_td"], d["st_td"]) == (3, 2, 1, 1, 1)
    assert d["forced_fumbles"] == 2 and qb["forced_fumbles"] == 0
    assert (d["safeties"], d["blocked_kicks"], d["pts_allowed"], d["yds_allowed"]) == (
        1,
        1,
        13,
        287,
    )
    assert by["WR2"]["played"] is False
    # offense rows carry zero DST stats and vice versa
    assert all(qb[c] == 0 for c in ("sacks", "pts_allowed", "yds_allowed"))
    assert all(d[c] == 0 for c in ("pass_yds", "rec", "xp_made"))
    for c in STAT_COLUMNS:
        assert out[c].dtype == pl.Int32


def test_actual_statlines_score_end_to_end(store, tmp_settings):
    out = actual_statlines([2024], "test", store=store)
    scored = ScoringRules.from_preset("ppr").with_points(out)
    p = dict(zip(scored["entity_id"], scored["points"], strict=True))
    assert p["QB1"] == pytest.approx(10 + 8 - 2 + 1.2 + 2 - 2)
    assert p["WR1"] == pytest.approx(6 + 8.8 + 6 + 6)
    assert p["K1"] == pytest.approx(3 + 4 + 5 - 1 + 3 - 1)
    assert p["AAA"] == pytest.approx(3 + 4 + 2 + 6 + 6 + 2 + 2 + 4)
    league = ScoringRules.from_dict({"ff": 1, "def_st_ff": 1, "sack": 1})
    assert dict(zip(out["entity_id"], league.score(out), strict=True))["AAA"] == 3 + 2


def test_dst_snapshot_without_forced_fumbles_reads_zero():
    from ffml.scoring.actuals import dst_statlines

    old = pl.DataFrame([_dst_row()]).drop("forced_fumbles")
    out = dst_statlines(old.with_columns(pl.lit(KICK).alias("kickoff_utc")))
    assert out["forced_fumbles"].to_list() == [0]


def test_postseason_kept_on_request(store, tmp_settings):
    out = actual_statlines([2024], "test", store=store, game_types=None, include_dst=False)
    assert out.height == 5
    assert set(out["game_type"]) == {"REG", "WC"}


def test_holdout_guard(store, tmp_settings):
    with pytest.raises(HoldoutLockedError):
        actual_statlines([2024, 2025], "backtest", store=store)
