"""MTGO published decklists: the one free, structured source of 60-card results.

Daybreak publishes every MTGO Challenge, League and Showcase at
``https://www.mtgo.com/decklists/<year>/<month>``, each event page carrying
its complete data -- players, standings, final ranks, every card of every
list -- in a single ``window.MTGO.decklists.data = {...};`` script. There is
no API and no documented schema; the shape below is what the pages held on
2026-09-03, and the parser refuses anything that does not fit rather than
guessing (ARCHITECTURE.md section 3.6).

Opt-in like every meta source (ADR-016): ``META_SOURCES_ENABLED`` must list
``mtgo``. The site publishes no robots.txt, so the base client's rule -- an
unreachable robots.txt is not a directive -- applies.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.clients.base import ExternalClient, SourceResponseError

PARSER_VERSION = 1

#: The event slug, as it appears in hrefs and as ``site_name`` in the data:
#: ``modern-challenge-32-2026-09-0212853730`` is format, kind, size, date
#: and, glued to the date, the event id.
SLUG = re.compile(
    r"^(?P<format>[a-z]+)-(?P<kind>[a-z]+)(?:-(?P<size>\d+))?-(?P<date>\d{4}-\d{2}-\d{2})(?P<id>\d+)$"
)
_HREF = re.compile(r'href="(?:https://www\.mtgo\.com)?/decklist/([a-z0-9-]+)"')
_DATA = re.compile(r"window\.MTGO\.decklists\.data\s*=\s*(\{.*?\});\s*\n", re.S)

#: The MTGO format codes, as the data's ``format`` field spells them.
FORMAT_CODES = {
    "CMODERN": "Modern",
    "CSTANDARD": "Standard",
    "CPIONEER": "Pioneer",
    "CLEGACY": "Legacy",
    "CVINTAGE": "Vintage",
    "CPAUPER": "Pauper",
}


@dataclass(frozen=True)
class EventRef:
    """One event on a month's listing page."""

    slug: str
    format: str
    kind: str
    size: int | None
    date: str
    event_id: str

    @property
    def url(self) -> str:
        """The event page, as a person would open it."""
        return f"https://www.mtgo.com/decklist/{self.slug}"


@dataclass
class MtgoDeck:
    """One player's list in one event, with where they finished."""

    player: str
    login_id: str
    rank: int | None
    wins: int | None
    losses: int | None
    main: list[tuple[str, int]] = field(default_factory=list)
    sideboard: list[tuple[str, int]] = field(default_factory=list)


@dataclass
class MtgoEvent:
    """One event's published results."""

    event_id: str
    slug: str
    description: str
    format: str
    date: str
    decks: list[MtgoDeck]
    url: str = ""

    def top(self, count: int) -> list[MtgoDeck]:
        """The best-placed lists, ranked first, unranked last, at most ``count``."""
        ranked = sorted(self.decks, key=lambda d: (d.rank is None, d.rank or 0, d.player))
        return ranked[:count]


def parse_listing(html: str) -> list[EventRef]:
    """Every event linked from a month's listing page, newest first as listed."""
    out: list[EventRef] = []
    seen: set[str] = set()
    for slug in _HREF.findall(html):
        if slug in seen:
            continue
        seen.add(slug)
        match = SLUG.match(slug)
        if match is None:
            continue
        out.append(
            EventRef(
                slug=slug,
                format=match["format"].capitalize(),
                kind=match["kind"],
                size=int(match["size"]) if match["size"] else None,
                date=match["date"],
                event_id=match["id"],
            )
        )
    return out


def extract_data(html: str) -> dict[str, Any]:
    """The embedded ``window.MTGO.decklists.data`` object of an event page."""
    match = _DATA.search(html)
    if match is None:
        raise SourceResponseError("mtgo event page carried no embedded decklist data")
    try:
        data = json.loads(match.group(1))
    except json.JSONDecodeError as error:
        raise SourceResponseError(f"mtgo event data was not JSON: {error}") from error
    if not isinstance(data, dict) or "decklists" not in data:
        raise SourceResponseError("mtgo event data had no decklists")
    return data


def _int(value: Any) -> int | None:
    try:
        return int(str(value))
    except (TypeError, ValueError):
        return None


def _cards(entries: Any) -> list[tuple[str, int]]:
    out: list[tuple[str, int]] = []
    for entry in entries or []:
        if not isinstance(entry, dict):
            continue
        attributes = entry.get("card_attributes") or {}
        name = attributes.get("card_name")
        quantity = _int(entry.get("qty"))
        if not name or not quantity:
            continue
        out.append((str(name), quantity))
    return out


def parse_event(data: dict[str, Any]) -> MtgoEvent:
    """Reduce an event's data to lists with their finishing positions.

    Rank comes from ``final_rank`` (the Top 8 bracket) where present, else
    from the Swiss ``standings``; wins and losses from ``winloss``.
    """
    decklists = data.get("decklists")
    if not isinstance(decklists, list) or not decklists:
        raise SourceResponseError("mtgo event held no decklists")
    ranks: dict[str, int] = {}
    for row in data.get("standings") or []:
        rank = _int(row.get("rank"))
        if rank is not None:
            ranks[str(row.get("loginid"))] = rank
    for row in data.get("final_rank") or []:
        rank = _int(row.get("rank"))
        if rank is not None:
            ranks[str(row.get("loginid"))] = rank
    record: dict[str, tuple[int | None, int | None]] = {}
    for row in data.get("winloss") or []:
        record[str(row.get("loginid"))] = (_int(row.get("wins")), _int(row.get("losses")))

    decks: list[MtgoDeck] = []
    for entry in decklists:
        if not isinstance(entry, dict):
            continue
        login = str(entry.get("loginid"))
        wins, losses = record.get(login, (None, None))
        decks.append(
            MtgoDeck(
                player=str(entry.get("player") or login),
                login_id=login,
                rank=ranks.get(login),
                wins=wins,
                losses=losses,
                main=_cards(entry.get("main_deck")),
                sideboard=_cards(entry.get("sideboard_deck")),
            )
        )
    slug = str(data.get("site_name") or "")
    match = SLUG.match(slug)
    code = str(data.get("format") or "")
    return MtgoEvent(
        event_id=str(data.get("event_id") or (match["id"] if match else "")),
        slug=slug,
        description=str(data.get("description") or slug),
        format=FORMAT_CODES.get(code, match["format"].capitalize() if match else code),
        date=match["date"] if match else str(data.get("starttime") or "")[:10],
        decks=decks,
        url=f"https://www.mtgo.com/decklist/{slug}" if slug else "",
    )


class MtgoClient(ExternalClient):
    """Read-only access to mtgo.com's published decklists."""

    service: ClassVar[str] = "mtgo"
    base_url: ClassVar[str] = "https://www.mtgo.com"
    timeout_s: ClassVar[float] = 30.0
    parser_version: ClassVar[int] = PARSER_VERSION
    # Event pages are 350 KB each; a second between them is polite.
    min_interval_s: float = 1.0

    async def month(self, year: int, month: int) -> list[EventRef]:
        """Every event published in a month."""
        html = await self.request_text(f"/decklists/{year}/{month:02d}")
        return parse_listing(html)

    async def event(self, ref: EventRef) -> MtgoEvent:
        """One event's full results."""
        html = await self.request_text(f"/decklist/{ref.slug}")
        return parse_event(extract_data(html))
