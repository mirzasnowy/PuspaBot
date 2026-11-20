"""Meeting-minutes store (B10).

One meeting, end to end, from a transcript:

1. Parse the transcript deterministically
   (:func:`puspa.meetings.parse_transcript`) — decisions and
   action items come only from explicit markers, never invented.
2. Put every action item on the shared room task board (B6,
   ``RoomTaskStore``): owner and deadline when the transcript
   states them, unassigned when the owner name cannot be resolved
   to a room member. The board is the B9 task board's base — B9's
   delegated-task layer extends the same board later; this module
   never imports from B9.
3. Record every decision as room memory of kind ``decision``
   (``RoomMemoryStore.record_decision``), so the room's shared
   record holds it.
4. Store the rendered minutes plus the structured extraction in
   ``meeting_minutes`` (see migration 0011_meetings).
5. When a ledger is given, append ledger events for the decisions
   and the action items, through the existing ledger / evidence
   pattern — no new event type (the closed set of 31 stays
   closed):

   - ``evidence_recorded`` per decision, written through the
     B2 shared outcome writer (``puspa.evidence.outcomes``)
     as a ``decision_recorded`` outcome record, content
     ``{"kind": "outcome_record", "outcome_type":
     "decision_recorded", "user_id": ..., "room_id": ...,
     "subject_id": <meeting id>, "summary": <decision>,
     "evidence": [{"kind": "meeting_minutes", ...}],
     "details": {<the pre-B2 payload, verbatim>}}``;
   - ``task_created`` per action item, content
     ``{"kind": "meeting_action_item", "meeting_id": ...,
     "room_id": ..., "room_task_id": ..., "title": ...,
     "owner_user_id": ..., "due_at": ...}`` — creating a task
     is not an outcome, so it stays a plain task event and is
     NOT routed through the outcome writer.

   The ledger's ``task_id`` for these events is the meeting id:
   the events chain per meeting, so the chain for one meeting
   verifies on its own. B2 adopted the decision events (same
   event type, the pre-B2 payload preserved verbatim under
   ``details``) instead of replacing them — see
   ``docs/outcome-records.md``.

The transcript itself is never stored: the minutes are the
room's record.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import UTC, date, datetime, time

import psycopg
from psycopg.rows import dict_row
from psycopg.types.json import Jsonb

from puspa.accounts.users import resolve_dsn
from puspa.evidence.outcomes import write_outcome
from puspa.ledger import Ledger
from puspa.rooms.errors import NotRoomMemberError
from puspa.rooms.memory import RoomMemoryStore
from puspa.rooms.rooms import RoomStore, _validate_uuid
from puspa.rooms.tasks import RoomTaskStore

from .parse import (
    MAX_MINUTES_TEXT_CHARS,
    MeetingMinutes,
    due_datetime,
    parse_transcript,
    render_minutes,
)

__all__ = [
    "MeetingMinutesRecord",
    "MeetingMinutesStore",
    "get_minutes",
    "list_minutes",
    "write_minutes",
]


@dataclass(frozen=True)
class MeetingMinutesRecord:
    """One stored meeting: rendered minutes + structured extraction."""

    id: str
    room_id: str
    title: str
    held_at: datetime
    author_user_id: str
    minutes_text: str
    decisions: list[str]
    action_items: list[dict]
    created_at: datetime


def _record_row(row) -> MeetingMinutesRecord:
    return MeetingMinutesRecord(
        id=str(row["id"]),
        room_id=str(row["room_id"]),
        title=row["title"],
        held_at=row["held_at"],
        author_user_id=str(row["author_user_id"]),
        minutes_text=row["minutes_text"],
        decisions=list(row["decisions"] or []),
        action_items=list(row["action_items"] or []),
        created_at=row["created_at"],
    )


def _connect(dsn=None):
    return psycopg.connect(resolve_dsn(dsn), row_factory=dict_row)


def _resolve_owner(
    owner_name: str | None,
    members_by_name: dict[str, str] | None,
    is_member,
) -> str | None:
    """Resolve an owner display name to a room member user id.

    ``members_by_name`` maps display name -> user id (the chat
    surface knows display names; the database only knows ids).
    Matches the full name first, then a first-name match. A name
    that matches nobody — or matches a non-member — resolves to
    None: the action item stays unassigned rather than guessing
    a user id.
    """
    if not owner_name or not members_by_name:
        return None
    lookup = {str(name).strip().lower(): uid for name, uid in members_by_name.items()}
    candidate = lookup.get(owner_name.strip().lower())
    if candidate is None:
        first = owner_name.strip().lower().split()[0]
        for name, uid in lookup.items():
            if name.split()[0] == first:
                candidate = uid
                break
    if candidate is None:
        return None
    try:
        candidate = _validate_uuid(candidate, "owner_user_id")
    except ValueError:
        return None
    return candidate if is_member(candidate) else None


class MeetingMinutesStore:
    """Postgres-backed meeting minutes for one room."""

    def __init__(
        self,
        dsn=None,
        room_store: RoomStore | None = None,
        task_store: RoomTaskStore | None = None,
        memory_store: RoomMemoryStore | None = None,
    ):
        self.dsn = resolve_dsn(dsn)
        self.rooms = room_store or RoomStore(dsn)
        self.tasks = task_store or RoomTaskStore(dsn)
        self.memory = memory_store or RoomMemoryStore(dsn)

    def write_minutes(
        self,
        room_id: str,
        *,
        author_user_id: str,
        transcript: str,
        title: str | None = None,
        held_at: datetime | date | None = None,
        members_by_name: dict[str, str] | None = None,
        ledger: Ledger | None = None,
        agent_id: str | None = None,
        llm_complete=None,
    ) -> MeetingMinutesRecord:
        """Write minutes for a meeting from its transcript.

        Parses the transcript, creates one board task per action
        item, records each decision in room memory, stores the
        minutes row, and (when ``ledger`` is given) appends the
        ledger events described in the module docstring. ``title``
        overrides the ``Title:`` line in the transcript when
        given. ``llm_complete`` is the optional polish seam (see
        :func:`puspa.meetings.polish_minutes`); without it the
        deterministic rendering is stored.

        Raises ``NotRoomMemberError`` for a non-member author,
        ``ValueError`` for an empty transcript or when a ledger is
        given without ``agent_id``.
        """
        room_id = _validate_uuid(room_id, "room_id")
        author = _validate_uuid(author_user_id, "author_user_id")
        if not isinstance(transcript, str) or not transcript.strip():
            raise ValueError("transcript must be a non-empty string")
        if ledger is not None and not agent_id:
            raise ValueError("agent_id is required when a ledger is given")
        self.rooms.require_room(room_id)
        if not self.rooms.is_member(room_id, author):
            raise NotRoomMemberError(f"user {author} is not a member of room {room_id}")
        parsed: MeetingMinutes = parse_transcript(transcript, held_at=held_at)
        if title is not None:
            if not isinstance(title, str) or not title.strip():
                raise ValueError("title must be a non-empty string")
            parsed = MeetingMinutes(
                title=title.strip(),
                held_on=parsed.held_on,
                attendees=parsed.attendees,
                decisions=parsed.decisions,
                actions=parsed.actions,
                notes=parsed.notes,
            )
        if held_at is None:
            held_dt = datetime.combine(parsed.held_on, time(9, 0), tzinfo=UTC)
        elif isinstance(held_at, datetime):
            held_dt = held_at if held_at.tzinfo is not None else held_at.replace(tzinfo=UTC)
        else:
            held_dt = datetime.combine(held_at, time(9, 0), tzinfo=UTC)
        if llm_complete is not None:
            from .parse import polish_minutes

            minutes_text = polish_minutes(parsed, llm_complete=llm_complete)
        else:
            minutes_text = render_minutes(parsed)
        if len(minutes_text) > MAX_MINUTES_TEXT_CHARS:
            raise ValueError(f"minutes text is longer than {MAX_MINUTES_TEXT_CHARS} characters")
        is_member = lambda uid: self.rooms.is_member(room_id, uid)  # noqa: E731
        action_items: list[dict] = []
        for action in parsed.actions:
            owner_id = _resolve_owner(action.owner_name, members_by_name, is_member)
            due_at = due_datetime(action.due_date) if action.due_date else None
            task = self.tasks.add_task(
                room_id,
                title=action.title,
                created_by=author,
                owner_user_id=owner_id,
                due_at=due_at,
            )
            action_items.append(
                {
                    "title": action.title,
                    "owner_name": action.owner_name,
                    "owner_user_id": owner_id,
                    "due_at": action.due_date.isoformat() if action.due_date else None,
                    "task_id": task.id,
                }
            )
        decisions = [decision.text for decision in parsed.decisions]
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO meeting_minutes "
                "(room_id, title, held_at, author_user_id, minutes_text, "
                " decisions, action_items) "
                "VALUES (%s, %s, %s, %s, %s, %s, %s) RETURNING *",
                (
                    room_id,
                    parsed.title,
                    held_dt,
                    author,
                    minutes_text,
                    Jsonb(decisions),
                    Jsonb(action_items),
                ),
            )
            record = _record_row(cur.fetchone())
            conn.commit()
        for decision in decisions:
            self.memory.record_decision(room_id, author_user_id=author, decision=decision)
        if ledger is not None:
            for decision in decisions:
                # B2: the decision is a verified outcome record.
                # The pre-B2 payload is preserved verbatim under
                # ``details``; the action-item event below stays a
                # plain ``task_created`` (creating a task is not
                # an outcome).
                write_outcome(
                    ledger,
                    task_id=record.id,
                    agent_id=agent_id,  # type: ignore[arg-type]
                    outcome_type="decision_recorded",
                    user_id=author,
                    room_id=room_id,
                    subject_id=record.id,
                    summary=decision,
                    evidence=[
                        {
                            "kind": "meeting_minutes",
                            "meeting_id": record.id,
                            "decision": decision,
                        }
                    ],
                    details={
                        "kind": "meeting_decision",
                        "meeting_id": record.id,
                        "room_id": room_id,
                        "decision": decision,
                    },
                )
            for item in action_items:
                ledger.append_event(
                    task_id=record.id,
                    agent_id=agent_id,  # type: ignore[arg-type]
                    event_type="task_created",
                    content={
                        "kind": "meeting_action_item",
                        "meeting_id": record.id,
                        "room_id": room_id,
                        "room_task_id": item["task_id"],
                        "title": item["title"],
                        "owner_user_id": item["owner_user_id"],
                        "due_at": item["due_at"],
                    },
                )
        return record

    def get_minutes(self, minutes_id: str, *, viewer_user_id: str) -> MeetingMinutesRecord | None:
        """One minutes row, visible only to a room member.

        Returns None when the id does not exist. Raises
        ``NotRoomMemberError`` for a non-member.
        """
        minutes_id = _validate_uuid(minutes_id, "minutes_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM meeting_minutes WHERE id = %s", (minutes_id,))
            row = cur.fetchone()
            if row is None:
                return None
            record = _record_row(row)
        if not self.rooms.is_member(record.room_id, viewer):
            raise NotRoomMemberError(f"user {viewer} is not a member of room {record.room_id}")
        return record

    def list_minutes(self, room_id: str, *, viewer_user_id: str) -> list[MeetingMinutesRecord]:
        """All minutes of a room, newest first. Members only."""
        room_id = _validate_uuid(room_id, "room_id")
        viewer = _validate_uuid(viewer_user_id, "viewer_user_id")
        if not self.rooms.is_member(room_id, viewer):
            raise NotRoomMemberError(f"user {viewer} is not a member of room {room_id}")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM meeting_minutes WHERE room_id = %s "
                "ORDER BY held_at DESC, created_at DESC, id",
                (room_id,),
            )
            return [_record_row(row) for row in cur.fetchall()]


def _store(dsn=None) -> MeetingMinutesStore:
    return MeetingMinutesStore(dsn)


def write_minutes(
    room_id: str,
    *,
    author_user_id: str,
    transcript: str,
    title: str | None = None,
    held_at: datetime | date | None = None,
    members_by_name: dict[str, str] | None = None,
    ledger: Ledger | None = None,
    agent_id: str | None = None,
    llm_complete=None,
    dsn=None,
) -> MeetingMinutesRecord:
    return _store(dsn).write_minutes(
        room_id,
        author_user_id=author_user_id,
        transcript=transcript,
        title=title,
        held_at=held_at,
        members_by_name=members_by_name,
        ledger=ledger,
        agent_id=agent_id,
        llm_complete=llm_complete,
    )


def get_minutes(minutes_id: str, *, viewer_user_id: str, dsn=None) -> MeetingMinutesRecord | None:
    return _store(dsn).get_minutes(minutes_id, viewer_user_id=viewer_user_id)


def list_minutes(room_id: str, *, viewer_user_id: str, dsn=None) -> list[MeetingMinutesRecord]:
    return _store(dsn).list_minutes(room_id, viewer_user_id=viewer_user_id)
