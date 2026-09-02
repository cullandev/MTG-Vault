"""Moxfield decklist fetcher.

edhtop16 entries point at Moxfield decks; this client resolves those URLs to card
lists through Moxfield's public deck JSON. It is an unofficial-but-open endpoint,
fetched only from the scheduled meta job at a polite rate (ADR-016: no fetch is
ever triggered by a page load), and every response is reduced immediately to
``(name, quantity, board)`` rows.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.clients.base import ExternalClient, SourceResponseError

PARSER_VERSION = 1

_DECK_URL = re.compile(r"moxfield\.com/decks/(?P<public_id>[A-Za-z0-9_-]+)")


class MoxfieldClient(ExternalClient):
    """Read-only access to Moxfield's public deck JSON."""

    service: ClassVar[str] = "moxfield"
    base_url: ClassVar[str] = "https://api2.moxfield.com"
    timeout_s: ClassVar[float] = 30.0
    parser_version: ClassVar[int] = PARSER_VERSION
    # Politeness: one request a second against an unofficial endpoint.
    min_interval_s: float = 1.0

    async def deck(self, url_or_id: str) -> dict[str, Any]:
        """Fetch one public deck's JSON by its page URL or bare public id.

        Raises:
            SourceResponseError: The reference is not a Moxfield deck URL.
        """
        match = _DECK_URL.search(url_or_id)
        public_id = match.group("public_id") if match else url_or_id
        if "/" in public_id or not public_id:
            raise SourceResponseError(f"Not a Moxfield deck reference: {url_or_id!r}")
        payload = await self.request_json(f"/v2/decks/all/{public_id}")
        if not isinstance(payload, dict):
            raise SourceResponseError("Moxfield returned a non-object payload")
        return payload


@dataclass
class FetchedDecklist:
    """One deck reduced to rows the ingest writes."""

    name: str
    rows: list[tuple[str, int, str]] = field(default_factory=list)
    """``(card name, quantity, board)`` with board in main/side/commander/companion."""


_BOARD_KEYS = (
    ("commanders", "commander"),
    ("mainboard", "main"),
    ("sideboard", "side"),
    ("companions", "companion"),
)


def parse_deck(payload: dict[str, Any]) -> FetchedDecklist:
    """Reduce Moxfield deck JSON to named rows.

    Raises:
        SourceResponseError: No board in the payload held any cards -- the shape
            of an API change rather than an empty deck, which Moxfield does not
            serve from tournament links.
    """
    fetched = FetchedDecklist(name=str(payload.get("name") or "unnamed"))
    for key, board in _BOARD_KEYS:
        cards = payload.get(key)
        if not isinstance(cards, dict):
            continue
        for entry in cards.values():
            if not isinstance(entry, dict):
                continue
            card = entry.get("card") or {}
            name = card.get("name")
            if not isinstance(name, str):
                continue
            fetched.rows.append((name, int(entry.get("quantity") or 1), board))
    if not fetched.rows:
        raise SourceResponseError("Moxfield deck held no recognisable boards")
    return fetched
