"""Create, edit and delete decks and their cards.

Every mutation writes audit rows under one batch id, so a deck edit -- like any
collection change -- is one line in History and one click to undo. Deck-card rows
have a composite primary key, so their audit entries always use the bulk actions,
whose snapshots carry the full key (see :mod:`app.services.audit`).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.errors import AppError, Conflict, NotFound
from app.models import (
    BOARDS,
    Card,
    Deck,
    DeckAllocation,
    DeckCard,
    OracleCard,
    utcnow,
)
from app.services import audit
from app.services.audit import BULK_CREATE, BULK_DELETE

#: WUBRG in canonical order, for ``colors_cached``.
_WUBRG = "WUBRG"


@dataclass
class DeckSpec:
    """What it takes to create a deck."""

    name: str
    format: str = "commander"
    is_built: bool = False
    commander_oracle_id: str | None = None
    goal_text: str | None = None
    source: str = "manual"
    source_ref: dict[str, Any] | None = None


def get_deck(db: DbSession, deck_id: int) -> Deck:
    """Fetch a deck or raise.

    Raises:
        NotFound: No such deck.
    """
    deck = db.get(Deck, deck_id)
    if deck is None:
        raise NotFound(f"No deck {deck_id}")
    return deck


def create_deck(db: DbSession, spec: DeckSpec) -> tuple[Deck, str]:
    """Create a deck, optionally seeding its commander.

    Returns:
        The deck and the audit batch id.
    """
    deck = Deck(
        name=spec.name,
        format=spec.format.lower(),
        is_built=False,
        source=spec.source,
        source_ref_json=spec.source_ref,
        goal_text=spec.goal_text,
    )
    db.add(deck)
    db.flush()
    batch = audit.new_batch_id()
    if spec.commander_oracle_id is not None:
        _require_oracle(db, spec.commander_oracle_id)
        db.add(
            DeckCard(
                deck_id=deck.id,
                oracle_id=spec.commander_oracle_id,
                board="commander",
                quantity=1,
            )
        )
        db.flush()
    refresh_caches(db, deck)
    audit.record(
        db,
        action="create",
        entity_type="deck",
        entity_id=deck.id,
        batch_id=batch,
        after=audit.snapshot(deck),
        note=f"created deck {deck.name}",
    )
    return deck, batch


def update_deck(db: DbSession, deck_id: int, changes: dict[str, Any]) -> tuple[Deck, str]:
    """Apply a partial update to the deck row itself.

    Returns:
        The deck and the audit batch id.
    """
    deck = get_deck(db, deck_id)
    before = audit.snapshot(deck)
    for key, value in changes.items():
        setattr(deck, key, value)
    deck.updated_at = utcnow()
    db.flush()
    batch = audit.new_batch_id()
    audit.record(
        db,
        action="update",
        entity_type="deck",
        entity_id=deck.id,
        batch_id=batch,
        before=before,
        after=audit.snapshot(deck),
    )
    return deck, batch


def delete_deck(db: DbSession, deck_id: int) -> str:
    """Delete a deck and its card rows.

    Raises:
        Conflict: The deck is built; release its copies first so the deletion
            cannot silently strand allocations.

    Returns:
        The audit batch id.
    """
    deck = get_deck(db, deck_id)
    if deck.is_built:
        raise Conflict("The deck is built; unbuild it before deleting")
    cards = list(db.scalars(select(DeckCard).where(DeckCard.deck_id == deck.id)))
    batch = audit.new_batch_id()
    if cards:
        audit.record(
            db,
            action=BULK_DELETE,
            entity_type="deck_card",
            entity_id=deck.id,
            batch_id=batch,
            before={
                "rows": [audit.snapshot(card) for card in cards],
                "summary": {"deck_id": deck.id, "count": len(cards)},
            },
        )
    audit.record(
        db,
        action="delete",
        entity_type="deck",
        entity_id=deck.id,
        batch_id=batch,
        before=audit.snapshot(deck),
        note=f"deleted deck {deck.name}",
    )
    for card in cards:
        db.delete(card)
    # There is no ORM relationship between Deck and DeckCard, so the unit of work
    # would not order these deletes; flushing the card rows first stops the deck's
    # ON DELETE CASCADE getting to them before their own DELETE statements do.
    db.flush()
    db.delete(deck)
    db.flush()
    return batch


@dataclass
class CardSpec:
    """One deck-card upsert."""

    oracle_id: str
    board: str = "main"
    quantity: int = 1
    preferred_set_code: str | None = None
    preferred_collector_number: str | None = None
    category: str | None = None
    is_proxy_intent: bool = False


def set_card(
    db: DbSession,
    deck_id: int,
    spec: CardSpec,
    *,
    batch_id: str | None = None,
) -> tuple[DeckCard, str]:
    """Add a card to a board, or update the row already there.

    Raises:
        AppError: Unknown board, unknown oracle id, or a non-positive quantity.

    Returns:
        The row and the audit batch id.
    """
    if spec.board not in BOARDS:
        raise AppError(f"Unknown board {spec.board!r}", code="unknown_board")
    if spec.quantity < 1:
        raise AppError("Quantity must be at least 1", code="bad_quantity")
    deck = get_deck(db, deck_id)
    _require_oracle(db, spec.oracle_id)
    # Set codes are stored lowercase (Scryfall import); a preference arriving as
    # "NEO" would otherwise never match and be silently ignored at build time.
    if spec.preferred_set_code:
        spec.preferred_set_code = spec.preferred_set_code.lower()
    batch = batch_id or audit.new_batch_id()

    row = db.get(DeckCard, (deck_id, spec.oracle_id, spec.board))
    if row is None:
        row = DeckCard(
            deck_id=deck_id,
            oracle_id=spec.oracle_id,
            board=spec.board,
            quantity=spec.quantity,
            preferred_set_code=spec.preferred_set_code,
            preferred_collector_number=spec.preferred_collector_number,
            category=spec.category,
            is_proxy_intent=spec.is_proxy_intent,
        )
        db.add(row)
        db.flush()
        audit.record(
            db,
            action=BULK_CREATE,
            entity_type="deck_card",
            entity_id=deck_id,
            batch_id=batch,
            after={"rows": [audit.snapshot(row)], "summary": {"deck_id": deck_id, "count": 1}},
        )
    else:
        before = audit.snapshot(row)
        row.quantity = spec.quantity
        row.preferred_set_code = spec.preferred_set_code
        row.preferred_collector_number = spec.preferred_collector_number
        row.category = spec.category
        row.is_proxy_intent = spec.is_proxy_intent
        db.flush()
        audit.record(
            db,
            action=BULK_DELETE,
            entity_type="deck_card",
            entity_id=deck_id,
            batch_id=batch,
            before={"rows": [before], "summary": {"deck_id": deck_id, "count": 1}},
            note="replaced by the row in the paired bulk_create entry",
        )
        audit.record(
            db,
            action=BULK_CREATE,
            entity_type="deck_card",
            entity_id=deck_id,
            batch_id=batch,
            after={"rows": [audit.snapshot(row)], "summary": {"deck_id": deck_id, "count": 1}},
        )
    refresh_caches(db, deck)
    return row, batch


def remove_card(
    db: DbSession,
    deck_id: int,
    oracle_id: str,
    board: str,
    *,
    batch_id: str | None = None,
) -> str:
    """Remove one card row from a deck.

    Raises:
        NotFound: The deck has no such row.

    Returns:
        The audit batch id.
    """
    deck = get_deck(db, deck_id)
    row = db.get(DeckCard, (deck_id, oracle_id, board))
    if row is None:
        raise NotFound(f"Deck {deck_id} has no {oracle_id} in {board}")
    batch = batch_id or audit.new_batch_id()
    audit.record(
        db,
        action=BULK_DELETE,
        entity_type="deck_card",
        entity_id=deck_id,
        batch_id=batch,
        before={"rows": [audit.snapshot(row)], "summary": {"deck_id": deck_id, "count": 1}},
    )
    db.delete(row)
    db.flush()
    refresh_caches(db, deck)
    return batch


def refresh_caches(db: DbSession, deck: Deck) -> None:
    """Recompute ``colors_cached`` and the commander/companion cache columns.

    The board rows are the source of truth; these columns exist so deck lists
    render without joining ``deck_cards`` (see :class:`app.models.decks.Deck`).
    """
    rows = list(
        db.execute(
            select(DeckCard.board, DeckCard.oracle_id, OracleCard.color_identity)
            .join(OracleCard, OracleCard.oracle_id == DeckCard.oracle_id)
            .where(DeckCard.deck_id == deck.id)
        )
    )
    letters = {
        letter
        for board, _oracle_id, identity in rows
        if board in ("main", "commander")
        for letter in (identity or "")
    }
    deck.colors_cached = "".join(letter for letter in _WUBRG if letter in letters)

    commanders = [oracle_id for board, oracle_id, _identity in rows if board == "commander"]
    deck.commander_oracle_id = commanders[0] if commanders else None
    deck.partner_oracle_id = commanders[1] if len(commanders) > 1 else None
    companions = [oracle_id for board, oracle_id, _identity in rows if board == "companion"]
    deck.companion_oracle_id = companions[0] if companions else None
    deck.updated_at = utcnow()
    db.flush()


def allocation_count(db: DbSession, deck_id: int) -> int:
    """How many physical copies are sleeved into the deck."""
    return len(list(db.scalars(select(DeckAllocation.id).where(DeckAllocation.deck_id == deck_id))))


def preferred_printing(db: DbSession, row: DeckCard) -> Card | None:
    """The printing a deck row asks for, or any printing of the oracle as fallback."""
    if row.preferred_set_code and row.preferred_collector_number:
        exact = db.scalars(
            select(Card).where(
                Card.set_code == row.preferred_set_code,
                Card.collector_number == row.preferred_collector_number,
                Card.oracle_id == row.oracle_id,
            )
        ).first()
        if exact is not None:
            return exact
    return db.scalars(
        select(Card)
        .where(Card.oracle_id == row.oracle_id, Card.digital.is_(False))
        .order_by(Card.released_at.desc())
    ).first()


def _require_oracle(db: DbSession, oracle_id: str) -> OracleCard:
    oracle = db.get(OracleCard, oracle_id)
    if oracle is None:
        raise NotFound(f"No card with oracle id {oracle_id}")
    return oracle
