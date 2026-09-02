"""Title-bar preprocessing and OCR.

The preprocessing tests are pure geometry and run anywhere. The recognition tests
need the ``tesseract`` binary, which ships in the app container -- run the suite there
(``docker compose -f docker-compose.test.yml run --rm tests``) or install it locally.
They are not marked skippable on purpose: an environment without an OCR engine cannot
honestly claim the scanner works.
"""

from __future__ import annotations

import pytest
from PIL import Image

from app.config import get_settings
from app.ocr import engine as ocr_engine
from app.ocr import preprocess
from tests.support import cards

# --- image loading ---------------------------------------------------------


def test_load_image_decodes_a_jpeg() -> None:
    image = preprocess.load_image(cards.as_jpeg(cards.render_card("Lightning Bolt")))
    assert image.size == cards.CARD_SIZE
    assert image.mode == "RGB"


@pytest.mark.parametrize("payload", [b"", b"not an image at all", b"\xff\xd8\xff"])
def test_undecodable_payloads_are_rejected(payload: bytes) -> None:
    with pytest.raises(preprocess.InvalidImage):
        preprocess.load_image(payload)


def test_oversized_payloads_are_rejected() -> None:
    with pytest.raises(preprocess.InvalidImage, match="too large"):
        preprocess.load_image(b"x" * (preprocess.MAX_IMAGE_BYTES + 1))


# --- cropping --------------------------------------------------------------


def test_title_bar_crop_takes_the_name_band() -> None:
    card = cards.render_card("Lightning Bolt")
    crop = preprocess.crop_title_bar(card)

    width, height = cards.CARD_SIZE
    assert crop.height == pytest.approx(
        height * (preprocess.TITLE_BOTTOM - preprocess.TITLE_TOP), abs=2
    )
    assert crop.width == pytest.approx(
        width * (preprocess.TITLE_RIGHT - preprocess.TITLE_LEFT), abs=2
    )


def test_the_crop_stops_before_the_mana_cost() -> None:
    """Mana symbols are round blobs OCR reads as letters; they must not be in frame."""
    assert preprocess.TITLE_RIGHT < 0.82


def test_a_tiny_image_is_rejected_rather_than_cropped_to_nothing() -> None:
    with pytest.raises(preprocess.InvalidImage):
        preprocess.crop_title_bar(Image.new("RGB", (4, 4)))


def test_prepare_produces_both_polarities() -> None:
    crops = preprocess.prepare(cards.render_card("Lightning Bolt"))
    variants = [name for name, _image in crops]
    assert variants == ["normal", "inverted"]
    assert crops.normal.mode == "L"
    # Inversion has to actually change something, or the foil path is a no-op.
    assert crops.normal.tobytes() != crops.inverted.tobytes()


def test_prepare_upscales_for_the_recogniser() -> None:
    card = cards.render_card("Lightning Bolt")
    raw = preprocess.crop_title_bar(card)
    prepared = preprocess.prepare(card).normal
    assert prepared.width == raw.width * preprocess.UPSCALE


def test_thresholding_produces_a_two_tone_image() -> None:
    prepared = preprocess.prepare(cards.render_card("Counterspell")).normal
    assert set(prepared.tobytes()) <= {0, 255}


# --- recognition (needs tesseract) -----------------------------------------


@pytest.fixture
def engine(settings: object) -> ocr_engine.OcrEngine:
    resolved = ocr_engine.get_engine(get_settings())
    assert resolved.available(), (
        "The tesseract binary is missing. Run the suite in the app container: "
        "docker compose -f docker-compose.test.yml run --rm tests"
    )
    return resolved


def _read(engine: ocr_engine.OcrEngine, card: Image.Image) -> str:
    """Read the better of the two prepared polarities, as the pipeline does."""
    best, best_weight = "", -1.0
    for _variant, image in preprocess.prepare(card):
        result = engine.recognise(image)
        weight = result.confidence * min(len(result.text.strip()), 30)
        if weight > best_weight:
            best_weight, best = weight, result.text
    return best


@pytest.mark.parametrize(
    "name",
    [
        "Lightning Bolt",
        "Counterspell",
        "Rhystic Study",
        "Swords to Plowshares",
        "Birds of Paradise",
        "Dockside Extortionist",
    ],
)
def test_a_clean_card_reads_back(engine: ocr_engine.OcrEngine, name: str) -> None:
    from app.util.text import normalize_name

    text = _read(engine, cards.render_card(name))
    assert normalize_name(text).startswith(normalize_name(name)[:8]), (
        f"expected something like {name!r}, read {text!r}"
    )


def test_an_old_frame_still_reads(engine: ocr_engine.OcrEngine) -> None:
    from app.util.text import normalize_name

    text = _read(engine, cards.render_card("Lightning Bolt", style=cards.OLD_FRAME))
    assert "lightning" in normalize_name(text)


def test_a_dark_card_still_reads(engine: ocr_engine.OcrEngine) -> None:
    """Light text on a dark title bar is what the inverted variant exists for."""
    from app.util.text import normalize_name

    text = _read(engine, cards.render_card("Counterspell", style=cards.DARK))
    assert "counterspell" in normalize_name(text).replace(" ", "")


def test_a_blurred_card_still_reads(engine: ocr_engine.OcrEngine) -> None:
    from app.util.text import normalize_name

    text = _read(engine, cards.with_blur(cards.render_card("Rhystic Study")))
    assert "rhystic" in normalize_name(text)


def test_a_noisy_card_still_reads(engine: ocr_engine.OcrEngine) -> None:
    from app.util.text import normalize_name

    text = _read(engine, cards.with_noise(cards.render_card("Mana Leak")))
    assert "mana" in normalize_name(text)


def test_the_mana_cost_is_not_read_as_part_of_the_name(
    engine: ocr_engine.OcrEngine,
) -> None:
    text = _read(engine, cards.render_card("Cancel", mana_cost="1UU"))
    assert "UU" not in text


def test_an_empty_card_reads_as_nothing(engine: ocr_engine.OcrEngine) -> None:
    blank = Image.new("RGB", cards.CARD_SIZE, (210, 205, 190))
    result = engine.recognise(preprocess.prepare(blank).normal)
    assert result.is_empty or len(result.text.strip()) <= 3


def test_confidence_is_reported_in_the_zero_to_one_range(
    engine: ocr_engine.OcrEngine,
) -> None:
    result = engine.recognise(preprocess.prepare(cards.render_card("Counterspell")).normal)
    assert 0.0 <= result.confidence <= 1.0
    assert result.confidence > 0.3, "a clean synthetic render should be read confidently"


def test_the_engine_is_cached(engine: ocr_engine.OcrEngine, settings: object) -> None:
    assert ocr_engine.get_engine(get_settings()) is engine
