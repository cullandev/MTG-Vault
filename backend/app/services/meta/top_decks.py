"""Real tournament decklists as playable decks.

Two shelves, one source label. The Commander shelf comes from the meta
snapshot, which already ingests every card of every top-cut cEDH list and
until now only ever *reduced* them -- to templates, to coverage, to the
gauntlet's owned-cards cuts. The 60-card shelf comes from MTGO's published
Challenge results (Modern and Standard by default), fetched by the job and
handed here already parsed. Both are materialised as decks of their own, so
the owner can sit down at the Arena and play the real thing, or play against
it, in the same format as their own deck.

These decks are never restricted to owned cards (Forge needs no sleeves) and
never enter the gauntlet's ladder. Each refresh replaces its own shelf: a
list that dropped out of the top is pruned, one the owner has physically
built is kept, and no deck of any other source is read, let alone touched.
"""

from __future__ import annotations

import logging
from collections import Counter
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.mtgo import MtgoEvent
from app.models import (
    Deck,
    MetaArchetype,
    MetaDecklist,
    MetaDecklistCard,
    MetaSnapshot,
    OracleCard,
)
from app.services.decks import crud as deck_crud
from app.services.decks import text_io
from app.services.rating.gauntlet import _basic_fill

log = logging.getLogger(__name__)

#: The deck source that marks a materialised tournament list. Pruning is
#: scoped to this value and nothing else.
SOURCE = "meta_top"

#: How many commanders' lists to keep on the Commander shelf.
DEFAULT_LIMIT = 10

#: How many lists per 60-card format to keep on that shelf.
DEFAULT_PER_FORMAT = 5

#: Below this many resolved main-deck cards a Commander list is too holed to
#: play; above it, the gap is filled with basics of the commander's colours.
MIN_RESOLVED_MAIN = 90

#: A 60-card list may be short at most this many cards before it is skipped;
#: a smaller hole is filled with the basic land it already plays most.
MAX_MISSING_SIXTY = 2

COMMANDER_FORMAT = "casual_commander"
SIXTY_FORMAT = "casual"


@dataclass
class TopDecksReport:
    """What one run did to a shelf, by deck name."""

    snapshot_id: int | None = None
    created: list[str] = field(default_factory=list)
    replaced: list[str] = field(default_factory=list)
    kept_built: list[str] = field(default_factory=list)
    pruned: list[str] = field(default_factory=list)
    skipped: list[dict[str, Any]] = field(default_factory=list)

    @property
    def changed(self) -> bool:
        """Whether the shelf looks different: something arrived or something left."""
        return bool(self.created or self.pruned)

    def as_dict(self) -> dict[str, Any]:
        """The report as the job run records it."""
        return {
            "snapshot_id": self.snapshot_id,
            "created": self.created,
            "replaced": self.replaced,
            "kept_built": self.kept_built,
            "pruned": self.pruned,
            "skipped": self.skipped,
        }

    def extend(self, other: TopDecksReport) -> None:
        """Fold another shelf's report into this one."""
        self.created += other.created
        self.replaced += other.replaced
        self.kept_built += other.kept_built
        self.pruned += other.pruned
        self.skipped += other.skipped


# -- shared -------------------------------------------------------------------


def _write_deck(
    db: DbSession,
    name: str,
    format_key: str,
    rows: list[dict[str, Any]],
    source_ref: dict[str, Any],
) -> tuple[int, str]:
    """Create the deck, or wholly replace the one of that name and source.

    Returns the deck id and one of ``created`` | ``replaced`` | ``kept_built``.
    """
    existing = db.scalars(select(Deck).where(Deck.name == name, Deck.source == SOURCE)).first()
    outcome = "created"
    if existing is not None:
        if existing.is_built:
            # The owner sleeved this list. Their cards beat a fresher copy.
            return existing.id, "kept_built"
        deck_crud.delete_deck(db, existing.id)
        outcome = "replaced"
    deck, batch = deck_crud.create_deck(
        db,
        deck_crud.DeckSpec(name=name, format=format_key, source=SOURCE, source_ref=source_ref),
    )
    merged: dict[tuple[str, str], int] = {}
    for row in rows:
        key = (str(row["oracle_id"]), str(row["board"]))
        merged[key] = merged.get(key, 0) + int(row["quantity"])
    for (oracle_id, board), quantity in merged.items():
        deck_crud.set_card(
            db,
            deck.id,
            deck_crud.CardSpec(oracle_id=oracle_id, board=board, quantity=quantity),
            batch_id=batch,
        )
    return deck.id, outcome


def prune_stale(db: DbSession, format_key: str, wanted: set[str], report: TopDecksReport) -> None:
    """Take this shelf's stale decks off: only this source and format, never a built deck."""
    for deck in db.scalars(select(Deck).where(Deck.source == SOURCE, Deck.format == format_key)):
        if deck.name in wanted:
            continue
        if deck.is_built:
            report.kept_built.append(deck.name)
            continue
        deck_crud.delete_deck(db, deck.id)
        report.pruned.append(deck.name)


# -- the Commander shelf --------------------------------------------------------


def deck_name(archetype_name: str) -> str:
    """What a Commander list is called on the shelf: the commander, marked as real."""
    return f"{archetype_name} (cEDH top list)"


def latest_snapshot(db: DbSession, format_key: str = "commander") -> MetaSnapshot | None:
    """The newest usable snapshot of the format: ok or partial, never failed."""
    return db.scalars(
        select(MetaSnapshot)
        .where(MetaSnapshot.format == format_key, MetaSnapshot.status.in_(("ok", "partial")))
        .order_by(MetaSnapshot.snapshot_date.desc(), MetaSnapshot.id.desc())
        .limit(1)
    ).first()


def leading_archetypes(db: DbSession, snapshot_id: int, limit: int) -> list[MetaArchetype]:
    """The snapshot's commanders by top cuts, then by share of the field."""
    return list(
        db.scalars(
            select(MetaArchetype)
            .where(MetaArchetype.snapshot_id == snapshot_id)
            .order_by(
                MetaArchetype.placement_count.desc(),
                MetaArchetype.meta_share_pct.desc(),
                MetaArchetype.name,
            )
            .limit(limit)
        )
    )


def best_decklist(db: DbSession, archetype_id: int) -> MetaDecklist | None:
    """The best-placed list of the archetype: lowest placement, unknown last."""
    return db.scalars(
        select(MetaDecklist)
        .where(MetaDecklist.archetype_id == archetype_id)
        .order_by(MetaDecklist.placement.is_(None), MetaDecklist.placement, MetaDecklist.id)
        .limit(1)
    ).first()


def commanders_for(db: DbSession, archetype_name: str, cards: list[MetaDecklistCard]) -> list[str]:
    """The commander oracle ids: from the archetype's name first, the list's row second.

    In Commander the archetype IS the commander, and a partner pair is named
    "A / B". The ingest stores that whole string as one commander row, which
    resolves to no card -- so six of the ten leading cEDH lists had "no
    commander". Splitting the name and resolving each half finds both.
    """
    names = [part.strip() for part in archetype_name.split(" / ") if part.strip()]
    resolved = [text_io.resolve_name(db, name) for name in names]
    if names and all(card is not None for card in resolved):
        return [card.oracle_id for card in resolved if card is not None]
    return [
        str(card.oracle_id)
        for card in cards
        if card.board == "commander" and card.oracle_id is not None
    ]


def deck_rows(
    db: DbSession, decklist: MetaDecklist, colors: str | None, archetype_name: str
) -> tuple[list[dict[str, Any]], int]:
    """A Commander list as deck rows, basics filling any hole left by unresolved names.

    Returns the rows and how many main-deck cards were unresolved. A list
    whose commanders cannot be found, or with too few resolved cards, comes
    back empty: those are reported, not played with a hole in them. A
    commander that the source also lists among the ninety-nine is counted
    once, on the commander board.
    """
    cards = list(
        db.scalars(select(MetaDecklistCard).where(MetaDecklistCard.decklist_id == decklist.id))
    )
    commanders = commanders_for(db, archetype_name, cards)
    if not commanders:
        return [], 0
    rows: list[dict[str, Any]] = [
        {"oracle_id": oracle_id, "board": "commander", "quantity": 1} for oracle_id in commanders
    ]
    main_total = 0
    unresolved = 0
    for card in cards:
        if card.board != "main":
            continue
        if card.oracle_id is None:
            unresolved += int(card.quantity or 1)
            continue
        if card.oracle_id in commanders:
            continue
        quantity = max(1, int(card.quantity or 1))
        rows.append({"oracle_id": card.oracle_id, "board": "main", "quantity": quantity})
        main_total += quantity
    main_size = 100 - len(commanders)
    if main_total < MIN_RESOLVED_MAIN - (len(commanders) - 1):
        return [], unresolved
    if main_total < main_size:
        rows.extend(_basic_fill(db, colors or "", main_size - main_total))
    return rows, unresolved


def materialize_top_decks(db: DbSession, *, limit: int = DEFAULT_LIMIT) -> TopDecksReport:
    """Put the leading commanders' best lists on the shelf, and take stale ones off.

    Idempotent: running it twice against the same snapshot replaces each deck
    with an identical one and prunes nothing. Decks of any other source or
    format are never read, let alone touched.
    """
    report = TopDecksReport()
    snapshot = latest_snapshot(db)
    if snapshot is None:
        log.info("meta_top_decks_no_snapshot")
        return report
    report.snapshot_id = snapshot.id

    wanted: set[str] = set()
    for archetype in leading_archetypes(db, snapshot.id, limit):
        decklist = best_decklist(db, archetype.id)
        if decklist is None:
            report.skipped.append({"archetype": archetype.name, "reason": "no decklist"})
            continue
        rows, unresolved = deck_rows(db, decklist, archetype.colors, archetype.name)
        if not rows:
            report.skipped.append(
                {
                    "archetype": archetype.name,
                    "reason": "commander not found, or too many unresolved cards",
                    "unresolved": unresolved,
                }
            )
            continue
        name = deck_name(archetype.name)
        _, outcome = _write_deck(
            db,
            name,
            COMMANDER_FORMAT,
            rows,
            {
                "archetype_key": archetype.archetype_key,
                "snapshot_id": snapshot.id,
                "decklist_id": decklist.id,
                "event": decklist.event,
                "event_date": decklist.event_date,
                "placement": decklist.placement,
                "player": decklist.player,
                "source_url": decklist.source_url,
                "unresolved": unresolved,
                "meta_share_pct": archetype.meta_share_pct,
                "top_cuts": archetype.placement_count,
            },
        )
        wanted.add(name)
        getattr(report, outcome).append(name)

    prune_stale(db, COMMANDER_FORMAT, wanted, report)
    return report


# -- the 60-card shelf --------------------------------------------------------


def sixty_label(db: DbSession, rows: list[dict[str, Any]]) -> str:
    """Two cards that say what the deck is: its most-played nonland spells."""
    counts = Counter[str]()
    names: dict[str, str] = {}
    oracle_ids = [str(r["oracle_id"]) for r in rows]
    for card in db.scalars(select(OracleCard).where(OracleCard.oracle_id.in_(oracle_ids))):
        if card.is_land:
            continue
        names[card.oracle_id] = card.name_front or card.name
    for row in rows:
        oracle_id = str(row["oracle_id"])
        if oracle_id in names:
            counts[oracle_id] += int(row["quantity"])
    ordered = sorted(counts.items(), key=lambda kv: (-kv[1], names[kv[0]]))
    return " / ".join(names[oracle_id] for oracle_id, _ in ordered[:2]) or "unnamed"


def sixty_name(format_name: str, rank: int | None, label: str, player: str, date: str) -> str:
    """What a 60-card list is called: format, finish, what it plays, who, when."""
    finish = f"top {rank}" if rank else "list"
    return f"{format_name} {finish}: {label} ({player}, {date})"


def sixty_rows(db: DbSession, main: list[tuple[str, int]]) -> tuple[list[dict[str, Any]], int]:
    """A 60-card list as deck rows; a small hole is filled with its own commonest basic."""
    rows: list[dict[str, Any]] = []
    unresolved = 0
    total = 0
    basics = Counter[str]()
    for name, quantity in main:
        card = text_io.resolve_name(db, name)
        if card is None:
            unresolved += quantity
            continue
        rows.append({"oracle_id": card.oracle_id, "board": "main", "quantity": quantity})
        total += quantity
        if card.is_land and "Basic" in (card.type_line or ""):
            basics[card.oracle_id] += quantity
    if unresolved > MAX_MISSING_SIXTY or total < 60 - MAX_MISSING_SIXTY:
        return [], unresolved
    if total < 60 and basics:
        oracle_id, _ = basics.most_common(1)[0]
        rows.append({"oracle_id": oracle_id, "board": "main", "quantity": 60 - total})
    return rows, unresolved


def materialize_sixty_top_decks(
    db: DbSession, events: list[MtgoEvent], *, per_format: int = DEFAULT_PER_FORMAT
) -> TopDecksReport:
    """Put each format's best-placed MTGO lists on the shelf, and take stale ones off.

    ``events`` are the newest Challenge results per format, already fetched
    and parsed; newer events first, so a format's ``per_format`` lists come
    from the freshest event that has enough of them.
    """
    report = TopDecksReport()
    wanted: set[str] = set()
    taken = Counter[str]()
    for event in events:
        for deck in event.top(per_format):
            if taken[event.format] >= per_format:
                break
            rows, unresolved = sixty_rows(db, deck.main)
            if not rows:
                report.skipped.append(
                    {
                        "event": event.description,
                        "player": deck.player,
                        "reason": "too many unresolved cards",
                        "unresolved": unresolved,
                    }
                )
                continue
            name = sixty_name(
                event.format, deck.rank, sixty_label(db, rows), deck.player, event.date
            )
            if name in wanted:
                continue
            _, outcome = _write_deck(
                db,
                name,
                SIXTY_FORMAT,
                rows,
                {
                    "source": "mtgo",
                    "event": event.description,
                    "event_id": event.event_id,
                    "event_date": event.date,
                    "format_name": event.format,
                    "placement": deck.rank,
                    "wins": deck.wins,
                    "losses": deck.losses,
                    "player": deck.player,
                    "source_url": event.url,
                    "sideboard": deck.sideboard,
                    "unresolved": unresolved,
                },
            )
            wanted.add(name)
            taken[event.format] += 1
            getattr(report, outcome).append(name)
    if events:
        prune_stale(db, SIXTY_FORMAT, wanted, report)
    return report
