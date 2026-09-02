"""EDHREC client and parser.

EDHREC has no official API; the JSON that powers its own pages is fetched from
``json.edhrec.com`` (one request per commander, cached for a week). Everything
about this integration assumes it can break at any time: the parser records its
version, the caller keeps the last good payload, and the deck page works fully
without it (OPEN-QUESTIONS risk table).
"""

from __future__ import annotations

import re
import unicodedata
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.clients.base import ExternalClient, SourceResponseError

PARSER_VERSION = 1

#: Card-list headers worth showing, in display order. EDHREC pages carry a dozen
#: more ("New Cards", ads); an unknown header is simply skipped.
_KEPT_HEADERS = (
    "High Synergy Cards",
    "Top Cards",
    "Creatures",
    "Instants",
    "Sorceries",
    "Enchantments",
    "Artifacts",
    "Planeswalkers",
    "Lands",
    "Utility Lands",
)


class EdhrecClient(ExternalClient):
    """Read-only access to EDHREC's page JSON."""

    service: ClassVar[str] = "edhrec"
    base_url: ClassVar[str] = "https://json.edhrec.com"
    timeout_s: ClassVar[float] = 30.0
    parser_version: ClassVar[int] = PARSER_VERSION

    async def commander_page(self, commander_name: str) -> dict[str, Any]:
        """Fetch the raw page JSON for one commander."""
        payload = await self.request_json(f"/pages/commanders/{slugify(commander_name)}.json")
        if not isinstance(payload, dict):
            raise SourceResponseError("EDHREC returned a non-object payload")
        return payload


def slugify(name: str) -> str:
    """EDHREC's URL slug for a card name.

    Lowercased ASCII with punctuation dropped and spaces as dashes: "Atraxa,
    Praetors' Voice" -> ``atraxa-praetors-voice``. Partner pages use a joined
    slug, which Phase 5 does not fetch -- the first commander's page is close
    enough until partners prove common enough to matter.
    """
    decomposed = unicodedata.normalize("NFKD", name.casefold())
    ascii_name = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    ascii_name = ascii_name.replace("'", "").replace(",", "").replace(".", "")
    ascii_name = ascii_name.split("//")[0].strip()
    return re.sub(r"[^a-z0-9]+", "-", ascii_name).strip("-")


@dataclass
class EdhrecCard:
    """One recommended card."""

    name: str
    inclusion_pct: float
    synergy: float


@dataclass
class EdhrecPage:
    """The parts of a commander page the deck view shows."""

    themes: list[str] = field(default_factory=list)
    lists: list[tuple[str, list[EdhrecCard]]] = field(default_factory=list)

    @property
    def all_cards(self) -> list[EdhrecCard]:
        """Every recommended card across the kept lists, deduplicated by name."""
        seen: dict[str, EdhrecCard] = {}
        for _header, cards in self.lists:
            for card in cards:
                seen.setdefault(card.name, card)
        return list(seen.values())


def parse_commander_page(payload: dict[str, Any]) -> EdhrecPage:
    """Reduce EDHREC's page JSON to what the deck page shows.

    Tolerant by design: a missing section yields an empty list, and an entry
    without the expected numbers is skipped -- but a payload with *no* card lists
    at all raises, because that is what a page-format change looks like.

    Raises:
        SourceResponseError: The payload does not look like a commander page.
    """
    container = payload.get("container") or {}
    json_dict = container.get("json_dict") or {}
    cardlists = json_dict.get("cardlists") or []

    page = EdhrecPage()
    for taglink in payload.get("panels", {}).get("taglinks", []) or []:
        value = taglink.get("value")
        if isinstance(value, str):
            page.themes.append(value)

    for cardlist in cardlists:
        header = str(cardlist.get("header") or "")
        if header not in _KEPT_HEADERS:
            continue
        cards: list[EdhrecCard] = []
        for view in cardlist.get("cardviews") or []:
            name = view.get("name")
            if not isinstance(name, str):
                continue
            num = view.get("num_decks")
            potential = view.get("potential_decks")
            inclusion = (
                100.0 * num / potential
                if isinstance(num, int | float) and isinstance(potential, int | float) and potential
                else 0.0
            )
            cards.append(
                EdhrecCard(
                    name=name,
                    inclusion_pct=round(float(inclusion), 1),
                    synergy=round(float(view.get("synergy") or 0.0), 3),
                )
            )
        if cards:
            page.lists.append((header, cards))

    if not page.lists:
        raise SourceResponseError("EDHREC page held no recognisable card lists")
    return page
