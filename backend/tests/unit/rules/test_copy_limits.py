"""Copy limits: the 4-of rule and every exemption named in TEST-PLAN.md section 1."""

from __future__ import annotations

import pytest

from app.services.rules import validate_deck
from tests.unit.rules.conftest import all_legal, card, entry


def _pad_to(count: int, entries: list) -> list:
    """Fill a deck to ``count`` cards with basic lands so size never interferes."""
    padding = count - sum(e.quantity for e in entries)
    filler = card("Wastes", type_line="Basic Land")
    return [*entries, entry(filler, padding)]


def _errors(entries: list, format_key: str = "modern") -> list[str]:
    result = validate_deck(entries, format_key=format_key, legality=all_legal(entries))
    return [error.code for error in result.errors]


def test_a_fifth_copy_breaks_the_limit() -> None:
    """CR 100.2a: four copies of a named card, no more."""
    bolt = card("Lightning Bolt", type_line="Instant", mana_cost="{R}", cmc=1)
    assert "copy_limit" in _errors(_pad_to(60, [entry(bolt, 5)]))
    assert _errors(_pad_to(60, [entry(bolt, 4)])) == []


def test_basic_lands_are_exempt(island_count: int = 24) -> None:
    """CR 100.2b / 903.5b: any number of basic lands, in any format."""
    island = card("Island", type_line="Basic Land — Island", identity="U")
    assert _errors(_pad_to(60, [entry(island, island_count)])) == []


def test_snow_basics_are_still_basic() -> None:
    """Snow-Covered Island's type line is "Basic Snow Land — Island"."""
    snow = card("Snow-Covered Island", type_line="Basic Snow Land — Island", identity="U")
    assert _errors(_pad_to(60, [entry(snow, 20)])) == []


@pytest.mark.parametrize(
    "name",
    [
        "Relentless Rats",
        "Rat Colony",
        "Persistent Petitioners",
        "Shadowborn Apostle",
        "Dragon's Approach",
        "Slime Against Humanity",
        "Templar Knight",
    ],
)
def test_any_number_exemption_comes_from_the_card_text(name: str) -> None:
    """Each card's own text -- "any number of cards named X" -- lifts the limit."""
    rats = card(
        name,
        type_line="Creature — Rat",
        oracle_text=f"A deck can have any number of cards named {name}.",
        cmc=2,
    )
    assert _errors(_pad_to(60, [entry(rats, 30)])) == []


def test_seven_dwarves_stops_at_seven() -> None:
    """Seven Dwarves: "A deck can have up to seven cards named Seven Dwarves"."""
    dwarves = card(
        "Seven Dwarves",
        type_line="Creature — Dwarf",
        oracle_text="A deck can have up to seven cards named Seven Dwarves.",
        cmc=2,
    )
    assert _errors(_pad_to(60, [entry(dwarves, 7)])) == []
    assert "copy_limit" in _errors(_pad_to(60, [entry(dwarves, 8)]))


def test_nazgul_stops_at_nine() -> None:
    """Nazgûl: "A deck can have up to nine cards named Nazgûl"."""
    nazgul = card(
        "Nazgûl",
        type_line="Creature — Wraith Knight",
        oracle_text="A deck can have up to nine cards named Nazgûl.",
        cmc=3,
    )
    assert _errors(_pad_to(60, [entry(nazgul, 9)])) == []
    assert "copy_limit" in _errors(_pad_to(60, [entry(nazgul, 10)]))


def test_vintage_restricted_means_exactly_one() -> None:
    """The restricted list allows one copy across main and sideboard."""
    vision = card("Ancestral Recall", type_line="Instant", mana_cost="{U}", cmc=1)
    deck = _pad_to(60, [entry(vision, 1), entry(card("Filler"), 1)])
    legality = {**all_legal(deck), "Ancestral Recall": "restricted"}

    one = validate_deck(deck, format_key="vintage", legality=legality)
    assert [error.code for error in one.errors] == []

    two_copies = _pad_to(60, [entry(vision, 2)])
    legality = {**all_legal(two_copies), "Ancestral Recall": "restricted"}
    result = validate_deck(two_copies, format_key="vintage", legality=legality)
    assert [error.code for error in result.errors] == ["restricted_limit"]


def test_restricted_counts_the_sideboard() -> None:
    """One in the main and one in the side is still two copies."""
    vision = card("Ancestral Recall", type_line="Instant", mana_cost="{U}", cmc=1)
    deck = [*_pad_to(60, [entry(vision, 1)]), entry(vision, 1, board="side")]
    legality = {**all_legal(deck), "Ancestral Recall": "restricted"}
    result = validate_deck(deck, format_key="vintage", legality=legality)
    assert [error.code for error in result.errors] == ["restricted_limit"]


def test_commander_is_singleton() -> None:
    """CR 903.5b: one copy of anything that is not a basic land."""
    ring = card("Sol Ring", type_line="Artifact", mana_cost="{1}", cmc=1)
    bruna = card(
        "Bruna, the Fading Light",
        type_line="Legendary Creature — Angel Horror",
        cmc=7,
        identity="W",
    )
    plains = card("Plains", type_line="Basic Land — Plains", identity="W")
    deck = [
        entry(bruna, 1, board="commander"),
        entry(ring, 2),
        entry(plains, 97),
    ]
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    assert [error.code for error in result.errors] == ["copy_limit"]
    assert result.errors[0].oracle_ids == ("Sol Ring",)


def test_the_commander_counts_toward_singleton() -> None:
    """The same card as commander and in the 99 is two copies of it."""
    bruna = card(
        "Bruna, the Fading Light",
        type_line="Legendary Creature — Angel Horror",
        cmc=7,
        identity="W",
    )
    plains = card("Plains", type_line="Basic Land — Plains", identity="W")
    deck = [
        entry(bruna, 1, board="commander"),
        entry(bruna, 1),
        entry(plains, 98),
    ]
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    assert "copy_limit" in [error.code for error in result.errors]
