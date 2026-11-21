# Meeting notes and follow-ups

Date: 19 Nov 2026. Source: `docs/tickets-beta-plan.md` section meeting notes and follow-ups,
plan section 8.5. From a transcript or notes, the agent writes
minutes, extracts action items with owner and deadline, and reminds
owners.

## What it is

A room member pastes a meeting transcript (or notes) and PuspaBot
returns minutes: decisions and action items with owners and
deadlines. Each action item lands on the shared room task board
(group chat); each decision lands in room memory and in the ledger; owners
get reminders through the scheduler and routines scheduler.

## Modules

All new code lives in `puspa/meetings/`; the schema is migration `0011_meetings.sql` (applied by
`puspa.meetings.create_schema`, like `puspa/rooms`).

| File | What it owns |
| --- | --- |
| `parse.py` | Deterministic extraction and rendering (pure functions, no DB, no model call), plus the opt-in LLM polish seam. |
| `store.py` | `MeetingMinutesStore`: write minutes end to end (board tasks + room decisions + ledger events), read them back. |
| `reminders.py` | Reminder queries (due / overdue meeting action items, per-owner digest, flat scheduler sweep) and the scheduler and routines cron registration. |
| `schema.py` | Schema loader / reset for `meeting_minutes`. |

No file in `puspa/rooms/`, `puspa/scheduler/`, or `puspa/ledger/`
was changed: meeting notes and follow-ups reuses them through their public surface, so the
parallel delegated tasks build (same board, same scheduler) does not collide
with this one.

## Extraction: deterministic, markers only

The parser recognises explicit markers, in the same style as the
group chat summariser (`Decision:` / `TODO:` / `- [ ]`):

- `Title:` — meeting title (default `Meeting`).
- `Attendees:` / `Present:` — comma-separated names.
- `Decision:` — a decision.
- `Action:` / `TODO:` / `- [ ]` — an action item.
- `Note:` — a note carried into the minutes.
- `@Name` inside an action item — the owner, by display name.
- `by 2026-11-25` / `due 2026-11-25` / `by Friday` /
  `tomorrow` — the deadline, resolved against the meeting date.
  Weekday names work in English and Indonesian (`Friday` /
  `Jumat`); a weekday means the next day of that name strictly
  after the meeting date, `next Friday` the one after that. A
  due date means due by the end of that day (23:59 UTC).

Lines without a marker are ignored. The parser never invents a
decision, an action item, an owner, or a deadline. An owner name
that cannot be resolved to a room member stays an unresolved
name: the board task is created unassigned rather than guessing
a user id. Owner names resolve only through a caller-supplied
display-name map (`members_by_name`): the database knows user
ids, the chat surface knows display names, and this module does
not guess the mapping.

## The LLM seam (opt-in polish only)

`polish_minutes(minutes, llm_complete=...)` takes a
`complete(prompt)` callable (the seam from
`puspa/router/complete.py`). It asks the model to rewrite the
deterministic minutes in smoother prose, then checks that every
extracted decision and action-item title still appears verbatim
in the polished text. When any of them is missing, the model
dropped or rewrote a fact, and the deterministic rendering is
returned instead. Without a callable, the deterministic rendering
is returned unchanged. The beta ships the deterministic minutes;
polish is opt-in. There is no LLM extraction path: extraction
stays deterministic.

## What `write_minutes` does, in order

1. Parses the transcript (`parse_transcript`).
2. Creates one board task per action item (`RoomTaskStore`),
   with the resolved owner (or none) and the deadline (or none).
3. Inserts the `meeting_minutes` row: rendered minutes text plus
   the structured extraction (decisions list, action items with
   their board task ids) as JSONB. The transcript itself is never
   stored.
4. Records each decision as room memory of kind `decision`
   (`RoomMemoryStore.record_decision`).
5. When a ledger is given (with `agent_id`), appends ledger
   events — see "Outcome records (verified outcome records stand-in)" below.

Only a room member may write or read minutes. The steps run in
separate connections (each store owns its own), so a failure
after step 2 leaves a partial write: the minutes row is the
anchor, and a retry re-parses the same transcript. A single
cross-store transaction is a follow-up, not part of this ticket.

## Outcome records (verified outcome records — adopted)

verified outcome records built the general outcome-record path
(`puspa.evidence.outcomes`, see `docs/outcome-records.md`)
and adopted these events: each decision is now a
`decision_recorded` outcome record written through the shared
writer, as an `evidence_recorded` event with content
`{"kind": "outcome_record", "outcome_type":
"decision_recorded", "user_id", "room_id", "subject_id":
<meeting id>, "summary": <decision>, "evidence":
[{"kind": "meeting_minutes", …}], "details":
{"kind": "meeting_decision", "meeting_id", "room_id",
"decision"}}` — the pre-verified outcome records payload is preserved verbatim
under `details`. The action item stays a plain `task_created`
event, content `{"kind": "meeting_action_item", "meeting_id",
"room_id", "room_task_id", "title", "owner_user_id",
"due_at"}`: creating a task is not an outcome.

The ledger `task_id` for these events is the meeting id, so the
events for one meeting form their own verifiable chain. The
records are queryable per user and per group through the verified outcome records
query API.

## Reminders

Reminder queries reuse the group chat board's `due_soon` / `overdue`
and filter to tasks that came from meeting minutes (their ids
are stored in the minutes rows, so the board schema is
unchanged):

- `due_meeting_actions(room, within=1 day)` — due within the
  window (group chat `due_soon` has no lower bound, so overdue items
  are included here too).
- `overdue_meeting_actions(room)` — past deadline.
- `reminders_for_owner(room, owner)` — one owner's items,
  overdue first, then due soon.
- `build_reminder_digest(room)` — per-owner digest: each owner
  maps to their reminder texts, most urgent first. Tasks without
  an owner are left out — there is nobody to remind.
- `run_reminder_sweep(room)` — the flat hook a scheduled job
  calls: one entry per (owner, task) with `owner_user_id`,
  `task_id`, `title`, `due_at`, `status`, `text`.

Nothing in this module sends a reminder: sending is an
approval-gated delivery, and the queries only answer who needs
one. The scheduler hook reuses the scheduler and routines general cron scheduler —
no second scheduler is built:

- `register_reminder_job(store, room_id, deliver_to=...)`
  creates a scheduler and routines cron job (`CronJobStore` + `cronjob_manage`,
  default every 12 hours, Asia/Jakarta) whose prompt tells a
  fresh agent to call `run_reminder_sweep` for the room and
  deliver each reminder to its owner, inventing nothing. The
  delivery target is the caller's choice (a room chat target or
  a per-owner target).

## Honest limits

- Extraction is marker-based: a transcript without `Decision:` /
  `Action:` markers yields empty minutes. Free-form "we agreed
  to..." talk is ignored by design, not understood.
- Owner resolution needs the caller-supplied display-name map;
  without it every action item is unassigned.
- No live chat delivery is verified anywhere in this ticket:
  the reminder job's delivery target is the caller's, and no
  Telegram / WhatsApp send is exercised (same limit as group chat).
- verified outcome records's general outcome-record path exists now (see above):
  decisions are outcome records; action items stay plain
  `task_created` events.
- `write_minutes` is not one cross-store transaction (see above).
