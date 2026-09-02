"""Collection CSV and JSON export.

Two audiences:

* **Round-trip** -- the ``native`` flavour re-imports into this application without
  loss, which is what the round-trip test asserts.
* **Insurance** -- the JSON export is readable without this application ever running
  again, which is the point of the "one-click full export" requirement.
"""

from __future__ import annotations

import csv
import io
import json
from collections.abc import Iterator
from typing import TYPE_CHECKING, Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.constants import PRICE_NOTE

if TYPE_CHECKING:
    from app.services.collection.query import CollectionFilters
from app.models import Card, CollectionItem, utcnow

NATIVE_COLUMNS = [
    "quantity",
    "name",
    "set_code",
    "set_name",
    "collector_number",
    "condition",
    "language",
    "finish",
    "proxy",
    "purchase_price",
    "notes",
    "oracle_id",
]

MOXFIELD_COLUMNS = [
    "Count",
    "Name",
    "Edition",
    "Condition",
    "Language",
    "Foil",
    "Collector Number",
    "Proxy",
    "Purchase Price",
]

_LANGUAGE_NAMES = {
    "en": "English",
    "ja": "Japanese",
    "de": "German",
    "fr": "French",
    "it": "Italian",
    "es": "Spanish",
    "pt": "Portuguese",
    "ru": "Russian",
    "ko": "Korean",
    "zhs": "Chinese Simplified",
    "zht": "Chinese Traditional",
}


def _rows(
    db: DbSession, filters: CollectionFilters | None = None
) -> Iterator[tuple[CollectionItem, Card | None]]:
    """Stream every (filtered) copy with its printing, oldest first."""
    if filters is not None:
        from app.services.collection.query import filtered_copies_statement

        statement = (
            filtered_copies_statement(filters)
            .order_by(CollectionItem.created_at, CollectionItem.id)
            .execution_options(yield_per=500)
        )
    else:
        statement = (
            select(CollectionItem, Card)
            .join(Card, Card.id == CollectionItem.card_id, isouter=True)
            .order_by(CollectionItem.created_at, CollectionItem.id)
            .execution_options(yield_per=500)
        )
    # Not `yield from`: db.execute yields Row objects, and callers (and the type
    # signature) expect a plain (item, card) tuple.
    for item, card in db.execute(statement):  # noqa: UP028
        yield item, card


def export_csv(
    db: DbSession, flavour: str = "native", filters: CollectionFilters | None = None
) -> Iterator[str]:
    """Yield a collection CSV line by line.

    Args:
        db: Open database session.
        flavour: ``native`` (lossless round-trip) or ``moxfield``.
        filters: The library view's active filters, so "export what I am
            looking at" exports exactly that. ``None`` exports everything.

    Yields:
        CSV text, one chunk per row, so a 10 000-card export never builds a big string.
    """
    buffer = io.StringIO()
    writer = csv.writer(buffer, lineterminator="\n")

    def take() -> str:
        value = buffer.getvalue()
        buffer.seek(0)
        buffer.truncate(0)
        return value

    if flavour == "moxfield":
        writer.writerow(MOXFIELD_COLUMNS)
        yield take()
        for item, card in _rows(db, filters):
            writer.writerow(
                [
                    1,
                    card.name if card else "",
                    (card.set_code if card else item.set_code).upper(),
                    item.condition,
                    _LANGUAGE_NAMES.get(item.lang, item.lang),
                    "foil" if item.finish in ("foil", "etched") else "",
                    card.collector_number if card else item.collector_number,
                    "proxy" if item.is_proxy else "",
                    f"{item.acquired_price_cents / 100:.2f}" if item.acquired_price_cents else "",
                ]
            )
            yield take()
        return

    writer.writerow(NATIVE_COLUMNS)
    yield take()
    for item, card in _rows(db, filters):
        writer.writerow(
            [
                1,
                card.name if card else "",
                item.set_code,
                card.set_name if card else "",
                item.collector_number,
                item.condition,
                item.lang,
                item.finish,
                "true" if item.is_proxy else "",
                f"{item.acquired_price_cents / 100:.2f}" if item.acquired_price_cents else "",
                item.notes or "",
                item.oracle_id,
            ]
        )
        yield take()


def export_json(db: DbSession, filters: CollectionFilters | None = None) -> str:
    """Return the whole collection as a self-describing JSON document.

    Insurance-grade: every field needed to reconstruct the collection by hand, with
    the price caveat and the export timestamp embedded.
    """
    items: list[dict[str, Any]] = []
    for item, card in _rows(db, filters):
        items.append(
            {
                "item_id": item.id,
                "oracle_id": item.oracle_id,
                "name": card.name if card else None,
                "set_code": item.set_code,
                "set_name": card.set_name if card else None,
                "collector_number": item.collector_number,
                "language": item.lang,
                "finish": item.finish,
                "condition": item.condition,
                "is_proxy": item.is_proxy,
                "acquired_at": item.acquired_at,
                "acquired_price_cents": item.acquired_price_cents,
                "notes": item.notes,
                "created_at": item.created_at,
                "price_usd_cents": card.price_usd_cents if card else None,
                "price_as_of": card.price_updated_at if card else None,
            }
        )

    return json.dumps(
        {
            "exported_at": utcnow(),
            "schema": "mtgvault.collection.v1",
            "price_note": PRICE_NOTE,
            "count": len(items),
            "items": items,
        },
        indent=2,
        ensure_ascii=False,
    )
