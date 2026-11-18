"""Factory wiring for the guardrail set (Wave 2 integration).

A bare ``build_hook`` keeps the old behavior (no rails) unless the
chassis carries a rail set. ``assemble`` is the production
composition root: it builds the default rail set when the chassis
does not carry one, so rails are live in the assembled loop.
"""

from puspa.chassis.factory import (
    PuspaChassis,
    assemble,
    build_hook,
    build_hook_factory,
)
from puspa.guardrails import RailSet, default_rail_set


def test_build_hook_without_rail_set_keeps_old_behavior():
    hook = build_hook(PuspaChassis())
    assert hook.rail_set is None


def test_build_hook_carries_chassis_rail_set():
    rail_set = default_rail_set()
    hook = build_hook(PuspaChassis(rail_set=rail_set))
    assert hook.rail_set is rail_set


def test_build_hook_factory_carries_rail_set():
    rail_set = default_rail_set()
    factory = build_hook_factory(PuspaChassis(rail_set=rail_set))
    hook = factory(object())
    assert hook.rail_set is rail_set


def test_assemble_builds_default_rail_set(monkeypatch):
    import nanobot.agent.loop as loop_module

    captured = {}

    def from_config(cls, config, bus=None, **extra):
        captured.update(extra)
        return object()

    monkeypatch.setattr(loop_module.AgentLoop, "from_config", classmethod(from_config))
    assemble(PuspaChassis(), config="fake-config")
    hooks = captured["hooks"]
    assert len(hooks) == 1
    assert isinstance(hooks[0].rail_set, RailSet)
    factories = captured["hook_factories"]
    assert len(factories) == 1
    assert factories[0](object()).rail_set is hooks[0].rail_set
