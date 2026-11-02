"""Depth chart as-of selection, legacy-chart timing (D-059) and rank harmonisation."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest

from ffml.features.context import depth_features, depth_ranks, select_snapshots
from ffml.features.data import DEPTH_POS_GROUP, FeatureContext

KICK1 = datetime(2025, 9, 5, 0, 20, tzinfo=UTC)
KICK2 = KICK1 + timedelta(days=7)
UTC_US = pl.Datetime("us", "UTC")
INTS = {"season": pl.Int32, "week": pl.Int32}


def _ctx(
    depth_rows: list[dict], games: list[tuple[int, int, datetime]] | None = None
) -> FeatureContext:
    games = games or [(2025, 1, KICK1), (2025, 2, KICK2)]
    dim_game = pl.DataFrame(
        {
            "game_id": [f"g{i}" for i in range(len(games))],
            "season": [g[0] for g in games],
            "week": [g[1] for g in games],
            "game_type": ["REG"] * len(games),
            "kickoff_utc": [g[2] for g in games],
        },
        schema_overrides={**INTS, "kickoff_utc": UTC_US},
    )
    depth = pl.DataFrame(
        depth_rows, schema_overrides={"snapshot_ts": UTC_US, "pos_rank": pl.Int32, **INTS}
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


def _snap(ts, player, pos, rank, team="KC", kind="timestamped", season=2025, week=2):
    return {
        "snapshot_ts": ts,
        "snapshot_kind": kind,
        "season": season,
        "week": week,
        "team": team,
        "player_id": player,
        "pos_abb": pos,
        "pos_rank": rank,
    }


def _rows(players, positions, season, week, asof, team="KC"):
    n = len(players)
    return pl.DataFrame(
        {
            "_rid": list(range(n)),
            "team": [team] * n,
            "player_id": players,
            "position": positions,
            "season": [season] * n,
            "week": [week] * n,
            "asof_ts": [asof] * n,
        },
        schema_overrides={**INTS, "asof_ts": UTC_US, "_rid": pl.UInt32},
    )


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
    assert select_snapshots(ctx, pairs, "asof_ts")["depth_snapshot_ts"].to_list() == [before]

    out = depth_features(_rows(["p1", "p2"], ["WR", "WR"], 2025, 2, asof), ctx).sort("_rid")
    assert out["depth__rank"].to_list() == [1.0, 2.0]
    assert out["meta__depth_snapshot_ts"].to_list() == [before, before]
    assert out["depth__chart_age_days"].to_list() == pytest.approx([2.0, 2.0])

    # a snapshot stamped exactly at asof_ts is allowed (snapshot_ts <= asof_ts)
    ctx_eq = _ctx([_snap(before, "p1", "WR", 1), _snap(asof, "p1", "WR", 3)])
    assert select_snapshots(ctx_eq, pairs, "asof_ts")["depth_snapshot_ts"].to_list() == [asof]


def _legacy_world():
    """Legacy weekly charts for 2023 weeks 17-18 and 2024 weeks 1-2 (stamped kickoff - 48 h)."""
    k = {
        (2023, 17): datetime(2023, 12, 29, 1, 15, tzinfo=UTC),
        (2023, 18): datetime(2024, 1, 6, 21, 30, tzinfo=UTC),
        (2024, 1): datetime(2024, 9, 6, 0, 20, tzinfo=UTC),
        (2024, 2): datetime(2024, 9, 13, 0, 15, tzinfo=UTC),
    }
    rows = []
    # the starter changes every week so the chosen chart is visible in the rank
    for i, ((season, week), kick) in enumerate(k.items()):
        ts = kick - timedelta(hours=48)
        rows += [
            _snap(ts, "a", "RB", 1 if i % 2 == 0 else 2, kind="weekly", season=season, week=week),
            _snap(ts, "b", "RB", 2 if i % 2 == 0 else 1, kind="weekly", season=season, week=week),
        ]
    games = [(s, w, kick) for (s, w), kick in k.items()]
    return _ctx(rows, games), k


def test_legacy_charts_use_previous_week_in_primary_and_own_week_in_sensitivity():
    ctx, k = _legacy_world()
    asof_w2 = k[(2024, 2)] - timedelta(hours=1)
    asof_w1 = k[(2024, 1)] - timedelta(hours=1)
    w1_chart = k[(2024, 1)] - timedelta(hours=48)
    w2_chart = k[(2024, 2)] - timedelta(hours=48)
    last_2023 = k[(2023, 18)] - timedelta(hours=48)

    prim2 = depth_features(_rows(["a", "b"], ["RB", "RB"], 2024, 2, asof_w2), ctx, "primary").sort(
        "_rid"
    )
    same2 = depth_features(
        _rows(["a", "b"], ["RB", "RB"], 2024, 2, asof_w2), ctx, "same_week"
    ).sort("_rid")
    assert prim2["meta__depth_snapshot_ts"].to_list() == [w1_chart, w1_chart]
    assert prim2["depth__rank"].to_list() == [1.0, 2.0]  # the week 1 chart (a is RB1)
    assert same2["meta__depth_snapshot_ts"].to_list() == [w2_chart, w2_chart]
    assert same2["depth__rank"].to_list() == [2.0, 1.0]  # the week 2 chart (b is RB1)
    # the primary chart for week 2 is about nine days old: 7 days plus the 47 h stamp gap
    age = prim2["depth__chart_age_days"][0]
    assert age == pytest.approx(((asof_w2 - w1_chart).total_seconds()) / 86400.0)
    assert age > 7

    # week 1 reads the previous season's last available chart
    prim1 = depth_features(_rows(["a"], ["RB"], 2024, 1, asof_w1), ctx, "primary")
    assert prim1["meta__depth_snapshot_ts"].to_list() == [last_2023]
    assert prim1["depth__rank"].to_list() == [2.0]
    same1 = depth_features(_rows(["a"], ["RB"], 2024, 1, asof_w1), ctx, "same_week")
    assert same1["meta__depth_snapshot_ts"].to_list() == [w1_chart]

    with pytest.raises(ValueError):
        depth_features(_rows(["a"], ["RB"], 2024, 1, asof_w1), ctx, "bogus")


def test_timestamped_charts_win_over_legacy_ones_and_ignore_the_variant():
    legacy_ts = datetime(2025, 2, 7, 23, 30, tzinfo=UTC)
    stamped_ts = KICK1 - timedelta(days=3)
    ctx = _ctx(
        [
            _snap(legacy_ts, "a", "RB", 1, kind="weekly", season=2025, week=1),
            _snap(stamped_ts, "a", "RB", 2, kind="timestamped", season=2025, week=1),
            _snap(stamped_ts, "b", "RB", 1, kind="timestamped", season=2025, week=1),
        ]
    )
    rows = _rows(["a"], ["RB"], 2025, 1, KICK1 - timedelta(hours=1))
    for variant in ("primary", "same_week"):
        out = depth_features(rows, ctx, variant)
        assert out["meta__depth_snapshot_ts"].to_list() == [stamped_ts]
        assert out["depth__rank"].to_list() == [2.0]


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
            _snap(t1, "r1", "RB", 1, week=1),
            _snap(t1, "r2", "RB", 2, week=1),
            _snap(t2, "r1", "RB", 2),
            _snap(t2, "r2", "RB", 1),
        ]
    )
    rows = _rows(["r1", "r2", "x9"], ["RB", "RB", "RB"], 2025, 2, KICK2 - timedelta(hours=1))
    out = depth_features(rows, ctx).sort("_rid")
    assert out["depth__rank"].to_list() == [2.0, 1.0, None]
    assert out["depth__rank_change"].to_list() == [1.0, -1.0, None]
    assert out["depth__starter_flag"].to_list() == [0.0, 1.0, 0.0]
    assert out["avail__depth_chart_present"].to_list() == [1.0, 1.0, 0.0]
