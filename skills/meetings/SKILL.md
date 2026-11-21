---
name: "Meetings"
description: "Turns a meeting transcript or notes into minutes with decisions and action items, stores them per room, and reminds owners about deadlines."
author: "Puspa Team"
---

# Meetings

This skill turns a meeting transcript or notes into minutes,
extracts action items with owner and deadline, stores the minutes
for the room, and reminds owners when deadlines approach.

## When to Use This Skill

Use this skill when the user:

- Pastes a meeting transcript or notes and asks for minutes
- Asks what was decided in a meeting, or who owes what by when
- Asks to remind someone about a meeting action item
- Wants the meeting notes for a room listed or read back

## How Meetings Work

### Step 1: Parse the Transcript

`puspa.meetings.parse` does deterministic extraction — no model
guessing:

```python
from puspa.meetings.parse import parse_transcript, render_minutes

minutes = parse_transcript(text, ref=meeting_date)
text_out = render_minutes(minutes)
```

Markers in the text drive the parse: `Decision:` lines become
decisions, `TODO:` / `- [ ]` lines become action items with an
owner and an optional due date. `polish_minutes(minutes,
llm_complete=...)` may smooth the prose with a model, but it must
never add or drop a fact — the polished text is only a rewrite of
the parsed facts.

### Step 2: Store the Minutes

```python
from puspa.meetings.store import write_minutes, list_minutes, get_minutes

record = write_minutes(room_id=room_id, minutes=minutes, user_id=user_id)
```

Writing minutes also writes to the room: action items become
board tasks and decisions become room-memory rows of kind
`decision`, plus ledger events (`evidence_recorded`,
`task_created`). Reading is membership-checked: only a room
member may read a room's minutes (`list_minutes`,
`get_minutes`).

### Step 3: Remind Owners

`puspa.meetings.reminders` provides the reminder queries and the
scheduler hook:

```python
from puspa.meetings.reminders import (
    due_meeting_actions,
    overdue_meeting_actions,
    reminders_for_owner,
    build_reminder_digest,
    run_reminder_sweep,
    register_reminder_job,
)
```

- `due_meeting_actions` / `overdue_meeting_actions` — action
  items due soon or past due, per room.
- `reminders_for_owner` / `build_reminder_digest` — one owner's
  reminders as text.
- `run_reminder_sweep` — the sweep the scheduler runs.
- `register_reminder_job` — creates the cron job that runs the
  sweep (it reuses the shared scheduler; there is no second
  scheduler).

## Limits — Read These First

- **Reminders are queries only.** The functions above find what
  is due and build the text; sending the reminder is the caller's
  job. Never claim a reminder was sent when only the digest was
  built.
- **Only room members.** Minutes, tasks, and reminders are all
  scoped to the room and checked against membership. A
  non-member gets nothing — do not leak another room's minutes.
- **Polish never changes facts.** If the user asks for polished
  minutes, the model may reword, never add a decision, drop an
  action item, or invent a deadline.
- **Due dates come from the text.** A due date is only real if
  the transcript states it (or a `TODO` line carries one). Do not
  infer a deadline from context.

## Example

User: "Buatin notulen rapat tadi, terus ingetin yang punya tugas."

1. `parse_transcript` on the pasted text, then `render_minutes`
   to show the draft.
2. `write_minutes` for the room once the user confirms it.
3. `register_reminder_job` for the room if no reminder job exists
   yet, so owners get reminded before their deadlines.
