"""Template extraction: the CORE/COMMON/FLEX split with pinned boundaries.

Ten fixture lists make the thresholds exact: 8 of 10 is exactly 80% (CORE by the
inclusive boundary), 4 of 10 exactly 40% (COMMON), 3 of 10 is FLEX
(TEST-PLAN Phase 7: boundary behaviour at exactly 80% and exactly 40% is pinned).
"""

from __future__ import annotations

from app.services.meta.template import extract_template

#: Ten decklists over five cards with hand-computed presences:
#:   staple  10/10 = 100%  CORE, always 1 copy
#:   core-edge 8/10 =  80%  CORE (inclusive boundary)
#:   common   6/10 =  60%  COMMON
#:   common-edge 4/10 = 40% COMMON (inclusive boundary)
#:   flex     3/10 =  30%  FLEX
LISTS = [
    {"staple": 1, "core-edge": 1, "common": 2, "common-edge": 4, "flex": 1},
    {"staple": 1, "core-edge": 1, "common": 2, "common-edge": 4},
    {"staple": 1, "core-edge": 1, "common": 3, "common-edge": 2},
    {"staple": 1, "core-edge": 1, "common": 3, "common-edge": 2, "flex": 1},
    {"staple": 1, "core-edge": 1, "common": 4, "flex": 1},
    {"staple": 1, "core-edge": 1, "common": 4},
    {"staple": 1, "core-edge": 1},
    {"staple": 1, "core-edge": 1},
    {"staple": 1},
    {"staple": 1},
]


def test_hand_computed_split() -> None:
    rows = {row.oracle_id: row for row in extract_template(LISTS)}

    assert rows["staple"].tier == "CORE"
    assert rows["staple"].presence_pct == 100.0
    assert rows["core-edge"].tier == "CORE"  # exactly 80% is CORE
    assert rows["core-edge"].presence_pct == 80.0
    assert rows["common"].tier == "COMMON"
    assert rows["common"].presence_pct == 60.0
    assert rows["common-edge"].tier == "COMMON"  # exactly 40% is COMMON
    assert rows["common-edge"].presence_pct == 40.0
    assert rows["flex"].tier == "FLEX"
    assert rows["flex"].presence_pct == 30.0


def test_typical_count_is_the_median_of_players_of_the_card() -> None:
    rows = {row.oracle_id: row for row in extract_template(LISTS)}
    # common appears at 2,2,3,3,4,4 copies -> median 3.
    assert rows["common"].typical_count == 3
    # common-edge appears at 4,4,2,2 -> median 3 (statistics.median of even run).
    assert rows["common-edge"].typical_count == 3
    assert rows["staple"].typical_count == 1


def test_ordering_is_core_first_then_presence() -> None:
    ordered = [row.oracle_id for row in extract_template(LISTS)]
    assert ordered == ["staple", "core-edge", "common", "common-edge", "flex"]


def test_no_lists_no_template() -> None:
    assert extract_template([]) == []
