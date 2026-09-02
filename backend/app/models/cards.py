"""Scryfall-derived card data.

Three levels of identity, and keeping them straight is the point of this module:

* :class:`Card` is a *printing* -- a specific physical object with an art, a set, a
  collector number and a price. The natural key is
  ``(set_code, collector_number, lang)`` (ADR-006).
* :class:`CardFace` is one face of a multi-face printing.
* :class:`OracleCard` is the *rules* identity. Deck legality, colour identity, quotas
  and synergy all operate on this table; printings only matter for price, art and
  physical location.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import ForeignKey, Index, Integer, LargeBinary, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.models.base import Base, utcnow

# WUBRG bit positions used by ``color_identity_mask``. A bitmask makes the Commander
# colour-identity test a single AND (ADR-010) instead of a set comparison per card.
COLOR_BITS: dict[str, int] = {"W": 1, "U": 2, "B": 4, "R": 8, "G": 16}


def color_mask(identity: str | None) -> int:
    """Convert a colour-identity string such as ``"BGW"`` into a WUBRG bitmask.

    Args:
        identity: Scryfall colour-identity letters in any order, or ``None``.

    Returns:
        A bitmask where each set bit is one colour. Colourless is ``0``.
    """
    if not identity:
        return 0
    mask = 0
    for letter in identity:
        mask |= COLOR_BITS.get(letter.upper(), 0)
    return mask


class OracleCard(Base):
    """One row per ``oracle_id``: the rules identity of a card."""

    __tablename__ = "oracle_cards"

    oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    name_norm: Mapped[str] = mapped_column(Text(), nullable=False, index=True)
    name_front: Mapped[str] = mapped_column(Text(), nullable=False)
    name_front_norm: Mapped[str] = mapped_column(Text(), nullable=False, index=True)
    layout: Mapped[str] = mapped_column(Text(), nullable=False)
    type_line: Mapped[str | None] = mapped_column(Text())
    oracle_text_all: Mapped[str | None] = mapped_column(Text())
    """All faces' oracle text concatenated; the FTS5 index reads this column."""
    mana_cost: Mapped[str | None] = mapped_column(Text())
    cmc: Mapped[float] = mapped_column(nullable=False, default=0.0)
    colors: Mapped[str | None] = mapped_column(Text())
    color_identity: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    color_identity_mask: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    keywords_json: Mapped[list[Any] | None] = mapped_column()
    produced_mana: Mapped[str | None] = mapped_column(Text())
    is_legendary: Mapped[bool] = mapped_column(default=False)
    is_creature: Mapped[bool] = mapped_column(default=False)
    is_land: Mapped[bool] = mapped_column(default=False)
    reserved: Mapped[bool] = mapped_column(default=False)
    game_changer: Mapped[bool] = mapped_column(default=False)
    """Scryfall's official Commander Bracket "Game Changer" flag (ADR-010, section 6)."""
    edhrec_rank: Mapped[int | None] = mapped_column(Integer())
    updated_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_oracle_cards_ci_cmc", "color_identity_mask", "cmc"),)


class Card(Base):
    """One row per Scryfall printing."""

    __tablename__ = "cards"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    scryfall_id: Mapped[str] = mapped_column(Text(), nullable=False)
    """Stored for URL construction only; never a foreign key (ADR-006)."""
    oracle_id: Mapped[str] = mapped_column(
        ForeignKey("oracle_cards.oracle_id", ondelete="CASCADE"), nullable=False
    )
    set_code: Mapped[str] = mapped_column(Text(), nullable=False)
    set_name: Mapped[str | None] = mapped_column(Text())
    collector_number: Mapped[str] = mapped_column(Text(), nullable=False)
    """Opaque text -- collector numbers include letters and stars ("123a", "★")."""
    lang: Mapped[str] = mapped_column(Text(), nullable=False, default="en")

    name: Mapped[str] = mapped_column(Text(), nullable=False)
    name_front: Mapped[str] = mapped_column(Text(), nullable=False)
    name_norm: Mapped[str] = mapped_column(Text(), nullable=False)
    layout: Mapped[str] = mapped_column(Text(), nullable=False)
    rarity: Mapped[str | None] = mapped_column(Text())
    mana_cost: Mapped[str | None] = mapped_column(Text())
    cmc: Mapped[float] = mapped_column(nullable=False, default=0.0)
    type_line: Mapped[str | None] = mapped_column(Text())
    oracle_text: Mapped[str | None] = mapped_column(Text())
    colors: Mapped[str | None] = mapped_column(Text())
    color_identity: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    color_identity_mask: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    keywords_json: Mapped[list[Any] | None] = mapped_column()
    produced_mana: Mapped[str | None] = mapped_column(Text())
    finishes_json: Mapped[list[Any] | None] = mapped_column()
    released_at: Mapped[str | None] = mapped_column(Text())
    illustration_id: Mapped[str | None] = mapped_column(Text())
    """Art identity. The Phase 6 pHash index is keyed on this, not on printing (ADR-012)."""
    image_normal_url: Mapped[str | None] = mapped_column(Text())
    image_art_crop_url: Mapped[str | None] = mapped_column(Text())
    border_color: Mapped[str | None] = mapped_column(Text())
    frame: Mapped[str | None] = mapped_column(Text())
    promo: Mapped[bool] = mapped_column(default=False)
    variation: Mapped[bool] = mapped_column(default=False)
    digital: Mapped[bool] = mapped_column(default=False)
    """Digital-only printings are excluded from every paper flow."""
    reserved: Mapped[bool] = mapped_column(default=False)
    game_changer: Mapped[bool] = mapped_column(default=False)
    edhrec_rank: Mapped[int | None] = mapped_column(Integer())

    price_usd_cents: Mapped[int | None] = mapped_column(Integer())
    price_usd_foil_cents: Mapped[int | None] = mapped_column(Integer())
    price_usd_etched_cents: Mapped[int | None] = mapped_column(Integer())
    price_updated_at: Mapped[str | None] = mapped_column(Text())
    """Rendered next to every price in the UI, along with the TCGplayer-market caveat."""

    imported_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    faces: Mapped[list[CardFace]] = relationship(
        back_populates="card", cascade="all, delete-orphan", lazy="selectin"
    )

    __table_args__ = (
        UniqueConstraint("set_code", "collector_number", "lang", name="cards_natural"),
        UniqueConstraint("scryfall_id", name="cards_scryfall_id"),
        Index("ix_cards_oracle_id_lang", "oracle_id", "lang"),
        Index("ix_cards_name_norm", "name_norm"),
        Index("ix_cards_illustration_id", "illustration_id"),
        Index("ix_cards_lib_filter", "color_identity_mask", "cmc", "rarity", "set_code"),
        Index("ix_cards_price_usd_cents", "price_usd_cents"),
        # Covering index for the Sets page's per-set rollup: it carries every
        # column that query selects, so the aggregate never fetches a table
        # row. Without it the endpoint pulled 117k rows and took 5.2 seconds
        # (migration 0018).
        Index(
            "ix_cards_set_rollup",
            "digital",
            "lang",
            "set_code",
            "collector_number",
            "id",
            "set_name",
            "released_at",
            "price_usd_cents",
            "price_usd_foil_cents",
            "price_usd_etched_cents",
        ),
    )


SCAN_EXCLUDED_LAYOUTS: tuple[str, ...] = (
    "art_series",
    "emblem",
    "token",
    # Oversized game aids, not playing cards: a Black Lotus Unknown Planechase
    # plane led a Hobbit scan the very session after the first exclusion round.
    "planar",
    "scheme",
    "vanguard",
)
"""Layouts the scanner must never propose. Measured on 9,447+ real scan events:
art-series cards (pure artwork -- perfect hash bait), tokens, emblems and
oversized formats led the candidate list 30+ times and were confirmed exactly
zero times in over 1,100 confirms."""

SCAN_EXCLUDED_SETS: tuple[str, ...] = ("unk",)
"""Scryfall's "Unknown Event" placeholder set: cards that exist in data but not
in any product a person could scan."""

SCAN_KEPT_ART_SETS: tuple[str, ...] = ("altr",)
"""Art-series sets the owner actually collects (LOTR); matchable despite the
layout exclusion. Add the Hobbit's here if Scryfall ever publishes one."""


def scannable_clause() -> Any:
    """SQL filter for printings the scanner may propose as candidates.

    Applied wherever candidates are drawn -- the visual hash index, the
    collector-line lookup, and name-to-printing resolution -- so the exclusion
    cannot be honoured in one rung and leak through another.
    """
    from sqlalchemy import and_, or_

    return or_(
        Card.set_code.in_(SCAN_KEPT_ART_SETS),
        and_(
            Card.layout.notin_(SCAN_EXCLUDED_LAYOUTS),
            Card.set_code.notin_(SCAN_EXCLUDED_SETS),
        ),
    )


class CardFace(Base):
    """One face of a multi-face printing."""

    __tablename__ = "card_faces"

    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    face_index: Mapped[int] = mapped_column(Integer(), primary_key=True)
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    mana_cost: Mapped[str | None] = mapped_column(Text())
    type_line: Mapped[str | None] = mapped_column(Text())
    oracle_text: Mapped[str | None] = mapped_column(Text())
    colors: Mapped[str | None] = mapped_column(Text())
    image_normal_url: Mapped[str | None] = mapped_column(Text())
    image_art_crop_url: Mapped[str | None] = mapped_column(Text())
    illustration_id: Mapped[str | None] = mapped_column(Text())

    card: Mapped[Card] = relationship(back_populates="faces")


class Legality(Base):
    """Format legality of an oracle card, as reported by Scryfall."""

    __tablename__ = "legalities"

    oracle_id: Mapped[str] = mapped_column(
        ForeignKey("oracle_cards.oracle_id", ondelete="CASCADE"), primary_key=True
    )
    format: Mapped[str] = mapped_column(Text(), primary_key=True)
    status: Mapped[str] = mapped_column(Text(), nullable=False)
    """One of ``legal``, ``not_legal``, ``restricted``, ``banned``."""
    updated_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (Index("ix_legalities_format_status", "format", "status"),)


class LegalityChange(Base):
    """A legality transition detected by the weekly refresh.

    Phase 5 turns these rows into "your deck is affected" flags; Phase 1 only records
    them so no change is lost between the import landing and the feature shipping.
    """

    __tablename__ = "legality_changes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    oracle_id: Mapped[str] = mapped_column(Text(), nullable=False, index=True)
    format: Mapped[str] = mapped_column(Text(), nullable=False)
    old_status: Mapped[str] = mapped_column(Text(), nullable=False)
    new_status: Mapped[str] = mapped_column(Text(), nullable=False)
    detected_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    import_run_id: Mapped[int | None] = mapped_column(Integer())

    __table_args__ = (Index("ix_legality_changes_detected_at", "detected_at"),)


class CardHash(Base):
    """The perceptual hash of a printing's reference image.

    Populated by a background job that fetches each printing's image from Scryfall,
    hashes it and throws the image away: the whole index for 107 000 printings is
    about 10 MB, so it is held in memory and searched by brute force (ADR-024).

    Kept in its own table rather than a column on ``cards`` so that a bulk card
    re-import does not discard hours of hashing, and so a printing whose image cannot
    be fetched is simply absent rather than a null nobody can distinguish from
    "not tried yet".
    """

    __tablename__ = "card_hashes"

    card_id: Mapped[int] = mapped_column(
        ForeignKey("cards.id", ondelete="CASCADE"), primary_key=True
    )
    phash: Mapped[bytes] = mapped_column(LargeBinary(), nullable=False)
    """Packed bits: 256 per colour channel, blue then green then red."""
    symbol_phash: Mapped[bytes | None] = mapped_column(LargeBinary())
    """The type-line band, where the set symbol is printed. 64 bits per channel.

    The artwork hash cannot separate printings that reuse an illustration -- 78% of the
    catalogue -- because they differ there by less than a photograph does (ADR-027).
    They differ in this band by a median of 62 bits out of 192, against a re-encode
    wobble of 4, so this is what tells them apart.

    Nullable because it arrived after the artwork hashes: a row without one has simply
    not been recomputed yet, and the job fills those in on its next pass."""
    source: Mapped[str] = mapped_column(Text(), nullable=False, default="scryfall_small")
    """Which reference image this was computed from, so a change of source can
    invalidate the right rows rather than all of them."""
    computed_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
