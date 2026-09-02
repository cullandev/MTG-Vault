"""Real battles: stored decks handed to Forge, results recorded (ADR-031 tier 2).

The serialisation rules follow the app's own name conventions (ARCHITECTURE.md
section 6): a transform DFC goes into the .dck under its front-face name, split
and adventure cards under the combined ``a // b`` name -- which is also what
Forge's card scripts answer to. Cards Forge does not recognise come back in the
result's ``unknown_cards`` rather than silently shrinking the deck.
"""

from __future__ import annotations

import logging

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.forge import (
    PARSER_VERSION,
    ForgeClient,
    parse_game_timelines,
    parse_sim_output,
)
from app.config import Settings
from app.db import session_scope
from app.errors import FeatureDisabled, NotFound
from app.models import BattleResult, Deck, DeckCard, Notification, OracleCard
from app.services.rules import profile_for

log = logging.getLogger("mtgvault.battles")

#: Layouts whose deck-list (and Forge) name is the front face only.
#
#: ``adventure`` belongs here and was missing. An Adventure card is ONE card
#: with a spell half, referred to everywhere by its creature name -- Forge
#: scripts it as "Bilbo Baggins, Burglar", never "Bilbo Baggins, Burglar //
#: Take a Glance". Sending the combined name made Forge log "an unsupported
#: card was requested" and play the deck WITHOUT it, so every battle involving
#: one was fought a card short and nothing said so. Measured against Forge's
#: 34,532 scripted names: all 13 Adventure cards in the vault miss on the full
#: name and hit on the front name.
#:
#: ``split`` deliberately stays out: Forge does script "Fire // Ice".
_FRONT_NAME_LAYOUTS = frozenset(
    {"transform", "modal_dfc", "flip", "meld", "reversible_card", "adventure"}
)


def ensure_enabled(settings: Settings) -> None:
    """Raise unless the battle sidecar is switched on.

    Raises:
        FeatureDisabled: ``ENABLE_FORGE`` is off. The message says how to turn
            it on, because the sidecar also needs its compose profile started.
    """
    if not settings.enable_forge:
        raise FeatureDisabled(
            "Battles are disabled. Start the sidecar (docker compose --profile "
            "battles up -d forge) and set ENABLE_FORGE=true",
            code="battles_disabled",
        )


def forge_card_name(oracle: OracleCard) -> str:
    """The name Forge's card scripts answer to."""
    if oracle.layout in _FRONT_NAME_LAYOUTS:
        return oracle.name_front
    return oracle.name


def dck_for_deck(db: DbSession, deck: Deck) -> tuple[str, str]:
    """Serialise a stored deck to Forge's .dck format.

    Returns:
        ``(deck name, dck text)``. The name is what the log parser will look for
        in Forge's win lines, so it is sanitised to a single line.
    """
    rows = db.execute(
        select(DeckCard, OracleCard)
        .join(OracleCard, OracleCard.oracle_id == DeckCard.oracle_id)
        .where(DeckCard.deck_id == deck.id, DeckCard.board.in_(("main", "commander")))
        .order_by(DeckCard.board, OracleCard.name)
    ).all()
    if not rows:
        raise NotFound(f"Deck {deck.id} has no cards to battle with")

    # The [#id] suffix makes every submitted name unique and non-prefixing --
    # "Burn [#1]" is never a substring of "Burn [#12]" -- so win attribution by
    # substring cannot conflate two decks (or a deck battling itself).
    name = f"{' '.join(deck.name.split())[:52] or 'Deck'} [#{deck.id}]"
    lines = ["[metadata]", f"Name={name}"]
    commanders = [(row, oracle) for row, oracle in rows if row.board == "commander"]
    if commanders:
        lines.append("[Commander]")
        for row, oracle in commanders:
            lines.append(f"{row.quantity} {forge_card_name(oracle)}")
    lines.append("[Main]")
    for row, oracle in rows:
        if row.board == "main":
            lines.append(f"{row.quantity} {forge_card_name(oracle)}")
    return name, "\n".join(lines) + "\n"


async def practice_open(settings: Settings, *, client: ForgeClient | None = None) -> bool:
    """Whether a practice game currently holds the sidecar's heap.

    The practice table used to be Forge's own client on a virtual display, and
    this probed ``/practice/status``. It is the bridge now -- a headless game
    narrating itself as JSON -- so the question is the same and the endpoint
    is not. ``since`` is set past the end so the probe carries no events back.

    Errors read as "not open": when the sidecar is unreachable the caller's own
    simulate call will say so with a better message than this probe could.
    """
    try:
        forge = client or ForgeClient(settings)
        payload = await forge.request_json(
            f"{settings.forge_url.rstrip('/')}/bridge/events?since=1000000000"
        )
    except Exception:
        return False
    return isinstance(payload, dict) and bool(payload.get("running"))


async def run_battle(
    settings: Settings,
    battle_id: int,
    deck_ids: list[int],
    games: int,
    *,
    client: ForgeClient | None = None,
    notify: bool = True,
    verbose: bool = False,
) -> None:
    """Execute one battle and record its outcome. Never raises.

    Runs as a background task: every exit path updates the ``battle_results``
    row and drops a notification, because a battle that silently vanished would
    look identical to one still running. The gauntlet passes ``notify=False``:
    nine battles in a row deserve one summary notification, not nine.
    """
    try:
        with session_scope() as db:
            decks = [_deck(db, deck_id) for deck_id in deck_ids]
            deck_files = [dck_for_deck(db, deck) for deck in decks]
            names = [name for name, _dck in deck_files]
            # Structure decides the Forge mode, not the format name: a house-rules
            # casual_commander deck is still a Commander game (40 life, command zone).
            game_format = (
                "Commander"
                if all(profile_for(deck.format).has_commander for deck in decks)
                else "Constructed"
            )
        forge = client or ForgeClient(settings)
        raw = await forge.simulate(
            deck_files, games=games, game_format=game_format, verbose=verbose
        )
        stdout_text = str(raw.get("stdout") or "")
        outcome = parse_sim_output(stdout_text, names)
        timelines = parse_game_timelines(stdout_text, names) if verbose else []

        with session_scope() as db:
            row = db.get(BattleResult, battle_id)
            if row is None:
                return
            row.status = "ok" if outcome.games_completed > 0 else "failed"
            row.games_completed = outcome.games_completed
            row.duration_ms = int(raw.get("duration_ms") or 0)
            row.decks_json = [
                {"deck_id": deck_id, "name": name, "wins": outcome.wins.get(name, 0)}
                for deck_id, name in zip(deck_ids, names, strict=True)
            ]
            row.detail_json = {
                "draws": outcome.draws,
                "unknown_cards": outcome.unknown_cards,
                "win_lines": outcome.win_lines[:20],
                "parser_version": PARSER_VERSION,
                "exit_code": raw.get("exit_code"),
                "log_tail": stdout_text[-4000:],
                # Per-turn playback (plays, combat, life) when the battle ran
                # verbose -- manual battles do; the gauntlet stays quiet.
                "games": timelines,
            }
            if outcome.games_completed == 0:
                # A sidecar refusal (practice table open, deck-count contract)
                # arrives as {"error": ...} at 200 -- show its words, not a shrug.
                row.error = (
                    str(raw.get("error") or "")
                    or "Forge ran but no game result could be attributed"
                )
            if notify:
                _notify(db, row, names)
    except Exception as error:
        log.exception("battle_failed", extra={"battle_id": battle_id})
        with session_scope() as db:
            row = db.get(BattleResult, battle_id)
            if row is not None:
                row.status = "failed"
                row.error = f"{type(error).__name__}: {error}"
                if notify:
                    _notify(db, row, [])


def _deck(db: DbSession, deck_id: int) -> Deck:
    deck = db.get(Deck, deck_id)
    if deck is None:
        raise NotFound(f"No deck {deck_id}")
    return deck


def _notify(db: DbSession, row: BattleResult, names: list[str]) -> None:
    if row.status == "ok":
        scores = ", ".join(f"{entry['name']} {entry['wins']}" for entry in (row.decks_json or []))
        title = f"Battle finished: {scores or 'no result'}"
        body = f"{row.games_completed} game(s) played by Forge in {row.duration_ms} ms."
    else:
        title = "Battle failed"
        body = row.error or "Forge did not return a usable result."
    db.add(Notification(kind="battle", title=title, body=body, link="/battles"))
