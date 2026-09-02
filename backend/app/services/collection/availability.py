"""The one definition of "available": not sleeved into a built deck.

Every feature that asks "can I use this copy" -- the library grid, deck building,
missing lists, the buy list -- uses these helpers, so the answer cannot drift
between screens (ARCHITECTURE.md section 3.2).
"""

from __future__ import annotations

from sqlalchemy import Select, select
from sqlalchemy.orm import Session as DbSession

from app.models import CollectionItem, Deck, DeckAllocation


def allocated_item_ids() -> Select[tuple[int]]:
    """Subquery of collection-item ids currently sleeved into a *built* deck.

    An allocation to a theoretical deck does not exist by construction -- building
    is what creates allocations -- but the join on ``is_built`` keeps the invariant
    even if a deck is later marked theoretical without releasing its copies.
    """
    return (
        select(DeckAllocation.collection_item_id)
        .join(Deck, Deck.id == DeckAllocation.deck_id)
        .where(Deck.is_built.is_(True))
    )


def available_items(oracle_id: str, *, include_proxies: bool = False) -> Select[tuple[int]]:
    """Ids of this oracle's copies that are free to sleeve into a deck."""
    statement = (
        select(CollectionItem.id)
        .where(CollectionItem.oracle_id == oracle_id)
        .where(CollectionItem.id.not_in(allocated_item_ids()))
    )
    if not include_proxies:
        statement = statement.where(CollectionItem.is_proxy.is_(False))
    return statement


def count_available(db: DbSession, oracle_id: str, *, include_proxies: bool = False) -> int:
    """How many copies of this oracle are free to sleeve into a deck."""
    return len(list(db.scalars(available_items(oracle_id, include_proxies=include_proxies))))
