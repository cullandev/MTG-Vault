"""Rating and strategy: classification, heuristic scores, brackets, AI review.

Like the rules engine, the scoring core is pure functions over
:class:`app.services.rules.RulesCard` snapshots -- the database stays at the
loader seam, and every score is explainable from the raw counts it exposes.
"""
