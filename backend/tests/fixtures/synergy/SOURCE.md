# Synergy pattern fixtures

`pattern_fixtures.yaml` holds, per pattern id in app/data/synergy_patterns.yaml,
at least one positive and one negative fixture card with oracle wording
transcribed from the real cards (Scryfall, 2026-08-28). The test suite iterates
the pattern table and FAILS any entry without both, so extending the table
cannot silently ship untested (TEST-PLAN Phase 8).
