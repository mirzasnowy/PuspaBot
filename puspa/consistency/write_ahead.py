"""Write-ahead step records on top of the Postgres ledger (E-06).

The ledger (``puspa.ledger``) is the authority for task status.
Every step that can have a side effect is recorded twice, both
times as ordinary ledger events inside the closed set of 31 types,
so the hash chain and the existing tooling keep working unchanged:

- before the effect runs: a ``tool_called`` event with
  ``kind == "step_intent"`` and the step's idempotency key
  (``task_id + step number + hash of the arguments``);
- after the effect runs: a ``tool_completed`` event with
  ``kind == "step_result"`` and the same key.

A step with an intent but no result is *uncertain*: the effect
may or may not have happened. Recovery (see
``puspa.consistency.runner.recover_task``) retries uncertain
read-only steps and never auto-retries uncertain outbound steps.

Task status is derived from the ledger, never from a second
store: the last terminal event wins (``task_completed``,
``task_failed``, ``budget_exhausted``, ``task_stopped``); a task
with a ``task_created`` event and no terminal event is
``running``. A ``task_stopped`` event whose content carries
``{"interrupted": true}`` is the ``interrupted`` status the
startup reconciler writes for tasks whose heartbeat went stale.
"""

from __future__ import annotations

from datetime import UTC, datetime

import psycopg
from psycopg.rows import dict_row

from puspa.ledger import Ledger

from .idempotency import args_hash, idempotency_key

#: Terminal event types and the status each one means.
TERMINAL_STATUS = {
    "task_completed": "completed",
    "task_failed": "failed",
    "budget_exhausted": "budget_exceeded",
    "task_stopped": "stopped",
}

#: Statuses a task can be in, as derived from the ledger.
TASK_STATUSES = (
    "running",
    "interrupted",
    "completed",
    "failed",
    "budget_exceeded",
    "stopped",
    "unknown",
)


def _jsonable(value: object) -> object:
    """Best-effort JSON-able copy of a step result for the ledger.

    Small plain values pass through; anything else becomes a
    short ``repr`` so recording a result can never fail the step
    it describes.
    """
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    if isinstance(value, dict):
        return {str(key): _jsonable(item) for key, item in value.items()}
    if isinstance(value, (list, tuple)):
        return [_jsonable(item) for item in value]
    text = repr(value)
    return text if len(text) <= 500 else text[:497] + "..."


class WriteAheadLog:
    """Write-ahead intents/results plus status derivation, on a Ledger."""

    def __init__(self, ledger: Ledger) -> None:
        self._ledger = ledger

    # -- intents and results --------------------------------------------

    def record_task_created(self, task_id: str, agent_id: str, *, purpose: str = "") -> dict:
        """Append ``task_created``; the task exists from this point on."""
        return self._ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type="task_created",
            content={"task_id": task_id, "agent_id": agent_id, "purpose": purpose},
        )

    def record_intent(
        self,
        task_id: str,
        agent_id: str,
        step_number: int,
        step_name: str,
        args: dict,
        *,
        outbound: bool,
        subagent_id: str | None = None,
    ) -> dict:
        """Append the "about to run" event for one step.

        Must be called *before* the effect runs. Returns the
        stored event (which carries the idempotency key).
        """
        return self._ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            subagent_id=subagent_id,
            event_type="tool_called",
            content={
                "kind": "step_intent",
                "step": step_number,
                "step_name": step_name,
                "idempotency_key": idempotency_key(task_id, step_number, args),
                "args_hash": args_hash(args),
                "outbound": bool(outbound),
            },
        )

    def record_result(
        self,
        task_id: str,
        agent_id: str,
        step_number: int,
        step_name: str,
        args: dict,
        result: object,
        *,
        subagent_id: str | None = None,
    ) -> dict:
        """Append the "ran, here is the outcome" event for one step.

        Must be called *after* the effect runs, with the same
        arguments the intent carried, so the two events share one
        idempotency key.
        """
        return self._ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            subagent_id=subagent_id,
            event_type="tool_completed",
            content={
                "kind": "step_result",
                "step": step_number,
                "step_name": step_name,
                "idempotency_key": idempotency_key(task_id, step_number, args),
                "result": _jsonable(result),
            },
        )

    def record_heartbeat(self, task_id: str, agent_id: str, *, step: str | None = None) -> dict:
        """Append a ``heartbeat`` event: this task is still alive."""
        content: dict[str, object] = {"task_id": task_id}
        if step is not None:
            content["step"] = step
        return self._ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type="heartbeat",
            content=content,
        )

    def mark_interrupted(
        self,
        task_id: str,
        agent_id: str,
        *,
        reason: str,
        last_heartbeat: datetime | None = None,
    ) -> dict:
        """Append ``task_stopped`` with ``interrupted: true``.

        The ledger stays append-only: interruption is a new event,
        never an edit of the running task's rows.
        """
        content: dict[str, object] = {"interrupted": True, "reason": reason}
        if last_heartbeat is not None:
            content["last_heartbeat"] = last_heartbeat.astimezone(UTC).isoformat()
        return self._ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type="task_stopped",
            content=content,
        )

    def mark_completed(self, task_id: str, agent_id: str) -> dict:
        """Append ``task_completed``."""
        return self._ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type="task_completed",
            content={"task_id": task_id, "agent_id": agent_id},
        )

    def mark_failed(self, task_id: str, agent_id: str, *, reason: str) -> dict:
        """Append ``task_failed`` with the reason."""
        return self._ledger.append_event(
            task_id=task_id,
            agent_id=agent_id,
            event_type="task_failed",
            content={"task_id": task_id, "reason": reason},
        )

    # -- reading ---------------------------------------------------------

    def task_status(self, task_id: str) -> str:
        """Derive one task's status from its ledger events.

        The last terminal event wins. A task with a
        ``task_created`` event and no terminal event is
        ``running``; a task with no events at all is ``unknown``.
        A ``task_stopped`` event marked ``interrupted`` reads as
        ``interrupted``; any other ``task_stopped`` reads as
        ``stopped``.
        """
        status = "unknown"
        for event in self._ledger.events(task_id):
            event_type = event["type"]
            if event_type == "task_created" and status == "unknown":
                status = "running"
            elif event_type in TERMINAL_STATUS:
                if event_type == "task_stopped" and event["content"].get("interrupted"):
                    status = "interrupted"
                else:
                    status = TERMINAL_STATUS[event_type]
        return status

    def find_uncertain_steps(self, task_id: str) -> list[dict]:
        """Intents with no matching result, in step order.

        Each entry carries the step number and name, the
        idempotency key, whether the step is outbound, and the
        intent event's id and time. An empty list means every
        recorded intent has its result — or no step ran at all.
        """
        intents: dict[str, dict] = {}
        results: set[str] = set()
        for event in self._ledger.events(task_id):
            content = event["content"]
            if event["type"] == "tool_called" and content.get("kind") == "step_intent":
                key = str(content.get("idempotency_key", ""))
                intents[key] = {
                    "step": content.get("step"),
                    "step_name": content.get("step_name"),
                    "idempotency_key": key,
                    "outbound": bool(content.get("outbound", False)),
                    "intent_event_id": event["id"],
                    "intent_created_at": event["created_at"],
                }
            elif event["type"] == "tool_completed" and content.get("kind") == "step_result":
                results.add(str(content.get("idempotency_key", "")))
        uncertain = [entry for key, entry in intents.items() if key not in results]
        uncertain.sort(key=lambda entry: entry["step"] if isinstance(entry["step"], int) else -1)
        return uncertain

    def scan_tasks(self) -> list[dict]:
        """One summary row per task known to the ledger.

        Each row: ``task_id``, ``agent_id`` (from ``task_created``),
        ``created_at``, ``last_heartbeat`` (or ``None``), and
        ``terminal`` (the last terminal status, or ``None`` while
        the task is still running). Used by the startup
        reconciler; it reads the ledger directly because the
        Ledger API is per-task.
        """
        with (
            psycopg.connect(self._ledger.dsn, row_factory=dict_row) as conn,
            conn.cursor() as cur,
        ):
            cur.execute(
                "SELECT task_id, agent_id, type, content, created_at"
                " FROM events ORDER BY created_at ASC, id ASC"
            )
            rows = cur.fetchall()
        tasks: dict[str, dict] = {}
        for row in rows:
            task_id = row["task_id"]
            entry = tasks.setdefault(
                task_id,
                {
                    "task_id": task_id,
                    "agent_id": row["agent_id"],
                    "created_at": row["created_at"],
                    "last_heartbeat": None,
                    "terminal": None,
                },
            )
            event_type = row["type"]
            if event_type == "task_created":
                entry["created_at"] = row["created_at"]
                entry["agent_id"] = row["agent_id"]
            elif event_type == "heartbeat":
                entry["last_heartbeat"] = row["created_at"]
            elif event_type in TERMINAL_STATUS:
                if event_type == "task_stopped" and (row["content"] or {}).get("interrupted"):
                    entry["terminal"] = "interrupted"
                else:
                    entry["terminal"] = TERMINAL_STATUS[event_type]
        return sorted(tasks.values(), key=lambda entry: entry["created_at"])

    def running_tasks(
        self, *, stale_after_seconds: float, now: datetime | None = None
    ) -> list[dict]:
        """Running tasks whose last heartbeat is older than the threshold.

        A task counts as running when it has a ``task_created``
        event and no terminal event. The last sign of life is its
        newest ``heartbeat`` event, falling back to its creation
        time when it never beat. Each row carries ``task_id``,
        ``agent_id``, ``last_heartbeat`` and ``stale_seconds``.
        """
        moment = now or datetime.now(UTC)
        stale: list[dict] = []
        for entry in self.scan_tasks():
            if entry["terminal"] is not None:
                continue
            last = entry["last_heartbeat"] or entry["created_at"]
            age = (moment - last).total_seconds()
            if age > stale_after_seconds:
                stale.append(
                    {
                        "task_id": entry["task_id"],
                        "agent_id": entry["agent_id"],
                        "last_heartbeat": last,
                        "stale_seconds": age,
                    }
                )
        return stale
