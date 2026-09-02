"""Coverage: how much of an archetype the vault can field, and at what price.

CORE is weighted heaviest -- missing the deck's reason to exist matters more than
missing a flex slot (TEST-PLAN Phase 7). Copies sleeved into built decks count as
conflicts, and ``exclude_allocated`` decides whether they still count as owned.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import (
    ArchetypeTemplate,
    ArchetypeTemplateCard,
    Card,
    CollectionItem,
    CoverageResult,
    Deck,
    DeckAllocation,
    OracleCard,
)

TIER_WEIGHTS = {"CORE": 3.0, "COMMON": 2.0, "FLEX": 1.0}


@dataclass
class CoverageDetail:
    """One template's coverage against the vault."""

    template_id: int
    weighted_coverage: float
    core_coverage: float
    missing_count: int
    missing_cost_cents: int
    conflict_count: int
    rank_score: float
    missing: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API."""
        return {
            "template_id": self.template_id,
            "coverage_pct": round(100 * self.weighted_coverage, 1),
            "core_coverage_pct": round(100 * self.core_coverage, 1),
            "missing_count": self.missing_count,
            "cost_to_complete_cents": self.missing_cost_cents,
            "conflicts": self.conflict_count,
            "rank_score": round(self.rank_score, 3),
            "missing": self.missing,
        }


def compute_coverage(
    db: DbSession,
    template: ArchetypeTemplate,
    *,
    exclude_allocated: bool = True,
    meta_share_pct: float = 0.0,
    persist: bool = True,
) -> CoverageDetail:
    """Score one template against the collection.

    Args:
        db: Open database session.
        template: The template to score.
        exclude_allocated: When true, a copy sleeved into a built deck does not
            count as owned; either way it counts as a conflict.
        meta_share_pct: The archetype's meta share, blended into the rank.
        persist: Whether to append a ``coverage_results`` row.
    """
    rows = list(
        db.scalars(
            select(ArchetypeTemplateCard).where(ArchetypeTemplateCard.template_id == template.id)
        )
    )
    oracle_ids = [row.oracle_id for row in rows]
    owned = _counts(db, oracle_ids, allocated=False)
    allocated = _counts(db, oracle_ids, allocated=True)
    # Both of these used to run once per MISSING card, inside the loop below.
    # On the live vault that was ~4,127 round trips per template and ~25
    # templates per request: the endpoint took 5.4 seconds, 92x of it in
    # queries a single GROUP BY answers.
    cheapest = _cheapest_prices(db, oracle_ids)
    names = _oracle_names(db, oracle_ids)

    weight_total = 0.0
    weight_covered = 0.0
    core_total = 0
    core_covered = 0
    missing_count = 0
    missing_cost = 0
    conflicts = 0
    missing_rows: list[dict[str, Any]] = []

    for row in rows:
        weight = TIER_WEIGHTS[row.tier]
        weight_total += weight
        free = owned.get(row.oracle_id, 0)
        held = allocated.get(row.oracle_id, 0)
        if held:
            conflicts += 1
        have = free if exclude_allocated else free + held
        if have >= 1:
            weight_covered += weight
            if row.tier == "CORE":
                core_total += 1
                core_covered += 1
        else:
            if row.tier == "CORE":
                core_total += 1
            missing_count += 1
            price = cheapest.get(row.oracle_id)
            if price is not None:
                missing_cost += price * row.typical_count
            missing_rows.append(
                {
                    "oracle_id": row.oracle_id,
                    "name": names.get(row.oracle_id, row.oracle_id),
                    "tier": row.tier,
                    "cheapest_cents": price,
                }
            )

    weighted = weight_covered / weight_total if weight_total else 0.0
    core = core_covered / core_total if core_total else 1.0
    # Buildability dominates; meta strength breaks ties within a measurement
    # (ADR-017: the blend never crosses measurement types).
    rank = 0.7 * weighted + 0.2 * core + 0.1 * min(1.0, meta_share_pct / 10.0)

    detail = CoverageDetail(
        template_id=template.id,
        weighted_coverage=weighted,
        core_coverage=core,
        missing_count=missing_count,
        missing_cost_cents=missing_cost,
        conflict_count=conflicts,
        rank_score=rank,
        missing=sorted(
            missing_rows,
            # CORE first -- alphabetical tier order would put COMMON ahead of it.
            key=lambda entry: (-TIER_WEIGHTS[str(entry["tier"])], str(entry["name"])),
        ),
    )
    if persist:
        db.add(
            CoverageResult(
                template_id=template.id,
                weighted_coverage=weighted,
                core_coverage=core,
                missing_count=missing_count,
                missing_cost_cents=missing_cost,
                conflict_count=conflicts,
                rank_score=rank,
                detail_json={"missing": detail.missing},
            )
        )
        db.flush()
    return detail


def _counts(db: DbSession, oracle_ids: list[str], *, allocated: bool) -> dict[str, int]:
    """Copies per oracle, split by whether they sit in a built deck."""
    if not oracle_ids:
        return {}
    statement = (
        select(CollectionItem.oracle_id, func.count())
        .where(CollectionItem.oracle_id.in_(oracle_ids))
        .group_by(CollectionItem.oracle_id)
    )
    allocated_ids = (
        select(DeckAllocation.collection_item_id)
        .join(Deck, Deck.id == DeckAllocation.deck_id)
        .where(Deck.is_built.is_(True))
    )
    if allocated:
        statement = statement.where(CollectionItem.id.in_(allocated_ids))
    else:
        statement = statement.where(CollectionItem.id.not_in(allocated_ids))
    return dict(db.execute(statement).tuples().all())


def _cheapest_prices(db: DbSession, oracle_ids: list[str]) -> dict[str, int]:
    """Cheapest paper price per oracle card, in one query."""
    if not oracle_ids:
        return {}
    rows = db.execute(
        select(Card.oracle_id, func.min(Card.price_usd_cents))
        .where(
            Card.oracle_id.in_(oracle_ids),
            Card.digital.is_(False),
            Card.price_usd_cents.is_not(None),
        )
        .group_by(Card.oracle_id)
    ).all()
    return {oracle_id: int(price) for oracle_id, price in rows if price is not None}


def _oracle_names(db: DbSession, oracle_ids: list[str]) -> dict[str, str]:
    """Printed names for a batch of oracle ids, in one query."""
    if not oracle_ids:
        return {}
    rows = db.execute(
        select(OracleCard.oracle_id, OracleCard.name).where(OracleCard.oracle_id.in_(oracle_ids))
    ).tuples()
    return dict(rows.all())
