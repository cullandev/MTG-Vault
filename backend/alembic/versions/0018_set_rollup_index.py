"""A covering index for the set rollup: 5.2 seconds to under a tenth.

The Sets page's ``GET /api/sets`` aggregates every printing by set. It could
walk an index for the grouping, but it also selects ``set_name``,
``released_at`` and the three price columns -- none of which any index
carried -- so SQLite fetched 117,613 individual rows out of the 92 MB
``cards`` table on every page load. Measured on the live instance: 5.25 s,
to return 10 KB of JSON.

Row fetches are what hurt here. The database file lives on a Windows
bind-mount, where table reads run at ~44 MB/s while covering-index scans run
at roughly 1 GB/s -- a measured 738x gap between the same aggregate with and
without the columns it needs in the index. This index carries them, so the
query never leaves the index.

7 MB on disk, ~0.2 s to build.

Revision ID: 0018_set_rollup_index
Revises: 0017_phase6_wishlist
"""

from __future__ import annotations

from alembic import op

revision: str = "0018_set_rollup_index"
down_revision: str | None = "0017_phase6_wishlist"
branch_labels: str | None = None
depends_on: str | None = None

INDEX_NAME = "ix_cards_set_rollup"


def upgrade() -> None:
    """Add the covering index for the per-set rollup."""
    op.create_index(
        INDEX_NAME,
        "cards",
        [
            "digital",
            "lang",
            "set_code",
            "collector_number",
            "id",
            "set_name",
            "released_at",
            "price_usd_cents",
            "price_usd_foil_cents",
            "price_usd_etched_cents",
        ],
    )


def downgrade() -> None:
    """Drop it."""
    op.drop_index(INDEX_NAME, table_name="cards")
