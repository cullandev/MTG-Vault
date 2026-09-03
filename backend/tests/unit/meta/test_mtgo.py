"""The MTGO decklist parser: slugs, the embedded data, ranks and cards."""

from __future__ import annotations

import json

import pytest

from app.clients import mtgo
from app.clients.base import SourceResponseError

LISTING = """
<a href="/decklist/standard-challenge-16-2026-09-0312853666">Standard Challenge 16</a>
<a href="/decklist/modern-challenge-32-2026-09-0212853730">Modern Challenge 32</a>
<a href="/decklist/modern-challenge-32-2026-09-0212853730">Modern Challenge 32 (again)</a>
<a href="/decklist/modern-league-2026-09-0112853000">Modern League</a>
<a href="/decklist/not-a-slug">nope</a>
"""


def _card(name: str, qty: int) -> dict:
    return {"qty": str(qty), "sideboard": "false", "card_attributes": {"card_name": name}}


EVENT = {
    "event_id": "12853730",
    "description": "Modern Challenge 32",
    "starttime": "2026-09-02 21:30:00.0",
    "format": "CMODERN",
    "site_name": "modern-challenge-32-2026-09-0212853730",
    "decklists": [
        {
            "loginid": "1",
            "player": "azax",
            "main_deck": [_card("Ragavan, Nimble Pilferer", 4), _card("Island", 20)],
            "sideboard_deck": [_card("Force of Vigor", 2)],
        },
        {
            "loginid": "2",
            "player": "bruno",
            "main_deck": [_card("Thoughtseize", 4)],
            "sideboard_deck": [],
        },
        {
            "loginid": "3",
            "player": "swiss-only",
            "main_deck": [_card("Lightning Bolt", 4)],
            "sideboard_deck": [],
        },
    ],
    "standings": [
        {"loginid": "1", "rank": "3"},
        {"loginid": "2", "rank": "5"},
        {"loginid": "3", "rank": "19"},
    ],
    "final_rank": [{"loginid": "2", "rank": "1"}, {"loginid": "1", "rank": "2"}],
    "winloss": [
        {"loginid": "1", "wins": "6", "losses": "1"},
        {"loginid": "2", "wins": "7", "losses": "0"},
    ],
}


def test_listing_yields_each_event_once_with_format_kind_size_and_date() -> None:
    refs = mtgo.parse_listing(LISTING)
    assert [r.slug for r in refs] == [
        "standard-challenge-16-2026-09-0312853666",
        "modern-challenge-32-2026-09-0212853730",
        "modern-league-2026-09-0112853000",
    ]
    modern = refs[1]
    assert (modern.format, modern.kind, modern.size, modern.date, modern.event_id) == (
        "Modern",
        "challenge",
        32,
        "2026-09-02",
        "12853730",
    )
    assert refs[2].size is None
    assert modern.url.endswith("/decklist/modern-challenge-32-2026-09-0212853730")


def test_event_data_is_lifted_from_the_page_script() -> None:
    html = (
        "<html><script>window.MTGO = {};\nwindow.MTGO.decklists.data = "
        + json.dumps(EVENT)
        + ";\n</script></html>"
    )
    data = mtgo.extract_data(html)
    assert data["event_id"] == "12853730"
    with pytest.raises(SourceResponseError):
        mtgo.extract_data("<html>loading</html>")


def test_event_parses_ranks_records_and_cards() -> None:
    event = mtgo.parse_event(EVENT)
    assert (event.format, event.date, event.description) == (
        "Modern",
        "2026-09-02",
        "Modern Challenge 32",
    )
    by_player = {d.player: d for d in event.decks}
    # The bracket's final rank beats the Swiss standing.
    assert by_player["bruno"].rank == 1 and by_player["azax"].rank == 2
    assert by_player["swiss-only"].rank == 19
    assert (by_player["bruno"].wins, by_player["bruno"].losses) == (7, 0)
    assert by_player["azax"].main == [("Ragavan, Nimble Pilferer", 4), ("Island", 20)]
    assert by_player["azax"].sideboard == [("Force of Vigor", 2)]
    assert [d.player for d in event.top(2)] == ["bruno", "azax"]


def test_an_event_without_lists_is_refused() -> None:
    with pytest.raises(SourceResponseError):
        mtgo.parse_event({"event_id": "1", "decklists": []})
