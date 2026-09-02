"""FastAPI dependencies.

:func:`require_session` is attached to the ``/api`` router itself rather than to
individual endpoints, so a new route is authenticated by construction and would have
to opt *out* explicitly (ADR-013). ``tests/unit/test_auth_coverage.py`` enumerates the
route table and fails the build if anything slips through.
"""

from __future__ import annotations

from collections.abc import Iterator
from typing import Annotated

from fastapi import Depends, Request
from sqlalchemy.orm import Session as DbSession

from app.config import Settings, get_settings
from app.db import get_session_factory
from app.errors import AppError, Unauthorized
from app.services import auth

# Endpoints intentionally reachable without a session. Each one is here for a stated
# reason; the list is asserted in tests/unit/test_auth_coverage.py, so extending it
# requires a deliberate, reviewed change to that test.
#
#   /health             liveness for Docker; returns only {status, version}
#   /ca.crt             the public root certificate, needed before TLS is trusted
#   /api/auth/login     obviously cannot require a session
#   /api/auth/logout    must work even with an already-expired cookie
#   /api/auth/session   the SPA's boot check; returns a boolean and an expiry
UNAUTHENTICATED_PATHS: frozenset[str] = frozenset(
    {"/health", "/ca.crt", "/api/auth/login", "/api/auth/logout", "/api/auth/session"}
)

_UNSAFE_METHODS = frozenset({"POST", "PUT", "PATCH", "DELETE"})


class CsrfFailure(AppError):
    """A state-changing request arrived without the required custom header."""

    status_code = 403
    code = "csrf_failed"


def get_db() -> Iterator[DbSession]:
    """Yield a request-scoped database session, committing on success."""
    session = get_session_factory()()
    try:
        yield session
        session.commit()
    except Exception:
        session.rollback()
        raise
    finally:
        session.close()


Db = Annotated[DbSession, Depends(get_db)]
Config = Annotated[Settings, Depends(get_settings)]


def client_key(request: Request) -> str:
    """Identify the caller for rate limiting.

    ``X-Forwarded-For`` is trusted because the only thing in front of the app is our
    own Caddy instance on a private Docker network; nothing else can reach port 8000.
    """
    forwarded = request.headers.get("x-forwarded-for")
    if forwarded:
        return forwarded.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def require_csrf(request: Request) -> None:
    """Require a custom header on state-changing requests.

    A same-origin SPA can always set a custom header; a cross-site form post cannot,
    and ``SameSite=Lax`` already blocks cross-site cookie-bearing XHR. Together these
    are sufficient for a single-origin app without a token round-trip.

    Raises:
        CsrfFailure: The header is missing on an unsafe method.
    """
    if request.method not in _UNSAFE_METHODS:
        return
    if request.url.path == "/api/scan/diagnostics":
        # navigator.sendBeacon cannot set custom headers, and the page-leave beacon
        # is the only record of a scanner that died mid-load. The endpoint only
        # appends to a log -- the worst a forged request can do is write a log line.
        return
    if request.headers.get("x-requested-with") != "MTGVault":
        raise CsrfFailure("Missing X-Requested-With header")


def require_session(request: Request, db: Db, settings: Config) -> None:
    """Reject the request unless it carries a live session cookie.

    Raises:
        Unauthorized: No cookie, or the session has expired.
    """
    if settings.auth_disabled:
        # Development only. The dependency stays attached to every route so that
        # clearing AUTH_DISABLED re-protects everything without touching any router.
        return
    token = request.cookies.get(auth.SESSION_COOKIE)
    session = auth.resolve_session(db, token)
    if session is None:
        raise Unauthorized("Authentication required")
