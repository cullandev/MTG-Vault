"""Card scanning endpoints."""

from __future__ import annotations

import logging
from collections import deque
from typing import Annotated, Any

from fastapi import APIRouter, File, Form, Query, Request, UploadFile, status
from pydantic import BaseModel
from pydantic import Field as PydanticField
from sqlalchemy import desc, select

from app.deps import Config, Db
from app.errors import AppError, NotFound, TooManyRequests
from app.models import Card, ScanEvent
from app.ocr.preprocess import InvalidImage
from app.schemas.scan import (
    AccuracyResponse,
    ConfirmRequest,
    ConfirmResponse,
    IdentifyResponse,
    SessionOut,
    StartSessionRequest,
    UndoRequest,
)
from app.services.collection.add import AddSpec
from app.services.scan import accuracy as accuracy_service
from app.services.scan import fusion
from app.services.scan import identify as identify_service
from app.services.scan import session as session_service

log = logging.getLogger("mtgvault.api.scan")

router = APIRouter(prefix="/scan", tags=["scan"])

MAX_FRAME_BYTES = 4 * 1024 * 1024
"""A 480x672 JPEG is tens of kilobytes; anything near this is not a card crop."""

MAX_DIAG_BYTES = 16 * 1024

_recent_diagnostics: deque[dict[str, Any]] = deque(maxlen=200)
"""Ring buffer of the latest client diagnostics. In-memory is fine: one worker
(ADR-014), and the point is live debugging, not history -- everything is also
written to the structured log for the durable trail."""


class DiagnosticsIn(BaseModel):
    """One telemetry report from the scanner page.

    ``data`` is deliberately schemaless: the client-side loop evolves faster than a
    backend contract should, and this endpoint's job is to make whatever the client
    saw readable on the server, not to validate it.
    """

    kind: str = PydanticField(max_length=40)
    session_id: str | None = PydanticField(default=None, max_length=64)
    data: dict[str, Any]


@router.post("/sessions", response_model=SessionOut, status_code=status.HTTP_201_CREATED)
def start_session(body: StartSessionRequest, db: Db) -> SessionOut:
    """Open a scanning session."""
    session = session_service.start_session(db, device=body.device)
    return SessionOut(session_id=session.id, started_at=session.started_at, added_count=0)


@router.get("/sessions/{session_id}", response_model=SessionOut)
def session_state(session_id: str, db: Db) -> SessionOut:
    """Running count, the last few cards added, and how many frames missed."""
    return SessionOut(**session_service.state(db, session_id).as_dict())


@router.post("/sessions/{session_id}/end", response_model=SessionOut)
def end_session(session_id: str, db: Db) -> SessionOut:
    """Close a session."""
    session_service.end_session(db, session_id)
    return SessionOut(**session_service.state(db, session_id).as_dict())


@router.post("/identify", response_model=IdentifyResponse)
async def identify(
    db: Db,
    settings: Config,
    image: Annotated[UploadFile, File()],
    session_id: Annotated[str | None, Form()] = None,
    seq: Annotated[int | None, Form()] = None,
) -> IdentifyResponse:
    """Identify a card from one rectified frame.

    Returns ``429`` when every OCR slot is busy. That is not an error condition -- the
    phone is expected to drop the frame and send the next one, because by the time a
    queued frame got its turn the card would have moved.
    """
    raw = await image.read(MAX_FRAME_BYTES + 1)
    if len(raw) > MAX_FRAME_BYTES:
        raise AppError(
            "Frame is too large",
            code="payload_too_large",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"max_bytes": MAX_FRAME_BYTES},
        )

    try:
        result = await identify_service.identify(db, settings, raw, session_id=session_id)
    except identify_service.ScanBusy as exc:
        raise TooManyRequests(
            "Scanner is busy",
            detail={"retry_after_ms": 250, "reason": "ocr_saturated"},
        ) from exc
    except InvalidImage as exc:
        raise AppError(str(exc), code="invalid_image", status_code=422) from exc

    return IdentifyResponse(**result.as_dict(), seq=seq)


@router.post("/confirm", response_model=ConfirmResponse, status_code=status.HTTP_201_CREATED)
def confirm(body: ConfirmRequest, db: Db) -> ConfirmResponse:
    """Add a locked-in card to the collection.

    Accepts an ``idempotency_key``: the scanner auto-adds on lock-in, and a retried
    request on a flaky phone connection must not turn one card into two.
    """
    replay = session_service.replay_idempotent(db, body.idempotency_key, "scan.confirm")
    if replay is not None:
        return ConfirmResponse(**replay)

    spec = _spec_from(body, db)
    items, batch, added = session_service.confirm(
        db,
        session_id=body.session_id,
        spec=spec,
        quantity=body.quantity,
        event_id=body.event_id,
        source=body.source,
    )
    # A confirmed card must not seed the next one: without this, a picker-based
    # confirm left the card's accumulated evidence alive for several seconds and
    # the NEXT card off the stack could inherit its candidate list wholesale.
    # (The exact-lock path already clears inside identify.)
    fusion.get_accumulator().clear(body.session_id)
    state = session_service.state(db, body.session_id)
    response = ConfirmResponse(
        item_ids=[item.id for item in items],
        batch_id=batch,
        added=added.as_dict(),
        running_count=state.added_count,
        running_value_cents=state.value_cents,
        last_added=[card.as_dict() for card in state.last_added],
    )
    session_service.remember_idempotent(
        db, body.idempotency_key, "scan.confirm", response.model_dump()
    )
    return response


def _spec_from(body: ConfirmRequest, db: Db) -> AddSpec:
    """Turn whatever identification the client had into an :class:`AddSpec`."""
    set_code, collector_number, oracle_id = body.set_code, body.collector_number, body.oracle_id
    if body.card_id is not None:
        card = db.scalars(select(Card).where(Card.id == body.card_id)).first()
        if card is None:
            raise NotFound(f"No card {body.card_id}")
        set_code, collector_number, oracle_id = (
            card.set_code,
            card.collector_number,
            card.oracle_id,
        )
    if not any((set_code and collector_number, oracle_id)):
        raise AppError(
            "Identify the card by card_id, by oracle_id, or by set and collector number",
            code="unidentified_card",
            status_code=422,
        )
    return AddSpec(
        oracle_id=oracle_id,
        set_code=set_code,
        collector_number=collector_number,
        lang=body.lang,
        finish=body.finish,
        condition=body.condition,
        is_proxy=body.is_proxy,
    )


class RejectRequest(BaseModel):
    """Body of ``POST /api/scan/reject``: the identification Rescan dismissed."""

    session_id: str
    event_id: int


@router.post("/reject", status_code=status.HTTP_200_OK)
def reject(body: RejectRequest, db: Db) -> dict[str, Any]:
    """Tag a scan the user rescanned away from, for the accuracy review.

    The next accepted scan in the session is linked back to it, giving every
    rescan a reviewable (what was proposed, what was kept) pair. The rejected
    lead rides back in the response so the scanner can stop re-proposing it
    for the rest of the sitting -- the same suppression "None of these" gets.
    """
    event = session_service.reject(db, body.session_id, body.event_id)
    return {
        "event_id": event.id,
        "rejected_at": event.rejected_at,
        "rejected_card_id": event.first_match_card_id,
        "rejected_oracle_id": event.first_match_oracle_id,
    }


@router.get("/rejections")
def rejections(db: Db, limit: int = 20) -> dict[str, Any]:
    """Recent rescans with what was proposed, why, and what was finally kept."""
    rows = list(
        db.scalars(
            select(ScanEvent)
            .where(ScanEvent.rejected_at.is_not(None))
            .order_by(desc(ScanEvent.id))
            .limit(min(limit, 100))
        )
    )
    out = []
    for event in rows:
        detail = event.detail_json or {}
        proposed = db.get(Card, event.first_match_card_id) if event.first_match_card_id else None
        accepted_name = None
        accepted_method = None
        accepted_card_id = detail.get("superseded_card_id")
        if event.superseded_by_event_id is not None:
            accepted = db.get(ScanEvent, event.superseded_by_event_id)
            if accepted is not None:
                accepted_method = accepted.method
                accepted_card_id = accepted.confirmed_card_id or accepted_card_id
        if accepted_card_id:
            accepted_card = db.get(Card, int(accepted_card_id))
            accepted_name = accepted_card.name if accepted_card else None
        out.append(
            {
                "event_id": event.id,
                "ts": event.ts,
                "rejected_at": event.rejected_at,
                "proposed_name": proposed.name if proposed else None,
                "proposed_set": proposed.set_code if proposed else None,
                "method": event.method,
                "fuzz_score": event.fuzz_score,
                "ocr_text": (event.ocr_text or "")[:80] or None,
                "diagnosis": detail.get("diagnosis"),
                "accepted_name": accepted_name,
                "accepted_method": accepted_method,
            }
        )
    return {"rejections": out}


@router.post("/undo", status_code=status.HTTP_200_OK)
def undo(body: UndoRequest, db: Db) -> dict[str, object]:
    """Undo one lock-in, from the 1.5 second toast."""
    reverted = session_service.undo(db, body.session_id, body.batch_id)
    state = session_service.state(db, body.session_id)
    return {
        "reverted": reverted,
        "running_count": state.added_count,
        "running_value_cents": state.value_cents,
        "last_added": [card.as_dict() for card in state.last_added],
    }


@router.post("/diagnostics", status_code=status.HTTP_204_NO_CONTENT)
async def record_diagnostics(request: Request) -> None:
    """Accept scanner telemetry from the client.

    The phone's console is unreachable from the server room; this is how "it just
    sits there" becomes a readable record of what the detection loop actually saw.

    The body is parsed by hand rather than through a typed parameter because
    ``navigator.sendBeacon`` can only send CORS-safelisted content types -- the
    page-leave beacon arrives as ``text/plain``, which FastAPI's JSON body
    handling would reject.
    """
    raw = await request.body()
    if len(raw) > MAX_DIAG_BYTES:
        raise AppError(
            "Diagnostics payload too large",
            code="payload_too_large",
            status_code=status.HTTP_413_REQUEST_ENTITY_TOO_LARGE,
            detail={"max_bytes": MAX_DIAG_BYTES},
        )
    try:
        body = DiagnosticsIn.model_validate_json(raw)
    except ValueError as exc:
        raise AppError(
            "Malformed diagnostics payload", code="invalid_diagnostics", status_code=422
        ) from exc
    from app.models import utcnow

    entry = {"ts": utcnow(), "kind": body.kind, "session_id": body.session_id, **body.data}
    _recent_diagnostics.append(entry)
    log.info(
        "scan_client_diag",
        extra={"kind": body.kind, "session_id": body.session_id, "diag": body.data},
    )


@router.get("/diagnostics/recent")
def recent_diagnostics(limit: int = Query(default=50, ge=1, le=200)) -> dict[str, Any]:
    """The latest client telemetry, newest first, for live debugging."""
    entries = list(_recent_diagnostics)[-limit:]
    entries.reverse()
    return {"entries": entries}


@router.get("/stats", response_model=AccuracyResponse)
def stats(
    db: Db,
    window_days: int = Query(default=30, ge=1, le=365),
    include_misses: bool = True,
) -> AccuracyResponse:
    """Scan accuracy, so OCR degradation is visible rather than a slow surprise."""
    computed = accuracy_service.scan_accuracy(db, window_days)
    return AccuracyResponse(
        **computed.as_dict(),
        recent_misses=accuracy_service.recent_misses(db) if include_misses else [],
        daily=accuracy_service.daily_counts(db),
    )
