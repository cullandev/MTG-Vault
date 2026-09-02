"""Structured logging must never collide with LogRecord's own attributes.

``log.info("event", extra={"name": deck.name})`` raises
``KeyError: "Attempt to overwrite 'name' in LogRecord"`` at the moment it runs
-- not at import, not in review. Two such calls sat in the gauntlet for
months in branches nothing had taken: one fires only when a stale *unbuilt*
gauntlet deck is cleaned up. Releasing a deck's cards made that branch
reachable and the next run died one second in, with a Python logging error
where the deck results should have been.

A static scan is the right shape for this. The dangerous call is invisible
until its branch executes, so waiting for coverage to reach it is exactly the
trap that let these two survive.
"""

from __future__ import annotations

import logging
import re
from pathlib import Path

import pytest

APP = Path(__file__).resolve().parents[2] / "app"

#: Attributes logging sets on every LogRecord. Passing any of them through
#: `extra` is fatal. Taken from logging.Logger.makeRecord's own guard rather
#: than hand-listed, so a new Python version cannot quietly widen the set.
RESERVED = set(logging.LogRecord("n", 0, "p", 0, "m", None, None).__dict__) | {
    "message",
    "asctime",
    "taskName",
}

_EXTRA = re.compile(r"extra=\{(?P<body>[^{}]*)\}", re.DOTALL)
_KEY = re.compile(r"""["'](?P<key>[A-Za-z_][A-Za-z_0-9]*)["']\s*:""")


def _offences() -> list[str]:
    found = []
    for path in sorted(APP.rglob("*.py")):
        source = path.read_text(encoding="utf-8")
        for match in _EXTRA.finditer(source):
            line = source[: match.start()].count("\n") + 1
            for key in _KEY.finditer(match.group("body")):
                name = key.group("key")
                if name in RESERVED:
                    found.append(f"{path.relative_to(APP.parent)}:{line} extra={{{name!r}: ...}}")
    return found


def test_no_logging_extra_shadows_a_logrecord_attribute() -> None:
    offences = _offences()
    assert not offences, (
        "these logging calls raise KeyError the moment their branch runs:\n  "
        + "\n  ".join(offences)
        + "\nRename the key ('name' -> 'deck', 'module' -> 'component', ...)."
    )


@pytest.mark.parametrize("reserved", ["name", "module", "filename", "lineno", "message"])
def test_the_guard_catches_a_real_collision(reserved: str) -> None:
    """The scan is only worth having if it would fail on the real thing."""
    assert reserved in RESERVED
    with pytest.raises(KeyError):
        logging.getLogger("mtgvault.test").makeRecord(
            "mtgvault.test", logging.INFO, "f", 1, "msg", None, None, extra={reserved: "x"}
        )
