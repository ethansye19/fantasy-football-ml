"""Registry of nflverse datasets ingested through nflreadpy.

Each :class:`DatasetSpec` names the nflreadpy loader, its keyword arguments,
whether the dataset is loaded one season at a time, the first season we keep,
the dtype casts applied on ingest, and the data licence. ``DATASETS`` is the
single place that decides what ``ffml ingest`` downloads.

RESTRICTED DATA
---------------
Specs with ``restricted=True`` (currently ``ff_rankings_all``, the FantasyPros
expert-consensus archive redistributed by DynastyProcess) are written under
``<raw>/restricted/<dataset>/...`` so the path itself says so, and their
manifests carry ``"restricted": true``. Per docs/DECISIONS.md D-007 these
files are private evaluation inputs only: **nothing outside ``ffml/eval`` may
read them** - not the processed tables, not the feature builders, not the API,
not the web app, and no committed report may contain their rows.

Network behaviour
-----------------
``configure_nflreadpy`` turns the nflreadpy cache off (D-016: the raw snapshot
store is the only copy) and applies the project HTTP timeout. ``fetch`` wraps
every loader call in a tenacity retry (3 attempts, exponential backoff) for
transient network errors. A season that nflverse does not publish (HTTP 404,
or nflreadpy's own "Season must be between" range check) raises
:class:`SeasonNotAvailable` instead so that the ingest driver can decide
whether skipping it is acceptable.
"""

from __future__ import annotations

import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

import nflreadpy
import polars as pl
from nflreadpy.config import update_config
from tenacity import (
    before_sleep_log,
    retry,
    retry_if_exception,
    stop_after_attempt,
    wait_exponential,
)

from ffml.config import get_settings

log = logging.getLogger(__name__)

NFLVERSE_LICENSE = "CC-BY-4.0 (nflverse)"
FTN_LICENSE = "CC-BY-SA-4.0 (ftndata.com via nflverse)"
FF_RANKINGS_LICENSE = "unverified; FantasyPros via DynastyProcess; private evaluation only"
RESTRICTED_PREFIX = "restricted"

RETRY_ATTEMPTS = 3


class SeasonNotAvailable(Exception):
    """nflverse publishes no file for this dataset and season (404 or range check)."""


@dataclass(frozen=True)
class DatasetSpec:
    """How one nflverse dataset is fetched and stored."""

    name: str
    loader: Callable[..., pl.DataFrame]
    kwargs: dict[str, Any] = field(default_factory=dict)
    seasonal: bool = True
    min_season: int | None = 2016
    key_columns: list[str] = field(default_factory=list)
    casts: dict[str, pl.DataType] = field(default_factory=dict)
    source_license: str = NFLVERSE_LICENSE
    restricted: bool = False
    #: rows with a null in any of these columns are dropped on ingest (count is logged)
    drop_null_keys: list[str] = field(default_factory=list)

    @property
    def loader_name(self) -> str:
        """``nflreadpy.load_pbp`` style name recorded in manifests."""
        target = self.loader if hasattr(self.loader, "__name__") else type(self.loader)
        module = getattr(target, "__module__", "") or ""
        name = getattr(target, "__qualname__", None) or getattr(target, "__name__", repr(target))
        if module.startswith("nflreadpy"):
            return f"nflreadpy.{name}"
        return f"{module}.{name}" if module else name

    @property
    def store_path(self) -> str:
        """Dataset key inside the snapshot store (restricted data lives under a prefix)."""
        return f"{RESTRICTED_PREFIX}/{self.name}" if self.restricted else self.name

    def seasons_to_load(self, seasons: list[int]) -> list[int]:
        """Requested seasons at or after ``min_season`` (all of them when unset)."""
        if not self.seasonal:
            return []
        if self.min_season is None:
            return sorted(set(seasons))
        return sorted(s for s in set(seasons) if s >= self.min_season)


def _spec(
    name: str,
    loader: Callable[..., pl.DataFrame],
    *,
    seasonal: bool = True,
    min_season: int | None = 2016,
    kwargs: dict[str, Any] | None = None,
    keys: list[str] | None = None,
    casts: dict[str, pl.DataType] | None = None,
    source_license: str = NFLVERSE_LICENSE,
    restricted: bool = False,
    drop_null_keys: list[str] | None = None,
) -> DatasetSpec:
    return DatasetSpec(
        name=name,
        loader=loader,
        kwargs=dict(kwargs or {}),
        seasonal=seasonal,
        min_season=min_season if seasonal else None,
        key_columns=list(keys or []),
        casts=dict(casts or {}),
        source_license=source_license,
        restricted=restricted,
        drop_null_keys=list(drop_null_keys or []),
    )


_SPECS: list[DatasetSpec] = [
    _spec("pbp", nflreadpy.load_pbp, keys=["game_id", "play_id"]),
    _spec(
        "player_stats",
        nflreadpy.load_player_stats,
        kwargs={"summary_level": "week"},
        keys=["player_id", "season", "week"],
        drop_null_keys=["player_id"],
    ),
    _spec(
        "team_stats",
        nflreadpy.load_team_stats,
        kwargs={"summary_level": "week"},
        keys=["team", "season", "week"],
    ),
    _spec("schedules", nflreadpy.load_schedules, keys=["game_id"]),
    _spec("rosters_weekly", nflreadpy.load_rosters_weekly),
    _spec("injuries", nflreadpy.load_injuries),
    # Format differs before and after 2025 (weekly rows vs timestamped `dt`); stored as-is.
    _spec("depth_charts", nflreadpy.load_depth_charts),
    _spec("snap_counts", nflreadpy.load_snap_counts),
    _spec("nextgen_passing", nflreadpy.load_nextgen_stats, kwargs={"stat_type": "passing"}),
    _spec("nextgen_rushing", nflreadpy.load_nextgen_stats, kwargs={"stat_type": "rushing"}),
    _spec("nextgen_receiving", nflreadpy.load_nextgen_stats, kwargs={"stat_type": "receiving"}),
    _spec(
        "ff_opportunity",
        nflreadpy.load_ff_opportunity,
        kwargs={"stat_type": "weekly"},
        # source has String season and Float64 week
        casts={"season": pl.Int32, "week": pl.Int32},
    ),
    _spec("ftn_charting", nflreadpy.load_ftn_charting, min_season=2022, source_license=FTN_LICENSE),
    _spec(
        "pfr_advstats_rec",
        nflreadpy.load_pfr_advstats,
        kwargs={"stat_type": "rec", "summary_level": "week"},
        min_season=2018,
    ),
    _spec(
        "pfr_advstats_rush",
        nflreadpy.load_pfr_advstats,
        kwargs={"stat_type": "rush", "summary_level": "week"},
        min_season=2018,
    ),
    _spec(
        "pfr_advstats_pass",
        nflreadpy.load_pfr_advstats,
        kwargs={"stat_type": "pass", "summary_level": "week"},
        min_season=2018,
    ),
    _spec("players", nflreadpy.load_players, seasonal=False),
    _spec("ff_playerids", nflreadpy.load_ff_playerids, seasonal=False),
    _spec("teams", nflreadpy.load_teams, seasonal=False),
    _spec(
        "ff_rankings_all",
        nflreadpy.load_ff_rankings,
        kwargs={"type": "all"},
        seasonal=False,
        restricted=True,
        source_license=FF_RANKINGS_LICENSE,
    ),
]

DATASETS: dict[str, DatasetSpec] = {spec.name: spec for spec in _SPECS}


def configure_nflreadpy(timeout_seconds: float | None = None) -> None:
    """Disable the nflreadpy cache and set the per-call HTTP timeout from settings."""
    timeout = (
        timeout_seconds if timeout_seconds is not None else get_settings().http_timeout_seconds
    )
    update_config(cache_mode="off", timeout=max(1, int(timeout)))


def is_not_found(exc: BaseException) -> bool:
    """True when the loader failure means "this season does not exist upstream"."""
    message = str(exc)
    if isinstance(exc, ValueError) and "Season must be between" in message:
        return True
    # nflreadpy wraps requests' HTTPError in a builtin ConnectionError
    return isinstance(exc, ConnectionError) and "404" in message


def is_transient(exc: BaseException) -> bool:
    """Network-level failures worth retrying (timeouts, resets, 5xx); never 404s."""
    return isinstance(exc, OSError) and not is_not_found(exc)


@retry(
    reraise=True,
    stop=stop_after_attempt(RETRY_ATTEMPTS),
    wait=wait_exponential(multiplier=1, min=1, max=30),
    retry=retry_if_exception(is_transient),
    before_sleep=before_sleep_log(log, logging.WARNING),
)
def _call_loader(spec: DatasetSpec, season: int | None) -> pl.DataFrame:
    if spec.seasonal:
        if season is None:
            raise ValueError(f"{spec.name} is seasonal; a season is required")
        return spec.loader(season, **spec.kwargs)
    return spec.loader(**spec.kwargs)


def fetch(spec: DatasetSpec, season: int | None = None) -> pl.DataFrame:
    """Download one dataset (one season for seasonal datasets) with retries.

    Raises :class:`SeasonNotAvailable` when nflverse has no file for the season;
    other errors propagate after the retries are exhausted.
    """
    try:
        return _call_loader(spec, season)
    except Exception as exc:
        if is_not_found(exc):
            raise SeasonNotAvailable(f"{spec.name} season {season}: {exc}") from exc
        raise


def clean(spec: DatasetSpec, df: pl.DataFrame) -> tuple[pl.DataFrame, dict[str, Any]]:
    """Apply the spec's null-key drops and dtype casts; return the frame and a report."""
    report: dict[str, Any] = {"dropped_null_rows": {}, "casts": {}}
    for column in spec.drop_null_keys:
        if column not in df.columns:
            continue
        nulls = df[column].null_count()
        if nulls:
            df = df.filter(pl.col(column).is_not_null())
            report["dropped_null_rows"][column] = nulls
            log.warning("%s: dropped %d rows with null %s", spec.name, nulls, column)
    exprs = []
    for column, dtype in spec.casts.items():
        if column not in df.columns:
            continue
        before = str(df.schema[column])
        if before != str(dtype):
            exprs.append(pl.col(column).cast(dtype))
            report["casts"][column] = {"from": before, "to": str(dtype)}
    if exprs:
        df = df.with_columns(exprs)
    return df, report


__all__ = [
    "DATASETS",
    "FF_RANKINGS_LICENSE",
    "FTN_LICENSE",
    "NFLVERSE_LICENSE",
    "RESTRICTED_PREFIX",
    "DatasetSpec",
    "SeasonNotAvailable",
    "clean",
    "configure_nflreadpy",
    "fetch",
    "is_not_found",
    "is_transient",
]
