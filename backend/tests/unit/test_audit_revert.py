"""Audit log revert.

This is the safety net for a bad scan session or a wrong import, so it is tested for
exactness: after a revert, the collection must be indistinguishable from before.
"""

from __future__ import annotations

import pytest
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.errors import Conflict, NotFound
from app.models import AuditLog, CollectionItem
from app.services import audit
from app.services.collection import add as add_service
from app.services.collection import update as update_service

BOLT = "Lightning Bolt"


def _fingerprint(db: DbSession) -> list[tuple]:
    """Every field of every copy, so "unchanged" means genuinely unchanged."""
    return [
        (
            item.id,
            item.oracle_id,
            item.set_code,
            item.collector_number,
            item.lang,
            item.finish,
            item.condition,
            item.is_proxy,
            item.acquired_price_cents,
            item.notes,
        )
        for item in db.scalars(select(CollectionItem).order_by(CollectionItem.id))
    ]


def test_revert_of_a_single_add_removes_the_copy(catalog: DbSession) -> None:
    before = _fingerprint(catalog)
    _, batch = add_service.add_copies(catalog, _spec := add_service.AddSpec(name=BOLT))
    assert _fingerprint(catalog) != before

    result = audit.revert_batch(catalog, batch)

    assert result.reverted == 1
    assert _fingerprint(catalog) == before


def test_revert_of_a_bulk_add_removes_every_copy(catalog: DbSession) -> None:
    _, batch = add_service.add_copies(catalog, add_service.AddSpec(name="Island"), 40)
    assert catalog.scalar(select(func.count()).select_from(CollectionItem)) == 40

    audit.revert_batch(catalog, batch)

    assert catalog.scalar(select(func.count()).select_from(CollectionItem)) == 0


def test_revert_of_an_update_restores_the_old_values(catalog: DbSession) -> None:
    items, _ = add_service.add_copies(catalog, add_service.AddSpec(name=BOLT))
    before = _fingerprint(catalog)

    _, batch = update_service.update_item(
        catalog, items[0].id, {"condition": "HP", "is_proxy": True, "notes": "oops"}
    )
    assert _fingerprint(catalog) != before

    audit.revert_batch(catalog, batch)
    catalog.flush()

    assert _fingerprint(catalog) == before


def test_revert_of_a_delete_restores_the_same_row_id(catalog: DbSession) -> None:
    """Restoring with the original id keeps deck allocations pointing at it."""
    items, _ = add_service.add_copies(catalog, add_service.AddSpec(name=BOLT), 3)
    before = _fingerprint(catalog)
    ids = [item.id for item in items]

    _, batch = update_service.delete_items(catalog, ids)
    catalog.flush()
    assert _fingerprint(catalog) == []

    audit.revert_batch(catalog, batch)
    catalog.flush()

    assert _fingerprint(catalog) == before


def test_revert_undoes_a_whole_multi_step_batch(catalog: DbSession) -> None:
    """One batch id groups everything a scan session or CSV import did."""
    before = _fingerprint(catalog)
    batch = audit.new_batch_id()

    add_service.add_copies(catalog, add_service.AddSpec(name=BOLT), 2, batch_id=batch)
    items, _ = add_service.add_copies(
        catalog, add_service.AddSpec(name="Island"), 3, batch_id=batch
    )
    update_service.update_item(catalog, items[0].id, {"condition": "LP"}, batch_id=batch)
    catalog.flush()

    result = audit.revert_batch(catalog, batch)
    catalog.flush()

    assert result.reverted == 3
    assert _fingerprint(catalog) == before


def test_reverting_twice_is_refused(catalog: DbSession) -> None:
    _, batch = add_service.add_copies(catalog, add_service.AddSpec(name=BOLT))
    audit.revert_batch(catalog, batch)
    with pytest.raises(Conflict, match="already been reverted"):
        audit.revert_batch(catalog, batch)


def test_reverting_an_unknown_batch_raises(catalog: DbSession) -> None:
    with pytest.raises(NotFound):
        audit.revert_batch(catalog, "does-not-exist")


def test_revert_writes_its_own_audit_trail(catalog: DbSession) -> None:
    """The undo is itself auditable, and the original entries are marked reverted."""
    _, batch = add_service.add_copies(catalog, add_service.AddSpec(name=BOLT))
    result = audit.revert_batch(catalog, batch, note="mis-scan")
    catalog.flush()

    original = catalog.scalars(select(AuditLog).where(AuditLog.batch_id == batch)).one()
    assert original.reverted_at is not None

    undo = catalog.scalars(select(AuditLog).where(AuditLog.batch_id == result.new_batch_id)).one()
    assert undo.action == "revert"
    assert undo.source == "revert"
    assert undo.note == "mis-scan"


def test_revert_skips_rows_that_are_already_gone(catalog: DbSession) -> None:
    """Deleting a copy by hand, then reverting the add, reports rather than explodes."""
    items, batch = add_service.add_copies(catalog, add_service.AddSpec(name=BOLT), 2)
    catalog.delete(catalog.get(CollectionItem, items[0].id))
    catalog.flush()

    result = audit.revert_batch(catalog, batch)
    catalog.flush()

    assert result.reverted == 1
    assert catalog.scalar(select(func.count()).select_from(CollectionItem)) == 0


# --- revert edge cases -----------------------------------------------------


def test_a_non_revertible_entity_type_is_reported_not_crashed(catalog: DbSession) -> None:
    """Later phases will log entity types this registry does not know yet.

    ("deck" was the example here until Phase 4 registered it for real.)
    """
    batch = audit.new_batch_id()
    audit.record(
        catalog,
        action="create",
        entity_type="wishlist_row",
        entity_id="1",
        batch_id=batch,
        after={"id": 1},
    )

    result = audit.revert_batch(catalog, batch)

    assert result.reverted == 0
    assert result.skipped == 1
    assert "not revertible" in result.details[0]


def test_reverting_a_bulk_delete_that_is_already_undone_is_reported(
    catalog: DbSession,
) -> None:
    items, _ = add_service.add_copies(catalog, add_service.AddSpec(name=BOLT), 2)
    _, batch = update_service.delete_items(catalog, [item.id for item in items])
    catalog.flush()

    assert audit.revert_batch(catalog, batch).reverted == 1
    catalog.flush()

    # Roll the restore back out by hand, then replay the same entry.
    entry = catalog.scalars(select(AuditLog).where(AuditLog.batch_id == batch)).one()
    entry.reverted_at = None
    catalog.flush()

    result = audit.revert_batch(catalog, batch)
    assert result.reverted == 0
    assert "already undone" in result.details[0]


def test_reverting_an_update_whose_row_is_gone_is_reported(catalog: DbSession) -> None:
    items, _ = add_service.add_copies(catalog, add_service.AddSpec(name=BOLT))
    _, batch = update_service.update_item(catalog, items[0].id, {"condition": "LP"})
    catalog.flush()

    catalog.delete(catalog.get(CollectionItem, items[0].id))
    catalog.flush()

    result = audit.revert_batch(catalog, batch)

    assert result.reverted == 0
    assert "is gone" in result.details[0]


def test_an_unknown_action_cannot_be_reverted(catalog: DbSession) -> None:
    batch = audit.new_batch_id()
    audit.record(
        catalog,
        action="recalculated",
        entity_type="collection_item",
        entity_id="1",
        batch_id=batch,
    )

    result = audit.revert_batch(catalog, batch)

    assert result.reverted == 0
    assert "cannot revert action recalculated" in result.details[0]


def test_reverting_a_delete_whose_row_came_back_is_reported(catalog: DbSession) -> None:
    """If the same id exists again, restoring the snapshot would clobber it."""
    items, _ = add_service.add_copies(catalog, add_service.AddSpec(name=BOLT))
    item_id = items[0].id
    _, batch = update_service.delete_items(catalog, [item_id])
    catalog.flush()

    restored = CollectionItem(
        id=item_id,
        card_id=items[0].card_id,
        oracle_id=items[0].oracle_id,
        set_code=items[0].set_code,
        collector_number=items[0].collector_number,
    )
    catalog.add(restored)
    catalog.flush()

    result = audit.revert_batch(catalog, batch)

    assert result.reverted == 0
    assert "already exists" in result.details[0]


def test_reverting_an_update_restores_only_the_fields_it_changed(catalog: DbSession) -> None:
    """A rename revert must not clobber later changes to other columns.

    The failure this pins: rename deck (batch X), then build it (is_built=true),
    then revert batch X -- a full-snapshot revert would reset is_built underneath
    live allocations.
    """
    from app.services.decks import crud

    deck, _batch = crud.create_deck(catalog, crud.DeckSpec(name="Old name", format="casual"))
    _deck, rename_batch = crud.update_deck(catalog, deck.id, {"name": "New name"})
    deck.is_built = True  # a later, unrelated change to the same row
    catalog.flush()

    audit.revert_batch(catalog, rename_batch)
    catalog.expire_all()
    reverted = catalog.get(type(deck), deck.id)
    assert reverted is not None
    assert reverted.name == "Old name"  # the rename is undone
    assert reverted.is_built is True  # the later change survives
