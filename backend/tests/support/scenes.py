"""Synthetic camera frames for detector tests.

The point of these is not realism -- a photograph of real cardboard under a real lamp
is the only honest accuracy measure, and that stays a manual step in TEST-PLAN.md.
The point is that the detector's *contract* becomes testable in CI: a card at an
angle, off to one side, small in the frame, on a cluttered background, over a lighting
gradient, all still get found.

That contract is exactly what the previous client-side detector could not be tested
against, because it only ran in a browser. Every regression in framing tolerance now
shows up here instead of on a phone.
"""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np

FRAME_WIDTH = 1280
FRAME_HEIGHT = 720
CARD_ASPECT = 88.0 / 63.0


@dataclass(frozen=True)
class Placement:
    """Where a card sits in a frame."""

    centre: tuple[int, int]
    height: int
    """Card height in pixels before rotation."""
    angle: float = 0.0
    """Degrees, anticlockwise."""


BRIGHT_FACE = "bright"
BLACK_BORDERED = "black-bordered"
"""What almost every real Magic card is: a black border around a dark face.

This is the case synthetic fixtures reliably miss. A bright card on a dark mat is the
easy problem, and a detector tuned only against it will fail on nearly every real card
while its tests stay green -- which is exactly what happened here."""


def render_card_face(height: int, *, pale: bool = False, style: str = BRIGHT_FACE) -> np.ndarray:
    """Draw a card-proportioned face: border, title bar, art box, text box.

    Args:
        height: Card height in pixels.
        pale: Use a white border rather than a black one.
        style: ``BRIGHT_FACE`` or ``BLACK_BORDERED``.
    """
    width = max(4, round(height / CARD_ASPECT))
    border = (28, 26, 24) if not pale else (238, 236, 232)
    face = np.full((height, width, 3), border, np.uint8)

    def box(x0: float, y0: float, x1: float, y1: float, colour: tuple[int, int, int]) -> None:
        cv2.rectangle(
            face,
            (int(width * x0), int(height * y0)),
            (int(width * x1), int(height * y1)),
            colour,
            -1,
        )

    if style == BRIGHT_FACE:
        box(0.04, 0.03, 0.96, 0.97, (206, 198, 180))
        box(0.07, 0.05, 0.93, 0.11, (238, 232, 216))
        box(0.07, 0.13, 0.93, 0.53, (118, 132, 96))
        box(0.07, 0.55, 0.93, 0.60, (232, 226, 210))
        box(0.07, 0.62, 0.93, 0.86, (224, 218, 202))
        return face

    # A black border around a dark face. The tones matter: the border is the darkest
    # thing on the card and the face sits just above it, which is how a real card is
    # printed. An earlier version of this fixture had the face *darker* than its own
    # border, which is backwards, and made the whole card read as one flat mass with
    # no outline to trace -- so it failed the detector for a reason no real card has.
    face[:] = (14, 12, 12)
    box(0.045, 0.035, 0.955, 0.965, (30, 27, 25))
    box(0.08, 0.055, 0.92, 0.115, (58, 54, 50))
    box(0.08, 0.135, 0.92, 0.525, (52, 60, 48))
    box(0.08, 0.55, 0.92, 0.60, (58, 54, 50))
    box(0.08, 0.625, 0.92, 0.855, (46, 42, 38))
    return face


def cluttered_background(seed: int = 3) -> np.ndarray:
    """A busy background: noise, plus a few rectangles that are not cards.

    A plain dark mat is the easy case and the one the old detector needed. These
    distractors -- a deck box, a phone, a notepad -- are the shapes a naive "largest
    bright rectangle" rule picks instead of the card.
    """
    rng = np.random.default_rng(seed)
    frame = rng.integers(45, 105, (FRAME_HEIGHT, FRAME_WIDTH, 3), dtype=np.uint8)
    # Aspect ratios chosen to sit clearly outside the detector's gate. A distractor
    # that is *nearly* card-shaped is a fixture bug, not a harder test: it makes every
    # other assertion in the file ambiguous about which shape was found.
    cv2.rectangle(frame, (40, 380), (340, 680), (150, 140, 130), -1)  # square, 1.00
    cv2.rectangle(frame, (980, 40), (1240, 130), (90, 110, 150), -1)  # wide, 2.89
    # Bottom right, clear of where a centred card lands. It used to sit directly under
    # one, and a detector sensitive enough to find a dark card correctly links the two
    # into a single outline -- which is a true property of touching objects, not a bug,
    # but it made every centred-card assertion measure the wrong thing.
    cv2.circle(frame, (1180, 650), 60, (170, 160, 150), -1)
    return frame


def place_card(
    frame: np.ndarray, placement: Placement, *, card: np.ndarray | None = None
) -> np.ndarray:
    """Composite a card into a frame at a position, size and angle."""
    face = card if card is not None else render_card_face(placement.height)
    card_height, card_width = face.shape[:2]

    canvas = np.zeros_like(frame)
    mask = np.zeros(frame.shape[:2], np.uint8)
    x = int(placement.centre[0] - card_width / 2)
    y = int(placement.centre[1] - card_height / 2)
    if x < 0 or y < 0 or y + card_height > frame.shape[0] or x + card_width > frame.shape[1]:
        raise ValueError("Card does not fit in the frame at that placement")

    canvas[y : y + card_height, x : x + card_width] = face
    mask[y : y + card_height, x : x + card_width] = 255

    if placement.angle:
        rotation = cv2.getRotationMatrix2D(
            (float(placement.centre[0]), float(placement.centre[1])), placement.angle, 1.0
        )
        size = (frame.shape[1], frame.shape[0])
        canvas = cv2.warpAffine(canvas, rotation, size, flags=cv2.INTER_LINEAR)
        mask = cv2.warpAffine(mask, rotation, size, flags=cv2.INTER_NEAREST)

    out = frame.copy()
    out[mask > 0] = canvas[mask > 0]
    return out


def with_lighting_gradient(frame: np.ndarray, *, strength: float = 0.65) -> np.ndarray:
    """Darken one side of the frame, as a single off-centre lamp does.

    This is the case a global threshold cannot survive: the bright side of the card
    and the dark side of the background end up on the same side of any single cut.
    """
    width = frame.shape[1]
    ramp = np.linspace(1.0, 1.0 - strength, width, dtype=np.float32)
    scaled = frame.astype(np.float32) * ramp[None, :, None]
    return np.clip(scaled, 0, 255).astype(np.uint8)


def with_perspective(frame: np.ndarray, *, tilt: float = 0.18) -> np.ndarray:
    """Warp the whole frame as if the camera were held at an angle to the table."""
    height, width = frame.shape[:2]
    offset = width * tilt
    source = np.float32([[0, 0], [width, 0], [width, height], [0, height]])
    destination = np.float32(
        [[offset, 0], [width - offset * 0.35, 0], [width, height], [0, height]]
    )
    transform = cv2.getPerspectiveTransform(source, destination)
    return cv2.warpPerspective(frame, transform, (width, height))


def as_jpeg(frame: np.ndarray, quality: int = 75) -> bytes:
    """Encode a frame the way the scanner does."""
    ok, buffer = cv2.imencode(".jpg", frame, [int(cv2.IMWRITE_JPEG_QUALITY), quality])
    if not ok:
        raise RuntimeError("Could not encode the frame")
    return bytes(buffer)
