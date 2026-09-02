"""The Forge adapter: .dck serialisation, log parsing, and the battle lifecycle.

The sidecar itself is a JRE and one dumb script; everything with judgement is
here and tested. The win-line samples are synthetic until the first live run
confirms the exact wording -- which is why the parser keeps unattributed win
lines visible instead of silently dropping them.
"""

from __future__ import annotations

from typing import Any

import pytest
from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.clients.forge import parse_sim_output
from app.config import get_settings
from app.errors import FeatureDisabled
from app.models import BattleResult, Notification
from app.services.decks import crud
from app.services.rating import battles
from tests.unit.meta.conftest import make_card

#: Transcribed from a live Forge 2.0.14 run (battle 3, 2026-08-27): every game
#: prints a "Game Outcome:" line AND a "Game Result:" line -- only the latter
#: counts, or wins double.
SAMPLE_LOG = """
Simulation mode
Warning: unknown card: Made Up Cardname
Game Outcome: Ai(0)-Deck Alpha has won because all opponents have lost
Game Result: Game 1 ended in 1707 ms. Ai(0)-Deck Alpha has won!
Game Outcome: Ai(1)-Deck Beta has won because all opponents have lost
Game Result: Game 2 ended in 1133 ms. Ai(1)-Deck Beta has won!
Game Result: Game 3 ended in a Draw! Took 2000 ms.
"""


def test_sim_log_parses_wins_draws_and_unknowns() -> None:
    outcome = parse_sim_output(SAMPLE_LOG, ["Deck Alpha", "Deck Beta"])
    assert outcome.wins == {"Deck Alpha": 1, "Deck Beta": 1}
    assert outcome.draws == 1
    assert outcome.games_completed == 3
    assert outcome.unknown_cards == ["Made Up Cardname"]


def test_unattributed_wins_stay_visible() -> None:
    """A Forge wording change must surface, not read as zero games."""
    outcome = parse_sim_output(
        "Game Result: Game 1 ended in 5 ms. Somebody Unexpected has won!", ["Deck Alpha"]
    )
    assert outcome.wins == {"Deck Alpha": 0}
    assert outcome.games_completed == 0
    assert len(outcome.win_lines) == 1


def test_wins_attribute_by_id_suffix_despite_forge_name_sanitising() -> None:
    """The +1/+1 counters regression: Forge logs "+1_+1 counters (60) [#67]"
    for a deck submitted as "+1/+1 counters (60) [#67]" -- slashes become
    underscores. Name matching dropped every such win (a 3-0 sweep was
    recorded as a FAILED battle); the [#id] token survives sanitising."""
    log_text = (
        "Game Result: Game 1 ended in 10 ms. Ai(1)-+1_+1 counters (60) [#67] has won!\n"
        "Game Result: Game 2 ended in 12 ms. Ai(2)-Kraum, Ludevic's Opus _ Tymna [#68] has won!\n"
        "Game Result: Game 3 ended in 9 ms. Ai(1)-+1_+1 counters (60) [#67] has won!"
    )
    outcome = parse_sim_output(
        log_text,
        ["+1/+1 counters (60) [#67]", "Kraum, Ludevic's Opus / Tymna [#68]"],
    )
    assert outcome.wins["+1/+1 counters (60) [#67]"] == 2
    assert outcome.wins["Kraum, Ludevic's Opus / Tymna [#68]"] == 1
    assert outcome.games_completed == 3


def test_outcome_lines_do_not_double_count() -> None:
    outcome = parse_sim_output(SAMPLE_LOG, ["Deck Alpha", "Deck Beta"])
    assert outcome.games_completed == 3  # not 5: outcome lines are records, not wins


def test_dck_serialisation_uses_the_right_names(catalog: DbSession) -> None:
    """Commander section, front-face names for DFCs, combined names for splits."""
    from app.models import OracleCard

    bruna = catalog.scalars(
        select(OracleCard).where(OracleCard.name == "Bruna, the Fading Light")
    ).one()
    delver = catalog.scalars(
        select(OracleCard).where(OracleCard.name_front == "Delver of Secrets")
    ).one()
    fire_ice = catalog.scalars(select(OracleCard).where(OracleCard.name == "Fire // Ice")).one()

    deck, _batch = crud.create_deck(
        catalog,
        crud.DeckSpec(
            name="  Serialise   me  ", format="commander", commander_oracle_id=bruna.oracle_id
        ),
    )
    crud.set_card(catalog, deck.id, crud.CardSpec(oracle_id=delver.oracle_id))
    crud.set_card(catalog, deck.id, crud.CardSpec(oracle_id=fire_ice.oracle_id))

    name, dck = battles.dck_for_deck(catalog, catalog.get(type(deck), deck.id))
    # Whitespace collapsed, and the [#id] suffix that keeps win attribution
    # unambiguous between similarly named decks.
    assert name == f"Serialise me [#{deck.id}]"
    assert "[Commander]\n1 Bruna, the Fading Light" in dck
    assert "1 Delver of Secrets\n" in dck  # front face only (transform)
    assert "1 Fire // Ice" in dck  # combined name (split)
    assert "Insectile Aberration" not in dck


def test_disabled_is_a_clean_409(settings: object) -> None:
    with pytest.raises(FeatureDisabled) as excinfo:
        battles.ensure_enabled(get_settings())
    assert excinfo.value.code == "battles_disabled"


class FakeForge:
    """Stands in for ForgeClient with a canned simulation result."""

    def __init__(self, stdout: str, exit_code: int = 0, error: str | None = None) -> None:
        self.stdout = stdout
        self.exit_code = exit_code
        self.error = error
        self.requests: list[dict[str, Any]] = []

    async def simulate(
        self,
        decks: list[tuple[str, str]],
        *,
        games: int,
        game_format: str,
        verbose: bool = False,
    ) -> dict[str, Any]:
        self.requests.append(
            {"decks": decks, "games": games, "format": game_format, "verbose": verbose}
        )
        result = {"exit_code": self.exit_code, "stdout": self.stdout, "duration_ms": 1234}
        if self.error is not None:
            result["error"] = self.error
        return result


async def test_run_battle_records_the_outcome(catalog: DbSession) -> None:
    commander = make_card(
        catalog, "Battle Commander", type_line="Legendary Creature — Human", identity="W"
    )
    spell = make_card(catalog, "Battle Spell", type_line="Instant", identity="W")
    ids = []
    for name in ("Alpha", "Beta"):
        deck, _b = crud.create_deck(
            catalog,
            crud.DeckSpec(name=name, format="commander", commander_oracle_id=commander.oracle_id),
        )
        crud.set_card(catalog, deck.id, crud.CardSpec(oracle_id=spell.oracle_id))
        ids.append(deck.id)
    row = BattleResult(format="Commander", games_requested=2, status="running")
    catalog.add(row)
    catalog.commit()

    fake = FakeForge(
        f"Game Result: Game 1 ended in 10 ms. Ai(0)-Alpha [#{ids[0]}] has won!\n"
        f"Game Result: Game 2 ended in 12 ms. Ai(1)-Beta [#{ids[1]}] has won!"
    )
    settings = get_settings().model_copy(update={"enable_forge": True})
    await battles.run_battle(settings, row.id, ids, 2, client=fake)  # type: ignore[arg-type]

    catalog.expire_all()
    stored = catalog.get(BattleResult, row.id)
    assert stored is not None
    assert stored.status == "ok"
    assert stored.games_completed == 2
    assert [entry["wins"] for entry in stored.decks_json or []] == [1, 1]
    assert fake.requests[0]["format"] == "Commander"

    notes = list(catalog.scalars(select(Notification).where(Notification.kind == "battle")))
    assert len(notes) == 1
    assert f"Alpha [#{ids[0]}] 1" in notes[0].title


async def test_a_sidecar_refusal_surfaces_its_own_words(catalog: DbSession) -> None:
    """A deliberate "not now" ({"error": ..., "exit_code": -2} at 200) is shown
    verbatim on the battle row, not flattened into a generic shrug -- and it
    must NOT arrive as an exception, or the circuit breaker counts it as an
    outage and locks the practice endpoints out for the cooldown."""
    commander = make_card(
        catalog, "Refusal Commander", type_line="Legendary Creature — Human", identity="W"
    )
    spell = make_card(catalog, "Refusal Spell", type_line="Instant", identity="W")
    ids = []
    for name in ("Gamma", "Delta"):
        deck, _b = crud.create_deck(
            catalog,
            crud.DeckSpec(name=name, format="commander", commander_oracle_id=commander.oracle_id),
        )
        crud.set_card(catalog, deck.id, crud.CardSpec(oracle_id=spell.oracle_id))
        ids.append(deck.id)
    row = BattleResult(format="Commander", games_requested=2, status="running")
    catalog.add(row)
    catalog.commit()

    fake = FakeForge(
        "", exit_code=-2, error="the practice table is open; close it to run simulations"
    )
    settings = get_settings().model_copy(update={"enable_forge": True})
    await battles.run_battle(settings, row.id, ids, 2, client=fake)  # type: ignore[arg-type]

    catalog.expire_all()
    stored = catalog.get(BattleResult, row.id)
    assert stored is not None
    assert stored.status == "failed"
    assert "practice table" in (stored.error or "")


async def test_practice_open_reads_the_sidecar_and_fails_closed(settings: object) -> None:
    """The probe reports the table's state, and any client failure means "not
    open" -- the caller's own simulate call produces the better error."""

    class Probe:
        def __init__(self, payload: Any) -> None:
            self.payload = payload

        async def request_json(self, url: str, **_kwargs: Any) -> Any:
            if isinstance(self.payload, Exception):
                raise self.payload
            return self.payload

    settings = get_settings().model_copy(update={"enable_forge": True})
    assert await battles.practice_open(settings, client=Probe({"running": True}))  # type: ignore[arg-type]
    assert not await battles.practice_open(settings, client=Probe({"running": False}))  # type: ignore[arg-type]
    assert not await battles.practice_open(settings, client=Probe(RuntimeError("down")))  # type: ignore[arg-type]


async def test_a_dead_sidecar_marks_the_battle_failed(catalog: DbSession) -> None:
    """No silent vanishing: failure lands on the row and in the inbox."""
    row = BattleResult(format="Commander", games_requested=2, status="running")
    catalog.add(row)
    catalog.commit()

    class Refusing:
        async def simulate(self, *args: Any, **kwargs: Any) -> Any:
            raise RuntimeError("connection refused")

    settings = get_settings().model_copy(update={"enable_forge": True})
    await battles.run_battle(settings, row.id, [999], 2, client=Refusing())  # type: ignore[arg-type]

    catalog.expire_all()
    stored = catalog.get(BattleResult, row.id)
    assert stored is not None
    assert stored.status == "failed"
    assert stored.error
    assert list(catalog.scalars(select(Notification).where(Notification.kind == "battle")))
