# Task progress rail beside Chat (plan section 12.13)

## What it is

One rail beside the chat thread (`/chat` in the PWA): the
tasks that are running right now, one row per working agent —
the task id it last started, how many of its subagents are
still working, and the cost so far. When a task finishes, its
agent goes idle and the row disappears.

## Data source: the existing SSE stream, nothing new

The rail reads the office projection the Chat screen already
receives over its existing `/sse` EventSource — the same
`office` frame the WorkStrip and the Office screen render.
No new endpoint, no new polling, no new event type, no
migration. The projection is rebuilt from ledger events on
every frame (`task_created` marks the agent working,
`task_completed` / `task_failed` / `task_stopped` marks it
idle), so a row appears when a task starts and disappears
when it ends. That is what "live" means here: the rail
follows the same SSE frames the other screens follow.

The derivation lives in `apps/pwa/src/state/office.ts`
(`runningTaskProgress`); the component is
`apps/pwa/src/components/TaskProgressRail.tsx`, rendered by
`ChatScreen.tsx` next to the message thread.

## What it is not (honest limits)

- **No purpose or stage line.** The office projection
  carries only agent id, status, last task id, cost, and
  subagent status. A task's purpose and stage live only in
  the Tasks screen's session state (created in that screen by
  the create-task call) and are not on the wire, so the rail
  shows the task id, never an invented purpose. Showing
  purpose would need a new task-list endpoint or a richer
  projection — a follow-up, not this small item.
- **No completion reason.** The projection collapses
  completed, failed, and stopped into one `idle` status, so
  the rail cannot say how a task ended. It only shows what
  is running now; the Tasks screen and the replay timeline
  are where an ended task is inspected.
- **"Live" is SSE-frame live, not a push socket.** The
  server's `/sse` endpoint yields one office frame, the room
  activity backlog, and one heartbeat, then ends; the
  browser's `EventSource` reconnects for the next frame.
  The rail updates when a frame arrives — the same cadence
  as the WorkStrip, not a per-second push.
- **No live-browser verification.** There is no browser on
  this VM to prove the rail moves on a real screen. What
  is verified: vitest component tests (rows render from both
  wire shapes, empty and waiting states) and state tests
  (derivation from working agents only), plus `tsc` and the
  PWA test suite. The rail moving on a real screen is still
  to be eyeball-checked in a browser.
- **Lite template unchanged.** The no-JS lite chat template
  keeps its existing 5-second `hx-get` work strip from
  `/office`; the rail is an SPA component only.

## The wire-shape fix this item had to make

While building the rail, the existing live strip turned out
to be blind to real server payloads: the server sends the
office projection as `{"agents": [...]}` (a list —
`puspa.evidence.projections.office`, asserted by
`tests/test_evidence.py`), while the OpenAPI spec described
only the keyed `agent` map, and the frontend read only the
map. With a real payload, `workingAgents` always returned
empty — the WorkStrip could never show a working agent.

task progress rail beside chat fixed it at the contract + state layers, without
touching the server or the Jinja templates (which also read
`office.agents`):

1. `docs/openapi-pwa.yaml`: `OfficeProjection` now also
   documents the `agents` list property (+5 lines; ratchet
   baseline 1889 -> 1894, still under the 2000-line cap).
   Types regenerated with `npm run generate` (clean diff,
   only the new property).
2. `apps/pwa/src/state/office.ts`: `officeAgents` accepts
   both shapes and merges by agent id (map wins on a repeat),
   so the Office screen, the WorkStrip, and the rail all read
   the same live data. `workingAgents` in `state/chat.ts`
   now delegates to it.

Unifying the server to emit only one shape is a follow-up
for the coordinator; both shapes are valid on the wire
until then.
