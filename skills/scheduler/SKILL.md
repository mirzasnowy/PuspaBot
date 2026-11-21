---
name: "Scheduler"
description: "Runs things on a schedule: the user's recurring routines (plain-language tasks on a daily, weekly, or monthly time) and the shared cron jobs that power reminders and sweeps."
author: "Puspa Team"
---

# Scheduler

This skill covers scheduled work: the user's recurring routines
and the cron jobs that run background work on time. There is one
scheduler — routines, meeting reminders, and sweeps all run
through it; there is no second scheduler.

## When to Use This Skill

Use this skill when the user:

- Asks for something to happen regularly ("remind me every
  morning", "every Monday, summarise my tasks")
- Asks to list, pause, resume, or delete a recurring task
- Asks when something will run next, or why it did not run

## Recurring Routines

A routine is a plain-language task plus a schedule
(`puspa.scheduler.routines`):

```python
from puspa.scheduler.routines import (
    create_routine,
    list_routines,
    pause_routine,
    resume_routine,
    remove_routine,
)

routine = create_routine(
    user_id,
    "Summarise my unread bills",
    {"type": "daily", "time": "07:00"},
)
routines = list_routines(user_id)
```

- `task` is the plain-language text the agent runs each time.
- `schedule` is a schedule dict — validated by
  `puspa.scheduler.schedules.validate_schedule` (daily / weekly /
  monthly, with a time and a timezone). A schedule the validator
  rejects is refused with the reasons listed; fix the schedule,
  do not silently replace it.
- `next_occurrence(schedule, after)` tells when a routine runs
  next — use it when the user asks "when next", instead of
  guessing.
- `pause_routine` / `resume_routine` stop and restart a routine
  without deleting it; `remove_routine` deletes it.

## Cron Jobs (Background Work)

Lower-level timed work uses cron jobs (`puspa.scheduler.cron`):

- `CronJobStore` holds jobs; `cronjob_manage` creates, lists,
  and removes them.
- `parse_schedule_spec` / `parse_cron_expression` parse a
  schedule or a cron expression; `parse_delay` parses "in 2
  hours" style delays.
- `run_due_jobs` runs what is due; `execute_job` runs one job.
  Deliveries are queued (`queue_delivery`) and flushed
  (`flush_deliveries`) — a job that produced a message does not
  mean the message was delivered.

Routines are the user-facing surface; cron jobs are the machinery
underneath. Meeting reminder jobs (`register_reminder_job` in the
`meetings` skill) and other sweeps are cron jobs on this same
scheduler.

## Limits — Read These First

- **A routine runs its task text, not a workflow.** If the user
  wants a multi-step automation with branches and approvals,
  that is a workflow (see the `workflows` skill), not a routine.
- **Paused is not deleted.** A paused routine still exists and
  still shows in the list. Say "paused" when it is paused; do
  not report it as gone.
- **A missed run is reported, not silently retried.** If a run
  was missed (the machine was asleep, the job errored), the
  scheduler records that. Tell the user it was missed and why
  instead of pretending it ran.
- **Times need a timezone.** A schedule without a timezone is
  ambiguous — confirm the user's timezone before creating a
  routine that must fire at a wall-clock time.

## Example

User: "Tiap pagi jam 7, ingetin tagihan yang belum dibayar."

1. Confirm the timezone, then `create_routine(user_id,
   "Remind me about unpaid bills", {"type": "daily",
   "time": "07:00"})`.
2. Confirm back the next run time from `next_occurrence`.
3. If the user later says "stop it", `pause_routine` (or
   `remove_routine` if they mean gone for good) and say which
   one happened.
