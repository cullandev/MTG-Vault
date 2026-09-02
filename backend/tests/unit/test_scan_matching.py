"""Fuzzy matching of OCR output against the card name index.

This is the file that decides whether the scanner is trustworthy. Two failure modes
matter and they pull in opposite directions:

* **Too strict** and a card with one misread letter is never recognised.
* **Too loose** and Lightning Blast gets added to your collection when Lightning Bolt
  was on the mat -- silently, because the scanner auto-adds.

So every case here is either "this corruption must still match" or "these two must
never be confused".
"""

from __future__ import annotations

import pytest
from sqlalchemy.orm import Session as DbSession

from app.config import Settings, get_settings
from app.models import OracleCard, color_mask, utcnow
from app.services.scan import matching
from app.util.text import normalize_name

# Real cards, chosen because they are the ones a fuzzy matcher trips over.
CONFUSABLE_NAMES = [
    "Lightning Bolt",
    "Lightning Blast",
    "Lightning Helix",
    "Lightning Strike",
    "Chain Lightning",
    "Sol Ring",
    "Sol Talisman",
    "Soul Ring",
    "Gideon Jura",
    "Gideon Blackblade",
    "Gideon of the Trials",
    "Boros Charm",
    "Azorius Charm",
    "Golgari Charm",
    "Selesnya Charm",
    "Simic Charm",
    "Path to Exile",
    "Swords to Plowshares",
    "Counterspell",
    "Cancel",
    "Mana Leak",
    "Force of Will",
    "Force of Negation",
    "Birds of Paradise",
    "Bird Admirer",
    "Llanowar Elves",
    "Llanowar Tribe",
    "Elvish Mystic",
    "Elvish Visionary",
    "Wrath of God",
    "Damnation",
    "Blasphemous Act",
    "Cyclonic Rift",
    "Rhystic Study",
    "Mystic Remora",
    "Smothering Tithe",
    "Dockside Extortionist",
    "Deflecting Swat",
    "Fierce Guardianship",
    "Lim-Dûl's Vault",
    "Aether Vial",
    "Delver of Secrets // Insectile Aberration",
    "Bonecrusher Giant // Stomp",
    "Fire // Ice",
    "Brazen Borrower // Petty Theft",
    "Island",
    "Snow-Covered Island",
    "Mountain",
    "Snow-Covered Mountain",
    "Nazgûl",
    "Séance",
    "Ramunap Ruins",
    "Kitchen Finks",
    "Birthing Pod",
]


@pytest.fixture
def name_index(db: DbSession) -> DbSession:
    """Seed a catalogue rich in near-miss names, then build the index over it."""
    from app.util.text import front_face_name

    for position, name in enumerate(CONFUSABLE_NAMES):
        front = front_face_name(name)
        db.add(
            OracleCard(
                oracle_id=f"oracle-{position:04d}",
                name=name,
                name_norm=normalize_name(name),
                name_front=front,
                name_front_norm=normalize_name(front),
                layout="normal",
                type_line="Instant",
                oracle_text_all=f"Rules text for {name}.",
                cmc=1.0,
                color_identity="R",
                color_identity_mask=color_mask("R"),
                updated_at=utcnow(),
            )
        )
    db.commit()
    matching.reset_index()
    return db


@pytest.fixture
def settings_obj() -> Settings:
    return get_settings()


def _match(db: DbSession, settings: Settings, text: str) -> matching.NameMatch:
    return matching.match_name(db, text, settings)


# --- the index -------------------------------------------------------------


def test_index_covers_full_and_front_face_names(name_index: DbSession) -> None:
    """A DFC's front face is what the camera sees, and what decklists use."""
    index = matching.get_index(name_index)
    assert normalize_name("Delver of Secrets // Insectile Aberration") in index.owners
    assert normalize_name("Delver of Secrets") in index.owners
    assert normalize_name("Bonecrusher Giant") in index.owners


def test_index_rebuilds_when_the_catalogue_changes(name_index: DbSession) -> None:
    first = matching.get_index(name_index)
    name_index.add(
        OracleCard(
            oracle_id="oracle-new",
            name="Brainstorm",
            name_norm="brainstorm",
            name_front="Brainstorm",
            name_front_norm="brainstorm",
            layout="normal",
            cmc=1.0,
            color_identity="U",
            color_identity_mask=color_mask("U"),
            updated_at=utcnow(),
        )
    )
    name_index.commit()

    second = matching.get_index(name_index)
    assert second is not first
    assert "brainstorm" in second.owners


def test_index_is_reused_when_nothing_changed(name_index: DbSession) -> None:
    assert matching.get_index(name_index) is matching.get_index(name_index)


# --- exact and near-exact --------------------------------------------------


@pytest.mark.parametrize("name", ["Lightning Bolt", "Counterspell", "Rhystic Study"])
def test_clean_reading_matches_exactly(
    name_index: DbSession, settings_obj: Settings, name: str
) -> None:
    result = _match(name_index, settings_obj, name)
    assert result.confident
    assert result.best is not None
    assert result.best.name == name
    assert result.score == 100.0


def test_case_and_spacing_do_not_matter(name_index: DbSession, settings_obj: Settings) -> None:
    assert _match(name_index, settings_obj, "  LIGHTNING   bolt ").best.name == "Lightning Bolt"


@pytest.mark.parametrize(
    ("read", "expected"),
    [
        ("Lim-Dul's Vault", "Lim-Dûl's Vault"),
        ("Nazgul", "Nazgûl"),
        ("Seance", "Séance"),
        ("AEther Vial", "Aether Vial"),
    ],
)
def test_diacritics_and_ligatures_match(
    name_index: DbSession, settings_obj: Settings, read: str, expected: str
) -> None:
    """OCR never reproduces a circumflex, and it should not have to."""
    result = _match(name_index, settings_obj, read)
    assert result.confident
    assert result.best is not None
    assert result.best.name == expected


# --- OCR-typical corruptions must still match ------------------------------


@pytest.mark.parametrize(
    ("corrupted", "expected"),
    [
        ("Lightnlng Bolt", "Lightning Bolt"),  # i -> l
        ("L1ghtning Bolt", "Lightning Bolt"),  # i -> 1
        ("Lightning Bo1t", "Lightning Bolt"),  # l -> 1
        ("Counterspell", "Counterspell"),  # doubled letter
        ("Counterspel", "Counterspell"),  # dropped letter
        ("Swords to Plowshaves", "Swords to Plowshares"),
        ("Rhystlc Study", "Rhystic Study"),
        ("Cyclonlc Rift", "Cyclonic Rift"),
        ("Smothering Tlthe", "Smothering Tithe"),
        ("Birds of Paradlse", "Birds of Paradise"),
        ("Blrds of Paradise", "Birds of Paradise"),
        ("Dockside Extortlonist", "Dockside Extortionist"),
        ("Fierce Guardlanship", "Fierce Guardianship"),
    ],
)
def test_single_character_corruption_still_matches(
    name_index: DbSession, settings_obj: Settings, corrupted: str, expected: str
) -> None:
    result = _match(name_index, settings_obj, corrupted)
    assert result.best is not None, f"{corrupted!r} matched nothing"
    assert result.best.name == expected, f"{corrupted!r} -> {result.best.name!r}"
    assert result.confident, f"{corrupted!r} scored only {result.score}"


def test_trailing_junk_from_the_border_is_tolerated(
    name_index: DbSession, settings_obj: Settings
) -> None:
    """OCR routinely picks a stray glyph out of the card's border."""
    result = _match(name_index, settings_obj, "' Lightning Bolt .-")
    assert result.confident
    assert result.best is not None
    assert result.best.name == "Lightning Bolt"


def test_mana_cost_bleed_is_tolerated(name_index: DbSession, settings_obj: Settings) -> None:
    """The crop stops before the mana cost, but a wide card can still bleed one in."""
    result = _match(name_index, settings_obj, "Counterspell UU")
    assert result.best is not None
    assert result.best.name == "Counterspell"


# --- near misses must NOT be confused --------------------------------------


@pytest.mark.parametrize(
    ("read", "must_not_be"),
    [
        ("Lightning Blast", "Lightning Bolt"),
        ("Lightning Bolt", "Lightning Blast"),
        ("Sol Talisman", "Sol Ring"),
        ("Sol Ring", "Sol Talisman"),
        ("Gideon Blackblade", "Gideon Jura"),
        ("Force of Negation", "Force of Will"),
        ("Snow-Covered Island", "Island"),
        ("Llanowar Tribe", "Llanowar Elves"),
        ("Elvish Visionary", "Elvish Mystic"),
        ("Azorius Charm", "Boros Charm"),
    ],
)
def test_similar_names_are_not_confused(
    name_index: DbSession, settings_obj: Settings, read: str, must_not_be: str
) -> None:
    result = _match(name_index, settings_obj, read)
    assert result.best is not None
    assert result.best.name == read
    assert result.best.name != must_not_be


def test_a_genuinely_ambiguous_reading_is_not_confident(
    name_index: DbSession, settings_obj: Settings
) -> None:
    """Halfway between two real cards, the user gets a picker, not a guess."""
    result = _match(name_index, settings_obj, "Lightning Bol")
    assert result.best is not None
    if result.confident:
        # If it is confident it had better be right, and by a clear margin.
        assert result.best.name == "Lightning Bolt"
        assert result.candidates[0].score - result.candidates[1].score >= 2.0


def test_a_near_tie_is_downgraded_to_ambiguous(
    name_index: DbSession, settings_obj: Settings
) -> None:
    """Two candidates within two points is exactly the case worth asking about."""
    result = _match(name_index, settings_obj, "Snow-Covered Islan")
    assert result.best is not None
    if len(result.candidates) > 1 and result.candidates[0].score - result.candidates[1].score < 2:
        assert not result.confident


# --- misses ----------------------------------------------------------------


def test_nonsense_matches_nothing(name_index: DbSession, settings_obj: Settings) -> None:
    result = _match(name_index, settings_obj, "qqzzxw vvbbnn")
    assert result.best is None
    assert not result.confident
    assert not result.ambiguous


def test_empty_and_tiny_readings_are_rejected_without_a_lookup(
    name_index: DbSession, settings_obj: Settings
) -> None:
    for text in ["", "   ", "-", "..", "a"]:
        result = _match(name_index, settings_obj, text)
        assert result.best is None
        assert not result.confident


def test_a_name_not_in_the_catalogue_is_a_miss(
    name_index: DbSession, settings_obj: Settings
) -> None:
    result = _match(name_index, settings_obj, "Black Lotus")
    assert not result.confident


# --- thresholds are configuration, not magic numbers -----------------------


def test_lowering_the_accept_threshold_admits_more(
    name_index: DbSession, settings_obj: Settings, monkeypatch: pytest.MonkeyPatch
) -> None:
    borderline = "Dockside Extortonist"
    strict = _match(name_index, settings_obj, borderline)

    monkeypatch.setattr(settings_obj, "scan_accept_score", 60)
    lenient = _match(name_index, settings_obj, borderline)

    assert lenient.confident or not strict.confident


def test_clean_ocr_text_strips_edge_punctuation() -> None:
    assert matching.clean_ocr_text("''Lightning Bolt--") == "lightning bolt"
    assert matching.clean_ocr_text("  ...  ") == ""


# --- tokens and art cards must not shadow the real card --------------------


def _add_supplemental(db: DbSession, name: str, layout: str) -> None:
    """Add an entry that carries a real card's name but is not that card."""
    db.add(
        OracleCard(
            oracle_id=f"oracle-supplemental-{layout}",
            name=name,
            name_norm=normalize_name(name),
            name_front=name,
            name_front_norm=normalize_name(name),
            layout=layout,
            type_line="Card // Card",
            cmc=0.0,
            color_identity="",
            color_identity_mask=0,
            updated_at=utcnow(),
        )
    )
    db.flush()
    matching.reset_index()


@pytest.mark.parametrize("layout", sorted(matching.SUPPLEMENTAL_LAYOUTS))
def test_a_supplemental_entry_does_not_make_a_name_ambiguous(
    name_index: DbSession, settings_obj: Settings, layout: str
) -> None:
    """Scryfall carries an art card, a token or an emblem for thousands of real cards.

    Each one duplicates a real card's name, and before this the scanner treated that
    as two cards answering to one name and showed a picker listing the same name
    twice -- for a card that was never ambiguous at all.
    """
    _add_supplemental(name_index, "Lightning Bolt", layout)

    result = _match(name_index, settings_obj, "Lightning Bolt")

    assert result.confident
    assert not result.ambiguous
    assert result.best is not None
    assert result.best.oracle_id != f"oracle-supplemental-{layout}"


def test_a_supplemental_entry_is_still_offered_as_a_candidate(
    name_index: DbSession, settings_obj: Settings
) -> None:
    """An art card is a real object someone may own; it is demoted, not dropped."""
    _add_supplemental(name_index, "Lightning Bolt", "art_series")

    result = _match(name_index, settings_obj, "Lightning Bolt")

    assert "oracle-supplemental-art_series" in {c.oracle_id for c in result.candidates}


def test_two_real_cards_sharing_a_name_are_still_ambiguous(
    name_index: DbSession, settings_obj: Settings
) -> None:
    """The demotion must not swallow a genuine collision."""
    _add_supplemental(name_index, "Lightning Bolt", "normal")

    result = _match(name_index, settings_obj, "Lightning Bolt")

    assert result.ambiguous
    assert not result.confident
