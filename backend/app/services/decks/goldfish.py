"""Goldfishing: opening hands under the London mulligan, and land drops.

The simulation is deliberately coarse -- cards are "a land" or "a spell" -- because
its question is coarse: does this deck keep hands and hit its land drops? Anything
finer (casting spells, mana colours) belongs to a future phase, not to a wider
goldfish. Runs are deterministic per seed (TEST-PLAN.md section 0).
"""

from __future__ import annotations

import random
from dataclasses import dataclass
from typing import Any

#: The smallest hand the simulation will mulligan to before keeping whatever it sees.
_FLOOR = 4


@dataclass
class GoldfishResult:
    """Distribution statistics over many simulated games."""

    hands: int
    turns: int
    kept_hand_sizes: dict[str, int]
    lands_in_kept_hands: dict[str, int]
    land_drop_rate: list[float]
    """Index ``t`` is the share of games that made their land drop on turn ``t+1``."""

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "hands": self.hands,
            "turns": self.turns,
            "kept_hand_sizes": self.kept_hand_sizes,
            "lands_in_kept_hands": self.lands_in_kept_hands,
            "land_drop_rate": [round(rate, 3) for rate in self.land_drop_rate],
        }


def keep_decision(lands: int, hand_size: int, mulligans: int) -> bool:
    """Whether a hand is a keep.

    The rule, stated so the tests can cite it: a hand needs at least two lands and
    at least one spell, and each mulligan already taken lowers the land requirement
    by one -- desperation is a strategy. At :data:`_FLOOR` cards everything is a keep.
    """
    if hand_size <= _FLOOR:
        return True
    min_lands = max(0, 2 - mulligans)
    return lands >= min_lands and hand_size - lands >= 1


def run_goldfish(
    library: list[bool],
    *,
    hands: int,
    turns: int,
    seed: int,
) -> GoldfishResult:
    """Simulate opening hands and early land drops.

    Args:
        library: One entry per card in the starting deck; ``True`` is a land
            (including a modal DFC with a land face, which can always be put down
            as one).
        hands: Number of games to simulate.
        turns: How many turns of draws and land drops to play out.
        seed: RNG seed; identical seeds give identical results.

    Returns:
        Distributions of kept hand sizes, lands in kept hands, and per-turn land
        drops made.
    """
    rng = random.Random(seed)
    kept_sizes: dict[str, int] = {}
    kept_lands: dict[str, int] = {}
    drops_made = [0] * turns

    for _ in range(hands):
        hand = _london_mulligan(library, rng)
        kept_sizes[str(len(hand))] = kept_sizes.get(str(len(hand)), 0) + 1
        lands_kept = sum(hand)
        kept_lands[str(lands_kept)] = kept_lands.get(str(lands_kept), 0) + 1

        deck = list(library)
        rng.shuffle(deck)
        # Remove one copy of each kept card kind from the top of the shuffled deck;
        # which physical instance is irrelevant when cards are booleans.
        for is_land in hand:
            deck.remove(is_land)

        lands_in_hand = lands_kept
        spells_in_hand = len(hand) - lands_kept
        for turn in range(turns):
            if turn > 0 and deck:  # on the play: no draw on turn one
                drawn = deck.pop()
                lands_in_hand += int(drawn)
                spells_in_hand += int(not drawn)
            if lands_in_hand > 0:
                lands_in_hand -= 1
                drops_made[turn] += 1

    return GoldfishResult(
        hands=hands,
        turns=turns,
        kept_hand_sizes=dict(sorted(kept_sizes.items())),
        lands_in_kept_hands=dict(sorted(kept_lands.items())),
        land_drop_rate=[count / hands for count in drops_made] if hands else [],
    )


def _london_mulligan(library: list[bool], rng: random.Random) -> list[bool]:
    """Draw seven, decide, and bottom the excess -- repeated to the floor.

    London mulligan (CR 103.5): every look is at seven cards, and a hand kept after
    ``m`` mulligans bottoms ``m`` of them. Bottoming keeps the hand near three
    lands: excess lands go first, then excess spells.
    """
    for mulligans in range(0, 8 - _FLOOR):
        deck = list(library)
        rng.shuffle(deck)
        seen = deck[:7]
        target_size = 7 - mulligans
        if keep_decision(sum(seen), 7, mulligans) or target_size <= _FLOOR:
            hand = list(seen)
            while len(hand) > target_size:
                if sum(hand) > 3 and True in hand:
                    hand.remove(True)
                elif False in hand:
                    hand.remove(False)
                else:
                    hand.pop()
            return hand
    return list(library)[:_FLOOR]
