"""Rating and strategy endpoints (ARCHITECTURE.md section 4.7).

Kept in their own module rather than swelling ``api/decks.py``: these routes share
the ``/decks/{id}`` prefix but a different set of services, failure modes (external
sources, the optional AI) and response shapes.
"""

from __future__ import annotations

from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import select

from app.config import get_settings
from app.deps import Db
from app.models import DeckCard, DeckValidation, LegalityChange, OracleCard
from app.services.decks import crud, loader
from app.services.rating import (
    ai_review,
    brackets,
    combos_service,
    edhrec_service,
    score_service,
)

router = APIRouter(prefix="/decks", tags=["rating"])


@router.get("/{deck_id}/score")
def score(deck_id: int, db: Db, refresh: bool = False) -> dict[str, Any]:
    """Heuristic sub-scores 1-10, with every raw count behind them."""
    deck = crud.get_deck(db, deck_id)
    if not refresh:
        stored = score_service.latest(db, deck)
        if stored is not None:
            return {
                "consistency": stored.consistency,
                "speed": stored.speed,
                "interaction": stored.interaction,
                "resilience": stored.resilience,
                "signals": (stored.signals_json or {}),
                "heuristic_version": stored.heuristic_version,
                "computed_at": stored.computed_at,
                "bracket": (stored.signals_json or {}).get("bracket"),
            }
    return score_service.compute_and_store(db, deck)


@router.get("/{deck_id}/bracket")
async def bracket(deck_id: int, db: Db) -> dict[str, Any]:
    """Commander Bracket 1-5, every signal citing the cards behind it."""
    deck = crud.get_deck(db, deck_id)
    combos = await combos_service.two_card_combos(db, get_settings(), deck)
    verdict = brackets.detect_bracket(loader.load_entries(db, deck), two_card_combos=combos)
    return verdict.as_dict()


@router.get("/{deck_id}/edhrec")
async def edhrec(deck_id: int, db: Db) -> dict[str, Any]:
    """EDHREC recommendations, each marked against the vault; stale beats absent."""
    deck = crud.get_deck(db, deck_id)
    return await edhrec_service.for_deck(db, get_settings(), deck)


@router.get("/{deck_id}/combos")
async def combos(deck_id: int, db: Db) -> dict[str, Any]:
    """Combos in the deck, and near-misses the vault could complete."""
    deck = crud.get_deck(db, deck_id)
    return await combos_service.for_deck(db, get_settings(), deck)


class AiReviewRequest(BaseModel):
    """Body of ``POST /api/decks/{id}/ai-review``."""

    goal: str | None = Field(default=None, max_length=2000)
    force_refresh: bool = False


@router.post("/{deck_id}/ai-review")
async def ai_review_endpoint(deck_id: int, body: AiReviewRequest, db: Db) -> dict[str, Any]:
    """The optional AI review; ``409 ai_disabled`` without an API key."""
    deck = crud.get_deck(db, deck_id)
    settings = get_settings()
    # The disabled check comes before any external work: a keyless install must
    # answer 409 without ever contacting Spellbook.
    ai_review.ensure_enabled(db, settings)
    combos = await combos_service.two_card_combos(db, settings, deck)
    return await ai_review.review_deck(
        db,
        settings,
        deck,
        goal=body.goal,
        force_refresh=body.force_refresh,
        two_card_combos=combos,
    )


@router.get("/{deck_id}/banlist-flags")
def banlist_flags(deck_id: int, db: Db) -> dict[str, Any]:
    """Legality changes that touch this deck, and its latest banlist re-check."""
    deck = crud.get_deck(db, deck_id)
    deck_oracles = select(DeckCard.oracle_id).where(DeckCard.deck_id == deck.id)
    changes = db.execute(
        select(LegalityChange, OracleCard.name)
        .join(OracleCard, OracleCard.oracle_id == LegalityChange.oracle_id, isouter=True)
        .where(
            LegalityChange.format == deck.format,
            LegalityChange.oracle_id.in_(deck_oracles),
        )
        .order_by(LegalityChange.detected_at.desc())
        .limit(50)
    ).all()
    latest_flagged = db.scalars(
        select(DeckValidation)
        .where(DeckValidation.deck_id == deck.id, DeckValidation.banlist_flag.is_(True))
        .order_by(DeckValidation.id.desc())
        .limit(1)
    ).first()
    return {
        "changes": [
            {
                "card": name or change.oracle_id,
                "format": change.format,
                "old_status": change.old_status,
                "new_status": change.new_status,
                "detected_at": change.detected_at,
            }
            for change, name in changes
        ],
        "last_check": {
            "checked_at": latest_flagged.checked_at,
            "is_legal": latest_flagged.is_legal,
        }
        if latest_flagged
        else None,
    }
