"""Batch trajectory runner (G12).

The hermes-agent ``batch_runner.py`` pattern, rewritten for
Puspa on top of the eval harness that already exists
(``puspa/evidence/eval_harness.py``): run a set of tasks,
record one trajectory per task, checkpoint incrementally to
``trajectories.jsonl`` (each finished task is appended, so a
killed run resumes with ``resume=True`` instead of starting
over), and aggregate tool usage across the batch.

A trajectory is the honest record of what ran — for harness
runs, a cassette replay (deterministic, no live calls); for
agent runs, whatever the injected agent callable returned.
``record_prompt_run`` is the agent path: it takes the agent's
own ``{"messages", "tool_calls", "final_state", "passed",
"score"}`` result and records it, it never invents a run.

Redaction before saving: trajectory step content passes through
``puspa.memory.trust.redact_secrets`` (the G7 redaction helper)
when redaction is on, so a secret that leaked into a transcript
never lands in a stored trajectory. The raw, unredacted
trajectory is never written to disk by this module.

The trajectory format itself (JSON + ShareGPT export) is
documented in ``docs/trajectory-format.md``.
"""

from __future__ import annotations

import json
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path

__all__ = [
    "BatchTrajectoryRunner",
    "Trajectory",
    "TrajectoryStep",
    "load_prompt_dataset",
    "redact_trajectory",
]

#: File the runner appends one trajectory per line to. The file
#: itself is the checkpoint: ``resume=True`` skips task ids
#: already present in it.
TRAJECTORIES_FILE = "trajectories.jsonl"
SUMMARY_FILE = "batch-summary.json"


@dataclass
class TrajectoryStep:
    """One step of a trajectory: a message, optionally with tool calls."""

    role: str  # "human" | "assistant" | "tool"
    content: str = ""
    tool_calls: list[dict] = field(default_factory=list)

    def to_dict(self) -> dict:
        return {"role": self.role, "content": self.content, "tool_calls": list(self.tool_calls)}


@dataclass
class Trajectory:
    """One recorded run of one task."""

    task_id: str
    steps: list[TrajectoryStep] = field(default_factory=list)
    final_state: dict = field(default_factory=dict)
    passed: bool = False
    score: float = 0.0
    usage: dict = field(default_factory=dict)
    started_at: str | None = None
    ended_at: str | None = None

    def to_dict(self) -> dict:
        return {
            "task_id": self.task_id,
            "steps": [step.to_dict() for step in self.steps],
            "final_state": dict(self.final_state),
            "passed": self.passed,
            "score": self.score,
            "usage": dict(self.usage),
            "started_at": self.started_at,
            "ended_at": self.ended_at,
        }

    @classmethod
    def from_dict(cls, data: dict) -> Trajectory:
        steps = [
            TrajectoryStep(
                role=str(step.get("role") or "assistant"),
                content=str(step.get("content") or ""),
                tool_calls=list(step.get("tool_calls") or []),
            )
            for step in data.get("steps") or []
        ]
        return cls(
            task_id=str(data.get("task_id") or "?"),
            steps=steps,
            final_state=dict(data.get("final_state") or {}),
            passed=bool(data.get("passed")),
            score=float(data.get("score") or 0.0),
            usage=dict(data.get("usage") or {}),
            started_at=data.get("started_at"),
            ended_at=data.get("ended_at"),
        )

    def to_sharegpt(self) -> list[dict]:
        """ShareGPT export: the hermes trajectory training format.

        ``human`` for prompts, ``gpt`` for assistant turns,
        ``function`` for tool outputs. Tool calls ride on the
        assistant turn that made them, as ``function_call`` items
        (see ``docs/trajectory-format.md``).
        """
        turns: list[dict] = []
        for step in self.steps:
            if step.role == "human":
                turns.append({"from": "human", "value": step.content})
            elif step.role == "tool":
                turns.append({"from": "function", "value": step.content})
            else:
                turn: dict = {"from": "gpt", "value": step.content}
                if step.tool_calls:
                    turn["function_call"] = list(step.tool_calls)
                turns.append(turn)
        return turns


def _redact_text(text: str) -> str:
    from puspa.memory.trust import redact_secrets

    return redact_secrets(text)


def redact_trajectory(trajectory: Trajectory) -> Trajectory:
    """A copy of the trajectory with secrets redacted from content.

    Tool-call arguments are redacted as JSON text and parsed
    back; an argument that does not survive the round trip is
    dropped rather than stored raw.
    """
    steps = []
    for step in trajectory.steps:
        calls = []
        for call in step.tool_calls:
            try:
                raw = json.dumps(call, ensure_ascii=False, sort_keys=True)
                calls.append(json.loads(_redact_text(raw)))
            except (TypeError, ValueError):
                continue
        steps.append(
            TrajectoryStep(
                role=step.role,
                content=_redact_text(step.content),
                tool_calls=calls,
            )
        )
    return Trajectory(
        task_id=trajectory.task_id,
        steps=steps,
        final_state=dict(trajectory.final_state),
        passed=trajectory.passed,
        score=trajectory.score,
        usage=dict(trajectory.usage),
        started_at=trajectory.started_at,
        ended_at=trajectory.ended_at,
    )


def load_prompt_dataset(path: str | Path) -> list[dict]:
    """Load a JSONL prompt dataset: one ``{"prompt": ...}`` per line.

    Each record needs ``prompt`` (string) and may carry
    ``task_id`` and ``metadata``. Blank lines are skipped; a
    record without a prompt raises ``ValueError`` naming the
    line, so a bad dataset fails loudly instead of running
    half a batch.
    """
    path = Path(path)
    records: list[dict] = []
    for number, line in enumerate(path.read_text(encoding="utf-8").splitlines(), start=1):
        if not line.strip():
            continue
        try:
            record = json.loads(line)
        except json.JSONDecodeError as exc:
            raise ValueError(f"{path}:{number}: invalid JSON: {exc}") from exc
        if not isinstance(record, dict) or not isinstance(record.get("prompt"), str):
            raise ValueError(f"{path}:{number}: record needs a string 'prompt'")
        records.append(record)
    return records


class BatchTrajectoryRunner:
    """Run tasks through the eval harness, recording trajectories.

    ``harness`` is an ``EvalHarness`` (default: a fresh one).
    ``output_dir`` receives ``trajectories.jsonl`` (one
    trajectory per line, appended per finished task) and
    ``batch-summary.json`` (written at the end of ``run``).
    """

    def __init__(self, *, harness=None, output_dir: str | Path, redaction: bool = True) -> None:
        from puspa.evidence.eval_harness import EvalHarness

        self.harness = harness if harness is not None else EvalHarness()
        self.output_dir = Path(output_dir)
        self.redaction = redaction

    def _trajectories_path(self) -> Path:
        return self.output_dir / TRAJECTORIES_FILE

    def _recorded_task_ids(self) -> set[str]:
        path = self._trajectories_path()
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except FileNotFoundError:
            return set()
        found: set[str] = set()
        for line in lines:
            if not line.strip():
                continue
            try:
                found.add(str(json.loads(line).get("task_id")))
            except (json.JSONDecodeError, AttributeError):
                continue
        return found

    def _append(self, trajectory: Trajectory) -> None:
        self.output_dir.mkdir(parents=True, exist_ok=True)
        stored = redact_trajectory(trajectory) if self.redaction else trajectory
        with self._trajectories_path().open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(stored.to_dict(), ensure_ascii=False, sort_keys=True) + "\n")

    def _trajectory_from_result(self, task, result) -> Trajectory:
        started = datetime.now(UTC).isoformat()
        steps = [TrajectoryStep(role="human", content=task.prompt)]
        tools_used = (result.final_state or {}).get("tools_used") or []
        outputs = (result.final_state or {}).get("tool_outputs") or {}
        for tool in tools_used:
            output = outputs.get(tool)
            text = output if isinstance(output, str) else json.dumps(output, ensure_ascii=False)
            steps.append(
                TrajectoryStep(
                    role="assistant",
                    content="",
                    tool_calls=[{"tool": str(tool)}],
                )
            )
            steps.append(TrajectoryStep(role="tool", content=text))
        verdict = "passed" if result.passed else "failed"
        steps.append(
            TrajectoryStep(role="assistant", content=f"Task {verdict} (score {result.score}).")
        )
        return Trajectory(
            task_id=result.task_id,
            steps=steps,
            final_state=dict(result.final_state or {}),
            passed=result.passed,
            score=result.score,
            usage={"tools_used": [str(tool) for tool in tools_used]},
            started_at=started,
            ended_at=datetime.now(UTC).isoformat(),
        )

    def run(self, tasks, *, resume: bool = False) -> dict:
        """Run tasks, record trajectories, return the batch summary.

        With ``resume=True``, tasks whose id is already in
        ``trajectories.jsonl`` are skipped (the file is the
        checkpoint). The summary is written to
        ``batch-summary.json`` and returned.
        """
        done = self._recorded_task_ids() if resume else set()
        ran = 0
        skipped = 0
        passed = 0
        trajectories: list[Trajectory] = []
        for task in tasks:
            if task.task_id in done:
                skipped += 1
                continue
            result = self.harness.run_task(task)
            trajectory = self._trajectory_from_result(task, result)
            self._append(trajectory)
            trajectories.append(trajectory)
            ran += 1
            if result.passed:
                passed += 1
        summary = {
            "ran": ran,
            "skipped": skipped,
            "passed": passed,
            "failed": ran - passed,
            "tool_usage": self.aggregate_tool_usage(trajectories),
            "output_dir": str(self.output_dir),
            "trajectories_file": TRAJECTORIES_FILE,
        }
        self.output_dir.mkdir(parents=True, exist_ok=True)
        (self.output_dir / SUMMARY_FILE).write_text(
            json.dumps(summary, indent=2, sort_keys=True) + "\n", encoding="utf-8"
        )
        return summary

    def record_prompt_run(self, *, task_id: str, prompt: str, agent_result: dict) -> Trajectory:
        """Record one agent run over a bare prompt (no cassette).

        ``agent_result`` is the agent's own result dict:
        ``messages`` (``[{"role", "content"}]``), ``tool_calls``
        (``[{"tool", ...}]``), ``final_state``, ``passed``,
        ``score``, ``usage``. The trajectory is appended to the
        same ``trajectories.jsonl`` checkpoint file.
        """
        messages = agent_result.get("messages") or []
        steps = [TrajectoryStep(role="human", content=prompt)]
        for message in messages:
            if not isinstance(message, dict):
                continue
            role = str(message.get("role") or "assistant")
            if role not in ("human", "assistant", "tool"):
                role = "assistant"
            calls = message.get("tool_calls")
            steps.append(
                TrajectoryStep(
                    role=role,
                    content=str(message.get("content") or ""),
                    tool_calls=list(calls) if isinstance(calls, list) else [],
                )
            )
        trajectory = Trajectory(
            task_id=task_id,
            steps=steps,
            final_state=dict(agent_result.get("final_state") or {}),
            passed=bool(agent_result.get("passed")),
            score=float(agent_result.get("score") or 0.0),
            usage=dict(agent_result.get("usage") or {}),
            started_at=datetime.now(UTC).isoformat(),
            ended_at=datetime.now(UTC).isoformat(),
        )
        self._append(trajectory)
        return trajectory

    @staticmethod
    def aggregate_tool_usage(trajectories: list[Trajectory]) -> dict[str, int]:
        """Total tool calls per tool across the given trajectories."""
        totals: dict[str, int] = {}
        for trajectory in trajectories:
            for step in trajectory.steps:
                for call in step.tool_calls:
                    tool = call.get("tool") if isinstance(call, dict) else None
                    if isinstance(tool, str) and tool:
                        totals[tool] = totals.get(tool, 0) + 1
        return dict(sorted(totals.items()))
