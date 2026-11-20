"""Tests for the affected-test computation + PR lane (E-17, plan 9.1).

The graph is exercised on a synthetic repo tree (no database, no
git): modules that import each other, tests that import them, and
the full-suite triggers. The CLI is exercised end to end through
``scripts/affected.py`` and ``scripts/pr_lane.py --dry-run``.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

import pytest

BUILD_ROOT = Path(__file__).resolve().parent.parent
AFFECTED_PATH = BUILD_ROOT / "scripts" / "affected.py"


def load_affected():
    """Load scripts/affected.py as a module (scripts/ is no package)."""
    spec = importlib.util.spec_from_file_location("affected_under_test", AFFECTED_PATH)
    assert spec is not None and spec.loader is not None
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


@pytest.fixture
def repo(tmp_path):
    """A synthetic repo tree with a known import graph.

    puspa.alpha.core <- puspa.beta.uses_alpha (absolute import)
                     <- puspa.beta.helper (relative import)
    puspa.gamma      <- nothing imports it except its own test
    tests/test_alpha, test_beta import the alpha/beta modules;
    tests/test_gamma imports gamma; tests/test_unrelated imports
    nothing first-party.
    """
    root = tmp_path / "repo"
    (root / "puspa" / "alpha").mkdir(parents=True)
    (root / "puspa" / "beta").mkdir(parents=True)
    (root / "puspa" / "gamma").mkdir(parents=True)
    (root / "tests").mkdir()
    (root / "puspa" / "__init__.py").write_text("")
    (root / "puspa" / "alpha" / "__init__.py").write_text("")
    (root / "puspa" / "alpha" / "core.py").write_text("THING = 1\n")
    (root / "puspa" / "beta" / "__init__.py").write_text("")
    (root / "puspa" / "beta" / "uses_alpha.py").write_text(
        "from puspa.alpha.core import THING\n\nVALUE = THING + 1\n"
    )
    (root / "puspa" / "beta" / "helper.py").write_text(
        "from ..alpha.core import THING\n\nVALUE = THING + 2\n"
    )
    (root / "puspa" / "gamma" / "__init__.py").write_text("")
    (root / "puspa" / "gamma" / "thing.py").write_text("VALUE = 3\n")
    (root / "tests" / "test_alpha.py").write_text(
        "from puspa.alpha.core import THING\n\n\ndef test_alpha():\n    assert THING == 1\n"
    )
    (root / "tests" / "test_beta.py").write_text(
        "from puspa.beta.uses_alpha import VALUE\n"
        "from puspa.beta.helper import VALUE as HELPER_VALUE\n\n\n"
        "def test_beta():\n    assert VALUE == 2\n    assert HELPER_VALUE == 3\n"
    )
    (root / "tests" / "test_gamma.py").write_text(
        "from puspa.gamma.thing import VALUE\n\n\ndef test_gamma():\n    assert VALUE == 3\n"
    )
    (root / "tests" / "test_unrelated.py").write_text(
        "import os\n\n\ndef test_unrelated():\n    assert os.pathsep\n"
    )
    # A cli test that imports the same module: the suite never
    # collects cli/tests (pytest.ini testpaths = tests), so it
    # must never be selected, even though it imports alpha.
    (root / "cli" / "tests").mkdir(parents=True)
    (root / "cli" / "tests" / "test_cli.py").write_text(
        "from puspa.alpha.core import THING\n\n\ndef test_cli():\n    assert THING == 1\n"
    )
    return root


def _graph(repo):
    affected = load_affected()
    graph = affected.ImportGraph(repo)
    graph.build()
    return affected, graph


def _names(paths, repo):
    return sorted(path.relative_to(repo).as_posix() for path in paths)


def test_change_to_leaf_module_selects_direct_and_transitive_tests(repo):
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["puspa/alpha/core.py"])
    assert reason == "graph"
    assert _names(selected, repo) == ["tests/test_alpha.py", "tests/test_beta.py"]


def test_change_to_gamma_selects_only_its_test(repo):
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["puspa/gamma/thing.py"])
    assert reason == "graph"
    assert _names(selected, repo) == ["tests/test_gamma.py"]


def test_cli_test_file_is_never_selected(repo):
    """cli/tests is not collected (testpaths = tests), so a change
    it imports must not select it."""
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["puspa/alpha/core.py"])
    assert reason == "graph"
    assert _names(selected, repo) == ["tests/test_alpha.py", "tests/test_beta.py"]


def test_changed_test_file_selects_itself(repo):
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["tests/test_beta.py"])
    assert reason == "graph"
    assert _names(selected, repo) == ["tests/test_beta.py"]


def test_unimported_module_selects_nothing(repo):
    (repo / "puspa" / "gamma" / "orphan.py").write_text("VALUE = 9\n")
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["puspa/gamma/orphan.py"])
    assert reason == "none"
    assert selected == []


def test_docs_change_selects_nothing(repo):
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["README.md", "docs/guide.md"])
    assert reason == "none"
    assert selected == []


def test_conftest_change_selects_the_full_suite(repo):
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["tests/conftest.py"])
    assert reason.startswith("full-suite:")
    assert _names(selected, repo) == [
        "tests/test_alpha.py",
        "tests/test_beta.py",
        "tests/test_gamma.py",
        "tests/test_unrelated.py",
    ]


def test_migration_change_selects_the_full_suite(repo):
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["puspa/migrations/versions/0023_new.sql"])
    assert reason.startswith("full-suite:")
    assert len(selected) == 4


def test_deleted_module_selects_the_full_suite(repo):
    affected, graph = _graph(repo)
    selected, reason = affected.affected_tests(graph, ["puspa/alpha/gone.py"])
    assert reason.startswith("full-suite:deleted:")
    assert len(selected) == 4


def test_affected_packages_names_changed_and_importers(repo):
    affected, graph = _graph(repo)
    assert affected.affected_packages(graph, ["puspa/alpha/core.py"]) == [
        "puspa.alpha",
        "puspa.beta",
    ]


def test_cli_lists_affected_tests_for_a_change_file(repo, tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_text("puspa/alpha/core.py\n")
    result = subprocess.run(
        [
            sys.executable,
            str(AFFECTED_PATH),
            "--root",
            str(repo),
            "--changed-files",
            str(changed),
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["tests/test_alpha.py", "tests/test_beta.py"]
    assert "puspa.alpha" in result.stderr


def test_pr_lane_dry_run_prints_the_selection(repo, tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_text("puspa/gamma/thing.py\n")
    result = subprocess.run(
        [
            sys.executable,
            str(BUILD_ROOT / "scripts" / "pr_lane.py"),
            "--root",
            str(repo),
            "--changed-files",
            str(changed),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout.splitlines() == ["tests/test_gamma.py"]
    assert "1 test file(s)" in result.stderr


def test_pr_lane_dry_run_with_no_affected_tests(repo, tmp_path):
    changed = tmp_path / "changed.txt"
    changed.write_text("README.md\n")
    result = subprocess.run(
        [
            sys.executable,
            str(BUILD_ROOT / "scripts" / "pr_lane.py"),
            "--root",
            str(repo),
            "--changed-files",
            str(changed),
            "--dry-run",
        ],
        capture_output=True,
        text=True,
        check=False,
        timeout=120,
    )
    assert result.returncode == 0, result.stderr
    assert result.stdout == ""
    assert "0 test file(s)" in result.stderr
