"""Price history, collection value over time, movers, alerts and the inbox.

Only cards that are *watched* -- owned, wishlisted, or referenced by a deck -- get a
daily snapshot (ADR-009). Snapshotting every printing would write about half a million
rows a day for data nobody looks at; snapshotting the watched few hundred is nothing.

The consequence is stated rather than hidden: price history begins the day a card
enters the collection, and a card added later has no back-history. The UI says so
instead of interpolating a line that was never measured.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow, utctoday

ALERT_SCOPES = ("owned", "card")
ALERT_DIRECTIONS = ("above", "below", "pct_up", "pct_down")
NOTIFICATION_KINDS = ("price_alert", "price_move", "job_failure", "legality_change", "battle")


class PriceSnapshot(Base):
    """One printing's price on one day.

    The composite primary key is the whole one-row-per-day guarantee: running the job
    twice in a day updates rather than duplicates, with no de-duplication logic to get
    wrong.
    """

    __tablename__ = "price_snapshots"

    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    snapshot_date: Mapped[str] = mapped_column(Text(), primary_key=True, default=utctoday)
    usd_cents: Mapped[int | None] = mapped_column(Integer())
    usd_foil_cents: Mapped[int | None] = mapped_column(Integer())
    usd_etched_cents: Mapped[int | None] = mapped_column(Integer())
    """NULL means "no price known", never zero. A card Scryfall has no price for is not
    a free card, and treating it as one would quietly deflate the collection total."""
    source: Mapped[str] = mapped_column(Text(), nullable=False, default="scryfall_bulk")

    __table_args__ = (Index("ix_price_snapshots_date", "snapshot_date"),)


class CollectionValueSnapshot(Base):
    """What the whole collection was worth on one day."""

    __tablename__ = "collection_value_snapshots"

    snapshot_date: Mapped[str] = mapped_column(Text(), primary_key=True, default=utctoday)
    total_cents: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    foil_cents: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    nonproxy_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    unique_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    unpriced_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    """Copies whose price is unknown. Shown beside the total rather than folded into
    it, so the number is never quietly wrong."""
    breakdown_json: Mapped[dict[str, Any] | None] = mapped_column(JSON())
    """By set, by rarity, and the ten most valuable copies."""


class PriceMovement(Base):
    """A printing whose price moved enough to be worth surfacing."""

    __tablename__ = "price_movements"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    card_id: Mapped[int] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    snapshot_date: Mapped[str] = mapped_column(Text(), nullable=False, default=utctoday)
    pct_change: Mapped[float] = mapped_column(nullable=False)
    from_cents: Mapped[int] = mapped_column(Integer(), nullable=False)
    to_cents: Mapped[int] = mapped_column(Integer(), nullable=False)
    compared_to_date: Mapped[str] = mapped_column(Text(), nullable=False)
    """Which earlier snapshot this was measured against. Not always yesterday: the job
    can miss a day, and a move measured over a week is a different claim from one
    measured overnight. The UI shows the span rather than implying it."""

    __table_args__ = (
        Index("ix_price_movements_date_pct", "snapshot_date", "pct_change"),
        Index("ix_price_movements_card_id", "card_id"),
    )


class PriceAlert(Base):
    """A standing rule about a price the user wants to hear about."""

    __tablename__ = "price_alerts"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scope: Mapped[str] = mapped_column(Text(), nullable=False, default="card")
    """``card`` watches one printing; ``owned`` watches everything in the collection."""
    card_id: Mapped[int | None] = mapped_column(ForeignKey("cards.id", ondelete="CASCADE"))
    direction: Mapped[str] = mapped_column(Text(), nullable=False)
    threshold_cents: Mapped[int | None] = mapped_column(Integer())
    threshold_pct: Mapped[float | None] = mapped_column()
    active: Mapped[bool] = mapped_column(default=True)
    cooldown_days: Mapped[int] = mapped_column(Integer(), nullable=False, default=7)
    """An alert that fires every day until the price moves back is an alert that gets
    ignored, so a fired alert stays quiet for this long."""
    last_fired_at: Mapped[str | None] = mapped_column(Text())
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_price_alerts_active", "active"),)


class Notification(Base):
    """One item in the in-app inbox.

    The inbox is the primary channel and always works; email is an optional extra
    delivery that can fail without losing the notification (ADR-011).
    """

    __tablename__ = "notifications"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    kind: Mapped[str] = mapped_column(Text(), nullable=False)
    title: Mapped[str] = mapped_column(Text(), nullable=False)
    body: Mapped[str | None] = mapped_column(Text())
    link: Mapped[str | None] = mapped_column(Text())
    read_at: Mapped[str | None] = mapped_column(Text())
    delivered_json: Mapped[dict[str, Any] | None] = mapped_column(JSON())
    """Which optional deliveries were attempted and how they went."""

    __table_args__ = (Index("ix_notifications_created_at", "created_at"),)
