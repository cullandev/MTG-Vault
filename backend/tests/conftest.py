"""Shared test fixtures.

Two rules encoded here:

* every test gets a real SQLite file created by running the *real* Alembic
  migrations, not ``metadata.create_all`` -- otherwise the migrations are never
  exercised until production;
* no test may open a socket. ``_no_network`` fails loudly instead of quietly hitting
  the internet from a unit test.
"""

from __future__ import annotations

import socket
from collections.abc import Iterator
from pathlib import Path

import pytest
from alembic.config import Config
from fastapi.testclient import TestClient
from sqlalchemy.orm import Session as DbSession

from alembic import command

BACKEND_DIR = Path(__file__).resolve().parents[1]
FIXTURES = Path(__file__).resolve().parent / "fixtures"


_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost", "testserver"})


@pytest.fixture(autouse=True)
def _no_network(monkeypatch: pytest.MonkeyPatch) -> None:
    """Fail any test that tries to reach a real host.

    Loopback stays open: anyio's blocking portal (which TestClient runs on) uses a
    local socket pair on Windows, and blocking that breaks the harness rather than
    catching a real network call.
    """
    real_connect = socket.socket.connect
    real_create = socket.create_connection

    def _host_of(address: object) -> str | None:
        if isinstance(address, tuple) and address:
            return str(address[0])
        return None

    def guarded_connect(self: socket.socket, address: object, *args: object) -> object:
        if _host_of(address) in _LOOPBACK:
            return real_connect(self, address, *args)  # type: ignore[arg-type]
        raise RuntimeError(
            f"A test attempted real network I/O to {address!r}. "
            "Use a fixture or an httpx MockTransport."
        )

    def guarded_create(address: object, *args: object, **kwargs: object) -> object:
        if _host_of(address) in _LOOPBACK:
            return real_create(address, *args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError(
            f"A test attempted real network I/O to {address!r}. "
            "Use a fixture or an httpx MockTransport."
        )

    real_getaddrinfo = socket.getaddrinfo

    def guarded_getaddrinfo(host: object, *args: object, **kwargs: object) -> object:
        # Async httpx never touches socket.connect (anyio drives the event loop's
        # own connector), but everything resolves names first. Guarding here is
        # what actually keeps an async client off the network.
        if host is None or str(host) in _LOOPBACK:
            return real_getaddrinfo(host, *args, **kwargs)  # type: ignore[arg-type]
        raise RuntimeError(
            f"A test attempted to resolve {host!r}. Use a fixture or an httpx MockTransport."
        )

    monkeypatch.setattr(socket.socket, "connect", guarded_connect)
    monkeypatch.setattr(socket, "create_connection", guarded_create)
    monkeypatch.setattr(socket, "getaddrinfo", guarded_getaddrinfo)


@pytest.fixture(autouse=True)
def _reset_client_state() -> None:
    """Clear rate-limiter, circuit-breaker and robots caches between tests.

    These live on the class so one process shares them, which is right in production
    and would otherwise leak an opened circuit from one test into the next.
    """
    from app.clients.base import ExternalClient

    ExternalClient.reset_state()


@pytest.fixture
def settings(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Iterator[object]:
    """Point the application at a throwaway data directory."""
    from app.clients.base import ExternalClient
    from app.config import get_settings
    from app.db import reset_engine
    from app.services import auth
    from app.services.scan import exact as scan_exact
    from app.services.scan import identify as scan_identify
    from app.services.scan import matching as scan_matching

    monkeypatch.setenv("DATA_DIR", str(tmp_path / "data"))
    monkeypatch.setenv("SECRET_KEY", "test-secret-key-0123456789abcdef")
    monkeypatch.setenv("APP_PASSWORD", "correct-horse-battery")
    monkeypatch.setenv("LAN_HOSTNAME", "vault.home.arpa")
    monkeypatch.setenv("STATIC_DIR", str(tmp_path / "no-frontend-build"))
    # Tests never want a live scheduler firing jobs underneath them.
    monkeypatch.setenv("ENABLE_SCHEDULER", "false")

    get_settings.cache_clear()
    reset_engine()
    auth.reset_login_limiter()
    ExternalClient.reset_state()
    scan_matching.reset_index()
    scan_exact.reset_index()
    scan_identify.reset_state()

    current = get_settings()
    current.ensure_directories()
    yield current

    reset_engine()
    get_settings.cache_clear()


@pytest.fixture
def migrated(settings: object) -> object:
    """Create the schema by running every Alembic migration."""
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.set_main_option("script_location", str(BACKEND_DIR / "alembic"))
    command.upgrade(config, "head")
    return settings


@pytest.fixture
def db(migrated: object) -> Iterator[DbSession]:
    """Yield a database session against the migrated database."""
    from app.db import get_session_factory

    session = get_session_factory()()
    try:
        yield session
        session.commit()
    finally:
        session.close()


@pytest.fixture
def catalog(db: DbSession) -> DbSession:
    """A database with the sample card catalogue imported."""
    from app.services.imports import scryfall_bulk

    scryfall_bulk.import_bulk(db, FIXTURES / "scryfall" / "sample_cards.json", batch_size=10)
    db.commit()
    return db


@pytest.fixture
def client(migrated: object) -> Iterator[TestClient]:
    """Yield an HTTP client.

    ``https://`` matters: session cookies are marked ``Secure``, and httpx will not
    store a Secure cookie received over plain http.
    """
    from app.main import create_app

    with TestClient(create_app(), base_url="https://testserver") as test_client:
        test_client.headers["X-Requested-With"] = "MTGVault"
        yield test_client


@pytest.fixture
def auth_client(client: TestClient) -> TestClient:
    """An HTTP client that has already logged in."""
    response = client.post("/api/auth/login", json={"password": "correct-horse-battery"})
    assert response.status_code == 204, response.text
    return client
