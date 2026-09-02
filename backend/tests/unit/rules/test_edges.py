"""The corners the main suites do not reach: serialisation and defensive branches.

The 100% requirement on ``app/services/rules`` (TEST-PLAN.md section 0) is the
point: in this module an unexercised branch is an illegal deck waiting to happen.
"""

from __future__ import annotations

from app.services.rules import validate_deck
from app.services.rules.commander import pair_allowed
from tests.unit.rules.conftest import all_legal, card, entry


def test_the_result_serialises_for_storage() -> None:
    """``deck_validations.errors_json`` stores exactly this shape."""
    deck = [entry(card("Wastes", type_line="Basic Land"), 59)]
    result = validate_deck(deck, format_key="modern", legality=all_legal(deck))
    payload = result.as_dict()
    assert payload["is_legal"] is False
    assert payload["errors"][0]["code"] == "deck_size"
    assert payload["errors"][0]["oracle_ids"] == []
    assert payload["warnings"] == []


def test_partner_with_read_from_mid_line_text() -> None:
    """Fixture text sometimes carries "Partner with X" mid-line, not as its own line."""
    left = card(
        "Proud Mentor",
        type_line="Legendary Creature — Human",
        oracle_text="Flying. Partner with Impetuous Protege (When this creature...)",
    )
    right = card(
        "Impetuous Protege",
        type_line="Legendary Creature — Human",
        oracle_text="Partner with Proud Mentor",
        keywords={"Partner with"},
    )
    assert pair_allowed(left, right) == "partner_with"


def test_umori_with_an_all_land_deck_has_no_offenders() -> None:
    """Umori's shared-type rule is vacuously satisfied by a deck of lands."""
    umori = card(
        "Umori, the Collector", type_line="Legendary Creature — Ooze", keywords={"Companion"}, cmc=4
    )
    deck = [
        entry(card("Wastes", type_line="Basic Land"), 60),
        entry(umori, 1, "companion"),
    ]
    result = validate_deck(deck, format_key="modern", legality=all_legal(deck))
    assert result.is_legal


def test_commander_sideboard_cards_warn_and_are_ignored() -> None:
    """Commander has no sideboard; cards there warn rather than break the deck."""
    bruna = card("Bruna, the Fading Light", type_line="Legendary Creature — Angel", identity="W")
    deck = [
        entry(bruna, 1, "commander"),
        entry(card("Plains", type_line="Basic Land — Plains", identity="W"), 99),
        entry(card("Disenchant", type_line="Instant", cmc=2, identity="W"), 1, "side"),
    ]
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    assert result.is_legal
    assert "sideboard_ignored" in [warning.code for warning in result.warnings]


def test_an_unknown_companion_keyword_enforces_nothing() -> None:
    """A future companion (keyword present, name unknown) passes rather than crashes.

    Wizards has said the mechanic will not return; if it somehow does, the deck
    stays legal and the named restrictions arrive with the card.
    """
    future = card(
        "Totally New Companion",
        type_line="Legendary Creature — Sphinx",
        keywords={"Companion"},
        cmc=4,
    )
    deck = [
        entry(card("Wastes", type_line="Basic Land"), 60),
        entry(future, 1, "companion"),
    ]
    result = validate_deck(deck, format_key="modern", legality=all_legal(deck))
    assert result.is_legal


def test_companion_counts_toward_the_sideboard_limit() -> None:
    """CR 702.139a: the companion is one of the fifteen sideboard cards."""
    from tests.unit.rules.test_companions import KAHEERA

    deck = [
        entry(card("Wastes", type_line="Basic Land"), 60),
        *[entry(card(f"Side {i}", type_line="Instant", cmc=1), 1, "side") for i in range(15)],
        entry(KAHEERA, 1, "companion"),
    ]
    result = validate_deck(deck, format_key="modern", legality=all_legal(deck))
    assert "sideboard_size" in [error.code for error in result.errors]


def test_companion_must_fit_the_commanders_identity() -> None:
    """A WUBRG companion cannot ride along with a mono-white commander."""
    from tests.unit.rules.test_companions import JEGANTHA

    bruna = card(
        "Bruna, the Fading Light",
        type_line="Legendary Creature — Angel",
        identity="W",
    )
    jegantha = card(
        JEGANTHA.name,
        type_line=JEGANTHA.type_line,
        keywords={"Companion"},
        cmc=5,
        identity="WUBRG",
    )
    deck = [
        entry(bruna, 1, "commander"),
        entry(card("Plains", type_line="Basic Land — Plains", identity="W"), 99),
        entry(jegantha, 1, "companion"),
    ]
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    assert "color_identity" in [error.code for error in result.errors]


def test_commander_side_board_is_ignored_consistently() -> None:
    """A swap copy parked in the side must not flunk the singleton rule it is
    exempted from by the 'sideboard ignored' warning."""
    bruna = card("Bruna, the Fading Light", type_line="Legendary Creature — Angel", identity="W")
    swap = card("Disenchant", type_line="Instant", cmc=2, identity="W")
    deck = [
        entry(bruna, 1, "commander"),
        entry(swap, 1),
        entry(card("Plains", type_line="Basic Land — Plains", identity="W"), 98),
        entry(swap, 1, "side"),
    ]
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    assert result.is_legal
    assert "sideboard_ignored" in [warning.code for warning in result.warnings]


def test_kaheera_accepts_changelings() -> None:
    """CR 702.73a: a changeling is every creature type, Cat included."""
    from tests.unit.rules.test_companions import KAHEERA, LAND

    changeling = card(
        "Universal Automaton",
        type_line="Artifact Creature — Shapeshifter",
        keywords={"Changeling"},
        cmc=1,
    )
    deck = [
        entry(changeling, 4),
        entry(LAND, 56),
        entry(KAHEERA, 1, "companion"),
    ]
    result = validate_deck(deck, format_key="modern", legality=all_legal(deck))
    assert result.is_legal


def test_unknown_formats_get_the_constructed_default_not_commander_rules() -> None:
    """Oathbreaker is deliberately unmodeled: planeswalker commanders would be
    wrongly rejected, so the profile no longer exists (better honest-default
    than confidently wrong)."""
    from app.services.rules import profile_for

    profile = profile_for("oathbreaker")
    assert profile.has_commander is False
    assert profile.min_main == 60
