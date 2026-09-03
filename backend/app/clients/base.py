"""Base class for every external service client.

`clients/` is the only package allowed to perform outbound network I/O
(ARCHITECTURE.md section 1, enforced by ``tests/unit/test_no_raw_http.py``). Each
subclass gets, in this order: a circuit-breaker check, a robots.txt check, a rate
limiter, a timeout, and bounded retries. (There is no response cache: the
``http_cache`` table it would have used was dropped in migration 0014, unread.)

The circuit breaker is the piece that makes "an outage degrades one feature, never the
app" true: once a source has failed repeatedly, calls fail *immediately* with
:class:`SourceUnavailable` instead of hanging a page load for 30 seconds, and callers
serve stale cached data marked ``stale``.
"""

from __future__ import annotations

import asyncio
import logging
import random
import time
import urllib.robotparser
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, ClassVar
from urllib.parse import urlparse

import httpx

from app.errors import AppError

log = logging.getLogger("mtgvault.client")


class SourceUnavailable(AppError):
    """An external source is failing or its circuit is open."""

    status_code = 503
    code = "source_unavailable"


class RobotsDisallowed(AppError):
    """The target path is disallowed by the host's robots.txt."""

    status_code = 403
    code = "robots_disallowed"


class SourceResponseError(AppError):
    """The source answered, and the answer was an error we should not retry."""

    status_code = 502
    code = "source_response_error"


TRANSIENT_STATUSES = frozenset({408, 425, 429, 500, 502, 503, 504})
"""Statuses worth retrying. Everything else is the source saying a definite no --
retrying a 404 only burns the rate limit and delays the failure by ten seconds."""


@dataclass
class _Breaker:
    """Per-service circuit-breaker state."""

    failure_threshold: int = 5
    cooldown_s: float = 300.0
    failures: int = 0
    opened_at: float | None = None

    def record_success(self) -> None:
        """Reset after a successful call."""
        self.failures = 0
        self.opened_at = None

    def record_failure(self) -> None:
        """Count a failure and open the circuit at the threshold."""
        self.failures += 1
        if self.failures >= self.failure_threshold:
            self.opened_at = time.monotonic()

    @property
    def is_open(self) -> bool:
        """Whether calls should fail fast right now."""
        if self.opened_at is None:
            return False
        if time.monotonic() - self.opened_at >= self.cooldown_s:
            # Half-open: allow one probe through.
            self.opened_at = None
            self.failures = self.failure_threshold - 1
            return False
        return True


@dataclass
class _RateLimiter:
    """Minimum-interval limiter shared by all calls to one service."""

    min_interval_s: float
    _last: float = 0.0
    _lock: asyncio.Lock = field(default_factory=asyncio.Lock)

    async def acquire(self) -> None:
        """Block until the next call is allowed."""
        async with self._lock:
            wait = self.min_interval_s - (time.monotonic() - self._last)
            if wait > 0:
                await asyncio.sleep(wait)
            self._last = time.monotonic()


class ExternalClient:
    """Shared behaviour for outbound HTTP.

    Subclasses set the class attributes and call :meth:`request_json` or
    :meth:`download`.
    """

    service: ClassVar[str] = "generic"
    base_url: ClassVar[str] = ""
    timeout_s: ClassVar[float] = 20.0
    min_interval_s: float = 0.1
    """Class-level default; an instance may tighten it from configuration."""
    max_attempts: ClassVar[int] = 3
    respect_robots: ClassVar[bool] = True
    parser_version: ClassVar[int] = 1

    _limiters: ClassVar[dict[str, _RateLimiter]] = {}
    _breakers: ClassVar[dict[str, _Breaker]] = {}
    _robots: ClassVar[dict[str, tuple[float, urllib.robotparser.RobotFileParser | None]]] = {}

    def __init__(self, user_agent: str, *, transport: httpx.AsyncBaseTransport | None = None):
        self.user_agent = user_agent
        self._transport = transport

    # -- infrastructure ----------------------------------------------------

    @property
    def _limiter(self) -> _RateLimiter:
        limiter = ExternalClient._limiters.get(self.service)
        if limiter is None or limiter.min_interval_s != self.min_interval_s:
            limiter = _RateLimiter(self.min_interval_s)
            ExternalClient._limiters[self.service] = limiter
        return limiter

    @property
    def _breaker(self) -> _Breaker:
        return ExternalClient._breakers.setdefault(self.service, _Breaker())

    @classmethod
    def reset_state(cls) -> None:
        """Clear limiter, breaker and robots caches. Tests use this between cases."""
        cls._limiters.clear()
        cls._breakers.clear()
        cls._robots.clear()

    def _client(self) -> httpx.AsyncClient:
        return httpx.AsyncClient(
            timeout=httpx.Timeout(self.timeout_s, connect=10.0),
            headers={"User-Agent": self.user_agent, "Accept": "application/json"},
            follow_redirects=True,
            transport=self._transport,
        )

    async def _check_robots(self, url: str) -> None:
        """Fail before the request if robots.txt disallows the path.

        The result is cached for a day per host. A robots.txt that cannot be fetched
        is treated as permissive, which matches the convention -- an unreachable
        robots.txt is not a directive.
        """
        if not self.respect_robots:
            return
        parsed = urlparse(url)
        host = f"{parsed.scheme}://{parsed.netloc}"
        cached = ExternalClient._robots.get(host)
        now = time.monotonic()
        if cached is None or now - cached[0] > 86400:
            parser: urllib.robotparser.RobotFileParser | None = None
            try:
                async with self._client() as client:
                    response = await client.get(f"{host}/robots.txt")
                if response.status_code == 200:
                    parser = urllib.robotparser.RobotFileParser()
                    parser.parse(response.text.splitlines())
            except httpx.HTTPError:
                parser = None
            ExternalClient._robots[host] = (now, parser)
            cached = ExternalClient._robots[host]
        parser = cached[1]
        if parser is not None and not parser.can_fetch(self.user_agent, url):
            raise RobotsDisallowed(
                f"robots.txt disallows {url} for {self.service}",
                detail={"service": self.service, "url": url},
            )

    async def _send(self, method: str, url: str, **kwargs: Any) -> httpx.Response:
        """Perform one request with rate limiting, retries and breaker accounting."""
        if self._breaker.is_open:
            raise SourceUnavailable(
                f"{self.service} is temporarily unavailable",
                detail={"service": self.service, "circuit": "open"},
            )
        await self._check_robots(url)

        last_error: Exception | str | None = None
        for attempt in range(1, self.max_attempts + 1):
            await self._limiter.acquire()
            try:
                async with self._client() as client:
                    response = await client.request(method, url, **kwargs)
            except httpx.HTTPError as exc:
                # Network-level failure: always worth another go.
                last_error = exc
            else:
                if response.status_code not in TRANSIENT_STATUSES:
                    if response.is_error:
                        # A permanent error means the source is *up* and told us no.
                        # Retrying wastes the rate limit, and counting it as an outage
                        # would open the circuit over one bad URL.
                        raise SourceResponseError(
                            f"{self.service} returned {response.status_code}",
                            detail={
                                "service": self.service,
                                "url": url,
                                "status": response.status_code,
                            },
                        )
                    self._breaker.record_success()
                    log.info(
                        "external_call",
                        extra={
                            "service": self.service,
                            "url": url,
                            "status": response.status_code,
                            "attempt": attempt,
                        },
                    )
                    return response
                last_error = f"HTTP {response.status_code}"

            if attempt == self.max_attempts:
                break
            backoff = min(2 ** (attempt - 1), 8) * (0.5 + random.random())
            log.warning(
                "external_retry",
                extra={
                    "service": self.service,
                    "url": url,
                    "attempt": attempt,
                    "error": str(last_error),
                },
            )
            await asyncio.sleep(backoff)

        self._breaker.record_failure()
        raise SourceUnavailable(
            f"{self.service} request failed after {self.max_attempts} attempts",
            detail={"service": self.service, "url": url, "error": str(last_error)},
        )

    # -- public surface ----------------------------------------------------

    async def request_json(self, path: str, *, method: str = "GET", **kwargs: Any) -> Any:
        """Request a JSON document.

        Args:
            path: Absolute URL, or a path appended to ``base_url``.
            method: HTTP method; GET unless a search API demands a POST body.
            **kwargs: Passed through to httpx.

        Returns:
            The decoded JSON body.
        """
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        response = await self._send(method, url, **kwargs)
        return response.json()

    async def request_text(self, path: str, *, method: str = "GET", **kwargs: Any) -> str:
        """Request a page as text -- for the sources that publish HTML, not JSON.

        Same limiter, breaker, robots check and retries as :meth:`request_json`.
        """
        url = path if path.startswith("http") else f"{self.base_url}{path}"
        response = await self._send(method, url, **kwargs)
        return response.text

    async def download(self, url: str, destination: Path, *, chunk_bytes: int = 1 << 20) -> int:
        """Stream a URL to disk.

        Written to a ``.part`` file and renamed on completion, so an interrupted
        download can never be mistaken for a complete one.

        Args:
            url: Absolute URL to fetch.
            destination: Final path on disk.
            chunk_bytes: Streaming chunk size.

        Returns:
            Number of bytes written.
        """
        if self._breaker.is_open:
            raise SourceUnavailable(
                f"{self.service} is temporarily unavailable",
                detail={"service": self.service, "circuit": "open"},
            )
        await self._check_robots(url)

        destination.parent.mkdir(parents=True, exist_ok=True)
        partial = destination.with_suffix(destination.suffix + ".part")
        last_error: Exception | str | None = None
        for attempt in range(1, self.max_attempts + 1):
            await self._limiter.acquire()
            written = 0
            try:
                async with self._client() as client, client.stream("GET", url) as response:
                    if response.status_code not in TRANSIENT_STATUSES and response.is_error:
                        # The source is up and said a definite no (a 404'd set
                        # icon, say). That is an answer, not an outage: five of
                        # those must never open the circuit and take every card
                        # image down with them.
                        raise SourceResponseError(
                            f"{self.service} returned {response.status_code}",
                            detail={
                                "service": self.service,
                                "url": url,
                                "status": response.status_code,
                            },
                        )
                    if response.is_error:
                        last_error = f"HTTP {response.status_code}"
                    else:
                        with partial.open("wb") as handle:
                            async for chunk in response.aiter_bytes(chunk_bytes):
                                handle.write(chunk)
                                written += len(chunk)
                        partial.replace(destination)
                        self._breaker.record_success()
                        log.info(
                            "external_download",
                            extra={"service": self.service, "url": url, "bytes": written},
                        )
                        return written
            except httpx.HTTPError as exc:
                partial.unlink(missing_ok=True)
                last_error = exc

            if attempt == self.max_attempts:
                break
            backoff = min(2 ** (attempt - 1), 8) * (0.5 + random.random())
            log.warning(
                "download_retry",
                extra={
                    "service": self.service,
                    "url": url,
                    "attempt": attempt,
                    "error": str(last_error),
                },
            )
            await asyncio.sleep(backoff)

        partial.unlink(missing_ok=True)
        self._breaker.record_failure()
        log.warning(
            "download_failed",
            extra={"service": self.service, "url": url, "error": str(last_error)},
        )
        raise SourceUnavailable(
            f"{self.service} download failed",
            detail={"service": self.service, "url": url, "error": str(last_error)},
        )
