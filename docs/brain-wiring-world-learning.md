# Brain wiring: world model + learning loop in the live loop

Date: 18 Nov 2026. Worker 4 (wiring phase). This wires the world
model (`puspa/world/model.py`) and the learning loop
(`puspa/memory/learning_loop.py`) into the live agent loop and into
Postgres persistence. Revision 7 was not changed: no definition
text, gate, number, or piece of evidence was altered, no demotion
rule was added, and the verifier, permission token, single outbound
gate, and orphan-number rule were not touched.

## The advisory rule

The world model is an advisor, never a gate. It predicts before
acting and learns after observing, but it never skips the verifier,
never bypasses a permission token, never opens the outbound gate,
and never supplies a meaningful number: numbers shown to the user
still come only from the code renderer and the Book of Evidence.
When the model or the loop is missing or raises, the task runner
keeps working exactly as before (fail-open for prediction,
fail-closed for action: no action is ever taken on the model's word
alone).

## Wiring map

### 1. Task loop: `TaskRunner` (`puspa/runtime/tasks.py`)

`TaskRunner` takes two optional constructor arguments,
`world_model` and `learning_loop`. When both are absent the runner
behaves exactly as before.

Per step, in `TaskRunner.run` stage 3 (run):

- Before acting: `_world_action_for_step(step)` translates the step
  into a world-model action (see the mapping table below). When the
  step maps to an action, `_predict_step` calls
  `WorldModel.predict(action)` and records a `world_prediction`
  event (advisory, with the predicted outcome and confidence).
- After the handoff passes: `_observe_step` calls
  `WorldModel.update({"action", "observed", "task": step.name,
  "day"})`. `update` scores the pending prediction, corrects the
  model, and reports a miss to the learning loop through the
  `record_misprediction` seam (task, predicted, observed, error,
  context). On a miss a `world_misprediction` event is recorded
  too.
- Steps with no world action are not predicted. Their outcome is
  still folded into the model through `ingest_task_result`
  (capability and schedule beliefs). Steps with a world action are
  not ingested as well: `update` already folds their outcome
  (price, bill, schedule, capability), so ingesting them again
  would count one outcome twice.

The prediction is recorded twice on purpose: once as the dedicated
`world_prediction` / `world_misprediction` events (visible in
`TaskRunner.event_memory()`), and once inside the
`handoff_proposed` / `handoff_handed_over` event content (keys
`world_prediction` / `world_observation`). The ledger keeps a
closed set of 31 event types (revision 7 section 2.3), so a
production Ledger sink drops the dedicated events silently
(best-effort, see `make_ledger_sink`); the handoff content is the
copy the ledger keeps. No new event type was added.

Outbound plans stop at `pending_approval` before stage 3, so no
prediction or observation ever happens for an outbound step.

### Step to action mapping

| Step inputs `type` | World action | Predicted and scored |
| --- | --- | --- |
| `check_price`, `check_bill`, `pay_bill`, `wait_day`, `buy_from_store`, `estimate_task`, `attempt_task` | the inputs themselves (plus `task_kind` defaulting to the step name) | yes, through `predict` + `update` |
| anything else | none | no; outcome folded through `ingest_task_result` only |

### 2. Session brain: `puspa/chassis/agent_session.py`

`AgentBrain` holds one `WorldModel` and one `LearningLoop` for one
(user, agent) pair. `get_session_brain(user_id, agent_id)` returns
the same instances on every call for the same pair, across turns,
until `reset_session_brains` clears them (tests only). `bind_turn`
registers a brain passed by the caller (`world_model` /
`learning_loop` keyword arguments), or reuses (creating it first)
the pair's existing brain. The brain is in-memory per process:
beliefs that must survive a restart are persisted through
`puspa/world/store.py`, not here.

`PuspaChassis` (`puspa/chassis/factory.py`) carries the same pair
as optional `world_model` / `learning_loop` fields; `brain()`
returns the pair, or None when no brain was given. Callers that run
tasks take the pair from there.

`WorldModel.learning_loop` (new attribute, default None) points the
model's misprediction reports at the session loop instead of the
module-level default loop. It is runtime wiring, not knowledge: it
is not part of `state()` and is not persisted. The lazy import seam
is unchanged: importing `puspa.world.model` still does not import
the learning loop, and a missing loop still leaves the record in
`WorldModel.mispredictions`.

### 3. Planner and curriculum pass-through

`Planner` (`puspa/runtime/planner.py`) and `CurriculumRunner` plus
`run_curriculum_tick` (`puspa/scheduler/curriculum.py`) accept
optional `world_model` / `learning_loop` arguments and forward them
to the `TaskRunner` they build. With no arguments their behaviour
is unchanged, so existing trigger and curriculum behaviour is
unaffected.

## Persistence: `puspa/world/store.py`

One row per (user, agent) in table `world_beliefs`
(migration `0006_world.sql`).

Stored: entities, relations, day, price beliefs, bill beliefs,
schedule beliefs, capability beliefs, and belief contradictions.
That is the durable knowledge: beliefs and the corrections folded
into them. Not stored: transient prediction stats (`stats`,
`episode_accuracies`, `mispredictions`), the pending prediction,
and the learning loop corrections (they live in `LearningLoop` in
memory; persisting them is a separate step).

Belief values in `state()` are dataclass instances, so
`serialize_state` converts each belief with `dataclasses.asdict`
and `deserialize_state` rebuilds the dataclass from the stored
dict. The round trip is exact for plain JSON values.

API:

- `create_schema(dsn=None)`: apply migration `0006_world.sql`
  (idempotent). Prerequisite: the Phase 0 schema (users, agents)
  already exists, because `world_beliefs` references both tables.
- `save_world_state(user_id, agent_id, model, dsn=None)`: upsert
  one row (a later save overwrites the earlier row).
- `load_world_state(user_id, agent_id, dsn=None)`: state dict for
  `_restore_state`, or None when nothing was saved.
- `load_world_model(user_id, agent_id, dsn=None)`: restored
  `WorldModel`, or None. The caller sets `model.learning_loop`.
- `delete_world_state(user_id, agent_id, dsn=None)`: tests only.

Apply the schema (after Postgres is up):

```sh
cd build
.venv/bin/python -c "from puspa.world.store import create_schema; create_schema()"
```

or run `python -m puspa.migrations up --target 0006`. DSN resolution
follows `puspa.ledger`: argument, else `PUSPA_TEST_DSN`, else the
peer-auth default `dbname=puspa_test user=root
host=/var/run/postgresql`.

## Tests

- `tests/test_world_brain_wiring.py` (in-memory, no Postgres):
  end-to-end through the real `TaskRunner` (predict, miss recorded
  in the loop, next prediction corrected), unmapped-step folding,
  fail-open on a broken model, plain runner without a brain,
  session brain shared across turns, `bind_turn` registering an
  explicit brain, `PuspaChassis.brain`, Planner pass-through, and
  CurriculumRunner pass-through. Run with
  `pytest tests/test_world_brain_wiring.py -q --noconftest`.
- `tests/test_world_store.py`: JSON serialization round trip
  (in-memory, no Postgres) plus Postgres save/load round trip and
  upsert. Each database test skips itself when Postgres is not
  reachable. Run with `pytest tests/test_world_store.py -q`.

## Known limits

- A failed step that aborts the run (handoff rejected, budget
  exhausted) is not folded into the model: only completed steps
  teach the model. The failure itself is still recorded as a
  `task_failed` event.
- `TaskRunner` predicts only steps whose inputs carry a world-model
  action type. Generic steps teach capability beliefs through
  `ingest_task_result` but are never predicted.
- The dedicated `world_prediction` / `world_misprediction` events
  do not reach the production ledger (closed event-type set); the
  ledger copy lives in the handoff event content.
- The learning loop corrections are not persisted yet: only the
  world model state is. After a restart the model keeps its
  beliefs, but the loop starts with an empty correction table
  until new mispredictions arrive.
