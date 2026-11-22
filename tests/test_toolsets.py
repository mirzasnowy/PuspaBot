"""Tests for toolsets: registration vs exposure (G23).

In-memory only: run with ``pytest tests/test_toolsets.py --noconftest``.
"""

import json
import sys
from pathlib import Path

# --noconftest skips the repo conftest, which is what puts the local
# nanobot clone on sys.path; the fixtures below import the chassis
# adapter, so replicate that one path setup here.
_NANOBOT_LOCAL = Path(__file__).resolve().parent.parent / "nanobot"
if _NANOBOT_LOCAL.is_dir() and str(_NANOBOT_LOCAL) not in sys.path:
    sys.path.insert(0, str(_NANOBOT_LOCAL))

import pytest

from puspa.connectors.allowlist import ToolAllowlist
from puspa.tools.registry import ToolRegistry, discover
from puspa.tools.toolsets import (
    _PUSPA_CORE_TOOLS,
    default_toolsets,
)


@pytest.fixture()
def registry():
    fresh = ToolRegistry()
    discover("puspa.tools", registry=fresh)
    return fresh


@pytest.fixture()
def toolsets():
    return default_toolsets()


def test_core_bundle_is_inherited_by_every_preset(toolsets, registry):
    assert frozenset({"plugin_echo"}) == _PUSPA_CORE_TOOLS
    assert toolsets.exposed_names("default", registry=registry) == ["plugin_echo"]
    # The coding preset inherits the core bundle and adds run_code.
    assert toolsets.exposed_names("coding", registry=registry) == [
        "plugin_echo",
        "run_code",
    ]


def test_registered_but_unnamed_tool_is_never_exposed(toolsets, registry):
    # Register a tool no toolset names: it exists, but no preset exposes it.
    registry.register("secret_tool", "Secret.", {}, lambda: "x")
    assert "secret_tool" in registry
    assert "secret_tool" not in toolsets.exposed_names("default", registry=registry)
    assert "secret_tool" not in toolsets.exposed_names("coding", registry=registry)


def test_toolset_names_a_tool_to_expose_it(toolsets, registry):
    registry.register("secret_tool", "Secret.", {}, lambda: "x")
    toolsets.register_custom("tenant_a", {"secret_tool"})
    assert toolsets.exposed_names("tenant_a", registry=registry) == [
        "plugin_echo",
        "secret_tool",
    ]


def test_custom_toolset_inherits_default_by_default(toolsets, registry):
    toolsets.register_custom("tenant_b", set())
    assert toolsets.exposed_names("tenant_b", registry=registry) == ["plugin_echo"]


def test_custom_toolset_can_opt_out_of_inheritance(toolsets, registry):
    toolsets.register_toolset("bare", {"run_code"}, inherits=())
    assert toolsets.exposed_names("bare", registry=registry) == ["run_code"]


def test_unknown_toolset_exposes_nothing(toolsets, registry):
    assert toolsets.exposed_names("nope", registry=registry) == []


def test_toolset_inheritance_is_transitive(toolsets, registry):
    toolsets.register_toolset("mid", {"run_code"}, inherits=("default",))
    toolsets.register_toolset("top", set(), inherits=("mid",))
    assert toolsets.exposed_names("top", registry=registry) == [
        "plugin_echo",
        "run_code",
    ]


def test_allowlist_is_honoured_even_when_toolset_names_the_tool(toolsets, registry):
    allowlist = ToolAllowlist(allowed_tools={"plugin_echo"})
    assert toolsets.exposed_names("coding", registry=registry, allowlist=allowlist) == [
        "plugin_echo"
    ]


def test_allowlist_empty_set_exposes_nothing(toolsets, registry):
    allowlist = ToolAllowlist(allowed_tools=set())
    assert toolsets.exposed_names("coding", registry=registry, allowlist=allowlist) == []


def test_allowlist_service_axis_is_honoured(toolsets, registry):
    registry.register("svc_tool", "Service.", {}, lambda: "x", service="notes")
    toolsets.register_custom("svc_tenant", {"svc_tool"})
    allowlist = ToolAllowlist(allowed_services={"other"})
    assert "svc_tool" not in toolsets.exposed_names(
        "svc_tenant", registry=registry, allowlist=allowlist
    )
    allowlist = ToolAllowlist(allowed_services={"notes"})
    assert "svc_tool" in toolsets.exposed_names(
        "svc_tenant", registry=registry, allowlist=allowlist
    )


def test_unavailable_tool_is_not_exposed(toolsets, registry):
    registry.register(
        "gated_tool",
        "Gated.",
        {},
        lambda: "x",
        check=lambda profile_id: profile_id == "owner",
    )
    toolsets.register_custom("gated_tenant", {"gated_tool"})
    assert toolsets.exposed_names("gated_tenant", registry=registry, profile_id="guest") == [
        "plugin_echo"
    ]
    assert "gated_tool" in toolsets.exposed_names(
        "gated_tenant", registry=registry, profile_id="owner"
    )


def test_custom_checker_overrides_tool_check(toolsets, registry):
    registry.register("plain_tool", "Plain.", {}, lambda: "x")
    toolsets.register_custom("checked", {"plain_tool"})
    denied = toolsets.exposed_names(
        "checked",
        registry=registry,
        checker=lambda definition, profile_id: False,
    )
    assert denied == []
    allowed = toolsets.exposed_names(
        "checked",
        registry=registry,
        checker=lambda definition, profile_id: True,
    )
    assert "plain_tool" in allowed


def test_exposed_schemas_match_exposed_names(toolsets, registry):
    schemas = toolsets.exposed_schemas("coding", registry=registry)
    names = [schema["function"]["name"] for schema in schemas]
    assert names == ["plugin_echo", "run_code"]
    echo = schemas[0]
    assert echo["function"]["parameters"]["required"] == ["text"]


def test_exposed_schemas_are_valid_json(toolsets, registry):
    schemas = toolsets.exposed_schemas("default", registry=registry)
    json.dumps(schemas)  # must not raise


def test_toolset_registry_lists_presets(toolsets):
    assert toolsets.names() == ["coding", "default", "web"]
