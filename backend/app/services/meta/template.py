"""Archetype template extraction: the CORE / COMMON / FLEX split.

The split is the "why" of a deck, made explicit: CORE cards (in at least 80% of
lists) are the archetype's reason to exist, COMMON cards (40%+) are the accepted
support, FLEX is where players disagree. Boundaries are inclusive and pinned by
tests at exactly 80% and exactly 40% (TEST-PLAN Phase 7).
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass

CORE_THRESHOLD_PCT = 80.0
COMMON_THRESHOLD_PCT = 40.0


@dataclass(frozen=True)
class TemplateRow:
    """One card's place in a template."""

    oracle_id: str
    tier: str
    presence_pct: float
    typical_count: int


def extract_template(lists: list[dict[str, int]]) -> list[TemplateRow]:
    """Reduce N decklists to a tiered template.

    Args:
        lists: One dict per decklist, ``oracle_id`` -> copies in the main board
            (commanders included -- in Commander the commander defines the
            archetype and is trivially CORE).

    Returns:
        Rows sorted CORE first, then by presence descending, then name-stable by
        oracle id. Presence is the share of lists playing the card at all;
        ``typical_count`` is the median copy count among lists that play it.
    """
    if not lists:
        return []
    total = len(lists)
    presence: dict[str, int] = {}
    counts: dict[str, list[int]] = {}
    for deck in lists:
        for oracle_id, quantity in deck.items():
            presence[oracle_id] = presence.get(oracle_id, 0) + 1
            counts.setdefault(oracle_id, []).append(quantity)

    rows = []
    for oracle_id, seen_in in presence.items():
        pct = round(100.0 * seen_in / total, 1)
        if pct >= CORE_THRESHOLD_PCT:
            tier = "CORE"
        elif pct >= COMMON_THRESHOLD_PCT:
            tier = "COMMON"
        else:
            tier = "FLEX"
        rows.append(
            TemplateRow(
                oracle_id=oracle_id,
                tier=tier,
                presence_pct=pct,
                typical_count=int(statistics.median(counts[oracle_id])),
            )
        )
    tier_rank = {"CORE": 0, "COMMON": 1, "FLEX": 2}
    rows.sort(key=lambda row: (tier_rank[row.tier], -row.presence_pct, row.oracle_id))
    return rows
