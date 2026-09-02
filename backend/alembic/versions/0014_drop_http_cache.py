"""Drop the never-used http_cache table.

Phase 0 sketched a generic external-response cache; what actually got built is a
purpose-built cache table per client (edhrec_cache, spellbook_cache, the meta
snapshot tables). Nothing ever read or wrote http_cache -- dropping it removes a
ghost the documentation had to keep explaining.

Revision ID: 0014_drop_http_cache
Revises: 0013_phase8_synergy
Created: 2026-08-28 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0014_drop_http_cache"
down_revision: str | None = "0013_phase8_synergy"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.drop_table("http_cache")


def downgrade() -> None:
    op.create_table(
        "http_cache",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column("service", sa.Text(), nullable=False),
        sa.Column("url_hash", sa.Text(), nullable=False),
        sa.Column("url", sa.Text(), nullable=False),
        sa.Column("method", sa.Text(), nullable=False),
        sa.Column("status", sa.Integer(), nullable=True),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("expires_at", sa.Text(), nullable=True),
        sa.Column("etag", sa.Text(), nullable=True),
        sa.Column("body_path", sa.Text(), nullable=True),
        sa.Column("error", sa.Text(), nullable=True),
        sa.UniqueConstraint("url_hash", name="http_cache_url_hash"),
    )
    op.create_index("ix_http_cache_service_expires_at", "http_cache", ["service", "expires_at"])
