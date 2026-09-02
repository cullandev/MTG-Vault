"""Matchup analysis: how decks line up, from their measurable properties.

This is the honest, instant version of "battle these decks": speed against
interaction, wincon kinds against hate pieces, bracket against bracket. It plays
no games -- a rules-accurate simulation needs a full rules engine, which is a
deliberately external concern (see DECISIONS: the app never rewrites the
comprehensive rules). Every verdict lists its reasons.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy.orm import Session as DbSession

from app.models import Deck
from app.services.decks import loader
from app.services.rating.brackets import detect_bracket
from app.services.rating.classify import classify
from app.services.rating.heuristics import score_deck


@dataclass
class DeckProfile:
    """One deck's combat-relevant properties."""

    ref: str
    name: str
    speed: float
    interaction: float
    interaction_density: float
    bracket: int
    wincon_kinds: list[str] = field(default_factory=list)
    hate_pieces: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "ref": self.ref,
            "name": self.name,
            "speed": self.speed,
            "interaction": self.interaction,
            "interaction_density": round(self.interaction_density, 3),
            "bracket": self.bracket,
            "wincon_kinds": self.wincon_kinds,
            "hate_pieces": self.hate_pieces,
        }


def profile_deck(db: DbSession, deck: Deck) -> DeckProfile:
    """Reduce one stored deck to its matchup-relevant numbers."""
    entries = [
        entry for entry in loader.load_entries(db, deck) if entry.board in ("main", "commander")
    ]
    scores = score_deck(entries)
    verdict = detect_bracket(entries, two_card_combos=None)
    deck_size = max(1, sum(entry.quantity for entry in entries))

    removal = 0
    burn = 0
    big_creatures = 0
    creatures = 0
    hate: list[str] = []
    for entry in entries:
        tags = classify(entry.card)
        if tags & {"removal", "mass_removal", "counterspell"}:
            removal += entry.quantity
        if "removal" in tags and "deals" in entry.card.oracle_text.lower():
            burn += entry.quantity
        if entry.card.is_creature:
            creatures += entry.quantity
            if entry.card.cmc >= 5:
                big_creatures += entry.quantity
        if "hate" in tags:
            hate.append(entry.card.name)

    wincons: list[str] = []
    if creatures >= 0.25 * deck_size:
        wincons.append("combat")
    if big_creatures >= 5:
        wincons.append("big creatures")
    if burn >= 0.15 * deck_size:
        wincons.append("burn")
    if verdict.signals["extra_turns"]:
        wincons.append("extra turns")
    if not wincons:
        wincons.append("attrition")

    return DeckProfile(
        ref=f"deck:{deck.id}",
        name=deck.name,
        speed=scores.speed,
        interaction=scores.interaction,
        interaction_density=removal / deck_size,
        bracket=verdict.bracket,
        wincon_kinds=wincons,
        hate_pieces=sorted(hate),
    )


def compare(profiles: list[DeckProfile]) -> dict[str, Any]:
    """Pairwise reads over a pod of profiled decks.

    The model, stated: a faster deck is favoured unless the slower deck's
    interaction outweighs the speed gap -- interaction is what turns speed into
    overextension. Margins are in score points; below half a point is a coin flip.
    """
    pairwise: list[dict[str, Any]] = []
    for i, a in enumerate(profiles):
        for b in profiles[i + 1 :]:
            speed_edge = a.speed - b.speed
            interaction_edge = a.interaction - b.interaction
            margin = 0.6 * speed_edge + 0.4 * interaction_edge
            reasons: list[str] = []
            if abs(speed_edge) >= 1:
                reasons.append(
                    f"{(a if speed_edge > 0 else b).name} is faster by {abs(speed_edge):.1f}"
                )
            if abs(interaction_edge) >= 1:
                reasons.append(
                    f"{(a if interaction_edge > 0 else b).name} interacts more by "
                    f"{abs(interaction_edge):.1f}"
                )
            if not reasons:
                reasons.append("evenly matched on speed and interaction")
            favoured = a.ref if margin > 0.5 else b.ref if margin < -0.5 else None
            pairwise.append(
                {
                    "a": a.ref,
                    "b": b.ref,
                    "favoured": favoured,
                    "margin": round(abs(margin), 2),
                    "reasons": reasons,
                }
            )

    brackets = [profile.bracket for profile in profiles]
    mismatch = bool(brackets) and max(brackets) - min(brackets) >= 2
    notes: list[str] = []
    if mismatch:
        notes.append("bracket spread is two or more: the strongest deck will warp the pod")
    all_hate = [name for profile in profiles for name in profile.hate_pieces]
    if all_hate:
        notes.append("hate pieces in the pod: " + ", ".join(sorted(set(all_hate))))

    return {
        "decks": [profile.as_dict() for profile in profiles],
        "pairwise": pairwise,
        "pod_notes": notes,
        "bracket_mismatch": mismatch,
    }
