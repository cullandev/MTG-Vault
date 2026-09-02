"""The verbose-log timeline parser, pinned on real Forge 2.0.14 output lines."""

from __future__ import annotations

from app.clients.forge import parse_game_timelines

NAMES = ["graveyard (suggested deck) [#43]", "treasure & artifacts (suggested 60) [#44]"]

# Verbatim line shapes from a live verbose sim (2026-08-28 probe). Assembled
# from parts so no source line breaks the 100-column rule.
_P1 = "Ai(1)-graveyard (suggested deck) [#43]"
_P2 = "Ai(2)-treasure & artifacts (suggested 60) [#44]"
LOG = "\n".join(
    [
        f"Turn: Turn 1 ({_P2})",
        f"Land: {_P2} played Mountain (141)",
        f"Turn: Turn 2 ({_P1})",
        f"Land: {_P1} played Dimir Guildgate (62)",
        f"Add To Stack: {_P1} cast Desolation Prowler",
        f"Turn: Turn 7 ({_P2})",
        f"Add To Stack: {_P2} triggered Battle-Rattle Shaman "
        "targeting [Battle-Rattle Shaman (119)]",
        f"Combat: {_P2} assigned Battle-Rattle Shaman (119) and Mauhur (115) to attack {_P1}.",
        f"Damage: Battle-Rattle Shaman (119) deals 4 combat damage to {_P1}.",
        f"Life: Life: {_P1} 2 > -4",
        "Game Outcome: Turn 7",
        f"Game Outcome: {_P1} has lost because life total reached 0",
        f"Game Result: Game 1 ended in 1323 ms. {_P2} has won!",
    ]
)


def test_timelines_capture_turns_plays_and_life() -> None:
    games = parse_game_timelines(LOG, NAMES)
    assert len(games) == 1
    game = games[0]
    assert [turn["turn"] for turn in game["turns"]] == [1, 2, 7]

    first = game["turns"][0]
    assert first["active"] == NAMES[1]
    assert first["events"] == [
        {"kind": "land", "text": f"{NAMES[1]} played", "card": "Mountain"}
    ], "instance ids must be stripped and the card carried separately for hover previews"

    second = game["turns"][1]
    assert {"kind": "cast", "text": f"{NAMES[0]} cast", "card": "Desolation Prowler"} in second[
        "events"
    ]

    last = game["turns"][2]
    assert any(
        event["kind"] == "cast" and event.get("card", "").startswith("Battle-Rattle Shaman")
        for event in last["events"]
    )
    assert any(
        event["kind"] == "damage" and "deals 4 to" in event["text"] for event in last["events"]
    )
    assert last["life"][NAMES[0]] == -4, "life after the turn must be recorded"
    assert any("has lost because life total reached 0" in line for line in game["outcome"])


def test_two_games_split_on_result_lines() -> None:
    two = LOG + LOG.replace("Game 1", "Game 2")
    games = parse_game_timelines(two, NAMES)
    assert len(games) == 2
    assert all(game["turns"] for game in games)
