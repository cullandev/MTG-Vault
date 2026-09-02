"""Suggest commanders for a synergy core.

A candidate must be able to lead (the Phase 4 rules answer that), its colour
identity must contain the core's, and it must be a card the vault actually
holds -- these are decks to put on a real table, so nothing can be led by a
card you don't own. Ranking is how much the commander itself participates in
the core's theme: shared tags and direct edges beat an unrelated legend of the
right colours.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import CollectionItem, OracleCard
from app.services.decks.loader import rules_card
from app.services.rules.cards import can_be_commander
from app.services.synergy.clustering import Core
from app.services.synergy.graph import Edge
from app.services.synergy.patterns import default_table


@dataclass
class CommanderSuggestion:
    """One candidate, with why."""

    oracle_id: str
    name: str
    owned: bool
    score: float
    reasons: list[str]

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "oracle_id": self.oracle_id,
            "name": self.name,
            "owned": self.owned,
            "score": self.score,
            "reasons": self.reasons,
        }


def suggest(
    db: DbSession,
    core: Core,
    edges: dict[tuple[str, str], Edge],
    *,
    limit: int = 5,
) -> list[CommanderSuggestion]:
    """Rank commanders whose identity contains the core's."""
    table = default_table()
    core_tags = set()
    for oracle_id in core.oracle_ids:
        oracle = db.get(OracleCard, oracle_id)
        if oracle is not None:
            core_tags |= set(table.tags_for(rules_card(oracle)))
    member_set = set(core.oracle_ids)
    owned_ids = set(db.scalars(select(CollectionItem.oracle_id).distinct()))

    candidates = db.scalars(
        select(OracleCard).where(
            OracleCard.is_legendary.is_(True), OracleCard.oracle_id.in_(owned_ids)
        )
    )
    suggestions: list[CommanderSuggestion] = []
    for oracle in candidates:
        # Identity must contain the core's window.
        if core.color_identity_mask & ~oracle.color_identity_mask:
            continue
        card = rules_card(oracle)
        if not can_be_commander(card):
            continue
        tags = set(table.tags_for(card))
        shared = tags & core_tags
        edge_weight = 0.0
        for member in member_set:
            key = (
                (oracle.oracle_id, member)
                if oracle.oracle_id < member
                else (member, oracle.oracle_id)
            )
            entry = edges.get(key)
            if entry is not None:
                edge_weight += entry.weight
        score = 2.0 * len(shared) + edge_weight + 1.5
        reasons = [f"shares {tag}" for tag in sorted(shared)][:3]
        if edge_weight:
            reasons.append(f"synergy weight {edge_weight:.1f} with the core")
        reasons.append("owned")
        suggestions.append(
            CommanderSuggestion(
                oracle_id=oracle.oracle_id,
                name=oracle.name,
                owned=True,
                score=round(score, 2),
                reasons=reasons,
            )
        )
    suggestions.sort(key=lambda s: (-s.score, s.name))
    return suggestions[:limit]
