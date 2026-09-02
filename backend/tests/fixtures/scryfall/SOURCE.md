# Scryfall fixtures

## `sample_cards.json`

**What it is.** A hand-authored bulk-data array, structurally faithful to Scryfall's
card object, chosen so that every layout and colour-identity edge case in
`TEST-PLAN.md` §1 is covered by at least one row.

**Provenance.** Hand-authored 2026-08-22 against the card-object schema documented at
<https://scryfall.com/docs/api/cards>. Card names, type lines, mana costs and colour
identities are real; `id`, `oracle_id` and `illustration_id` are **synthetic
well-formed UUIDs**, because these tests never call Scryfall and identity churn is
exactly what ADR-006 says not to depend on.

**Parser version.** Exercises `app/services/imports/scryfall_bulk.py` as of Phase 1.

**Refreshing from the real API.** `refresh.py` in this directory rebuilds the file
from live Scryfall data by name. Run it deliberately, never from a test:

```bash
python tests/fixtures/scryfall/refresh.py
```

It respects the 100 ms rate limit and sends the project User-Agent. If you run it, the
synthetic UUIDs are replaced by real ones and this file should be updated with the
fetch date.

## Coverage map

| row | covers |
|---|---|
| Lightning Bolt | plain `normal` layout, mono-colour, cheap baseline |
| Delver of Secrets | `transform` DFC — front-face name, back face adds no colour |
| Agadeem's Awakening | `modal_dfc` — MDFC land back, colour identity from the front |
| Fire // Ice | `split` — combined name, MV 4, two colours |
| Dusk // Dawn | `split` (aftermath) — second half is not a separate card |
| Bonecrusher Giant | `adventure` — creature type, MV from the creature half |
| Bushi Tenderfoot | `flip` — front-face name is the deck-list name |
| Bruna / Gisela / Brisela | `meld` — three distinct cards, Brisela separate |
| Island (2ed, and a Japanese printing) | basic land; the same card in two languages |
| Kitchen Finks | hybrid mana — identity is **both** colours |
| Birthing Pod | Phyrexian mana — the Phyrexian colour counts |
| Ancestral Vision | colour indicator, no mana cost, suspend |
| Dryad Arbor | land with a colour identity and no mana cost |
| Transguild Courier | reminder text does **not** add colour identity |
| Sol Ring | `game_changer: true` — Commander Bracket signal |
| Lim-Dûl's Vault | diacritics in the name |
| Aether Vial | ligature spelling (`AEther`) folds to the same normalised form |
| Alrund's Epiphany (Arena) | `digital: true` — must never appear in paper flows |
