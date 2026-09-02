"""Load a deck into the plain shapes the rules engine and stats read.

This is the seam that keeps :mod:`app.services.rules` pure: everything database
touches this module, nothing database crosses it.
"""

from __future__ import annotations

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Deck, DeckCard, Legality, OracleCard
from app.services.rules import DeckEntry, RulesCard


def rules_card(oracle: OracleCard) -> RulesCard:
    """Project one oracle row onto the rules engine's snapshot."""
    keywords = frozenset(str(keyword) for keyword in (oracle.keywords_json or []))
    return RulesCard(
        oracle_id=oracle.oracle_id,
        name=oracle.name,
        type_line=oracle.type_line or "",
        oracle_text=oracle.oracle_text_all or "",
        mana_cost=oracle.mana_cost or "",
        cmc=oracle.cmc,
        color_identity_mask=oracle.color_identity_mask,
        is_legendary=oracle.is_legendary,
        is_creature=oracle.is_creature,
        is_land=oracle.is_land,
        game_changer=oracle.game_changer,
        keywords=keywords,
        layout=oracle.layout,
    )


def load_entries(db: DbSession, deck: Deck) -> list[DeckEntry]:
    """Every deck-card row joined to its oracle snapshot."""
    rows = db.execute(
        select(DeckCard, OracleCard)
        .join(OracleCard, OracleCard.oracle_id == DeckCard.oracle_id)
        .where(DeckCard.deck_id == deck.id)
    )
    return [
        DeckEntry(
            card=rules_card(oracle),
            quantity=row.quantity,
            board=row.board,
            is_proxy_intent=row.is_proxy_intent,
        )
        for row, oracle in rows
    ]


def legality_map(db: DbSession, format_key: str, oracle_ids: list[str]) -> dict[str, str]:
    """``oracle_id`` -> Scryfall legality status in this format.

    The ``casual`` formats are house rules: every card is legal, because the
    kitchen table has no banlist. Structural rules (deck size, copy limits,
    commander identity) still apply through the format profile.
    """
    if not oracle_ids:
        return {}
    if format_key.lower().startswith("casual"):
        return dict.fromkeys(oracle_ids, "legal")
    rows = db.execute(
        select(Legality.oracle_id, Legality.status).where(
            Legality.format == format_key.lower(), Legality.oracle_id.in_(oracle_ids)
        )
    )
    return dict(rows.tuples().all())
