"""Deck rankings across gauntlet history: Elo per theme, matchups per meta.

Computed on read from the persisted runs rather than stored: the whole
history is a handful of JSON blobs, the math is microseconds, and a stored
rating would just be one more thing to migrate when the formula changes.

Two views come out of the same walk:

* **Elo per theme.** Every pairing's games are individual Elo updates
  (K=24) against the opponent archetype's rating, which evolves in the same
  walk -- so beating Kinnan is worth more than beating a fringe list, and a
  theme that keeps winning as the vault grows climbs visibly across runs.
* **The matchup matrix.** Theme x archetype lifetime win rates, which is
  the "how do my decks do against different metas" question answered
  directly -- Elo summarises, the matrix explains.
"""

from __future__ import annotations

import json
from collections import defaultdict
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session as DbSession

from app.models import GauntletRun

BASE_RATING = 1000.0
K_FACTOR = 24.0

EPOCH_RUN_ID = 8
"""The ladder starts here. Runs 1-7 are systematically skewed twice over:
seats were not alternated (our decks were always on the play -- measured),
and win attribution matched deck names verbatim while Forge sanitises them,
so every game won by a slash-named deck (Kraum / Tymna, Ral //, our own
+1/+1 counters) simply vanished from the books -- run 7 recorded 35 of 45
games played. Ratings built on that are flattery, not measurement; the
epoch starts the ladder at the first honest run."""


def _expected(rating_a: float, rating_b: float) -> float:
    return 1.0 / (1.0 + 10 ** ((rating_b - rating_a) / 400.0))


def rankings(db: DbSession) -> dict[str, Any]:
    """Elo standings and the theme-vs-archetype matchup matrix."""
    runs = list(
        db.scalars(
            select(GauntletRun)
            .where(GauntletRun.status == "ok", GauntletRun.id >= EPOCH_RUN_ID)
            .order_by(GauntletRun.id)
        )
    )
    theme_rating: dict[str, float] = defaultdict(lambda: BASE_RATING)
    opponent_rating: dict[str, float] = defaultdict(lambda: BASE_RATING)
    theme_games: dict[str, int] = defaultdict(int)
    theme_wins: dict[str, int] = defaultdict(int)
    theme_last_run: dict[str, int] = {}
    matchup: dict[tuple[str, str], dict[str, int]] = defaultdict(lambda: {"wins": 0, "games": 0})

    for run in runs:
        detail = (
            run.detail_json
            if isinstance(run.detail_json, dict)
            else json.loads(run.detail_json or "{}")
        )
        for candidate in detail.get("candidates", []):
            if candidate.get("role") == "challenger":
                # Experiments are deliberately handicapped builds: letting
                # their games into the ladder made the weakest theme weaker
                # every time it was studied -- a feedback loop where being
                # experimented on kept you the permanent experiment subject.
                continue
            theme = str(candidate.get("theme") or candidate.get("name") or "?")
            for versus in candidate.get("versus", []):
                archetype = str(versus.get("archetype") or versus.get("name") or "?")
                wins = int(versus.get("wins") or 0)
                games = int(versus.get("games") or 0)
                draws = int(versus.get("draws") or 0)
                if games <= 0:
                    continue
                cell = matchup[(theme, archetype)]
                cell["wins"] += wins
                cell["games"] += games
                theme_games[theme] += games
                theme_wins[theme] += wins
                theme_last_run[theme] = run.id
                # Game-by-game Elo: each game is one update, so a 3-0 moves
                # the needle more than a 2-1 against the same list. Draws
                # score half for each side, never a free opponent win.
                for game in range(games):
                    if game < wins:
                        won = 1.0
                    elif game < wins + draws:
                        won = 0.5
                    else:
                        won = 0.0
                    expected = _expected(theme_rating[theme], opponent_rating[archetype])
                    theme_rating[theme] += K_FACTOR * (won - expected)
                    opponent_rating[archetype] += K_FACTOR * ((1.0 - won) - (1.0 - expected))

    standings = sorted(
        (
            {
                "theme": theme,
                "rating": round(rating, 1),
                "games": theme_games[theme],
                "wins": theme_wins[theme],
                "win_rate": round(theme_wins[theme] / theme_games[theme], 3)
                if theme_games[theme]
                else None,
                "last_run_id": theme_last_run.get(theme),
            }
            for theme, rating in theme_rating.items()
        ),
        key=lambda entry: -float(entry["rating"] or 0.0),
    )
    opponents = [
        {"archetype": name, "rating": round(rating, 1)}
        for name, rating in sorted(opponent_rating.items(), key=lambda kv: -kv[1])
    ]
    matrix = [
        {
            "theme": theme,
            "archetype": archetype,
            "wins": cell["wins"],
            "games": cell["games"],
            "win_rate": round(cell["wins"] / cell["games"], 3),
        }
        for (theme, archetype), cell in sorted(matchup.items())
        if cell["games"]
    ]
    return {"standings": standings, "opponents": opponents, "matchups": matrix, "runs": len(runs)}
