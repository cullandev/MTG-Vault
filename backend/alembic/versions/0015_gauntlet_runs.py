"""Gauntlet runs: fresh vault decks battled against the ingested meta, over time.

Revision ID: 0015_gauntlet_runs
Revises: 0014_drop_http_cache
Created: 2026-08-28 12:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0015_gauntlet_runs"
down_revision: str | None = "0014_drop_http_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "gauntlet_runs",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("started_at", sa.Text(), nullable=False),
        sa.Column("finished_at", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("vault_distinct", sa.Integer(), nullable=False),
        sa.Column("games_played", sa.Integer(), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
    )
    op.create_index("ix_gauntlet_runs_started_at", "gauntlet_runs", ["started_at"])


def downgrade() -> None:
    op.drop_index("ix_gauntlet_runs_started_at", table_name="gauntlet_runs")
    op.drop_table("gauntlet_runs")
