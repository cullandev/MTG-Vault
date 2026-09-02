"""Synthetic card images for scanner tests.

These are **not** photographs of real cards, and they do not claim to measure
real-world accuracy -- only a phone, a lamp and a stack of actual cardboard can do
that, which is why the on-device scan run is a required manual step in TEST-PLAN.md.

What they *do* measure is that the pipeline works and stays working: that the title
bar is cropped from the right place, that thresholding survives a dark mat and a
blown-out foil, and that a name rendered into a card-shaped image comes back out of
OCR intact. A regression in preprocessing shows up here immediately.
"""

from __future__ import annotations

import io
import random
from dataclasses import dataclass
from pathlib import Path

from PIL import Image, ImageDraw, ImageFilter, ImageFont

CARD_SIZE = (480, 672)
"""What the scanner sends after rectifying the frame."""

_FONT_CANDIDATES = [
    "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
    "/usr/share/fonts/dejavu/DejaVuSans-Bold.ttf",
    "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
    "C:/Windows/Fonts/arialbd.ttf",
    "C:/Windows/Fonts/arial.ttf",
    "/System/Library/Fonts/Supplemental/Arial Bold.ttf",
]


class NoFontAvailable(RuntimeError):
    """No TrueType font was found to render test cards with."""


def find_font(size: int) -> ImageFont.FreeTypeFont:
    """Locate a usable TrueType font.

    Raises:
        NoFontAvailable: None of the known locations has one. The app image installs
            ``fonts-dejavu-core`` precisely so this cannot happen there.
    """
    for candidate in _FONT_CANDIDATES:
        path = Path(candidate)
        if path.is_file():
            return ImageFont.truetype(str(path), size)
    raise NoFontAvailable(
        "No TrueType font found. Install fonts-dejavu-core, or run the tests in the "
        "app container where it is already present."
    )


@dataclass(frozen=True)
class CardStyle:
    """How to render a synthetic card."""

    background: tuple[int, int, int] = (222, 214, 196)
    title_bar: tuple[int, int, int] = (238, 232, 216)
    text: tuple[int, int, int] = (24, 20, 16)
    font_size: int = 30
    title_y: float = 0.055
    """Where the baseline of the name sits, as a fraction of card height."""
    collector_font_size: int = 12
    """The collector line is printed tiny -- about 12px on a 672px-tall card. Rendering
    it at a realistic size is the point: anything larger would not exercise the
    upscaling the preprocessor does to make it legible."""
    collector_bar: tuple[int, int, int] = (18, 16, 14)
    collector_text: tuple[int, int, int] = (238, 236, 232)


MODERN = CardStyle()
OLD_FRAME = CardStyle(
    background=(198, 190, 172), title_bar=(206, 199, 182), font_size=28, title_y=0.070
)
DARK = CardStyle(background=(38, 34, 30), title_bar=(46, 41, 36), text=(232, 228, 220))
WHITE_BORDER = CardStyle(
    background=(244, 242, 238),
    title_bar=(250, 249, 246),
    collector_bar=(250, 249, 246),
    collector_text=(28, 26, 24),
)
"""A white-bordered treatment, where the collector line is dark on light -- the
polarity the modern black border does not exercise."""


def render_card(
    name: str,
    *,
    style: CardStyle = MODERN,
    size: tuple[int, int] = CARD_SIZE,
    mana_cost: str = "2R",
    type_line: str = "Instant",
    collector_number: str | None = "0028",
    collector_total: str = "281",
    set_code: str = "FIN",
    rarity: str = "R",
) -> Image.Image:
    """Render a card-shaped image with ``name`` in the title bar.

    Args:
        collector_number: Printed in the bottom-left corner over the set code, as
            every card since Magic 2015 carries. Pass ``None`` for a pre-2015 frame,
            which has no collector line at all.
    """
    width, height = size
    card = Image.new("RGB", size, style.background)
    draw = ImageDraw.Draw(card)

    # Title bar band.
    draw.rectangle(
        [(int(width * 0.03), int(height * 0.025)), (int(width * 0.97), int(height * 0.105))],
        fill=style.title_bar,
    )
    font = find_font(style.font_size)
    draw.text((int(width * 0.06), int(height * style.title_y)), name, font=font, fill=style.text)

    # A mana cost on the right, which the crop is supposed to exclude.
    small = find_font(max(12, style.font_size - 8))
    draw.text(
        (int(width * 0.83), int(height * style.title_y)), mana_cost, font=small, fill=style.text
    )

    # Art box and type line, so the card is not a blank field the crop could drift into.
    draw.rectangle(
        [(int(width * 0.06), int(height * 0.12)), (int(width * 0.94), int(height * 0.52))],
        fill=(120, 116, 104),
    )
    draw.text((int(width * 0.06), int(height * 0.545)), type_line, font=small, fill=style.text)

    if collector_number is not None:
        _draw_collector_line(
            draw,
            size,
            style,
            f"{collector_number}/{collector_total} {rarity}",
            f"{set_code} • EN • Test Artist",
        )
    return card


def _draw_collector_line(
    draw: ImageDraw.ImageDraw,
    size: tuple[int, int],
    style: CardStyle,
    first: str,
    second: str,
) -> None:
    """Print the two-line collector block into the bottom-left corner."""
    width, height = size
    # Positions measured off a photograph of a real card rather than guessed: the
    # number sits at about 0.937 of the card's height and the set line at 0.956. An
    # earlier version placed them some five percent higher, which quietly aimed the
    # OCR band at the wrong part of every real card while these tests stayed green.
    draw.rectangle(
        [(0, int(height * 0.915)), (width, int(height * 0.985))], fill=style.collector_bar
    )
    tiny = find_font(style.collector_font_size)
    draw.text(
        (int(width * 0.055), int(height * 0.928)), first, font=tiny, fill=style.collector_text
    )
    draw.text(
        (int(width * 0.055), int(height * 0.950)), second, font=tiny, fill=style.collector_text
    )


def with_glare(card: Image.Image, *, strength: float = 0.85) -> Image.Image:
    """Blow out a diagonal band across the title bar, the way a foil under a lamp does."""
    glare = Image.new("L", card.size, 0)
    draw = ImageDraw.Draw(glare)
    width, height = card.size
    draw.polygon(
        [
            (0, int(height * 0.02)),
            (int(width * 0.75), 0),
            (width, int(height * 0.12)),
            (int(width * 0.2), int(height * 0.16)),
        ],
        fill=int(255 * strength),
    )
    glare = glare.filter(ImageFilter.GaussianBlur(radius=12))
    white = Image.new("RGB", card.size, (255, 255, 255))
    return Image.composite(white, card, glare)


def with_noise(card: Image.Image, *, amount: int = 12, seed: int = 7) -> Image.Image:
    """Add camera sensor noise."""
    rng = random.Random(seed)
    noisy = card.copy()
    pixels = noisy.load()
    assert pixels is not None
    width, height = noisy.size
    for y in range(0, height, 2):
        for x in range(0, width, 2):
            jitter = rng.randint(-amount, amount)
            red, green, blue = pixels[x, y]
            pixels[x, y] = (
                max(0, min(255, red + jitter)),
                max(0, min(255, green + jitter)),
                max(0, min(255, blue + jitter)),
            )
    return noisy


def with_blur(card: Image.Image, *, radius: float = 1.2) -> Image.Image:
    """Soften the image the way a hand-held camera does."""
    return card.filter(ImageFilter.GaussianBlur(radius=radius))


def rotated(card: Image.Image, degrees: float) -> Image.Image:
    """Rotate slightly, as an imperfectly rectified crop would be."""
    return card.rotate(degrees, resample=Image.Resampling.BICUBIC, fillcolor=(200, 194, 178))


def as_jpeg(card: Image.Image, quality: int = 85) -> bytes:
    """Encode as the scanner does."""
    buffer = io.BytesIO()
    card.save(buffer, format="JPEG", quality=quality)
    return buffer.getvalue()
