"""Gauntlet endpoints: queue a run, read the history with progress deltas."""

from __future__ import annotations

import asyncio
import itertools
from typing import Any

from fastapi import APIRouter, Query
from sqlalchemy import desc, select

from app.config import get_settings
from app.deps import Db
from app.errors import Conflict, NotFound
from app.models import GauntletRun
from app.services.rating import battles as battle_service
from app.services.rating import gauntlet as gauntlet_service
from app.services.rating import rankings as rankings_service

router = APIRouter(prefix="/gauntlet", tags=["gauntlet"])

_gauntlet_tasks: set[asyncio.Task[None]] = set()


@router.post("")
async def start(db: Db) -> dict[str, Any]:
    """Queue a full gauntlet run; ``409`` when Forge is disabled.

    Refuses while another run is still going -- the Forge sidecar plays one
    game at a time, and two interleaved runs would corrupt both scoreboards.
    """
    settings = get_settings()
    battle_service.ensure_enabled(settings)
    if await battle_service.practice_open(settings):
        raise Conflict("The practice table is open; close it before running the gauntlet")
    running = db.scalars(
        select(GauntletRun).where(GauntletRun.status == "running").limit(1)
    ).first()
    if running is not None:
        return {"run_id": running.id, "status": "running", "already": True}
    row = GauntletRun(status="running")
    db.add(row)
    db.flush()
    run_id = row.id
    db.commit()  # visible to the task's own session before we return

    task = asyncio.create_task(gauntlet_service.run_gauntlet(settings, run_id))
    _gauntlet_tasks.add(task)
    task.add_done_callback(_gauntlet_tasks.discard)
    return {"run_id": run_id, "status": "running"}


@router.get("")
def runs(db: Db, limit: int = Query(default=20, ge=1, le=100)) -> dict[str, Any]:
    """Run history, newest first, with per-candidate progress deltas.

    Each candidate carries its win-rate delta vs the previous finished run,
    matched by theme -- the "did new cards make a better deck?" readout.
    """
    rows = list(
        db.scalars(select(GauntletRun).order_by(desc(GauntletRun.id)).limit(min(limit, 100)))
    )
    serialised = [_serialise(row) for row in rows]
    # Deltas: compare each ok run to the next-older ok run.
    ok_runs = [entry for entry in serialised if entry["status"] == "ok"]
    for newer, older in itertools.pairwise(ok_runs):
        # Challengers are deliberately handicapped experiment builds: they
        # neither provide a baseline nor receive a delta, or the theme under
        # study would show its progress measured against its own sabotage.
        previous = {
            c["theme"]: c.get("win_rate")
            for c in older["candidates"]
            if c.get("role") != "challenger"
        }
        for candidate in newer["candidates"]:
            if candidate.get("role") == "challenger":
                continue
            before = previous.get(candidate["theme"])
            if before is not None and candidate.get("win_rate") is not None:
                candidate["delta"] = round(candidate["win_rate"] - before, 3)
    return {"runs": serialised}


@router.get("/rankings")
def gauntlet_rankings(db: Db) -> dict[str, Any]:
    """Elo standings, the theme-vs-archetype matchup matrix, and lessons learned."""
    from app.services.rating import learning as learning_service

    payload = rankings_service.rankings(db)
    payload["lessons"] = learning_service.all_lessons(db)
    return payload


@router.get("/{run_id}")
def run_detail(run_id: int, db: Db) -> dict[str, Any]:
    """One run in full."""
    row = db.get(GauntletRun, run_id)
    if row is None:
        raise NotFound(f"No gauntlet run {run_id}")
    return _serialise(row, with_versus=True)


def _serialise(row: GauntletRun, *, with_versus: bool = False) -> dict[str, Any]:
    detail = row.detail_json or {}
    candidates = []
    for candidate in detail.get("candidates", []):
        entry = {k: v for k, v in candidate.items() if k != "versus"}
        if with_versus:
            entry["versus"] = candidate.get("versus", [])
        candidates.append(entry)
    return {
        "id": row.id,
        "started_at": row.started_at,
        "finished_at": row.finished_at,
        "status": row.status,
        "vault_distinct": row.vault_distinct,
        "games_played": row.games_played,
        "candidates": candidates,
        "opponents": detail.get("opponents", []),
        # Present only while the run is going: who is at the table right now,
        # how far through the bracket it is, and the tallies so far.
        "live": detail.get("live"),
        "error": row.error,
    }
