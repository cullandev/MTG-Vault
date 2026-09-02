"""Weekly set-icon pre-fetch: every set symbol, cached before anyone asks.

The scanner's picker shows the set symbol on every candidate row; fetching
icons lazily meant the first scan of a new set paid the latency and -- until
the Hobbit fix -- a missing icon's 404 could poison the Scryfall breaker
mid-session. All ~1,050 icons total about 2 MB, so the honest fix is to just
have them all: fetch what's missing, remember what Scryfall doesn't host,
and the picker never waits on the network again.
"""

from __future__ import annotations

from app.config import get_settings
from app.db import session_scope
from app.errors import NotFound
from app.jobs.runner import job_run
from app.services import images as image_service
from app.services.scan import exact

JOB_NAME = "set_icon_prefetch"


async def run() -> None:
    """Scheduled entry point."""
    with job_run(JOB_NAME) as context, session_scope() as db:
        settings = get_settings()
        cached = 0
        fetched = 0
        missing = 0
        failed = 0
        for code in sorted(exact.set_codes(db)):
            path = settings.images_path / "set_icons" / f"{code}.svg"
            if path.is_file():
                cached += 1
                continue
            try:
                await image_service.get_set_icon(db, settings, code)
                fetched += 1
            except NotFound:
                # Scryfall hosts no icon for this code (promo/List variants);
                # the negative cache remembers, and the count keeps it honest.
                missing += 1
            except Exception:
                failed += 1
        context.report(
            already_cached=cached, fetched=fetched, no_icon_upstream=missing, failed=failed
        )
