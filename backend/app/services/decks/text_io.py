"""Decklist text: parsing what people paste, writing what other tools read.

The dialects (Moxfield, Archidekt, MTGO's ``SB:``, Arena's section headers) differ
only in dressing -- quantity markers, ``(SET) 123`` printing hints, ``[Category]``
tags. One parser handles them all, and anything it cannot resolve is *reported*,
never guessed at or dropped (README: import behaviour).
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Deck, DeckCard, OracleCard
from app.services.decks import crud
from app.util.text import normalize_name

_HEADERS: dict[str, str] = {
    "commander": "commander",
    "commanders": "commander",
    "companion": "companion",
    "deck": "main",
    "main": "main",
    "mainboard": "main",
    "sideboard": "side",
    "side": "side",
    "maybeboard": "maybe",
    "maybe": "maybe",
    "considering": "maybe",
}

_LINE = re.compile(
    r"^(?:(?P<quantity>\d+)x?\s+)?"  # "4 " or "4x " or nothing
    r"(?P<name>.+?)"
    r"(?:\s+\((?P<set>[A-Za-z0-9]{2,6})\)(?:\s+(?P<collector>[A-Za-z0-9★\-]+))?)?"
    r"(?:\s+\[(?P<category>[^\]]+)\])?"
    r"(?:\s+\*[FE]\*)?"  # Moxfield foil / etched markers
    r"\s*$"
)


@dataclass
class ParsedLine:
    """One decklist line, before any database resolution."""

    quantity: int
    name: str
    board: str = "main"
    set_code: str | None = None
    collector_number: str | None = None
    category: str | None = None


@dataclass
class ImportOutcome:
    """What a text import produced."""

    deck: Deck
    added: int
    unresolved: list[str] = field(default_factory=list)

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {"deck_id": self.deck.id, "added": self.added, "unresolved": self.unresolved}


def parse_decklist(text: str) -> list[ParsedLine]:
    """Split decklist text into structured lines.

    Section headers ("Sideboard", "Commander") switch the current board; MTGO's
    ``SB:`` prefix marks a single line; everything else lands in the current board.
    Comment lines (``//`` or ``#``) are skipped -- but a *name* containing ``//``
    (Fire // Ice) is not a comment, so only a leading marker counts.
    """
    parsed: list[ParsedLine] = []
    board = "main"
    for raw in text.splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("// "):
            continue
        header = _HEADERS.get(line.rstrip(":").strip().lower())
        if header is not None:
            board = header
            continue
        line_board = board
        if line.upper().startswith("SB:"):
            line_board = "side"
            line = line[3:].strip()
        match = _LINE.match(line)
        if match is None:
            continue
        parsed.append(
            ParsedLine(
                quantity=int(match.group("quantity") or 1),
                name=match.group("name").strip(),
                board=line_board,
                set_code=(match.group("set") or "").lower() or None,
                collector_number=match.group("collector"),
                category=match.group("category"),
            )
        )
    return parsed


def resolve_name(db: DbSession, name: str) -> OracleCard | None:
    """Find the oracle card a decklist name refers to.

    Accepts the full ``//`` name, the front-face name, and -- because people type
    what is on the half they are looking at -- the *back* half of a split card
    (TEST-PLAN.md section 1: import accepts ``Fire``, ``Ice`` and ``Fire // Ice``).
    """
    normalized = normalize_name(name)
    card = db.scalars(
        select(OracleCard).where(
            (OracleCard.name_norm == normalized) | (OracleCard.name_front_norm == normalized)
        )
    ).first()
    if card is not None:
        return card
    # The back half of a multi-face name. Normalisation flattens "//" away, so the
    # halves have to be compared one by one -- and only against actual multi-face
    # names, or "Ice" would happily resolve to "Essence of Ice".
    for candidate in db.scalars(select(OracleCard).where(OracleCard.name.contains("//"))):
        for half in candidate.name.split("//"):
            if normalize_name(half) == normalized:
                return candidate
    return None


def import_text(
    db: DbSession,
    *,
    text: str,
    name: str,
    format_key: str,
    source_text: str | None = None,
) -> ImportOutcome:
    """Create a deck from decklist text.

    Unresolvable names are returned, not dropped: the deck is created with what
    resolved, and the caller shows the rest.
    """
    deck, batch = crud.create_deck(
        db,
        crud.DeckSpec(
            name=name,
            format=format_key,
            source="import",
            source_ref={"text": source_text} if source_text else None,
        ),
    )
    added = 0
    unresolved: list[str] = []
    for line in parse_decklist(text):
        oracle = resolve_name(db, line.name)
        if oracle is None:
            unresolved.append(line.name)
            continue
        existing = db.get(DeckCard, (deck.id, oracle.oracle_id, line.board))
        quantity = line.quantity + (existing.quantity if existing is not None else 0)
        crud.set_card(
            db,
            deck.id,
            crud.CardSpec(
                oracle_id=oracle.oracle_id,
                board=line.board,
                quantity=quantity,
                preferred_set_code=line.set_code,
                preferred_collector_number=line.collector_number,
                category=line.category,
            ),
            batch_id=batch,
        )
        added += line.quantity
    return ImportOutcome(deck=deck, added=added, unresolved=unresolved)


#: Board order and headers used by every export flavour.
_EXPORT_SECTIONS = (
    ("commander", "Commander"),
    ("companion", "Companion"),
    ("main", "Deck"),
    ("side", "Sideboard"),
    ("maybe", "Maybeboard"),
)


def export_text(db: DbSession, deck: Deck, *, flavour: str = "text") -> str:
    """Render the deck as decklist text.

    ``text`` is bare names; ``moxfield`` adds ``(SET) 123`` printing hints where a
    preference is recorded; ``archidekt`` adds ``4x``-style quantities and
    ``[Category]`` tags. All three round-trip through :func:`parse_decklist`.
    """
    rows = list(
        db.execute(
            select(DeckCard, OracleCard.name)
            .join(OracleCard, OracleCard.oracle_id == DeckCard.oracle_id)
            .where(DeckCard.deck_id == deck.id)
            .order_by(DeckCard.board, OracleCard.name)
        )
    )
    by_board: dict[str, list[tuple[DeckCard, str]]] = {}
    for row, card_name in rows:
        by_board.setdefault(row.board, []).append((row, card_name))

    sections: list[str] = []
    for board, header in _EXPORT_SECTIONS:
        entries = by_board.get(board)
        if not entries:
            continue
        lines = [header]
        for row, card_name in entries:
            lines.append(_format_line(row, card_name, flavour))
        sections.append("\n".join(lines))
    return "\n\n".join(sections) + "\n"


def _format_line(row: DeckCard, card_name: str, flavour: str) -> str:
    quantity = f"{row.quantity}x" if flavour == "archidekt" else str(row.quantity)
    line = f"{quantity} {card_name}"
    if flavour in ("moxfield", "archidekt") and row.preferred_set_code:
        set_code = (
            row.preferred_set_code.upper()
            if flavour == "moxfield"
            else row.preferred_set_code.lower()
        )
        line += f" ({set_code})"
        if row.preferred_collector_number:
            line += f" {row.preferred_collector_number}"
    if flavour == "archidekt" and row.category:
        line += f" [{row.category}]"
    return line
