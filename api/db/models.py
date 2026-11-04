"""SQLAlchemy models for the app database (DATA_SCHEMA.md section 5, BE-4.1).

The same models run on SQLite (local development, tests) and PostgreSQL
(compose and CI, D-004). Portable types: ``Uuid`` (native UUID on PostgreSQL,
CHAR(32) on SQLite), ``JSON`` with a JSONB variant on PostgreSQL, timezone-aware
timestamps. Schema changes go through Alembic (``api/migrations``).

Differences from the section 5 sketch, all additive:

- ``leagues``: unique ``(source, external_id, season)`` so a Sleeper league
  re-imports in place.
- ``league_teams``: ``team_name`` (Sleeper's per-league team name).
- ``league_roster_slots``: ``position``, ``name`` and ``nfl_team`` captured at sync
  time for display when the crosswalk has no gsis id.
- ``projection_runs``: ``approval_status``, ``environment`` and
  ``source_kind`` (published, private_test, replay, fixture) so a private
  test run can never be mistaken for a published one (D-099).
- ``league_projection_cache``: ``scoring_hash`` in the key.
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    JSON,
    Boolean,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    MetaData,
    String,
    UniqueConstraint,
    Uuid,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column, relationship

JSONType = JSON().with_variant(JSONB(), "postgresql")

NAMING = {
    "ix": "ix_%(column_0_label)s",
    "uq": "uq_%(table_name)s_%(column_0_name)s",
    "ck": "ck_%(table_name)s_%(constraint_name)s",
    "fk": "fk_%(table_name)s_%(column_0_name)s_%(referred_table_name)s",
    "pk": "pk_%(table_name)s",
}


def utcnow() -> datetime:
    return datetime.now(UTC)


class Base(DeclarativeBase):
    metadata = MetaData(naming_convention=NAMING)


class League(Base):
    __tablename__ = "leagues"
    __table_args__ = (UniqueConstraint("source", "external_id", "season"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    source: Mapped[str] = mapped_column(String(16))  # sleeper | manual
    external_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    name: Mapped[str] = mapped_column(String(200))
    season: Mapped[int] = mapped_column(Integer)
    num_teams: Mapped[int] = mapped_column(Integer)
    scoring_settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    roster_positions: Mapped[list[str]] = mapped_column(JSONType, default=list)
    settings: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow
    )
    last_synced_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True), nullable=True)

    teams: Mapped[list[LeagueTeam]] = relationship(
        back_populates="league",
        cascade="all, delete-orphan",
        order_by="LeagueTeam.external_roster_id",
    )


class LeagueTeam(Base):
    __tablename__ = "league_teams"
    __table_args__ = (Index("ix_league_teams_league_id", "league_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    league_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leagues.id", ondelete="CASCADE")
    )
    external_roster_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    team_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner_name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    owner_external_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    is_me: Mapped[bool] = mapped_column(Boolean, default=False)

    league: Mapped[League] = relationship(back_populates="teams")
    slots: Mapped[list[LeagueRosterSlot]] = relationship(
        back_populates="league_team", cascade="all, delete-orphan"
    )


class LeagueRosterSlot(Base):
    __tablename__ = "league_roster_slots"
    __table_args__ = (Index("ix_league_roster_slots_league_team_id", "league_team_id"),)

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    league_team_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("league_teams.id", ondelete="CASCADE")
    )
    player_id: Mapped[str | None] = mapped_column(String(32), nullable=True)  # gsis id / team
    sleeper_id: Mapped[str | None] = mapped_column(String(32), nullable=True)
    position: Mapped[str | None] = mapped_column(String(8), nullable=True)
    name: Mapped[str | None] = mapped_column(String(200), nullable=True)
    nfl_team: Mapped[str | None] = mapped_column(String(8), nullable=True)
    slot: Mapped[str] = mapped_column(String(16))  # starter | bench | ir | taxi
    synced_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)

    league_team: Mapped[LeagueTeam] = relationship(back_populates="slots")


class ProjectionRun(Base):
    __tablename__ = "projection_runs"

    run_id: Mapped[str] = mapped_column(String(128), primary_key=True)
    season: Mapped[int] = mapped_column(Integer)
    week: Mapped[int] = mapped_column(Integer)
    model_version: Mapped[str] = mapped_column(String(128))
    status: Mapped[str] = mapped_column(String(16))  # published | superseded | failed | private
    approval_status: Mapped[str] = mapped_column(String(16), default="not approved")
    environment: Mapped[str] = mapped_column(String(16))
    source_kind: Mapped[str] = mapped_column(String(16))
    metrics: Mapped[dict[str, Any]] = mapped_column(JSONType, default=dict)
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class LeagueProjectionCache(Base):
    __tablename__ = "league_projection_cache"

    league_id: Mapped[uuid.UUID] = mapped_column(
        Uuid, ForeignKey("leagues.id", ondelete="CASCADE"), primary_key=True
    )
    run_id: Mapped[str] = mapped_column(
        String(128), ForeignKey("projection_runs.run_id", ondelete="CASCADE"), primary_key=True
    )
    scoring_hash: Mapped[str] = mapped_column(String(16), primary_key=True)
    week: Mapped[int] = mapped_column(Integer, primary_key=True)
    player_id: Mapped[str] = mapped_column(String(32), primary_key=True)
    floor: Mapped[float | None] = mapped_column(Float, nullable=True)
    median: Mapped[float | None] = mapped_column(Float, nullable=True)
    ceiling: Mapped[float | None] = mapped_column(Float, nullable=True)
    boom_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    bust_prob: Mapped[float | None] = mapped_column(Float, nullable=True)
    ros_median: Mapped[float | None] = mapped_column(Float, nullable=True)
    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


class AppSession(Base):
    """Browser session pointing at a league and team (D-011, no accounts)."""

    __tablename__ = "sessions"

    id: Mapped[uuid.UUID] = mapped_column(Uuid, primary_key=True, default=uuid.uuid4)
    league_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("leagues.id", ondelete="SET NULL"), nullable=True
    )
    league_team_id: Mapped[uuid.UUID | None] = mapped_column(
        Uuid, ForeignKey("league_teams.id", ondelete="SET NULL"), nullable=True
    )
    created_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)
    last_seen_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), default=utcnow)


__all__ = [
    "AppSession",
    "Base",
    "League",
    "LeagueProjectionCache",
    "LeagueRosterSlot",
    "LeagueTeam",
    "ProjectionRun",
]
