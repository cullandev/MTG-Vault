"""Perceptual hashing of card images.

A perceptual hash is what makes identification survive the things OCR cannot: foil
glare across the text, a language the recogniser has no training data for, a worn or
scuffed title bar, and the fact that stylised card fonts are hard to read at all. It
compares what the card *looks like* against a reference image of every printing.

Three decisions worth stating, each taken from what the reference implementations
found (ADR-024):

**16x16 DCT hash (256 bits).** Measured as sufficient to separate cards where 8x8 is
not, and where 32x32 buys nothing.

**Per channel, not greyscale.** Magic reprints the same artwork in different frame
treatments and different border colours; collapsing to luminance throws away exactly
the signal that separates them. Three hashes cost three DCTs of a 32x32 image, which
is nothing.

**An inset crop, not the whole card.** The single most-reported failure mode of
hash-based card matching is sensitivity to the segmentation border -- include a sliver
of the table, or clip a millimetre of the card, and every bit shifts. Hashing an inset
region throws away the few percent nearest the edge, where rectification error lives,
and keeps the artwork and title, where the identity lives.
"""

from __future__ import annotations

import logging

import cv2
import numpy as np

log = logging.getLogger("mtgvault.vision.hashing")

HASH_SIDE = 16
"""Low-frequency DCT coefficients kept per side, so HASH_SIDE**2 bits per channel."""

DCT_SIDE = 32
"""Image is reduced to this before the DCT. Twice HASH_SIDE, which is the usual ratio."""

CHANNELS = 3
BITS_PER_CHANNEL = HASH_SIDE * HASH_SIDE
HASH_BYTES = CHANNELS * BITS_PER_CHANNEL // 8
"""96 bytes per card: 256 bits x 3 channels."""

INSET = 0.06
"""Fraction trimmed from each edge before hashing. Absorbs the rectification error
that a hash of the full card would amplify into every bit."""

TOP_FRACTION = 0.62
"""How far down the card to keep. Stopping above the text box removes the region that
differs most between printings of the same card -- reminder text, flavour text,
set symbol -- and keeps the name and the artwork, which is what identifies it."""


def _inset_crop(card: np.ndarray) -> np.ndarray:
    """Take the name-and-artwork region out of a rectified card."""
    height, width = card.shape[:2]
    left = int(width * INSET)
    right = int(width * (1.0 - INSET))
    top = int(height * INSET)
    bottom = int(height * TOP_FRACTION)
    if right - left < 8 or bottom - top < 8:
        raise ValueError("Card image is too small to hash")
    return card[top:bottom, left:right]


def _channel_hash(
    channel: np.ndarray, *, side: int = HASH_SIDE, dct_side: int = DCT_SIDE
) -> np.ndarray:
    """Compute one channel's DCT hash as a bool array.

    Args:
        channel: One colour plane.
        side: Low-frequency coefficients kept per axis; the hash is ``side**2`` bits.
        dct_side: Size the plane is reduced to before the transform.
    """
    reduced = cv2.resize(channel, (dct_side, dct_side), interpolation=cv2.INTER_AREA)
    coefficients: np.ndarray = cv2.dct(reduced.astype(np.float32))
    low = coefficients[:side, :side].flatten()
    # The DC term is overall brightness. Keeping it would make the hash track exposure,
    # which is the one thing that certainly differs between a scan and a reference.
    median = np.median(low[1:])
    return np.asarray(low > median)


SYMBOL_BAND = (0.78, 0.540, 0.96, 0.620)
"""The right end of the type line, in fractions of the rectified card.

Where the set symbol is printed, and the one mark on a pre-2015 card that names its
edition. Measured against real images from 6th Edition (1997) through Foundations
(2024): the position is stable across every standard, planeswalker and borderless
frame in that span. Slightly wider than the symbol so a few pixels of rectification
error cannot slide it off.

It misses full-art lands, sagas, adventures and split cards, whose type lines are
elsewhere. That costs nothing: the band then holds artwork, which is identical across
the printings being told apart, so the comparison simply fails to discriminate and the
user is asked. A crop that lands wrong produces no answer rather than a wrong one --
which is the whole reason this is a hash comparison and not a symbol classifier."""

SYMBOL_HASH_SIDE = 8
"""16x16 over a band this small would be hashing the JPEG's noise. 8x8 per channel
gives 192 bits, which separates a trident from a set of crossed swords comfortably."""

SYMBOL_DCT_SIDE = 16
SYMBOL_HASH_BYTES = CHANNELS * (SYMBOL_HASH_SIDE * SYMBOL_HASH_SIDE) // 8


def symbol_hash(card: np.ndarray) -> bytes:
    """Hash the type-line band, where the set symbol sits.

    Args:
        card: A rectified card, BGR.

    Returns:
        :data:`SYMBOL_HASH_BYTES` of packed bits.

    Raises:
        ValueError: The image is too small, or not colour.
    """
    if card.ndim != 3 or card.shape[2] != 3:
        raise ValueError("symbol_hash expects a 3-channel BGR image")

    height, width = card.shape[:2]
    left = int(width * SYMBOL_BAND[0])
    top = int(height * SYMBOL_BAND[1])
    right = max(left + 1, int(width * SYMBOL_BAND[2]))
    bottom = max(top + 1, int(height * SYMBOL_BAND[3]))
    region = card[top:bottom, left:right]
    if region.shape[0] < 4 or region.shape[1] < 4:
        raise ValueError("card is too small to hash a symbol band from")

    bits = np.concatenate(
        [
            _channel_hash(region[:, :, index], side=SYMBOL_HASH_SIDE, dct_side=SYMBOL_DCT_SIDE)
            for index in range(CHANNELS)
        ]
    )
    return np.packbits(bits).tobytes()


def card_hash(card: np.ndarray) -> bytes:
    """Hash a rectified card image.

    Args:
        card: A rectified card, BGR, at any size (the detector emits 488x680).

    Returns:
        HASH_BYTES of packed bits: blue, then green, then red.

    Raises:
        ValueError: The image is too small to hash.
    """
    if card.ndim != 3 or card.shape[2] != 3:
        raise ValueError("card_hash expects a 3-channel BGR image")

    region = _inset_crop(card)
    bits = np.concatenate([_channel_hash(region[:, :, index]) for index in range(CHANNELS)])
    return np.packbits(bits).tobytes()


def card_hash_both_orientations(card: np.ndarray) -> tuple[bytes, bytes]:
    """Hash a card the right way up and upside down.

    A rectified quad is upright as a rectangle but a card rotated 180 degrees is an
    equally valid rectangle, and nothing in the geometry says which way round it is.
    Hashing both and keeping whichever matches better costs one extra DCT and removes
    a whole class of "it just sits there" failures.
    """
    return card_hash(card), card_hash(cv2.rotate(card, cv2.ROTATE_180))


# A byte's popcount, precomputed, for numpy builds without a native one.
_POPCOUNT = np.array([bin(value).count("1") for value in range(256)], dtype=np.uint8)

_HAS_BITWISE_COUNT = hasattr(np, "bitwise_count")
"""numpy 2.0 added a native popcount. Counting over 64-bit words rather than bytes gives
the same answer over an eighth as many elements, which is most of the cost of a search
across the whole index."""


def hamming_distances(query: bytes, table: np.ndarray) -> np.ndarray:
    """Distance from ``query`` to every row of a packed hash table.

    Args:
        query: Packed bits. Any width, so long as it matches the table's rows: this
            serves both the artwork hash and the narrower type-line band.
        table: ``(n, width)`` uint8 array of packed hashes, or the ``uint64`` view of
            one that the index builds.

    Returns:
        ``(n,)`` int32 array of bit distances.
    """
    row_bytes = int(table.shape[1]) * table.dtype.itemsize if table.ndim == 2 else 0
    if row_bytes and len(query) != row_bytes:
        raise ValueError(f"Query hash must be {row_bytes} bytes, got {len(query)}")

    if _HAS_BITWISE_COUNT and table.dtype == np.uint64:
        wide = np.frombuffer(query, dtype=np.uint64)
        counted = np.bitwise_count(np.bitwise_xor(table, wide))
        return np.asarray(counted.sum(axis=1, dtype=np.int32))

    needle = np.frombuffer(query, dtype=np.uint8)
    distances = _POPCOUNT[np.bitwise_xor(table, needle)].sum(axis=1, dtype=np.int32)
    return np.asarray(distances)


SHARPNESS_SIDE = 256
"""Width the sharpness check works at. Cheap, and enough to tell text from a smear."""


def sharpness(card: np.ndarray) -> float:
    """Variance of the Laplacian over a rectified card.

    A card carries a great deal of fine, high-contrast detail -- rules text, a type
    line, a border. A motion-blurred smear carries none, and neither does a patch of
    carpet that happened to be card-shaped. Measuring this costs about a millisecond
    and saves the several hundred that OCR would spend failing to read it.
    """
    region = _inset_crop(card)
    scale = SHARPNESS_SIDE / max(region.shape[1], 1)
    if scale < 1.0:
        region = cv2.resize(
            region,
            (SHARPNESS_SIDE, max(1, int(region.shape[0] * scale))),
            interpolation=cv2.INTER_AREA,
        )
    gray = cv2.cvtColor(region, cv2.COLOR_BGR2GRAY)
    return float(cv2.Laplacian(gray, cv2.CV_64F).var())
