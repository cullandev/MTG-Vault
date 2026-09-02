"""Companion deck-building restrictions (CR 702.139).

Each companion imposes a condition on the *starting deck* -- here, the main board
plus any commanders, which is what starts the game outside the companion's own zone.
Restrictions are keyed by card name: there are exactly ten companions, Wizards has
said the mechanic will not return, and the alternative -- parsing restriction text --
is strictly worse than ten named predicates with the rules text cited beside them.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable

from app.services.rules.cards import (
    RulesCard,
    card_types,
    has_activated_ability,
    has_repeated_mana_symbol,
    is_permanent_card,
    subtypes,
)

#: Kaheera: "each creature card in your starting deck has one of these types".
_KAHEERA_TYPES = frozenset({"Cat", "Elemental", "Nightmare", "Dinosaur", "Beast"})


def _lurrus(cards: Iterable[RulesCard]) -> list[RulesCard]:
    """Each permanent card has mana value 2 or less."""
    return [c for c in cards if is_permanent_card(c) and c.cmc > 2]


def _jegantha(cards: Iterable[RulesCard]) -> list[RulesCard]:
    """No card has two of the same mana symbol in its mana cost."""
    return [c for c in cards if has_repeated_mana_symbol(c)]


def _obosh(cards: Iterable[RulesCard]) -> list[RulesCard]:
    """Every nonland card has an odd mana value."""
    return [c for c in cards if not c.is_land and int(c.cmc) % 2 == 0]


def _gyruda(cards: Iterable[RulesCard]) -> list[RulesCard]:
    """Every nonland card has an even mana value."""
    return [c for c in cards if not c.is_land and int(c.cmc) % 2 == 1]


def _keruga(cards: Iterable[RulesCard]) -> list[RulesCard]:
    """Every nonland card has mana value 3 or greater."""
    return [c for c in cards if not c.is_land and c.cmc < 3]


def _kaheera(cards: Iterable[RulesCard]) -> list[RulesCard]:
    """Every creature card is a Cat, Elemental, Nightmare, Dinosaur or Beast.

    A changeling is every creature type in every zone (CR 702.73a), so it
    satisfies Kaheera despite its printed subtypes.
    """
    return [
        c
        for c in cards
        if "Creature" in card_types(c.type_line)
        and "Changeling" not in c.keywords
        and not (subtypes(c.type_line) & _KAHEERA_TYPES)
    ]


def _zirda(cards: Iterable[RulesCard]) -> list[RulesCard]:
    """Every permanent card has an activated ability."""
    return [c for c in cards if is_permanent_card(c) and not has_activated_ability(c)]


def _umori(cards: Iterable[RulesCard]) -> list[RulesCard]:
    """All nonland cards share a card type; offenders are those outside the largest share."""
    nonland = [c for c in cards if not c.is_land]
    if not nonland:
        return []
    shared = frozenset.intersection(*(card_types(c.type_line) for c in nonland))
    if shared:
        return []
    # Report the cards outside the most common type, so the message names the problem.
    counts: dict[str, int] = {}
    for card in nonland:
        for card_type in card_types(card.type_line):
            counts[card_type] = counts.get(card_type, 0) + 1
    majority = max(counts, key=lambda t: counts[t])
    return [c for c in nonland if majority not in card_types(c.type_line)]


COMPANION_CHECKS: dict[str, Callable[[Iterable[RulesCard]], list[RulesCard]]] = {
    "Lurrus of the Dream-Den": _lurrus,
    "Jegantha, the Wellspring": _jegantha,
    "Obosh, the Preypiercer": _obosh,
    "Gyruda, Doom of Depths": _gyruda,
    "Keruga, the Macrosage": _keruga,
    "Kaheera, the Orphanguard": _kaheera,
    "Zirda, the Dawnwaker": _zirda,
    "Umori, the Collector": _umori,
}
"""Companions whose restriction is a per-card predicate over the starting deck.

Yorion (deck size +20) and Lutri (singleton) restrict the deck's *shape*, not its
cards, and are handled inside :mod:`app.services.rules.validate` where the sizes and
copy counts are already on hand.
"""

YORION = "Yorion, Sky Nomad"
LUTRI = "Lutri, the Spellchaser"

COMPANION_NAMES = frozenset(COMPANION_CHECKS) | {YORION, LUTRI}


def is_companion(card: RulesCard) -> bool:
    """Whether the card is one of the ten companions."""
    return "Companion" in card.keywords or card.name in COMPANION_NAMES
