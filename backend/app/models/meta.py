"""Meta snapshots, archetype templates, and coverage (Phase 7).

A snapshot is one source's view of one format on one day; archetypes and their
decklists hang off it. Templates are *derived* -- the CORE/COMMON/FLEX split over a
snapshot's lists -- and coverage results are derived again, against the vault. Both
carry ``computed_at`` and their inputs' ids so a stale derivation is recomputed,
never trusted (ARCHITECTURE.md sections 3.6).
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import Float, ForeignKey, Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow

TEMPLATE_TIERS = ("CORE", "COMMON", "FLEX")
MEASUREMENTS = ("results", "popularity")
"""ADR-017: tournament results and play counts are different facts, never blended."""


class MetaSnapshot(Base):
    """One source's fetch of one format's meta."""

    __tablename__ = "meta_snapshots"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    format: Mapped[str] = mapped_column(Text(), nullable=False)
    source: Mapped[str] = mapped_column(Text(), nullable=False)
    measurement: Mapped[str] = mapped_column(Text(), nullable=False, default="results")
    """One of :data:`MEASUREMENTS` (ADR-017)."""
    snapshot_date: Mapped[str] = mapped_column(Text(), nullable=False)
    fetched_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="ok")
    """``ok`` | ``partial`` | ``failed``."""
    parser_version: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
    item_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    error: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (Index("ix_meta_snapshots_fmt_src_date", "format", "source", "snapshot_date"),)


class MetaArchetype(Base):
    """One archetype within a snapshot. In Commander, the commander is the archetype."""

    __tablename__ = "meta_archetypes"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("meta_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    name: Mapped[str] = mapped_column(Text(), nullable=False)
    archetype_key: Mapped[str] = mapped_column(Text(), nullable=False)
    meta_share_pct: Mapped[float] = mapped_column(Float(), nullable=False, default=0.0)
    placement_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    colors: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (Index("ix_meta_archetypes_key", "archetype_key", "snapshot_id"),)


class MetaDecklist(Base):
    """One real decklist attributed to an archetype."""

    __tablename__ = "meta_decklists"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    archetype_id: Mapped[int] = mapped_column(
        ForeignKey("meta_archetypes.id", ondelete="CASCADE"), nullable=False
    )
    source_url: Mapped[str] = mapped_column(Text(), nullable=False)
    event: Mapped[str | None] = mapped_column(Text())
    player: Mapped[str | None] = mapped_column(Text())
    placement: Mapped[int | None] = mapped_column(Integer())
    event_date: Mapped[str | None] = mapped_column(Text())
    raw_json: Mapped[dict[str, Any] | None] = mapped_column()

    __table_args__ = (Index("ix_meta_decklists_archetype_id", "archetype_id"),)


class MetaDecklistCard(Base):
    """One card of one ingested decklist.

    Unresolved names keep ``oracle_id`` NULL and the raw text -- reported, never
    silently dropped (ARCHITECTURE.md section 3.6).
    """

    __tablename__ = "meta_decklist_cards"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    decklist_id: Mapped[int] = mapped_column(
        ForeignKey("meta_decklists.id", ondelete="CASCADE"), nullable=False
    )
    oracle_id: Mapped[str | None] = mapped_column(Text())
    name_raw: Mapped[str] = mapped_column(Text(), nullable=False)
    quantity: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)
    board: Mapped[str] = mapped_column(Text(), nullable=False, default="main")

    __table_args__ = (
        Index("ix_meta_decklist_cards_decklist_id", "decklist_id"),
        Index("ix_meta_decklist_cards_oracle_id", "oracle_id"),
    )


class ArchetypeTemplate(Base):
    """The CORE/COMMON/FLEX reduction of one archetype's lists in one snapshot."""

    __tablename__ = "archetype_templates"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    archetype_key: Mapped[str] = mapped_column(Text(), nullable=False)
    format: Mapped[str] = mapped_column(Text(), nullable=False)
    computed_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    snapshot_id: Mapped[int] = mapped_column(
        ForeignKey("meta_snapshots.id", ondelete="CASCADE"), nullable=False
    )
    list_count: Mapped[int] = mapped_column(Integer(), nullable=False)

    __table_args__ = (Index("ix_archetype_templates_key_format", "archetype_key", "format"),)


class ArchetypeTemplateCard(Base):
    """One card of a template with its tier and typical copy count."""

    __tablename__ = "archetype_template_cards"

    template_id: Mapped[int] = mapped_column(
        ForeignKey("archetype_templates.id", ondelete="CASCADE"), primary_key=True
    )
    oracle_id: Mapped[str] = mapped_column(Text(), primary_key=True)
    tier: Mapped[str] = mapped_column(Text(), nullable=False)
    """One of :data:`TEMPLATE_TIERS`: CORE >= 80% presence, COMMON >= 40%."""
    presence_pct: Mapped[float] = mapped_column(Float(), nullable=False)
    typical_count: Mapped[int] = mapped_column(Integer(), nullable=False, default=1)


class CoverageResult(Base):
    """How much of a template the vault can field, at one point in time."""

    __tablename__ = "coverage_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    template_id: Mapped[int] = mapped_column(
        ForeignKey("archetype_templates.id", ondelete="CASCADE"), nullable=False
    )
    computed_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    weighted_coverage: Mapped[float] = mapped_column(Float(), nullable=False)
    core_coverage: Mapped[float] = mapped_column(Float(), nullable=False)
    missing_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    missing_cost_cents: Mapped[int] = mapped_column(Integer(), nullable=False)
    conflict_count: Mapped[int] = mapped_column(Integer(), nullable=False)
    rank_score: Mapped[float] = mapped_column(Float(), nullable=False)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column()

    __table_args__ = (Index("ix_coverage_results_rank", "computed_at", "rank_score"),)
