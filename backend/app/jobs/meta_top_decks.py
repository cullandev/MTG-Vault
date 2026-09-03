"""Put the leading tournament lists on the shelf as playable decks.

Two shelves: the cEDH Commander lists the meta snapshot already holds, and
the newest MTGO Challenge results for the 60-card formats the operator
asked for (``TOP_DECK_FORMATS``, Modern and Standard by default), fetched
here if ``mtgo`` is among the opted-in meta sources (ADR-016). Runs after
the weekly snapshot and on demand from the Arena. The work is in
:mod:`app.services.meta.top_decks`; this wraps it in a job run and says what
changed, so a new top deck is a notification rather than a surprise in the
picker.
"""

from __future__ import annotations

import datetime as dt
import logging

from app.clients.base import SourceResponseError, SourceUnavailable
from app.clients.mtgo import EventRef, MtgoClient, MtgoEvent
from app.config import Settings, get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import Notification
from app.services.meta import top_decks

JOB_NAME = "meta_top_decks"

#: Newest Challenges to read per format; the shelf fills from the first that
#: has enough lists, the rest are there for a thin week.
EVENTS_PER_FORMAT = 2

log = logging.getLogger(__name__)


async def fetch_sixty_events(settings: Settings, formats: tuple[str, ...]) -> list[MtgoEvent]:
    """The newest Challenge results per format, newest first.

    This month's listing, and last month's when this month is thin -- the
    first days of a month have few events, and a Tuesday job on the 2nd
    should not come back empty-handed.
    """
    client = MtgoClient(settings.scryfall_user_agent)
    today = dt.date.today()
    first = today.replace(day=1)
    previous = (first - dt.timedelta(days=1)).replace(day=1)
    refs: list[EventRef] = []
    for month in (first, previous):
        refs.extend(await client.month(month.year, month.month))
        by_format = {
            fmt: [r for r in refs if r.format == fmt and r.kind == "challenge"] for fmt in formats
        }
        if all(len(rows) >= EVENTS_PER_FORMAT for rows in by_format.values()):
            break
    events: list[MtgoEvent] = []
    for fmt in formats:
        candidates = sorted(
            (r for r in refs if r.format == fmt and r.kind == "challenge"),
            key=lambda r: (r.date, r.size or 0, r.event_id),
            reverse=True,
        )
        for ref in candidates[:EVENTS_PER_FORMAT]:
            events.append(await client.event(ref))
    return events


async def run(*, notify_always: bool = False) -> None:
    """Materialise the top lists; prune the ones that dropped out.

    Args:
        notify_always: The button press wants its confirmation even when
            nothing changed; the weekly run speaks only when the shelf moved.
    """
    settings = get_settings()
    with job_run(JOB_NAME) as context, session_scope() as db:
        report = top_decks.materialize_top_decks(db)
        problems: list[str] = []
        if "mtgo" in settings.meta_sources:
            formats = settings.top_deck_format_list
            try:
                events = await fetch_sixty_events(settings, formats)
                report.extend(top_decks.materialize_sixty_top_decks(db, events))
            except (SourceUnavailable, SourceResponseError) as error:
                # The Commander shelf still lands; the 60-card one keeps last week's.
                problems.append(f"mtgo: {error}")
                log.warning("meta_top_decks_mtgo_failed", extra={"error": str(error)})
        if report.snapshot_id is None and not report.created and not report.replaced:
            context.report(snapshot=None, note="no lists to pull", problems=problems)
            if notify_always:
                db.add(
                    Notification(
                        kind="meta",
                        title="No tournament lists to pull yet",
                        body="; ".join(problems)
                        or "The meta snapshot has not run. Refresh the meta first.",
                        link="/meta",
                    )
                )
            return
        if report.changed or problems or notify_always:
            parts = []
            if report.created:
                parts.append("new: " + ", ".join(report.created))
            if report.pruned:
                parts.append("pruned: " + ", ".join(report.pruned))
            if report.replaced and not report.created:
                parts.append(f"{len(report.replaced)} refreshed")
            if report.skipped:
                parts.append(f"skipped: {len(report.skipped)}")
            parts.extend(problems)
            db.add(
                Notification(
                    kind="meta",
                    title="Top decks are on the shelf",
                    body="; ".join(parts) if parts else "Nothing changed.",
                    link="/arena",
                )
            )
        context.report(problems=problems, **report.as_dict())
        log.info("meta_top_decks_done", extra={"top_decks": report.as_dict(), "problems": problems})
