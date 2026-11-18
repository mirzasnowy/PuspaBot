"""Footprint ladder: where a new capability should live (G23).

A new capability should become a core tool only as a last resort.
The ladder, cheapest first:

1. ``cli`` -- a CLI command. The user (or another program) invokes
   it directly; the model does not need it in its loop. Best when
   the capability is a standalone action with clear arguments.
2. ``skill`` -- a skill (guidance + scripts the model follows). Best
   when the capability is mostly knowledge or a procedure: the model
   already has the tools it needs, it just needs to know the steps.
3. ``plugin`` -- a plugin (its own tools, hooks, commands, and
   lifecycle, tenant-scoped). Best when the capability composes
   several tools, keeps its own state, or must be installed and
   removed per tenant.
4. ``core_tool`` -- a tool in the model's function-call loop. Only
   when the model must be able to call it on any turn, with low
   latency, under the token gate.

:func:`recommend_layer` implements the ladder as a decision
function, so the choice is testable and explainable -- not a
document the next author has to interpret.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "LAYERS",
    "Capability",
    "LayerRecommendation",
    "recommend_layer",
]

#: The ladder, cheapest first.
LAYERS: tuple[str, ...] = ("cli", "skill", "plugin", "core_tool")


@dataclass
class Capability:
    """What the new capability is, in decision-relevant terms."""

    name: str
    description: str = ""
    #: The model must be able to call it on any turn, with low
    #: latency, inside the function-call loop.
    needs_every_turn: bool = False
    #: The capability composes several tools, keeps its own state,
    #: or needs install/remove lifecycle per tenant.
    composes_tools: bool = False
    #: Installable and removable per tenant.
    tenant_scoped: bool = False
    #: Mostly knowledge or a procedure: steps the model follows with
    #: tools it already has.
    is_procedure: bool = False
    #: A standalone action the user (or a script) invokes directly.
    user_invoked: bool = False
    #: It executes code or network calls (as opposed to pure text).
    needs_execution: bool = False


@dataclass
class LayerRecommendation:
    """The ladder's answer for one capability."""

    layer: str
    #: Why this layer, in plain language (one reason per rule hit).
    reasons: list[str] = field(default_factory=list)
    #: Layers that were considered and passed over, cheapest first.
    skipped: list[str] = field(default_factory=list)


def recommend_layer(capability: Capability) -> LayerRecommendation:
    """Pick the cheapest ladder layer that fits this capability.

    Rules, in order:

    1. The model needs it every turn -> ``core_tool`` (the only
       rule that reaches the top of the ladder).
    2. It composes tools, keeps state, or is tenant-scoped ->
       ``plugin``.
    3. It is mostly a procedure the model follows -> ``skill``.
    4. Otherwise -> ``cli``: a standalone command first; promote
       it up the ladder only when a rule above fires.
    """
    reasons: list[str] = []
    if capability.needs_every_turn:
        reasons.append(
            "the model must call it on any turn, so it belongs in "
            "the function-call loop as a core tool"
        )
        return LayerRecommendation("core_tool", reasons, skipped=["cli", "skill", "plugin"])
    if capability.composes_tools or capability.tenant_scoped:
        if capability.composes_tools:
            reasons.append(
                "it composes several tools or keeps its own state, so it needs a plugin lifecycle"
            )
        if capability.tenant_scoped:
            reasons.append("it is installed and removed per tenant, so it needs a plugin boundary")
        return LayerRecommendation("plugin", reasons, skipped=["cli", "skill"])
    if capability.is_procedure:
        reasons.append(
            "it is mostly knowledge or a procedure the model follows "
            "with tools it already has, so a skill is enough"
        )
        return LayerRecommendation("skill", reasons, skipped=["cli"])
    if capability.user_invoked:
        reasons.append("the user invokes it directly, so a CLI command is enough")
    else:
        reasons.append(
            "nothing requires the model loop, a plugin lifecycle, or "
            "a skill, so start as a CLI command and promote only "
            "when a rule above fires"
        )
    if capability.needs_execution:
        reasons.append(
            "execution still runs through the CLI command (or a "
            "skill script), not through a new core tool"
        )
    return LayerRecommendation("cli", reasons)
