"""Optional MCP definitions + readiness tests (G27).

Definition format validation (required fields, server shape,
tool list, semver) plus the install-free readiness check:
declared dependencies are verified importable and the server
command is verified resolvable, without installing, downloading,
or running anything.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from puspa.plugins.errors import PluginStructureError
from puspa.plugins.mcp import (
    check_mcp_readiness,
    check_readiness,
    load_mcp_definition,
    validate_mcp_definition,
)

MCP_DIR = Path(__file__).resolve().parent.parent / "puspa" / "plugins" / "mcp_definitions"

VALID_DEFINITION = {
    "id": "local-files-mcp",
    "name": "Local Files MCP",
    "version": "1.0.0",
    "description": "Read local files.",
    "server": {
        "command": "python3",
        "args": ["-m", "local_files_mcp"],
        "env": {},
        "transport": "stdio",
    },
    "tools": [
        {"name": "read_file", "description": "Read a text file."},
        {"name": "list_directory", "description": "List a directory."},
    ],
    "dependencies": ["pyyaml>=6.0"],
}


def _with(**overrides):
    definition = json.loads(json.dumps(VALID_DEFINITION))
    definition.update(overrides)
    return definition


def test_seed_mcp_definitions_load():
    found = sorted(p.name for p in MCP_DIR.glob("*.json"))
    assert found == [
        "http-fetch-mcp.json",
        "local-files-mcp.json",
        "sqlite-mcp.json",
    ]
    for path in sorted(MCP_DIR.glob("*.json")):
        definition = load_mcp_definition(path)
        assert validate_mcp_definition(definition) == []


def test_valid_mcp_definition_passes():
    assert validate_mcp_definition(_with()) == []


def test_mcp_definition_missing_required_field():
    definition = _with()
    del definition["server"]
    assert any("server" in p for p in validate_mcp_definition(definition))


@pytest.mark.parametrize(
    "field,value",
    [
        ("id", "Bad ID!"),
        ("version", "1.0"),
        ("tools", []),
        ("tools", [{"name": "BadName", "description": "x"}]),
        (
            "tools",
            [
                {"name": "dup", "description": "x"},
                {"name": "dup", "description": "y"},
            ],
        ),
        (
            "tools",
            [{"name": "no_desc", "description": "  "}],
        ),
        ("dependencies", ["not a requirement!!!"]),
    ],
)
def test_mcp_definition_invalid_fields(field, value):
    assert validate_mcp_definition(_with(**{field: value}))


@pytest.mark.parametrize(
    "server",
    [
        {"command": ""},
        {"command": "python3", "args": "not-a-list"},
        {"command": "python3", "transport": "carrier-pigeon"},
        {"command": "python3", "env": {"KEY": 1}},
        "not-a-dict",
    ],
)
def test_mcp_definition_invalid_server(server):
    assert validate_mcp_definition(_with(server=server))


def test_load_mcp_definition_rejects_bad_json(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text("{not json", encoding="utf-8")
    with pytest.raises(PluginStructureError, match="not valid JSON"):
        load_mcp_definition(path)


def test_load_mcp_definition_rejects_invalid_definition(tmp_path):
    path = tmp_path / "bad.json"
    path.write_text(json.dumps({"id": "x"}), encoding="utf-8")
    with pytest.raises(PluginStructureError, match="is invalid"):
        load_mcp_definition(path)


# ---------------------------------------------------------------- readiness


def test_check_readiness_present_dependency():
    report = check_readiness(["pyyaml>=6.0"])
    assert report["ready"] is True
    assert report["missing"] == []
    assert report["checks"][0]["kind"] == "dependency"
    assert report["checks"][0]["ready"] is True


def test_check_readiness_missing_dependency_reported_not_raised():
    report = check_readiness(["definitely-missing-package-xyz>=1.0"])
    assert report["ready"] is False
    assert report["missing"] == ["definitely-missing-package-xyz>=1.0"]
    assert "not installed" in report["checks"][0]["detail"]


def test_check_readiness_server_command():
    report = check_readiness([], server_command="python3")
    assert report["ready"] is True
    report = check_readiness([], server_command="definitely-missing-cmd-xyz")
    assert report["ready"] is False
    assert report["missing"] == ["definitely-missing-cmd-xyz"]


def test_check_readiness_mixed():
    report = check_readiness(
        ["pyyaml", "definitely-missing-package-xyz"],
        server_command="python3",
    )
    assert report["ready"] is False
    assert report["missing"] == ["definitely-missing-package-xyz"]
    assert len(report["checks"]) == 3


def test_check_mcp_readiness_on_seed():
    definition = load_mcp_definition(MCP_DIR / "local-files-mcp.json")
    report = check_mcp_readiness(definition)
    # pyyaml is installed and python3 is on PATH: fully ready.
    assert report["ready"] is True
    assert report["missing"] == []


def test_check_mcp_readiness_reports_missing_command():
    definition = _with(server={"command": "definitely-missing-cmd-xyz"})
    report = check_mcp_readiness(definition)
    assert report["ready"] is False
    assert "definitely-missing-cmd-xyz" in report["missing"]
