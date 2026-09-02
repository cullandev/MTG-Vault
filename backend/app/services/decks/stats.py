"""Deck statistics: curve, pips, types, and the land recommendation.

Pure functions over :class:`app.services.rules.DeckEntry`, for the same reason the
rules engine is pure: the edge cases (X spells, MDFC lands, hybrid pips) are exactly
the ones worth testing without a database in the way.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any

from app.services.rules import DeckEntry
from app.services.rules.cards import card_types

_SYMBOL = re.compile(r"\{([^}]+)\}")

#: Buckets of the mana curve; everything at seven or more shares the last one.
_CURVE_TOP = 7

#: Display order for the type breakdown; a card counts once, under its first match.
_TYPE_ORDER = (
    "Creature",
    "Planeswalker",
    "Instant",
    "Sorcery",
    "Artifact",
    "Enchantment",
    "Battle",
    "Land",
)


@dataclass
class DeckStats:
    """Everything the deck page's stats panel shows."""

    card_count: int
    curve: dict[str, int]
    x_spells: int
    pips: dict[str, int]
    types: dict[str, int]
    avg_mv: float
    lands: int
    mdfc_lands: int
    recommended_lands: int

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "card_count": self.card_count,
            "curve": self.curve,
            "x_spells": self.x_spells,
            "pips": self.pips,
            "types": self.types,
            "avg_mv": round(self.avg_mv, 2),
            "lands": self.lands,
            "mdfc_lands": self.mdfc_lands,
            "recommended_lands": self.recommended_lands,
        }


def is_pure_land(type_line: str) -> bool:
    """Whether every face of the card is a land -- a card that is only ever a land."""
    faces = type_line.split("//")
    return all("Land" in card_types(face) for face in faces) if type_line else False


def is_mdfc_land(type_line: str) -> bool:
    """Whether the card has a land face and a nonland face (Agadeem's Awakening).

    These count in the curve -- they are spells -- *and* are surfaced separately,
    because they change how many true lands a deck wants.
    """
    faces = type_line.split("//")
    if len(faces) < 2:
        return False
    lands = [face for face in faces if "Land" in card_types(face)]
    return bool(lands) and len(lands) < len(faces)


def compute_stats(entries: list[DeckEntry]) -> DeckStats:
    """Compute the stats panel over the main board plus commanders.

    The curve buckets nonland cards by mana value with X counting as zero (its
    printed mana value, CR 203.3b) and X spells counted separately; hybrid pips
    count as both colours; average mana value excludes lands.
    """
    counted = [entry for entry in entries if entry.board in ("main", "commander")]

    curve = {**{str(bucket): 0 for bucket in range(_CURVE_TOP)}, f"{_CURVE_TOP}+": 0}
    pips = dict.fromkeys("WUBRGC", 0)
    types = dict.fromkeys(_TYPE_ORDER, 0)
    x_spells = 0
    lands = 0
    mdfc_lands = 0
    nonland_count = 0
    nonland_mv = 0.0

    for entry in counted:
        card = entry.card
        quantity = entry.quantity
        pure_land = is_pure_land(card.type_line)

        for type_name in _TYPE_ORDER:
            if type_name == "Land" and pure_land:
                types["Land"] += quantity
                break
            if type_name != "Land" and type_name in card_types(card.type_line):
                types[type_name] += quantity
                break

        if pure_land:
            lands += quantity
            continue
        if is_mdfc_land(card.type_line):
            mdfc_lands += quantity

        nonland_count += quantity
        nonland_mv += card.cmc * quantity
        bucket = min(int(card.cmc), _CURVE_TOP)
        curve[str(bucket) if bucket < _CURVE_TOP else f"{_CURVE_TOP}+"] += quantity
        if "X" in {symbol.upper() for symbol in _SYMBOL.findall(card.mana_cost)}:
            x_spells += quantity
        for symbol in _SYMBOL.findall(card.mana_cost):
            for part in symbol.split("/"):
                if part in pips:
                    pips[part] += quantity

    card_count = sum(entry.quantity for entry in counted)
    avg_mv = nonland_mv / nonland_count if nonland_count else 0.0
    return DeckStats(
        card_count=card_count,
        curve=curve,
        x_spells=x_spells,
        pips=pips,
        types={name: count for name, count in types.items() if count},
        avg_mv=avg_mv,
        lands=lands,
        mdfc_lands=mdfc_lands,
        recommended_lands=recommend_lands(
            deck_size=card_count, avg_mv=avg_mv, mdfc_lands=mdfc_lands
        ),
    )


def recommend_lands(*, deck_size: int, avg_mv: float, mdfc_lands: int) -> int:
    """How many lands a deck of this size and curve wants.

    Frank Karsten's regression for 60 cards -- 19.59 lands plus 1.90 per point of
    average mana value ("How Many Lands Do You Need", 2017) -- scaled linearly to
    the deck's size, with an MDFC land counting as most of a land (0.9, matching
    Karsten's treatment of them as slightly worse lands).
    """
    if deck_size == 0:
        return 0
    baseline = (19.59 + 1.90 * avg_mv) * deck_size / 60
    recommended = round(baseline - 0.9 * mdfc_lands)
    return max(0, min(deck_size, recommended))
