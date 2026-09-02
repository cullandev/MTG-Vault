"""Phase 2 revision: perceptual hash index for card images.

Revision ID: 0003_card_hashes
Revises: 0002_phase2_scan
Created: 2026-08-23 22:40:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0003_card_hashes"
down_revision: str | None = "0002_phase2_scan"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the table the perceptual hash index is built from."""
    op.create_table(
        "card_hashes",
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("phash", sa.LargeBinary(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"],
            ["cards.id"],
            name=op.f("fk_card_hashes_card_id_cards"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("card_id", name=op.f("pk_card_hashes")),
    )


def downgrade() -> None:
    """Drop the hash table."""
    op.drop_table("card_hashes")
