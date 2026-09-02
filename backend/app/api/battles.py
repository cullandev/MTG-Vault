"""Battle endpoints: enqueue a Forge match, read the results.

A battle takes minutes; POST answers immediately with the recorded row's id and
the match runs as a background task that updates the row and drops an inbox
notification -- the same fire-and-hold pattern as the meta refresh.
"""

from __future__ import annotations

import asyncio
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field, field_validator
from sqlalchemy import desc, select

from app.config import get_settings
from app.deps import Db
from app.errors import NotFound
from app.models import BattleResult, Deck
from app.services.rating import battles as battle_service
from app.services.rules import profile_for

router = APIRouter(prefix="/battles", tags=["battles"])

_battle_tasks: set[asyncio.Task[None]] = set()


class BattleRequest(BaseModel):
    """Body of ``POST /api/battles``."""

    deck_ids: list[int] = Field(min_length=2, max_length=4)
    games: int | None = Field(default=None, ge=1, le=20)

    @field_validator("deck_ids")
    @classmethod
    def _distinct(cls, deck_ids: list[int]) -> list[int]:
        if len(set(deck_ids)) != len(deck_ids):
            raise ValueError("a deck cannot battle itself; deck_ids must be distinct")
        return deck_ids


@router.post("")
async def start_battle(body: BattleRequest, db: Db) -> dict[str, Any]:
    """Queue a real Forge match between stored decks; ``409`` when disabled."""
    settings = get_settings()
    battle_service.ensure_enabled(settings)
    decks = []
    for deck_id in body.deck_ids:
        deck = db.get(Deck, deck_id)
        if deck is None:
            raise NotFound(f"No deck {deck_id}")
        decks.append(deck)
    games = body.games or settings.forge_games_default

    row = BattleResult(
        format=(
            "Commander"
            if all(profile_for(d.format).has_commander for d in decks)
            else "Constructed"
        ),
        games_requested=games,
        status="running",
    )
    db.add(row)
    db.flush()
    battle_id = row.id
    db.commit()  # visible to the task's own session before we return

    # Manual battles run verbose: a handful of games whose whole point is
    # watching them play out. The gauntlet's 27-game sweeps stay quiet.
    task = asyncio.create_task(
        battle_service.run_battle(settings, battle_id, body.deck_ids, games, verbose=True)
    )
    _battle_tasks.add(task)
    task.add_done_callback(_battle_tasks.discard)
    return {"battle_id": battle_id, "games": games, "status": "running"}


@router.get("")
def list_battles(db: Db, limit: int = 20) -> dict[str, Any]:
    """Recent battles, newest first."""
    rows = db.scalars(select(BattleResult).order_by(desc(BattleResult.id)).limit(min(limit, 100)))
    return {"battles": [_serialise(row) for row in rows]}


@router.get("/{battle_id}")
def get_battle(battle_id: int, db: Db) -> dict[str, Any]:
    """One battle, with its detail."""
    row = db.get(BattleResult, battle_id)
    if row is None:
        raise NotFound(f"No battle {battle_id}")
    return _serialise(row, with_detail=True)


def _serialise(row: BattleResult, *, with_detail: bool = False) -> dict[str, Any]:
    body: dict[str, Any] = {
        "id": row.id,
        "ran_at": row.ran_at,
        "engine": row.engine,
        "engine_version": row.engine_version,
        "format": row.format,
        "games_requested": row.games_requested,
        "games_completed": row.games_completed,
        "status": row.status,
        "duration_ms": row.duration_ms,
        "decks": row.decks_json or [],
        "draws": (row.detail_json or {}).get("draws", 0),
        "unknown_cards": (row.detail_json or {}).get("unknown_cards", []),
        "error": row.error,
    }
    if with_detail:
        body["detail"] = row.detail_json or {}
    return body
