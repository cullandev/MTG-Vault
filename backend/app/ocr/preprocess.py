"""Turn a warped card crop into something Tesseract can read.

The scanner sends a 480x672 rectified card. Only the title bar matters for name
recognition, and cropping to it before OCR is the single biggest accuracy win
available: it removes the art, the type line, the rules text and the set symbol, all
of which give the recogniser things to misread.

Two variants are produced for every frame because foils are the common failure case.
A foil under a lamp blows the title bar out to near-white with dark text in places and
near-black with light text in others; running both polarities and keeping whichever
matches a real card name better costs one extra OCR call and rescues a lot of cards.

Pillow only, deliberately: OpenCV on the backend would add ~60 MB to the image for
operations that amount to crop, scale, contrast and threshold.
"""

from __future__ import annotations

import io
from collections.abc import Iterator
from dataclasses import dataclass

from PIL import Image, ImageFilter, ImageOps

# Fractions of the card's height and width that contain the name. Measured against a
# modern frame; the 8th-edition-and-earlier frames sit slightly lower, which is why
# the band is generous at the bottom rather than tight.
TITLE_TOP = 0.030
TITLE_BOTTOM = 0.135
TITLE_LEFT = 0.045
TITLE_RIGHT = 0.780
"""Stops before the mana cost. Mana symbols are round blobs that OCR happily reads as
letters, and every one of them is noise in a name lookup."""

UPSCALE = 3
"""Tesseract wants roughly 30px of x-height; a 480px-wide card gives about a third of
that, so the crop is enlarged before thresholding."""

MIN_IMAGE_BYTES = 256
MAX_IMAGE_BYTES = 8 * 1024 * 1024


@dataclass(frozen=True)
class PreparedCrops:
    """The variants handed to the OCR engine, best-guess first."""

    normal: Image.Image
    inverted: Image.Image

    def __iter__(self) -> Iterator[tuple[str, Image.Image]]:
        """Iterate the variants in the order they should be tried."""
        return iter((("normal", self.normal), ("inverted", self.inverted)))


class InvalidImage(ValueError):
    """The uploaded bytes are not a usable image."""


def load_image(data: bytes) -> Image.Image:
    """Decode uploaded bytes into an RGB image.

    Args:
        data: Raw bytes from the scan upload.

    Returns:
        The decoded image in RGB.

    Raises:
        InvalidImage: The payload is empty, oversized, or not a decodable image.
    """
    if len(data) < MIN_IMAGE_BYTES:
        raise InvalidImage("Image payload is too small to be a card crop")
    if len(data) > MAX_IMAGE_BYTES:
        raise InvalidImage("Image payload is too large")
    try:
        image = Image.open(io.BytesIO(data))
        image.load()
    except Exception as exc:  # Pillow raises a wide variety of decoder errors.
        raise InvalidImage(f"Could not decode the image: {exc}") from exc
    return image.convert("RGB")


def crop_title_bar(card: Image.Image) -> Image.Image:
    """Crop the name band out of a rectified card image."""
    width, height = card.size
    box = (
        int(width * TITLE_LEFT),
        int(height * TITLE_TOP),
        int(width * TITLE_RIGHT),
        int(height * TITLE_BOTTOM),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise InvalidImage("Card crop is too small to contain a title bar")
    return card.crop(box)


def _binarise(gray: Image.Image, *, invert: bool, upscale: int = UPSCALE) -> Image.Image:
    """Scale up, normalise contrast and threshold a grayscale title bar.

    The threshold is the image's own mean rather than a fixed value, which keeps it
    working across a lamp-lit desk and a dim bedroom without a magic number.
    """
    scaled = gray.resize(
        (gray.width * upscale, gray.height * upscale), resample=Image.Resampling.LANCZOS
    )
    normalised = ImageOps.autocontrast(scaled, cutoff=2)
    if invert:
        normalised = ImageOps.invert(normalised)
    # A light blur before thresholding removes the speckle that upscaling introduces
    # and that otherwise becomes stray punctuation in the OCR output.
    blurred = normalised.filter(ImageFilter.GaussianBlur(radius=1.0))

    histogram = blurred.histogram()
    total = sum(histogram) or 1
    mean = sum(value * count for value, count in enumerate(histogram)) / total
    threshold = max(60, min(200, int(mean)))
    return blurred.point(lambda pixel: 255 if pixel > threshold else 0, mode="L")


def prepare(card: Image.Image) -> PreparedCrops:
    """Produce the OCR-ready variants of a card's title bar.

    Args:
        card: A rectified card image (the scanner sends 480x672).

    Returns:
        Normal and inverted binarised crops.
    """
    gray = ImageOps.grayscale(crop_title_bar(card))
    return PreparedCrops(
        normal=_binarise(gray, invert=False),
        inverted=_binarise(gray, invert=True),
    )


# --- collector line -----------------------------------------------------------
#
# Cards printed from Magic 2015 onwards carry their own primary key in the bottom-left
# corner, over two lines:
#
#     0028/281 R
#     FIN * EN * Some Artist
#
# Reading that beats reading the name: it resolves to exactly one printing instead of
# a shortlist of similarly-named cards. The band is cropped wide enough to keep both
# lines and stops well before the artist name, which is long, stylised and useless
# here.

COLLECTOR_TOP = 0.905
COLLECTOR_BOTTOM = 0.985
COLLECTOR_LEFT = 0.038
COLLECTOR_RIGHT = 0.430

COLLECTOR_BANDS = ((0.0, 0.0), (-0.032, -0.032), (0.018, 0.012))
"""Vertical offsets to try, in order, as fractions of card height.

A fixed fraction only finds the collector line if the rectified card is framed exactly
like a whole card -- and detection does not guarantee that. A low-contrast border
against a dark mat can leave the traced outline sitting slightly inside the card's true
edge, which shifts every fraction below it. Trying a couple of offsets costs one extra
OCR call each and only in the cases that would otherwise have failed outright, since
the loop stops at the first band that resolves to a real printing."""
"""Stops before the artist name. Artist names are the longest and least predictable
text in the band, and every extra character is another chance to misread the set code."""

COLLECTOR_UPSCALE = 5
"""The collector line is printed far smaller than the name -- roughly 8px tall on a
480px card -- so it needs more enlargement than the title bar to reach a legible
x-height."""


def crop_collector_bar(
    card: Image.Image, *, top_offset: float = 0.0, bottom_offset: float = 0.0
) -> Image.Image:
    """Crop the bottom-left collector block out of a rectified card image.

    Args:
        card: A rectified card image.
        top_offset: Shift of the band's top edge, as a fraction of card height.
        bottom_offset: Shift of the band's bottom edge.
    """
    width, height = card.size
    top = min(max(COLLECTOR_TOP + top_offset, 0.0), 0.99)
    bottom = min(max(COLLECTOR_BOTTOM + bottom_offset, top + 0.01), 1.0)
    box = (
        int(width * COLLECTOR_LEFT),
        int(height * top),
        int(width * COLLECTOR_RIGHT),
        int(height * bottom),
    )
    if box[2] <= box[0] or box[3] <= box[1]:
        raise InvalidImage("Card crop is too small to contain a collector line")
    return card.crop(box)


def prepare_collector(
    card: Image.Image, *, top_offset: float = 0.0, bottom_offset: float = 0.0
) -> PreparedCrops:
    """Produce OCR-ready variants of one collector-line band.

    Both polarities are produced for the same reason as the title bar, but the need is
    stronger here: the collector line is white-on-black on most modern frames and
    black-on-white on white-bordered and showcase treatments.
    """
    gray = ImageOps.grayscale(
        crop_collector_bar(card, top_offset=top_offset, bottom_offset=bottom_offset)
    )
    return PreparedCrops(
        normal=_binarise(gray, invert=False, upscale=COLLECTOR_UPSCALE),
        inverted=_binarise(gray, invert=True, upscale=COLLECTOR_UPSCALE),
    )


def collector_candidates(card: Image.Image) -> Iterator[tuple[str, Image.Image]]:
    """Every collector-line crop worth trying, best first.

    Ordered so the common case -- a well-framed card, dark band, light text -- is the
    first thing tried and costs a single OCR call.
    """
    for index, (top_offset, bottom_offset) in enumerate(COLLECTOR_BANDS):
        crops = prepare_collector(card, top_offset=top_offset, bottom_offset=bottom_offset)
        for variant, image in crops:
            yield f"band{index}-{variant}", image
