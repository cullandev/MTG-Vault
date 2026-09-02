"""Scan request/response models."""

from __future__ import annotations

from typing import Any, Literal

from pydantic import BaseModel, Field

from app.constants import PRICE_NOTE
from app.schemas.collection import Condition, Finish


class StartSessionRequest(BaseModel):
    """Body of ``POST /api/scan/sessions``."""

    device: str | None = Field(default=None, max_length=200)


class SessionOut(BaseModel):
    """A scanning session's running state."""

    session_id: str
    started_at: str
    added_count: int
    value_cents: int = 0
    unpriced: int = 0
    last_added: list[dict[str, Any]] = []
    events: int = 0
    misses: int = 0
    price_note: str = PRICE_NOTE


class IdentifyResponse(BaseModel):
    """Result of one identification attempt."""

    match: dict[str, Any] | None
    candidates: list[dict[str, Any]]
    detections: list[dict[str, Any]] = []
    """Card outlines the server found in the frame, so the overlay draws what was
    actually seen rather than what the phone guessed."""
    stage_ms: dict[str, float] = {}
    """Per-stage timings. The pipeline escalates from cheap signals to expensive ones
    and stops early, so which stages ran is the clearest measure of how it did."""
    confidence: float
    fuzz_score: float
    ocr_text: str
    collector_text: str = ""
    """What the bottom-left corner read, kept even when it did not resolve so a
    mis-aimed crop is visible in the diagnostics rather than silently invisible."""
    method: str
    ambiguous: bool
    clipped: int = 0
    """Card-shaped regions that ran off the frame edge, so the whole card was not in
    view. Actionable in a way that "no card found" is not."""
    exact: bool = False
    """The evidence is conclusive -- a resolved collector line, an unmistakable
    artwork match, or two weaker signals agreeing. The overlay locks in on this single
    frame when it is set, instead of waiting for frames to agree."""
    latency_ms: float
    event_id: int | None
    seq: int | None = None


class ConfirmRequest(BaseModel):
    """Body of ``POST /api/scan/confirm``.

    Identify the card as precisely as the scanner managed: a locked-in match sends
    ``card_id``; the printing picker sends ``set_code`` and ``collector_number``; the
    manual search box sends ``oracle_id``.
    """

    session_id: str
    card_id: int | None = None
    oracle_id: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    event_id: int | None = None
    quantity: int = Field(default=1, ge=1, le=500)
    finish: Finish = "nonfoil"
    condition: Condition = "NM"
    lang: str = "en"
    is_proxy: bool = False
    idempotency_key: str | None = Field(default=None, max_length=200)
    source: Literal["auto", "tap", "close_matches", "name"] | None = None
    """How the confirm happened: the auto-picker firing, a tap on the candidate
    list, the close-matches picker, or the typed-name fallback. Recorded on the
    scan event so accuracy can be tuned per path instead of in aggregate."""


class ConfirmResponse(BaseModel):
    """Result of a confirmed lock-in."""

    item_ids: list[int]
    batch_id: str
    added: dict[str, Any]
    running_count: int
    running_value_cents: int = 0
    last_added: list[dict[str, Any]]


class UndoRequest(BaseModel):
    """Body of ``POST /api/scan/undo``."""

    session_id: str
    batch_id: str


class AccuracyResponse(BaseModel):
    """Scan health over a window."""

    window_days: int
    events: int
    confirmed: int
    correct: int
    unconfirmed: int
    misses: int
    first_match_accuracy: float | None
    method_mix: dict[str, int]
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    mean_fuzz_score: float | None
    recent_misses: list[dict[str, Any]] = []
    daily: list[dict[str, Any]] = []
