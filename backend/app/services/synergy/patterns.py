"""Load and apply the synergy pattern table (ADR-018).

The table is data; this module gives it teeth. Every pattern is validated and
timing-bounded at load -- a regex that takes pathologically long against a large
oracle text fails startup loudly instead of hanging the rebuild job.
"""

from __future__ import annotations

import re
import time
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from app.services.rules.cards import RulesCard, strip_reminder_text

PATTERNS_FILE = Path(__file__).resolve().parents[2] / "data" / "synergy_patterns.yaml"

#: A pattern must clear a 5 000-character text within this bound at load time.
REGEX_TIME_BOUND_S = 0.05
_PROBE_TEXT = ("whenever a creature dies, sacrifice a permanent and draw. " * 90)[:5000]


@dataclass(frozen=True)
class Pattern:
    """One compiled entry of the pattern table."""

    pattern_id: str
    tag: str
    role: str
    pairs_with: tuple[str, ...]
    weight: float
    note: str
    oracle_regex: re.Pattern[str] | None = None
    type_regex: re.Pattern[str] | None = None
    keyword: str | None = None

    def matches(self, card: RulesCard) -> bool:
        """Whether the card carries this pattern's behaviour."""
        if self.keyword is not None:
            return self.keyword in card.keywords
        if self.type_regex is not None:
            return bool(self.type_regex.search(card.type_line))
        if self.oracle_regex is not None:
            return bool(self.oracle_regex.search(strip_reminder_text(card.oracle_text)))
        return False


@dataclass
class PatternTable:
    """Every pattern, with the pairings expanded for edge derivation."""

    patterns: list[Pattern] = field(default_factory=list)

    def tags_for(self, card: RulesCard) -> dict[str, Pattern]:
        """Tag -> the pattern that granted it, for one card."""
        found: dict[str, Pattern] = {}
        for pattern in self.patterns:
            if pattern.tag not in found and pattern.matches(card):
                found[pattern.tag] = pattern
        return found

    def pairings(self) -> list[tuple[str, str, float, str]]:
        """Every (tag_a, tag_b, weight, reason) pair the table declares.

        Direction does not matter to the graph; each declared pairing appears
        once with the two tags in sorted order.
        """
        seen: dict[tuple[str, str], tuple[float, str]] = {}
        for pattern in self.patterns:
            for other in pattern.pairs_with:
                first, second = sorted((pattern.tag, other))
                key = (first, second)
                weight = pattern.weight
                reason = f"{key[0]} + {key[1]} ({pattern.note})"
                if key not in seen or weight > seen[key][0]:
                    seen[key] = (weight, reason)
        return [(a, b, weight, reason) for (a, b), (weight, reason) in seen.items()]


class PatternError(ValueError):
    """The table failed validation; the message names the entry."""


def load_patterns(path: Path = PATTERNS_FILE) -> PatternTable:
    """Load, validate, compile and time-bound the pattern table.

    Raises:
        PatternError: A malformed entry, or a regex exceeding the time bound.
    """
    raw = yaml.safe_load(path.read_text(encoding="utf-8"))
    if not isinstance(raw, list) or not raw:
        raise PatternError("synergy_patterns.yaml must be a non-empty list")

    table = PatternTable()
    for entry in raw:
        pattern_id = str(entry.get("id") or "")
        if not pattern_id:
            raise PatternError(f"entry without an id: {entry!r}")
        match = entry.get("match") or {}
        if len(match) != 1:
            raise PatternError(f"{pattern_id}: match must hold exactly one matcher")
        role = str(entry.get("role") or "")
        if role not in ("enabler", "payoff", "both"):
            raise PatternError(f"{pattern_id}: role must be enabler|payoff|both")

        oracle_regex = type_regex = None
        keyword = None
        try:
            if "oracle_regex" in match:
                oracle_regex = re.compile(str(match["oracle_regex"]), re.IGNORECASE)
                _time_bound(pattern_id, oracle_regex)
            elif "type_regex" in match:
                type_regex = re.compile(str(match["type_regex"]))
                _time_bound(pattern_id, type_regex)
            elif "keyword" in match:
                keyword = str(match["keyword"])
            else:
                raise PatternError(f"{pattern_id}: unknown matcher {list(match)!r}")
        except re.error as error:
            raise PatternError(f"{pattern_id}: regex does not compile: {error}") from error

        table.patterns.append(
            Pattern(
                pattern_id=pattern_id,
                tag=str(entry.get("tag") or pattern_id),
                role=role,
                pairs_with=tuple(str(t) for t in entry.get("pairs_with") or ()),
                weight=float(entry.get("weight") or 1.0),
                note=str(entry.get("note") or ""),
                oracle_regex=oracle_regex,
                type_regex=type_regex,
                keyword=keyword,
            )
        )
    return table


@lru_cache(maxsize=1)
def default_table() -> PatternTable:
    """The shipped table, loaded once per process."""
    return load_patterns()


def _time_bound(pattern_id: str, compiled: re.Pattern[str]) -> None:
    started = time.perf_counter()
    compiled.search(_PROBE_TEXT)
    elapsed = time.perf_counter() - started
    if elapsed > REGEX_TIME_BOUND_S:
        raise PatternError(
            f"{pattern_id}: regex took {elapsed * 1000:.0f} ms against a 5000-char "
            "text; likely catastrophic backtracking"
        )
