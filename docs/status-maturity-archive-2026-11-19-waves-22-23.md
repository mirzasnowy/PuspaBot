# Status archive: maturity Waves 22-23 (19 Nov 2026)

Archived from `docs/status-maturity.md` on 19 Nov 2026, when the
live log passed the soft cap again (1,051 lines with the Wave 28
K12 entry). Frozen, never appended to; history is never deleted,
only archived. The living log continues in
`docs/status-maturity.md` (Wave 24 onwards).

## Wave 22: Kosmos K3 (Bitemporal state, DONE 19 Nov 2026)

Second Kosmos item (K1 done, Wave 21). K3 adds valid time vs
known time on beliefs and ledger entries (kosmos-design.md
section C point 3). Mechanism complete, tested, pushed by the
coordinator (worker d3ba4e51, verified centrally).

What was built:
- `puspa/world/bitemporal.py` (451 lines, pure Python, no DB,
  no network) — `BeliefVersion` (frozen; key/value/version,
  valid_from/valid_to, known_from/known_to, supersedes) and
  `BitemporalBeliefStore` with `record()` / `correct()` /
  `current()` / `history()` / `known_as_of()` / `valid_as_of()`
  / `as_of()` plus `to_dict` / `from_dict` with history
  validation. Intervals are half-open `[from, to)`; exactly
  one open version per key at any moment. Naive datetimes and
  out-of-order known times are rejected (fail-closed).
  Corrections NEVER update a row in place: a correction appends
  a new version and always closes the old version's `known_to`;
  it closes the old `valid_to` only when the caller passes
  `end_previous_valid_at` (the world really changed). A late
  correction about the past leaves the old valid interval as
  believed. New beliefs default valid time to known time.
  As-of rule: `valid_as_of` returns the latest version whose
  valid interval covers the asked time (a late correction
  overrides the past it covers; a world-change correction does
  not cover the old interval, so the old fact still reads true
  then).
- Ledger — `Ledger.append_event(..., valid_at=...)` in
  `puspa/ledger/ledger.py`: `valid_at` defaults to
  `created_at`, lives in its own column, and is NOT a hash
  input — `compute_event_hash` signature and inputs are
  byte-identical to before, pinned by a regression test on a
  fixed hash vector (`98b22fbe...`). New
  `events_by_valid_time()` orders by when events were true,
  not when recorded (with `COALESCE(valid_at, created_at)`
  fallback for unmigrated DBs). Append tolerates a
  pre-migration DB (retries without the column instead of
  failing the write). One design point: an event gets a single
  valid instant, not a pair — an event is a point in valid
  time; beliefs are intervals. No 32nd event type added (the
  31 revisi-7 types stay closed, G6 precedent).
- Postgres — new table `belief_versions` (one row per version,
  CHECKs on both intervals, RLS like `world_beliefs`) in
  `infra/schema_world.sql`, plus `events.valid_at` in
  `puspa/ledger/schema.sql`. New idempotent migration
  `infra/migrate_bitemporal.sql` (71 lines): creates the table,
  adds `valid_at`, backfills `valid_at = created_at` honestly.
  `puspa/world/store.py` gains `apply_bitemporal_migration()`,
  `save_belief_versions()` (immutable re-save inserts 0 rows),
  `load_belief_history()`, `beliefs_known_at()` /
  `beliefs_valid_at()` SQL as-of queries,
  `delete_belief_history()` (tests only). The `world_beliefs`
  JSON snapshot is untouched — the version table is purely
  additive.
- `docs/bitemporal-state.md` (282 lines): the two time pairs,
  the two correction cases, as-of queries, the ledger hash
  decision and its consequence (`verify_chain` does not
  authenticate `valid_at`; the `puspa_app` UPDATE ban is the
  protection), migration instructions, honest limits.

One real bug caught by the worker's own tests and fixed in
code: the first `valid_as_of` only consulted the current
version, so after a world-change correction the old fact's
interval became unreadable (2 tests failed). The correct rule,
now implemented in both the in-memory store and the SQL query
(`DISTINCT ON ... ORDER BY version DESC`): the latest version
whose valid interval covers the asked time wins. The docs were
corrected to match.

Suite (coordinator, SOLO, 19 Nov 2026): 3,222 passed, 23 skipped,
1 warning, 19m59s, EXIT 0 (log hidden_files/suite-wave22-k3.log).
Reconciliation: 3,200 (Wave 21) + 22 (K3 module tests) = 3,222;
skipped unchanged at 23. NOTE: the first suite attempt was SIGTERM'd
at 64% (no failures visible) by a VM replacement that wiped
Postgres; the replay reinstalled postgresql-16 16.15 + pgvector
0.6.0, the coordinator restarted the cluster, recreated role root,
re-ran infra/schema_core.sql, re-verified RLS, and re-ran the suite
to green. Module tests (coordinator re-run): 22 passed
(test_bitemporal.py — correction appends without deleting
history; known-time query returns the old belief; valid-time
query returns the fact true then; hash pin; chain verifies
after migration + backfill; migration run twice is idempotent,
simulating a pre-K3 DB by dropping the column/table first).
Neighbors (coordinator re-run): 105 passed
(test_world_*.py + test_ledger*.py + test_bitemporal.py) and
55 passed (test_sentinel.py + test_sentinel_logs_traces.py +
test_sentinel_trajectory_insights.py). Ruff check + format:
clean. Mypy on the changed files: clean.

Honest limits: `valid_at` / default valid times are claims,
not measurements; nothing checks them against the world. The
ledger hash does not cover `valid_at` — tampering with it alone
is invisible to `verify_chain`. The world model itself is not
yet wired to record versions automatically (`WorldModel`
beliefs still update in place); callers needing history record
versions explicitly. That wiring is a FOLLOW-UP, not claimed
here — recorded for a later wave. No delete API for versions
(append-only by design); no cross-key in-memory queries (use
the Postgres as-of queries); model-day clock vs wall-clock UTC
is not reconciled; out-of-order known-time ingestion is
rejected rather than spliced in.

Remaining: Kosmos K4, K5, K6, K10, K11, K12, K13, then B
features (B6, B9, B10, B11, B12, B13), then B15 culture LAST.
Then uji + 15 gates + 10 reference tasks (waiting on Nero's
decisions).

## Wave 23: Kosmos K4 (Causal Ledger, DONE 19 Nov 2026)

Third Kosmos item (K1, K3 done). K4 adds hypotheses "X -> Y
under condition C" with strength, evidence, expiry, extending
the learning loop (kosmos-design.md section C point 4).
Mechanism complete, tested, pushed by the coordinator
(worker 10ea8c40, verified centrally).

What was built (home `puspa/world/causal.py`, 580 lines —
hypotheses are beliefs about cause and effect, so they live
with the world model, not in `puspa/memory/`; the learning
loop calls into it):
- `CausalHypothesis` — cause X, effect Y (canonical key + the
  real `effect_value`), condition C ("" = unconditional),
  strength 0..1, evidence links (`ledger_event` /
  `observation` ids, deduped, newest 100 kept), `created_at`
  (known time), `expires_at` (TTL), `valid_from` /
  `valid_to` (valid time), status `active` / `expired` /
  `contradicted`, supporting/contradicting counts.
- Update rule (deterministic, documented in the module
  docstring and docs): first observation proposes at 0.5
  (neutral, not proof); consistent +0.1 (cap 1.0) and
  refreshes expiry; contradicting -0.2 (floor 0.0) —
  asymmetric on purpose, one counterexample disproves more
  than one success proves; below 0.2 -> `contradicted`;
  90-day TTL with no confirmation -> `expired`. Propose is
  idempotent; contradiction never creates a hypothesis;
  expired/contradicted are terminal (re-observing starts a
  fresh hypothesis, old one kept as history). Expiry is swept
  on every read/write, so expired hypotheses are never
  returned by the read path.
- Wiring: `LearningLoop(causal_ledger=...)` is opt-in (a plain
  loop behaves exactly as before). `record_misprediction`
  contradicts the predicted outcome and proposes the observed
  one; `record_feedback` "correct" strengthens, "wrong"
  contradicts and proposes the correction — except a
  correction held as a manual-edit proposal is deliberately
  NOT proposed (tested: it cannot leak back into predictions
  through the ledger). `predict` falls back to the strongest
  active hypothesis when no correction exists, so hypotheses
  genuinely influence predictions. Read path
  `expect(cause, condition)` exists on `CausalLedger`,
  `LearningLoop`, and `WorldModel`; `TaskRunner` and
  `AgentBrain` share the loop's ledger with the model (never
  wiping one the model already has). Both accept `condition=`
  and `evidence_event_id=` for evidence linkage. NO 32nd
  ledger event type — hypotheses cite ledger events by id
  only (test asserts `len(EVENT_TYPES) == 31`).
- Persistence: table `causal_hypotheses`, idempotent additive
  migration `infra/migrate_causal_ledger.sql` + identical DDL
  in `infra/schema_world.sql`; `apply_causal_migration` /
  `save_causal_hypotheses` (upsert by id) /
  `load_causal_ledger` / `causal_hypotheses_valid_at` /
  `delete_causal_hypotheses` in `puspa/world/store.py` (K3
  precedent). One real bug found by the worker's tests and
  fixed in code: Postgres returns UUIDs dashed while
  in-memory ids are undashed hex — load now normalizes.
- Bitemporality: the valid interval applies to the claim
  itself (`valid_to` set on contradiction/expiry;
  `causal_hypotheses_valid_at` answers "which claims held at
  T"). It does NOT apply to strength — the score is a running
  summary updated in place, so no as-of strength query;
  documented as such.
- `docs/causal-ledger.md` (204 lines): the record, the update
  rule, the lifecycle, the read path, honest limits.

Three test failures during development, all fixed and re-run
green (2 test bugs: strength-cap arithmetic, expiry
arithmetic; 1 real determinism wart: same-instant sort ties
fell back to random UUID order — sort key now
`(-strength, created_at, effect, condition)`).

Suite (coordinator, SOLO, 19 Nov 2026): first run 3,259 passed,
23 skipped, 1 warning, 1 FAILED, 21m13s, EXIT 1 (log
hidden_files/suite-wave23-k4.log) — the single failure was the
file-size ratchet: `puspa/world/model.py` grew to 1,626 lines,
past its 1,605 baseline, from the K4 `expect` + ledger slot the
worker added. The coordinator did NOT re-baseline new-logic
growth (the ratchet only sanctions mechanical re-baselines);
instead the file was split on the clean seam its own baseline
comment anticipates: the six belief dataclasses + price-key
helper + three constants moved to new `puspa/world/beliefs.py`
(238 lines), and the causal read path to the
`CausalExpectationMixin` in `puspa/world/causal.py`
(`WorldModel.expect` unchanged as API). model.py shrank
1,626 -> 1,397; baseline re-recorded down to 1,397 with the
reason in the test file. The affected set re-ran green after
the split (161 passed, same as before). Full central suite
re-run after the fix (coordinator, SOLO, 19 Nov 2026):
3,260 passed, 23 skipped, 1 warning, 21m11s, EXIT 0 (log
hidden_files/suite-wave23-k4.log). Reconciliation: 3,222 (Wave 22)
+ 38 (K4 module tests) = 3,260; skipped unchanged at 23.
Module tests (coordinator re-run): 38 passed
(test_causal_ledger.py 17 + test_causal_learning_wiring.py 16
+ test_causal_store.py 5, Postgres live). Affected existing
tests run together with the new ones (coordinator re-run):
161 passed (incl. test_learning_loop, test_feedback,
test_loop_persistence, test_loop_persistence_db,
test_world_model, test_world_beliefs, test_world_store,
test_bitemporal, test_world_brain_wiring,
test_failed_steps_teach, test_turn_loop). Ruff check + format:
clean. Mypy on the changed files: clean.

Honest limits: strength is counting with fixed steps, not a
probability — 0.8 must never be presented as "80% likely".
One observation proposes a hypothesis; a single coincidence
becomes a claim at 0.5 (mitigated by the neutral start and
fast contradiction, not cured). Conditions are exact strings,
no condition language; the loop passes "" unless the caller
names one, so most loop-formed hypotheses are unconditional.
`75000` vs `"75000"` are different effects; evidence caps at
100 links. The ledger only observes correlation, never proves
causation. THE PLANNER DOES NOT CONSULT HYPOTHESES YET — the
read path exists on the world model, but `planner.py` still
plans from price/bill/schedule/capability beliefs only;
wiring causal expectations into plan steps is a FOLLOW-UP,
recorded for a later wave (verified by the coordinator: no
"causal" / "expect(" in puspa/world/planner.py).

Remaining: Kosmos K5, K6, K10, K11, K12, K13, then B features
(B6, B9, B10, B11, B12, B13), then B15 culture LAST. Then uji
+ 15 gates + 10 reference tasks (waiting on Nero's decisions).


