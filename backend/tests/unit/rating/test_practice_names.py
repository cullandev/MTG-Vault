"""Deck naming for the practice table, and the sidecar's file-name contract.

The two must agree. Forge relocates any deck whose file name disagrees with the
``Name=`` in its metadata -- out of the folder that is read and into the decks
root, where nothing sees it. Seven of every twelve pushed decks disappeared
that way while every call reported success, so both halves are pinned here.
It still matters: the bridge loads decks from Forge's profile folder by name.
"""

from __future__ import annotations

import importlib.util
import sys
from pathlib import Path
from types import ModuleType

import pytest

from app.api.practice import _retitled, practice_name
from app.models import Deck, OracleCard
from app.services.rating.battles import forge_card_name


def _sidecar() -> ModuleType:
    """Load the sidecar shim, which lives outside the backend package."""
    path = Path(__file__).resolve().parents[3].parent / "docker" / "forge" / "server.py"
    spec = importlib.util.spec_from_file_location("forge_sidecar", path)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    sys.modules["forge_sidecar"] = module
    spec.loader.exec_module(module)
    return module


def deck(name: str, deck_id: int = 1) -> Deck:
    return Deck(id=deck_id, name=name, format="casual", source="synergy")


# -- the name a person reads ------------------------------------------------


def test_the_picker_name_carries_no_id() -> None:
    """The [#id] suffix is for a log parser, not for someone choosing a deck.

    It also churns: the meta job renumbers its decks every week, which both
    fills the picker with near-identical names and breaks Forge's own memory
    of the deck played last time.
    """
    assert practice_name(deck("treasure & artifacts (suggested 60)", 86), set()) == (
        "treasure & artifacts (suggested 60)"
    )


def test_a_double_faced_commander_names_the_deck_once() -> None:
    """ "Ral, Monsoon Mage // Ral, Leyline Prodigy" is one commander, not two."""
    assert practice_name(deck("[Meta] Ral, Monsoon Mage // Ral, Leyline Prodigy", 84), set()) == (
        "[Meta] Ral, Monsoon Mage"
    )


def test_a_partner_pair_reads_as_a_pair() -> None:
    assert practice_name(deck("[Meta 60] Rograkh / Thrasios", 82), set()) == (
        "[Meta 60] Rograkh - Thrasios"
    )


def test_a_slash_inside_a_word_just_closes_up() -> None:
    """Forge would file this as "+1_+1 counters", which reads as damage."""
    assert practice_name(deck("+1/+1 counters (suggested 60)", 45), set()) == (
        "+1+1 counters (suggested 60)"
    )


def test_the_picker_name_survives_forges_own_filing_rule() -> None:
    """The two rules must agree, or Forge relocates the deck out of the picker."""
    safe = _sidecar().forge_safe_name
    for raw in (
        "[Meta] Ral, Monsoon Mage // Ral, Leyline Prodigy",
        "+1/+1 counters (suggested 60)",
        "[Meta 60] Kraum, Ludevic's Opus / Tymna the Weaver",
        "treasure & artifacts (suggested 60)",
    ):
        picked = practice_name(deck(raw), set())
        assert safe(picked) == picked, f"{picked!r} would be refiled as {safe(picked)!r}"


def test_a_genuine_collision_gets_the_id_back() -> None:
    taken = {"go wide".casefold()}
    assert practice_name(deck("go wide", 47), taken) == "go wide [#47]"


def test_collisions_are_case_insensitive() -> None:
    """Two files differing only in case collide on a Windows bind mount."""
    assert practice_name(deck("Go Wide", 47), {"go wide"}) == "Go Wide [#47]"


def test_a_nameless_deck_still_gets_a_name() -> None:
    assert practice_name(deck("   ", 5), set()) == "Deck"


def test_retitling_replaces_the_metadata_name() -> None:
    dck = "[metadata]\nName=old [#3]\n[Main]\n4 Shock\n"
    assert "Name=go wide" in _retitled(dck, "go wide")
    assert "old [#3]" not in _retitled(dck, "go wide")


# -- the name Forge would file it under -------------------------------------


@pytest.mark.parametrize(
    ("given", "expected"),
    [
        # Forge maps the path separator; everything else in a deck name survives.
        ("Rograkh / Thrasios", "Rograkh _ Thrasios"),
        ("treasure & artifacts (suggested 60)", "treasure & artifacts (suggested 60)"),
        ("Kraum, Ludevic's Opus", "Kraum, Ludevic's Opus"),
        ("+1/+1 counters", "+1_+1 counters"),
        # Windows refuses these on the bind-mounted volume.
        ('Deck: "The Reckoning"?', "Deck_ _The Reckoning__"),
        ("back\\slash", "back_slash"),
        # Whitespace is collapsed: Forge normalises it, and a name that only
        # matches before normalisation is exactly the mismatch that relocates.
        ("go   wide\tnow", "go wide now"),
        ("  padded  ", "padded"),
        ("", "deck"),
        ("///", "___"),
    ],
)
def test_the_sidecar_files_decks_under_forges_own_name(given: str, expected: str) -> None:
    assert _sidecar().forge_safe_name(given) == expected


def test_a_filed_name_is_stable_under_refiling() -> None:
    """Applying the rule twice must not move the name, or the deck relocates."""
    safe = _sidecar().forge_safe_name
    for name in ("Rograkh / Thrasios", "+1/+1 counters", "  odd   spacing  ", "a.b."):
        assert safe(safe(name)) == safe(name)


def test_the_sidecar_retitles_the_deck_to_match_the_file() -> None:
    module = _sidecar()
    out = module._retitle("[metadata]\nName=mismatched\n[Main]\n1 Island\n", "agreed")
    assert "Name=agreed" in out
    assert "mismatched" not in out


def test_retitling_a_deck_with_no_metadata_block_adds_one() -> None:
    module = _sidecar()
    assert module._retitle("[Main]\n1 Island\n", "agreed").startswith("[metadata]\nName=agreed")


# -- the name Forge's card scripts answer to --------------------------------


def _oracle(name: str, layout: str) -> OracleCard:
    front = name.split("//")[0].strip()
    return OracleCard(oracle_id="x", name=name, name_front=front, layout=layout)


def test_an_adventure_card_is_sent_by_its_creature_name() -> None:
    """One card with a spell half, and Forge scripts it under the creature.

    Sending "Bilbo Baggins, Burglar // Take a Glance" made Forge log "an
    unsupported card was requested" and play the deck WITHOUT it -- every
    battle involving one was fought a card short, silently. All 13 Adventure
    cards in the vault miss on the full name and hit on the front name against
    Forge's 34,532 scripted names.
    """
    card = _oracle("Bilbo Baggins, Burglar // Take a Glance", "adventure")
    assert forge_card_name(card) == "Bilbo Baggins, Burglar"


def test_a_split_card_keeps_both_halves() -> None:
    """Forge really does script "Fire // Ice"; only some layouts fold."""
    assert forge_card_name(_oracle("Fire // Ice", "split")) == "Fire // Ice"


@pytest.mark.parametrize("layout", ["transform", "modal_dfc", "flip", "meld", "reversible_card"])
def test_the_other_folding_layouts_are_unchanged(layout: str) -> None:
    assert forge_card_name(_oracle("Front Face // Back Face", layout)) == "Front Face"


def test_an_ordinary_card_is_sent_verbatim() -> None:
    assert forge_card_name(_oracle("Lightning Bolt", "normal")) == "Lightning Bolt"
