"""Card image cache.

The policy that matters is the one that stops the data directory growing without
bound: cache ``normal`` under an LRU cap, never store ``art_crop``.
"""

from __future__ import annotations

from pathlib import Path

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.scryfall import ScryfallClient
from app.config import Settings, get_settings
from app.errors import NotFound
from app.models import Card, ImageCacheEntry
from app.services import images as image_service


@pytest.fixture
def fake_download(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    """Replace the network download with a local write, recording the URLs asked for."""
    requested: list[str] = []

    async def _download(_self: ScryfallClient, url: str, destination: Path, **_kw: object) -> int:
        # Local filesystem writes in a test double; no event loop to starve.
        requested.append(url)
        destination.parent.mkdir(parents=True, exist_ok=True)
        destination.write_bytes(b"jpeg-bytes")  # noqa: ASYNC240
        return len(b"jpeg-bytes")

    monkeypatch.setattr(ScryfallClient, "download", _download)
    return requested


def _a_card(db: DbSession) -> Card:
    return db.scalars(select(Card).where(Card.name == "Lightning Bolt")).one()


async def test_first_request_downloads_and_caches(
    catalog: DbSession, fake_download: list[str]
) -> None:
    settings: Settings = get_settings()
    card = _a_card(catalog)

    image = await image_service.get_image(catalog, settings, card.id)

    assert image.path.read_bytes() == b"jpeg-bytes"
    assert fake_download == ["https://cards.example/lightning-bolt.jpg"]
    entry = catalog.scalars(select(ImageCacheEntry)).one()
    assert entry.card_id == card.id
    assert entry.bytes == 10


async def test_second_request_is_served_from_disk(
    catalog: DbSession, fake_download: list[str]
) -> None:
    settings: Settings = get_settings()
    card = _a_card(catalog)

    await image_service.get_image(catalog, settings, card.id)
    await image_service.get_image(catalog, settings, card.id)

    assert len(fake_download) == 1


async def test_a_cache_row_without_its_file_re_downloads(
    catalog: DbSession, fake_download: list[str]
) -> None:
    """Survives a partial restore or a manual clean-out of the images directory."""
    settings: Settings = get_settings()
    card = _a_card(catalog)

    first = await image_service.get_image(catalog, settings, card.id)
    first.path.unlink()

    second = await image_service.get_image(catalog, settings, card.id)

    assert second.path.exists()
    assert len(fake_download) == 2
    assert catalog.scalars(select(ImageCacheEntry)).one()


async def test_unknown_card_is_a_404(catalog: DbSession, fake_download: list[str]) -> None:
    with pytest.raises(NotFound):
        await image_service.get_image(catalog, get_settings(), 999_999)
    assert fake_download == []


async def test_a_card_without_an_image_url_is_a_404(
    catalog: DbSession, fake_download: list[str]
) -> None:
    card = _a_card(catalog)
    card.image_normal_url = None
    catalog.flush()

    with pytest.raises(NotFound):
        await image_service.get_image(catalog, get_settings(), card.id)


async def test_art_crop_is_never_served(catalog: DbSession, fake_download: list[str]) -> None:
    """art_crop is downloaded, hashed and discarded by Phase 6; it is never cached."""
    with pytest.raises(NotFound) as excinfo:
        await image_service.get_image(catalog, get_settings(), _a_card(catalog).id, "art_crop")
    assert excinfo.value.detail["allowed"] == ["normal", "small"]


async def test_images_are_sharded_across_directories(
    catalog: DbSession, fake_download: list[str]
) -> None:
    """One directory holding tens of thousands of files is slow on most filesystems."""
    settings: Settings = get_settings()
    image = await image_service.get_image(catalog, settings, _a_card(catalog).id)
    relative = image.path.relative_to(settings.images_path)
    assert relative.parts[0] == "normal"
    assert len(relative.parts) == 3


def test_lru_eviction_frees_space_down_to_the_cap(
    catalog: DbSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings: Settings = get_settings()
    monkeypatch.setattr(settings, "image_cache_max_mb", 0)

    paths = []
    for index in range(3):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(b"x" * 1024)
        paths.append(path)
        catalog.add(
            ImageCacheEntry(
                card_id=index + 1,
                size="normal",
                path=str(path),
                bytes=1024,
                last_accessed_at=f"2026-01-0{index + 1}T00:00:00+00:00",
            )
        )
    catalog.flush()

    assert image_service.cache_size_bytes(catalog) == 3072
    evicted = image_service.enforce_cache_limit(catalog, settings)

    assert evicted == 3
    assert all(not path.exists() for path in paths)
    assert image_service.cache_size_bytes(catalog) == 0


def test_eviction_removes_the_least_recently_used_first(
    catalog: DbSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    settings: Settings = get_settings()
    # Cap of 1 MiB with three 512 KiB entries: exactly one has to go.
    monkeypatch.setattr(settings, "image_cache_max_mb", 1)

    half_mib = 512 * 1024
    for index, accessed in enumerate(["2026-03-01", "2026-01-01", "2026-02-01"]):
        path = tmp_path / f"{index}.jpg"
        path.write_bytes(b"x")
        catalog.add(
            ImageCacheEntry(
                card_id=index + 1,
                size="normal",
                path=str(path),
                bytes=half_mib,
                last_accessed_at=f"{accessed}T00:00:00+00:00",
            )
        )
    catalog.flush()

    assert image_service.enforce_cache_limit(catalog, settings) == 1
    remaining = {entry.card_id for entry in catalog.scalars(select(ImageCacheEntry))}
    assert remaining == {1, 3}  # card 2, accessed in January, is the one evicted


def test_eviction_is_a_no_op_under_the_cap(catalog: DbSession) -> None:
    assert image_service.enforce_cache_limit(catalog, get_settings()) == 0
