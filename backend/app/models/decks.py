"""Decks, their cards, and the allocation of physical copies.

A deck lists *oracle* identities -- what the deck wants -- while an allocation binds
one specific physical copy to one built deck. The ``UNIQUE`` constraint on
``deck_allocations.collection_item_id`` is what makes "a copy sleeved in Deck A is not
available to Deck B" a database invariant rather than application discipline
(ARCHITECTURE.md section 3.4).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow

BOARDS = ("main", "side", "commander", "companion", "maybe")
DECK_SOURCES = ("manual", "meta", "synergy", "import")


class Deck(Base):
    """A deck: a named list of oracle cards in a format.

    ``commander_oracle_id``, ``partner_oracle_id`` and ``companion_oracle_id`` are
    caches derived from the ``commander`` and ``companion`` board rows -- the board
    rows are the source of truth, and the service layer refreshes these columns on
    every card mutation so list views never join ``deck_cards``.
    """

    __tablename__ = "decks"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    format: Mapped[str] = mapped_column(Text(), nullable=False, default="commander")
    is_built: Mapped[bool] = mapped_column(default=False)
    """Built decks hold physical copies; theoretical decks allocate nothing."""
    colors_cached: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    """WUBRG letters of the deck's colour identity, refreshed on card mutation."""
    commander_oracle_id: Mapped[str | None] = mapped_column(Text())
    partner_oracle_id: Mapped[str | None] = mapped_column(Text())
    companion_oracle_id: Mapped[str | None] = mapped_column(Text())
    source: Mapped[str] = mapped_column(Text(), nullable=False, default="manual")
    """One of :data:`DECK_SOURCES`."""
    source_ref_json: Mapped[dict[str, Any] | None] = mapped_column()
    goal_text: Mapped[str | None] = mapped_column(Text())
    archived: Mapped[bool] = mapped_column(default=False)
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    updated_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)


class DeckCard(Base):
    """One oracle card in one board of one deck."""

    __tablename__ = "deck_cards"

    deck_id: Mapped[int] = mapped_column(
        ForeignKey("decks.id", ondelete="CASCADE"), primary_key=True
    )
    oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    board: Mapped[str] = mapped_column(Text(), primary_key=True, default="main")
    """One of :data:`BOARDS`."""
    quantity: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
    preferred_set_code: Mapped[str | None] = mapped_column(Text())
    preferred_collector_number: Mapped[str | None] = mapped_column(Text())
    """Which printing to allocate and to show; any printing when NULL."""
    category: Mapped[str | None] = mapped_column(Text())
    """Free-text grouping in the deck view ("Ramp", "Removal", ...)."""
    is_proxy_intent: Mapped[bool] = mapped_column(default=False)
    """The owner intends to play a proxy of this card (OPEN-QUESTIONS item 11)."""

    __table_args__ = (Index("ix_deck_cards_oracle_id", "oracle_id"),)


class DeckValidation(Base):
    """The recorded outcome of one legality check of one deck."""

    __tablename__ = "deck_validations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), nullable=False)
    checked_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    is_legal: Mapped[bool] = mapped_column(nullable=False)
    errors_json: Mapped[dict[str, Any] | None] = mapped_column()
    """``{"errors": [...], "warnings": [...]}`` as returned by the rules engine."""
    banlist_flag: Mapped[bool] = mapped_column(default=False)
    """Set when the check was provoked by a legality change touching this deck."""
    triggered_by: Mapped[str] = mapped_column(Text(), nullable=False, default="edit")
    """``edit`` | ``legality_change``."""

    __table_args__ = (Index("ix_deck_validations_deck_id_checked_at", "deck_id", "checked_at"),)


class DeckAllocation(Base):
    """One physical copy sleeved into one built deck.

    ``collection_item_id`` is UNIQUE: a copy can be in at most one built deck, and a
    double allocation is an integrity error rather than a silent overlap.
    """

    __tablename__ = "deck_allocations"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    collection_item_id: Mapped[int] = mapped_column(
        ForeignKey("collection_items.id", ondelete="CASCADE"), nullable=False
    )
    deck_id: Mapped[int] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), nullable=False)
    allocated_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (
        UniqueConstraint("collection_item_id", name="uq_deck_allocations_collection_item_id"),
        Index("ix_deck_allocations_deck_id", "deck_id"),
    )
