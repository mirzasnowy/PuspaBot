"""Cassette replay for gate 12 (plan section 8.2).

A reference-task run is recorded to a JSONL cassette: one record
per task with the outcome (ok) and the step count. Wall time and
cost are deliberately NOT compared: time varies between runs and
cost is always 0.0 here. A replay re-runs the tasks and compares
outcome and steps per task. The gate target is 9 of 10 identical;
the tasks are deterministic, so a healthy run replays 10 of 10.
"""

from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass
from pathlib import Path

from .reference_tasks import REFERENCE_TASK_IDS, reference_tasks, run_reference_tasks

CASSETTE_VERSION = 1


def _outcome_hash(ok: bool, steps: int) -> str:
    payload = json.dumps({"ok": ok, "steps": steps}, sort_keys=True)
    return hashlib.sha256(payload.encode("utf-8")).hexdigest()[:16]


@dataclass(frozen=True)
class ReplayComparison:
    total: int
    identical: int
    differing: list[str]

    @property
    def passed_gate(self) -> bool:
        """Gate 12 target: at least 9 of 10 identical."""
        return self.identical >= 9 and self.total == 10


def record_run(path: str | Path, *, repetitions: int = 1) -> Path:
    """Run the reference tasks once per task and write a cassette.

    One record per task (the first repetition); repetitions only
    prove stability, they are not stored.
    """
    out_path = Path(path)
    runs = run_reference_tasks(repetitions=repetitions)
    firsts = {run.task_id: run for run in runs}
    lines = []
    for task_id in REFERENCE_TASK_IDS:
        run = firsts[task_id]
        lines.append(
            {
                "version": CASSETTE_VERSION,
                "task_id": task_id,
                "ok": run.ok,
                "steps": run.steps,
                "outcome_hash": _outcome_hash(run.ok, run.steps),
            }
        )
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text("\n".join(json.dumps(line) for line in lines) + "\n", encoding="utf-8")
    return out_path


def replay_run(path: str | Path, *, repetitions: int = 1) -> ReplayComparison:
    """Re-run the tasks and compare outcome and steps to a cassette."""
    cassette_path = Path(path)
    recorded = {}
    for line in cassette_path.read_text(encoding="utf-8").splitlines():
        if not line.strip():
            continue
        record = json.loads(line)
        recorded[record["task_id"]] = record
    fresh = {task.id: task.execute() for task in reference_tasks()}
    differing: list[str] = []
    identical = 0
    for task_id in REFERENCE_TASK_IDS:
        old = recorded.get(task_id)
        new = fresh[task_id]
        if old is None:
            differing.append(f"{task_id}: missing from cassette")
            continue
        if (
            old["ok"] == new.ok
            and old["steps"] == new.steps
            and old["outcome_hash"] == _outcome_hash(new.ok, new.steps)
        ):
            identical += 1
        else:
            differing.append(
                f"{task_id}: recorded ok={old['ok']} steps={old['steps']}, "
                f"replayed ok={new.ok} steps={new.steps}"
            )
    return ReplayComparison(total=len(REFERENCE_TASK_IDS), identical=identical, differing=differing)
