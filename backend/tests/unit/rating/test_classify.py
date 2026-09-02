"""Interaction classification: the named true positives and the named traps.

Oracle text in these fixtures is quoted from the real cards (TEST-PLAN Phase 5).
"""

from __future__ import annotations

from app.services.rating.classify import classify
from tests.unit.rules.conftest import card


def test_swords_to_plowshares_is_removal() -> None:
    swords = card(
        "Swords to Plowshares",
        type_line="Instant",
        oracle_text="Exile target creature. Its controller gains life equal to its power.",
    )
    tags = classify(swords)
    assert "removal" in tags
    assert "instant_speed" in tags


def test_counterspell_counters() -> None:
    counter = card("Counterspell", type_line="Instant", oracle_text="Counter target spell.")
    assert "counterspell" in classify(counter)


def test_wrath_of_god_is_mass_removal() -> None:
    wrath = card(
        "Wrath of God",
        type_line="Sorcery",
        oracle_text="Destroy all creatures. They can't be regenerated.",
    )
    tags = classify(wrath)
    assert "mass_removal" in tags
    assert "removal" not in tags


def test_rest_in_peace_is_hate() -> None:
    rip = card(
        "Rest in Peace",
        type_line="Enchantment",
        oracle_text=(
            "When Rest in Peace enters the battlefield, exile all graveyards. "
            "If a card or token would be put into a graveyard from anywhere, "
            "exile it instead."
        ),
    )
    # The blanket replacement wording is the classifier's graveyard-hate signal.
    assert classify(rip) & {"hate", "removal", "mass_removal"}


def test_doom_blade_and_an_ability_that_destroys_both_count() -> None:
    """The trap: removal on a permanent is still removal, at permanent speed."""
    doom_blade = card(
        "Doom Blade", type_line="Instant", oracle_text="Destroy target nonblack creature."
    )
    nekrataal = card(
        "Nekrataal",
        type_line="Creature — Human Assassin",
        oracle_text=(
            "When Nekrataal enters the battlefield, destroy target nonartifact, "
            "nonblack creature. It can't be regenerated."
        ),
    )
    assert "instant_speed" in classify(doom_blade)
    nekrataal_tags = classify(nekrataal)
    assert "removal" in nekrataal_tags
    assert "permanent_speed" in nekrataal_tags


def test_fog_is_not_removal() -> None:
    """The trap: preventing damage answers nothing."""
    fog = card(
        "Fog",
        type_line="Instant",
        oracle_text="Prevent all combat damage that would be dealt this turn.",
    )
    tags = classify(fog)
    assert "fog" in tags
    assert "removal" not in tags


def test_pacifism_counts_as_removal() -> None:
    """The trap, the other way: neutralising a creature answers it."""
    pacifism = card(
        "Pacifism",
        type_line="Enchantment — Aura",
        oracle_text="Enchant creature\nEnchanted creature can't attack or block.",
    )
    tags = classify(pacifism)
    assert "removal" in tags
    assert "soft_removal" in tags


def test_reminder_text_never_classifies() -> None:
    """A card whose only 'destroy target creature' is reminder text stays clean."""
    vanilla = card(
        "Runeclaw Bear",
        type_line="Creature — Bear",
        oracle_text="(Some card frames explain deathtouch as: destroy target creature.)",
    )
    assert classify(vanilla) == frozenset()


def test_ramp_and_draw_and_tutor() -> None:
    cultivate = card(
        "Cultivate",
        type_line="Sorcery",
        oracle_text=(
            "Search your library for up to two basic land cards, reveal those cards, "
            "and put one onto the battlefield tapped and the other into your hand."
        ),
    )
    tags = classify(cultivate)
    assert "ramp" in tags
    assert "tutor" not in tags  # basic-land fetch is mana development, not tutoring

    demonic = card(
        "Demonic Tutor",
        type_line="Sorcery",
        oracle_text="Search your library for a card, put that card into your hand, then shuffle.",
    )
    assert "tutor" in classify(demonic)

    divination = card("Divination", type_line="Sorcery", oracle_text="Draw two cards.")
    assert "draw" in classify(divination)


def test_fetchland_is_not_a_tutor() -> None:
    strand = card(
        "Flooded Strand",
        type_line="Land",
        oracle_text=(
            "{T}, Pay 1 life, Sacrifice Flooded Strand: Search your library for a "
            "Plains or Island card, put it onto the battlefield, then shuffle."
        ),
    )
    assert "tutor" not in classify(strand)


def test_protection_and_recursion() -> None:
    intervention = card(
        "Heroic Intervention",
        type_line="Instant",
        oracle_text="Permanents you control gain hexproof and indestructible until end of turn.",
    )
    assert "protection" in classify(intervention)

    regrowth = card(
        "Regrowth",
        type_line="Sorcery",
        oracle_text="Return target card from your graveyard to your hand.",
    )
    assert "recursion" in classify(regrowth)


def test_essence_scatter_is_a_counterspell() -> None:
    """ "Counter target creature spell" -- the typed counters count too."""
    scatter = card(
        "Essence Scatter", type_line="Instant", oracle_text="Counter target creature spell."
    )
    assert "counterspell" in classify(scatter)


def test_blink_is_not_removal() -> None:
    """Cloudshift exiles your own creature and returns it: protection, not an answer."""
    cloudshift = card(
        "Cloudshift",
        type_line="Instant",
        oracle_text=(
            "Exile target creature you control, then return that card to the "
            "battlefield under your control."
        ),
    )
    assert "removal" not in classify(cloudshift)


def test_x_damage_counts_as_removal() -> None:
    fireball = card(
        "Fireball",
        type_line="Sorcery",
        oracle_text="Fireball deals X damage divided evenly... deals X damage to any target.",
    )
    assert "removal" in classify(fireball)

    quake = card(
        "Earthquake",
        type_line="Sorcery",
        oracle_text="Earthquake deals X damage to each creature without flying and each player.",
    )
    assert "mass_removal" in classify(quake)


def test_opponent_draw_punishers_are_not_card_draw() -> None:
    dreams = card(
        "Underworld Dreams",
        type_line="Enchantment",
        oracle_text="Whenever an opponent draws a card, Underworld Dreams deals 1 damage to them.",
    )
    assert "draw" not in classify(dreams)


def test_mass_land_fetch_is_not_a_tutor() -> None:
    realms = card(
        "Boundless Realms",
        type_line="Sorcery",
        oracle_text=(
            "Search your library for a number of basic land cards equal to the "
            "number of lands you control, put them onto the battlefield tapped."
        ),
    )
    assert "tutor" not in classify(realms)
