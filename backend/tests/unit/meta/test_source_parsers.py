"""edhtop16 and Moxfield parsing against fixtures, and the robots gate.

Corrupted fixtures assert clean failure -- the ingest keeps the previous snapshot
rather than writing garbage (TEST-PLAN Phase 7).
"""

from __future__ import annotations

import json

import httpx
import pytest

from app.clients import edhtop16, moxfield
from app.clients.base import ExternalClient, RobotsDisallowed, SourceResponseError
from tests.conftest import FIXTURES


def _load(path: str) -> dict:
    return json.loads((FIXTURES / path).read_text(encoding="utf-8"))


def test_edhtop16_standings_parse() -> None:
    standings = edhtop16.parse_top_commanders(_load("edhtop16/top_commanders.json"))
    assert [s.name for s in standings] == [
        "Bruna, the Fading Light",
        "Gisela, the Broken Blade",
    ]
    bruna = standings[0]
    assert bruna.entry_count == 40
    assert bruna.meta_share_pct == 8.1
    assert bruna.top_cuts == 6
    assert [ref.placement for ref in bruna.decklists] == [1, 4]
    assert bruna.decklists[0].url.endswith("abc123XYZ")
    assert bruna.decklists[0].event_date == "2026-08-20"
    # The maindeck arrives inline, names plus Scryfall oracle ids where known.
    assert bruna.decklists[0].cards[0] == ("Sol Ring", "aaaa1111-0000-4000-8000-000000000018")
    assert bruna.decklists[0].cards[1] == ("Island", None)


def test_edhtop16_corrupted_body_raises() -> None:
    with pytest.raises(SourceResponseError):
        edhtop16.parse_top_commanders(_load("edhtop16/corrupted.json"))


def test_moxfield_deck_parses_boards() -> None:
    fetched = moxfield.parse_deck(_load("moxfield/deck.json"))
    assert fetched.name == "Bruna Reanimator"
    rows = {(name, board): quantity for name, quantity, board in fetched.rows}
    assert rows[("Bruna, the Fading Light", "commander")] == 1
    assert rows[("Island", "main")] == 30


def test_moxfield_corrupted_body_raises() -> None:
    with pytest.raises(SourceResponseError):
        moxfield.parse_deck(_load("moxfield/corrupted.json"))


async def test_moxfield_url_extraction() -> None:
    """A URL that is not a Moxfield deck must refuse before any request."""
    client = moxfield.MoxfieldClient("test-agent")
    with pytest.raises(SourceResponseError):
        await client.deck("https://example.com/x/y")


async def test_robots_disallow_blocks_the_fetch_before_any_request() -> None:
    """TEST-PLAN Phase 7: a Disallow fixture blocks the fetch, not the response."""

    calls: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        calls.append(str(request.url))
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /decks/")
        return httpx.Response(200, json={})

    class ScrapeClient(ExternalClient):
        service = "scrape-test"
        base_url = "https://scrape.example"
        respect_robots = True

    client = ScrapeClient("test-agent", transport=httpx.MockTransport(handler))
    with pytest.raises(RobotsDisallowed):
        await client.request_json("/decks/some-deck")
    # Only robots.txt was ever requested.
    assert all(url.endswith("robots.txt") for url in calls)
