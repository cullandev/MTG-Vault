"""Rating endpoints over HTTP: scores, brackets, sources failing gracefully, AI off.

The standing requirement (TEST-PLAN Phase 5): an external source's timeout, 500,
malformed body or open circuit reaches the deck page as stale data or a clean
error envelope -- never as an exception.
"""

from __future__ import annotations

from typing import Any

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.base import SourceUnavailable
from app.models import OracleCard


@pytest.fixture
def api(auth_client: TestClient, catalog: DbSession) -> TestClient:
    """A logged-in client against a database with the sample catalogue loaded."""
    return auth_client


def _deck_id(api: TestClient, catalog: DbSession, format_key: str = "commander") -> int:
    bruna = catalog.scalars(
        select(OracleCard).where(OracleCard.name == "Bruna, the Fading Light")
    ).one()
    deck = api.post(
        "/api/decks",
        json={"name": "Rated", "format": format_key, "commander_oracle_id": bruna.oracle_id},
    ).json()
    ring = catalog.scalars(select(OracleCard).where(OracleCard.name == "Sol Ring")).one()
    api.post(f"/api/decks/{deck['id']}/cards", json={"oracle_id": ring.oracle_id, "quantity": 1})
    return int(deck["id"])


def test_score_computes_stores_and_replays(api: TestClient, catalog: DbSession) -> None:
    deck_id = _deck_id(api, catalog)
    first = api.get(f"/api/decks/{deck_id}/score")
    assert first.status_code == 200, first.text
    body = first.json()
    assert set(body) >= {"consistency", "speed", "interaction", "resilience", "signals"}

    replay = api.get(f"/api/decks/{deck_id}/score").json()
    assert "computed_at" in replay  # served from the stored row, not recomputed


def test_bracket_reports_signals_without_spellbook(
    api: TestClient, catalog: DbSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setenv("ENABLE_SPELLBOOK", "false")
    from app.config import get_settings

    get_settings.cache_clear()
    try:
        deck_id = _deck_id(api, catalog)
        response = api.get(f"/api/decks/{deck_id}/bracket")
        assert response.status_code == 200, response.text
        body = response.json()
        assert 1 <= body["bracket"] <= 5
        assert any("Spellbook" in reason for reason in body["rationale"])
    finally:
        get_settings.cache_clear()


def test_combos_endpoint_serves_stale_when_the_source_is_down(
    api: TestClient, catalog: DbSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise SourceUnavailable("spellbook is down")

    from app.clients.spellbook import SpellbookClient

    monkeypatch.setattr(SpellbookClient, "find_my_combos", refuse)
    deck_id = _deck_id(api, catalog)
    response = api.get(f"/api/decks/{deck_id}/combos")
    assert response.status_code == 200, response.text
    body = response.json()
    assert body["stale"] is True
    assert body["present"] == []


def test_edhrec_failure_is_a_clean_503_when_nothing_is_cached(
    api: TestClient, catalog: DbSession, monkeypatch: pytest.MonkeyPatch
) -> None:
    async def refuse(self: Any, *args: Any, **kwargs: Any) -> Any:
        raise SourceUnavailable("edhrec is down")

    from app.clients.edhrec import EdhrecClient

    monkeypatch.setattr(EdhrecClient, "commander_page", refuse)
    deck_id = _deck_id(api, catalog)
    response = api.get(f"/api/decks/{deck_id}/edhrec")
    assert response.status_code == 503
    assert response.json()["error"]["code"] == "source_unavailable"


def test_ai_review_is_409_without_a_key_and_nothing_else_breaks(
    api: TestClient, catalog: DbSession
) -> None:
    deck_id = _deck_id(api, catalog)
    refused = api.post(f"/api/decks/{deck_id}/ai-review", json={})
    assert refused.status_code == 409
    assert refused.json()["error"]["code"] == "ai_disabled"

    # Every other deck feature still answers.
    for path in ("stats", "score", "missing", "banlist-flags"):
        response = api.get(f"/api/decks/{deck_id}/{path}")
        assert response.status_code == 200, (path, response.text)


def test_banlist_flags_endpoint_shape(api: TestClient, catalog: DbSession) -> None:
    deck_id = _deck_id(api, catalog)
    body = api.get(f"/api/decks/{deck_id}/banlist-flags").json()
    assert body == {"changes": [], "last_check": None}
