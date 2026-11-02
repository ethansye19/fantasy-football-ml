"""Generate ``docs/DATA_DICTIONARY.md`` from the LATEST snapshots (DE-2.8).

The dictionary has one section per non-restricted processed table
(description from :data:`TABLE_DESCRIPTIONS`; per column: name, dtype, null
share, one example value), a summary table of the raw datasets (name,
loader, seasons, rows, licence, all from the manifests) and a one-line note
naming the restricted datasets, which are private (D-007) and therefore not
documented column by column.

Generation stays well under a minute: everything but the column statistics
comes from manifests, and null shares and examples are computed on a sample
of at most :data:`SAMPLE_ROWS` rows per table, spread evenly over its
partitions so that layout differences between seasons show up.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

import polars as pl

from ffml.config import PROJECT_ROOT, Settings, get_settings
from ffml.data.store import SnapshotStore
from ffml.data.warehouse import is_restricted_dataset

log = logging.getLogger(__name__)

DEFAULT_PATH = PROJECT_ROOT / "docs" / "DATA_DICTIONARY.md"
SAMPLE_ROWS = 50_000
EXAMPLE_MAX_CHARS = 40

TABLE_DESCRIPTIONS: dict[str, str] = {
    "dim_team": "One row per current team code plus alias rows mapping historical codes "
    "(OAK, SD, STL, JAC) to the canonical ones; colours and logos for the UI.",
    "dim_player": "Player dimension keyed by nflverse `gsis_id` with the Sleeper, ESPN, "
    "Yahoo, PFR and FantasyPros ids from the union of `ff_playerids`, weekly rosters and "
    "the Sleeper players map (D-022).",
    "dim_game": "One row per scheduled game with `kickoff_utc` built from gameday and "
    "gametime (ET), closing Vegas lines, venue, rest days and results.",
    "fact_player_game": "One row per rostered QB/RB/WR/TE/K per game (including players "
    "who did not play): usage, efficiency, situational opportunities, expected points, "
    "Next Gen Stats and kicking; key `(player_id, season, week)`.",
    "fact_team_game": "One row per team-game with volume, pace, pass-rate, efficiency, "
    "scoring and the mirrored `allowed_*` defensive view; key `(team, season, week)`.",
    "fact_dst_game": "Team defense and special teams scoring inputs per game.",
    "fact_injury_report": "Weekly injury report, one row per `(season, week, player_id, team)`. "
    "Both source layouts are unified; `report_modified_at` is the source timestamp where "
    "nflverse publishes one (2016-2024, D-029) and null otherwise; `snapshot_ts` is the ingest "
    "time of the raw snapshot. Duplicates keep the latest timestamp, then the most severe status.",
    "fact_depth_chart": "Depth charts unified across the 2016-2024 weekly format "
    "(`snapshot_kind = weekly`, `snapshot_ts` = first kickoff of the week minus 48 h) and the "
    "2025+ timestamped format (`snapshot_kind = timestamped`, `(season, week)` assigned from "
    "the schedule; week 0 = offseason). Key `(snapshot_ts, team, player_id, pos_abb)`.",
    "fact_roster_week": "Weekly rosters with status, depth-chart position and string external "
    "ids; key `(season, week, player_id, team)`, so a player traded mid-week has two rows.",
    "fact_ecr_weekly": "RESTRICTED (D-007): FantasyPros weekly expert consensus for private "
    "evaluation only.",
    "sleeper_players": "Daily Sleeper players map (documented subset of columns) with "
    "`gsis_id` stripped of padding and external ids as strings; one row per `sleeper_id`.",
}

#: Preferred section order; anything else follows alphabetically.
TABLE_ORDER = [
    "dim_team",
    "dim_player",
    "dim_game",
    "fact_player_game",
    "fact_team_game",
    "fact_dst_game",
    "fact_injury_report",
    "fact_depth_chart",
    "fact_roster_week",
    "sleeper_players",
]


def _md_cell(text: Any) -> str:
    return str(text).replace("|", "\\|").replace("\n", " ").replace("\r", " ")


def _example_value(series: pl.Series) -> str:
    non_null = series.drop_nulls()
    if non_null.is_empty():
        return ""
    text = str(non_null[0])
    if len(text) > EXAMPLE_MAX_CHARS:
        text = text[: EXAMPLE_MAX_CHARS - 1] + "..."
    return "`" + _md_cell(text).replace("`", "'") + "`"


def sample_snapshot(
    store: SnapshotStore, dataset: str, snapshot_id: str, limit: int = SAMPLE_ROWS
) -> pl.DataFrame:
    """Up to ``limit`` rows spread evenly over the snapshot's partitions."""
    keys = store.partition_keys(dataset, snapshot_id)
    if not keys:
        return pl.DataFrame()
    per_partition = max(1, limit // len(keys))
    frames = [
        pl.read_parquet(store.partition_path(dataset, snapshot_id, key), n_rows=per_partition)
        for key in keys
    ]
    return frames[0] if len(frames) == 1 else pl.concat(frames, how="diagonal_relaxed")


def _season_span(manifest: dict[str, Any]) -> str:
    seasons = manifest.get("seasons") or []
    seasons = [int(s) for s in seasons if str(s).isdigit()]
    if seasons:
        return f"{min(seasons)}-{max(seasons)}" if min(seasons) != max(seasons) else str(seasons[0])
    partitions = manifest.get("partitions") or []
    if partitions == ["all"]:
        return "all"
    if manifest.get("snapshot_date"):
        return f"daily ({manifest['snapshot_date']})"
    return "n/a"


def _rows_total(manifest: dict[str, Any]) -> int | None:
    rows = manifest.get("rows")
    if isinstance(rows, dict):
        rows = rows.get("total")
    return int(rows) if rows is not None else None


def _fmt_int(value: int | None) -> str:
    return f"{value:,}" if value is not None else "n/a"


def _processed_section(store: SnapshotStore, table: str) -> list[str]:
    snapshot_id = store.latest_id(table)
    lines = [f"### {table}", "", TABLE_DESCRIPTIONS.get(table, "No description yet."), ""]
    if snapshot_id is None:
        lines += ["_No snapshot yet._", ""]
        return lines
    try:
        manifest = store.read_manifest(table, snapshot_id)
    except FileNotFoundError:
        manifest = {}
    inputs = (manifest.get("extra") or {}).get("inputs") or {}
    lines.append(
        f"Snapshot `{snapshot_id}`, {_fmt_int(_rows_total(manifest))} rows, "
        f"partitions {', '.join(str(p) for p in manifest.get('partitions', [])) or 'n/a'}."
    )
    if inputs:
        lines.append(
            "Built from raw snapshots: "
            + ", ".join(f"`{k}={v}`" for k, v in sorted(inputs.items()))
            + "."
        )
    lines.append("")
    sample = sample_snapshot(store, table, snapshot_id)
    n = sample.height
    lines += [
        f"| column | dtype | null share (sample of {n:,}) | example |",
        "| --- | --- | ---: | --- |",
    ]
    for name, dtype in sample.schema.items():
        nulls = sample[name].null_count()
        share = f"{nulls / n:.1%}" if n else "n/a"
        lines.append(f"| `{name}` | {_md_cell(dtype)} | {share} | {_example_value(sample[name])} |")
    lines.append("")
    return lines


def _raw_rows(store: SnapshotStore) -> tuple[list[str], list[str]]:
    rows: list[str] = []
    restricted: list[str] = []
    for dataset in store.list_datasets():
        if is_restricted_dataset(dataset):
            restricted.append(dataset)
            continue
        snapshot_id = store.latest_id(dataset)
        if snapshot_id is None:
            continue
        try:
            manifest = store.read_manifest(dataset, snapshot_id)
        except FileNotFoundError:
            manifest = {}
        loader = manifest.get("loader") or manifest.get("source") or "n/a"
        licence = manifest.get("source_license") or manifest.get("terms") or "n/a"
        rows.append(
            f"| `{dataset}` | `{_md_cell(loader)}` | {_season_span(manifest)} | "
            f"{_fmt_int(_rows_total(manifest))} | {_md_cell(licence)} | `{snapshot_id}` |"
        )
    return rows, restricted


def render_data_dictionary(
    raw: SnapshotStore, processed: SnapshotStore, *, generated_at: datetime | None = None
) -> str:
    """Markdown text of the dictionary for the given stores."""
    stamp = (generated_at or datetime.now(UTC)).strftime("%Y-%m-%d %H:%MZ")
    lines = [
        "# Data Dictionary",
        "",
        f"Generated by `ffml dictionary` on {stamp} from the LATEST snapshots under `data/`.",
        "Do not edit by hand; the contracts live in docs/DATA_SCHEMA.md. Null shares and examples",
        f"come from a sample of at most {SAMPLE_ROWS:,} rows per table, spread over its partitions.",
        "",
        "## Processed tables",
        "",
    ]
    tables = [t for t in processed.list_datasets() if not is_restricted_dataset(t)]
    restricted_processed = [t for t in processed.list_datasets() if is_restricted_dataset(t)]
    ordered = [t for t in TABLE_ORDER if t in tables] + sorted(
        t for t in tables if t not in TABLE_ORDER
    )
    if not ordered:
        lines += ["_No processed tables yet; run `ffml process`._", ""]
    for table in ordered:
        lines += _processed_section(processed, table)

    raw_rows, restricted_raw = _raw_rows(raw)
    lines += [
        "## Raw datasets",
        "",
        "| dataset | loader | seasons | rows | licence | snapshot |",
        "| --- | --- | --- | ---: | --- | --- |",
        *raw_rows,
        "",
    ]
    restricted = sorted(restricted_raw + restricted_processed)
    lines += ["## Restricted data (D-007)", ""]
    if restricted:
        lines.append(
            "Private evaluation inputs that exist under `data/raw/restricted/` and "
            "`data/processed/restricted/` and are not documented here, never served by the "
            "API or web app, and never committed: " + ", ".join(f"`{d}`" for d in restricted) + "."
        )
    else:
        lines.append("None present.")
    lines.append("")
    return "\n".join(lines)


def write_data_dictionary(
    path: Path | str | None = None, *, settings: Settings | None = None
) -> Path:
    """Render the dictionary for the configured stores and write it (LF newlines)."""
    settings = settings or get_settings()
    target = Path(path) if path is not None else DEFAULT_PATH
    text = render_data_dictionary(
        SnapshotStore(settings.raw_dir), SnapshotStore(settings.processed_dir)
    )
    target.parent.mkdir(parents=True, exist_ok=True)
    with target.open("w", encoding="utf-8", newline="\n") as handle:
        handle.write(text)
    log.info("data dictionary written to %s (%d bytes)", target, len(text))
    return target


__all__ = [
    "DEFAULT_PATH",
    "SAMPLE_ROWS",
    "TABLE_DESCRIPTIONS",
    "render_data_dictionary",
    "sample_snapshot",
    "write_data_dictionary",
]
