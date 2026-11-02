"""Targets (historical and future), the team-change reset on real rows, and the guarded loader."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

import polars as pl
import pytest
from polars.testing import assert_frame_equal

from ffml.data.store import SnapshotStore
from ffml.features.access import load_features, training_frame
from ffml.features.build import build_player_week, build_team_week, run_features
from ffml.features.manifest import columns, feature_names
from ffml.features.targets import PLAYER_KEYS, TARGET_NAMES, TIME_COLS, future_targets
from ffml.holdout import HoldoutLockedError

NOW = datetime(2023, 10, 20, 12, 0, tzinfo=UTC)


def test_future_targets_shape(fixture_ctx, cutoff_of):
    fut = future_targets(2023, 7, fixture_ctx, now=NOW, include_final=True)
    players, teams, dst = fut["player_week"], fut["team_week"], fut["dst_week"]
    assert players.columns == [*PLAYER_KEYS, *TIME_COLS, *TARGET_NAMES["player_week"]]
    assert set(players["team"].unique().to_list()) == {"KC", "DET"}
    roster = fixture_ctx.roster.filter(
        pl.col("season") == 2023, pl.col("week") == 6, pl.col("status").is_in(["ACT", "INA"])
    ).join(
        fixture_ctx.dim_player.select("player_id", pl.col("position").alias("p")),
        on="player_id",
        how="left",
    )
    expected = roster.filter(pl.coalesce("p", "position").is_in(["QB", "RB", "WR", "TE", "K"]))
    assert players.height == expected["player_id"].n_unique()
    assert players["player_id"].is_unique().all()
    games_week7 = fixture_ctx.dim_game.filter(pl.col("season") == 2023, pl.col("week") == 7)
    assert teams.height == 2 * games_week7.height == dst.height
    for frame, name in ((players, "player_week"), (teams, "team_week"), (dst, "dst_week")):
        assert frame.select(TARGET_NAMES[name]).null_count().row(0) == tuple(
            [frame.height] * len(TARGET_NAMES[name])
        )
        assert (frame["asof_ts"] == NOW).all()
        assert (frame["cutoff_ts"] == cutoff_of(2023, 7)).all()
    # by default final games are skipped (all of 2023 week 7 is final in the fixture)
    assert future_targets(2023, 7, fixture_ctx, now=NOW)["team_week"].height == 0

    built = build_player_week(fixture_ctx, players)
    assert built.columns == [c.name for c in columns("player_week")]
    assert built.height == players.height
    assert (built["meta__last_game_kickoff"].drop_nulls() < cutoff_of(2023, 7)).all()
    assert build_team_week(fixture_ctx, teams).height == teams.height


def test_future_rows_match_historical_rows_at_the_same_asof(fixture_ctx, fixture_build, cutoff_of):
    cutoff = cutoff_of(2023, 6)
    fut = future_targets(2023, 6, fixture_ctx, now=cutoff - timedelta(hours=1), include_final=True)
    built = build_player_week(fixture_ctx, fut["player_week"])
    hist = fixture_build["player_week"].filter(pl.col("season") == 2023, pl.col("week") == 6)
    cols = [c.name for c in columns("player_week") if c.kind in ("key", "meta", "feature", "avail")]
    common = built.join(hist.select("player_id"), on="player_id", how="semi")
    assert common.height >= 25
    left = common.select(cols).sort("player_id")
    right = (
        hist.join(common.select("player_id"), on="player_id", how="semi")
        .select(cols)
        .sort("player_id")
    )
    assert_frame_equal(left, right)


def test_team_change_resets_shares_and_keeps_prev_team(fixture_ctx, fixture_build):
    pg = fixture_ctx.player_game
    played22 = pg.filter(
        pl.col("season") == 2022,
        pl.col("played"),
        pl.col("team") == "KC",
        pl.col("position") == "WR",
    )
    rows23 = pg.filter(pl.col("season") == 2023, pl.col("played"), pl.col("week").is_between(2, 6))
    cands = (
        played22.group_by("player_id")
        .len()
        .filter(pl.col("len") >= 8)
        .join(rows23.group_by("player_id").len().filter(pl.col("len") >= 3), on="player_id")
    )
    pid = sorted(cands["player_id"].to_list())[0]
    moved_pg = pg.with_columns(
        pl.when((pl.col("player_id") == pid) & (pl.col("season") == 2023) & (pl.col("week") >= 2))
        .then(pl.lit("DET"))
        .otherwise(pl.col("team"))
        .alias("team")
    )
    moved_ctx = fixture_ctx.with_tables(fact_player_game=moved_pg)
    from ffml.features.targets import player_targets

    moved = build_player_week(moved_ctx, player_targets(moved_ctx)).filter(
        pl.col("player_id") == pid
    )
    orig = fixture_build["player_week"].filter(pl.col("player_id") == pid)
    first_week = int(
        moved_pg.filter(pl.col("player_id") == pid, pl.col("team") == "DET")["week"].min()
    )
    m = moved.filter(pl.col("season") == 2023, pl.col("week") == first_week).row(0, named=True)
    o = orig.filter(pl.col("season") == 2023, pl.col("week") == first_week).row(0, named=True)
    for w in ("l1", "l3", "l5", "ew", "std"):
        assert m[f"opp__target_share__{w}"] is None
        assert o[f"opp__target_share__{w}"] is not None
    assert m["opp__prev_team_target_share__ew"] == pytest.approx(o["opp__target_share__ew"])
    assert m["opp__target_share__car"] == pytest.approx(o["opp__target_share__car"])
    assert m["opp__targets__l3"] == pytest.approx(o["opp__targets__l3"])  # volume is not reset
    assert m["attr__new_team_flag"] == 1.0 and o["attr__new_team_flag"] == 0.0
    assert m["attr__weeks_on_team"] == 0.0
    later = moved.filter(
        pl.col("season") == 2023, pl.col("week") > first_week, pl.col("y_played") == 1
    ).sort("week")
    nxt = later.row(0, named=True)
    assert nxt["attr__new_team_flag"] == 0.0
    assert nxt["opp__target_share__l1"] is not None
    assert nxt["opp__prev_team_target_share__ew"] == pytest.approx(o["opp__target_share__ew"])


def test_access_guard_and_training_frame(tmp_settings, fixture_ctx, monkeypatch):
    from ffml.config import reset_settings_cache

    store = SnapshotStore(tmp_settings.features_dir)
    ids = run_features([2022, 2023], ctx=fixture_ctx, store=store)
    assert set(ids) == {"player_week", "team_week", "dst_week"}
    manifest = store.read_manifest("player_week")
    assert manifest["extra"]["feature_version"] == "0.1.0"
    assert manifest["extra"]["inputs"]["fact_player_game"] == "fixture"
    assert manifest["seasons"] == [2022, 2023]

    df = training_frame("player_week", [2023], "unit_test")
    assert (df["game_type"] == "REG").all()
    assert (df["y_played"] == 1.0).all()
    assert not [c for c in df.columns if c.startswith(("avail__", "meta__"))]
    assert set(feature_names("player_week")) <= set(df.columns)
    full = load_features("player_week", [2022, 2023], "unit_test")
    assert full.height > df.height

    with pytest.raises(HoldoutLockedError):
        load_features("player_week", [2024, 2025], "unit_test")
    monkeypatch.setenv("FFML_HOLDOUT_UNLOCK", "true")
    reset_settings_cache()
    with pytest.raises(FileNotFoundError):
        load_features("player_week", [2025], "unit_test")
    log = (tmp_settings.artifacts_dir / "holdout_access.log").read_text(encoding="utf-8")
    assert "purpose=unit_test" in log and "2025" in log
