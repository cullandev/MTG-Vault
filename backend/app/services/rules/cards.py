"""Card-level facts the rules read: types, copy limits, mana symbols.

Everything here operates on Scryfall-imported oracle data (ADR-010): colour identity
is *read*, never derived, and copy-limit exceptions are parsed from the card's own
rules text rather than maintained as a hand-curated list that rots.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field

#: Card types that make a card a permanent (CR 300.1 less instant/sorcery).
PERMANENT_TYPES = frozenset(
    {"Artifact", "Battle", "Creature", "Enchantment", "Land", "Planeswalker"}
)

#: All card types, for Umori's "share a card type" (CR 205.2a; Tribal is pre-errata Kindred).
CARD_TYPES = frozenset(
    PERMANENT_TYPES | {"Instant", "Sorcery", "Kindred", "Tribal", "Conspiracy", "Scheme"}
)

#: Keyword abilities that are themselves activated abilities (CR 702), so a card can
#: satisfy Zirda without a colon appearing in its oracle text.
ACTIVATED_KEYWORDS = frozenset(
    {
        "Cycling",
        "Equip",
        "Crew",
        "Unearth",
        "Embalm",
        "Eternalize",
        "Fortify",
        "Reconfigure",
        "Transmute",
        "Transfigure",
        "Level up",
        "Outlast",
        "Ninjutsu",
    }
)

_REMINDER = re.compile(r"\([^)]*\)")
_ANY_NUMBER = re.compile(r"any number of cards named", re.IGNORECASE)
_UP_TO_N = re.compile(r"up to (?P<count>[a-z]+) cards named", re.IGNORECASE)
_WORD_NUMBERS = {
    "one": 1,
    "two": 2,
    "three": 3,
    "four": 4,
    "five": 5,
    "six": 6,
    "seven": 7,
    "eight": 8,
    "nine": 9,
    "ten": 10,
}
_MANA_SYMBOL = re.compile(r"\{([^}]+)\}")


@dataclass(frozen=True)
class RulesCard:
    """The oracle facts the rules engine needs about one card."""

    oracle_id: str
    name: str
    type_line: str = ""
    oracle_text: str = ""
    mana_cost: str = ""
    cmc: float = 0.0
    color_identity_mask: int = 0
    is_legendary: bool = False
    is_creature: bool = False
    is_land: bool = False
    game_changer: bool = False
    """Scryfall's Commander Bracket "Game Changer" flag, carried through verbatim."""
    keywords: frozenset[str] = frozenset()
    layout: str = "normal"


@dataclass(frozen=True)
class DeckEntry:
    """One deck row: a card, how many, and which board it sits in."""

    card: RulesCard
    quantity: int
    board: str = "main"
    is_proxy_intent: bool = False
    categories: tuple[str, ...] = field(default=())


def strip_reminder_text(text: str) -> str:
    """Remove parenthesised reminder text, which never carries rules weight."""
    return _REMINDER.sub("", text)


def supertypes_and_types(type_line: str) -> frozenset[str]:
    """The words left of the em-dash: supertypes and card types."""
    left = type_line.split("—")[0]
    return frozenset(left.replace("//", " ").split())


def card_types(type_line: str) -> frozenset[str]:
    """The card types of a (possibly multi-faced) type line, without supertypes."""
    return supertypes_and_types(type_line) & CARD_TYPES


def subtypes(type_line: str) -> frozenset[str]:
    """The words right of the em-dash, across every face."""
    found: set[str] = set()
    for face in type_line.split("//"):
        parts = face.split("—")
        if len(parts) > 1:
            found.update(parts[1].split())
    return frozenset(found)


def is_basic_land(card: RulesCard) -> bool:
    """Whether the card is a basic land, exempt from copy limits (CR 100.2b, 903.5b)."""
    return "Basic" in supertypes_and_types(card.type_line)


def is_permanent_card(card: RulesCard) -> bool:
    """Whether the card enters the battlefield as a permanent (CR 300.1)."""
    return bool(card_types(card.type_line) & PERMANENT_TYPES)


def copy_limit_override(card: RulesCard) -> int | None:
    """The copy limit the card's own text grants, or ``None`` for no exception.

    Parsed from oracle text rather than hard-coded: "any number of cards named"
    (Relentless Rats, CR 100.2b exception) returns a very large limit, and
    "up to seven/nine cards named" (Seven Dwarves, Nazgul) returns that number.
    """
    text = strip_reminder_text(card.oracle_text)
    if _ANY_NUMBER.search(text):
        return 10_000
    match = _UP_TO_N.search(text)
    if match:
        return _WORD_NUMBERS.get(match.group("count").lower())
    return None


def mana_cost_symbols(mana_cost: str) -> list[list[str]]:
    """Per-face lists of mana symbols, ``"{1}{W/U}{W/U}"`` -> ``[["1", "W/U", "W/U"]]``."""
    return [_MANA_SYMBOL.findall(face) for face in mana_cost.split("//")]


def has_repeated_mana_symbol(card: RulesCard) -> bool:
    """Whether any face's mana cost holds the same symbol twice (Jegantha's test)."""
    return any(len(face) != len(set(face)) for face in mana_cost_symbols(card.mana_cost))


def has_activated_ability(card: RulesCard) -> bool:
    """Whether the card has an activated ability (Zirda's test).

    Approximation, stated: a colon outside reminder text, an activated keyword
    ability, or being a basic land (whose mana ability is intrinsic, CR 305.6).
    """
    if is_basic_land(card):
        return True
    if card.keywords & ACTIVATED_KEYWORDS:
        return True
    return ":" in strip_reminder_text(card.oracle_text)


def can_be_commander(card: RulesCard) -> bool:
    """Whether the card can lead a Commander deck on its own (CR 903.3)."""
    if card.is_legendary and card.is_creature:
        return True
    return "can be your commander" in card.oracle_text.lower()


def is_background(card: RulesCard) -> bool:
    """Whether the card is a Background enchantment (CR 903.3d)."""
    return "Background" in subtypes(card.type_line)
