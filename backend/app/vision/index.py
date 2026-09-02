"""The searchable index of card image hashes.

Scale first, because it determines the whole design: 107 000 printings at 96 bytes is
**10 MB**. That fits in memory with room to spare, and a byte-wise XOR plus a table
lookup over 10 MB is a few milliseconds in numpy. No BK-tree, no multi-index hashing,
no vector database. The one published implementation that struggled with search cost
was paying Python per-object overhead on every comparison, not hitting a real limit.

The confidence measure is the interesting part. A fixed Hamming cutoff ("accept below
40 bits") cannot work across a whole catalogue: some artworks are near-duplicates of
each other and some are unique, so the distance that means "certain" differs per card.
Instead the best distance is scored against the *distribution* of all the others --
how many standard deviations below the mean it sits. That is self-calibrating: a card
with many similar-looking reprints has to beat them by a wider margin, and no
threshold has to be guessed.

**A z-score answers "is this a card I know", not "is this that printing".** Those come
apart whenever one artwork is reused across sets, which is more than half of a real
collection. Measured on this catalogue, printings sharing an ``illustration_id`` sit
16 to 60 bits apart -- they differ only in frame and border -- while the mean distance
across 107 000 printings is around 384. Two siblings at 90 and 110 bits therefore both
score enormous z-values, and their *difference* clears any z-margin while representing
nothing but the lighting the photograph was taken under. So every hit carries whether
its artwork is shared, and that is what the printing-level decision reads (ADR-027).
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

import numpy as np
from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import Card, CardHash
from app.vision.hashing import HASH_BYTES, SYMBOL_HASH_BYTES, hamming_distances

log = logging.getLogger("mtgvault.vision.index")

MIN_Z_SCORE = 4.0
"""How many standard deviations below the mean the best distance must sit to count as
a confident visual match."""

MIN_INDEX_SIZE = 50
"""Below this many hashes the distance distribution is too small for a z-score to mean
anything, so visual matching reports nothing rather than something unfounded. This is
the state a fresh install is in until the hashing job has run."""

MAX_HITS = 8

SYMBOL_MAX_DISTANCE = 24
"""How far the scanned type-line band may sit from a candidate's and still be called a
match, out of 192 bits.

Measured on real printings: re-encoding, softening and dimming one card moves this hash
by a median of 4 bits and never more than 14. Two printings of the same artwork from
different sets sit a median of 62 apart. This threshold is set above the observed noise
with headroom, and far below the separation."""

SYMBOL_MIN_MARGIN = 20
"""How far clear of the next candidate the best band must stand.

Some symbols genuinely resemble each other -- the M10 and M12 core-set logos measured
12 bits apart -- and two printings from the *same* set share a symbol exactly, so a
nearest-wins rule would pick between them on noise. Without this margin the tie is
reported unbroken, which sends the question to the user rather than guessing at it."""

DECISIVE_Z = 6.0
DECISIVE_MARGIN = 3.0
"""A result this far clear of the field will not be overturned by searching the other
orientation, so the second search is skipped. Kept in step with the fusion thresholds
of the same name: this decides where time is spent, those decide what is believed."""


@dataclass(frozen=True)
class VisualHit:
    """One printing that looks like the scanned card."""

    card_id: int
    distance: int
    """Bits differing out of 768."""
    z_score: float
    """Standard deviations below the mean distance. Higher is better."""
    flipped: bool = False
    """The match was found with the card rotated 180 degrees."""
    art_id: int = -1
    """Code for this printing's ``illustration_id``; -1 when the printing has none."""
    art_shared: bool = False
    """Whether another printing in the catalogue reuses this artwork.

    When true the hash has identified the *artwork* and cannot identify the *printing*,
    however large its z-score: the siblings differ by less than photographic noise. The
    answer then has to come from the collector line, or from asking."""


@dataclass
class HashIndex:
    """Packed hashes for every printing that has one."""

    card_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    table: np.ndarray = field(
        default_factory=lambda: np.zeros((0, HASH_BYTES // 8), dtype=np.uint64)
    )
    art_ids: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=np.int64))
    """Per row: an integer code for the printing's ``illustration_id``, -1 when absent."""
    art_shared: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    """Per row: whether any other printing in the catalogue reuses this artwork."""
    symbols: np.ndarray = field(
        default_factory=lambda: np.zeros((0, SYMBOL_HASH_BYTES // 8), dtype=np.uint64)
    )
    """Per row: the type-line band hash, zeroed where one has not been computed."""
    has_symbol: np.ndarray = field(default_factory=lambda: np.zeros(0, dtype=bool))
    """Per row: whether :attr:`symbols` holds a real hash rather than a placeholder."""
    _row_of: dict[int, int] = field(default_factory=dict)
    """card_id -> row, so a tie-break can find a candidate's symbol without a scan."""
    signature: tuple[int, int, str] = (0, 0, "")
    """(row count, symbol-hash count, newest computed_at) -- see :func:`_signature`."""

    def __len__(self) -> int:
        """Number of printings that can be recognised visually."""
        return int(self.card_ids.shape[0])

    @property
    def usable(self) -> bool:
        """Whether the index holds enough hashes for a z-score to be meaningful."""
        return len(self) >= MIN_INDEX_SIZE


_cached: HashIndex | None = None


def _signature(db: DbSession) -> tuple[int, int, str]:
    """A change marker for the hash table.

    A bare row count misses in-place updates -- the symbol-hash backfill and
    recomputes change rows without changing how many there are -- so the count is
    paired with how many rows carry a symbol hash and the newest ``computed_at``.
    """
    row = db.execute(
        select(
            func.count(),
            func.count(CardHash.symbol_phash),
            func.coalesce(func.max(CardHash.computed_at), ""),
        ).select_from(CardHash)
    ).one()
    return (int(row[0]), int(row[1]), str(row[2]))


def build_index(db: DbSession) -> HashIndex:
    """Load every stored hash into a packed numpy table.

    Artwork identity is loaded alongside, because whether a printing's art is unique is
    a property of the whole catalogue rather than of any one search, and answering it
    per query would mean a lookup on the hot path.
    """
    from app.models.cards import scannable_clause

    rows = db.execute(
        select(CardHash.card_id, CardHash.phash, CardHash.symbol_phash, Card.illustration_id)
        .join(Card, Card.id == CardHash.card_id)
        # Junk layouts (art series, tokens, emblems, placeholder sets) stay out
        # of the search table even if their hashes are still on disk.
        .where(scannable_clause())
        .order_by(CardHash.card_id)
    ).all()
    usable = [
        (card_id, blob, symbol, illustration_id)
        for card_id, blob, symbol, illustration_id in rows
        if blob and len(blob) == HASH_BYTES
    ]
    signature = _signature(db)
    if not usable:
        return HashIndex(signature=signature)

    card_ids = np.array([card_id for card_id, _blob, _sym, _art in usable], dtype=np.int64)
    # Viewed as 64-bit words so the search can use numpy's native popcount, which
    # counts an eighth as many elements for the same answer.
    table = np.frombuffer(
        b"".join(blob for _card_id, blob, _sym, _art in usable), dtype=np.uint64
    ).reshape(len(usable), HASH_BYTES // 8)

    # The symbol band. Rows without one are zero-filled and masked off rather than
    # compacted, so a row index means the same thing in every table here.
    blank = bytes(SYMBOL_HASH_BYTES)
    symbols = np.frombuffer(
        b"".join(
            symbol if symbol and len(symbol) == SYMBOL_HASH_BYTES else blank
            for _card_id, _blob, symbol, _art in usable
        ),
        dtype=np.uint64,
    ).reshape(len(usable), SYMBOL_HASH_BYTES // 8)
    has_symbol = np.array(
        [bool(symbol) and len(symbol) == SYMBOL_HASH_BYTES for _c, _b, symbol, _a in usable],
        dtype=bool,
    )

    # Artwork strings interned to integers: the search compares these per hit, and
    # comparing 36-character uuids there would cost more than the search itself.
    codes: dict[str, int] = {}
    art_ids = np.full(len(usable), -1, dtype=np.int64)
    for position, (_card_id, _blob, _symbol, illustration_id) in enumerate(usable):
        if illustration_id:
            art_ids[position] = codes.setdefault(illustration_id, len(codes))

    # A printing whose artwork appears more than once cannot be identified by that
    # artwork, however good the match. Counted once here rather than per query.
    #
    # Counted over the *catalogue*, not over this table. A printing whose only siblings
    # are digital-only or unhashed looks unique in here, and would then be allowed to
    # lock in -- which is how a promo Plague Myr came to be certain of itself while two
    # paper reprints of it sat one join away.
    shared_ids = {
        str(row)
        for row in db.scalars(
            select(Card.illustration_id)
            .where(Card.illustration_id.isnot(None))
            .group_by(Card.illustration_id)
            .having(func.count() > 1)
        )
    }
    art_shared = np.array(
        [
            bool(illustration_id) and illustration_id in shared_ids
            for _c, _b, _s, illustration_id in usable
        ],
        dtype=bool,
    )

    log.info(
        "hash_index_built",
        extra={
            "entries": len(usable),
            "artworks": len(codes),
            "shared_art_printings": int(art_shared.sum()),
            "with_symbol": int(has_symbol.sum()),
        },
    )
    return HashIndex(
        card_ids=card_ids,
        table=table,
        art_ids=art_ids,
        art_shared=art_shared,
        symbols=symbols,
        has_symbol=has_symbol,
        signature=signature,
        _row_of={int(card_id): row for row, card_id in enumerate(card_ids)},
    )


def get_index(db: DbSession, *, force: bool = False) -> HashIndex:
    """Return the cached index, reloading it when the hashing job has added rows."""
    global _cached
    signature = _signature(db)
    if force or _cached is None or _cached.signature != signature:
        _cached = build_index(db)
    return _cached


def reset_index() -> None:
    """Drop the cached index. Tests and the hashing job use this."""
    global _cached
    _cached = None


def _rank(index: HashIndex, query: bytes, *, flipped: bool, limit: int) -> list[VisualHit]:
    distances = hamming_distances(query, index.table)
    mean = float(distances.mean())
    deviation = float(distances.std())

    order = np.argsort(distances, kind="stable")[:limit]
    hits: list[VisualHit] = []
    for position in order:
        distance = int(distances[position])
        # A zero-variance table would make every z-score infinite; that only happens
        # with a degenerate index, and reporting no confidence is the safe reading.
        z_score = (mean - distance) / deviation if deviation > 1e-6 else 0.0
        hits.append(
            VisualHit(
                card_id=int(index.card_ids[position]),
                distance=distance,
                z_score=z_score,
                flipped=flipped,
                art_id=int(index.art_ids[position]) if index.art_ids.size else -1,
                art_shared=bool(index.art_shared[position]) if index.art_shared.size else False,
            )
        )
    return hits


def _is_decisive(hits: list[VisualHit]) -> bool:
    """Whether a ranking settles which *artwork* this is.

    Only the artwork, deliberately: this decides whether searching the upside-down
    orientation could overturn the result, and a printing's siblings share its artwork
    exactly, so they are not rivals for that question. Measuring against them instead
    would send every reprinted card through a second search to re-discover the tie it
    already found. Which *printing* it is remains open -- see :attr:`VisualHit.art_shared`.
    """
    if not hits:
        return False
    rival = next(
        (hit.z_score for hit in hits[1:] if hit.art_id != hits[0].art_id or hit.art_id < 0),
        0.0,
    )
    return hits[0].z_score >= DECISIVE_Z and hits[0].z_score - rival >= DECISIVE_MARGIN


def search(
    index: HashIndex, queries: tuple[bytes, bytes], *, limit: int = MAX_HITS
) -> list[VisualHit]:
    """Find the printings that look most like a scanned card.

    Args:
        index: The loaded hash index.
        queries: The card's hash the right way up and upside down. Nothing in a
            rectified rectangle says which way round it is, so both are searched and
            the better orientation wins.
        limit: Maximum hits to return.

    Returns:
        Hits sorted by confidence, best first. Empty when the index is too small to
        support a confidence judgement at all.
    """
    if not index.usable:
        return []

    upright, upside_down = queries
    hits = _rank(index, upright, flipped=False, limit=limit)

    # Searching the flipped orientation costs as much again as the upright one. When
    # the upright result is already decisive there is nothing for it to overturn, so
    # the common case -- a card held the right way up -- pays for one search, not two.
    if not _is_decisive(hits):
        hits.extend(_rank(index, upside_down, flipped=True, limit=limit))

    # One printing can appear in both orientations; keep whichever scored better.
    best: dict[int, VisualHit] = {}
    for hit in hits:
        current = best.get(hit.card_id)
        if current is None or hit.z_score > current.z_score:
            best[hit.card_id] = hit

    return sorted(best.values(), key=lambda hit: -hit.z_score)[:limit]


@dataclass(frozen=True)
class SymbolVerdict:
    """The outcome of comparing type-line bands across candidates."""

    card_id: int | None
    """The printing the band picked out, or ``None`` when it could not choose."""
    distance: int = 0
    margin: int = 0
    """Bits between the winner and the next candidate. Zero when nothing was chosen."""
    compared: int = 0
    """Candidates that had a stored band to compare against."""


def break_tie(index: HashIndex, hits: list[VisualHit], symbol_query: bytes) -> SymbolVerdict:
    """Choose between printings the artwork could not separate, using the set symbol.

    The artwork hash identifies which picture is on the card; when that picture appears
    in several sets it cannot say which of them this copy came from. The type-line band
    can: the sets print different symbols there, and on a pre-2015 card that symbol is
    the only mark naming the edition at all (ADR-027).

    Args:
        index: The loaded hash index.
        hits: Candidate printings, best artwork match first.
        symbol_query: The scanned card's type-line band hash.

    Returns:
        The printing chosen, or a verdict with ``card_id`` of ``None``. Refusing to
        choose is a normal outcome and a useful one -- two printings of the same set
        share a symbol exactly, and the band lands on artwork rather than a type line
        for sagas, adventures, split cards and full-art lands. In every one of those
        the honest answer is to ask.
    """
    if not hits or len(symbol_query) != SYMBOL_HASH_BYTES:
        return SymbolVerdict(card_id=None)

    scored: list[tuple[int, int]] = []
    for hit in hits:
        row = index._row_of.get(hit.card_id)
        if row is None or not index.has_symbol[row]:
            continue
        distance = int(hamming_distances(symbol_query, index.symbols[row : row + 1])[0])
        scored.append((distance, hit.card_id))

    if not scored:
        return SymbolVerdict(card_id=None)

    scored.sort()
    best_distance, best_id = scored[0]
    runner_up = scored[1][0] if len(scored) > 1 else best_distance + SYMBOL_MIN_MARGIN
    margin = runner_up - best_distance

    if best_distance > SYMBOL_MAX_DISTANCE or margin < SYMBOL_MIN_MARGIN:
        return SymbolVerdict(
            card_id=None, distance=best_distance, margin=margin, compared=len(scored)
        )
    return SymbolVerdict(
        card_id=best_id, distance=best_distance, margin=margin, compared=len(scored)
    )
