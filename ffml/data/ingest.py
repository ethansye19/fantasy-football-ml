"""``ffml ingest``: download nflverse datasets into the raw snapshot store.

For every :class:`~ffml.data.nflverse.DatasetSpec` in the registry the driver

1. loads each requested season at or after ``min_season`` (one call per season
   so play-by-play never has to sit in memory all at once),
2. drops null-key rows and applies the spec's dtype casts,
3. writes one snapshot with one partition per season (``season=all`` for
   non-seasonal datasets) and a manifest,
4. compares the newest partition's schema with the pin in
   ``ffml/data/schemas/expected_schemas.json``.

Schema drift policy (DATA_SCHEMA.md section 6, "late schema changes"):

* no pin yet -> the current schema is pinned;
* columns added -> warning, pin updated;
* columns removed or retyped -> :class:`SchemaDriftError`. The snapshot is
  still committed so the data can be inspected, but ``LATEST`` is **not**
  advanced, so downstream steps keep reading the previous snapshot.

The pin records the season it was taken from. Partitions from earlier seasons
whose layout differs (depth charts changed format in 2025) only produce a
warning: the check guards the current season's feed, not history.

Missing seasons: when nflverse has no file for a season (HTTP 404 or the
loader's own range check) and no earlier season of that dataset loaded either,
the season is treated as "before the data start" and skipped with a warning
(recorded under ``extra.skipped_seasons`` in the manifest). A missing season
*after* a season that did load is a gap and fails the dataset.

Failures are per dataset: the run continues with the remaining datasets and
raises at the end (``SchemaDriftError`` when every failure was drift, otherwise
``IngestError``).
"""

from __future__ import annotations

import json
import logging
import time
from dataclasses import asdict, dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from ffml.data.nflverse import (
    DATASETS,
    DatasetSpec,
    SeasonNotAvailable,
    clean,
    configure_nflreadpy,
    fetch,
)
from ffml.data.store import ALL_KEY, SnapshotStore, schema_hash, schema_items

log = logging.getLogger(__name__)

SCHEMA_PIN_PATH = Path(__file__).resolve().parent / "schemas" / "expected_schemas.json"


class IngestError(RuntimeError):
    """One or more datasets failed to ingest."""

    def __init__(self, message: str, failures: dict[str, Exception] | None = None) -> None:
        super().__init__(message)
        self.failures = failures or {}


class SchemaDriftError(IngestError):
    """Columns were removed or changed dtype relative to the pinned schema."""


@dataclass
class DriftReport:
    dataset: str
    status: str  # pinned | unchanged | added | reordered | historical_mismatch | historical_added
    added: list[str] = field(default_factory=list)
    removed: list[str] = field(default_factory=list)
    retyped: list[dict[str, str]] = field(default_factory=list)
    pin_updated: bool = False


@dataclass
class DatasetResult:
    dataset: str
    store_path: str
    snapshot_id: str | None = None
    rows_per_partition: dict[str, int] = field(default_factory=dict)
    total_rows: int = 0
    skipped_seasons: list[int] = field(default_factory=list)
    dropped_null_rows: dict[str, int] = field(default_factory=dict)
    drift: DriftReport | None = None
    latest_updated: bool = False
    seconds: float = 0.0
    error: str | None = None

    def to_dict(self) -> dict[str, Any]:
        return asdict(self)


# --------------------------------------------------------------------------- #
# Schema pins
# --------------------------------------------------------------------------- #


def load_pins(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {}
    return json.loads(path.read_text(encoding="utf-8"))


def save_pins(path: Path, pins: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    ordered = {name: pins[name] for name in sorted(pins)}
    tmp = path.with_name(path.name + ".tmp")
    tmp.write_text(json.dumps(ordered, indent=1) + "\n", encoding="utf-8")
    tmp.replace(path)


def _format_drift(report: DriftReport) -> str:
    parts = []
    if report.removed:
        parts.append(f"removed {report.removed}")
    if report.retyped:
        parts.append(
            "retyped "
            + ", ".join(f"{r['column']}: {r['expected']} -> {r['actual']}" for r in report.retyped)
        )
    if report.added:
        parts.append(f"added {report.added}")
    return "; ".join(parts) or "no differences"


def check_schema(
    dataset: str,
    columns: dict[str, str],
    pins: dict[str, Any],
    *,
    season: int | None = None,
) -> DriftReport:
    """Compare ``columns`` (name -> dtype string) with the pin; mutate ``pins`` as needed.

    Raises :class:`SchemaDriftError` for removed or retyped columns unless the
    checked partition is older than the pinned season (historical layout).
    """
    now = datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")
    current_hash = schema_hash(columns)
    pin = pins.get(dataset)
    if pin is None:
        pins[dataset] = {
            "schema_hash": current_hash,
            "columns": dict(columns),
            "season": season,
            "pinned_at": now,
        }
        log.info("%s: pinned schema (%d columns, season %s)", dataset, len(columns), season)
        return DriftReport(dataset, "pinned", pin_updated=True)

    expected: dict[str, str] = pin["columns"]
    report = DriftReport(
        dataset,
        "unchanged",
        added=[c for c in columns if c not in expected],
        removed=[c for c in expected if c not in columns],
        retyped=[
            {"column": c, "expected": expected[c], "actual": columns[c]}
            for c in columns
            if c in expected and expected[c] != columns[c]
        ],
    )
    pin_season = pin.get("season")
    historical = season is not None and pin_season is not None and season < pin_season

    if report.removed or report.retyped:
        if historical:
            report.status = "historical_mismatch"
            log.warning(
                "%s: season %s layout differs from the pinned season %s (%s); not treated as drift",
                dataset,
                season,
                pin_season,
                _format_drift(report),
            )
            return report
        raise SchemaDriftError(
            f"schema drift in {dataset} (season {season}, pinned season {pin_season}): "
            f"{_format_drift(report)}"
        )

    if report.added:
        if historical:
            report.status = "historical_added"
            log.warning(
                "%s: season %s has columns the pin lacks (%s); pin kept at season %s",
                dataset,
                season,
                report.added,
                pin_season,
            )
            return report
        report.status = "added"
        log.warning("%s: new columns %s; updating the schema pin", dataset, report.added)
    elif current_hash != pin["schema_hash"]:
        report.status = "reordered"
        log.warning("%s: columns reordered relative to the pin; updating the pin", dataset)
    elif season is not None and pin_season is not None and season > pin_season:
        pins[dataset]["season"] = season
        return report
    else:
        return report

    pins[dataset] = {
        "schema_hash": current_hash,
        "columns": dict(columns),
        "season": season if season is not None else pin_season,
        "pinned_at": now,
    }
    report.pin_updated = True
    return report


# --------------------------------------------------------------------------- #
# Per-dataset ingest
# --------------------------------------------------------------------------- #


def _fetched_at() -> str:
    return datetime.now(UTC).replace(microsecond=0).isoformat().replace("+00:00", "Z")


def ingest_dataset(
    spec: DatasetSpec,
    seasons: list[int],
    store: SnapshotStore,
    pins: dict[str, Any],
) -> DatasetResult:
    """Download, clean, snapshot, and schema-check one dataset."""
    started = time.perf_counter()
    result = DatasetResult(dataset=spec.name, store_path=spec.store_path)
    fetched_at = _fetched_at()
    writer = store.begin_snapshot(spec.store_path)
    latest_key: int | str | None = None
    latest_columns: dict[str, str] = {}
    wanted: list[int] = []

    try:
        if spec.seasonal:
            wanted = spec.seasons_to_load(seasons)
            if not wanted:
                raise IngestError(
                    f"{spec.name}: no requested season is at or after min_season {spec.min_season}"
                )
            for season in wanted:
                try:
                    df = fetch(spec, season)
                except SeasonNotAvailable as exc:
                    if latest_key is not None:
                        raise IngestError(
                            f"{spec.name}: season {season} missing upstream after season "
                            f"{latest_key} loaded (gap): {exc}"
                        ) from exc
                    log.warning(
                        "%s: season %s not published upstream; skipped (%s)", spec.name, season, exc
                    )
                    result.skipped_seasons.append(season)
                    continue
                df, report = clean(spec, df)
                for column, count in report["dropped_null_rows"].items():
                    result.dropped_null_rows[column] = (
                        result.dropped_null_rows.get(column, 0) + count
                    )
                writer.write_partition(season, df)
                latest_key = season
                latest_columns = dict(schema_items(df))
                result.rows_per_partition[str(season)] = df.height
                log.info(
                    "%s season %s: %s rows, %d columns",
                    spec.name,
                    season,
                    f"{df.height:,}",
                    df.width,
                )
        else:
            df = fetch(spec)
            df, report = clean(spec, df)
            result.dropped_null_rows.update(report["dropped_null_rows"])
            writer.write_partition(ALL_KEY, df)
            latest_key = ALL_KEY
            latest_columns = dict(schema_items(df))
            result.rows_per_partition[ALL_KEY] = df.height
            log.info("%s: %s rows, %d columns", spec.name, f"{df.height:,}", df.width)

        if latest_key is None:
            raise IngestError(f"{spec.name}: no season loaded (all of {wanted} missing upstream)")

        meta = {
            "loader": spec.loader_name,
            "loader_kwargs": dict(spec.kwargs),
            "fetched_at": fetched_at,
            "source_license": spec.source_license,
            "restricted": spec.restricted,
            "dataset_name": spec.name,
            "key_columns": list(spec.key_columns),
            "casts": {k: str(v) for k, v in spec.casts.items()},
            "skipped_seasons": list(result.skipped_seasons),
            "dropped_null_rows": dict(result.dropped_null_rows),
            "requested_seasons": list(seasons) if spec.seasonal else [],
        }
        result.total_rows = sum(result.rows_per_partition.values())

        try:
            result.drift = check_schema(
                spec.name,
                latest_columns,
                pins,
                season=latest_key if isinstance(latest_key, int) else None,
            )
        except SchemaDriftError as exc:
            meta["schema_drift"] = str(exc)
            result.snapshot_id = writer.commit(meta, update_latest=False)
            result.latest_updated = False
            log.error(
                "%s: %s. Snapshot %s written but LATEST not advanced.",
                spec.name,
                exc,
                result.snapshot_id,
            )
            raise

        result.snapshot_id = writer.commit(meta)
        result.latest_updated = True
    except BaseException:
        writer.abort()
        raise
    finally:
        result.seconds = round(time.perf_counter() - started, 1)
    return result


# --------------------------------------------------------------------------- #
# Driver
# --------------------------------------------------------------------------- #


def select_specs(datasets: list[str] | None) -> list[DatasetSpec]:
    if datasets is None:
        return list(DATASETS.values())
    names = [d.strip() for d in datasets if d.strip()]
    unknown = [d for d in names if d not in DATASETS]
    if unknown:
        raise ValueError(f"unknown dataset(s) {unknown}; available: {sorted(DATASETS)}")
    return [DATASETS[name] for name in names]


def run_ingest(
    seasons: list[int],
    datasets: list[str] | None = None,
    *,
    store: SnapshotStore | None = None,
    pin_path: Path | None = None,
    timeout_seconds: float | None = None,
) -> dict[str, DatasetResult]:
    """Ingest the given datasets (all when ``None``) for ``seasons``.

    Returns per-dataset results. Raises :class:`SchemaDriftError` or
    :class:`IngestError` after all datasets were attempted if any failed.
    """
    if not seasons:
        raise ValueError("seasons is empty")
    seasons = sorted(set(int(s) for s in seasons))
    specs = select_specs(datasets)
    store = store or SnapshotStore()
    pin_path = Path(pin_path) if pin_path is not None else SCHEMA_PIN_PATH
    pins = load_pins(pin_path)
    pins_before = json.dumps(pins, sort_keys=True)

    configure_nflreadpy(timeout_seconds)
    log.info(
        "ingest: %d dataset(s), seasons %s-%s, store %s",
        len(specs),
        seasons[0],
        seasons[-1],
        store.root,
    )

    results: dict[str, DatasetResult] = {}
    failures: dict[str, Exception] = {}
    for spec in specs:
        try:
            results[spec.name] = ingest_dataset(spec, seasons, store, pins)
        except Exception as exc:  # keep going; report at the end
            failures[spec.name] = exc
            results[spec.name] = DatasetResult(
                dataset=spec.name, store_path=spec.store_path, error=f"{type(exc).__name__}: {exc}"
            )
            log.error("%s failed: %s", spec.name, exc, exc_info=not isinstance(exc, IngestError))
        finally:
            if json.dumps(pins, sort_keys=True) != pins_before:
                save_pins(pin_path, pins)
                pins_before = json.dumps(pins, sort_keys=True)

    _log_summary(results)

    if failures:
        summary = "; ".join(f"{name}: {exc}" for name, exc in failures.items())
        if all(isinstance(exc, SchemaDriftError) for exc in failures.values()):
            raise SchemaDriftError(
                f"schema drift in {len(failures)} dataset(s): {summary}", failures
            )
        raise IngestError(f"{len(failures)} dataset(s) failed: {summary}", failures)
    return results


def _log_summary(results: dict[str, DatasetResult]) -> None:
    lines = [f"{'dataset':<20} {'rows':>12} {'parts':>6} {'secs':>7}  snapshot / status"]
    for name, res in results.items():
        if res.error:
            lines.append(f"{name:<20} {'-':>12} {'-':>6} {res.seconds:>7}  FAILED {res.error}")
            continue
        status = res.snapshot_id or "-"
        if not res.latest_updated:
            status += " (LATEST not advanced)"
        if res.skipped_seasons:
            status += f" skipped={res.skipped_seasons}"
        if res.drift and res.drift.status not in ("unchanged", "pinned"):
            status += f" schema={res.drift.status}"
        lines.append(
            f"{name:<20} {res.total_rows:>12,} {len(res.rows_per_partition):>6} {res.seconds:>7}  {status}"
        )
    log.info("ingest summary\n%s", "\n".join(lines))


__all__ = [
    "SCHEMA_PIN_PATH",
    "DatasetResult",
    "DriftReport",
    "IngestError",
    "SchemaDriftError",
    "check_schema",
    "ingest_dataset",
    "load_pins",
    "run_ingest",
    "save_pins",
    "select_specs",
]
