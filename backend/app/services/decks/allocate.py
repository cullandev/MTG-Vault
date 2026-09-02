"""Building a deck: binding physical copies to it, atomically.

A build either allocates every card or nothing at all (TEST-PLAN.md, Phase 4): a
half-sleeved deck is worse than an unbuilt one, because the missing list would then
lie about both this deck and every other. Conflicts come back with enough detail to
act on -- how many are needed, how many exist, and which built decks hold the rest.
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.errors import Conflict
from app.models import (
    Card,
    CollectionItem,
    Deck,
    DeckAllocation,
    DeckCard,
    OracleCard,
    utcnow,
)
from app.services import audit
from app.services.audit import BULK_CREATE, BULK_DELETE
from app.services.collection.availability import allocated_item_ids

#: Boards whose cards physically go into the box when a deck is built.
PHYSICAL_BOARDS = ("main", "commander", "companion", "side")


@dataclass
class BuildConflict:
    """One card the build could not satisfy."""

    oracle_id: str
    name: str
    needed: int
    available: int
    blocking_decks: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "oracle_id": self.oracle_id,
            "name": self.name,
            "needed": self.needed,
            "available": self.available,
            "blocking_decks": self.blocking_decks,
        }


@dataclass
class BuildResult:
    """The outcome of a build attempt."""

    allocated: int
    conflicts: list[BuildConflict]
    batch_id: str | None = None
    assumed_basics: int = 0
    """Basic lands the build assumed from the land box rather than allocating."""

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "allocated": self.allocated,
            "conflicts": [conflict.as_dict() for conflict in self.conflicts],
            "batch_id": self.batch_id,
            "assumed_basics": self.assumed_basics,
        }


def basic_oracle_ids(db: DbSession, oracle_ids: list[str]) -> set[str]:
    """Which of these oracle ids are basic lands (Wastes and snow basics included)."""
    if not oracle_ids:
        return set()
    return set(
        db.scalars(
            select(OracleCard.oracle_id).where(
                OracleCard.oracle_id.in_(oracle_ids),
                OracleCard.type_line.like("%Basic%Land%"),
            )
        )
    )


def build(db: DbSession, deck: Deck) -> BuildResult:
    """Allocate a physical copy for every card of the deck, or nothing.

    Copies are chosen preferred printing first, then any printing; non-proxy copies
    before proxies, unless the deck row says it intends a proxy. The allocation and
    the ``is_built`` flip land in the caller's transaction.

    Raises:
        Conflict: The deck is already built.
    """
    if deck.is_built:
        raise Conflict("The deck is already built; unbuild it first")

    rows = list(
        db.scalars(
            select(DeckCard).where(DeckCard.deck_id == deck.id, DeckCard.board.in_(PHYSICAL_BOARDS))
        )
    )
    taken = set(db.scalars(allocated_item_ids()))

    chosen: list[CollectionItem] = []
    conflicts: list[BuildConflict] = []
    needs: dict[str, int] = defaultdict(int)
    prefer_proxy: dict[str, bool] = {}
    preferred_printing: dict[str, tuple[str, str] | None] = {}
    for row in rows:
        needs[row.oracle_id] += row.quantity
        prefer_proxy[row.oracle_id] = prefer_proxy.get(row.oracle_id, False) or row.is_proxy_intent
        if row.preferred_set_code and row.preferred_collector_number:
            preferred_printing[row.oracle_id] = (
                row.preferred_set_code,
                row.preferred_collector_number,
            )
        else:
            preferred_printing.setdefault(row.oracle_id, None)

    basics = basic_oracle_ids(db, list(needs))
    assumed_basics = 0
    for oracle_id, needed in needs.items():
        if oracle_id in basics:
            # The land box is real even when it was never scanned: basic lands
            # are assumed available rather than blocking a build (owner's rule).
            # Scanned copies still get sleeved when they exist.
            copies = [
                item
                for item in db.scalars(
                    select(CollectionItem).where(CollectionItem.oracle_id == oracle_id)
                )
                if item.id not in taken
            ]
            copies.sort(key=_preference_key(preferred_printing[oracle_id], prefer_proxy[oracle_id]))
            chosen.extend(copies[:needed])
            assumed_basics += max(0, needed - len(copies))
            continue
        copies = [
            item
            for item in db.scalars(
                select(CollectionItem).where(CollectionItem.oracle_id == oracle_id)
            )
            if item.id not in taken
        ]
        copies.sort(key=_preference_key(preferred_printing[oracle_id], prefer_proxy[oracle_id]))
        if len(copies) < needed:
            conflicts.append(
                BuildConflict(
                    oracle_id=oracle_id,
                    name=_oracle_name(db, oracle_id),
                    needed=needed,
                    available=len(copies),
                    blocking_decks=_blocking_decks(db, oracle_id),
                )
            )
            continue
        chosen.extend(copies[:needed])

    if conflicts:
        return BuildResult(allocated=0, conflicts=conflicts)

    batch = audit.new_batch_id()
    allocations = [DeckAllocation(collection_item_id=item.id, deck_id=deck.id) for item in chosen]
    db.add_all(allocations)
    before = audit.snapshot(deck)
    deck.is_built = True
    deck.updated_at = utcnow()
    try:
        db.flush()
    except IntegrityError as error:
        # Check-then-insert race: another build took one of these copies between
        # our availability check and this flush. The UNIQUE constraint kept the
        # data consistent; give the caller a retryable answer, not a 500.
        raise Conflict("A copy was allocated by another build while this one ran; retry") from error
    audit.record(
        db,
        action=BULK_CREATE,
        entity_type="deck_allocation",
        entity_id=deck.id,
        batch_id=batch,
        after={
            "rows": [audit.snapshot(allocation) for allocation in allocations],
            "summary": {"deck_id": deck.id, "count": len(allocations)},
        },
        note=f"built {deck.name}",
    )
    audit.record(
        db,
        action="update",
        entity_type="deck",
        entity_id=deck.id,
        batch_id=batch,
        before=before,
        after=audit.snapshot(deck),
    )
    return BuildResult(
        allocated=len(allocations),
        conflicts=[],
        batch_id=batch,
        assumed_basics=assumed_basics,
    )


def unbuild(db: DbSession, deck: Deck) -> tuple[int, str]:
    """Release every copy the deck holds and mark it theoretical again.

    Returns:
        The number of copies released and the audit batch id.
    """
    allocations = list(db.scalars(select(DeckAllocation).where(DeckAllocation.deck_id == deck.id)))
    batch = audit.new_batch_id()
    if allocations:
        audit.record(
            db,
            action=BULK_DELETE,
            entity_type="deck_allocation",
            entity_id=deck.id,
            batch_id=batch,
            before={
                "rows": [audit.snapshot(allocation) for allocation in allocations],
                "summary": {"deck_id": deck.id, "count": len(allocations)},
            },
            note=f"unbuilt {deck.name}",
        )
    for allocation in allocations:
        db.delete(allocation)
    before = audit.snapshot(deck)
    deck.is_built = False
    deck.updated_at = utcnow()
    db.flush()
    audit.record(
        db,
        action="update",
        entity_type="deck",
        entity_id=deck.id,
        batch_id=batch,
        before=before,
        after=audit.snapshot(deck),
    )
    return len(allocations), batch


@dataclass
class MissingRow:
    """One card the collection cannot currently cover."""

    oracle_id: str
    name: str
    needed: int
    owned_free: int
    missing: int
    cheapest_cents: int | None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "oracle_id": self.oracle_id,
            "name": self.name,
            "needed": self.needed,
            "owned_free": self.owned_free,
            "missing": self.missing,
            "cheapest_cents": self.cheapest_cents,
            "subtotal_cents": (
                self.cheapest_cents * self.missing if self.cheapest_cents is not None else None
            ),
        }


def missing_list(db: DbSession, deck: Deck) -> tuple[list[MissingRow], int]:
    """What the deck needs that the collection cannot supply, with buy prices.

    Copies the deck itself holds count as satisfied; copies sleeved into *other*
    built decks do not -- that is the point of allocation.

    Returns:
        The rows, and the total price in cents over the rows that have one.
    """
    rows = list(
        db.scalars(
            select(DeckCard).where(DeckCard.deck_id == deck.id, DeckCard.board.in_(PHYSICAL_BOARDS))
        )
    )
    taken_elsewhere = set(
        db.scalars(
            select(DeckAllocation.collection_item_id)
            .join(Deck, Deck.id == DeckAllocation.deck_id)
            .where(Deck.is_built.is_(True), Deck.id != deck.id)
        )
    )

    needs: dict[str, int] = defaultdict(int)
    for row in rows:
        needs[row.oracle_id] += row.quantity

    # Basic lands never appear on a buy list: the land box is assumed real
    # whether or not it was ever scanned (owner's rule).
    basics = basic_oracle_ids(db, list(needs))

    result: list[MissingRow] = []
    total = 0
    for oracle_id, needed in needs.items():
        if oracle_id in basics:
            continue
        owned_free = sum(
            1
            for item_id in db.scalars(
                select(CollectionItem.id).where(CollectionItem.oracle_id == oracle_id)
            )
            if item_id not in taken_elsewhere
        )
        missing = max(0, needed - owned_free)
        if missing == 0:
            continue
        cheapest = db.scalars(
            select(Card.price_usd_cents)
            .where(
                Card.oracle_id == oracle_id,
                Card.digital.is_(False),
                Card.price_usd_cents.is_not(None),
            )
            .order_by(Card.price_usd_cents)
        ).first()
        result.append(
            MissingRow(
                oracle_id=oracle_id,
                name=_oracle_name(db, oracle_id),
                needed=needed,
                owned_free=owned_free,
                missing=missing,
                cheapest_cents=cheapest,
            )
        )
        if cheapest is not None:
            total += cheapest * missing
    result.sort(key=lambda row: row.name)
    return result, total


def _preference_key(
    preferred: tuple[str, str] | None, prefer_proxy: bool
) -> Callable[[CollectionItem], tuple[int, int, int]]:
    """Sort available copies: preferred printing first, then proxy preference."""

    def key(item: CollectionItem) -> tuple[int, int, int]:
        exact = (
            0
            if preferred is not None and (item.set_code, item.collector_number) == preferred
            else 1
        )
        proxy_rank = int(item.is_proxy) if not prefer_proxy else int(not item.is_proxy)
        return (exact, proxy_rank, item.id)

    return key


def _oracle_name(db: DbSession, oracle_id: str) -> str:
    oracle = db.get(OracleCard, oracle_id)
    return oracle.name if oracle is not None else oracle_id


def _blocking_decks(db: DbSession, oracle_id: str) -> list[str]:
    """Names of built decks holding copies of this oracle."""
    names = db.scalars(
        select(Deck.name)
        .distinct()
        .join(DeckAllocation, DeckAllocation.deck_id == Deck.id)
        .join(CollectionItem, CollectionItem.id == DeckAllocation.collection_item_id)
        .where(Deck.is_built.is_(True), CollectionItem.oracle_id == oracle_id)
    )
    return sorted(names)
