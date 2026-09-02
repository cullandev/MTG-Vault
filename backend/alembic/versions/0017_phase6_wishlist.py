"""Phase 6: the wishlist.

Standalone wishes only; per-deck needs stay derived from missing lists.

Revision ID: 0017_phase6_wishlist
Revises: 0016_scan_rejections
Created: 2026-08-28 14:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0017_phase6_wishlist"
down_revision: str | None = "0016_scan_rejections"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.create_table(
        "wishlist",
        sa.Column("id", sa.Integer(), primary_key=True, autoincrement=True),
        sa.Column(
            "oracle_id",
            sa.Text(),
            sa.ForeignKey("oracle_cards.oracle_id", ondelete="CASCADE"),
            nullable=False,
        ),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("priority", sa.Integer(), nullable=False),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
    )
    op.create_index("ix_wishlist_oracle_id", "wishlist", ["oracle_id"])


def downgrade() -> None:
    op.drop_index("ix_wishlist_oracle_id", table_name="wishlist")
    op.drop_table("wishlist")
