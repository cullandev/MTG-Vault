# EDHREC fixture provenance

- `commander_page.json` — shape transcribed from
  https://json.edhrec.com/pages/commanders/atraxa-praetors-voice.json as served on
  2026-08-26, trimmed by hand to two kept card lists, one skipped list ("New
  Cards"), and two theme taglinks. Exercises parser_version 1
  (app/clients/edhrec.py).
- `malformed.json` — hand-written page missing every card list, standing in for a
  page-format change; the parser must raise, not return an empty page.
