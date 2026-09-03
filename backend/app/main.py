"""Application entry point.

Assembles the FastAPI app: structured logging, error envelope, request logging,
the two API routers, and the SPA fallback that serves the built frontend for any
non-``/api`` path.
"""

from __future__ import annotations

import logging
import time
import uuid
from collections.abc import AsyncIterator, Awaitable, Callable
from contextlib import asynccontextmanager
from pathlib import Path

from fastapi import FastAPI, Request, Response
from fastapi.responses import FileResponse, JSONResponse
from fastapi.staticfiles import StaticFiles
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.api import public_router, router
from app.config import get_settings
from app.db import run_migrations, session_scope
from app.errors import NotFound, install_error_handlers
from app.jobs import scheduler
from app.logging_setup import configure_logging, request_id_var
from app.models import GauntletRun, JobRun, utcnow
from app.services import auth

VERSION = "1.1.0"

ORPHAN_REASON = "interrupted by an application restart"

log = logging.getLogger("mtgvault.request")


def fail_orphaned_runs(db: DbSession) -> int:
    """Fail every run left "running" by a restart, and say how many.

    Jobs and gauntlet runs both live in this process, so a restart mid-run
    leaves the row "running" forever. For a gauntlet that also blocks every
    future run, since the API refuses to start a second one. For a job it
    quietly corrupts the history the System page is read from -- three
    ``card_hash_index`` runs sat "running" for a week because only the
    gauntlet half of this was ever written. Orphans fail honestly instead.
    """
    reaped = 0
    for orphan in db.scalars(select(GauntletRun).where(GauntletRun.status == "running")):
        orphan.status = "failed"
        orphan.finished_at = utcnow()
        orphan.error = ORPHAN_REASON
        logging.getLogger("mtgvault").warning("gauntlet_orphan_failed", extra={"run_id": orphan.id})
        reaped += 1
    for stale in db.scalars(select(JobRun).where(JobRun.status == "running")):
        stale.status = "failed"
        stale.finished_at = utcnow()
        stale.detail_json = {**(stale.detail_json or {}), "error": ORPHAN_REASON}
        logging.getLogger("mtgvault").warning(
            "job_orphan_failed", extra={"job": stale.job_name, "run_id": stale.id}
        )
        reaped += 1
    return reaped


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Prepare the data directory, logging and the seed user on startup."""
    settings = get_settings()
    settings.ensure_directories()
    configure_logging(settings.log_level, settings.logs_path)
    logging.getLogger("mtgvault").info(
        "starting", extra={"version": VERSION, "data_dir": str(settings.data_dir)}
    )
    run_migrations()
    if settings.auth_disabled:
        logging.getLogger("mtgvault").warning(
            "auth_disabled",
            extra={"detail": "AUTH_DISABLED is set: every endpoint is open. Development only."},
        )
    with session_scope() as db:
        auth.ensure_user(db, settings.app_password)
        removed = auth.purge_expired_sessions(db)
        if removed:
            logging.getLogger("mtgvault").info("purged_sessions", extra={"count": removed})
        fail_orphaned_runs(db)
    scheduler.start(settings)
    try:
        yield
    finally:
        scheduler.shutdown()


def create_app() -> FastAPI:
    """Build and return the FastAPI application."""
    app = FastAPI(
        title="MTG Vault",
        version=VERSION,
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    install_error_handlers(app)

    @app.middleware("http")
    async def _request_log(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        request_id = uuid.uuid4().hex[:16]
        token = request_id_var.set(request_id)
        started = time.perf_counter()
        try:
            response = await call_next(request)
        finally:
            request_id_var.reset(token)
        duration_ms = round((time.perf_counter() - started) * 1000, 2)
        log.info(
            "request",
            extra={
                "method": request.method,
                "path": request.url.path,
                "status": response.status_code,
                "duration_ms": duration_ms,
            },
        )
        response.headers["X-Request-Id"] = request_id
        return response

    @app.get("/health", include_in_schema=False)
    async def health() -> JSONResponse:
        """Liveness probe.

        Deliberately unauthenticated and deliberately uninformative: it reveals
        nothing about the collection's existence or size (ADR-013).
        """
        return JSONResponse({"status": "ok", "version": VERSION})

    @app.get("/ca.crt", include_in_schema=False)
    async def ca_certificate() -> Response:
        """Serve Caddy's root certificate for phone installation.

        Deliberately unauthenticated: a root *certificate* is public key material,
        and the whole point is that it is needed precisely when the phone does not
        yet trust the TLS connection. iOS Safari will not even show the camera
        permission prompt on a page whose certificate is untrusted -- getUserMedia
        hangs, silently -- so getting this file onto the phone is a prerequisite
        for the scanner, and downloading it from the site itself beats emailing
        certificate files around.
        """
        settings = get_settings()
        # Caddy keeps its PKI directory 0700 (it also holds the CA private key), so
        # the app usually cannot traverse into it -- and on Python 3.12 even
        # Path.is_file() *raises* PermissionError there rather than returning False.
        # The deploy copies just the public root into data/ca/ (see README); the
        # direct path stays as a fallback for setups that relaxed the permissions.
        candidates = [
            settings.data_dir / "ca" / "root.crt",
            settings.data_dir
            / "caddy"
            / "data"
            / "caddy"
            / "pki"
            / "authorities"
            / "local"
            / "root.crt",
        ]
        cert = next((path for path in candidates if _readable(path)), None)
        if cert is None:
            raise NotFound(
                "The certificate is not available. Copy it once on the host: "
                "cp data/caddy/data/caddy/pki/authorities/local/root.crt data/ca/root.crt"
            )
        return FileResponse(
            cert,
            media_type="application/x-x509-ca-cert",
            filename="mtgvault-root.crt",
            headers={"Cache-Control": "no-cache"},
        )

    app.include_router(public_router)
    app.include_router(router)

    _mount_spa(app)
    return app


def _readable(path: Path) -> bool:
    """Whether a file exists and this process may actually read it."""
    try:
        with path.open("rb"):
            return True
    except OSError:
        return False


def _mount_spa(app: FastAPI) -> None:
    """Serve the built frontend, falling back to index.html for client-side routes."""
    settings = get_settings()
    static_dir = settings.static_dir or Path("static")
    if not static_dir.is_dir():
        # Backend-only development and the test suite run without a frontend build.
        return

    assets = static_dir / "assets"
    if assets.is_dir():
        app.mount("/assets", StaticFiles(directory=assets), name="assets")

    index = static_dir / "index.html"

    # The shell must never be cached: a phone that heuristically cached /scan
    # keeps running last week's bundle no matter what gets deployed. (Caddy only
    # marks "/" and "/index.html" no-cache; SPA routes like /scan arrive here.)
    shell_headers = {"Cache-Control": "no-cache"}

    @app.get("/{full_path:path}", include_in_schema=False)
    async def spa(full_path: str) -> Response:
        """Serve a static file when it exists, otherwise the SPA shell."""
        if full_path.startswith("api/"):
            # Without this an unknown API path returns the HTML shell with a 200,
            # which the client would try to parse as JSON.
            raise NotFound(f"No such endpoint: /{full_path}")
        candidate = (static_dir / full_path).resolve()
        try:
            candidate.relative_to(static_dir.resolve())
        except ValueError:
            # Path traversal attempt; fall through to the shell.
            return FileResponse(index, headers=shell_headers)
        if full_path and candidate.is_file():
            if full_path.startswith("opencv/"):
                # 11MB of WASM re-fetched on every reload is a scanner that takes a
                # minute to open on weak WiFi. The file changes only when the vendored
                # build is replaced, so let phones keep it for a week.
                return FileResponse(candidate, headers={"Cache-Control": "public, max-age=604800"})
            return FileResponse(candidate)
        return FileResponse(index, headers=shell_headers)


# No module-level ``app`` on purpose: creating it at import time would read settings
# before the environment is ready and would make the module unimportable in tests.
# Uvicorn is started with ``--factory`` (see backend/Dockerfile).
