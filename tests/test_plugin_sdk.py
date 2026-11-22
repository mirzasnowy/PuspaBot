"""Stable plugin API (SDK) tests (AST-01).

The versioned plugin interface: a valid v1 plugin loads and
runs; a plugin declaring an unknown or future API version is
refused with a clear error; a manifest missing required fields
is refused; the version-compat check accepts same-major older
minor and rejects a different major; declared permissions are
validated and enforced. The hello-world example in
``docs/plugin-sdk.md`` is loaded here too, so the docs example
can never silently rot.
"""

from __future__ import annotations

import json
import textwrap
from pathlib import Path

import pytest

from puspa.plugins import (
    API_VERSION,
    API_VERSION_MAJOR,
    KNOWN_PERMISSIONS,
    ApiVersion,
    check_api_version,
    is_compatible,
    load_plugin,
    load_plugins,
    parse_api_version,
    validate_manifest,
)
from puspa.plugins.catalog import load_manifest
from puspa.plugins.errors import PluginApiVersionError, PluginStructureError

REPO_ROOT = Path(__file__).resolve().parent.parent
API_SPEC = json.loads(
    (REPO_ROOT / "puspa" / "plugins" / "plugin_api.json").read_text(encoding="utf-8")
)
SDK_DOC = REPO_ROOT / "docs" / "plugin-sdk.md"

VALID_MANIFEST = {
    "id": "acme-widget",
    "name": "Acme Widget",
    "version": "1.0.0",
    "description": "Does a widget thing.",
    "author": "Acme (hello@example.com)",
    "license": "MIT",
    "repository": "https://github.com/acme/widget-plugin",
    "entry_point": "acme_widget.plugin",
    "kind": "general",
    "api_version": 1,
}


def _write_plugin(tmp_path: Path, name: str, body: str) -> Path:
    path = tmp_path / f"{name}.py"
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


def _params():
    return {
        "type": "object",
        "properties": {"text": {"type": "string"}},
        "required": ["text"],
    }


# ---------------------------------------------------------------- version parsing


@pytest.mark.parametrize(
    "value,expected",
    [
        (1, ApiVersion(1, 0)),
        ("1", ApiVersion(1, 0)),
        ("1.0", ApiVersion(1, 0)),
        ("1.0.3", ApiVersion(1, 0)),
        ("2.3", ApiVersion(2, 3)),
        (ApiVersion(1, 2), ApiVersion(1, 2)),
    ],
)
def test_parse_api_version(value, expected):
    assert parse_api_version(value) == expected


@pytest.mark.parametrize("value", ["", "v1", "one", "1.x", "1.0.0.0", "-1", 1.5, True, None])
def test_parse_api_version_rejects_garbage(value):
    with pytest.raises(PluginStructureError, match="not a version"):
        parse_api_version(value)


# ---------------------------------------------------------------- compatibility rule


@pytest.mark.parametrize(
    "declared,current,expected",
    [
        ("1.0", "1.0", True),  # exact match
        ("1.0", "1.3", True),  # same major, older minor: compatible
        ("1.2", "1.3", True),  # same major, older minor: compatible
        ("1.3", "1.3", True),  # same major, same minor
        ("1.4", "1.3", False),  # newer minor than the host
        ("2.0", "1.3", False),  # different major
        ("0.9", "1.3", False),  # different major (older)
        (1, "1.0", True),  # bare int major
        (2, "1.0", False),  # bare int, different major
    ],
)
def test_is_compatible(declared, current, expected):
    assert is_compatible(declared, current) is expected


def test_check_api_version_refuses_major_mismatch_with_guidance():
    with pytest.raises(PluginApiVersionError) as excinfo:
        check_api_version("2.0", plugin_id="future-plugin", current="1.0")
    message = str(excinfo.value)
    assert "future-plugin" in message
    assert "2.0" in message and "1.0" in message
    assert "major" in message


def test_check_api_version_refuses_newer_minor_with_guidance():
    with pytest.raises(PluginApiVersionError) as excinfo:
        check_api_version("1.3", plugin_id="new-plugin", current="1.0")
    message = str(excinfo.value)
    assert "new-plugin" in message
    assert "upgrade PuspaBot" in message


def test_api_version_error_is_a_structure_error():
    assert issubclass(PluginApiVersionError, PluginStructureError)


def test_published_api_version_matches_spec():
    assert API_VERSION_MAJOR == API_SPEC["api_version"] == 1
    assert API_SPEC["api_version_string"] == API_VERSION
    assert list(KNOWN_PERMISSIONS) == API_SPEC["permissions"]


# ---------------------------------------------------------------- loading


def test_valid_v1_plugin_loads_and_runs(tmp_path):
    path = _write_plugin(
        tmp_path,
        "greeter",
        """\
        PLUGIN_ID = "greeter"
        PLUGIN_VERSION = "1.0.0"
        PLUGIN_API_VERSION = "1.0"
        PLUGIN_PERMISSIONS = ["tools", "hooks", "commands"]


        def register(ctx):
            ctx.register_tool(
                "greet",
                "Greet someone.",
                {
                    "type": "object",
                    "properties": {"text": {"type": "string"}},
                    "required": ["text"],
                },
                lambda text: f"hello {text}",
                read_only=True,
            )
            ctx.register_hook("after_run", lambda event, payload: None)
            ctx.register_command("greet", "Greet from the CLI.", lambda argv: 0)
        """,
    )
    record = load_plugin(path)
    assert record.plugin_id == "greeter"
    assert record.api_version == "1.0"
    assert record.permissions == ("tools", "hooks", "commands")
    assert record.context.tools["greet"].handler(text="Ada") == "hello Ada"
    summary = record.summary()
    assert summary["api_version"] == "1.0"
    assert summary["permissions"] == ["tools", "hooks", "commands"]
    assert summary["tools"] == ["greet"]


def test_plugin_without_declared_version_loads_as_current(tmp_path):
    path = _write_plugin(
        tmp_path,
        "legacy",
        """\
        PLUGIN_ID = "legacy"


        def register(ctx):
            ctx.register_tool(
                "legacy_tool", "Old plugin.", {}, lambda: "legacy", read_only=True
            )
        """,
    )
    record = load_plugin(path)
    assert record.api_version == API_VERSION


def test_future_major_api_version_refused(tmp_path):
    path = _write_plugin(
        tmp_path,
        "future",
        """\
        PLUGIN_ID = "future"
        PLUGIN_API_VERSION = "2.0"


        def register(ctx):
            pass
        """,
    )
    with pytest.raises(PluginApiVersionError) as excinfo:
        load_plugin(path)
    message = str(excinfo.value)
    assert "future" in message and "2.0" in message and "1.0" in message


def test_newer_minor_api_version_refused(tmp_path):
    path = _write_plugin(
        tmp_path,
        "too_new",
        """\
        PLUGIN_ID = "too-new"
        PLUGIN_API_VERSION = "1.1"


        def register(ctx):
            pass
        """,
    )
    with pytest.raises(PluginApiVersionError, match="upgrade PuspaBot"):
        load_plugin(path)


def test_garbage_api_version_refused(tmp_path):
    path = _write_plugin(
        tmp_path,
        "garbage",
        """\
        PLUGIN_ID = "garbage"
        PLUGIN_API_VERSION = "v1"


        def register(ctx):
            pass
        """,
    )
    with pytest.raises(PluginStructureError, match="not a version"):
        load_plugin(path)


def test_incompatible_plugin_is_collected_not_fatal(tmp_path):
    good = _write_plugin(
        tmp_path,
        "good_one",
        """\
        PLUGIN_ID = "good-one"
        PLUGIN_API_VERSION = "1.0"


        def register(ctx):
            ctx.register_tool(
                "good_tool", "Works.", {}, lambda: "ok", read_only=True
            )
        """,
    )
    bad = _write_plugin(
        tmp_path,
        "bad_one",
        """\
        PLUGIN_ID = "bad-one"
        PLUGIN_API_VERSION = "3.0"


        def register(ctx):
            pass
        """,
    )
    result = load_plugins(sources=[bad, good])
    assert result.loaded_ids == ["good-one"]
    assert len(result.failures) == 1
    assert isinstance(result.failures[0][1], PluginApiVersionError)


# ---------------------------------------------------------------- manifests


def test_manifest_missing_required_fields_refused(tmp_path):
    for missing in ("license", "api_version", "entry_point", "name"):
        manifest = dict(VALID_MANIFEST)
        del manifest[missing]
        problems = validate_manifest(manifest)
        assert any(missing in p for p in problems), f"{missing} should be required"
    path = tmp_path / "bad.yaml"
    path.write_text(
        "id: bad-one\nname: Bad\nversion: 1.0.0\n"
        "description: x\nauthor: x\nlicense: MIT\n"
        "repository: https://github.com/acme/bad\n"
        "entry_point: bad.plugin\nkind: general\n",
        encoding="utf-8",
    )
    with pytest.raises(PluginStructureError, match="failed admission"):
        load_manifest(path)


@pytest.mark.parametrize("api_version", [1, "1", "1.0"])
def test_manifest_api_version_compatible_forms_pass(api_version):
    manifest = dict(VALID_MANIFEST, api_version=api_version)
    assert validate_manifest(manifest) == []


@pytest.mark.parametrize("api_version", [2, "2.0", "1.1", "v1"])
def test_manifest_api_version_incompatible_refused(api_version):
    manifest = dict(VALID_MANIFEST, api_version=api_version)
    problems = validate_manifest(manifest)
    assert any("api_version" in p for p in problems)


def test_manifest_permissions_validated():
    assert validate_manifest(dict(VALID_MANIFEST, permissions=["tools", "hooks"])) == []
    problems = validate_manifest(dict(VALID_MANIFEST, permissions=["teleport"]))
    assert any("unknown permission" in p for p in problems)
    problems = validate_manifest(dict(VALID_MANIFEST, permissions="tools"))
    assert any("permissions" in p for p in problems)


# ---------------------------------------------------------------- permissions


def test_undeclared_permission_use_refused(tmp_path):
    path = _write_plugin(
        tmp_path,
        "sneaky",
        """\
        PLUGIN_ID = "sneaky"
        PLUGIN_API_VERSION = "1.0"
        PLUGIN_PERMISSIONS = ["hooks"]


        def register(ctx):
            ctx.register_tool(
                "sneaky_tool", "Not declared.", {}, lambda: "x", read_only=True
            )
        """,
    )
    with pytest.raises(PluginStructureError, match="did not declare"):
        load_plugin(path)


def test_manifest_permissions_enforced_on_load(tmp_path):
    path = _write_plugin(
        tmp_path,
        "declared",
        """\
        PLUGIN_ID = "declared"
        PLUGIN_API_VERSION = "1.0"


        def register(ctx):
            ctx.register_tool(
                "declared_tool", "Declared.", {}, lambda: "x", read_only=True
            )
        """,
    )
    with pytest.raises(PluginStructureError, match="did not declare"):
        load_plugin(path, permissions=["hooks"])
    record = load_plugin(path, permissions=["tools", "network"])
    assert record.permissions == ("tools", "network")
    assert "declared_tool" in record.tools


# ---------------------------------------------------------------- docs example


def test_doc_example_plugin_loads_and_runs(tmp_path):
    text = SDK_DOC.read_text(encoding="utf-8")
    start = text.index("```python\n") + len("```python\n")
    end = text.index("```", start)
    code = text[start:end]
    assert 'PLUGIN_API_VERSION = "1.0"' in code
    path = tmp_path / "hello_world.py"
    path.write_text(code, encoding="utf-8")
    record = load_plugin(path)
    assert record.plugin_id == "hello-world"
    assert record.api_version == "1.0"
    result = record.context.tools["hello_world"].handler(name="Ada")
    assert result == "Hello, Ada! This plugin runs on PuspaBot plugin API 1."
