"""The practice table: a real game, played through the bridge.

Forge's rules engine runs headless in the sidecar -- no display, no VNC, no
streamed desktop -- and narrates the board as JSON, which the playmat draws.
``watch`` starts a game, ``watch/events`` is what the page polls, and
``watch/answer`` and ``watch/action`` are how a person answers a prompt or
presses a button while Forge's game thread waits on them.

This replaced ADR-031 tier 3, which streamed Forge's own Swing client over
noVNC because there was no other way to play a game. Nothing streams now.
"""

from __future__ import annotations

from collections.abc import Sequence
from typing import Any

from fastapi import APIRouter
from pydantic import BaseModel, Field
from sqlalchemy import desc, select

from app.clients.forge import ForgeClient
from app.config import get_settings
from app.deps import Db
from app.errors import Conflict, NotFound
from app.models import Deck, GauntletRun
from app.services.rating import battles as battle_service
from app.services.rating.battles import dck_for_deck
from app.services.rules import profile_for

router = APIRouter(prefix="/practice", tags=["practice"])

#: How many meta decks to consider when picking an opponent.


#: How long the engine pauses after each of the AI's plays and combat steps,
#: so a person can see what it did before the next thing happens. Forge on
#: its own resolves a whole turn faster than the board can be read.
PACE_MS = 1000


class WatchRequest(BaseModel):
    """Body of ``POST /api/practice/watch``."""

    deck_id: int
    opponent_id: int | None = None
    play: bool = False
    """Sit in seat one yourself instead of watching two AIs."""
    fast: bool = False
    """Let Forge play at full speed instead of pausing after the AI's plays."""
    name: str | None = Field(default=None, max_length=24)
    """What the table calls the person in seat one. Forge invents one otherwise."""


class AnswerRequest(BaseModel):
    """Body of ``POST /api/practice/watch/answer``."""

    id: str
    value: str = ""


class ActionRequest(BaseModel):
    """Body of ``POST /api/practice/watch/action``."""

    value: str
    """ok, cancel, pass, concede, endturn, undo, alpha, resync, stop:..., or pace:<ms>."""


@router.post("/watch")
async def watch(body: WatchRequest, db: Db) -> dict[str, Any]:
    """Play one AI-vs-AI game through the bridge and narrate it.

    Not the streamed table: the bridge runs Forge's engine headless -- no
    display, no VNC -- and reports the board as JSON, which the playmat draws
    itself. This is the spectator half; the human seat comes later.
    """
    settings = get_settings()
    battle_service.ensure_enabled(settings)
    _refuse_during_gauntlet(db)

    deck = db.get(Deck, body.deck_id)
    if deck is None:
        raise NotFound(f"No deck {body.deck_id}")
    commander = profile_for(deck.format).has_commander
    if body.opponent_id is not None:
        named = db.get(Deck, body.opponent_id)
        candidates = [named] if named is not None else []
    else:
        candidates = _decks(db)
    opponent = choose_opponent(deck, candidates)
    if opponent is None:
        kind = "Commander" if commander else "60-card"
        raise Conflict(f"No {kind} [Meta] opponent to watch this deck against")

    settings_client = ForgeClient(settings)
    pushed, skipped = await _push_all(db, settings, [deck, opponent])
    if len(pushed) < 2:
        reason = skipped[0]["reason"] if skipped else "a deck could not be prepared"
        raise Conflict(str(reason))

    payload = await settings_client.request_json(
        f"{settings.forge_url.rstrip('/')}/bridge/start",
        method="POST",
        json={
            "decks": [f"{name}.dck" for name in pushed[:2]],
            "format": "Commander" if commander else "Constructed",
            "human": body.play,
            "pace": 0 if body.fast else PACE_MS,
            "name": (body.name or "").strip(),
        },
    )
    if isinstance(payload, dict) and payload.get("error"):
        raise Conflict(str(payload["error"]))
    return {"running": True, "decks": pushed[:2], "playing": body.play}


@router.get("/watch/events")
async def watch_events(since: int = 0) -> dict[str, Any]:
    """Board events after ``since``; the page polls this while a game runs."""
    settings = get_settings()
    battle_service.ensure_enabled(settings)
    client = ForgeClient(settings)
    payload = await client.request_json(
        f"{settings.forge_url.rstrip('/')}/bridge/events?since={max(0, int(since))}"
    )
    if not isinstance(payload, dict):
        payload = {}
    return {
        "running": bool(payload.get("running")),
        "next": int(payload.get("next") or 0),
        "events": payload.get("events") or [],
        "error": payload.get("error"),
    }


@router.post("/watch/answer")
async def watch_answer(body: AnswerRequest) -> dict[str, Any]:
    """Answer a prompt the game is blocked on.

    Forge's prompts are synchronous: the engine holds its game thread inside
    the call until this arrives. That is the same shape Forge's own online
    play uses, and the reason the bridge has a timeout at all.
    """
    settings = get_settings()
    battle_service.ensure_enabled(settings)
    client = ForgeClient(settings)
    payload = await client.request_json(
        f"{settings.forge_url.rstrip('/')}/bridge/answer",
        method="POST",
        json={"id": body.id, "value": body.value},
    )
    if isinstance(payload, dict) and payload.get("error"):
        raise Conflict(str(payload["error"]))
    return {"delivered": True}


@router.post("/watch/action")
async def watch_action(body: ActionRequest) -> dict[str, Any]:
    """Press a button, pass priority, or concede.

    Distinct from an answer: mulligans and priority are advertised through
    Forge's button pair rather than as a prompt, and go back to the engine
    through IGameController.
    """
    settings = get_settings()
    battle_service.ensure_enabled(settings)
    client = ForgeClient(settings)
    payload = await client.request_json(
        f"{settings.forge_url.rstrip('/')}/bridge/action",
        method="POST",
        json={"value": body.value},
    )
    if isinstance(payload, dict) and payload.get("error"):
        raise Conflict(str(payload["error"]))
    return {"delivered": True}


@router.post("/watch/stop")
async def watch_stop() -> dict[str, Any]:
    """Abandon a watched game."""
    settings = get_settings()
    battle_service.ensure_enabled(settings)
    client = ForgeClient(settings)
    await client.request_json(
        f"{settings.forge_url.rstrip('/')}/bridge/stop", method="POST", json={}
    )
    return {"running": False}


def _decks(db: Db) -> list[Deck]:
    """Every deck, archived or not -- the gauntlet's are created archived.

    Not only built ones. Built means physically sleeved from owned cards, and
    Forge plays a list whether or not its cards are in a box; filtering on it
    left one eligible opponent in the whole vault.
    """
    return list(db.scalars(select(Deck).order_by(desc(Deck.updated_at))).all())


def choose_opponent(deck: Deck, candidates: Sequence[Deck | None]) -> Deck | None:
    """Who the table seats across from ``deck`` when nobody is named.

    Same format family, or the game is nonsense: a 100-card Commander list
    played as Constructed is a 100-card deck with no commander, which is what
    the first watched game turned out to be.

    A real deck before a "[Meta]" cut. The table used to take the first meta
    deck it found, and all three of those are cEDH commander lists cut to
    sixty -- five to eight creatures, sixteen counterspells, win conditions
    that need the commanders the cut left out. Forge's own pre-game notice
    listed most of such a deck as cards its AI cannot play well, and the game
    was an opponent playing lands and one enchantment for twelve turns. The
    engine was fine; the deck had nothing to do.
    """
    commander = profile_for(deck.format).has_commander
    fitting = [
        row
        for row in candidates
        if row is not None
        and row.id != deck.id
        and profile_for(row.format).has_commander is commander
    ]
    for row in fitting:
        if row.source != "gauntlet_meta":
            return row
    return fitting[0] if fitting else None


def _refuse_during_gauntlet(db: Db) -> None:
    if (
        db.scalars(select(GauntletRun).where(GauntletRun.status == "running").limit(1)).first()
        is not None
    ):
        raise Conflict("A gauntlet run is in progress; the table opens when it finishes")


def practice_name(deck: Deck, taken: set[str]) -> str:
    """The name this deck wears in Forge's New Game picker.

    The gauntlet's ``[#id]`` suffix exists so a log parser can attribute wins
    by substring. A person reading a list needs none of that, and the id churns
    -- the meta job renumbers its decks every week -- which breaks Forge's own
    memory of the deck you played last time and leaves the picker full of
    near-identical names. Clean and stable here; the id comes back only to
    separate two decks that would genuinely collide.

    Slashes are spent before the sidecar sees them. Forge maps a slash to an
    underscore because it cannot go in a file name, which turned a deck led by
    a double-faced commander into "Ral, Monsoon Mage __ Ral, Leyline Prodigy"
    and a counters deck into "+1_+1 counters" -- names that read as damage.
    """
    base = " ".join(deck.name.split())
    # A double-faced commander names the deck twice; the front face is the name
    # anyone would use, and forge_card_name already makes that choice for cards.
    base = base.split("//")[0].strip() or base.strip()
    # A partner pair reads as a pair; "+1/+1" reads as it is said.
    base = base.replace(" / ", " - ").replace("/", "")
    base = " ".join(base.split())[:60].strip() or "Deck"
    if base.casefold() not in taken:
        return base
    return f"{base} [#{deck.id}]"


async def _push_all(
    db: Db, settings: Any, decks: list[Deck]
) -> tuple[list[str], list[dict[str, Any]]]:
    """File each deck, skipping the ones Forge could not be given.

    A single empty deck used to 404 the whole table open. One unusable deck on
    the shelf is not a reason to refuse someone a game, so it is reported and
    stepped over.
    """
    client = ForgeClient(settings)
    pushed: list[str] = []
    skipped: list[dict[str, Any]] = []
    taken: set[str] = set()
    seen_ids: set[int] = set()
    for deck in decks:
        if deck.id in seen_ids:
            continue
        seen_ids.add(deck.id)
        name = practice_name(deck, taken)
        try:
            _, dck = dck_for_deck(db, deck)
        except NotFound as error:
            skipped.append({"deck_id": deck.id, "name": deck.name, "reason": str(error)})
            continue
        payload = await client.request_json(
            f"{settings.forge_url.rstrip('/')}/practice/deck",
            method="POST",
            json={
                "name": name,
                "dck": _retitled(dck, name),
                "format": "Commander" if profile_for(deck.format).has_commander else "Constructed",
            },
        )
        # The sidecar files the deck under the name Forge itself would choose;
        # trust its answer over ours so the page shows what the picker shows.
        filed = payload.get("name") if isinstance(payload, dict) else None
        final = str(filed or name)
        taken.add(final.casefold())
        pushed.append(final)
    return pushed, skipped


def _retitled(dck: str, name: str) -> str:
    """Rewrite the deck text's ``Name=`` to the practice name."""
    lines = dck.splitlines()
    for index, line in enumerate(lines):
        if line.startswith("Name="):
            lines[index] = f"Name={name}"
            break
    return "\n".join(lines) + "\n"
