"""Planner with bounded replanning (revision 7 section 2.4).

Pattern studied (rewritten, not copied):
- OpenManus / AutoGPT classic goal to plan to tool loop (MIT): a task
  is decomposed into steps, executed step by step, and replanned when
  a step fails.
- langgraph (MIT): checkpointed, resumable orchestration. Puspa keeps
  the no-LangGraph decision: orchestration stays Puspa's own task cycle
  in ``puspa/runtime/tasks.py`` plus this planner on top of it.

This module does NOT execute steps itself and does NOT call any model
or network. It decomposes a purpose into ``Step`` objects, runs them
through the existing ``TaskRunner``, and replans when a run fails:

- Attempt 1: run the initial plan.
- Replan 1 (on failure): retry the failed step with a narrower input
  (``retry`` flag plus attempt number) and run the whole plan again.
- Replan 2 (on a second failure of the same step): drop the failed
  step and run the remaining steps, if any remain.
- After ``max_replans`` failures the task stays failed; the planner
  never loops forever and never silently drops the whole task.

Plans that contain an outbound step stop at the approval point inside
``TaskRunner`` (``pending_approval``); the planner never replans
around an approval gate and never executes an outbound action itself.

Every plan and every replan is recorded to the ledger sink with the
existing closed event type ``plan_created`` (the ledger event set is
closed in revision 7, so no new event type is invented here).
"""

from __future__ import annotations

import re

from puspa.runtime.hierarchy import HierarchicalPlan, HierarchicalRunner
from puspa.runtime.tasks import Step, Task, TaskRunner, create_plan

__all__ = [
    "Planner",
    "PlannerError",
    "decompose",
    "decompose_for_reference_task",
    "decompose_hierarchical",
    "ROLE_FOR_STEP",
    "step_tool",
]

#: Default maximum number of replans per task.
DEFAULT_MAX_REPLANS = 2

#: Reference task names (``puspa/cassette/cassette.py``) mapped to a
#: purpose string the planner can decompose.
REFERENCE_PURPOSES = {
    "research_summarize_5_articles": "research and summarize 5 articles",
    "read_email_create_draft": "read email and create a draft reply",
    "schedule_meeting_from_email": "schedule a meeting from an email",
    "compare_price_laptop": "compare laptop prices across stores",
    "check_bill_pln_prepare_payment": "check PLN bill and prepare payment",
    "search_file_drive_summarize": "search a file in Drive and summarize it",
    "remember_yesterday_preferences": "remember yesterday preferences",
    "bill_reminder": "remind me about bills due this week",
    "remind_bill_due_this_week": "remind me about bills due this week",
    "prepare_payment_electricity_token_50k": "prepare payment for electricity token 50000",
}

#: Step name prefix to cassette tool name, for coverage scoring in
#: ``puspa/memory/optimizer.py`` and ``puspa/evidence/eval_harness.py``.
STEP_TOOL_PREFIXES = (
    ("read_price", "read_price"),
    ("read_bill", "read_bill"),
    ("gmail_", "gmail"),
    ("calendar_", "calendar"),
    ("drive_", "drive"),
    ("search_listings", "search_listings"),
    ("search_artikel", "search_artikel"),
    ("read_artikel", "read_artikel"),
    ("read_memory", "read_memory"),
    ("create_trigger", "create_trigger"),
    ("prepare_payment", "prepare_payment"),
)


def step_tool(step_name: str) -> str:
    """Cassette tool a step name maps to (prefix match, else itself)."""
    for prefix, tool in STEP_TOOL_PREFIXES:
        if step_name.startswith(prefix):
            return tool
    return step_name


class PlannerError(Exception):
    """Planner cannot decompose or replan a task."""


def _step(name: str, *, permission_class: str = "Pembaca",
          outbound: bool = False, inputs_type: str = "request") -> Step:
    return Step(
        name=name,
        inputs={"type": inputs_type},
        expected_output={"type": "result"},
        permission_class=permission_class,
        outbound_action=outbound,
        completion_condition=name,
    )


def decompose(purpose: str, *, include_verify: bool = True,
              include_present: bool = True, max_price_reads: int = 3) -> list[Step]:
    """Decompose a purpose into steps (deterministic, no model call).

    Keyword templates, checked in order. The generic fallback is a
    three-step gather, verify, present plan. ``include_verify`` and
    ``include_present`` are the optimiser's knobs (see
    ``puspa/memory/optimizer.py``); ``max_price_reads`` caps how many
    store price reads a comparison plan contains.
    """
    if not purpose or not str(purpose).strip():
        raise PlannerError("purpose must not be empty")
    text = str(purpose).lower()

    def verify_step() -> list[Step]:
        return [_step("verify_price", permission_class="Pemeriksa")] if include_verify else []

    def present_step(name: str = "present") -> list[Step]:
        return [_step(name, permission_class="Pelaksana")] if include_present else []

    if ("compare" in text and "price" in text) or "laptop" in text:
        stores = ["tokopedia", "shopee", "blibli"][: max(1, max_price_reads)]
        steps = [_step("search_listings")]
        steps += [_step(f"read_price_{store}") for store in stores]
        steps += verify_step()
        steps += present_step("present_comparison")
        return steps
    if "bill" in text or "pln" in text or "pdam" in text:
        if "remind" in text or "reminder" in text or "due" in text:
            return [_step("read_bill"), _step("create_trigger", permission_class="Pelaksana")]
        steps = [_step("read_bill"), _step("verify_bill", permission_class="Pemeriksa")]
        steps.append(_step("prepare_payment", permission_class="Pelaksana", outbound=True))
        return steps
    if "electricity" in text and "token" in text:
        return [_step("prepare_payment", permission_class="Pelaksana", outbound=True)]
    if "research" in text or "article" in text:
        steps = [_step("search_artikel"), _step("read_artikel")]
        steps += present_step("summarize")
        return steps
    if "meeting" in text:
        return [_step("gmail_read"),
                _step("calendar_create_event", permission_class="Pelaksana", outbound=True)]
    if "email" in text:
        steps = [_step("gmail_search"), _step("gmail_read")]
        if "draft" in text:
            steps.append(_step("gmail_create_draft", permission_class="Pelaksana"))
        steps += present_step()
        return steps
    if "drive" in text or "file" in text:
        steps = [_step("drive_search"), _step("drive_read")]
        steps += present_step("summarize")
        return steps
    if "remember" in text or "preference" in text:
        return [_step("read_memory")] + present_step()
    if "remind" in text or "trigger" in text:
        return [_step("read_bill"), _step("create_trigger", permission_class="Pelaksana")]
    steps = [_step("gather")]
    steps += verify_step() if include_verify else []
    if include_verify:
        steps[-1] = _step("verify", permission_class="Pemeriksa")
    steps += present_step()
    return steps


def decompose_for_reference_task(name: str, **kwargs) -> list[Step]:
    """Plan for one of the 10 reference tasks, by cassette name."""
    purpose = REFERENCE_PURPOSES.get(name)
    if purpose is None:
        raise PlannerError(f"unknown reference task: {name!r}")
    return decompose(purpose, **kwargs)


#: Step-name prefix to the hierarchy role that owns the step.
#: Read/search steps belong to the researcher, checks to the
#: checker, presentation to the writer, outbound preparation to
#: the executor. Anything else stays with the root.
ROLE_FOR_STEP = (
    ("read_price", "researcher"),
    ("search_listings", "researcher"),
    ("search_artikel", "researcher"),
    ("read_artikel", "researcher"),
    ("drive_search", "researcher"),
    ("drive_read", "researcher"),
    ("gmail_search", "researcher"),
    ("gmail_read", "researcher"),
    ("read_bill", "researcher"),
    ("read_memory", "researcher"),
    ("verify", "checker"),
    ("present", "writer"),
    ("summarize", "writer"),
    ("prepare_payment", "executor"),
    ("calendar_create_event", "executor"),
    ("create_trigger", "executor"),
)


def _role_for_step(step_name: str) -> str | None:
    for prefix, role in ROLE_FOR_STEP:
        if step_name.startswith(prefix):
            return role
    return None


def decompose_hierarchical(purpose: str, *, composition: str = "sequential",
                            max_iterations: int = 3, **kwargs) -> HierarchicalPlan:
    """Decompose a purpose into a hierarchical plan.

    Same steps as ``decompose``; each step is assigned to a role
    by ``ROLE_FOR_STEP``. Steps with no role run on the root.
    ``composition`` picks the runner shape (sequential, parallel,
    loop, delegate); ``loop`` repeats until the caller's exit
    condition holds or ``max_iterations`` is reached.
    """
    steps = decompose(purpose, **kwargs)
    assignments = {}
    for step in steps:
        role = _role_for_step(step.name)
        if role is not None:
            assignments[step.name] = role
    return HierarchicalPlan(steps=steps, assignments=assignments,
                            composition=composition,
                            max_iterations=max_iterations)


def _failed_step_name(reason: str) -> str | None:
    """Extract the step name from a TaskRunner failure reason, if any."""
    match = re.search(r"langkah '([^']+)'", reason or "")
    if match:
        return match.group(1)
    match = re.search(r"step '([^']+)'", reason or "")
    return match.group(1) if match else None


class Planner:
    """Plan, execute through ``TaskRunner``, replan on failure.

    ``sink`` is the ledger sink callable ``(event_type, content)``;
    the default keeps events in memory (``planner.events``).
    ``executor`` / ``verifier`` are passed through to ``TaskRunner``.
    ``world_model`` / ``learning_loop`` are passed through to
    ``TaskRunner`` as well (brain wiring, advisory only); when they
    are None the planner behaves exactly as before.
    """

    def __init__(self, *, sink=None, executor=None, verifier=None,
                 max_replans: int = DEFAULT_MAX_REPLANS,
                 world_model=None, learning_loop=None) -> None:
        if max_replans < 0:
            raise PlannerError("max_replans must be 0 or more")
        self.max_replans = max_replans
        self._events: list[dict] = []
        self.sink = sink or (lambda event_type, content: self._events.append(
            {"type": event_type, "content": content}))
        self.executor = executor
        self.verifier = verifier
        self.world_model = world_model
        self.learning_loop = learning_loop
        self.attempts = 0
        self.replans = 0
        self.last_failure_reason = ""

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def _record(self, event_type: str, content: dict) -> None:
        try:
            self.sink(event_type, content)
        except Exception:
            pass

    def _run_once(self, task: Task, plan: list[Step]) -> Task:
        attempt_task = Task(
            task_id=task.task_id, user_id=task.user_id,
            agent_id=task.agent_id, purpose=task.purpose, plan=list(plan),
        )
        runner = TaskRunner(sink=self.sink, executor=self.executor,
                            verifier=self.verifier,
                            world_model=self.world_model,
                            learning_loop=self.learning_loop)
        return runner.run(attempt_task)

    def _replan(self, plan: list[Step], failed_step: str | None,
                attempt: int) -> list[Step] | None:
        """Next plan after a failure, or None if no replan is possible.

        Attempt 1: retry the failed step with a narrower input.
        Attempt 2: drop the failed step and keep the rest.
        """
        if failed_step is None:
            return None
        names = [step.name for step in plan]
        if failed_step not in names:
            return None
        if attempt == 1:
            revised = []
            for step in plan:
                if step.name == failed_step:
                    inputs = dict(step.inputs)
                    inputs["retry"] = True
                    inputs["attempt"] = 2
                    revised.append(Step(
                        name=step.name, inputs=inputs,
                        expected_output=dict(step.expected_output),
                        permission_class=step.permission_class,
                        budget=dict(step.budget),
                        completion_condition=step.completion_condition,
                        outbound_action=step.outbound_action,
                        parallel_with=list(step.parallel_with),
                    ))
                else:
                    revised.append(step)
            return revised
        if attempt == 2:
            remaining = [step for step in plan if step.name != failed_step]
            return remaining or None
        return None

    def run(self, task: Task) -> Task:
        """Run a task: plan, execute, replan on failure (bounded)."""
        if task.plan:
            plan = create_plan(task.purpose, list(task.plan))
        else:
            plan = decompose(task.purpose)
        self.attempts = 0
        self.replans = 0
        self.last_failure_reason = ""
        result = task
        for attempt in range(self.max_replans + 1):
            self.attempts = attempt + 1
            result = self._run_once(task, plan)
            if result.status in ("completed", "pending_user", "pending_approval"):
                return result
            # Failed: extract the reason from the sink events of this run.
            reason = ""
            for event in reversed(self._events):
                content = event.get("content") or {}
                if event.get("type") == "task_failed" and content.get("task_id") == task.task_id:
                    reason = str(content.get("reason", ""))
                    break
            self.last_failure_reason = reason
            if attempt >= self.max_replans:
                return result
            revised = self._replan(plan, _failed_step_name(reason), attempt + 1)
            if revised is None:
                return result
            self.replans += 1
            self._record("plan_created", {
                "task_id": task.task_id, "attempt": attempt + 2,
                "replan": True, "reason": reason,
                "step": [step.name for step in revised],
            })
            plan = revised
        return result

    def run_hierarchical(self, task: Task, root, *, composition: str = "sequential",
                         state=None, runner: HierarchicalRunner | None = None,
                         max_iterations: int = 3, **kwargs):
        """Run a task as a hierarchical plan through ``root``.

        Builds the plan with ``decompose_hierarchical`` and runs
        it through a ``HierarchicalRunner`` (given, or built here
        with this planner's executor adapted to the runner's
        ``(node, step, inputs, state)`` shape and ``step_tool`` as
        the tool resolver). Returns the runner's ``RunResult``.
        The flat ``run`` path above is untouched.
        """
        plan = decompose_hierarchical(task.purpose, composition=composition,
                                      max_iterations=max_iterations, **kwargs)
        if runner is None:
            executor = self.executor
            adapted = None
            if executor is not None:
                def adapted(node_name, step_name, inputs, state):
                    return executor(step_name, inputs)
            runner = HierarchicalRunner(root, executor=adapted, sink=self.sink,
                                        tool_resolver=step_tool)
        result = runner.run(plan, state=state, task_id=task.task_id)
        task.result.update(result.results)
        task.status = "failed" if result.blocked else "completed"
        return result
