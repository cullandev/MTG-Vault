"""Tag rescans for review: rejected_at and the accepted scan that superseded it.

Hitting "Rescan" is the scanner's only ground-truth signal that an
identification was wrong before anything was added. Recording it -- and linking
it to the scan the user eventually accepted -- turns every rescan into a
reviewable (proposed, accepted) pair.

Revision ID: 0016_scan_rejections
Revises: 0015_gauntlet_runs
Created: 2026-08-28 13:00:00.000000
"""

from __future__ import annotations

from collections.abc import Sequence

import sqlalchemy as sa

from alembic import op

revision: str = "0016_scan_rejections"
down_revision: str | None = "0015_gauntlet_runs"
branch_labels: str | Sequence[str] | None = None
depends_on: str | Sequence[str] | None = None


def upgrade() -> None:
    op.add_column("scan_events", sa.Column("rejected_at", sa.Text(), nullable=True))
    op.add_column("scan_events", sa.Column("superseded_by_event_id", sa.Integer(), nullable=True))
    op.create_index("ix_scan_events_rejected_at", "scan_events", ["rejected_at"])


def downgrade() -> None:
    op.drop_index("ix_scan_events_rejected_at", table_name="scan_events")
    op.drop_column("scan_events", "superseded_by_event_id")
    op.drop_column("scan_events", "rejected_at")
