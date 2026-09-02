"""Wishlist CRUD and the buy-list merge (TEST-PLAN Phase 6).

The merge rules under test: a card needed by two decks appears once at the max
quantity; wishlist wants stack on top; basics never appear; prices come from
the cheapest paper printing; undo works through the audit log.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import OracleCard


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    return auth_client


def _oracle_id(db: DbSession, name: str) -> str:
    return db.scalars(select(OracleCard.oracle_id).where(OracleCard.name == name)).one()


def test_wishlist_crud_round_trip(api: TestClient, catalog: DbSession) -> None:
    oracle_id = _oracle_id(catalog, "Lightning Bolt")

    created = api.post(
        "/api/wishlist", json={"oracle_id": oracle_id, "quantity": 2, "priority": 1}
    ).json()
    assert created["name"] == "Lightning Bolt"
    assert created["quantity"] == 2

    # Wishing again merges rather than duplicating.
    merged = api.post("/api/wishlist", json={"oracle_id": oracle_id, "quantity": 1}).json()
    assert merged["id"] == created["id"]
    assert merged["quantity"] == 3
    assert merged["priority"] == 1, "merging must keep the strongest priority"

    patched = api.patch(f"/api/wishlist/{created['id']}", json={"quantity": 4}).json()
    assert patched["quantity"] == 4

    listed = api.get("/api/wishlist").json()["wishes"]
    assert len(listed) == 1

    assert api.delete(f"/api/wishlist/{created['id']}").status_code == 204
    assert api.get("/api/wishlist").json()["wishes"] == []


def test_wishlist_writes_are_audited_and_undoable(api: TestClient, catalog: DbSession) -> None:
    oracle_id = _oracle_id(catalog, "Lightning Bolt")
    api.post("/api/wishlist", json={"oracle_id": oracle_id, "quantity": 2})

    entries = api.get("/api/audit").json()["items"]
    entry = next(e for e in entries if e["entity_type"] == "wishlist")
    reverted = api.post(f"/api/audit/batches/{entry['batch_id']}/revert")
    assert reverted.status_code == 200
    assert api.get("/api/wishlist").json()["wishes"] == []


def test_buylist_merges_deck_needs_and_wishes(api: TestClient, catalog: DbSession) -> None:
    bolt = _oracle_id(catalog, "Lightning Bolt")
    island = _oracle_id(catalog, "Island")

    # Two theoretical decks each missing Bolts: need is the MAX, not the sum.
    for name, quantity in (("Burn A", 3), ("Burn B", 2)):
        deck = api.post("/api/decks", json={"name": name, "format": "casual"}).json()
        api.post(
            f"/api/decks/{deck['id']}/cards",
            json={"oracle_id": bolt, "quantity": quantity},
        )
        # Basics in a deck must never reach the buy list.
        api.post(
            f"/api/decks/{deck['id']}/cards",
            json={"oracle_id": island, "quantity": 20},
        )
    api.post("/api/wishlist", json={"oracle_id": bolt, "quantity": 1, "priority": 1})

    body = api.get("/api/buylist").json()
    names = [row["name"] for row in body["rows"]]
    assert "Island" not in names, "a basic land reached the buy list"
    row = next(r for r in body["rows"] if r["name"] == "Lightning Bolt")
    assert row["deck_need"] == 3, "deck need must be the max across decks, not the sum"
    assert row["wishlist_quantity"] == 1
    assert row["quantity"] == 4
    assert len(row["decks"]) == 2
    assert row["cheapest_cents"] is not None
    assert body["total_cents"] >= row["subtotal_cents"]
