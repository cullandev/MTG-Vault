"""Scan accuracy statistics.

The requirement is that OCR degradation is *visible* rather than something you notice
six months later. The measure is deliberately simple and honest: of the frames where a
card was actually confirmed, how often was the backend's first proposal the card the
user kept?

Frames that were never confirmed are excluded from the accuracy figure entirely --
counting a frame the camera caught mid-wobble as a "miss" would make the number
meaningless. They are reported separately as ``unconfirmed``.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import ScanEvent


@dataclass
class AccuracyStats:
    """Scan health over a window."""

    window_days: int
    events: int
    confirmed: int
    correct: int
    unconfirmed: int
    misses: int
    """Frames where OCR produced no candidate at all."""
    first_match_accuracy: float | None
    method_mix: dict[str, int]
    p50_latency_ms: float | None
    p95_latency_ms: float | None
    mean_fuzz_score: float | None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "window_days": self.window_days,
            "events": self.events,
            "confirmed": self.confirmed,
            "correct": self.correct,
            "unconfirmed": self.unconfirmed,
            "misses": self.misses,
            "first_match_accuracy": self.first_match_accuracy,
            "method_mix": self.method_mix,
            "p50_latency_ms": self.p50_latency_ms,
            "p95_latency_ms": self.p95_latency_ms,
            "mean_fuzz_score": self.mean_fuzz_score,
        }


def _percentile(values: list[float], fraction: float) -> float | None:
    if not values:
        return None
    ordered = sorted(values)
    # Nearest-rank: with a handful of samples this is more honest than interpolating
    # between values that were never measured.
    index = min(len(ordered) - 1, max(0, round(fraction * (len(ordered) - 1))))
    return round(ordered[index], 1)


def scan_accuracy(db: DbSession, window_days: int = 30) -> AccuracyStats:
    """Compute scan accuracy over the last ``window_days``.

    Args:
        db: Open database session.
        window_days: Size of the window, in days.

    Returns:
        Counts, the accuracy ratio, the method mix and latency percentiles.
    """
    since = (datetime.now(tz=UTC) - timedelta(days=window_days)).isoformat()
    events = list(db.scalars(select(ScanEvent).where(ScanEvent.ts >= since)))

    confirmed = [event for event in events if event.confirmed_oracle_id is not None]
    correct = sum(
        1
        for event in confirmed
        if event.first_match_oracle_id is not None
        and event.first_match_oracle_id == event.confirmed_oracle_id
    )
    misses = sum(1 for event in events if event.first_match_oracle_id is None)

    method_mix: dict[str, int] = {}
    for event in events:
        method_mix[event.method] = method_mix.get(event.method, 0) + 1

    latencies = [event.latency_ms for event in events if event.latency_ms is not None]
    scores = [event.fuzz_score for event in events if event.fuzz_score]

    return AccuracyStats(
        window_days=window_days,
        events=len(events),
        confirmed=len(confirmed),
        correct=correct,
        unconfirmed=len(events) - len(confirmed),
        misses=misses,
        first_match_accuracy=(round(correct / len(confirmed), 4) if confirmed else None),
        method_mix=method_mix,
        p50_latency_ms=_percentile(latencies, 0.50),
        p95_latency_ms=_percentile(latencies, 0.95),
        mean_fuzz_score=(round(sum(scores) / len(scores), 1) if scores else None),
    )


def recent_misses(db: DbSession, limit: int = 20) -> list[dict[str, Any]]:
    """The most recent frames OCR could not turn into a candidate.

    Useful for spotting a pattern -- a whole set with an unusual title placement, say --
    rather than guessing at why the hit rate dropped.
    """
    rows = db.scalars(
        select(ScanEvent)
        .where(ScanEvent.first_match_oracle_id.is_(None))
        .order_by(ScanEvent.id.desc())
        .limit(limit)
    )
    return [
        {
            "ts": event.ts,
            "ocr_text": event.ocr_text,
            "ocr_confidence": event.ocr_confidence,
            "fuzz_score": event.fuzz_score,
            "latency_ms": event.latency_ms,
        }
        for event in rows
    ]


def daily_counts(db: DbSession, window_days: int = 14) -> list[dict[str, Any]]:
    """Per-day event and confirmation counts, for a sparkline on the dashboard."""
    since = (datetime.now(tz=UTC) - timedelta(days=window_days)).isoformat()
    day = func.substr(ScanEvent.ts, 1, 10)
    rows = db.execute(
        select(
            day.label("day"),
            func.count(ScanEvent.id),
            func.sum(
                func.coalesce(func.nullif(ScanEvent.confirmed_oracle_id, ""), None).is_not(None)
            ),
        )
        .where(ScanEvent.ts >= since)
        .group_by(day)
        .order_by(day)
    ).all()
    return [
        {"day": str(row[0]), "events": int(row[1] or 0), "confirmed": int(row[2] or 0)}
        for row in rows
    ]
