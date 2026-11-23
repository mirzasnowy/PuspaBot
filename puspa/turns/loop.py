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
import time
import uuid
from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING

from puspa.db import Error as PsycopgError
from puspa.observability import metrics as observability_metrics
from puspa.observability import tracing as observability_tracing
from puspa.router.fallback import AllTiersFailed, ProviderFailure
from puspa.runtime.budget import BudgetExhausted

if TYPE_CHECKING:  # pragma: no cover - typing only, no runtime import
    from puspa.intent.act_ask import ActAskDecision

from .budget import IterationBudget, SubagentIterationBudget, TurnBudgetExceeded
from .config import TurnConfig
from .conflict_wiring import apply_conflict_gate
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
    #: Act-vs-ask verdicts the intent advisor gave during this turn
    #: (K1, ``puspa.intent.turn_wiring.IntentTurnAdvisor``): one
    #: ``ActAskDecision`` per tool call the advisor was consulted
    #: on, in order. Empty when no advisor is wired in — the loop's
    #: behaviour without an advisor is unchanged.
    intent_decisions: list = field(default_factory=list)
    #: Autonomy-dial verdicts the dial gave during this turn (K11,
    #: ``puspa.autonomy.dial.AutonomyDial``): one ``DialDecision``
    #: per tool call the dial was consulted on, in order. Empty
    #: when no dial is wired in — the loop's behaviour without a
    #: dial is unchanged.
    dial_decisions: list = field(default_factory=list)
    #: Conflict verdicts the gate gave during this turn (K13), one per tool call, in order.
    conflict_decisions: list = field(default_factory=list)


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
    means the deterministic keyword selector). ``intent_advisor`` is an optional
    :class:`puspa.intent.turn_wiring.IntentTurnAdvisor`: when wired in, the loop consults it
    before dispatching a tool call to the registry — on ``ask`` the call is not run and the model
    gets a payload telling it to ask the user the clarifying question instead (K1). Without an
    advisor the loop behaves exactly as before.
    ``autonomy_dial`` is an optional
    :class:`puspa.autonomy.dial.AutonomyDial`: when wired in, the
    loop consults it before dispatching a registry tool call to
    the area the tool belongs to (K11) — on ``propose`` the call
    is not run and the model gets a payload telling it to propose
    the action to the user instead; on ``do_and_show`` and
    ``do_silently`` the call runs, and the executed outcome is
    reported back to the dial as that area's calibration (a run
    as expected counts correct, an error counts wrong).
    ``dial_area_for_tool`` is an optional ``tool name -> area``
    override for the dial's default tool-to-area mapping; a tool
    the mapping cannot place in an area is not gated by the dial
    (fail-open). Without a dial the loop behaves exactly as
    before.
    ``conflict_gate`` is an optional K13
    :class:`puspa.policy.conflicts.ConflictGate`: consulted after
    the dial, before dispatching a registry tool call — on
    ``surface`` the call is NOT run and the model gets a payload
    telling it to show the conflict to the user instead. The gate
    runs after the dial on purpose: even at ``do_silently`` a
    conflicted action is surfaced, never run silently.
    ``takeover_hold`` is an optional P-09 callable ``agent_id ->
    bool`` (pass a bound
    :meth:`puspa.pwa.live_view.TakeoverStore.is_held`): when it
    reports the agent held, the turn ends as ``interrupted``
    before the next model call and no further tool call runs.
    A hold check that fails fails open, like the advisor and
    the dial: a broken signal must never block the turn.
    ``turn_committer`` is an optional write-through hook, a
    callable ``(*, user_id, task_id, agent_id, reply_text,
    history)`` (see :func:`puspa.memory.commit_turn.
    make_turn_committer`): when wired in, a turn that reached a
    final reply commits that reply through it before it may
    report completed. A committer that raises ends the turn as
    ``failed`` with the failure shown in the message, so a turn
    whose memory did not land never reports success. Without a
    committer the loop behaves exactly as before.
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
        intent_advisor=None,
        autonomy_dial=None,
        dial_area_for_tool=None,
        conflict_gate=None,
        takeover_hold=None,
        turn_committer=None,
        persona_home=None,
        dsn=None,
        model_transport=None,
    ) -> None:
        """Build a turn loop.

        ``persona_home`` is the Puspa home holding the user's
        editable persona files (SOUL.md, IDENTITY.md, USER.md;
        see ``puspa.persona.files``). When given, the files are
        created with defaults if missing, and the leading system
        message is rebuilt from the *active* persona fresh on every
        turn (see ``puspa.persona.switcher``: several personas can
        live under ``<home>/personas/``, one active at a time; with
        no extra persona the home's own files are the persona).
        When None (the default) the loop never touches the persona
        files and behaves exactly as before.

        ``dsn`` is the Postgres DSN the per-capability routing
        table is read from (COW-08). When a turn runs with a
        ``user_id`` and no explicit ``model_call`` (neither on
        the turn nor on the loop), the loop builds the routed
        adapter for that user, so the turn's primary model tier
        goes to the user's routed chat provider. With no
        ``user_id`` the loop behaves exactly as before.
        ``model_transport`` is the httpx transport the routed
        adapter uses (tests pass a ``MockTransport``); with none
        the adapter builds a real client per call.
        """
        self.config = config or TurnConfig()
        self.model_call = model_call
        self.dsn = dsn
        self.model_transport = model_transport
        self.tool_call = tool_call
        self.intercepts = intercepts if intercepts is not None else InterceptedTools()
        self.refresher = refresher
        self.quota_store = quota_store
        self.budget = budget
        self.compressor = compressor
        self.tool_catalog = list(tool_catalog) if tool_catalog is not None else None
        self.narrowing_skills = list(narrowing_skills)
        self.narrowing_selector = narrowing_selector
        self.intent_advisor = intent_advisor
        self.autonomy_dial = autonomy_dial
        self.dial_area_for_tool = dict(dial_area_for_tool) if dial_area_for_tool else {}
        self.conflict_gate = conflict_gate
        self.takeover_hold = takeover_hold
        self.turn_committer = turn_committer
        self._refusal_message = ""
        self._event: list[dict] = []
        self.persona_home = Path(persona_home).expanduser() if persona_home is not None else None
        #: The persona block this loop injected on the previous
        #: turn, so the next turn can strip it before rebuilding
        #: (the caller's own system text is kept exactly once).
        self._persona_block = ""
        if self.persona_home is not None:
            # First use creates the files with defaults so the
            # user has something to edit. Fail-open: a home that
            # cannot be written must not stop the loop from
            # starting; the per-turn load falls back to defaults.
            with contextlib.suppress(Exception):
                from puspa.persona.files import ensure_persona_files

                ensure_persona_files(home=self.persona_home)
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

        E-18: the turn runs under one trace id (the task id) in a
        ``turn`` span, and its latency, cost, and abandoned cost
        are recorded in the observability metrics. Observability
        never changes the result: a metrics failure is swallowed.

        When this loop was built with ``persona_home``, the leading
        system message is rebuilt from the user's persona files
        fresh on every turn (see :meth:`_refresh_persona_system`):
        an edit to SOUL.md, IDENTITY.md, or USER.md changes what
        the model sees on the very next turn.
        """
        trace_id = task_id or f"turn-{uuid.uuid4().hex[:12]}"
        started = time.monotonic()
        token = observability_tracing.start_trace(trace_id)
        result: TurnResult | None = None
        try:
            with observability_tracing.span(
                "turn",
                kind="turn",
                attributes={"task_id": trace_id, "agent_id": agent_id},
                task_id=trace_id,
            ):
                result = self._run_turn_impl(
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
            return result
        finally:
            with contextlib.suppress(Exception):
                latency_s = time.monotonic() - started
                status = result.status if result is not None else "error"
                observability_metrics.get_metrics().record_turn_latency(
                    latency_s, status=status, task_id=trace_id, user_id=user_id
                )
                if result is not None and result.cost_rp:
                    observability_metrics.get_metrics().record_cost(
                        trace_id, user_id, result.cost_rp
                    )
                    if result.status == TURN_INTERRUPTED:
                        observability_metrics.get_metrics().record_abandoned_cost(
                            trace_id, user_id, result.cost_rp
                        )
            observability_tracing.end_trace(token)

    def _run_turn_impl(
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
        """The turn itself, after observability is set up (see :meth:`run_turn`)."""
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

    def _refresh_persona_system(self, history: list[InternalMessage]) -> list[InternalMessage]:
        """Rebuild the leading system message from the active persona.

        The active persona's files are read fresh on every turn (no
        cache), so an edit to SOUL.md, IDENTITY.md, or USER.md — or
        a switch to another persona — takes effect on the next turn.
        The block this loop injected on the previous turn is
        stripped first, so the caller's own system text survives
        exactly once and the block never accumulates across turns.
        A malformed file falls back to its default and is recorded
        on the sink as a ``persona_file_warning`` event; the turn
        still runs.
        """
        from puspa.persona.files import render_persona_block
        from puspa.persona.switcher import load_active_persona_files

        persona = load_active_persona_files(home=self.persona_home)
        for warning in persona.warnings:
            self._record("persona_file_warning", {"warning": warning})
        block = render_persona_block(persona)
        previous = self._persona_block
        self._persona_block = block
        if history and history[0].role == "system":
            text = history[0].text
            if previous and text.startswith(previous):
                base = text[len(previous) :].lstrip("\n")
            else:
                base = text
            new_text = block + ("\n\n" + base if base else "") if block else base
            history[0] = InternalMessage.system(new_text)
        elif block:
            history = [InternalMessage.system(block), *history]
        return history

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
        if self.persona_home is not None:
            history = self._refresh_persona_system(history)
        repairs: list[str] = []
        notes: list[str] = []
        attempts: list = []
        hops: list = []
        intent_decisions: list = []
        dial_decisions: list = []
        conflict_decisions: list = []
        cost_rp = 0
        tier_used = ""
        # E-18: time to first token is the first model reply's
        # arrival (the model call is not streamed).
        ttft_recorded = False
        call_started = time.monotonic()
        self._record(
            "turn_started",
            {"agent_id": agent_id, "task_id": task_id, "messages": len(history)},
        )
        while True:
            checkpoint = take_checkpoint(history)
            try:
                self._check_takeover_hold(agent_id)
            except TurnInterrupted as exc:
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
                    reason=str(exc),
                )
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

            # COW-08: with no explicit model call anywhere and a
            # user on the turn, the primary tier goes to the user's
            # routed chat provider (the loop's own adapter is built
            # per turn, so a Settings change applies immediately).
            # An explicit model_call (turn or loop) always wins,
            # and a turn with no user keeps the old default.
            effective_model_call = model_call if model_call is not None else self.model_call
            if effective_model_call is None and user_id is not None:
                from puspa.turns.turn_api import make_routed_model_call

                effective_model_call = make_routed_model_call(
                    user_id=user_id,
                    dsn=self.dsn,
                    transport=self.model_transport,
                    temperature=self.config.temperature,
                    max_tokens=self.config.max_tokens,
                    timeout=self.config.call_timeout_seconds,
                )

            try:
                call_started = time.monotonic()
                outcome = call_model(
                    history,
                    config=self.config,
                    model_call=effective_model_call,
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
            if not ttft_recorded:
                with contextlib.suppress(Exception):
                    observability_metrics.get_metrics().record_ttft(
                        time.monotonic() - call_started, task_id=task_id, user_id=user_id
                    )
                ttft_recorded = True
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
                commit_failure = self._run_turn_committer(
                    user_id=user_id,
                    agent_id=agent_id,
                    task_id=task_id,
                    reply=reply,
                    history=history,
                )
                if commit_failure is not None:
                    return self._finish_failed(
                        checkpoint,
                        iteration_budget,
                        commit_failure,
                        repairs,
                        notes,
                        attempts,
                        hops,
                        cost_rp,
                        agent_id,
                        task_id,
                    )
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
                    intent_decisions=intent_decisions,
                    dial_decisions=dial_decisions,
                    conflict_decisions=conflict_decisions,
                )
            try:
                history, turn_intent, turn_dial, turn_conflict = self._run_tool_calls(
                    history, reply, agent_id=agent_id, task_id=task_id, tool_call=tool_call
                )
            except TurnInterrupted as exc:
                # A takeover hold landed mid-turn: no further tool
                # call ran (the check inside _run_tool_calls fires
                # before each dispatch), and the turn ends here.
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
                    reason=str(exc),
                )
            intent_decisions.extend(turn_intent)
            dial_decisions.extend(turn_dial)
            conflict_decisions.extend(turn_conflict)

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
    ) -> tuple[list[InternalMessage], list, list, list]:
        """Dispatch the reply's tool calls; return (history, intent decisions, dial decisions, conflict decisions).

        Intercepted tools run first, as before — they are internal
        state operations, not user-facing actions, so the intent
        advisor, the autonomy dial, and the conflict gate never
        see them. For a registry tool, when an intent advisor is wired in, the
        advisor is consulted first with the last user message: on
        ``ask`` the tool is NOT run and the model gets the ask
        payload (with the clarifying question) as the tool result
        instead; on ``act`` the call runs exactly as before. An
        advisor failure never breaks the turn: it fails open to
        running the call.

        When an autonomy dial is wired in (K11), it is consulted
        next, for the area the tool belongs to: on ``propose``
        the tool is NOT run and the model gets the dial ask
        payload instead; on ``do_and_show`` / ``do_silently``
        the call runs, and the executed outcome is reported back
        to the dial as that area's calibration (ran as expected
        counts correct, an error counts wrong). A dial failure
        never breaks the turn either: it fails open to running
        the call. A tool the dial cannot place in an area is not
        gated by it.

        A conflict gate (K13, :mod:`puspa.turns.conflict_wiring`)
        is consulted last, after the dial: on ``surface`` the tool
        is NOT run and the model gets the surface payload instead.
        Even at ``do_silently`` a conflicted action is surfaced.
        """
        registry = tool_call if tool_call is not None else self.tool_call
        intent_decisions: list = []
        dial_decisions: list = []
        conflict_decisions: list = []
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
            if self.intent_advisor is not None:
                decision = self._consult_intent_advisor(name, arguments, history)
                if decision is not None:
                    intent_decisions.append(decision)
                    if decision.outcome == "ask":
                        self._record(
                            "intent_ask",
                            {
                                "agent_id": agent_id,
                                "task_id": task_id,
                                "tool": name,
                                "reason": decision.reason,
                            },
                        )
                        from puspa.intent.turn_wiring import ask_payload

                        history.append(
                            InternalMessage.tool_result(
                                call_id, ask_payload(decision, tool_name=name), is_error=False
                            )
                        )
                        continue
            dial_area: str | None = None
            dial_decision = None
            if self.autonomy_dial is not None:
                dial_decision = self._consult_autonomy_dial(name)
                if dial_decision is not None:
                    dial_area = dial_decision.area
                    dial_decisions.append(dial_decision)
                    if not dial_decision.run:
                        self._record(
                            "dial_ask",
                            {
                                "agent_id": agent_id,
                                "task_id": task_id,
                                "tool": name,
                                "area": dial_area,
                                "stage": dial_decision.stage,
                                "score": dial_decision.score,
                                "reason": dial_decision.reason,
                            },
                        )
                        from puspa.autonomy.dial import dial_ask_payload

                        history.append(
                            InternalMessage.tool_result(
                                call_id,
                                dial_ask_payload(dial_decision, tool_name=name),
                                is_error=False,
                            )
                        )
                        continue
            if registry is not None:
                # P-09: a takeover hold stops the turn before the
                # next tool call runs — never after it started.
                # (Intercepted tools above are internal state ops,
                # not user-facing actions, so the hold does not
                # block them, like the dial and the conflict gate.)
                self._check_takeover_hold(agent_id)
                # The conflict gate (K13): last check before the call runs, after the dial.
                if self.conflict_gate is not None:
                    conflict_decision, event, message = apply_conflict_gate(
                        self.conflict_gate, name, agent_id, task_id, call_id
                    )
                    if conflict_decision is not None:
                        conflict_decisions.append(conflict_decision)
                        if event is not None and message is not None:
                            self._record("conflict_surfaced", event)
                            history.append(message)
                            continue
                try:
                    # E-18: one ``tool`` span per registry call.
                    with observability_tracing.span(
                        f"tool:{name}",
                        kind="tool",
                        attributes={"task_id": task_id or "", "agent_id": agent_id, "tool": name},
                        task_id=task_id,
                    ):
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
                if dial_decision is not None and dial_area is not None:
                    # The executed outcome feeds the dial as this
                    # area's calibration: ran as expected counts
                    # correct, an error counts wrong. A failure
                    # here never breaks the turn.
                    with contextlib.suppress(Exception):
                        self.autonomy_dial.record_outcome(
                            dial_area, correct=not is_error, note=f"tool {name}"
                        )
                    self._record(
                        "dial_silent" if not dial_decision.show else "dial_show",
                        {
                            "agent_id": agent_id,
                            "task_id": task_id,
                            "tool": name,
                            "area": dial_area,
                            "stage": dial_decision.stage,
                            "score": dial_decision.score,
                        },
                    )
            else:
                result_text = (
                    f"tool {name!r} is not available: no tool registry is wired "
                    "into this turn loop (pass tool_call=... to TurnLoop)"
                )
                is_error = True
            history.append(InternalMessage.tool_result(call_id, result_text, is_error=is_error))
        return history, intent_decisions, dial_decisions, conflict_decisions

    def _consult_intent_advisor(self, name: str, arguments: dict, history) -> ActAskDecision | None:
        """Ask the intent advisor about one tool call, failing open.

        Returns the advisor's ``ActAskDecision``, or None when the
        advisor raised (the call then runs as if no advisor were
        wired in — a broken signal must never block the turn).
        """
        try:
            return self.intent_advisor.advise(_last_user_text(history), name, arguments)
        except Exception:
            return None

    def _consult_autonomy_dial(self, name: str):
        """Ask the autonomy dial about one tool call, failing open.

        Returns the dial's ``DialDecision`` for the tool's area,
        or None when the tool cannot be placed in an area or the
        dial raised (the call then runs as if no dial were wired
        in — a broken signal must never block the turn).
        """
        try:
            from puspa.autonomy.dial import area_for_tool

            area = area_for_tool(name, overrides=self.dial_area_for_tool or None)
            if area is None:
                return None
            return self.autonomy_dial.advise(area)
        except Exception:
            return None

    def _check_takeover_hold(self, agent_id: str) -> None:
        """Raise :class:`TurnInterrupted` when the agent is held (P-09).

        Called before every model call and every registry tool
        call. No hold callable wired in means no hold: the loop
        behaves exactly as before. A hold check that fails fails
        open (the narrowed failure modes are a database error
        from the store and a bad agent id or callable) — a broken
        signal must never block the turn, like the advisor and
        the dial above.
        """
        if self.takeover_hold is None:
            return
        try:
            held = bool(self.takeover_hold(agent_id))
        except (AttributeError, TypeError, ValueError, OSError):
            return
        except PsycopgError:
            # PsycopgError is a (sqlite, psycopg) tuple alias: it
            # cannot sit inside another except tuple, so it gets
            # its own clause with the same fail-open body.
            return
        if held:
            raise TurnInterrupted(
                f"turn held: agent {agent_id!r} was taken over by its user; "
                "autonomous work is paused until the takeover is released"
            )

    # -- finishing -------------------------------------------------------

    def _record(self, event_type: str, content: dict) -> None:
        with contextlib.suppress(Exception):
            self.sink(event_type, content)

    def _run_turn_committer(self, *, user_id, agent_id, task_id, reply, history) -> str | None:
        """Write-through at turn completion; failure message or None.

        No committer wired in means nothing to commit: returns
        None and the turn completes as before. A committer that
        raises ends the turn failed: the message names the
        failure, and the sink carries a ``turn_commit_failed``
        event so the failure is shown, not swallowed.
        """
        if self.turn_committer is None:
            return None
        try:
            self.turn_committer(
                user_id=user_id,
                task_id=task_id,
                agent_id=agent_id,
                reply_text=reply.text,
                history=list(history),
            )
        except Exception as exc:
            message = (
                "turn did not complete: the turn's memory could not be written "
                f"({exc}); the reply was not saved"
            )
            self._record(
                "turn_commit_failed",
                {"agent_id": agent_id, "task_id": task_id, "reason": str(exc)},
            )
            return message
        return None

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
        intent_decisions=None,
        dial_decisions=None,
        conflict_decisions=None,
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
            intent_decisions=list(intent_decisions) if intent_decisions is not None else [],
            dial_decisions=list(dial_decisions) if dial_decisions is not None else [],
            conflict_decisions=list(conflict_decisions) if conflict_decisions is not None else [],
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
        reason: str | None = None,
    ) -> TurnResult:
        restored = restore_checkpoint(checkpoint)
        note = interruption_note()
        event_content: dict = {
            "agent_id": agent_id,
            "task_id": task_id,
            "iterations_used": budget.used,
        }
        result_notes = [*notes, recovery_note("interrupted by the user")]
        if reason is not None:
            # A takeover hold, not a user interrupt: the message
            # says so, and the sink event carries the reason so
            # the live view can tell the two apart.
            note = reason
            event_content["reason"] = reason
            result_notes = [*notes, recovery_note(reason)]
        self._record("turn_interrupted", event_content)
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
            notes=result_notes,
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
