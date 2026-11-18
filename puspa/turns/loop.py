"""The turn loop (G21): one interruptible, budgeted loop for every surface.

This is the split loop. The loop that was split is Puspa's
:class:`puspa.runtime.tasks.TaskRunner` — or rather, the model
call inside it: ``TaskRunner`` stays the task lifecycle
(accept -> plan -> run -> verify -> present) and this loop is
the conversational engine that runs inside its ``run`` stage,
injected as the step executor (see :func:`make_task_executor`).
There is NO second parallel loop: the PWA, the channels, and
the scheduler all reach this loop through ``TaskRunner`` (the
channels through the existing ``TaskRunnerAgent`` seam), so one
history, one budget, and one interruption rule serve every
surface. No nanobot code is touched — the loop sits above the
chassis, and the production model executor (chassis call) is
still the follow-up named in ``puspa/channels/agent.py``; until
it lands, the default model adapter is the router's live
OpenRouter caller.

One turn, in order, per iteration (each phase is its own module):

1. prepare (``turn_prepare``): interrupt check, one iteration
   of budget, alternation repair.
2. overflow (``turn_overflow`` / ``turn_truncate``): shrink the
   history when it passes the caps, never touching the system
   block or the protected tail.
3. API call (``turn_api``): the internal history goes out
   through the fallback chain, interruptibly, with
   refresh-first auth handling (``turn_api_error``).
4. tool calls: intercepted tools first (``intercepts`` — they
   never reach the registry), then the injected registry
   callable. Results return as tool messages; the loop runs
   again until the model answers with text, the budget runs
   out, or the user interrupts.
5. recovery (``turn_recover``): a checkpoint is taken before
   every call; an interrupt, timeout, or all-tiers failure
   restores it, so no partial reply ever enters the history.

A turn ends with status ``"completed"``, ``"interrupted"``,
``"budget_exceeded"``, or ``"failed"`` — always with a clear
message, never a silent stop and never a crash.
"""

from __future__ import annotations

import contextlib
import threading
from dataclasses import dataclass, field

from puspa.router.fallback import AllTiersFailed, ProviderFailure
from puspa.runtime.budget import BudgetExhausted

from .budget import IterationBudget, SubagentIterationBudget, TurnBudgetExceeded
from .config import TurnConfig
from .intercepts import InterceptedTools
from .interrupt import TurnInterrupted, TurnTimeout
from .messages import InternalMessage, enforce_alternation
from .turn_api import ModelReply, call_model
from .turn_api_error import describe_failure
from .turn_overflow import check_overflow
from .turn_prepare import prepare_iteration
from .turn_recover import (
    interruption_note,
    recovery_note,
    restore_checkpoint,
    take_checkpoint,
    timeout_note,
)
from .turn_truncate import truncate_history

__all__ = ["TurnLoop", "TurnResult", "make_task_executor"]

#: Turn statuses. "failed" covers timeouts, all-tiers failures,
#: and Rupiah-budget exhaustion; the message says which.
TURN_COMPLETED = "completed"
TURN_INTERRUPTED = "interrupted"
TURN_BUDGET_EXCEEDED = "budget_exceeded"
TURN_FAILED = "failed"


@dataclass
class TurnResult:
    """What one turn produced, and what it cost."""

    status: str
    text: str = ""
    message: str = ""
    messages: list[InternalMessage] = field(default_factory=list)
    iterations_used: int = 0
    iterations_limit: int = 0
    subagent_iterations_used: int = 0
    cost_rp: int = 0
    tier_used: str = ""
    attempts: list = field(default_factory=list)
    hops: list = field(default_factory=list)
    repairs: list[str] = field(default_factory=list)
    notes: list[str] = field(default_factory=list)


class TurnLoop:
    """One conversational loop, shared by every surface.

    ``model_call`` is the ``(entry, wire_messages, tools) ->
    ModelReply`` seam (default: the live OpenRouter adapter).
    ``tool_call`` is the registry seam: ``tool_call(name,
    arguments) -> result dict`` for tools the loop does NOT
    intercept. ``intercepts`` is the :class:`InterceptedTools`
    instance (default: one built with no stores — the memory
    store then defaults in-memory, session search and delegation
    answer with a clear "not wired" error result). ``compressor``
    is an optional ``messages -> InternalMessage | None``
    callable for the overflow-compress path. ``sink(event_type,
    content)`` records turn events (best-effort, like the
    TaskRunner sink); the default keeps them in memory.
    ``tool_catalog`` is the run's tool catalog (a list of
    :class:`puspa.tools.registry.ToolDefinition`, already filtered
    by toolset / allowlist / availability): when it is set, every
    run narrows it for the last user message first (G17, see
    :meth:`run_turn`) — unless the caller passes explicit
    ``tools``, which win. ``narrowing_skills`` groups the catalog
    into skills; ``narrowing_selector`` picks the skills (None
    means the deterministic keyword selector).
    """

    def __init__(
        self,
        *,
        config: TurnConfig | None = None,
        model_call=None,
        tool_call=None,
        intercepts: InterceptedTools | None = None,
        refresher=None,
        quota_store=None,
        budget=None,
        compressor=None,
        sink=None,
        tool_catalog=None,
        narrowing_skills=(),
        narrowing_selector=None,
    ) -> None:
        self.config = config or TurnConfig()
        self.model_call = model_call
        self.tool_call = tool_call
        self.intercepts = intercepts if intercepts is not None else InterceptedTools()
        self.refresher = refresher
        self.quota_store = quota_store
        self.budget = budget
        self.compressor = compressor
        self.tool_catalog = list(tool_catalog) if tool_catalog is not None else None
        self.narrowing_skills = list(narrowing_skills)
        self.narrowing_selector = narrowing_selector
        self._refusal_message = ""
        self._event: list[dict] = []
        self.sink = sink or (
            lambda event_type, content: self._event.append({"type": event_type, "content": content})
        )
        self._interrupt_event = threading.Event()

    # -- interruption -------------------------------------------------

    @property
    def interrupt_event(self) -> threading.Event:
        """The event :meth:`request_interrupt` sets."""
        return self._interrupt_event

    def request_interrupt(self) -> None:
        """Interrupt the running turn (call from any thread)."""
        self._interrupt_event.set()

    # -- running ------------------------------------------------------

    def run_turn(
        self,
        messages: list[InternalMessage],
        *,
        user_id=None,
        agent_id: str = "puspa",
        task_id: str | None = None,
        tools: list[dict] | None = None,
        interrupt_event: threading.Event | None = None,
        model_call=None,
        tool_call=None,
        budget: IterationBudget | None = None,
    ) -> TurnResult:
        """Run one turn to completion, interruption, or budget end.

        ``messages`` is the starting history (usually the
        session history plus the new user message). The returned
        ``TurnResult.messages`` is the full history afterwards,
        including every assistant and tool message of the turn.
        ``budget`` overrides the turn's iteration budget (a
        subagent turn passes its own narrowed budget — see
        :meth:`run_subagent_turn`).

        When this loop was built with a ``tool_catalog`` (and the
        caller did not pass explicit ``tools``), the catalog is
        narrowed for this run first (G17, see
        :meth:`_prepare_run_tools`): the model is offered the
        narrowed set, the hidden tools stay reachable through
        ``search_tools`` for the whole turn, and a catalog past
        the hard tool limit refuses the turn with a failed result
        instead of starting it.
        """
        if tools is None and self.tool_catalog is not None:
            prepared = self._prepare_run_tools(messages, agent_id=agent_id, task_id=task_id)
            if prepared is None:
                # Refusal: _prepare_run_tools already recorded the
                # audit event and the turn failure; the message
                # names the limit.
                return TurnResult(
                    status=TURN_FAILED,
                    text="",
                    message=self._refusal_message,
                    messages=list(messages),
                    iterations_used=0,
                    iterations_limit=self.config.max_iterations,
                )
            tools = prepared.schemas()
            if prepared.hidden_tools:
                from puspa.tools.narrowing import hidden_tools_context

                with hidden_tools_context(prepared.hidden_tools):
                    return self._run_turn_inner(
                        messages,
                        user_id=user_id,
                        agent_id=agent_id,
                        task_id=task_id,
                        tools=tools,
                        interrupt_event=interrupt_event,
                        model_call=model_call,
                        tool_call=tool_call,
                        budget=budget,
                    )
        return self._run_turn_inner(
            messages,
            user_id=user_id,
            agent_id=agent_id,
            task_id=task_id,
            tools=tools,
            interrupt_event=interrupt_event,
            model_call=model_call,
            tool_call=tool_call,
            budget=budget,
        )

    def _prepare_run_tools(self, messages, *, agent_id, task_id):
        """Narrow the loop's tool catalog for one run (G17).

        Returns the :class:`NarrowingResult`, or None when the
        run is refused: the ``agent.tool_limit_refused`` audit
        event goes out through the loop sink and the turn failure
        is recorded, so the refusal is visible without starting
        the turn. The user message narrowed on is the last user
        message in the history (``""`` when there is none, which
        fails open to the whole catalog).
        """
        # Lazy import: the turns package must not import the tools
        # package at module level (tools pulls in the connectors
        # allowlist); the loop only needs narrowing when a catalog
        # is actually wired in.
        from puspa.tools.narrowing import (
            NarrowingConfig,
            ToolLimitError,
            prepare_run_tools,
        )

        message = _last_user_text(messages)
        config = NarrowingConfig(
            max_direct_tools=self.config.max_direct_tools,
            max_tools=self.config.max_tools,
            max_offered_tools=self.config.max_offered_tools,
            search_result_limit=self.config.search_result_limit,
        )
        try:
            return prepare_run_tools(
                message,
                list(self.tool_catalog),
                skills=list(self.narrowing_skills),
                selector=self.narrowing_selector,
                config=config,
                audit_sink=self._record,
            )
        except ToolLimitError as exc:
            self._refusal_message = str(exc)
            self._record(
                "turn_failed",
                {"agent_id": agent_id, "task_id": task_id, "reason": str(exc)},
            )
            return None

    def _run_turn_inner(
        self,
        messages: list[InternalMessage],
        *,
        user_id=None,
        agent_id: str = "puspa",
        task_id: str | None = None,
        tools: list[dict] | None = None,
        interrupt_event: threading.Event | None = None,
        model_call=None,
        tool_call=None,
        budget: IterationBudget | None = None,
    ) -> TurnResult:
        """The turn itself, after tool preparation (see :meth:`run_turn`)."""
        event = interrupt_event if interrupt_event is not None else self._interrupt_event
        if event is self._interrupt_event:
            event.clear()
        iteration_budget = (
            budget if budget is not None else IterationBudget(self.config.max_iterations)
        )
        history = list(messages)
        repairs: list[str] = []
        notes: list[str] = []
        attempts: list = []
        hops: list = []
        cost_rp = 0
        tier_used = ""
        self._record(
            "turn_started",
            {"agent_id": agent_id, "task_id": task_id, "messages": len(history)},
        )
        while True:
            checkpoint = take_checkpoint(history)
            try:
                prepared = prepare_iteration(
                    history, budget=iteration_budget, interrupt_event=event
                )
            except TurnInterrupted:
                return self._finish_interrupted(
                    checkpoint,
                    iteration_budget,
                    repairs,
                    notes,
                    attempts,
                    hops,
                    cost_rp,
                    agent_id,
                    task_id,
                )
            except TurnBudgetExceeded as exc:
                return self._finish_budget(
                    checkpoint,
                    iteration_budget,
                    exc,
                    repairs,
                    notes,
                    attempts,
                    hops,
                    cost_rp,
                    agent_id,
                    task_id,
                )
            history = prepared.messages
            repairs.extend(prepared.repairs)

            history, overflow_note = self._apply_overflow(history)
            if overflow_note:
                notes.append(overflow_note)

            try:
                outcome = call_model(
                    history,
                    config=self.config,
                    model_call=model_call if model_call is not None else self.model_call,
                    tools=tools,
                    interrupt_event=event,
                    refresher=self.refresher,
                    quota_store=self.quota_store,
                    budget=self.budget,
                )
            except TurnInterrupted:
                return self._finish_interrupted(
                    checkpoint,
                    iteration_budget,
                    repairs,
                    notes,
                    attempts,
                    hops,
                    cost_rp,
                    agent_id,
                    task_id,
                )
            except TurnTimeout as exc:
                return self._finish_failed(
                    checkpoint,
                    iteration_budget,
                    timeout_note(exc.timeout_seconds),
                    repairs,
                    notes,
                    attempts,
                    hops,
                    cost_rp,
                    agent_id,
                    task_id,
                )
            except (AllTiersFailed, ProviderFailure) as exc:
                return self._finish_failed(
                    checkpoint,
                    iteration_budget,
                    f"model call failed on every tier: {describe_failure(exc)}",
                    repairs,
                    notes,
                    attempts,
                    hops,
                    cost_rp,
                    agent_id,
                    task_id,
                )
            except Exception as exc:
                if isinstance(exc, BudgetExhausted):
                    return self._finish_failed(
                        checkpoint,
                        iteration_budget,
                        f"turn stopped: Rupiah budget exhausted: {exc}",
                        repairs,
                        notes,
                        attempts,
                        hops,
                        cost_rp,
                        agent_id,
                        task_id,
                    )
                raise

            history = [*history, outcome.as_assistant_message()]
            attempts = list(outcome.attempts)
            hops = list(outcome.hops)
            cost_rp += outcome.total_cost_rp
            tier_used = outcome.tier_used.tier
            notes.extend(outcome.chain_notes)
            self._record(
                "model_called",
                {
                    "agent_id": agent_id,
                    "task_id": task_id,
                    "iteration": prepared.iteration,
                    "tier": outcome.tier_used.tier,
                    "provider": outcome.tier_used.provider,
                    "model": outcome.tier_used.model,
                    "cost_rp": outcome.total_cost_rp,
                },
            )
            reply = outcome.reply
            if not reply.tool_calls:
                return self._finish_completed(
                    history,
                    iteration_budget,
                    reply,
                    repairs,
                    notes,
                    attempts,
                    hops,
                    cost_rp,
                    tier_used,
                    agent_id,
                    task_id,
                )
            history = self._run_tool_calls(
                history,
                reply,
                agent_id=agent_id,
                task_id=task_id,
                tool_call=tool_call,
            )

    def run_subagent_turn(
        self,
        messages: list[InternalMessage],
        *,
        parent_budget: IterationBudget,
        user_id=None,
        agent_id: str = "puspa",
        task_id: str | None = None,
        tools: list[dict] | None = None,
        interrupt_event: threading.Event | None = None,
        model_call=None,
        tool_call=None,
    ) -> TurnResult:
        """Run a subagent turn under its own narrowed budget.

        The subagent budget caps at ``config.subagent_max_iterations``
        (default 50) AND at the parent's remaining iterations —
        whichever is smaller. Every subagent iteration also
        consumes one parent iteration. The result records both
        counts; ``iterations_limit`` is the subagent cap.
        """
        sub_budget = SubagentIterationBudget(
            parent_budget, max_iterations=self.config.subagent_max_iterations
        )
        result = self.run_turn(
            messages,
            user_id=user_id,
            agent_id=agent_id,
            task_id=task_id,
            tools=tools,
            interrupt_event=interrupt_event,
            model_call=model_call,
            tool_call=tool_call,
            budget=sub_budget,
        )
        result.subagent_iterations_used = sub_budget.used
        return result

    # -- overflow ------------------------------------------------------

    def _apply_overflow(
        self, history: list[InternalMessage]
    ) -> tuple[list[InternalMessage], str | None]:
        decision = check_overflow(history, config=self.config, compressor=self.compressor)
        if decision.action == "ok":
            return history, None
        if decision.action == "compress" and self.compressor is not None:
            summary = self.compressor(history)
            if summary is not None:
                system_block = [m for m in history if m.role == "system"][:1]
                tail = history[-self.config.keep_last_messages :]
                compressed = [*system_block, summary, *tail]
                repaired = enforce_alternation(compressed)
                return list(repaired.value), (
                    f"overflow: compressed the middle turns into one summary "
                    f"({decision.messages_over} messages, {decision.chars_over} "
                    "chars over the caps)"
                )
        truncated = truncate_history(history, config=self.config)
        repaired = enforce_alternation(truncated.messages)
        return list(repaired.value), (
            f"overflow: truncated {truncated.dropped_count} oldest message(s) "
            f"({truncated.dropped_chars} chars)"
        )

    # -- tool calls ------------------------------------------------------

    def _run_tool_calls(
        self,
        history: list[InternalMessage],
        reply: ModelReply,
        *,
        agent_id: str,
        task_id: str | None,
        tool_call=None,
    ) -> list[InternalMessage]:
        registry = tool_call if tool_call is not None else self.tool_call
        for call in reply.tool_calls:
            name = call.get("name", "")
            arguments = call.get("arguments") or {}
            call_id = call.get("id") or ""
            intercepted = self.intercepts.try_intercept(name, arguments, task_id=task_id)
            if intercepted is not None:
                self._record(
                    "tool_intercepted",
                    {"agent_id": agent_id, "task_id": task_id, "tool": name},
                )
                result_text = _result_text(intercepted)
                history.append(InternalMessage.tool_result(call_id, result_text, is_error=False))
                continue
            if registry is not None:
                try:
                    raw = registry(name, arguments)
                    result_text = _result_text(raw)
                    is_error = False
                except Exception as exc:
                    result_text = f"tool {name!r} failed: {type(exc).__name__}: {exc}"
                    is_error = True
                self._record(
                    "tool_called",
                    {"agent_id": agent_id, "task_id": task_id, "tool": name},
                )
            else:
                result_text = (
                    f"tool {name!r} is not available: no tool registry is wired "
                    "into this turn loop (pass tool_call=... to TurnLoop)"
                )
                is_error = True
            history.append(InternalMessage.tool_result(call_id, result_text, is_error=is_error))
        return history

    # -- finishing -------------------------------------------------------

    def _record(self, event_type: str, content: dict) -> None:
        with contextlib.suppress(Exception):
            self.sink(event_type, content)

    def _finish_completed(
        self,
        history,
        budget,
        reply,
        repairs,
        notes,
        attempts,
        hops,
        cost_rp,
        tier_used,
        agent_id,
        task_id,
    ) -> TurnResult:
        self._record(
            "turn_completed",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "iterations_used": budget.used,
                "cost_rp": cost_rp,
            },
        )
        return TurnResult(
            status=TURN_COMPLETED,
            text=reply.text,
            message="turn completed",
            messages=history,
            iterations_used=budget.used,
            iterations_limit=budget.max_iterations,
            cost_rp=cost_rp,
            tier_used=tier_used,
            attempts=attempts,
            hops=hops,
            repairs=repairs,
            notes=notes,
        )

    def _finish_interrupted(
        self,
        checkpoint,
        budget,
        repairs,
        notes,
        attempts,
        hops,
        cost_rp,
        agent_id,
        task_id,
    ) -> TurnResult:
        restored = restore_checkpoint(checkpoint)
        note = interruption_note()
        self._record(
            "turn_interrupted",
            {"agent_id": agent_id, "task_id": task_id, "iterations_used": budget.used},
        )
        return TurnResult(
            status=TURN_INTERRUPTED,
            text="",
            message=note,
            messages=restored,
            iterations_used=budget.used,
            iterations_limit=budget.max_iterations,
            cost_rp=cost_rp,
            attempts=attempts,
            hops=hops,
            repairs=repairs,
            notes=[*notes, recovery_note("interrupted by the user")],
        )

    def _finish_budget(
        self,
        checkpoint,
        budget,
        exc,
        repairs,
        notes,
        attempts,
        hops,
        cost_rp,
        agent_id,
        task_id,
    ) -> TurnResult:
        restored = restore_checkpoint(checkpoint)
        self._record(
            "budget_exceeded",
            {
                "agent_id": agent_id,
                "task_id": task_id,
                "iterations_used": budget.used,
                "limit": budget.max_iterations,
            },
        )
        return TurnResult(
            status=TURN_BUDGET_EXCEEDED,
            text="",
            message=str(exc),
            messages=restored,
            iterations_used=budget.used,
            iterations_limit=budget.max_iterations,
            cost_rp=cost_rp,
            attempts=attempts,
            hops=hops,
            repairs=repairs,
            notes=[*notes, recovery_note("iteration budget exhausted")],
        )

    def _finish_failed(
        self,
        checkpoint,
        budget,
        message,
        repairs,
        notes,
        attempts,
        hops,
        cost_rp,
        agent_id,
        task_id,
    ) -> TurnResult:
        restored = restore_checkpoint(checkpoint)
        self._record(
            "turn_failed",
            {"agent_id": agent_id, "task_id": task_id, "reason": message},
        )
        return TurnResult(
            status=TURN_FAILED,
            text="",
            message=message,
            messages=restored,
            iterations_used=budget.used,
            iterations_limit=budget.max_iterations,
            cost_rp=cost_rp,
            attempts=attempts,
            hops=hops,
            repairs=repairs,
            notes=[*notes, recovery_note(message)],
        )

    def event_memory(self) -> list[dict]:
        """Events recorded by the default sink (for tests)."""
        return list(self._event)


def _result_text(result) -> str:
    """Render a tool result as the text of a tool-result message."""
    import json

    if isinstance(result, str):
        return result
    if isinstance(result, dict) and "error" in result and len(result) == 1:
        return str(result["error"])
    return json.dumps(result, ensure_ascii=False, default=str)


def _last_user_text(messages: list[InternalMessage]) -> str:
    """Text of the last user message, or "" when there is none.

    Only ``text`` blocks count; a user message with no text
    (images only) narrows on "" and therefore fails open.
    """
    for message in reversed(messages):
        if message.role != "user":
            continue
        parts = [
            block.get("text", "")
            for block in message.blocks
            if block.get("type") == "text" and block.get("text")
        ]
        return " ".join(parts)
    return ""


def make_task_executor(turn_loop: TurnLoop | None = None, **loop_kwargs) -> object:
    """Build a ``TaskRunner`` step executor from a turn loop.

    This is the wiring between the split loop and the task
    lifecycle: ``TaskRunner(executor=make_task_executor(...))``
    runs each step's model work through the turn loop, and the
    channels reach it through the existing ``TaskRunnerAgent``
    seam — one loop for every surface, no second loop. The
    executor takes the step's text (``inputs["text"]``,
    ``inputs["purpose"]``, or the step name), runs one turn on
    it, and returns ``{"type": "answer", "text": ...}``. A turn
    that ends interrupted, over budget, or failed returns an
    honest failure notice as the text — never a silent drop,
    never a fabricated answer.
    """
    loop = turn_loop if turn_loop is not None else TurnLoop(**loop_kwargs)

    def executor(step_name: str, inputs: dict) -> dict:
        text = inputs.get("text") or inputs.get("purpose") or step_name
        if not isinstance(text, str):
            text = str(text)
        result = loop.run_turn(
            [InternalMessage.user(text)],
            task_id=inputs.get("task_id"),
        )
        if result.status == TURN_COMPLETED:
            return {"type": "answer", "text": result.text}
        if result.status == TURN_INTERRUPTED:
            return {
                "type": "answer",
                "text": "The turn was interrupted before it finished, so there is no reply yet.",
            }
        if result.status == TURN_BUDGET_EXCEEDED:
            return {"type": "answer", "text": f"The turn stopped: {result.message}"}
        return {"type": "answer", "text": f"Sorry, I could not answer that: {result.message}"}

    return executor
