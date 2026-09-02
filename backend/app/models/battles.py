"""Recorded Forge battle results (ADR-031, tier 2)."""

from __future__ import annotations

from typing import Any

from sqlalchemy import Index, Integer, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, utcnow


class GauntletRun(Base):
    """One run of the meta gauntlet: fresh vault decks vs real internet lists.

    Runs persist so they can be compared over time -- the point is to watch
    win rates move as newly scanned cards change what the vault can build.
    """

    __tablename__ = "gauntlet_runs"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    started_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    finished_at: Mapped[str | None] = mapped_column(Text())
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="running")
    """``running`` | ``ok`` | ``failed``."""
    vault_distinct: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    """Distinct owned cards at run time -- the x-axis of progress."""
    games_played: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    detail_json: Mapped[dict[str, Any] | None] = mapped_column()
    """``{candidates:[{deck_id, name, theme, structure, wins, games, win_rate,
    opponents:[...]}], opponents:[{deck_id, name, archetype}]}``."""
    error: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (Index("ix_gauntlet_runs_started_at", "started_at"),)


class BattleResult(Base):
    """One simulated match between stored decks, as Forge played it.

    Deck references are names plus ids at the time of the run -- deliberately not
    foreign keys, so deleting a deck does not erase the history of games it played.
    """

    __tablename__ = "battle_results"

    id: Mapped[int] = mapped_column(primary_key=True, autoincrement=True)
    ran_at: Mapped[str] = mapped_column(Text(), nullable=False, default=utcnow)
    engine: Mapped[str] = mapped_column(Text(), nullable=False, default="forge")
    engine_version: Mapped[str | None] = mapped_column(Text())
    format: Mapped[str] = mapped_column(Text(), nullable=False)
    games_requested: Mapped[int] = mapped_column(Integer(), nullable=False)
    games_completed: Mapped[int] = mapped_column(Integer(), nullable=False, default=0)
    status: Mapped[str] = mapped_column(Text(), nullable=False, default="running")
    """``running`` | ``ok`` | ``failed``."""
    duration_ms: Mapped[int | None] = mapped_column(Integer())
    decks_json: Mapped[list[Any] | None] = mapped_column()
    """``[{deck_id, name, wins}]`` in seat order; draws live in ``detail_json``."""
    detail_json: Mapped[dict[str, Any] | None] = mapped_column()
    """Draws, unrecognised cards, the log tail, and the raw win lines."""
    error: Mapped[str | None] = mapped_column(Text())

    __table_args__ = (Index("ix_battle_results_ran_at", "ran_at"),)
