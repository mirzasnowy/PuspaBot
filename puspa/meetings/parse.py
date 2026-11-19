"""Deterministic meeting-minutes extraction (B10).

From a transcript or notes, the agent writes minutes: decisions
and action items with owners and deadlines. This module is the
pure half of that work — no database, no model call:

- :func:`parse_transcript` turns marked-up text into a
  :class:`MeetingMinutes` value. Only explicit markers become
  decisions or action items:

  - ``Decision: ...`` — a decision.
  - ``Action: ...`` / ``TODO: ...`` / ``- [ ] ...`` — an action
    item (same markers as the B6 task splitter, so a chat line
    and a transcript line mean the same thing).
  - ``Title: ...`` — the meeting title (default: "Meeting").
  - ``Attendees: a, b`` / ``Present: a, b`` — who was there.
  - ``Note: ...`` — a note carried into the minutes.
  - ``@Name`` inside an action item — the owner, by display
    name. ``by 2026-11-25`` / ``due 2026-11-25`` / ``by Friday``
    / ``tomorrow`` — the deadline, resolved against a reference
    date (the meeting date, or today). Weekday names work in
    English and Indonesian (``Friday`` / ``Jumat``).

  Lines without a marker are ignored: the parser never invents
  a decision or an action item out of ordinary talk. An owner
  name that cannot be resolved to a room member stays an
  unresolved name — the store records it and leaves the board
  task unassigned rather than guessing a user id.
- :func:`render_minutes` turns the parsed value into the plain
  minutes text the room reads.
- :func:`polish_minutes` is the optional LLM seam: when a
  ``llm_complete`` callable (the ``complete(prompt)`` seam from
  ``puspa/router/complete.py``) is given, it asks the model to
  rewrite the minutes in smoother prose — then checks that
  every extracted decision and action-item title still appears
  verbatim in the polished text. When any of them is missing
  (the model dropped or rewrote a fact), the deterministic
  rendering is returned instead. Without a callable, the
  deterministic rendering is returned unchanged. The beta ships
  the deterministic minutes; the polish is opt-in.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import UTC, date, datetime, time, timedelta

__all__ = [
    "MeetingMinutes",
    "ParsedActionItem",
    "ParsedDecision",
    "due_datetime",
    "parse_transcript",
    "polish_minutes",
    "render_minutes",
]

#: An action-item title is at most this long (same cap as the B6
#: board, so a minutes action item always fits its board task).
MAX_ACTION_TITLE_CHARS = 500

#: Minutes text is at most this long (same cap as room memory).
MAX_MINUTES_TEXT_CHARS = 10_000

_TITLE_RE = re.compile(r"^\s*(?:Title|Meeting|Subject)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_ATTENDEES_RE = re.compile(r"^\s*(?:Attendees|Present|Hadir)\s*:\s*(.+?)\s*$", re.IGNORECASE)
_DECISION_RE = re.compile(r"^\s*Decision\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_NOTE_RE = re.compile(r"^\s*Note\s*:\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE)
_ACTION_RE = re.compile(
    r"^\s*(?:Action\s*:|TODO\s*:|- \[\s\]\s*)\s*(.+?)\s*$", re.IGNORECASE | re.MULTILINE
)
_OWNER_RE = re.compile(r"@([A-Za-z][A-Za-z0-9_.-]*)")
_ISO_DATE_RE = re.compile(r"\b(\d{4})-(\d{2})-(\d{2})\b")
_DUE_RE = re.compile(
    r"\b(?:by|due|before|deadline)\s+"
    r"(\d{4}-\d{2}-\d{2}|today|tomorrow|hari ini|besok"
    r"|next\s+[A-Za-z]+|[A-Za-z]+)\b",
    re.IGNORECASE,
)
_OWNER_CLAUSE_RE = re.compile(r"\bowner\s*:\s*([A-Za-z][A-Za-z0-9_.-]*)", re.IGNORECASE)

#: Weekday name (English and Indonesian) -> Monday=0 .. Sunday=6.
_WEEKDAYS = {
    "monday": 0,
    "senin": 0,
    "tuesday": 1,
    "selasa": 1,
    "wednesday": 2,
    "rabu": 2,
    "thursday": 3,
    "kamis": 3,
    "friday": 4,
    "jumat": 4,
    "saturday": 5,
    "sabtu": 5,
    "sunday": 6,
    "minggu": 6,
}


@dataclass(frozen=True)
class ParsedDecision:
    """One decision stated in the transcript."""

    text: str


@dataclass(frozen=True)
class ParsedActionItem:
    """One action item: title, owner name (if stated), deadline."""

    title: str
    owner_name: str | None
    due_date: date | None
    raw: str


@dataclass(frozen=True)
class MeetingMinutes:
    """A parsed meeting: title, date, attendees, decisions, actions."""

    title: str
    held_on: date
    attendees: tuple[str, ...]
    decisions: tuple[ParsedDecision, ...]
    actions: tuple[ParsedActionItem, ...]
    notes: tuple[str, ...]


def _resolve_due(token: str, ref: date) -> date | None:
    """Resolve one due-date token against the reference date.

    ``today`` / ``hari ini`` is the reference date itself;
    ``tomorrow`` / ``besok`` is the next day; a weekday name is
    the next day of that name strictly after the reference date
    (``by Friday`` said on a Friday means next Friday); ``next
    <weekday>`` is the one after that. An ISO date is itself.
    Returns None for a token that is not a date at all.
    """
    token = token.strip().lower()
    if token in ("today", "hari ini"):
        return ref
    if token in ("tomorrow", "besok"):
        return ref + timedelta(days=1)
    iso = _ISO_DATE_RE.fullmatch(token)
    if iso:
        try:
            return date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3)))
        except ValueError:
            return None
    is_next = token.startswith("next ")
    name = token[5:].strip() if is_next else token
    target = _WEEKDAYS.get(name)
    if target is None:
        return None
    offset = (target - ref.weekday()) % 7
    if offset == 0:
        offset = 7
    if is_next:
        offset += 7
    return ref + timedelta(days=offset)


def _parse_action(raw: str, ref: date) -> ParsedActionItem:
    """Parse one action-item line: owner, deadline, clean title."""
    owner_name: str | None = None
    owner_match = _OWNER_RE.search(raw)
    if owner_match:
        owner_name = owner_match.group(1)
    else:
        clause_match = _OWNER_CLAUSE_RE.search(raw)
        if clause_match:
            owner_name = clause_match.group(1)
    due_date: date | None = None
    due_match = _DUE_RE.search(raw)
    if due_match:
        due_date = _resolve_due(due_match.group(1), ref)
    title = raw
    title = _OWNER_RE.sub("", title)
    title = _OWNER_CLAUSE_RE.sub("", title)
    # Remove the due clause by pattern, not by the raw-text offsets:
    # the owner removal above may have shifted them.
    title = _DUE_RE.sub("", title)
    title = re.sub(r"\s+", " ", title)
    title = re.sub(r"^[\s\-:,.]+|[\s\-:,.]+$", "", title)
    return ParsedActionItem(
        title=title[:MAX_ACTION_TITLE_CHARS],
        owner_name=owner_name,
        due_date=due_date,
        raw=raw.strip(),
    )


def parse_transcript(
    text: str,
    *,
    ref_date: date | None = None,
    held_at: datetime | date | None = None,
) -> MeetingMinutes:
    """Parse a transcript or notes into structured minutes.

    ``held_at`` is when the meeting happened (a datetime or a
    date); ``ref_date`` overrides the date deadlines are resolved
    against. Both default to today (UTC). Only explicit markers
    become decisions or action items — unmarked talk is ignored.
    """
    if not isinstance(text, str):
        raise ValueError("transcript must be a string")
    if held_at is None:
        held_on = ref_date or datetime.now(UTC).date()
    elif isinstance(held_at, datetime):
        held_on = held_at.date()
    else:
        held_on = held_at
    ref = ref_date or held_on
    title = "Meeting"
    attendees: list[str] = []
    for line in text.splitlines():
        title_match = _TITLE_RE.match(line)
        if title_match and title_match.group(1):
            title = title_match.group(1).strip()
            continue
        attendees_match = _ATTENDEES_RE.match(line)
        if attendees_match:
            attendees = [
                name.strip().lstrip("@")
                for name in attendees_match.group(1).split(",")
                if name.strip()
            ]
    decisions = tuple(
        ParsedDecision(match.group(1).strip())
        for match in _DECISION_RE.finditer(text)
        if match.group(1).strip()
    )
    notes = tuple(
        match.group(1).strip() for match in _NOTE_RE.finditer(text) if match.group(1).strip()
    )
    actions = tuple(
        _parse_action(match.group(1).strip(), ref)
        for match in _ACTION_RE.finditer(text)
        if match.group(1).strip()
    )
    return MeetingMinutes(
        title=title,
        held_on=held_on,
        attendees=tuple(attendees),
        decisions=decisions,
        actions=actions,
        notes=notes,
    )


def render_minutes(minutes: MeetingMinutes) -> str:
    """Render parsed minutes as the plain text the room reads."""
    lines = [f"Meeting minutes: {minutes.title}", f"Held: {minutes.held_on.isoformat()}"]
    if minutes.attendees:
        lines.append("Attendees: " + ", ".join(minutes.attendees) + ".")
    else:
        lines.append("Attendees: not stated.")
    if minutes.decisions:
        lines.append("Decisions:")
        for index, decision in enumerate(minutes.decisions, start=1):
            lines.append(f"{index}. {decision.text}")
    else:
        lines.append("Decisions: none stated.")
    if minutes.actions:
        lines.append("Action items:")
        for index, action in enumerate(minutes.actions, start=1):
            owner = action.owner_name or "unassigned"
            due = action.due_date.isoformat() if action.due_date else "no deadline"
            lines.append(f"{index}. {action.title} — owner: {owner} — due: {due}")
    else:
        lines.append("Action items: none stated.")
    if minutes.notes:
        lines.append("Notes:")
        for note in minutes.notes:
            lines.append(f"- {note}")
    return "\n".join(lines)


def due_datetime(due: date) -> datetime:
    """A due date means due by the end of that day (UTC)."""
    return datetime.combine(due, time(23, 59), tzinfo=UTC)


def polish_minutes(minutes: MeetingMinutes, *, llm_complete=None) -> str:
    """Optionally rewrite the minutes in smoother prose via LLM.

    ``llm_complete`` is a ``complete(prompt)`` callable (the seam
    from ``puspa/router/complete.py``). When it is None, the
    deterministic rendering is returned unchanged. When it is
    given, the model rewrites the deterministic minutes — then
    every extracted decision and action-item title must still
    appear verbatim in the polished text. When any of them is
    missing, the model dropped or rewrote a fact, and the
    deterministic rendering is returned instead.
    """
    deterministic = render_minutes(minutes)
    if llm_complete is None:
        return deterministic
    prompt = (
        "Rewrite these meeting minutes in smooth, concise prose. "
        "Keep every decision and every action item exactly as stated, "
        "with the same owner and deadline. Do not add decisions, "
        "action items, owners, or deadlines that are not in the text.\n\n" + deterministic
    )
    polished = llm_complete(prompt)
    if not isinstance(polished, str) or not polished.strip():
        return deterministic
    for decision in minutes.decisions:
        if decision.text not in polished:
            return deterministic
    for action in minutes.actions:
        if action.title not in polished:
            return deterministic
    return polished.strip()
