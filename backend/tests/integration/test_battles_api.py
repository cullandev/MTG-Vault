"""The battles HTTP surface: refusal paths that never need the Forge sidecar.

The service layer (parsing, attribution, notifications) is covered in
``tests/unit/rating/test_battles.py``; these pin the API contract.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    """A logged-in client with the sample catalogue loaded."""
    return auth_client


def test_battles_refuse_cleanly_when_forge_is_disabled(api: TestClient) -> None:
    """409 battles_disabled, with the enable instructions in the message."""
    response = api.post("/api/battles", json={"deck_ids": [1, 2]})
    assert response.status_code == 409
    body = response.json()["error"]
    assert body["code"] == "battles_disabled"


def test_a_deck_cannot_battle_itself(api: TestClient) -> None:
    response = api.post("/api/battles", json={"deck_ids": [7, 7]})
    assert response.status_code == 422


def test_battle_history_is_served_even_with_forge_off(api: TestClient) -> None:
    """The /battles page must render history whether or not the sidecar runs."""
    assert api.get("/api/battles").json() == {"battles": []}
