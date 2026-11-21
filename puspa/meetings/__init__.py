"""Meeting notes and follow-ups (B10).

From a transcript or notes, the agent writes minutes, extracts
action items with owner and deadline, and reminds owners.

Public surface:

- :func:`parse_transcript` / :func:`render_minutes` /
  :func:`polish_minutes` — deterministic extraction and
  rendering, plus the opt-in LLM polish seam (the model may
  smooth the prose, never add or drop a fact).
- :class:`MeetingMinutesStore` — write minutes for a room
  (board tasks + room decisions + ledger events), read them back.
- :func:`due_meeting_actions` / :func:`overdue_meeting_actions`
  / :func:`reminders_for_owner` / :func:`build_reminder_digest`
  / :func:`run_reminder_sweep` — the reminder queries and the
  scheduler hook (queries only; sending is the caller's job).
- :func:`register_reminder_job` — create the G8 cron job that
  runs the sweep (reuses the G8 scheduler; no second scheduler).

Reuse (mechanisms taken, rewritten as Puspa code, never copied
verbatim):

- ``puspa/rooms`` (B6) — the shared task board (owner,
  deadline, status) and the deterministic marker style
  (``Decision:`` / ``TODO:`` / ``- [ ]``); meeting action items
  are board tasks, and meeting decisions are room-memory rows of
  kind ``decision``.
- ``puspa/ledger`` — the append-only hash-chained ledger and
  its existing ``evidence_recorded`` / ``task_created`` event
  types carry the decision and action-item outcome records
  (the stand-in B2 path; B2 generalises it, it does not replace
  it).
- ``puspa/scheduler/cron`` (G8) — the general cron scheduler
  (``CronJobStore`` + ``cronjob_manage``) runs the reminder job.
- ``puspa/router/complete.py`` — the ``complete(prompt)`` seam
  the optional polish step takes as ``llm_complete``.

Honest limits (see ``docs/meetings.md``): extraction is
deterministic and marker-based — unmarked talk is ignored, so
a transcript without ``Decision:`` / ``Action:`` markers yields
empty minutes; owner names resolve only through a
caller-supplied display-name map; no live chat delivery is
verified (the reminder job's delivery target is the caller's);
B2's general outcome-record path does not exist yet, so the
ledger events here are the stand-in B2 adopts later.
"""

from __future__ import annotations

from .parse import (
    MAX_ACTION_TITLE_CHARS,
    MAX_MINUTES_TEXT_CHARS,
    MeetingMinutes,
    ParsedActionItem,
    ParsedDecision,
    due_datetime,
    parse_transcript,
    polish_minutes,
    render_minutes,
)
from .reminders import (
    DEFAULT_REMINDER_SCHEDULE,
    DEFAULT_REMINDER_TIMEZONE,
    build_reminder_digest,
    due_meeting_actions,
    overdue_meeting_actions,
    register_reminder_job,
    reminder_job_prompt,
    reminder_text,
    reminders_for_owner,
    run_reminder_sweep,
)
from .schema import MEETINGS_TABLES, create_schema, reset_tables, schema_path
from .store import (
    MeetingMinutesRecord,
    MeetingMinutesStore,
    get_minutes,
    list_minutes,
    write_minutes,
)

__all__ = [
    "DEFAULT_REMINDER_SCHEDULE",
    "DEFAULT_REMINDER_TIMEZONE",
    "MAX_ACTION_TITLE_CHARS",
    "MAX_MINUTES_TEXT_CHARS",
    "MEETINGS_TABLES",
    "MeetingMinutes",
    "MeetingMinutesRecord",
    "MeetingMinutesStore",
    "ParsedActionItem",
    "ParsedDecision",
    "build_reminder_digest",
    "create_schema",
    "due_datetime",
    "due_meeting_actions",
    "get_minutes",
    "list_minutes",
    "overdue_meeting_actions",
    "parse_transcript",
    "polish_minutes",
    "register_reminder_job",
    "reminder_job_prompt",
    "reminder_text",
    "reminders_for_owner",
    "render_minutes",
    "reset_tables",
    "run_reminder_sweep",
    "schema_path",
    "write_minutes",
]
