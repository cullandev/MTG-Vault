"""Run the rules engine over a stored deck and record the verdict."""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from app.models import Deck, DeckValidation
from app.services.decks import loader
from app.services.rules import ValidationResult, validate_deck


def validate_and_record(
    db: DbSession,
    deck: Deck,
    *,
    triggered_by: str = "edit",
    banlist_flag: bool = False,
) -> ValidationResult:
    """Validate the deck and append a ``deck_validations`` row.

    Args:
        db: Open database session.
        deck: The deck to check.
        triggered_by: ``edit`` for user-driven checks, ``legality_change`` when the
            weekly refresh re-checks affected decks.
        banlist_flag: Whether a legality change provoked this check.

    Returns:
        The rules engine's verdict.
    """
    entries = loader.load_entries(db, deck)
    legality = loader.legality_map(db, deck.format, [entry.card.oracle_id for entry in entries])
    result = validate_deck(entries, format_key=deck.format, legality=legality)
    db.add(
        DeckValidation(
            deck_id=deck.id,
            is_legal=result.is_legal,
            errors_json=result.as_dict(),
            banlist_flag=banlist_flag,
            triggered_by=triggered_by,
        )
    )
    db.flush()
    return result
