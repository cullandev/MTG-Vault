"""Goldfish simulation: London mulligan behaviour and determinism."""

from __future__ import annotations

from app.services.decks import goldfish


def _library(lands: int, spells: int) -> list[bool]:
    return [True] * lands + [False] * spells


def test_a_forty_land_deck_keeps_seven_almost_always() -> None:
    """TEST-PLAN Phase 4: a 40-land deck keeps a 7-card hand ~always.

    The only mulligans are all-land hands (~4.8% hypergeometric) and one-or-no-land
    hands (~0.4%), so well over nine in ten hands keep at seven.
    """
    result = goldfish.run_goldfish(_library(40, 20), hands=2000, turns=5, seed=7)
    assert result.kept_hand_sizes.get("7", 0) / result.hands > 0.9


def test_statistics_are_stable_across_runs_with_the_same_seed() -> None:
    """TEST-PLAN section 0: fixed seeds, identical results."""
    first = goldfish.run_goldfish(_library(24, 36), hands=500, turns=7, seed=42)
    second = goldfish.run_goldfish(_library(24, 36), hands=500, turns=7, seed=42)
    assert first.as_dict() == second.as_dict()


def test_different_seeds_differ() -> None:
    first = goldfish.run_goldfish(_library(24, 36), hands=500, turns=7, seed=1)
    second = goldfish.run_goldfish(_library(24, 36), hands=500, turns=7, seed=2)
    assert first.as_dict() != second.as_dict()


def test_land_drops_track_the_land_count() -> None:
    """A 40-land deck hits nearly every drop; a 12-land deck misses plenty."""
    heavy = goldfish.run_goldfish(_library(40, 20), hands=1000, turns=5, seed=3)
    light = goldfish.run_goldfish(_library(12, 48), hands=1000, turns=5, seed=3)
    assert heavy.land_drop_rate[0] > 0.95
    assert heavy.land_drop_rate[4] > light.land_drop_rate[4]


def test_a_spell_only_deck_mulligans_to_the_floor() -> None:
    """With no lands, the keep rule fails until desperation takes over."""
    result = goldfish.run_goldfish(_library(0, 60), hands=100, turns=3, seed=5)
    kept = {int(size) for size in result.kept_hand_sizes}
    assert max(kept) < 7
    assert result.land_drop_rate[0] == 0.0


def test_the_keep_rule_is_as_documented() -> None:
    """Two lands and a spell keeps; zero lands ships; the floor keeps anything."""
    assert goldfish.keep_decision(2, 7, mulligans=0)
    assert not goldfish.keep_decision(0, 7, mulligans=0)
    assert not goldfish.keep_decision(7, 7, mulligans=0)  # no spells
    assert goldfish.keep_decision(0, 7, mulligans=2)  # requirement decays
    assert goldfish.keep_decision(0, 4, mulligans=3)  # the floor


def test_london_bottoming_returns_the_right_hand_size() -> None:
    result = goldfish.run_goldfish(_library(0, 60), hands=50, turns=1, seed=9)
    for size, count in result.kept_hand_sizes.items():
        assert 4 <= int(size) <= 7
        assert count > 0
