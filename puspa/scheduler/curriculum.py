"""Curriculum runner: idle-time learning tasks from the learning loop.

The learning loop (``puspa/memory/learning_loop.py``) proposes the
next learning task: a retry for the worst misprediction gap, or the
verification of the oldest unverified draft skill. This module runs
those proposals as learning tasks while the system is idle, and feeds
the outcome back into the loop as experience.

A learning task is a learning task, not a user task. It never
interrupts user work, never performs an outbound action, and never
spends more than its per-task cost ceiling.

Hard limits, all enforced in code:

- Daily cap: at most ``DEFAULT_MAX_LEARNING_TASKS_PER_DAY`` learning
  tasks per calendar day (UTC) per runner instance. The cap is a named
  constant and can be changed per runner through the
  ``max_tasks_per_day`` parameter. The count lives on the runner, so
  the same runner instance must be reused across ticks for the cap
  to hold across ticks (see ``run_curriculum_tick``).
- Daily caps per user and global (plan section 7.5, see
  ``puspa/memory/curriculum_guardrails.py``): on top of the
  per-task Rp2.000 ceiling, a shared ``DailyCaps`` ledger caps
  learning work per user per day and globally per day, by task
  count and by rupiah. The spend caps are enforced by
  reservation (the day's spend so far plus the whole per-task
  ceiling must still fit the cap), so recorded spend can never
  pass a cap. Pass one ``DailyCaps`` instance to every runner
  (``caps=...``) to enforce the caps across runner instances;
  a runner without one builds its own ledger and enforces the
  caps only for itself. A task blocked by a cap reports
  ``user_daily_cap_reached``, ``user_daily_spend_reached``,
  ``global_daily_cap_reached`` or ``global_daily_spend_reached``,
  runs nothing, and consumes no daily run.
- Cost ceiling: each learning task runs under its own
  ``BudgetTracker`` with ``rupiah_max`` equal to
  ``DEFAULT_COST_CEILING_RP_PER_TASK`` (Rp2.000, the same ceiling
  as ``puspa/runtime/budget.py``). The executor result of a step may
  carry a ``cost_rp`` integer; the runner charges it to the task
  budget, and a step whose charge would pass the ceiling fails the
  task through ``BudgetExhausted`` instead of running on.
- Idle only: the runner executes a task only when ``is_idle_now()``
  is true. ``is_idle`` is a caller-supplied callable ``() -> bool``
  that must report whether any user task is pending or running
  (production callers wire it to the real task queue, for example
  by checking for claimed or accepted rows before calling
  ``run_once``). When no callable is given, the ``user_task_active``
  flag decides instead; set it with ``set_user_task_active(True)``
  while a user task runs. A busy runner returns a ``busy`` outcome
  without consuming daily budget and without touching the loop.
- No outbound action without approval: a learning task whose plan
  contains an outbound step stops at ``pending_approval`` inside
  ``TaskRunner`` before anything runs. The runner records that
  outcome, never executes the step, and never records it as a
  misprediction.

How outcomes feed back into the loop (plan section 7.5: the
runner never learns from its own unverified experience):

- Completed and verified: an experience entry is appended to
  the loop experience log, the gap that produced the task is
  cleared so the curriculum does not propose the same retry
  again, and the task is marked completed for the day on this
  runner. A run counts as verified only when it was
  corroborated by an independent check: a real verifier
  supplied to the runner passed the result (the trivial
  default verifier never counts), or the loop's correction for
  the task changed during the run because the world-model
  scoring path inside ``TaskRunner`` recorded a new correction
  for it. A bare "completed" from the runner's own say-so is
  not verification.
- Completed but unverified, failed, or pending approval: the
  outcome is recorded as a quarantined experience
  (``LearningLoop.quarantine_experience``): it is stored and
  listed for review, but it never changes a prediction, never
  grows a gap, and never clears one. A failed run in
  particular is never recorded as a misprediction of its own:
  the failure is the runner's own unverified experience, and
  only a user releasing it with
  ``LearningLoop.confirm_experience`` can let it teach. The
  gap stays open, so the curriculum may propose a retry later,
  subject to the daily caps.

Execution goes through the existing ``Planner`` (bounded replans,
at most 2) and ``TaskRunner`` (5 stage cycle). The plan comes from
``planner.decompose`` applied to the proposed task text, wrapped in
a full purpose sentence so the accept stage does not treat the
short task name as ambiguous. A caller may inject a different plan
builder through ``plan_builder``.

Scheduler hookup: ``run_curriculum_tick`` is the function a tick
calls. It takes an existing runner (preferred, keeps the daily cap
and the completed set across ticks) or builds a fresh one. The
scheduler calls it only when the caller passes a runner to
``scheduler.tick(curriculum=runner)``; by default ``tick`` runs no
curriculum work at all, so existing trigger behaviour is unchanged.
To enable curriculum runs from the scheduler, keep one
``CurriculumRunner`` for the process lifetime and pass it on every
tick, with a real ``is_idle`` callback wired to the task queue::

    runner = CurriculumRunner(loop=get_learning_loop(), is_idle=queue_is_idle)
    tick(sink=sink, dsn=dsn, curriculum=runner)

No new ledger event type is introduced here: the runner reuses the
events ``TaskRunner`` and ``Planner`` already emit (``plan_created``,
``approval_requested``, ``task_completed``, ``task_failed``,
``budget_exhausted``) and keeps its own outcome history in memory
(``runner.history``). No network call is made on any path here.
"""

from __future__ import annotations

from datetime import UTC, datetime

from puspa.memory.curriculum_guardrails import (
    DEFAULT_MAX_SPEND_RP_GLOBAL_PER_DAY,
    DEFAULT_MAX_SPEND_RP_PER_USER_PER_DAY,
    DEFAULT_MAX_TASKS_GLOBAL_PER_DAY,
    DEFAULT_MAX_TASKS_PER_USER_PER_DAY,
    DailyCaps,
)
from puspa.memory.learning_loop import get_learning_loop
from puspa.runtime.budget import CEILING_RP_PER_TASK, BudgetTracker
from puspa.runtime.planner import Planner, decompose
from puspa.runtime.tasks import Task, create_task_id

#: Maximum learning tasks per day (UTC) for one runner instance.
DEFAULT_MAX_LEARNING_TASKS_PER_DAY = 5

#: Cost ceiling per learning task, in rupiah. Same ceiling as the
#: per-task budget in ``puspa/runtime/budget.py``.
DEFAULT_COST_CEILING_RP_PER_TASK = CEILING_RP_PER_TASK

#: Default agent identity for learning tasks (no user is involved).
DEFAULT_CURRICULUM_USER_ID = "system"
DEFAULT_CURRICULUM_AGENT_ID = "puspa"

#: Outcomes a curriculum run can report.
RUN_STATUSES = (
    "completed",
    "failed",
    "pending_approval",
    "pending_user",
    "busy",
    "daily_cap_reached",
    "user_daily_cap_reached",
    "user_daily_spend_reached",
    "global_daily_cap_reached",
    "global_daily_spend_reached",
    "budget_hold",
    "no_task",
    "already_completed",
    "error",
)


def default_task_budget_check():
    """Lazy, fail-soft resolver for the billing budget check.

    Always returns a callable ``(user_id, task_id, *, cap_rp) ->
    int`` (rupiah remaining for the task): the budget port
    (``puspa.billing.ports.task_budget_remaining``) always exists,
    so there is no "ledger not there yet" case any more. The call
    itself stays fail-soft — a broken check returns None and the
    runner runs ungated instead of failing — and billing is never
    imported at module top level.
    """
    from puspa.billing.ports import task_budget_remaining

    def _check(user_id, task_id, *, cap_rp=2000):
        try:
            return int(task_budget_remaining(user_id, task_id, cap_rp=cap_rp))
        except Exception:
            return None

    return _check


class CurriculumError(Exception):
    """Curriculum runner cannot run a learning task."""


class CurriculumRunner:
    """Run learning tasks proposed by the learning loop, idle only."""

    def __init__(
        self,
        *,
        loop=None,
        sink=None,
        executor=None,
        verifier=None,
        is_idle=None,
        user_id=None,
        agent_id=None,
        max_tasks_per_day=None,
        cost_ceiling_rp=None,
        max_replans=2,
        plan_builder=None,
        world_model=None,
        learning_loop=None,
        budget_check=None,
        caps=None,
        max_tasks_per_user_per_day=None,
        max_spend_rp_per_user_per_day=None,
        max_tasks_global_per_day=None,
        max_spend_rp_global_per_day=None,
    ):
        self.loop = loop if loop is not None else get_learning_loop()
        self._events: list[dict] = []
        if sink is None:
            self.sink = lambda event_type, content: self._events.append(
                {"type": event_type, "content": content}
            )
        else:
            # Always keep an in-memory copy of events (the runner
            # reads failure reasons back from them), and forward
            # each event to the caller sink.
            user_sink = sink

            def _sink(event_type, content, _user=user_sink):
                self._events.append({"type": event_type, "content": content})
                _user(event_type, content)

            self.sink = _sink
        self.executor = executor or (lambda name, inputs: {"type": "result", "value": None})
        self.verifier = verifier
        # Idle check: a caller-supplied callable () -> bool wins; when
        # none is given, the user_task_active flag decides.
        self.is_idle = is_idle
        self.user_task_active = False
        self.user_id = user_id or DEFAULT_CURRICULUM_USER_ID
        self.agent_id = agent_id or DEFAULT_CURRICULUM_AGENT_ID
        if max_tasks_per_day is None:
            max_tasks_per_day = DEFAULT_MAX_LEARNING_TASKS_PER_DAY
        if max_tasks_per_day < 0:
            raise CurriculumError("max_tasks_per_day must be 0 or more")
        self.max_tasks_per_day = max_tasks_per_day
        if cost_ceiling_rp is None:
            cost_ceiling_rp = DEFAULT_COST_CEILING_RP_PER_TASK
        if cost_ceiling_rp < 0:
            raise CurriculumError("cost_ceiling_rp must be 0 or more")
        self.cost_ceiling_rp = cost_ceiling_rp
        self.max_replans = max_replans
        self.plan_builder = plan_builder
        self.world_model = world_model
        self.learning_loop = learning_loop
        # Budget gate (policy on top of the general scheduler):
        # ``budget_check(user_id, task_id, cap_rp=...) -> int`` is
        # the remaining rupiah for the task. ``None`` means "use the
        # default lazy budget-port check" (fail-soft: a broken check
        # runs the task ungated instead of failing).
        # Tests inject a stub. Never imported at top level.
        if budget_check is None:
            budget_check = default_task_budget_check()
        self.budget_check = budget_check
        # Daily caps per user and global (plan section 7.5), on
        # top of the per-runner daily cap and the per-task cost
        # ceiling. A shared ``caps`` ledger is what makes the
        # caps hold across runner instances: pass one instance
        # to every runner. Without one, this runner builds its
        # own ledger from the per-runner cap parameters (or the
        # defaults) and enforces the caps only for itself.
        if caps is None:
            caps = DailyCaps(
                max_tasks_per_user_per_day=(
                    max_tasks_per_user_per_day
                    if max_tasks_per_user_per_day is not None
                    else DEFAULT_MAX_TASKS_PER_USER_PER_DAY
                ),
                max_spend_rp_per_user_per_day=(
                    max_spend_rp_per_user_per_day
                    if max_spend_rp_per_user_per_day is not None
                    else DEFAULT_MAX_SPEND_RP_PER_USER_PER_DAY
                ),
                max_tasks_global_per_day=(
                    max_tasks_global_per_day
                    if max_tasks_global_per_day is not None
                    else DEFAULT_MAX_TASKS_GLOBAL_PER_DAY
                ),
                max_spend_rp_global_per_day=(
                    max_spend_rp_global_per_day
                    if max_spend_rp_global_per_day is not None
                    else DEFAULT_MAX_SPEND_RP_GLOBAL_PER_DAY
                ),
            )
        self.caps = caps
        self._runs_by_day: dict[str, int] = {}
        self._completed_by_day: dict[str, set] = {}
        self.history: list[dict] = []
        self.last_budget: BudgetTracker | None = None

    @property
    def events(self) -> list[dict]:
        """Events recorded through the runner sink (for tests)."""
        return list(self._events)

    def set_user_task_active(self, active: bool) -> None:
        """Flag a user task as running (or finished).

        Used when no ``is_idle`` callable was supplied. While the
        flag is set, the runner refuses to start a learning task.
        """
        self.user_task_active = bool(active)

    def is_idle_now(self) -> bool:
        """True only when no user task is pending or running."""
        if self.is_idle is not None:
            return bool(self.is_idle())
        return not self.user_task_active

    def runs_today(self, day: str | None = None) -> int:
        """Learning tasks already run on the given day (UTC, ISO date)."""
        if day is None:
            day = datetime.now(UTC).date().isoformat()
        return self._runs_by_day.get(day, 0)

    def _purpose_for(self, proposal: dict) -> str:
        """Full purpose sentence for a proposal.

        The accept stage asks a question for purposes under 3 words,
        so the short task name alone (for example ``check_pln``)
        would never run. The prefix keeps the task text intact so
        ``decompose`` still sees its keywords.
        """
        task = str(proposal["task"])
        if proposal.get("reason") == "unverified_draft":
            return f"Verify learned skill: {task}"
        return f"Retry learning task: {task}"

    def _plan_for(self, proposal: dict, purpose: str) -> list:
        if self.plan_builder is not None:
            return self.plan_builder(proposal, purpose)
        return decompose(purpose)

    def _failure_reason(self, task_id: str, planner: Planner) -> str:
        """Failure reason for a task, from the planner or the events.

        ``Planner`` reads its own in-memory event list for the reason,
        which stays empty when a sink is injected, so the runner also
        searches the events it recorded through its own sink.
        """
        if planner.last_failure_reason:
            return planner.last_failure_reason
        for event in reversed(self._events):
            content = event.get("content") or {}
            if event.get("type") == "task_failed" and content.get("task_id") == task_id:
                return str(content.get("reason", "") or "")
        return "learning task failed"

    def _charging_executor(self, budget: BudgetTracker):
        """Wrap the executor so step costs count against the budget.

        A step result may carry ``cost_rp``; charging it raises
        ``BudgetExhausted`` when the per-task ceiling would be
        passed, and ``TaskRunner`` turns that into a failed task.
        """
        executor = self.executor

        def wrapped(name, inputs):
            result = executor(name, inputs)
            cost = 0
            if isinstance(result, dict):
                cost = result.get("cost_rp", 0) or 0
            if cost:
                budget.record_cost(int(cost))
            return result

        return wrapped

    def _run_verified(self, task_id: str, task_name: str, correction_before) -> bool:
        """Whether a completed run was corroborated independently.

        Two channels count, and a bare "completed" counts for
        neither: (1) a real verifier was supplied to this runner
        and the verify stage passed for this task (the trivial
        default verifier never counts, so ``self.verifier`` must
        not be None); (2) the loop's correction for the task
        changed during the run, which means the world-model
        scoring path inside ``TaskRunner`` recorded a new
        correction for it. Either channel makes the outcome
        verified; both absent means the run is the runner's own
        unverified experience.
        """
        if self.verifier is not None:
            for event in self._events:
                content = event.get("content") or {}
                if (
                    event.get("type") == "verification_completed"
                    and content.get("task_id") == task_id
                    and content.get("passed")
                ):
                    return True
        correction_after = self.loop.correction_for(task_name)
        return correction_after is not None and correction_after != correction_before

    def _record_experience(
        self,
        proposal: dict,
        *,
        outcome: str,
        observed: str,
        error=None,
        task_id=None,
        cost_rp: int = 0,
        verified: bool = False,
    ) -> dict:
        """Record one runner outcome in the loop experience log.

        A verified outcome is stored as a verified experience.
        Anything else is quarantined (plan section 7.5): it is
        stored and listed for review, but it never changes a
        prediction, never grows a gap, and never clears one.
        Only a user releasing it with
        ``LearningLoop.confirm_experience`` can let it teach.
        """
        entry = {
            "task": str(proposal["task"]),
            "predicted": self.loop.predict(str(proposal["task"])),
            "observed": observed,
            "error": error,
            "context": {
                "source": "curriculum_runner",
                "reason": proposal.get("reason"),
                "outcome": outcome,
                "task_id": task_id,
                "cost_rp": cost_rp,
            },
            "kind": "curriculum_success" if outcome == "completed" else "curriculum",
            "outcome": outcome,
        }
        if verified:
            return self.loop.record_experience(entry, verification="verified")
        return self.loop.quarantine_experience(entry, reasons=["unverified"])

    def run_once(self, *, now: datetime | None = None) -> dict:
        """Run at most one proposed learning task, when idle.

        Returns an outcome dict with ``status``, ``ran`` (True only
        when a learning task was actually executed), the proposal,
        the task id and status, the cost in rupiah, and the daily
        run count. Never raises for a busy runner, a reached daily
        cap, or an empty curriculum: those are reported as outcomes.
        """
        now = now or datetime.now(UTC)
        day = now.date().isoformat()
        if not self.is_idle_now():
            outcome = {
                "status": "busy",
                "ran": False,
                "proposal": None,
                "task_id": None,
                "task_status": None,
                "cost_rp": 0,
                "runs_today": self.runs_today(day),
                "reason": "a user task is pending or running",
            }
            self.history.append(outcome)
            return outcome
        if self.runs_today(day) >= self.max_tasks_per_day:
            outcome = {
                "status": "daily_cap_reached",
                "ran": False,
                "proposal": None,
                "task_id": None,
                "task_status": None,
                "cost_rp": 0,
                "runs_today": self.runs_today(day),
                "reason": "daily learning task cap reached",
            }
            self.history.append(outcome)
            return outcome
        completed = self._completed_by_day.setdefault(day, set())
        proposal = self.loop.propose_next_task(exclude=completed)
        if proposal is None:
            unfiltered = self.loop.propose_next_task()
            if unfiltered is not None:
                # Everything left to propose was already completed
                # today: report that instead of running it again.
                outcome = {
                    "status": "already_completed",
                    "ran": False,
                    "proposal": unfiltered,
                    "task_id": None,
                    "task_status": None,
                    "cost_rp": 0,
                    "runs_today": self.runs_today(day),
                    "reason": "task already completed today",
                }
                self.history.append(outcome)
                return outcome
            outcome = {
                "status": "no_task",
                "ran": False,
                "proposal": None,
                "task_id": None,
                "task_status": None,
                "cost_rp": 0,
                "runs_today": self.runs_today(day),
                "reason": "curriculum has nothing to propose",
            }
            self.history.append(outcome)
            return outcome
        task_name = str(proposal["task"])
        # Ledger budget gate: no remaining budget means the task
        # waits (a hold, not a failure) and consumes no daily run.
        if self.budget_check is not None:
            try:
                remaining = self.budget_check(self.user_id, task_name, cap_rp=self.cost_ceiling_rp)
            except Exception:
                remaining = None
            if remaining is not None and remaining <= 0:
                outcome = {
                    "status": "budget_hold",
                    "ran": False,
                    "proposal": proposal,
                    "task_id": None,
                    "task_status": None,
                    "cost_rp": 0,
                    "runs_today": self.runs_today(day),
                    "reason": "no task budget remaining",
                }
                self.history.append(outcome)
                return outcome

        # Daily caps per user and global (plan section 7.5):
        # a blocked task waits (a hold, not a failure) and
        # consumes no daily run and no cap room.
        cap = self.caps.check(self.user_id, day=day, cost_ceiling_rp=self.cost_ceiling_rp)
        if not cap["allowed"]:
            outcome = {
                "status": cap["status"],
                "ran": False,
                "proposal": proposal,
                "task_id": None,
                "task_status": None,
                "cost_rp": 0,
                "runs_today": self.runs_today(day),
                "reason": cap["reason"],
            }
            self.history.append(outcome)
            return outcome

        # Snapshot the loop's correction for the task: when it
        # changes during the run, the world-model scoring path
        # corroborated the outcome (see `_run_verified`).
        correction_before = self.loop.correction_for(task_name)

        purpose = self._purpose_for(proposal)
        plan = self._plan_for(proposal, purpose)
        budget = BudgetTracker(rupiah_max=self.cost_ceiling_rp)
        self.last_budget = budget
        planner = Planner(
            sink=self.sink,
            executor=self._charging_executor(budget),
            verifier=self.verifier,
            max_replans=self.max_replans,
            world_model=self.world_model,
            learning_loop=self.learning_loop,
        )
        task = Task(
            task_id=create_task_id(),
            user_id=self.user_id,
            agent_id=self.agent_id,
            purpose=purpose,
            plan=plan,
        )
        self._runs_by_day[day] = self.runs_today(day) + 1
        result = planner.run(task)
        cost_rp = budget.rupiah_used
        # The task ran, whatever its outcome: record its cost
        # against the per-user and global daily caps.
        self.caps.record(self.user_id, day=day, cost_rp=cost_rp)
        status = result.status
        outcome = {
            "status": status,
            "ran": True,
            "proposal": proposal,
            "task_id": result.task_id,
            "task_status": status,
            "cost_rp": cost_rp,
            "runs_today": self.runs_today(day),
            "reason": proposal.get("reason"),
        }
        if status == "completed":
            verified = self._run_verified(result.task_id, task_name, correction_before)
            outcome["verified"] = verified
            self._record_experience(
                proposal,
                outcome="completed",
                observed="completed",
                task_id=result.task_id,
                cost_rp=cost_rp,
                verified=verified,
            )
            # Only a verified completion clears the gap: an
            # unverified run is the runner's own say-so, and the
            # curriculum may propose the retry again later.
            if verified and proposal.get("reason") == "misprediction_gap":
                self.loop.gaps.pop(task_name, None)
            completed.add(task_name)
        elif status == "failed":
            reason = self._failure_reason(result.task_id, planner)
            outcome["reason"] = reason
            # The failure is the runner's own unverified
            # experience: quarantine it, never teach from it.
            # The gap stays open, so the curriculum may propose
            # a retry later, subject to the daily caps.
            self._record_experience(
                proposal,
                outcome="failed",
                observed=f"failed: {reason}",
                error=reason,
                task_id=result.task_id,
                cost_rp=cost_rp,
            )
        elif status == "pending_approval":
            # Outbound step: stopped before execution, recorded only.
            self._record_experience(
                proposal,
                outcome="pending_approval",
                observed="pending_approval",
                task_id=result.task_id,
                cost_rp=cost_rp,
            )
        elif status == "pending_user":
            self._record_experience(
                proposal,
                outcome="pending_user",
                observed="pending_user",
                task_id=result.task_id,
                cost_rp=cost_rp,
            )
        self.history.append(outcome)
        return outcome


def run_curriculum_tick(
    *,
    runner: CurriculumRunner | None = None,
    loop=None,
    sink=None,
    executor=None,
    verifier=None,
    is_idle=None,
    max_tasks_per_day=None,
    cost_ceiling_rp=None,
    world_model=None,
    learning_loop=None,
    budget_check=None,
    caps=None,
    max_tasks_per_user_per_day=None,
    max_spend_rp_per_user_per_day=None,
    max_tasks_global_per_day=None,
    max_spend_rp_global_per_day=None,
    now: datetime | None = None,
) -> dict:
    """Run one curriculum step from a scheduler tick.

    Pass an existing ``runner`` to keep the daily cap and the
    completed set across ticks; when ``runner`` is None a fresh
    runner is built from the other arguments (its daily cap then
    covers only this call). Pass a shared ``caps`` ledger (or
    the per-runner cap parameters) to enforce the per-user and
    global daily caps for a fresh runner. Any failure inside
    the runner is reported as an ``error`` outcome, never
    raised, so a tick that calls this helper cannot be broken
    by curriculum work.
    """
    try:
        if runner is None:
            runner = CurriculumRunner(
                loop=loop,
                sink=sink,
                executor=executor,
                verifier=verifier,
                is_idle=is_idle,
                max_tasks_per_day=max_tasks_per_day,
                cost_ceiling_rp=cost_ceiling_rp,
                world_model=world_model,
                learning_loop=learning_loop,
                budget_check=budget_check,
                caps=caps,
                max_tasks_per_user_per_day=max_tasks_per_user_per_day,
                max_spend_rp_per_user_per_day=max_spend_rp_per_user_per_day,
                max_tasks_global_per_day=max_tasks_global_per_day,
                max_spend_rp_global_per_day=max_spend_rp_global_per_day,
            )
        return runner.run_once(now=now)
    except Exception as exc:
        return {
            "status": "error",
            "ran": False,
            "proposal": None,
            "task_id": None,
            "task_status": None,
            "cost_rp": 0,
            "runs_today": 0,
            "reason": str(exc),
        }
