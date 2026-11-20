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

import psycopg

from ..memory.schema import resolve_dsn
from .holidays import holidays_on_date, upcoming_holidays

__all__ = [
    "BILL_QUERY",
    "OPEN_TASK_STATUSES",
    "build_morning_summary",
    "extract_due_hint",
    "extract_email_subject",
    "find_bill_candidates",
    "open_tasks_for_user",
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
    with psycopg.connect(resolve_dsn(dsn)) as conn:
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


def build_morning_summary(
    *,
    day: datetime.date,
    events: list[dict] | None,
    bill_candidates: list[dict] | None,
    open_tasks: list[dict] | None,
    holidays: list[dict] | None,
    prayer: dict | None,
    source_status: dict,
) -> dict:
    """Assemble the morning summary from already-fetched sources.

    Each section is either data or ``None`` with its reason in
    ``source_status`` (``ok`` / ``not_connected`` / ``unavailable``).
    ``prayer`` follows the Ramadan rule: imsak is included only
    when ``prayer["is_ramadan"]`` is true; otherwise the prayer
    section carries ``imsak: None`` and a note saying imsak is
    shown only during Ramadan.
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
