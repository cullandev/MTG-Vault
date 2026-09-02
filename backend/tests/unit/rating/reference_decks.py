"""Six hand-scored reference decks for the heuristic scorer (TEST-PLAN Phase 5).

Each deck is a caricature of a real archetype, built from cards with genuine
oracle wordings so the classifier sees what it would see in production. The
expected bands below each deck are justified in prose; the ordering assertions in
``test_heuristics.py`` are the stronger guard -- a formula tweak that inverts a
known ranking (cEDH out-speeding a preconstructed deck, control out-interacting
burn) fails regardless of the bands.
"""

from __future__ import annotations

from app.services.rules import DeckEntry
from tests.unit.rules.conftest import card, entry

# --- the shared card vocabulary --------------------------------------------

LAND = card("Plains", type_line="Basic Land — Plains")
BOLT = card(
    "Lightning Bolt",
    type_line="Instant",
    cmc=1,
    oracle_text="Lightning Bolt deals 3 damage to any target.",
)
COUNTER = card("Counterspell", type_line="Instant", cmc=2, oracle_text="Counter target spell.")
WRATH = card(
    "Wrath of God",
    type_line="Sorcery",
    cmc=4,
    oracle_text="Destroy all creatures. They can't be regenerated.",
)
DRAW2 = card("Divination", type_line="Sorcery", cmc=3, oracle_text="Draw two cards.")
CANTRIP = card("Opt", type_line="Instant", cmc=1, oracle_text="Scry 1. Draw a card.")
TUTOR = card(
    "Demonic Tutor",
    type_line="Sorcery",
    cmc=2,
    oracle_text="Search your library for a card, put that card into your hand, then shuffle.",
)
MANA_ROCK = card("Sol Ring", type_line="Artifact", cmc=1, oracle_text="{T}: Add {C}{C}.")
RAMP_SORCERY = card(
    "Rampant Growth",
    type_line="Sorcery",
    cmc=2,
    oracle_text="Search your library for a basic land card, put that card onto the "
    "battlefield tapped, then shuffle.",
)
PROTECT = card(
    "Heroic Intervention",
    type_line="Instant",
    cmc=2,
    oracle_text="Permanents you control gain hexproof and indestructible until end of turn.",
)
RECUR = card(
    "Regrowth",
    type_line="Sorcery",
    cmc=2,
    oracle_text="Return target card from your graveyard to your hand.",
)
BEAR = card("Grizzly Bears", type_line="Creature — Bear", cmc=2)
BIG_DUMB = card("Craw Wurm", type_line="Creature — Wurm", cmc=6)
MIDSIZE = card("Hill Giant", type_line="Creature — Giant", cmc=4)


def _deck(*rows: tuple[object, int]) -> list[DeckEntry]:
    return [entry(c, n) for c, n in rows]  # type: ignore[arg-type]


# --- Commander -------------------------------------------------------------

#: cEDH: low curve, dense tutors and interaction, every card selected.
#: Expect: the fastest and most consistent Commander deck here; interaction high.
CEDH = _deck(
    (LAND, 28),
    (MANA_ROCK, 10),
    (TUTOR, 8),
    (COUNTER, 10),
    (BOLT, 6),
    (CANTRIP, 12),
    (PROTECT, 4),
    (RECUR, 2),
    (BEAR, 20),
)

#: A preconstructed-style Commander deck: high curve, thin draw, token interaction.
#: Expect: the low bar every other deck clears somewhere.
PRECON = _deck(
    (LAND, 38),
    (MIDSIZE, 30),
    (BIG_DUMB, 20),
    (WRATH, 2),
    (BOLT, 2),
    (DRAW2, 4),
    (RAMP_SORCERY, 4),
)

# --- Modern ----------------------------------------------------------------

#: Burn: nineteen lands and a fistful of three-damage instants.
#: Expect: the fastest deck in the file; its damage counts as removal, so its
#: interaction lands mid-high -- burn does answer creatures, at the cost of reach.
BURN = _deck((LAND, 19), (BOLT, 30), (BEAR, 11))

#: Control: counters, wipes and card draw; slow by design.
#: Expect: the most interactive deck here, and the slowest of the sixty-card ones.
CONTROL = _deck(
    (LAND, 25),
    (COUNTER, 12),
    (WRATH, 4),
    (DRAW2, 8),
    (CANTRIP, 6),
    (PROTECT, 3),
    (BEAR, 2),
)

# --- Pauper ----------------------------------------------------------------

#: Midrange commons: some of everything, excellence at nothing.
#: Expect: consistent (its land count sits exactly on the recommendation) and
#: reasonably interactive, but unexceptional in speed and fragile in resilience.
PAUPER_MIDRANGE = _deck(
    (LAND, 23),
    (BEAR, 14),
    (MIDSIZE, 8),
    (BOLT, 6),
    (DRAW2, 4),
    (CANTRIP, 5),
)

#: Ramp stompy: mana acceleration into big dumb creatures, no interaction.
#: Expect: speed above the precon, interaction the lowest in the file.
RAMP_STOMPY = _deck(
    (LAND, 24),
    (RAMP_SORCERY, 8),
    (MANA_ROCK, 4),
    (BIG_DUMB, 16),
    (MIDSIZE, 8),
)
