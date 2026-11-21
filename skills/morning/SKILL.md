---
name: "Morning"
description: "Builds the user's morning summary from schedule, bills due, open tasks, Indonesian holidays, and prayer times. Every section reports honestly when its source is not connected."
author: "Puspa Team"
---

# Morning

This skill builds the morning summary: one short briefing made of
five sections. Each section is either data or an honest "not
available, here is why" — a missing source is never papered over
with invented content.

## When to Use This Skill

Use this skill when the user:

- Asks "what's on today", "give me my morning summary", or
  "what do I have this morning"
- Wants to know today's holidays, prayer times, or bills due
- Asks to save or read a morning note
- Asks what tasks are still open

## The Five Sections

All assembly is done by `puspa.morning.summary.build_morning_summary`:

```python
from puspa.morning.summary import build_morning_summary

summary = build_morning_summary(
    day=today,
    events=events,                # today's calendar events, or None
    bill_candidates=bill_candidates,
    open_tasks=open_tasks,
    holidays=holidays,
    prayer=prayer,
    source_status=source_status,  # per-source: ok / not_connected / unavailable
)
```

The function is pure: it assembles already-fetched data, so it
needs no network or database. Each section is data or `None`,
with the reason in `source_status`.

1. **Schedule** — today's events from Google Calendar, via the
   Google connector (`puspa.connectors.google`). Calendar is not
   connected yet (Gmail and Drive are), so until it is, this
   section reports `not_connected`. Never invent events.
2. **Bills due** — bill *candidates* from Gmail, found with
   `find_bill_candidates(messages)`. They are candidates, not
   confirmed bills: the keyword match and any due date in the
   subject or snippet are labelled `verified: false`, and the
   summary tells the user to open the email to confirm the amount
   and due date. No amount is ever parsed out of an email body
   and presented as a fact.
3. **Tasks** — the user's open rows from `open_tasks_for_user(user_id)`
   (the task store): anything not completed or failed.
4. **Holidays** — Indonesian public holidays from Nager.Date
   (`puspa.morning.holidays.fetch_public_holidays`,
   `holidays_on_date`, `upcoming_holidays`).
5. **Prayer times** — Aladhan, method 20 (Kemenag RI)
   (`puspa.morning.prayer.fetch_prayer_times`). Imsak is shown
   only during Ramadan; outside Ramadan the section says so
   plainly.

## Morning Notes

Short notes the user dictates in the morning, with actions pulled
out of them (`puspa.morning.notes`):

```python
from puspa.morning.notes import (
    save_note,
    list_notes,
    extract_actions,
    summarise_note_text,
)

note = save_note(user_id, text)
actions = extract_actions(text)  # owner + due hint per action line
```

`extract_actions` finds owners and due hints in plain lines
("TODO: ...", "- [ ] ..."); `summarise_note_text` shortens a long
note without dropping its actions.

## Limits — Read These First

- **No calendar, no schedule.** Until Google Calendar is
  connected, the schedule section is empty with reason
  `not_connected`. Say that; do not fill it from memory or guess.
- **Bill candidates are unverified.** A Gmail match is a hint,
  not a bill. Never state its amount or due date as fact; always
  tell the user to open the email to confirm. For a checked bill,
  use the `bills` skill instead.
- **Prayer times come from Aladhan.** If the fetch fails, the
  section reports `unavailable` with the reason — never fall back
  to a hardcoded timetable.
- **Holidays come from Nager.Date.** Same rule: a failed fetch is
  reported, not replaced with a remembered list.

## Example

User: "Ringkasan pagi ini dong."

1. Fetch the five sources (events, Gmail candidates, open tasks,
   holidays, prayer times), noting which fetch failed.
2. Call `build_morning_summary` with what was fetched.
3. Present each section, and for every empty section state the
   reason from `source_status` in one plain sentence.
