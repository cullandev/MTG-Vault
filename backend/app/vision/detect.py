"""Find cards in a camera frame.

This is the piece that decides whether a card has to be centred, square-on and
filling the screen. The old client-side detector thresholded the whole frame and kept
the largest bright blob, which requires exactly that: a card noticeably brighter than
a plain background, big enough to dominate the histogram.

The pipeline (see ADR-024 for the survey of how other card recognisers do this):

    adaptive threshold + Canny -- two local views of the frame, unioned
    -> findContours            -- trace outlines, not regions
    -> quad hypotheses         -- several per contour, at several simplification levels
    -> geometry gates          -- deliberately loose
    -> non-maximum suppression
    -> perspective transform   -- any angle, any position

Finding the *outline* rather than a bright region is what makes a busy card face on a
light table work. Keeping several hypotheses per frame is what makes a partially
occluded or off-centre card work, and it is nearly free: the expensive part is the
rectification, and that only runs on candidates that survive the gates.

**Everything here is local, deliberately.** An earlier version equalised the frame with
CLAHE and thresholded it globally, which is what most published examples do. Measured
against real card images rather than synthetic ones it found the card in 17 of 48
presentations; the version below finds it in 47. Both halves of that gap were caused
by global operations, and both are worth remembering:

* **A global threshold cannot separate a dark card from a dark background.** Magic
  cards are mostly black-bordered, and the recommended scanning surface is a dark mat.
  Otsu puts the card and the mat on the same side of a single cut, so the card simply
  is not there to be traced. An adaptive threshold asks a local question instead --
  is this pixel darker than its neighbourhood -- which a card border answers even when
  the global histogram cannot.
* **CLAHE must not feed an edge detector.** It amplifies local contrast, so in flat
  regions -- a table, a mat, an empty background -- it amplifies sensor noise, and
  Canny then fires across the whole frame. Dilation merges that into one blob and the
  card's outline is lost inside it. Canny sees the plain greyscale.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

import cv2
import numpy as np

log = logging.getLogger("mtgvault.vision.detect")

ADAPTIVE_BLOCK = 61
"""Neighbourhood the adaptive threshold compares each pixel against. Must be odd.
Large, because a card's border is thin and what it has to stand out from is the whole
surface behind it."""

ADAPTIVE_C = 7
"""Constant subtracted from the local mean. Higher rejects more faint texture."""

WORKING_EDGE = 1000
"""Longest edge the detector works at. Contour tracing cost is quadratic in edge
length and a card only needs a few hundred pixels to be found."""

CARD_ASPECT = 88.0 / 63.0
"""A Magic card is 63 x 88 mm, so 1.397 tall by wide."""

ASPECT_TOLERANCE = 0.30
"""How far from 1.397 a quad's aspect may sit. Generous on purpose: perspective
foreshortens a tilted card, and rejecting those is precisely the behaviour being
fixed. False positives are cheap -- they fail identification a few milliseconds
later -- while a false negative is a card that will not scan."""

MIN_AREA_FRACTION = 0.012
"""Smallest quad worth rectifying, as a fraction of the frame. At 1.2% of a 1280x720
frame a card is about 100x140 px, which is enough for a 16x16 perceptual hash. The
old value was 5%, which is what "fill the screen" meant in practice."""

MAX_AREA_FRACTION = 0.98
TIGHT_FILL_TOLERANCE = 0.03
"""How far from a perfect fill a quad may sit and still count as a *tight* fit.

Ranking is bucketed rather than continuous: every tight quad is considered before any
loose one, and within a bucket the largest wins. Ranking on fill alone would prefer a
small inner element that happens to fit perfectly over the card containing it; ranking
on area alone loses to a merged blob, which is bigger than the card precisely because
it swallowed some background too."""

MIN_FILL = 0.85
"""How much of a proposed quad the traced contour actually fills.

A card fills its own bounding quad almost completely -- the rounded corners cost about
0.15% -- so anything much below this is not one shape but several that the threshold
merged, with a card-shaped box drawn around the union. That merged-blob case is the
main source of confident nonsense, and comparing the quad to the contour's *convex
hull* does not catch it, because the hull of a merged blob is large too."""

MAX_CANDIDATES = 6

MAX_ALTERNATES = 3
"""How many losing hypotheses to keep per detection for identification to fall back on.
Each retry costs a hash and an index search, about 13 ms, and only happens when the
first attempt was not already conclusive."""
"""How many quads to rectify per frame. Every serious implementation keeps several:
it is what makes an off-centre card, a partially occluded card and several cards laid
out at once all work."""

OVERLAP_THRESHOLD = 0.55
"""Intersection-over-union above which two quads are treated as the same card."""

CONTAINMENT_THRESHOLD = 0.80
"""How much of a candidate must lie inside an already-accepted one before it is
treated as part of that card rather than a card of its own.

This is not a refinement, it is essential: a card's art box is itself a bright
rectangle of almost exactly card-like proportions, so every real card produces a
convincing false candidate nested inside it. Intersection-over-union does not catch
that -- a small box inside a large one has a low IoU -- so containment is checked
separately."""

EDGE_MARGIN = 3
"""How close to the frame border a corner may sit before the quad counts as clipped,
in working-resolution pixels."""

OUTPUT_WIDTH = 488
OUTPUT_HEIGHT = 680
"""Scryfall's ``normal`` image size, so a rectified card and a reference image are
directly comparable without a further resize."""


@dataclass(frozen=True)
class Detection:
    """One card-shaped region found in a frame."""

    corners: np.ndarray
    """4x2 float32, clockwise from the top-left, in *original frame* coordinates."""
    area_fraction: float
    aspect: float
    fill: float
    alternates: tuple[np.ndarray, ...] = ()
    """Other quads that described the same region and lost the ranking.

    They are kept rather than discarded because the ranking is not reliable enough to
    be final. On a low-contrast edge -- a borderless card on a dark mat -- several
    hypotheses survive the gates and the one that wins can be slightly sheared, which
    leaves the geometry looking plausible and the perceptual hash worthless. Measured
    on one frame: the chosen quad matched at a distance of 290 bits, while an
    alternative from the same contour matched the right card at 48."""
    clipped: bool = False
    """The quad touches the frame border, so the card is not wholly in view.

    Such a region cannot be identified: rectifying it stretches a partial card to full
    size, so the hash is computed over the wrong content and the OCR crops land in the
    wrong place. It is reported rather than dropped, because "you are too close" is a
    far more useful thing to tell someone than "no card found" -- which is what a
    silent drop would say."""

    def as_dict(self) -> dict[str, object]:
        """Serialise for the scan overlay, which draws the outline."""
        return {
            "corners": [[round(float(x), 1), round(float(y), 1)] for x, y in self.corners],
            "area_fraction": round(self.area_fraction, 4),
            "aspect": round(self.aspect, 3),
            "clipped": self.clipped,
        }


def _binary_views(gray: np.ndarray) -> list[np.ndarray]:
    """Produce complementary binary images to trace contours in.

    Two views, because they fail in different places. The adaptive threshold finds a
    card whose border is darker than the surface it sits on, whatever the overall
    exposure; Canny finds the card's edge from the gradient alone, which still works
    when the border and the surface are similarly toned. Tracing both and pooling the
    candidates costs one extra contour pass -- measured at about 3 ms.

    Adding further views (inverted variants, a global Otsu) was measured and bought
    nothing: 47 of 48 real-card presentations either way, at up to double the cost.
    """
    blurred = cv2.GaussianBlur(gray, (5, 5), 0)

    # A large block: the question being asked is "is this darker than the surrounding
    # *region*", and a card border is millimetres wide against a whole table.
    adaptive = cv2.adaptiveThreshold(
        blurred, 255, cv2.ADAPTIVE_THRESH_GAUSSIAN_C, cv2.THRESH_BINARY, ADAPTIVE_BLOCK, ADAPTIVE_C
    )
    kernel = cv2.getStructuringElement(cv2.MORPH_RECT, (5, 5))
    adaptive = cv2.morphologyEx(adaptive, cv2.MORPH_CLOSE, kernel)

    edges = cv2.Canny(blurred, 40, 120)
    # Close the gaps a soft or low-contrast border leaves in the edge map, so the
    # outline is a single traceable loop rather than four disconnected sides.
    edges = cv2.dilate(edges, kernel, iterations=1)
    edges = cv2.morphologyEx(edges, cv2.MORPH_CLOSE, kernel)

    return [adaptive, edges]


def _order_corners(points: np.ndarray) -> np.ndarray:
    """Order four points clockwise, starting from the one nearest the frame origin.

    Sorted by angle about the centroid, which is the only ordering that survives
    rotation. The obvious alternative -- top-left minimises x+y, bottom-right maximises
    it, the other diagonal falls out of x-y -- is correct only while the quad is
    roughly axis-aligned. Past about thirty degrees it starts swapping adjacent corners,
    and since the aspect gate measures width and height *between* ordered corners, a
    swap measures the card across its diagonals. The card's true 1.4 then never appears
    at all and the quad is rejected for the wrong reason: a rotated card is not
    detected, which is precisely the failure this module exists to prevent.
    """
    points = points.reshape(4, 2).astype(np.float32)
    centre = points.mean(axis=0)
    # Image coordinates put y downwards, so ascending angle runs clockwise on screen.
    angles = np.arctan2(points[:, 1] - centre[1], points[:, 0] - centre[0])
    ordered = points[np.argsort(angles)]
    start = int(np.argmin(ordered.sum(axis=1)))
    return np.asarray(np.roll(ordered, -start, axis=0), dtype=np.float32)


def _quad_hypotheses(contour: np.ndarray) -> list[np.ndarray]:
    """Propose four-cornered shapes for one contour.

    Three proposals, because no single simplification is reliable. ``approxPolyDP``
    at a tight epsilon keeps a card's rounded corners as extra vertices; at a loose
    one it can cut a corner off entirely. The rotated bounding box is immune to both
    but overshoots when the contour includes a shadow. Proposing all of them and
    letting the gates and the identification stage decide is far more robust than
    picking one and hoping.
    """
    proposals: list[np.ndarray] = []
    hull = cv2.convexHull(contour)
    perimeter = cv2.arcLength(hull, True)
    if perimeter <= 0:
        return proposals

    for fraction in (0.02, 0.04, 0.08):
        approximation = cv2.approxPolyDP(hull, fraction * perimeter, True)
        if len(approximation) == 4:
            proposals.append(_order_corners(approximation))

    box = cv2.boxPoints(cv2.minAreaRect(hull))
    proposals.append(_order_corners(box))
    return proposals


def _measure(corners: np.ndarray, contour: np.ndarray) -> tuple[float, float, float]:
    """Return ``(area, aspect, fill)`` for a candidate quad."""
    area = abs(float(cv2.contourArea(corners)))
    if area <= 0:
        return 0.0, 0.0, 0.0

    top_left, top_right, bottom_right, bottom_left = corners
    width = (
        float(np.linalg.norm(top_right - top_left))
        + float(np.linalg.norm(bottom_right - bottom_left))
    ) / 2
    height = (
        float(np.linalg.norm(bottom_left - top_left))
        + float(np.linalg.norm(bottom_right - top_right))
    ) / 2
    if width <= 1 or height <= 1:
        return area, 0.0, 0.0

    # A card lying on its side is still a card; the aspect gate should not care which
    # way round it is, and rectification puts it upright afterwards.
    aspect = max(width, height) / min(width, height)

    contour_area = abs(float(cv2.contourArea(contour)))
    fill = contour_area / area if area > 0 else 0.0
    return area, aspect, fill


def _touches_edge(corners: np.ndarray, width: int, height: int) -> bool:
    """Whether a quad runs into the frame border."""
    return bool(
        corners[:, 0].min() <= EDGE_MARGIN
        or corners[:, 1].min() <= EDGE_MARGIN
        or corners[:, 0].max() >= width - 1 - EDGE_MARGIN
        or corners[:, 1].max() >= height - 1 - EDGE_MARGIN
    )


def _bounds(quad: np.ndarray) -> tuple[float, float, float, float]:
    """Axis-aligned bounds of a quad."""
    return (
        float(quad[:, 0].min()),
        float(quad[:, 1].min()),
        float(quad[:, 0].max()),
        float(quad[:, 1].max()),
    )


def _intersection_area(first: np.ndarray, second: np.ndarray) -> float:
    """Overlap of two quads, via their axis-aligned bounds.

    Exact polygon intersection is not worth it here: this only has to recognise that
    two hypotheses describe the same card, and near-duplicate quads have near-identical
    bounding boxes.
    """
    ax0, ay0, ax1, ay1 = _bounds(first)
    bx0, by0, bx1, by1 = _bounds(second)
    width = max(0.0, min(ax1, bx1) - max(ax0, bx0))
    height = max(0.0, min(ay1, by1) - max(ay0, by0))
    return width * height


def _containment(inner: np.ndarray, outer: np.ndarray) -> float:
    """Fraction of ``inner`` that lies inside ``outer``."""
    x0, y0, x1, y1 = _bounds(inner)
    area = (x1 - x0) * (y1 - y0)
    if area <= 0:
        return 0.0
    return _intersection_area(inner, outer) / area


def _iou(first: np.ndarray, second: np.ndarray) -> float:
    """Intersection over union of two quads."""
    intersection = _intersection_area(first, second)
    if intersection <= 0:
        return 0.0
    ax0, ay0, ax1, ay1 = _bounds(first)
    bx0, by0, bx1, by1 = _bounds(second)
    union = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - intersection
    return intersection / union if union > 0 else 0.0


def detect_cards(frame: np.ndarray, *, limit: int = MAX_CANDIDATES) -> list[Detection]:
    """Find every card-shaped quadrilateral in a BGR frame.

    Args:
        frame: The camera frame, BGR, any size.
        limit: Maximum detections to return, largest first.

    Returns:
        Detections in original-frame coordinates, largest first. Empty when nothing
        card-shaped is in view.
    """
    if frame.ndim != 3 or frame.shape[2] != 3:
        raise ValueError("detect_cards expects a 3-channel BGR frame")

    height, width = frame.shape[:2]
    scale = min(1.0, WORKING_EDGE / max(height, width))
    working = (
        cv2.resize(frame, (int(width * scale), int(height * scale)), interpolation=cv2.INTER_AREA)
        if scale < 1.0
        else frame
    )
    frame_area = float(working.shape[0] * working.shape[1])

    gray = cv2.cvtColor(working, cv2.COLOR_BGR2GRAY)

    candidates: list[Detection] = []
    for binary in _binary_views(gray):
        contours, _hierarchy = cv2.findContours(binary, cv2.RETR_LIST, cv2.CHAIN_APPROX_SIMPLE)
        for contour in contours:
            if cv2.contourArea(contour) < frame_area * MIN_AREA_FRACTION:
                continue
            for corners in _quad_hypotheses(contour):
                area, aspect, fill = _measure(corners, contour)
                fraction = area / frame_area
                if not MIN_AREA_FRACTION <= fraction <= MAX_AREA_FRACTION:
                    continue
                if abs(aspect - CARD_ASPECT) > ASPECT_TOLERANCE:
                    continue
                if fill < MIN_FILL:
                    continue
                candidates.append(
                    Detection(
                        corners=corners / scale,
                        area_fraction=fraction,
                        aspect=aspect,
                        fill=fill,
                        clipped=_touches_edge(corners, working.shape[1], working.shape[0]),
                    )
                )

    return _suppress_overlaps(candidates, limit)


def _suppress_overlaps(candidates: list[Detection], limit: int) -> list[Detection]:
    """Keep the best hypothesis per physical card.

    Every contour produces up to four proposals and the two binary views often find
    the same card, so the raw list is mostly duplicates.
    """
    ranked = sorted(
        candidates,
        key=lambda item: (
            0 if abs(1.0 - item.fill) <= TIGHT_FILL_TOLERANCE else 1,
            -item.area_fraction,
        ),
    )
    kept: list[Detection] = []
    alternates: list[list[np.ndarray]] = []
    for candidate in ranked:
        overlapping = next(
            (
                position
                for position, other in enumerate(kept)
                if _iou(candidate.corners, other.corners) > OVERLAP_THRESHOLD
                # Containment is checked both ways round. One direction rejects a
                # card's own art box; the other rejects a blob that swallowed an
                # accepted card, which is what a low-contrast border produces.
                or _containment(candidate.corners, other.corners) > CONTAINMENT_THRESHOLD
                or _containment(other.corners, candidate.corners) > CONTAINMENT_THRESHOLD
            ),
            None,
        )
        if overlapping is not None:
            # Same region, worse rank -- keep it as a fallback rather than dropping it.
            if len(alternates[overlapping]) < MAX_ALTERNATES:
                alternates[overlapping].append(candidate.corners)
            continue
        kept.append(candidate)
        alternates.append([])
        if len(kept) >= limit:
            break

    resolved = [
        Detection(
            corners=detection.corners,
            area_fraction=detection.area_fraction,
            aspect=detection.aspect,
            fill=detection.fill,
            alternates=tuple(extra),
            clipped=detection.clipped,
        )
        for detection, extra in zip(kept, alternates, strict=True)
    ]
    return sorted(resolved, key=lambda item: -item.area_fraction)


def rectify_corners(frame: np.ndarray, corners: np.ndarray) -> np.ndarray:
    """Flatten an arbitrary quad out of a frame. See :func:`rectify`."""
    return rectify(frame, Detection(corners=corners, area_fraction=0.0, aspect=0.0, fill=0.0))


def rectify(frame: np.ndarray, detection: Detection) -> np.ndarray:
    """Flatten a detected card into an upright OUTPUT_WIDTH x OUTPUT_HEIGHT image.

    Args:
        frame: The original full-resolution frame the detection came from.
        detection: Corners in that frame's coordinates.

    Returns:
        The rectified card, BGR. A card detected on its side is rotated upright, so
        the title bar is always at the top -- which is what the OCR crops assume.
    """
    corners = detection.corners.astype(np.float32)
    top_left, top_right, bottom_right, bottom_left = corners
    width = float(np.linalg.norm(top_right - top_left))
    height = float(np.linalg.norm(bottom_left - top_left))
    if width > height:
        # Landscape: rotate the correspondence a quarter turn rather than the pixels.
        corners = np.array([bottom_left, top_left, top_right, bottom_right], dtype=np.float32)

    destination = np.array(
        [[0, 0], [OUTPUT_WIDTH, 0], [OUTPUT_WIDTH, OUTPUT_HEIGHT], [0, OUTPUT_HEIGHT]],
        dtype=np.float32,
    )
    transform = cv2.getPerspectiveTransform(corners, destination)
    return cv2.warpPerspective(
        frame, transform, (OUTPUT_WIDTH, OUTPUT_HEIGHT), flags=cv2.INTER_LINEAR
    )
