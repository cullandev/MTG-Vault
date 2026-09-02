"""EDHREC and Spellbook parsing against saved fixtures, plus the failure paths.

No test here performs network I/O (TEST-PLAN section 0); the clients' fetch
machinery is exercised separately through the service-level failure tests.
"""

from __future__ import annotations

import json

import pytest

from app.clients.base import SourceResponseError
from app.clients.edhrec import parse_commander_page, slugify
from app.clients.spellbook import parse_find_my_combos
from tests.conftest import FIXTURES


def _load(path: str) -> dict:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


# --- EDHREC ----------------------------------------------------------------


def test_edhrec_page_parses_to_kept_lists() -> None:
    page = parse_commander_page(_load("edhrec/commander_page.json"))
    headers = [header for header, _cards in page.lists]
    assert headers == ["High Synergy Cards", "Top Cards"]  # "New Cards" is skipped
    assert page.themes == ["+1/+1 Counters", "Superfriends"]

    synergy_cards = dict(page.lists)["High Synergy Cards"]
    assert synergy_cards[0].name == "Deepglow Skate"
    assert synergy_cards[0].inclusion_pct == 84.0
    assert synergy_cards[0].synergy == 0.61


def test_edhrec_zero_potential_decks_does_not_divide() -> None:
    page = parse_commander_page(_load("edhrec/commander_page.json"))
    wonder = next(
        card for _header, cards in page.lists for card in cards if card.name == "Nameless Wonder"
    )
    assert wonder.inclusion_pct == 0.0


def test_edhrec_malformed_page_raises() -> None:
    """An empty page is what a format change looks like; never an empty answer."""
    with pytest.raises(SourceResponseError):
        parse_commander_page(_load("edhrec/malformed.json"))


@pytest.mark.parametrize(
    ("name", "slug"),
    [
        ("Atraxa, Praetors' Voice", "atraxa-praetors-voice"),
        ("Bruna, the Fading Light", "bruna-the-fading-light"),
        ("Lim-Dûl's Vault", "lim-duls-vault"),
        ("Bruna, the Fading Light // Brisela", "bruna-the-fading-light"),
    ],
)
def test_edhrec_slugs(name: str, slug: str) -> None:
    assert slugify(name) == slug


# --- Spellbook -------------------------------------------------------------


def test_spellbook_combos_parse() -> None:
    search = parse_find_my_combos(_load("spellbook/find_my_combos.json"))
    assert len(search.included) == 1
    combo = search.included[0]
    assert combo.combo_id == "450-1658"
    assert combo.card_names == ["Basalt Monolith", "Rings of Brighthearth"]
    assert combo.result_text == "Infinite colorless mana"

    assert len(search.almost_included) == 1
    assert search.almost_included[0].colors == "WU"


def test_spellbook_malformed_body_raises() -> None:
    with pytest.raises(SourceResponseError):
        parse_find_my_combos(_load("spellbook/malformed.json"))
