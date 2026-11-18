"""Tests for the footprint ladder (G23).

In-memory only: run with ``pytest tests/test_tool_footprint.py --noconftest``.
"""

from puspa.tools.footprint import (
    LAYERS,
    Capability,
    recommend_layer,
)


def test_ladder_order_is_cheapest_first():
    assert LAYERS == ("cli", "skill", "plugin", "core_tool")


def test_standalone_user_action_is_cli():
    rec = recommend_layer(
        Capability(
            name="export_notes",
            description="Export notes to a file.",
            user_invoked=True,
            needs_execution=True,
        )
    )
    assert rec.layer == "cli"
    assert rec.reasons


def test_plain_capability_defaults_to_cli():
    rec = recommend_layer(Capability(name="convert_file"))
    assert rec.layer == "cli"
    assert "CLI command" in " ".join(rec.reasons)


def test_procedure_is_a_skill():
    rec = recommend_layer(
        Capability(
            name="review_code",
            description="Review a diff against the style guide.",
            is_procedure=True,
        )
    )
    assert rec.layer == "skill"
    assert rec.skipped == ["cli"]


def test_composing_tools_is_a_plugin():
    rec = recommend_layer(
        Capability(
            name="notes_sync",
            description="Sync notes across services with its own state.",
            composes_tools=True,
        )
    )
    assert rec.layer == "plugin"
    assert rec.skipped == ["cli", "skill"]


def test_tenant_scoped_is_a_plugin():
    rec = recommend_layer(
        Capability(
            name="tenant_backup",
            description="Per-tenant backup installed on demand.",
            tenant_scoped=True,
        )
    )
    assert rec.layer == "plugin"


def test_every_turn_need_is_a_core_tool():
    rec = recommend_layer(
        Capability(
            name="search_memory",
            description="Search episodic memory during a turn.",
            needs_every_turn=True,
        )
    )
    assert rec.layer == "core_tool"
    assert rec.skipped == ["cli", "skill", "plugin"]


def test_every_turn_beats_plugin():
    # Even a stateful capability is a core tool when the model needs
    # it on any turn -- the loop requirement wins.
    rec = recommend_layer(
        Capability(
            name="search_memory",
            composes_tools=True,
            tenant_scoped=True,
            needs_every_turn=True,
        )
    )
    assert rec.layer == "core_tool"


def test_plugin_beats_skill():
    rec = recommend_layer(
        Capability(
            name="guided_sync",
            is_procedure=True,
            composes_tools=True,
        )
    )
    assert rec.layer == "plugin"


def test_skill_beats_cli():
    rec = recommend_layer(
        Capability(
            name="write_tests",
            user_invoked=True,
            is_procedure=True,
        )
    )
    assert rec.layer == "skill"


def test_recommendation_always_explains_itself():
    for kwargs in (
        {},
        {"user_invoked": True},
        {"is_procedure": True},
        {"composes_tools": True},
        {"tenant_scoped": True},
        {"needs_every_turn": True},
    ):
        rec = recommend_layer(Capability(name="x", **kwargs))
        assert rec.layer in LAYERS
        assert len(rec.reasons) >= 1
