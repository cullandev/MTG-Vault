"""The ten companions, each by name (TEST-PLAN.md section 1, Commander-specific)."""

from __future__ import annotations

from app.services.rules import validate_deck
from tests.unit.rules.conftest import all_legal, card, entry

LURRUS = card(
    "Lurrus of the Dream-Den",
    type_line="Legendary Creature — Cat Nightmare",
    keywords={"Companion"},
    cmc=3,
)
YORION = card(
    "Yorion, Sky Nomad",
    type_line="Legendary Creature — Bird Serpent",
    keywords={"Companion"},
    cmc=5,
)
JEGANTHA = card(
    "Jegantha, the Wellspring",
    type_line="Legendary Creature — Elemental Elk",
    keywords={"Companion"},
    cmc=5,
)
OBOSH = card(
    "Obosh, the Preypiercer",
    type_line="Legendary Creature — Hellion Horror",
    keywords={"Companion"},
    cmc=5,
)
GYRUDA = card(
    "Gyruda, Doom of Depths",
    type_line="Legendary Creature — Demon Kraken",
    keywords={"Companion"},
    cmc=6,
)
KERUGA = card(
    "Keruga, the Macrosage",
    type_line="Legendary Creature — Dinosaur Hippo",
    keywords={"Companion"},
    cmc=5,
)
KAHEERA = card(
    "Kaheera, the Orphanguard",
    type_line="Legendary Creature — Cat Beast",
    keywords={"Companion"},
    cmc=3,
)
ZIRDA = card(
    "Zirda, the Dawnwaker",
    type_line="Legendary Creature — Elemental Fox",
    keywords={"Companion"},
    cmc=3,
)
UMORI = card(
    "Umori, the Collector", type_line="Legendary Creature — Ooze", keywords={"Companion"}, cmc=4
)
LUTRI = card(
    "Lutri, the Spellchaser",
    type_line="Legendary Creature — Elemental Otter",
    keywords={"Companion"},
    cmc=3,
)

LAND = card("Wastes", type_line="Basic Land")


def _codes(deck: list, format_key: str = "modern") -> list[str]:
    result = validate_deck(deck, format_key=format_key, legality=all_legal(deck))
    return [error.code for error in result.errors]


def _sixty_with(companion, *extra) -> list:
    body = list(extra)
    padding = 60 - sum(e.quantity for e in body)
    return [*body, entry(LAND, padding), entry(companion, 1, "companion")]


def test_lurrus_limits_permanents_to_two_mana() -> None:
    """Lurrus: each permanent card in the starting deck costs 2 or less."""
    cheap = card("Esper Sentinel", type_line="Artifact Creature — Human Soldier", cmc=1)
    fine = _sixty_with(LURRUS, entry(cheap, 4))
    assert _codes(fine) == []

    heavy = card("Sun Titan", type_line="Creature — Giant", cmc=6)
    broken = _sixty_with(LURRUS, entry(heavy, 1))
    assert "companion_restriction" in _codes(broken)


def test_lurrus_does_not_restrict_spells() -> None:
    """A six-mana instant is fine; only permanent cards are constrained."""
    big_spell = card("Overwhelming Splendor", type_line="Instant", cmc=6)
    deck = _sixty_with(LURRUS, entry(big_spell, 4))
    assert _codes(deck) == []


def test_yorion_wants_twenty_more_cards() -> None:
    """Yorion: the starting deck has at least 20 cards over the minimum."""
    eighty = [entry(LAND, 80), entry(YORION, 1, "companion")]
    assert _codes(eighty) == []

    seventy_nine = [entry(LAND, 79), entry(YORION, 1, "companion")]
    assert "companion_restriction" in _codes(seventy_nine)


def test_jegantha_bans_repeated_symbols() -> None:
    """Jegantha: no card with two of the same symbol in its mana cost."""
    rainbow = card(
        "Niv-Mizzet Reborn",
        type_line="Legendary Creature — Dragon Avatar",
        mana_cost="{W}{U}{B}{R}{G}",
        cmc=5,
    )
    assert _codes(_sixty_with(JEGANTHA, entry(rainbow, 1))) == []

    double_white = card("Wrath of God", type_line="Sorcery", mana_cost="{2}{W}{W}", cmc=4)
    assert "companion_restriction" in _codes(_sixty_with(JEGANTHA, entry(double_white, 1)))


def test_jegantha_counts_hybrid_symbols_as_themselves() -> None:
    """{W/U}{W/U} is the same symbol twice."""
    hybrid = card(
        "Curse of Chains", type_line="Enchantment — Aura Curse", mana_cost="{1}{W/U}{W/U}", cmc=3
    )
    assert "companion_restriction" in _codes(_sixty_with(JEGANTHA, entry(hybrid, 1)))


def test_obosh_wants_odd_mana_values() -> None:
    """Obosh: every nonland card has an odd mana value; lands are exempt."""
    odd = card(
        "Bonecrusher Giant // Stomp",
        type_line="Creature — Giant // Instant — Adventure",
        cmc=3,
        layout="adventure",
    )
    assert _codes(_sixty_with(OBOSH, entry(odd, 4))) == []

    even = card("Grizzly Bears", type_line="Creature — Bear", cmc=2)
    assert "companion_restriction" in _codes(_sixty_with(OBOSH, entry(even, 1)))


def test_gyruda_wants_even_mana_values() -> None:
    even = card("Grizzly Bears", type_line="Creature — Bear", cmc=2)
    assert _codes(_sixty_with(GYRUDA, entry(even, 4))) == []

    odd = card("Lightning Bolt", type_line="Instant", cmc=1)
    assert "companion_restriction" in _codes(_sixty_with(GYRUDA, entry(odd, 1)))


def test_keruga_wants_three_or_more() -> None:
    """Keruga: nonland cards cost 3 or more; lands stay legal."""
    big = card("Sun Titan", type_line="Creature — Giant", cmc=6)
    assert _codes(_sixty_with(KERUGA, entry(big, 4))) == []

    small = card("Lightning Bolt", type_line="Instant", cmc=1)
    assert "companion_restriction" in _codes(_sixty_with(KERUGA, entry(small, 1)))


def test_kaheera_wants_her_tribes() -> None:
    """Kaheera: every creature card is a Cat, Elemental, Nightmare, Dinosaur or Beast."""
    cat = card("Savannah Lions", type_line="Creature — Cat", cmc=1)
    assert _codes(_sixty_with(KAHEERA, entry(cat, 4))) == []

    human = card("Grizzly Bears", type_line="Creature — Bear", cmc=2)
    assert "companion_restriction" in _codes(_sixty_with(KAHEERA, entry(human, 1)))


def test_kaheera_ignores_noncreatures() -> None:
    spell = card("Opt", type_line="Instant", cmc=1)
    assert _codes(_sixty_with(KAHEERA, entry(spell, 4))) == []


def test_zirda_wants_activated_abilities() -> None:
    """Zirda: every permanent card has an activated ability.

    Basic lands count (their mana ability is intrinsic, CR 305.6); an Equipment
    counts through Equip; a colon in rules text counts; a vanilla creature fails.
    """
    equipment = card(
        "Shadowspear",
        type_line="Legendary Artifact — Equipment",
        oracle_text="Equip {3}",
        keywords={"Equip"},
        cmc=1,
    )
    fetch = card(
        "Prismatic Vista",
        type_line="Land",
        oracle_text="{T}, Pay 1 life, Sacrifice Prismatic Vista: Search your library...",
    )
    assert _codes(_sixty_with(ZIRDA, entry(equipment, 4), entry(fetch, 4))) == []

    vanilla = card("Grizzly Bears", type_line="Creature — Bear", cmc=2)
    assert "companion_restriction" in _codes(_sixty_with(ZIRDA, entry(vanilla, 1)))


def test_umori_wants_one_shared_type() -> None:
    """Umori: all nonland cards share a card type; artifact creatures bridge."""
    creature = card("Grizzly Bears", type_line="Creature — Bear", cmc=2)
    artifact_creature = card("Esper Sentinel", type_line="Artifact Creature — Human", cmc=1)
    assert _codes(_sixty_with(UMORI, entry(creature, 4), entry(artifact_creature, 4))) == []

    instant = card("Opt", type_line="Instant", cmc=1)
    assert "companion_restriction" in _codes(
        _sixty_with(UMORI, entry(creature, 4), entry(instant, 4))
    )


def test_lutri_wants_singleton_nonlands() -> None:
    """Lutri: no two nonland cards share a name; basics stay unlimited."""
    bolt = card("Lightning Bolt", type_line="Instant", cmc=1)
    singleton = _sixty_with(LUTRI, entry(bolt, 1))
    assert _codes(singleton) == []

    doubled = _sixty_with(LUTRI, entry(bolt, 2))
    assert "companion_restriction" in _codes(doubled)


def test_the_companion_sits_outside_the_hundred() -> None:
    """In Commander the companion is not one of the 100 (TEST-PLAN §1)."""
    arahbo = card(
        "Arahbo, Roar of the World",
        type_line="Legendary Creature — Cat Avatar",
        cmc=5,
        identity="GW",
    )
    plains = card("Plains", type_line="Basic Land — Plains", identity="W")
    deck = [
        entry(arahbo, 1, "commander"),
        entry(plains, 99),
        entry(KAHEERA, 1, "companion"),
    ]
    result = validate_deck(deck, format_key="commander", legality=all_legal(deck))
    assert [error.code for error in result.errors] == []


def test_only_one_companion() -> None:
    deck = [
        entry(LAND, 60),
        entry(KAHEERA, 1, "companion"),
        entry(LURRUS, 1, "companion"),
    ]
    assert "companion_count" in _codes(deck)


def test_a_non_companion_in_the_companion_board() -> None:
    deck = [entry(LAND, 60), entry(card("Grizzly Bears", cmc=2), 1, "companion")]
    assert "not_a_companion" in _codes(deck)
