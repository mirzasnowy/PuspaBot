---
name: "Onboarding"
description: "Guides a new user through their first minutes with Puspa: name and style, notifications, and one first task — and measures whether it finished in the target time."
author: "Puspa Team"
---

# Onboarding

This skill runs the first-run flow for a new user: three short
steps, then one first task that proves the agent works. The flow
is measured — if it takes too long, that is a finding, not a
detail to hide.

## When to Use This Skill

Use this skill when:

- A new user starts with Puspa for the first time
- The user asks to redo setup, or asks what the setup steps are
- The user asks how long setup should take

## The Three Steps

```python
from puspa.onboarding.onboarding import (
    start_onboarding,
    get_status,
    complete_step1,
    complete_step2,
    complete_step3,
    run_first_task,
    measure_onboarding_seconds,
    within_target,
)

status = start_onboarding(user_id)
```

1. **Step 1 — who you are.** `complete_step1(user_id, name,
   style, example_message=...)` records the user's name, their
   preferred reply style, and optionally one example message in
   their voice (the style feeds the `persona` skill).
2. **Step 2 — how to reach you.** `complete_step2(user_id,
   notifier=...)` records how the agent may notify the user.
   With no notifier given, a stub notifier is used — reminders
   are recorded, not delivered (see the limits).
3. **Step 3 — connect something, or later.**
   `complete_step3(user_id, connector="nanti")` records which
   connector the user connected, or `"nanti"` (later) if they
   skipped it. Skipping is allowed and recorded — never block
   the flow on a connection.

`get_status(user_id)` returns where the user is in the flow at
any time, so a returning user resumes instead of restarting.

## The First Task

After the three steps, the user runs one first task — the proof
the agent works:

```python
result = run_first_task(user_id, event_type, target)
```

The first task is a real task on a real target (for example, the
first bill check or the first note), not a demo button. If it
fails, the onboarding is not complete — say what failed and fix
it instead of marking the step done.

## Timing

```python
seconds = measure_onboarding_seconds(status)
ok = within_target(status)
```

The flow is timed from start to finish. `within_target` reports
whether it finished inside the target time. If it did not, say
how long it took and which step was slow — the timing is part of
the report, not a number to hide.

## Limits — Read These First

- **The notifier may be a stub.** Until the user connects a real
  notifier, step 2 records a stub: notifications are recorded
  but not delivered. Never tell a new user "I will notify you"
  when only the stub is in place.
- **Skipping is a valid outcome.** A user who picks `"nanti"`
  in step 3 has finished onboarding. Do not nag them into
  connecting, and do not mark the flow incomplete.
- **One flow per user.** `start_onboarding` for a user who
  already finished returns their existing status — do not wipe
  their progress by restarting.
- **The first task must be real.** A faked first task teaches
  the user the agent pretends. Run the real thing, on the real
  target, and report the real result.

## Example

New user: "Baru pertama kali pakai Puspa."

1. `start_onboarding`, then ask for their name and style
   (step 1).
2. Ask how they want to be notified (step 2); record the stub
   honestly if they skip it.
3. Ask whether to connect anything now or later (step 3).
4. Run one first task together (for example, their first morning
   summary), report how long the whole flow took, and whether it
   was inside the target.
