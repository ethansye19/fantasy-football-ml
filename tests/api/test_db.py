"""BE-4.1: Alembic migrations match the SQLAlchemy models (SQLite here, PostgreSQL in CI)."""

from __future__ import annotations

import os
import uuid

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.migration import MigrationContext
from sqlalchemy import inspect, select

from api.db.models import Base, League, LeagueRosterSlot, LeagueTeam, ProjectionRun
from api.db.session import Database, alembic_config

TABLES = {
    "leagues",
    "league_teams",
    "league_roster_slots",
    "projection_runs",
    "league_projection_cache",
    "sessions",
}


def _url(tmp_path) -> str:
    # CI sets FFML_TEST_DATABASE_URL to a PostgreSQL service; locally SQLite.
    return os.environ.get("FFML_TEST_DATABASE_URL") or f"sqlite:///{(tmp_path / 't.db').as_posix()}"


@pytest.fixture
def db(tmp_path):
    database = Database(_url(tmp_path))
    with database.engine.begin() as conn:  # clean slate on a shared PostgreSQL
        cfg = alembic_config(database.url)
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
    database.migrate()
    yield database
    with database.engine.begin() as conn:
        cfg = alembic_config(database.url)
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
    database.dispose()


def test_upgrade_creates_every_table(db):
    names = set(inspect(db.engine).get_table_names())
    assert TABLES <= names
    assert "alembic_version" in names


def test_migrations_match_models(db):
    with db.engine.connect() as conn:
        ctx = MigrationContext.configure(conn, opts={"compare_type": True})
        diff = compare_metadata(ctx, Base.metadata)
    assert diff == [], diff


def test_downgrade_removes_tables(tmp_path):
    database = Database(_url(tmp_path))
    database.migrate()
    with database.engine.begin() as conn:
        cfg = alembic_config(database.url)
        cfg.attributes["connection"] = conn
        command.downgrade(cfg, "base")
    assert not (TABLES & set(inspect(database.engine).get_table_names()))
    database.dispose()


def test_round_trip_and_cascade(db):
    with db.session() as s:
        league = League(
            source="manual",
            name="Test",
            season=2026,
            num_teams=2,
            scoring_settings={"rec": 1.0},
            roster_positions=["QB", "BN"],
            settings={"playoff_week_start": 15},
        )
        team = LeagueTeam(team_name="Mine", is_me=True)
        team.slots.append(LeagueRosterSlot(player_id="00-0036322", sleeper_id="6794", slot="bench"))
        league.teams.append(team)
        s.add(league)
        s.add(
            ProjectionRun(
                run_id="r1", season=2024, week=10, model_version="v", status="private",
                environment="private_test", source_kind="replay", metrics={},
            )
        )
        s.flush()
        league_id = league.id
    with db.session() as s:
        got = s.get(League, league_id)
        assert isinstance(got.id, uuid.UUID)
        assert got.scoring_settings == {"rec": 1.0}
        assert got.teams[0].slots[0].player_id == "00-0036322"
        assert got.created_at.tzinfo is not None or db.engine.dialect.name == "sqlite"
        s.delete(got)
    with db.session() as s:
        assert s.scalars(select(LeagueRosterSlot)).all() == []
        assert s.scalars(select(LeagueTeam)).all() == []


def test_app_startup_migrates(make_client, api_dirs):
    client = make_client()
    assert client.get("/meta/health").json()["checks"]["database"] == "ok"
    names = set(inspect(client.app.state.db.engine).get_table_names())
    assert TABLES <= names
