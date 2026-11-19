"""Kill-drill worker subprocess (E-06).

Runs a fixed three-step task through ``ConsistentStepRunner``
and kills itself with SIGKILL at the boundary named by the
environment, so the drill harness can crash the process at every
step boundary and check the invariants afterwards.

Environment (all set by ``puspa.consistency.kill_drill``):

- ``PUSPA_KILL_DRILL_DSN`` — Postgres DSN for the ledger.
- ``PUSPA_KILL_DRILL_TASK_ID`` — task id for this run.
- ``PUSPA_KILL_DRILL_AGENT_ID`` — agent id (default ``"puspa"``).
- ``PUSPA_KILL_DRILL_KILL_AT`` — boundary to die at, one of
  ``before_intent``, ``after_intent``, ``after_effect``,
  ``after_result``, ``checkpoint``.
- ``PUSPA_KILL_DRILL_KILL_STEP`` — step index to die at (default 1).
- ``PUSPA_KILL_DRILL_EFFECT_LOG`` — file the outbound step
  appends to: one JSON line per performed effect, fsynced, so
  the effect log is the simulated outside world. Read-only steps
  never touch it.

Run as ``python -m puspa.consistency.worker``. Exits 0 only when
no kill was requested at a boundary the run actually reaches.
"""

from __future__ import annotations

import json
import os
import signal
import sys
from pathlib import Path

from puspa.consistency.idempotency import idempotency_key
from puspa.consistency.runner import ConsistentStepRunner, StepSpec
from puspa.ledger import Ledger


def drill_steps() -> list[StepSpec]:
    """The drill's three steps: read, outbound send, read."""
    return [
        StepSpec(
            name="read_catalog",
            args={"type": "read", "query": "price of rice cooker"},
            outbound=False,
        ),
        StepSpec(
            name="send_quote",
            args={"type": "send", "to": "seller", "text": "quote"},
            outbound=True,
        ),
        StepSpec(
            name="summarize",
            args={"type": "read", "query": "summarize quotes"},
            outbound=False,
        ),
    ]


def drill_executor(effect_log: Path, task_id: str):
    """Executor for the drill steps.

    The outbound step records its effect in the effect log (the
    simulated outside world) and fsyncs, so a kill after the
    effect still leaves exactly one effect behind. Read-only
    steps compute without touching the log.
    """

    def execute(step: StepSpec, step_index: int) -> dict:
        if step.outbound:
            key = idempotency_key(task_id, step_index, step.args)
            with effect_log.open("a", encoding="utf-8") as handle:
                handle.write(json.dumps({"idempotency_key": key, "step": step.name}) + "\n")
                handle.flush()
                os.fsync(handle.fileno())
            return {"type": "sent", "key": key}
        return {"type": "computed", "step": step.name}

    return execute


def main() -> int:
    dsn = os.environ["PUSPA_KILL_DRILL_DSN"]
    task_id = os.environ["PUSPA_KILL_DRILL_TASK_ID"]
    agent_id = os.environ.get("PUSPA_KILL_DRILL_AGENT_ID", "puspa")
    kill_at = os.environ["PUSPA_KILL_DRILL_KILL_AT"]
    kill_step = int(os.environ.get("PUSPA_KILL_DRILL_KILL_STEP", "1"))
    effect_log = Path(os.environ["PUSPA_KILL_DRILL_EFFECT_LOG"])
    ledger = Ledger(dsn)

    def on_boundary(boundary: str, step_index: int) -> None:
        if boundary == kill_at and step_index == kill_step:
            sys.stdout.flush()
            sys.stderr.flush()
            os.kill(os.getpid(), signal.SIGKILL)

    runner = ConsistentStepRunner(ledger, on_boundary=on_boundary)
    runner.run_task(task_id, agent_id, drill_steps(), drill_executor(effect_log, task_id))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
