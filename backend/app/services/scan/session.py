"""Scanning sessions: confirming a lock-in, the running count, and undo.

A session is one sitting at the scanner. Everything added during it shares a single
audit batch id, so "I just scanned that whole box wrong" is one undo rather than four
hundred, while each individual lock-in also stays undoable on its own from the toast.
"""

from __future__ import annotations

import logging
import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session as DbSession

from app.constants import PRICE_NOTE
from app.errors import Conflict, NotFound
from app.models import (
    AuditLog,
    Card,
    CollectionItem,
    IdempotencyKey,
    ScanEvent,
    ScanSession,
    utcnow,
)
from app.services import audit
from app.services.collection.add import AddSpec, add_copies

log = logging.getLogger("mtgvault.scan.session")

LAST_ADDED_LIMIT = 5
"""How many recent cards the scanner's bottom strip shows."""


@dataclass
class AddedCard:
    """One entry in the scanner's "last added" strip."""

    batch_id: str
    oracle_id: str
    card_id: int | None
    name: str
    set_code: str
    quantity: int
    finish: str
    image_url: str | None
    added_at: str

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "batch_id": self.batch_id,
            "oracle_id": self.oracle_id,
            "card_id": self.card_id,
            "name": self.name,
            "set_code": self.set_code,
            "quantity": self.quantity,
            "finish": self.finish,
            "image_url": self.image_url,
            "added_at": self.added_at,
        }


@dataclass
class SessionState:
    """The scanner's running state for one sitting."""

    session_id: str
    started_at: str
    added_count: int
    value_cents: int = 0
    """What has been scanned so far is worth. Proxies contribute nothing, and a card
    with no price contributes nothing either -- ``unpriced`` says how many those were,
    so the running total is never quietly wrong."""
    unpriced: int = 0
    last_added: list[AddedCard] = field(default_factory=list)
    events: int = 0
    misses: int = 0

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "session_id": self.session_id,
            "started_at": self.started_at,
            "added_count": self.added_count,
            "value_cents": self.value_cents,
            "unpriced": self.unpriced,
            "last_added": [card.as_dict() for card in self.last_added],
            "events": self.events,
            "misses": self.misses,
            "price_note": PRICE_NOTE,
        }


def start_session(db: DbSession, *, device: str | None = None) -> ScanSession:
    """Open a new scanning session."""
    session = ScanSession(id=uuid.uuid4().hex, device=(device or "")[:200] or None)
    db.add(session)
    db.flush()
    return session


def get_session(db: DbSession, session_id: str) -> ScanSession:
    """Fetch a session or raise."""
    session = db.get(ScanSession, session_id)
    if session is None:
        raise NotFound(f"No scan session {session_id}")
    return session


def end_session(db: DbSession, session_id: str) -> ScanSession:
    """Close a session so it stops appearing as active."""
    session = get_session(db, session_id)
    session.ended_at = utcnow()
    db.flush()
    return session


def replay_idempotent(db: DbSession, key: str | None, endpoint: str) -> dict[str, Any] | None:
    """Return the stored response for a repeated request, if there is one.

    The scanner auto-adds on lock-in and phones drop connections; without this a
    retried POST turns one card into two.
    """
    if not key:
        return None
    row = db.get(IdempotencyKey, key)
    if row is None:
        return None
    if row.endpoint != endpoint:
        raise Conflict(
            "That idempotency key was already used for a different request",
            detail={"key": key, "original_endpoint": row.endpoint},
        )
    return row.response_json or {}


def remember_idempotent(
    db: DbSession, key: str | None, endpoint: str, response: dict[str, Any]
) -> None:
    """Store a response so a replay of the same key returns it verbatim."""
    if not key:
        return
    db.add(IdempotencyKey(key=key, endpoint=endpoint, response_json=response))
    db.flush()


def confirm(
    db: DbSession,
    *,
    session_id: str,
    spec: AddSpec,
    quantity: int = 1,
    event_id: int | None = None,
    source: str | None = None,
) -> tuple[list[CollectionItem], str, AddedCard]:
    """Add the locked-in card to the collection and close the loop on the scan event.

    Args:
        db: Open database session.
        session_id: The session this lock-in belongs to.
        spec: What to add, already resolved by the scanner or the picker.
        quantity: Copies to add. The stepper exists for basics and bulk duplicates.
        event_id: The ``scan_events`` row this confirms, so the accuracy statistic can
            compare what was proposed against what was kept.
        source: How the confirm happened (``auto``, ``tap``, ``close_matches``,
            ``name``), recorded on the event for per-path accuracy analysis.

    Returns:
        The created rows, the audit batch id for the undo toast, and the strip entry.
    """
    session = get_session(db, session_id)

    # Each lock-in gets its own batch so the 1.5 second undo toast can revert exactly
    # that card. The session id is recorded on the audit entry as well, which is what
    # makes "undo the whole session" possible later.
    items, batch = add_copies(db, spec, quantity, source="scan", note=f"scan session {session_id}")

    session.added_count += len(items)
    db.flush()

    card = db.get(Card, items[0].card_id) if items[0].card_id else None
    if event_id is not None:
        event = db.get(ScanEvent, event_id)
        if event is not None:
            event.confirmed_card_id = items[0].card_id
            event.confirmed_oracle_id = items[0].oracle_id
            if event.session_id is None:
                event.session_id = session_id
            # How the confirm happened (auto-picker, tap, close-matches, typed
            # name): the per-path accuracy question was unanswerable before.
            detail = dict(event.detail_json or {})
            # "unknown", never a guessed "tap": a stale frontend bundle sends no
            # source at all, and fabricating one would pollute the very per-path
            # statistic this field exists to collect.
            detail["confirm_source"] = source or "unknown"
            detail["confirmed_at"] = utcnow()
            event.detail_json = detail
            db.flush()

    # Close the loop on any rescans this acceptance supersedes: every rejected,
    # not-yet-linked event in the session becomes half of a (proposed, accepted)
    # review pair. Linked whether the acceptance came from a fresh scan or the
    # typed-name fallback -- the review cares what was finally kept, not how.
    for rejected in db.scalars(
        select(ScanEvent).where(
            ScanEvent.session_id == session_id,
            ScanEvent.rejected_at.is_not(None),
            ScanEvent.superseded_by_event_id.is_(None),
            ScanEvent.id != (event_id or -1),
        )
    ):
        # 0 is the "accepted without a scan event" sentinel (the typed-name
        # fallback): the link must become terminal either way, or every later
        # confirm in the session would keep rewriting this pair.
        rejected.superseded_by_event_id = event_id if event_id is not None else 0
        detail = dict(rejected.detail_json or {})
        detail["superseded_card_id"] = items[0].card_id
        detail["superseded_oracle_id"] = items[0].oracle_id
        rejected.detail_json = detail
    db.flush()

    added = AddedCard(
        batch_id=batch,
        oracle_id=items[0].oracle_id,
        card_id=items[0].card_id,
        name=card.name if card else items[0].oracle_id,
        set_code=items[0].set_code,
        quantity=len(items),
        finish=items[0].finish,
        image_url=f"/api/images/{card.id}/normal" if card and card.image_normal_url else None,
        added_at=items[0].created_at,
    )
    log.info(
        "scan_confirm",
        extra={"session": session_id, "card": added.name, "quantity": len(items)},
    )
    return items, batch, added


def reject(db: DbSession, session_id: str, event_id: int) -> ScanEvent:
    """Tag an identification the user rescanned away from.

    The only ground-truth "that was wrong" signal that costs the user nothing:
    they were pressing Rescan anyway. The next accepted scan in the session
    links back to this event in :func:`confirm`.

    Raises:
        NotFound: The event does not exist or belongs to another session.
    """
    event = db.get(ScanEvent, event_id)
    if event is None or (event.session_id is not None and event.session_id != session_id):
        raise NotFound(f"No scan event {event_id} in this session")
    if event.session_id is None:
        event.session_id = session_id
    if event.rejected_at is None:
        event.rejected_at = utcnow()
    db.flush()
    return event


def undo(db: DbSession, session_id: str, batch_id: str) -> int:
    """Undo one lock-in.

    Raises:
        NotFound: The batch does not belong to this session.
    """
    session = get_session(db, session_id)
    entries = list(db.scalars(select(AuditLog).where(AuditLog.batch_id == batch_id)))
    if not entries:
        raise NotFound(f"No audit batch {batch_id}")
    if not any(entry.note and session_id in entry.note for entry in entries):
        raise NotFound("That batch does not belong to this scan session")

    result = audit.revert_batch(db, batch_id, note=f"undo during scan session {session_id}")
    session.added_count = max(0, session.added_count - result.reverted)
    db.flush()
    return result.reverted


def state(db: DbSession, session_id: str) -> SessionState:
    """Return the running count, the last few cards added, and the miss count."""
    session = get_session(db, session_id)

    events = list(
        db.scalars(
            select(ScanEvent).where(ScanEvent.session_id == session_id).order_by(desc(ScanEvent.id))
        )
    )
    misses = sum(1 for event in events if event.first_match_oracle_id is None)

    entries = list(
        db.scalars(
            select(AuditLog)
            .where(
                AuditLog.source == "scan",
                AuditLog.note == f"scan session {session_id}",
                AuditLog.reverted_at.is_(None),
            )
            .order_by(desc(AuditLog.id))
            .limit(LAST_ADDED_LIMIT)
        )
    )

    last_added: list[AddedCard] = []
    for entry in entries:
        payload = entry.after_json or {}
        summary = payload.get("summary") if isinstance(payload, dict) else None
        card = (summary or {}).get("card") if isinstance(summary, dict) else None
        if card is None and isinstance(payload, dict):
            # Single-copy adds store the row snapshot directly rather than a summary.
            card = {
                "oracle_id": payload.get("oracle_id"),
                "set_code": payload.get("set_code"),
                "card_id": payload.get("card_id"),
                "name": payload.get("oracle_id"),
            }
        rows = payload.get("rows") if isinstance(payload, dict) else None
        quantity = len(rows) if isinstance(rows, list) else 1
        card_id = card.get("card_id") if isinstance(card, dict) else None
        resolved = db.get(Card, card_id) if card_id else None
        last_added.append(
            AddedCard(
                batch_id=entry.batch_id,
                oracle_id=str((card or {}).get("oracle_id") or ""),
                card_id=card_id,
                name=resolved.name if resolved else str((card or {}).get("name") or "Unknown"),
                set_code=str((card or {}).get("set_code") or ""),
                quantity=quantity,
                finish=str((summary or {}).get("finish") or payload.get("finish") or "nonfoil"),
                image_url=(
                    f"/api/images/{resolved.id}/normal"
                    if resolved and resolved.image_normal_url
                    else None
                ),
                added_at=entry.ts,
            )
        )

    value_cents, unpriced = _session_value(db, session_id)
    return SessionState(
        session_id=session.id,
        started_at=session.started_at,
        added_count=session.added_count,
        value_cents=value_cents,
        unpriced=unpriced,
        last_added=last_added,
        events=len(events),
        misses=misses,
    )


def _session_value(db: DbSession, session_id: str) -> tuple[int, int]:
    """What this session has scanned so far, and how many copies had no price.

    Finish-aware and proxy-excluding, exactly like the collection totals -- the two
    must never disagree about what a card is worth.
    """
    item_ids = _session_item_ids(db, session_id)
    if not item_ids:
        return 0, 0

    ordered = sorted(item_ids)
    rows: list[tuple[CollectionItem, Card | None]] = []
    for start in range(0, len(ordered), 500):
        chunk = ordered[start : start + 500]
        for item, card in db.execute(
            select(CollectionItem, Card)
            .join(Card, Card.id == CollectionItem.card_id, isouter=True)
            .where(CollectionItem.id.in_(chunk))
        ):
            rows.append((item, card))

    total = 0
    unpriced = 0
    for item, card in rows:
        if item.is_proxy or card is None:
            continue
        if item.finish == "foil":
            price = card.price_usd_foil_cents or card.price_usd_cents
        elif item.finish == "etched":
            price = card.price_usd_etched_cents or card.price_usd_cents
        else:
            price = card.price_usd_cents
        if price is None:
            unpriced += 1
        else:
            total += price
    return total, unpriced


def _session_item_ids(db: DbSession, session_id: str) -> set[int]:
    """Collection item ids added by this session and not since undone.

    The ids live in the audit entries' row snapshots, which is the only record that
    ties a physical copy back to the sitting that scanned it.
    """
    entries = db.scalars(
        select(AuditLog).where(
            AuditLog.source == "scan",
            AuditLog.note == f"scan session {session_id}",
            AuditLog.reverted_at.is_(None),
        )
    )
    item_ids: set[int] = set()
    for entry in entries:
        payload = entry.after_json
        if not isinstance(payload, dict):
            continue
        rows = payload.get("rows")
        if isinstance(rows, list):
            item_ids.update(
                int(row["id"]) for row in rows if isinstance(row, dict) and row.get("id")
            )
        elif payload.get("id"):
            item_ids.add(int(payload["id"]))
    return item_ids
