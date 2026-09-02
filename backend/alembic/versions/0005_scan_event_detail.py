"""Record per-stage detail on every scan event.

Revision ID: 0005_scan_detail
Revises: 0004_drop_frame_cache
Created: 2026-08-24 14:40:00.000000

``latency_ms`` can say a frame was slow but not which rung was slow, nor whether the
frame held a card at all. A scanning session is the only place that data exists, and it
is gone once the session ends unless it is written down.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0005_scan_detail"
down_revision: str | None = "0004_drop_frame_cache"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the per-event detail column."""
    with op.batch_alter_table("scan_events", schema=None) as batch_op:
        batch_op.add_column(sa.Column("detail_json", sa.JSON(), nullable=True))


def downgrade() -> None:
    """Drop it again."""
    with op.batch_alter_table("scan_events", schema=None) as batch_op:
        batch_op.drop_column("detail_json")
