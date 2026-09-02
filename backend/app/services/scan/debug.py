"""Keep the last few scanned frames on disk, for diagnosing a card that will not scan.

Every hard scanner bug in this project has been solved by looking at what the pipeline
actually received rather than at a simulation of it. Simulations encode assumptions,
and the assumption is usually the bug: a synthetic card that was too bright, a
perspective warp applied to a card's *contents* but not its outline. A real frame
carries the glare, the angle, the focus and the surface all at once, and none of them
have to be guessed.

Off by default. When ``SCAN_DEBUG_FRAMES`` is set it keeps that many recent scans --
the frame as uploaded, every rectified candidate, and what the pipeline concluded --
and deletes the oldest beyond the limit, so it cannot fill a disk while someone forgets
it is on.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

import cv2
import numpy as np

log = logging.getLogger("mtgvault.scan.debug")

DIRECTORY = "scan-debug"


def debug_dir(data_dir: Path) -> Path:
    """Where captures are written."""
    return data_dir / DIRECTORY


def _prune(directory: Path, keep: int) -> None:
    """Delete all but the newest ``keep`` captures."""
    stems = sorted({path.name.split("-")[0] for path in directory.glob("*") if path.is_file()})
    for stem in stems[: max(0, len(stems) - keep)]:
        for path in directory.glob(f"{stem}-*"):
            path.unlink(missing_ok=True)


def capture(
    data_dir: Path,
    keep: int,
    *,
    sequence: int,
    frame: np.ndarray,
    cards: list[np.ndarray],
    summary: dict[str, Any],
) -> None:
    """Write one scan's frame, rectified candidates and verdict.

    Never raises: a diagnostic that can break scanning is worse than no diagnostic.
    """
    if keep <= 0:
        return
    try:
        directory = debug_dir(data_dir)
        directory.mkdir(parents=True, exist_ok=True)
        stem = f"{sequence:08d}"
        cv2.imwrite(str(directory / f"{stem}-frame.jpg"), frame)
        for position, card in enumerate(cards):
            cv2.imwrite(str(directory / f"{stem}-card{position}.jpg"), card)
        (directory / f"{stem}-result.json").write_text(
            json.dumps(summary, indent=2), encoding="utf-8"
        )
        _prune(directory, keep)
    except Exception as exc:  # pragma: no cover - diagnostics must not break scanning
        log.warning("scan_debug_capture_failed", extra={"error": str(exc)[:200]})
