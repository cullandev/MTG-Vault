"""External service clients.

The only package permitted to make outbound network calls. Every service gets exactly
one module here with timeouts, retries, rate limiting, caching and a circuit breaker;
``tests/unit/test_no_raw_http.py`` fails the build if ``httpx`` or ``requests`` is
imported anywhere else.
"""

from app.clients.base import (
    ExternalClient,
    RobotsDisallowed,
    SourceResponseError,
    SourceUnavailable,
)
from app.clients.scryfall import BulkFile, ScryfallClient

__all__ = [
    "BulkFile",
    "ExternalClient",
    "RobotsDisallowed",
    "ScryfallClient",
    "SourceResponseError",
    "SourceUnavailable",
]
