"""Frozen evaluation sets, attack corpus, benchmark harness, and scorecard.

This package is the measurement side of Puspa (plan section 8). It
proves numbers instead of claiming them:

- frozen, versioned eval sets with a private holdout discipline
  (``corpus``);
- reference tasks with deterministic checkers, run three times and
  replayed from cassettes (``reference_tasks``, ``replay``);
- retrieval comparison: BM25 vs plain vectors vs HippoRAG
  (``retrieval_sets``, ``bm25``);
- gold sets with manual labels for the core task types
  (``gold_sets``);
- an injection corpus plus a harness that runs it through the real
  defense layers (``attack_harness``);
- mutation testing and gateway fuzzing (``mutation``, ``fuzz``);
- a one-page release scorecard (``scorecard``).

Nothing here calls a model. Every number is produced by
deterministic code, reported with an uncertainty interval, a date,
and a corpus version. Where a measurement cannot be made honestly
yet (no outside corpus authors, no adaptive attacker model run, no
imported public corpus), the scorecard says so instead of
inventing a number.
"""

from __future__ import annotations

__all__ = [
    "attack_harness",
    "bm25",
    "corpus",
    "fuzz",
    "gold_sets",
    "intervals",
    "mutation",
    "reference_tasks",
    "replay",
    "retrieval_sets",
    "scorecard",
]
