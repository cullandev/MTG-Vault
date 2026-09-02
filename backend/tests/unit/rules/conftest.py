"""Builders for rules-engine tests.

The engine is pure, so tests hand it :class:`RulesCard` snapshots that quote the
real cards' oracle data -- type lines, mana costs and rules text are copied from
Scryfall, and each test cites the rule it asserts (TEST-PLAN.md section 1).
"""

from __future__ import annotations

import pytest

from app.models.cards import color_mask
from app.services.rules import DeckEntry, RulesCard


def card(
    name: str,
    *,
    type_line: str = "Creature — Human",
    oracle_text: str = "",
    mana_cost: str = "",
    cmc: float = 0.0,
    identity: str = "",
    keywords: frozenset[str] | set[str] = frozenset(),
    layout: str = "normal",
    game_changer: bool = False,
) -> RulesCard:
    """A rules snapshot with the card's name doubling as its oracle id."""
    return RulesCard(
        oracle_id=name,
        name=name,
        type_line=type_line,
        oracle_text=oracle_text,
        mana_cost=mana_cost,
        cmc=cmc,
        color_identity_mask=color_mask(identity),
        is_legendary="Legendary" in type_line,
        is_creature="Creature" in type_line,
        is_land="Land" in type_line,
        game_changer=game_changer,
        keywords=frozenset(keywords),
        layout=layout,
    )


def entry(
    rules_card: RulesCard,
    quantity: int = 1,
    board: str = "main",
    *,
    is_proxy_intent: bool = False,
) -> DeckEntry:
    """One deck row."""
    return DeckEntry(
        card=rules_card, quantity=quantity, board=board, is_proxy_intent=is_proxy_intent
    )


def all_legal(entries: list[DeckEntry]) -> dict[str, str]:
    """A legality map declaring every entry legal."""
    return {deck_entry.card.oracle_id: "legal" for deck_entry in entries}


@pytest.fixture
def forest() -> RulesCard:
    """A basic land."""
    return card("Forest", type_line="Basic Land — Forest", identity="G")


@pytest.fixture
def island() -> RulesCard:
    """A basic land."""
    return card("Island", type_line="Basic Land — Island", identity="U")
