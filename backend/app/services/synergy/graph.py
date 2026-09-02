"""Build the synergy graph over a pool of cards.

Three edge sources, weighted separately so every edge can explain itself
(ADR-018, and A13: each source degrades to zero without breaking the others):

* **mechanical** -- enabler/payoff tag pairings from the pattern table;
* **combo** -- two cards sharing a Commander Spellbook combo;
* **co-occurrence** -- two cards repeatedly appearing in the same ingested
  tournament decklists (absent until Phase 7's meta job has run; optional).
"""

from __future__ import annotations

from collections import Counter
from dataclasses import dataclass, field
from itertools import combinations

from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.models import (
    MetaDecklistCard,
    OracleCard,
    SpellbookComboCard,
    SynergyEdge,
)
from app.services.decks.loader import rules_card
from app.services.synergy.patterns import PatternTable, default_table

COMBO_WEIGHT = 2.0
"""A proven two-card combo is the strongest edge there is."""

COOCCUR_WEIGHT_PER_LIST = 0.1
COOCCUR_WEIGHT_CAP = 0.5
COOCCUR_MIN_LISTS = 2
"""Two cards must share at least this many ingested lists to earn an edge."""


@dataclass
class Edge:
    """One undirected edge under construction."""

    mechanical_w: float = 0.0
    combo_w: float = 0.0
    cooccur_w: float = 0.0
    reasons: list[str] = field(default_factory=list)

    @property
    def weight(self) -> float:
        """The combined weight the clustering reads."""
        return self.mechanical_w + self.combo_w + self.cooccur_w


def tag_pool(
    db: DbSession, oracle_ids: list[str], table: PatternTable | None = None
) -> dict[str, set[str]]:
    """Tag -> oracle ids carrying it, over the pool."""
    table = table or default_table()
    by_tag: dict[str, set[str]] = {}
    for oracle in db.scalars(select(OracleCard).where(OracleCard.oracle_id.in_(oracle_ids))):
        for tag in table.tags_for(rules_card(oracle)):
            by_tag.setdefault(tag, set()).add(oracle.oracle_id)
    return by_tag


def build_edges(
    db: DbSession,
    oracle_ids: list[str],
    *,
    table: PatternTable | None = None,
) -> dict[tuple[str, str], Edge]:
    """Every edge between pool members, from all three sources."""
    table = table or default_table()
    pool = set(oracle_ids)
    edges: dict[tuple[str, str], Edge] = {}

    def edge(a: str, b: str) -> Edge:
        key = (a, b) if a < b else (b, a)
        return edges.setdefault(key, Edge())

    # Mechanical: enabler/payoff pairings from the table.
    by_tag = tag_pool(db, oracle_ids, table)
    for tag_a, tag_b, weight, reason in table.pairings():
        for a in by_tag.get(tag_a, ()):
            for b in by_tag.get(tag_b, ()):
                if a == b:
                    continue
                entry = edge(a, b)
                if reason not in entry.reasons:
                    entry.mechanical_w += weight
                    entry.reasons.append(reason)

    # Combos: membership rows sharing a combo_id.
    members: dict[str, set[str]] = {}
    for combo_id, oracle_id in db.execute(
        select(SpellbookComboCard.combo_id, SpellbookComboCard.oracle_id).where(
            SpellbookComboCard.oracle_id.in_(pool)
        )
    ):
        members.setdefault(combo_id, set()).add(oracle_id)
    for combo_id, ids in members.items():
        for a, b in combinations(sorted(ids), 2):
            entry = edge(a, b)
            if entry.combo_w == 0.0:
                entry.combo_w = COMBO_WEIGHT
                entry.reasons.append(f"proven combo (Spellbook {combo_id})")

    # Co-occurrence: pairs sharing ingested tournament lists. Optional by design.
    per_list: dict[int, set[str]] = {}
    for decklist_id, oracle_id in db.execute(
        select(MetaDecklistCard.decklist_id, MetaDecklistCard.oracle_id).where(
            MetaDecklistCard.oracle_id.in_(pool)
        )
    ):
        per_list.setdefault(decklist_id, set()).add(oracle_id)
    pair_counts: Counter[tuple[str, str]] = Counter()
    for ids in per_list.values():
        for pair in combinations(sorted(ids), 2):
            pair_counts[pair] += 1
    for (a, b), count in pair_counts.items():
        if count < COOCCUR_MIN_LISTS:
            continue
        entry = edge(a, b)
        entry.cooccur_w = min(COOCCUR_WEIGHT_CAP, COOCCUR_WEIGHT_PER_LIST * count)
        entry.reasons.append(f"played together in {count} tournament lists")

    return edges


def store_edges(db: DbSession, edges: dict[tuple[str, str], Edge]) -> int:
    """Replace the stored graph with this build's edges."""
    db.execute(delete(SynergyEdge))
    for (a, b), entry in edges.items():
        if entry.weight <= 0:
            continue
        db.add(
            SynergyEdge(
                oracle_id_a=a,
                oracle_id_b=b,
                weight=entry.weight,
                mechanical_w=entry.mechanical_w,
                combo_w=entry.combo_w,
                cooccur_w=entry.cooccur_w,
                reasons_json=entry.reasons[:6],
            )
        )
    db.flush()
    return len(edges)
