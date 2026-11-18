"""The agent seam: how a channel message reaches Puspa's core (G14).

The channels package does NOT build a second agent. It calls the
existing :class:`puspa.runtime.tasks.TaskRunner` through one
narrow seam, :class:`TaskRunnerAgent`:

- One inbound message becomes one :class:`Task` with a single
  ``respond`` step. The step's executor is injected by the caller
  (the production executor calls the model + tools through the
  Phase 0 chassis; tests inject a fake).
- The reply text is the step's ``answer`` result. When the runner
  asks a clarifying question instead (purpose too short), that
  question IS the reply. When the task fails, the reply is a short
  honest failure notice — never a silent drop, never a fabricated
  answer.

Follow-up (named, not hidden): wire the production executor —
``puspa.runtime.tasks.TaskRunner`` needs an executor that calls
the model through the chassis (``puspa.chassis``) instead of the
injected callable. Until that wiring lands, ``TaskRunnerAgent``
with no executor raises :class:`ChannelError` (fail-closed: a
channel must never pretend the agent answered).

``ChannelAgent`` is the protocol the pipeline depends on, so a
future agent entry point can replace ``TaskRunnerAgent`` without
touching the pipeline.
"""

from __future__ import annotations

from typing import Protocol

from puspa.runtime.tasks import Step, Task, TaskRunner

from .errors import ChannelError
from .events import MessageEvent

__all__ = ["ChannelAgent", "TaskRunnerAgent", "answer_via_task_runner"]


class ChannelAgent(Protocol):
    """Something that turns a channel message into a reply string."""

    def answer(self, event: MessageEvent, *, user_id: str | None) -> str: ...


def _default_executor(step_name: str, inputs: dict) -> dict:
    raise ChannelError(
        "no agent executor configured: wire the production executor "
        "(model via puspa.chassis) before enabling channels"
    )


class TaskRunnerAgent:
    """Answer channel messages through the existing TaskRunner.

    ``executor`` is the step executor injected into the runner
    (``executor(step_name, inputs) -> result dict``). ``runner`` may
    be supplied (tests inject a runner with a fake executor and a
    recording sink); otherwise one is built around ``executor``.
    ``agent_id`` identifies the Puspa agent that owns the task.
    """

    def __init__(
        self,
        *,
        executor=None,
        runner: TaskRunner | None = None,
        agent_id: str = "puspa",
        sink=None,
    ) -> None:
        self.agent_id = agent_id
        if runner is not None:
            self._runner = runner
        else:
            self._runner = TaskRunner(executor=executor or _default_executor, sink=sink)

    def answer(self, event: MessageEvent, *, user_id: str | None) -> str:
        task = Task(
            task_id=f"channel-{event.platform}-{event.message_id}",
            user_id=user_id or event.sender_id,
            agent_id=self.agent_id,
            purpose=event.text,
            plan=[
                Step(
                    name="respond",
                    inputs={"type": "respond", "text": event.text},
                    expected_output={"type": "answer"},
                )
            ],
        )
        finished = self._runner.run(task)
        if finished.status == "pending_user" and finished.question:
            return finished.question
        if finished.status != "completed":
            return "Sorry, I could not handle that message right now. Please try again in a moment."
        result = finished.result.get("respond") or {}
        text = result.get("text") or result.get("answer") or ""
        if not isinstance(text, str) or not text.strip():
            return "Sorry, I could not handle that message right now. Please try again in a moment."
        return text


def answer_via_task_runner(
    event: MessageEvent,
    *,
    user_id: str | None,
    runner: TaskRunner,
    agent_id: str = "puspa",
) -> str:
    """One-function seam: answer one event with a given TaskRunner."""
    return TaskRunnerAgent(runner=runner, agent_id=agent_id).answer(event, user_id=user_id)
