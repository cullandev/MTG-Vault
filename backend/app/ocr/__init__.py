"""Optical character recognition for the card scanner.

``preprocess`` turns a rectified card into a thresholded title bar; ``engine`` reads
it. Nothing here knows about Magic cards -- name matching lives in
:mod:`app.services.scan.matching`.
"""

from app.ocr.engine import OcrEngine, OcrResult, OcrUnavailable, get_engine
from app.ocr.preprocess import InvalidImage, PreparedCrops, load_image, prepare

__all__ = [
    "InvalidImage",
    "OcrEngine",
    "OcrResult",
    "OcrUnavailable",
    "PreparedCrops",
    "get_engine",
    "load_image",
    "prepare",
]
