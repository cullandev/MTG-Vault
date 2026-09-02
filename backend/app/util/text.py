"""Card-name normalisation.

One normalisation function, used by the bulk importer, the search box, CSV import and
(from Phase 2) the OCR fuzzy matcher. If these ever diverge, a card that imports fine
stops being findable, so there is exactly one implementation.

The rules exist because of real cards:

* ``Lim-Dul's Vault`` and ``Nazgul`` need diacritics folded (they are spelled
  ``Lim-Dul`` and ``Nazgul`` with combining marks in Scryfall's data).
* ``Aether Vial`` was printed as ``AEther Vial``; the ligature has to fold to ``ae``.
* Typographic apostrophes appear in exports from other collection managers.
* ``Fire // Ice`` must normalise to something a user typing ``fire ice`` will match.
"""

from __future__ import annotations

import re
import unicodedata

# Characters NFKD does not decompose, mapped to their ASCII equivalents.
_LIGATURES = str.maketrans(
    {
        "Æ": "ae",  # AE
        "æ": "ae",  # ae
        "Œ": "oe",  # OE
        "œ": "oe",  # oe
        "Ø": "o",
        "ø": "o",
        "ß": "ss",
        "Ł": "l",
        "ł": "l",
        "Ð": "d",
        "ð": "d",
        "Þ": "th",
        "þ": "th",
        "’": "'",  # right single quotation mark
        "‘": "'",
        "“": '"',
        "”": '"',
        "–": "-",  # en dash
        "—": "-",  # em dash
        "−": "-",  # minus sign
    }
)

_APOSTROPHES = re.compile(r"['’]")
_NON_ALNUM = re.compile(r"[^a-z0-9]+")


def normalize_name(name: str) -> str:
    """Fold a card name to its canonical comparison form.

    Casefolds, folds ligatures and diacritics to ASCII, drops all punctuation, and
    collapses runs of whitespace to a single space.

    Args:
        name: A card name, in any of the spellings that appear in the wild.

    Returns:
        The normalised form, e.g. ``"lim dul s vault"``.

    Examples:
        >>> normalize_name("Lim-Dul's Vault")
        'lim duls vault'
        >>> normalize_name("AEther Vial") == normalize_name("Aether Vial")
        True
        >>> normalize_name("Fire // Ice")
        'fire ice'
    """
    folded = name.translate(_LIGATURES).casefold()
    # Apostrophes are *removed*, not turned into a space: a possessive should
    # normalise the same whether or not the typist bothered with the punctuation,
    # so "Thorin's Last Stand" and "thorins last stand" have to agree.
    folded = _APOSTROPHES.sub("", folded)
    decomposed = unicodedata.normalize("NFKD", folded)
    stripped = "".join(ch for ch in decomposed if not unicodedata.combining(ch))
    return _NON_ALNUM.sub(" ", stripped).strip()


def front_face_name(name: str) -> str:
    """Return the front-face name of a possibly multi-face card name.

    Scryfall joins faces with `` // ``. Double-faced cards are matched and listed by
    their front face; split and adventure cards keep the combined name because that
    is the whole card's name.

    Args:
        name: The card's full name, e.g. ``"Delver of Secrets // Insectile Aberration"``.

    Returns:
        The text before the first `` // `` separator.
    """
    return name.split(" // ", 1)[0].strip()


def is_multiface_name(name: str) -> bool:
    """Whether a name contains the Scryfall multi-face separator."""
    return " // " in name
