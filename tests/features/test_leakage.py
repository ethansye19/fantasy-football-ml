"""Leakage test suite (docs/FEATURES.md section J) on the offline fixture slice.

Slice: KC and DET, 2022 plus 2023 weeks 1-6 (all teams for team and DST
tables), extracted by tests/fixtures/features/make_fixture.py. Runs offline.
"""

from __future__ import annotations

import dataclasses
from datetime import timedelta

import numpy as np
import polars as pl
import pytest
from polars.testing import assert_frame_equal

from ffml.config import PROJECT_ROOT
from ffml.features.build import build_all
from ffml.features.manifest import columns, dtype_name, fingerprint_json
from ffml.features.player import player_events
from ffml.features.spec import FEATURE_VERSION
from ffml.features.targets import KEYS
from ffml.features.team import team_events

FEATURE_FIXTURES = PROJECT_ROOT / "tests" / "fixtures" / "features"
DATASETS = ("player_week", "team_week", "dst_week")
ROW_KEYS = {
    "player_week": ["player_id", "season", "week"],
    "team_week": ["team", "season", "week"],
    "dst_week": ["team", "season", "week"],
}
GAME_KEY_COLS = {
    "player_id",
    "season",
    "week",
    "game_id",
    "team",
    "opponent",
    "position",
    "game_type",
    "home",
    "kickoff_utc",
    "row_source",
}
SHIFT_TOL = 0.10
SHIFT_MIN_ROWS = 30


def _assert_same_rows(a: pl.DataFrame, b: pl.DataFrame, keys: list[str]) -> None:
    a = a.sort(keys)
    b = b.sort(keys)
    assert a.height == b.height
    assert_frame_equal(a, b, check_exact=True)
    assert a.hash_rows().to_list() == b.hash_rows().to_list()  # byte-level equality of every row


# --------------------------------------------------------------------------- #
# 1. Truncation equality
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("week", [2, 4, 6])
def test_truncation_equality(fixture_ctx, fixture_build, cutoff_of, week):
    cut = cutoff_of(2023, week)
    truncated = build_all(fixture_ctx.truncate(cut))
    for name in DATASETS:
        full = fixture_build[name].filter(pl.col("cutoff_ts") < cut)
        assert full.height > 0
        _assert_same_rows(truncated[name], full, ROW_KEYS[name])


# --------------------------------------------------------------------------- #
# 2. Future-week invariance
# --------------------------------------------------------------------------- #


@pytest.mark.parametrize("week", [1, 3, 5])
def test_future_week_invariance(fixture_ctx, cutoff_of, week):
    through_w = build_all(fixture_ctx.truncate(cutoff_of(2023, week + 1)))
    with_next = build_all(
        fixture_ctx.truncate(cutoff_of(2023, week + 2)) if week + 2 <= 6 else fixture_ctx
    )
    for name in DATASETS:
        old = through_w[name]
        new = with_next[name].filter(pl.col("cutoff_ts") < cutoff_of(2023, week + 1))
        _assert_same_rows(new, old, ROW_KEYS[name])
        assert (
            with_next[name].filter(pl.col("season") == 2023, pl.col("week") == week + 1).height > 0
        )


# --------------------------------------------------------------------------- #
# 3. Outcome scramble
# --------------------------------------------------------------------------- #


def _scramble_week(df: pl.DataFrame, season: int, week: int, seed: int) -> pl.DataFrame:
    rng = np.random.default_rng(seed)
    mask = (df["season"] == season) & (df["week"] == week)
    idx = np.flatnonzero(mask.to_numpy())
    out = df
    for c in df.columns:
        if c in GAME_KEY_COLS or not (df.schema[c].is_numeric() or df.schema[c] == pl.Boolean):
            continue
        series = df[c].clone()
        perm = idx[rng.permutation(len(idx))]
        out = out.with_columns(series.scatter(idx, df[c].gather(perm)).alias(c))
    return out


@pytest.mark.parametrize("week", [1, 4])
def test_outcome_scramble(fixture_ctx, fixture_build, week):
    ctx = fixture_ctx
    scrambled_ctx = ctx.with_tables(
        fact_player_game=_scramble_week(ctx.player_game, 2023, week, 1),
        fact_team_game=_scramble_week(ctx.team_game, 2023, week, 2),
        fact_dst_game=_scramble_week(ctx.dst_game, 2023, week, 3),
    )
    scrambled = build_all(scrambled_ctx)
    for name in DATASETS:
        inputs = [c.name for c in columns(name) if c.kind != "target"]
        targets = [c.name for c in columns(name) if c.kind == "target"]
        sel = (pl.col("season") == 2023) & (pl.col("week") == week)
        a = fixture_build[name].filter(sel)
        b = scrambled[name].filter(sel)
        assert a.height > 0
        _assert_same_rows(b.select(inputs), a.select(inputs), ROW_KEYS[name])
        # the scramble really changed the outcomes of that week
        ya = a.sort(ROW_KEYS[name]).select(targets)
        yb = b.sort(ROW_KEYS[name]).select(targets)
        assert not ya.equals(yb)


# --------------------------------------------------------------------------- #
# 4. Shift check
# --------------------------------------------------------------------------- #


def _per_game_value(events: pl.DataFrame, source: list[str]) -> pl.Expr:
    num = pl.col(source[0]).cast(pl.Float64)
    if len(source) == 1:
        return num
    den = pl.col(source[1]).cast(pl.Float64)
    return pl.when(den > 0).then(num / den).otherwise(None)


def _shift_violations(
    rows: pl.DataFrame, events: pl.DataFrame, entity: str, row_entity: str, cols
) -> list[tuple]:
    """For each rolling feature: corr with the same-game raw stat vs with the previous game's."""
    ev = events.sort(entity, "kickoff_utc")
    bad = []
    checked = 0
    for c in cols:
        x = _per_game_value(ev, list(c.source)).alias("_x")
        per = ev.select(entity, "season", "week", x).with_columns(
            pl.col("_x").shift(1).over(entity).alias("_x_prev")
        )
        j = rows.select(
            pl.col(row_entity).alias(entity),
            "season",
            "week",
            pl.col(c.name).cast(pl.Float64).alias("_f"),
        ).join(per, on=[entity, "season", "week"], how="inner")
        j = j.drop_nulls(["_f", "_x", "_x_prev"])
        if (
            j.height < SHIFT_MIN_ROWS
            or j["_f"].std() == 0
            or j["_x"].std() == 0
            or j["_x_prev"].std() == 0
        ):
            continue
        same = j.select(pl.corr("_f", "_x")).item()
        lag = j.select(pl.corr("_f", "_x_prev")).item()
        checked += 1
        if same > lag + SHIFT_TOL:
            bad.append((c.name, round(same, 3), round(lag, 3), j.height))
    return bad, checked


def _rolling_cols(name: str, groups: tuple[str, ...]):
    return [
        c
        for c in columns(name)
        if c.kind == "feature"
        and c.group in groups
        and c.source is not None
        and c.window in ("l1", "l3", "l5", "ew", "std", "car")
        and c.variant in ("raw", "shrunk")
    ]


def test_shift_check_player_features(fixture_ctx, fixture_build):
    rows = fixture_build["player_week"].filter(pl.col("y_played") == 1)
    ev = player_events(fixture_ctx)
    bad, checked = _shift_violations(
        rows, ev, "player_id", "player_id", _rolling_cols("player_week", ("opp", "eff"))
    )
    assert checked >= 150
    assert bad == []


def test_shift_check_team_features(fixture_ctx, fixture_build):
    rows = fixture_build["team_week"]
    ev = team_events(fixture_ctx)
    bad_team, n_team = _shift_violations(
        rows, ev, "team", "team", _rolling_cols("team_week", ("team",))
    )
    bad_def, n_def = _shift_violations(
        rows, ev, "team", "opponent", _rolling_cols("team_week", ("def",))
    )
    assert n_team >= 80 and n_def >= 80
    assert bad_team == [] and bad_def == []


def test_shift_check_detects_a_leaky_feature(fixture_ctx, fixture_build):
    """Positive control: a column equal to the same-game target share must be flagged."""
    ev = player_events(fixture_ctx)
    leaky = ev.select(
        "player_id",
        "season",
        "week",
        (pl.col("targets") / pl.col("team_targets")).alias("opp__leaky__ew"),
    )
    rows = (
        fixture_build["player_week"]
        .filter(pl.col("y_played") == 1)
        .join(leaky, on=["player_id", "season", "week"])
    )
    col = next(c for c in columns("player_week") if c.name == "opp__target_share__ew")
    fake = dataclasses.replace(col, name="opp__leaky__ew")
    bad, checked = _shift_violations(rows, ev, "player_id", "player_id", [fake])
    assert checked == 1 and len(bad) == 1


# --------------------------------------------------------------------------- #
# 5. Timestamp audit
# --------------------------------------------------------------------------- #


def test_timestamp_audit(fixture_ctx, fixture_build):
    hour = timedelta(hours=1)
    for name in DATASETS:
        df = fixture_build[name]
        assert (df["cutoff_ts"] <= df["kickoff_utc"]).all()
        assert (df["asof_ts"] == df["cutoff_ts"] - hour).all()
        for c in columns(name):
            if c.kind != "meta":
                continue
            used = df.filter(pl.col(c.name).is_not_null())
            assert used.height > 0, c.name
            assert (used[c.name] < used["cutoff_ts"]).all(), c.name
            if "depth_snapshot" in c.name:
                assert (used[c.name] <= used["asof_ts"]).all(), c.name

    # the depth snapshot used is the latest one at or before asof_ts for that team
    pw = fixture_build["player_week"].filter(pl.col("meta__depth_snapshot_ts").is_not_null())
    snaps = fixture_ctx.depth.select("team", "snapshot_ts").unique()
    later = (
        pw.select("team", "asof_ts", "meta__depth_snapshot_ts")
        .unique()
        .join(snaps, on="team")
        .filter(
            (pl.col("snapshot_ts") > pl.col("meta__depth_snapshot_ts"))
            & (pl.col("snapshot_ts") <= pl.col("asof_ts"))
        )
    )
    assert later.height == 0
    exists = (
        pw.select("team", pl.col("meta__depth_snapshot_ts").alias("snapshot_ts"))
        .unique()
        .join(snaps, on=["team", "snapshot_ts"], how="anti")
    )
    assert exists.height == 0
    # lagged injury information was published before the cutoff (report_modified_at
    # where nflverse has it, else the last kickoff of the report's week); the
    # table's own snapshot_ts is the ingest time and is not an information time
    inj = pw.filter(pl.col("meta__inj_info_ts").is_not_null())
    assert inj.height > 0
    assert (inj["meta__inj_info_ts"] < inj["cutoff_ts"]).all()


# --------------------------------------------------------------------------- #
# 6. Schema fingerprint
# --------------------------------------------------------------------------- #


def test_schema_fingerprint(fixture_build):
    pinned = (FEATURE_FIXTURES / f"feature_manifest_{FEATURE_VERSION}.json").read_text(
        encoding="utf-8"
    )
    assert fingerprint_json() == pinned, (
        "feature columns changed: bump FEATURE_VERSION and regenerate the pinned manifest "
        "with `python -m ffml.features.manifest`"
    )
    for name in DATASETS:
        df = fixture_build[name]
        expected = [(c.name, c.dtype) for c in columns(name)]
        actual = [(n, dtype_name(t)) for n, t in df.schema.items()]
        assert actual == expected
        assert df.columns[: len(KEYS[name])] == KEYS[name]
        assert (df["feature_version"] == FEATURE_VERSION).all()
