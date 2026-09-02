"""Client for the Forge battle sidecar, and the parser for Forge's game log.

The sidecar (docker/forge) is deliberately dumb -- it runs the jar and returns
raw stdout -- so everything with judgement in it lives here, unit-testable: the
.dck serialisation is in ``services/rating/battles.py``, and this parser turns
Forge's log into per-deck win counts without trusting any one line format.
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from typing import Any, ClassVar

from app.clients.base import ExternalClient, SourceResponseError
from app.config import Settings

PARSER_VERSION = 1

#: Forge prints two "has won" lines per game -- "Game Outcome: ..." and the
#: canonical "Game Result: Game N ended in M ms. Ai(N)-<deck> has won!" (observed
#: live from 2.0.14). Only the Game Result line counts, or every win doubles;
#: the outcome lines still land in ``win_lines`` for the record.
_WIN_LINE = re.compile(r"^(?P<line>Game Result:.*\bhas won\b.*)$", re.MULTILINE | re.IGNORECASE)
_ANY_WIN_LINE = re.compile(r"^(?P<line>.*\bhas won\b.*)$", re.MULTILINE | re.IGNORECASE)
_DRAW_LINE = re.compile(r"ended in a draw|\bDraw!\B|game is a draw", re.IGNORECASE)
_UNKNOWN_CARD = re.compile(
    r"(?:unknown card|could not (?:find|load) card|no card named)[:\s]+\"?([^\"\n]+)",
    re.IGNORECASE,
)


class ForgeClient(ExternalClient):
    """Access to the sidecar's two endpoints."""

    service: ClassVar[str] = "forge"
    timeout_s: ClassVar[float] = 920.0
    # A simulation is minutes of CPU; a failure is investigated, never re-run
    # automatically.
    max_attempts: ClassVar[int] = 1
    respect_robots: ClassVar[bool] = False

    def __init__(self, settings: Settings, **kwargs: Any) -> None:
        super().__init__(settings.scryfall_user_agent, **kwargs)
        self._base = settings.forge_url.rstrip("/")

    async def health(self) -> dict[str, Any]:
        """The sidecar's liveness answer, including the Forge version."""
        payload = await self.request_json(f"{self._base}/health")
        if not isinstance(payload, dict):
            raise SourceResponseError("forge sidecar returned a non-object health body")
        return payload

    async def simulate(
        self,
        decks: list[tuple[str, str]],
        *,
        games: int,
        game_format: str,
        verbose: bool = False,
    ) -> dict[str, Any]:
        """Run one simulated match; returns the sidecar's raw result.

        ``verbose`` asks Forge for the full per-turn game log (plays, life
        totals) instead of the quiet result lines -- the battle playback's
        source material.
        """
        payload = await self.request_json(
            f"{self._base}/simulate",
            method="POST",
            json={
                "decks": [{"name": name, "dck": dck} for name, dck in decks],
                "games": games,
                "format": game_format,
                "verbose": verbose,
            },
        )
        if not isinstance(payload, dict) or "stdout" not in payload:
            raise SourceResponseError("forge sidecar returned no stdout")
        return payload


@dataclass
class SimOutcome:
    """Forge's log, reduced to what the battle record stores."""

    wins: dict[str, int]
    draws: int = 0
    unknown_cards: list[str] = field(default_factory=list)
    win_lines: list[str] = field(default_factory=list)

    @property
    def games_completed(self) -> int:
        """Games with an attributed outcome: wins we recognised plus draws."""
        return sum(self.wins.values()) + self.draws


_ID_SUFFIX = re.compile(r"\[#(\d+)\]")


def parse_sim_output(stdout: str, deck_names: list[str]) -> SimOutcome:
    """Attribute wins in Forge's log to the decks we submitted.

    Attribution keys on the ``[#id]`` suffix every submitted deck name carries,
    because Forge SANITISES names in its logs: "+1/+1 counters" comes back as
    "+1_+1 counters" and "Kraum / Tymna" as "Kraum _ Tymna", and full-name
    matching silently dropped every game such a deck won -- the +1/+1 deck went
    3-0 against Kraum/Tymna and was recorded as a FAILED battle, while opponent
    victories vanished from run totals for weeks. The numeric id survives any
    sanitisation. Name-substring matching remains as a fallback for decks
    without a suffix. A win line matching nothing stays visible in
    ``win_lines``, so a format change surfaces rather than silently zeroing.
    """
    outcome = SimOutcome(wins=dict.fromkeys(deck_names, 0))
    for match in _ANY_WIN_LINE.finditer(stdout):
        outcome.win_lines.append(match.group("line").strip()[:200])
    by_id: dict[str, str] = {}
    for name in filter(None, deck_names):
        id_match = _ID_SUFFIX.search(name)
        if id_match:
            by_id[id_match.group(1)] = name
    # Longest name first, so "Burn deluxe" is never claimed by "Burn".
    by_length = sorted(filter(None, deck_names), key=len, reverse=True)
    for match in _WIN_LINE.finditer(stdout):
        line = match.group("line")
        line_id = _ID_SUFFIX.search(line)
        if line_id and line_id.group(1) in by_id:
            outcome.wins[by_id[line_id.group(1)]] += 1
            continue
        for name in by_length:
            if name in line:
                outcome.wins[name] += 1
                break
    # Draws only from Game Result lines: Forge also prints per-game "Game
    # Outcome" wording, which would double-count.
    outcome.draws = sum(
        1
        for log_line in stdout.splitlines()
        if log_line.startswith("Game Result:") and _DRAW_LINE.search(log_line)
    )
    seen: set[str] = set()
    for card_match in _UNKNOWN_CARD.finditer(stdout):
        name = card_match.group(1).strip()
        if name and name not in seen:
            seen.add(name)
            outcome.unknown_cards.append(name)
    return outcome


# -- verbose playback --------------------------------------------------------

_TURN_LINE = re.compile(r"^Turn: Turn (\d+) \((.+)\)\s*$")
_LAND_LINE = re.compile(r"^Land: (.+?) played (.+?)(?: \(\d+\))?\s*$")
_CAST_LINE = re.compile(r"^Add To Stack: (.+?) (cast|triggered) (.+?)(?: targeting .*)?\s*$")
_DAMAGE_LINE = re.compile(r"^Damage: (.+?) deals (\d+) .*damage to (.+?)\.\s*$")
_LIFE_LINE = re.compile(r"^Life: Life: (.+?) (-?\d+) > (-?\d+)\s*$")
_ATTACK_LINE = re.compile(r"^Combat: (.+?) assigned (.+) to attack (.+?)\.?\s*$")
_GAME_END = re.compile(r"^Game Result: Game (\d+) ended")

MAX_TURNS_KEPT = 40
MAX_EVENTS_PER_TURN = 40


def parse_game_timelines(stdout: str, deck_names: list[str]) -> list[dict[str, Any]]:
    """Turn a verbose Forge log into per-game, per-turn playback.

    One dict per game: ``{turns: [{turn, active, events, life}], outcome}``.
    ``life`` is each player's total at the end of that turn (only players whose
    total changed since the game started appear). Events are strings, plays and
    combat only -- phases and mana are noise at this altitude. Card instance
    numbers ("Mountain (141)") are stripped; Forge's "Ai(1)-" player prefixes
    are mapped back to the submitted deck names, longest first.
    """
    by_length = sorted(filter(None, deck_names), key=len, reverse=True)

    def player(raw: str) -> str:
        for name in by_length:
            if name in raw:
                return name
        return re.sub(r"^Ai\(\d+\)-", "", raw)

    def strip_ids(text: str) -> str:
        return re.sub(r" \(\d+\)", "", text)

    games: list[dict[str, Any]] = []
    turns: list[dict[str, Any]] = []
    current: dict[str, Any] | None = None
    life: dict[str, int] = {}
    outcome: list[str] = []

    def add_event(kind: str, text: str, *, card: str | None = None) -> None:
        """Structured, so the UI can hover-preview the card an event names."""
        if current is not None and len(current["events"]) < MAX_EVENTS_PER_TURN:
            event: dict[str, Any] = {"kind": kind, "text": text}
            if card:
                event["card"] = card
            current["events"].append(event)

    for line in stdout.splitlines():
        turn_match = _TURN_LINE.match(line)
        if turn_match:
            current = {
                "turn": int(turn_match.group(1)),
                "active": player(turn_match.group(2)),
                "events": [],
                "life": {},
            }
            if len(turns) < MAX_TURNS_KEPT:
                turns.append(current)
            continue
        land = _LAND_LINE.match(line)
        if land:
            add_event("land", f"{player(land.group(1))} played", card=strip_ids(land.group(2)))
            continue
        cast = _CAST_LINE.match(line)
        if cast:
            verb = "cast" if cast.group(2) == "cast" else "triggered"
            add_event("cast", f"{player(cast.group(1))} {verb}", card=strip_ids(cast.group(3)))
            continue
        attack = _ATTACK_LINE.match(line)
        if attack:
            add_event(
                "attack",
                f"{player(attack.group(1))} attacks with {strip_ids(attack.group(2))}",
            )
            continue
        damage = _DAMAGE_LINE.match(line)
        if damage:
            add_event(
                "damage",
                f"deals {damage.group(2)} to {player(damage.group(3))}",
                card=strip_ids(damage.group(1)),
            )
            continue
        life_match = _LIFE_LINE.match(line)
        if life_match:
            who = player(life_match.group(1))
            after = int(life_match.group(3))
            life[who] = after
            if current is not None:
                current["life"] = dict(life)
            add_event("life", f"{who} to {after} life")
            continue
        if line.startswith("Game Outcome: "):
            outcome.append(line.removeprefix("Game Outcome: ").strip()[:160])
            continue
        if _GAME_END.match(line):
            games.append({"turns": turns, "outcome": outcome})
            turns, current, life, outcome = [], None, {}, []

    if turns or outcome:
        games.append({"turns": turns, "outcome": outcome})
    return games
