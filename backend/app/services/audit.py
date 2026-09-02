"""Audit log and batch revert.

Every collection mutation records what changed, when, and the full row before and
after. That is what makes "a bad scan session or a wrong import can be undone"
true rather than aspirational.

Revert is deliberately generic: it works from the recorded row snapshots and a small
registry of entity types, so adding a revertible entity is one registry line rather
than a new inverse-operation implementation (and a new place to get it wrong).
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Any

from sqlalchemy import select
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session as DbSession

from app.errors import Conflict, NotFound
from app.models import (
    AuditLog,
    Base,
    CollectionItem,
    Deck,
    DeckAllocation,
    DeckCard,
    WishlistItem,
    utcnow,
)

#: Entity types that :func:`revert_batch` knows how to restore.
REVERTIBLE: dict[str, type[Base]] = {
    "collection_item": CollectionItem,
    "deck": Deck,
    "deck_card": DeckCard,
    "deck_allocation": DeckAllocation,
    "wishlist": WishlistItem,
}

BULK_CREATE = "bulk_create"
BULK_DELETE = "bulk_delete"
"""Actions whose payload is ``{"rows": [snapshot, ...], "summary": {...}}``.

One user action that touched many rows stays one audit entry -- adding 40 basic lands
should be one line in the log and one click to undo, not forty of each.
"""


def new_batch_id() -> str:
    """Return an identifier grouping one logical operation."""
    return uuid.uuid4().hex


def snapshot(instance: Base) -> dict[str, Any]:
    """Capture every column of a row as a plain dict."""
    return {column.name: getattr(instance, column.name) for column in instance.__table__.columns}


def record(
    db: DbSession,
    *,
    action: str,
    entity_type: str,
    entity_id: str | int | None,
    batch_id: str,
    before: dict[str, Any] | None = None,
    after: dict[str, Any] | None = None,
    source: str = "api",
    note: str | None = None,
) -> AuditLog:
    """Append one audit entry.

    Args:
        db: Open database session.
        action: ``create``, ``update``, ``delete`` or ``revert``.
        entity_type: Key into :data:`REVERTIBLE`.
        entity_id: Primary key of the affected row.
        batch_id: Groups every entry of one logical operation.
        before: Row snapshot before the change, for updates and deletes.
        after: Row snapshot after the change, for creates and updates.
        source: Where the change came from (``api``, ``csv_import``, ``scan``, ...).
        note: Free text shown in the audit UI.

    Returns:
        The persisted audit row.
    """
    if action == "update" and before is not None and after is not None:
        # Store only the fields this update changed. Reverting restores exactly
        # those fields -- a full-row snapshot would clobber every *later* change
        # to the row when an older batch is reverted (e.g. a rename revert
        # resetting is_built underneath live allocations).
        changed = {key for key in before if before.get(key) != after.get(key)}
        before = {key: before[key] for key in changed if key in before}
        after = {key: after[key] for key in changed if key in after}
    entry = AuditLog(
        action=action,
        entity_type=entity_type,
        entity_id=str(entity_id) if entity_id is not None else None,
        batch_id=batch_id,
        before_json=before,
        after_json=after,
        source=source,
        note=note,
    )
    db.add(entry)
    # Flush so the entry has an id (revert entries reference it) and so a caller
    # reading the log back in the same transaction sees it -- the session runs with
    # autoflush off, so nothing else would make it visible.
    db.flush()
    return entry


@dataclass
class RevertResult:
    """Outcome of reverting one batch."""

    batch_id: str
    new_batch_id: str
    reverted: int
    skipped: int
    details: list[str]

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "batch_id": self.batch_id,
            "new_batch_id": self.new_batch_id,
            "reverted": self.reverted,
            "skipped": self.skipped,
            "details": self.details,
        }


def revert_batch(db: DbSession, batch_id: str, *, note: str | None = None) -> RevertResult:
    """Undo every change recorded under ``batch_id``.

    Entries are replayed newest-first so that dependent changes unwind in the right
    order, and the whole revert happens in the caller's transaction: either all of it
    lands or none of it does.

    Args:
        db: Open database session.
        batch_id: The batch to undo.
        note: Free text recorded against the revert entries.

    Returns:
        Counts and per-entry notes.

    Raises:
        NotFound: No such batch.
        Conflict: The batch has already been reverted.
    """
    entries = list(
        db.scalars(
            select(AuditLog).where(AuditLog.batch_id == batch_id).order_by(AuditLog.id.desc())
        )
    )
    if not entries:
        raise NotFound(f"No audit batch {batch_id}")
    if all(entry.reverted_at is not None for entry in entries):
        raise Conflict(f"Batch {batch_id} has already been reverted")

    result = RevertResult(
        batch_id=batch_id,
        new_batch_id=new_batch_id(),
        reverted=0,
        skipped=0,
        details=[],
    )
    now = utcnow()

    for entry in entries:
        if entry.reverted_at is not None:
            result.skipped += 1
            continue
        model = REVERTIBLE.get(entry.entity_type)
        if model is None:
            result.skipped += 1
            result.details.append(f"{entry.entity_type} is not revertible")
            continue

        applied = _revert_entry(db, entry, model, result)
        if not applied:
            continue

        entry.reverted_at = now
        record(
            db,
            action="revert",
            entity_type=entry.entity_type,
            entity_id=entry.entity_id,
            batch_id=result.new_batch_id,
            before=entry.after_json,
            after=entry.before_json,
            source="revert",
            note=note or f"revert of batch {batch_id}",
        )
        result.reverted += 1

    db.flush()
    return result


def _revert_entry(db: DbSession, entry: AuditLog, model: type[Base], result: RevertResult) -> bool:
    """Apply the inverse of one audit entry. Returns whether anything changed."""
    pk_value: Any = entry.entity_id
    if pk_value is not None and _pk_is_integer(model):
        pk_value = int(pk_value)

    if entry.action == BULK_CREATE:
        rows = (entry.after_json or {}).get("rows") or []
        removed = 0
        for snapshot_row in rows:
            existing = db.get(model, _pk_of(model, snapshot_row))
            if existing is not None:
                db.delete(existing)
                removed += 1
        if removed == 0:
            result.skipped += 1
            result.details.append(f"{entry.entity_type} bulk create already undone")
            return False
        return True

    if entry.action == BULK_DELETE:
        rows = (entry.before_json or {}).get("rows") or []
        restored = 0
        for snapshot_row in rows:
            if db.get(model, _pk_of(model, snapshot_row)) is None:
                # Flush row by row: a snapshot can violate a UNIQUE constraint the
                # primary-key check cannot see (a released copy since allocated to
                # another deck). One blocked row is a skip, not an aborted revert.
                nested = db.begin_nested()
                try:
                    db.add(model(**snapshot_row))
                    db.flush()
                except IntegrityError:
                    nested.rollback()
                    result.details.append(
                        f"{entry.entity_type} row {_pk_of(model, snapshot_row)} "
                        "conflicts with current state; left as-is"
                    )
                    continue
                else:
                    nested.commit()
                restored += 1
        if restored == 0:
            result.skipped += 1
            result.details.append(f"{entry.entity_type} bulk delete already undone")
            return False
        return True

    if entry.action == "create":
        row = db.get(model, pk_value)
        if row is None:
            result.skipped += 1
            result.details.append(f"{entry.entity_type} {entry.entity_id} already gone")
            return False
        db.delete(row)
        return True

    if entry.action == "delete":
        if entry.before_json is None:
            result.skipped += 1
            result.details.append(f"{entry.entity_type} {entry.entity_id} has no before-state")
            return False
        if db.get(model, pk_value) is not None:
            result.skipped += 1
            result.details.append(f"{entry.entity_type} {entry.entity_id} already exists")
            return False
        # Re-inserting with the original primary key keeps any references to this row
        # (deck allocations) pointing at the same copy after the undo.
        db.add(model(**entry.before_json))
        return True

    if entry.action == "update":
        row = db.get(model, pk_value)
        if row is None or entry.before_json is None:
            result.skipped += 1
            result.details.append(f"{entry.entity_type} {entry.entity_id} is gone")
            return False
        for key, value in entry.before_json.items():
            setattr(row, key, value)
        return True

    result.skipped += 1
    result.details.append(f"cannot revert action {entry.action}")
    return False


def _pk_of(model: type[Base], row: dict[str, Any]) -> Any:
    """Extract the primary-key value of a row snapshot."""
    columns = list(model.__table__.primary_key.columns)  # type: ignore[attr-defined]
    if len(columns) == 1:
        return row.get(columns[0].name)
    return tuple(row.get(column.name) for column in columns)


def _pk_is_integer(model: type[Base]) -> bool:
    """Whether the model's single-column primary key is an integer."""
    columns = list(model.__table__.primary_key.columns)  # type: ignore[attr-defined]
    return len(columns) == 1 and columns[0].type.python_type is int
