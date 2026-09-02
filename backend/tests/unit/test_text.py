"""Card-name normalisation.

These cases are all real cards whose spelling breaks naive comparison.
"""

from __future__ import annotations

import pytest

from app.util.text import front_face_name, is_multiface_name, normalize_name


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("Lightning Bolt", "lightning bolt"),
        ("Lim-Dul's Vault", "lim duls vault"),
        ("Fire // Ice", "fire ice"),
        ("  Sol   Ring  ", "sol ring"),
        ("Jotun Grunt", "jotun grunt"),
        ("Mu Yanling, Sky Dancer", "mu yanling sky dancer"),
        # An apostrophe is dropped, not spaced, so a typist who omits it still matches.
        ("Thorin's Last Stand", "thorins last stand"),
        ("Thorins Last Stand", "thorins last stand"),
    ],
)
def test_normalize_name(raw: str, expected: str) -> None:
    assert normalize_name(raw) == expected


@pytest.mark.parametrize(
    ("a", "b"),
    [
        ("AEther Vial", "Aether Vial"),
        ("Lim-Dûl's Vault", "Lim-Dul's Vault"),
        ("Nazgûl", "Nazgul"),
        ("Séance", "Seance"),
        ("Ramúnap Ruins", "Ramunap Ruins"),
        ("Lightning’s Bolt", "Lightning's Bolt"),
        ("Dusk – Dawn", "Dusk - Dawn"),
    ],
)
def test_spelling_variants_fold_together(a: str, b: str) -> None:
    """Diacritics, ligatures and typographic punctuation must not split a card in two."""
    assert normalize_name(a) == normalize_name(b)


def test_distinct_cards_stay_distinct() -> None:
    """Normalisation must not be so aggressive that different cards collide."""
    assert normalize_name("Lightning Bolt") != normalize_name("Lightning Blast")
    assert normalize_name("Sol Ring") != normalize_name("Sol Talisman")


@pytest.mark.parametrize(
    ("full", "front"),
    [
        ("Delver of Secrets // Insectile Aberration", "Delver of Secrets"),
        ("Bonecrusher Giant // Stomp", "Bonecrusher Giant"),
        ("Fire // Ice", "Fire"),
        ("Lightning Bolt", "Lightning Bolt"),
    ],
)
def test_front_face_name(full: str, front: str) -> None:
    assert front_face_name(full) == front


def test_is_multiface_name() -> None:
    assert is_multiface_name("Fire // Ice")
    assert not is_multiface_name("Lightning Bolt")
    # A name containing a slash but not the separator is not multi-face.
    assert not is_multiface_name("Ach! Hans, Run!")
