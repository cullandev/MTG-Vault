"""Turning card ids into the shape the scan overlay needs.

Split out of the identification pipeline because it is the one part with no opinion
about *how* a card was recognised: whether the answer came from the collector line,
the artwork or the name, the overlay wants the same fields.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.orm import Session as DbSession

from app.models import Card, CollectionItem


@dataclass
class PrintingRef:
    """A printing, in the shape the scan overlay needs."""

    card_id: int
    oracle_id: str
    name: str
    set_code: str
    set_name: str | None
    collector_number: str
    lang: str
    image_url: str | None
    price_usd_cents: int | None
    price_usd_foil_cents: int | None
    price_as_of: str | None
    owned_count: int = 0
    illustration_id: str | None = None
    """Scryfall's artwork identity. Two printings sharing it are visually the
    same card face, which is what licenses the sticky-set reorder to treat
    their score difference as hash noise rather than evidence."""
    score: float = 0.0
    """Fused evidence score, for the overlay's confidence display."""
    reasons: list[str] | None = None
    """Which signals backed this printing, in plain words -- "collector line fin/28",
    "artwork match z=9.4". The scanner used to be unable to say why it thought
    anything, which made every misidentification a mystery."""

    def as_dict(self) -> dict[str, Any]:
        """Serialise for the API response."""
        return {
            "card_id": self.card_id,
            "oracle_id": self.oracle_id,
            "name": self.name,
            "set_code": self.set_code,
            "set_name": self.set_name,
            "collector_number": self.collector_number,
            "lang": self.lang,
            "image_url": self.image_url,
            "price_usd_cents": self.price_usd_cents,
            "price_usd_foil_cents": self.price_usd_foil_cents,
            "price_as_of": self.price_as_of,
            "owned_count": self.owned_count,
            "score": round(self.score, 3),
            "reasons": self.reasons or [],
        }


def printing_ref(card: Card, owned: int = 0) -> PrintingRef:
    """Build a reference from a loaded card row."""
    return PrintingRef(
        card_id=card.id,
        oracle_id=card.oracle_id,
        name=card.name,
        set_code=card.set_code,
        set_name=card.set_name,
        collector_number=card.collector_number,
        lang=card.lang,
        image_url=f"/api/images/{card.id}/normal" if card.image_normal_url else None,
        price_usd_cents=card.price_usd_cents,
        price_usd_foil_cents=card.price_usd_foil_cents,
        price_as_of=card.price_updated_at,
        owned_count=owned,
        illustration_id=card.illustration_id,
    )


def owned_counts_for(db: DbSession, card_ids: list[int]) -> dict[int, int]:
    """How many copies of each printing are already in the collection.

    One grouped query rather than one per card: the overlay asks about a handful of
    candidates on every frame, several times a second.
    """
    if not card_ids:
        return {}
    rows = db.execute(
        select(CollectionItem.card_id, func.count(CollectionItem.id))
        .where(CollectionItem.card_id.in_(card_ids))
        .group_by(CollectionItem.card_id)
    )
    return {int(card_id): int(count) for card_id, count in rows if card_id is not None}


def refs_for(db: DbSession, card_ids: list[int]) -> dict[int, PrintingRef]:
    """Load several printings at once, keyed by id."""
    if not card_ids:
        return {}
    cards = list(db.scalars(select(Card).where(Card.id.in_(card_ids))))
    owned = owned_counts_for(db, [card.id for card in cards])
    return {card.id: printing_ref(card, owned.get(card.id, 0)) for card in cards}


def printings_of(db: DbSession, oracle_id: str) -> list[Card]:
    """Every scannable paper printing of an oracle card."""
    from app.models.cards import scannable_clause

    return list(
        db.scalars(
            select(Card).where(
                Card.oracle_id == oracle_id, Card.digital.is_(False), scannable_clause()
            )
        )
    )


NEAR_TIE = 0.15
"""Score gap under which two printings with *different* artwork still count as
tied for the sticky-set reorder."""


def order_sticky(candidates: list[PrintingRef], preferred_sets: set[str]) -> list[PrintingRef]:
    """Lead with the session's set when the leader is the same card anyway.

    People scan piles from one product: after one Magic 2011 confirm, every
    reprint ambiguity in the session should offer M11 first. The reorder only
    ever chooses WHICH PRINTING of the already-leading card sits first -- it
    never promotes a different card, so it cannot cause a wrong-card lock.

    Eligibility is artwork-aware. Printings sharing the leader's illustration
    are visually identical, so their hash-score differences are noise and the
    session's set wins outright (measured: M10/M11 boxes were led by A25/List
    reprints of the right card 22-26% of the time). Printings with different
    artwork keep the old near-tie rule: there the score gap is real evidence.
    """
    if not preferred_sets or len(candidates) < 2:
        return candidates
    top = candidates[0]
    if top.set_code in preferred_sets:
        return candidates

    def eligible(candidate: PrintingRef) -> bool:
        if candidate.set_code not in preferred_sets:
            return False
        same_art = bool(top.illustration_id) and candidate.illustration_id == top.illustration_id
        return same_art or top.score - candidate.score <= NEAR_TIE

    pool = [
        candidate
        for candidate in candidates
        if candidate.oracle_id == top.oracle_id and eligible(candidate)
    ]
    if not pool:
        return candidates
    best = max(pool, key=lambda candidate: candidate.score)
    # The promoted printing inherits the leader's accumulated score: the
    # evidence was about the shared artwork, which both printings wear, and
    # everything downstream (the auto-picker's settled gate, the confidence
    # bar) reads candidates[0].score as "how sure are we about this card".
    # Leaving the sibling's own lower tally there made confidence visibly
    # DROP the moment the preference kicked in.
    best.score = max(best.score, top.score)
    return [best] + [candidate for candidate in candidates if candidate is not best]


def resolve_printing(
    db: DbSession, oracle_id: str, *, prefer_numbers: set[str] | None = None
) -> PrintingRef | None:
    """Pick the printing to show when only the *card* is known.

    A name identifies a card, never a printing, so something has to choose. Preference
    order, and the reasoning:

    0. **A printing whose collector number matches what the corner read.** Even when
       the collector line was too damaged to identify the card outright, the number
       often survives when the set code does not.
    1. **A printing already in the collection.** If you own three Lightning Bolts from
       Unlimited, the one on the mat is almost certainly Unlimited.
    2. **The cheapest paper printing.** It is the most likely one to be sitting in a
       bulk box, and it never overstates collection value.
    3. **The most recent printing**, when nothing has a price.

    Digital-only printings are never chosen; they do not exist on cardboard.
    """
    candidates = printings_of(db, oracle_id)
    if not candidates:
        return None

    owned = owned_counts_for(db, [card.id for card in candidates])
    wanted = prefer_numbers or set()

    def sort_key(card: Card) -> tuple[int, int, int, str]:
        return (
            0 if card.collector_number in wanted else 1,
            0 if owned.get(card.id) else 1,
            card.price_usd_cents if card.price_usd_cents is not None else 10**9,
            # Rule 3 promises the most recent printing; ascending text would
            # quietly hand ties to the oldest.
            _descending_date(card.released_at),
        )

    candidates.sort(key=sort_key)
    chosen = candidates[0]
    return printing_ref(chosen, owned.get(chosen.id, 0))


def _descending_date(released_at: str | None) -> str:
    """An ascending sort key that orders ISO dates newest-first.

    Each digit is flipped to its nine's complement, so "2024-01-01" sorts before
    "1994-01-01"; missing dates sort last.
    """
    if not released_at:
        return "~"
    return "".join(str(9 - int(ch)) if ch.isdigit() else ch for ch in released_at)
