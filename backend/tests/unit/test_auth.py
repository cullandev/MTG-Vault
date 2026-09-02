"""Authentication: login, sessions, rate limiting, CSRF, password change."""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import Session
from app.services import auth


def test_login_sets_a_session_cookie(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "correct-horse-battery"})
    assert response.status_code == 204
    assert auth.SESSION_COOKIE in client.cookies


def test_wrong_password_is_rejected(client: TestClient) -> None:
    response = client.post("/api/auth/login", json={"password": "nope"})
    assert response.status_code == 401
    assert response.json()["error"]["code"] == "unauthorized"
    assert auth.SESSION_COOKIE not in client.cookies


def test_session_endpoint_reports_state(client: TestClient) -> None:
    assert client.get("/api/auth/session").json() == {
        "authenticated": False,
        "expires_at": None,
    }
    client.post("/api/auth/login", json={"password": "correct-horse-battery"})
    body = client.get("/api/auth/session").json()
    assert body["authenticated"] is True
    assert body["expires_at"]


def test_logout_destroys_the_session(auth_client: TestClient, db: DbSession) -> None:
    assert db.scalars(select(Session)).all()
    assert auth_client.post("/api/auth/logout").status_code == 204
    db.expire_all()
    assert db.scalars(select(Session)).all() == []
    assert auth_client.get("/api/collection").status_code == 401


def test_login_is_rate_limited(client: TestClient) -> None:
    """Five failures per fifteen minutes, then the sixth is refused outright."""
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401
    response = client.post("/api/auth/login", json={"password": "wrong"})
    assert response.status_code == 429
    assert response.json()["error"]["detail"]["retry_after_s"] > 0

    # Even the correct password is refused while the window is exhausted.
    assert (
        client.post("/api/auth/login", json={"password": "correct-horse-battery"}).status_code
        == 429
    )


def test_successful_login_clears_the_limiter(client: TestClient) -> None:
    for _ in range(4):
        client.post("/api/auth/login", json={"password": "wrong"})
    assert (
        client.post("/api/auth/login", json={"password": "correct-horse-battery"}).status_code
        == 204
    )
    for _ in range(5):
        assert client.post("/api/auth/login", json={"password": "wrong"}).status_code == 401


def test_state_changing_requests_require_the_custom_header(auth_client: TestClient) -> None:
    """SameSite=Lax plus a custom header is the CSRF defence (ADR-013)."""
    headers = {"X-Requested-With": None}
    response = auth_client.post(
        "/api/collection/items",
        json={"name": "Lightning Bolt"},
        headers={k: v for k, v in headers.items() if v is not None},
    )
    # TestClient keeps the session header; remove it explicitly.
    response = auth_client.request(
        "POST",
        "/api/collection/items",
        json={"name": "Lightning Bolt"},
        headers={"X-Requested-With": ""},
    )
    assert response.status_code == 403
    assert response.json()["error"]["code"] == "csrf_failed"


def test_reads_do_not_require_the_custom_header(auth_client: TestClient) -> None:
    response = auth_client.request("GET", "/api/collection", headers={"X-Requested-With": ""})
    assert response.status_code == 200


def test_password_change_invalidates_sessions(auth_client: TestClient, db: DbSession) -> None:
    response = auth_client.post(
        "/api/auth/password",
        json={"current": "correct-horse-battery", "new": "a-much-better-password"},
    )
    assert response.status_code == 204
    db.expire_all()
    assert db.scalars(select(Session)).all() == []
    assert auth_client.get("/api/collection").status_code == 401

    assert (
        auth_client.post("/api/auth/login", json={"password": "a-much-better-password"}).status_code
        == 204
    )


def test_password_change_requires_the_current_password(auth_client: TestClient) -> None:
    response = auth_client.post(
        "/api/auth/password", json={"current": "wrong", "new": "another-password"}
    )
    assert response.status_code == 401


def test_session_token_is_never_stored(auth_client: TestClient, db: DbSession) -> None:
    """Only the SHA-256 of the cookie is persisted (ADR-013)."""
    token = auth_client.cookies[auth.SESSION_COOKIE]
    stored = db.scalars(select(Session.id)).all()
    assert token not in stored
    assert len(stored) == 1
    assert len(stored[0]) == 64


def test_expired_sessions_are_rejected(auth_client: TestClient, db: DbSession) -> None:
    row = db.scalars(select(Session)).one()
    row.expires_at = "2000-01-01T00:00:00+00:00"
    db.commit()

    assert auth_client.get("/api/collection").status_code == 401


def test_expired_sessions_are_purged_at_startup(db: DbSession) -> None:
    """Rejecting a request rolls its transaction back, so cleanup happens here."""
    db.add(Session(id="a" * 64, expires_at="2000-01-01T00:00:00+00:00"))
    db.add(Session(id="b" * 64, expires_at="2999-01-01T00:00:00+00:00"))
    db.commit()

    assert auth.purge_expired_sessions(db) == 1
    db.commit()
    assert [row.id for row in db.scalars(select(Session))] == ["b" * 64]


@pytest.mark.parametrize(
    "path",
    ["/api/collection", "/api/collection/stats", "/api/audit", "/api/system/status"],
)
def test_endpoints_require_authentication(client: TestClient, path: str) -> None:
    assert client.get(path).status_code == 401


def test_health_is_public_and_says_nothing(client: TestClient) -> None:
    response = client.get("/health")
    assert response.status_code == 200
    assert set(response.json()) == {"status", "version"}


def test_auth_disabled_opens_the_api_and_reports_authenticated(
    migrated: object, monkeypatch: pytest.MonkeyPatch
) -> None:
    """AUTH_DISABLED must open every endpoint AND tell the SPA to skip the login
    screen -- the session boot-check is what the frontend keys off."""
    from fastapi.testclient import TestClient

    from app.config import get_settings
    from app.main import create_app

    monkeypatch.setenv("AUTH_DISABLED", "true")
    get_settings.cache_clear()
    try:
        with TestClient(create_app(), base_url="https://testserver") as open_client:
            open_client.headers["X-Requested-With"] = "MTGVault"
            assert open_client.get("/api/auth/session").json()["authenticated"] is True
            assert open_client.get("/api/collection").status_code == 200
    finally:
        get_settings.cache_clear()
