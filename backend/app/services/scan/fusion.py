"""Combine independent identification signals into one answer.

Three things can tell you which printing is on the mat, and each fails differently:

* **The collector line** is a primary key, and unreadable on pre-2015 cards, on a
  glare-blown corner, and whenever the bottom of the card is out of frame.
* **The image hash** does not care about language, font, wear or glare, but cannot
  separate printings that share an artwork -- more than half of a real collection --
  and needs the reference index to have been built.
* **The card name** survives a partial or angled view of the card, but cannot separate
  printings at all and mis-reads stylised type.

Scoring them together is what makes the scanner robust rather than merely fast: two
weak signals that agree beat one strong signal on its own, and no single failure stops
identification. That is the difference between "the corner was unreadable, so nothing
happened" and "the corner was unreadable, so it used the art and the name instead".

Evidence also **accumulates across frames**. The old loop demanded three consecutive
responses naming the same card and threw away everything else, so a frame that read
the number but not the set contributed nothing. Here frame 1 reading the number, frame
2 reading the set code and frame 3 hashing the artwork add up to an answer.
"""

from __future__ import annotations

import logging
import threading
import time
from dataclasses import dataclass, field

from app.services.scan.identifiers import CollectorIdentity
from app.services.scan.matching import NameMatch
from app.vision.index import MIN_Z_SCORE, VisualHit

log = logging.getLogger("mtgvault.scan.fusion")

LOCK_THRESHOLD = 1.0
"""Total score at which the scanner may lock in without asking."""

PICKER_THRESHOLD = 0.4
"""Below the lock threshold but above this, the user gets a picker rather than
silence."""

COLLECTOR_SCORE = 1.0
"""A resolved collector line is ``(set_code, collector_number)`` -- the natural key of
a printing. It is not evidence *towards* an answer, it is the answer, so on its own it
reaches the lock threshold."""

COLLECTOR_NEAR_MISS_SCORE = 0.45
"""A collector line whose set code had to be *guessed* (one character corrected) names
a printing but is not the key read off the card. Enough for the picker, never enough
to lock alone -- a garbled ``LTR`` read as ``EVES`` once resolved to a real (set,
number) pair and locked a completely different card. Artwork or name agreement has to
push it over the line."""

VISUAL_FLOOR = 0.45
VISUAL_CEILING = 1.0
SHARED_ART_CEILING = 0.85
"""What a hash match can be worth when other printings reuse the same artwork.

Below :data:`LOCK_THRESHOLD` on purpose, so the artwork cannot lock a printing by
itself. What stops it doing so *in combination* with a name is
:attr:`ScoredPrinting.printing_certain`, not this number -- both signals answer "which
card", and no amount of agreement between them answers "which printing".

The match is still strong evidence: it is the right picture. Scoring it highly is what
ranks the siblings to the top of the picker, which is exactly the question worth
putting to the user -- not "which card is this" but "which of these"."""
VISUAL_SATURATION_Z = 12.0
"""A visual match scores between the floor and the ceiling as its z-score rises from
:data:`MIN_Z_SCORE` to here. At saturation the artwork is unmistakable and locks in
alone, which is how a pre-2015 card with no collector line gets scanned at all."""

DECISIVE_SEPARATION = 3.0
DECISIVE_MIN_Z = 6.0
"""How far clear of the runner-up the best match must stand to be treated as certain.

The absolute z-score alone is a poor judge. A frame with no card in it produces a
cluster of weak, near-equal matches -- 5.3, 5.0, 4.9, 4.7 -- which says nothing except
that the query resembles the whole database slightly. A real card produces one match
standing well clear of the field: 10.0 against a runner-up of 4.7. The *shape* of the
result set is the signal, and reading it is what lets a genuine match lock in on one
frame instead of asking the user to pick from a list."""

PRINT_YEAR_SCORE = 0.35
"""What the copyright year adds to a printing the artwork already proposed.

The same weight as the set symbol, and for the same reason: a year names a dozen sets,
so it identifies nothing on its own and is decisive only as a choice between candidates
already narrowed. It is counted only when exactly one candidate was printed that year."""

SYMBOL_SCORE = 0.35
"""What the set symbol adds to a printing the artwork already proposed.

Small on its own, because a symbol names a *set* and thousands of cards share one. It
is only ever consulted to choose between printings the artwork has already narrowed to,
and in that role it is decisive: enough, on top of a shared-artwork match, to clear the
lock threshold."""

NAME_CONFIDENT_SCORE = 0.55
NAME_AMBIGUOUS_SCORE = 0.30
"""Deliberately below the lock threshold. A confident name match identifies the *card*
but never the *printing*, and over-trusting it is what produced a picker full of
near-identical names. It needs corroboration -- from the artwork, the corner, or
simply from a second frame agreeing."""

EVIDENCE_TTL_S = 6.0
"""How long a frame's evidence keeps counting. Long enough to span the pauses of a
hand-held scan, short enough that the next card off the stack starts clean."""

MAX_TRACKED = 32
"""Printings tracked per session. The accumulator only ever holds the handful of
candidates recent frames proposed."""


@dataclass
class Evidence:
    """Everything one rectified candidate yielded."""

    visual: list[VisualHit] = field(default_factory=list)
    collector: CollectorIdentity = field(default_factory=CollectorIdentity)
    collector_card_id: int | None = None
    collector_near_miss: bool = False
    """The set code was corrected, not read: the resolution is evidence, not the key."""
    symbol_card_id: int | None = None
    """The printing the set symbol picked out from among the artwork's candidates."""
    year_card_id: int | None = None
    """The printing the copyright year picked out from among the artwork's candidates."""
    symbol_verdict: object | None = None
    """The full symbol comparison, kept for the scan event so the thresholds governing
    it can be calibrated against real scans rather than against degraded references."""
    name: NameMatch | None = None
    name_card_ids: dict[int, float] = field(default_factory=dict)
    """Printing id -> the name score that backs it, already resolved from oracle ids."""
    ocr_text: str = ""


@dataclass
class ScoredPrinting:
    """One printing and the case for it."""

    card_id: int
    score: float
    reasons: list[str] = field(default_factory=list)
    printing_certain: bool = False
    """Whether any signal here can name the *printing*, as opposed to the card.

    The collector line can, always. The artwork can only when no other printing reuses
    it. The card name never can. A high score without this is a confident answer to a
    different question -- "which card is this" -- and locking on it is how a Forest
    scanned from a 2010 core set was filed under a World Championship deck."""

    @property
    def confident(self) -> bool:
        """Whether this alone justifies locking in."""
        return self.score >= LOCK_THRESHOLD and self.printing_certain


def visual_score(hit: VisualHit, runner_up_z: float | None = None) -> float:
    """Map a hash match's z-score, and its lead over the field, onto the scoring scale.

    Args:
        hit: The match being scored.
        runner_up_z: z-score of the next-best printing with *different* artwork, when
            known. Siblings are excluded because beating them is not an achievement the
            hash is capable of; see :data:`SHARED_ART_CEILING`.
    """
    if hit.z_score < MIN_Z_SCORE:
        return 0.0

    if hit.art_shared:
        # However unmistakable the artwork, it is worn by several printings. Scoring
        # this to the ceiling would let art plus a name -- two signals that both answer
        # "which card" -- add up to a lock on "which printing".
        span = max(VISUAL_SATURATION_Z - MIN_Z_SCORE, 1e-6)
        fraction = min(1.0, (hit.z_score - MIN_Z_SCORE) / span)
        return VISUAL_FLOOR + (SHARED_ART_CEILING - VISUAL_FLOOR) * fraction

    if (
        runner_up_z is not None
        and hit.z_score >= DECISIVE_MIN_Z
        and hit.z_score - runner_up_z >= DECISIVE_SEPARATION
    ):
        return VISUAL_CEILING

    span = max(VISUAL_SATURATION_Z - MIN_Z_SCORE, 1e-6)
    fraction = min(1.0, (hit.z_score - MIN_Z_SCORE) / span)
    return VISUAL_FLOOR + (VISUAL_CEILING - VISUAL_FLOOR) * fraction


def score_evidence(evidence: Evidence) -> list[ScoredPrinting]:
    """Turn one candidate's signals into scored printings, best first.

    Reasons name the *signal*, not the observation. Including the score made
    every frame's reason a different string, so a card seen three times
    accumulated "artwork match z=5.1 + artwork match z=4.6 + ..." and read on
    screen like a malfunction. The scores are kept where they are useful: the
    scan event's detail, and the log.
    """
    scores: dict[int, float] = {}
    reasons: dict[int, list[str]] = {}
    certain: set[int] = set()

    def add(card_id: int, amount: float, reason: str) -> None:
        if amount <= 0:
            return
        scores[card_id] = scores.get(card_id, 0.0) + amount
        reasons.setdefault(card_id, []).append(reason)

    if evidence.collector_card_id is not None:
        identity = evidence.collector
        if evidence.collector_near_miss:
            # The set code was guessed. Strong evidence -- picker on its own,
            # a lock only when artwork or the name agrees -- and never
            # printing-certain (ADR-027: only a verbatim line or unique art
            # can lock a printing).
            add(
                evidence.collector_card_id,
                COLLECTOR_NEAR_MISS_SCORE,
                f"collector line {identity.set_code}?/{identity.collector_number} "
                "(set code guessed)",
            )
        else:
            add(
                evidence.collector_card_id,
                COLLECTOR_SCORE,
                f"collector line {identity.set_code}/{identity.collector_number}",
            )
            # (set_code, collector_number) is the natural key of a printing, so this
            # is the one signal that answers the printing question outright.
            certain.add(evidence.collector_card_id)

    for position, hit in enumerate(evidence.visual):
        runner_up = _rival_z(evidence.visual, position)
        add(hit.card_id, visual_score(hit, runner_up), "artwork")
        if not hit.art_shared:
            certain.add(hit.card_id)

    if evidence.symbol_card_id is not None:
        # Only ever reached for a printing the artwork already proposed, so this is a
        # choice among candidates rather than an identification of its own.
        add(evidence.symbol_card_id, SYMBOL_SCORE, "set symbol")
        certain.add(evidence.symbol_card_id)

    if evidence.year_card_id is not None:
        add(evidence.year_card_id, PRINT_YEAR_SCORE, "printed year")
        certain.add(evidence.year_card_id)

    for card_id, name_score in evidence.name_card_ids.items():
        add(card_id, name_score, "card name")

    ranked = [
        ScoredPrinting(
            card_id=card_id,
            score=score,
            reasons=reasons.get(card_id, []),
            printing_certain=card_id in certain,
        )
        for card_id, score in scores.items()
    ]
    return sorted(ranked, key=lambda item: -item.score)


def _rival_z(hits: list[VisualHit], position: int) -> float | None:
    """The z-score the hit at ``position`` has to beat to be called decisive.

    The best hit with *different* artwork, because siblings share the artwork exactly
    and out-scoring them measures the lighting, not the card. Only the leading hit is
    scored against a rival at all; the rest are ranked on their own z-scores.
    """
    if position != 0:
        return None
    leader = hits[0]
    return next(
        (hit.z_score for hit in hits[1:] if hit.art_id != leader.art_id or hit.art_id < 0),
        None,
    )


def name_score_for(match: NameMatch | None) -> float:
    """Score contributed by a fuzzy name match."""
    if match is None or match.best is None:
        return 0.0
    if match.confident:
        return NAME_CONFIDENT_SCORE
    if match.ambiguous:
        return NAME_AMBIGUOUS_SCORE
    return 0.0


@dataclass
class _Tracked:
    score: float
    reasons: list[str]
    updated_at: float
    printing_certain: bool = False
    """Sticky within the evidence window.

    Certainty comes from a signal that was actually observed -- a collector line read on
    frame two -- and a later frame that could not read it does not un-read it. Without
    this the flag would be lost the moment scores were accumulated, and nothing would
    ever lock in."""


class Accumulator:
    """Per-session evidence, decaying over a few seconds.

    Single-process by design: the app runs one uvicorn worker, and the alternative --
    round-tripping partial evidence through the database on every frame -- would cost
    more than it could possibly save for a single user scanning a stack of cards.
    Single-process is not single-*thread*, though: frames run in the threadpool up
    to ``SCAN_MAX_CONCURRENCY`` wide, so every mutation holds the lock.
    """

    def __init__(self, ttl_s: float = EVIDENCE_TTL_S) -> None:
        self._ttl = ttl_s
        self._sessions: dict[str, dict[int, _Tracked]] = {}
        self._lock = threading.Lock()

    def clear(self, session_key: str) -> None:
        """Forget a session's evidence, on lock-in or when the card leaves frame."""
        with self._lock:
            self._sessions.pop(session_key, None)

    def reset(self) -> None:
        """Forget everything. Tests use this between cases."""
        with self._lock:
            self._sessions.clear()

    def _sweep_dormant(self, now: float) -> None:
        """Drop sessions whose newest evidence is long stale.

        ``_prune`` only runs for the session being added to; a session abandoned
        mid-scan would otherwise hold its entries for the life of the process.
        Caller holds the lock.
        """
        for key in list(self._sessions):
            entries = self._sessions[key]
            if not entries or all(
                now - entry.updated_at > 10 * self._ttl for entry in entries.values()
            ):
                del self._sessions[key]

    def _prune(self, tracked: dict[int, _Tracked], now: float) -> None:
        for card_id, entry in list(tracked.items()):
            if now - entry.updated_at > self._ttl:
                del tracked[card_id]
        if len(tracked) > MAX_TRACKED:
            for card_id, _entry in sorted(tracked.items(), key=lambda item: item[1].score)[
                : len(tracked) - MAX_TRACKED
            ]:
                del tracked[card_id]

    def add(self, session_key: str, scored: list[ScoredPrinting]) -> list[ScoredPrinting]:
        """Fold one frame's scores in and return the running totals, best first.

        Args:
            session_key: Scan session id, or any stable key for one scanning run.
            scored: What this frame concluded.

        Returns:
            Accumulated scores across the live evidence window, best first.
        """
        now = time.monotonic()
        with self._lock:
            self._sweep_dormant(now)
            tracked = self._sessions.setdefault(session_key, {})
            self._prune(tracked, now)

            for item in scored:
                entry = tracked.get(item.card_id)
                if entry is None:
                    tracked[item.card_id] = _Tracked(
                        score=item.score,
                        reasons=list(item.reasons),
                        updated_at=now,
                        printing_certain=item.printing_certain,
                    )
                    continue
                entry.score += item.score
                entry.updated_at = now
                entry.printing_certain = entry.printing_certain or item.printing_certain
                for reason in item.reasons:
                    if reason not in entry.reasons:
                        entry.reasons.append(reason)

            return sorted(
                (
                    ScoredPrinting(
                        card_id=card_id,
                        score=entry.score,
                        reasons=list(entry.reasons),
                        printing_certain=entry.printing_certain,
                    )
                    for card_id, entry in tracked.items()
                ),
                key=lambda item: -item.score,
            )


_accumulator = Accumulator()


def get_accumulator() -> Accumulator:
    """The process-wide evidence accumulator."""
    return _accumulator
