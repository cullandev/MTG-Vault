"""API routers.

Two routers are mounted under ``/api``:

* ``public_router`` carries the CSRF dependency only, and holds the handful of auth
  endpoints that cannot require a session.
* ``router`` carries session **and** CSRF dependencies. Everything else goes here, so
  a new endpoint is authenticated by construction (ADR-013).
"""

from __future__ import annotations

from fastapi import APIRouter, Depends

from app.api import (
    auth,
    battles,
    cards,
    collection,
    dashboard,
    decks,
    gauntlet,
    meta,
    practice,
    rating,
    scan,
    sets,
    settings,
    synergy,
    system,
    wishlist,
)
from app.deps import require_csrf, require_session

public_router = APIRouter(prefix="/api")
public_router.include_router(auth.router)

router = APIRouter(
    prefix="/api",
    dependencies=[Depends(require_session), Depends(require_csrf)],
)
router.include_router(cards.router)
router.include_router(collection.router)
router.include_router(dashboard.router)
router.include_router(decks.router)
router.include_router(battles.router)
router.include_router(gauntlet.router)
router.include_router(meta.router)
router.include_router(practice.router)
router.include_router(rating.router)
router.include_router(scan.router)
router.include_router(sets.router)
router.include_router(settings.router)
router.include_router(synergy.router)
router.include_router(system.router)
router.include_router(wishlist.router)

__all__ = ["public_router", "router"]
