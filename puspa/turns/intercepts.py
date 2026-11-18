"""Tools intercepted before the tool registry (G21).

Pattern studied, then rewritten as Puspa's own code (see
``docs/reuse.md``): hermes-agent intercepts ``todo``,
``memory``, ``session_search`` and ``delegate_task`` before the
registry, because they change agent state directly and return
synthetic results. Only the PATTERN is taken: no hermes-agent
code is copied.

The interception happens in the turn loop, never in the
registry: the loop asks :class:`InterceptedTools` first, and a
tool it handles NEVER reaches the registry. Every intercepted
call returns a synthetic result dict (``{"intercepted": True,
"tool": ..., ...}``) — the model sees the result as a normal
tool result, but no registry tool ran.

Real stores, not stubs:

- ``todo`` -> :class:`puspa.turns.todos.TodoStore` (in-memory
  per turn — Puspa has NO durable todo store; see that module's
  honest limit).
- ``memory`` -> the real memory stores: ``record_fact`` /
  ``list_active_facts`` / ``hybrid_recall`` over
  ``puspa.memory.facts`` (the store is injected; the default is
  an in-memory ``MemoryFactStore``). The trust gate runs on
  record, exactly as a direct ``record_fact`` call.
- ``session_search`` -> the real session store:
  ``SessionGateway.search_messages`` (FTS5, G7 mechanism). The
  gateway is injected; when none is wired the tool returns a
  synthetic error result saying so — never a fake empty list.
- ``delegate_task`` -> the injected ``delegate`` callable, or
  the real ``SubagentPool.spawn`` when a pool and token payloads
  are given. One level only (the pool enforces it). When
  neither is wired the tool returns a synthetic error result
  saying delegation is unavailable — never a fake subagent id.

An unknown tool name returns None: it is not intercepted, and
the loop passes it to the registry as usual.
"""

from __future__ import annotations

__all__ = ["INTERCEPTED_TOOLS", "InterceptedTools"]

#: Tool names the loop intercepts before the registry.
INTERCEPTED_TOOLS = ("todo", "memory", "session_search", "delegate_task")


class InterceptedTools:
    """The four intercepted tools and the real stores behind them."""

    def __init__(
        self,
        *,
        user_id=None,
        agent_id: str = "puspa",
        todo_store=None,
        memory_store=None,
        session_gateway=None,
        delegate=None,
        subagent_pool=None,
        agent_token: dict | None = None,
        subagent_token: dict | None = None,
    ) -> None:
        self.user_id = user_id
        self.agent_id = agent_id
        self.todo_store = todo_store
        self.memory_store = memory_store
        self.session_gateway = session_gateway
        self.delegate = delegate
        self.subagent_pool = subagent_pool
        self.agent_token = agent_token
        self.subagent_token = subagent_token

    # -- entry point ---------------------------------------------------

    def try_intercept(
        self, name: str, arguments: dict, *, task_id: str | None = None
    ) -> dict | None:
        """Handle an intercepted tool call, or None when not intercepted.

        ``arguments`` must be a dict (the model call already parsed
        it). Returns the synthetic result dict for the four
        intercepted names; returns None for anything else so the
        loop passes it to the registry.
        """
        if name not in INTERCEPTED_TOOLS:
            return None
        if not isinstance(arguments, dict):
            return self._error(name, "tool arguments must be an object")
        handler = {
            "todo": self._todo,
            "memory": self._memory,
            "session_search": self._session_search,
            "delegate_task": self._delegate_task,
        }[name]
        try:
            return handler(arguments, task_id=task_id)
        except ValueError as exc:
            return self._error(name, str(exc))
        except Exception as exc:  # a store failure is a tool error, not a crash
            return self._error(name, f"{type(exc).__name__}: {exc}")

    # -- helpers -------------------------------------------------------

    @staticmethod
    def _ok(name: str, result: dict) -> dict:
        return {"intercepted": True, "tool": name, "result": result}

    @staticmethod
    def _error(name: str, message: str) -> dict:
        return {"intercepted": True, "tool": name, "error": message}

    def _todo_store(self):
        if self.todo_store is None:
            from .todos import TodoStore

            self.todo_store = TodoStore()
        return self.todo_store

    def _memory_store(self):
        if self.memory_store is None:
            from puspa.memory.facts import MemoryFactStore

            self.memory_store = MemoryFactStore()
        return self.memory_store

    # -- todo ----------------------------------------------------------

    def _todo(self, arguments: dict, *, task_id: str | None) -> dict:
        store = self._todo_store()
        task_key = task_id or "default"
        action = arguments.get("action", "list")
        if action == "add":
            item = store.add(task_key, arguments.get("text", ""))
            return self._ok("todo", {"added": item.to_dict()})
        if action == "list":
            items = store.list_items(task_key, open_only=bool(arguments.get("open_only", False)))
            return self._ok("todo", {"items": [item.to_dict() for item in items]})
        if action == "complete":
            item = store.complete(task_key, str(arguments.get("item_id", "")))
            if item is None:
                return self._error("todo", f"unknown todo id: {arguments.get('item_id')!r}")
            return self._ok("todo", {"completed": item.to_dict()})
        if action == "remove":
            removed = store.remove(task_key, str(arguments.get("item_id", "")))
            if not removed:
                return self._error("todo", f"unknown todo id: {arguments.get('item_id')!r}")
            return self._ok("todo", {"removed": arguments.get("item_id")})
        return self._error(
            "todo",
            f"unknown action: {action!r} (want 'add', 'list', 'complete', or 'remove')",
        )

    # -- memory --------------------------------------------------------

    def _memory(self, arguments: dict, *, task_id: str | None) -> dict:
        from puspa.memory.facts import list_active_facts, record_fact
        from puspa.memory.recall import hybrid_recall

        store = self._memory_store()
        action = arguments.get("action", "recall")
        if self.user_id is None:
            return self._error("memory", "no user_id: memory tools need a user to scope facts to")
        if action == "record":
            key = arguments.get("key", "")
            content = arguments.get("content", "")
            result = record_fact(
                self.user_id,
                key,
                content,
                source=arguments.get("source", "user_stated"),
                agent_id=self.agent_id,
                store=store,
            )
            return self._ok("memory", {"recorded": result})
        if action == "list":
            facts = list_active_facts(self.user_id, store=store, agent_id=self.agent_id)
            return self._ok("memory", {"facts": facts})
        if action == "recall":
            query = arguments.get("query", "")
            if not query:
                return self._error("memory", "recall needs a non-empty 'query'")
            facts = list_active_facts(self.user_id, store=store, agent_id=self.agent_id)
            limit = arguments.get("limit", 5)
            recalled = hybrid_recall(query, facts, limit=limit if isinstance(limit, int) else 5)
            return self._ok(
                "memory",
                {
                    "recalled": [
                        {"fact": entry["fact"], "score": entry["score"]} for entry in recalled
                    ]
                },
            )
        return self._error(
            "memory",
            f"unknown action: {action!r} (want 'record', 'list', or 'recall')",
        )

    # -- session_search -------------------------------------------------

    def _session_search(self, arguments: dict, *, task_id: str | None) -> dict:
        if self.session_gateway is None:
            return self._error(
                "session_search",
                "no session store wired: open a session gateway and pass it "
                "to InterceptedTools (session_gateway=...) first",
            )
        query = arguments.get("query", "")
        if not query:
            return self._error("session_search", "session_search needs a non-empty 'query'")
        limit = arguments.get("limit", 10)
        messages = self.session_gateway.search_messages(
            query,
            limit=limit if isinstance(limit, int) else 10,
            session_id=arguments.get("session_id"),
        )
        return self._ok(
            "session_search",
            {
                "matches": [
                    {
                        "session_id": row.get("session_id"),
                        "role": row.get("role"),
                        "content": row.get("content"),
                    }
                    for row in messages
                ]
            },
        )

    # -- delegate_task ---------------------------------------------------

    def _delegate_task(self, arguments: dict, *, task_id: str | None) -> dict:
        purpose = arguments.get("purpose") or arguments.get("task") or ""
        if not purpose:
            return self._error("delegate_task", "delegate_task needs a non-empty 'purpose'")
        permission_class = arguments.get("permission_class", "Pelaksana")
        if self.delegate is not None:
            result = self.delegate(
                purpose=purpose,
                permission_class=permission_class,
                task_id=task_id,
            )
            if not isinstance(result, dict):
                return self._error(
                    "delegate_task",
                    "the delegate callable must return a dict result",
                )
            return self._ok("delegate_task", {"delegated": result})
        if self.subagent_pool is not None:
            if self.agent_token is None or self.subagent_token is None:
                return self._error(
                    "delegate_task",
                    "a subagent pool is wired but no agent/subagent token "
                    "payloads were given: pass agent_token= and "
                    "subagent_token= to InterceptedTools",
                )
            from puspa.runtime.subagents import SubagentError

            try:
                subagent = self.subagent_pool.spawn(
                    agent_id=self.agent_id,
                    task_id=task_id or "default",
                    permission_class=permission_class,
                    agent_token=self.agent_token,
                    subagent_token=self.subagent_token,
                    role=arguments.get("role", ""),
                )
            except SubagentError as exc:
                return self._error("delegate_task", str(exc))
            return self._ok(
                "delegate_task",
                {
                    "delegated": {
                        "subagent_id": subagent.subagent_id,
                        "status": subagent.status,
                        "purpose": purpose,
                    }
                },
            )
        return self._error(
            "delegate_task",
            "no subagent pool wired: pass delegate= or subagent_pool= to InterceptedTools first",
        )
