# Status: Brain wiring phase (Penyambungan Otak)

Date: 18 Nov 2026. Coordinator: wiring phase after the Brain phase
(main `72b646c`, 641 passed). Revision 7 was NOT changed: no
definition text, gate, number, or piece of evidence was altered,
no demotion rule was added, and the verifier, permission token,
single outbound gate, and orphan-number rule were not touched.
Full design write-up: `docs/brain-wiring-world-learning.md`.

## What was wired, per gap

### 1. World model + learning loop into the live loop

- `puspa/runtime/tasks.py`: `TaskRunner` takes optional
  `world_model` / `learning_loop` constructor arguments. With both
  absent the runner behaves exactly as before.
- Per step, in stage 3 (run): before acting, the step is
  translated to a world action and `WorldModel.predict` is called
  (advisory `world_prediction` event); after the handoff passes,
  `WorldModel.update` scores the real result and a miss is
  reported to the learning loop through the `record_misprediction`
  seam (task, predicted, observed, error, context), plus a
  `world_misprediction` event. Steps with no world action are
  folded through `ingest_task_result` only, so no outcome is
  counted twice. Outbound plans stop at `pending_approval` before
  stage 3, so no prediction or observation ever happens for an
  outbound step.
- No new ledger event type was added (revision 7 keeps a closed
  set of 31): the prediction is also attached to the
  `handoff_proposed` / `handoff_handed_over` event content, which
  is the copy the production ledger keeps. The dedicated events
  are dropped silently by a production Ledger sink (best-effort).
- Advisory rule: the model predicts before acting and learns
  after observing, but it never skips the verifier, never bypasses
  a permission token, never opens the outbound gate, and never
  supplies a meaningful number (numbers shown to the user still
  come only from the code renderer and the Book of Evidence).
  When the model or the loop is missing or raises, the runner
  keeps working exactly as before (fail-open for prediction,
  fail-closed for action).
- `puspa/chassis/agent_session.py`: `AgentBrain` holds one
  `WorldModel` + one `LearningLoop` per (user, agent) pair,
  shared across turns via `get_session_brain`; `bind_turn`
  registers an explicit brain or reuses the pair's existing one.
  `puspa/chassis/factory.py`: `PuspaChassis` carries the pair and
  `brain()` returns it (or None).
- `WorldModel.learning_loop` (new attribute, default None) routes
  misprediction reports to the session loop; it is runtime wiring,
  not knowledge: it is not part of `state()` and is not persisted.
  The lazy import seam is unchanged.
- `puspa/runtime/planner.py` and `puspa/scheduler/curriculum.py`:
  brain pass-through to the `TaskRunner` they build.

E2E proof (in-memory, through the real `TaskRunner`, not a scratch
script): `tests/test_world_brain_wiring.py::
test_e2e_miss_recorded_then_next_prediction_corrected`: the model
predicts before the step runs, the observed result differs, the
miss is recorded in the learning loop, and the next prediction for
the same task returns the corrected value.

### 2. Curriculum runner

- New module `puspa/scheduler/curriculum.py`: `CurriculumRunner`
  pulls `propose_next_task()` from the learning loop, executes the
  proposed learning task through `Planner` + `TaskRunner`, and
  feeds the outcome back (success adds experience and clears the
  gap; failure is recorded as a new misprediction).
- Hard limits, all enforced in code and tested: max 5 learning
  tasks per day by default (`DEFAULT_MAX_LEARNING_TASKS_PER_DAY`,
  changeable per runner); cost ceiling Rp2.000 per task (own
  `BudgetTracker`, same value as the harness); idle only (an
  `is_idle` callable or the `user_task_active` flag; a busy run
  returns `busy` without consuming the daily budget); never an
  outbound action without approval (outbound learning tasks stop
  at `pending_approval` with the executor never called).
- Wiring point: `scheduler.tick(..., curriculum=None)`. Default
  `None` means no curriculum work at all and trigger behaviour is
  unchanged. Production passes one long-lived `CurriculumRunner`
  (the daily cap counts per runner instance, so a fresh runner per
  tick would reset it).
- Tests: `tests/test_curriculum_runner.py`, 18 tests.

### 3. User feedback signal

- `LearningLoop.record_feedback(task, verdict, *, correction=None,
  source="user")` in `puspa/memory/learning_loop.py`, verdict in
  {"correct", "wrong"}, plus a module-level seam delegating to the
  default loop (same shape as `record_misprediction`).
- `wrong` + correction enters the experience log and the
  correction table like a misprediction (observed = correction),
  marks an active skill for the task suspect, and
  `propose_next_task` offers a retry for the gap. `wrong` without
  a correction still logs the gap; the prediction does not change.
  `correct` only reinforces (positive experience) and never
  touches corrections, gaps, or skills.
- Every feedback-derived entry carries `source: "user_feedback"`
  (constant `FEEDBACK_SOURCE`) and `kind: "user_feedback"`; the
  calling actor is kept separately in `feedback_source`.
- A feedback correction never overwrites a manual user edit: when
  `note_manual_edit` already exists for the task, the correction
  is stored as a proposal (`conflicts_with_manual_edit: True`) in
  `feedback_proposals`, readable through
  `list_feedback_proposals(task=None)`, and `predict` is unchanged.
- Minimal interface: CLI command `/umpanbalik <tugas> benar|salah
  [koreksi]` in `cli/puspa_cli/repl.py` (CLI is local only, not
  pushed to the repo). After a `wrong` feedback the REPL shows the
  next retry task from `propose_next_task`.
- Tests: `tests/test_feedback.py`, 13 tests.

### 4. Belief expansion

- `puspa/world/model.py`: two new belief kinds, both from
  observation only, never invented.
  - Schedule (`_ScheduleBelief`, per task kind): average duration
    from task results plus a deadline reliability rate
    (`on_time_rate`). New action `estimate_task` predicts
    `expected_days` and `deadline_reliable` (rate >= 0.8).
  - Capability (`_CapabilityBelief`, per task kind): success /
    failure ratio from task results. New action `attempt_task`
    predicts success or failure. `ingest_task_result` now learns
    from ALL task results, success and failure (a failure still
    teaches capability but never changes price or bill beliefs).
    `predict` for pay/buy turns to failure with reason
    `low_capability` when the success ratio is below 0.5 with at
    least 3 observations.
- Versioned, with decay and contradiction following the
  `procedural.py` pattern and no demotion: verified after 3
  consistent observations; `decay_stale_beliefs()` returns a
  verified belief untouched for 90 days to trial (old version kept
  in `history`); an observation contradicting a verified belief is
  held aside as a candidate (schedule needs 3 confirmations,
  capability needs 2 consecutive runs) and recorded in
  `model.belief_contradictions`, never overwriting silently.
- `state()` / `_restore_state()` include both new belief kinds
  (restore uses `.get`, so old saved state still loads).
- Tests: `tests/test_world_beliefs.py`, 11 tests.

### 5. Honest evaluation: second simulator with irregular drift

- New `puspa/world/noisy_simulator.py`: `NoisySimulatedWorld`
  with the SAME interface as `SimulatedWorld` (`price`,
  `bill_status`, `observe_price`, `observe_bill`, `act`,
  `run_episode`, `run_episodes`). Base drift is the same, but each
  day: 70% the exact drift, 20% off by Rp1.000-2.000, 10% a spike
  of Rp10.000-20.000. Noise is seeded (default 7, each episode
  uses `seed + index`), so runs are reproducible. The
  deterministic simulator is unchanged as the mechanism check.
- Accuracy per episode, 17 predictions each:
  - Deterministic (fixed drift): 0.647, 1.000, 1.000 (unchanged).
  - Noisy (irregular drift): 0.588, 0.706, 0.588. No episode
    reaches 1.0. The mechanism still learns and corrects to the
    last observed price; the 1.0 figure applies ONLY to the
    fixed-drift mechanism check, never to real prices.
- Tests: `tests/test_world_noisy.py`, 5 tests, including an
  explicit assertion that no noisy episode is 1.0.

## Persistence: `puspa/world/store.py`

One row per (user, agent) in table `world_beliefs`
(`infra/schema_world.sql`, apply with `create_schema()` after
Postgres is up; prerequisite is the Phase 0 schema, because the
table references `users` and `agents`). Upsert: a later save
overwrites the earlier row. RLS like the other schemas.

Stored: entities, relations, day, price beliefs, bill beliefs,
schedule beliefs, capability beliefs, and belief contradictions.
Not stored: transient prediction stats (`stats`,
`episode_accuracies`, `mispredictions`), the pending prediction,
and the learning loop corrections (they live in `LearningLoop` in
memory; persisting them is a separate step).

API: `create_schema`, `serialize_state` / `deserialize_state`
(exact JSON round trip, beliefs rebuilt as dataclasses),
`save_world_state`, `load_world_state`, `load_world_model`
(caller sets `model.learning_loop`), `delete_world_state`
(tests only).

Tests: `tests/test_world_store.py`, 2 tests (serialization round
trip in-memory; Postgres save/load round trip plus upsert, one row
per agent). One test-cleanup fix by the coordinator: the test used
to `DELETE FROM users` in its `finally` block, which cascades into
`agent_meta` and is rightly refused by the `tolak_delete_puspa`
database guard for the required Puspa row. The user row is now
left for the shared conftest fixture, which removes it with
TRUNCATE (TRUNCATE does not fire the row-level DELETE trigger).

## Final test numbers (full suite, single process)

Command, from `build/`:

    NO_PROXY=localhost,127.0.0.1 no_proxy=localhost,127.0.0.1 \
      .venv/bin/python -m pytest -q

Result: **699 passed, 0 failed, 5 skipped, 1 warning**, in about
5 minutes. Baseline before this phase was 641 passed, 5 skipped.
New tests in this phase: beliefs 11, noisy 5, feedback 13,
curriculum 18, brain wiring 9, world store 2. Total new: 58.
641 + 58 = 699, which matches the run exactly. The 5 skips are the
4 live-container Docker tests (kernel rejects `setns`) plus the
Chromium reader test (no browser on the host), same as before.
The 1 warning is the pre-existing starlette testclient
DeprecationWarning, unchanged.

No new dependency was added in this phase (`requirements.txt`
unchanged). No new third-party pattern was introduced: every
pattern used here was already attributed in the Brain phase
(`docs/reuse.md`).

## Environment recovery (Postgres)

The VM restarted around 01:43 UTC on 18 Nov 2026 and wiped the
Postgres packages, roles, and databases (the known fragile-Postgres
pattern). Recovery, in order, as done by the coordinator:

1. `apt-get update` was slow through the proxy (about 3 minutes)
   and first left zero package lists; a second full run populated
   them (50, then 68 ubuntu list files).
2. `apt-get install postgresql-16` failed with unmet dependencies
   because the `cloudflare-warp` package was installed with its
   dependencies missing (`gnupg2`, `libpcap0.8`,
   `desktop-file-utils`, `libayatana-appindicator3-1`,
   `libwebkit2gtk-4.1-0` all uninstalled) and apt refuses every
   transaction while a package is broken. Warp was verified NOT
   running (`warp-svc` inactive, no process) and NOT providing the
   network (egress works through the 198.19.0.1:3128 gateway
   proxy; all downloads kept working after removal). It was
   removed with `dpkg --remove cloudflare-warp` (exit 0; only
   config files remain, `rc` state). Nothing depends on it.
3. `postgresql-16` installed; cluster 16 main online on port 5432.
4. `postgresql-16-pgvector` installed: without it every test
   errored at setup (`extension "vector" is not available`).
5. Roles and database recreated from `infra/schema_core.sql`
   (role `root` superuser bootstrapped first via the `postgres`
   OS user, then `puspa_app` NOLOGIN and database `puspa_test`).

Note for future sessions: after a VM restart, expect Postgres
gone. Recovery order is: update (slow, retry if lists stay empty)
-> remove the broken `cloudflare-warp` package if it blocks apt
-> install `postgresql-16` AND `postgresql-16-pgvector` -> create
the `root` role -> run `infra/schema_core.sql`. The old
`infra/setup_db.sql` no longer exists; the setup lives in
`infra/schema_core.sql`.

## Known gaps

- The learning loop corrections are not persisted yet: only the
  world model state is. After a restart the model keeps its
  beliefs, but the loop starts with an empty correction table
  until new mispredictions arrive.
- A failed step that aborts the run (handoff rejected, budget
  exhausted) is not folded into the model: only completed steps
  teach it. The failure itself is still recorded as a
  `task_failed` event.
- `TaskRunner` predicts only steps whose inputs carry a
  world-model action type. Generic steps teach capability beliefs
  through `ingest_task_result` but are never predicted.
- The dedicated `world_prediction` / `world_misprediction` events
  do not reach the production ledger (closed event-type set); the
  ledger copy lives in the handoff event content.
- The curriculum daily cap counts per runner instance. Production
  must pass one long-lived `CurriculumRunner` to `tick()`; a
  fresh runner per tick would reset the cap.
- CLI tests (`cli/tests/test_repl.py`) cannot be collected with
  pytest on this host (`ModuleNotFoundError: byok_demo`,
  pre-existing breakage, not caused by this phase). The new
  `/umpanbalik` handler was verified through an isolated harness
  instead.
- The world model still only believes in prices, bills,
  schedules, and capabilities. It has no beliefs about contacts,
  the user's habits beyond tasks, or the wider world.
- Old Indonesian docstrings and em dashes in pre-existing files
  were not touched. No em dash and no Indonesian naming was added
  by this phase (checked with grep over every new or changed file).
- A real end-to-end sandbox run still needs a host with working
  Docker (unchanged from the Brain phase).

## Files changed by this phase

- New: `puspa/world/noisy_simulator.py`,
  `puspa/world/store.py`, `puspa/scheduler/curriculum.py`,
  `infra/schema_world.sql`,
  `docs/brain-wiring-world-learning.md`,
  `docs/status-brain-wiring.md` (this file),
  `tests/test_world_beliefs.py`, `tests/test_world_noisy.py`,
  `tests/test_feedback.py`, `tests/test_curriculum_runner.py`,
  `tests/test_world_brain_wiring.py`, `tests/test_world_store.py`.
- Changed: `puspa/world/model.py`, `puspa/world/__init__.py`,
  `puspa/memory/learning_loop.py`, `puspa/runtime/tasks.py`,
  `puspa/runtime/planner.py`, `puspa/chassis/__init__.py`,
  `puspa/chassis/agent_session.py`, `puspa/chassis/factory.py`,
  `puspa/scheduler/scheduler.py`, `docs/status-brain-phase.md`
  (section 5 appended), `docs/reuse.md` (wiring section),
  `cli/puspa_cli/repl.py` (local only, not pushed).
- Not changed: `build/web-replica/`, any artifact,
  `~/workspace/puspa-refactor/`, revision 7, `README.md`,
  `requirements.txt`, `NOTICE.md` (no new pattern or dependency
  to attribute).

## Wiring gaps fixed (18 Nov 2026, after the wiring phase)

Three gaps the wiring phase left open were fixed as separate
iterations, each with its own commit and push, each verified by a
full SOLO suite run. Full suite after both fixes: 918 passed,
5 skipped, 1 warning.

### Gap 1: learning loop corrections are persisted

Before: only the world model state was stored (table
`world_beliefs`); the learning loop started with an empty
correction table on every restart.

Now: corrections (one row per task in `loop_corrections`) and the
experiences that carry a correction (in `loop_experiences`) are
saved to Postgres and loaded back at startup. New module
`puspa/memory/loop_store.py` (`save_loop_state`,
`load_loop_state`, `load_learning_loop`, `create_schema`),
schema `infra/schema_learning_loop.sql`,
`loop_state()` / `restore_loop_state()` in
`puspa/memory/learning_loop.py`, and DSN load wiring in
`puspa/chassis/agent_session.py` (`get_session_brain(...,
dsn=...)` loads the saved loop when a DSN is given). Proof:
`tests/test_loop_persistence_db.py` round-trips save, builds a
fresh loop from zero, and asserts the same predictions
(`watch_price` 11500, `pay_bill` "paid 75000"). Commit
279cb361fdbaa3fe22b061095d5ce1c5b7298cd5.

Honest limits: only corrections and correction-carrying
experiences are persisted (draft skills, manual edits, feedback
proposals, and correction-less gaps are NOT stored; gaps are
rebuilt from the corrections on restore). Load happens only
when the caller passes a DSN; there is no automatic schema
creation on startup. One round-trip bug was caught by the DB
test during verification and fixed in the same iteration: the
`feedback_source` field of a correction was dropped on save
(the `loop_corrections` table had no such column), so the column
was added to the schema (with an idempotent ALTER for existing
databases) and to save/load.

### Gap 2: failed steps teach the model

Before: only steps that completed taught the model. A step that
aborted the run (handoff refused, budget exhausted, run failed)
left no trace in the model.

Now: `WorldModel.ingest_step_failure` in `puspa/world/model.py`
plus `_fold_step_failure` in `puspa/runtime/tasks.py` fold an
aborting step into the model, on two separate paths, and the
difference is deliberate:

- `gate_rejected` (handoff refused by the schema, budget
  exhausted): capability learns, the pending prediction is
  discarded unscored, and no price/bill belief and no
  learning-loop correction is touched. A refusal is policy
  evidence, not world evidence.
- `world_failed` (the executor raised): a pending prediction is
  scored against the failure, so a miss is recorded as a
  `world_misprediction` event and reported to the learning loop,
  and capability learns.

Behavior change: executor exceptions now become a failed task
instead of propagating out of `TaskRunner.run()`. Commit
9d11c8d12b263fc1a190db7ad989b31c9c4762b7. Tests:
`tests/test_failed_steps_teach.py` (12 tests, including
budget exhaustion teaching capability and not price).

One regression was caught by the full suite during verification
and fixed in the same iteration: `BudgetExhausted` raised inside
a charging executor wrapper was caught by the generic executor
exception handler and misclassified as `world_failed`, which
lost the `budget_exhausted` event in the curriculum runner.
`tasks.py` now re-raises `BudgetExhausted` from the executor
call so it reaches the outer handler that records
`budget_exhausted` and folds it as a gate refusal.

### Gap 3: contact and habit beliefs (not done yet)

The world model still has no beliefs about contacts or the
user's habits. That is the next iteration after these two
commits: two new belief kinds in `puspa/world/model.py`, both
only from observation, versioned with decay and contradiction
following `puspa/memory/procedural.py`, persisted in the world
store. Wide-world facts stay out of scope (graph and memory
layer, covered by the HippoRAG and LightRAG deepening).

### Gap 3: contact and habit beliefs

Before: the world model only believed in prices, bills,
schedules, and capabilities. It had no beliefs about contacts
or the user's habits.

Now: two new belief kinds in `puspa/world/model.py`, both only
from observation, never invented, versioned with decay and
contradiction following `puspa/memory/procedural.py` (trial to
verified after 3 observations, decay back to trial after 90
days without an observation, contradictions held as candidates,
old versions kept in `history`, no demotion):

- Contact beliefs (`_ContactBelief`): interaction patterns per
  contact from observed task and message results: `reply_rate`,
  `preferred_channel`, `mean_reply_delay_hours` (only from
  replies actually observed). They enter through
  `ingest_task_result` when the result carries a `contact_id`
  (plus optional `channel`, `replied`, `reply_delay_hours`),
  or through the new `contact_reply` action. Contradiction
  follows the capability pattern: a verified belief with reply
  rate at or above 0.8 (or at or below 0.2) contradicted twice
  in a row is replaced by a new version; a single contradiction
  is held as a candidate.
- Habit beliefs (`_HabitBelief`): recurring patterns from the
  user's own task history per task type: `peak_hour` (0-23),
  `peak_weekday` (0-6), `mean_interval_days`. Hour and weekday
  are only used when the observation actually carries them;
  nothing is guessed. A habit is verified only on real
  repetition (same hour or day at least twice, or a stable
  interval over at least two gaps); one task forms no habit,
  and scattered tasks form no pattern. Contradiction follows
  the schedule pattern: 3 consecutive observations at a
  different hour from a verified peak hour promote a new
  version.

Both can be predicted (`predict` for the new actions
`contact_reply` and `habit_pattern`, answering "unknown" with
no observation), scored through `update` (a miss becomes a
misprediction to the learning loop), and covered by the
`decay_stale_beliefs` sweep. Persistence (`puspa/world/store.py`
+ `puspa/world/__init__.py`): `contact_beliefs` and
`habit_beliefs` join the snapshot state and JSON round-trip.
No SQL schema change was needed: `world_beliefs.state` is
already one JSONB column, so the new keys are stored without a
new column or table. Old state without the new keys still
restores (default `{}`). Commit
cd825812bbbd47b24d882a9f1cdc24de15878d08. Tests:
`tests/test_world_contact_habit.py` (15 new) plus 1 assertion
updated in `tests/test_world_beliefs.py` (decay now also covers
habit). Full suite SOLO after the fix: 933 passed, 5 skipped,
1 warning.

Honest limits: habit hour and weekday depend on data the caller
sends. The model uses a day counter as its internal clock;
`hour` and `weekday` only exist when a task result carries
them, so without that data a habit can only learn from the
interval between tasks, not from hour or day patterns. No
wall-clock source was wired in this iteration. Contacts only
learn from task results that name a `contact_id`; no wiring
from a real messaging channel (WhatsApp or Telegram) to
`ingest_task_result` exists yet, that belongs to the runtime and
channel layer, not to this model. Wide-world facts stay out of
scope, as ordered. Preferred channel is raw frequency, with no
account of context (an urgent channel vs an ordinary one); it
mirrors the data, it does not judge it.

All three wiring gaps are now closed. The remaining honest
limits from the wiring phase are: `world_prediction` and
`world_misprediction` events do not reach the production ledger
(closed event-type set; the ledger copy lives in the handoff
event content); the curriculum daily cap counts per runner
instance; CLI tests cannot be collected with pytest on this
host; old Indonesian docstrings and em dashes in pre-existing
files were not touched; a real end-to-end sandbox run still
needs a host with working Docker.
