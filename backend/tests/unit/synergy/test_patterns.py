"""The pattern table: fixture coverage, correctness, pairs, and regex safety.

The coverage test is the enforcement arm of ADR-018: an entry added to
``synergy_patterns.yaml`` without fixtures fails the build, so the table cannot
grow untested.
"""

from __future__ import annotations

import re
import time

import yaml

from app.services.rules import RulesCard
from app.services.synergy.patterns import (
    REGEX_TIME_BOUND_S,
    load_patterns,
)
from tests.conftest import FIXTURES

FIXTURE_FILE = FIXTURES / "synergy" / "pattern_fixtures.yaml"


def _fixture_card(entry: dict) -> RulesCard:
    return RulesCard(
        oracle_id=entry["name"],
        name=entry["name"],
        type_line=entry.get("type", "Creature — Human"),
        oracle_text=entry.get("text", ""),
        keywords=frozenset(entry.get("keywords", [])),
    )


def test_every_pattern_has_positive_and_negative_fixtures() -> None:
    """An entry without fixtures cannot ship (TEST-PLAN Phase 8)."""
    table = load_patterns()
    fixtures = yaml.safe_load(FIXTURE_FILE.read_text(encoding="utf-8"))
    for pattern in table.patterns:
        entry = fixtures.get(pattern.pattern_id)
        assert entry, f"{pattern.pattern_id} has no fixture entry"
        assert entry.get("positive"), f"{pattern.pattern_id} has no positive fixture"
        assert entry.get("negative"), f"{pattern.pattern_id} has no negative fixture"


def test_fixtures_classify_correctly() -> None:
    table = load_patterns()
    fixtures = yaml.safe_load(FIXTURE_FILE.read_text(encoding="utf-8"))
    by_id = {pattern.pattern_id: pattern for pattern in table.patterns}
    for pattern_id, entry in fixtures.items():
        pattern = by_id[pattern_id]
        for positive in entry["positive"]:
            assert pattern.matches(_fixture_card(positive)), (
                f"{pattern_id} misses its positive fixture {positive['name']}"
            )
        for negative in entry["negative"]:
            assert not pattern.matches(_fixture_card(negative)), (
                f"{pattern_id} wrongly matches its negative fixture {negative['name']}"
            )


def test_regexes_stay_within_the_time_bound() -> None:
    """No catastrophic backtracking against a 5000-character oracle text."""
    table = load_patterns()
    hostile = ("sacrifice a creature a " * 250)[:5000]
    for pattern in table.patterns:
        for compiled in (pattern.oracle_regex, pattern.type_regex):
            if compiled is None:
                continue
            started = time.perf_counter()
            compiled.search(hostile)
            assert time.perf_counter() - started < REGEX_TIME_BOUND_S, pattern.pattern_id
            assert isinstance(compiled, re.Pattern)


def test_pairings_are_deduplicated_and_symmetric() -> None:
    table = load_patterns()
    pairs = table.pairings()
    keys = [(a, b) for a, b, _w, _r in pairs]
    assert len(keys) == len(set(keys))
    assert all(a <= b for a, b in keys)
