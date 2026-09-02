"""Heuristic scores against the six reference decks (TEST-PLAN Phase 5).

Bands are hand-set around what the archetypes deserve; the ordering assertions are
the stronger guard, so a formula tweak that inverts a known ranking fails even if
every deck still lands somewhere plausible.
"""

from __future__ import annotations

import pytest

from app.services.rating.heuristics import HEURISTIC_VERSION, score_deck
from tests.unit.rating import reference_decks as decks

#: deck -> (consistency, speed, interaction, resilience) as (low, high) bands.
#: Justifications live beside the decks in reference_decks.py.
EXPECTED: dict[str, tuple[tuple[float, float], ...]] = {
    "CEDH": ((5.0, 7.5), (9.0, 10.0), (9.0, 10.0), (6.5, 8.5)),
    "PRECON": ((2.5, 4.0), (2.5, 4.0), (3.0, 5.0), (1.0, 2.5)),
    "BURN": ((2.5, 4.0), (6.0, 8.0), (9.0, 10.0), (1.0, 2.0)),
    "CONTROL": ((6.5, 8.5), (5.0, 7.0), (9.0, 10.0), (4.5, 6.5)),
    "PAUPER_MIDRANGE": ((6.5, 8.5), (5.0, 7.0), (8.0, 10.0), (2.0, 4.0)),
    "RAMP_STOMPY": ((2.5, 4.0), (5.0, 7.0), (1.0, 2.0), (1.0, 2.0)),
}


@pytest.mark.parametrize("deck_name", sorted(EXPECTED))
def test_reference_deck_lands_in_its_band(deck_name: str) -> None:
    scores = score_deck(getattr(decks, deck_name))
    bands = EXPECTED[deck_name]
    for axis, (low, high) in zip(
        ("consistency", "speed", "interaction", "resilience"), bands, strict=True
    ):
        value = getattr(scores, axis)
        assert low <= value <= high, f"{deck_name} {axis}={value}, expected {low}..{high}"


def test_known_orderings_hold() -> None:
    """The rankings everyone at the table would agree on."""
    scored = {name: score_deck(getattr(decks, name)) for name in EXPECTED}

    assert scored["CEDH"].speed > scored["PRECON"].speed
    assert scored["CEDH"].consistency > scored["PRECON"].consistency
    assert scored["CEDH"].resilience > scored["BURN"].resilience
    assert scored["BURN"].speed > scored["CONTROL"].speed
    assert scored["CONTROL"].resilience > scored["BURN"].resilience
    assert scored["CONTROL"].interaction > scored["RAMP_STOMPY"].interaction
    # Burn's damage genuinely answers creatures, so it may tie control on the
    # density measure -- but it must never *beat* it.
    assert scored["CONTROL"].interaction >= scored["BURN"].interaction


def test_every_raw_count_is_exposed() -> None:
    """A score is always explainable: the signals carry the counts and components."""
    scores = score_deck(decks.CONTROL)
    for key in ("removal", "counterspell", "draw", "lands", "avg_mv", "components"):
        assert key in scores.signals, key
    assert scores.version == HEURISTIC_VERSION
    payload = scores.as_dict()
    assert payload["heuristic_version"] == HEURISTIC_VERSION


def test_an_empty_deck_scores_the_floor() -> None:
    scores = score_deck([])
    assert scores.speed >= 1.0
    assert scores.interaction == 1.0
