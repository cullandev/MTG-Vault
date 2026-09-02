"""Deck statistics: the curve's edge cases, hybrid pips, and the land count."""

from __future__ import annotations

from app.services.decks import stats
from tests.unit.rules.conftest import card, entry


def test_x_spells_sit_in_their_printed_bucket_and_are_counted() -> None:
    """CR 203.3b: X is zero everywhere but the stack, so Fireball is MV 1."""
    fireball = card("Fireball", type_line="Sorcery", mana_cost="{X}{R}", cmc=1)
    result = stats.compute_stats([entry(fireball, 4)])
    assert result.curve["1"] == 4
    assert result.x_spells == 4


def test_mdfc_lands_count_in_the_curve_and_separately() -> None:
    """Agadeem's Awakening is a spell in the curve *and* an extra land in waiting."""
    awakening = card(
        "Agadeem's Awakening // Agadeem, the Undercrypt",
        type_line="Sorcery // Land",
        mana_cost="{X}{B}{B}{B}",
        cmc=3,
        layout="modal_dfc",
    )
    plains = card("Plains", type_line="Basic Land — Plains")
    result = stats.compute_stats([entry(awakening, 2), entry(plains, 20)])
    assert result.curve["3"] == 2
    assert result.mdfc_lands == 2
    assert result.lands == 20


def test_hybrid_pips_count_as_both_colours() -> None:
    """Boros Charm's {R/W}{R/W} adds two red and two white pips."""
    charm = card("Boros Charm", type_line="Instant", mana_cost="{R/W}{R/W}", cmc=2)
    result = stats.compute_stats([entry(charm, 1)])
    assert result.pips["R"] == 2
    assert result.pips["W"] == 2


def test_phyrexian_pips_count_their_colour() -> None:
    """Gitaxian Probe's {U/P} is one blue pip."""
    probe = card("Gitaxian Probe", type_line="Sorcery", mana_cost="{U/P}", cmc=1)
    result = stats.compute_stats([entry(probe, 4)])
    assert result.pips["U"] == 4


def test_average_mana_value_excludes_lands() -> None:
    """Twenty lands must not drag a 3.0-average deck toward zero."""
    spell = card("Divination", type_line="Sorcery", mana_cost="{2}{U}", cmc=3)
    island = card("Island", type_line="Basic Land — Island")
    result = stats.compute_stats([entry(spell, 10), entry(island, 20)])
    assert result.avg_mv == 3.0


def test_seven_plus_bucket_absorbs_the_top() -> None:
    emrakul = card("Emrakul, the Aeons Torn", type_line="Legendary Creature — Eldrazi", cmc=15)
    result = stats.compute_stats([entry(emrakul, 2)])
    assert result.curve["7+"] == 2


def test_type_breakdown_counts_each_card_once() -> None:
    """An artifact creature counts as a creature, its first matching type."""
    sentinel = card("Esper Sentinel", type_line="Artifact Creature — Human Soldier", cmc=1)
    result = stats.compute_stats([entry(sentinel, 3)])
    assert result.types == {"Creature": 3}


def test_land_recommendation_follows_the_documented_formula() -> None:
    """Karsten 2017: 19.59 + 1.90 * avg MV at 60 cards, scaled by size."""
    assert stats.recommend_lands(deck_size=60, avg_mv=2.0, mdfc_lands=0) == 23
    assert stats.recommend_lands(deck_size=100, avg_mv=2.5, mdfc_lands=0) == 41
    assert stats.recommend_lands(deck_size=60, avg_mv=2.0, mdfc_lands=4) == 20
    assert stats.recommend_lands(deck_size=0, avg_mv=0.0, mdfc_lands=0) == 0


def test_side_and_maybe_boards_are_not_counted() -> None:
    spell = card("Divination", type_line="Sorcery", mana_cost="{2}{U}", cmc=3)
    result = stats.compute_stats(
        [entry(spell, 4), entry(spell, 4, board="side"), entry(spell, 4, board="maybe")]
    )
    assert result.card_count == 4
