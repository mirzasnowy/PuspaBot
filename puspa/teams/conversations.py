"""The shared conversation: one log per team task.

A conversation is a team's run at one task. The task itself lives
on the conversation row; every turn the members take is one row in
``team_messages``, numbered in order (``seq``) and attributed to
the member who wrote it (``author_agent_name``). The log is the
team's shared memory for that task: every member reads the whole
log before it speaks, so a later member can build on what an
earlier member found.

Status:

- ``running`` — the run is still going.
- ``completed`` — every member finished (signalled ``done``).
- ``max_rounds`` — the round budget ran out before every member
  finished. The log stays readable; nothing is thrown away.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from datetime import datetime

from puspa.db import connect as _db_connect
from puspa.db import dict_row

from puspa.accounts.ports import resolve_dsn

from .errors import TeamConversationNotFoundError

__all__ = [
    "CONVERSATION_STATUSES",
    "STATUS_COMPLETED",
    "STATUS_MAX_ROUNDS",
    "STATUS_RUNNING",
    "TeamConversation",
    "TeamConversationStore",
    "TeamMessage",
    "append_message",
    "finish_conversation",
    "get_conversation",
    "list_conversations",
    "list_messages",
    "start_conversation",
]

STATUS_RUNNING = "running"
STATUS_COMPLETED = "completed"
STATUS_MAX_ROUNDS = "max_rounds"
CONVERSATION_STATUSES = (STATUS_RUNNING, STATUS_COMPLETED, STATUS_MAX_ROUNDS)


@dataclass(frozen=True)
class TeamConversation:
    """One run of a team at one task."""

    id: str
    team_id: str
    task: str
    status: str
    rounds_used: int
    created_at: datetime
    finished_at: datetime | None = None


@dataclass(frozen=True)
class TeamMessage:
    """One turn in the shared log, attributed to its author."""

    id: str
    conversation_id: str
    seq: int
    author_agent_name: str | None
    content: str
    created_at: datetime


def _validate_uuid(value, what: str) -> str:
    try:
        return str(uuid.UUID(str(value)))
    except (ValueError, AttributeError, TypeError) as exc:
        raise ValueError(f"{what} is not a valid UUID: {value!r}") from exc


def _conversation_row(row) -> TeamConversation:
    return TeamConversation(
        id=str(row["id"]),
        team_id=str(row["team_id"]),
        task=row["task"],
        status=row["status"],
        rounds_used=int(row["rounds_used"]),
        created_at=row["created_at"],
        finished_at=row["finished_at"],
    )


def _message_row(row) -> TeamMessage:
    return TeamMessage(
        id=str(row["id"]),
        conversation_id=str(row["conversation_id"]),
        seq=int(row["seq"]),
        author_agent_name=row["author_agent_name"],
        content=row["content"],
        created_at=row["created_at"],
    )


def _connect(dsn=None):
    return _db_connect(resolve_dsn(dsn), row_factory=dict_row)


class TeamConversationStore:
    """Postgres-backed team conversations and their shared log.

    One instance per call site; the DSN is fixed at construction.
    Every method opens its own short connection.
    """

    def __init__(self, dsn=None):
        self.dsn = resolve_dsn(dsn)

    def start_conversation(self, team_id: str, task: str) -> TeamConversation:
        """Open a conversation for one task (status ``running``).

        Raises ``ValueError`` for an empty task or a bad team id.
        """
        team_id = _validate_uuid(team_id, "team_id")
        if not isinstance(task, str) or not task.strip():
            raise ValueError("task must be a non-empty string")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO team_conversations (team_id, task) VALUES (%s, %s) RETURNING *",
                (team_id, task.strip()),
            )
            conversation = _conversation_row(cur.fetchone())
            conn.commit()
        return conversation

    def get_conversation(self, conversation_id: str) -> TeamConversation | None:
        """Return the conversation, or None when the id does not exist."""
        conversation_id = _validate_uuid(conversation_id, "conversation_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM team_conversations WHERE id = %s",
                (conversation_id,),
            )
            row = cur.fetchone()
        return _conversation_row(row) if row is not None else None

    def require_conversation(self, conversation_id: str) -> TeamConversation:
        """Return the conversation, or raise ``TeamConversationNotFoundError``."""
        conversation = self.get_conversation(conversation_id)
        if conversation is None:
            raise TeamConversationNotFoundError(f"conversation not found: {conversation_id}")
        return conversation

    def list_conversations(self, team_id: str) -> list[TeamConversation]:
        """All conversations of a team, oldest first."""
        team_id = _validate_uuid(team_id, "team_id")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM team_conversations WHERE team_id = %s ORDER BY created_at, id",
                (team_id,),
            )
            return [_conversation_row(row) for row in cur.fetchall()]

    def append_message(
        self, conversation_id: str, *, author_agent_name: str | None, content: str
    ) -> TeamMessage:
        """Append one turn to the shared log.

        ``author_agent_name`` is the member who wrote the turn, or
        None for a turn with no agent author. The sequence number
        is assigned inside the same statement, so two writers can
        never take the same ``seq``. Raises ``ValueError`` for empty
        content.
        """
        conversation = self.require_conversation(conversation_id)
        if author_agent_name is not None and (
            not isinstance(author_agent_name, str) or not author_agent_name.strip()
        ):
            raise ValueError("author_agent_name must be a non-empty string or None")
        if not isinstance(content, str) or not content.strip():
            raise ValueError("content must be a non-empty string")
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "INSERT INTO team_messages (conversation_id, seq, author_agent_name, content) "
                "SELECT %s, COALESCE(MAX(seq), -1) + 1, %s, %s "
                "FROM team_messages WHERE conversation_id = %s RETURNING *",
                (
                    conversation.id,
                    author_agent_name.strip() if author_agent_name else None,
                    content,
                    conversation.id,
                ),
            )
            message = _message_row(cur.fetchone())
            conn.commit()
        return message

    def list_messages(self, conversation_id: str) -> list[TeamMessage]:
        """The whole shared log, in turn order."""
        conversation = self.require_conversation(conversation_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM team_messages WHERE conversation_id = %s ORDER BY seq",
                (conversation.id,),
            )
            return [_message_row(row) for row in cur.fetchall()]

    def finish_conversation(
        self, conversation_id: str, *, status: str, rounds_used: int
    ) -> TeamConversation:
        """Close a conversation: set its final status and round count.

        Raises ``ValueError`` for an unknown status or a negative
        round count.
        """
        if status not in CONVERSATION_STATUSES or status == STATUS_RUNNING:
            raise ValueError(f"final status must be one of {(STATUS_COMPLETED, STATUS_MAX_ROUNDS)}")
        if not isinstance(rounds_used, int) or rounds_used < 0:
            raise ValueError("rounds_used must be a non-negative integer")
        conversation = self.require_conversation(conversation_id)
        with _connect(self.dsn) as conn, conn.cursor() as cur:
            cur.execute(
                "UPDATE team_conversations SET status = %s, rounds_used = %s, "
                "finished_at = now() WHERE id = %s RETURNING *",
                (status, rounds_used, conversation.id),
            )
            finished = _conversation_row(cur.fetchone())
            conn.commit()
        return finished


# --- Module-level functions (thin wrappers over a fresh store) -----------


def _store(dsn=None) -> TeamConversationStore:
    return TeamConversationStore(dsn)


def start_conversation(team_id: str, task: str, dsn=None) -> TeamConversation:
    return _store(dsn).start_conversation(team_id, task)


def get_conversation(conversation_id: str, dsn=None) -> TeamConversation | None:
    return _store(dsn).get_conversation(conversation_id)


def list_conversations(team_id: str, dsn=None) -> list[TeamConversation]:
    return _store(dsn).list_conversations(team_id)


def append_message(
    conversation_id: str, *, author_agent_name: str | None, content: str, dsn=None
) -> TeamMessage:
    return _store(dsn).append_message(
        conversation_id, author_agent_name=author_agent_name, content=content
    )


def list_messages(conversation_id: str, dsn=None) -> list[TeamMessage]:
    return _store(dsn).list_messages(conversation_id)


def finish_conversation(
    conversation_id: str, *, status: str, rounds_used: int, dsn=None
) -> TeamConversation:
    return _store(dsn).finish_conversation(conversation_id, status=status, rounds_used=rounds_used)
