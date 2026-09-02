"""Scryfall client.

Scryfall asks for an identifying User-Agent, an explicit Accept header, and 50-100 ms
between requests. It also asks that bulk data be used instead of iterating the API,
which is why the price job downloads a file rather than making 10 000 calls (ADR-009).

See https://scryfall.com/docs/api (rate limits and bulk data).
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from app.clients.base import ExternalClient, SourceResponseError, SourceUnavailable
from app.config import Settings


@dataclass(frozen=True)
class BulkFile:
    """Metadata for one Scryfall bulk-data file."""

    type: str
    download_uri: str
    updated_at: str
    size: int
    format: str = "json"
    """``json`` (one array) or ``jsonl`` (one object per line, Scryfall's current
    format). The importer sniffs the file as well, so this is advisory."""

    @classmethod
    def from_api(cls, payload: dict[str, Any]) -> BulkFile:
        """Build from a Scryfall ``bulk_data`` entry.

        Scryfall migrated bulk files from a single JSON array (``download_uri``)
        to gzipped JSON Lines (``jsonl_download_uri``); older mirrors may still
        carry the array form, so both are accepted.
        """
        if payload.get("jsonl_download_uri"):
            uri, fmt = str(payload["jsonl_download_uri"]), "jsonl"
        elif payload.get("download_uri"):
            uri, fmt = str(payload["download_uri"]), "json"
        else:
            raise KeyError(f"bulk_data entry {payload.get('type')!r} has no download URI")
        return cls(
            type=str(payload["type"]),
            download_uri=uri,
            updated_at=str(payload["updated_at"]),
            size=int(payload.get("size") or payload.get("compressed_size") or 0),
            format=fmt,
        )

    @property
    def filename(self) -> str:
        """Local filename preserving the extensions the importer sniffs on."""
        suffix = ".jsonl" if self.format == "jsonl" else ".json"
        if self.download_uri.endswith(".gz"):
            suffix += ".gz"
        return f"{self.type}{suffix}"


class ScryfallClient(ExternalClient):
    """Read-only Scryfall access: bulk metadata, bulk download, single-card lookup."""

    service: ClassVar[str] = "scryfall"
    base_url: ClassVar[str] = "https://api.scryfall.com"
    timeout_s: ClassVar[float] = 60.0
    # Scryfall's robots.txt covers the website, not the API, and the API's own docs
    # specify the rate limit we honour above; checking it would only add a request.
    respect_robots: ClassVar[bool] = False

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        super().__init__(settings.scryfall_user_agent, **kwargs)
        # Scryfall asks for 50-100ms between requests; never go below 50ms.
        self.min_interval_s = max(settings.scryfall_min_interval_ms, 50) / 1000.0

    async def list_bulk_files(self) -> list[BulkFile]:
        """Return every available bulk-data file."""
        payload = await self.request_json("/bulk-data")
        return [BulkFile.from_api(entry) for entry in payload.get("data", [])]

    async def get_bulk_file(self, bulk_type: str) -> BulkFile | None:
        """Return metadata for one bulk-data type, e.g. ``default_cards``."""
        for entry in await self.list_bulk_files():
            if entry.type == bulk_type:
                return entry
        return None

    async def download_bulk(self, bulk: BulkFile, destination: Path) -> int:
        """Download a bulk file to disk. Returns the number of bytes written."""
        return await self.download(bulk.download_uri, destination)

    async def card_by_name(self, name: str, *, exact: bool = True) -> dict[str, Any] | None:
        """Look up a single card by name.

        Used only for one-off gap filling; bulk import is the normal path.

        Args:
            name: Card name to search for.
            exact: Use the exact-name endpoint rather than fuzzy matching.

        Returns:
            The card object, or ``None`` when Scryfall has no match.
        """
        key = "exact" if exact else "fuzzy"
        try:
            payload = await self.request_json("/cards/named", params={key: name})
        except (SourceResponseError, SourceUnavailable):
            # A name Scryfall does not know is a normal outcome, not an error, and an
            # outage must not take down whatever asked.
            return None
        return payload if isinstance(payload, dict) else None
