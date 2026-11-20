"""Live view of a bot + human takeover (P-09, plan section 12.9).

What this is, honestly: Puspa has no real computer/desktop to
watch — G16 built only the execution backend and its contract,
the Docker sandbox never ran a container on this VM, and there
is no CDP browser driving anywhere. So there is no video stream
of a desktop here, and this module never pretends there is one.

What the live view IS: the bot's live activity as a *frame*
built from real events — the current task, its status, the last
tool call, the last step, and the recent event stream — read
from the ledger (durable, replayable) plus the PWA's in-memory
live tail. The frame is the same shape P-10 (replay timeline)
replays later: the ledger events in it, oldest first, are the
replay; the live tail is marked ``source: "live"`` and is
ephemeral, never part of a replay.

Takeover is the other half, and it works for real:

- Pressing "take over" writes one row to ``takeover_holds``
  (migration 0026) for the agent, interrupts the live work
  (cooperative cancel of the lifecycle children, like P-02's
  stop, plus the PWA pool's per-agent stop), and appends a
  ``takeover_started`` event to the ledger. Unlike stop-all,
  it does NOT revoke the agent's own tokens: the agent can
  resume after release. The in-flight turn ends interrupted;
  history is kept, so the next turn can continue.
- While a hold row exists, the turn loop refuses new work:
  :class:`puspa.turns.loop.TurnLoop` takes an optional
  ``takeover_hold`` callable (``agent_id -> bool``; pass a
  bound :meth:`TakeoverStore.is_held`) and checks it before
  every model call and every tool call. A held turn ends as
  ``interrupted`` with a takeover note, and no tool runs.
- Releasing deletes the hold row and appends
  ``takeover_released``. The ledger is the audit trail; the
  table keeps no history.

Ownership is enforced at this layer, not just in the HTTP
layer: every function here takes the requesting ``user_id``
and refuses (``TakeoverError`` with status 403) when the agent
is not theirs. A user can never take over someone else's bot.

Frame-capture seam: :class:`FrameSource` is the protocol a
backend implements when it CAN produce frames (a real browser
or desktop backend, post-MVP). :class:`LedgerFrameSource` is
the implementation that exists today: it builds the frame from
ledger events. No pixel frames exist yet — when a backend
lands, it implements ``capture_frame`` and the view gains a
``screenshot`` field without changing the API shape.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import TYPE_CHECKING, Any, Protocol

import psycopg
from psycopg.errors import UniqueViolation
from psycopg.rows import dict_row

from ..evidence.proof import user_agent_ids
from ..ledger import Ledger

if TYPE_CHECKING:
    from ..ledger import Ledger as LedgerType
    from ..runtime.subagents import SubagentPool
    from ..subagents.lifecycle import SubagentLifecycle

__all__ = [
    "AUDIT_TRAIL_URL",
    "LIVE_TAIL_LIMIT",
    "LIVE_WINDOW_SECONDS",
    "MAX_FRAME_EVENTS",
    "FrameSource",
    "LedgerFrameSource",
    "LiveFrame",
    "TakeoverError",
    "TakeoverHold",
    "TakeoverStore",
    "build_live_frame",
    "release_takeover",
    "takeover_for_agent",
]

#: URL of the audit trail a takeover is recorded in.
AUDIT_TRAIL_URL = "/api/proof/audit-trail"

#: A task counts as "working" when its newest event is this
#: recent (seconds). Older than that, an open task is shown as
#: idle with its last activity time: the bot may have died, and
#: the view must not claim it is working when it is not.
LIVE_WINDOW_SECONDS = 300

#: Newest ledger events kept in one frame (P-10 replays these).
MAX_FRAME_EVENTS = 100

#: In-memory live events kept in one frame (ephemeral tail).
LIVE_TAIL_LIMIT = 20

#: Ledger event types that end a task, so it is no longer open.
TERMINAL_TASK_EVENTS = frozenset({"task_completed", "task_failed", "task_stopped"})

#: Default DSN (peer auth as the OS user, no password).
DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"

#: Migration that owns the holds table (and the takeover event
#: types in the ledger CHECK): ``create_schema`` applies it
#: through the migration runner — table DDL lives only in
#: migrations (see tests/test_migrations.py). 0027 drops the
#: duplicate inline CHECK 0026 could not reach.
TAKEOVER_MIGRATION = "0027"


class TakeoverError(Exception):
    """A takeover (or release, or live view) was refused.

    ``status`` is the HTTP status the API layer answers with:
    403 not your agent, 404 nothing held, 409 already held.
    """

    def __init__(self, message: str, *, status: int = 403) -> None:
        super().__init__(message)
        self.status = status


@dataclass
class TakeoverHold:
    """One live hold: a human has taken over this agent."""

    agent_id: str
    held_by_user_id: str
    reason: str = ""
    task_id: str | None = None
    held_at: str = ""

    def to_dict(self) -> dict:
        """JSON-safe dict of this hold."""
        return {
            "agent_id": self.agent_id,
            "held_by_user_id": self.held_by_user_id,
            "reason": self.reason,
            "task_id": self.task_id,
            "held_at": self.held_at,
        }


def _row_to_hold(row: dict) -> TakeoverHold:
    held_at = row["held_at"]
    if isinstance(held_at, datetime):
        held_at = held_at.astimezone(UTC).isoformat()
    return TakeoverHold(
        agent_id=row["agent_id"],
        held_by_user_id=row["held_by_user_id"],
        reason=row["reason"] or "",
        task_id=row["task_id"],
        held_at=str(held_at),
    )


class TakeoverStore:
    """The durable hold table: one live row per held agent.

    The row is the enforcement: while it exists, the turn loop
    (wired with :meth:`is_held`) refuses new work for the
    agent. Releasing deletes the row; the ledger keeps the
    history, so this table never needs a ``released_at`` column.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)

    def create_schema(self) -> None:
        """Apply migration 0026 through the migration runner.

        Idempotent; safe to call before every test. Test
        fixtures apply only migration 0001, so live-view tests
        call this themselves; a database that already records
        0027 applies nothing here.
        """
        from ..migrations import apply_migrations

        apply_migrations(self.dsn, target=TAKEOVER_MIGRATION)

    def _connect(self):
        return psycopg.connect(self.dsn, row_factory=dict_row)

    def hold(
        self, agent_id: str, held_by_user_id: str, *, reason: str = "", task_id: str | None = None
    ) -> TakeoverHold:
        """Hold ``agent_id``; raises 409 when it is already held."""
        if not isinstance(agent_id, str) or not agent_id:
            raise ValueError("agent_id must be a non-empty string")
        if not isinstance(held_by_user_id, str) or not held_by_user_id:
            raise ValueError("held_by_user_id must be a non-empty string")
        with self._connect() as conn, conn.cursor() as cur:
            try:
                cur.execute(
                    "INSERT INTO takeover_holds "
                    "(agent_id, held_by_user_id, reason, task_id) "
                    "VALUES (%s, %s, %s, %s) RETURNING *",
                    (agent_id, held_by_user_id, reason, task_id),
                )
                row = cur.fetchone()
                conn.commit()
            except UniqueViolation:
                conn.rollback()
                existing = self.get_hold(agent_id)
                raise TakeoverError(
                    f"agent {agent_id!r} is already held"
                    + (f" by {existing.held_by_user_id}" if existing else ""),
                    status=409,
                ) from None
        return _row_to_hold(row)

    def release(self, agent_id: str) -> TakeoverHold | None:
        """Release ``agent_id``; the released hold, or None."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "DELETE FROM takeover_holds WHERE agent_id = %s RETURNING *",
                (agent_id,),
            )
            row = cur.fetchone()
            conn.commit()
        return _row_to_hold(row) if row is not None else None

    def is_held(self, agent_id: str) -> bool:
        """True when ``agent_id`` has a live hold row."""
        return self.get_hold(agent_id) is not None

    def get_hold(self, agent_id: str) -> TakeoverHold | None:
        """The live hold for ``agent_id``, or None."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute("SELECT * FROM takeover_holds WHERE agent_id = %s", (agent_id,))
            row = cur.fetchone()
        return _row_to_hold(row) if row is not None else None

    def holds_for_user(self, user_id: str) -> list[TakeoverHold]:
        """Live holds placed by one user, oldest first."""
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM takeover_holds WHERE held_by_user_id = %s ORDER BY held_at ASC",
                (user_id,),
            )
            rows = cur.fetchall()
        return [_row_to_hold(row) for row in rows]


# -- the frame -------------------------------------------------------------


def _event_time_iso(event: dict) -> str:
    created_at = event.get("created_at")
    if isinstance(created_at, datetime):
        return created_at.astimezone(UTC).isoformat()
    if isinstance(created_at, (int, float)):
        return datetime.fromtimestamp(created_at, tz=UTC).isoformat()
    return str(created_at)


def _frame_event(event: dict) -> dict:
    """One ledger event, JSON-safe, in replay shape (P-10 replays these)."""
    content = event.get("content") or {}
    return {
        "id": str(event.get("id", "")),
        "type": event.get("type", ""),
        "task_id": event.get("task_id", ""),
        "agent_id": event.get("agent_id", ""),
        "subagent_id": event.get("subagent_id"),
        "created_at": _event_time_iso(event),
        "content": content,
        "hash": event.get("hash", ""),
        "previous_hash": event.get("previous_hash", ""),
        "source": "ledger",
    }


def _live_tail_event(event: dict) -> dict:
    """One in-memory live event: same shape, marked ephemeral."""
    return {
        "id": "",
        "type": event.get("type", ""),
        "task_id": event.get("task_id", ""),
        "agent_id": event.get("agent_id", ""),
        "subagent_id": None,
        "created_at": _event_time_iso(event),
        "content": event.get("content") or {},
        "hash": "",
        "previous_hash": "",
        "source": "live",
    }


def _open_task_id(events_newest_first: list[dict]) -> str | None:
    """The newest task with a start event and no terminal event after it."""
    terminal_tasks = {
        event["task_id"]
        for event in events_newest_first
        if event.get("type") in TERMINAL_TASK_EVENTS and event.get("task_id")
    }
    for event in events_newest_first:
        if (
            event.get("type") == "task_created"
            and event.get("task_id")
            and event["task_id"] not in terminal_tasks
        ):
            return event["task_id"]
    return None


def _last_tool(events_newest_first: list[dict]) -> dict | None:
    """The newest tool call/completion, or None when the bot ran none."""
    for event in events_newest_first:
        if event.get("type") in ("tool_called", "tool_completed"):
            content = event.get("content") or {}
            return {
                "tool": content.get("tool", ""),
                "task_id": event.get("task_id", ""),
                "type": event.get("type", ""),
                "created_at": _event_time_iso(event),
            }
    return None


@dataclass
class LiveFrame:
    """One live-view frame: what the bot is doing right now.

    ``status`` is ``held`` (a human has taken over), ``working``
    (an open task with a recent event), or ``idle`` (no open
    task, or an open task gone quiet). ``events`` are the
    replayable ledger events, oldest first; ``live_tail`` is the
    ephemeral in-memory tail, newest last. ``screenshot`` stays
    None until a backend that can produce frames exists (see the
    module docstring); P-10 replays ``events``.
    """

    agent_id: str
    status: str
    hold: TakeoverHold | None = None
    current_task_id: str | None = None
    last_tool: dict | None = None
    last_event_at: str | None = None
    events: list[dict] = field(default_factory=list)
    live_tail: list[dict] = field(default_factory=list)
    screenshot: str | None = None
    generated_at: str = ""

    def to_dict(self) -> dict:
        """JSON-safe dict of this frame."""
        return {
            "agent_id": self.agent_id,
            "status": self.status,
            "hold": self.hold.to_dict() if self.hold is not None else None,
            "current_task_id": self.current_task_id,
            "last_tool": self.last_tool,
            "last_event_at": self.last_event_at,
            "events": self.events,
            "live_tail": self.live_tail,
            "screenshot": self.screenshot,
            "generated_at": self.generated_at,
        }


class FrameSource(Protocol):
    """A backend that can produce one live-view frame.

    The protocol is the capture seam: a real browser/desktop
    backend implements ``capture_frame`` and returns a frame
    with a ``screenshot``; until then :class:`LedgerFrameSource`
    is the only implementation and ``screenshot`` stays None.
    """

    def capture_frame(self, agent_id: str, *, task_id: str | None = None) -> LiveFrame | None:
        """The current frame for ``agent_id``, or None when unknown."""


class LedgerFrameSource:
    """Frame source backed by the ledger (the only one today).

    Builds the frame from ledger events, so every frame is
    replayable by construction: P-10 replays the frame's
    ``events`` list. Ownership is checked here (``user_id``
    must own ``agent_id``), so a frame source never serves
    another user's bot.
    """

    def __init__(self, dsn: str | None = None) -> None:
        self.dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)

    def capture_frame(self, agent_id: str, *, task_id: str | None = None) -> LiveFrame | None:
        """The frame for ``agent_id`` (no ownership check here).

        Ownership lives in :func:`build_live_frame`, which is
        the user-facing entry point; this method is the raw
        capture seam for a caller that already checked it.
        """
        return build_live_frame("", agent_id, dsn=self.dsn, check_owner=False, task_id=task_id)


def _require_owner(user_id: str, agent_id: str, *, dsn: str | None) -> None:
    """403 when ``agent_id`` is not one of ``user_id``'s agents."""
    if not isinstance(user_id, str) or not user_id:
        raise ValueError("user_id must be a non-empty string")
    if not isinstance(agent_id, str) or not agent_id:
        raise ValueError("agent_id must be a non-empty string")
    if agent_id not in user_agent_ids(user_id, dsn=dsn):
        raise TakeoverError(f"agent {agent_id!r} does not belong to this user", status=403)


def build_live_frame(
    user_id: str,
    agent_id: str,
    *,
    dsn: str | None = None,
    ledger: LedgerType | None = None,
    store: TakeoverStore | None = None,
    live_events: list[dict] | None = None,
    limit: int = MAX_FRAME_EVENTS,
    now: datetime | None = None,
    check_owner: bool = True,
    task_id: str | None = None,
) -> LiveFrame:
    """The live frame for one agent: status, task, tools, events.

    Reads the agent's newest ledger events (capped at
    ``limit``), derives the status and the open task, and
    merges the PWA's in-memory live tail when ``live_events``
    is given (``app.state.event``). ``task_id`` narrows the
    frame to one task (the ledger events of other tasks are
    left out); without it the frame covers the agent's recent
    work across tasks. Raises :class:`TakeoverError` (403)
    when the agent is not the user's, unless ``check_owner``
    is False (the raw capture seam).
    """
    resolved_dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    if check_owner:
        _require_owner(user_id, agent_id, dsn=resolved_dsn)
    if not isinstance(limit, int) or isinstance(limit, bool) or limit < 1:
        raise ValueError("limit must be a positive integer")
    ledger = ledger if ledger is not None else Ledger(resolved_dsn)
    store = store if store is not None else TakeoverStore(resolved_dsn)
    now = now or datetime.now(UTC)
    events = ledger.events_for_agent(agent_id, limit=limit)
    if task_id is not None:
        events = [event for event in events if event.get("task_id") == task_id]
    hold = store.get_hold(agent_id)
    open_task = _open_task_id(events)
    last_tool = _last_tool(events)
    last_event_at = _event_time_iso(events[0]) if events else None
    if hold is not None:
        status = "held"
    elif open_task is not None and events:
        newest = events[0].get("created_at")
        if isinstance(newest, datetime):
            age = (now - newest).total_seconds()
        else:
            age = LIVE_WINDOW_SECONDS + 1
        status = "working" if age <= LIVE_WINDOW_SECONDS else "idle"
    else:
        status = "idle"
    live_tail: list[dict] = []
    if live_events:
        mine = [event for event in live_events if event.get("agent_id") == agent_id]
        if task_id is not None:
            mine = [event for event in mine if event.get("task_id") == task_id]
        live_tail = [_live_tail_event(event) for event in mine[-LIVE_TAIL_LIMIT:]]
    return LiveFrame(
        agent_id=agent_id,
        status=status,
        hold=hold,
        current_task_id=open_task,
        last_tool=last_tool,
        last_event_at=last_event_at,
        events=[_frame_event(event) for event in reversed(events)],
        live_tail=live_tail,
        screenshot=None,
        generated_at=now.isoformat(),
    )


# -- takeover --------------------------------------------------------------


def takeover_for_agent(
    user_id: str,
    agent_id: str,
    *,
    dsn: str | None = None,
    ledger: LedgerType | None = None,
    store: TakeoverStore | None = None,
    lifecycle: SubagentLifecycle | None = None,
    pool: SubagentPool | None = None,
    actor: str | None = None,
    reason: str = "",
) -> dict:
    """Take over one agent: hold it, interrupt live work, record it.

    Steps, in order: ownership (403 when the agent is not the
    user's), interrupt the live children (lifecycle cancel, then
    the PWA pool's per-agent stop — children only, the agent's
    own tokens are NOT revoked, so release can resume it),
    write the hold row (409 when already held), and append the
    ``takeover_started`` ledger event. The ledger event hangs
    off the agent's open task when there is one, else off the
    agent id itself. Returns the takeover report.
    """
    resolved_dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    _require_owner(user_id, agent_id, dsn=resolved_dsn)
    ledger = ledger if ledger is not None else Ledger(resolved_dsn)
    store = store if store is not None else TakeoverStore(resolved_dsn)
    actor = actor or user_id
    if not isinstance(reason, str):
        raise ValueError("reason must be a string")
    # Already held: refuse before touching anything live, so a
    # second press never cancels work it did not take over.
    # (store.hold below is still the race guard for two presses
    # landing at the same instant.)
    existing = store.get_hold(agent_id)
    if existing is not None:
        raise TakeoverError(
            f"agent {agent_id!r} is already held by {existing.held_by_user_id}",
            status=409,
        )
    # Interrupt first, hold second: a failed interrupt leaves
    # nothing changed, so a failed press never claims a hold it
    # did not take. The hold row is the enforcement for new
    # work; the ledger event is the audit trail.
    interrupted: list[dict[str, Any]] = []
    lifecycle_wired = False
    if lifecycle is not None:
        lifecycle_wired = True
        interrupted = lifecycle.cancel_all_for_agent(agent_id)
    pool_stopped = 0
    pool_wired = False
    if pool is not None:
        pool_wired = True
        pool_stopped = pool.stop_all_for_agent(agent_id)["stopped"]
    frame = build_live_frame(
        user_id, agent_id, dsn=resolved_dsn, ledger=ledger, store=store, check_owner=False
    )
    hold = store.hold(agent_id, user_id, reason=reason, task_id=frame.current_task_id)
    event = ledger.append_event(
        task_id=frame.current_task_id or agent_id,
        agent_id=agent_id,
        event_type="takeover_started",
        content={
            "user_id": user_id,
            "actor": actor,
            "reason": reason,
            "held_by": user_id,
            "interrupted_subagents": interrupted,
            "pool_stopped": pool_stopped,
        },
    )
    return {
        "agent_id": agent_id,
        "status": "held",
        "held_by": user_id,
        "actor": actor,
        "reason": reason,
        "task_id": frame.current_task_id,
        "held_at": hold.held_at,
        "interrupted_subagents": interrupted,
        "pool_stopped": pool_stopped,
        "lifecycle_wired": lifecycle_wired,
        "pool_wired": pool_wired,
        "ledger_event_id": event["id"],
        "audit_trail_url": AUDIT_TRAIL_URL,
    }


def release_takeover(
    user_id: str,
    agent_id: str,
    *,
    dsn: str | None = None,
    ledger: LedgerType | None = None,
    store: TakeoverStore | None = None,
    actor: str | None = None,
) -> dict:
    """Release a takeover: delete the hold row, record the release.

    The agent resumes autonomous work on its next turn; the
    release itself is a ``takeover_released`` ledger event, so
    the hold's whole life is in the audit trail. Raises
    :class:`TakeoverError` (403) when the agent is not the
    user's, (404) when nothing is held.
    """
    resolved_dsn = dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN)
    _require_owner(user_id, agent_id, dsn=resolved_dsn)
    ledger = ledger if ledger is not None else Ledger(resolved_dsn)
    store = store if store is not None else TakeoverStore(resolved_dsn)
    actor = actor or user_id
    hold = store.get_hold(agent_id)
    if hold is None:
        raise TakeoverError(f"agent {agent_id!r} is not held", status=404)
    store.release(agent_id)
    event = ledger.append_event(
        task_id=hold.task_id or agent_id,
        agent_id=agent_id,
        event_type="takeover_released",
        content={
            "user_id": user_id,
            "actor": actor,
            "held_by": hold.held_by_user_id,
            "held_at": hold.held_at,
            "reason": hold.reason,
        },
    )
    return {
        "agent_id": agent_id,
        "status": "released",
        "held_by": hold.held_by_user_id,
        "actor": actor,
        "held_at": hold.held_at,
        "released_at": datetime.now(UTC).isoformat(),
        "reason": hold.reason,
        "ledger_event_id": event["id"],
        "audit_trail_url": AUDIT_TRAIL_URL,
    }
