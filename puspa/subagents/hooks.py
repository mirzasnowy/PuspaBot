"""Serial ``subagent_stop`` hook dispatch for the lifecycle (G25).

When a child finishes, the host path fires the ``subagent_stop``
hook once, serially, in registration order — the same serial shape
as the hermes-agent stop hook the report describes. Hooks are
observation-only: a failing hook is recorded in the dispatch report
and never changes the child's terminal state or result.

Follow-up: ``puspa.plugins.context.HOOK_EVENTS`` does not yet list
``subagent_stop``, so plugins cannot register this event through
``PluginContext.register_hook`` today. Until that event is added
(G27 area), stop hooks are passed to the lifecycle as plain
``(event, payload)`` callables. :func:`collect_stop_hooks` already
understands plugin contexts, so it starts working the moment the
event is allowed there.
"""

from __future__ import annotations

from collections.abc import Callable, Iterable
from typing import Any

__all__ = [
    "SUBAGENT_STOP_EVENT",
    "collect_stop_hooks",
    "dispatch_subagent_stop",
]

#: Hook event fired once per finished child, serially.
SUBAGENT_STOP_EVENT = "subagent_stop"


def _hook_name(handler: Callable[..., Any]) -> str:
    return getattr(handler, "__name__", None) or repr(handler)


def dispatch_subagent_stop(
    hooks: Iterable[Callable[..., Any]],
    payload: dict[str, Any],
) -> list[dict[str, Any]]:
    """Fire ``subagent_stop`` hooks serially, in registration order.

    Each hook is called as ``handler("subagent_stop", payload)``.
    A hook that raises is isolated: the failure is recorded in the
    returned report and dispatch continues with the next hook. The
    report is one entry per hook, in dispatch order, with ``ok`` and
    (on failure) ``error``.
    """
    report: list[dict[str, Any]] = []
    for handler in hooks:
        try:
            handler(SUBAGENT_STOP_EVENT, payload)
        except Exception as exc:
            report.append({"hook": _hook_name(handler), "ok": False, "error": str(exc)})
        else:
            report.append({"hook": _hook_name(handler), "ok": True, "error": ""})
    return report


def collect_stop_hooks(contexts: Iterable[Any]) -> list[Callable[..., Any]]:
    """Collect ``subagent_stop`` handlers from plugin contexts.

    Each context is expected to expose ``.hooks`` — a list of
    hook definitions with ``.event`` and ``.handler`` (the shape of
    ``puspa.plugins.context.HookDefinition``). Only definitions for
    ``subagent_stop`` are collected, in order.
    """
    collected: list[Callable[..., Any]] = []
    for context in contexts:
        for definition in getattr(context, "hooks", []):
            if getattr(definition, "event", None) == SUBAGENT_STOP_EVENT:
                collected.append(definition.handler)
    return collected
