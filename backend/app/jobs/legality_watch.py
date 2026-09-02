"""Banlist watch: turn legality changes into flags on the decks they touch.

Runs after every bulk refresh (the import writes ``legality_changes`` rows). Each
affected deck is re-validated with ``banlist_flag`` set and ``triggered_by``
``legality_change``, and one notification per deck lands in the inbox -- in the
right format only: a Modern banning does not flag a Commander deck
(TEST-PLAN Phase 5).
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.db import session_scope
from app.jobs.runner import job_run
from app.models import Deck, DeckCard, LegalityChange, Notification, OracleCard, Setting
from app.services.decks import validate_service

JOB_NAME = "legality_watch"
_MARKER_KEY = "legality_watch_last_change_id"

log = logging.getLogger("mtgvault.jobs.legality")


def process_changes(db: DbSession) -> dict[str, int]:
    """Handle every legality change past the high-water mark.

    Returns:
        ``{"changes": n, "decks_flagged": n}``.
    """
    marker = db.get(Setting, _MARKER_KEY)
    stored = (marker.value_json or {}).get("last_id") if marker else None
    last_seen = int(stored) if isinstance(stored, int) else 0

    changes = list(
        db.scalars(
            select(LegalityChange).where(LegalityChange.id > last_seen).order_by(LegalityChange.id)
        )
    )
    flagged: set[int] = set()
    for change in changes:
        decks = db.scalars(
            select(Deck)
            .join(DeckCard, DeckCard.deck_id == Deck.id)
            .where(
                DeckCard.oracle_id == change.oracle_id,
                Deck.format == change.format,
                Deck.archived.is_(False),
            )
            .distinct()
        )
        for deck in decks:
            validate_service.validate_and_record(
                db, deck, triggered_by="legality_change", banlist_flag=True
            )
            oracle = db.get(OracleCard, change.oracle_id)
            card_name = oracle.name if oracle else change.oracle_id
            db.add(
                Notification(
                    kind="legality_change",
                    title=f"{card_name} is now {change.new_status} in {change.format}",
                    body=(
                        f'The deck "{deck.name}" plays it. '
                        f"It was {change.old_status}; the deck has been re-checked."
                    ),
                    link=f"/decks/{deck.id}",
                )
            )
            flagged.add(deck.id)

    if changes:
        if marker is None:
            marker = Setting(key=_MARKER_KEY)
            db.add(marker)
        marker.value_json = {"last_id": changes[-1].id}
    db.flush()
    return {"changes": len(changes), "decks_flagged": len(flagged)}


async def run() -> None:
    """Scheduled entry point."""
    with job_run(JOB_NAME) as context:
        with session_scope() as db:
            counts = process_changes(db)
        context.report(**counts)
