# Moxfield fixture provenance

- `deck.json` — shape transcribed from GET https://api2.moxfield.com/v2/decks/all/<id>
  as served on 2026-08-27, trimmed by hand to a commander plus a few mainboard
  cards from the sample catalogue. Exercises parser_version 1 (app/clients/moxfield.py).
- `corrupted.json` — hand-written body with no recognisable boards; the parser must
  raise cleanly rather than record an empty deck.
