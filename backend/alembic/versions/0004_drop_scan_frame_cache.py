"""Drop the scan_events columns the frame cache used.

Revision ID: 0004_drop_frame_cache
Revises: 0003_card_hashes
Created: 2026-08-24 14:20:00.000000

The client no longer computes a perceptual hash of each frame -- it waits for the view
to settle instead (ADR-026) -- so nothing sends one and the server-side frame cache
could never fire. Both it and the two columns it wrote are gone.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0004_drop_frame_cache"
down_revision: str | None = "0003_card_hashes"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Remove the frame-cache bookkeeping from scan_events."""
    with op.batch_alter_table("scan_events", schema=None) as batch_op:
        batch_op.drop_column("dhash")
        batch_op.drop_column("cached")


def downgrade() -> None:
    """Restore the columns, defaulting to what the cache would have written."""
    with op.batch_alter_table("scan_events", schema=None) as batch_op:
        batch_op.add_column(
            sa.Column("cached", sa.Boolean(), nullable=False, server_default=sa.false())
        )
        batch_op.add_column(sa.Column("dhash", sa.Text(), nullable=True))
