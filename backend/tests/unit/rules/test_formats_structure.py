"""Deck shape and per-card legality: sizes, sideboards, banlists, and the field rule.

Format legality is read from the imported ``legalities`` status and never inferred
from set membership or rarity (TEST-PLAN.md section 1, last block).
"""

from __future__ import annotations

from app.services.rules import validate_deck
from tests.unit.rules.conftest import all_legal, card, entry


def _deck_of(count: int) -> list:
    return [entry(card("Wastes", type_line="Basic Land"), count)]


def test_sixty_card_minimum() -> None:
    """CR 100.2a: constructed decks are at least 60 cards."""
    short = _deck_of(59)
    result = validate_deck(short, format_key="modern", legality=all_legal(short))
    assert [error.code for error in result.errors] == ["deck_size"]
    full = _deck_of(60)
    assert validate_deck(full, format_key="modern", legality=all_legal(full)).is_legal


def test_sideboard_maximum_is_fifteen() -> None:
    """CR 100.4a: up to fifteen sideboard cards."""
    names = ["Duress", "Damping Sphere", "Rest in Peace", "Pithing Needle"]
    side_16 = [entry(card(name, type_line="Sorcery", cmc=1), 4, board="side") for name in names]
    deck = _deck_of(60) + side_16
    result = validate_deck(deck, format_key="modern", legality=all_legal(deck))
    assert "sideboard_size" in [error.code for error in result.errors]

    side_15 = [*side_16[:3], entry(card("Pithing Needle", type_line="Artifact"), 3, board="side")]
    deck = _deck_of(60) + side_15
    assert validate_deck(deck, format_key="modern", legality=all_legal(deck)).is_legal


def test_commander_is_exactly_one_hundred_including_commanders() -> None:
    """CR 903.5a: exactly 100 cards, commander included."""
    bruna = card(
        "Bruna, the Fading Light", type_line="Legendary Creature — Angel Horror", identity="W"
    )
    ninety_nine = entry(card("Plains", type_line="Basic Land — Plains", identity="W"), 99)
    deck = [entry(bruna, 1, board="commander"), ninety_nine]
    assert validate_deck(deck, format_key="commander", legality=all_legal(deck)).is_legal

    hundred = entry(card("Plains", type_line="Basic Land — Plains", identity="W"), 100)
    deck = [entry(bruna, 1, board="commander"), hundred]
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    assert "deck_size" in [error.code for error in result.errors]


def test_banned_cards_are_named() -> None:
    """A banned card errors with its name and oracle id."""
    deck = [*_deck_of(59), entry(card("Splinter Twin", type_line="Enchantment — Aura", cmc=4))]
    legality = {**all_legal(deck), "Splinter Twin": "banned"}
    result = validate_deck(deck, format_key="modern", legality=legality)
    banned = [error for error in result.errors if error.code == "banned"]
    assert len(banned) == 1
    assert "Splinter Twin" in banned[0].message
    assert banned[0].oracle_ids == ("Splinter Twin",)


def test_absent_from_the_legality_map_means_not_legal() -> None:
    """Scryfall omits formats a card was never legal in; absence is not legality."""
    deck = [*_deck_of(59), entry(card("Black Lotus", type_line="Artifact"))]
    legality = {"Wastes": "legal"}
    result = validate_deck(deck, format_key="modern", legality=legality)
    assert [error.code for error in result.errors] == ["not_legal"]


def test_pauper_legality_comes_from_the_field_not_rarity() -> None:
    """The engine has no rarity input at all: only the status decides.

    A card downshifted to common in one printing is pauper-legal by field; a
    common-looking card whose field says not_legal stays out.
    """
    monarch = card("Palace Sentinels", type_line="Creature — Human Soldier", cmc=4)
    deck = [*_deck_of(59), entry(monarch)]
    assert validate_deck(deck, format_key="pauper", legality={**all_legal(deck)}).is_legal

    result = validate_deck(
        deck, format_key="pauper", legality={**all_legal(deck), "Palace Sentinels": "not_legal"}
    )
    assert [error.code for error in result.errors] == ["not_legal"]


def test_the_maybe_board_is_ignored() -> None:
    """Cards under consideration are not part of the deck's legality."""
    deck = [*_deck_of(60), entry(card("Splinter Twin", type_line="Enchantment"), 4, board="maybe")]
    legality = {**all_legal(deck), "Splinter Twin": "banned"}
    assert validate_deck(deck, format_key="modern", legality=legality).is_legal


def test_proxies_warn_but_do_not_make_a_deck_illegal() -> None:
    """OPEN-QUESTIONS item 11: legal for playtesting, with the count surfaced."""
    proxied = entry(card("Wastes", type_line="Basic Land"), 60, is_proxy_intent=True)
    result = validate_deck([proxied], format_key="modern", legality={"Wastes": "legal"})
    assert result.is_legal
    assert [warning.code for warning in result.warnings] == ["contains_proxies"]
    assert "60" in result.warnings[0].message


def test_a_commander_board_outside_a_commander_format_errors() -> None:
    """A commander row in a modern deck is a mistake worth naming."""
    deck = [
        *_deck_of(60),
        entry(card("Bruna, the Fading Light", type_line="Legendary Creature"), 1, "commander"),
    ]
    result = validate_deck(deck, format_key="modern", legality=all_legal(deck))
    assert "no_commander_in_format" in [error.code for error in result.errors]
