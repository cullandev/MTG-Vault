"""Sets: completion, value, and the browse-a-set-in-order view.

Completion counts distinct collector numbers, English paper printings only:
that is the binder page the percentage describes. Sets the vault holds nothing
from are omitted by default -- a thousand 0% rows help nobody -- but
``all=true`` lists them for the completionist mood.
"""

from __future__ import annotations

import re
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import case, func, select

from app.deps import Db
from app.errors import NotFound
from app.models import Card, CollectionItem
from app.services import pricing

router = APIRouter(prefix="/sets", tags=["sets"])

_CODE = re.compile(r"^[a-z0-9]{2,6}$")


def _set_rows(db: Db, *, only_owned: bool) -> list[dict[str, Any]]:
    # Guarded by the join: the finish-aware price's else-branch would otherwise
    # count every UNOWNED card's price through the outer join's NULL row.
    price = case((CollectionItem.id.is_not(None), pricing.price_column()), else_=None)
    statement = (
        select(
            Card.set_code,
            func.min(Card.set_name).label("set_name"),
            func.max(Card.released_at).label("released_at"),
            func.count(func.distinct(Card.collector_number)).label("total"),
            func.count(
                func.distinct(case((CollectionItem.id.is_not(None), Card.collector_number)))
            ).label("owned_numbers"),
            func.count(CollectionItem.id).label("copies"),
            func.coalesce(func.sum(price), 0).label("value_cents"),
            func.sum(case((CollectionItem.id.is_not(None) & price.is_(None), 1), else_=0)).label(
                "unpriced"
            ),
        )
        .select_from(Card)
        .join(
            CollectionItem,
            (CollectionItem.card_id == Card.id) & CollectionItem.is_proxy.is_(False),
            isouter=True,
        )
        .where(Card.digital.is_(False), Card.lang == "en")
        .group_by(Card.set_code)
    )
    rows = []
    for row in db.execute(statement):
        if only_owned and not row.copies:
            continue
        total = int(row.total or 0)
        owned = int(row.owned_numbers or 0)
        rows.append(
            {
                "set_code": row.set_code,
                "set_name": row.set_name,
                "released_at": row.released_at,
                "total_numbers": total,
                "owned_numbers": owned,
                "completion": round(owned / total, 4) if total else 0.0,
                "copies": int(row.copies or 0),
                "value_cents": int(row.value_cents or 0),
                "unpriced_copies": int(row.unpriced or 0),
            }
        )
    return rows


def _natural_key(collector_number: str) -> tuple[Any, ...]:
    """Order collector numbers the way a binder does.

    Alternating text/number runs compared piecewise: "13" before "132", and
    "A25-13" before "A25-132" but after "10E-343" -- a plain integer CAST
    scored every letter-prefixed number zero and shuffled The List entirely.
    """
    runs = re.findall(r"\d+|\D+", collector_number)
    # Each run is (kind, number, text) so numeric and text runs stay mutually
    # comparable -- "13" vs "A25-13" would otherwise compare int to str and 500.
    return tuple((0, int(run), "") if run.isdigit() else (1, 0, run.lower()) for run in runs)


@router.get("")
def sets(db: Db, all: bool = Query(default=False)) -> dict[str, Any]:
    """Every set the vault touches: completion, copies, value."""
    rows = _set_rows(db, only_owned=not all)
    rows.sort(key=lambda entry: (-entry["value_cents"], -entry["completion"]))
    return {"sets": rows}


@router.get("/{set_code}/cards")
def set_cards(db: Db, set_code: str) -> dict[str, Any]:
    """One whole set in collector order, with owned counts.

    The binder view: everything the set contains, owned or not, so the gaps
    are visible in place. Collector numbers are text ("184", "12a", "A25-13"),
    ordered by natural-sort runs -- see :func:`_natural_key`.
    """
    code = set_code.lower()
    if not _CODE.fullmatch(code):
        raise NotFound(f"No set {set_code!r}")

    owned = (
        select(
            CollectionItem.card_id,
            func.count(CollectionItem.id).label("owned_count"),
        )
        .where(CollectionItem.is_proxy.is_(False))
        .group_by(CollectionItem.card_id)
        .subquery()
    )
    rows = db.execute(
        select(Card, func.coalesce(owned.c.owned_count, 0).label("owned_count"))
        .join(owned, owned.c.card_id == Card.id, isouter=True)
        .where(Card.set_code == code, Card.digital.is_(False), Card.lang == "en")
    ).all()
    if not rows:
        raise NotFound(f"No set {set_code!r}")
    # Ordered in Python: collector numbers need natural-sort runs, which SQL
    # cannot express cleanly, and the largest set is ~5k rows -- milliseconds.
    rows = sorted(rows, key=lambda row: _natural_key(row[0].collector_number))

    cards = [
        {
            "card_id": card.id,
            "oracle_id": card.oracle_id,
            "name": card.name,
            "collector_number": card.collector_number,
            "rarity": card.rarity,
            "image_url": f"/api/images/{card.id}/normal" if card.image_normal_url else None,
            # The grid tile: a sixth the bytes of "normal", which is the
            # difference between a binder page and a loading bar.
            "image_small": f"/api/images/{card.id}/small" if card.image_normal_url else None,
            "price_usd_cents": card.price_usd_cents,
            "owned_count": int(owned_count),
        }
        for card, owned_count in rows
    ]
    total = len({entry["collector_number"] for entry in cards})
    owned_numbers = len({entry["collector_number"] for entry in cards if entry["owned_count"] > 0})
    return {
        "set_code": code,
        "set_name": rows[0][0].set_name,
        "total_numbers": total,
        "owned_numbers": owned_numbers,
        "completion": round(owned_numbers / total, 4) if total else 0.0,
        "cards": cards,
    }


@router.get("/{set_code}/value-history")
def set_value_history(
    db: Db, set_code: str, days: int = Query(default=365, ge=1, le=3650)
) -> dict[str, Any]:
    """This set's owned copies valued at each day's snapshotted prices."""
    code = set_code.lower()
    if not _CODE.fullmatch(code) or not db.scalar(
        select(Card.id).where(Card.set_code == code).limit(1)
    ):
        # The cards endpoint 404s unknown sets; an empty 200 here would hand a
        # typo'd client a blank chart instead of an answer.
        raise NotFound(f"No set {set_code!r}")
    return {
        "set_code": code,
        "days": days,
        "points": pricing.set_value_history(db, code, days=days),
    }
