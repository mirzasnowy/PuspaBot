"""Public subagent lifecycle API (G25).

One :class:`SubagentLifecycle` owns the children of one process:
it mints versioned opaque capability handles, drives children
through the stable states (``PENDING`` -> ``STARTING`` ->
``RUNNING`` -> terminal), and exposes the public operations
``launch`` / ``status`` / ``wait`` / ``cancel`` / ``steer`` /
``result`` / ``reconnect``.

Fail-closed rules, enforced before anything runs:

- A forged or corrupt handle is refused with ``UNKNOWN_HANDLE``
  and can never reach a child.
- Goal, context, and metadata sizes are bounded; oversized input
  is refused, never silently truncated.
- The child toolset must be known and inside the parent toolset;
  an unknown toolset or a widening one is refused. A child toolset
  that is wider than the parent *token* tools is refused too.
- A per-launch working-directory override and a per-tool timeout
  are refused explicitly until they can be supported without
  weakening child isolation.
- Cancel is cooperative: ``cancel`` asks for an interrupt at the
  next safe iteration boundary. The child is ``CANCEL_REQUESTED``
  until the child itself stops, and neither ``wait`` nor ``result``
  claims completion before a terminal state is observed.
- ``reconnect`` after a process restart reports
  ``RECONNECT_UNAVAILABLE`` and never starts a replacement child:
  records live only in this process, keyed by a per-process
  generation id carried in the handle.

The child runs through the existing host path
(:class:`puspa.subagents.host.HostSession`): pool accounting, token
subset, serial stop hooks, pool cleanup, and cost rollup. The child
body is a caller-supplied ``child_fn(ctx) -> str``; the child only
ever sees the frozen view of its toolset names, never the parent
registry, so the parent tools are restored by construction — the
lifecycle still verifies the parent toolset is unchanged at finish
and records the outcome in the finish report.
"""

from __future__ import annotations

import hashlib
import hmac
import threading
import uuid
from collections.abc import Callable
from typing import Any

from puspa.runtime.budget import BudgetTracker
from puspa.runtime.subagents import MAX_SUB_PER_AGENT, MAX_SUBSYSTEM, SubagentError, SubagentPool
from puspa.tools.registry import ToolRegistry, default_registry
from puspa.tools.toolsets import DEFAULT_TOOLSET, ToolsetRegistry, default_toolsets

from .errors import (
    LifecycleError,
    LimitExceededError,
    NotTerminalError,
    ReconnectUnavailableError,
    UnknownHandleError,
    UnsupportedOptionError,
)
from .handles import SubagentHandle, mint_handle
from .host import HostSession, build_child_token_payload
from .limits import MAX_STEER_ITEMS, validate_launch_sizes, validate_steer_text
from .records import ChildRecord
from .results import SubagentResult, build_result
from .runner import ChildCancelled, ChildContext, Runner, ThreadRunner
from .states import SubagentState, is_terminal
from .toolsets import resolved_tools, validate_child_toolset

__all__ = [
    "SubagentLifecycle",
]


class SubagentLifecycle:
    """Public lifecycle for subagent children in this process."""

    def __init__(
        self,
        *,
        pool: SubagentPool | None = None,
        toolsets: ToolsetRegistry | None = None,
        registry: ToolRegistry | None = None,
        budget: BudgetTracker | None = None,
        sink: Callable[[str, dict[str, Any]], None] | None = None,
        memory_notify: Callable[[dict[str, Any]], None] | None = None,
        stop_hooks: list[Callable[..., Any]] | None = None,
        runner: Runner | None = None,
    ) -> None:
        self._pool = pool if pool is not None else SubagentPool()
        self._toolsets = toolsets if toolsets is not None else default_toolsets()
        self._registry = registry if registry is not None else default_registry()
        self._host = HostSession(
            pool=self._pool,
            budget=budget,
            sink=sink if sink is not None else self._record_event,
            memory_notify=memory_notify,
            stop_hooks=stop_hooks,
        )
        self._runner = runner if runner is not None else ThreadRunner()
        #: Per-process generation: handles minted here carry it, and
        #: ``reconnect`` refuses handles from any other generation.
        self.generation = uuid.uuid4().hex
        self._records: dict[str, ChildRecord] = {}
        self._token_hashes: dict[str, str] = {}
        self._done: dict[str, threading.Event] = {}
        self._cancel_events: dict[str, threading.Event] = {}
        self._lock = threading.Lock()
        self._event_log: list[dict[str, Any]] = []

    # -- events ---------------------------------------------------------

    def _record_event(self, event_type: str, content: dict[str, Any]) -> None:
        self._event_log.append({"type": event_type, "content": content})

    def event_log(self) -> list[dict[str, Any]]:
        """Events recorded by the default sink (for tests)."""
        return list(self._event_log)

    # -- handles ----------------------------------------------------------

    def _resolve(self, handle: SubagentHandle | str) -> ChildRecord:
        """Resolve a handle to its record, or raise ``UnknownHandleError``.

        The handle must parse, name a record in this process, and
        carry the capability token minted for that record (compared
        in constant time against the stored hash). Any failure is
        the same error: a forged handle learns nothing.
        """
        parsed = handle if isinstance(handle, SubagentHandle) else SubagentHandle.parse(handle)
        with self._lock:
            record = self._records.get(parsed.subagent_id)
            expected = self._token_hashes.get(parsed.subagent_id)
        if record is None or expected is None:
            raise UnknownHandleError("unknown handle")
        presented = hashlib.sha256(parsed.token.encode("utf-8")).hexdigest()
        if not hmac.compare_digest(presented, expected):
            raise UnknownHandleError("unknown handle")
        return record

    # -- launch -----------------------------------------------------------

    def launch(
        self,
        *,
        user_id: str,
        agent_id: str,
        task_id: str,
        goal: str,
        child_fn: Callable[[ChildContext], str],
        context: str = "",
        metadata: dict | None = None,
        toolset: str = DEFAULT_TOOLSET,
        parent_toolset: str = DEFAULT_TOOLSET,
        parent_token: dict | None = None,
        permission_class: str = "Pelaksana",
        working_dir: str | None = None,
        tool_timeout_s: float | None = None,
    ) -> SubagentHandle:
        """Launch a child and return its capability handle.

        ``child_fn`` is the child body: ``(ctx) -> str``, where
        ``ctx`` is a :class:`puspa.subagents.runner.ChildContext`.
        The child runs on the injected runner (a daemon thread by
        default). ``parent_token`` is the verified parent token
        payload when the caller has one; without it the child runs
        under a minimal synthetic parent payload built from the
        child toolset (see :mod:`puspa.subagents.host`).
        """
        if working_dir is not None:
            raise UnsupportedOptionError(
                "per-launch working-directory override is not supported: "
                "it cannot be offered without weakening child isolation"
            )
        if tool_timeout_s is not None:
            raise UnsupportedOptionError(
                "per-tool timeout is not supported: "
                "it cannot be offered without weakening child isolation"
            )
        if not callable(child_fn):
            raise LifecycleError("child_fn must be callable")
        validate_launch_sizes(goal=goal, context=context, metadata=metadata)
        if parent_token is not None and (
            parent_token.get("user_id") != user_id or parent_token.get("agent_id") != agent_id
        ):
            raise LifecycleError("parent token does not belong to this user and agent")
        child_tools = validate_child_toolset(
            child_name=toolset,
            parent_name=parent_toolset,
            toolsets=self._toolsets,
            registry=self._registry,
        )
        parent_tools = resolved_tools(parent_toolset, toolsets=self._toolsets)
        if parent_token is not None:
            token_tools = set(parent_token.get("tools") or [])
            outside_token = sorted(child_tools - token_tools)
            if outside_token:
                raise LifecycleError(
                    "child toolset names tools outside the parent token: "
                    + ", ".join(outside_token)
                )
        subagent_id = f"sub-{uuid.uuid4().hex[:12]}"
        child_token_payload = build_child_token_payload(
            parent_token=parent_token,
            user_id=user_id,
            agent_id=agent_id,
            subagent_id=subagent_id,
            tool_names=child_tools,
            task_id=task_id,
        )
        record = ChildRecord(
            subagent_id=subagent_id,
            agent_id=agent_id,
            task_id=task_id,
            goal=goal,
            context=context,
            metadata=dict(metadata) if metadata else {},
            toolset=toolset,
            parent_toolset=parent_toolset,
            exposed_tools=child_tools,
            parent_exposed_tools=parent_tools,
        )
        with self._lock:
            per_agent = len(self._pool.active_per_agent(agent_id))
            if per_agent >= MAX_SUB_PER_AGENT or self._pool.count_active() >= MAX_SUBSYSTEM:
                raise LifecycleError(
                    "subagent pool is full: "
                    f"{per_agent} active for this agent, {self._pool.count_active()} system-wide"
                )
            try:
                spawned = self._host.spawn(
                    record,
                    parent_token=parent_token,
                    child_token_payload=child_token_payload,
                    permission_class=permission_class,
                )
            except SubagentError as exc:
                raise LifecycleError(str(exc)) from exc
            record.pool_subagent_id = spawned.subagent_id
            handle = mint_handle(subagent_id=subagent_id, generation=self.generation)
            self._records[subagent_id] = record
            self._token_hashes[subagent_id] = handle.token_hash()
            self._done[subagent_id] = threading.Event()
            self._cancel_events[subagent_id] = threading.Event()
        self._record_event(
            "subagent_launched",
            {
                "subagent_id": subagent_id,
                "agent_id": agent_id,
                "task_id": task_id,
                "toolset": toolset,
            },
        )
        try:
            self._runner(record, lambda: self._run_child(record, child_fn))
        except Exception as exc:
            self._finalize(
                record,
                SubagentState.FAILED,
                build_result(state=SubagentState.FAILED, text="", error=str(exc)[:500]),
            )
            raise LifecycleError(f"runner failed to start the child: {exc}") from exc
        return handle

    # -- child execution ----------------------------------------------------

    def _run_child(
        self,
        record: ChildRecord,
        child_fn: Callable[[ChildContext], str],
    ) -> None:
        """Body the runner executes: drive the child to a terminal state."""
        with self._lock:
            if record.finished or record.cancel_requested:
                return
            record.state = SubagentState.STARTING
        ctx = ChildContext(
            record=record,
            cancel_event=self._cancel_events[record.subagent_id],
            lock=self._lock,
        )
        with self._lock:
            if record.finished or record.cancel_requested:
                return
            record.state = SubagentState.RUNNING
        try:
            output = child_fn(ctx)
            if not isinstance(output, str):
                raise LifecycleError("child must return a string result")
        except ChildCancelled:
            self._finalize(
                record,
                SubagentState.CANCELLED,
                build_result(
                    state=SubagentState.CANCELLED,
                    text="",
                    error="cancelled at an iteration boundary",
                ),
            )
        except Exception as exc:
            self._finalize(
                record,
                SubagentState.FAILED,
                build_result(state=SubagentState.FAILED, text="", error=str(exc)[:500]),
            )
        else:
            self._finalize(
                record,
                SubagentState.SUCCEEDED,
                build_result(state=SubagentState.SUCCEEDED, text=output),
            )

    def _verify_parent_tools_restored(self, record: ChildRecord) -> bool:
        """Check the parent toolset is exactly what launch captured.

        The child only ever received a frozen view of its tool
        names, never the registry, so this must always hold; the
        check exists so a future regression is recorded in the
        finish report instead of silently assumed.
        """
        try:
            current = resolved_tools(record.parent_toolset, toolsets=self._toolsets)
        except Exception:
            return False
        return current == record.parent_exposed_tools

    def _finalize(
        self,
        record: ChildRecord,
        state: SubagentState,
        result: SubagentResult,
    ) -> dict[str, Any]:
        """Move a record to a terminal state and run the finish path.

        Idempotent: a record that is already terminal (or already
        finished) is left alone and the first finish report is
        returned. The finish path (memory notify, stop hooks, pool
        cleanup, cost rollup) runs exactly once per child.
        """
        with self._lock:
            if record.finished:
                return {"subagent_id": record.subagent_id, "already_finished": True}
            record.finished = True
            record.state = state
            record.result = result
            record.error = result.error
        parent_tools_restored = self._verify_parent_tools_restored(record)
        report = self._host.finish(record, parent_tools_restored=parent_tools_restored)
        self._done[record.subagent_id].set()
        return report

    def _snapshot(self, record: ChildRecord) -> dict[str, Any]:
        """Current snapshot of a record (caller holds no lock)."""
        with self._lock:
            return {
                "subagent_id": record.subagent_id,
                "agent_id": record.agent_id,
                "task_id": record.task_id,
                "state": record.state.value,
                "toolset": record.toolset,
                "exposed_tools": sorted(record.exposed_tools),
                "cost_rp": record.cost_rp,
                "steer_queued": len(record.steer_queue),
                "cancel_requested": record.cancel_requested,
                "error": record.error,
                "has_result": record.result is not None,
            }

    # -- operations ---------------------------------------------------------

    def status(self, handle: SubagentHandle | str) -> dict[str, Any]:
        """Current snapshot of a child (state, cost, queue, error)."""
        return self._snapshot(self._resolve(handle))

    def wait(
        self,
        handle: SubagentHandle | str,
        timeout_s: float | None = None,
    ) -> SubagentState:
        """Block until the child is terminal or the timeout elapses.

        Returns the state observed when the wait ends — a terminal
        state only when the child actually finished. A timeout
        returns the current non-terminal state; it never claims the
        child is done.
        """
        record = self._resolve(handle)
        self._done[record.subagent_id].wait(timeout=timeout_s)
        with self._lock:
            return record.state

    def cancel(self, handle: SubagentHandle | str) -> SubagentState:
        """Ask for a cooperative cancel at the next safe boundary.

        A ``PENDING`` child (not yet started) is cancelled at once:
        nothing ran, so there is no boundary to wait for. A
        ``STARTING`` / ``RUNNING`` child becomes ``CANCEL_REQUESTED``
        and finishes as ``CANCELLED`` when its next boundary
        observes the request. A terminal child is returned as-is.
        """
        record = self._resolve(handle)
        return SubagentState(self._cancel_record(record)["state"])

    def _cancel_record(self, record: ChildRecord) -> dict[str, Any]:
        """Request cancel for one record (shared by :meth:`cancel`
        and :meth:`cancel_all_for_agent`). Returns the subagent id
        plus the state right after the request. A terminal record
        is reported as-is and never re-finalized."""
        with self._lock:
            if is_terminal(record.state):
                return {"subagent_id": record.subagent_id, "state": record.state.value}
            if record.state is SubagentState.PENDING:
                record.cancel_requested = True
                self._cancel_events[record.subagent_id].set()
                state = SubagentState.CANCELLED
                needs_finalize = True
            else:
                record.cancel_requested = True
                self._cancel_events[record.subagent_id].set()
                record.state = SubagentState.CANCEL_REQUESTED
                state = SubagentState.CANCEL_REQUESTED
                needs_finalize = False
        self._record_event(
            "subagent_cancel_requested",
            {"subagent_id": record.subagent_id, "state": state.value},
        )
        if needs_finalize:
            self._finalize(
                record,
                SubagentState.CANCELLED,
                build_result(
                    state=SubagentState.CANCELLED,
                    text="",
                    error="cancelled before start",
                ),
            )
        return {"subagent_id": record.subagent_id, "state": state.value}

    def live_subagent_ids(self, agent_id: str) -> list[str]:
        """Ids of this agent's children that are not terminal yet."""
        with self._lock:
            return [
                record.subagent_id
                for record in self._records.values()
                if record.agent_id == agent_id and not is_terminal(record.state)
            ]

    def cancel_all_for_agent(self, agent_id: str) -> list[dict[str, Any]]:
        """Request cancel for every live child of one agent (E-11:
        the "interrupt all subagents" step of a stop).

        The same cooperative cancel as :meth:`cancel`, applied to
        all of the agent's live children at once. Returns one entry
        per affected child (subagent id + state after the request);
        children already terminal, or with a cancel already
        requested, are not listed again. Children of other agents
        are untouched.
        """
        with self._lock:
            live = [
                record
                for record in self._records.values()
                if record.agent_id == agent_id
                and not is_terminal(record.state)
                and not record.cancel_requested
            ]
        return [self._cancel_record(record) for record in live]

    def steer(self, handle: SubagentHandle | str, text: str) -> int:
        """Queue steered text for the child's next iteration boundary.

        Returns the queue length after queueing. Steering a terminal
        child is refused, and a full queue (``MAX_STEER_ITEMS``) is
        refused — steered text is never silently dropped.
        """
        validate_steer_text(text)
        record = self._resolve(handle)
        with self._lock:
            if is_terminal(record.state):
                raise LifecycleError("cannot steer a terminal child")
            if len(record.steer_queue) >= MAX_STEER_ITEMS:
                raise LimitExceededError(
                    f"steer queue is full ({MAX_STEER_ITEMS} items); wait for the child to drain it"
                )
            record.steer_queue.append(text)
            queued = len(record.steer_queue)
        self._record_event(
            "subagent_steered",
            {"subagent_id": record.subagent_id, "queued": queued},
        )
        return queued

    def result(self, handle: SubagentHandle | str) -> SubagentResult:
        """The immutable result of a finished child.

        Idempotent: every call returns the same frozen result, with
        the same text and the same stable hash. Raises
        ``NotTerminalError`` while the child is still running.
        """
        record = self._resolve(handle)
        with self._lock:
            if not is_terminal(record.state) or record.result is None:
                raise NotTerminalError(
                    f"child is {record.state.value}; result needs a terminal state"
                )
            return record.result

    def reconnect(self, handle: SubagentHandle | str) -> dict[str, Any]:
        """Re-attach to a live child in this process.

        A handle minted in a previous process generation reports
        ``RECONNECT_UNAVAILABLE``: the child no longer exists here
        and reconnect never starts a replacement. A handle from
        this generation re-attaches and returns the status snapshot.
        """
        parsed = handle if isinstance(handle, SubagentHandle) else SubagentHandle.parse(handle)
        if parsed.generation != self.generation:
            raise ReconnectUnavailableError(
                "handle is from a previous process generation; "
                "the child is gone and reconnect never starts a replacement"
            )
        return self.status(parsed)

    def shutdown(self) -> list[dict[str, Any]]:
        """Abandon every non-terminal child as ``INTERRUPTED``.

        The host is going away: each live child is marked terminal
        with its finish path run once, and any further work its
        thread does is discarded. Returns one status snapshot per
        affected child.
        """
        with self._lock:
            live = [r for r in self._records.values() if not is_terminal(r.state)]
            for record in live:
                record.cancel_requested = True
                self._cancel_events[record.subagent_id].set()
        affected: list[dict[str, Any]] = []
        for record in live:
            self._finalize(
                record,
                SubagentState.INTERRUPTED,
                build_result(
                    state=SubagentState.INTERRUPTED,
                    text="",
                    error="host shutdown",
                ),
            )
            affected.append(self._snapshot(record))
        return affected
