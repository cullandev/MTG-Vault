"""The scan identification pipeline.

    full camera frame (the phone sends frames, not crops)
      -> vision/detect.py    find every card-shaped quad, any angle, any position
      -> rectify each candidate
      -> escalating signal ladder, cheapest first:
           1. perceptual hash  ~5 ms   -> vision/index.py
           2. collector line   ~85 ms  -> services/scan/exact.py
           3. card name        ~600 ms -> services/scan/matching.py
      -> fusion.py   score the signals together, and across frames
      -> scan_events row

Two properties fall out of this shape, and both are the point.

**The card does not have to be aligned, centred, or fill the frame.** Detection traces
outlines rather than thresholding for a bright region, proposes several quads per
frame, and rectifies each with a perspective transform. A card at an angle, off to one
side, occupying a fiftieth of the frame, is still a card.

**No single signal has to work.** The ladder stops as soon as the evidence is
conclusive, so the common case is fast; but when the corner is unreadable the artwork
answers, and when the artwork is ambiguous the name breaks the tie. Evidence also
accumulates across frames, so partial reads that would each have been discarded add up.

Everything is bounded on purpose. The work is CPU-bound and a phone will happily fire
frames faster than they can be processed, so a semaphore caps concurrency and a
cheap gates in front of the expensive rungs mean a frame that cannot be read costs
almost nothing (ADR-026).
"""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass, field
from typing import Any

import cv2
import numpy as np
from PIL import Image
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.models import Card, OracleCard, ScanEvent, ScanSession
from app.ocr import engine as ocr_engine
from app.ocr import preprocess
from app.services.scan import debug as scan_debug
from app.services.scan import exact as exact_lookup
from app.services.scan import fusion, identifiers
from app.services.scan.fusion import Evidence, ScoredPrinting
from app.services.scan.identifiers import CollectorIdentity, parse_collector_line
from app.services.scan.matching import NameMatch, match_name
from app.services.scan.printings import (
    PrintingRef,
    order_sticky,
    printings_of,
    refs_for,
    resolve_printing,
)
from app.vision import detect as vision_detect
from app.vision import hashing as vision_hashing
from app.vision import index as vision_index
from app.vision.index import VisualHit

log = logging.getLogger("mtgvault.scan.identify")

MAX_ANALYSED = 3
"""How many detected quads to analyse per frame. Detection may legitimately return
several -- a card next to a deck box, or a row laid out to be scanned together -- but
running OCR on all of them would blow the frame budget."""

MAX_CANDIDATES_RETURNED = 8

MIN_SHARPNESS = 35.0
"""Below this, a rectified candidate is too soft to read and is not worth OCR.

Measured on real captured frames: motion-blurred smears and card-shaped patches of
carpet score 2 to 14, while cards in focus score 114 to 360. Most of a scanning
session's frames are the former -- a hand moving a card into place -- and each was
costing the better part of a second of OCR that could never have succeeded."""

RESEMBLES_NOTHING_Z = 5.0
"""Below this, with no lead over the runner-up, the crop resembles nothing in the whole
index and is not worth OCR either.

Sharpness alone cannot reject a card-shaped patch of *textured* carpet -- speckle scores
as high as printed text. But a real card, with all 107 000 printings indexed, always
resembles something; a texture patch produces a flat cluster of weak matches. Set well
below the worst real card measured (5.2, under combined blur, tilt and glare) so a
genuinely difficult card still gets every rung."""

_semaphores: dict[int, asyncio.Semaphore] = {}
_debug_sequence = 0
_cache: dict[str, tuple[float, IdentifyResult]] = {}


class ScanBusy(RuntimeError):
    """Every processing slot is taken; the client should drop this frame and retry."""


@dataclass
class IdentifyResult:
    """What the scanner overlay is told about one frame."""

    match: PrintingRef | None = None
    candidates: list[PrintingRef] = field(default_factory=list)
    detections: list[dict[str, Any]] = field(default_factory=list)
    """Card outlines found in the frame, in frame coordinates, so the overlay can draw
    what the server actually saw. The phone no longer detects anything itself."""
    confidence: float = 0.0
    """The winning printing's fused evidence score, capped at 1.0 for display."""
    fuzz_score: float = 0.0
    ocr_text: str = ""
    collector_text: str = ""
    method: str = "none"
    """Which signal carried the identification: collector, visual, name, fused, none."""
    ambiguous: bool = False
    clipped: int = 0
    """Card-shaped regions that ran off the frame edge and so could not be analysed.
    The overlay turns this into "fit the whole card in view", which is actionable in a
    way that "no card found" is not."""
    exact: bool = False
    """The evidence is conclusive; the overlay may lock in on this single frame."""
    latency_ms: float = 0.0
    stage_ms: dict[str, float] = field(default_factory=dict)
    """Per-stage timings. The ladder only pays for the stages it needed, and this is
    how that stays visible rather than becoming folklore."""
    margin: float | None = None
    """Evidence separation: top score minus runner-up, before any display
    reorder. The single best predictor of whether a lead can be trusted, and
    the number every threshold-tuning session needs first."""
    sticky_sets: list[str] = field(default_factory=list)
    """The session's already-confirmed sets, as used by the sticky reorder."""
    sticky_promoted: bool = False
    """Whether the sticky reorder changed which printing leads this frame."""
    diagnosis: dict[str, Any] = field(default_factory=dict)
    """What the artwork proposed and what the symbol band measured.

    Recorded because the thresholds governing both were first set from *reference*
    images degraded synthetically, which turned out to be a far gentler population than
    a photograph taken through perspective correction and a desk lamp. Calibrating them
    honestly needs distances from real scans, and a scanning session is the only place
    those exist."""
    event_id: int | None = None

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "match": self.match.as_dict() if self.match else None,
            "candidates": [candidate.as_dict() for candidate in self.candidates],
            "detections": self.detections,
            "confidence": round(min(self.confidence, 1.0), 3),
            "fuzz_score": round(self.fuzz_score, 1),
            "ocr_text": self.ocr_text,
            "collector_text": self.collector_text,
            "method": self.method,
            "ambiguous": self.ambiguous,
            "clipped": self.clipped,
            "exact": self.exact,
            "latency_ms": round(self.latency_ms, 1),
            "stage_ms": {key: round(value, 1) for key, value in self.stage_ms.items()},
            "event_id": self.event_id,
        }


def get_semaphore(settings: Settings) -> asyncio.Semaphore:
    """Return the concurrency limiter for the configured width."""
    width = max(1, settings.scan_max_concurrency)
    semaphore = _semaphores.get(width)
    if semaphore is None:
        semaphore = asyncio.Semaphore(width)
        _semaphores[width] = semaphore
    return semaphore


def reset_state() -> None:
    """Clear the semaphore and accumulated evidence. Tests use this between cases."""
    _semaphores.clear()
    fusion.get_accumulator().reset()


def _decode_frame(image_bytes: bytes) -> np.ndarray:
    """Decode an uploaded frame to BGR.

    Raises:
        preprocess.InvalidImage: The payload is not a usable image.
    """
    if len(image_bytes) < preprocess.MIN_IMAGE_BYTES:
        raise preprocess.InvalidImage("Image payload is too small to be a camera frame")
    if len(image_bytes) > preprocess.MAX_IMAGE_BYTES:
        raise preprocess.InvalidImage("Image payload is too large")
    buffer = np.frombuffer(image_bytes, dtype=np.uint8)
    frame = cv2.imdecode(buffer, cv2.IMREAD_COLOR)
    if frame is None:
        raise preprocess.InvalidImage("Could not decode the uploaded frame")
    return frame


def _to_pil(card: np.ndarray) -> Image.Image:
    """Convert a rectified BGR card to the RGB image the OCR crops expect."""
    return Image.fromarray(cv2.cvtColor(card, cv2.COLOR_BGR2RGB))


def _read_collector(
    db: DbSession, settings: Settings, card: Image.Image
) -> tuple[int | None, bool, CollectorIdentity, ocr_engine.OcrResult]:
    """Try to resolve the printing from the collector line alone.

    Crops are tried one at a time -- both polarities, at a few vertical offsets -- and
    the loop stops at the first that resolves to a real printing, so a well-framed
    modern card costs a single OCR call. The extra offsets only cost anything on cards
    that would otherwise have failed outright, because detection cannot guarantee the
    rectified card is framed exactly like a whole one.
    """
    engine = ocr_engine.get_engine(settings)
    best_identity = CollectorIdentity()
    best_reading = ocr_engine.OcrResult(text="", confidence=0.0, engine=engine.name)

    for variant, image in preprocess.collector_candidates(card):
        reading = engine.recognise(image, mode=ocr_engine.MODE_BLOCK)
        if reading.is_empty:
            continue
        identity = parse_collector_line(reading.text)
        tagged = ocr_engine.OcrResult(
            text=reading.text,
            confidence=reading.confidence,
            variant=variant,
            engine=reading.engine,
        )
        # Keep the most complete parse seen, so a failed lookup still reports what it
        # read -- a mis-aimed crop is otherwise invisible.
        if identity.is_exact or (identity.collector_number and not best_identity.is_exact):
            best_identity = identity
            best_reading = tagged
        found = exact_lookup.lookup_exact(db, identity, lang=settings.scan_default_lang)
        if found is not None:
            return found.card.id, found.near_miss, identity, tagged

    return None, False, best_identity, best_reading


def _read_name(settings: Settings, card: Image.Image) -> ocr_engine.OcrResult:
    """Run title-bar OCR over both polarities and keep the more promising reading.

    "More promising" is length-weighted confidence rather than raw confidence: a foil
    that reads two confident characters loses to one that reads a whole name slightly
    less confidently.
    """
    engine = ocr_engine.get_engine(settings)
    best: ocr_engine.OcrResult | None = None
    best_weight = -1.0
    for variant, image in preprocess.prepare(card):
        result = engine.recognise(image)
        if result.is_empty:
            continue
        weight = result.confidence * min(len(result.text.strip()), 30)
        if weight > best_weight:
            best_weight = weight
            best = ocr_engine.OcrResult(
                text=result.text,
                confidence=result.confidence,
                variant=variant,
                engine=result.engine,
            )
    return best or ocr_engine.OcrResult(text="", confidence=0.0, engine=engine.name)


def _name_card_ids(db: DbSession, match: NameMatch, prefer_numbers: set[str]) -> dict[int, float]:
    """Spread a name match's score onto concrete printings.

    A name identifies an oracle card, so one printing per candidate is chosen the same
    way the picker would -- preferring what the corner read, then what is already
    owned. The alternative, scoring every printing of every candidate, would let a
    card with forty reprints drown out one with two.
    """
    score = fusion.name_score_for(match)
    if score <= 0:
        return {}

    resolved: dict[int, float] = {}
    for candidate in match.candidates[:MAX_CANDIDATES_RETURNED]:
        ref = resolve_printing(db, candidate.oracle_id, prefer_numbers=prefer_numbers)
        if ref is None:
            continue
        # Runners-up in an ambiguous match are worth less than the best reading.
        weight = score if candidate.score >= match.score - 1e-6 else score * 0.6
        resolved[ref.card_id] = max(resolved.get(ref.card_id, 0.0), weight)
    return resolved


def _conclusive(evidence: Evidence) -> bool:
    """Whether the evidence so far already settles the question.

    Asks the scorer rather than re-stating a threshold, so the ladder stops on exactly
    the same rule the answer is judged by. Keeping a separate condition here is how the
    ladder came to run every rung on a frame whose artwork match was already decisive,
    turning a fifty-millisecond answer into an eight-hundred-millisecond one.
    """
    return any(item.confident for item in fusion.score_evidence(evidence))


def _diagnose(db: DbSession, evidence: Evidence) -> dict[str, Any]:
    """Record what each signal saw, for calibrating the thresholds against real scans.

    Names and set codes rather than ids: this is read by a human afterwards, and an
    integer that needs a join to mean anything does not survive the trip.
    """
    top: list[dict[str, Any]] = []
    for hit in evidence.visual[:5]:
        card = db.get(Card, hit.card_id)
        top.append(
            {
                "printing": f"{card.set_code}/{card.collector_number}" if card else hit.card_id,
                "name": card.name if card else None,
                "bits": hit.distance,
                "z": round(hit.z_score, 2),
                "art_shared": hit.art_shared,
            }
        )

    verdict = evidence.symbol_verdict
    symbol: dict[str, Any] | None = None
    if verdict is not None:
        chosen = db.get(Card, verdict.card_id) if verdict.card_id else None  # type: ignore[attr-defined]
        symbol = {
            "chose": f"{chosen.set_code}/{chosen.collector_number}" if chosen else None,
            "bits": verdict.distance,  # type: ignore[attr-defined]
            "margin": verdict.margin,  # type: ignore[attr-defined]
            "compared": verdict.compared,  # type: ignore[attr-defined]
        }

    return {
        "visual": top,
        "symbol": symbol,
        "collector_raw": evidence.collector.raw[:60] if evidence.collector.raw else None,
        "collector_resolved": evidence.collector_card_id is not None,
        "print_year": evidence.collector.print_year,
        "year_chose": _label(db, evidence.year_card_id),
    }


def _label(db: DbSession, card_id: int | None) -> str | None:
    """``set/number`` for a printing, for the human reading a scan event afterwards."""
    if card_id is None:
        return None
    card = db.get(Card, card_id)
    return f"{card.set_code}/{card.collector_number}" if card else str(card_id)


def _narrow_by_year(db: DbSession, hits: list[VisualHit], year: int) -> int | None:
    """Choose among the artwork's candidates using the year printed on the card.

    Only a unique survivor counts, so two candidates from one year leaves the question
    open rather than handing it back to the artwork under another name.
    """
    candidates = [hit.card_id for hit in hits]
    if not candidates:
        return None
    released = {
        int(card_id): released_at
        for card_id, released_at in db.execute(
            select(Card.id, Card.released_at).where(Card.id.in_(candidates))
        )
    }
    return identifiers.narrow_by_print_year(released, candidates, year)


def _break_tie_on_symbol(
    db: DbSession, card: np.ndarray, hits: list[VisualHit]
) -> vision_index.SymbolVerdict:
    """Ask the type-line band which of the artwork's candidates this is.

    Returning nothing is a normal outcome: two printings from one set share a symbol,
    and the band lands on artwork rather than a type line for sagas, adventures, split
    cards and full-art lands. The tie then stays unbroken and the user is asked, which
    is the right answer to a question the card does not carry.
    """
    index = vision_index.get_index(db)
    try:
        query = vision_hashing.symbol_hash(card)
    except ValueError:
        return vision_index.SymbolVerdict(card_id=None)
    return vision_index.break_tie(index, hits, query)


def _search_visual(db: DbSession, card: np.ndarray) -> list[VisualHit]:
    """Hash one rectified card and search the index."""
    index = vision_index.get_index(db)
    if not index.usable:
        return []
    return vision_index.search(index, vision_hashing.card_hash_both_orientations(card))


def _best_quad(
    db: DbSession,
    frame: np.ndarray,
    detection: vision_detect.Detection,
    *,
    stage_ms: dict[str, float],
) -> tuple[np.ndarray, list[VisualHit]]:
    """Rectify the detected quad, and its alternates if it does not convince.

    Detection proposes several quads per contour and ranks them, but the ranking is not
    reliable enough to be final: on a low-contrast edge -- a borderless card on a dark
    mat -- the quad that wins can be slightly sheared, which leaves the geometry looking
    plausible and the hash worthless. Measured on one frame, the chosen quad matched at
    a distance of 290 bits while an alternative from the same contour matched the right
    card at 48.

    Each retry costs a hash and an index search, and only runs while nothing convincing
    has been found, so a card that identifies first time pays nothing for this.
    """
    started = time.perf_counter()
    card = vision_detect.rectify(frame, detection)
    hits = _search_visual(db, card)

    for corners in detection.alternates:
        if hits and fusion.visual_score(hits[0], hits[1].z_score if len(hits) > 1 else None) >= (
            fusion.LOCK_THRESHOLD
        ):
            break
        other = vision_detect.rectify_corners(frame, corners)
        other_hits = _search_visual(db, other)
        if other_hits and (not hits or other_hits[0].z_score > hits[0].z_score):
            card, hits = other, other_hits

    stage_ms["visual"] = stage_ms.get("visual", 0.0) + (time.perf_counter() - started) * 1000
    return card, hits


def _analyse(
    db: DbSession,
    settings: Settings,
    frame: np.ndarray,
    detection: vision_detect.Detection,
    *,
    stage_ms: dict[str, float],
) -> tuple[Evidence, np.ndarray]:
    """Run the signal ladder over one rectified card, stopping when it is conclusive.

    The ordering is the whole speed story: hashing costs a few tens of milliseconds,
    the collector line several hundred, and title OCR several hundred more. Each rung
    only runs when the ones above it left the answer in doubt.
    """
    evidence = Evidence()

    # Rung 1: the artwork, over the detected quad and its alternates. It survives blur
    # far better than text does, so it runs even on a soft frame.
    card, evidence.visual = _best_quad(db, frame, detection, stage_ms=stage_ms)

    # Rung 0, after the fact: is this worth *reading* at all? A smear cannot be OCR'd
    # however long is spent trying, and most frames in a session are smears.
    started = time.perf_counter()
    focus = vision_hashing.sharpness(card)
    stage_ms["focus"] = stage_ms.get("focus", 0.0) + (time.perf_counter() - started) * 1000
    readable = focus >= MIN_SHARPNESS

    if _conclusive(evidence) or not readable:
        return evidence, card

    # "A cluster of weak, near-equal matches" means the frame resembles the whole
    # database slightly -- there is no card here. Siblings are excluded from that
    # reading: printings sharing an artwork are *supposed* to score near-equally, and
    # counting them as the cluster would abandon every reprinted card held at a
    # slightly soft focus (ADR-027).
    best = evidence.visual[0] if evidence.visual else None
    rival = (
        next(
            (
                hit.z_score
                for hit in evidence.visual[1:]
                if hit.art_id != best.art_id or hit.art_id < 0
            ),
            0.0,
        )
        if best is not None
        else 0.0
    )
    if best is not None and best.z_score < RESEMBLES_NOTHING_Z and best.z_score - rival < 1.0:
        return evidence, card

    image = _to_pil(card)

    # Rung 2: the collector line, which is a primary key when it can be read.
    started = time.perf_counter()
    card_id, near_miss, identity, reading = _read_collector(db, settings, image)
    evidence.collector_card_id = card_id
    evidence.collector_near_miss = near_miss
    evidence.collector = identity
    stage_ms["collector"] = stage_ms.get("collector", 0.0) + (time.perf_counter() - started) * 1000
    if card_id is not None:
        evidence.ocr_text = reading.text
    if _conclusive(evidence):
        return evidence, card

    # Rung 2a: the copyright year, which the same OCR pass already read. Free, and on a
    # pre-2015 card it is the strongest thing available: those cards print no collector
    # number and no set code, and their set symbols can sit fourteen bits apart -- inside
    # photographic noise -- while their printed years differ outright (ADR-029).
    if evidence.visual and evidence.visual[0].art_shared and identity.print_year:
        started = time.perf_counter()
        evidence.year_card_id = _narrow_by_year(db, evidence.visual, identity.print_year)
        stage_ms["year"] = stage_ms.get("year", 0.0) + (time.perf_counter() - started) * 1000
        if _conclusive(evidence):
            return evidence, card

    # Rung 2b: the set symbol, for the same question the year answers, on the cards the
    # year cannot reach -- a modern reprint whose corner was unreadable, or two printings
    # from one year (ADR-028). Costs one small hash and a handful of comparisons.
    if evidence.visual and evidence.visual[0].art_shared:
        started = time.perf_counter()
        verdict = _break_tie_on_symbol(db, card, evidence.visual)
        evidence.symbol_card_id = verdict.card_id
        evidence.symbol_verdict = verdict
        stage_ms["symbol"] = stage_ms.get("symbol", 0.0) + (time.perf_counter() - started) * 1000
        if _conclusive(evidence):
            return evidence, card

    # Rung 3: the name. Slowest, so it runs last and only when nothing above it
    # settled the question.
    started = time.perf_counter()
    name_reading = _read_name(settings, image)
    evidence.ocr_text = evidence.ocr_text or name_reading.text
    if not name_reading.is_empty:
        match = match_name(db, name_reading.text, settings)
        evidence.name = match
        prefer = {identity.collector_number} if identity.collector_number else set()
        evidence.name_card_ids = _name_card_ids(db, match, prefer)
    stage_ms["name"] = stage_ms.get("name", 0.0) + (time.perf_counter() - started) * 1000
    return evidence, card


_SIGNAL_TOKENS = (
    ("collector", "collector line"),
    ("year", "printed year"),
    ("symbol", "set symbol"),
    ("visual", "artwork"),
    ("name", "card name"),
)
"""Reason text -> the method name recorded on the scan event. Kept in step with the
labels fusion.score_evidence writes; nothing else parses them."""


def _method_for(best: ScoredPrinting | None) -> str:
    """Name the signal that carried an identification, for the accuracy statistic."""
    if best is None:
        return "none"
    reasons = " ".join(best.reasons)
    signals = [name for name, token in _SIGNAL_TOKENS if token in reasons]
    if len(signals) > 1:
        return "fused"
    return signals[0] if signals else "none"


def identify_sync(
    db: DbSession,
    settings: Settings,
    image_bytes: bytes,
    *,
    session_id: str | None = None,
) -> IdentifyResult:
    """Identify the card in a camera frame. Blocking; call via a thread.

    Args:
        db: Open database session.
        settings: Application settings.
        image_bytes: A whole camera frame as JPEG.
        session_id: Scanning session this frame belongs to.

    Returns:
        The identification result, with a ``scan_events`` row already written.

    Raises:
        preprocess.InvalidImage: The upload is not a usable image.
    """
    started = time.perf_counter()
    stage_ms: dict[str, float] = {}

    # A stale session id (app restore, expired tab) must degrade to an unattributed
    # event, not violate the foreign key and 500 on every frame.
    if session_id is not None and db.get(ScanSession, session_id) is None:
        session_id = None

    decode_started = time.perf_counter()
    frame = _decode_frame(image_bytes)
    stage_ms["decode"] = (time.perf_counter() - decode_started) * 1000

    detect_started = time.perf_counter()
    detections = vision_detect.detect_cards(frame)
    stage_ms["detect"] = (time.perf_counter() - detect_started) * 1000

    result = IdentifyResult(detections=[item.as_dict() for item in detections])
    # A card running off the edge of the frame cannot be identified: rectifying it
    # stretches a partial card to full size, so the hash covers the wrong content and
    # the OCR crops land in the wrong place. Analysing it anyway is worse than not
    # analysing it, because it produces a confident-looking list of wrong cards.
    usable = [item for item in detections if not item.clipped]
    result.clipped = len(detections) - len(usable)
    if not usable:
        result.latency_ms = (time.perf_counter() - started) * 1000
        result.stage_ms = stage_ms
        result.event_id = _record_event(db, result, session_id)
        return result

    frame_scores: list[ScoredPrinting] = []
    rectified: list[np.ndarray] = []
    for detection in usable[:MAX_ANALYSED]:
        evidence, card = _analyse(db, settings, frame, detection, stage_ms=stage_ms)
        rectified.append(card)
        if not result.ocr_text:
            result.ocr_text = evidence.ocr_text
        if not result.collector_text and evidence.collector.raw:
            result.collector_text = evidence.collector.raw
        if evidence.name is not None:
            result.fuzz_score = max(result.fuzz_score, evidence.name.score)
        frame_scores.extend(fusion.score_evidence(evidence))
        if not result.diagnosis:
            result.diagnosis = _diagnose(db, evidence)
        # One conclusive candidate is enough; the rest of the frame is not worth OCR.
        if any(item.confident for item in frame_scores):
            break

    # Evidence only accumulates within a scanning session. Without one there is no
    # sequence of frames to accumulate *over*, and folding unrelated frames into a
    # shared bucket would let one card's evidence decide another's identification.
    accumulator = fusion.get_accumulator()
    if session_id is None:
        totals = sorted(frame_scores, key=lambda item: -item.score)
    else:
        totals = accumulator.add(session_id, frame_scores)

    _apply_scores(db, result, totals, session_id=session_id)
    if result.exact and session_id is not None:
        # A locked-in card must not seed the next one off the stack.
        accumulator.clear(session_id)

    result.latency_ms = (time.perf_counter() - started) * 1000
    result.stage_ms = stage_ms
    event_started = time.perf_counter()
    result.event_id = _record_event(db, result, session_id)
    stage_ms["event"] = (time.perf_counter() - event_started) * 1000

    capture_started = time.perf_counter()
    _capture_debug(settings, frame, rectified, result)
    if settings.scan_debug_frames > 0:
        # Only reported when it actually ran, so its cost is never mistaken for the
        # pipeline's. Writing a frame and its crops to disk is not cheap.
        stage_ms["capture"] = (time.perf_counter() - capture_started) * 1000
    log.info(
        "scan_identify",
        extra={
            "detections": len(detections),
            "clipped": result.clipped,
            "method": result.method,
            "matched": bool(result.match),
            "confidence": round(result.confidence, 3),
            "latency_ms": round(result.latency_ms, 1),
            "stage_ms": {key: round(value) for key, value in stage_ms.items()},
        },
    )
    return result


def _capture_debug(
    settings: Settings,
    frame: np.ndarray,
    cards: list[np.ndarray],
    result: IdentifyResult,
) -> None:
    """Write this scan to disk when frame capture is switched on."""
    if settings.scan_debug_frames <= 0:
        return
    global _debug_sequence
    _debug_sequence += 1
    scan_debug.capture(
        settings.data_dir,
        settings.scan_debug_frames,
        sequence=_debug_sequence,
        frame=frame,
        cards=cards,
        summary={
            "method": result.method,
            "confidence": round(result.confidence, 3),
            "exact": result.exact,
            "detections": result.detections,
            "ocr_text": result.ocr_text,
            "collector_text": result.collector_text,
            "stage_ms": {key: round(value) for key, value in result.stage_ms.items()},
            "candidates": [
                {
                    "name": candidate.name,
                    "set": candidate.set_code,
                    "number": candidate.collector_number,
                    "score": round(candidate.score, 3),
                    "reasons": candidate.reasons,
                }
                for candidate in result.candidates
            ],
        },
    )


def _session_sets(db: DbSession, session_id: str | None, *, limit: int = 12) -> set[str]:
    """Sets the user has already confirmed this session -- the pile being scanned."""
    if session_id is None:
        return set()
    rows = db.execute(
        select(Card.set_code)
        .join(ScanEvent, ScanEvent.confirmed_card_id == Card.id)
        .where(ScanEvent.session_id == session_id, ScanEvent.confirmed_card_id.is_not(None))
        .order_by(ScanEvent.id.desc())
        .limit(limit)
    ).scalars()
    return {code for code in rows if code}


def _apply_scores(
    db: DbSession,
    result: IdentifyResult,
    totals: list[ScoredPrinting],
    *,
    session_id: str | None = None,
) -> None:
    """Turn accumulated scores into a match, candidates and a verdict."""
    if not totals:
        result.method = "none"
        return

    shortlist = totals[:MAX_CANDIDATES_RETURNED]
    refs = refs_for(db, [item.card_id for item in shortlist])

    candidates: list[PrintingRef] = []
    for item in shortlist:
        ref = refs.get(item.card_id)
        if ref is None:
            continue
        ref.score = item.score
        ref.reasons = item.reasons
        candidates.append(ref)

    preferred = _session_sets(db, session_id)
    ordered = order_sticky(candidates, preferred)
    result.sticky_sets = sorted(preferred)
    result.sticky_promoted = bool(candidates) and bool(ordered) and ordered[0] is not candidates[0]
    candidates = ordered
    result.candidates = candidates
    if len(shortlist) >= 2:
        result.margin = round(shortlist[0].score - shortlist[1].score, 3)
    if not candidates:
        result.method = "none"
        return

    best = shortlist[0]
    result.confidence = best.score
    result.method = _method_for(best)

    if best.confident:
        # Score alone must never lock: accumulated shared-art frames can sum
        # past the threshold while still unable to name the printing (ADR-027).
        # ``confident`` is the score AND ``printing_certain`` together. The lock
        # goes to the printing the evidence named -- the sticky-set reorder is
        # for the picker's display and must never redirect a certain answer.
        result.match = next(
            (candidate for candidate in candidates if candidate.card_id == best.card_id),
            candidates[0],
        )
        result.exact = True
    elif best.score >= fusion.PICKER_THRESHOLD:
        # Knows the card, not the printing -- offer the siblings instead.
        result.ambiguous = True


def _record_event(db: DbSession, result: IdentifyResult, session_id: str | None) -> int:
    """Write the scan_events row that the accuracy statistic is computed from.

    The detail is worth the bytes. A scanning session is the only place this data
    exists, and once the session ends the question "which rung was slow, and did the
    frame even hold a card?" can no longer be answered at all.
    """
    # The accuracy statistic asks "was the first thing we put in front of the
    # user what they kept" -- which includes the picker's top row, not only a
    # hard lock (the certainty gate makes pickers the normal path for cards the
    # evidence cannot pin to a printing).
    first = result.match or (result.candidates[0] if result.candidates else None)
    event = ScanEvent(
        session_id=session_id,
        first_match_card_id=first.card_id if first else None,
        first_match_oracle_id=first.oracle_id if first else None,
        method=result.method,
        ocr_text=(result.ocr_text or result.collector_text)[:200] or None,
        ocr_confidence=min(result.confidence, 1.0),
        fuzz_score=result.fuzz_score,
        candidate_count=len(result.candidates),
        latency_ms=result.latency_ms,
        detail_json={
            "stage_ms": {key: round(value, 1) for key, value in result.stage_ms.items()},
            "detections": len(result.detections),
            "clipped": result.clipped,
            # Uncapped, unlike ocr_confidence: two signals agreeing scores above 1.0,
            # and that is exactly the case worth being able to count afterwards.
            "score": round(result.confidence, 3),
            **({"diagnosis": result.diagnosis} if result.diagnosis else {}),
            # The tuning record: evidence separation, the shortlist as shown,
            # and whether the sticky reorder acted. Only frames that proposed
            # something pay the bytes; blank frames stay lean.
            **({"margin": result.margin} if result.margin is not None else {}),
            **(
                {
                    "top": [
                        {
                            "p": f"{candidate.set_code}/{candidate.collector_number}",
                            "n": candidate.name,
                            "s": round(candidate.score, 3),
                            "r": candidate.reasons or [],
                        }
                        for candidate in result.candidates[:3]
                    ]
                }
                if result.method != "none" and result.candidates
                else {}
            ),
            **({"session_sets": result.sticky_sets} if result.sticky_sets else {}),
            **({"sticky_promoted": True} if result.sticky_promoted else {}),
        },
    )
    db.add(event)
    db.flush()
    return int(event.id)


async def identify(
    db: DbSession,
    settings: Settings,
    image_bytes: bytes,
    *,
    session_id: str | None = None,
) -> IdentifyResult:
    """Identify a card, with the concurrency limit applied.

    Raises:
        ScanBusy: Every slot is in use. The caller turns this into a 429 so the phone
            drops the frame instead of queueing a backlog it no longer wants.
    """
    semaphore = get_semaphore(settings)
    if semaphore.locked():
        # Shed the frame rather than queueing it. By the time a backlogged frame got
        # its turn the card would have moved, and the phone has already sent newer
        # ones -- a queue here only adds latency to results nobody wants.
        raise ScanBusy("All processing slots are busy")

    async with semaphore:
        # The session is only ever touched by one thread at a time (this coroutine
        # awaits the thread), and the engine is built with check_same_thread=False.
        result = await asyncio.to_thread(
            identify_sync, db, settings, image_bytes, session_id=session_id
        )

    return result


def oracle_name(db: DbSession, oracle_id: str) -> str | None:
    """Look up a card's printed name, for logging and manual-search fallbacks."""
    return db.scalar(select(OracleCard.name).where(OracleCard.oracle_id == oracle_id))


__all__ = [
    "IdentifyResult",
    "PrintingRef",
    "ScanBusy",
    "identify",
    "identify_sync",
    "oracle_name",
    "printings_of",
    "reset_state",
    "resolve_printing",
]
