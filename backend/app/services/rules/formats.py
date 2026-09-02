"""What each format demands of a deck's shape.

Card-by-card legality always comes from the imported ``legalities`` rows -- never
from set membership or rarity (TEST-PLAN.md section 1) -- so a profile only carries
the *structural* rules: size, copy limit, sideboard, and whether a commander leads it.
"""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FormatProfile:
    """Structural deck-building rules of one format."""

    key: str
    min_main: int = 60
    exact_main: int | None = None
    """Exact deck size counting the commander board (Commander's 100)."""
    copy_limit: int = 4
    sideboard_max: int = 15
    has_commander: bool = False


PROFILES: dict[str, FormatProfile] = {
    profile.key: profile
    for profile in (
        FormatProfile(key="standard"),
        FormatProfile(key="pioneer"),
        FormatProfile(key="modern"),
        FormatProfile(key="legacy"),
        FormatProfile(key="vintage"),
        FormatProfile(key="pauper"),
        # House rules for home games: same structures, no banlist (the legality
        # loader treats every card as legal in the casual formats).
        FormatProfile(key="casual"),
        FormatProfile(
            key="commander",
            min_main=0,
            exact_main=100,
            copy_limit=1,
            sideboard_max=0,
            has_commander=True,
        ),
        FormatProfile(
            key="casual_commander",
            min_main=0,
            exact_main=100,
            copy_limit=1,
            sideboard_max=0,
            has_commander=True,
        ),
        # Deliberately absent: Oathbreaker. Its commanders are planeswalkers and it
        # adds a signature-spell rule; routing it through the Commander checks would
        # rule every real Oathbreaker deck illegal. Unknown formats get the honest
        # 60-card default instead of a confidently wrong verdict.
    )
}


def profile_for(format_key: str) -> FormatProfile:
    """The profile of a format, defaulting unknown formats to 60-card constructed."""
    return PROFILES.get(format_key.lower(), FormatProfile(key=format_key.lower()))
