"""Rating and strategy: heuristic scores, external-source caches, AI review cache.

The external tables double as the serving cache: a deck page never waits on EDHREC
or Spellbook -- it reads what the refresh fetched, marked stale when old, and the
clients repopulate in the background (ARCHITECTURE.md section 3.5).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class DeckScore(Base):
    """One computed heuristic score of one deck."""

    __tablename__ = "deck_scores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    deck_id: Mapped[int] = mapped_column(ForeignKey("decks.id", ondelete="CASCADE"), nullable=False)
    computed_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    consistency: Mapped[float] = mapped_column(Float(), nullable=False)
    speed: Mapped[float] = mapped_column(Float(), nullable=False)
    interaction: Mapped[float] = mapped_column(Float(), nullable=False)
    resilience: Mapped[float] = mapped_column(Float(), nullable=False)
    bracket: Mapped[int | None] = mapped_column(Integer())
    signals_json: Mapped[dict[str, Any] | None] = mapped_column()
    """Every raw count behind the sub-scores, so a score is always explainable."""
    heuristic_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    """Bumped whenever the formulas change; stale rows are recomputed, not trusted."""

    __table_args__ = (Index("ix_deck_scores_deck_id_computed_at", "deck_id", "computed_at"),)


class AiCache(Base):
    """One AI review response, keyed by the hash of exactly what was asked."""

    __tablename__ = "ai_cache"

    request_hash: Mapped[str] = mapped_column(Text(), primary_key=True)
    model: Mapped[str] = mapped_column(Text(), nullable=False)
    prompt_version: Mapped[int] = mapped_column(Integer(), nullable=False)
    request_json: Mapped[dict[str, Any] | None] = mapped_column()
    response_json: Mapped[dict[str, Any] | None] = mapped_column()
    input_tokens: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    output_tokens: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    created_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)


class EdhrecCommander(Base):
    """EDHREC's page for one commander, as fetched."""

    __tablename__ = "edhrec_commanders"

    oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    fetched_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    payload_json: Mapped[dict[str, Any] | None] = mapped_column()
    """The trimmed page: top cards and themes, already reduced to what the UI shows."""
    parser_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)


class EdhrecCooccurrence(Base):
    """One card's inclusion rate under one commander, from EDHREC."""

    __tablename__ = "edhrec_cooccurrence"

    commander_oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    inclusion_pct: Mapped[float] = mapped_column(Float(), nullable=False)
    synergy: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)


class SpellbookCombo(Base):
    """One combo from Commander Spellbook."""

    __tablename__ = "spellbook_combos"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    combo_id: Mapped[str] = mapped_column(Text(), nullable=False, unique=True)
    oracle_ids_json: Mapped[list[Any] | None] = mapped_column()
    """Oracle ids of the cards used; names that did not resolve are kept as names."""
    result_text: Mapped[str | None] = mapped_column(Text())
    colors: Mapped[str | None] = mapped_column(Text())
    fetched_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)


class SpellbookComboCard(Base):
    """Membership row: this oracle card is part of this combo."""

    __tablename__ = "spellbook_combo_cards"

    combo_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)

    __table_args__ = (Index("ix_spellbook_combo_cards_oracle_id", "oracle_id"),)
