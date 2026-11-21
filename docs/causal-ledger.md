# Causal Ledger (Kosmos causal ledger)

Hypotheses of the form "X -> Y under condition C" with a
strength, evidence, and expiry (`docs/kosmos-design.md`
section C point 4). A hypothesis is a belief about cause and
effect, so it lives in `puspa/world` next to the other beliefs
(`puspa/world/causal.py`), and the learning loop
(`puspa/memory/learning_loop.py`) proposes and updates
hypotheses from the action -> outcome pairs it already sees.
No LLM call is involved anywhere: proposing, strengthening,
weakening, and expiring are all deterministic arithmetic.

## The record

One `CausalHypothesis` holds:

| Field | Meaning |
| --- | --- |
| `cause` | X, the action or task name (plain string) |
| `effect` | Y, the canonical key of the observed outcome (see below) |
| `effect_value` | the outcome itself, so the read path returns a real value, not just a key |
| `condition` | C, the condition the claim holds under; "" means unconditional |
| `strength` | 0..1, how much the system believes the claim |
| `evidence` | links `{"kind", "id"}`: kind `ledger_event` (an id from the append-only ledger) or `observation` (an experience id from the learning loop) |
| `created_at` | known time: when the system formed the hypothesis |
| `expires_at` | TTL deadline: no confirming observation by then, and the hypothesis expires |
| `valid_from` / `valid_to` | valid time: when the cause-effect held in the world |
| `status` | `active`, `expired`, or `contradicted` (the last two are terminal) |
| `supporting` / `contradicting` | observation counts |

The effect key is canonical JSON of the outcome (keys
sorted), so the same outcome always maps to the same key
whatever shape it arrived in. Keys longer than 200 characters
are stored as `sha256:<hex>` of the canonical form: bounded
keys without ever colliding two different effects through
truncation.

## The update rule

Stated here and in the module docstring — these numbers are
bookkeeping choices, not statistics, and nothing here
pretends otherwise:

- A first observation proposes the hypothesis at strength
  **0.5**: one observation is not proof, so a new hypothesis
  starts neutral, neither believed nor disbelieved. The
  proposing observation counts as the first supporting one.
- A consistent observation adds **0.1** (capped at 1.0) and
  refreshes the expiry to now + TTL.
- A contradicting observation removes **0.2** (floored at
  0.0). The asymmetry is deliberate: one counterexample
  disproves a causal claim more than one success proves it.
- Strength below **0.2** means the hypothesis is
  **contradicted**: its status flips and its valid interval
  closes at the contradicting observation's time.
- No confirming observation for **90 days** (the TTL) means
  the hypothesis is **expired**: its valid interval closes at
  the expiry time. Expiry is checked on every read and write,
  so an expired hypothesis is never returned even when nobody
  calls the explicit expiry pass.
- Proposing is idempotent and is not evidence: proposing a
  triple that already has a live hypothesis returns it
  unchanged. Contradiction never creates a hypothesis:
  weakening a triple nobody proposed returns nothing.

## Lifecycle

```
propose (first observation, strength 0.5)
  -> strengthen on consistent observations (+0.1, cap 1.0)
  -> weaken on contradicting observations (-0.2)
  -> contradicted when strength falls below 0.2 (terminal)
  -> expired when the TTL passes with no confirmation (terminal)
```

Expired and contradicted are terminal: observing the same
triple again starts a fresh hypothesis with a new id, and the
old one stays stored as history. The daily sleep
consolidation (daily sleep consolidation and monthly ontology review) calls `prune_expired()` for an explicit
expiry pass that reports how many hypotheses expired.

## The read path

"What do we expect if X under C?" — `CausalLedger.expect(cause, condition)` returns the active hypotheses for the cause as plain dicts, strongest first (ties: oldest first, then by effect and condition, so the order is deterministic). A hypothesis applies when its condition is unconditional ("") or equals the queried condition; a conditional claim is never returned when the caller does not state the condition. Expired and contradicted hypotheses are never returned.

The same read path is available where it is used:

- `LearningLoop.expect(cause, condition)` — empty when the
  loop has no ledger attached.
- `WorldModel.expect(cause, condition)` — empty when the
  model has no ledger attached. The read path is a mixin
  (`CausalExpectationMixin` in `puspa/world/causal.py`), not
  code in `puspa/world/model.py`: that file's size baseline
  forbids growth, and the one-method delegation is the clean
  seam its baseline comment anticipates. The session brain
  (`puspa/chassis/agent_session.py`) and the `TaskRunner`
  (`puspa/runtime/tasks.py`) share the learning loop's ledger
  with the model, so the model's read path sees the same
  hypotheses the loop is learning. Sharing only ever sets the
  model's ledger when the loop has one; it never wipes a
  ledger the model already carries.

Hypotheses influence predictions, not just storage:
`LearningLoop.predict` falls back to the ledger when no
correction exists for the task — the strongest active
hypothesis supplies the predicted value. A correction still
wins over the ledger.

## Wiring into the learning loop

The ledger is opt-in: `LearningLoop(causal_ledger=CausalLedger())`. A loop without one behaves exactly as before.

| Loop event | Causal effect |
| --- | --- |
| `record_misprediction` | contradicts the hypothesis for the predicted outcome; proposes/strengthens the one for the observed outcome |
| `record_feedback(..., "correct")` | strengthens the hypothesis for the predicted outcome |
| `record_feedback(..., "wrong")` without a correction | contradicts the hypothesis for the predicted outcome only |
| `record_feedback(..., "wrong", correction=...)` | contradicts the predicted one; proposes/strengthens the correction — unless the correction is held as a manual-edit proposal, in which case it is deliberately *not* proposed (a held proposal must not leak back into predictions through the ledger) |

Both methods accept `condition=""` (the condition the claim
holds under) and `evidence_event_id=None` (the id of the real
ledger event that recorded the outcome, stored as a
`ledger_event` evidence link). Every causal observation also
carries the experience id of the outcome as an `observation`
link. Causal updates are fail-open: a ledger failure never
breaks recording.

No new ledger event type was added: the closed set of 31
revision-7 event types stays closed (causal ledger follows the audit trail with initiator
precedent). Hypotheses reference ledger events by id in
their evidence only; when the loop needs to log anything it
uses existing event types (for example `evidence_recorded`).

## Persistence

Table `causal_hypotheses`, one row per hypothesis (migration
`0006_world.sql` — the fresh-install and upgrade paths are the
same migration now; it is idempotent and additive). Hypotheses mutate in place (strength updates,
status changes), so saving upserts by id
(`ON CONFLICT (id) DO UPDATE`); expired and contradicted rows
are saved too, as history. Python side, in
`puspa/world/store.py`: `apply_causal_migration()`,
`save_causal_hypotheses()`, `load_causal_ledger()` (returns
the full history, or None when nothing was saved),
`causal_hypotheses_valid_at()`, `delete_causal_hypotheses()`
(tests only).

## Bitemporality: where it applies and where it does not

It applies to the claim itself: `valid_from` is when the
cause-effect was first observed to hold (defaults to the
moment the hypothesis was formed), `valid_to` is set when
the claim stops holding — the contradicting observation's
time, or the expiry time. "Which causal claims held at time
T" is answered from this interval, in memory
(`hypothesis.is_valid_at(at)`) and in Postgres
(`causal_hypotheses_valid_at`).

It does not apply to the strength score: strength is a
running summary updated in place, not a fact about the past,
so there are no strength versions and no as-of strength
query. Known time is `created_at` (when the system formed
the hypothesis); there is no `known_to`, because hypotheses
are never corrected in place — they change status, and the
old status stays readable in the stored record.

## Honest limits

- The update rule is counting with fixed steps, not
  statistics: strength 0.8 does not mean "80% likely", it
  means "three more confirmations than contradictions, from
  a neutral start". Nothing downstream should present it as
  a probability.
- One observation proposes a hypothesis. A single
  coincidence — the bill happened to be Rp75.000 the one
  time the user checked at noon — becomes a hypothesis at
  0.5. The neutral start and the fast contradiction step
  are the mitigation, not a cure: a hypothesis is only
  trusted once it has been confirmed repeatedly, and the
  read path orders by strength for exactly that reason.
- Conditions are exact strings, not a condition language:
  "peak_hours" matches only "peak_hours". Two wordings of
  the same condition ("peak hours" vs "peak_hours") are two
  different conditions, and their evidence never merges.
  The learning loop passes "" unless the caller names a
  condition, so most loop-formed hypotheses are
  unconditional.
- The effect is keyed by its canonical form: two outcomes
  that differ only in key order are the same effect, but
  two outcomes that differ in any value are different
  effects, even when a human would call them the same
  (75000 vs "75000" are different effects).
- Evidence is capped at the newest 100 links per
  hypothesis; old links drop off silently. A hypothesis
  with a long history keeps its counts, not its full paper
  trail.
- The ledger does not know whether the cause really caused
  the effect: it only knows the effect followed the cause
  repeatedly. Correlation is all it ever observes; the
  name "causal" is the claim being tested, not a proof.
- The planner does not consult hypotheses yet: the read
  path exists on the world model for planning to use, but
  `puspa/world/planner.py` still plans from price, bill,
  schedule, and capability beliefs only. Wiring causal
  expectations into plan steps is a follow-up, not part of
  this item.
