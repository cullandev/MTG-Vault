"""Card detection: the framing tolerance contract.

Every test here is a case the previous client-side detector failed, and each states
the promise in its name. The old detector thresholded the whole frame and kept the
largest bright blob, so a card had to be centred, square-on, on a plain dark
background, and large enough to dominate the histogram. None of that is required now,
and these tests are what keeps it that way.
"""

from __future__ import annotations

import numpy as np
import pytest

from app.vision import detect
from tests.support import scenes

CENTRE = (scenes.FRAME_WIDTH // 2, scenes.FRAME_HEIGHT // 2)


def _corner_error(found: detect.Detection, expected_centre: tuple[int, int]) -> float:
    """Distance from a detection's centre to where the card was actually placed."""
    centre = found.corners.mean(axis=0)
    return float(np.hypot(centre[0] - expected_centre[0], centre[1] - expected_centre[1]))


# --- the basic promise ------------------------------------------------------


def test_a_centred_card_is_found() -> None:
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=440)
    )
    found = detect.detect_cards(frame)

    assert len(found) == 1
    assert _corner_error(found[0], CENTRE) < 12
    assert found[0].aspect == pytest.approx(detect.CARD_ASPECT, abs=0.12)


@pytest.mark.parametrize("angle", [-40, -23, -8, 8, 23, 40])
def test_a_rotated_card_is_found(angle: float) -> None:
    """The card does not have to be square-on to the camera."""
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=420, angle=angle)
    )
    found = detect.detect_cards(frame)

    assert found, f"no detection at {angle} degrees"
    assert _corner_error(found[0], CENTRE) < 15


@pytest.mark.parametrize(
    "centre",
    [(260, 200), (1030, 200), (260, 520), (1030, 520)],
)
def test_an_off_centre_card_is_found(centre: tuple[int, int]) -> None:
    """The card does not have to be in the middle of the frame."""
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=centre, height=330)
    )
    found = detect.detect_cards(frame)

    assert found
    assert _corner_error(found[0], centre) < 15


@pytest.mark.parametrize("height", [140, 200, 300, 460, 640])
def test_a_card_is_found_across_a_wide_range_of_sizes(height: int) -> None:
    """The card does not have to fill the screen.

    At 140px tall the card covers about 1.9% of a 720p frame -- the old detector
    required 5%, which in practice meant holding it close enough to fill the view.
    """
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=height)
    )
    found = detect.detect_cards(frame)

    assert found, f"no detection at {height}px tall"
    assert _corner_error(found[0], CENTRE) < 15


@pytest.mark.parametrize("strength", [0.2, 0.35, 0.5])
def test_a_card_over_a_lighting_gradient_is_located_accurately(strength: float) -> None:
    """One lamp off to the side must not cut the card in half.

    This is what CLAHE is for: without lighting normalisation a single global
    threshold puts the bright half of the card and the dark half of the background on
    the same side of the cut.
    """
    frame = scenes.with_lighting_gradient(
        scenes.place_card(
            scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=420)
        ),
        strength=strength,
    )
    found = detect.detect_cards(frame)

    assert found
    assert _corner_error(found[0], CENTRE) < 20


def test_an_extreme_lighting_gradient_degrades_rather_than_fails() -> None:
    """At a 80% falloff the card is still found, but the quad fits loosely.

    Stated as a test rather than left as folklore: past this point the card's dark
    border and the darkened background stop being separable, so the outline drifts by
    tens of pixels. Identification still works -- the hash is taken from an inset crop
    and other frames land better -- but the promise here is *found*, not *precise*.
    """
    frame = scenes.with_lighting_gradient(
        scenes.place_card(
            scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=420)
        ),
        strength=0.8,
    )
    found = detect.detect_cards(frame)

    assert found
    assert _corner_error(found[0], CENTRE) < 60


def test_a_card_on_a_pale_background_is_found() -> None:
    """A card on a light table is not the brightest thing in the frame."""
    background = np.full((scenes.FRAME_HEIGHT, scenes.FRAME_WIDTH, 3), 235, np.uint8)
    frame = scenes.place_card(background, scenes.Placement(centre=CENTRE, height=420))
    found = detect.detect_cards(frame)

    assert found
    assert _corner_error(found[0], CENTRE) < 15


def test_a_tilted_camera_still_finds_the_card() -> None:
    """Perspective foreshortening changes the aspect ratio; the gate allows for it."""
    frame = scenes.with_perspective(
        scenes.place_card(
            scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=430)
        )
    )
    assert detect.detect_cards(frame)


# --- dark cards, which is what almost every real card is ---------------------
#
# These are the cases synthetic fixtures miss. A bright card on a dark mat is the easy
# problem; a black-bordered card on a dark surface is the normal one, and a detector
# tuned only against the former fails on nearly every real card with its tests green.


def _dark_scene(centre: tuple[int, int], height: int, angle: float = 0.0) -> np.ndarray:
    """A black-bordered card on a dark surface, which is the recommended setup."""
    background = np.full((scenes.FRAME_HEIGHT, scenes.FRAME_WIDTH, 3), 52, np.uint8)
    face = scenes.render_card_face(height, style=scenes.BLACK_BORDERED)
    return scenes.place_card(
        background, scenes.Placement(centre=centre, height=height, angle=angle), card=face
    )


def test_a_dark_card_on_a_dark_surface_is_found() -> None:
    """The case a global threshold cannot do at all: both sides of the cut are dark."""
    frame = _dark_scene(CENTRE, 440)
    found = detect.detect_cards(frame)

    assert found
    assert _corner_error(found[0], CENTRE) < 15


@pytest.mark.parametrize("angle", [-40, -22, 22, 40])
def test_a_rotated_dark_card_is_found(angle: float) -> None:
    frame = _dark_scene(CENTRE, 420, angle=angle)
    found = detect.detect_cards(frame)

    assert found, f"no detection at {angle} degrees"
    assert _corner_error(found[0], CENTRE) < 18


@pytest.mark.parametrize("height", [180, 260, 380, 560])
def test_a_dark_card_is_found_across_sizes(height: int) -> None:
    """Detection on real cards used to collapse below about 450px."""
    frame = _dark_scene(CENTRE, height)
    found = detect.detect_cards(frame)

    assert found, f"no detection at {height}px tall"
    assert _corner_error(found[0], CENTRE) < 15


def test_an_off_centre_dark_card_is_found() -> None:
    centre = (980, 250)
    found = detect.detect_cards(_dark_scene(centre, 300))

    assert found
    assert _corner_error(found[0], centre) < 15


def test_a_dark_card_on_a_light_surface_is_found() -> None:
    """The other polarity: a black border against a pale table."""
    background = np.full((scenes.FRAME_HEIGHT, scenes.FRAME_WIDTH, 3), 226, np.uint8)
    face = scenes.render_card_face(420, style=scenes.BLACK_BORDERED)
    frame = scenes.place_card(background, scenes.Placement(centre=CENTRE, height=420), card=face)
    found = detect.detect_cards(frame)

    assert found
    assert _corner_error(found[0], CENTRE) < 15


def test_a_dark_card_over_a_lighting_gradient_is_found() -> None:
    """Local thresholding has to carry this now that CLAHE is gone."""
    frame = scenes.with_lighting_gradient(_dark_scene(CENTRE, 440), strength=0.45)
    found = detect.detect_cards(frame)

    assert found
    assert _corner_error(found[0], CENTRE) < 25


# --- several cards ----------------------------------------------------------


def test_two_cards_side_by_side_are_both_found() -> None:
    """Keeping several hypotheses is what makes a laid-out row scannable at once."""
    frame = scenes.cluttered_background()
    left = (330, 360)
    right = (950, 360)
    frame = scenes.place_card(frame, scenes.Placement(centre=left, height=380))
    frame = scenes.place_card(frame, scenes.Placement(centre=right, height=380))

    found = detect.detect_cards(frame)

    assert len(found) >= 2
    centres = sorted(_corner_error(item, left) for item in found)
    assert centres[0] < 20


def test_the_art_box_is_not_reported_as_a_second_card() -> None:
    """A card's art box is a bright rectangle of nearly card-like proportions.

    Every real card produces this false candidate, and intersection-over-union does
    not catch it -- a small box inside a large one scores low. Containment does.
    """
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=520)
    )
    found = detect.detect_cards(frame)

    assert len(found) == 1


# --- cards that run off the edge of the frame --------------------------------


def test_a_card_overflowing_the_frame_is_flagged_clipped() -> None:
    """Held too close, the card's own edges are outside the picture.

    Such a region cannot be identified -- rectifying it stretches a partial card to
    full size, so the hash covers the wrong content and the OCR crops land in the
    wrong place -- and analysing it anyway produces a confident-looking list of wrong
    cards, which is exactly what a real scan did.
    """
    face = scenes.render_card_face(900)
    height, width = face.shape[:2]
    frame = np.full((scenes.FRAME_HEIGHT, scenes.FRAME_WIDTH, 3), 60, np.uint8)
    top = (height - scenes.FRAME_HEIGHT) // 2
    left = (scenes.FRAME_WIDTH - width) // 2
    frame[:, left : left + width] = face[top : top + scenes.FRAME_HEIGHT, :]

    found = detect.detect_cards(frame)
    assert found, "something card-shaped should still be seen"
    assert all(item.clipped for item in found)


def test_a_card_running_off_one_edge_is_flagged_clipped() -> None:
    background = np.full((scenes.FRAME_HEIGHT, scenes.FRAME_WIDTH, 3), 60, np.uint8)
    face = scenes.render_card_face(600)
    # Placed so its left half hangs outside the frame.
    height, width = face.shape[:2]
    background[60 : 60 + height, 0 : width // 2] = face[:, width // 2 :]
    found = detect.detect_cards(background)

    assert all(item.clipped for item in found)


def test_a_card_wholly_inside_the_frame_is_not_clipped() -> None:
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=440)
    )
    found = detect.detect_cards(frame)

    assert found
    assert not found[0].clipped


# --- things that are not cards ----------------------------------------------


def test_an_empty_scene_yields_nothing() -> None:
    assert detect.detect_cards(scenes.cluttered_background()) == []


def test_a_flat_frame_yields_nothing() -> None:
    """A capped lens or a blank wall must not produce a phantom card."""
    frame = np.full((scenes.FRAME_HEIGHT, scenes.FRAME_WIDTH, 3), 20, np.uint8)
    assert detect.detect_cards(frame) == []


def test_a_square_object_is_rejected() -> None:
    """The aspect gate, in isolation.

    On a plain background so the square cannot merge with anything else: two objects
    close enough to touch can genuinely produce a card-shaped outline around their
    union, and no geometric gate can tell that apart. Detection optimises for recall
    and lets identification reject what is not a card -- a false quad costs one hash
    lookup and matches nothing.
    """
    frame = np.full((scenes.FRAME_HEIGHT, scenes.FRAME_WIDTH, 3), 60, np.uint8)
    frame[200:560, 500:860] = 220
    assert detect.detect_cards(frame) == []


def test_a_card_smaller_than_the_floor_is_rejected() -> None:
    """Below the area floor there are not enough pixels left to identify anything."""
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=70)
    )
    assert detect.detect_cards(frame) == []


def test_detection_count_is_bounded() -> None:
    """A frame full of rectangles must not turn into unbounded downstream OCR."""
    frame = scenes.cluttered_background()
    for index, x in enumerate(range(150, 1150, 190)):
        frame = scenes.place_card(frame, scenes.Placement(centre=(x, 200 + index), height=250))

    found = detect.detect_cards(frame)
    assert len(found) <= detect.MAX_CANDIDATES


def test_a_non_bgr_frame_is_rejected() -> None:
    with pytest.raises(ValueError, match="3-channel"):
        detect.detect_cards(np.zeros((100, 100), np.uint8))


# --- alternative quad hypotheses --------------------------------------------


def test_losing_hypotheses_are_kept_as_alternates() -> None:
    """Detection's ranking is not reliable enough to be final.

    On a low-contrast edge -- a borderless card on a dark mat -- several hypotheses
    survive the gates and the one that wins can be slightly sheared, which leaves the
    geometry looking plausible and the perceptual hash worthless. Measured on one
    frame: the chosen quad matched at 290 bits, an alternative from the same contour
    matched the right card at 48. Identification can only try the alternative if
    detection kept it.
    """
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=460)
    )
    found = detect.detect_cards(frame)

    assert found
    assert found[0].alternates, "the losing hypotheses for this region were discarded"
    assert len(found[0].alternates) <= detect.MAX_ALTERNATES


def test_alternates_describe_the_same_region() -> None:
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=460)
    )
    found = detect.detect_cards(frame)
    assert found

    for corners in found[0].alternates:
        centre = corners.mean(axis=0)
        assert float(np.hypot(centre[0] - CENTRE[0], centre[1] - CENTRE[1])) < 60


def test_an_alternate_rectifies_like_any_other_quad() -> None:
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=460)
    )
    found = detect.detect_cards(frame)
    assert found and found[0].alternates

    card = detect.rectify_corners(frame, found[0].alternates[0])
    assert card.shape == (detect.OUTPUT_HEIGHT, detect.OUTPUT_WIDTH, 3)


# --- rectification ----------------------------------------------------------


def test_rectification_produces_an_upright_card() -> None:
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=440, angle=17)
    )
    found = detect.detect_cards(frame)
    assert found

    card = detect.rectify(frame, found[0])
    assert card.shape == (detect.OUTPUT_HEIGHT, detect.OUTPUT_WIDTH, 3)


def test_rectification_puts_the_title_bar_at_the_top() -> None:
    """The OCR crops address fixed fractions of the card, so this has to hold."""
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=520)
    )
    found = detect.detect_cards(frame)
    assert found

    card = detect.rectify(frame, found[0])
    title_band = card[int(detect.OUTPUT_HEIGHT * 0.05) : int(detect.OUTPUT_HEIGHT * 0.10)]
    art_band = card[int(detect.OUTPUT_HEIGHT * 0.25) : int(detect.OUTPUT_HEIGHT * 0.45)]

    # The rendered title bar is near-white and the art box is a mid green.
    assert title_band.mean() > art_band.mean() + 40


def test_a_card_lying_on_its_side_is_rectified_upright() -> None:
    """A landscape quad is a valid rectangle but an unreadable crop."""
    frame = scenes.place_card(
        scenes.cluttered_background(), scenes.Placement(centre=CENTRE, height=420, angle=90)
    )
    found = detect.detect_cards(frame)
    assert found

    card = detect.rectify(frame, found[0])
    assert card.shape[0] > card.shape[1]
