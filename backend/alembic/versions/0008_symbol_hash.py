"""Hash the type-line band, so reprints can be told apart.

Revision ID: 0008_symbol_hash
Revises: 0007_phase3_pricing
Created: 2026-08-25 00:30:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0008_symbol_hash"
down_revision: str | None = "0007_phase3_pricing"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Add the symbol-band hash column.

    Nullable, and left empty: the existing artwork hashes stay exactly as they are, and
    the hashing job fills this in on its next pass. Making it NOT NULL would mean
    discarding hours of hashing to add a column.
    """
    op.add_column("card_hashes", sa.Column("symbol_phash", sa.LargeBinary(), nullable=True))


def downgrade() -> None:
    """Drop the symbol-band hash column."""
    op.drop_column("card_hashes", "symbol_phash")
