# Commander Spellbook fixture provenance

- `find_my_combos.json` — shape transcribed from
  POST https://backend.commanderspellbook.com/find-my-combos as served on
  2026-08-26, trimmed by hand to one included and one almost-included variant.
  Exercises parser_version 1 (app/clients/spellbook.py).
- `malformed.json` — hand-written body without a `results` object, standing in
  for an API change; the parser must raise, not return no combos.
