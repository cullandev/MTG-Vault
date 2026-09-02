"""Collection value, price history and movers.

Three rules run through all of this, and each exists because the obvious alternative
quietly lies:

**An unknown price is not zero.** Scryfall has no price for plenty of printings. Folding
those into a total as zero understates the collection by however many they are, and
nothing on screen would say so. They are excluded from the total and *counted*, and the
count is shown next to it.

**A proxy is worth nothing.** It is a real object in the collection and a real card in
a deck, but it is not an asset, and a collection total that includes proxies is not a
collection total.

**The price used depends on the finish.** A foil copy is worth the foil price. Using the
non-foil price for everything is a rounding error on commons and badly wrong on the
cards most worth knowing about.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import date, timedelta
from typing import Any

from sqlalchemy import Select, case, func, select
from sqlalchemy.orm import Session as DbSession

from app.models import Card, CollectionItem, PriceSnapshot, utctoday

log = logging.getLogger("mtgvault.pricing")

TOP_CARDS = 10
DEFAULT_MOVER_DAYS = 7


def price_column() -> Any:
    """The price of one copy, in cents, according to its finish.

    NULL when the price is unknown, which every caller then has to decide what to do
    about -- deliberately, rather than defaulting to zero somewhere out of sight.
    """
    return case(
        (CollectionItem.finish == "foil", Card.price_usd_foil_cents),
        (CollectionItem.finish == "etched", Card.price_usd_etched_cents),
        else_=Card.price_usd_cents,
    )


def _priced_copies() -> Select[Any]:
    """Copies that count towards value: real cards, not proxies."""
    return (
        select(CollectionItem, Card)
        .join(Card, Card.id == CollectionItem.card_id)
        .where(CollectionItem.is_proxy.is_(False))
    )


@dataclass
class CollectionValue:
    """What the collection is worth right now."""

    total_cents: int = 0
    foil_cents: int = 0
    nonproxy_count: int = 0
    unique_count: int = 0
    unpriced_count: int = 0
    """Copies whose price is unknown. Never folded into the total."""
    by_set: list[dict[str, Any]] = field(default_factory=list)
    by_rarity: list[dict[str, Any]] = field(default_factory=list)
    top_cards: list[dict[str, Any]] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API and the value snapshot."""
        return {
            "total_cents": self.total_cents,
            "foil_cents": self.foil_cents,
            "nonproxy_count": self.nonproxy_count,
            "unique_count": self.unique_count,
            "unpriced_count": self.unpriced_count,
            "by_set": self.by_set,
            "by_rarity": self.by_rarity,
            "top_cards": self.top_cards,
        }


def collection_value(db: DbSession) -> CollectionValue:
    """Total the collection, with the breakdowns the dashboard shows."""
    price = price_column()
    totals = db.execute(
        _priced_copies()
        .with_only_columns(
            func.count(CollectionItem.id),
            func.count(func.distinct(CollectionItem.oracle_id)),
            func.coalesce(func.sum(price), 0),
            func.coalesce(func.sum(case((CollectionItem.finish != "nonfoil", price), else_=0)), 0),
            func.sum(case((price.is_(None), 1), else_=0)),
        )
        .order_by(None)
    ).one()
    copies, unique, total, foil, unpriced = totals

    by_set = [
        {
            "set_code": row.set_code,
            "set_name": row.set_name,
            "copies": int(row.copies),
            "value_cents": int(row.value_cents or 0),
        }
        for row in db.execute(
            _priced_copies()
            .with_only_columns(
                Card.set_code.label("set_code"),
                func.min(Card.set_name).label("set_name"),
                func.count(CollectionItem.id).label("copies"),
                func.coalesce(func.sum(price), 0).label("value_cents"),
            )
            .group_by(Card.set_code)
            .order_by(func.coalesce(func.sum(price), 0).desc())
        )
    ]

    by_rarity = [
        {
            "rarity": row.rarity or "unknown",
            "copies": int(row.copies),
            "value_cents": int(row.value_cents or 0),
        }
        for row in db.execute(
            _priced_copies()
            .with_only_columns(
                Card.rarity.label("rarity"),
                func.count(CollectionItem.id).label("copies"),
                func.coalesce(func.sum(price), 0).label("value_cents"),
            )
            .group_by(Card.rarity)
            .order_by(func.coalesce(func.sum(price), 0).desc())
        )
    ]

    top_cards = [
        {
            "card_id": row.card_id,
            "name": row.name,
            "set_code": row.set_code,
            "collector_number": row.collector_number,
            "finish": row.finish,
            "value_cents": int(row.value_cents or 0),
        }
        for row in db.execute(
            _priced_copies()
            .with_only_columns(
                CollectionItem.card_id.label("card_id"),
                Card.name.label("name"),
                Card.set_code.label("set_code"),
                Card.collector_number.label("collector_number"),
                CollectionItem.finish.label("finish"),
                price.label("value_cents"),
            )
            .where(price.isnot(None))
            .order_by(price.desc())
            .limit(TOP_CARDS)
        )
    ]

    return CollectionValue(
        total_cents=int(total or 0),
        foil_cents=int(foil or 0),
        nonproxy_count=int(copies or 0),
        unique_count=int(unique or 0),
        unpriced_count=int(unpriced or 0),
        by_set=by_set,
        by_rarity=by_rarity,
        top_cards=top_cards,
    )


def watched_card_ids(db: DbSession) -> list[int]:
    """Printings worth snapshotting: the ones actually in the collection (ADR-009).

    Snapshotting every printing would write about half a million rows a day for data
    nobody looks at. Phase 4 adds deck-referenced cards to this set.
    """
    return [
        int(row)
        for row in db.scalars(
            select(CollectionItem.card_id).where(CollectionItem.card_id.isnot(None)).distinct()
        )
        if row is not None
    ]


def price_history(db: DbSession, card_id: int, *, days: int = 90) -> list[dict[str, Any]]:
    """A printing's recorded prices, oldest first.

    History begins the day the card entered the collection (ADR-009). Nothing is
    interpolated backwards: a flat line to the left of the first real reading would be
    a measurement nobody took.
    """
    cutoff = (date.fromisoformat(utctoday()) - timedelta(days=days)).isoformat()
    rows = db.execute(
        select(
            PriceSnapshot.snapshot_date,
            PriceSnapshot.usd_cents,
            PriceSnapshot.usd_foil_cents,
        )
        .where(PriceSnapshot.card_id == card_id, PriceSnapshot.snapshot_date >= cutoff)
        .order_by(PriceSnapshot.snapshot_date)
    )
    return [
        {
            "date": row.snapshot_date,
            "usd_cents": row.usd_cents,
            "usd_foil_cents": row.usd_foil_cents,
        }
        for row in rows
    ]


@dataclass(frozen=True)
class Move:
    """One printing's price change between two snapshots."""

    card_id: int
    from_cents: int
    to_cents: int
    pct_change: float
    compared_to_date: str
    """Which earlier reading this was measured against; the UI shows the span."""


def detect_movements(
    db: DbSession, *, snapshot_date: str | None = None, threshold_pct: float
) -> list[Move]:
    """Find printings whose price moved by at least ``threshold_pct``.

    Compared against the **nearest prior snapshot**, not against yesterday. The job can
    miss a day -- the machine was off, the download failed -- and comparing today with
    a gap in between silently reports a week's drift as an overnight move. The date
    actually compared against is recorded so the UI can say over what span.
    """
    today = snapshot_date or utctoday()
    current = {
        row.card_id: row.usd_cents
        for row in db.execute(
            select(PriceSnapshot.card_id, PriceSnapshot.usd_cents).where(
                PriceSnapshot.snapshot_date == today, PriceSnapshot.usd_cents.isnot(None)
            )
        )
    }
    if not current:
        return []

    # The most recent earlier reading per card, whenever it happened to be taken.
    previous_date = (
        select(
            PriceSnapshot.card_id.label("card_id"),
            func.max(PriceSnapshot.snapshot_date).label("prior"),
        )
        .where(
            PriceSnapshot.snapshot_date < today,
            PriceSnapshot.card_id.in_(list(current)),
            PriceSnapshot.usd_cents.isnot(None),
        )
        .group_by(PriceSnapshot.card_id)
        .subquery()
    )
    prior_rows = db.execute(
        select(PriceSnapshot.card_id, PriceSnapshot.snapshot_date, PriceSnapshot.usd_cents).join(
            previous_date,
            (PriceSnapshot.card_id == previous_date.c.card_id)
            & (PriceSnapshot.snapshot_date == previous_date.c.prior),
        )
    )

    moves: list[Move] = []
    for row in prior_rows:
        before = row.usd_cents
        after = current.get(row.card_id)
        if not before or after is None:
            continue
        change = (after - before) / before * 100.0
        if abs(change) >= threshold_pct:
            moves.append(
                Move(
                    card_id=row.card_id,
                    from_cents=before,
                    to_cents=after,
                    pct_change=round(change, 2),
                    compared_to_date=row.snapshot_date,
                )
            )
    return sorted(moves, key=lambda move: -abs(move.pct_change))


def set_value_history(db: DbSession, set_code: str, *, days: int = 365) -> list[dict[str, Any]]:
    """One set's tracked-copies value over time, oldest first.

    Same semantics as the whole-collection series: each point is what the
    copies owned AND price-tracked that day were worth, so the line moves on
    both prices and acquisitions. ``copies`` rides along on every point and
    the UI shows it -- a jump on a scanning day must read as "63 copies
    arrived", never as a price spike. English paper copies, finish-strict
    pricing (a foil without a foil price counts as unpriced, module rule one),
    matching the sets list exactly.
    """
    cutoff = (date.fromisoformat(utctoday()) - timedelta(days=days)).isoformat()
    price = case(
        (CollectionItem.finish == "foil", PriceSnapshot.usd_foil_cents),
        (CollectionItem.finish == "etched", PriceSnapshot.usd_etched_cents),
        else_=PriceSnapshot.usd_cents,
    )
    rows = db.execute(
        select(
            PriceSnapshot.snapshot_date,
            func.coalesce(func.sum(price), 0),
            func.count(CollectionItem.id),
            func.sum(case((price.is_(None), 1), else_=0)),
        )
        .select_from(CollectionItem)
        .join(PriceSnapshot, PriceSnapshot.card_id == CollectionItem.card_id)
        .where(
            CollectionItem.is_proxy.is_(False),
            CollectionItem.lang == "en",
            CollectionItem.set_code == set_code.lower(),
            PriceSnapshot.snapshot_date >= cutoff,
            # A copy only counts from the day it was acquired: without this,
            # cards scanned today inflate every HISTORICAL point and the jump
            # appears at the chart's left edge instead of on the scanning day.
            func.substr(CollectionItem.created_at, 1, 10) <= PriceSnapshot.snapshot_date,
        )
        .group_by(PriceSnapshot.snapshot_date)
        .order_by(PriceSnapshot.snapshot_date)
    ).all()
    return [
        {
            "date": snapshot_date,
            "value_cents": int(total or 0),
            "copies": int(copies),
            "unpriced": int(unpriced or 0),
        }
        for snapshot_date, total, copies, unpriced in rows
    ]


def value_history(db: DbSession, *, days: int = 365) -> list[dict[str, Any]]:
    """Collection value over time, oldest first."""
    from app.models import CollectionValueSnapshot

    cutoff = (date.fromisoformat(utctoday()) - timedelta(days=days)).isoformat()
    rows = db.scalars(
        select(CollectionValueSnapshot)
        .where(CollectionValueSnapshot.snapshot_date >= cutoff)
        .order_by(CollectionValueSnapshot.snapshot_date)
    )
    return [
        {
            "date": row.snapshot_date,
            "total_cents": row.total_cents,
            "foil_cents": row.foil_cents,
            "copies": row.nonproxy_count,
            "unpriced": row.unpriced_count,
        }
        for row in rows
    ]
