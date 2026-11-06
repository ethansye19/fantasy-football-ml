"""Read-only client for the public Sleeper API (https://docs.sleeper.com).

Scope (PLAN.md DE-2.3): NFL state, users, leagues, rosters, league users,
matchups, transactions, trending players, and the daily players map with a
Parquet cache. Nothing here writes to Sleeper.

Terms we respect (docs/DATA_SOURCES.md):

* Stay far below Sleeper's limit of 1,000 requests per minute. A client-side
  token bucket (default 300 per minute) makes it impossible for a misbehaving
  caller to approach the limit from one process.
* Fetch the full players map (``/players/nfl``, about 14.7 MB) at most once per
  day. Use :class:`SleeperPlayersCache`, which snapshots it to Parquet under
  ``<raw_dir>/sleeper_players/snapshot=<YYYYMMDD>/``.
* Trending data must credit Sleeper wherever it is displayed
  (:data:`TRENDING_ATTRIBUTION`). All league and player data shown in the app
  carries :data:`SLEEPER_ATTRIBUTION`.
* Send the configured ``User-Agent``.

Phase 3 builds the scoring engine; this module only normalizes the raw
``scoring_settings`` and ``roster_positions`` into clean primitives.
"""

from __future__ import annotations

import hashlib
import json
import logging
import math
import threading
import time
from collections.abc import Callable, Mapping
from datetime import UTC, date, datetime, timedelta
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any, Literal

import httpx
import polars as pl
from pydantic import BaseModel, ConfigDict, Field, PrivateAttr, field_validator, model_validator
from tenacity import (
    RetryCallState,
    Retrying,
    retry_if_exception_type,
    stop_after_attempt,
    wait_exponential,
)

from ffml.config import get_settings

log = logging.getLogger(__name__)

SLEEPER_ATTRIBUTION = "League and player data from Sleeper."
TRENDING_ATTRIBUTION = "Trending player data from Sleeper."

#: Roster slot vocabulary accepted by :func:`normalize_roster_positions`.
SLEEPER_ROSTER_SLOTS: frozenset[str] = frozenset(
    {
        "QB",
        "RB",
        "WR",
        "TE",
        "FLEX",
        "SUPER_FLEX",
        "WRRB_FLEX",
        "REC_FLEX",
        "K",
        "DEF",
        "DL",
        "LB",
        "DB",
        "IDP_FLEX",
        "BN",
        "IR",
        "TAXI",
    }
)

PLAYERS_DATASET = "sleeper_players"
PLAYERS_TERMS_NOTE = (
    "Sleeper API, non-commercial use. The full players map is fetched at most "
    "once per UTC day; display must credit Sleeper."
)

#: Column order and dtypes of :func:`players_frame`.
PLAYERS_SCHEMA: dict[str, pl.DataType] = {
    "sleeper_id": pl.String,
    "full_name": pl.String,
    "first_name": pl.String,
    "last_name": pl.String,
    "position": pl.String,
    "fantasy_positions": pl.List(pl.String),
    "team": pl.String,
    "status": pl.String,
    "active": pl.Boolean,
    "injury_status": pl.String,
    "injury_body_part": pl.String,
    "gsis_id": pl.String,
    "espn_id": pl.String,
    "yahoo_id": pl.String,
    "sportradar_id": pl.String,
    "rotowire_id": pl.String,
    "age": pl.Int32,
    "years_exp": pl.Int32,
    "number": pl.Int32,
    "depth_chart_order": pl.Int32,
    "depth_chart_position": pl.String,
    "search_rank": pl.Int32,
    "snapshot_date": pl.Date,
}


# --------------------------------------------------------------------------- errors
class SleeperError(Exception):
    """Base class for Sleeper client failures."""

    def __init__(self, message: str, *, status_code: int | None = None, url: str | None = None):
        super().__init__(message)
        self.status_code = status_code
        self.url = url


class SleeperNotFound(SleeperError):
    """The resource does not exist (HTTP 404, or a ``null`` body for a lookup)."""


class SleeperRateLimited(SleeperError):
    """Sleeper answered 429. ``retry_after`` holds the parsed header in seconds."""

    def __init__(
        self,
        message: str,
        *,
        status_code: int | None = 429,
        url: str | None = None,
        retry_after: float | None = None,
    ):
        super().__init__(message, status_code=status_code, url=url)
        self.retry_after = retry_after


class SleeperServerError(SleeperError):
    """Sleeper answered 5xx; retried automatically."""


# --------------------------------------------------------------------------- rate limit
class TokenBucket:
    """Thread-safe token bucket.

    ``acquire`` returns immediately while tokens remain; otherwise it reserves a
    token and sleeps for the time the bucket needs to refill it. Tokens may go
    negative (reservations), which keeps the arrival rate at ``rate_per_minute``
    on average even when many callers queue at once. ``clock`` and ``sleep`` are
    injectable for tests.
    """

    def __init__(
        self,
        rate_per_minute: float = 300.0,
        capacity: int = 30,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
    ):
        if rate_per_minute <= 0:
            raise ValueError("rate_per_minute must be positive")
        if capacity < 1:
            raise ValueError("capacity must be at least 1")
        self.rate_per_second = rate_per_minute / 60.0
        self.capacity = float(capacity)
        self._tokens = float(capacity)
        self._clock = clock
        self._sleep = sleep
        self._last = clock()
        self._lock = threading.Lock()

    @property
    def tokens(self) -> float:
        with self._lock:
            self._refill()
            return self._tokens

    def _refill(self) -> None:
        now = self._clock()
        elapsed = max(0.0, now - self._last)
        self._last = now
        self._tokens = min(self.capacity, self._tokens + elapsed * self.rate_per_second)

    def acquire(self) -> float:
        """Take one token, sleeping if necessary. Returns the seconds waited."""
        with self._lock:
            self._refill()
            self._tokens -= 1.0
            deficit = -self._tokens
            wait = deficit / self.rate_per_second if deficit > 0 else 0.0
        if wait > 0:
            log.debug("sleeper rate limiter waiting %.2fs", wait)
            self._sleep(wait)
        return wait


# --------------------------------------------------------------------------- models
class _SleeperModel(BaseModel):
    """Base for API payloads: unknown keys are kept in ``model_extra``."""

    model_config = ConfigDict(extra="allow", populate_by_name=True)

    _raw: dict[str, Any] = PrivateAttr(default_factory=dict)

    @property
    def raw(self) -> dict[str, Any]:
        """The payload exactly as Sleeper returned it (empty if built by hand)."""
        return self._raw

    @classmethod
    def from_api(cls, payload: Mapping[str, Any]):
        obj = cls.model_validate(dict(payload))
        obj._raw = dict(payload)
        return obj


def _none_to_list(value: Any) -> list:
    return [] if value is None else list(value)


def _none_to_dict(value: Any) -> dict:
    return {} if value is None else dict(value)


def _to_int_or_none(value: Any) -> int | None:
    if value is None or value == "":
        return None
    return int(value)


class SleeperState(_SleeperModel):
    """``GET /state/nfl``."""

    season: int
    week: int
    season_type: str
    season_start_date: date | None = None
    display_week: int | None = None
    previous_season: int | None = None
    league_season: int | None = None
    leg: int | None = None
    season_has_scores: bool | None = None

    _coerce = field_validator(
        "season", "previous_season", "league_season", "display_week", "leg", mode="before"
    )(_to_int_or_none)


class SleeperUser(_SleeperModel):
    """``GET /user/<username_or_id>``."""

    user_id: str
    username: str | None = None
    display_name: str | None = None
    avatar: str | None = None
    is_bot: bool | None = None


class SleeperLeague(_SleeperModel):
    """``GET /league/<league_id>`` and items of ``GET /user/<id>/leagues/nfl/<season>``."""

    league_id: str
    name: str
    season: int
    status: str | None = None
    sport: str = "nfl"
    season_type: str | None = None
    total_rosters: int
    scoring_settings: dict[str, float] = Field(default_factory=dict)
    roster_positions: list[str] = Field(default_factory=list)
    settings: dict[str, Any] = Field(default_factory=dict)
    metadata: dict[str, Any] | None = None
    previous_league_id: str | None = None
    draft_id: str | None = None
    avatar: str | None = None

    _season = field_validator("season", mode="before")(_to_int_or_none)
    _lists = field_validator("roster_positions", mode="before")(_none_to_list)
    _dicts = field_validator("scoring_settings", "settings", mode="before")(_none_to_dict)

    @property
    def num_teams(self) -> int:
        return int(self.settings.get("num_teams") or self.total_rosters)

    @property
    def playoff_week_start(self) -> int | None:
        value = self.settings.get("playoff_week_start")
        return int(value) if value else None


class SleeperRosterSettings(_SleeperModel):
    """Standings block of a roster. Sleeper splits points into integer and hundredths."""

    wins: int = 0
    losses: int = 0
    ties: int = 0
    fpts: int = 0
    fpts_decimal: int = 0
    fpts_against: int = 0
    fpts_against_decimal: int = 0
    ppts: int = 0
    ppts_decimal: int = 0
    waiver_position: int | None = None
    waiver_budget_used: int | None = None
    total_moves: int | None = None

    @property
    def points_for(self) -> float:
        return self.fpts + self.fpts_decimal / 100.0

    @property
    def points_against(self) -> float:
        return self.fpts_against + self.fpts_against_decimal / 100.0

    @property
    def potential_points(self) -> float:
        return self.ppts + self.ppts_decimal / 100.0


class SleeperRoster(_SleeperModel):
    """``GET /league/<league_id>/rosters`` item.

    ``starters`` keeps Sleeper's ordering, which matches ``roster_positions``
    minus bench/IR/taxi slots; an empty starting slot is the string ``"0"``.
    """

    roster_id: int
    league_id: str | None = None
    owner_id: str | None = None
    co_owners: list[str] = Field(default_factory=list)
    players: list[str] = Field(default_factory=list)
    starters: list[str] = Field(default_factory=list)
    reserve: list[str] = Field(default_factory=list)
    taxi: list[str] = Field(default_factory=list)
    keepers: list[str] = Field(default_factory=list)
    settings: SleeperRosterSettings = Field(default_factory=SleeperRosterSettings)
    metadata: dict[str, Any] | None = None

    _lists = field_validator(
        "co_owners", "players", "starters", "reserve", "taxi", "keepers", mode="before"
    )(_none_to_list)

    @field_validator("settings", mode="before")
    @classmethod
    def _settings_none(cls, value: Any) -> Any:
        return {} if value is None else value

    @property
    def bench(self) -> list[str]:
        """Rostered players who are neither starters nor on reserve/taxi."""
        used = set(self.starters) | set(self.reserve) | set(self.taxi)
        return [p for p in self.players if p not in used]


class SleeperLeagueUser(_SleeperModel):
    """``GET /league/<league_id>/users`` item. ``team_name`` is lifted from metadata."""

    user_id: str
    display_name: str | None = None
    team_name: str | None = None
    is_owner: bool = False
    avatar: str | None = None
    league_id: str | None = None
    metadata: dict[str, Any] | None = None

    @model_validator(mode="before")
    @classmethod
    def _lift(cls, data: Any) -> Any:
        if isinstance(data, Mapping):
            data = dict(data)
            meta = data.get("metadata") or {}
            if data.get("team_name") is None and isinstance(meta, Mapping):
                data["team_name"] = meta.get("team_name")
            if data.get("is_owner") is None:
                data["is_owner"] = False
        return data


class SleeperMatchup(_SleeperModel):
    """``GET /league/<league_id>/matchups/<week>`` item.

    Two rosters share one ``matchup_id`` in a given week; ``matchup_id`` is
    ``None`` for a roster on bye in leagues with an odd team count.
    """

    roster_id: int
    matchup_id: int | None = None
    points: float = 0.0
    custom_points: float | None = None
    players: list[str] = Field(default_factory=list)
    starters: list[str] = Field(default_factory=list)
    players_points: dict[str, float] = Field(default_factory=dict)
    starters_points: list[float] = Field(default_factory=list)

    _lists = field_validator("players", "starters", "starters_points", mode="before")(_none_to_list)
    _dicts = field_validator("players_points", mode="before")(_none_to_dict)

    @field_validator("points", mode="before")
    @classmethod
    def _points_none(cls, value: Any) -> Any:
        return 0.0 if value is None else value


class SleeperTransaction(_SleeperModel):
    """``GET /league/<league_id>/transactions/<round>`` item.

    ``adds``/``drops`` map ``player_id -> roster_id``. ``leg`` is Sleeper's name
    for the week; :attr:`week` is an alias. ``created`` is epoch milliseconds.
    """

    transaction_id: str
    type: str
    status: str
    roster_ids: list[int] = Field(default_factory=list)
    adds: dict[str, int] = Field(default_factory=dict)
    drops: dict[str, int] = Field(default_factory=dict)
    draft_picks: list[dict[str, Any]] = Field(default_factory=list)
    waiver_budget: list[dict[str, Any]] = Field(default_factory=list)
    consenter_ids: list[int] = Field(default_factory=list)
    created: int | None = None
    status_updated: int | None = None
    leg: int | None = None
    creator: str | None = None
    settings: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

    _lists = field_validator(
        "roster_ids", "draft_picks", "waiver_budget", "consenter_ids", mode="before"
    )(_none_to_list)
    _dicts = field_validator("adds", "drops", mode="before")(_none_to_dict)

    @property
    def week(self) -> int | None:
        return self.leg

    @property
    def created_at(self) -> datetime | None:
        if self.created is None:
            return None
        return datetime.fromtimestamp(self.created / 1000.0, tz=UTC)


class SleeperTrending(_SleeperModel):
    """``GET /players/nfl/trending/<add|drop>`` item. Display must credit Sleeper."""

    player_id: str
    count: int


# --------------------------------------------------------------------------- normalization
def normalize_scoring_settings(settings: Mapping[str, Any] | None) -> dict[str, float]:
    """Return Sleeper ``scoring_settings`` as ``{key: float}`` without zero entries.

    Keys are kept exactly as Sleeper names them (``pass_yd``, ``rec``,
    ``bonus_rec_yd_100`` and so on) and sorted for deterministic hashing. ``None``
    values are dropped; non-numeric and non-finite values (NaN, infinities,
    including strings such as ``"inf"`` or ``"1e999"``) raise ``ValueError``, as
    the API refuses them at its boundary. Mapping these keys onto stat lines is
    the Phase 3 scoring engine's job.
    """
    if settings is None:
        return {}
    if not isinstance(settings, Mapping):
        raise TypeError(f"scoring_settings must be a mapping, got {type(settings).__name__}")
    out: dict[str, float] = {}
    for key, value in settings.items():
        if value is None:
            continue
        if isinstance(value, bool):
            raise ValueError(f"scoring setting {key!r} has a boolean value {value!r}")
        try:
            points = float(value)
        except OverflowError as exc:  # an int beyond the float range
            raise ValueError(f"scoring setting {key!r} is infinite: out of the float range") from exc
        except (TypeError, ValueError) as exc:
            raise ValueError(f"scoring setting {key!r} is not numeric: {value!r}") from exc
        if math.isnan(points):
            raise ValueError(f"scoring setting {key!r} is NaN")
        if math.isinf(points):
            raise ValueError(f"scoring setting {key!r} is infinite: {value!r}")
        if points == 0.0:
            continue
        out[str(key)] = points
    return dict(sorted(out.items()))


def normalize_roster_positions(positions: list[str] | tuple[str, ...] | None) -> list[str]:
    """Upper-case and validate Sleeper ``roster_positions``; order and duplicates are kept.

    Raises ``ValueError`` naming every slot that is not in
    :data:`SLEEPER_ROSTER_SLOTS`.
    """
    if positions is None:
        return []
    if isinstance(positions, str | bytes | Mapping):
        raise TypeError("roster_positions must be a list of slot names")
    cleaned: list[str] = []
    unknown: list[str] = []
    for slot in positions:
        if not isinstance(slot, str):
            unknown.append(repr(slot))
            continue
        name = slot.strip().upper()
        if name not in SLEEPER_ROSTER_SLOTS:
            unknown.append(slot)
            continue
        cleaned.append(name)
    if unknown:
        raise ValueError(
            f"unknown Sleeper roster slots: {unknown}; known slots: {sorted(SLEEPER_ROSTER_SLOTS)}"
        )
    return cleaned


def _clean_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _clean_int(value: Any) -> int | None:
    if value is None or value == "":
        return None
    try:
        return int(value)
    except (TypeError, ValueError):
        return None


def players_frame(
    raw: Mapping[str, Mapping[str, Any]], snapshot_date: date | None = None
) -> pl.DataFrame:
    """Flatten the ``/players/nfl`` map into a typed frame (schema: :data:`PLAYERS_SCHEMA`).

    Team defenses (ids like ``KC``, position ``DEF``) are kept; they carry only
    a handful of keys, so ``full_name`` is composed from first and last name.
    External ids are strings. ``gsis_id`` is stripped because Sleeper pads
    hundreds of them with a leading space.
    """
    snap = snapshot_date or datetime.now(UTC).date()
    rows: list[dict[str, Any]] = []
    for pid, p in raw.items():
        if not isinstance(p, Mapping):
            continue
        first = _clean_str(p.get("first_name"))
        last = _clean_str(p.get("last_name"))
        full = _clean_str(p.get("full_name")) or " ".join(x for x in (first, last) if x) or None
        fps = p.get("fantasy_positions")
        rows.append(
            {
                "sleeper_id": str(p.get("player_id") or pid),
                "full_name": full,
                "first_name": first,
                "last_name": last,
                "position": _clean_str(p.get("position")),
                "fantasy_positions": [str(x) for x in fps] if fps else None,
                "team": _clean_str(p.get("team")),
                "status": _clean_str(p.get("status")),
                "active": bool(p.get("active")) if p.get("active") is not None else None,
                "injury_status": _clean_str(p.get("injury_status")),
                "injury_body_part": _clean_str(p.get("injury_body_part")),
                "gsis_id": _clean_str(p.get("gsis_id")),
                "espn_id": _clean_str(p.get("espn_id")),
                "yahoo_id": _clean_str(p.get("yahoo_id")),
                "sportradar_id": _clean_str(p.get("sportradar_id")),
                "rotowire_id": _clean_str(p.get("rotowire_id")),
                "age": _clean_int(p.get("age")),
                "years_exp": _clean_int(p.get("years_exp")),
                "number": _clean_int(p.get("number")),
                "depth_chart_order": _clean_int(p.get("depth_chart_order")),
                "depth_chart_position": _clean_str(p.get("depth_chart_position")),
                "search_rank": _clean_int(p.get("search_rank")),
                "snapshot_date": snap,
            }
        )
    frame = pl.DataFrame(rows, schema=PLAYERS_SCHEMA, orient="row")
    return frame.sort("sleeper_id")


# --------------------------------------------------------------------------- client
def _parse_retry_after(value: str | None) -> float | None:
    if not value:
        return None
    value = value.strip()
    try:
        return max(0.0, float(value))
    except ValueError:
        pass
    try:
        when = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if when.tzinfo is None:
        when = when.replace(tzinfo=UTC)
    return max(0.0, (when - datetime.now(UTC)).total_seconds())


class SleeperClient:
    """Synchronous Sleeper API client with retries and a client-side rate limiter.

    Parameters default to :func:`ffml.config.get_settings`. Pass ``client`` to
    share an ``httpx.Client`` (it is not closed by :meth:`close`). ``sleep``
    is used by both the rate limiter and the retry backoff; tests inject a
    recorder.
    """

    RETRYABLE = (httpx.TransportError, SleeperRateLimited, SleeperServerError)

    def __init__(
        self,
        base_url: str | None = None,
        timeout: float | None = None,
        client: httpx.Client | None = None,
        *,
        user_agent: str | None = None,
        rate_limit_per_minute: float = 300.0,
        burst: int = 30,
        max_attempts: int = 3,
        max_retry_after: float = 30.0,
        sleep: Callable[[float], None] = time.sleep,
    ):
        settings = get_settings()
        self.base_url = (base_url or settings.sleeper_base_url).rstrip("/")
        self.timeout = timeout if timeout is not None else settings.http_timeout_seconds
        self.user_agent = user_agent or settings.http_user_agent
        self._sleep = sleep
        self._owns_client = client is None
        # Sent on every request, so a shared client (which carries httpx's own
        # default User-Agent) still identifies us without being mutated.
        self._headers = {"User-Agent": self.user_agent, "Accept": "application/json"}
        if client is None:
            client = httpx.Client(
                base_url=self.base_url,
                timeout=httpx.Timeout(self.timeout),
                headers=self._headers,
                follow_redirects=True,
            )
        self._client = client
        self.bucket = TokenBucket(rate_limit_per_minute, burst, sleep=sleep)
        self.max_attempts = max_attempts
        self.max_retry_after = max_retry_after
        self._exponential = wait_exponential(multiplier=0.5, min=0.5, max=8.0)
        self._retrying = Retrying(
            stop=stop_after_attempt(max_attempts),
            wait=self._wait,
            retry=retry_if_exception_type(self.RETRYABLE),
            sleep=sleep,
            reraise=True,
        )
        self.request_count = 0

    # -- lifecycle
    def close(self) -> None:
        if self._owns_client:
            self._client.close()

    def __enter__(self) -> SleeperClient:
        return self

    def __exit__(self, *exc: object) -> None:
        self.close()

    # -- transport
    def _wait(self, retry_state: RetryCallState) -> float:
        outcome = retry_state.outcome
        exc = outcome.exception() if outcome is not None and outcome.failed else None
        retry_after = getattr(exc, "retry_after", None)
        if retry_after is not None:
            return min(float(retry_after), self.max_retry_after)
        return float(self._exponential(retry_state))

    def _url(self, path: str) -> str:
        return f"{self.base_url}/{path.lstrip('/')}"

    def _fetch_once(self, path: str, params: Mapping[str, Any] | None) -> Any:
        self.bucket.acquire()
        url = self._url(path)
        self.request_count += 1
        log.debug("GET %s %s", url, dict(params or {}))
        response = self._client.get(url, params=params, headers=self._headers)
        status = response.status_code
        if status == 404:
            raise SleeperNotFound(f"not found: {url}", status_code=404, url=url)
        if status == 429:
            raise SleeperRateLimited(
                f"rate limited by Sleeper: {url}",
                url=url,
                retry_after=_parse_retry_after(response.headers.get("Retry-After")),
            )
        if status >= 500:
            raise SleeperServerError(
                f"Sleeper server error {status}: {url}", status_code=status, url=url
            )
        if status >= 400:
            raise SleeperError(f"HTTP {status} from Sleeper: {url}", status_code=status, url=url)
        if not response.content:
            return None
        try:
            return response.json()
        except ValueError as exc:
            raise SleeperError(f"invalid JSON from {url}", status_code=status, url=url) from exc

    def get_json(self, path: str, params: Mapping[str, Any] | None = None) -> Any:
        """GET a path under the base URL with rate limiting and retries; returns parsed JSON."""
        return self._retrying(self._fetch_once, path, params)

    def _require(self, payload: Any, what: str) -> Any:
        if payload is None:
            raise SleeperNotFound(f"{what} not found", status_code=None)
        return payload

    # -- endpoints
    def get_state(self, sport: str = "nfl") -> SleeperState:
        """Current season, week, and season type for ``sport``."""
        return SleeperState.from_api(self._require(self.get_json(f"state/{sport}"), "state"))

    def get_user(self, username_or_id: str) -> SleeperUser:
        """Look up a user by username or numeric user_id. Unknown users raise ``SleeperNotFound``."""
        payload = self.get_json(f"user/{username_or_id}")
        return SleeperUser.from_api(self._require(payload, f"user {username_or_id!r}"))

    def get_user_leagues(
        self, user_id: str, season: int | str, sport: str = "nfl"
    ) -> list[SleeperLeague]:
        """All leagues of ``user_id`` for one season (empty list when none)."""
        payload = self.get_json(f"user/{user_id}/leagues/{sport}/{season}") or []
        return [SleeperLeague.from_api(item) for item in payload]

    def get_league(self, league_id: str) -> SleeperLeague:
        """League settings including ``scoring_settings`` and ``roster_positions``."""
        payload = self.get_json(f"league/{league_id}")
        return SleeperLeague.from_api(self._require(payload, f"league {league_id}"))

    def get_rosters(self, league_id: str) -> list[SleeperRoster]:
        payload = self._require(self.get_json(f"league/{league_id}/rosters"), f"league {league_id}")
        return [SleeperRoster.from_api(item) for item in payload]

    def get_league_users(self, league_id: str) -> list[SleeperLeagueUser]:
        payload = self._require(self.get_json(f"league/{league_id}/users"), f"league {league_id}")
        return [SleeperLeagueUser.from_api(item) for item in payload]

    def get_matchups(self, league_id: str, week: int) -> list[SleeperMatchup]:
        payload = self.get_json(f"league/{league_id}/matchups/{int(week)}") or []
        return [SleeperMatchup.from_api(item) for item in payload]

    def get_transactions(self, league_id: str, round: int) -> list[SleeperTransaction]:
        """Transactions for one ``round`` (Sleeper's name for the week / leg)."""
        payload = self.get_json(f"league/{league_id}/transactions/{int(round)}") or []
        return [SleeperTransaction.from_api(item) for item in payload]

    def get_trending(
        self,
        kind: Literal["add", "drop"] = "add",
        lookback_hours: int = 24,
        limit: int = 25,
        sport: str = "nfl",
    ) -> list[SleeperTrending]:
        """Most added or dropped players.

        Sleeper's terms require that any display of this data credit Sleeper;
        use :data:`TRENDING_ATTRIBUTION`.
        """
        if kind not in ("add", "drop"):
            raise ValueError("kind must be 'add' or 'drop'")
        payload = (
            self.get_json(
                f"players/{sport}/trending/{kind}",
                params={"lookback_hours": int(lookback_hours), "limit": int(limit)},
            )
            or []
        )
        return [SleeperTrending.from_api(item) for item in payload]

    def get_players(self, sport: str = "nfl") -> dict[str, dict[str, Any]]:
        """The full players map (about 14.7 MB).

        Sleeper asks that this be called at most once per day. Prefer
        :class:`SleeperPlayersCache`, which enforces that.
        """
        log.info(
            "fetching full Sleeper players map for %s (once per day by Sleeper's terms)", sport
        )
        payload = self._require(self.get_json(f"players/{sport}"), "players map")
        return {str(k): v for k, v in payload.items()}


# --------------------------------------------------------------------------- players cache
def _schema_hash(frame: pl.DataFrame) -> str:
    text = "|".join(f"{name}:{dtype}" for name, dtype in frame.schema.items())
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


class SleeperPlayersCache:
    """Daily Parquet snapshot of the Sleeper players map.

    Layout (DATA_SCHEMA.md section 1)::

        <root>/snapshot=<YYYYMMDD>/players.parquet
        <root>/snapshot=<YYYYMMDD>/_manifest.json
        <root>/LATEST                      newest snapshot id (YYYYMMDD)

    ``root`` defaults to ``<raw_dir>/sleeper_players``. The map is never
    fetched more than once per UTC day unless ``force=True``.
    """

    PARQUET_NAME = "players.parquet"
    MANIFEST_NAME = "_manifest.json"

    def __init__(
        self,
        root: Path | None = None,
        client: SleeperClient | None = None,
        sport: str = "nfl",
    ):
        self.root = Path(root) if root is not None else get_settings().raw_dir / PLAYERS_DATASET
        self._client = client
        self.sport = sport

    # -- snapshot discovery
    @staticmethod
    def _today() -> date:
        return datetime.now(UTC).date()

    def snapshot_dir(self, day: date) -> Path:
        return self.root / f"snapshot={day:%Y%m%d}"

    def snapshots(self) -> list[date]:
        """Snapshot dates that have a Parquet file, oldest first."""
        if not self.root.exists():
            return []
        out: list[date] = []
        for child in self.root.iterdir():
            if not child.is_dir() or not child.name.startswith("snapshot="):
                continue
            stamp = child.name.split("=", 1)[1]
            try:
                day = datetime.strptime(stamp, "%Y%m%d").date()
            except ValueError:
                continue
            if (child / self.PARQUET_NAME).exists():
                out.append(day)
        return sorted(out)

    def latest(self) -> date | None:
        snaps = self.snapshots()
        return snaps[-1] if snaps else None

    def read(self, day: date) -> pl.DataFrame:
        return pl.read_parquet(self.snapshot_dir(day) / self.PARQUET_NAME)

    def manifest(self, day: date) -> dict[str, Any] | None:
        path = self.snapshot_dir(day) / self.MANIFEST_NAME
        if not path.exists():
            return None
        return json.loads(path.read_text(encoding="utf-8"))

    def _fetched_at(self, day: date) -> datetime:
        manifest = self.manifest(day) or {}
        stamp = manifest.get("fetched_at")
        if stamp:
            try:
                return datetime.fromisoformat(stamp).astimezone(UTC)
            except ValueError:
                pass
        return datetime.combine(day, datetime.min.time(), tzinfo=UTC)

    # -- main entry point
    def get(self, max_age_hours: float = 24.0, force: bool = False) -> pl.DataFrame:
        """Return today's snapshot, or one fetched within ``max_age_hours``; otherwise fetch.

        ``force=True`` refetches even if today's snapshot exists (the only way
        to hit Sleeper twice in one UTC day).
        """
        today = self._today()
        latest = self.latest()
        if latest is not None and not force:
            if latest >= today:
                return self.read(latest)
            age = datetime.now(UTC) - self._fetched_at(latest)
            if age <= timedelta(hours=max_age_hours):
                return self.read(latest)
        return self.refresh(today)

    def refresh(self, day: date | None = None) -> pl.DataFrame:
        """Fetch the map now and write the snapshot for ``day`` (default today, UTC)."""
        day = day or self._today()
        client = self._client
        owns = client is None
        if client is None:
            client = SleeperClient()
        try:
            raw = client.get_players(self.sport)
        finally:
            if owns:
                client.close()
        frame = players_frame(raw, snapshot_date=day)
        self._write(day, frame, endpoint=f"{client.base_url}/players/{self.sport}")
        return frame

    def _write(self, day: date, frame: pl.DataFrame, *, endpoint: str) -> Path:
        target = self.snapshot_dir(day)
        target.mkdir(parents=True, exist_ok=True)
        parquet = target / self.PARQUET_NAME
        tmp = target / f".{self.PARQUET_NAME}.tmp"
        frame.write_parquet(tmp)
        tmp.replace(parquet)
        manifest = {
            "dataset": PLAYERS_DATASET,
            "source": "Sleeper API",
            "endpoint": endpoint,
            "snapshot_date": day.isoformat(),
            "fetched_at": datetime.now(UTC).isoformat(timespec="seconds"),
            "rows": frame.height,
            "columns": list(frame.columns),
            "schema_hash": _schema_hash(frame),
            "file_sha256": _file_sha256(parquet),
            "terms": PLAYERS_TERMS_NOTE,
            "attribution": SLEEPER_ATTRIBUTION,
        }
        (target / self.MANIFEST_NAME).write_text(
            json.dumps(manifest, indent=2, sort_keys=True), encoding="utf-8"
        )
        latest_id = f"{day:%Y%m%d}"
        current = self.latest()
        if current is None or current <= day:
            (self.root / "LATEST").write_text(latest_id + "\n", encoding="utf-8")
        log.info("wrote %s rows to %s", frame.height, parquet)
        return parquet


__all__ = [
    "PLAYERS_SCHEMA",
    "SLEEPER_ATTRIBUTION",
    "SLEEPER_ROSTER_SLOTS",
    "TRENDING_ATTRIBUTION",
    "SleeperClient",
    "SleeperError",
    "SleeperLeague",
    "SleeperLeagueUser",
    "SleeperMatchup",
    "SleeperNotFound",
    "SleeperPlayersCache",
    "SleeperRateLimited",
    "SleeperRoster",
    "SleeperRosterSettings",
    "SleeperServerError",
    "SleeperState",
    "SleeperTransaction",
    "SleeperTrending",
    "SleeperUser",
    "TokenBucket",
    "normalize_roster_positions",
    "normalize_scoring_settings",
    "players_frame",
]
