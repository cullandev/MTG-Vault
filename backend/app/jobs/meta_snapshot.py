"""Weekly meta snapshot: fan-out over enabled (format, source) pairs.

Each pair runs as its own ``job_runs`` row; one source failing never marks the
parent failed, and a parse that yields fewer than half of the previous run's items
is treated as a parser break -- the run fails, the previous snapshot keeps serving,
and a notification is raised (ARCHITECTURE.md section 5). All fetching happens
here and only here: no endpoint fetches inline (ADR-016).
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import desc, select
from sqlalchemy.orm import Session as DbSession

from app.clients import edhtop16 as edhtop16_client
from app.clients import moxfield as moxfield_client
from app.clients.base import SourceResponseError, SourceUnavailable
from app.config import Settings, get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import MetaSnapshot, Notification, utctoday
from app.services.meta import ingest

JOB_NAME = "meta_snapshot"

#: Yielding under this share of the previous run's items reads as a parser break.
BREAK_RATIO = 0.5

#: How many decklists to fetch per archetype. Templates stabilise quickly; eight
#: top-standing lists identify CORE at 80% with one dissenter tolerated.
LISTS_PER_ARCHETYPE = 8

log = logging.getLogger("mtgvault.jobs.meta")

#: The source registry (ADR-016): what each source is and whether it is api-kind.
#: Scraped sources ship later phases; only the documented API is registered today.
SOURCES: dict[str, dict[str, str]] = {
    "edhtop16": {"kind": "api", "format": "commander", "measurement": "results"},
}


async def snapshot_edhtop16(db: DbSession, settings: Settings) -> ingest.IngestReport:
    """Fetch edhtop16 standings and their decklists, and persist a snapshot."""
    client = edhtop16_client.Edhtop16Client(settings.scryfall_user_agent)
    standings = edhtop16_client.parse_top_commanders(await client.top_commanders())

    moxfield = moxfield_client.MoxfieldClient(settings.scryfall_user_agent)
    decklists: dict[str, list[tuple[dict[str, Any], moxfield_client.FetchedDecklist]]] = {}
    fetch_failures = 0
    for standing in standings:
        fetched: list[tuple[dict[str, Any], moxfield_client.FetchedDecklist]] = []
        for ref in standing.decklists[:LISTS_PER_ARCHETYPE]:
            if ref.cards:
                # edhtop16 served the maindeck inline; no second fetch needed.
                deck = moxfield_client.FetchedDecklist(
                    name=f"{standing.name} ({ref.player or 'unknown'})",
                    rows=[(standing.name, 1, "commander")]
                    + [(name, 1, "main") for name, _oracle_id in ref.cards],
                )
            elif "moxfield.com" in ref.url:
                try:
                    deck = moxfield_client.parse_deck(await moxfield.deck(ref.url))
                except (SourceUnavailable, SourceResponseError):
                    fetch_failures += 1
                    continue
            else:
                continue
            fetched.append(
                (
                    {
                        "url": ref.url,
                        "player": ref.player,
                        "event": ref.event,
                        "event_date": ref.event_date,
                        "placement": ref.placement,
                    },
                    deck,
                )
            )
        decklists[standing.name] = fetched

    report = ingest.write_snapshot(
        db,
        format_key="commander",
        source="edhtop16",
        measurement="results",
        parser_version=edhtop16_client.PARSER_VERSION,
        standings=standings,
        decklists_by_archetype=decklists,
    )
    if fetch_failures:
        log.warning("meta_decklists_failed", extra={"count": fetch_failures})
    return report


def check_for_parser_break(db: DbSession, source: str, format_key: str, count: int) -> None:
    """Fail loudly when this run yields under half of the previous run's items.

    Raises:
        SourceResponseError: The drop looks like a parser break, not a quiet meta.
    """
    previous = db.scalars(
        select(MetaSnapshot)
        .where(
            MetaSnapshot.source == source,
            MetaSnapshot.format == format_key,
            MetaSnapshot.status == "ok",
        )
        .order_by(desc(MetaSnapshot.id))
        .offset(1)  # the row this run just wrote sits at offset 0
        .limit(1)
    ).first()
    if (
        previous is not None
        and previous.item_count > 0
        and count < BREAK_RATIO * previous.item_count
    ):
        raise SourceResponseError(
            f"{source} yielded {count} items against {previous.item_count} last "
            "time; treating this as a parser break"
        )


async def run() -> None:
    """Scheduled entry point: one sub-run per enabled (format, source) pair."""
    settings = get_settings()
    for source in settings.meta_sources:
        registration = SOURCES.get(source)
        if registration is None:
            log.warning("meta_source_unknown", extra={"source": source})
            continue
        with job_run(JOB_NAME, sub_source=source) as context:
            try:
                with session_scope() as db:
                    report = await snapshot_edhtop16(db, settings)
                    check_for_parser_break(db, source, registration["format"], report.archetypes)
                    # Success used to be silent; the UI told people to "reload in
                    # a few minutes". Say it landed instead.
                    db.add(
                        Notification(
                            kind="meta",
                            title=f"Meta snapshot ingested from {source}",
                            body=(
                                f"{report.archetypes} archetype(s), {report.decklists} decklist(s)."
                            ),
                            link="/meta",
                        )
                    )
                    context.report(
                        snapshot_id=report.snapshot_id,
                        archetypes=report.archetypes,
                        decklists=report.decklists,
                        unresolved=len(report.unresolved_names),
                    )
            except (SourceUnavailable, SourceResponseError) as error:
                # The transaction above rolled back, so the previous good snapshot
                # keeps serving untouched. Record a failed marker row for the
                # history view, and raise a notification.
                with session_scope() as db:
                    db.add(
                        MetaSnapshot(
                            format=registration["format"],
                            source=source,
                            measurement=registration["measurement"],
                            snapshot_date=utctoday(),
                            status="failed",
                            item_count=0,
                            error=str(error),
                        )
                    )
                    db.add(
                        Notification(
                            kind="job_failure",
                            title=f"Meta source {source} failed",
                            body=str(error),
                            link="/system",
                        )
                    )
                raise
