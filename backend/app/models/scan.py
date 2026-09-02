"""Scanning sessions, per-frame events, and idempotency keys.

``scan_events`` exists so OCR degradation is *visible*: comparing the first match the
backend proposed against the card the user actually confirmed gives a real accuracy
number, per day, without anyone having to keep score by hand (ARCHITECTURE.md
section 10).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import JSON, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow

SCAN_METHODS = ("collector", "visual", "name", "fused", "manual", "none")
"""Which signal carried an identification (ADR-024). Recorded per frame so the
accuracy statistic can be read per signal -- a drop in ``visual`` means the hash index
is stale, a drop in ``collector`` means the corner crop has drifted."""


class ScanSession(Base):
    """One sitting at the scanner: a stack of cards, one running count."""

    __tablename__ = "scan_sessions"

    id: Mapped[str] = mapped_column(Text(), primary_key=True)
    started_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    ended_at: Mapped[str | None] = mapped_column(Text())
    device: Mapped[str | None] = mapped_column(Text())
    added_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    note: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (Index("ix_scan_sessions_started_at", "started_at"),)


class ScanEvent(Base):
    """One identification attempt.

    ``first_match_card_id`` is what the backend proposed; ``confirmed_card_id`` is what
    the user actually kept. Equality between the two, over a window, is the scan
    accuracy statistic.
    """

    __tablename__ = "scan_events"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ts: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    session_id: Mapped[str | None] = mapped_column(
        ForeignKey("scan_sessions.id", ondelete="SET NULL")
    )
    first_match_card_id: Mapped[int | None] = mapped_column(Integer())
    first_match_oracle_id: Mapped[str | None] = mapped_column(Text())
    confirmed_card_id: Mapped[int | None] = mapped_column(Integer())
    confirmed_oracle_id: Mapped[str | None] = mapped_column(Text())
    method: Mapped[str] = mapped_column(Text(), nullable=False, default="ocr")
    ocr_text: Mapped[str | None] = mapped_column(Text())
    ocr_confidence: Mapped[float | None] = mapped_column()
    fuzz_score: Mapped[float | None] = mapped_column()
    candidate_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    latency_ms: Mapped[float | None] = mapped_column()
    rejected_at: Mapped[str | None] = mapped_column(Text())
    """Set when the user hit Rescan on this identification: ground truth that it
    was wrong (or at least unwanted) before anything was added."""
    superseded_by_event_id: Mapped[int | None] = mapped_column(Integer())
    """The accepted scan event that followed a rejection in the same session --
    the (proposed, accepted) pair the review screen shows."""
    detail_json: Mapped[dict[str, Any] | None] = mapped_column(JSON())
    """Per-stage timings, detection counts and the winning score.

    ``latency_ms`` says a frame was slow; this says *which rung* was slow, and whether
    the frame even held a card. Without it a session's data can only raise the
    question. Same shape and intent as ``job_runs.detail_json``."""

    __table_args__ = (
        Index("ix_scan_events_ts", "ts"),
        Index("ix_scan_events_session_id_ts", "session_id", "ts"),
    )


class IdempotencyKey(Base):
    """A replayed mutating request returns its original response.

    The scanner auto-adds on lock-in; a retried POST on a flaky phone connection must
    not turn one card into two.
    """

    __tablename__ = "idempotency_keys"

    key: Mapped[str] = mapped_column(Text(), primary_key=True)
    endpoint: Mapped[str] = mapped_column(Text(), nullable=False)
    response_json: Mapped[dict[str, Any] | None] = mapped_column()
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_idempotency_keys_created_at", "created_at"),)
