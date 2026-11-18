"""Plugin loader + discovery tests (G27).

Covers the three discovery sources (user / project / pip entry
point), loading by module path, file path, and module object,
rejection of malformed plugins, plugin-id shadowing, and the
single-select kinds (memory provider + context engine) with
the G24 bridge. In-memory only: no database, no network.
"""

from __future__ import annotations

import importlib.metadata
import sys
import textwrap
from pathlib import Path

import pytest

from puspa.memory.provider import ProviderRegistry
from puspa.plugins.errors import (
    PluginError,
    PluginShadowError,
    PluginStructureError,
    UnknownPluginError,
)
from puspa.plugins.loader import (
    PluginSource,
    discover_plugins,
    load_plugin,
    load_plugins,
    run_command,
)
from puspa.plugins.single_select import (
    install_into_provider_registry,
)

ECHO_MODULE = "puspa.plugins.builtin.echo"
CONTEXT_MODULE = "puspa.plugins.builtin.simple_context"
MEMORY_MODULE = "puspa.plugins.builtin.memory_inmemory"


def _write_plugin(directory: Path, filename: str, body: str) -> Path:
    path = directory / filename
    path.write_text(textwrap.dedent(body), encoding="utf-8")
    return path


# ---------------------------------------------------------------- builtins


def test_load_echo_plugin_by_module_path():
    record = load_plugin(ECHO_MODULE)
    assert record.plugin_id == "echo"
    assert record.origin == "module"
    assert record.summary() == {
        "plugin_id": "echo",
        "origin": "module",
        "location": ECHO_MODULE,
        "tools": ["plugin_echo"],
        "hooks": ["after_run"],
        "commands": ["echo"],
        "single_select": [],
    }
    assert record.tools["plugin_echo"].handler(text="hi") == "echo: hi"
    assert record.tools["plugin_echo"].read_only is True
    assert run_command([record], "echo", ["a", "b"]) == 0


def test_echo_hook_records_events():
    record = load_plugin(ECHO_MODULE)
    hook = record.hooks[0]
    hook.handler("after_run", {"turn": 1})
    assert record.context.shared["events"] == [
        {"event": "after_run", "payload": {"turn": 1}}
    ]


def test_simple_context_tool_feeds_engine():
    record = load_plugin(CONTEXT_MODULE)
    assert record.single_select_names == [("context_engine", "simple")]
    record.tools["remember_note"].handler(
        text="the deploy key is on the shelf", user_id="u1"
    )
    record.tools["remember_note"].handler(
        text="buy milk on the way home", user_id="u1"
    )
    engine = record.single_select.select("context_engine")
    assert record.single_select.active("context_engine") == "simple"
    context = engine.build_context("u1", "where is the deploy key?")
    assert "deploy key" in context
    assert "milk" not in context
    assert engine.build_context("u1", "something unrelated entirely") == ""
    assert engine.build_context("nobody", "deploy key") == ""


def test_memory_provider_plugin_stores_and_queries():
    record = load_plugin(MEMORY_MODULE)
    assert record.single_select_names == [("memory_provider", "inmemory")]
    provider = record.single_select.select("memory_provider")
    stored = provider.store(
        "u1", {"content": "loves strong coffee", "status": "active"}
    )
    assert stored["id"] and stored["content"] == "loves strong coffee"
    assert stored["source"] == "inmemory"
    results = provider.query("u1", "coffee")
    assert len(results) == 1
    assert results[0]["id"] == stored["id"]
    assert provider.query("u1", "tea") == []
    assert provider.query("u2", "coffee") == []


def test_memory_provider_bridge_into_g24_registry():
    record = load_plugin(MEMORY_MODULE)
    provider_registry = ProviderRegistry()
    installed = install_into_provider_registry(
        record.single_select, "inmemory", provider_registry
    )
    assert installed.name == "inmemory"
    from puspa.memory.provider import select_provider

    provider = select_provider(
        "inmemory", registry=provider_registry
    )
    provider.store("u1", {"content": "bridge works"})
    assert provider.query("u1", "bridge")[0]["content"] == "bridge works"
    with pytest.raises(UnknownPluginError):
        install_into_provider_registry(
            record.single_select, "nope", ProviderRegistry()
        )


def test_load_all_builtins_together():
    result = load_plugins(
        [PluginSource(origin="module", location=name, module_name=name)
         for name in (ECHO_MODULE, CONTEXT_MODULE, MEMORY_MODULE)]
    )
    assert result.failures == []
    assert result.loaded_ids == ["echo", "simple_context", "memory_inmemory"]
    assert result.single_select.names("context_engine") == ["simple"]
    assert result.single_select.names("memory_provider") == ["inmemory"]


# ---------------------------------------------------------------- rejection


def test_load_rejects_module_without_register(tmp_path):
    path = _write_plugin(tmp_path, "no_register.py", 'X = 1\n')
    with pytest.raises(PluginStructureError, match="no register"):
        load_plugin(path)


def test_load_rejects_register_with_wrong_arity(tmp_path):
    path = _write_plugin(
        tmp_path, "bad_arity.py", "def register(a, b):\n    pass\n"
    )
    with pytest.raises(PluginStructureError, match="exactly one argument"):
        load_plugin(path)


def test_load_rejects_register_that_raises(tmp_path):
    path = _write_plugin(
        tmp_path,
        "raises.py",
        "def register(ctx):\n    raise ValueError('boom')\n",
    )
    with pytest.raises(PluginStructureError, match="register\\(ctx\\) raised"):
        load_plugin(path)


def test_load_rejects_import_failure(tmp_path):
    path = _write_plugin(
        tmp_path, "bad_import.py", "import definitely_missing_module_xyz\n"
    )
    with pytest.raises(PluginStructureError, match="failed to import"):
        load_plugin(path)


def test_load_rejects_unknown_hook_event(tmp_path):
    path = _write_plugin(
        tmp_path,
        "bad_hook.py",
        "def register(ctx):\n"
        "    ctx.register_hook('on_monday', lambda e, p: None)\n",
    )
    with pytest.raises(PluginStructureError, match="unknown hook event"):
        load_plugin(path)


def test_load_plugins_rejects_shadowed_id_via_modules():
    body = "PLUGIN_ID = 'same-id'\n\ndef register(ctx):\n    pass\n"
    module_a = type(sys)("dup_a")
    module_b = type(sys)("dup_b")
    exec(compile(body, "dup_a", "exec"), module_a.__dict__)
    exec(compile(body, "dup_b", "exec"), module_b.__dict__)
    result = load_plugins([module_a, module_b])
    assert result.loaded_ids == ["same-id"]
    assert len(result.failures) == 1
    assert isinstance(result.failures[0][1], PluginShadowError)
    with pytest.raises(PluginError):
        result.raise_if_failed()


def test_load_plugins_rejects_shadowed_id(tmp_path):
    plugin_dir = tmp_path / "plugins"
    plugin_dir.mkdir()
    _write_plugin(
        plugin_dir,
        "one.py",
        "PLUGIN_ID = 'dup'\n\ndef register(ctx):\n    pass\n",
    )
    _write_plugin(
        plugin_dir,
        "two.py",
        "PLUGIN_ID = 'dup'\n\ndef register(ctx):\n    pass\n",
    )
    result = load_plugins(
        user_dir=tmp_path / "no-such-user-dir",
        project_dir=plugin_dir,
        entry_points=False,
    )
    assert result.loaded_ids == ["dup"]
    assert len(result.failures) == 1
    assert isinstance(result.failures[0][1], PluginShadowError)
    with pytest.raises(PluginError):
        result.raise_if_failed()


def test_single_select_shadowing_rejected():
    from puspa.plugins.single_select import SingleSelectRegistry

    registry = SingleSelectRegistry()
    first = load_plugin(CONTEXT_MODULE, single_select=registry)
    assert first.plugin_id == "simple_context"
    with pytest.raises(PluginShadowError):
        load_plugin(CONTEXT_MODULE, single_select=registry)


def test_single_select_unknown_name_and_env_override(monkeypatch):
    from puspa.plugins.single_select import SingleSelectRegistry

    registry = SingleSelectRegistry()
    load_plugin(CONTEXT_MODULE, single_select=registry)
    with pytest.raises(UnknownPluginError):
        registry.select("context_engine", name="nope")
    monkeypatch.setenv("PUSPA_CONTEXT_ENGINE_PLUGIN", "simple")
    engine = registry.select("context_engine")
    assert engine.name == "simple"
    monkeypatch.setenv("PUSPA_CONTEXT_ENGINE_PLUGIN", "nope")
    with pytest.raises(UnknownPluginError):
        registry.select("context_engine")


# ---------------------------------------------------------------- discovery


def test_discover_user_before_project(tmp_path):
    user_dir = tmp_path / "user"
    project_dir = tmp_path / "project"
    user_dir.mkdir()
    project_dir.mkdir()
    _write_plugin(
        user_dir, "alpha.py", "def register(ctx):\n    pass\n"
    )
    _write_plugin(
        project_dir, "beta.py", "def register(ctx):\n    pass\n"
    )
    (project_dir / "pkg").mkdir()
    (project_dir / "pkg" / "__init__.py").write_text(
        "def register(ctx):\n    pass\n", encoding="utf-8"
    )
    sources = discover_plugins(
        user_dir=user_dir, project_dir=project_dir, entry_points=False
    )
    assert [(s.origin, Path(s.location).name) for s in sources] == [
        ("user", "alpha.py"),
        ("project", "beta.py"),
        ("project", "pkg"),
    ]


def test_discover_missing_dirs_is_empty(tmp_path):
    sources = discover_plugins(
        user_dir=tmp_path / "nope-user",
        project_dir=tmp_path / "nope-project",
        entry_points=False,
    )
    assert sources == []


def test_discover_entry_points(monkeypatch):
    point = importlib.metadata.EntryPoint(
        name="acme", value="puspa.plugins.builtin.echo", group="puspa.plugins"
    )
    monkeypatch.setattr(
        importlib.metadata,
        "entry_points",
        lambda group=None: [point] if group == "puspa.plugins" else [],
    )
    sources = discover_plugins(
        user_dir="/definitely/not/a/dir",
        project_dir="/definitely/not/a/dir",
        entry_points=True,
    )
    assert len(sources) == 1
    assert sources[0].origin == "entry_point"
    assert sources[0].module_name == "puspa.plugins.builtin.echo"
    record = load_plugin(sources[0])
    assert record.plugin_id == "echo"


def test_discover_respects_plugin_dir_env_vars(tmp_path, monkeypatch):
    plugin_dir = tmp_path / "envplugins"
    plugin_dir.mkdir()
    _write_plugin(
        plugin_dir, "envplug.py", "def register(ctx):\n    pass\n"
    )
    monkeypatch.setenv("PUSPA_PLUGIN_DIR", str(plugin_dir))
    monkeypatch.setenv("PUSPA_PROJECT_PLUGIN_DIR", "/definitely/not/a/dir")
    sources = discover_plugins(entry_points=False)
    assert [s.origin for s in sources] == ["user"]


def test_run_command_unknown_raises():
    record = load_plugin(ECHO_MODULE)
    with pytest.raises(UnknownPluginError, match="no loaded plugin"):
        run_command([record], "no-such-command")
