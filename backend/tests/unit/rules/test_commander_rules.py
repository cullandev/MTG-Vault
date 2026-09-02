"""Commander leadership: who may lead, which pairs work, and the identity of the 99.

Oracle text in these fixtures is quoted from the real cards; colour identity
arrives pre-computed as Scryfall's field (ADR-010) -- the subset *logic* is what
is under test here, not the derivation.
"""

from __future__ import annotations

from app.services.rules import validate_deck
from tests.unit.rules.conftest import all_legal, card, entry

BRUNA = card(
    "Bruna, the Fading Light",
    type_line="Legendary Creature — Angel Horror",
    mana_cost="{5}{W}{W}",
    cmc=7,
    identity="W",
)


def _filler(count: int, identity: str = "") -> list:
    land = card("Wastes", type_line="Basic Land", identity=identity)
    return [entry(land, count)]


def _check(deck: list) -> list[str]:
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    return [error.code for error in result.errors]


def test_a_legendary_creature_may_lead() -> None:
    """CR 903.3: the commander is a legendary creature."""
    assert _check([entry(BRUNA, 1, "commander"), *_filler(99)]) == []


def test_can_be_your_commander_text_counts() -> None:
    """Teferi, Temporal Archmage: "Teferi ... can be your commander"."""
    teferi = card(
        "Teferi, Temporal Archmage",
        type_line="Legendary Planeswalker — Teferi",
        oracle_text="Teferi, Temporal Archmage can be your commander.",
        cmc=6,
        identity="U",
    )
    assert _check([entry(teferi, 1, "commander"), *_filler(99)]) == []


def test_a_nonlegendary_creature_may_not_lead() -> None:
    """A bear is not a commander."""
    bear = card("Grizzly Bears", type_line="Creature — Bear", cmc=2, identity="G")
    assert _check([entry(bear, 1, "commander"), *_filler(99)]) == ["invalid_commander"]


def test_no_commander_at_all() -> None:
    assert "no_commander" in _check(_filler(100))


def test_three_commanders_are_too_many() -> None:
    partner = card(
        "Sidar Kondo of Jamuraa",
        type_line="Legendary Creature — Human Knight",
        keywords={"Partner"},
        identity="GW",
    )
    deck = [
        entry(BRUNA, 1, "commander"),
        entry(partner, 1, "commander"),
        entry(
            card("Tana, the Bloodsower", type_line="Legendary Creature", keywords={"Partner"}),
            1,
            "commander",
        ),
        *_filler(97),
    ]
    assert "too_many_commanders" in _check(deck)


def test_two_generic_partners_pair() -> None:
    """CR 702.124: two commanders, each with Partner."""
    tana = card(
        "Tana, the Bloodsower",
        type_line="Legendary Creature — Elf Druid",
        keywords={"Partner"},
        identity="RG",
    )
    sidar = card(
        "Sidar Kondo of Jamuraa",
        type_line="Legendary Creature — Human Knight",
        keywords={"Partner"},
        identity="GW",
    )
    assert _check([entry(tana, 1, "commander"), entry(sidar, 1, "commander"), *_filler(98)]) == []


def test_two_random_legends_do_not_pair() -> None:
    """Two legendary creatures without a pairing mechanic cannot both lead."""
    urza = card(
        "Urza, Lord High Artificer", type_line="Legendary Creature — Human Artificer", identity="U"
    )
    deck = [entry(BRUNA, 1, "commander"), entry(urza, 1, "commander"), *_filler(98)]
    assert _check(deck) == ["invalid_partner"]


def test_partner_with_requires_the_named_pair() -> None:
    """ "Partner with" is specific: Brallin pairs with Shabraz, not with anyone else."""
    brallin = card(
        "Brallin, Skyshark Rider",
        type_line="Legendary Creature — Human Shaman",
        oracle_text="Partner with Shabraz, the Skyshark",
        keywords={"Partner with"},
        identity="R",
    )
    shabraz = card(
        "Shabraz, the Skyshark",
        type_line="Legendary Creature — Shark Bird",
        oracle_text="Partner with Brallin, Skyshark Rider",
        keywords={"Partner with"},
        identity="WU",
    )
    deck = [entry(brallin, 1, "commander"), entry(shabraz, 1, "commander"), *_filler(98)]
    assert _check(deck) == []

    wrong = [entry(brallin, 1, "commander"), entry(BRUNA, 1, "commander"), *_filler(98)]
    assert _check(wrong) == ["invalid_partner"]


def test_friends_forever_pair() -> None:
    """CR 702.124f-style: both commanders say Friends forever."""
    bess = card(
        "Sophina, Spearsage Deserter",
        type_line="Legendary Creature — Human Rogue",
        keywords={"Friends forever"},
        identity="W",
    )
    tale = card(
        "Wernog, Rider's Chaplain",
        type_line="Legendary Creature — Human Cleric",
        keywords={"Friends forever"},
        identity="WU",
    )
    assert _check([entry(bess, 1, "commander"), entry(tale, 1, "commander"), *_filler(98)]) == []


def test_choose_a_background() -> None:
    """CR 903.3d: a "Choose a Background" commander plus one Background."""
    wilson = card(
        "Wilson, Refined Grizzly",
        type_line="Legendary Creature — Bear",
        oracle_text="Choose a Background",
        keywords={"Choose a Background"},
        identity="G",
    )
    background = card(
        "Raised by Giants",
        type_line="Legendary Enchantment — Background",
        identity="G",
    )
    deck = [entry(wilson, 1, "commander"), entry(background, 1, "commander"), *_filler(98)]
    assert _check(deck) == []


def test_a_background_needs_its_enabler() -> None:
    """A Background without a "Choose a Background" commander is rejected."""
    background = card(
        "Raised by Giants", type_line="Legendary Enchantment — Background", identity="G"
    )
    alone = [entry(background, 1, "commander"), *_filler(99)]
    assert _check(alone) == ["invalid_commander"]

    with_bruna = [entry(BRUNA, 1, "commander"), entry(background, 1, "commander"), *_filler(98)]
    assert _check(with_bruna) == ["invalid_partner"]


def test_doctors_companion() -> None:
    """A Time Lord Doctor pairs with a card that says Doctor's companion."""
    doctor = card(
        "The Tenth Doctor",
        type_line="Legendary Creature — Time Lord Doctor",
        identity="UR",
    )
    companion = card(
        "Rose Tyler",
        type_line="Legendary Creature — Human",
        oracle_text="Doctor's companion (You can have two commanders if the other is the Doctor.)",
        keywords={"Doctor's companion"},
        identity="W",
    )
    deck = [entry(doctor, 1, "commander"), entry(companion, 1, "commander"), *_filler(98)]
    assert _check(deck) == []


def test_the_99_must_fit_the_commanders_identity() -> None:
    """CR 903.4-5: every card's colour identity within the commander's."""
    bolt = card("Lightning Bolt", type_line="Instant", mana_cost="{R}", cmc=1, identity="R")
    deck = [entry(BRUNA, 1, "commander"), entry(bolt, 1), *_filler(98)]
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    offending = [error for error in result.errors if error.code == "color_identity"]
    assert len(offending) == 1
    assert offending[0].oracle_ids == ("Lightning Bolt",)


def test_hybrid_identity_spans_both_colours() -> None:
    """Boros Charm's identity is RW; a mono-W commander cannot run it."""
    charm = card("Boros Charm", type_line="Instant", mana_cost="{R/W}{R/W}", cmc=2, identity="RW")
    deck = [entry(BRUNA, 1, "commander"), entry(charm, 1), *_filler(98)]
    assert "color_identity" in _check(deck)


def test_partner_identities_combine() -> None:
    """The 99 may use the union of both partners' identities."""
    tana = card(
        "Tana, the Bloodsower",
        type_line="Legendary Creature — Elf Druid",
        keywords={"Partner"},
        identity="RG",
    )
    sidar = card(
        "Sidar Kondo of Jamuraa",
        type_line="Legendary Creature — Human Knight",
        keywords={"Partner"},
        identity="GW",
    )
    naya_card = card("Naya Charm", type_line="Instant", cmc=3, identity="RGW")
    deck = [
        entry(tana, 1, "commander"),
        entry(sidar, 1, "commander"),
        entry(naya_card, 1),
        *_filler(97),
    ]
    assert _check(deck) == []
