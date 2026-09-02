"""Decklist text: parsing dialects, name resolution, and the round trip.

Round-trip requirement (TEST-PLAN Phase 4): Moxfield and Archidekt formats survive
export -> import including the commander section, sideboard, companion, categories,
and ``//`` names.
"""

from __future__ import annotations

from sqlalchemy.orm import Session as DbSession

from app.models import DeckCard, OracleCard
from app.services.decks import text_io


def test_quantities_and_x_suffixes() -> None:
    lines = text_io.parse_decklist("4 Lightning Bolt\n2x Sol Ring\nOpt")
    assert [(line.quantity, line.name) for line in lines] == [
        (4, "Lightning Bolt"),
        (2, "Sol Ring"),
        (1, "Opt"),
    ]


def test_moxfield_printing_hints() -> None:
    (line,) = text_io.parse_decklist("4 Lightning Bolt (2ED) 162")
    assert (line.set_code, line.collector_number) == ("2ed", "162")


def test_archidekt_categories() -> None:
    (line,) = text_io.parse_decklist("1x Sol Ring (lea) 263 [Ramp]")
    assert line.category == "Ramp"
    assert line.set_code == "lea"


def test_section_headers_switch_boards() -> None:
    text = "Commander\n1 Bruna, the Fading Light\n\nDeck\n40 Island\n\nSideboard\n2 Duress"
    lines = text_io.parse_decklist(text)
    assert [(line.board, line.name) for line in lines] == [
        ("commander", "Bruna, the Fading Light"),
        ("main", "Island"),
        ("side", "Duress"),
    ]


def test_sb_prefix_marks_a_sideboard_line() -> None:
    lines = text_io.parse_decklist("4 Opt\nSB: 2 Duress")
    assert [(line.board, line.name) for line in lines] == [("main", "Opt"), ("side", "Duress")]


def test_split_names_are_not_comments() -> None:
    """``Fire // Ice`` is a card; ``// note`` is a comment."""
    lines = text_io.parse_decklist("2 Fire // Ice\n// just a note\n# another note")
    assert [(line.quantity, line.name) for line in lines] == [(2, "Fire // Ice")]


def test_foil_markers_are_stripped() -> None:
    (line,) = text_io.parse_decklist("1 Sol Ring (lea) 263 *F*")
    assert line.name == "Sol Ring"
    assert line.collector_number == "263"


def test_split_cards_resolve_from_either_half(catalog: DbSession) -> None:
    """TEST-PLAN section 1: import accepts ``Fire``, ``Ice`` and ``Fire // Ice``."""
    for spelling in ("Fire // Ice", "Fire", "Ice"):
        oracle = text_io.resolve_name(catalog, spelling)
        assert oracle is not None, spelling
        assert oracle.name == "Fire // Ice"


def test_transform_cards_resolve_from_the_front_face(catalog: DbSession) -> None:
    oracle = text_io.resolve_name(catalog, "Delver of Secrets")
    assert oracle is not None
    assert oracle.name == "Delver of Secrets // Insectile Aberration"


def test_unresolved_names_are_reported_not_dropped(catalog: DbSession) -> None:
    outcome = text_io.import_text(
        catalog,
        text="1 Sol Ring\n1 Storm Crow",
        name="Test deck",
        format_key="commander",
    )
    assert outcome.added == 1
    assert outcome.unresolved == ["Storm Crow"]


def test_the_import_populates_every_board(catalog: DbSession) -> None:
    text = (
        "Commander\n1 Bruna, the Fading Light\n\n"
        "Deck\n4 Lightning Bolt (2ed) 162 [Burn]\n10 Island\n\n"
        "Sideboard\n1 Sol Ring\n\nMaybeboard\n1 Birthing Pod"
    )
    outcome = text_io.import_text(catalog, text=text, name="Boards", format_key="commander")
    assert outcome.unresolved == []
    deck = outcome.deck
    rows = {
        (row.board, _name(catalog, row.oracle_id)): row
        for row in catalog.query(DeckCard).filter(DeckCard.deck_id == deck.id)
    }
    assert ("commander", "Bruna, the Fading Light") in rows
    bolt = rows[("main", "Lightning Bolt")]
    assert bolt.quantity == 4
    assert bolt.preferred_set_code == "2ed"
    assert bolt.preferred_collector_number == "162"
    assert bolt.category == "Burn"
    assert ("side", "Sol Ring") in rows
    assert ("maybe", "Birthing Pod") in rows
    assert deck.commander_oracle_id is not None


def test_moxfield_and_archidekt_round_trip(catalog: DbSession) -> None:
    """Export in each dialect, re-import, and get the same deck back."""
    text = (
        "Commander\n1 Bruna, the Fading Light\n\n"
        "Deck\n4 Fire // Ice\n10 Island\n2 Sol Ring (lea) 263 [Ramp]\n\n"
        "Sideboard\n2 Ancestral Vision"
    )
    original = text_io.import_text(catalog, text=text, name="Original", format_key="commander")
    assert original.unresolved == []

    for flavour in ("text", "moxfield", "archidekt"):
        exported = text_io.export_text(catalog, original.deck, flavour=flavour)
        reimported = text_io.import_text(
            catalog, text=exported, name=f"Round trip {flavour}", format_key="commander"
        )
        assert reimported.unresolved == []
        assert _contents(catalog, reimported.deck.id) == _contents(catalog, original.deck.id)


def _name(db: DbSession, oracle_id: str) -> str:
    oracle = db.get(OracleCard, oracle_id)
    assert oracle is not None
    return oracle.name


def _contents(db: DbSession, deck_id: int) -> set[tuple[str, str, int]]:
    return {
        (row.board, row.oracle_id, row.quantity)
        for row in db.query(DeckCard).filter(DeckCard.deck_id == deck_id)
    }
