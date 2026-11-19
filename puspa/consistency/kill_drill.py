"""Fault-injection kill drill (E-06, gate-12 evidence).

For each kill case (a step boundary plus a step index) this
harness spawns the drill worker as a subprocess, the worker
kills itself with SIGKILL at that boundary, and the harness
then recovers the task in-process and checks the invariants:

- the worker really died by SIGKILL (return code -9), not by
  finishing or by an error;
- no effect happened twice: the effect log holds each
  idempotency key at most once;
- no task is lost: after recovery the task has a terminal
  status (``interrupted`` or ``completed``);
- the ledger hash chain is still valid;
- an uncertain outbound step was never auto-retried: its effect
  count is 0 (killed before the effect) or 1 (killed after it),
  never more;
- every uncertain step left after recovery is outbound and is
  listed in the recovery report's ``needs_user_decision``.

Honest limit: the kill here is fault injection on a worker
process and harness that run on this VM. Verifying the same
invariants against a live container (kill -9 on the container
itself) waits for a Docker host.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import uuid
from pathlib import Path

from puspa.ledger import Ledger

from .idempotency import idempotency_key
from .runner import recover_task
from .worker import drill_executor, drill_steps
from .write_ahead import WriteAheadLog

#: (boundary, step index) pairs the drill crashes at. The five
#: boundaries at the outbound step cover every point where an
#: effect can be half-recorded; the two read-only cases prove
#: the recovery retry path, including a last-step crash that
#: recovers to ``completed``.
KILL_CASES: tuple[tuple[str, int], ...] = (
    ("before_intent", 1),
    ("after_intent", 1),
    ("after_effect", 1),
    ("after_result", 1),
    ("checkpoint", 1),
    ("after_intent", 0),
    ("after_effect", 2),
)


def _read_effects(effect_log: Path) -> list[dict]:
    if not effect_log.exists():
        return []
    return [
        json.loads(line)
        for line in effect_log.read_text(encoding="utf-8").splitlines()
        if line.strip()
    ]


def run_kill_drill(
    *,
    dsn: str,
    work_dir: str | Path,
    kill_cases: tuple[tuple[str, int], ...] = KILL_CASES,
) -> dict:
    """Run the drill for every kill case; return the full report.

    ``work_dir`` holds one effect log per case. The report is
    ``{"passed": bool, "cases": {label: {...}}}``; each case
    carries the worker return code, the recovery report, the
    final status, the effect counts, the chain check, and the
    list of invariant failures (empty when the case passed).
    """
    root = Path(__file__).resolve().parents[2]
    work_dir = Path(work_dir)
    work_dir.mkdir(parents=True, exist_ok=True)
    ledger = Ledger(dsn)
    wal = WriteAheadLog(ledger)
    steps = drill_steps()
    outbound_key_index = 1
    cases: dict[str, dict] = {}
    for boundary, step_index in kill_cases:
        label = f"{boundary}@step{step_index}"
        task_id = f"kill-drill-{uuid.uuid4().hex[:12]}"
        effect_log = work_dir / f"effects-{label}.jsonl"
        env = dict(os.environ)
        env.update(
            {
                "PUSPA_KILL_DRILL_DSN": dsn,
                "PUSPA_KILL_DRILL_TASK_ID": task_id,
                "PUSPA_KILL_DRILL_AGENT_ID": "puspa",
                "PUSPA_KILL_DRILL_KILL_AT": boundary,
                "PUSPA_KILL_DRILL_KILL_STEP": str(step_index),
                "PUSPA_KILL_DRILL_EFFECT_LOG": str(effect_log),
                "PYTHONPATH": str(root),
                "NO_PROXY": "localhost,127.0.0.1",
                "no_proxy": "localhost,127.0.0.1",
            }
        )
        proc = subprocess.run(
            [sys.executable, "-m", "puspa.consistency.worker"],
            check=False,  # the worker is expected to die by SIGKILL
            cwd=root,
            env=env,
            capture_output=True,
            text=True,
            timeout=120,
        )
        recovery = recover_task(
            ledger, task_id, "puspa", steps, drill_executor(effect_log, task_id)
        )
        effects = _read_effects(effect_log)
        keys = [effect["idempotency_key"] for effect in effects]
        chain_ok, broken = ledger.verify_chain(task_id)
        status = wal.task_status(task_id)
        remaining_uncertain = wal.find_uncertain_steps(task_id)
        outbound_key = idempotency_key(task_id, outbound_key_index, steps[1].args)
        failures: list[str] = []
        if proc.returncode != -signal.SIGKILL:
            failures.append(
                f"worker did not die by SIGKILL (returncode {proc.returncode},"
                f" stderr: {proc.stderr.strip()[-300:]})"
            )
        if len(keys) != len(set(keys)):
            failures.append(f"duplicate effect in the effect log: {keys}")
        if status not in ("interrupted", "completed"):
            failures.append(f"task lost after recovery: status is {status!r}")
        if not chain_ok:
            failures.append(f"hash chain broken at events {broken}")
        if keys.count(outbound_key) > 1:
            failures.append("outbound effect happened more than once")
        for entry in remaining_uncertain:
            if not entry["outbound"]:
                failures.append(f"read-only step left uncertain: {entry}")
            if entry["idempotency_key"] not in {
                item["idempotency_key"] for item in recovery["needs_user_decision"]
            }:
                failures.append(f"uncertain step missing from needs_user_decision: {entry}")
        cases[label] = {
            "passed": not failures,
            "failures": failures,
            "returncode": proc.returncode,
            "status": status,
            "recovery": recovery,
            "effect_count": len(effects),
            "outbound_effect_count": keys.count(outbound_key),
            "chain_ok": chain_ok,
        }
    return {"passed": all(case["passed"] for case in cases.values()), "cases": cases}
