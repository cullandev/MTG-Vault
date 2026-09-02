"""Weekly EDHREC refresh -- only for commanders actually leading a deck.

One request per commander at the client's rate limit; a failing fetch marks the
run partial and keeps the previous payload serving, because a stale
recommendation list beats an empty panel (ARCHITECTURE.md section 5).
"""

from __future__ import annotations

import logging

from sqlalchemy import select

from app.clients.base import SourceResponseError, SourceUnavailable
from app.config import get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import Deck, OracleCard
from app.services.rating import edhrec_service

JOB_NAME = "edhrec_refresh"

log = logging.getLogger("mtgvault.jobs.edhrec")


async def run() -> None:
    """Scheduled entry point."""
    settings = get_settings()
    with job_run(JOB_NAME) as context:
        if not settings.enable_edhrec:
            context.report(skipped="edhrec disabled")
            return
        refreshed = 0
        failed: list[str] = []
        with session_scope() as db:
            commander_ids = set(
                db.scalars(
                    select(Deck.commander_oracle_id).where(
                        Deck.commander_oracle_id.is_not(None), Deck.archived.is_(False)
                    )
                )
            ) | set(
                db.scalars(
                    select(Deck.partner_oracle_id).where(
                        Deck.partner_oracle_id.is_not(None), Deck.archived.is_(False)
                    )
                )
            )
            for oracle_id in sorted(filter(None, commander_ids)):
                oracle = db.get(OracleCard, oracle_id)
                if oracle is None:
                    continue
                try:
                    await edhrec_service.refresh_commander(db, settings, oracle)
                    refreshed += 1
                except (SourceUnavailable, SourceResponseError) as error:
                    failed.append(oracle.name)
                    log.warning(
                        "edhrec_refresh_failed",
                        extra={"commander": oracle.name, "error": str(error)},
                    )
        context.report(refreshed=refreshed, failed=failed)
        if failed:
            context.mark_partial(f"{len(failed)} commander(s) failed to refresh")
