"""Resolve a printing from the collector line, without fuzzy name matching.

``(set_code, collector_number, lang)`` is the natural key of the ``cards`` table
(ADR-006), and both halves are printed on the card. When they can be read, this path
answers "which printing is this?" with an index lookup instead of a shortlist, which
is why it runs before name OCR rather than after it.

The one thing that has to be forgiving is the set code itself: three small capitals
are exactly where OCR slips, and ``FIN`` misread as ``FLN`` would otherwise turn a
certain answer into a miss. So the code is snapped to the nearest real set in the
database before the lookup, and a snap that is not close enough simply fails and
lets the name path take over.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass

from rapidfuzz import fuzz, process
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import Card
from app.services.scan.identifiers import CollectorIdentity, collector_variants

log = logging.getLogger("mtgvault.scan.exact")

SET_CODE_CUTOFF = 60.0
"""How close an OCR'd set code must be to a real one. At three characters a single
substitution scores about 67, so this admits one slipped character and rejects two.

On its own that would be far too loose -- with hundreds of sets in the database,
plenty of three-letter codes are one substitution apart. What makes it safe is that a
near miss is never accepted on its own: the collector number has to land in exactly
one of the candidate sets, and an ambiguity falls through to the name path instead of
picking a side."""

NEAR_MISS_LIMIT = 8
"""How many near-miss set codes to cross-check against the collector number."""


_cached_codes: list[str] | None = None
_cached_signature: int | None = None


def _signature(db: DbSession) -> int:
    """Cheap staleness check: the number of distinct set codes."""
    return int(db.scalar(select(func.count(func.distinct(Card.set_code)))) or 0)


def set_codes(db: DbSession) -> list[str]:
    """Every set code present in the card data, cached until an import changes it."""
    global _cached_codes, _cached_signature
    signature = _signature(db)
    if _cached_codes is None or _cached_signature != signature:
        _cached_codes = [row for row in db.scalars(select(Card.set_code).distinct()) if row]
        _cached_signature = signature
        log.info("set_code_index_built", extra={"entries": len(_cached_codes)})
    return _cached_codes


def reset_index() -> None:
    """Drop the cached set-code list. Tests and the bulk importer use this."""
    global _cached_codes, _cached_signature
    _cached_codes = None
    _cached_signature = None


def correct_set_code(db: DbSession, code: str | None) -> str | None:
    """Return ``code`` if it names a real set, else ``None``. No guessing."""
    if not code:
        return None
    lowered = code.lower()
    return lowered if lowered in set_codes(db) else None


def near_miss_set_codes(db: DbSession, code: str | None) -> list[str]:
    """Real set codes within about one slipped character of ``code``."""
    if not code:
        return []
    candidates = set_codes(db)
    if not candidates:
        return []
    hits = process.extract(
        code.lower(),
        candidates,
        scorer=fuzz.ratio,
        limit=NEAR_MISS_LIMIT,
        score_cutoff=SET_CODE_CUTOFF,
    )
    return [str(choice) for choice, _score, _position in hits]


def _printings_in(db: DbSession, set_code: str, numbers: list[str]) -> list[Card]:
    """Every printing in ``set_code`` matching any of the collector-number spellings."""
    from app.models.cards import scannable_clause

    for number in numbers:
        rows = list(
            db.scalars(
                select(Card).where(
                    Card.set_code == set_code,
                    Card.collector_number == number,
                    # A near-miss set-code guess must never land on a token or
                    # placeholder printing -- nothing scannable lives there.
                    scannable_clause(),
                )
            )
        )
        if rows:
            return rows
    return []


def _prefer_language(rows: list[Card], lang: str) -> Card:
    """Collector numbers repeat across languages; the key includes lang for a reason."""
    for row in rows:
        if row.lang == lang:
            return row
    return rows[0]


@dataclass(frozen=True)
class ExactMatch:
    """A collector-line resolution, and how much to trust it.

    ``near_miss=True`` means the set code was *guessed* -- corrected by one
    character -- so this names a printing but is not the natural key read off
    the card. The fusion layer scores it as strong evidence, never as the
    outright answer: a garbled ``LTR`` read as ``EVES`` once locked the wrong
    card entirely on its own.
    """

    card: Card
    near_miss: bool = False


def lookup_exact(
    db: DbSession, identity: CollectorIdentity, *, lang: str = "en"
) -> ExactMatch | None:
    """Find the single printing a collector line names.

    Two passes. The first trusts only set codes that exist verbatim, which is the
    common case and costs one indexed query. The second admits codes one character
    away, but accepts the result only when the collector number lands in exactly one
    of them -- and marks it :attr:`ExactMatch.near_miss`, because a guessed set code
    must not lock a printing without corroboration. When the line also carried a
    copyright year, a near-miss printing from a different era is rejected outright.

    Args:
        db: Open database session.
        identity: What was read from the bottom-left corner.
        lang: Preferred language when a collector number resolves to several
            printings, so a foreign card still lands in the right slot.

    Returns:
        The printing with its trust level, or ``None`` if the line does not
        resolve to exactly one.
    """
    if not identity.is_exact:
        return None

    numbers = collector_variants(identity)
    # Every token the parser thought could be a set code: the copyright notice under
    # the collector line supplies plenty of three-letter words, and checking them
    # against the sets that exist is a cheaper filter than blacklisting English.
    raw_codes = identity.set_code_candidates or ((identity.set_code,) if identity.set_code else ())

    for raw_code in raw_codes:
        set_code = correct_set_code(db, raw_code)
        if set_code is None:
            continue
        rows = _printings_in(db, set_code, numbers)
        if rows:
            return ExactMatch(_prefer_language(rows, lang))

    resolved: list[Card] = []
    seen: set[str] = set()
    for raw_code in raw_codes:
        for set_code in near_miss_set_codes(db, raw_code):
            if set_code in seen:
                continue
            seen.add(set_code)
            rows = _printings_in(db, set_code, numbers)
            if identity.print_year is not None:
                # A copy's copyright year sits within a year of its printing's
                # release; a 2023 line cannot belong to a 2008 set the guesser
                # dreamt up.
                rows = [row for row in rows if _year_plausible(row, identity.print_year)]
            if rows:
                resolved.append(_prefer_language(rows, lang))

    if len(resolved) == 1:
        return ExactMatch(resolved[0], near_miss=True)
    if resolved:
        log.info(
            "collector_near_miss_ambiguous",
            extra={"code": identity.set_code, "matches": len(resolved)},
        )
    return None


def _year_plausible(card: Card, print_year: int) -> bool:
    """Whether a printing's release year fits the copyright year on the copy."""
    if not card.released_at:
        return True
    try:
        released = int(str(card.released_at)[:4])
    except ValueError:
        return True
    return abs(released - print_year) <= 1
