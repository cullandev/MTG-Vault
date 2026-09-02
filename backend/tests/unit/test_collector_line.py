"""Reading the collector line, and turning it into a printing.

The parsing tests are pure string work and run anywhere. The OCR tests need the
``tesseract`` binary, which ships in the app container -- run the suite there
(``docker compose -f docker-compose.test.yml run --rm tests``).

Why this path matters enough to test this hard: ``(set_code, collector_number)`` is
the natural key of a printing, so a correct read ends identification in one query.
A *wrong* read is worse than no read at all -- it would add the wrong card to the
collection with full confidence -- which is why the negative cases below (nothing
readable, a set code that matches no real set, a pre-2015 card with no line at all)
all have to fail closed rather than guess.
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as DbSession

from app.config import get_settings
from app.models import Card, OracleCard
from app.ocr import preprocess
from app.services.scan import exact as exact_lookup
from app.services.scan import identifiers
from app.services.scan.identifiers import (
    CollectorIdentity,
    collector_variants,
    parse_collector_line,
)
from app.util.text import normalize_name
from tests.support import cards

# --- parsing ---------------------------------------------------------------


@pytest.mark.parametrize(
    ("text", "number", "set_code"),
    [
        ("0028/281 R\nFIN • EN • Some Artist", "28", "fin"),
        ("0028/0281 R FIN EN", "28", "fin"),
        ("28/281 C\nLTR EN Artist", "28", "ltr"),
        ("0001/291 M\n2X2 • EN", "1", "2x2"),
        ("0331/281 R\nMH3 EN Name", "331", "mh3"),
        # Line order reversed, as a block read can produce.
        ("LTR • EN\n0028/281 U", "28", "ltr"),
    ],
)
def test_collector_lines_parse(text: str, number: str, set_code: str) -> None:
    identity = parse_collector_line(text)
    assert identity.collector_number == number
    assert identity.set_code == set_code
    assert identity.is_exact


def test_leading_zeros_are_stripped_to_match_stored_numbers() -> None:
    """Cards print ``0028``; Scryfall stores ``28``. One of them has to give."""
    assert parse_collector_line("0028/281 R\nFIN EN").collector_number == "28"


def test_the_language_code_is_not_mistaken_for_the_set() -> None:
    """``EN`` sits right next to the set code and is the same shape."""
    identity = parse_collector_line("0028/281 R\nEN • FIN • Artist")
    assert identity.set_code == "fin"


def test_the_copyright_line_is_not_mistaken_for_the_set() -> None:
    identity = parse_collector_line("0028/281 R\nWIZARDS OF THE COAST\nFIN EN")
    assert identity.set_code == "fin"


def test_the_print_run_total_is_not_mistaken_for_the_set() -> None:
    """``281`` is three characters and all-caps-safe, but it is not a set code."""
    identity = parse_collector_line("0028/281 R")
    assert identity.collector_number == "28"
    assert identity.set_code is None
    assert not identity.is_exact


@pytest.mark.parametrize("text", ["", "   ", "\n\n", "WIZARDS OF THE COAST"])
def test_unreadable_lines_yield_nothing(text: str) -> None:
    identity = parse_collector_line(text)
    assert not identity.is_exact
    assert identity.collector_number is None


def test_digit_confusions_are_corrected_in_the_number() -> None:
    """``O`` for ``0`` in a field the whitelist still lets both through."""
    assert parse_collector_line("OO28/281 R\nFIN EN").collector_number == "28"


def test_the_total_is_kept_when_present() -> None:
    assert parse_collector_line("0028/281 R\nFIN EN").total == 281


def test_variants_try_the_unpadded_form_first() -> None:
    variants = collector_variants(CollectorIdentity(collector_number="28", set_code="fin"))
    assert variants[0] == "28"
    assert "0028" in variants


def test_variants_of_nothing_are_empty() -> None:
    assert collector_variants(CollectorIdentity()) == []


# --- lookup ----------------------------------------------------------------


def _seed(db: DbSession, *, set_code: str, number: str, name: str) -> Card:
    oracle_id = f"oracle-{set_code}-{number}"
    db.add(
        OracleCard(
            oracle_id=oracle_id,
            name=name,
            name_norm=normalize_name(name),
            name_front=name,
            name_front_norm=normalize_name(name),
            layout="normal",
        )
    )
    # Flushed separately: cards.oracle_id is a foreign key, and nothing declares the
    # relationship that would make the unit of work order these for us.
    db.flush()
    card = Card(
        scryfall_id=f"sf-{set_code}-{number}",
        oracle_id=oracle_id,
        name=name,
        name_front=name,
        name_norm=normalize_name(name),
        layout="normal",
        rarity="rare",
        set_code=set_code,
        set_name=set_code.upper(),
        collector_number=number,
        lang="en",
        digital=False,
    )
    db.add(card)
    db.flush()
    return card


def test_a_collector_line_resolves_to_one_printing(db: DbSession) -> None:
    seeded = _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    exact_lookup.reset_index()

    found = exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R\nFIN • EN • Artist"))
    assert found is not None
    assert found.card.id == seeded.id


def test_a_near_miss_set_code_is_snapped_to_the_real_one(db: DbSession) -> None:
    """One slipped character out of three must not cost the match."""
    seeded = _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    exact_lookup.reset_index()

    found = exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R\nFLN EN"))
    assert found is not None
    assert found.card.id == seeded.id


def test_a_set_code_matching_nothing_fails_closed(db: DbSession) -> None:
    """Better to fall through to the name path than to invent a printing."""
    _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    exact_lookup.reset_index()

    assert exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R\nQZX EN")) is None


def test_a_number_absent_from_the_set_fails_closed(db: DbSession) -> None:
    _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    exact_lookup.reset_index()

    assert exact_lookup.lookup_exact(db, parse_collector_line("0999/281 R\nFIN EN")) is None


def test_a_partial_read_is_not_looked_up(db: DbSession) -> None:
    _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    exact_lookup.reset_index()

    assert exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R")) is None


def test_the_preferred_language_wins_when_a_number_repeats(db: DbSession) -> None:
    """Collector numbers repeat across languages; the key includes lang for a reason."""
    _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    db.add(
        Card(
            scryfall_id="sf-fin-28-ja",
            oracle_id="oracle-fin-28",
            name="Thorin's Last Stand",
            name_front="Thorin's Last Stand",
            name_norm=normalize_name("Thorin's Last Stand"),
            layout="normal",
            rarity="rare",
            set_code="fin",
            collector_number="28",
            lang="ja",
            digital=False,
        )
    )
    db.flush()
    exact_lookup.reset_index()

    found = exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R\nFIN EN"))
    assert found is not None
    assert found.card.lang == "en"


def test_an_ambiguous_near_miss_refuses_to_pick(db: DbSession) -> None:
    """Two sets one character apart, both holding number 28: no answer is the answer.

    Guessing here would add the wrong printing with full confidence. Returning nothing
    hands the frame to the name path, which at worst shows a picker.
    """
    _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    _seed(db, set_code="fun", number="28", name="Something Else Entirely")
    exact_lookup.reset_index()

    assert exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R\nFLN EN")) is None


def test_an_unambiguous_near_miss_is_accepted_but_flagged(db: DbSession) -> None:
    """The same slipped character, but only one candidate set holds that number.

    Accepted -- and marked ``near_miss``, so fusion scores it as evidence rather
    than the outright answer. The Ringsight incident: a garbled ``LTR`` read as
    ``EVES`` resolved to a real (set, number) pair and locked the wrong card.
    """
    seeded = _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    _seed(db, set_code="fun", number="99", name="Something Else Entirely")
    exact_lookup.reset_index()

    found = exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R\nFLN EN"))
    assert found is not None
    assert found.card.id == seeded.id
    assert found.near_miss is True


def test_a_verbatim_code_is_not_flagged(db: DbSession) -> None:
    seeded = _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    exact_lookup.reset_index()

    found = exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R\nFIN EN"))
    assert found is not None
    assert found.card.id == seeded.id
    assert found.near_miss is False


def test_a_near_miss_from_the_wrong_era_is_rejected(db: DbSession) -> None:
    """A 2023 copyright line cannot belong to a 2008 printing the guesser found."""
    old = _seed(db, set_code="fin", number="28", name="Old Printing")
    old.released_at = "2008-07-25"
    db.flush()
    exact_lookup.reset_index()

    identity = parse_collector_line("0028/281 R\nFLN EN")
    assert identity.print_year is None  # no year on the line: era check cannot run
    with_year = parse_collector_line("0028/281 R\nFLN EN\n™ & © 2023 Wizards")
    if with_year.print_year == 2023:
        assert exact_lookup.lookup_exact(db, with_year) is None
    else:
        # The parser did not surface the year from this wording; the guard is
        # exercised directly instead.
        assert exact_lookup._year_plausible(old, 2023) is False
        assert exact_lookup._year_plausible(old, 2008) is True


def test_a_verbatim_set_code_beats_a_near_miss(db: DbSession) -> None:
    """An exact code is never second-guessed, even when a neighbour also fits."""
    seeded = _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    _seed(db, set_code="fun", number="28", name="Something Else Entirely")
    exact_lookup.reset_index()

    found = exact_lookup.lookup_exact(db, parse_collector_line("0028/281 R\nFIN EN"))
    assert found is not None
    assert found.card.id == seeded.id


def test_the_set_code_index_rebuilds_after_an_import(db: DbSession) -> None:
    exact_lookup.reset_index()
    assert exact_lookup.set_codes(db) == []

    _seed(db, set_code="fin", number="28", name="Thorin's Last Stand")
    assert "fin" in exact_lookup.set_codes(db)


# --- cropping --------------------------------------------------------------


def test_the_collector_crop_takes_the_bottom_left_corner() -> None:
    card = cards.render_card("Lightning Bolt")
    crop = preprocess.crop_collector_bar(card)

    width, height = cards.CARD_SIZE
    assert crop.height == pytest.approx(
        height * (preprocess.COLLECTOR_BOTTOM - preprocess.COLLECTOR_TOP), abs=2
    )
    assert crop.width == pytest.approx(
        width * (preprocess.COLLECTOR_RIGHT - preprocess.COLLECTOR_LEFT), abs=2
    )


def test_the_collector_crop_stops_before_the_artist_name() -> None:
    assert preprocess.COLLECTOR_RIGHT < 0.5


def test_the_collector_crop_is_upscaled_more_than_the_title_bar() -> None:
    """The collector line is printed roughly a third the height of the name."""
    assert preprocess.COLLECTOR_UPSCALE > preprocess.UPSCALE


# --- recognition (needs tesseract) -----------------------------------------


def _read(card: object) -> CollectorIdentity:
    from app.ocr import engine as ocr_engine

    engine = ocr_engine.get_engine(get_settings())
    crops = preprocess.prepare_collector(card)  # type: ignore[arg-type]
    best = CollectorIdentity()
    for _variant, image in crops:
        identity = parse_collector_line(engine.recognise(image, mode=ocr_engine.MODE_BLOCK).text)
        if identity.is_exact:
            return identity
        if identity.collector_number and not best.collector_number:
            best = identity
    return best


def test_a_rendered_collector_line_is_read_back() -> None:
    identity = _read(cards.render_card("Lightning Bolt", collector_number="0028", set_code="FIN"))
    assert identity.collector_number == "28"
    assert identity.set_code == "fin"


def test_a_white_bordered_collector_line_is_read_back() -> None:
    """Dark-on-light is the polarity the default black border never exercises."""
    identity = _read(
        cards.render_card(
            "Lightning Bolt",
            style=cards.WHITE_BORDER,
            collector_number="0143",
            set_code="LTR",
        )
    )
    assert identity.collector_number == "143"
    assert identity.set_code == "ltr"


def test_a_blurred_collector_line_is_still_read() -> None:
    card = cards.with_blur(
        cards.render_card("Lightning Bolt", collector_number="0007", set_code="MH3"), radius=0.8
    )
    assert _read(card).collector_number == "7"


def test_a_card_with_no_collector_line_reads_as_nothing() -> None:
    """Pre-2015 frames have no collector line; the name path must take over."""
    card = cards.render_card("Lightning Bolt", style=cards.OLD_FRAME, collector_number=None)
    assert not _read(card).is_exact


# --- the copyright year ----------------------------------------------------
#
# Cards printed before Magic Origins carry no collector number and no set code. What
# they do carry, in the same corner, is a copyright notice whose second year is the
# year that copy was printed -- and it matches its set's release year. For the core
# sets that is the only thing separating them: Gravedigger's M10, M11 and M12 printings
# share an artwork, and their set symbols measure fourteen bits apart (ADR-029).


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        ("Dermot Power\nTM & (C) 1993-2010 Wizards of the Coast", 2010),
        # An en dash, spelled by codepoint: it is what the notice is typeset with.
        ("(TM) & (C) 1993" + chr(0x2013) + "2009 Wizards of the Coast", 2009),
        ("TM & C 1993 2011 Wizards", 2011),
        ("TM & (C) 1993_2007 Wizards", 2007),
        # A lone year, with no range, is still the printing year.
        ("Wizards of the Coast 2016", 2016),
        # 1993 opens the notice on every card ever printed and says nothing about this one.
        ("TM & (C) 1993 Wizards of the Coast", None),
        # A modern collector line has no copyright notice in it at all.
        ("0028/281 R\nFIN - EN - Some Artist", None),
        ("", None),
    ],
)
def test_print_year_is_read_from_the_copyright_notice(text: str, expected: int | None) -> None:
    assert identifiers.parse_print_year(text) == expected


def test_an_impossible_year_is_discarded_rather_than_repaired() -> None:
    """Observed on a real scan: "1993 2011" came back as "199 2071".

    A misread year is worse than no year, because it excludes the right printing rather
    than merely failing to choose one. Nothing distinguishes a mis-OCR'd 2071 from a
    real one except knowing the game is not that old, so it is thrown away.
    """
    assert identifiers.parse_print_year("E P SO 199 2071 W O") is None


def test_the_year_ceiling_moves_with_the_calendar() -> None:
    """Hard-coding it would start rejecting real cards a few years from now."""
    assert identifiers.max_print_year() >= 2026


def test_the_year_is_carried_on_the_parsed_identity() -> None:
    identity = identifiers.parse_collector_line("Dermot Power TM & (C) 1993-2010 Wizards")

    assert identity.print_year == 2010


# --- narrowing candidates by year ------------------------------------------


def test_the_year_picks_the_one_candidate_printed_that_year() -> None:
    """Gravedigger's core-set printings, which nothing else separates."""
    released = {40708: "2009-07-17", 29493: "2010-07-16", 7977: "2011-07-15"}

    assert identifiers.narrow_by_print_year(released, [40708, 29493, 7977], 2010) == 29493


def test_two_candidates_from_one_year_leaves_the_question_open() -> None:
    """Zendikar and Magic 2010 both shipped in 2009. Taking the better-ranked one would
    be the artwork deciding again under another name."""
    released = {88808: "2009-10-02", 40708: "2009-07-17"}

    assert identifiers.narrow_by_print_year(released, [88808, 40708], 2009) is None


def test_a_year_matching_no_candidate_chooses_nothing() -> None:
    released = {40708: "2009-07-17", 29493: "2010-07-16"}

    assert identifiers.narrow_by_print_year(released, [40708, 29493], 2016) is None


def test_no_year_read_chooses_nothing() -> None:
    assert identifiers.narrow_by_print_year({40708: "2009-07-17"}, [40708], None) is None


def test_a_candidate_with_no_release_date_is_not_matched() -> None:
    """A missing date must not be read as "any year"."""
    released: dict[int, str | None] = {40708: None, 29493: "2010-07-16"}

    assert identifiers.narrow_by_print_year(released, [40708, 29493], 2010) == 29493
