"""Behaviour at collection scale.

The brief targets 10 000+ cards. These tests exist so that "it's fast enough" is a
measured claim rather than a hope, and so a future index change that quietly turns a
seek into a scan fails the build.
"""

from __future__ import annotations

import json
import time
import tracemalloc
from pathlib import Path
from typing import Any

import pytest
from sqlalchemy import text
from sqlalchemy.orm import Session as DbSession

from app.models import Card, CollectionItem, OracleCard, color_mask, utcnow
from app.services.collection import query as query_service
from app.services.imports import scryfall_bulk

CARD_COUNT = 3_000
COPY_COUNT = 10_000

_COLOURS = ["W", "U", "B", "R", "G", "WU", "BR", "GW", "", "WUBRG"]
_TYPES = ["Creature — Human", "Instant", "Sorcery", "Artifact", "Land", "Enchantment"]
_SETS = ["2ed", "isd", "znr", "eld", "shm", "nph", "mh2", "cmr", "ltr", "otj"]


def _synthetic_cards(count: int) -> list[dict[str, Any]]:
    """Build a catalogue big enough that the indexes matter."""
    rows = []
    for index in range(count):
        identity = _COLOURS[index % len(_COLOURS)]
        rows.append(
            {
                "oracle_id": f"oracle-{index:06d}",
                "set_code": _SETS[index % len(_SETS)],
                "collector_number": str(index),
                "lang": "en",
                "identity": identity,
                "mask": color_mask(identity),
                "type_line": _TYPES[index % len(_TYPES)],
                "cmc": float(index % 8),
                "rarity": ["common", "uncommon", "rare", "mythic"][index % 4],
                "price": (index * 37) % 5000,
            }
        )
    return rows


@pytest.fixture
def big_collection(db: DbSession) -> DbSession:
    """Seed 3 000 printings and 10 000 owned copies."""
    now = utcnow()
    cards = _synthetic_cards(CARD_COUNT)

    db.bulk_insert_mappings(  # type: ignore[arg-type]
        OracleCard,
        [
            {
                "oracle_id": row["oracle_id"],
                "name": f"Test Card {index:06d}",
                "name_norm": f"test card {index:06d}",
                "name_front": f"Test Card {index:06d}",
                "name_front_norm": f"test card {index:06d}",
                "layout": "normal",
                "type_line": row["type_line"],
                "oracle_text_all": f"Synthetic rules text number {index}.",
                "cmc": row["cmc"],
                "color_identity": row["identity"],
                "color_identity_mask": row["mask"],
                "updated_at": now,
                "is_legendary": False,
                "is_creature": "Creature" in row["type_line"],
                "is_land": "Land" in row["type_line"],
                "reserved": False,
                "game_changer": False,
            }
            for index, row in enumerate(cards)
        ],
    )
    db.bulk_insert_mappings(  # type: ignore[arg-type]
        Card,
        [
            {
                "id": index + 1,
                "scryfall_id": f"scry-{index:06d}",
                "oracle_id": row["oracle_id"],
                "set_code": row["set_code"],
                "collector_number": row["collector_number"],
                "lang": "en",
                "name": f"Test Card {index:06d}",
                "name_front": f"Test Card {index:06d}",
                "name_norm": f"test card {index:06d}",
                "layout": "normal",
                "rarity": row["rarity"],
                "cmc": row["cmc"],
                "type_line": row["type_line"],
                "color_identity": row["identity"],
                "color_identity_mask": row["mask"],
                "digital": False,
                "promo": False,
                "variation": False,
                "reserved": False,
                "game_changer": False,
                "price_usd_cents": row["price"],
                "price_updated_at": now,
                "imported_at": now,
            }
            for index, row in enumerate(cards)
        ],
    )
    db.bulk_insert_mappings(  # type: ignore[arg-type]
        CollectionItem,
        [
            {
                "card_id": (index % CARD_COUNT) + 1,
                "oracle_id": cards[index % CARD_COUNT]["oracle_id"],
                "set_code": cards[index % CARD_COUNT]["set_code"],
                "collector_number": cards[index % CARD_COUNT]["collector_number"],
                "lang": "en",
                "finish": "foil" if index % 7 == 0 else "nonfoil",
                "condition": "NM",
                "is_proxy": index % 50 == 0,
                "created_at": now,
                "updated_at": now,
            }
            for index in range(COPY_COUNT)
        ],
    )
    db.commit()
    db.execute(text("ANALYZE"))
    db.commit()
    return db


def test_seeded_at_target_scale(big_collection: DbSession) -> None:
    totals = query_service.collection_totals(big_collection)
    assert totals["copies"] == COPY_COUNT
    assert totals["unique_cards"] == CARD_COUNT


def test_first_page_is_fast(big_collection: DbSession) -> None:
    """The library grid must feel instant on a phone, not merely finish eventually."""
    started = time.perf_counter()
    page = query_service.query_collection(big_collection, limit=60)
    elapsed_ms = (time.perf_counter() - started) * 1000

    assert len(page.items) == 60
    assert elapsed_ms < 150, f"first page took {elapsed_ms:.0f}ms"


def test_deep_paging_does_not_degrade(big_collection: DbSession) -> None:
    """Keyset paging is why page 40 costs the same as page 1 (ADR-020)."""
    cursor = None
    timings = []
    for _ in range(40):
        started = time.perf_counter()
        page = query_service.query_collection(
            big_collection, cursor=cursor, limit=60, with_totals=False
        )
        timings.append((time.perf_counter() - started) * 1000)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(timings) >= 40
    assert max(timings[-5:]) < 3 * max(timings[:5]) + 50


def test_paging_never_repeats_or_skips_a_row(big_collection: DbSession) -> None:
    seen: list[str] = []
    cursor = None
    while True:
        page = query_service.query_collection(
            big_collection, cursor=cursor, limit=250, with_totals=False
        )
        seen.extend(row.group_key for row in page.items)
        cursor = page.next_cursor
        if cursor is None:
            break

    assert len(seen) == CARD_COUNT
    assert len(set(seen)) == CARD_COUNT


def test_oracle_lookup_uses_its_index(big_collection: DbSession) -> None:
    plan = big_collection.execute(
        text(
            "EXPLAIN QUERY PLAN SELECT id FROM collection_items "
            "WHERE oracle_id = 'oracle-000001' AND finish = 'foil'"
        )
    ).all()
    detail = " ".join(str(row[-1]) for row in plan)
    assert "ix_collection_items_oracle_id_finish_is_proxy" in detail


def test_filtered_query_returns_a_sensible_subset(big_collection: DbSession) -> None:
    page = query_service.query_collection(
        big_collection,
        query_service.CollectionFilters(type_contains="Land", mv_max=0),
        limit=200,
    )
    assert page.items
    assert all("Land" in (row.type_line or "") for row in page.items)


# --- streaming import memory ----------------------------------------------


def _write_big_bulk(path: Path, count: int) -> None:
    """Write a bulk-shaped file large enough that loading it whole would show up."""
    with path.open("w", encoding="utf-8") as handle:
        handle.write("[")
        for index in range(count):
            if index:
                handle.write(",")
            handle.write(
                json.dumps(
                    {
                        "object": "card",
                        "id": f"11111111-0000-4000-8000-{index:012d}",
                        "oracle_id": f"aaaa1111-0000-4000-8000-{index:012d}",
                        "name": f"Bulk Card {index}",
                        "lang": "en",
                        "layout": "normal",
                        "set": "big",
                        "set_name": "Big Set",
                        "collector_number": str(index),
                        "rarity": "common",
                        "mana_cost": "{1}{G}",
                        "cmc": 2.0,
                        "type_line": "Creature — Test",
                        "oracle_text": "Lorem ipsum dolor sit amet. " * 12,
                        "colors": ["G"],
                        "color_identity": ["G"],
                        "keywords": ["Trample"],
                        "finishes": ["nonfoil", "foil"],
                        "released_at": "2024-01-01",
                        "illustration_id": f"bbbb1111-0000-4000-8000-{index:012d}",
                        "image_uris": {"normal": "https://cards.example/x.jpg"},
                        "digital": False,
                        "reserved": False,
                        "game_changer": False,
                        "prices": {"usd": "1.00", "usd_foil": "2.00", "usd_etched": None},
                        "legalities": {
                            "standard": "legal",
                            "modern": "legal",
                            "legacy": "legal",
                            "vintage": "legal",
                            "commander": "legal",
                            "pauper": "legal",
                        },
                    }
                )
            )
        handle.write("]")


@pytest.mark.slow
def test_bulk_import_memory_is_bounded_by_batch_size(db: DbSession, tmp_path: Path) -> None:
    """ADR-004. The file is streamed, so peak allocation tracks the batch, not the file.

    Measured with tracemalloc rather than RSS so the assertion is about *our*
    allocations and does not depend on the allocator returning pages to the OS.
    """
    bulk = tmp_path / "big_bulk.json"
    _write_big_bulk(bulk, 20_000)
    size_mb = bulk.stat().st_size / 1024 / 1024
    assert size_mb > 8, f"fixture too small to be meaningful ({size_mb:.1f} MB)"

    tracemalloc.start()
    try:
        stats = scryfall_bulk.import_bulk(db, bulk, batch_size=2000)
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()

    peak_mb = peak / 1024 / 1024
    assert stats.cards_written == 20_000
    assert peak_mb < 120, f"peak python allocation {peak_mb:.0f} MB"
    # Streaming means peak memory tracks the batch size plus fixed overhead (statement
    # compilation caches and the like), not the file. The allowance is one batch of
    # rows (a few MB) plus 45 MB of fixed overhead -- json.load() on the same file
    # peaks at several times the file size and blows straight through this.
    assert peak_mb < 45 + size_mb, f"peak {peak_mb:.0f} MB for a {size_mb:.0f} MB file"


@pytest.mark.slow
async def test_price_sync_at_scale_is_bounded_and_quick(
    big_collection: DbSession, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    """The nightly price sync over a 10 000-copy collection (ADR-009).

    Two claims, both measured: one snapshot row per *watched printing* rather than per
    copy or per printing in existence, and peak allocation that tracks the flush batch
    rather than the bulk file.
    """
    import httpx
    from sqlalchemy import func, select

    from app.clients.scryfall import ScryfallClient
    from app.config import Settings, get_settings
    from app.jobs import prices as price_job
    from app.models import PriceSnapshot

    # A bulk file covering every seeded printing, plus as many again that are not owned
    # -- the ones the job must stream past without writing.
    bulk = tmp_path / "priced_bulk.json"
    _write_priced_bulk(bulk, CARD_COUNT * 2)
    original_init = ScryfallClient.__init__

    def patched_init(self: ScryfallClient, settings: Settings, **kwargs: object) -> None:
        def handler(request: httpx.Request) -> httpx.Response:
            if request.url.path == "/bulk-data":
                return httpx.Response(
                    200,
                    json={
                        "data": [
                            {
                                "type": "default_cards",
                                "download_uri": "https://data.test/default_cards.json",
                                "updated_at": utcnow(),
                                "size": bulk.stat().st_size,
                            }
                        ]
                    },
                )
            return httpx.Response(200, content=bulk.read_bytes())

        original_init(self, settings, transport=httpx.MockTransport(handler))  # type: ignore[arg-type]
        self.min_interval_s = 0.0

    monkeypatch.setattr(ScryfallClient, "__init__", patched_init)

    tracemalloc.start()
    started = time.perf_counter()
    try:
        stats = await price_job.sync_prices(get_settings())
        _current, peak = tracemalloc.get_traced_memory()
    finally:
        tracemalloc.stop()
    elapsed_s = time.perf_counter() - started

    # One row per watched printing: not per copy (10 000) and not per printing in the
    # bulk file (6 000). Getting either wrong is a silent order-of-magnitude mistake.
    assert stats.watched == CARD_COUNT
    assert stats.snapshotted == CARD_COUNT
    big_collection.expire_all()
    assert big_collection.scalar(select(func.count()).select_from(PriceSnapshot)) == CARD_COUNT

    peak_mb = peak / 1024 / 1024
    assert peak_mb < 120, f"peak python allocation {peak_mb:.0f} MB"
    assert elapsed_s < 60, f"price sync took {elapsed_s:.1f}s"


def _write_priced_bulk(path: Path, count: int) -> None:
    """A bulk file whose first ``CARD_COUNT`` ids match the seeded printings."""
    with path.open("w", encoding="utf-8") as handle:
        handle.write("[")
        for index in range(count):
            if index:
                handle.write(",")
            handle.write(
                json.dumps(
                    {
                        "id": f"scry-{index:06d}",
                        "object": "card",
                        "name": f"Test Card {index:06d}",
                        "prices": {
                            "usd": f"{(index % 500) / 10:.2f}",
                            "usd_foil": f"{(index % 900) / 10:.2f}",
                            "usd_etched": None,
                        },
                    }
                )
            )
        handle.write("]")
