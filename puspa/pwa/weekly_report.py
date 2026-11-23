"""Weekly report (P-02, plan section 12.7).

One page per week, from the audit log: tasks completed, tasks
corrected, and time or money saved, with links to the audit
log for every counted event. The counts come from the ledger
(the same events the proof surface serves), so a number in
this report always has a receipt behind it.

Definitions, so no count is ever mysterious:

- ``tasks_completed`` — ``task_completed`` events in the window.
- ``tasks_corrected`` — the user pushing back: ``approval_rejected``
  events (a draft or action the user refused) plus
  ``false_report`` events (the agent reported something that
  was not true). A correction is the user teaching the agent,
  not a failure: a corrected task still counts as completed
  when it later completed.
- ``approvals_granted`` / ``approvals_rejected`` — the two sides
  of the same decisions, so the corrected count can be checked.
- ``tasks_stopped`` / ``tasks_failed`` — work that ended early,
  for context (a stopped task is not a completed one).

Time and money saved are reported as null, on purpose, with
the reason in ``savings_note``: no savings baseline is
recorded anywhere yet. Time saved needs a manual-time
baseline (how long the user would have taken by hand) and
money saved needs a compared price (what the same purchase
would have cost elsewhere); the ledger records neither, so
claiming a saved figure would be invented data. What IS
measured is shown instead: tasks completed, and the cost
incurred in the same window (from the billing usage ledger,
via ``puspa.billing.ports``), labelled as cost, never as
savings. When the cost ledger is unreachable the cost is null
too, not zero: zero would claim no money was spent.

The report links to the audit log twice: ``audit_trail_url``
is the user's whole trail (``GET /api/proof/audit-trail``),
and every counted event in ``linked_events`` carries its own
receipt URL (``GET /api/proof/receipts/{event_id}``).

The window is the last ``days`` calendar days ending today
(UTC), day-aligned like the insights report, so the report
and the activity chart cover the same days. Default 7.
"""

from __future__ import annotations

from datetime import UTC, datetime, time, timedelta

from puspa.db import Error as _db_Error

from ..billing.ports import format_idr, insights_for_user, validate_days
from ..evidence.proof import event_belongs_to_user, user_agent_ids
from ..ledger import Ledger

__all__ = ["summarise_weekly", "weekly_report_for_user"]

#: How many counted events the report links to (newest first).
MAX_LINKED_EVENTS = 20

#: URL of the user's audit trail (the proof surface).
AUDIT_TRAIL_URL = "/api/proof/audit-trail"

#: Event types that count as a correction (the user pushing back).
CORRECTED_EVENT_TYPES = ("approval_rejected", "false_report")

#: Event types the report links to as receipts.
LINKED_EVENT_TYPES = (
    "task_completed",
    "approval_rejected",
    "false_report",
    "task_stopped",
)

#: Why no savings figure is claimed (see the module docstring).
SAVINGS_NOTE = (
    "No savings figure is claimed: time saved needs a manual-time "
    "baseline and money saved needs a compared price, and neither "
    "is recorded in the ledger yet. Tasks completed and cost "
    "incurred are the measured figures."
)


def _as_utc(moment: datetime | None) -> datetime | None:
    """A datetime as UTC (naive values are read as UTC)."""
    if moment is None:
        return None
    if moment.tzinfo is None:
        return moment.replace(tzinfo=UTC)
    return moment.astimezone(UTC)


def summarise_weekly(
    events, *, days: int = 7, now: datetime | None = None, cost_idr: int | None = None
) -> dict:
    """Summarise ledger events into a weekly report.

    Pure function: ``events`` may include rows outside the
    window (they are ignored) and may be unsorted. ``now``
    defaults to the current UTC time. ``cost_idr`` is the
    cost incurred in the same window, from the billing usage
    ledger (None when unknown).
    """
    days = validate_days(days)
    now = _as_utc(now) or datetime.now(UTC)
    first_day = now.date() - timedelta(days=days - 1)
    since = datetime.combine(first_day, time.min, tzinfo=UTC)
    until = datetime.combine(now.date(), time.max, tzinfo=UTC)

    in_window = []
    for event in events:
        created_at = _as_utc(event.get("created_at"))
        if created_at is None or created_at < since or created_at > until:
            continue
        in_window.append(event)

    def count(*types: str) -> int:
        return sum(1 for event in in_window if event.get("type") in types)

    completed = count("task_completed")
    corrected = count(*CORRECTED_EVENT_TYPES)
    linked = [
        {
            "event_id": str(event.get("id")),
            "type": event.get("type"),
            "task_id": event.get("task_id"),
            "created_at": (_as_utc(event.get("created_at")) or now).isoformat(),
            "receipt_url": f"/api/proof/receipts/{event.get('id')}",
        }
        for event in sorted(
            (event for event in in_window if event.get("type") in LINKED_EVENT_TYPES),
            key=lambda item: _as_utc(item.get("created_at")) or now,
            reverse=True,
        )
    ][:MAX_LINKED_EVENTS]

    return {
        "window_days": days,
        "since": since.isoformat(),
        "until": until.isoformat(),
        "tasks_completed": completed,
        "tasks_corrected": corrected,
        "approvals_granted": count("approval_granted"),
        "approvals_rejected": count("approval_rejected"),
        "tasks_stopped": count("task_stopped"),
        "tasks_failed": count("task_failed"),
        "time_saved_minutes": None,
        "money_saved_idr": None,
        "savings_note": SAVINGS_NOTE,
        "cost_idr": cost_idr,
        "cost_label": format_idr(cost_idr) if cost_idr is not None else None,
        "audit_trail_url": AUDIT_TRAIL_URL,
        "linked_events": linked,
    }


def weekly_report_for_user(
    user_id: str,
    *,
    dsn=None,
    days: int = 7,
    now: datetime | None = None,
    ledger: Ledger | None = None,
    events=None,
) -> dict:
    """The weekly report for one user over the last ``days`` days.

    Loads the user's ledger events in the window (or uses the
    ``events`` given, for tests) and the cost incurred from
    the billing usage ledger. A user with nothing recorded
    gets an honest all-zero report with null savings, not
    invented figures.
    """
    days = validate_days(days)
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    now = _as_utc(now) or datetime.now(UTC)
    first_day = now.date() - timedelta(days=days - 1)
    since = datetime.combine(first_day, time.min, tzinfo=UTC)
    if events is None:
        store = ledger if ledger is not None else Ledger(dsn)
        agent_ids = user_agent_ids(user_id, dsn=dsn)
        recent = store.recent_events(since)
        events = [
            event for event in recent if event_belongs_to_user(event, user_id, agent_ids, dsn=dsn)
        ]
    cost_idr: int | None
    try:
        cost_idr = insights_for_user(user_id, days=days, dsn=dsn, now=now)["cost"]["total_idr"]
    except _db_Error:
        cost_idr = None
    return summarise_weekly(events, days=days, now=now, cost_idr=cost_idr)
