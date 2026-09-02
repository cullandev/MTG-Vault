"""Does the type-line band actually tell sibling printings apart?

The artwork hash cannot separate printings that share an illustration -- measured at 16
to 60 bits, inside photographic noise (ADR-027). This asks the same question of the
symbol band before anything is built on it: take real printings that share an artwork,
hash their type-line bands, and report how far apart siblings land versus how far apart
a printing lands from *itself* re-encoded.

The second number is the one that matters. A signal is only useful if the gap between
different sets is larger than the wobble introduced by a photograph.

Usage::

    docker compose cp backend/scripts/symbol_separation.py app:/tmp/sep.py
    docker compose exec app sh -c 'cd /srv && PYTHONPATH=/srv python /tmp/sep.py'
"""

from __future__ import annotations

import asyncio
import statistics
import sys

import cv2
import numpy as np
from sqlalchemy import select, text

from app.config import get_settings
from app.db import session_scope
from app.models import Card
from app.services import images as image_service
from app.vision import hashing

CARD_W, CARD_H = 488, 680
GROUPS = 12
"""Artworks to test. Each contributes one sibling comparison per extra printing."""


def _bits(one: bytes, two: bytes) -> int:
    """Hamming distance between two packed hashes."""
    left = np.frombuffer(one, dtype=np.uint8)
    right = np.frombuffer(two, dtype=np.uint8)
    return int(np.unpackbits(np.bitwise_xor(left, right)).sum())


def _degrade(card: np.ndarray) -> np.ndarray:
    """Approximate what a phone does to a card: soften, dim, and re-encode."""
    blurred = cv2.GaussianBlur(card, (3, 3), 0)
    dimmed = np.clip(blurred.astype(np.float32) * 0.88 + 6.0, 0, 255).astype(np.uint8)
    ok, buffer = cv2.imencode(".jpg", dimmed, [int(cv2.IMWRITE_JPEG_QUALITY), 72])
    return cv2.imdecode(buffer, cv2.IMREAD_COLOR) if ok else dimmed


async def main() -> int:
    """Measure sibling separation against self-distance."""
    settings = get_settings()
    sibling_gaps: list[int] = []
    self_gaps: list[int] = []
    art_gaps: list[int] = []
    checked = 0

    with session_scope() as db:
        artworks = (
            db.execute(
                text(
                    """
                select k.illustration_id
                from cards k
                join collection_items ci on ci.card_id = k.id
                where k.illustration_id is not null
                group by k.illustration_id
                having count(distinct k.set_code) > 1
                limit :limit
                """
                ),
                {"limit": GROUPS},
            )
            .scalars()
            .all()
        )

        for illustration_id in artworks:
            printings = db.scalars(
                select(Card)
                .where(
                    Card.illustration_id == illustration_id,
                    Card.image_normal_url.isnot(None),
                    Card.lang == "en",
                    Card.digital.is_(False),
                )
                .limit(4)
            ).all()
            if len(printings) < 2:
                continue

            hashes: list[tuple[str, bytes, bytes]] = []
            for card in printings:
                try:
                    cached = await image_service.get_image(db, settings, card.id)
                except Exception as error:
                    print(f"  {card.set_code}: {error}", file=sys.stderr)
                    continue
                image = cv2.imread(str(cached.path), cv2.IMREAD_COLOR)
                if image is None:
                    continue
                rectified = cv2.resize(image, (CARD_W, CARD_H), interpolation=cv2.INTER_AREA)
                hashes.append(
                    (
                        f"{card.set_code}/{card.collector_number}",
                        hashing.symbol_hash(rectified),
                        hashing.card_hash(rectified),
                    )
                )
                self_gaps.append(
                    _bits(hashing.symbol_hash(rectified), hashing.symbol_hash(_degrade(rectified)))
                )
            db.commit()

            for i in range(len(hashes)):
                for j in range(i + 1, len(hashes)):
                    sibling_gaps.append(_bits(hashes[i][1], hashes[j][1]))
                    art_gaps.append(_bits(hashes[i][2], hashes[j][2]))
            if len(hashes) >= 2:
                checked += 1
                print(f"{illustration_id[:8]} {[h[0] for h in hashes]}")
                print(
                    f"   symbol band apart: {[_bits(hashes[0][1], h[1]) for h in hashes[1:]]}"
                    f"   artwork apart: {[_bits(hashes[0][2], h[2]) for h in hashes[1:]]}"
                )

    if not sibling_gaps:
        print("no sibling pairs available", file=sys.stderr)
        return 1

    def summarise(label: str, values: list[int], total_bits: int) -> None:
        print(
            f"{label:28s} n={len(values):3d}  "
            f"median={statistics.median(values):6.1f}  "
            f"min={min(values):3d}  max={max(values):3d}   of {total_bits} bits"
        )

    print()
    summarise("siblings, symbol band", sibling_gaps, hashing.SYMBOL_HASH_BYTES * 8)
    summarise("same card, re-encoded", self_gaps, hashing.SYMBOL_HASH_BYTES * 8)
    summarise("siblings, artwork hash", art_gaps, hashing.HASH_BYTES * 8)
    print()
    print("The symbol band is worth using only if siblings sit well above the re-encode wobble.")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
