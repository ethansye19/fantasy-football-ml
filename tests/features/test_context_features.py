"""Depth chart as-of selection and rank harmonisation on synthetic frames."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl

from ffml.features.context import depth_features, depth_ranks, select_snapshots
from ffml.features.data import DEPTH_POS_GROUP, FeatureContext

KICK1 = datetime(2025, 9, 5, 0, 20, tzinfo=UTC)
KICK2 = KICK1 + timedelta(days=7)
UTC_US = pl.Datetime("us", "UTC")


def _ctx(depth_rows: list[dict]) -> FeatureContext:
    dim_game = pl.DataFrame(
        {
            "game_id": ["g1", "g2"],
            "season": [2025, 2025],
            "week": [1, 2],
            "game_type": ["REG", "REG"],
            "kickoff_utc": [KICK1, KICK2],
        },
        schema_overrides={"season": pl.Int32, "week": pl.Int32, "kickoff_utc": UTC_US},
    )
    depth = pl.DataFrame(
        depth_rows, schema_overrides={"snapshot_ts": UTC_US, "pos_rank": pl.Int32}
    ).with_columns(
        pl.col("pos_abb").replace_strict(DEPTH_POS_GROUP, default=None).alias("pos_group")
    )
    empty = pl.DataFrame()
    return FeatureContext(
        dim_game=dim_game,
        dim_player=empty,
        player_game=empty,
        team_game=empty,
        dst_game=empty,
        injury=empty,
        depth=depth,
        roster=empty,
    )


def _snap(ts, player, pos, rank, team="KC"):
    return {"snapshot_ts": ts, "team": team, "player_id": player, "pos_abb": pos, "pos_rank": rank}


def test_snapshot_after_asof_is_never_used():
    asof = KICK2 - timedelta(hours=1)
    before = asof - timedelta(days=2)
    after = asof + timedelta(minutes=1)
    ctx = _ctx(
        [
            _snap(before, "p1", "WR", 1),
            _snap(before, "p2", "WR", 2),
            _snap(after, "p1", "WR", 2),
            _snap(after, "p2", "WR", 1),
        ]
    )
    pairs = pl.DataFrame({"team": ["KC"], "asof_ts": [asof]}, schema_overrides={"asof_ts": UTC_US})
    sel = select_snapshots(ctx, pairs, "asof_ts")
    assert sel["depth_snapshot_ts"].to_list() == [before]

    rows = pl.DataFrame(
        {
            "_rid": [0, 1],
            "team": ["KC", "KC"],
            "player_id": ["p1", "p2"],
            "position": ["WR", "WR"],
            "season": [2025, 2025],
            "week": [2, 2],
            "asof_ts": [asof, asof],
        },
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "asof_ts": UTC_US,
            "_rid": pl.UInt32,
        },
    )
    out = depth_features(rows, ctx).sort("_rid")
    assert out["depth__rank"].to_list() == [1.0, 2.0]
    assert out["meta__depth_snapshot_ts"].to_list() == [before, before]
    assert (out["meta__depth_snapshot_ts"] <= out["asof_ts"]).all()

    # a snapshot stamped exactly at asof_ts is allowed (snapshot_ts <= asof_ts)
    ctx_eq = _ctx([_snap(before, "p1", "WR", 1), _snap(asof, "p1", "WR", 3)])
    assert select_snapshots(ctx_eq, pairs, "asof_ts")["depth_snapshot_ts"].to_list() == [asof]


def test_rank_harmonised_between_legacy_and_timestamped_charts():
    t = KICK1 - timedelta(hours=48)
    legacy = [_snap(t, f"w{i}", "WR", r) for i, r in enumerate([1, 1, 1, 2])]
    stamped = [_snap(t, f"w{i}", "WR", r, team="DET") for i, r in enumerate([1, 2, 3, 4])]
    ctx = _ctx([*legacy, *stamped, _snap(t, "q1", "QB", 1), _snap(t, "f1", "FB", 1)])
    sel = pl.DataFrame(
        {"team": ["KC", "DET"], "depth_snapshot_ts": [t, t]},
        schema_overrides={"depth_snapshot_ts": UTC_US},
    )
    ranks = depth_ranks(ctx, sel).filter(pl.col("pos_group") == "WR").sort("team", "player_id")
    kc = ranks.filter(pl.col("team") == "KC")["depth_rank"].to_list()
    det = ranks.filter(pl.col("team") == "DET")["depth_rank"].to_list()
    assert kc == [1, 1, 1, 4]  # three listed starters, the next WR has three players ahead
    assert det == [1, 2, 3, 4]
    assert set(ranks["same_pos_count"].to_list()) == {4}


def test_depth_rank_change_and_starter_flag():
    t1 = KICK1 - timedelta(hours=48)
    t2 = KICK2 - timedelta(hours=48)
    ctx = _ctx(
        [
            _snap(t1, "r1", "RB", 1),
            _snap(t1, "r2", "RB", 2),
            _snap(t2, "r1", "RB", 2),
            _snap(t2, "r2", "RB", 1),
        ]
    )
    rows = pl.DataFrame(
        {
            "_rid": [0, 1, 2],
            "team": ["KC", "KC", "KC"],
            "player_id": ["r1", "r2", "x9"],
            "position": ["RB", "RB", "RB"],
            "season": [2025] * 3,
            "week": [2] * 3,
            "asof_ts": [KICK2 - timedelta(hours=1)] * 3,
        },
        schema_overrides={
            "season": pl.Int32,
            "week": pl.Int32,
            "asof_ts": UTC_US,
            "_rid": pl.UInt32,
        },
    )
    out = depth_features(rows, ctx).sort("_rid")
    assert out["depth__rank"].to_list() == [2.0, 1.0, None]
    assert out["depth__rank_change"].to_list() == [1.0, -1.0, None]
    assert out["depth__starter_flag"].to_list() == [0.0, 1.0, 0.0]
    assert out["avail__depth_chart_present"].to_list() == [1.0, 1.0, 0.0]
