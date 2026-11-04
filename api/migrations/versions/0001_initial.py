"""Initial app schema (DATA_SCHEMA.md section 5).

Revision ID: 0001_initial
Revises:
Create Date: 2026-11-04
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects.postgresql import JSONB

revision = "0001_initial"
down_revision = None
branch_labels = None
depends_on = None

JSONType = sa.JSON().with_variant(JSONB(), "postgresql")
TS = sa.DateTime(timezone=True)


def upgrade() -> None:
    op.create_table(
        "leagues",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("source", sa.String(16), nullable=False),
        sa.Column("external_id", sa.String(32), nullable=True),
        sa.Column("name", sa.String(200), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("num_teams", sa.Integer(), nullable=False),
        sa.Column("scoring_settings", JSONType, nullable=False),
        sa.Column("roster_positions", JSONType, nullable=False),
        sa.Column("settings", JSONType, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("updated_at", TS, nullable=False),
        sa.Column("last_synced_at", TS, nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_leagues")),
        sa.UniqueConstraint("source", "external_id", "season", name=op.f("uq_leagues_source")),
    )
    op.create_table(
        "league_teams",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("external_roster_id", sa.String(32), nullable=True),
        sa.Column("team_name", sa.String(200), nullable=True),
        sa.Column("owner_name", sa.String(200), nullable=True),
        sa.Column("owner_external_id", sa.String(32), nullable=True),
        sa.Column("is_me", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name=op.f("fk_league_teams_league_id_leagues"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_league_teams")),
    )
    op.create_index("ix_league_teams_league_id", "league_teams", ["league_id"])
    op.create_table(
        "league_roster_slots",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("league_team_id", sa.Uuid(), nullable=False),
        sa.Column("player_id", sa.String(32), nullable=True),
        sa.Column("sleeper_id", sa.String(32), nullable=True),
        sa.Column("position", sa.String(8), nullable=True),
        sa.Column("name", sa.String(200), nullable=True),
        sa.Column("nfl_team", sa.String(8), nullable=True),
        sa.Column("slot", sa.String(16), nullable=False),
        sa.Column("synced_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["league_team_id"],
            ["league_teams.id"],
            name=op.f("fk_league_roster_slots_league_team_id_league_teams"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_league_roster_slots")),
    )
    op.create_index(
        "ix_league_roster_slots_league_team_id", "league_roster_slots", ["league_team_id"]
    )
    op.create_table(
        "projection_runs",
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("season", sa.Integer(), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("model_version", sa.String(128), nullable=False),
        sa.Column("status", sa.String(16), nullable=False),
        sa.Column("approval_status", sa.String(16), nullable=False),
        sa.Column("environment", sa.String(16), nullable=False),
        sa.Column("source_kind", sa.String(16), nullable=False),
        sa.Column("metrics", JSONType, nullable=False),
        sa.Column("created_at", TS, nullable=False),
        sa.PrimaryKeyConstraint("run_id", name=op.f("pk_projection_runs")),
    )
    op.create_table(
        "league_projection_cache",
        sa.Column("league_id", sa.Uuid(), nullable=False),
        sa.Column("run_id", sa.String(128), nullable=False),
        sa.Column("scoring_hash", sa.String(16), nullable=False),
        sa.Column("week", sa.Integer(), nullable=False),
        sa.Column("player_id", sa.String(32), nullable=False),
        sa.Column("floor", sa.Float(), nullable=True),
        sa.Column("median", sa.Float(), nullable=True),
        sa.Column("ceiling", sa.Float(), nullable=True),
        sa.Column("boom_prob", sa.Float(), nullable=True),
        sa.Column("bust_prob", sa.Float(), nullable=True),
        sa.Column("ros_median", sa.Float(), nullable=True),
        sa.Column("computed_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name=op.f("fk_league_projection_cache_league_id_leagues"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["run_id"],
            ["projection_runs.run_id"],
            name=op.f("fk_league_projection_cache_run_id_projection_runs"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "league_id",
            "run_id",
            "scoring_hash",
            "week",
            "player_id",
            name=op.f("pk_league_projection_cache"),
        ),
    )
    op.create_table(
        "sessions",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("league_id", sa.Uuid(), nullable=True),
        sa.Column("league_team_id", sa.Uuid(), nullable=True),
        sa.Column("created_at", TS, nullable=False),
        sa.Column("last_seen_at", TS, nullable=False),
        sa.ForeignKeyConstraint(
            ["league_id"],
            ["leagues.id"],
            name=op.f("fk_sessions_league_id_leagues"),
            ondelete="SET NULL",
        ),
        sa.ForeignKeyConstraint(
            ["league_team_id"],
            ["league_teams.id"],
            name=op.f("fk_sessions_league_team_id_league_teams"),
            ondelete="SET NULL",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_sessions")),
    )


def downgrade() -> None:
    op.drop_table("sessions")
    op.drop_table("league_projection_cache")
    op.drop_table("projection_runs")
    op.drop_index("ix_league_roster_slots_league_team_id", table_name="league_roster_slots")
    op.drop_table("league_roster_slots")
    op.drop_index("ix_league_teams_league_id", table_name="league_teams")
    op.drop_table("league_teams")
    op.drop_table("leagues")
