# Scheduled routines: tasks that run on their own

A routine is a task you write once, plus a schedule that says
when it repeats. Puspa then runs it on its own, with no one
there to press a button. For example: "every weekday at 07:00,
summarise my unread bills" or "every 2 hours, check the price
of the laptop I want".

## How it works

There are three pieces, and each one is small.

1. **A routine is a trigger.** A trigger is a row in the
   database that says "run this at that time". Creating a
   routine stores your task text and your schedule in a trigger
   row. No new table was added; routines reuse the trigger table
   that already holds bill reminders and price-drop alerts.
2. **The tick fires due triggers.** The tick is one pass of the
   scheduler: it looks for triggers whose time has come, claims
   each one once (so a trigger never runs twice), and now also
   runs the job behind it. Before this change the tick only
   wrote "this trigger fired" and never ran anything; system
   jobs like the nightly backup and the memory consolidation
   existed but nothing ever called them. Now the tick calls
   them, through `run_trigger_job`.
3. **The serve loop calls the tick on a timer.** A tiny loop
   calls the tick once a minute (or as often as you set it).
   Run it with `python -m puspa.scheduler.serve`. It runs until
   you stop it. A tick that fails is reported and the loop
   carries on with the next minute; one bad job never kills the
   loop or the other jobs.

## What runs when a routine fires

A routine runs through the turn loop, the same loop a chat turn
uses. The turn loop is the piece that talks to the language
model (the AI that reads and writes text), runs tools, and
applies the safety checks: the act-or-ask rule (act only when
confident enough, otherwise ask), the policy gateway (a
fail-closed set of rules that blocks unsafe actions), and the
conflict gate (refuses to act on contradictory instructions
until the user chooses). A scheduled turn has no user in the
room, so when the turn would need to ask a question, it cannot;
that turn ends as failed and the failure is recorded, not
hidden.

The routine's task text becomes the turn's first message, as if
the user had typed it. The turn's reply is the routine's result.
The result rides on the tick's returned run (under `"job"`) and
the serve loop reports it through its sink, so a channel or a
daily digest can deliver it to the user.

System jobs are different: they do not need the language model.
The nightly backup, the memory consolidation, the dream pass
(the nightly learning pass over recent activity), the sentinel
scan (the background check for late failures), and the trust-root
and revocation-list refreshes (security lists that are kept
fresh) run directly, with no turn.

## Defining a routine

From Python:

```python
from puspa.scheduler.routines import create_routine

routine = create_routine(
    user_id,
    "Summarise my unread bills.",
    {"type": "daily", "time": "07:00", "timezone": "Asia/Jakarta"},
    dsn=dsn,
)
```

The schedule can also be a short string: `"every 2h"`,
`"30m"`, `"0 9 * * 1-5"` (a cron pattern: minute, hour,
day of month, month, day of week), or an ISO time for a
one-shot. A one-shot runs once and then deactivates itself.
Other helpers: `list_routines(user_id)`,
`pause_routine(routine_id, user_id)`,
`resume_routine(routine_id, user_id)`,
`remove_routine(routine_id, user_id)`.

Schedule shapes (the same shapes the cron scheduler uses):

- `{"type": "once", "at": "<ISO time with offset>"}`
- `{"type": "daily", "time": "HH:MM", "weekdays": [0..6]}`
  (weekday 0 is Monday; an empty list means every day)
- `{"type": "interval", "seconds": <at least 60>}`
- `{"type": "cron", "expression": "<min hour day month weekday>"}`

Every schedule carries a `timezone`, an IANA name such as
`Asia/Jakarta` or `UTC`. A bad timezone or a schedule with no
future fire time is rejected at creation, with a plain reason.

## Missed runs, failures, and safety

- **Missed:** a trigger more than one hour late is run once
  when the scheduler wakes (`run_missed`), and reported as
  missed. A run more than 12 hours late in the cron scheduler
  is counted and skipped instead; the trigger path has no
  12-hour cutoff, it runs once and reports the lateness.
- **One failure does not stop the rest:** each job runs in its
  own guarded step. A failing job is recorded with its error
  text, and the tick moves on to the next trigger.
- **One trigger, one run:** the idempotency key (a unique key
  built from the trigger id and its fire time) means a trigger
  never runs twice for the same fire time, even if two ticks
  overlap.
- **Kill switch:** when the kill switch is active, the whole
  tick is a no-op: no heartbeat, no runs, nothing.
- **Interruption budget:** proactive pings (a bill about to
  fall due, a price drop) still pay from the daily interruption
  budget first; a denied trigger stays due and is tried again
  later. Routines the user defined never pay: the user asked
  for them.
- **Next fire time:** after a routine fires, its next time is
  recomputed from its own schedule, so an hourly routine fires
  hourly and a weekday routine skips the weekend. (The old
  trigger step always added one day, which was wrong for
  anything but a daily routine.)

## What this does not do yet

- **No delivery target on routines.** A routine's result is
  reported through the sink; nothing in this change sends it to
  Telegram, WhatsApp, or email by itself. The file-backed cron
  jobs (`cronjob_manage`) already support a delivery target;
  wiring a routine result to a channel is follow-up work.
- **No built-in service file.** The serve loop is a plain
  process; the host must start it (a terminal, a process
  manager, or the hosted runtime) and keep it running. If it is
  not running, nothing fires, and missed triggers are only
  caught up to one hour late on wake.
- **No chat command yet.** Routines are defined through the
  Python helpers above, not yet through a chat message or a
  model tool. The helpers are the seam a tool would call.
- **No retry of a failed routine turn.** A routine whose turn
  fails waits for its next scheduled fire time; it is not
  retried a minute later. (The cron scheduler requeues a failed
  run once; routines do not, yet.)
