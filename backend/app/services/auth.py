"""Single-user authentication.

Design notes (ADR-013):

* argon2id for the password hash -- the current OWASP recommendation for new work.
* The cookie carries 256 bits of ``secrets.token_urlsafe`` entropy; only its SHA-256
  is stored, so a database leak does not hand over live sessions.
* Login is rate limited in process memory. That is sufficient *because* the app runs
  as exactly one worker (ADR-014); if that ever changes, this moves to the database.
"""

from __future__ import annotations

import hashlib
import secrets
import time
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from argon2 import PasswordHasher
from argon2.exceptions import InvalidHashError, VerifyMismatchError
from sqlalchemy import delete, select
from sqlalchemy.orm import Session as DbSession

from app.errors import TooManyRequests, Unauthorized
from app.models import AppUser, Session, utcnow

SESSION_COOKIE = "mtgv"
_hasher = PasswordHasher()

# Sliding-window login limiter: 5 attempts per 15 minutes per client address.
_LOGIN_MAX_ATTEMPTS = 5
_LOGIN_WINDOW_S = 15 * 60
_login_attempts: defaultdict[str, list[float]] = defaultdict(list)


def hash_password(password: str) -> str:
    """Hash a password with argon2id."""
    return _hasher.hash(password)


def _token_digest(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def ensure_user(db: DbSession, initial_password: str | None) -> AppUser | None:
    """Create the single application user on first run.

    Args:
        db: Open database session.
        initial_password: ``APP_PASSWORD`` from the environment. Used once, then the
            hash in the database is authoritative and the env var is ignored.

    Returns:
        The user row, or ``None`` when no user exists and no seed password was given.
    """
    user = db.scalars(select(AppUser).limit(1)).first()
    if user is not None:
        return user
    if not initial_password:
        return None
    user = AppUser(password_hash=hash_password(initial_password), password_set_at=utcnow())
    db.add(user)
    db.flush()
    return user


def check_login_rate_limit(client_key: str) -> None:
    """Raise if this client has failed too many logins recently.

    Args:
        client_key: Stable identifier for the caller, normally the client IP.

    Raises:
        TooManyRequests: The window is exhausted.
    """
    now = time.monotonic()
    attempts = [t for t in _login_attempts[client_key] if now - t < _LOGIN_WINDOW_S]
    _login_attempts[client_key] = attempts
    if len(attempts) >= _LOGIN_MAX_ATTEMPTS:
        retry_after = int(_LOGIN_WINDOW_S - (now - attempts[0])) + 1
        raise TooManyRequests(
            "Too many failed login attempts",
            detail={"retry_after_s": retry_after},
        )


def record_failed_login(client_key: str) -> None:
    """Record a failed attempt against the rate limiter."""
    _login_attempts[client_key].append(time.monotonic())


def clear_login_attempts(client_key: str) -> None:
    """Forget a client's failed attempts after a successful login."""
    _login_attempts.pop(client_key, None)


def reset_login_limiter() -> None:
    """Clear all rate-limiter state. Tests use this between cases."""
    _login_attempts.clear()


def verify_password(db: DbSession, password: str) -> bool:
    """Check a password against the stored hash, rehashing if parameters changed."""
    user = db.scalars(select(AppUser).limit(1)).first()
    if user is None:
        return False
    try:
        _hasher.verify(user.password_hash, password)
    except (VerifyMismatchError, InvalidHashError):
        return False
    if _hasher.check_needs_rehash(user.password_hash):
        user.password_hash = hash_password(password)
        user.password_set_at = utcnow()
    return True


def change_password(db: DbSession, current: str, new: str) -> None:
    """Replace the password and invalidate every existing session.

    Raises:
        Unauthorized: ``current`` does not match the stored hash.
    """
    if not verify_password(db, current):
        raise Unauthorized("Current password is incorrect")
    user = db.scalars(select(AppUser).limit(1)).one()
    user.password_hash = hash_password(new)
    user.password_set_at = utcnow()
    db.execute(delete(Session))


def create_session(db: DbSession, ttl_days: int, user_agent: str | None = None) -> str:
    """Create a session row and return the raw cookie value.

    Args:
        db: Open database session.
        ttl_days: Session lifetime.
        user_agent: Client user agent, recorded for the sessions list.

    Returns:
        The raw token to place in the cookie. It is never stored.
    """
    token = secrets.token_urlsafe(32)
    expires = datetime.now(tz=UTC) + timedelta(days=ttl_days)
    db.add(
        Session(
            id=_token_digest(token),
            expires_at=expires.isoformat(),
            user_agent=(user_agent or "")[:300] or None,
        )
    )
    return token


def resolve_session(db: DbSession, token: str | None) -> Session | None:
    """Look up a live session by cookie value.

    An expired row is treated as absent but is *not* deleted here: rejecting the
    request raises, which rolls the request's transaction back, so a delete at this
    point would be discarded anyway. Expired rows are removed by
    :func:`purge_expired_sessions` at startup instead.

    Args:
        db: Open database session.
        token: Raw cookie value, or ``None``.

    Returns:
        The session row, or ``None`` when absent or expired.
    """
    if not token:
        return None
    row = db.get(Session, _token_digest(token))
    if row is None:
        return None
    if datetime.fromisoformat(row.expires_at) <= datetime.now(tz=UTC):
        return None
    row.last_seen_at = utcnow()
    return row


def destroy_session(db: DbSession, token: str | None) -> None:
    """Delete the session identified by a cookie value, if it exists."""
    if not token:
        return
    row = db.get(Session, _token_digest(token))
    if row is not None:
        db.delete(row)


def purge_expired_sessions(db: DbSession) -> int:
    """Delete every expired session. Returns the number removed."""
    result = db.execute(delete(Session).where(Session.expires_at <= utcnow()))
    return int(getattr(result, "rowcount", 0) or 0)
