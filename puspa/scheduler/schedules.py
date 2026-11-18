"""Explicit schedule schema for routines (G8).

A schedule is one member of an explicit union (adapted from the
pattern in the comparison repos, rewritten for Puspa):

- ``{"type": "once", "at": "<ISO timestamp>", "timezone": ...}``
- ``{"type": "daily", "time": "HH:MM", "weekdays": [0..6], "timezone": ...}``
  (weekday 0 is Monday; an empty/absent list means every day)
- ``{"type": "interval", "seconds": <int>, "timezone": ...}``
- ``{"type": "cron", "expression": "<min hour dom month dow>",
  "timezone": ...}``

Every schedule carries an explicit ``timezone`` (an IANA name).
The timezone is validated at the create/update gate
(:func:`validate_schedule` / :func:`RoutineScheduleInput.from_user_input`).
Readers use ``.get("timezone", "UTC")`` so records written before
the field existed keep working without a migration.

``next_occurrence`` computes the next fire time after a reference
moment; ``merge_schedule`` folds a new schedule into an old one.
"""

from __future__ import annotations

import re
from datetime import UTC, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

__all__ = [
    "DEFAULT_TIMEZONE",
    "RoutineScheduleInput",
    "ScheduleError",
    "is_valid_timezone",
    "merge_schedule",
    "next_occurrence",
    "parse_cron_expression",
    "schedule_from_record",
    "validate_schedule",
]

#: Timezone assumed when a stored record has no ``timezone`` field.
DEFAULT_TIMEZONE = "UTC"

#: One-shot grace: a run this late still executes (see ``due``).
LATE_GRACE_HOURS = 12

_SCHEDULE_TYPES = ("once", "daily", "interval", "cron")
_TIME_RE = re.compile(r"^([01]\d|2[0-3]):([0-5]\d)$")
_DELAY_RE = re.compile(r"^(\d+)\s*([smhd])$", re.IGNORECASE)


class ScheduleError(Exception):
    """A schedule dict or user schedule input is invalid."""


def is_valid_timezone(name) -> bool:
    """True when ``name`` is a loadable IANA timezone name."""
    if not isinstance(name, str) or not name:
        return False
    try:
        ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError):
        return False
    return True


def _timezone_or_default(schedule: dict) -> str:
    tz = schedule.get("timezone") or DEFAULT_TIMEZONE
    return tz if isinstance(tz, str) else DEFAULT_TIMEZONE


def validate_schedule(schedule) -> list[str]:
    """Validate an internal schedule dict; return a list of problems.

    An empty list means the schedule is valid. The timezone is
    always checked, even for schedules that do not otherwise use
    it, so a bad zone fails at the create/update gate.
    """
    problems: list[str] = []
    if not isinstance(schedule, dict):
        return ["schedule must be an object"]
    kind = schedule.get("type")
    if kind not in _SCHEDULE_TYPES:
        problems.append(f"unknown schedule type: {kind!r}")
        return problems
    tz = schedule.get("timezone") or DEFAULT_TIMEZONE
    if not is_valid_timezone(tz):
        problems.append(f"unknown timezone: {tz!r}")
    if kind == "once":
        at = schedule.get("at")
        if not isinstance(at, str):
            problems.append("'at' must be an ISO 8601 timestamp string")
        else:
            try:
                parsed = datetime.fromisoformat(at)
            except ValueError:
                problems.append(f"'at' is not a valid ISO timestamp: {at!r}")
            else:
                if parsed.tzinfo is None:
                    problems.append("'at' must carry a timezone offset")
    elif kind == "daily":
        when = schedule.get("time")
        if not isinstance(when, str) or not _TIME_RE.match(when):
            problems.append("'time' must be HH:MM (24h)")
        weekdays = schedule.get("weekdays")
        if weekdays is not None:
            if not isinstance(weekdays, list) or any(
                not isinstance(d, int) or d < 0 or d > 6 for d in weekdays
            ):
                problems.append("'weekdays' must be a list of ints 0-6 (Mon=0)")
            elif len(set(weekdays)) != len(weekdays):
                problems.append("'weekdays' must not repeat a day")
    elif kind == "interval":
        seconds = schedule.get("seconds")
        if not isinstance(seconds, int) or isinstance(seconds, bool):
            problems.append("'seconds' must be an integer number of seconds")
        elif seconds < 60:
            problems.append("'seconds' must be at least 60")
    elif kind == "cron":
        expression = schedule.get("expression")
        if not isinstance(expression, str):
            problems.append("'expression' must be a cron string")
        else:
            try:
                parse_cron_expression(expression)
            except ScheduleError as exc:
                problems.append(str(exc))
    return problems


def schedule_from_record(record) -> dict | None:
    """Normalise a stored schedule record to a validated dict.

    Readers call this instead of trusting the stored shape: a
    missing ``timezone`` becomes ``"UTC"`` and a missing
    ``weekdays`` becomes "every day", so legacy records keep
    working with no migration. Returns ``None`` for a record that
    is not a schedule at all.
    """
    if not isinstance(record, dict):
        return None
    if record.get("type") not in _SCHEDULE_TYPES:
        return None
    schedule = dict(record)
    schedule["timezone"] = _timezone_or_default(schedule)
    if schedule["type"] == "daily":
        schedule.setdefault("weekdays", [])
    return schedule


# --- cron parsing ------------------------------------------------------------

_CRON_RANGES = ((0, 59), (0, 23), (1, 31), (1, 12), (0, 6))


def parse_cron_expression(expression: str) -> tuple[frozenset, ...]:
    """Parse a 5-field cron expression into sets of allowed values.

    Fields are ``minute hour day-of-month month day-of-week``
    (day-of-week 0-6, Sunday=0; 7 is accepted as Sunday too).
    Each field accepts ``*``, a single value, a comma list, a
    range ``a-b``, and a step ``*/n`` or ``a-b/n``.
    """
    fields = str(expression).split()
    if len(fields) != 5:
        raise ScheduleError(f"cron expression must have 5 fields: {expression!r}")
    parsed: list[frozenset] = []
    for field, (low, high) in zip(fields, _CRON_RANGES):
        values: set[int] = set()
        for raw_part in field.split(","):
            part = raw_part.strip()
            step = 1
            if "/" in part:
                base, step_text = part.split("/", 1)
                try:
                    step = int(step_text)
                except ValueError:
                    raise ScheduleError(f"bad cron step in {expression!r}") from None
                if step < 1:
                    raise ScheduleError(f"cron step must be >= 1 in {expression!r}")
                part = base
            if part == "*":
                start, end = low, high
            elif "-" in part:
                try:
                    start, end = (int(x) for x in part.split("-", 1))
                except ValueError:
                    raise ScheduleError(f"bad cron range in {expression!r}") from None
            else:
                try:
                    start = end = int(part)
                except ValueError:
                    raise ScheduleError(f"bad cron value in {expression!r}") from None
            if start < low or end > high or start > end:
                raise ScheduleError(f"cron value out of range in {expression!r}")
            values.update(range(start, end + 1, step))
        if not values:
            raise ScheduleError(f"empty cron field in {expression!r}")
        parsed.append(frozenset(values))
    return tuple(parsed)


def _next_daily(schedule: dict, after: datetime) -> datetime:
    tz = ZoneInfo(_timezone_or_default(schedule))
    local_after = after.astimezone(tz)
    hour, minute = (int(x) for x in schedule["time"].split(":"))
    weekdays = schedule.get("weekdays") or []
    for day_offset in range(8):
        day = local_after.date() + timedelta(days=day_offset)
        if weekdays and day.weekday() not in weekdays:
            continue
        candidate = datetime.combine(day, time(hour, minute), tzinfo=tz)
        if candidate > local_after:
            return candidate.astimezone(UTC)
    raise ScheduleError("no next daily occurrence within a week")


def _next_cron(schedule: dict, after: datetime) -> datetime | None:
    fields = parse_cron_expression(schedule["expression"])
    minute_set, hour_set, dom_set, month_set, dow_set = fields
    tz = ZoneInfo(_timezone_or_default(schedule))
    cursor = after.astimezone(tz).replace(second=0, microsecond=0)
    cursor += timedelta(minutes=1)
    for _ in range(366 * 24 * 60):
        # Sunday is 0 in cron; Python Monday is 0.
        dow = (cursor.weekday() + 1) % 7
        if (
            cursor.minute in minute_set
            and cursor.hour in hour_set
            and cursor.day in dom_set
            and cursor.month in month_set
            and dow in dow_set
        ):
            return cursor.astimezone(UTC)
        cursor += timedelta(minutes=1)
    return None


def next_occurrence(schedule: dict, after: datetime) -> datetime | None:
    """Next fire time strictly after ``after`` (aware UTC), or None.

    ``once`` returns its time only when it is still ahead;
    ``daily`` / ``interval`` / ``cron`` always have a next
    occurrence. Raises :class:`ScheduleError` for an invalid
    schedule.
    """
    problems = validate_schedule(schedule)
    if problems:
        raise ScheduleError("; ".join(problems))
    if after.tzinfo is None:
        after = after.replace(tzinfo=UTC)
    kind = schedule["type"]
    if kind == "once":
        at = datetime.fromisoformat(schedule["at"])
        if at.tzinfo is None:
            at = at.replace(tzinfo=UTC)
        return at if at > after else None
    if kind == "daily":
        return _next_daily(schedule, after)
    if kind == "interval":
        return after + timedelta(seconds=int(schedule["seconds"]))
    if kind == "cron":
        return _next_cron(schedule, after)
    raise ScheduleError(f"unknown schedule type: {kind!r}")


def merge_schedule(old: dict, new: dict) -> dict:
    """Fold a partial schedule update into an old schedule.

    Fields present in ``new`` replace the old ones; a type change
    replaces the whole schedule (fields from the old type do not
    leak into the new one). The old timezone survives when the
    update does not set one. The result is validated.
    """
    old = schedule_from_record(old) or {}
    if not isinstance(new, dict):
        raise ScheduleError("schedule update must be an object")
    if new.get("type") is not None and new.get("type") != old.get("type"):
        merged = {"type": new["type"]}
        merged.update({k: v for k, v in new.items() if k != "type"})
        merged["timezone"] = new.get("timezone") or old.get("timezone") or DEFAULT_TIMEZONE
    else:
        merged = dict(old)
        merged.update(new)
        merged["timezone"] = _timezone_or_default(merged)
    problems = validate_schedule(merged)
    if problems:
        raise ScheduleError("; ".join(problems))
    return merged


class RoutineScheduleInput:
    """Validation of a user-supplied schedule (not the internal schema).

    Users send one flat object; this validates it and converts it
    to the internal union dict, or returns the list of problems
    without raising.
    """

    @staticmethod
    def from_user_input(data) -> tuple[dict | None, list[str]]:
        if not isinstance(data, dict):
            return None, ["schedule input must be an object"]
        kind = data.get("type")
        if kind not in _SCHEDULE_TYPES:
            return None, [f"unknown schedule type: {kind!r}"]
        schedule: dict = {"type": kind}
        if kind == "once":
            schedule["at"] = data.get("at")
        elif kind == "daily":
            schedule["time"] = data.get("time")
            if data.get("weekdays") is not None:
                schedule["weekdays"] = data.get("weekdays")
        elif kind == "interval":
            schedule["seconds"] = data.get("seconds")
        elif kind == "cron":
            schedule["expression"] = data.get("expression")
        schedule["timezone"] = data.get("timezone") or DEFAULT_TIMEZONE
        problems = validate_schedule(schedule)
        if problems:
            return None, problems
        return schedule, []
