"""Phase 3: price history, collection value, movers, alerts and the inbox.

Revision ID: 0007_phase3_pricing
Revises: 0006_drop_lending
Created: 2026-08-24 16:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0007_phase3_pricing"
down_revision: str | None = "0006_drop_lending"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    """Create the pricing, alerting and notification tables."""
    op.create_table(
        "price_snapshots",
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Text(), nullable=False),
        sa.Column("usd_cents", sa.Integer(), nullable=True),
        sa.Column("usd_foil_cents", sa.Integer(), nullable=True),
        sa.Column("usd_etched_cents", sa.Integer(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_price_snapshots_card_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("card_id", "snapshot_date", name=op.f("pk_price_snapshots")),
    )
    op.create_index("ix_price_snapshots_date", "price_snapshots", ["snapshot_date"])

    op.create_table(
        "collection_value_snapshots",
        sa.Column("snapshot_date", sa.Text(), nullable=False),
        sa.Column("total_cents", sa.Integer(), nullable=False),
        sa.Column("foil_cents", sa.Integer(), nullable=False),
        sa.Column("nonproxy_count", sa.Integer(), nullable=False),
        sa.Column("unique_count", sa.Integer(), nullable=False),
        sa.Column("unpriced_count", sa.Integer(), nullable=False),
        sa.Column("breakdown_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("snapshot_date", name=op.f("pk_collection_value_snapshots")),
    )

    op.create_table(
        "price_movements",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=False),
        sa.Column("snapshot_date", sa.Text(), nullable=False),
        sa.Column("pct_change", sa.Float(), nullable=False),
        sa.Column("from_cents", sa.Integer(), nullable=False),
        sa.Column("to_cents", sa.Integer(), nullable=False),
        sa.Column("compared_to_date", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_price_movements_card_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_movements")),
    )
    op.create_index(
        "ix_price_movements_date_pct", "price_movements", ["snapshot_date", "pct_change"]
    )
    op.create_index("ix_price_movements_card_id", "price_movements", ["card_id"])

    op.create_table(
        "price_alerts",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("scope", sa.Text(), nullable=False),
        sa.Column("card_id", sa.Integer(), nullable=True),
        sa.Column("direction", sa.Text(), nullable=False),
        sa.Column("threshold_cents", sa.Integer(), nullable=True),
        sa.Column("threshold_pct", sa.Float(), nullable=True),
        sa.Column("active", sa.Boolean(), nullable=False),
        sa.Column("cooldown_days", sa.Integer(), nullable=False),
        sa.Column("last_fired_at", sa.Text(), nullable=True),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.ForeignKeyConstraint(
            ["card_id"], ["cards.id"], name=op.f("fk_price_alerts_card_id"), ondelete="CASCADE"
        ),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_price_alerts")),
    )
    op.create_index("ix_price_alerts_active", "price_alerts", ["active"])

    op.create_table(
        "notifications",
        sa.Column("id", sa.Integer(), nullable=False),
        sa.Column("created_at", sa.Text(), nullable=False),
        sa.Column("kind", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=False),
        sa.Column("body", sa.Text(), nullable=True),
        sa.Column("link", sa.Text(), nullable=True),
        sa.Column("read_at", sa.Text(), nullable=True),
        sa.Column("delivered_json", sa.JSON(), nullable=True),
        sa.PrimaryKeyConstraint("id", name=op.f("pk_notifications")),
    )
    op.create_index("ix_notifications_created_at", "notifications", ["created_at"])


def downgrade() -> None:
    """Drop everything Phase 3 added."""
    op.drop_table("notifications")
    op.drop_table("price_alerts")
    op.drop_table("price_movements")
    op.drop_table("collection_value_snapshots")
    op.drop_table("price_snapshots")
