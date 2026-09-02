"""Phase 7: meta snapshots, archetype templates, coverage.

Revision ID: 0011_phase7_meta
Revises: 0010_phase5_rating
Created: 2026-08-27 10:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0011_phase7_meta"
down_revision: str | None = "0010_phase5_rating"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the meta ingestion and template tables."""
    op.create_table(
        "meta_snapshots",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("measurement", sa.Text(), nullable=False),
        sa.Column("snapshot_date", sa.Text(), nullable=False),
        sa.Column("fetched_at", sa.Text(), nullable=False),
        sa.Column("status", sa.Text(), nullable=False),
        sa.Column("parser_version", sa.Integer(), nullable=False),
        sa.Column("item_count", sa.Integer(), nullable=False),
        sa.Column("error", sa.Text(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meta_snapshots")),
    )
    op.create_index(
        "ix_meta_snapshots_fmt_src_date", "meta_snapshots", ["format", "source", "snapshot_date"]
    )

    op.create_table(
        "meta_archetypes",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("archetype_key", sa.Text(), nullable=False),
        sa.Column("meta_share_pct", sa.Float(), nullable=False),
        sa.Column("placement_count", sa.Integer(), nullable=False),
        sa.Column("colors", sa.Text(), nullable=True),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["meta_snapshots.id"],
            name=op.f("fk_meta_archetypes_snapshot_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meta_archetypes")),
    )
    op.create_index("ix_meta_archetypes_key", "meta_archetypes", ["archetype_key", "snapshot_id"])

    op.create_table(
        "meta_decklists",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("archetype_id", sa.Integer(), nullable=False),
        sa.Column("source_url", sa.Text(), nullable=False),
        sa.Column("event", sa.Text(), nullable=True),
        sa.Column("player", sa.Text(), nullable=True),
        sa.Column("placement", sa.Integer(), nullable=True),
        sa.Column("event_date", sa.Text(), nullable=True),
        sa.Column("raw_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["archetype_id"],
            ["meta_archetypes.id"],
            name=op.f("fk_meta_decklists_archetype_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meta_decklists")),
    )
    op.create_index("ix_meta_decklists_archetype_id", "meta_decklists", ["archetype_id"])

    op.create_table(
        "meta_decklist_cards",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("decklist_id", sa.Integer(), nullable=False),
        sa.Column("oracle_id", sa.Text(), nullable=True),
        sa.Column("name_raw", sa.Text(), nullable=False),
        sa.Column("quantity", sa.Integer(), nullable=False),
        sa.Column("board", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["decklist_id"],
            ["meta_decklists.id"],
            name=op.f("fk_meta_decklist_cards_decklist_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_meta_decklist_cards")),
    )
    op.create_index("ix_meta_decklist_cards_decklist_id", "meta_decklist_cards", ["decklist_id"])
    op.create_index("ix_meta_decklist_cards_oracle_id", "meta_decklist_cards", ["oracle_id"])

    op.create_table(
        "archetype_templates",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("archetype_key", sa.Text(), nullable=False),
        sa.Column("format", sa.Text(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
        sa.Column("snapshot_id", sa.Integer(), nullable=False),
        sa.Column("list_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["snapshot_id"],
            ["meta_snapshots.id"],
            name=op.f("fk_archetype_templates_snapshot_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_archetype_templates")),
    )
    op.create_index(
        "ix_archetype_templates_key_format", "archetype_templates", ["archetype_key", "format"]
    )

    op.create_table(
        "archetype_template_cards",
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("oracle_id", sa.Text(), nullable=False),
        sa.Column("tier", sa.Text(), nullable=False),
        sa.Column("presence_pct", sa.Float(), nullable=False),
        sa.Column("typical_count", sa.Integer(), nullable=False),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["archetype_templates.id"],
            name=op.f("fk_archetype_template_cards_template_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint(
            "template_id", "oracle_id", name=op.f("pk_archetype_template_cards")
        ),
    )

    op.create_table(
        "coverage_results",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("template_id", sa.Integer(), nullable=False),
        sa.Column("computed_at", sa.Text(), nullable=False),
        sa.Column("weighted_coverage", sa.Float(), nullable=False),
        sa.Column("core_coverage", sa.Float(), nullable=False),
        sa.Column("missing_count", sa.Integer(), nullable=False),
        sa.Column("missing_cost_cents", sa.Integer(), nullable=False),
        sa.Column("conflict_count", sa.Integer(), nullable=False),
        sa.Column("rank_score", sa.Float(), nullable=False),
        sa.Column("detail_json", sa.JSON(), nullable=True),
        sa.ForeignKeyConstraint(
            ["template_id"],
            ["archetype_templates.id"],
            name=op.f("fk_coverage_results_template_id"),
            ondelete="CASCADE",
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_coverage_results")),
    )
    op.create_index("ix_coverage_results_rank", "coverage_results", ["computed_at", "rank_score"])


def downgrade() -> None:
    """Drop the meta tables."""
    op.drop_table("coverage_results")
    op.drop_table("archetype_template_cards")
    op.drop_table("archetype_templates")
    op.drop_table("meta_decklist_cards")
    op.drop_table("meta_decklists")
    op.drop_table("meta_archetypes")
    op.drop_table("meta_snapshots")
