"""External client infrastructure: rate limiting, retries, breaker, robots, downloads.

Everything here runs against ``httpx.MockTransport``. The point of these tests is the
*failure* paths -- an outage has to degrade one feature, not hang a page load or take
the app down, and that is only true if the retry/breaker logic actually works.
"""

from __future__ import annotations

import time
from pathlib import Path

import httpx
import pytest

from app.clients.base import (
    ExternalClient,
    RobotsDisallowed,
    SourceResponseError,
    SourceUnavailable,
)
from app.clients.scryfall import BulkFile, ScryfallClient
from app.config import Settings


class _Probe(ExternalClient):
    """Test double with fast timings so retry paths do not slow the suite."""

    service = "probe"
    base_url = "https://example.test"
    max_attempts = 3
    respect_robots = False

    def __init__(self, handler: object, **kwargs: object) -> None:
        super().__init__("MTGVaultTest/1.0", transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
        self.min_interval_s = 0.0
        for key, value in kwargs.items():
            setattr(self, key, value)


def _json_handler(payload: dict[str, object]) -> object:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, json=payload)

    return handler


# --- happy path ------------------------------------------------------------


async def test_request_json_returns_the_body() -> None:
    client = _Probe(_json_handler({"hello": "world"}))
    assert await client.request_json("/thing") == {"hello": "world"}


async def test_relative_paths_are_joined_to_base_url() -> None:
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(str(request.url))
        return httpx.Response(200, json={})

    await _Probe(handler).request_json("/a/b")
    assert seen == ["https://example.test/a/b"]


async def test_the_user_agent_is_sent() -> None:
    """Scryfall requires an identifying User-Agent; sending the default would be rude."""
    seen: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        seen.append(request.headers["user-agent"])
        return httpx.Response(200, json={})

    await _Probe(handler).request_json("/x")
    assert seen == ["MTGVaultTest/1.0"]


# --- retries and the breaker ----------------------------------------------


async def test_transient_failures_are_retried() -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, json={"ok": True})

    assert await _Probe(handler).request_json("/flaky") == {"ok": True}
    assert attempts["n"] == 3


async def test_persistent_failure_raises_source_unavailable() -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    with pytest.raises(SourceUnavailable) as excinfo:
        await _Probe(handler).request_json("/broken")
    assert excinfo.value.detail["service"] == "probe"
    assert excinfo.value.status_code == 503


async def test_a_404_is_not_retried() -> None:
    """Only 429 and 5xx are transient; retrying a 404 just wastes the rate limit."""
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        return httpx.Response(404)

    client = _Probe(handler)
    with pytest.raises(SourceResponseError) as excinfo:
        await client.request_json("/missing")

    assert attempts["n"] == 1
    assert excinfo.value.detail["status"] == 404


async def test_a_permanent_error_does_not_open_the_circuit() -> None:
    """One bad URL is not an outage; it must not disable the whole source."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/missing":
            return httpx.Response(404)
        return httpx.Response(200, json={"ok": True})

    client = _Probe(handler)
    for _ in range(10):
        with pytest.raises(SourceResponseError):
            await client.request_json("/missing")

    assert await client.request_json("/present") == {"ok": True}


async def test_the_circuit_opens_and_then_fails_fast() -> None:
    calls = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        calls["n"] += 1
        return httpx.Response(500)

    client = _Probe(handler)
    for _ in range(5):
        with pytest.raises(SourceUnavailable):
            await client.request_json("/down")

    calls_before = calls["n"]
    started = time.perf_counter()
    with pytest.raises(SourceUnavailable) as excinfo:
        await client.request_json("/down")
    elapsed = time.perf_counter() - started

    # No further requests, and no waiting around for them either.
    assert calls["n"] == calls_before
    assert elapsed < 0.1
    assert excinfo.value.detail["circuit"] == "open"


async def test_a_success_resets_the_failure_count() -> None:
    state = {"fail": True}

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500 if state["fail"] else 200, json={})

    client = _Probe(handler, max_attempts=1)
    with pytest.raises(SourceUnavailable):
        await client.request_json("/x")

    state["fail"] = False
    await client.request_json("/x")

    state["fail"] = True
    # Four more failures would open the circuit only if the counter had *not* reset.
    for _ in range(3):
        with pytest.raises(SourceUnavailable):
            await client.request_json("/x")
    state["fail"] = False
    assert await client.request_json("/x") == {}


# --- rate limiting ---------------------------------------------------------


async def test_the_rate_limiter_spaces_calls_out() -> None:
    client = _Probe(_json_handler({}))
    client.min_interval_s = 0.05

    started = time.perf_counter()
    for _ in range(3):
        await client.request_json("/x")
    elapsed = time.perf_counter() - started

    assert elapsed >= 0.10, f"three calls took only {elapsed * 1000:.0f}ms"


# --- robots.txt ------------------------------------------------------------


class _Scraper(_Probe):
    service = "scraper"
    respect_robots = True


async def test_disallowed_paths_are_refused_before_the_request() -> None:
    requested: list[str] = []

    def handler(request: httpx.Request) -> httpx.Response:
        requested.append(request.url.path)
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, json={})

    client = _Scraper(handler)
    with pytest.raises(RobotsDisallowed):
        await client.request_json("https://example.test/private/data")

    assert requested == ["/robots.txt"]


async def test_allowed_paths_pass_the_robots_check() -> None:
    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            return httpx.Response(200, text="User-agent: *\nDisallow: /private\n")
        return httpx.Response(200, json={"ok": True})

    client = _Scraper(handler)
    assert await client.request_json("https://example.test/public") == {"ok": True}


async def test_an_unreachable_robots_txt_is_treated_as_permissive() -> None:
    """An unreachable robots.txt is not a directive; that is the usual convention."""

    def handler(request: httpx.Request) -> httpx.Response:
        if request.url.path == "/robots.txt":
            raise httpx.ConnectError("nope")
        return httpx.Response(200, json={"ok": True})

    assert await _Scraper(handler).request_json("https://example.test/x") == {"ok": True}


# --- downloads -------------------------------------------------------------


async def test_download_writes_the_file(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"payload-bytes")

    target = tmp_path / "nested" / "bulk.json"
    written = await _Probe(handler).download("https://example.test/bulk", target)

    assert written == len(b"payload-bytes")
    assert target.read_bytes() == b"payload-bytes"


async def test_an_interrupted_download_leaves_no_file(tmp_path: Path) -> None:
    """A half-written file must never be mistaken for a complete one."""

    def handler(_request: httpx.Request) -> httpx.Response:
        raise httpx.ReadError("connection dropped")

    target = tmp_path / "bulk.json"
    with pytest.raises(SourceUnavailable):
        await _Probe(handler).download("https://example.test/bulk", target)

    assert not target.exists()
    assert not target.with_suffix(".json.part").exists()


async def test_a_download_404_is_an_answer_not_an_outage(tmp_path: Path) -> None:
    """The Hobbit regression: promo/List set codes have no icon on Scryfall, and
    four 404'd SVGs opened the shared breaker -- which then 503'd every freshly
    scanned card's artwork for five minutes. A definite no must never count."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404)

    client = _Probe(handler)
    for _ in range(6):  # more than the breaker threshold
        with pytest.raises(SourceResponseError):
            await client.download("https://example.test/sets/plst.svg", tmp_path / "plst.svg")

    # The circuit stayed closed: a real download still goes through.
    def ok_handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(200, content=b"art")

    target = tmp_path / "card.jpg"
    assert await _Probe(ok_handler).download("https://example.test/card.jpg", target) == 3


async def test_transient_download_failures_are_retried(tmp_path: Path) -> None:
    attempts = {"n": 0}

    def handler(_request: httpx.Request) -> httpx.Response:
        attempts["n"] += 1
        if attempts["n"] < 3:
            return httpx.Response(503)
        return httpx.Response(200, content=b"finally")

    target = tmp_path / "bulk.json"
    written = await _Probe(handler).download("https://example.test/bulk", target)
    assert written == len(b"finally")
    assert attempts["n"] == 3


async def test_download_is_refused_while_the_circuit_is_open(tmp_path: Path) -> None:
    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(500)

    client = _Probe(handler, max_attempts=1)
    for _ in range(5):
        with pytest.raises(SourceUnavailable):
            await client.request_json("/x")

    with pytest.raises(SourceUnavailable) as excinfo:
        await client.download("https://example.test/bulk", tmp_path / "x.json")
    assert excinfo.value.detail["circuit"] == "open"


# --- Scryfall --------------------------------------------------------------


def _settings() -> Settings:
    from app.config import get_settings

    return get_settings()


async def test_scryfall_finds_the_requested_bulk_file(settings: object) -> None:
    """Scryfall's current API exposes jsonl_download_uri + compressed_size."""
    payload = {
        "data": [
            {
                "type": "oracle_cards",
                "jsonl_download_uri": "https://data.test/oracle.jsonl.gz",
                "updated_at": "2026-08-01T00:00:00+00:00",
                "compressed_size": 100,
            },
            {
                "type": "default_cards",
                "jsonl_download_uri": "https://data.test/default-cards-x.jsonl.gz",
                "updated_at": "2026-08-22T00:00:00+00:00",
                "compressed_size": 500,
            },
        ]
    }
    client = ScryfallClient(_settings(), transport=httpx.MockTransport(_json_handler(payload)))
    client.min_interval_s = 0.0

    bulk = await client.get_bulk_file("default_cards")

    assert isinstance(bulk, BulkFile)
    assert bulk.download_uri == "https://data.test/default-cards-x.jsonl.gz"
    assert bulk.updated_at == "2026-08-22T00:00:00+00:00"
    assert bulk.format == "jsonl"
    assert bulk.filename == "default_cards.jsonl.gz"


def test_bulk_file_accepts_the_legacy_array_shape() -> None:
    bulk = BulkFile.from_api(
        {
            "type": "default_cards",
            "download_uri": "https://data.test/default.json",
            "updated_at": "2026-08-22T00:00:00+00:00",
            "size": 500,
        }
    )
    assert bulk.format == "json"
    assert bulk.filename == "default_cards.json"


def test_bulk_file_without_any_uri_is_a_loud_error() -> None:
    import pytest as _pytest

    with _pytest.raises(KeyError, match="no download URI"):
        BulkFile.from_api({"type": "default_cards", "updated_at": "2026-01-01"})


async def test_scryfall_returns_none_for_an_unknown_bulk_type(settings: object) -> None:
    client = ScryfallClient(_settings(), transport=httpx.MockTransport(_json_handler({"data": []})))
    client.min_interval_s = 0.0
    assert await client.get_bulk_file("default_cards") is None


async def test_scryfall_honours_the_configured_rate_limit(settings: object) -> None:
    """Scryfall asks for 50-100ms between calls; the floor is never below 50ms."""
    client = ScryfallClient(_settings(), transport=httpx.MockTransport(_json_handler({})))
    assert client.min_interval_s >= 0.05


async def test_card_by_name_swallows_a_miss(settings: object) -> None:
    """A name that Scryfall does not know is a normal outcome, not an error."""

    def handler(_request: httpx.Request) -> httpx.Response:
        return httpx.Response(404, json={"details": "no card"})

    client = ScryfallClient(_settings(), transport=httpx.MockTransport(handler))
    client.min_interval_s = 0.0
    assert await client.card_by_name("Nonexistent Card") is None
