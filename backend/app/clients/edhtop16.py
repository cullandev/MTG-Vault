"""edhtop16 client and parser -- the one meta source with a documented public API.

edhtop16 aggregates cEDH tournament results and serves them over GraphQL
(ADR-016). Its measurement is **results**, never popularity (ADR-017). The parser
reduces the response to archetype standings plus decklist references; fetching the
decklists themselves is the Moxfield client's job.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.clients.base import ExternalClient, SourceResponseError

PARSER_VERSION = 1
MEASUREMENT = "results"

#: Standings plus entry decklists for the most-played commanders of a period.
#: Field names verified against the live schema by introspection on 2026-08-27
#: (CommandersSortBy: CONVERSION|POPULARITY|TOP_CUTS|WINRATE; Entry.maindeck is
#: [Card!] carrying name and oracleId, so the decklists arrive in this one call).
_QUERY = """
query TopCommanders($timePeriod: TimePeriod!, $limit: Int!) {
  commanders(timePeriod: $timePeriod, sortBy: POPULARITY, first: $limit) {
    edges { node {
      name
      colorId
      stats(filters: { timePeriod: $timePeriod }) { count metaShare topCuts }
      entries(first: 8, sortBy: TOP, filters: { timePeriod: $timePeriod, minEventSize: 16 }) {
        edges { node {
          standing
          decklist
          maindeck { name oracleId }
          player { name }
          tournament { name tournamentDate size }
        } }
      }
    } }
  }
}
"""


class Edhtop16Client(ExternalClient):
    """Read-only access to edhtop16's GraphQL endpoint."""

    service: ClassVar[str] = "edhtop16"
    base_url: ClassVar[str] = "https://edhtop16.com"
    timeout_s: ClassVar[float] = 30.0
    parser_version: ClassVar[int] = PARSER_VERSION
    # A documented public API; robots.txt governs the website, not this endpoint.
    respect_robots: ClassVar[bool] = False

    async def top_commanders(self, *, months: int = 3, limit: int = 25) -> dict[str, Any]:
        """Fetch standings for the most-played commanders of the last N months."""
        period = "THREE_MONTHS" if months >= 3 else "ONE_MONTH"
        payload = await self.request_json(
            "/api/graphql",
            method="POST",
            json={"query": _QUERY, "variables": {"timePeriod": period, "limit": limit}},
        )
        if not isinstance(payload, dict):
            raise SourceResponseError("edhtop16 returned a non-object payload")
        return payload


@dataclass
class DecklistRef:
    """One tournament decklist: its cards when edhtop16 served them, else a URL.

    ``cards`` is ``(name, oracle_id)`` per main-deck card, straight from the API.
    When empty (older entries), ``url`` points at the source list and the Moxfield
    client is the fallback fetcher.
    """

    url: str
    player: str | None = None
    event: str | None = None
    event_date: str | None = None
    placement: int | None = None
    cards: list[tuple[str, str | None]] = field(default_factory=list)


@dataclass
class ArchetypeStanding:
    """One commander's standing: the archetype row, plus its decklist pointers."""

    name: str
    colors: str
    entry_count: int
    meta_share_pct: float
    top_cuts: int
    decklists: list[DecklistRef] = field(default_factory=list)


def parse_top_commanders(payload: dict[str, Any]) -> list[ArchetypeStanding]:
    """Reduce the GraphQL response to archetype standings.

    Raises:
        SourceResponseError: The payload carries GraphQL errors or no commander
            edges at all -- the shape of an API change, not an empty meta.
    """
    if payload.get("errors"):
        raise SourceResponseError(f"edhtop16 returned errors: {payload['errors']!r}")
    edges = (((payload.get("data") or {}).get("commanders") or {}).get("edges")) or []
    if not edges:
        raise SourceResponseError("edhtop16 response held no commander edges")

    standings: list[ArchetypeStanding] = []
    for edge in edges:
        node = edge.get("node") or {}
        name = node.get("name")
        if not isinstance(name, str):
            continue
        stats = node.get("stats") or {}
        decklists: list[DecklistRef] = []
        for entry_edge in ((node.get("entries") or {}).get("edges")) or []:
            entry = entry_edge.get("node") or {}
            url = entry.get("decklist")
            cards = [
                (str(card["name"]), card.get("oracleId"))
                for card in entry.get("maindeck") or []
                if isinstance(card, dict) and card.get("name")
            ]
            if not cards and (not isinstance(url, str) or not url):
                continue
            tournament = entry.get("tournament") or {}
            player = entry.get("player") or {}
            decklists.append(
                DecklistRef(
                    url=str(url or ""),
                    player=player.get("name"),
                    event=tournament.get("name"),
                    event_date=tournament.get("tournamentDate"),
                    placement=entry.get("standing"),
                    cards=cards,
                )
            )
        standings.append(
            ArchetypeStanding(
                name=name,
                colors=str(node.get("colorId") or ""),
                entry_count=int(stats.get("count") or 0),
                meta_share_pct=round(100.0 * float(stats.get("metaShare") or 0.0), 2),
                top_cuts=int(stats.get("topCuts") or 0),
                decklists=decklists,
            )
        )
    return standings
