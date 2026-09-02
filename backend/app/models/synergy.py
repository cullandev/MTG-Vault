"""The synergy graph: tags, edges, and the cores mined from the vault (Phase 8).

Edges are undirected and stored once with ``oracle_id_a < oracle_id_b`` enforced
by a CHECK constraint; each carries its component weights separately so the UI can
always answer "why are these two connected" (ARCHITECTURE.md section 3.7).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import CheckConstraint, Float, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow

TAG_SOURCES = ("pattern", "scryfall", "manual", "ai")


class CardTag(Base):
    """One functional tag on one oracle card, from the pattern table."""

    __tablename__ = "card_tags"

    oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    tag: Mapped[str] = mapped_column(Text(), primary_key=True)
    source: Mapped[str] = mapped_column(Text(), nullable=False, default="pattern")
    """One of :data:`TAG_SOURCES`."""
    confidence: Mapped[float] = mapped_column(Float(), nullable=False, default=1.0)

    __table_args__ = (Index("ix_card_tags_tag", "tag"),)


class SynergyEdge(Base):
    """One undirected synergy edge between two oracle cards."""

    __tablename__ = "synergy_edges"

    oracle_id_a: Mapped[str] = mapped_column(Text(), primary_key=True)
    oracle_id_b: Mapped[str] = mapped_column(Text(), primary_key=True)
    weight: Mapped[float] = mapped_column(Float(), nullable=False)
    combo_w: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)
    cooccur_w: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)
    mechanical_w: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)
    ai_w: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)
    reasons_json: Mapped[list[Any] | None] = mapped_column()
    computed_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)

    __table_args__ = (
        CheckConstraint("oracle_id_a < oracle_id_b", name="ordered_pair"),
        Index("ix_synergy_edges_a", "oracle_id_a", "weight"),
        Index("ix_synergy_edges_b", "oracle_id_b", "weight"),
    )


class SynergyCore(Base):
    """One cluster of vault cards that keep pointing at each other."""

    __tablename__ = "synergy_cores"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    computed_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    color_identity: Mapped[str] = mapped_column(Text(), nullable=False, default="")
    color_identity_mask: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    theme_name: Mapped[str] = mapped_column(Text(), nullable=False)
    card_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    density: Mapped[float] = mapped_column(Float(), nullable=False)
    """Internal edge weight over possible pairs -- how tightly the core holds."""
    buildability: Mapped[float] = mapped_column(Float(), nullable=False)
    """Share of the core's copies free right now (not sleeved into built decks)."""
    combined_score: Mapped[float] = mapped_column(Float(), nullable=False)


class SynergyCoreCard(Base):
    """Membership of one card in one core, with its centrality."""

    __tablename__ = "synergy_core_cards"

    core_id: Mapped[int] = mapped_column(
        ForeignKey("synergy_cores.id", ondelete="CASCADE"), primary_key=True
    )
    oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    centrality: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)
