"""Morning summary assembly (P-07, plan section 12.8).

The summary is built from five sources, each with its own honest
status — the same pattern as ``GET /api/voice/status`` (P-03):
a source that is not connected or not reachable says so, and its
section is empty with a reason, instead of the summary pretending
the data exists.

Sources:

- **Schedule** — today's events from Google Calendar, via the
  Google connector (``puspa.connectors.google``). Calendar is not
  connected yet (Gmail + Drive are; see ``GET /api/morning/status``),
  so until it is, this section honestly reports ``not_connected``.
- **Bills due** — bill *candidates* from Gmail: messages matching
  bill words, found with ``gmail_search`` and read with
  ``gmail_read``. They are candidates, not confirmed bills: the
  keyword match and any due date printed in the subject or
  snippet are labelled as such (``verified: false``), and the
  summary tells the user to open the email to confirm the amount
  and the due date. No amount is ever parsed out of an email body
  and presented as a fact.
- **Tasks** — the user's open rows in the ``tasks`` table
  (the P-02 task store): anything not completed or failed.
- **Holidays** — Indonesian public holidays from Nager.Date
  (:mod:`puspa.morning.holidays`).
- **Prayer times** — Aladhan, method 20 (Kemenag RI)
  (:mod:`puspa.morning.prayer`). Imsak is shown only during
  Ramadan; outside Ramadan the section says so plainly.

:func:`build_morning_summary` is pure (it assembles already-fetched
data), so it is tested without any network or database. The two
fetching helpers (:func:`find_bill_candidates`,
:func:`open_tasks_for_user`) are thin and tested separately.
"""

from __future__ import annotations

import datetime
import re
from collections.abc import Callable

from puspa.db import connect as _db_connect

from ..memory.schema import resolve_dsn
from ..mind.workspace import Candidate, WorkspaceBus
from .holidays import holidays_on_date, upcoming_holidays
from .prayer import JAKARTA_TZ

__all__ = [
    "BILL_QUERY",
    "OPEN_TASK_STATUSES",
    "build_morning_summary",
    "extract_due_hint",
    "extract_email_subject",
    "find_bill_candidates",
    "open_tasks_for_user",
    "select_morning_focus",
]

#: Gmail search for bill-like mail (last 60 days; the caller adds
#: the ``newer:`` bound when it wants one — the search itself is
#: cheap metadata, the reads are what cost calls).
BILL_QUERY = 'tagihan OR "jatuh tempo" OR PLN OR PDAM OR cicilan OR paylater'

#: Words that mark a message as a bill candidate (matched against
#: the subject + snippet, case-insensitive).
BILL_KEYWORDS = (
    "tagihan",
    "jatuh tempo",
    "pln",
    "pdam",
    "cicilan",
    "paylater",
    "listrik",
    "air minum",
    "langganan",
    "pembayaran",
)

#: How many bill candidates the summary reads in full (each read is
#: one Gmail call; the rest stay unopened).
MAX_BILL_READS = 5

#: Task statuses that count as "still open" (see
#: ``puspa.runtime.tasks``: a task ends as ``completed`` or
#: ``failed``; anything else is still the user's to do).
OPEN_TASK_STATUSES = ("accepted", "running", "pending_user", "pending_approval")

#: Indonesian month names, for the due-date hint in a subject/snippet.
_INDONESIAN_MONTHS = (
    "Januari",
    "Februari",
    "Maret",
    "April",
    "Mei",
    "Juni",
    "Juli",
    "Agustus",
    "September",
    "Oktober",
    "November",
    "Desember",
)

#: ``12 Desember`` or ``12 Desember 2026`` inside a subject/snippet.
_DUE_DATE_RE = re.compile(
    r"(\d{1,2})\s+(" + "|".join(_INDONESIAN_MONTHS) + r")(?:\s+(\d{4}))?",
    re.IGNORECASE,
)

#: ``tanggal 12`` / ``tgl 12`` as a weaker due hint.
_DUE_DAY_RE = re.compile(r"\bt(?:an)?ggal\s+(\d{1,2})\b", re.IGNORECASE)


def extract_due_hint(text: str) -> str | None:
    """A due-date hint printed in a subject or snippet, or None.

    Only a date literally printed in the text is returned, as the
    printed words (``"12 Desember 2026"``); nothing is inferred
    from the email's received date. The caller labels the hint
    with its source, so the user knows it came from the subject
    line, not from a parsed bill.
    """
    match = _DUE_DATE_RE.search(text or "")
    if match is not None:
        day, month, year = match.groups()
        hint = f"{int(day)} {month.capitalize()}"
        return f"{hint} {year}" if year else hint
    match = _DUE_DAY_RE.search(text or "")
    if match is not None:
        return f"tanggal {int(match.group(1))}"
    return None


def extract_email_subject(data: dict) -> str:
    """The Subject header of a Gmail ``messages.get`` payload."""
    payload = data.get("payload") or {}
    for header in payload.get("headers") or []:
        if str(header.get("name", "")).lower() == "subject":
            return str(header.get("value", ""))
    return ""


def find_bill_candidates(messages: list[dict]) -> list[dict]:
    """Bill candidates from Gmail messages already read in full.

    ``messages`` are ``gmail_read`` results (``{id, snippet,
    payload}``). A message is a candidate when its subject or
    snippet contains a bill word. Each candidate carries
    ``verified: false`` and a note saying the amount and due date
    must be confirmed by opening the email — a keyword match is
    not a bill.
    """
    candidates = []
    for message in messages:
        subject = extract_email_subject(message)
        snippet = str(message.get("snippet") or "")
        haystack = f"{subject} {snippet}".lower()
        if not any(word in haystack for word in BILL_KEYWORDS):
            continue
        due_hint = extract_due_hint(f"{subject} {snippet}")
        candidates.append(
            {
                "message_id": str(message.get("id", "")),
                "subject": subject or "(no subject)",
                "snippet": snippet,
                "date": str(message.get("internalDate") or ""),
                "due_hint": due_hint,
                "due_hint_source": "subject_or_snippet" if due_hint else None,
                "verified": False,
                "note": (
                    "Candidate from a keyword match, not a confirmed bill. "
                    "Open the email to confirm the amount and due date."
                ),
            }
        )
    return candidates


def open_tasks_for_user(user_id: str, *, dsn=None) -> list[dict]:
    """The user's open tasks from the ``tasks`` table (P-02 store)."""
    if not user_id:
        raise ValueError("user_id must be a non-empty string")
    with _db_connect(resolve_dsn(dsn)) as conn:
        rows = conn.execute(
            "SELECT id, purpose, status, created_at FROM tasks "
            "WHERE user_id = %s AND status = ANY(%s) "
            "ORDER BY created_at ASC",
            (user_id, list(OPEN_TASK_STATUSES)),
        ).fetchall()
    return [
        {
            "task_id": str(row[0]),
            "purpose": str(row[1]),
            "status": str(row[2]),
            "created_at": row[3].isoformat() if row[3] is not None else None,
        }
        for row in rows
    ]


def _status(status: str, detail: str) -> dict:
    return {"status": status, "detail": detail}


#: Focus scores for the K18 workspace bus (see
#: :func:`select_morning_focus`): each briefing section bids
#: with fixed numbers, so the focus is deterministic. Bills bid
#: high on urgency when a due hint is printed, but low on
#: confidence — they are unverified keyword matches, never
#: confirmed bills — so a bill never outranks the schedule on
#: confidence alone. The schedule bids a flat urgency: a later
#: event loses to a bill with a due hint on score, and only the
#: imminent-event veto below puts it first.
_FOCUS_BIDS: dict[str, tuple[float, float, float]] = {
    "schedule": (0.6, 0.9, 0.9),
    "bills": (0.9, 0.8, 0.4),
    "tasks": (0.6, 0.7, 0.9),
    "holidays": (0.5, 0.6, 0.9),
}

#: An event starting within this window owns the morning focus:
#: 30 minutes ago (still running) to 60 minutes from now.
IMMINENT_EVENT_MINUTES_BEFORE = 30
IMMINENT_EVENT_MINUTES_AFTER = 60


def _parse_event_start(start: str, day: datetime.date) -> datetime.datetime | None:
    """Parse an event start into an aware Jakarta datetime.

    Accepts an ISO datetime (``"2026-11-20T09:00:00+07:00"``,
    naive values are read as Jakarta time), a bare ``"HH:MM"``
    time on ``day``, or a bare date. Anything else returns None
    instead of guessing — an unparseable start simply cannot
    claim imminence.
    """
    text = (start or "").strip()
    if not text:
        return None
    try:
        parsed = datetime.datetime.fromisoformat(text)
    except ValueError:
        parsed = None
    if parsed is not None:
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=JAKARTA_TZ)
        return parsed
    try:
        clock = datetime.time.fromisoformat(text)
    except ValueError:
        return None
    return datetime.datetime.combine(day, clock, tzinfo=JAKARTA_TZ)


def select_morning_focus(
    *,
    day: datetime.date,
    events: list[dict] | None,
    bill_candidates: list[dict] | None,
    open_tasks: list[dict] | None,
    holidays: list[dict] | None,
    now: datetime.datetime | None = None,
    record: Callable[[dict], str | None] | None = None,
) -> dict | None:
    """Pick the morning briefing's focus through the K18 bus.

    Each non-empty section posts one candidate — schedule,
    bills, tasks, holidays — with the fixed bids in
    :data:`_FOCUS_BIDS` (a bill without a due hint bids lower
    urgency, a task waiting on the user bids higher). One hard
    constraint is registered: when the first event starts
    within the imminent window it vetoes every non-schedule
    candidate, so an event starting within the hour owns the
    focus even when a bill with a due hint outscores the
    schedule. ``now`` defaults to the current Jakarta time; a
    naive ``now`` is read as Jakarta time.

    Returns the broadcast record (winner, action, scores,
    vetoes, ledger event id), or None when every section is
    empty and nothing bids. ``record`` is the bus recorder
    (see :func:`puspa.mind.workspace.ledger_recorder`); when
    given, the broadcast is recorded in the ledger.
    """
    moment = now or datetime.datetime.now(JAKARTA_TZ)
    if moment.tzinfo is None:
        moment = moment.replace(tzinfo=JAKARTA_TZ)
    bus = WorkspaceBus(record=record)
    first_event: dict | None = events[0] if events else None
    first_start: datetime.datetime | None = None
    if first_event is not None:
        first_start = _parse_event_start(str(first_event.get("start") or ""), day)
    if events:
        urgency, relevance, confidence = _FOCUS_BIDS["schedule"]
        bus.post(
            Candidate(
                module="schedule",
                action="check_schedule",
                content=str(first_event.get("summary") or "(no title)")
                if first_event is not None
                else "",
                urgency=urgency,
                relevance=relevance,
                confidence=confidence,
            )
        )
    if bill_candidates:
        has_due_hint = any(candidate.get("due_hint") for candidate in bill_candidates)
        urgency, relevance, confidence = _FOCUS_BIDS["bills"]
        bus.post(
            Candidate(
                module="bills",
                action="review_bill",
                content=str(bill_candidates[0].get("subject") or "(no subject)"),
                urgency=urgency if has_due_hint else 0.5,
                relevance=relevance,
                confidence=confidence,
            )
        )
    if open_tasks:
        needs_user = any(
            task.get("status") in ("pending_user", "pending_approval") for task in open_tasks
        )
        urgency, relevance, confidence = _FOCUS_BIDS["tasks"]
        bus.post(
            Candidate(
                module="tasks",
                action="resume_task",
                content=str(open_tasks[0].get("purpose") or "(no purpose)"),
                urgency=urgency if needs_user else 0.4,
                relevance=relevance,
                confidence=confidence,
            )
        )
    today_holidays = holidays_on_date(holidays or [], day)
    if today_holidays:
        urgency, relevance, confidence = _FOCUS_BIDS["holidays"]
        bus.post(
            Candidate(
                module="holidays",
                action="note_holiday",
                content=str(
                    today_holidays[0].get("local_name") or today_holidays[0].get("name") or ""
                ),
                urgency=urgency,
                relevance=relevance,
                confidence=confidence,
            )
        )
    if first_start is not None:
        delta_minutes = (first_start - moment).total_seconds() / 60.0
        if -IMMINENT_EVENT_MINUTES_BEFORE <= delta_minutes <= IMMINENT_EVENT_MINUTES_AFTER:
            bus.register_constraint(
                "schedule",
                lambda candidate: candidate.module != "schedule",
                reason=("An event starts within the hour, so it owns the morning focus."),
            )
    if len(bus) == 0:
        return None
    return bus.broadcast().to_dict()


def build_morning_summary(
    *,
    day: datetime.date,
    events: list[dict] | None,
    bill_candidates: list[dict] | None,
    open_tasks: list[dict] | None,
    holidays: list[dict] | None,
    prayer: dict | None,
    source_status: dict,
    now: datetime.datetime | None = None,
    focus_record: Callable[[dict], str | None] | None = None,
) -> dict:
    """Assemble the morning summary from already-fetched sources.

    Each section is either data or ``None`` with its reason in
    ``source_status`` (``ok`` / ``not_connected`` / ``unavailable``).
    ``prayer`` follows the Ramadan rule: imsak is included only
    when ``prayer["is_ramadan"]`` is true; otherwise the prayer
    section carries ``imsak: None`` and a note saying imsak is
    shown only during Ramadan.

    ``focus`` is the briefing's focus, picked by the K18
    workspace bus from the non-empty sections (see
    :func:`select_morning_focus`): the winning section, its
    scores, and any vetoes. None when every section is empty.
    ``now`` fixes the moment the focus is judged at (default:
    current Jakarta time); ``focus_record`` records the
    broadcast in the ledger when given.
    """
    today_holidays = holidays_on_date(holidays or [], day)
    upcoming = upcoming_holidays(holidays or [], day)
    prayer_section = None
    if prayer is not None:
        timings = dict(prayer["timings"])
        if not prayer["is_ramadan"]:
            timings["imsak"] = None
        prayer_section = {
            "city": prayer["city"],
            "date": prayer["date"],
            "timings": timings,
            "hijri": prayer["hijri"],
            "is_ramadan": prayer["is_ramadan"],
            "method_name": prayer["method_name"],
            "imsak_note": (None if prayer["is_ramadan"] else "Imsak is shown only during Ramadan."),
            "source": prayer["source"],
        }
    return {
        "date": day.isoformat(),
        "weekday": day.strftime("%A"),
        "focus": select_morning_focus(
            day=day,
            events=events,
            bill_candidates=bill_candidates,
            open_tasks=open_tasks,
            holidays=holidays,
            now=now,
            record=focus_record,
        ),
        "schedule": {"events": events if events is not None else []},
        "bills": {"candidates": bill_candidates if bill_candidates is not None else []},
        "tasks": {"open": open_tasks if open_tasks is not None else []},
        "holidays": {
            "today": today_holidays,
            "upcoming": upcoming,
        },
        "prayer": prayer_section,
        "source_status": {
            "calendar": source_status.get(
                "calendar", _status("unavailable", "Calendar was not checked.")
            ),
            "gmail": source_status.get("gmail", _status("unavailable", "Gmail was not checked.")),
            "tasks": source_status.get("tasks", _status("unavailable", "Tasks were not checked.")),
            "holidays": source_status.get(
                "holidays", _status("unavailable", "Holidays were not checked.")
            ),
            "prayer": source_status.get(
                "prayer", _status("unavailable", "Prayer times were not checked.")
            ),
        },
    }
