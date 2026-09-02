"""Bracket detection: each signal keyed to the card that defines it (TEST-PLAN Phase 5)."""

from __future__ import annotations

from app.services.rating.brackets import detect_bracket
from tests.unit.rules.conftest import card, entry

LAND = card("Plains", type_line="Basic Land — Plains")

ARMAGEDDON = card("Armageddon", type_line="Sorcery", oracle_text="Destroy all lands.", cmc=4)
WINTER_ORB = card(
    "Winter Orb",
    type_line="Artifact",
    oracle_text=(
        "As long as Winter Orb is untapped, lands don't untap during their "
        "controllers' untap steps."
    ),
    cmc=2,
)
BLOOD_MOON = card(
    "Blood Moon",
    type_line="Enchantment",
    oracle_text="Nonbasic lands are Mountains.",
    cmc=3,
)
TIME_WARP = card(
    "Time Warp",
    type_line="Sorcery",
    oracle_text="Target player takes an extra turn after this one.",
    cmc=5,
)
DEMONIC = card(
    "Demonic Tutor",
    type_line="Sorcery",
    oracle_text="Search your library for a card, put that card into your hand, then shuffle.",
    cmc=2,
)


def _base(count: int = 99) -> list:
    return [entry(LAND, count)]


def test_a_plain_deck_is_core() -> None:
    verdict = detect_bracket(_base(100), two_card_combos=[])
    assert verdict.bracket == 2
    assert verdict.signals == {
        "game_changers": [],
        "extra_turns": [],
        "mass_land_denial": [],
        "two_card_combos": [],
        "tutors": [],
    }


def test_game_changers_come_from_the_scryfall_flag() -> None:
    """The flag decides -- there is no name list to go stale."""
    crypt = card("Mana Crypt", type_line="Artifact", cmc=0)
    flagged = card("Mana Crypt", type_line="Artifact", cmc=0, game_changer=True)
    assert detect_bracket([*_base(), entry(crypt)], two_card_combos=[]).bracket == 2

    verdict = detect_bracket([*_base(), entry(flagged)], two_card_combos=[])
    assert verdict.bracket == 3
    assert verdict.signals["game_changers"] == ["Mana Crypt"]


def test_armageddon_is_mass_land_denial() -> None:
    verdict = detect_bracket([*_base(), entry(ARMAGEDDON)], two_card_combos=[])
    assert verdict.bracket == 4
    assert verdict.signals["mass_land_denial"] == ["Armageddon"]


def test_winter_orb_is_mass_land_denial() -> None:
    verdict = detect_bracket([*_base(), entry(WINTER_ORB)], two_card_combos=[])
    assert "Winter Orb" in verdict.signals["mass_land_denial"]


def test_blood_moon_is_not_mass_land_denial() -> None:
    """Changing what lands produce is not denying them (TEST-PLAN, by name)."""
    verdict = detect_bracket([*_base(), entry(BLOOD_MOON)], two_card_combos=[])
    assert verdict.signals["mass_land_denial"] == []
    assert verdict.bracket == 2


def test_time_warp_is_an_extra_turn() -> None:
    verdict = detect_bracket([*_base(), entry(TIME_WARP)], two_card_combos=[])
    assert verdict.signals["extra_turns"] == ["Time Warp"]
    assert verdict.bracket == 3  # one extra turn is upgraded, not optimized

    doubled = detect_bracket(
        [
            *_base(98),
            entry(TIME_WARP),
            entry(
                card(
                    "Temporal Manipulation",
                    type_line="Sorcery",
                    oracle_text="Take an extra turn after this one.",
                    cmc=5,
                )
            ),
        ],
        two_card_combos=[],
    )
    assert doubled.bracket == 4


def test_tutors_are_counted_and_named() -> None:
    verdict = detect_bracket([*_base(), entry(DEMONIC)], two_card_combos=[])
    assert verdict.signals["tutors"] == ["Demonic Tutor"]


def test_two_card_combos_push_to_optimized() -> None:
    verdict = detect_bracket(_base(100), two_card_combos=["Thassa's Oracle + Demonic Consultation"])
    assert verdict.bracket == 4


def test_unknown_combo_state_is_reported_not_zeroed() -> None:
    verdict = detect_bracket(_base(100), two_card_combos=None)
    assert verdict.bracket == 2
    assert any("Spellbook" in reason for reason in verdict.rationale)
