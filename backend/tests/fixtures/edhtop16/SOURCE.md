# edhtop16 fixture provenance

- `top_commanders.json` — shape transcribed from POST https://edhtop16.com/api/graphql
  (query TopCommanders) as served on 2026-08-27, trimmed by hand to two commanders
  with two entries each, card names replaced with ones the sample catalogue holds.
  Exercises parser_version 1 (app/clients/edhtop16.py).
- `corrupted.json` — hand-written body with a GraphQL errors array; the parser must
  raise cleanly, keeping the previous snapshot (TEST-PLAN Phase 7).

Updated 2026-08-27 after introspecting the live schema: `sortBy: POPULARITY`
(ENTRIES does not exist), and entries carry `maindeck { name oracleId }` inline.
