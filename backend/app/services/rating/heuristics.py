"""Heuristic deck scores: consistency, speed, interaction, resilience, 1-10 each.

The formulas are simple, stated, and versioned; every raw count they read is
returned beside the scores, so the UI can always answer "why is this a 4". A change
to any formula bumps :data:`HEURISTIC_VERSION`, and the reference-deck tests assert
*ordering* between known decks as well as absolute bands, so a tweak that inverts
cEDH and a preconstructed deck fails loudly (TEST-PLAN.md Phase 5).
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from app.services.decks.stats import compute_stats
from app.services.rating.classify import classify
from app.services.rules import DeckEntry

HEURISTIC_VERSION = 1

#: Density targets, as shares of deck size: "a deck wants about this much of X".
#: A deck at or above the target scores full marks on that component.
_DRAW_TARGET = 0.10
_RAMP_TARGET = 0.08
_REMOVAL_TARGET = 0.12
_INSTANT_TARGET = 0.06
_RESILIENCE_TARGET = 0.08
_RECURSION_TARGET = 0.04
_TUTOR_FULL_MARKS = 5


@dataclass
class HeuristicScores:
    """The four sub-scores and every count behind them."""

    consistency: float
    speed: float
    interaction: float
    resilience: float
    signals: dict[str, Any]
    version: int = HEURISTIC_VERSION

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API and ``deck_scores``."""
        return {
            "consistency": self.consistency,
            "speed": self.speed,
            "interaction": self.interaction,
            "resilience": self.resilience,
            "signals": self.signals,
            "heuristic_version": self.version,
        }


def score_deck(entries: list[DeckEntry]) -> HeuristicScores:
    """Score the main board plus commanders.

    Components (each 0..1, weighted, then mapped to 1..10):

    * **consistency** = 0.45 land fit + 0.35 draw density + 0.20 tutors.
    * **speed** = 0.40 inverted average MV + 0.35 ramp density + 0.25 cheap share.
    * **interaction** = 0.70 removal density + 0.30 instant-speed density.
    * **resilience** = 0.50 protection density + 0.30 recursion + 0.20 draw.
    """
    counted = [entry for entry in entries if entry.board in ("main", "commander")]
    stats = compute_stats(counted)
    deck_size = max(1, stats.card_count)
    nonland = max(1, deck_size - stats.lands)

    counts = {
        "removal": 0,
        "mass_removal": 0,
        "counterspell": 0,
        "instant_speed_interaction": 0,
        "draw": 0,
        "ramp": 0,
        "tutor": 0,
        "protection": 0,
        "recursion": 0,
        "hate": 0,
        "cheap_nonland": 0,
    }
    for entry in counted:
        tags = classify(entry.card)
        quantity = entry.quantity
        for tag in (
            "removal",
            "mass_removal",
            "counterspell",
            "draw",
            "ramp",
            "tutor",
            "protection",
            "recursion",
            "hate",
        ):
            if tag in tags:
                counts[tag] += quantity
        if "instant_speed" in tags and tags & {"removal", "mass_removal", "counterspell"}:
            counts["instant_speed_interaction"] += quantity
        if not entry.card.is_land and entry.card.cmc <= 2:
            counts["cheap_nonland"] += quantity

    land_fit = 1.0 - min(
        1.0,
        abs(stats.lands + stats.mdfc_lands - stats.recommended_lands) / max(4.0, 0.1 * deck_size),
    )
    draw_score = min(1.0, counts["draw"] / (_DRAW_TARGET * deck_size))
    tutor_score = min(1.0, counts["tutor"] / _TUTOR_FULL_MARKS)
    consistency = _band(0.45 * land_fit + 0.35 * draw_score + 0.20 * tutor_score)

    inverted_mv = min(1.0, max(0.0, (4.5 - stats.avg_mv) / 3.0))
    ramp_score = min(1.0, counts["ramp"] / (_RAMP_TARGET * deck_size))
    cheap_share = min(1.0, counts["cheap_nonland"] / nonland * 2.0)
    speed = _band(0.40 * inverted_mv + 0.35 * ramp_score + 0.25 * cheap_share)

    removal_total = counts["removal"] + counts["mass_removal"] + counts["counterspell"]
    removal_score = min(1.0, removal_total / (_REMOVAL_TARGET * deck_size))
    instant_score = min(1.0, counts["instant_speed_interaction"] / (_INSTANT_TARGET * deck_size))
    interaction = _band(0.70 * removal_score + 0.30 * instant_score)

    tough_score = min(
        1.0, (counts["protection"] + counts["recursion"]) / (_RESILIENCE_TARGET * deck_size)
    )
    recursion_score = min(1.0, counts["recursion"] / (_RECURSION_TARGET * deck_size))
    resilience = _band(0.50 * tough_score + 0.30 * recursion_score + 0.20 * draw_score)

    signals = {
        **counts,
        "deck_size": deck_size,
        "lands": stats.lands,
        "mdfc_lands": stats.mdfc_lands,
        "recommended_lands": stats.recommended_lands,
        "avg_mv": round(stats.avg_mv, 2),
        "components": {
            "land_fit": round(land_fit, 3),
            "draw_score": round(draw_score, 3),
            "tutor_score": round(tutor_score, 3),
            "inverted_mv": round(inverted_mv, 3),
            "ramp_score": round(ramp_score, 3),
            "cheap_share": round(cheap_share, 3),
            "removal_score": round(removal_score, 3),
            "instant_score": round(instant_score, 3),
            "tough_score": round(tough_score, 3),
            "recursion_score": round(recursion_score, 3),
        },
    }
    return HeuristicScores(
        consistency=consistency,
        speed=speed,
        interaction=interaction,
        resilience=resilience,
        signals=signals,
    )


def _band(component: float) -> float:
    """Map a 0..1 component sum onto the 1..10 display band, one decimal."""
    return round(1.0 + 9.0 * max(0.0, min(1.0, component)), 1)
