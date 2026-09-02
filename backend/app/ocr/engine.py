"""OCR engines.

One interface, two implementations. Tesseract is the default (ADR-007): with a tightly
cropped, pre-thresholded title bar and ``--psm 7`` it reads a single line of text in
tens of milliseconds and adds nothing to the image beyond the system package. PaddleOCR
is markedly better on stylised type but pulls in a heavyweight runtime and model
weights, so it stays behind a config switch until the accuracy statistic says it is
needed.
"""

from __future__ import annotations

import logging
import shutil
from abc import ABC, abstractmethod
from dataclasses import dataclass
from functools import lru_cache
from typing import Any

from PIL import Image

from app.config import Settings

log = logging.getLogger("mtgvault.ocr")

# Card names use letters, digits, spaces and a small set of punctuation. Restricting
# the character set stops the recogniser inventing symbols out of frame artefacts.
# No double-quote: no card name needs one, and pytesseract shlex-splits this config
# string, so an unbalanced quote inside it is a parse error. The whole argument is
# double-quoted so the space and apostophe survive the split.
TESSERACT_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZabcdefghijklmnopqrstuvwxyz0123456789 ',-.!/&:"
TESSERACT_CONFIG = f'--psm 7 --oem 3 -c "tessedit_char_whitelist={TESSERACT_CHARS}"'
"""``--psm 7`` means "treat the image as a single text line", which is exactly what a
cropped title bar is."""

# The collector block is two lines of digits, a slash and an upper-case set code. Its
# alphabet is far narrower than a card name's, and narrowing it is what stops "0" being
# read as "O" in a field where that distinction decides which printing was scanned.
TESSERACT_COLLECTOR_CHARS = "ABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789/ "
TESSERACT_COLLECTOR_CONFIG = (
    f'--psm 6 --oem 3 -c "tessedit_char_whitelist={TESSERACT_COLLECTOR_CHARS}"'
)
"""``--psm 6`` treats the crop as a uniform block, which keeps both lines of the
collector block -- number over set code -- instead of merging them."""

MODE_LINE = "line"
MODE_BLOCK = "block"


class OcrUnavailable(RuntimeError):
    """The configured OCR engine is not installed."""


@dataclass(frozen=True)
class OcrResult:
    """One recognition attempt."""

    text: str
    confidence: float
    """0.0-1.0. Tesseract reports per-word confidence; this is the mean over words."""
    variant: str = "normal"
    engine: str = "tesseract"

    @property
    def is_empty(self) -> bool:
        """Whether the recogniser produced nothing usable."""
        return not self.text.strip()


class OcrEngine(ABC):
    """Recognises a single line of text in a prepared image."""

    name: str = "abstract"

    @abstractmethod
    def recognise(self, image: Image.Image, *, mode: str = MODE_LINE) -> OcrResult:
        """Read the text in ``image``.

        Args:
            image: A prepared, binarised crop.
            mode: ``MODE_LINE`` for a single line such as a title bar, ``MODE_BLOCK``
                for the multi-line collector block.
        """

    @abstractmethod
    def available(self) -> bool:
        """Whether this engine can actually run here."""


class TesseractEngine(OcrEngine):
    """Tesseract via pytesseract."""

    name = "tesseract"

    def available(self) -> bool:
        """Whether the ``tesseract`` binary is on PATH."""
        return shutil.which("tesseract") is not None

    def recognise(self, image: Image.Image, *, mode: str = MODE_LINE) -> OcrResult:
        """Read text, returning the mean per-word confidence.

        Raises:
            OcrUnavailable: The tesseract binary is missing.
        """
        import pytesseract
        from pytesseract import Output, TesseractNotFoundError

        config = TESSERACT_COLLECTOR_CONFIG if mode == MODE_BLOCK else TESSERACT_CONFIG
        try:
            data = pytesseract.image_to_data(image, config=config, output_type=Output.DICT)
        except TesseractNotFoundError as exc:
            raise OcrUnavailable(
                "The tesseract binary is not installed. It ships in the app container; "
                "on a bare development machine install it separately or run the tests "
                "in Docker."
            ) from exc

        words: list[str] = []
        confidences: list[float] = []
        for text, confidence in zip(data.get("text", []), data.get("conf", []), strict=False):
            cleaned = str(text).strip()
            if not cleaned:
                continue
            try:
                score = float(confidence)
            except (TypeError, ValueError):
                continue
            # Tesseract uses -1 for "no confidence available".
            if score < 0:
                continue
            words.append(cleaned)
            confidences.append(score)

        mean = sum(confidences) / len(confidences) if confidences else 0.0
        return OcrResult(text=" ".join(words), confidence=mean / 100.0, engine=self.name)


class PaddleEngine(OcrEngine):
    """PaddleOCR, selected with ``OCR_ENGINE=paddle`` (ADR-007).

    Deliberately not a dependency of the default image. Selecting it without the extra
    package installed fails loudly at startup rather than silently degrading.
    """

    name = "paddle"

    def available(self) -> bool:
        """Whether paddleocr is importable."""
        try:
            import paddleocr  # noqa: F401
        except ImportError:
            return False
        return True

    def recognise(self, image: Image.Image, *, mode: str = MODE_LINE) -> OcrResult:
        """Read text with PaddleOCR.

        ``mode`` is accepted for interface parity; Paddle segments lines itself.

        Raises:
            OcrUnavailable: paddleocr is not installed.
        """
        if not self.available():
            raise OcrUnavailable("OCR_ENGINE=paddle but paddleocr is not installed in this image.")
        import numpy as np
        from paddleocr import PaddleOCR

        reader: Any = _paddle_reader(PaddleOCR)
        lines = reader.ocr(np.array(image.convert("RGB")), cls=False) or []
        words: list[str] = []
        confidences: list[float] = []
        for block in lines:
            for entry in block or []:
                text, score = entry[1]
                words.append(str(text))
                confidences.append(float(score))
        mean = sum(confidences) / len(confidences) if confidences else 0.0
        return OcrResult(text=" ".join(words), confidence=mean, engine=self.name)


@lru_cache(maxsize=1)
def _paddle_reader(factory: object) -> object:  # pragma: no cover - optional engine
    """Build the PaddleOCR reader once; model loading is slow."""
    return factory(use_angle_cls=False, lang="en", show_log=False)  # type: ignore[operator]


_ENGINES: dict[str, type[OcrEngine]] = {
    "tesseract": TesseractEngine,
    "paddle": PaddleEngine,
}


@lru_cache(maxsize=2)
def _engine_for(name: str) -> OcrEngine:
    return _ENGINES[name]()


def get_engine(settings: Settings) -> OcrEngine:
    """Return the configured OCR engine.

    Args:
        settings: Application settings; ``ocr_engine`` selects the implementation.

    Returns:
        A ready engine instance. Engines are cached, because PaddleOCR in particular
        takes seconds to construct.
    """
    return _engine_for(settings.ocr_engine)
