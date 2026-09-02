"""The deck-legality rules engine.

Pure functions over plain data: the caller (``app.services.decks``) loads oracle
snapshots and legality rows from the database and hands them in, so every rule in
here is testable without a database and the module can honestly carry the 100%
coverage requirement (TEST-PLAN.md section 0) -- this is the module where a bug
means an illegal deck.
"""

from app.services.rules.cards import DeckEntry, RulesCard
from app.services.rules.formats import PROFILES, FormatProfile, profile_for
from app.services.rules.validate import RuleError, ValidationResult, validate_deck

__all__ = [
    "PROFILES",
    "DeckEntry",
    "FormatProfile",
    "RuleError",
    "RulesCard",
    "ValidationResult",
    "profile_for",
    "validate_deck",
]
