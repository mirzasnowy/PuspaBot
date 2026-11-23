"""Missions with checklist (V2-42, issue #219).

A mission is one multi-step goal the user gives the agent, worked
through in the background with a checklist the user can follow in
the PWA. Mechanism studied from nouverse/nouride missions
(FSL-1.1-ALv2, verified 22 Nov 2026) and rewritten as Puspa
Python, never verbatim; the TypeScript code itself was not taken.

This module is the store: create, read, list, cancel. Running a
step lives in ``puspa.missions.runner`` and waking a mission from
outside lives in ``puspa.missions.webhooks``.

A mission row holds its checklist as JSONB: one entry per step
(``title``, ``status``, ``result``, and an optional step-ask
``question`` with ``options`` and the user's ``answer``). Every
step outcome is also recorded in the hash-chained ledger, using
the closed event types (``task_created`` / ``task_completed`` /
``task_failed`` / ``task_stopped`` / ``approval_requested`` /
``approval_granted`` / ``approval_rejected`` /
``evidence_recorded``) with ``{"kind": "mission..."}`` content,
so no new event type is needed.
"""

from __future__ import annotations

import json
import os
import secrets
import uuid
from dataclasses import dataclass, field
from datetime import UTC, datetime

from puspa.db import connect as _db_connect
from puspa.db import dict_row as _db_dict_row

DEFAULT_DSN = None

#: Mission states. ``waiting`` means one step asked the user a
#: question and the mission pauses until it is answered.
MISSION_STATUSES = ("active", "waiting", "done", "failed", "stopped")

#: Step states. ``active`` is the step currently running or
#: waiting on its question; ``skipped`` is a step the user
#: declined at its step-ask.
STEP_STATUSES = ("pending", "active", "done", "failed", "skipped")


class MissionError(Exception):
    """A mission could not be created, found, or changed."""


def _now() -> datetime:
    return datetime.now(UTC)


def _connect(dsn=None):
    return _db_connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def create_schema(dsn=None) -> None:
    """Create the missions table (same shape as migration 0045).

    The migration is the source of truth; this is the safety net
    the PWA calls on first use, like the gallery and support
    stores, so a database the operator has not migrated yet still
    serves the missions screen instead of failing every request.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS missions ("
                " id TEXT PRIMARY KEY,"
                " user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,"
                " title TEXT NOT NULL,"
                " goal TEXT NOT NULL,"
                " status TEXT NOT NULL DEFAULT 'active'"
                " CHECK (status IN ('active', 'waiting', 'done', 'failed', 'stopped')),"
                " steps JSONB NOT NULL DEFAULT '[]'::jsonb,"
                " webhook_token TEXT NOT NULL UNIQUE,"
                " created_at TIMESTAMPTZ NOT NULL DEFAULT now(),"
                " updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            )
            cur.execute("CREATE INDEX IF NOT EXISTS idx_missions_user_id ON missions(user_id)")
        conn.commit()


@dataclass
class MissionStep:
    """One checklist entry of a mission."""

    title: str
    status: str = "pending"
    result: str | None = None
    question: str | None = None
    options: list[str] = field(default_factory=list)
    answer: str | None = None

    def to_dict(self) -> dict:
        return {
            "title": self.title,
            "status": self.status,
            "result": self.result,
            "question": self.question,
            "options": list(self.options),
            "answer": self.answer,
        }


@dataclass
class Mission:
    """One multi-step goal the agent works through."""

    id: str
    user_id: str
    title: str
    goal: str
    status: str = "active"
    steps: list[MissionStep] = field(default_factory=list)
    webhook_token: str = ""
    created_at: datetime = field(default_factory=_now)
    updated_at: datetime = field(default_factory=_now)

    def to_dict(self) -> dict:
        return {
            "id": self.id,
            "user_id": self.user_id,
            "title": self.title,
            "goal": self.goal,
            "status": self.status,
            "steps": [step.to_dict() for step in self.steps],
            "webhook_token": self.webhook_token,
            "created_at": self.created_at.isoformat(),
            "updated_at": self.updated_at.isoformat(),
        }


def _normalise_step(raw, index: int) -> MissionStep:
    """One user-supplied step (a title, or a dict) into a step.

    A dict step may carry a ``question`` with ``options``: the
    decision point of that step. The mission pauses there and
    asks the user instead of guessing (see ``runner``).
    """
    if isinstance(raw, str):
        title, question, options = raw.strip(), None, []
    elif isinstance(raw, dict):
        title = str(raw.get("title") or "").strip()
        question = raw.get("question")
        question = str(question).strip() if question is not None else None
        options = [str(o) for o in raw.get("options") or []]
    else:
        raise MissionError(f"step {index} must be a title or a dict, got {type(raw).__name__}")
    if not title:
        raise MissionError(f"step {index} needs a non-empty title")
    if question == "":
        question = None
    return MissionStep(title=title, question=question, options=options)


def _default_title(goal: str) -> str:
    """A few words for the mission, from the goal's first sentence."""
    first = goal.strip().split(".")[0].strip()
    return first[:80] if first else goal.strip()[:80]


def _row_to_mission(row: dict) -> Mission:
    return Mission(
        id=row["id"],
        user_id=str(row["user_id"]),
        title=row["title"],
        goal=row["goal"],
        status=row["status"],
        steps=[MissionStep(**step) for step in row["steps"] or []],
        webhook_token=row["webhook_token"],
        created_at=row["created_at"],
        updated_at=row["updated_at"],
    )


def _ledger(task_id: str, event_type: str, content: dict, *, dsn=None) -> None:
    """Append one mission event to the ledger.

    Deliberately loud: a mission step whose outcome cannot be
    recorded must fail the call, not vanish silently — the
    ledger is the mission's durable record.
    """
    from puspa.ledger import Ledger

    Ledger(dsn).append_event(
        task_id=task_id, agent_id="puspa", event_type=event_type, content=content
    )


def create_mission(
    user_id: str,
    goal: str,
    steps,
    *,
    title: str | None = None,
    dsn=None,
) -> Mission:
    """Give the agent a multi-step goal.

    ``steps`` is the checklist: titles (``"Cari tiga harga"``) or
    dicts (``{"title": ..., "question": ..., "options": [...]}``).
    At least one step is required: a goal with no steps is a plain
    task, not a mission. The mission starts ``active`` with a
    fresh webhook token; its creation is recorded in the ledger.
    """
    goal_text = str(goal or "").strip()
    if not goal_text:
        raise MissionError("create needs a non-empty goal")
    if not steps:
        raise MissionError("create needs at least one step")
    step_list = [_normalise_step(raw, i) for i, raw in enumerate(steps)]
    mission = Mission(
        id=f"misi-{uuid.uuid4().hex[:12]}",
        user_id=str(user_id),
        title=(title or "").strip() or _default_title(goal_text),
        goal=goal_text,
        steps=step_list,
        webhook_token=secrets.token_urlsafe(24),
    )
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO missions (id, user_id, title, goal, status, steps, webhook_token)"
                " VALUES (%s, %s, %s, %s, %s, %s::jsonb, %s)",
                (
                    mission.id,
                    mission.user_id,
                    mission.title,
                    mission.goal,
                    mission.status,
                    json.dumps([s.to_dict() for s in step_list]),
                    mission.webhook_token,
                ),
            )
        conn.commit()
    _ledger(
        mission.id,
        "task_created",
        {
            "kind": "mission",
            "mission_id": mission.id,
            "title": mission.title,
            "goal": mission.goal,
            "steps": [step.title for step in step_list],
        },
        dsn=dsn,
    )
    return mission


def get_mission(mission_id: str, user_id: str, *, dsn=None) -> Mission:
    """One mission of one user, or raise when it is not theirs."""
    with _connect(dsn) as conn, conn.cursor(row_factory=_db_dict_row) as cur:
        cur.execute(
            "SELECT id, user_id, title, goal, status, steps, webhook_token,"
            " created_at, updated_at FROM missions WHERE id = %s AND user_id = %s",
            (str(mission_id), str(user_id)),
        )
        row = cur.fetchone()
    if row is None:
        raise MissionError(f"no such mission: {mission_id!r}")
    return _row_to_mission(row)


def list_missions(user_id: str, *, dsn=None) -> list[Mission]:
    """All missions of one user, newest first."""
    with _connect(dsn) as conn, conn.cursor(row_factory=_db_dict_row) as cur:
        cur.execute(
            "SELECT id, user_id, title, goal, status, steps, webhook_token,"
            " created_at, updated_at FROM missions WHERE user_id = %s"
            " ORDER BY created_at DESC",
            (str(user_id),),
        )
        rows = cur.fetchall()
    return [_row_to_mission(row) for row in rows]


def _save(mission: Mission, *, dsn=None) -> Mission:
    """Write a changed mission back (steps + status + timestamp)."""
    mission.updated_at = _now()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "UPDATE missions SET status = %s, steps = %s::jsonb, updated_at = %s"
                " WHERE id = %s AND user_id = %s",
                (
                    mission.status,
                    json.dumps([s.to_dict() for s in mission.steps]),
                    mission.updated_at,
                    mission.id,
                    mission.user_id,
                ),
            )
        conn.commit()
    return mission


def cancel_mission(mission_id: str, user_id: str, *, dsn=None) -> Mission:
    """Stop a mission. A finished mission cannot be cancelled."""
    mission = get_mission(mission_id, user_id, dsn=dsn)
    if mission.status in ("done", "failed", "stopped"):
        raise MissionError(f"mission is already {mission.status}: {mission_id!r}")
    mission.status = "stopped"
    _save(mission, dsn=dsn)
    _ledger(
        mission.id,
        "task_stopped",
        {"kind": "mission", "mission_id": mission.id, "title": mission.title},
        dsn=dsn,
    )
    return mission
