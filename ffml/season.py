"""Season and week resolution from the schedule table (DE-2.7).

The app never derives "what week is it" from the calendar. It looks at the
nflverse schedule: the projection week is the earliest regular-season week
with a game whose ``result`` is still null, and a week in which some games are
final and others are not is a first-class state (actuals for the finished
games, projections for the rest).

Only these schedule columns are used: ``season``, ``game_type``, ``week``,
``gameday``, ``gametime`` (Eastern, may be null), ``result``, ``home_team``,
``away_team``, ``game_id``. ``kickoff_utc`` is built from ``gameday`` and
``gametime`` in America/New_York; a null ``gametime`` is treated as 13:00 ET.

Rules
-----
* candidate season: the largest season whose first regular-season kickoff
  minus 45 days is at or before ``now``; if none qualifies, the smallest
  season in the table;
* ``mode``: ``regular`` if any REG game has a null result; ``postseason`` if
  all REG games are final and some non-REG game is not; otherwise
  ``offseason`` (this includes the short window after week 18 before the
  playoff games appear in the schedule);
* ``projection_week``: smallest REG week with any null result (None outside
  regular mode); ``week_in_progress``: that week also has a final game;
* ``completed_weeks``: REG weeks in which every game is final;
* ``next_kickoff_utc``: earliest kickoff strictly after ``now`` (any game
  type); ``last_final_kickoff_utc``: latest kickoff among final games.
"""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
from typing import Any, Literal

import polars as pl

SCHEDULE_COLUMNS = (
    "season",
    "game_type",
    "week",
    "gameday",
    "gametime",
    "result",
    "home_team",
    "away_team",
    "game_id",
)
ET_ZONE = "America/New_York"
DEFAULT_KICKOFF_ET = "13:00"
SEASON_LEAD_DAYS = 45
REGULAR = "REG"

Mode = Literal["regular", "postseason", "offseason"]


@dataclass(frozen=True)
class SeasonState:
    """Resolved season, mode, and week bookkeeping at ``as_of_utc``."""

    season: int
    mode: Mode
    projection_week: int | None
    week_in_progress: bool
    completed_weeks: tuple[int, ...]
    last_completed_week: int | None
    next_kickoff_utc: datetime | None
    last_final_kickoff_utc: datetime | None
    as_of_utc: datetime
    games_total: int
    games_final: int

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        for key, value in data.items():
            if isinstance(value, datetime):
                data[key] = value.isoformat()
            elif isinstance(value, tuple):
                data[key] = list(value)
        return data


def _as_utc(now: datetime) -> datetime:
    if now.tzinfo is None:
        return now.replace(tzinfo=UTC)
    return now.astimezone(UTC)


def _to_py_datetime(value: Any) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, datetime):
        return value if value.tzinfo is not None else value.replace(tzinfo=UTC)
    raise TypeError(f"expected datetime, got {type(value).__name__}")


def with_kickoff_utc(schedules: pl.DataFrame) -> pl.DataFrame:
    """Add ``kickoff_utc`` (``Datetime[us, UTC]``) built from ``gameday`` and ``gametime``.

    Accepts ``gameday`` as a ``YYYY-MM-DD`` string or a ``Date``; ``gametime`` as
    ``HH:MM`` string, ``Time``, or null (13:00 ET).
    """
    missing = [c for c in SCHEDULE_COLUMNS if c not in schedules.columns]
    if missing:
        raise ValueError(f"schedules is missing columns {missing}")

    gameday = pl.col("gameday")
    if schedules.schema["gameday"] == pl.String:
        gameday = gameday.str.strip_chars().str.to_date("%Y-%m-%d")
    elif schedules.schema["gameday"] != pl.Date:
        gameday = gameday.cast(pl.Date)

    gametime = pl.col("gametime")
    if schedules.schema["gametime"] == pl.Time:
        gametime = gametime.fill_null(pl.lit(DEFAULT_KICKOFF_ET).str.to_time("%H:%M"))
    else:
        gametime = (
            gametime.cast(pl.String)
            .str.strip_chars()
            .fill_null(DEFAULT_KICKOFF_ET)
            .str.to_time("%H:%M")
        )

    kickoff = (
        gameday.dt.combine(gametime)
        .dt.replace_time_zone(ET_ZONE, ambiguous="earliest", non_existent="null")
        .dt.convert_time_zone("UTC")
        .alias("kickoff_utc")
    )
    return schedules.with_columns(kickoff)


def _candidate_season(games: pl.DataFrame, now: datetime) -> int:
    reg = games.filter(pl.col("game_type") == REGULAR)
    if reg.is_empty():
        reg = games
    first_kick = (
        reg.group_by("season")
        .agg(pl.col("kickoff_utc").min().alias("first_kickoff"))
        .sort("season")
    )
    threshold = now + timedelta(days=SEASON_LEAD_DAYS)
    eligible = first_kick.filter(pl.col("first_kickoff") <= pl.lit(threshold))
    if eligible.is_empty():
        return int(first_kick["season"].min())
    return int(eligible["season"].max())


def resolve_state(schedules: pl.DataFrame, now_utc: datetime) -> SeasonState:
    """Pure function: resolve the season state from a schedules frame at ``now_utc``."""
    missing = [c for c in SCHEDULE_COLUMNS if c not in schedules.columns]
    if missing:
        raise ValueError(f"schedules is missing columns {missing}")
    if schedules.is_empty():
        raise ValueError("schedules is empty")
    now = _as_utc(now_utc)
    games = with_kickoff_utc(schedules.select(list(SCHEDULE_COLUMNS)))
    games = games.with_columns(
        pl.col("season").cast(pl.Int32),
        pl.col("week").cast(pl.Int32),
        pl.col("result").is_not_null().alias("is_final"),
    )

    season = _candidate_season(games, now)
    games = games.filter(pl.col("season") == season)
    reg = games.filter(pl.col("game_type") == REGULAR)
    post = games.filter(pl.col("game_type") != REGULAR)

    reg_pending = reg.filter(~pl.col("is_final"))
    post_pending = post.filter(~pl.col("is_final"))

    mode: Mode
    if not reg_pending.is_empty():
        mode = "regular"
    elif not post_pending.is_empty():
        mode = "postseason"
    else:
        mode = "offseason"

    by_week = (
        reg.group_by("week")
        .agg(
            pl.len().alias("games"),
            pl.col("is_final").sum().alias("finals"),
        )
        .sort("week")
    )
    completed = tuple(
        int(w) for w in by_week.filter(pl.col("finals") == pl.col("games"))["week"].to_list()
    )

    projection_week: int | None = None
    week_in_progress = False
    if mode == "regular":
        projection_week = int(reg_pending["week"].min())
        row = by_week.filter(pl.col("week") == projection_week)
        week_in_progress = int(row["finals"][0]) > 0

    future = games.filter(pl.col("kickoff_utc") > pl.lit(now))
    next_kickoff = _to_py_datetime(future["kickoff_utc"].min()) if not future.is_empty() else None
    finals = games.filter(pl.col("is_final"))
    last_final = _to_py_datetime(finals["kickoff_utc"].max()) if not finals.is_empty() else None

    return SeasonState(
        season=season,
        mode=mode,
        projection_week=projection_week,
        week_in_progress=week_in_progress,
        completed_weeks=completed,
        last_completed_week=max(completed) if completed else None,
        next_kickoff_utc=next_kickoff,
        last_final_kickoff_utc=last_final,
        as_of_utc=now,
        games_total=games.height,
        games_final=finals.height,
    )


def load_latest_schedules(store: Any | None = None) -> pl.DataFrame:
    """Read the latest ``schedules`` snapshot from the raw store (all seasons)."""
    from ffml.data.store import SnapshotStore

    store = store or SnapshotStore()
    return store.read("schedules", columns=list(SCHEDULE_COLUMNS))


def current_state(now_utc: datetime | None = None, store: Any | None = None) -> SeasonState:
    """Resolve the state from the latest schedules snapshot."""
    now = now_utc if now_utc is not None else datetime.now(UTC)
    return resolve_state(load_latest_schedules(store), now)


def describe_current_state(now_utc: datetime | None = None) -> dict[str, Any]:
    """Dict form of :func:`current_state`, or an explanatory error when no snapshot exists."""
    try:
        return current_state(now_utc).to_dict()
    except FileNotFoundError as exc:
        return {"error": f"{exc}; run `ffml ingest --datasets schedules` first"}


__all__ = [
    "SCHEDULE_COLUMNS",
    "SeasonState",
    "current_state",
    "describe_current_state",
    "load_latest_schedules",
    "resolve_state",
    "with_kickoff_utc",
]
