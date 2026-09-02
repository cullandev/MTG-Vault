# Collection CSV fixtures

Hand-authored 2026-08-22 to match the *column layouts* each site exports, using cards
that exist in `tests/fixtures/scryfall/sample_cards.json` so the importer can actually
resolve them.

| file | header taken from | exercises |
|---|---|---|
| `moxfield_collection.csv` | Moxfield collection export | quoted fields, `Tradelist Count`, foil marker, proxy flag, diacritics (`Lim-Dûl's Vault`), ligature spelling (`AEther Vial`), a count-0 tradelist-only row, and one unresolvable name |
| `archidekt_collection.csv` | Archidekt collection export | unquoted fields, `Finish` vocabulary (`Normal`/`Foil`), two-letter language codes, a `//` split name, a front-face-only adventure name, a Japanese printing |
| `deckbox_collection.csv` | Deckbox inventory export | no set code at all (edition name only), Deckbox condition vocabulary (`Good (Lightly Played)`), `My Price` column |

**Replace these with your real export when you have one.** They were written to cover
the parser's decision points, not to be representative of a real collection; a real
file will surface column quirks these cannot.
