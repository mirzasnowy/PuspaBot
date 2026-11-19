# Data Consistency: Postgres Ledger vs SQLite Sessions

One page. The rule, the mechanism, and the proof (E-06, plan §5.1).

## The rule

- **Postgres is the authority for task status.** Status (`running`,
  `interrupted`, `completed`, `failed`, `budget_exceeded`) lives
  only in the ledger (`puspa/ledger/`, table `events`). It is
  derived from the events — the last terminal event wins — never
  stored in a second place that could disagree.
- **SQLite `state.db` holds session transcripts and the search
  index only.** It is rebuildable from the ledger (see below). When
  the two disagree, Postgres wins.
- **The `tasks` table in Postgres is a projection**, rebuildable
  from the ledger, not a second authority.

## Write-ahead: intent before effect, result after

Before a tool with a side effect runs, the runner appends a
`tool_called` event with `kind: "step_intent"` and an idempotency
key (`task_id + step number + hash of the arguments`). After the
effect runs, it appends a `tool_completed` event with
`kind: "step_result"` and the same key. Both are ordinary ledger
events, so the hash chain covers them and no new event type was
needed. `TaskRunner` emits the same pair through its sink; the
reference implementation is `ConsistentStepRunner`
(`puspa/consistency/runner.py`), with one heartbeat per step.

## Recovery: uncertain means ask, never guess

A step with an intent but no result is **uncertain**: the effect
may or may not have happened. On recovery (`recover_task`):

- uncertain **read-only** steps are retried once (a read has
  nothing to duplicate) and the result is recorded under the same
  key;
- uncertain **outbound** steps (send, share, post, pay, delete)
  are **never auto-retried** — they are reported in
  `needs_user_decision` for a human to resolve;
- the task is `completed` only when every step has a recorded
  result, otherwise it is marked `interrupted` (append-only
  `task_stopped` with `{"interrupted": true}`).

## Startup reconciliation

`reconcile_startup(ledger, store)` runs before new work is
accepted: it scans for tasks still `running` whose last heartbeat
is older than the threshold (default 300 s), marks each
`interrupted`, then runs the routine session housekeeping pass
(integrity check + FTS5 rebuild/verify). Call site: process
startup, before the first task is accepted.

## Rebuilding sessions

`rebuild_sessions_from_ledger(ledger, store)` rebuilds missing
session transcripts from the ledger's `user_message` /
`agent_message` events. Rebuilt sessions are marked
`recovered_from_ledger` in their metadata and keep the ledger
task id, so a second rebuild is a no-op. The ledger does not hold
every turn's full text in every channel, so a rebuilt transcript
is a partial record, marked as such — never presented as the
original. The FTS5 index is derived and rebuilt from `messages`
afterwards.

## Proof: kill -9 fault injection

`run_kill_drill` (`puspa/consistency/kill_drill.py`) spawns the
drill worker, which kills itself with SIGKILL at one boundary per
case — before intent, between intent and effect, between effect
and result, after result, during checkpoint — then recovers and
checks: no duplicate effect, no lost task, hash chain valid,
uncertain outbound steps never auto-retried. This is the gate-12
evidence ("kill -9 without duplicate actions").

## Honest limit

The kill -9 here is fault injection on a worker process and
harness that run on this VM. Verifying the same invariants
against a live container (kill -9 on the container itself)
waits for a Docker host.
