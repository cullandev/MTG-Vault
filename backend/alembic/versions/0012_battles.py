"""Forge battle results (ADR-031).

Revision ID: 0012_battles
Revises: 0011_phase7_meta
Created: 2026-08-27 20:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0012_battles"
down_revision: str | None = "0011_phase7_meta"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the battle_results table."""
    op.create_table(
        "battle_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("ran_at", sa.Text(), nullable=False),
        sa.Column("engine", sa.Text(), nullable=False),
        sa.Column("engine_version", sa.Text(), nullable=True),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("games_requested", sa.Integer(), nullable=False),
        sa.Column("games_completed", sa.Integer(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("duration_ms", sa.Integer(), nullable=True),
        sa.Column("decks_json", sa.JSON(), nullable=True),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_battle_results")),
    )
    op.create_index("ix_battle_results_ran_at", "battle_results", ["ran_at"])


def downgrade() -> None:
    """Drop the battle_results table."""
    op.drop_table("battle_results")
