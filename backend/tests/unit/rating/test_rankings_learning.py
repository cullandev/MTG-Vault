"""Elo rankings from gauntlet history, and the learning loop's rules."""

from __future__ import annotations

import itertools

from sqlalchemy.orm import Session as DbSession

from app.models import GauntletRun
from app.services.rating import learning, rankings

_next_run_id = itertools.count(rankings.EPOCH_RUN_ID)


def _run(db: DbSession, candidates: list[dict]) -> None:
    # Explicit ids at the epoch: rows below EPOCH_RUN_ID belong to the
    # pre-fix era (seat bias + vanished slash-named wins) and are excluded.
    db.add(GauntletRun(id=next(_next_run_id), status="ok", detail_json={"candidates": candidates}))
    db.flush()


def test_elo_rewards_wins_against_strong_opponents(catalog: DbSession) -> None:
    _run(
        catalog,
        [
            {
                "theme": "winner",
                "versus": [{"archetype": "Kinnan", "wins": 3, "games": 3}],
            },
            {
                "theme": "loser",
                "versus": [{"archetype": "Kinnan", "wins": 0, "games": 3}],
            },
        ],
    )
    payload = rankings.rankings(catalog)
    by_theme = {row["theme"]: row for row in payload["standings"]}
    assert by_theme["winner"]["rating"] > rankings.BASE_RATING
    assert by_theme["loser"]["rating"] < rankings.BASE_RATING
    assert by_theme["winner"]["win_rate"] == 1.0
    matrix = {(m["theme"], m["archetype"]): m for m in payload["matchups"]}
    assert matrix[("winner", "Kinnan")]["games"] == 3


def test_ratings_accumulate_across_runs(catalog: DbSession) -> None:
    for _ in range(2):
        _run(
            catalog,
            [{"theme": "steady", "versus": [{"archetype": "Sisay", "wins": 2, "games": 3}]}],
        )
    payload = rankings.rankings(catalog)
    steady = next(row for row in payload["standings"] if row["theme"] == "steady")
    assert steady["games"] == 6
    assert payload["runs"] == 2


def test_challenger_games_stay_off_the_ladder(catalog: DbSession) -> None:
    """Experiments are handicapped builds: counting their games made the
    weakest theme weaker every time it was studied -- a feedback loop."""
    _run(
        catalog,
        [
            {
                "theme": "studied",
                "role": "champion",
                "versus": [{"archetype": "Kinnan", "wins": 3, "games": 3}],
            },
            {
                "theme": "studied",
                "role": "challenger",
                "versus": [{"archetype": "Kinnan", "wins": 0, "games": 3}],
            },
        ],
    )
    payload = rankings.rankings(catalog)
    studied = next(row for row in payload["standings"] if row["theme"] == "studied")
    assert studied["games"] == 3, "challenger games leaked into the ladder"
    assert studied["wins"] == 3
    assert studied["win_rate"] == 1.0


def test_draws_score_half_not_a_free_opponent_win(catalog: DbSession) -> None:
    _run(
        catalog,
        [{"theme": "drawish", "versus": [{"archetype": "K", "wins": 0, "games": 2, "draws": 2}]}],
    )
    payload = rankings.rankings(catalog)
    drawish = next(row for row in payload["standings"] if row["theme"] == "drawish")
    # Two draws against an equal opponent should leave the rating essentially
    # unmoved -- under the old scoring they counted as two full losses.
    assert abs(drawish["rating"] - rankings.BASE_RATING) < 1.0


def test_relaxing_rolls_back_the_newest_lesson(catalog: DbSession) -> None:
    learning.record_lesson(
        catalog,
        "starved",
        probe=["old"],
        promoted=True,
        champion_wins=1,
        challenger_wins=2,
        games=6,
    )
    learning.record_lesson(
        catalog,
        "starved",
        probe=["new"],
        promoted=True,
        champion_wins=1,
        challenger_wins=2,
        games=6,
    )
    assert learning.learned_exclusions(catalog, "starved") == {"old", "new"}
    restored = learning.relax_exclusions(catalog, "starved")
    assert restored == ["new"], "the newest lesson should roll back first"
    assert learning.learned_exclusions(catalog, "starved") == {"old"}


def test_a_promoted_lesson_persists_and_caps(catalog: DbSession) -> None:
    learning.record_lesson(
        catalog,
        "graveyard",
        probe=["a", "b"],
        promoted=True,
        champion_wins=3,
        challenger_wins=5,
        games=9,
    )
    assert learning.learned_exclusions(catalog, "graveyard") == {"a", "b"}
    # A failed experiment records history but changes nothing.
    learning.record_lesson(
        catalog,
        "graveyard",
        probe=["c"],
        promoted=False,
        champion_wins=5,
        challenger_wins=3,
        games=9,
    )
    assert learning.learned_exclusions(catalog, "graveyard") == {"a", "b"}
    lessons = learning.all_lessons(catalog)
    entry = next(item for item in lessons if item["theme"] == "graveyard")
    assert entry["experiments"] == 2
    assert entry["promotions"] == 1


def test_weakest_theme_needs_history_and_presence() -> None:
    standings = [
        {"theme": "strong", "rating": 1100.0, "games": 12},
        {"theme": "weak", "rating": 900.0, "games": 12},
        {"theme": "new", "rating": 800.0, "games": 2},
    ]
    # "new" is lowest-rated but has no real history; "weak" is the target.
    assert learning.weakest_theme(standings, {"strong", "weak", "new"}) == "weak"
    # A theme not fielding a deck this run cannot be experimented on.
    assert learning.weakest_theme(standings, {"strong"}) == "strong"
    assert learning.weakest_theme(standings, set()) is None
