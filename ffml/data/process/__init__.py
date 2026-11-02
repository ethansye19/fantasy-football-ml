"""Processed-table pipeline: ``ffml process``.

Builders run in dependency order. A builder module that does not exist yet is
skipped with a warning so that modules can land independently; a builder that
exists but fails aborts the run. Every written table records the raw snapshot
ids it was built from.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Sequence
from datetime import UTC, datetime

from ffml.data.process.base import RESTRICTED_TABLES, ProcessContext

log = logging.getLogger(__name__)

# (module, function, tables produced) in dependency order.
BUILDERS: list[tuple[str, str, tuple[str, ...]]] = [
    ("ffml.data.process.dims", "build_dims", ("dim_team", "dim_player", "dim_game")),
    ("ffml.data.process.team_game", "build_team_game", ("fact_team_game", "fact_dst_game")),
    ("ffml.data.process.player_game", "build_player_game", ("fact_player_game",)),
    (
        "ffml.data.process.context",
        "build_context",
        ("fact_injury_report", "fact_depth_chart", "fact_roster_week"),
    ),
    ("ffml.data.process.sleeper_players", "build_sleeper_players", ("sleeper_players",)),
    ("ffml.data.process.ecr", "build_ecr", ("fact_ecr_weekly",)),
]


def run_process(
    seasons: Sequence[int],
    tables: Sequence[str] | None = None,
    ctx: ProcessContext | None = None,
) -> ProcessContext:
    """Build and snapshot processed tables for ``seasons``.

    ``tables`` restricts the run to builders that produce at least one of the
    named tables. Returns the context (with ``ctx.tables`` populated) for tests.
    """
    ctx = ctx or ProcessContext.from_settings(list(seasons))
    wanted = set(tables) if tables else None
    started = datetime.now(UTC)
    for module_name, func_name, outputs in BUILDERS:
        if wanted is not None and not wanted.intersection(outputs):
            continue
        try:
            module = importlib.import_module(module_name)
        except ModuleNotFoundError as exc:
            if exc.name == module_name:
                log.warning("builder %s not implemented yet; skipping %s", module_name, outputs)
                continue
            raise
        builder = getattr(module, func_name)
        log.info("building %s via %s.%s", ", ".join(outputs), module_name, func_name)
        result = builder(ctx)
        for table, frames in result.items():
            if not frames:
                log.warning("builder %s returned no partitions for %s", module_name, table)
                continue
            store = ctx.store_for(table)
            rows = sum(df.height for df in frames.values())
            snapshot_id = store.write_snapshot(
                table,
                frames,
                meta={
                    "builder": f"{module_name}.{func_name}",
                    "inputs": dict(ctx.raw_snapshot_ids),
                    "seasons_requested": ctx.seasons,
                    "restricted": table in RESTRICTED_TABLES,
                    "source_license": "derived; see docs/DATA_SOURCES.md",
                    "run_started_at": started.isoformat(),
                },
            )
            ctx.tables[table] = frames
            log.info("wrote %s rows=%d partitions=%d snapshot=%s", table, rows, len(frames), snapshot_id)
    return ctx


__all__ = ["BUILDERS", "ProcessContext", "run_process"]
