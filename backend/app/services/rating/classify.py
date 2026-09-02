"""Classify what a card *does*, from its oracle text.

Every pattern cites the card that motivated it, and the traps named in
TEST-PLAN.md Phase 5 are handled deliberately rather than accidentally:

* Doom Blade is removal; so is Nekrataal's ability -- a permanent that removes is
  still interaction, but its ``permanent_speed`` tag is kept so the heuristics can
  weight instants higher.
* A Fog effect prevents damage without answering anything; it is ``fog``, never
  removal.
* A Pacifism-style aura neutralises a creature without destroying it; it counts as
  removal (the deck's problem is answered), tagged ``soft_removal``.

Bracket signals (extra turns, mass land denial, tutors) load from
``app/data/bracket_patterns.yaml`` so the pattern list is data, reviewable next to
the card names that motivated each line.
"""

from __future__ import annotations

import re
from functools import lru_cache
from pathlib import Path

import yaml

from app.services.rules.cards import RulesCard, card_types, strip_reminder_text

_PATTERNS_FILE = Path(__file__).resolve().parents[2] / "data" / "bracket_patterns.yaml"

#: Interaction and engine patterns, each citing its motivating card.
_SPOT_REMOVAL = re.compile(
    # Swords to Plowshares "Exile target creature"; Doom Blade "Destroy target
    # nonblack creature"; Nekrataal "destroy target nonartifact, nonblack
    # creature"; Beast Within "Destroy target permanent".
    r"(destroy|exile) target (non\w+,? )*(attacking |blocking )?"
    r"(creature|artifact|enchantment|planeswalker|permanent)",
    re.IGNORECASE,
)
_DAMAGE_REMOVAL = re.compile(
    # Lightning Bolt "deals 3 damage to any target"; Fireball "deals X damage".
    r"deals? (\d+|x) damage to (any target|target creature)",
    re.IGNORECASE,
)
_BLINK_VETO = re.compile(
    # Cloudshift "Exile target creature you control, then return that card":
    # protection, not removal -- the exile answers nothing.
    r"(destroy|exile)[^.]{0,60}you control[^.]{0,80}return",
    re.IGNORECASE,
)
_SOFT_REMOVAL = re.compile(
    # Pacifism "Enchanted creature can't attack or block"; Darksteel Mutation-style
    # "loses all abilities"; Song of the Dryads-style type change is left out --
    # too varied to pin without false positives.
    r"(enchanted (creature|permanent) can'?t attack|loses all abilities)",
    re.IGNORECASE,
)
_MASS_REMOVAL = re.compile(
    # Wrath of God "Destroy all creatures"; Cyclonic Rift wording is bounce and
    # deliberately out; Toxic Deluge "each creature gets -X/-X".
    r"(destroy all creatures|exile all creatures|each creature gets [-\u2212]|"
    r"deals? (\d+|x) damage to each creature|each player sacrifices \w+ creatures?)",
    re.IGNORECASE,
)
_COUNTERSPELL = re.compile(
    # Counterspell "Counter target spell"; Essence Scatter "Counter target
    # creature spell"; Annul "Counter target artifact or enchantment spell".
    r"counter target [\w' ]{0,30}?(spell|activated ability|triggered ability)",
    re.IGNORECASE,
)
_HATE = re.compile(
    # Rest in Peace "exile all graveyards" and "would be put into a graveyard from
    # anywhere, exile it instead"; Grafdigger's Cage; Yixlid Jailer-style locks.
    r"(exile all graveyards|"
    r"would be put into (a|his or her|their) graveyard.{0,30}exile|"
    r"can'?t enter the battlefield from (a graveyard|graveyards|libraries)|"
    r"players can'?t cast spells from graveyards)",
    re.IGNORECASE,
)
_FOG = re.compile(
    # Fog "Prevent all combat damage that would be dealt this turn."
    r"prevent all (combat )?damage",
    re.IGNORECASE,
)
_DRAW = re.compile(
    # Divination "Draw two cards"; Rhystic Study "you may draw a card". The
    # lookbehind keeps out punishers -- Underworld Dreams "Whenever an opponent
    # draws a card" draws the owner nothing.
    r"(?<!opponent )(?<!opponents )draws? (a card|one card|two|three|four|x) ?(cards?)?",
    re.IGNORECASE,
)
_RAMP_FETCH = re.compile(
    # Rampant Growth / Cultivate: land onto the battlefield from the library.
    r"search your library for .{0,40}land .{0,60}onto the battlefield",
    re.IGNORECASE,
)
_RAMP_MANA = re.compile(
    # Sol Ring "{T}: Add {C}{C}"; Llanowar Elves.
    r"add \{",
    re.IGNORECASE,
)
_PROTECTION = re.compile(
    # Heroic Intervention "hexproof and indestructible"; Teferi's Protection.
    r"(hexproof|indestructible|protection from|phases out|can'?t be countered)",
    re.IGNORECASE,
)
_RECURSION = re.compile(
    # Eternal Witness / Regrowth: "return target card from your graveyard".
    r"return .{0,40} from (your|a) graveyard to (your hand|the battlefield)",
    re.IGNORECASE,
)


@lru_cache(maxsize=1)
def _bracket_patterns() -> dict[str, list[re.Pattern[str]]]:
    """The compiled extra-turn / MLD / tutor patterns from the YAML file."""
    raw = yaml.safe_load(_PATTERNS_FILE.read_text(encoding="utf-8"))
    return {
        key: [re.compile(pattern, re.IGNORECASE) for pattern in patterns]
        for key, patterns in raw.items()
    }


def classify(card: RulesCard) -> frozenset[str]:
    """Tag one card with everything its text says it does.

    Returns:
        Tags from: ``removal``, ``soft_removal``, ``mass_removal``,
        ``counterspell``, ``hate``, ``fog``, ``draw``, ``ramp``, ``tutor``,
        ``extra_turn``, ``mass_land_denial``, ``protection``, ``recursion``,
        ``permanent_speed`` (the effect sits on a permanent, not an
        instant/sorcery), ``instant_speed``.
    """
    text = strip_reminder_text(card.oracle_text)
    types = card_types(card.type_line)
    tags: set[str] = set()

    if (_SPOT_REMOVAL.search(text) and not _BLINK_VETO.search(text)) or _DAMAGE_REMOVAL.search(
        text
    ):
        tags.add("removal")
    if _SOFT_REMOVAL.search(text):
        tags.update(("removal", "soft_removal"))
    if _MASS_REMOVAL.search(text):
        tags.add("mass_removal")
    if _COUNTERSPELL.search(text):
        tags.add("counterspell")
    if _HATE.search(text):
        tags.add("hate")
    if _FOG.search(text) and not tags & {"removal", "mass_removal"}:
        tags.add("fog")
    if _DRAW.search(text):
        tags.add("draw")
    if not card.is_land and (_RAMP_FETCH.search(text) or _RAMP_MANA.search(text)):
        tags.add("ramp")
    if _PROTECTION.search(text):
        tags.add("protection")
    if _RECURSION.search(text):
        tags.add("recursion")

    for signal, patterns in _bracket_patterns().items():
        if any(pattern.search(text) for pattern in patterns):
            tags.add(signal)

    if tags:
        if "Instant" in types or "Flash" in card.keywords:
            tags.add("instant_speed")
        elif types - {"Instant", "Sorcery"}:
            tags.add("permanent_speed")
    return frozenset(tags)
