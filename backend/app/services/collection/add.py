"""Adding copies to the collection.

Resolution is the interesting part. A user (or a CSV, or a scan) can identify a card
by printing, by oracle id, or by name alone, and each of those is progressively less
precise. Ambiguity is *reported*, never guessed: a name with several printings and no
set comes back as candidates for the picker rather than silently becoming whichever
row happened to sort first.

Non-English copies resolve to the English printing of the same card and record the
language on the copy (OPEN-QUESTIONS B2, option (a)). If the exact-language printing
exists it is used instead.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.errors import AppError, NotFound
from app.models import Card, CollectionItem, OracleCard, utcnow
from app.services import audit
from app.util.text import normalize_name

MAX_QUANTITY = 500
"""Guard against a fat-fingered quantity turning into 100 000 rows."""


class AmbiguousCard(AppError):
    """A name or oracle id matched more than one plausible printing."""

    status_code = 409
    code = "ambiguous_card"


@dataclass
class AddSpec:
    """What to add, at whatever precision the caller has."""

    oracle_id: str | None = None
    set_code: str | None = None
    collector_number: str | None = None
    name: str | None = None
    lang: str = "en"
    finish: str = "nonfoil"
    condition: str = "NM"
    is_proxy: bool = False
    acquired_at: str | None = None
    acquired_price_cents: int | None = None
    notes: str | None = None


@dataclass
class Resolution:
    """Outcome of resolving an :class:`AddSpec` to a printing."""

    card: Card | None
    candidates: list[Card] = field(default_factory=list)
    matched_on: str = "none"


def _printing_sort_key(card: Card) -> tuple[int, int, str]:
    """Prefer paper printings, then the cheapest, then the earliest set code.

    "Cheapest" is the right default for adding a card you own but did not specify:
    it is the printing you most likely have, and it never overstates collection value.
    """
    return (
        1 if card.digital else 0,
        card.price_usd_cents if card.price_usd_cents is not None else 10**9,
        card.set_code,
    )


def resolve_card(db: DbSession, spec: AddSpec) -> Resolution:
    """Resolve a spec to a single printing.

    Args:
        db: Open database session.
        spec: What the caller knows about the card.

    Returns:
        A :class:`Resolution`. ``card`` is ``None`` when nothing matched; ``candidates``
        is populated when the match was ambiguous.
    """
    if spec.set_code and spec.collector_number:
        exact = db.scalars(
            select(Card).where(
                Card.set_code == spec.set_code.lower(),
                Card.collector_number == str(spec.collector_number),
                Card.lang == spec.lang,
            )
        ).first()
        if exact is not None:
            return Resolution(card=exact, matched_on="printing")
        # The exact-language printing may not exist in default_cards; fall back to
        # English and keep the requested language on the copy.
        fallback = db.scalars(
            select(Card).where(
                Card.set_code == spec.set_code.lower(),
                Card.collector_number == str(spec.collector_number),
                Card.lang == "en",
            )
        ).first()
        if fallback is not None:
            return Resolution(card=fallback, matched_on="printing_lang_fallback")
        return Resolution(card=None, matched_on="none")

    oracle_id = spec.oracle_id
    if oracle_id is None and spec.name:
        normalized = normalize_name(spec.name)
        oracles = list(
            db.scalars(
                select(OracleCard).where(
                    (OracleCard.name_norm == normalized)
                    | (OracleCard.name_front_norm == normalized)
                )
            )
        )
        if not oracles:
            return Resolution(card=None, matched_on="none")
        if len(oracles) > 1:
            cards = _printings_for(db, [o.oracle_id for o in oracles])
            return Resolution(card=None, candidates=cards, matched_on="name_ambiguous")
        oracle_id = oracles[0].oracle_id

    if oracle_id is None:
        return Resolution(card=None, matched_on="none")

    printings = _printings_for(db, [oracle_id])
    if not printings:
        return Resolution(card=None, matched_on="none")
    printings.sort(key=_printing_sort_key)
    return Resolution(card=printings[0], candidates=printings, matched_on="oracle")


def _printings_for(db: DbSession, oracle_ids: list[str]) -> list[Card]:
    """All printings of the given oracle cards, English first."""
    return list(
        db.scalars(
            select(Card)
            .where(Card.oracle_id.in_(oracle_ids))
            .order_by(Card.lang != "en", Card.released_at.desc())
        )
    )


def add_copies(
    db: DbSession,
    spec: AddSpec,
    quantity: int = 1,
    *,
    batch_id: str | None = None,
    source: str = "api",
    note: str | None = None,
) -> tuple[list[CollectionItem], str]:
    """Add ``quantity`` physical copies of one card.

    Every copy is its own row (ADR-005), but the whole add is **one** audit entry:
    adding 40 basic lands should be one undoable action, not forty.

    Args:
        db: Open database session.
        spec: What to add.
        quantity: Number of copies.
        batch_id: Join an existing batch (a scan session, a CSV import).
        source: Audit source label.
        note: Free text recorded on the audit entry.

    Returns:
        The created rows and the batch id they were recorded under.

    Raises:
        AmbiguousCard: The spec matched several printings.
        NotFound: Nothing matched.
        AppError: ``quantity`` is out of range.
    """
    if quantity < 1 or quantity > MAX_QUANTITY:
        raise AppError(
            f"Quantity must be between 1 and {MAX_QUANTITY}",
            code="invalid_quantity",
            detail={"quantity": quantity},
        )

    resolution = resolve_card(db, spec)
    if resolution.card is None:
        if resolution.candidates:
            raise AmbiguousCard(
                "Several printings match; choose one",
                detail={"candidates": [_candidate(c) for c in resolution.candidates[:25]]},
            )
        raise NotFound(
            "No card matched",
            detail={
                "name": spec.name,
                "oracle_id": spec.oracle_id,
                "set_code": spec.set_code,
                "collector_number": spec.collector_number,
            },
        )

    card = resolution.card
    batch = batch_id or audit.new_batch_id()
    now = utcnow()
    items = [
        CollectionItem(
            card_id=card.id,
            oracle_id=card.oracle_id,
            set_code=card.set_code,
            collector_number=card.collector_number,
            lang=spec.lang,
            finish=spec.finish,
            condition=spec.condition,
            is_proxy=spec.is_proxy,
            acquired_at=spec.acquired_at,
            acquired_price_cents=spec.acquired_price_cents,
            notes=spec.notes,
            created_at=now,
            updated_at=now,
        )
        for _ in range(quantity)
    ]
    db.add_all(items)
    db.flush()

    # Adding 40 basic lands is one thing the user did, so it is one audit entry with
    # 40 row snapshots inside it -- not 40 entries to scroll past and undo one by one.
    audit.record(
        db,
        action=audit.BULK_CREATE if quantity > 1 else "create",
        entity_type="collection_item",
        entity_id=items[0].id if quantity == 1 else None,
        batch_id=batch,
        after=(
            {
                "rows": [audit.snapshot(item) for item in items],
                "summary": _summary(card, spec, quantity),
            }
            if quantity > 1
            else audit.snapshot(items[0])
        ),
        source=source,
        note=note,
    )
    return items, batch


def _summary(card: Card, spec: AddSpec, quantity: int) -> dict[str, Any]:
    """Human-readable description of a bulk add, shown in the audit view."""
    return {
        "quantity": quantity,
        "card": _candidate(card),
        "finish": spec.finish,
        "condition": spec.condition,
        "lang": spec.lang,
        "is_proxy": spec.is_proxy,
    }


def _candidate(card: Card) -> dict[str, Any]:
    """Compact printing description used in ambiguity errors and audit entries."""
    return {
        "card_id": card.id,
        "oracle_id": card.oracle_id,
        "name": card.name,
        "set_code": card.set_code,
        "set_name": card.set_name,
        "collector_number": card.collector_number,
        "lang": card.lang,
        "price_usd_cents": card.price_usd_cents,
        "image_url": f"/api/images/{card.id}/normal",
    }
