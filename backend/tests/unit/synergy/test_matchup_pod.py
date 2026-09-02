"""Matchup reads over a pod (TEST-PLAN Phase 8, last unit block)."""

from __future__ import annotations

from app.services.rating.matchup import DeckProfile, compare


def _profile(name: str, *, speed: float, interaction: float, bracket: int) -> DeckProfile:
    return DeckProfile(
        ref=f"deck:{name}",
        name=name,
        speed=speed,
        interaction=interaction,
        interaction_density=interaction / 10,
        bracket=bracket,
        wincon_kinds=["combat"],
        hate_pieces=[],
    )


def test_fast_combo_beats_slow_durdle_with_cited_reasons() -> None:
    fast = _profile("Turbo", speed=9.5, interaction=6.0, bracket=4)
    durdle = _profile("Durdle", speed=3.0, interaction=2.0, bracket=4)
    result = compare([fast, durdle])
    (pair,) = result["pairwise"]
    assert pair["favoured"] == "deck:Turbo"
    assert any("faster" in reason for reason in pair["reasons"])


def test_a_mixed_bracket_pod_raises_the_mismatch_flag() -> None:
    pod = [
        _profile("Precon", speed=3.0, interaction=3.0, bracket=2),
        _profile("Upgraded", speed=5.0, interaction=5.0, bracket=3),
        _profile("Tuned", speed=7.0, interaction=7.0, bracket=4),
        _profile("cEDH", speed=9.9, interaction=9.9, bracket=5),
    ]
    result = compare(pod)
    assert result["bracket_mismatch"] is True
    assert len(result["pairwise"]) == 6
    assert any("bracket spread" in note for note in result["pod_notes"])


def test_matched_brackets_raise_no_flag() -> None:
    pod = [
        _profile("A", speed=5.0, interaction=5.0, bracket=3),
        _profile("B", speed=5.2, interaction=4.8, bracket=3),
    ]
    result = compare(pod)
    assert result["bracket_mismatch"] is False
