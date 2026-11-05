"""Player directory: Sleeper id <-> nflverse gsis id, names, positions, teams (D-022, D-023).

Built from the processed lake: ``dim_player`` (the crosswalk union of
ff_playerids, rosters, Sleeper gsis ids, name matching and manual overrides)
and ``sleeper_players`` (names, current team and injury status for display).
Only identity columns are read; nothing restricted is touched (D-007, D-039).

Team defenses use the nflverse team code as their id (the simulation's DST
``entity_id``). Sleeper's ``DEF`` ids are team codes in Sleeper's spelling
(``LAR``, ``JAC``, ``WSH``), mapped through ``dim_team.aliases``.
"""

from __future__ import annotations

import logging
import re
import threading
from dataclasses import dataclass
from pathlib import Path
from typing import Any

import polars as pl

log = logging.getLogger(__name__)

#: nflverse team codes (2016+), the DST entity ids.
NFL_TEAMS: frozenset[str] = frozenset(
    "ARI ATL BAL BUF CAR CHI CIN CLE DAL DEN DET GB HOU IND JAX KC LA LAC LV MIA MIN NE NO "
    "NYG NYJ PHI PIT SEA SF TB TEN WAS".split()
)
#: Fallback aliases when dim_team is unavailable (dim_team.aliases plus Sleeper spellings).
DEFAULT_TEAM_ALIASES: dict[str, str] = {
    "ARZ": "ARI",
    "BLT": "BAL",
    "CLV": "CLE",
    "HST": "HOU",
    "JAC": "JAX",
    "LAR": "LA",
    "STL": "LA",
    "SL": "LA",
    "SD": "LAC",
    "OAK": "LV",
    "WSH": "WAS",
}
GSIS_RE = re.compile(r"^00-\d{7}$")
SKILL = ("QB", "RB", "WR", "TE", "K")
FANTASY_POSITIONS = frozenset({*SKILL, "DST"})


@dataclass(frozen=True)
class PlayerInfo:
    player_id: str | None  # gsis id, or team code for a defense
    sleeper_id: str | None
    name: str | None
    position: str | None  # QB RB WR TE K DST (None when unknown)
    team: str | None
    injury_status: str | None = None
    last_active_season: int | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "player_id": self.player_id,
            "sleeper_id": self.sleeper_id,
            "name": self.name,
            "position": self.position,
            "team": self.team,
            "injury_status": self.injury_status,
        }


def _as_int(value: Any) -> int | None:
    try:
        return int(value) if value is not None else None
    except (TypeError, ValueError):
        return None


def _norm_position(pos: str | None) -> str | None:
    if pos is None:
        return None
    p = str(pos).strip().upper()
    return {"DEF": "DST", "PK": "K"}.get(p, p)


class PlayerDirectory:
    """In-memory lookups; build with :meth:`from_store` or :meth:`from_frames`."""

    def __init__(
        self,
        dim_player: pl.DataFrame | None = None,
        sleeper_players: pl.DataFrame | None = None,
        team_aliases: dict[str, str] | None = None,
    ):
        self.aliases = dict(DEFAULT_TEAM_ALIASES)
        self.aliases.update(team_aliases or {})
        self.by_gsis: dict[str, PlayerInfo] = {}
        self.by_sleeper: dict[str, PlayerInfo] = {}
        sleeper_rows: dict[str, dict[str, Any]] = {}
        if sleeper_players is not None and sleeper_players.height:
            for row in sleeper_players.iter_rows(named=True):
                sid = row.get("sleeper_id")
                if sid:
                    sleeper_rows[str(sid)] = row
        if dim_player is not None and dim_player.height:
            for row in dim_player.iter_rows(named=True):
                gsis = row.get("player_id")
                if not gsis:
                    continue
                sid = row.get("sleeper_id")
                sp = sleeper_rows.get(str(sid)) if sid else None
                info = PlayerInfo(
                    player_id=str(gsis),
                    sleeper_id=str(sid) if sid else None,
                    name=row.get("full_name") or (sp or {}).get("full_name"),
                    position=_norm_position(row.get("position")),
                    team=self.team((sp or {}).get("team")) or self.team(row.get("latest_team")),
                    injury_status=(sp or {}).get("injury_status"),
                    last_active_season=_as_int(row.get("last_active_season")),
                )
                self.by_gsis[info.player_id] = info  # type: ignore[index]
                if info.sleeper_id:
                    self.by_sleeper.setdefault(info.sleeper_id, info)
        # Sleeper-only players (no crosswalk row): keep them displayable.
        for sid, row in sleeper_rows.items():
            if sid in self.by_sleeper:
                continue
            pos = _norm_position(row.get("position"))
            gsis = (row.get("gsis_id") or "").strip() or None
            if pos == "DST" or sid in NFL_TEAMS or sid in self.aliases:
                continue  # defenses resolve through team codes
            info = PlayerInfo(
                player_id=gsis if gsis and GSIS_RE.match(gsis) else None,
                sleeper_id=sid,
                name=row.get("full_name"),
                position=pos,
                team=self.team(row.get("team")),
                injury_status=row.get("injury_status"),
            )
            self.by_sleeper[sid] = info
            if info.player_id and info.player_id not in self.by_gsis:
                self.by_gsis[info.player_id] = info

    # -- construction ------------------------------------------------------ #

    @classmethod
    def from_frames(
        cls,
        dim_player: pl.DataFrame | None = None,
        sleeper_players: pl.DataFrame | None = None,
        dim_team: pl.DataFrame | None = None,
    ) -> PlayerDirectory:
        aliases: dict[str, str] = {}
        if dim_team is not None and "aliases" in dim_team.columns:
            for row in dim_team.select("team", "aliases").iter_rows(named=True):
                for alias in row["aliases"] or []:
                    aliases[str(alias)] = str(row["team"])
        return cls(dim_player, sleeper_players, aliases)

    @classmethod
    def from_store(cls, processed_dir: Path) -> PlayerDirectory:
        """Read the latest processed snapshots; missing tables give an empty directory."""
        from ffml.data.store import SnapshotStore

        store = SnapshotStore(processed_dir)

        def read(name: str, columns: list[str]) -> pl.DataFrame | None:
            try:
                return store.read(name, columns=columns)
            except Exception as exc:  # noqa: BLE001 - tables are optional for the API
                log.warning("player directory: %s unavailable (%r)", name, exc)
                return None

        return cls.from_frames(
            read(
                "dim_player",
                [
                    "player_id",
                    "sleeper_id",
                    "full_name",
                    "position",
                    "latest_team",
                    "last_active_season",
                ],
            ),
            read(
                "sleeper_players",
                ["sleeper_id", "full_name", "position", "team", "injury_status", "gsis_id"],
            ),
            read("dim_team", ["team", "aliases"]),
        )

    # -- lookups ----------------------------------------------------------- #

    def team(self, code: Any) -> str | None:
        if code is None:
            return None
        text = str(code).strip().upper()
        if not text:
            return None
        text = self.aliases.get(text, text)
        return text if text in NFL_TEAMS else None

    def defense(self, code: str) -> PlayerInfo | None:
        team = self.team(code)
        if team is None:
            return None
        return PlayerInfo(
            player_id=team, sleeper_id=str(code).upper(), name=f"{team} D/ST", position="DST",
            team=team,
        )

    def from_sleeper(self, sleeper_id: str) -> PlayerInfo:
        """Resolve a Sleeper roster id; unknown ids keep the Sleeper id only."""
        sid = str(sleeper_id).strip()
        if not sid.isdigit():
            dst = self.defense(sid)
            if dst is not None:
                return dst
        info = self.by_sleeper.get(sid)
        if info is not None:
            return info
        return PlayerInfo(player_id=None, sleeper_id=sid, name=None, position=None, team=None)

    def resolve(self, any_id: str) -> PlayerInfo | None:
        """A gsis id, a Sleeper id or a team code; None when nothing matches."""
        text = str(any_id).strip()
        if GSIS_RE.match(text):
            return self.by_gsis.get(text) or PlayerInfo(text, None, None, None, None)
        if text.isdigit():
            info = self.by_sleeper.get(text)
            return info
        return self.defense(text)

    def info(self, player_id: str) -> PlayerInfo | None:
        """Display info for an API player id (gsis id or team code)."""
        if player_id in NFL_TEAMS:
            return self.defense(player_id)
        return self.by_gsis.get(player_id)

    def __len__(self) -> int:
        return len(self.by_gsis)

    # -- search ------------------------------------------------------------ #

    def _searchable(self) -> list[PlayerInfo]:
        """Players with a name and a fantasy position, plus the 32 defenses (built once)."""
        rows = getattr(self, "_search_rows", None)
        if rows is None:
            rows = [i for i in self.by_gsis.values() if i.name and i.position in FANTASY_POSITIONS]
            rows += [d for d in (self.defense(t) for t in sorted(NFL_TEAMS)) if d is not None]
            self._search_rows = rows
        return rows

    def search(
        self, query: str, position: str | None = None, limit: int = 50
    ) -> list[PlayerInfo]:
        """Case-insensitive name search (at least 2 characters).

        An exact gsis id, team code or team alias (``LAR``, ``JAC``, ``WSH``)
        also matches. Order: names starting with the query (any word) first,
        then the most recently active players, then players with a team, then
        alphabetically.
        """
        needle = query.strip().lower()
        if len(needle) < 2:
            return []
        wanted = _norm_position(position) if position else None
        hits: list[tuple[int, int, int, str, PlayerInfo]] = []
        for info in self._searchable():
            if wanted and info.position != wanted:
                continue
            name = (info.name or "").lower()
            ids = {str(info.player_id or "").lower()}
            if info.position == "DST" and info.team:
                ids |= {a.lower() for a, t in self.aliases.items() if t == info.team}
            exact_id = needle in ids
            if needle not in name and not exact_id:
                continue
            starts = name.startswith(needle) or any(w.startswith(needle) for w in name.split())
            # defenses are always current; unknown recency sorts last
            recency = -(info.last_active_season or (9999 if info.position == "DST" else 0))
            hits.append((0 if (starts or exact_id) else 1, recency, 0 if info.team else 1, name, info))
        hits.sort(key=lambda h: h[:4])
        return [h[4] for h in hits[: max(int(limit), 0)]]


class DirectoryHolder:
    """Lazily loaded, replaceable directory shared by the app (tests swap it in)."""

    def __init__(self, processed_dir: Path):
        self.processed_dir = processed_dir
        self._directory: PlayerDirectory | None = None
        self._lock = threading.Lock()

    def get(self) -> PlayerDirectory:
        with self._lock:
            if self._directory is None:
                self._directory = PlayerDirectory.from_store(self.processed_dir)
            return self._directory

    def set(self, directory: PlayerDirectory) -> None:
        with self._lock:
            self._directory = directory


__all__ = ["DirectoryHolder", "NFL_TEAMS", "PlayerDirectory", "PlayerInfo"]
