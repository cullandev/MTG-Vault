"""The physical collection: one row per physical copy.

There is deliberately no ``quantity`` column (ADR-005). Condition, finish and -- from
Phase 4 -- deck allocation are all per-copy facts, and stack splitting is the richest
source of bugs in collection managers. Aggregation happens in queries.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow

FINISHES = ("nonfoil", "foil", "etched")
CONDITIONS = ("NM", "LP", "MP", "HP", "DMG")


class CollectionItem(Base):
    """A single physical copy of a card."""

    __tablename__ = "collection_items"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id", ondelete="SET NULL"))
    """Resolved printing. NULL only while a card is awaiting a Scryfall re-import."""

    # Denormalised natural key (ADR-006). Survives Scryfall ID churn and lets the
    # collection be re-resolved after any bulk import without a join.
    oracle_id: Mapped[str] = mapped_column(Text(), nullable=False)
    set_code: Mapped[str] = mapped_column(Text(), nullable=False)
    collector_number: Mapped[str] = mapped_column(Text(), nullable=False)
    lang: Mapped[str] = mapped_column(Text(), nullable=False, default="en")

    finish: Mapped[str] = mapped_column(Text(), nullable=False, default="nonfoil")
    condition: Mapped[str] = mapped_column(Text(), nullable=False, default="NM")
    is_proxy: Mapped[bool] = mapped_column(default=False)
    """Proxies are excluded from every value calculation."""

    acquired_at: Mapped[str | None] = mapped_column(Text())
    acquired_price_cents: Mapped[int | None] = mapped_column(Integer())
    notes: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (
        Index("ix_collection_items_oracle_id_finish_is_proxy", "oracle_id", "finish", "is_proxy"),
        Index("ix_collection_items_card_id", "card_id"),
        Index("ix_collection_items_created_at", "created_at"),
    )


class WishlistItem(Base):
    """A card the owner wants but does not (sufficiently) own (Phase 6).

    Standalone wishes only -- per-deck needs are *derived* from theoretical
    decks' missing lists at read time, never duplicated in here. The buy list
    endpoint merges the two.
    """

    __tablename__ = "wishlist"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    oracle_id: Mapped[str] = mapped_column(
        ForeignKey("oracle_cards.oracle_id", ondelete="CASCADE"), nullable=False
    )
    quantity: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
    priority: Mapped[int] = mapped_column(Integer(), nullable=False, default=2)
    """1 = must have, 2 = want, 3 = someday."""
    note: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_wishlist_oracle_id", "oracle_id"),)


class AuditLog(Base):
    """One row per collection mutation, with enough state to undo it.

    ``batch_id`` groups a logical operation -- a CSV import, a scan session, a bulk
    add -- so the whole thing can be reverted as a unit.
    """

    __tablename__ = "audit_log"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    actor: Mapped[str] = mapped_column(Text(), nullable=False, default="user")
    action: Mapped[str] = mapped_column(Text(), nullable=False)
    """``create`` | ``update`` | ``delete`` | ``revert``."""
    entity_type: Mapped[str] = mapped_column(Text(), nullable=False)
    entity_id: Mapped[str | None] = mapped_column(Text())
    batch_id: Mapped[str] = mapped_column(Text(), nullable=False)
    source: Mapped[str] = mapped_column(Text(), nullable=False, default="api")
    """``api`` | ``csv_import`` | ``scan`` | ``job`` | ``revert``."""
    before_json: Mapped[dict[str, Any] | None] = mapped_column()
    after_json: Mapped[dict[str, Any] | None] = mapped_column()
    note: Mapped[str | None] = mapped_column(Text())
    reverted_at: Mapped[str | None] = mapped_column(Text())
    revert_of_id: Mapped[int | None] = mapped_column(Integer())

    __table_args__ = (
        Index("ix_audit_log_batch_id_ts", "batch_id", "ts"),
        Index("ix_audit_log_entity_type_entity_id_ts", "entity_type", "entity_id", "ts"),
        Index("ix_audit_log_ts", "ts"),
    )
