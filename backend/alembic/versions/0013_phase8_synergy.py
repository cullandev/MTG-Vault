"""Phase 8: card tags, synergy edges, cores.

Revision ID: 0013_phase8_synergy
Revises: 0012_battles
Created: 2026-08-28 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0013_phase8_synergy"
down_revision: str | None = "0012_battles"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the synergy tables."""
    op.create_table(
        "card_tags",
        sa.Column("oracle_id", sa.Text(), nullable=False),
        sa.Column("tag", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("oracle_id", "tag", name=op.f("pk_card_tags")),
    )
    op.create_index("ix_card_tags_tag", "card_tags", ["tag"])

    op.create_table(
        "synergy_edges",
        sa.Column("oracle_id_a", sa.Text(), nullable=False),
        sa.Column("oracle_id_b", sa.Text(), nullable=False),
        sa.Column("weight", sa.Float(), nullable=False),
        sa.Column("combo_w", sa.Float(), nullable=False),
        sa.Column("cooccur_w", sa.Float(), nullable=False),
        sa.Column("mechanical_w", sa.Float(), nullable=False),
        sa.Column("ai_w", sa.Float(), nullable=False),
        sa.Column("reasons_json", sa.JSON(), nullable=True),
        sa.Column("computed_at", sa.Text(), nullable=False),
        sa.CheckConstraint("oracle_id_a < oracle_id_b", name=op.f("ck_synergy_edges_ordered_pair")),
        sa.PrimaryKeyConstraint("oracle_id_a", "oracle_id_b", name=op.f("pk_synergy_edges")),
    )
    op.create_index("ix_synergy_edges_a", "synergy_edges", ["oracle_id_a", "weight"])
    op.create_index("ix_synergy_edges_b", "synergy_edges", ["oracle_id_b", "weight"])

    op.create_table(
        "synergy_cores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
        sa.Column("color_identity", sa.Text(), nullable=False),
        sa.Column("color_identity_mask", sa.Integer(), nullable=False),
        sa.Column("theme_name", sa.Text(), nullable=False),
        sa.Column("card_count", sa.Integer(), nullable=False),
        sa.Column("density", sa.Float(), nullable=False),
        sa.Column("buildability", sa.Float(), nullable=False),
        sa.Column("combined_score", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_synergy_cores")),
    )

    op.create_table(
        "synergy_core_cards",
        sa.Column("core_id", sa.Integer(), nullable=False),
        sa.Column("oracle_id", sa.Text(), nullable=False),
        sa.Column("centrality", sa.Float(), nullable=False),
        sa.ForeignKeyConstraint(
            ["core_id"],
            ["synergy_cores.id"],
            name=op.f("fk_synergy_core_cards_core_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("core_id", "oracle_id", name=op.f("pk_synergy_core_cards")),
    )


def downgrade() -> None:
    """Drop the synergy tables."""
    op.drop_table("synergy_core_cards")
    op.drop_table("synergy_cores")
    op.drop_table("synergy_edges")
    op.drop_table("card_tags")
