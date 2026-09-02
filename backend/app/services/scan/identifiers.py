"""Read a card's identity from the collector line printed on it.

Every card printed since Magic 2015 carries, in the bottom-left corner:

    0028/281 R
    FIN · EN · Some Artist

That is an *exact* identifier. ``(set_code, collector_number)`` is the natural key
this application already stores collections against (ADR-006), so reading those two
values turns identification from a fuzzy name match into a primary-key lookup — no
guessing between similar names, and no waiting for several frames to agree.

Name OCR remains the fallback: cards printed before 2015 have no collector line at
all, and a glare-blown corner still has to resolve to something.

Those older cards do carry one thing in the same corner, and it turns out to be worth
reading. Where the collector number would be, they print the artist and a copyright
notice::

    Dermot Power
    (TM) & (C) 1993-2010 Wizards of the Coast

**The second year is the year that printing was made**, and it matches its set's
release year. That is not an exact identifier -- several sets ship in a year -- but
combined with the handful of printings the artwork has already proposed it is usually
decisive, and it is the only thing separating the core sets: Gravedigger's M10, M11 and
M12 printings share an artwork, and their set symbols are three variations on a
stylised M that measure fourteen bits apart, well inside photographic noise (ADR-029).
"""

from __future__ import annotations

import logging
import re
from dataclasses import dataclass

from app.models import utctoday

log = logging.getLogger("mtgvault.scan.identifiers")

# "0028/281" and "28/281". The first group admits the characters OCR substitutes
# for digits -- O for 0, I/l for 1, S for 5, B for 8 -- because discarding the whole
# line over one substituted character is exactly the failure this path exists to
# prevent. It is translated back to digits below, and a group holding no digit at
# all is rejected so a stray word before a slash cannot become a collector number.
_NUMBER_OVER_TOTAL = re.compile(r"([0-9OoIlSB]{1,4})\s*[/\\|]\s*(\d{1,4})")

# Cards printed since about 2021, and every Universes Beyond set, print the collector
# number *without* a total: "0001", not "0001/321". Requiring the slash meant those
# cards -- an ever-growing share of them -- could never be identified from the corner
# at all, even when OCR read the number perfectly.
_STANDALONE_NUMBER = re.compile(r"\b([0-9OoIlSB]{3,4})\b")

_MIN_REAL_DIGITS = 2
"""How many characters of a standalone candidate must be actual digits.

Without this the confusable-character class eats set codes: "BOS" is three characters
all of which OCR might have meant as digits, and would translate to "805"."""


# A set code is three to five characters, letters or digits, standing alone. Real
# examples: FIN, LTR, 2X2, MH3, PLST. Two-character tokens are admitted as well,
# because OCR drops the last letter of a short code often enough to matter -- "OTJ"
# read as "OT" is a real observed failure. No real set code is two characters, so a
# token that short can never match verbatim; it only ever reaches the near-miss pass,
# where the collector number still has to single out one set. Rarity letters
# (C/U/R/M) are one character and excluded by the length rule.
_SET_CODE = re.compile(r"\b([A-Z0-9]{2,5})\b")

# Language codes that appear on the same line as the set code and must not be
# mistaken for it.
_LANGUAGES = frozenset(
    {"EN", "DE", "FR", "IT", "ES", "PT", "JA", "JP", "KO", "RU", "ZHS", "ZHT", "PH"}
)

# Words Tesseract commonly lifts out of the copyright line, which sits directly
# below the set code and is not part of the identity.
_NOISE = frozenset(
    {
        "WIZARDS",
        "COAST",
        "HASBRO",
        "AND",
        "LLC",
        "INC",
        "NOT",
        "FOR",
        "SALE",
        "THE",
        "OF",
        "ALL",
        "RIGHTS",
        "RESERVED",
    }
)

# The copyright notice's year range. Both years are captured because the first is
# always 1993 -- the year Magic was published, printed on every card ever made -- and
# it is the *second* that says when this copy was printed.
_YEAR = r"(19[0-9]{2}|20[0-9]{2})"

# The separator is spelled by codepoint: the notice is typeset with an en dash, and OCR
# returns an em dash or an underscore about as often as a hyphen.
_YEAR_SEPARATOR = "[-" + chr(0x2013) + chr(0x2014) + "_]?"

_COPYRIGHT_YEARS = re.compile(rf"\b{_YEAR}\s*{_YEAR_SEPARATOR}\s*{_YEAR}\b")

_STANDALONE_YEAR = re.compile(r"\b(19[0-9]{2}|20[0-9]{2})\b")

MIN_PRINT_YEAR = 1993
"""Magic was first published in 1993; nothing was printed before it."""


def max_print_year() -> int:
    """The latest year a card could have been printed in.

    Next year rather than this one, since sets are printed slightly ahead of release.
    Computed rather than hard-coded so it does not quietly go stale and start rejecting
    real cards.

    This ceiling does real work: a scan of a 2011 card came back as "2071", and without
    it that reading would have excluded every genuine candidate instead of being thrown
    away as unreadable.
    """
    return int(utctoday()[:4]) + 1


# OCR confusions that matter in a field that is mostly digits.
_DIGIT_FIXES = str.maketrans({"O": "0", "o": "0", "l": "1", "I": "1", "|": "1", "S": "5", "B": "8"})


@dataclass(frozen=True)
class CollectorIdentity:
    """What could be read from the collector line."""

    collector_number: str | None = None
    set_code: str | None = None
    """Best guess. Prefer :attr:`set_code_candidates` when a real set list is to hand."""
    set_code_candidates: tuple[str, ...] = ()
    """Every token that could be the set code, in reading order. The copyright notice
    under the collector line is full of three-letter words, so the lookup checks these
    against the sets that actually exist rather than trusting the first one."""
    total: int | None = None
    rarity: str | None = None
    print_year: int | None = None
    """The later year of the copyright notice: when this copy was printed.

    Only useful alongside candidates, since a year names a dozen sets rather than one.
    Against the printings one artwork appears in, it is usually decisive."""
    raw: str = ""

    @property
    def is_exact(self) -> bool:
        """Whether this is enough to look up a single printing."""
        return bool(self.collector_number and self.set_code)


def _normalise_number(raw: str) -> str:
    """Strip the zero padding Magic prints, so ``0028`` matches Scryfall's ``28``."""
    digits = raw.translate(_DIGIT_FIXES)
    stripped = digits.lstrip("0")
    return stripped or "0"


def parse_collector_line(text: str) -> CollectorIdentity:
    """Extract the collector number and set code from OCR of the bottom-left corner.

    Args:
        text: Raw OCR output, which typically spans two or three lines.

    Returns:
        Whatever could be read. ``is_exact`` says whether it is enough to identify
        a printing outright.
    """
    if not text or not text.strip():
        return CollectorIdentity()

    upper = text.upper()
    collector_number: str | None = None
    total: int | None = None

    for haystack in (upper.replace(" ", ""), upper):
        for match in _NUMBER_OVER_TOTAL.finditer(haystack):
            if not any(character.isdigit() for character in match.group(1)):
                continue
            collector_number = _normalise_number(match.group(1))
            try:
                total = int(match.group(2))
            except ValueError:
                total = None
            break
        if collector_number is not None:
            break

    number_span: tuple[int, int] | None = None
    if collector_number is None:
        # No total printed: take the first standalone run that is mostly digits.
        for match in _STANDALONE_NUMBER.finditer(upper):
            token = match.group(1)
            if sum(character.isdigit() for character in token) < _MIN_REAL_DIGITS:
                continue
            # Pre-2015 corners carry "(TM) & (C) 1993-2010 Wizards..."; a bare
            # 4-digit copyright year is never a collector number worth risking a
            # wrong exact match on (real 4-digit numbers exist only in novelty
            # sets, and those print a total alongside).
            if len(token) == 4 and token.isdigit() and 1990 <= int(token) <= 2035:
                continue
            collector_number = _normalise_number(token)
            number_span = match.span(1)
            break

    # Every standalone 3-5 character token that is not a language code, a bare
    # number (that is the print run total) or a word out of the copyright notice.
    # The number-over-total is removed first: a mis-OCR'd "OO28/281" leaves "OO28"
    # sitting there looking exactly like a set code.
    residue = _NUMBER_OVER_TOTAL.sub(" ", upper)
    if number_span is not None:
        # Blank the standalone number too, so it cannot also be read as a set code.
        residue = (
            residue[: number_span[0]]
            + " " * (number_span[1] - number_span[0])
            + residue[number_span[1] :]
        )
    candidates: list[str] = []
    for candidate in _SET_CODE.findall(residue):
        if candidate in _LANGUAGES or candidate in _NOISE or candidate.isdigit():
            continue
        lowered = candidate.lower()
        if lowered not in candidates:
            candidates.append(lowered)
    set_code = candidates[0] if candidates else None

    rarity = None
    rarity_match = re.search(r"\b([CURMSTL])\b(?!\w)", upper)
    if rarity_match:
        rarity = rarity_match.group(1)

    return CollectorIdentity(
        collector_number=collector_number,
        set_code=set_code,
        set_code_candidates=tuple(candidates),
        total=total,
        rarity=rarity,
        print_year=parse_print_year(upper),
        raw=text.strip()[:120],
    )


def parse_print_year(text: str) -> int | None:
    """Read the printing year out of a copyright notice.

    Args:
        text: OCR of the bottom-left corner, in any case.

    Returns:
        The later year of a ``1993-2010`` range, or a lone plausible year. ``None`` when
        nothing readable is there.

    A misread year is worse than no year: it excludes the right printing rather than
    merely failing to choose one. So anything outside the years in which Magic cards
    have existed is discarded rather than repaired -- OCR turns "2011" into "2071"
    readily, and nothing distinguishes that from a real 2071 except knowing the game is
    not that old.
    """
    if not text:
        return None
    upper = text.upper()

    ranged = _COPYRIGHT_YEARS.search(upper)
    if ranged:
        later = max(int(ranged.group(1)), int(ranged.group(2)))
        return later if MIN_PRINT_YEAR <= later <= max_print_year() else None

    # No range: a year on its own. 1993 is discarded -- it opens the copyright notice on
    # every card ever printed, so it says nothing about this one.
    for match in _STANDALONE_YEAR.finditer(upper):
        year = int(match.group(1))
        if MIN_PRINT_YEAR < year <= max_print_year():
            return year
    return None


def collector_variants(identity: CollectorIdentity) -> list[str]:
    """Collector-number spellings to try against the database, best first.

    Scryfall stores collector numbers unpadded (``28``), but promos and variants
    carry suffixes (``28a``, ``★``) that OCR may or may not catch, so the padded
    form is tried too rather than assuming one convention.
    """
    if not identity.collector_number:
        return []
    number = identity.collector_number
    variants = [number]
    for width in (2, 3, 4):
        padded = number.zfill(width)
        if padded not in variants:
            variants.append(padded)
    return variants


def narrow_by_print_year(
    released_at: dict[int, str | None], candidates: list[int], year: int | None
) -> int | None:
    """Choose among candidate printings using the year printed on the card.

    Args:
        released_at: Candidate printing id -> its set's release date, ISO.
        candidates: Printing ids the artwork proposed, best first.
        year: The year read from the copyright notice.

    Returns:
        The single candidate printed that year, or ``None``.

    Only a *unique* survivor counts. Two candidates from the same year means the year
    has not chosen between them -- Zendikar and Magic 2010 both shipped in 2009 -- and
    picking the better-ranked one would be the artwork deciding again under a new name.
    """
    if year is None or not candidates:
        return None
    matches = [
        card_id for card_id in candidates if (released_at.get(card_id) or "")[:4] == str(year)
    ]
    return matches[0] if len(matches) == 1 else None
