"""Who the practice table seats across from you when nobody is named.

The game that prompted this: twelve turns against a "[Meta 60]" deck that
played lands and one enchantment. All three meta decks are cEDH commander
lists cut to sixty, and Forge's AI cannot play them. The engine was fine; the
default opponent was the problem.
"""

from __future__ import annotations

from app.api.practice import choose_opponent
from app.models import Deck


def _deck(
    id_: int, name: str, *, fmt: str = "casual", source: str = "user", built: bool = True
) -> Deck:
    deck = Deck(name=name, format=fmt, source=source, is_built=built)
    deck.id = id_
    return deck


def test_prefers_a_real_deck_to_a_meta_cut() -> None:
    mine = _deck(1, "Thorin's Company")
    meta = _deck(3, "[Meta 60] Rograkh / Thrasios", source="gauntlet_meta")
    theirs = _deck(2, "Goblin-town Horde")
    # Newest-first ordering puts the meta deck ahead; the choice still skips it.
    assert choose_opponent(mine, [meta, theirs]) is theirs


def test_falls_back_to_a_meta_cut_when_nothing_else_fits() -> None:
    mine = _deck(1, "Thorin's Company")
    meta = _deck(3, "[Meta 60] Kinnan", source="gauntlet_meta")
    assert choose_opponent(mine, [meta]) is meta


def test_never_seats_you_against_yourself() -> None:
    mine = _deck(1, "Thorin's Company")
    assert choose_opponent(mine, [mine]) is None


def test_matches_the_format_family() -> None:
    # A Commander list against a 60-card deck is a 100-card deck with no
    # commander: nonsense, and what the first watched game turned out to be.
    mine = _deck(1, "Thorin's Company")
    commander = _deck(4, "The Elvenking's Court", fmt="casual_commander")
    assert choose_opponent(mine, [commander]) is None
    assert choose_opponent(commander, [mine]) is None


def test_plays_decks_that_are_not_sleeved() -> None:
    # "Built" means physically assembled from owned cards. Forge does not
    # need the cards in a box, and requiring it left one opponent in the vault.
    mine = _deck(1, "Thorin's Company")
    unsleeved = _deck(5, "Not yet sleeved", built=False)
    assert choose_opponent(mine, [unsleeved]) is unsleeved


def test_a_named_opponent_still_has_to_fit() -> None:
    # The route passes a named deck through the same rule, so naming a
    # Commander deck against a 60-card one is refused rather than played.
    mine = _deck(1, "Thorin's Company")
    named = _deck(6, "Wrong format", fmt="casual_commander")
    assert choose_opponent(mine, [named]) is None
