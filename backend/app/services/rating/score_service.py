"""Compute, persist and serve a deck's heuristic score and bracket."""

from __future__ import annotations

from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session as DbSession

from app.models import Deck, DeckScore
from app.services.decks import loader
from app.services.rating.brackets import detect_bracket
from app.services.rating.heuristics import HEURISTIC_VERSION, score_deck


def compute_and_store(
    db: DbSession,
    deck: Deck,
    *,
    two_card_combos: list[str] | None = None,
) -> dict[str, Any]:
    """Score the deck now and append a ``deck_scores`` row.

    Args:
        db: Open database session.
        deck: The deck to score.
        two_card_combos: Known two-card combos from Spellbook, or ``None`` when
            the source is unavailable (recorded as unchecked, not as zero).
    """
    entries = loader.load_entries(db, deck)
    scores = score_deck(entries)
    verdict = detect_bracket(entries, two_card_combos=two_card_combos)
    db.add(
        DeckScore(
            deck_id=deck.id,
            consistency=scores.consistency,
            speed=scores.speed,
            interaction=scores.interaction,
            resilience=scores.resilience,
            bracket=verdict.bracket,
            signals_json={**scores.signals, "bracket": verdict.as_dict()},
            heuristic_version=HEURISTIC_VERSION,
        )
    )
    db.flush()
    return {**scores.as_dict(), "bracket": verdict.as_dict()}


def latest(db: DbSession, deck: Deck) -> DeckScore | None:
    """The most recent stored score at the current formula version, if any."""
    return db.scalars(
        select(DeckScore)
        .where(
            DeckScore.deck_id == deck.id,
            DeckScore.heuristic_version == HEURISTIC_VERSION,
        )
        .order_by(desc(DeckScore.id))
        .limit(1)
    ).first()
