"""Turn-loop wiring for the conflict gate (Kosmos K13).

This module is the seam between the conflict gate and the
turn loop's dispatch path, kept separate from
:mod:`puspa.turns.loop` so the loop file stays under the
file-size ratchet's 1000-line soft cap (the same split
precedent as the K4/K5 fixes in ``puspa/world/model.py``).

One function, :func:`apply_conflict_gate`: for one proposed
registry tool call it asks the gate (failing open when the
gate raises or the tool has no spec), and when the gate says
``surface`` it returns the ``conflict_surfaced`` event content
and the tool-result message the model gets instead of the tool
running. The loop itself only appends those two and moves on.
"""

from __future__ import annotations

from puspa.policy.conflicts import conflict_surface_payload

from .messages import InternalMessage

__all__ = ["apply_conflict_gate"]


def apply_conflict_gate(
    gate, tool_name: str, agent_id: str, task_id: str | None, call_id: str
) -> tuple[object | None, dict | None, InternalMessage | None]:
    """Ask the conflict gate about one tool call.

    Returns ``(decision, event, message)``:

    - ``decision`` is the gate's ``ConflictDecision``, or None
      when the gate raised (fail-open: the call then runs as
      if no gate were wired in).
    - ``event`` is the ``conflict_surfaced`` event content, or
      None when the decision is ``proceed``.
    - ``message`` is the tool-result message carrying the
      surface payload, or None when the decision is ``proceed``
      (the call runs).

    A ``proceed`` decision carries no event and no message:
    the tool runs exactly as before.
    """
    try:
        decision = gate.check_tool(tool_name)
    except Exception:
        return None, None, None
    if decision.outcome != "surface" or decision.conflict is None:
        return decision, None, None
    conflict = decision.conflict
    return (
        decision,
        {
            "agent_id": agent_id,
            "task_id": task_id,
            "tool": tool_name,
            "conflict_id": conflict.conflict_id,
            "conflict_kind": conflict.kind,
            "reason": decision.reason,
        },
        InternalMessage.tool_result(
            call_id, conflict_surface_payload(decision, tool_name=tool_name), is_error=False
        ),
    )
