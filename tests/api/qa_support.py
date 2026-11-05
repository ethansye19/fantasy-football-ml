"""Helpers for the QA-4.1 API tests (not a test module).

:class:`FileAudit` records the files and directories the process touches while
it is active, through a Python audit hook (``open``, ``os.scandir``,
``os.listdir``, ``glob.glob``) plus wrappers around the Polars and DuckDB
readers, whose I/O happens in native code and raises no audit event. The
hook is installed once per process and does nothing while no audit is active.
"""

from __future__ import annotations

import os
import sys
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
from typing import Any

import pytest

_EVENTS = frozenset({"open", "os.scandir", "os.listdir", "glob.glob"})
_active: list[list[str]] = []
_guard = threading.local()
_installed = False


def _hook(event: str, args: tuple[Any, ...]) -> None:
    if not _active or event not in _EVENTS or getattr(_guard, "busy", False):
        return
    _guard.busy = True
    try:
        target = args[0] if args else None
        if isinstance(target, bytes):
            target = os.fsdecode(target)
        if isinstance(target, str | os.PathLike):
            for sink in _active:
                sink.append(os.fspath(target))
    finally:
        _guard.busy = False


def _install() -> None:
    global _installed
    if not _installed:
        sys.addaudithook(_hook)
        _installed = True


def _wrap(module: Any, name: str, sink: list[str], mp: pytest.MonkeyPatch) -> None:
    original = getattr(module, name, None)
    if original is None:
        return

    def wrapper(source: Any = None, *args: Any, **kwargs: Any) -> Any:
        if isinstance(source, str | os.PathLike):
            sink.append(os.fspath(source))
        elif isinstance(source, list | tuple):
            sink.extend(os.fspath(s) for s in source if isinstance(s, str | os.PathLike))
        return original(source, *args, **kwargs)

    mp.setattr(module, name, wrapper)


@contextmanager
def file_audit() -> Iterator[list[str]]:
    """Collect every path opened, listed or read by Polars/DuckDB in the block."""
    import duckdb
    import polars as pl

    _install()
    sink: list[str] = []
    mp = pytest.MonkeyPatch()
    for name in ("read_parquet", "scan_parquet", "read_ipc", "scan_ipc", "read_csv",
                 "scan_csv", "read_json", "read_ndjson", "scan_ndjson"):
        _wrap(pl, name, sink, mp)
    _wrap(duckdb, "connect", sink, mp)
    _active.append(sink)
    try:
        yield sink
    finally:
        _active.remove(sink)
        mp.undo()


def under(path: str, root: Path) -> bool:
    try:
        Path(path).resolve().relative_to(root.resolve())
    except (ValueError, OSError):
        return False
    return True


__all__ = ["file_audit", "under"]
