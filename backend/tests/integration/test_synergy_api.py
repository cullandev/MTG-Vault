"""Synergy over HTTP: rebuild, cores, assembly into a stored deck, neighbours."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession

from app.services.synergy.rebuild import rebuild
from tests.unit.meta.conftest import make_card, own


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    """A logged-in client with the sample catalogue loaded."""
    return auth_client


def _seed_vault(db: DbSession) -> None:
    """A sacrifice-value vault big enough to cluster and assemble."""
    leader = make_card(
        db,
        "Pod Aristocrat",
        type_line="Legendary Creature — Vampire Noble",
        oracle_text="Whenever another creature dies, each opponent loses 1 life.",
        identity="B",
        cmc=4,
    )
    own(db, leader)
    for i in range(14):
        oracle = make_card(
            db,
            f"Sac Piece {i:02d}",
            type_line="Artifact" if i % 2 else "Creature — Vampire",
            oracle_text=(
                "Sacrifice a creature: Add {C}{C}."
                if i % 2
                else "Whenever another creature dies, you gain 1 life."
            ),
            identity="B",
            cmc=(i % 3) + 1,
        )
        own(db, oracle)
    for role, text in (
        ("ramp", "Search your library for a basic land card and put it onto the battlefield."),
        ("draw", "Draw a card."),
        ("removal", "Destroy target creature."),
        ("wipe", "Destroy all creatures."),
    ):
        for i in range(12):
            oracle = make_card(
                db,
                f"{role} pod {i:02d}",
                type_line="Sorcery",
                oracle_text=text,
                identity="B",
                cmc=2,
            )
            own(db, oracle)
    make_card(db, "Swamp", type_line="Basic Land — Swamp", identity="B", mana_cost="", cmc=0)
    for i in range(40):
        own(
            db,
            make_card(
                db, f"Black Body {i:02d}", type_line="Creature — Zombie", identity="B", cmc=2
            ),
        )
    rebuild(db)
    db.commit()


def test_the_hidden_deck_flow(api: TestClient, catalog: DbSession) -> None:
    _seed_vault(catalog)

    cores = api.get("/api/synergy/cores").json()["cores"]
    assert cores, "no cores found in a planted vault"
    top = cores[0]
    assert top["theme"] == "sacrifice value"
    assert top["suggested_commanders"]
    assert top["suggested_commanders"][0]["name"] == "Pod Aristocrat"

    detail = api.get(f"/api/synergy/cores/{top['core_id']}").json()
    assert len(detail["cards"]) == top["card_count"]
    assert detail["edges"], "a core with no explained edges"
    assert all(edge["reasons"] for edge in detail["edges"])

    assembled = api.post(
        f"/api/synergy/cores/{top['core_id']}/assemble", json={"create_deck": True}
    ).json()
    assert assembled["is_legal"] is True
    assert sum(int(row["quantity"]) for row in assembled["deck"]) == 100
    assert assembled["quota_report"]

    # The generator explains itself: mechanics counted, reasons recorded.
    summary = assembled["summary"]
    assert summary["provenance"] == "synergy"
    assert "sacrifice value" in summary["headline"]
    assert summary["mechanics"], "no mechanics counted"
    assert all(m["count"] > 0 for m in summary["mechanics"])
    assert summary["why_picked"]

    deck = api.get(f"/api/decks/{assembled['deck_id']}").json()
    assert deck["source"] == "synergy"
    assert deck["card_count"] == 100
    # The summary is persisted with the deck and served on its detail.
    assert deck["summary"]["headline"] == summary["headline"]

    some_card = detail["cards"][0]
    neighbours = api.get(f"/api/synergy/edges/{some_card['oracle_id']}").json()
    assert neighbours["neighbours"]
    assert all(entry["reasons"] for entry in neighbours["neighbours"])


def test_an_empty_vault_serves_empty_cores_not_an_error(api: TestClient) -> None:
    assert api.get("/api/synergy/cores").json() == {"cores": []}
