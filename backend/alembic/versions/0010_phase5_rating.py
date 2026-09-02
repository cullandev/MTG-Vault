"""Phase 5: heuristic scores, AI cache, EDHREC and Spellbook caches.

Revision ID: 0010_phase5_rating
Revises: 0009_phase4_decks
Created: 2026-08-26 09:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0010_phase5_rating"
down_revision: str | None = "0009_phase4_decks"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the rating and external-cache tables."""
    op.create_table(
        "deck_scores",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("deck_id", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
        sa.Column("consistency", sa.Float(), nullable=False),
        sa.Column("speed", sa.Float(), nullable=False),
        sa.Column("interaction", sa.Float(), nullable=False),
        sa.Column("resilience", sa.Float(), nullable=False),
        sa.Column("bracket", sa.Integer(), nullable=True),
        sa.Column("signals_json", sa.JSON(), nullable=True),
        sa.Column("heuristic_version", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["deck_id"], ["decks.id"], name=op.f("fk_deck_scores_deck_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_deck_scores")),
    )
    op.create_index("ix_deck_scores_deck_id_computed_at", "deck_scores", ["deck_id", "computed_at"])

    op.create_table(
        "ai_cache",
        sa.Column("request_hash", sa.Text(), nullable=False),
        sa.Column("model", sa.Text(), nullable=False),
        sa.Column("prompt_version", sa.Integer(), nullable=False),
        sa.Column("request_json", sa.JSON(), nullable=True),
        sa.Column("response_json", sa.JSON(), nullable=True),
        sa.Column("input_tokens", sa.Integer(), nullable=False),
        sa.Column("output_tokens", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("request_hash", name=op.f("pk_ai_cache")),
    )

    op.create_table(
        "edhrec_commanders",
        sa.Column("oracle_id", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("payload_json", sa.JSON(), nullable=True),
        sa.Column("parser_version", sa.Integer(), nullable=False),
        sa.PrimaryKeyConstraint("oracle_id", name=op.f("pk_edhrec_commanders")),
    )

    op.create_table(
        "edhrec_cooccurrence",
        sa.Column("commander_oracle_id", sa.Text(), nullable=False),
        sa.Column("oracle_id", sa.Text(), nullable=False),
        sa.Column("inclusion_pct", sa.Float(), nullable=False),
        sa.Column("synergy", sa.Float(), nullable=False),
        sa.PrimaryKeyConstraint(
            "commander_oracle_id", "oracle_id", name=op.f("pk_edhrec_cooccurrence")
        ),
    )

    op.create_table(
        "spellbook_combos",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("combo_id", sa.Text(), nullable=False),
        sa.Column("oracle_ids_json", sa.JSON(), nullable=True),
        sa.Column("result_text", sa.Text(), nullable=True),
        sa.Column("colors", sa.Text(), nullable=True),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_spellbook_combos")),
        sa.UniqueConstraint("combo_id", name=op.f("uq_spellbook_combos_combo_id")),
    )

    op.create_table(
        "spellbook_combo_cards",
        sa.Column("combo_id", sa.Text(), nullable=False),
        sa.Column("oracle_id", sa.Text(), nullable=False),
        sa.PrimaryKeyConstraint("combo_id", "oracle_id", name=op.f("pk_spellbook_combo_cards")),
    )
    op.create_index("ix_spellbook_combo_cards_oracle_id", "spellbook_combo_cards", ["oracle_id"])


def downgrade() -> None:
    """Drop the rating and external-cache tables."""
    op.drop_table("spellbook_combo_cards")
    op.drop_table("spellbook_combos")
    op.drop_table("edhrec_cooccurrence")
    op.drop_table("edhrec_commanders")
    op.drop_table("ai_cache")
    op.drop_table("deck_scores")
