"""Authentication endpoints.

These are the only endpoints under ``/api`` that are reachable without a session --
they are mounted on a separate router that carries the CSRF dependency but not the
session dependency (ADR-013). Everything else is authenticated by construction.
"""

from __future__ import annotations

from fastapi import APIRouter, Depends, Request, Response, status

from app.deps import Config, Db, client_key, require_csrf, require_session
from app.errors import Unauthorized
from app.schemas.auth import ChangePasswordRequest, LoginRequest, SessionInfo
from app.services import auth

router = APIRouter(prefix="/auth", tags=["auth"], dependencies=[Depends(require_csrf)])


def _set_session_cookie(response: Response, token: str, settings: Config) -> None:
    response.set_cookie(
        key=auth.SESSION_COOKIE,
        value=token,
        max_age=settings.session_ttl_days * 24 * 3600,
        httponly=True,
        secure=True,
        samesite="lax",
        path="/",
    )


@router.post("/login", status_code=status.HTTP_204_NO_CONTENT)
def login(
    body: LoginRequest,
    request: Request,
    response: Response,
    db: Db,
    settings: Config,
) -> Response:
    """Exchange the application password for a long-lived session cookie."""
    key = client_key(request)
    auth.check_login_rate_limit(key)

    auth.ensure_user(db, settings.app_password)
    if not auth.verify_password(db, body.password):
        auth.record_failed_login(key)
        raise Unauthorized("Incorrect password")

    auth.clear_login_attempts(key)
    token = auth.create_session(db, settings.session_ttl_days, request.headers.get("user-agent"))
    _set_session_cookie(response, token, settings)
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
def logout(request: Request, response: Response, db: Db) -> Response:
    """Destroy the current session and clear the cookie."""
    auth.destroy_session(db, request.cookies.get(auth.SESSION_COOKIE))
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response


@router.get("/session", response_model=SessionInfo)
def session_info(request: Request, db: Db, settings: Config) -> SessionInfo:
    """Report whether the caller holds a live session.

    Deliberately unauthenticated: the SPA calls it on boot to decide between the login
    screen and the app, and it reveals nothing beyond a boolean and an expiry.
    """
    if settings.auth_disabled:
        # The SPA keys the login screen off this endpoint; with auth off there is
        # no session to hold, and no screen to show.
        return SessionInfo(authenticated=True, expires_at=None)
    session = auth.resolve_session(db, request.cookies.get(auth.SESSION_COOKIE))
    if session is None:
        return SessionInfo(authenticated=False)
    return SessionInfo(authenticated=True, expires_at=session.expires_at)


@router.post(
    "/password",
    status_code=status.HTTP_204_NO_CONTENT,
    dependencies=[Depends(require_session)],
)
def change_password(body: ChangePasswordRequest, response: Response, db: Db) -> Response:
    """Change the application password, invalidating every existing session."""
    auth.change_password(db, body.current, body.new)
    response.delete_cookie(auth.SESSION_COOKIE, path="/")
    response.status_code = status.HTTP_204_NO_CONTENT
    return response
