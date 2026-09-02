"""Look at the set-symbol region on real card images, across eras and layouts.

Before choosing how to match a set symbol it is worth seeing what there is to match.
This crops the region from a spread of real printings, upscales it, and lays them out
in one contact sheet with labels, so the questions that actually decide the design can
be answered by looking: does one fixed crop land on the symbol across twenty years of
frame changes, how much does rarity colour move it, and is the silhouette clean enough
to threshold.

Usage, against the running stack::

    docker compose cp backend/scripts/symbol_probe.py app:/tmp/symbol_probe.py
    docker compose exec app sh -c 'cd /srv && PYTHONPATH=/srv python /tmp/symbol_probe.py'

Writes ``/data/symbol_probe.png``. Read-only apart from that file and the image cache.
"""

from __future__ import annotations

import asyncio
import sys

from PIL import Image, ImageDraw
from sqlalchemy import select

from app.config import get_settings
from app.db import session_scope
from app.models import Card
from app.services import images as image_service

# The rectified card the scanner works with.
CARD_W, CARD_H = 488, 680

# The type line's right end, where the symbol sits. Deliberately generous: this probe
# exists to find the real bounds, not to assume them.
BAND = (0.030, 0.885, 0.480, 0.995)

UPSCALE = 4

# A spread chosen to break a naive crop if anything will: pre-modern frames, a
# planeswalker, a full-art land, a borderless card, an adventure, and a showcase.
WANTED: list[tuple[str, None]] = []

# The layouts a crop tuned on normal cards is most likely to miss.
AWKWARD = [
    ("full-art land", "Card.type_line.like('%Basic Land%') and full art"),
    ("borderless", "border_color = borderless"),
    ("showcase", "frame_effects showcase"),
    ("planeswalker", "type_line like %Planeswalker%"),
    ("saga", "type_line like %Saga%"),
    ("adventure", "layout = adventure"),
]


async def main() -> int:
    """Build the contact sheet."""
    settings = get_settings()
    tiles: list[tuple[str, Image.Image]] = []

    base = [
        (
            set_code,
            select(Card).where(
                Card.set_code == set_code,
                Card.image_normal_url.isnot(None),
                Card.lang == "en",
                Card.digital.is_(False),
            ),
        )
        for set_code, _ in WANTED
    ]
    common = (
        Card.image_normal_url.isnot(None),
        Card.lang == "en",
        Card.digital.is_(False),
    )
    tricky = [
        (label, select(Card).where(*common, Card.id == cid))
        for cid, label in (
            (40708, "m10-97"),
            (29493, "m11-98"),
            (7977, "m12-99"),
            (65417, "mbs-125"),
            (88808, "zen-90"),
            (99700, "10e-146"),
        )
    ]

    with session_scope() as db:
        for set_code, statement in base + tricky:
            card = db.scalars(statement.order_by(Card.collector_number).limit(1)).first()
            if card is None:
                print(f"  {set_code}: no printing with an image", file=sys.stderr)
                continue
            try:
                cached = await image_service.get_image(db, settings, card.id)
            except Exception as error:
                print(f"  {set_code}: {error}", file=sys.stderr)
                continue

            with Image.open(cached.path) as raw:
                card_image = raw.convert("RGB").resize((CARD_W, CARD_H), Image.LANCZOS)
            box = (
                int(BAND[0] * CARD_W),
                int(BAND[1] * CARD_H),
                int(BAND[2] * CARD_W),
                int(BAND[3] * CARD_H),
            )
            crop = card_image.crop(box)
            crop = crop.resize((crop.width * UPSCALE, crop.height * UPSCALE), Image.LANCZOS)
            tiles.append((f"{set_code} {card.collector_number} {card.rarity}", crop))
            db.commit()

    if not tiles:
        print("nothing to show", file=sys.stderr)
        return 1

    tile_w = max(tile.width for _label, tile in tiles)
    tile_h = max(tile.height for _label, tile in tiles)
    label_h = 18
    columns = 2
    rows = (len(tiles) + columns - 1) // columns
    sheet = Image.new("RGB", (columns * (tile_w + 8), rows * (tile_h + label_h + 8)), (20, 22, 28))
    draw = ImageDraw.Draw(sheet)
    for position, (label, tile) in enumerate(tiles):
        column, row = position % columns, position // columns
        x = column * (tile_w + 8) + 4
        y = row * (tile_h + label_h + 8) + 4
        sheet.paste(tile, (x, y))
        draw.text((x, y + tile_h + 2), label, fill=(210, 215, 225))

    out = settings.data_dir / "symbol_probe.png"
    sheet.save(out)
    print(f"wrote {out} ({len(tiles)} tiles, band={BAND})")
    return 0


if __name__ == "__main__":
    raise SystemExit(asyncio.run(main()))
