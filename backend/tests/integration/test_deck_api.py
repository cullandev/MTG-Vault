"""Deck lifecycle over HTTP: create, fill, validate, build, and watch availability
propagate into another deck's missing list (TEST-PLAN Phase 4, integration block)."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Card, CollectionItem, OracleCard


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    """A logged-in client against a database with the sample catalogue loaded."""
    return auth_client


def _oracle_id(db: DbSession, name: str) -> str:
    return db.scalars(select(OracleCard).where(OracleCard.name == name)).one().oracle_id


def _own(db: DbSession, name: str, count: int) -> None:
    oracle_id = _oracle_id(db, name)
    printing = db.scalars(select(Card).where(Card.oracle_id == oracle_id)).first()
    assert printing is not None
    db.add_all(
        CollectionItem(
            card_id=printing.id,
            oracle_id=oracle_id,
            set_code=printing.set_code,
            collector_number=printing.collector_number,
            lang=printing.lang,
        )
        for _ in range(count)
    )
    db.commit()


def test_the_deck_lifecycle(api: TestClient, catalog: DbSession) -> None:
    """Create, add cards, stats, validate, build; a second deck sees the shortage."""
    _own(catalog, "Sol Ring", 1)
    _own(catalog, "Island", 30)

    created = api.post("/api/decks", json={"name": "Lifecycle", "format": "casual"})
    assert created.status_code == 201, created.text
    deck_id = created.json()["id"]

    for name, quantity in (("Sol Ring", 1), ("Island", 30)):
        response = api.post(
            f"/api/decks/{deck_id}/cards",
            json={"oracle_id": _oracle_id(catalog, name), "quantity": quantity},
        )
        assert response.status_code == 200, response.text

    stats = api.get(f"/api/decks/{deck_id}/stats").json()
    assert stats["card_count"] == 31
    assert stats["lands"] == 30

    verdict = api.post(f"/api/decks/{deck_id}/validate").json()
    assert verdict["is_legal"] is False  # 31 cards is short of 60
    assert any(error["code"] == "deck_size" for error in verdict["errors"])

    built = api.post(f"/api/decks/{deck_id}/build").json()
    assert built["allocated"] == 31
    assert built["conflicts"] == []
    assert api.get(f"/api/decks/{deck_id}").json()["is_built"] is True

    # A second deck wanting the same Sol Ring now sees it as missing and blocked.
    second = api.post("/api/decks", json={"name": "Second", "format": "casual"}).json()["id"]
    api.post(
        f"/api/decks/{second}/cards",
        json={"oracle_id": _oracle_id(catalog, "Sol Ring"), "quantity": 1},
    )
    missing = api.get(f"/api/decks/{second}/missing").json()
    assert [row["name"] for row in missing["rows"]] == ["Sol Ring"]

    conflict = api.post(f"/api/decks/{second}/build").json()
    assert conflict["allocated"] == 0
    assert conflict["conflicts"][0]["blocking_decks"] == ["Lifecycle"]

    # The deck page reports availability per card.
    cards = api.get(f"/api/decks/{second}/cards").json()
    (row,) = cards["boards"]["main"]
    assert row["owned"] == 1
    assert row["free"] == 0

    # Unbuild releases the copy and the second deck can then build.
    released = api.post(f"/api/decks/{deck_id}/unbuild").json()
    assert released["released"] == 31
    assert api.post(f"/api/decks/{second}/build").json()["allocated"] == 1


def test_goldfish_endpoint_is_deterministic(api: TestClient, catalog: DbSession) -> None:
    deck_id = api.post("/api/decks", json={"name": "Fish", "format": "casual"}).json()["id"]
    api.post(
        f"/api/decks/{deck_id}/cards",
        json={"oracle_id": _oracle_id(catalog, "Island"), "quantity": 40},
    )
    api.post(
        f"/api/decks/{deck_id}/cards",
        json={"oracle_id": _oracle_id(catalog, "Sol Ring"), "quantity": 20},
    )
    body = {"hands": 200, "turns": 5, "seed": 11}
    first = api.post(f"/api/decks/{deck_id}/goldfish", json=body).json()
    second = api.post(f"/api/decks/{deck_id}/goldfish", json=body).json()
    assert first == second
    assert first["kept_hand_sizes"].get("7", 0) > 150


def test_import_and_export_round_trip_over_http(api: TestClient) -> None:
    text = "Deck\n4 Fire // Ice\n20 Island\n\nSideboard\n1 Sol Ring"
    imported = api.post(
        "/api/decks/import", json={"text": text, "name": "Pasted", "format": "casual"}
    ).json()
    assert imported["unresolved"] == []
    deck_id = imported["deck_id"]

    exported = api.get(f"/api/decks/{deck_id}/export", params={"flavour": "moxfield"})
    assert exported.status_code == 200
    assert "Fire // Ice" in exported.text
    assert "Sideboard" in exported.text


def test_deleting_a_built_deck_is_refused(api: TestClient, catalog: DbSession) -> None:
    _own(catalog, "Sol Ring", 1)
    deck_id = api.post("/api/decks", json={"name": "Built", "format": "casual"}).json()["id"]
    api.post(
        f"/api/decks/{deck_id}/cards",
        json={"oracle_id": _oracle_id(catalog, "Sol Ring"), "quantity": 1},
    )
    api.post(f"/api/decks/{deck_id}/build")
    refused = api.delete(f"/api/decks/{deck_id}")
    assert refused.status_code == 409

    api.post(f"/api/decks/{deck_id}/unbuild")
    assert api.delete(f"/api/decks/{deck_id}").status_code == 200


def test_a_built_deck_is_never_hidden_by_archiving(api: TestClient, catalog: DbSession) -> None:
    """Archiving is a shelf decision; building is a physical one.

    Gauntlet decks are created archived, so a built one dropped out of the deck
    list while still holding 60 sleeved copies -- and unbuilding it needs a deck
    you can see. The owner could only free the cards by asking the database.
    """
    _own(catalog, "Sol Ring", 1)

    created = api.post("/api/decks", json={"name": "Sleeved", "format": "casual"})
    deck_id = created.json()["id"]
    api.post(
        f"/api/decks/{deck_id}/cards",
        json={"oracle_id": _oracle_id(catalog, "Sol Ring"), "quantity": 1},
    )
    assert api.post(f"/api/decks/{deck_id}/build", json={}).status_code == 200
    assert api.patch(f"/api/decks/{deck_id}", json={"archived": True}).status_code == 200

    listed = {deck["id"] for deck in api.get("/api/decks").json()["decks"]}
    assert deck_id in listed, "a built deck holding physical copies must stay visible"

    # Released, it is an ordinary archived deck and may leave the shelf again.
    assert api.post(f"/api/decks/{deck_id}/unbuild", json={}).status_code == 200
    after = {deck["id"] for deck in api.get("/api/decks").json()["decks"]}
    assert deck_id not in after
    assert deck_id in {d["id"] for d in api.get("/api/decks?include_archived=true").json()["decks"]}
