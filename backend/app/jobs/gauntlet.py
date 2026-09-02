"""Weekly meta gauntlet: does the growing vault build better decks yet?

Skips quietly (a partial run with a note) when the Forge sidecar is disabled --
the job existing must not force a 4 GB container on anyone.
"""

from __future__ import annotations

from app.config import get_settings
from app.db import session_scope
from app.jobs.runner import job_run
from app.models import GauntletRun
from app.services.rating import gauntlet as gauntlet_service

JOB_NAME = "meta_gauntlet"


async def run() -> None:
    """Scheduled entry point."""
    settings = get_settings()
    with job_run(JOB_NAME) as context:
        if not settings.enable_forge:
            context.mark_partial("ENABLE_FORGE is false; gauntlet skipped")
            return
        with session_scope() as db:
            from sqlalchemy import select

            # Same guard as the API: two interleaved runs would delete each
            # other's decks mid-battle and corrupt both scoreboards.
            already = db.scalars(
                select(GauntletRun).where(GauntletRun.status == "running").limit(1)
            ).first()
            if already is not None:
                context.mark_partial(f"run {already.id} is still in progress; skipped")
                return
            row = GauntletRun(status="running")
            db.add(row)
            db.flush()
            run_id = row.id
        await gauntlet_service.run_gauntlet(settings, run_id)
        with session_scope() as db:
            finished = db.get(GauntletRun, run_id)
            context.report(
                run_id=run_id,
                status=finished.status if finished else "missing",
                games=finished.games_played if finished else 0,
            )
