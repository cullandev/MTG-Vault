"""End-to-end scanner probe against the live database and real card images.

The unit suites run against synthetic scenes and a 17-set fixture catalogue. This runs
the whole pipeline against the *real* one -- 116 000+ printings, 900-odd set codes --
using **each card's actual Scryfall image** composited into a cluttered camera frame.

That matters for one signal in particular: the perceptual hash can only be measured
against real artwork. A synthetic card face matches nothing in the index no matter how
well the hash works, so a probe that renders its own cards silently tests two thirds of
the pipeline and reports the result as if it were the whole thing.

Each card is presented several ways -- centred, rotated, small and off-centre, upside
down -- because framing tolerance is the thing ADR-024 set out to fix, and a probe that
only ever centres the card cannot see whether it did.

Usage, against the running stack:

    docker compose cp backend/scripts/scan_smoke.py app:/tmp/scan_smoke.py
    docker compose exec app sh -c 'cd /srv && PYTHONPATH=/srv python /tmp/scan_smoke.py'

Outcomes are reported in four buckets, not two. "Did it lock in?" is the wrong
question on its own: declining to auto-add a basic Island when forty near-identical
printings exist, and offering a picker instead, is correct behaviour rather than a
failure. What matters is whether the right printing was *found* and how confidently.

Note also that this measures **single-frame** identification, which is the strictest
possible reading. The real scanner accumulates evidence across frames within a session,
so a card scoring 0.9 here locks in on its second frame.

Writes scan_events rows (that is what the accuracy statistic is made of) and rolls back
everything else.
"""

from __future__ import annotations

import asyncio
import sys
import tempfile
import time
from pathlib import Path

import cv2
import numpy as np
from sqlalchemy import select

from app.clients.scryfall import ScryfallClient
from app.config import Settings, get_settings
from app.db import session_scope
from app.models import Card, CardHash
from app.services.scan.identify import identify_sync
from app.vision import index as vision_index

SAMPLES = 10

FRAME_WIDTH = 1280
FRAME_HEIGHT = 720

Presentation = tuple[str, float, tuple[int, int], int]
PRESENTATIONS: tuple[Presentation, ...] = (
    ("centred", 0.0, (640, 360), 560),
    ("rotated 25 deg", 25.0, (640, 360), 500),
    ("rotated -40 deg", -40.0, (640, 360), 430),
    ("off-centre, small", 0.0, (330, 250), 300),
    ("upside down", 180.0, (640, 360), 520),
)
"""Before ADR-024 only the first of these would have been found at all."""


def background(seed: int = 5) -> np.ndarray:
    """A cluttered frame: noise plus a distractor that is not card-shaped."""
    rng = np.random.default_rng(seed)
    frame = rng.integers(45, 105, (FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    cv2.rectangle(frame, (40, 380), (340, 680), (150, 140, 130), -1)
    return frame


def compose(face: np.ndarray, presentation: Presentation) -> bytes:
    """Composite a real card image into a camera frame, as JPEG."""
    _label, angle, centre, height = presentation
    frame = background()
    card = cv2.resize(face, (max(4, round(height / 1.397)), height))
    card_height, card_width = card.shape[:2]

    canvas = np.zeros_like(frame)
    mask = np.zeros(frame.shape[:2], np.uint8)
    x = centre[0] - card_width // 2
    y = centre[1] - card_height // 2
    canvas[y : y + card_height, x : x + card_width] = card
    mask[y : y + card_height, x : x + card_width] = 255

    if angle:
        rotation = cv2.getRotationMatrix2D((float(centre[0]), float(centre[1])), angle, 1.0)
        size = (FRAME_WIDTH, FRAME_HEIGHT)
        canvas = cv2.warpAffine(canvas, rotation, size)
        mask = cv2.warpAffine(mask, rotation, size, flags=cv2.INTER_NEAREST)

    frame[mask > 0] = canvas[mask > 0]
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), 75])
    if not ok:
        raise RuntimeError("Could not encode the frame")
    return bytes(buffer)


async def fetch_faces(settings: Settings, cards: list[Card], into: Path) -> dict[int, np.ndarray]:
    """Download each sample's real card image."""
    client = ScryfallClient(settings)
    faces: dict[int, np.ndarray] = {}
    for card in cards:
        if not card.image_normal_url:
            continue
        destination = into / f"{card.id}.jpg"
        await client.download(card.image_normal_url, destination)
        image = cv2.imdecode(np.fromfile(str(destination), dtype=np.uint8), cv2.IMREAD_COLOR)
        if image is not None:
            faces[card.id] = image
        destination.unlink(missing_ok=True)
    return faces


def main() -> int:
    """Present real card images to the pipeline several ways each, and report."""
    settings = get_settings()
    checked = 0
    by_method: dict[str, int] = {}
    outcomes = {
        "locked": 0,
        "locked wrong": 0,
        "picker, right": 0,
        "picker, wrong": 0,
        "nothing": 0,
    }

    with session_scope() as db:
        index_size = len(vision_index.get_index(db))
        print(f"hash index: {index_size:,} printings" + ("" if index_size else "  (EMPTY)"))

        # Sample from printings that *are* in the hash index, so the visual signal is
        # actually under test rather than silently absent.
        samples = list(
            db.scalars(
                select(Card)
                .join(CardHash, CardHash.card_id == Card.id)
                .where(Card.digital.is_(False), Card.lang == "en")
                .order_by(Card.id)
                .limit(SAMPLES * 8)
            )
        )[::8][:SAMPLES]
        if not samples:
            print("No hashed printings yet -- run `python -m app.cli build-hashes` first.")
            return 1

        with tempfile.TemporaryDirectory(prefix="mtgvault-smoke-") as scratch:
            faces = asyncio.run(fetch_faces(settings, samples, Path(scratch)))

        for card in samples:
            face = faces.get(card.id)
            if face is None:
                continue
            for presentation in PRESENTATIONS:
                label = presentation[0]
                checked += 1
                payload = compose(face, presentation)

                started = time.perf_counter()
                result = identify_sync(db, settings, payload)
                elapsed = (time.perf_counter() - started) * 1000

                by_method[result.method] = by_method.get(result.method, 0) + 1
                ranked = [item.card_id for item in result.candidates]
                if result.match is not None:
                    outcome = "locked" if result.match.card_id == card.id else "locked wrong"
                elif not ranked:
                    outcome = "nothing"
                else:
                    outcome = "picker, right" if card.id in ranked[:3] else "picker, wrong"
                outcomes[outcome] += 1
                hit = outcome == "locked"

                # A miss that named a *different printing of the same card* is a very
                # different problem from one that named the wrong card: while the hash
                # index is still being built the correct printing may simply not be in
                # it yet, and a sibling printing legitimately wins. Distinguishing the
                # two is the difference between "wait for the job" and "fix something".
                note = ""
                if result.match is not None and not hit:
                    same = result.match.oracle_id == card.oracle_id
                    note = (
                        f"  -> {'same card' if same else 'WRONG CARD'} "
                        f"{result.match.set_code}/{result.match.collector_number}"
                    )
                elif outcome.startswith("picker"):
                    note = f"  -> rank {ranked.index(card.id) + 1}" if card.id in ranked else ""
                print(
                    f"{outcome:<13} {card.set_code}/{card.collector_number:>4} "
                    f"{card.name[:20]:<20} {label:<18} n={len(result.detections)} "
                    f"method={result.method:<9} conf={result.confidence:4.2f} "
                    f"{elapsed:6.0f}ms{note}"
                )

        db.rollback()

    breakdown = ", ".join(f"{name}={count}" for name, count in sorted(by_method.items()))
    print(f"\nof {checked} presentations:")
    for name, count in outcomes.items():
        print(f"  {name:<14} {count:>3}")
    found = outcomes["locked"] + outcomes["picker, right"]
    print(f"\nright printing found: {found}/{checked}   signals: [{breakdown}]")
    return 0 if outcomes["locked wrong"] == 0 and found >= checked * 0.8 else 1


if __name__ == "__main__":
    sys.exit(main())
