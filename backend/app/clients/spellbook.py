"""Commander Spellbook client and parser.

Spellbook has a real public API (``backend.commanderspellbook.com``). The
``find-my-combos`` endpoint takes the deck as card names and answers with combos
the deck contains and combos it is one card away from -- exactly the two questions
the deck page asks. Results are persisted into the ``spellbook_*`` tables so the
answers survive an outage, served stale rather than not at all.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.clients.base import ExternalClient, SourceResponseError

PARSER_VERSION = 1


class SpellbookClient(ExternalClient):
    """Read-only access to Commander Spellbook's combo search."""

    service: ClassVar[str] = "spellbook"
    base_url: ClassVar[str] = "https://backend.commanderspellbook.com"
    timeout_s: ClassVar[float] = 30.0
    parser_version: ClassVar[int] = PARSER_VERSION
    # A documented public API; robots.txt governs the website crawler, not this.
    respect_robots: ClassVar[bool] = False

    async def find_my_combos(self, commanders: list[str], main: list[str]) -> dict[str, Any]:
        """Ask which combos the deck contains or nearly contains."""
        payload = await self.request_json(
            "/find-my-combos",
            method="POST",
            json={"commanders": commanders, "main": main},
        )
        if not isinstance(payload, dict):
            raise SourceResponseError("Spellbook returned a non-object payload")
        return payload


@dataclass
class Combo:
    """One combo, reduced to what the app stores and shows."""

    combo_id: str
    card_names: list[str]
    result_text: str
    colors: str = ""

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "combo_id": self.combo_id,
            "cards": self.card_names,
            "result": self.result_text,
            "colors": self.colors,
        }


@dataclass
class ComboSearch:
    """Parsed ``find-my-combos`` response."""

    included: list[Combo] = field(default_factory=list)
    almost_included: list[Combo] = field(default_factory=list)


def _parse_combo(variant: dict[str, Any]) -> Combo | None:
    combo_id = variant.get("id")
    if not isinstance(combo_id, str | int):
        return None
    names = [
        str(use["card"]["name"])
        for use in variant.get("uses") or []
        if isinstance(use, dict) and isinstance(use.get("card"), dict) and use["card"].get("name")
    ]
    if not names:
        return None
    produces = "; ".join(
        str(item["feature"]["name"])
        for item in variant.get("produces") or []
        if isinstance(item, dict)
        and isinstance(item.get("feature"), dict)
        and item["feature"].get("name")
    )
    identity = variant.get("identity")
    return Combo(
        combo_id=str(combo_id),
        card_names=names,
        result_text=produces,
        colors=str(identity or "").replace("C", ""),
    )


def parse_find_my_combos(payload: dict[str, Any]) -> ComboSearch:
    """Reduce a ``find-my-combos`` response to included / almost-included combos.

    Raises:
        SourceResponseError: The payload does not carry a ``results`` object --
            the shape of an API change, as opposed to a deck with no combos,
            which is a normal empty result.
    """
    results = payload.get("results")
    if not isinstance(results, dict):
        raise SourceResponseError("Spellbook response held no results object")
    search = ComboSearch()
    for variant in results.get("included") or []:
        combo = _parse_combo(variant)
        if combo is not None:
            search.included.append(combo)
    for variant in results.get("almostIncluded") or []:
        combo = _parse_combo(variant)
        if combo is not None:
            search.almost_included.append(combo)
    return search
