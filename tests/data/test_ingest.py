"""Tests for nflverse ingestion and schema drift handling (DE-2.2)."""

from __future__ import annotations

import hashlib
from pathlib import Path

import polars as pl
import pytest
from tenacity import wait_none

from ffml.data import ingest as ingest_mod
from ffml.data import nflverse
from ffml.data.ingest import (
    IngestError,
    SchemaDriftError,
    load_pins,
    run_ingest,
)
from ffml.data.nflverse import DATASETS, DatasetSpec, SeasonNotAvailable, fetch
from ffml.data.store import SnapshotStore, schema_hash


def weekly_frame(season: int, *, drop: str | None = None, extra: bool = False) -> pl.DataFrame:
    df = pl.DataFrame(
        {
            "player_id": ["00-001", "00-002", None],
            "season": [season] * 3,
            "week": [1.0, 1.0, 2.0],
            "yards": [10.0, 5.0, 1.0],
        }
    )
    if extra:
        df = df.with_columns(pl.lit("new").alias("new_col"))
    if drop:
        df = df.drop(drop)
    return df


@pytest.fixture
def registry(monkeypatch) -> dict[str, DatasetSpec]:
    reg: dict[str, DatasetSpec] = {}
    monkeypatch.setattr(ingest_mod, "DATASETS", reg)
    return reg


@pytest.fixture
def store(tmp_path) -> SnapshotStore:
    return SnapshotStore(tmp_path / "raw")


@pytest.fixture
def pin_path(tmp_path) -> Path:
    return tmp_path / "expected_schemas.json"


def add_weekly(registry, loader, name="fake_weekly", **overrides) -> DatasetSpec:
    kwargs = dict(
        name=name,
        loader=loader,
        min_season=2024,
        key_columns=["player_id", "season", "week"],
        casts={"week": pl.Int32},
        drop_null_keys=["player_id"],
    )
    kwargs.update(overrides)
    spec = DatasetSpec(**kwargs)
    registry[name] = spec
    return spec


def add_schedule(registry, final: set[int] = frozenset(), name="schedules") -> DatasetSpec:
    """A fake schedule: one REG game per season, final (``result`` set) for the seasons in ``final``."""

    def loader(season=None, **kwargs):
        return pl.DataFrame({
            "game_id": [f"{season}_01_A_B"], "season": [season], "week": [1], "game_type": ["REG"],
            "result": [3 if season in final else None],
        }, schema_overrides={"result": pl.Int64})

    spec = DatasetSpec(name=name, loader=loader, min_season=2016, key_columns=["game_id"], casts={},
                       drop_null_keys=["game_id"])
    registry[name] = spec
    return spec


class Loader:
    """Configurable fake loader that records its calls."""

    def __init__(self, **options):
        self.options = options
        self.calls: list[int | None] = []

    def __call__(self, season=None, **kwargs):
        self.calls.append(season)
        fail = self.options.get("fail", {})
        if season in fail:
            raise fail[season]
        return weekly_frame(
            season,
            drop=self.options.get("drop"),
            extra=self.options.get("extra", False),
        )


def test_first_run_pins_schema_and_writes_snapshots(registry, store, pin_path) -> None:
    add_weekly(registry, Loader())
    registry["fake_dim"] = DatasetSpec(
        name="fake_dim", loader=lambda **kw: pl.DataFrame({"id": [1, 2]}), seasonal=False
    )

    results = run_ingest([2023, 2024, 2025], None, store=store, pin_path=pin_path)

    weekly = results["fake_weekly"]
    assert weekly.rows_per_partition == {"2024": 2, "2025": 2}  # null player_id rows dropped
    assert weekly.dropped_null_rows == {"player_id": 2}
    assert weekly.drift.status == "pinned"
    assert weekly.latest_updated is True
    manifest = store.read_manifest("fake_weekly")
    assert manifest["seasons"] == [2024, 2025]  # 2023 is below min_season
    assert manifest["columns"]["week"] == "Int32"  # cast applied
    assert manifest["loader"].endswith("Loader")
    assert manifest["extra"]["dropped_null_rows"] == {"player_id": 2}
    assert manifest["extra"]["key_columns"] == ["player_id", "season", "week"]
    assert manifest["source_license"] == nflverse.NFLVERSE_LICENSE

    assert store.read("fake_dim").height == 2
    assert store.read_manifest("fake_dim")["partitions"] == ["all"]

    pins = load_pins(pin_path)
    assert set(pins) == {"fake_weekly", "fake_dim"}
    assert pins["fake_weekly"]["season"] == 2025
    assert pins["fake_weekly"]["columns"] == manifest["columns"]
    assert pins["fake_weekly"]["schema_hash"] == schema_hash(manifest["columns"])
    assert pins["fake_dim"]["season"] is None


def test_added_column_warns_and_updates_pin(registry, store, pin_path, caplog) -> None:
    add_weekly(registry, Loader())
    run_ingest([2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)

    add_weekly(registry, Loader(extra=True))
    with caplog.at_level("WARNING"):
        results = run_ingest([2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)

    assert results["fake_weekly"].drift.status == "added"
    assert results["fake_weekly"].drift.added == ["new_col"]
    assert "new columns" in caplog.text
    assert "new_col" in load_pins(pin_path)["fake_weekly"]["columns"]
    assert len(store.list_snapshots("fake_weekly")) == 2


def test_removed_column_fails_and_keeps_latest(registry, store, pin_path) -> None:
    add_weekly(registry, Loader())
    run_ingest([2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)
    sid1 = store.latest_id("fake_weekly")
    pins_before = load_pins(pin_path)

    add_weekly(registry, Loader(drop="yards"))
    with pytest.raises(SchemaDriftError) as excinfo:
        run_ingest([2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)

    assert "removed ['yards']" in str(excinfo.value)
    assert isinstance(excinfo.value.failures["fake_weekly"], SchemaDriftError)
    assert store.latest_id("fake_weekly") == sid1  # LATEST not advanced
    snapshots = store.list_snapshots("fake_weekly")
    assert len(snapshots) == 2  # the drifted snapshot is kept for inspection
    drifted = store.read_manifest("fake_weekly", snapshot_id=snapshots[-1])
    assert "schema_drift" in drifted["extra"]
    assert load_pins(pin_path) == pins_before


def test_retyped_column_fails(registry, store, pin_path) -> None:
    add_weekly(registry, Loader())
    run_ingest([2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)

    def retyped(season=None, **kw):
        return weekly_frame(season).with_columns(pl.col("yards").cast(pl.String))

    add_weekly(registry, retyped)
    with pytest.raises(SchemaDriftError, match="retyped yards: Float64 -> String"):
        run_ingest([2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)


def test_historical_layout_only_warns(registry, store, pin_path, caplog) -> None:
    add_weekly(registry, Loader())
    run_ingest([2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)

    # a backfill of an older season with a different layout must not fail the pin
    add_weekly(registry, Loader(drop="yards"))
    with caplog.at_level("WARNING"):
        results = run_ingest([2024], ["fake_weekly"], store=store, pin_path=pin_path)
    assert results["fake_weekly"].drift.status == "historical_mismatch"
    assert results["fake_weekly"].latest_updated is True
    assert "not treated as drift" in caplog.text
    assert load_pins(pin_path)["fake_weekly"]["season"] == 2025


def test_seasons_before_data_start_are_skipped(registry, store, pin_path, caplog) -> None:
    unavailable = ValueError("Season must be between 2024 and 2026")
    add_weekly(registry, Loader(fail={2022: unavailable, 2023: unavailable}), min_season=2016)

    with caplog.at_level("WARNING"):
        results = run_ingest(
            [2022, 2023, 2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path
        )

    assert results["fake_weekly"].skipped_seasons == [2022, 2023]
    assert results["fake_weekly"].rows_per_partition == {"2024": 2, "2025": 2}
    assert "not published upstream" in caplog.text
    manifest = store.read_manifest("fake_weekly")
    assert manifest["extra"]["skipped_seasons"] == [2022, 2023]
    assert manifest["seasons"] == [2024, 2025]


def test_missing_season_after_a_loaded_one_is_a_gap(registry, store, pin_path) -> None:
    not_found = ConnectionError("Failed to download https://x/2024.parquet: 404 Client Error")
    add_weekly(registry, Loader(fail={2024: not_found}), min_season=2016)

    with pytest.raises(IngestError, match="gap"):
        run_ingest([2023, 2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)
    assert store.list_snapshots("fake_weekly") == []
    assert store.latest_id("fake_weekly") is None
    assert not pin_path.exists()


def test_seasons_to_load_and_the_plan_for_2016_2026_are_unchanged(registry, store, pin_path) -> None:
    """D-122: every real spec requests the same seasons, and a fully published source loads as before."""
    span = list(range(2016, 2027))
    for spec in DATASETS.values():
        expected = [] if not spec.seasonal else [s for s in span if spec.min_season is None or s >= spec.min_season]
        assert spec.seasons_to_load(span) == expected, spec.name
    loader = Loader()
    add_weekly(registry, loader, min_season=2016)
    result = run_ingest(span, ["fake_weekly"], store=store, pin_path=pin_path)["fake_weekly"]
    assert loader.calls == span
    assert sorted(result.rows_per_partition) == [str(s) for s in span]
    assert result.skipped_seasons == [] and result.not_yet_published == []
    extra = store.read_manifest("fake_weekly")["extra"]
    assert "not_yet_published_seasons" not in extra and extra["skipped_seasons"] == []


def test_newest_season_not_yet_published_is_skipped_not_a_gap(registry, store, pin_path, caplog) -> None:
    """D-122 with D-126: 2027 has no final game yet, so its absent file is skipped."""
    not_found = ConnectionError("Failed to download https://x/2027.parquet: 404 Client Error")
    add_weekly(registry, Loader(fail={2027: not_found}), min_season=2016)
    add_schedule(registry, final={2024, 2025, 2026})
    both = ["fake_weekly", "schedules"]
    run_ingest(list(range(2024, 2027)), both, store=store, pin_path=pin_path)  # 2024-2026 published
    with caplog.at_level("WARNING"):
        result = run_ingest(list(range(2024, 2028)), both, store=store, pin_path=pin_path)["fake_weekly"]
    assert result.not_yet_published == [2027] and result.skipped_seasons == [] and result.latest_updated
    assert sorted(result.rows_per_partition) == ["2024", "2025", "2026"]
    assert "not published yet" in caplog.text
    manifest = store.read_manifest("fake_weekly")
    assert manifest["seasons"] == [2024, 2025, 2026] and manifest["extra"]["not_yet_published_seasons"] == [2027]


def test_newest_season_with_a_final_game_and_no_file_is_a_gap(registry, store, pin_path, caplog) -> None:
    """D-126: once a 2027 regular-season game is final, an absent 2027 file fails the dataset as a gap."""
    not_found = ConnectionError("Failed to download https://x/2027.parquet: 404 Client Error")
    add_weekly(registry, Loader(fail={2027: not_found}), min_season=2016)
    add_schedule(registry, final={2024, 2025, 2026, 2027})
    both = ["fake_weekly", "schedules"]  # schedules is ingested first whatever the order asked
    with caplog.at_level("INFO"):
        with pytest.raises(IngestError, match="gap") as excinfo:
            run_ingest(list(range(2024, 2028)), both, store=store, pin_path=pin_path)
    assert set(excinfo.value.failures) == {"fake_weekly"}
    assert "a regular-season game of that season is final (D-126)" in str(excinfo.value)
    assert store.latest_id("fake_weekly") is None and store.latest_id("schedules") is not None
    assert caplog.text.index("schedules season 2024") < caplog.text.index("fake_weekly season 2024")
    # with no schedule at all, nothing is skipped as not yet published
    registry.clear()
    add_weekly(registry, Loader(fail={2027: not_found}), min_season=2016, name="other_weekly")
    with pytest.raises(IngestError, match="gap"):
        run_ingest([2026, 2027], ["other_weekly"], store=SnapshotStore(store.root / "empty"), pin_path=pin_path)


def test_missing_season_before_the_newest_published_is_still_a_gap(registry, store, pin_path) -> None:
    not_found = ConnectionError("Failed to download https://x/2025.parquet: 404 Client Error")
    # 2025 absent, 2026 published: a gap whether 2026 is known from an earlier snapshot or loads after it
    add_weekly(registry, Loader(fail={2025: not_found}), min_season=2016)
    with pytest.raises(IngestError, match="gap"):
        run_ingest([2024, 2025, 2026], ["fake_weekly"], store=store, pin_path=pin_path)
    assert store.latest_id("fake_weekly") is None
    registry.clear()
    add_weekly(registry, Loader(), min_season=2016)
    run_ingest([2024, 2025, 2026], ["fake_weekly"], store=store, pin_path=pin_path)
    registry.clear()
    loader = Loader(fail={2025: not_found})
    add_weekly(registry, loader, min_season=2016)
    before = store.latest_id("fake_weekly")
    with pytest.raises(IngestError, match="gap"):
        run_ingest([2024, 2025, 2026], ["fake_weekly"], store=store, pin_path=pin_path)
    assert loader.calls == [2024, 2025]  # known from the earlier snapshot: fails at once, as before D-122
    assert store.latest_id("fake_weekly") == before


def test_all_seasons_missing_fails(registry, store, pin_path) -> None:
    unavailable = ValueError("Season must be between 2030 and 2031")
    add_weekly(registry, Loader(fail={2024: unavailable, 2025: unavailable}))
    with pytest.raises(IngestError, match="no season loaded"):
        run_ingest([2024, 2025], ["fake_weekly"], store=store, pin_path=pin_path)
    assert store.list_snapshots("fake_weekly") == []


def test_run_continues_after_a_dataset_fails(registry, store, pin_path) -> None:
    def broken(season=None, **kw):
        raise RuntimeError("boom")

    add_weekly(registry, broken, name="broken")
    add_weekly(registry, Loader(), name="healthy")

    with pytest.raises(IngestError) as excinfo:
        run_ingest([2024], None, store=store, pin_path=pin_path)
    assert set(excinfo.value.failures) == {"broken"}
    assert store.latest_id("healthy") is not None
    assert store.latest_id("broken") is None
    assert set(load_pins(pin_path)) == {"healthy"}


def test_unknown_dataset_and_empty_seasons(store, pin_path) -> None:
    with pytest.raises(ValueError, match="unknown dataset"):
        run_ingest([2026], ["nope"], store=store, pin_path=pin_path)
    with pytest.raises(ValueError):
        run_ingest([], None, store=store, pin_path=pin_path)


def test_fetch_retries_transient_errors_but_not_404(monkeypatch) -> None:
    monkeypatch.setattr(
        nflverse, "_call_loader", nflverse._call_loader.retry_with(wait=wait_none())
    )

    flaky = Loader(fail={2024: ConnectionError("connection reset")})
    spec = DatasetSpec(name="flaky", loader=flaky)
    with pytest.raises(ConnectionError):
        fetch(spec, 2024)
    assert flaky.calls == [2024, 2024, 2024]  # three attempts

    gone = Loader(fail={2024: ConnectionError("Failed to download x: 404 Client Error")})
    spec = DatasetSpec(name="gone", loader=gone)
    with pytest.raises(SeasonNotAvailable):
        fetch(spec, 2024)
    assert gone.calls == [2024]  # not retried

    ranged = Loader(fail={2016: ValueError("Season must be between 2022 and 2026")})
    with pytest.raises(SeasonNotAvailable):
        fetch(DatasetSpec(name="ranged", loader=ranged), 2016)
    assert ranged.calls == [2016]


def test_registry_matches_data_sources() -> None:
    expected = {
        "pbp": 2016,
        "player_stats": 2016,
        "team_stats": 2016,
        "schedules": 2016,
        "rosters_weekly": 2016,
        "injuries": 2016,
        "depth_charts": 2016,
        "snap_counts": 2016,
        "nextgen_passing": 2016,
        "nextgen_rushing": 2016,
        "nextgen_receiving": 2016,
        "ff_opportunity": 2016,
        "ftn_charting": 2022,
        "pfr_advstats_rec": 2018,
        "pfr_advstats_rush": 2018,
        "pfr_advstats_pass": 2018,
    }
    for name, min_season in expected.items():
        assert DATASETS[name].seasonal and DATASETS[name].min_season == min_season, name
    for name in ("players", "ff_playerids", "teams", "ff_rankings_all"):
        assert not DATASETS[name].seasonal, name
    assert DATASETS["ff_rankings_all"].restricted is True
    assert DATASETS["ff_rankings_all"].store_path == "restricted/ff_rankings_all"
    assert "private evaluation only" in DATASETS["ff_rankings_all"].source_license
    assert DATASETS["ftn_charting"].source_license.startswith("CC-BY-SA-4.0")
    assert DATASETS["ff_opportunity"].casts == {"season": pl.Int32, "week": pl.Int32}
    assert DATASETS["player_stats"].drop_null_keys == ["player_id"]
    assert DATASETS["pbp"].loader_name == "nflreadpy.load_pbp"
    assert DATASETS["pbp"].seasons_to_load([2010, 2016, 2026]) == [2016, 2026]
    assert all(not spec.restricted for name, spec in DATASETS.items() if name != "ff_rankings_all")


@pytest.mark.network
def test_network_ingest_teams_and_schedules_2026(tmp_settings, tmp_path) -> None:
    results = run_ingest([2026], ["teams", "schedules"], pin_path=tmp_path / "pins.json")
    store = SnapshotStore(tmp_settings.raw_dir)

    schedules = store.read_manifest("schedules")
    assert schedules["rows"]["total"] == 272
    assert schedules["rows"]["per_season"] == {"2026": 272}
    assert schedules["seasons"] == [2026]
    assert schedules["loader"] == "nflreadpy.load_schedules"
    assert schedules["source_license"] == "CC-BY-4.0 (nflverse)"
    assert schedules["restricted"] is False
    assert schedules["nflreadpy_version"] == "0.1.5"
    frame = store.read("schedules")
    assert frame.height == 272
    assert schedules["schema_hash"] == schema_hash(frame)
    part = store.partition_path("schedules", schedules["snapshot_id"], 2026)
    assert part.relative_to(tmp_settings.raw_dir).as_posix() == (
        f"schedules/snapshot={schedules['snapshot_id']}/season=2026/part-0.parquet"
    )
    assert schedules["file_sha256"]["2026"] == hashlib.sha256(part.read_bytes()).hexdigest()

    teams = store.read_manifest("teams")
    assert teams["partitions"] == ["all"]
    assert teams["rows"]["total"] == 36
    assert store.read("teams").height == 36
    assert results["teams"].drift.status == "pinned"
    assert set(load_pins(tmp_path / "pins.json")) == {"teams", "schedules"}


def test_default_pin_path_is_the_working_copy_not_the_tracked_file(
    registry, store, tmp_settings, tmp_path, monkeypatch
) -> None:
    """A routine ingest never rewrites the tracked pin file, so a pinned worktree stays clean."""
    import shutil

    add_weekly(registry, Loader(extra=True))
    tracked = ingest_mod.SCHEMA_PIN_PATH
    before = hashlib.sha256(tracked.read_bytes()).hexdigest()
    run_ingest([2024, 2025], ["fake_weekly"], store=store)  # no pin_path: the working copy
    working = tmp_settings.schema_pin_path
    assert working == tmp_settings.data_dir / "schema_pins" / "expected_schemas.json"
    pins = load_pins(working)
    assert "new_col" in pins["fake_weekly"]["columns"]
    assert "schedules" in pins  # seeded from the tracked file
    assert hashlib.sha256(tracked.read_bytes()).hexdigest() == before
    # a deliberate re-pin goes to the tracked file (a temporary copy stands in for it here)
    copy = tmp_path / "tracked.json"
    shutil.copyfile(tracked, copy)
    monkeypatch.setattr(ingest_mod, "SCHEMA_PIN_PATH", copy)
    run_ingest([2024, 2025], ["fake_weekly"], store=store, update_schema_pin=True)
    assert "fake_weekly" in load_pins(copy)
    assert load_pins(working)["fake_weekly"] == load_pins(copy)["fake_weekly"]  # mirrored
