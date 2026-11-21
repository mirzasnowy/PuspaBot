"""Shared permission gate for computer actions (V2-32).

V2-31's :class:`puspa.computer.hands.DesktopHands` gates every
click twice (token check, then the user prompt); the visual
fallback and the web bridge go through the exact same gate, only
with their own tool names, because a coordinate click is riskier
than a ref click and must never be smuggled in under a
``computer.click`` grant. This module holds the two steps so the
three callers cannot drift apart; it invents no new model.

- :func:`check_token_gate`: ``Gate.authorize_tool`` for one tool
  name; a ``deny`` becomes :class:`PermissionDenied`.
- :func:`ask_user`: the localexec prompt (approve once, approve
  for the session, or deny); a denial raises
  :class:`PermissionDenied`. Pass a prompt object in tests.
"""

from __future__ import annotations

from pathlib import Path

from puspa.localexec.executor import (
    PermissionDecision,
    PermissionDenied,
    PermissionPrompt,
    ask_permission_interactively,
)
from puspa.policy.gate import Gate, GateDenied

__all__ = ["ask_user", "check_token_gate"]


def check_token_gate(gate: Gate | None, token_payload: dict | None, tool: str) -> None:
    """Token-level check for one tool name. No gate or no payload
    means no token check (the caller runs ungated, as V2-31 does
    when it is constructed without them)."""
    if gate is None or token_payload is None:
        return
    try:
        gate.authorize_tool(token_payload, tool)
    except GateDenied as exc:
        raise PermissionDenied(f"desktop action refused by the permission gate: {exc}") from exc


def ask_user(
    label: str,
    prompt: PermissionPrompt | None,
    *,
    session_approved: bool = False,
) -> PermissionDecision:
    """Ask the user before one action. Returns the decision; a
    denial raises PermissionDenied and nothing runs. When
    ``session_approved`` is True the user already approved for
    the session and no prompt is shown."""
    if session_approved:
        return PermissionDecision.APPROVE_SESSION
    if prompt is None:
        decision = ask_permission_interactively(label, Path.cwd())
    elif callable(prompt):
        decision = prompt(label, Path.cwd())
    else:
        decision = prompt.ask(label, Path.cwd())
    if decision is PermissionDecision.DENY:
        raise PermissionDenied(f"denied by the user: {label}")
    return decision
