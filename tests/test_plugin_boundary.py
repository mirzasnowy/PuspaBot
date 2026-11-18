"""Plugin boundary lint tests (G27).

The lint must pass on the real tree (including the G3/G7/G24
files that already exist) and must fail when a new
core/plugin crossing is introduced. The failure case is proven
against a synthetic tree, never by breaking the real one.
"""

from __future__ import annotations

import shutil
from pathlib import Path

from puspa.plugins.boundary import lint_tree, main

REPO_ROOT = Path(__file__).resolve().parent.parent
API_SPEC_PATH = REPO_ROOT / "puspa" / "plugins" / "plugin_api.json"


def _synthetic_tree(tmp_path: Path) -> Path:
    """A minimal repo tree with the real API spec copied in."""
    root = tmp_path / "repo"
    (root / "puspa" / "plugins").mkdir(parents=True)
    (root / "puspa" / "core.py").write_text("VALUE = 1\n", encoding="utf-8")
    shutil.copy(API_SPEC_PATH, root / "puspa" / "plugins" / "plugin_api.json")
    return root


def test_boundary_lint_clean_on_real_tree():
    violations = lint_tree(REPO_ROOT)
    assert violations == []


def test_boundary_lint_cli_clean_on_real_tree(capsys):
    assert main(["--root", str(REPO_ROOT)]) == 0
    assert "clean" in capsys.readouterr().out


def test_boundary_catches_core_importing_builtin_plugin(tmp_path):
    root = _synthetic_tree(tmp_path)
    (root / "puspa" / "sneaky.py").write_text(
        "from puspa.plugins.builtin import echo\n", encoding="utf-8"
    )
    violations = lint_tree(root)
    assert any(v.rule == "B1" and "puspa.plugins.builtin" in v.message for v in violations)


def test_boundary_catches_plugin_importing_core(tmp_path):
    root = _synthetic_tree(tmp_path)
    (root / "puspa" / "plugins" / "sneaky.py").write_text(
        "from puspa.ledger import Ledger\n", encoding="utf-8"
    )
    violations = lint_tree(root)
    assert any(v.rule == "B2" and "puspa.ledger" in v.message for v in violations)


def test_boundary_catches_plugin_importing_nanobot(tmp_path):
    root = _synthetic_tree(tmp_path)
    (root / "puspa" / "plugins" / "sneaky.py").write_text(
        "from nanobot.agent.tools.base import Tool\n", encoding="utf-8"
    )
    violations = lint_tree(root)
    assert any(v.rule == "B2" and "nanobot" in v.message for v in violations)


def test_boundary_allows_adapter_nanobot_import(tmp_path):
    root = _synthetic_tree(tmp_path)
    (root / "puspa" / "plugins" / "adapters.py").write_text(
        "from nanobot.agent.tools.base import Tool\nfrom nanobot.agent.hook import AgentHook\n",
        encoding="utf-8",
    )
    assert lint_tree(root) == []


def test_boundary_allows_core_importing_loader(tmp_path):
    root = _synthetic_tree(tmp_path)
    (root / "puspa" / "factory.py").write_text(
        "from puspa.plugins.loader import load_plugins\n",
        encoding="utf-8",
    )
    assert lint_tree(root) == []


def test_boundary_catches_builtin_importing_builtin(tmp_path):
    root = _synthetic_tree(tmp_path)
    builtin = root / "puspa" / "plugins" / "builtin"
    builtin.mkdir()
    (builtin / "a.py").write_text("from puspa.plugins.builtin import b\n", encoding="utf-8")
    (builtin / "b.py").write_text("VALUE = 1\n", encoding="utf-8")
    violations = lint_tree(root)
    assert any(v.rule == "B3" for v in violations)


def test_boundary_cli_fails_on_synthetic_tree(tmp_path, capsys):
    root = _synthetic_tree(tmp_path)
    (root / "puspa" / "plugins" / "sneaky.py").write_text(
        "from puspa.ledger import Ledger\n", encoding="utf-8"
    )
    assert main(["--root", str(root)]) == 1
    assert "violation" in capsys.readouterr().out


def test_boundary_allows_published_api_import_in_plugin(tmp_path):
    root = _synthetic_tree(tmp_path)
    (root / "puspa" / "plugins" / "good.py").write_text(
        "from puspa.memory.provider import MemoryProvider\n"
        "from puspa.plugins.context import PluginContext\n"
        "import yaml\n",
        encoding="utf-8",
    )
    assert lint_tree(root) == []
