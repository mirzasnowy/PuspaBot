"""Hierarchical multi-agent runtime (ADK-style, rewritten, not copied).

Patterns studied from google/adk-python (Apache 2.0), reimplemented
as Puspa's own code from the documented behavior:

- Agent hierarchy: a root agent with sub-agents, one parent per
  agent, delegation by role (``LlmAgent.sub_agents`` + transfer).
  ADK 2.0 also offers graph ``Workflow`` orchestration; Puspa keeps
  its own task cycle and adds the three deterministic workflow
  shapes as compositions here: sequential, parallel, loop
  (``SequentialAgent`` / ``ParallelAgent`` / ``LoopAgent``).
- Sessions and state: ``SessionService`` + ``session.state`` with
  namespaced keys. ADK scopes are plain session keys, ``user:``
  (shared across the user's sessions), ``app:`` (shared across the
  app's sessions), and ``temp:`` (current invocation only, never
  persisted). State writes ride on events as deltas; here the
  ``SessionState`` snapshot is the fold, saved through a store.
- Memory: ``MemoryService`` ingests finished sessions and answers
  ``search_memory``. Puspa already has the equivalent access rule:
  a sub-agent never touches memory storage directly; it receives a
  slice through ``puspa.memory.slices.grant_slice`` (recorded in
  ``slice_grants``). ``MemoryAccess`` is the seam to that rule.
- Tool confirmation: ADK guards a tool with explicit confirmation
  (``require_confirmation`` / the HITL tool-confirmation flow) and
  ``before_tool_callback`` / ``after_tool_callback`` hooks around
  tool calls, plus ``before_model_callback`` / ``after_model_callback``
  around model calls. Here confirmation goes through Puspa's own
  approval flow: ``puspa.policy.Gate`` (``request_approval`` /
  ``approve`` / ``execute``). An impactful tool call with no
  confirmation is blocked, fail-closed, never executed.
- Handoff: every parent/child delegation goes through the existing
  ``puspa.runtime.handoff`` contract (10 fields, status machine,
  max 3 handoffs between agents per task, cycle rejection). No new
  bypass path is invented here.

What this module does NOT do: it calls no model and no network.
Step execution is an injected ``executor`` callable
``(node_name, step_name, inputs, state) -> dict``, exactly like
``TaskRunner``. Production wiring injects an executor that calls
the model and tools through the chassis.

Persistence, stated honestly: ``SessionState`` is in-memory by
default (``InMemorySessionStore``), with a ``SessionStore``
save/load interface for a durable backend. There is no Postgres
table for session state yet: adding one needs a schema migration
and an RLS policy, which is a coordinator decision, not a silent
addition by this module. ``puspa.world.store`` was NOT reused: its
table stores world-model beliefs only, not session state. Like
``AgentBrain`` in ``puspa.chassis.agent_session``, state survives
across turns inside one process and is lost on restart until a
durable store is wired in.

Parallel composition is logical, not threaded: children run one
after another in a fixed order and their results are merged, the
same single-threaded execution ``TaskRunner`` uses. Real
concurrency is a follow-up, not a silent claim.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field
from typing import Any, Callable

from puspa.runtime.handoff import (
    Handoff,
    HandoffContract,
    HandoffError,
    check_handoff_limit_between_agents,
    reject_cycle,
)
from puspa.runtime.tasks import Step, Task

__all__ = [
    "AgentNode",
    "CallbackRegistry",
    "COMPOSITIONS",
    "HierarchicalPlan",
    "HierarchicalRunner",
    "HierarchyError",
    "InMemorySessionStore",
    "MemoryAccess",
    "RunResult",
    "SessionState",
    "SessionStore",
    "ToolConfirmation",
    "ToolConfirmationError",
    "TOOL_KINDS",
]

#: Deterministic composition shapes (ADK workflow agents).
COMPOSITIONS = ("sequential", "parallel", "loop", "delegate")

#: Permission classes, same closed set as the rest of the runtime.
PERMISSION_CLASSES = ("Pembaca", "Pelaksana", "Pemeriksa")

#: Tool name to Gate kind for confirmation. Only tools listed here
#: can go through the Gate; an impactful tool with no kind mapping
#: is refused rather than guessed (fail-closed).
TOOL_KINDS = {
    "prepare_payment": "prepare_payment",
    "pay": "prepare_payment",
    "payment": "prepare_payment",
    "gmail_send": "send",
    "gmail.kirim": "send",
    "gmail.send": "send",
    "send": "send",
    "calendar_create_event": "create_event",
    "calendar.create_event": "create_event",
    "create_event": "create_event",
    "delete": "delete",
    "deploy": "deploy",
}

#: Tools that change the world and therefore always need
#: confirmation, even when no Gate is wired in. Read-only tools
#: (search, read, verify) are never in this set.
DEFAULT_IMPACTFUL_TOOLS = frozenset({
    "prepare_payment", "pay", "payment",
    "gmail_send", "gmail.kirim", "gmail.send", "send",
    "calendar_create_event", "calendar.create_event", "create_event",
    "delete", "deploy",
})


class HierarchyError(Exception):
    """Hierarchy, composition, or delegation rule violated."""


class ToolConfirmationError(HierarchyError):
    """An impactful tool call was attempted without confirmation."""


# ----------------------------------------------------------------------
# Agent tree
# ----------------------------------------------------------------------


@dataclass
class AgentNode:
    """One agent in the hierarchy: a role, a parent, children.

    ADK rules kept: a node has at most one parent (single-parent
    rule), the tree has no cycles, and a child is delegated to by
    its role. ``permission_class`` is the Puspa permission class
    the node's work runs under; ``output_key`` is the session-state
    key the node's result is written to (ADK ``output_key``).
    """

    name: str
    role: str
    permission_class: str = "Pembaca"
    instruction: str = ""
    tools: tuple[str, ...] = ()
    output_key: str = ""
    parent: "AgentNode | None" = field(default=None, repr=False)
    children: list["AgentNode"] = field(default_factory=list, repr=False)

    def __post_init__(self) -> None:
        if not self.name or not str(self.name).strip():
            raise HierarchyError("node name must not be empty")
        if not self.role or not str(self.role).strip():
            raise HierarchyError("node role must not be empty")
        if self.permission_class not in PERMISSION_CLASSES:
            raise HierarchyError(
                f"permission_class must be one of {PERMISSION_CLASSES}"
            )
        if not self.output_key:
            self.output_key = self.name

    def add_child(self, child: "AgentNode") -> "AgentNode":
        """Attach a child. Enforces the single-parent rule and no cycles."""
        if child is self:
            raise HierarchyError("an agent cannot be its own child")
        if child.parent is not None:
            raise HierarchyError(
                f"agent {child.name!r} already has a parent "
                f"({child.parent.name!r}); one parent only"
            )
        if self.is_descendant_of(child):
            raise HierarchyError(
                f"adding {child.name!r} under {self.name!r} would create a cycle"
            )
        child.parent = self
        self.children.append(child)
        return child

    def is_descendant_of(self, node: "AgentNode") -> bool:
        """True when this node sits below ``node`` in the tree."""
        current = self.parent
        while current is not None:
            if current is node:
                return True
            current = current.parent
        return False

    @property
    def is_root(self) -> bool:
        return self.parent is None

    def path(self) -> list[str]:
        """Names from the root down to this node."""
        names: list[str] = []
        current: AgentNode | None = self
        while current is not None:
            names.append(current.name)
            current = current.parent
        return list(reversed(names))

    def find_role(self, role: str) -> "AgentNode | None":
        """First node in this subtree (self first) with this role."""
        if self.role == role:
            return self
        for child in self.children:
            found = child.find_role(role)
            if found is not None:
                return found
        return None

    def leaves(self) -> list["AgentNode"]:
        """Leaf nodes of this subtree, in tree order."""
        if not self.children:
            return [self]
        out: list[AgentNode] = []
        for child in self.children:
            out.extend(child.leaves())
        return out


# ----------------------------------------------------------------------
# Session state
# ----------------------------------------------------------------------


class SessionStore:
    """Durable backend for session state snapshots.

    ``save`` stores the snapshot dict for a session id; ``load``
    returns it, or None when the session has no saved state.
    Implementations must be keyed by session id and must not mix
    sessions. The in-memory store below is the default.
    """

    def save(self, session_id: str, snapshot: dict) -> None:
        raise NotImplementedError

    def load(self, session_id: str) -> dict | None:
        raise NotImplementedError


class InMemorySessionStore(SessionStore):
    """Process-local store: state survives across turns, not restarts."""

    def __init__(self) -> None:
        self._sessions: dict[str, dict] = {}

    def save(self, session_id: str, snapshot: dict) -> None:
        self._sessions[str(session_id)] = dict(snapshot)

    def load(self, session_id: str) -> dict | None:
        snapshot = self._sessions.get(str(session_id))
        return dict(snapshot) if snapshot is not None else None


#: Key prefixes and their scope, mirroring ADK state scopes.
SCOPE_PREFIXES = ("user:", "app:", "temp:")


def _scope_of(key: str) -> str:
    for prefix in SCOPE_PREFIXES:
        if key.startswith(prefix):
            return prefix
    return ""


@dataclass
class SessionState:
    """State for one session, namespaced by key prefix.

    - plain keys: this session only.
    - ``user:`` keys: shared across this user's sessions.
    - ``app:`` keys: shared across the app's sessions.
    - ``temp:`` keys: current invocation only, never saved.

    ``temp:`` values are kept apart and stripped on ``save``,
    exactly like ADK strips them before persisting. ``save`` /
    ``load`` go through a ``SessionStore``; the default store is
    in-memory, so without a durable store the state survives
    across turns in one process and is lost on restart.
    """

    session_id: str
    user_id: str = ""
    app_name: str = "puspa"
    store: SessionStore | None = None
    _values: dict[str, Any] = field(default_factory=dict, repr=False)
    _temp: dict[str, Any] = field(default_factory=dict, repr=False)

    def get(self, key: str, default: Any = None) -> Any:
        if key.startswith("temp:"):
            return self._temp.get(key, default)
        return self._values.get(key, default)

    def set(self, key: str, value: Any) -> None:
        if not isinstance(key, str) or not key:
            raise HierarchyError("state key must be a non-empty string")
        if key.startswith("temp:"):
            self._temp[key] = value
        else:
            self._values[key] = value

    def delete(self, key: str) -> None:
        if key.startswith("temp:"):
            self._temp.pop(key, None)
        else:
            self._values.pop(key, None)

    def keys(self, scope: str = "") -> list[str]:
        """Keys in one scope ("" for session scope, else a prefix)."""
        if scope == "temp:":
            return sorted(self._temp)
        return sorted(k for k in self._values if _scope_of(k) == scope)

    def snapshot(self) -> dict:
        """All saved values (``temp:`` keys excluded)."""
        return dict(self._values)

    def save(self) -> None:
        """Persist the snapshot through the store, if one is set."""
        if self.store is None:
            return
        self.store.save(self.session_id, self.snapshot())

    @classmethod
    def load(cls, session_id: str, *, user_id: str = "",
             app_name: str = "puspa",
             store: SessionStore | None = None) -> "SessionState":
        """Load saved state for a session, or start empty."""
        state = cls(session_id=session_id, user_id=user_id,
                    app_name=app_name, store=store)
        if store is not None:
            snapshot = store.load(session_id)
            if snapshot:
                state._values.update(snapshot)
        return state

    def clear_temp(self) -> None:
        """Drop invocation-only values at the end of a turn."""
        self._temp.clear()


# ----------------------------------------------------------------------
# Memory access (slice grants)
# ----------------------------------------------------------------------


class MemoryAccess:
    """Per-agent memory access through recorded slice grants.

    Mirrors ADK ``MemoryService.search_memory`` in shape (a query
    returns the memory a node may see) but enforces Puspa's rule:
    a node never touches memory storage directly. Every read goes
    through ``puspa.memory.slices.grant_slice`` (or an injected
    ``grant`` callable with the same signature), which records the
    grant and returns only the requested columns, at most 5
    episodic hits. The default grant is imported lazily so this
    module stays importable without Postgres.
    """

    def __init__(self, user_id: str, agent_id: str, *,
                 grant: Callable | None = None) -> None:
        self.user_id = user_id
        self.agent_id = agent_id
        self._grant = grant

    def _resolve_grant(self) -> Callable:
        if self._grant is not None:
            return self._grant
        from puspa.memory.slices import grant_slice

        return grant_slice

    def read(self, node: AgentNode, query: str,
             columns: tuple[str, ...] = ("profile", "episodic"),
             *, token_ref: str) -> dict:
        """Memory slice for one node, for one bounded query.

        ``token_ref`` is the node's permission-token reference
        (required: a slice is only granted under a token).
        """
        if not token_ref or not isinstance(token_ref, str):
            raise HierarchyError("token_ref is required for a memory slice")
        grant = self._resolve_grant()
        return grant(self.user_id, self.agent_id, node.name, query,
                     columns, token_ref)


# ----------------------------------------------------------------------
# Tool confirmation
# ----------------------------------------------------------------------


@dataclass
class ToolConfirmation:
    """Confirmation gate for impactful tool calls (ADK HITL).

    With a ``Gate`` wired in, confirmation is the Gate's own
    approval flow: ``request`` files ``request_approval``,
    ``confirm`` calls ``approve``, and ``run`` calls ``execute``
    (which re-checks approval, content hash, token, and spending
    before running anything, exactly once). Without a Gate, an
    in-memory pending registry tracks confirmations instead; the
    blocking rule is identical. Either way, an impactful tool
    call with no confirmation raises ``ToolConfirmationError``
    and the tool never runs.
    """

    user_id: str = ""
    agent_id: str = ""
    token: Any = None
    gate: Any = None
    impactful_tools: frozenset = DEFAULT_IMPACTFUL_TOOLS
    _pending: dict[str, dict] = field(default_factory=dict, repr=False)
    _confirmed: set[str] = field(default_factory=set, repr=False)

    def is_impactful(self, tool: str) -> bool:
        return tool in self.impactful_tools

    def request(self, tool: str, args: dict | None = None,
                *, kind: str | None = None) -> str:
        """Ask for confirmation. Returns the confirmation id."""
        args = dict(args or {})
        if self.gate is not None:
            gate_kind = kind or TOOL_KINDS.get(tool)
            if gate_kind is None:
                raise ToolConfirmationError(
                    f"no Gate kind for impactful tool {tool!r}; refusing"
                )
            request = self.gate.request_approval(
                user_id=self.user_id, agent_id=self.agent_id,
                token=self.token, kind=gate_kind, content=args,
            )
            return request.request_id
        confirmation_id = f"confirm-{uuid.uuid4().hex[:12]}"
        self._pending[confirmation_id] = {"tool": tool, "args": args}
        return confirmation_id

    def confirm(self, confirmation_id: str, user_id: str) -> None:
        """Mark a confirmation as approved by the owning user.

        Approvals must go through this object: a direct
        ``gate.approve`` call is not tracked here.
        """
        if self.gate is not None:
            self.gate.approve(confirmation_id, user_id)
            self._confirmed.add(confirmation_id)
            return
        if confirmation_id not in self._pending:
            raise ToolConfirmationError(
                f"unknown confirmation {confirmation_id!r}"
            )
        self._pending.pop(confirmation_id)
        self._confirmed.add(confirmation_id)

    def is_confirmed(self, confirmation_id: str) -> bool:
        return confirmation_id in self._confirmed

    def run(self, tool: str, args: dict | None, executor: Callable,
            *, confirmation_id: str | None = None) -> Any:
        """Run a tool, blocking impactful calls without confirmation.

        ``executor`` is called as ``executor(tool, args)`` for the
        in-memory path, and as ``executor(content)`` through
        ``Gate.execute`` for the Gate path (the Gate passes the
        approved content). Non-impactful tools run directly.
        """
        args = dict(args or {})
        if not self.is_impactful(tool):
            return executor(tool, args)
        if confirmation_id is None or not self.is_confirmed(confirmation_id):
            raise ToolConfirmationError(
                f"tool {tool!r} needs confirmation before it runs"
            )
        if self.gate is not None:
            return self.gate.execute(
                confirmation_id, executor=lambda content: executor(tool, content))
        self._confirmed.discard(confirmation_id)
        return executor(tool, args)


# ----------------------------------------------------------------------
# Callbacks (ADK before/after model and tool hooks)
# ----------------------------------------------------------------------


@dataclass
class CallbackRegistry:
    """Before/after hooks around model and tool calls.

    A ``before_tool`` callback returns False to block the tool
    call (the runner records a blocked result instead of running
    it). ``after_tool`` callbacks receive the result and may
    return a replacement result. Model callbacks receive the
    node name and the prompt or response text. Callbacks never
    raise into the runner: a failing callback is skipped.
    """

    before_model: list[Callable] = field(default_factory=list)
    after_model: list[Callable] = field(default_factory=list)
    before_tool: list[Callable] = field(default_factory=list)
    after_tool: list[Callable] = field(default_factory=list)

    def run_before_tool(self, node_name: str, tool: str,
                        args: dict) -> bool:
        """True when the tool may run (no callback blocked it)."""
        for callback in self.before_tool:
            try:
                if callback(node_name, tool, args) is False:
                    return False
            except Exception:
                continue
        return True

    def run_after_tool(self, node_name: str, tool: str, args: dict,
                       result: Any) -> Any:
        for callback in self.after_tool:
            try:
                replacement = callback(node_name, tool, args, result)
                if replacement is not None:
                    result = replacement
            except Exception:
                continue
        return result

    def run_before_model(self, node_name: str, prompt: str) -> None:
        for callback in self.before_model:
            try:
                callback(node_name, prompt)
            except Exception:
                continue

    def run_after_model(self, node_name: str, response: str) -> None:
        for callback in self.after_model:
            try:
                callback(node_name, response)
            except Exception:
                continue


# ----------------------------------------------------------------------
# Hierarchical plan and runner
# ----------------------------------------------------------------------


@dataclass
class HierarchicalPlan:
    """A plan with role assignments and a composition shape.

    ``steps`` are the ``Step`` objects from ``puspa.runtime.tasks``;
    ``assignments`` maps a step name to the role of the child node
    that owns it. A step with no assignment runs on the root.
    ``composition`` is one of ``COMPOSITIONS``; ``loop`` repeats
    the steps until ``exit_condition(state)`` is true or
    ``max_iterations`` is reached.
    """

    steps: list[Step]
    assignments: dict[str, str] = field(default_factory=dict)
    composition: str = "sequential"
    max_iterations: int = 3
    exit_condition: Callable[[SessionState], bool] | None = None

    def __post_init__(self) -> None:
        if self.composition not in COMPOSITIONS:
            raise HierarchyError(
                f"composition must be one of {COMPOSITIONS}"
            )
        if self.max_iterations < 1:
            raise HierarchyError("max_iterations must be 1 or more")


@dataclass
class RunResult:
    """Outcome of a hierarchical run."""

    results: dict[str, dict]
    state: SessionState
    handoffs: list[Handoff] = field(default_factory=list)
    iterations: int = 1
    blocked: list[str] = field(default_factory=list)


class HierarchicalRunner:
    """Run a hierarchical plan through a root agent and its children.

    Delegation always goes through the existing handoff contract:
    one ``HandoffContract`` per delegated step, accepted, started,
    handed over, and marked passed, with the per-task handoff
    limit (max 3 between agents) and cycle rejection enforced.
    The root executes steps with no role assignment itself.

    ``executor`` is ``(node_name, step_name, inputs, state) -> dict``
    and must return a schema dict with a ``type`` key (the handoff
    contract rejects anything else). ``tool_resolver`` maps a step
    name to the tool it calls, for confirmation checks; the
    default resolver returns the step name itself.
    """

    def __init__(self, root: AgentNode, *,
                 executor: Callable | None = None,
                 sink: Callable | None = None,
                 pool: Any = None,
                 memory: MemoryAccess | None = None,
                 confirmation: ToolConfirmation | None = None,
                 callbacks: CallbackRegistry | None = None,
                 tool_resolver: Callable[[str], str] | None = None) -> None:
        if not root.is_root:
            raise HierarchyError("the runner root must have no parent")
        self.root = root
        self.executor = executor or (
            lambda node_name, step_name, inputs, state: {
                "type": inputs.get("type", "result"), "value": None})
        self._events: list[dict] = []
        self.sink = sink or (lambda event_type, content: self._events.append(
            {"type": event_type, "content": content}))
        self.pool = pool
        self.memory = memory
        self.confirmation = confirmation
        self.callbacks = callbacks or CallbackRegistry()
        self.tool_resolver = tool_resolver or (lambda step_name: step_name)
        self._handoffs_between_agents = 0
        self._handoff_path: list[str] = [root.name]

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def _record(self, event_type: str, content: dict) -> None:
        try:
            self.sink(event_type, content)
        except Exception:
            pass

    def _delegate(self, node: AgentNode, step: Step,
                  state: SessionState, task_id: str) -> tuple[dict, Handoff]:
        """Run one step on a child node through a handoff contract."""
        check_handoff_limit_between_agents(self._handoffs_between_agents)
        reject_cycle(self._handoff_path, self.root.name, node.name)
        contract = HandoffContract(
            from_id=self.root.name, to_id=node.name,
            purpose=step.name, inputs=step.inputs,
            expected_output=step.expected_output,
            budget=step.budget, permission_class=step.permission_class,
            completion_definition=step.completion_condition or step.name,
        )
        handoff = Handoff(contract)
        self._record("handoff_proposed", {
            "task_id": task_id, "handoff_id": handoff.id,
            "purpose": step.name, "from": self.root.name, "to": node.name,
        })
        handoff.accept()
        handoff.start()
        self._handoffs_between_agents += 1
        self._handoff_path.append(node.name)
        try:
            result = self._execute_on(node, step, state)
        finally:
            self._handoff_path.pop()
        status = handoff.hand_over(result)
        if status == "rejected":
            self._record("handoff_rejected", {
                "task_id": task_id, "handoff_id": handoff.id})
            raise HierarchyError(
                f"step {step.name!r} rejected: output failed its schema")
        handoff.mark_passed()
        self._record("handoff_handed_over", {
            "task_id": task_id, "handoff_id": handoff.id})
        return result, handoff

    def _execute_on(self, node: AgentNode, step: Step,
                    state: SessionState) -> dict:
        """Run one step on one node, with callbacks and confirmation."""
        tool = self.tool_resolver(step.name)
        if not self.callbacks.run_before_tool(node.name, tool, step.inputs):
            return {"type": step.expected_output.get("type", "result"),
                    "blocked": True, "reason": "blocked by before_tool callback"}
        if self.confirmation is not None and self.confirmation.is_impactful(tool):
            raise ToolConfirmationError(
                f"tool {tool!r} for step {step.name!r} needs confirmation "
                "before it runs; request it through ToolConfirmation first")
        result = self.executor(node.name, step.name, step.inputs, state)
        if not isinstance(result, dict) or "type" not in result:
            raise HierarchyError(
                f"step {step.name!r} on {node.name!r} returned a "
                "result without a schema type")
        return self.callbacks.run_after_tool(node.name, tool, step.inputs, result)

    def _run_steps(self, plan: HierarchicalPlan, state: SessionState,
                   task_id: str) -> tuple[dict[str, dict], list[Handoff], list[str]]:
        results: dict[str, dict] = {}
        handoffs: list[Handoff] = []
        blocked: list[str] = []
        for step in plan.steps:
            role = plan.assignments.get(step.name)
            node = self.root.find_role(role) if role else None
            if node is None or node is self.root:
                result = self._execute_on(self.root, step, state)
            else:
                # Spawn a pool record for the child when a pool is
                # given, so the one-level rule and the per-agent /
                # system-wide caps still apply to hierarchy runs.
                # Full token payloads: the pool checks the child
                # token is a subset of the parent token.
                if self.pool is not None:
                    now = int(time.time())
                    agent_token = {
                        "user_id": state.user_id, "agent_id": self.root.name,
                        "subagent_id": None, "tools": list(self.root.tools),
                        "domains": [], "connectors": {},
                        "budget": {"step_max": 25, "token_max": 100000,
                                   "rupiah_max": 0},
                        "exp": now + 3600, "iat": now, "parent": None,
                    }
                    subagent_token = dict(
                        agent_token, subagent_id=node.name,
                        tools=list(node.tools))
                    self.pool.spawn(
                        agent_id=self.root.name, task_id=task_id,
                        permission_class=step.permission_class,
                        agent_token=agent_token,
                        subagent_token=subagent_token,
                        role=node.role,
                    )
                result, handoff = self._delegate(node, step, state, task_id)
                handoffs.append(handoff)
            if isinstance(result, dict) and result.get("blocked"):
                blocked.append(step.name)
            results[step.name] = result
            if node is not None and node is not self.root:
                state.set(node.output_key, result)
            state.set(step.name, result)
        return results, handoffs, blocked

    def run(self, plan: HierarchicalPlan, *,
            state: SessionState | None = None,
            task_id: str = "") -> RunResult:
        """Run a hierarchical plan. Returns the merged results."""
        state = state or SessionState(session_id=task_id or f"session-{uuid.uuid4().hex[:12]}")
        task_id = task_id or state.session_id
        self._handoffs_between_agents = 0
        self._handoff_path = [self.root.name]
        if plan.composition == "loop":
            return self._run_loop(plan, state, task_id)
        results, handoffs, blocked = self._run_steps(plan, state, task_id)
        state.save()
        state.clear_temp()
        return RunResult(results=results, state=state,
                         handoffs=handoffs,
                         iterations=1, blocked=blocked)

    def _run_loop(self, plan: HierarchicalPlan, state: SessionState,
                  task_id: str) -> RunResult:
        """Repeat the steps until the exit condition holds or the cap hits."""
        merged: dict[str, dict] = {}
        blocked: list[str] = []
        all_handoffs: list[Handoff] = []
        iterations = 0
        for iteration in range(1, plan.max_iterations + 1):
            iterations = iteration
            results, iteration_handoffs, iteration_blocked = self._run_steps(
                plan, state, task_id)
            merged.update(results)
            all_handoffs.extend(iteration_handoffs)
            blocked.extend(iteration_blocked)
            if iteration_blocked:
                break
            if plan.exit_condition is not None and plan.exit_condition(state):
                break
        state.save()
        state.clear_temp()
        return RunResult(results=merged, state=state,
                         handoffs=all_handoffs,
                         iterations=iterations, blocked=blocked)

    def run_task(self, task: Task, plan: HierarchicalPlan, *,
                 state: SessionState | None = None) -> tuple[Task, RunResult]:
        """Run a plan for a ``Task`` and fold the outcome into it."""
        result = self.run(plan, state=state, task_id=task.task_id)
        task.result.update(result.results)
        task.status = "failed" if result.blocked else "completed"
        return task, result
