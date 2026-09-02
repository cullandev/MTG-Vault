"""Fuzzy matching of OCR output against the card name index.

This is where a scanner is won or lost. OCR of a title bar is never clean: it drops
diacritics, turns ``rn`` into ``m``, reads ``I`` as ``l`` or ``1``, and trails off into
punctuation picked out of the frame border. The matcher has to be forgiving enough to
absorb all of that, and strict enough that ``Lightning Bolt`` never comes back when the
card on the mat is ``Lightning Blast``.

Three bands, from :mod:`app.config`:

* score >= ``scan_accept_score``  -> confident, the scanner can lock in
* ``scan_ambiguous_score`` .. accept -> show the user a picker
* below that -> a miss; the scanner keeps looking

Double-faced, flip and adventure cards are indexed under their **front-face** name as
well as their full name, because that is what is printed on the card the camera can
see -- and it is also what decklists and CSV exports use.
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass, field

from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.config import Settings
from app.models import OracleCard
from app.util.text import normalize_name

log = logging.getLogger("mtgvault.scan.matching")

MAX_CANDIDATES = 8

# Layouts that are not the card someone is scanning into a collection, but which carry
# a *real card's name* and therefore collide with it in the index. Scryfall lists 2 243
# art-series cards, 910 tokens, 87 emblems and 80 double-faced tokens; without this,
# every one of them makes a perfectly unique card name look ambiguous, and the scanner
# shows a picker with the same name twice -- which is exactly what it did.
#
# They are not dropped: an art card is a real object someone may own. They are ranked
# below the actual card, and they do not by themselves make a name ambiguous.
SUPPLEMENTAL_LAYOUTS = frozenset({"art_series", "token", "double_faced_token", "emblem"})

# OCR frequently emits a leading or trailing fragment picked out of the card border.
# Anything shorter than this is not worth matching against a 30 000 entry index.
MIN_QUERY_LENGTH = 3

_JUNK_EDGES = re.compile(r"^[^a-z0-9]+|[^a-z0-9]+$")


@dataclass
class NameCandidate:
    """One plausible reading of the title bar."""

    oracle_id: str
    name: str
    score: float


@dataclass
class NameMatch:
    """The outcome of matching one piece of OCR text."""

    query: str
    best: NameCandidate | None = None
    candidates: list[NameCandidate] = field(default_factory=list)
    confident: bool = False
    ambiguous: bool = False

    @property
    def score(self) -> float:
        """Score of the best candidate, or zero when nothing matched."""
        return self.best.score if self.best else 0.0


@dataclass
class NameIndex:
    """Normalised card names, ready for fuzzy lookup."""

    choices: list[str]
    owners: dict[str, list[str]]
    """normalised name -> oracle ids that answer to it."""
    display: dict[str, str]
    """normalised name -> the name as printed."""
    primary_counts: dict[str, int]
    """normalised name -> how many *non-supplemental* oracle cards answer to it. This,
    not the raw owner count, is what decides ambiguity."""
    signature: tuple[int, str]
    """``(row count, max updated_at)``; a change means the index is stale."""

    def __len__(self) -> int:
        """Number of distinct normalised names."""
        return len(self.choices)


_cached_index: NameIndex | None = None


def _index_signature(db: DbSession) -> tuple[int, str]:
    count, newest = db.execute(
        select(func.count(OracleCard.oracle_id), func.max(OracleCard.updated_at))
    ).one()
    return int(count or 0), str(newest or "")


def build_index(db: DbSession) -> NameIndex:
    """Build the name index from ``oracle_cards``.

    At ~30 000 cards this is a couple of megabytes and takes well under a second, so it
    is built once and reused until a bulk import changes the signature.
    """
    choices: list[str] = []
    owners: dict[str, list[str]] = {}
    display: dict[str, str] = {}

    primary_counts: dict[str, int] = {}
    supplemental: dict[str, list[str]] = {}

    rows = db.execute(
        select(
            OracleCard.oracle_id,
            OracleCard.name,
            OracleCard.name_norm,
            OracleCard.name_front,
            OracleCard.name_front_norm,
            OracleCard.layout,
        )
    ).all()

    for oracle_id, name, name_norm, name_front, front_norm, layout in rows:
        is_supplemental = (layout or "") in SUPPLEMENTAL_LAYOUTS
        for normalised, printed in ((name_norm, name), (front_norm, name_front)):
            if not normalised:
                continue
            # Supplemental entries are held back and appended after every real card,
            # so owners[name][0] is always the card someone means.
            bucket = (
                supplemental.setdefault(normalised, [])
                if is_supplemental
                else (owners.setdefault(normalised, []))
            )
            if oracle_id not in bucket:
                bucket.append(oracle_id)
                if not is_supplemental:
                    primary_counts[normalised] = primary_counts.get(normalised, 0) + 1
            display.setdefault(normalised, printed)

    for normalised, extra in supplemental.items():
        bucket = owners.setdefault(normalised, [])
        bucket.extend(oracle_id for oracle_id in extra if oracle_id not in bucket)

    choices = list(owners)
    index = NameIndex(
        choices=choices,
        owners=owners,
        display=display,
        primary_counts=primary_counts,
        signature=_index_signature(db),
    )
    log.info("name_index_built", extra={"entries": len(choices)})
    return index


def get_index(db: DbSession, *, force: bool = False) -> NameIndex:
    """Return the cached name index, rebuilding it when the card data has changed."""
    global _cached_index
    signature = _index_signature(db)
    if force or _cached_index is None or _cached_index.signature != signature:
        _cached_index = build_index(db)
    return _cached_index


def reset_index() -> None:
    """Drop the cached index. Tests and the bulk importer use this."""
    global _cached_index
    _cached_index = None


def clean_ocr_text(raw: str) -> str:
    """Normalise OCR output into something worth looking up.

    Beyond the shared normalisation this strips the punctuation fragments OCR picks
    out of the card border, which would otherwise drag every score down.
    """
    normalised = normalize_name(raw)
    return _JUNK_EDGES.sub("", normalised).strip()


def match_name(
    db: DbSession, raw_text: str, settings: Settings, *, limit: int = MAX_CANDIDATES
) -> NameMatch:
    """Match OCR text against the card name index.

    Args:
        db: Open database session.
        raw_text: Whatever the OCR engine read.
        settings: Supplies the accept and ambiguity thresholds.
        limit: Maximum candidates to return.

    Returns:
        A :class:`NameMatch`. ``confident`` means the scanner may lock in;
        ``ambiguous`` means show the user a picker; neither means keep looking.
    """
    query = clean_ocr_text(raw_text)
    if len(query) < MIN_QUERY_LENGTH:
        return NameMatch(query=query)

    index = get_index(db)
    if not index.choices:
        return NameMatch(query=query)

    # An exact hit on the normalised name is by far the common case and skips the
    # fuzzy pass entirely.
    if query in index.owners:
        best = NameCandidate(
            oracle_id=index.owners[query][0], name=index.display[query], score=100.0
        )
        others = [
            NameCandidate(oracle_id=oracle_id, name=index.display[query], score=100.0)
            for oracle_id in index.owners[query][1:]
        ]
        # One real card plus its tokens and art cards is not an ambiguity.
        rivals = index.primary_counts.get(query, len(index.owners[query]))
        return NameMatch(
            query=query,
            best=best,
            candidates=[best, *others][:limit],
            confident=rivals <= 1,
            ambiguous=rivals > 1,
        )

    # WRatio handles both "OCR dropped a letter" and "OCR appended half the type line"
    # without the partial-ratio failure mode where a short name matches everything.
    raw_hits = process.extract(
        query,
        index.choices,
        scorer=fuzz.WRatio,
        limit=limit,
        score_cutoff=float(settings.scan_ambiguous_score) - 10,
    )

    candidates: list[NameCandidate] = []
    for choice, score, _position in raw_hits:
        for oracle_id in index.owners[choice]:
            candidates.append(
                NameCandidate(oracle_id=oracle_id, name=index.display[choice], score=float(score))
            )
    candidates = candidates[:limit]

    if not candidates:
        return NameMatch(query=query)

    best = candidates[0]
    # The demotion below exists for *different* names scoring alike (Lightning
    # Bolt / Lightning Blast). Another owner of the same name -- a token or art
    # card twin -- scores identically by construction and is not a rival name,
    # exactly as the exact-hit path already treats it via primary_counts.
    runner_up = next(
        (candidate.score for candidate in candidates[1:] if candidate.name != best.name),
        0.0,
    )
    confident = best.score >= settings.scan_accept_score
    if confident and best.score - runner_up < 2.0 and runner_up > 0.0:
        confident = False

    return NameMatch(
        query=query,
        best=best,
        candidates=candidates,
        confident=confident,
        ambiguous=not confident and best.score >= settings.scan_ambiguous_score,
    )
