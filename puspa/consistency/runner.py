"""Write-ahead step execution and crash recovery (E-06).

``ConsistentStepRunner`` runs a list of steps with the write-ahead
protocol from ``write_ahead``: intent before the effect, result
after it, a heartbeat plus a checkpoint boundary after each step.
``on_boundary`` is a test seam — the kill drill uses it to kill
the worker process at an exact boundary (see
``puspa.consistency.kill_drill``).

``recover_task`` is the recovery policy, and it is deliberately
asymmetric:

- an uncertain *read-only* step is retried once (a read has no
  side effect to duplicate), and its result is recorded under the
  same idempotency key;
- an uncertain *outbound* step is NEVER retried automatically:
  the effect may already have happened, so retrying could send
  twice. It is reported in ``needs_user_decision`` for a human to
  resolve.

After recovery a task is ``completed`` only when every step has a
recorded result; otherwise it is marked ``interrupted`` and the
report says exactly which steps are unresolved.
"""

from __future__ import annotations

from collections.abc import Callable
from dataclasses import dataclass, field

from puspa.ledger import Ledger

from .idempotency import idempotency_key
from .write_ahead import WriteAheadLog

#: Boundaries the runner reports, in the order they happen per step.
BOUNDARIES = (
    "before_intent",
    "after_intent",
    "after_effect",
    "after_result",
    "checkpoint",
)


@dataclass
class StepSpec:
    """One step to run under write-ahead.

    ``outbound`` marks a step whose effect leaves the process
    (send, share, post, pay, delete): it is never auto-retried.
    Anything else is read-only and safe to retry after a crash.
    """

    name: str
    args: dict = field(default_factory=dict)
    outbound: bool = False

    @property
    def read_only(self) -> bool:
        return not self.outbound


#: ``executor(step, step_index) -> result``: performs the step's
#: effect (if any) and returns a JSON-able result dict.
Executor = Callable[[StepSpec, int], dict]

#: ``on_boundary(boundary, step_index)``: called at each boundary.
BoundaryHook = Callable[[str, int], None]


class ConsistentStepRunner:
    """Run steps with write-ahead intents, results, and heartbeats."""

    def __init__(self, ledger: Ledger, *, on_boundary: BoundaryHook | None = None) -> None:
        self._ledger = ledger
        self._wal = WriteAheadLog(ledger)
        self._on_boundary = on_boundary

    def _boundary(self, name: str, step_index: int) -> None:
        if self._on_boundary is not None:
            self._on_boundary(name, step_index)

    def run_task(
        self, task_id: str, agent_id: str, steps: list[StepSpec], executor: Executor
    ) -> dict:
        """Run every step; return ``{"task_id", "status", "steps"}``.

        Raises the executor's exception (after recording
        ``task_failed``) when a step fails; a process killed
        mid-step leaves an intent without a result, which
        ``recover_task`` resolves.
        """
        self._wal.record_task_created(task_id, agent_id)
        for index, step in enumerate(steps):
            self._boundary("before_intent", index)
            self._wal.record_intent(
                task_id, agent_id, index, step.name, step.args, outbound=step.outbound
            )
            self._boundary("after_intent", index)
            try:
                result = executor(step, index)
            except Exception as exc:
                self._wal.mark_failed(task_id, agent_id, reason=f"{type(exc).__name__}: {exc}")
                raise
            self._boundary("after_effect", index)
            self._wal.record_result(task_id, agent_id, index, step.name, step.args, result)
            self._boundary("after_result", index)
            self._wal.record_heartbeat(task_id, agent_id, step=step.name)
            self._boundary("checkpoint", index)
        self._wal.mark_completed(task_id, agent_id)
        return {"task_id": task_id, "status": "completed", "steps": len(steps)}


def _recorded_result_keys(ledger: Ledger, task_id: str) -> set[str]:
    """Idempotency keys that already have a recorded result."""
    keys: set[str] = set()
    for event in ledger.events(task_id):
        content = event["content"]
        if event["type"] == "tool_completed" and content.get("kind") == "step_result":
            keys.add(str(content.get("idempotency_key", "")))
    return keys


def recover_task(
    ledger: Ledger,
    task_id: str,
    agent_id: str,
    steps: list[StepSpec],
    executor: Executor,
) -> dict:
    """Recover one task after a crash; return a recovery report.

    Retries uncertain read-only steps once; never retries an
    uncertain outbound step. Marks the task ``completed`` only
    when every step has a recorded result, ``interrupted``
    otherwise. A task that already has a terminal event is left
    alone and reported as-is.
    """
    wal = WriteAheadLog(ledger)
    status = wal.task_status(task_id)
    if status in ("completed", "failed", "budget_exceeded", "stopped", "interrupted"):
        return {
            "task_id": task_id,
            "status": status,
            "uncertain": 0,
            "retried": [],
            "needs_user_decision": [],
            "already_terminal": True,
        }
    uncertain = wal.find_uncertain_steps(task_id)
    retried: list[str] = []
    needs_user_decision: list[dict] = []
    for entry in uncertain:
        step_index = entry["step"]
        step = (
            steps[step_index]
            if isinstance(step_index, int) and 0 <= step_index < len(steps)
            else None
        )
        if step is None:
            needs_user_decision.append({**entry, "reason": "no matching step in the plan anymore"})
            continue
        if step.read_only:
            try:
                result = executor(step, step_index)
            except Exception as exc:
                needs_user_decision.append(
                    {**entry, "reason": f"retry failed: {type(exc).__name__}: {exc}"}
                )
                continue
            wal.record_result(task_id, agent_id, step_index, step.name, step.args, result)
            retried.append(entry["idempotency_key"])
        else:
            needs_user_decision.append(
                {
                    **entry,
                    "reason": (
                        "outbound effect may already have happened;"
                        " never auto-retried, needs a human decision"
                    ),
                }
            )
    result_keys = _recorded_result_keys(ledger, task_id)
    all_done = all(
        idempotency_key(task_id, index, step.args) in result_keys
        for index, step in enumerate(steps)
    )
    if needs_user_decision or not all_done:
        wal.mark_interrupted(
            task_id,
            agent_id,
            reason="recovered after crash with unresolved steps",
        )
        final_status = "interrupted"
    else:
        wal.mark_completed(task_id, agent_id)
        final_status = "completed"
    return {
        "task_id": task_id,
        "status": final_status,
        "uncertain": len(uncertain),
        "retried": retried,
        "needs_user_decision": needs_user_decision,
        "already_terminal": False,
    }
