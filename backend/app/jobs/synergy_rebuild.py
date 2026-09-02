"""Weekly synergy rebuild, also triggerable after a large import."""

from __future__ import annotations

from app.db import session_scope
from app.jobs.runner import job_run
from app.models import Notification
from app.services.synergy import rebuild as rebuild_service

JOB_NAME = "synergy_rebuild"


async def run() -> None:
    """Scheduled entry point."""
    with job_run(JOB_NAME) as context:
        with session_scope() as db:
            report = rebuild_service.rebuild(db)
            # The UI's only other completion signal is reload-and-hope; a rebuild
            # that found the vault's suggested decks deserves to say so.
            db.add(
                Notification(
                    kind="synergy",
                    title=f"Synergy graph rebuilt: {report.cores} suggested deck(s)",
                    body=(
                        f"{report.pool_size} distinct cards, {report.edges} connections, "
                        f"{report.cores} cores."
                    ),
                    link="/suggested-decks",
                )
            )
        context.report(
            pool=report.pool_size,
            tagged=report.tagged,
            edges=report.edges,
            cores=report.cores,
        )
