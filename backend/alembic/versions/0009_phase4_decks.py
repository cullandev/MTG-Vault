"""Phase 4: decks, deck cards, validations, and physical-copy allocation.

Revision ID: 0009_phase4_decks
Revises: 0008_symbol_hash
Created: 2026-08-25 21:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0009_phase4_decks"
down_revision: str | None = "0008_symbol_hash"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the deck tables."""
    op.create_table(
        "decks",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("is_built", sa.Boolean(), nullable=False),
        sa.Column("colors_cached", sa.Text(), nullable=False),
        sa.Column("commander_oracle_id", sa.Text(), nullable=True),
        sa.Column("partner_oracle_id", sa.Text(), nullable=True),
        sa.Column("companion_oracle_id", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("source_ref_json", sa.JSON(), nullable=True),
        sa.Column("goal_text", sa.Text(), nullable=True),
        sa.Column("archived", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("updated_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_decks")),
    )

    op.create_table(
        "deck_cards",
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("oracle_id", sa.Text(), nullable=False),
        sa.Column("board", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("preferred_set_code", sa.Text(), nullable=True),
        sa.Column("preferred_collector_number", sa.Text(), nullable=True),
        sa.Column("category", sa.Text(), nullable=True),
        sa.Column("is_proxy_intent", sa.Boolean(), nullable=False),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name=op.f("fk_deck_cards_deck_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("deck_id", "oracle_id", "board", name=op.f("pk_deck_cards")),
    )
    op.create_index("ix_deck_cards_oracle_id", "deck_cards", ["oracle_id"])

    op.create_table(
        "deck_validations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("checked_at", sa.Text(), nullable=False),
        sa.Column("is_legal", sa.Boolean(), nullable=False),
        sa.Column("errors_json", sa.JSON(), nullable=True),
        sa.Column("banlist_flag", sa.Boolean(), nullable=False),
        sa.Column("triggered_by", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name=op.f("fk_deck_validations_deck_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deck_validations")),
    )
    op.create_index(
        "ix_deck_validations_deck_id_checked_at", "deck_validations", ["deck_id", "checked_at"]
    )

    op.create_table(
        "deck_allocations",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("collection_item_id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("allocated_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["collection_item_id"],
            ["collection_items.id"],
            name=op.f("fk_deck_allocations_collection_item_id"),
            ondelete="CASCADE",
        ),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name=op.f("fk_deck_allocations_deck_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deck_allocations")),
        sa.UniqueConstraint("collection_item_id", name="uq_deck_allocations_collection_item_id"),
    )
    op.create_index("ix_deck_allocations_deck_id", "deck_allocations", ["deck_id"])


def downgrade() -> None:
    """Drop the deck tables."""
    op.drop_table("deck_allocations")
    op.drop_table("deck_validations")
    op.drop_table("deck_cards")
    op.drop_table("decks")
