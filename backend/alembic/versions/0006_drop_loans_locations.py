"""Remove lending and storage locations.

Revision ID: 0006_drop_lending
Revises: 0005_scan_detail
Created: 2026-08-24 15:30:00.000000

Neither is wanted for this collection. Both were built in Phase 1 and neither ever
held a row, so nothing is lost -- but leaving them would mean Phase 3's dashboard and
Phase 4's deck availability building on top of features nobody uses.
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0006_drop_lending"
down_revision: str | None = "0005_scan_detail"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Drop the loan and location tables, and the column pointing at them."""
    with op.batch_alter_table("collection_items", schema=None) as batch_op:
        batch_op.drop_index("ix_collection_items_storage_location_id_oracle_id")
        batch_op.drop_column("storage_location_id")

    op.drop_table("loans")
    op.drop_table("storage_locations")


def downgrade() -> None:
    """Recreate them, empty."""
    op.create_table(
        "storage_locations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("parent_id", sa.Integer(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["parent_id"], ["storage_locations.id"], name=op.f("fk_storage_locations_parent_id")
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_storage_locations")),
    )
    op.create_table(
        "loans",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_item_id", sa.Integer(), nullable=False),
        sa.Column("person", sa.Text(), nullable=False),
        sa.Column("lent_at", sa.Text(), nullable=False),
        sa.Column("due_at", sa.Text(), nullable=True),
        sa.Column("returned_at", sa.Text(), nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["collection_item_id"],
            ["collection_items.id"],
            name=op.f("fk_loans_collection_item_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_loans")),
    )
    with op.batch_alter_table("collection_items", schema=None) as batch_op:
        batch_op.add_column(sa.Column("storage_location_id", sa.Integer(), nullable=True))
        batch_op.create_index(
            "ix_collection_items_storage_location_id_oracle_id",
            ["storage_location_id", "oracle_id"],
        )
