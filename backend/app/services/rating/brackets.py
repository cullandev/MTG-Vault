"""Commander Bracket detection (1-5), every verdict citing its signals.

The signal sources follow ARCHITECTURE.md section 6: Game Changers are Scryfall's
``game_changer`` boolean on the oracle row -- never a hand-maintained list; extra
turns, mass land denial and tutors come from the patterns in
``app/data/bracket_patterns.yaml``; two-card combos come from Commander Spellbook
rows the caller passes in. The bracket rules encode WotC's published system:

* **1 - Exhibition / 2 - Core**: no Game Changers, no mass land denial, no extra
  turns, no two-card infinite combos; a handful of tutors at most. (1 and 2 differ
  by *intent*, which a card list cannot see; the detector reports 2, the floor a
  card list can prove.)
* **3 - Upgraded**: up to three Game Changers, still no mass land denial; combos
  and extra turns push past it.
* **4 - Optimized**: anything goes.
* **5 - cEDH**: 4's card pool played with tournament density -- reported when the
  combo and tutor counts are both high, and honestly fuzzy; the rationale says so.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from app.services.rating.classify import classify
from app.services.rules import DeckEntry

#: Tutor density at or below which a deck can still sit in bracket 2.
_CORE_TUTOR_ALLOWANCE = 2
#: Combo and tutor counts that push an optimized deck to a cEDH read.
_CEDH_COMBOS = 3
_CEDH_TUTORS = 6


@dataclass
class BracketVerdict:
    """The bracket and the exact cards behind each signal."""

    bracket: int
    signals: dict[str, list[str]]
    rationale: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API and ``deck_scores.signals_json``."""
        return {"bracket": self.bracket, "signals": self.signals, "rationale": self.rationale}


def detect_bracket(
    entries: list[DeckEntry],
    *,
    two_card_combos: list[str] | None = None,
) -> BracketVerdict:
    """Detect the bracket of the main board plus commanders.

    Args:
        entries: The deck rows; boards other than main/commander are ignored.
        two_card_combos: Human-readable descriptions of two-card infinite combos
            present in the deck, from Commander Spellbook. ``None`` when the
            source is disabled -- reported as such rather than read as zero.
    """
    counted = [entry for entry in entries if entry.board in ("main", "commander")]

    game_changers: list[str] = []
    extra_turns: list[str] = []
    mass_land_denial: list[str] = []
    tutors: list[str] = []
    for entry in counted:
        card = entry.card
        if card.game_changer:
            game_changers.append(card.name)
        tags = classify(card)
        if "extra_turn" in tags:
            extra_turns.append(card.name)
        if "mass_land_denial" in tags:
            mass_land_denial.append(card.name)
        if "tutor" in tags:
            tutors.append(card.name)

    combos = two_card_combos or []
    signals = {
        "game_changers": sorted(game_changers),
        "extra_turns": sorted(extra_turns),
        "mass_land_denial": sorted(mass_land_denial),
        "two_card_combos": combos,
        "tutors": sorted(tutors),
    }

    rationale: list[str] = []
    bracket = 2
    if not any([game_changers, extra_turns, mass_land_denial, combos]) and (
        len(tutors) <= _CORE_TUTOR_ALLOWANCE
    ):
        rationale.append("no Game Changers, mass land denial, extra turns or two-card combos")
    else:
        bracket = 3
        if game_changers:
            rationale.append(f"{len(game_changers)} Game Changer(s)")
        if len(game_changers) > 3:
            bracket = 4
            rationale.append("more than three Game Changers")
        if mass_land_denial:
            bracket = 4
            rationale.append("mass land denial: " + ", ".join(signals["mass_land_denial"]))
        if combos:
            bracket = max(bracket, 4)
            rationale.append(f"{len(combos)} two-card infinite combo(s)")
        if extra_turns and len(extra_turns) > 1:
            bracket = max(bracket, 4)
            rationale.append("chained extra turns")
        elif extra_turns:
            rationale.append("an extra-turn card")
        if len(tutors) > _CORE_TUTOR_ALLOWANCE:
            rationale.append(f"{len(tutors)} tutors")

    if bracket == 4 and len(combos) >= _CEDH_COMBOS and len(tutors) >= _CEDH_TUTORS:
        bracket = 5
        rationale.append(
            "combo and tutor density in tournament range -- a cEDH read, which a "
            "card list can only suggest"
        )
    if two_card_combos is None:
        rationale.append("Spellbook is unavailable; combo signals were not checked")

    return BracketVerdict(bracket=bracket, signals=signals, rationale=rationale)
