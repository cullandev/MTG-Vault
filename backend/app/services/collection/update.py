"""Changing and removing copies.

Every mutation records a before/after snapshot so it can be undone (see
:mod:`app.services.audit`).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.errors import Conflict, NotFound
from app.models import CollectionItem, utcnow
from app.services import audit

MUTABLE_FIELDS = frozenset(
    {
        "finish",
        "condition",
        "is_proxy",
        "acquired_at",
        "acquired_price_cents",
        "notes",
        "lang",
    }
)


def get_item(db: DbSession, item_id: int) -> CollectionItem:
    """Fetch one copy or raise."""
    item = db.get(CollectionItem, item_id)
    if item is None:
        raise NotFound(f"No collection item {item_id}")
    return item


def update_item(
    db: DbSession,
    item_id: int,
    changes: dict[str, Any],
    *,
    batch_id: str | None = None,
    source: str = "api",
    note: str | None = None,
) -> tuple[CollectionItem, str]:
    """Apply field changes to one copy.

    Args:
        db: Open database session.
        item_id: Copy to change.
        changes: Field name to new value; unknown fields are rejected.
        batch_id: Join an existing batch.
        source: Audit source label.
        note: Free text on the audit entry.

    Returns:
        The updated row and its batch id.

    Raises:
        NotFound: No such copy.
        Conflict: An unknown field was supplied.
    """
    item = get_item(db, item_id)
    unknown = set(changes) - MUTABLE_FIELDS
    if unknown:
        raise Conflict(
            "Unknown or immutable fields",
            detail={"fields": sorted(unknown), "mutable": sorted(MUTABLE_FIELDS)},
        )

    before = audit.snapshot(item)
    for key, value in changes.items():
        setattr(item, key, value)
    item.updated_at = utcnow()
    db.flush()

    batch = batch_id or audit.new_batch_id()
    audit.record(
        db,
        action="update",
        entity_type="collection_item",
        entity_id=item.id,
        batch_id=batch,
        before=before,
        after=audit.snapshot(item),
        source=source,
        note=note,
    )
    return item, batch


def delete_items(
    db: DbSession,
    item_ids: list[int],
    *,
    batch_id: str | None = None,
    source: str = "api",
    note: str | None = None,
) -> tuple[int, str]:
    """Remove copies from the collection.

    Args:
        db: Open database session.
        item_ids: Copies to remove.
        batch_id: Join an existing batch.
        source: Audit source label.
        note: Free text on the audit entry.

    Returns:
        How many rows were removed, and the batch id.

    Raises:
        NotFound: One of the ids does not exist.
    """
    if not item_ids:
        return 0, batch_id or audit.new_batch_id()

    items = list(db.scalars(select(CollectionItem).where(CollectionItem.id.in_(item_ids))))
    found = {item.id for item in items}
    missing = sorted(set(item_ids) - found)
    if missing:
        raise NotFound("Some collection items do not exist", detail={"missing": missing})

    snapshots = [audit.snapshot(item) for item in items]
    batch = batch_id or audit.new_batch_id()
    audit.record(
        db,
        action=audit.BULK_DELETE if len(items) > 1 else "delete",
        entity_type="collection_item",
        entity_id=items[0].id if len(items) == 1 else None,
        batch_id=batch,
        before=({"rows": snapshots} if len(items) > 1 else snapshots[0]),
        source=source,
        note=note,
    )
    for item in items:
        db.delete(item)
    db.flush()
    return len(items), batch
