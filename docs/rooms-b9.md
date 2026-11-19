# B9 — Delegated tasks with approval

Date: 19 Nov 2026. Source: `docs/tickets-beta-plan.md` section B9,
plan section 8.4. A user hands a task to the agent, or to a
teammate through the agent; the agent works, reports, and waits
for approval on irreversible steps. Shared task board per group.

## What it is

B9 extends the B6 shared task board — it never builds a second
one. The B6 board already gives every task an owner, a deadline,
and a status (`open` / `done`); B9 adds the delegation on top of
it: who works on the task (an agent subagent, or a teammate),
the delegation status, progress reports, and approval-gated
steps for the irreversible parts of the work.

## Modules

All new code lives in `puspa/rooms/delegation.py` and
`puspa/pwa/rooms_api.py`; the schema additions are in
`infra/schema_rooms.sql` (applied by `puspa.rooms.create_schema`,
like the rest of the room schema).

| File | What it owns |
| --- | --- |
| `puspa/rooms/delegation.py` | `DelegationService`: delegate to agent/teammate, start/report/block/resume, propose/approve/reject/execute steps, board reads, reminder queries, board rendering. |
| `puspa/pwa/rooms_api.py` | PWA HTTP layer for the board (`GET /api/rooms`, `GET /api/rooms/{id}/tasks`, `POST .../tasks`, `POST .../steps`, `POST .../steps/{id}/approve\|reject`), mounted in `puspa/pwa/app.py`. |
| `puspa/rooms/routing.py` | Board command in the group chat: `@puspa board` / `@puspa tasks` / `@puspa tugas` is answered with the rendered board; the agent is never called for it. |
| `apps/pwa/src/screens/TasksScreen.tsx` | "Shared task boards" section: the user's rooms and each room's board, fetched through `fetchRooms` / `fetchRoomBoard` in `apps/pwa/src/api/client.ts`. |
| `infra/schema_rooms.sql` | `delegated_tasks` (one row per delegated task) and `delegated_task_steps` (one row per proposed step), with RLS policies in the same shape as the other room tables. |

## Delegation

- **To the agent** (`delegate_to_agent`): the assignee is a G25
  subagent handle or id. When a `SubagentLifecycle` is passed,
  the handle must resolve there — a forged handle is refused
  before anything is recorded. The room task itself stays
  `open`; the delegation row carries the status.
- **To a teammate** (`delegate_to_teammate`): the teammate
  becomes the task owner (the B6 board shows the new owner) and
  the delegation records the handover. The teammate must be an
  owner or member of the room — a guest is never handed work,
  and a guest may never delegate work to anyone.
- **Work cycle**: `delegated` -> `in_progress` (`start_work`)
  -> `done` (`complete_task`, which also marks the B6 task
  done). `block_task` records why the worker cannot proceed;
  `resume_task` goes back to work. `report_progress` stores a
  note shown on the board, so the group sees progress without
  asking. Reporting never changes the status (a blocked task
  stays blocked until resumed). Only the assignee, the task
  creator, or a room owner may work on or report on a task.
- **Completing** is refused while any step is still proposed or
  awaiting approval — the work is not done until its steps are
  decided and executed.

## Approval-gated steps: the G4/G5 path, never a second one

The agent's work is proposed as steps (`propose_step`). A
reversible step is `proposed` and runs without approval. An
irreversible step goes through the existing approval path:

1. `ApprovalPolicy.decide` (G4) classifies it from the tool
   name. A credential change is `rejected` at once (never
   approvable, even with an "always" answer). A usable tiered
   answer (`allow`) approves it at once; anything else asks.
2. When a gate + token are supplied, the request is filed with
   `ApprovalPolicy.request`, so the G5 `Gate` binds the approval
   to the step's content hash and enforces its 24-hour token
   expiry. A filing that fails (bad token, token does not allow
   the kind) leaves the step `awaiting_approval` — fail-closed,
   never approved on a decision alone.
3. `approve_step` / `reject_step` are decided by the task owner
   (or the creator when the task has no owner): the person who
   delegated the work approves its irreversible steps. When the
   step has a gate request, the gate approves it too.
4. `execute_step` re-reads the step row and refuses anything
   that is not `approved` (irreversible) or `proposed`
   (reversible). An approved step filed with the gate runs
   through `Gate.execute`, so a changed content hash or an
   expired token voids the approval at execution time. An
   executed step returns its stored result instead of running
   twice; a step filed with the gate refuses to run without the
   gate.

With no policy wired in, an irreversible step is recorded as
`awaiting_approval` with no approval request: it can be approved
directly by the owner, but it can never run unapproved. The
delegation status follows the steps: it becomes
`awaiting_approval` while any step waits, and returns to
`in_progress` (or `delegated`) when none does.

## The board: PWA and group chat

`DelegationService.board` returns every room task with its
delegation and its pending-step count (members only).
`render_board` is the pure text rendering for the group chat:
one line per task with status, title, owner, deadline with its
urgency (`due soon` / `overdue` from the G8
`classify_due`), delegation, last report, blocked reason, and
waiting steps. A board with no tasks says so — it never invents
work. In the group chat, a message asking for the board
(`@puspa board` / `@puspa tasks` / `@puspa tugas`, English and
everyday Indonesian forms) is answered with the rendered board
and the agent is never called — the board is data, not a turn.
Anything else still goes to the agent.

## Reminders

`delegation_reminders` answers which delegated tasks need a
nudge (the B6 `due_soon` / `overdue` queries, filtered to
delegated tasks); each task's urgency is classified with the G8
scheduler's `classify_due`, and `reminder_lines` writes one
reminder line per task. This module only answers which tasks
need a reminder — the caller (a scheduler, G8) sends it.

## HTTP layer

No approval policy or gate is wired into the HTTP layer in this
iteration: steps proposed through the API go through the service
with no policy, so irreversible steps stay `awaiting_approval`
until the owner decides them through the service with a policy
wired in (or a later ticket wires the policy into the app).
That is the safe default — the API never approves its own steps.
The routes are documented in `docs/openapi-rooms.yaml` (split out of `docs/openapi-pwa.yaml` in Wave 30; the main spec keeps $ref stubs)
(`RoomSummary`, `RoomBoard`, `RoomBoardTask`, `DelegatedStep`,
`DelegatedStepDecision`); the generated TypeScript types were
regenerated from it (`npm run generate`).

## Honest limits

- **The agent worker itself is not executed here.**
  `delegate_to_agent` records the delegation and (when a
  lifecycle is passed) verifies the handle; running the child
  is the caller's job, through `SubagentLifecycle` (G25).
- **No live group-chat delivery is verified anywhere in this
  repo** (same as B6): the board reply is tested with a fake
  adapter and a fake agent. WhatsApp groups stay a postponed
  surface (plan section 12.4).
- **The PWA board is read-only plus create/propose/decide.**
  Starting work, reporting progress, blocking, and executing
  steps are service-level operations, not HTTP endpoints in
  this iteration.
- **The decider (G9) is not consulted for delegation
  decisions.** The G9 overlap is a routing question for a later
  ticket (which worker should take a task); B9 records the
  delegation the user or the agent already chose.

## Reuse

Mechanisms taken, rewritten as Puspa code, never copied verbatim:

- `puspa/rooms/tasks.py` (B6) — the shared task board itself:
  owner, deadline, status, `due_soon` / `overdue`.
- `puspa/policy/approvals.py` (G4) + `puspa/policy/gate.py`
  (G5) — the only approval path; no parallel system exists.
- `puspa/subagents/lifecycle.py` (G25) — handle resolution
  for the agent assignee (verified through the lifecycle seam,
  proved in tests with a stub that refuses unknown handles).
- `puspa/scheduler/due.py` (G8) — `classify_due` for reminder
  urgency.
- `puspa/pwa/insights_api.py` (G28) — the `register_routes`
  pattern for the PWA API module.

No outside repository was studied for B9 beyond the mechanisms
already recorded in `REUSE.md` for G4/G5/G8/G25/B6.

## Tests

- `tests/test_rooms_delegation_b9.py` — delegation to agent
  and teammate, guest refusals, handle verification, the work
  cycle, reversible steps, irreversible steps with and without
  a policy/gate, credential-change rejection, tiered-answer
  approval, completion blocked by pending steps, board
  rendering and commands, reminders, and the group-chat board
  command (agent not called).
- `tests/test_pwa_rooms_api.py` — the HTTP layer on the real
  `create_app`: rooms list, empty board, 403 for a non-member,
  401 anonymous, 404 unknown room, create with teammate/agent
  delegation, propose + approve/reject a step (non-owner 403).
- `apps/pwa/src/api/client.test.ts` — `fetchRooms` /
  `fetchRoomBoard` helpers, including the 403 case.
