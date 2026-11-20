"""Affected-test selection for the PR lane (E-17, plan section 9.1).

The PR lane runs only the tests that can observe a change, computed
from Puspa's own import graph — no external tool, no test-name
heuristic. ``build_graph`` walks the Python roots (``puspa/``,
``tests/``, ``demo/``, ``cli/``), parses every file with ``ast``,
and records first-party import edges (file -> file). ``affected``
then takes the changed files and returns the reverse-transitive
closure: every test file that imports (directly or through other
modules) something that changed, plus any changed test file itself.

Rules, in order:

- A changed file that shapes every test (``tests/conftest.py``,
  ``tests/worker_db.py``, ``pytest.ini``, ``pyproject.toml``,
  ``requirements*.txt``, anything under ``.github/`` or
  ``scripts/``, or a migration under
  ``puspa/migrations/versions/``) selects the FULL suite: the
  change is not attributable to one package.
- A changed ``.py`` file that no longer exists on disk (deleted)
  also selects the full suite: its importers would fail at import
  time, and the graph (built from current files) cannot see them.
- Anything else (docs, web assets, data files) selects nothing.
- A changed module nobody imports selects nothing: nothing in the
  suite can observe it.

Output is the selected test paths, one per line, sorted, relative
to the repo root; a full-suite selection prints every test file.
A short summary (affected packages, test count, trigger) goes to
stderr so the path list stays machine-readable.

Usage:

    python scripts/affected.py --base <sha> --head <sha>
    python scripts/affected.py --changed-files <file>   # newline list
    python scripts/affected.py                            # uncommitted diff

Honest limits: only static imports become edges, so dynamic
imports (``importlib.import_module`` with a computed name, a
plugin loaded from a string) are invisible; a test that reaches
a module only through a subprocess or a shell script is invisible
too, except for the full-suite triggers above. The graph is
rebuilt from the working tree on every run, so it never goes
stale — but it also costs a few seconds of AST parsing.
"""

from __future__ import annotations

import argparse
import ast
import subprocess
import sys
from pathlib import Path

# Roots scanned for the import graph, in claim order: the first
# file to claim a module name wins (a tests/ helper shadows a
# same-named cli/tests/ helper; the main suite is what the lane
# runs). nanobot/ is a vendored external clone and is excluded.
SCAN_ROOTS = ("puspa", "tests", "demo", "cli")

#: A change to any of these selects the full suite (see module docstring).
FULL_SUITE_PATHS = frozenset(
    {
        "tests/conftest.py",
        "tests/worker_db.py",
        "pytest.ini",
        "pyproject.toml",
        "requirements.txt",
        "requirements-dev.txt",
        "requirements-hashed.txt",
    }
)
FULL_SUITE_PREFIXES = (".github/", "scripts/", "puspa/migrations/versions/")


class ImportGraph:
    """First-party import edges between Python files in the repo."""

    def __init__(self, root: Path) -> None:
        self.root = root
        #: module name -> file (first claim wins).
        self.module_to_file: dict[str, Path] = {}
        #: file -> files it imports (first-party only).
        self.imports: dict[Path, set[Path]] = {}
        #: file -> files that import it (reverse edges).
        self.imported_by: dict[Path, set[Path]] = {}

    def module_name(self, path: Path) -> str | None:
        """The name this file is imported under, or None to skip it."""
        rel = path.relative_to(self.root)
        parts = rel.parts
        if parts[0] == "puspa":
            dotted = ".".join(parts)[: -len(".py")]
            if parts[-1] == "__init__.py":
                dotted = dotted[: -len(".__init__")]
            return dotted
        if parts[0] in ("tests", "demo"):
            if parts[-1] == "conftest.py":
                return ".".join(parts)[: -len(".py")]
            return path.stem
        if parts[0] == "cli":
            if len(parts) >= 2 and parts[1] == "puspa_cli":
                dotted = ".".join(parts[1:])[: -len(".py")]
                if parts[-1] == "__init__.py":
                    dotted = dotted[: -len(".__init__")]
                return dotted
            return path.stem
        return None

    def build(self) -> None:
        """Parse every scanned file and record its first-party imports."""
        files: list[Path] = []
        for root_name in SCAN_ROOTS:
            root_dir = self.root / root_name
            if not root_dir.is_dir():
                continue
            for path in sorted(root_dir.rglob("*.py")):
                if "__pycache__" in path.parts:
                    continue
                files.append(path)
        for path in files:
            name = self.module_name(path)
            if name is not None and name not in self.module_to_file:
                self.module_to_file[name] = path
        for path in files:
            self.imports[path] = set()
            self.imported_by.setdefault(path, set())
            try:
                tree = ast.parse(path.read_text(encoding="utf-8"))
            except (SyntaxError, UnicodeDecodeError):
                continue
            module = self.module_name(path) or ""
            if path.name == "__init__.py":
                package = module
            elif "." in module:
                package = module.rsplit(".", 1)[0]
            else:
                package = ""
            for node in ast.walk(tree):
                if isinstance(node, ast.Import):
                    for alias in node.names:
                        self._add_edge(path, alias.name)
                elif isinstance(node, ast.ImportFrom):
                    self._add_import_from(path, package, node)
        for importer, imported in self.imports.items():
            for target in imported:
                self.imported_by.setdefault(target, set()).add(importer)

    def _add_edge(self, importer: Path, module: str) -> None:
        """Edge to the longest known prefix of ``module``."""
        parts = module.split(".")
        for end in range(len(parts), 0, -1):
            candidate = ".".join(parts[:end])
            if candidate in self.module_to_file:
                self.imports[importer].add(self.module_to_file[candidate])
                return

    def _add_import_from(self, importer: Path, package: str, node: ast.ImportFrom) -> None:
        if node.level:
            base = _resolve_relative(package, node.level, node.module or "")
        else:
            base = node.module or ""
        if not base:
            return
        for alias in node.names:
            if alias.name == "*":
                self._add_edge(importer, base)
            else:
                # _add_edge falls back to the longest known prefix,
                # so this covers both "from pkg import submodule"
                # (edge to the submodule) and "from pkg import Name"
                # (edge to the package).
                self._add_edge(importer, f"{base}.{alias.name}")

    def reverse_closure(self, changed: set[Path]) -> set[Path]:
        """Every file that (transitively) imports a changed file."""
        seen: set[Path] = set()
        stack = [path for path in changed if path in self.imported_by]
        while stack:
            current = stack.pop()
            for importer in self.imported_by.get(current, ()):
                if importer not in seen:
                    seen.add(importer)
                    stack.append(importer)
        return seen


def _resolve_relative(package: str, level: int, name: str) -> str:
    """Resolve ``from .`` / ``from ..`` to an absolute module name.

    ``package`` is the importing file's package (its own dotted
    name for ``__init__.py`` files, else the parent); ``level`` is
    the number of leading dots.
    """
    parts = package.split(".") if package else []
    up = level - 1
    if up > len(parts):
        return ""
    base = parts[: len(parts) - up] if up else parts
    if name:
        base = [*base, name]
    return ".".join(base)


def _is_test_file(path: Path, root: Path) -> bool:
    """True for files the suite actually collects.

    Only ``tests/`` counts: ``pytest.ini`` sets ``testpaths =
    tests``, so ``cli/tests/`` files are never collected — and
    they cannot even be imported standalone (they need a
    ``byok_demo`` module that does not exist), so selecting them
    breaks the lane with collection errors. ``cli/`` modules stay
    in the import graph as sources (they import Puspa code), only
    their test files are never emitted.
    """
    rel = path.relative_to(root)
    return rel.parts[0] == "tests" and path.name.startswith("test_") and path.suffix == ".py"


def _all_test_files(root: Path) -> list[Path]:
    found: list[Path] = []
    root_dir = root / "tests"
    if not root_dir.is_dir():
        return found
    for path in sorted(root_dir.rglob("test_*.py")):
        if "__pycache__" in path.parts:
            continue
        found.append(path)
    return found


def _to_paths(root: Path, changed: list[str]) -> list[Path]:
    """Changed entries (relative or absolute) as absolute paths."""
    paths: list[Path] = []
    for change in changed:
        path = Path(change)
        paths.append(path if path.is_absolute() else (root / path).resolve())
    return paths


def affected_tests(graph: ImportGraph, changed: list[str]) -> tuple[list[Path], str]:
    """Selected test files + the reason (for the stderr summary).

    Returns ``(tests, reason)`` where ``reason`` is one of
    ``"graph"``, ``"full-suite:<trigger>"``, or ``"none"``.
    """
    root = graph.root
    changed_paths = _to_paths(root, changed)
    # Normalize to repo-relative posix paths for trigger matching.
    rels = []
    for path in changed_paths:
        try:
            rels.append(path.relative_to(root).as_posix())
        except ValueError:
            rels.append(path.as_posix())
    for rel in rels:
        if rel in FULL_SUITE_PATHS or rel.startswith(FULL_SUITE_PREFIXES):
            return _all_test_files(root), f"full-suite:{rel}"
    for rel, path in zip(rels, changed_paths):
        if rel.endswith(".py") and not path.exists():
            return _all_test_files(root), f"full-suite:deleted:{rel}"
    changed_set = {path for path in changed_paths if path.suffix == ".py"}
    selected = {path for path in graph.reverse_closure(changed_set) if _is_test_file(path, root)}
    selected.update(path for path in changed_set if _is_test_file(path, root))
    if not selected:
        return [], "none"
    return sorted(selected), "graph"


def affected_packages(graph: ImportGraph, changed: list[str]) -> list[str]:
    """Top-level packages the change touches, for the summary."""
    root = graph.root
    packages: set[str] = set()
    for path in _to_paths(root, changed):
        try:
            rel = path.relative_to(root)
        except ValueError:
            continue
        parts = rel.parts
        if parts and parts[0] == "puspa" and len(parts) > 1:
            packages.add(f"puspa.{parts[1]}")
        elif parts and parts[0] in ("tests", "demo", "cli"):
            packages.add(parts[0])
    for path in graph.reverse_closure({p for p in _to_paths(root, changed) if p.suffix == ".py"}):
        rel = path.relative_to(root)
        if rel.parts[0] == "puspa" and len(rel.parts) > 1:
            packages.add(f"puspa.{rel.parts[1]}")
    return sorted(packages)


def _changed_from_git(root: Path, base: str | None, head: str) -> list[str]:
    spec = f"{base}...{head}" if base else head
    result = subprocess.run(
        ["git", "diff", "--name-only", spec],
        cwd=root,
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0:
        print(f"git diff failed: {result.stderr.strip()}", file=sys.stderr)
        raise SystemExit(2)
    return [line for line in result.stdout.splitlines() if line]


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Print the test files affected by a change, from the import graph."
    )
    parser.add_argument("--root", default=None, help="repo root (default: parent of scripts/)")
    parser.add_argument(
        "--base", default=None, help="diff base ref (default: uncommitted diff vs HEAD)"
    )
    parser.add_argument("--head", default="HEAD", help="diff head ref (default: HEAD)")
    parser.add_argument(
        "--changed-files",
        default=None,
        help="file with one changed path per line (instead of git diff)",
    )
    args = parser.parse_args(argv)
    root = Path(args.root).resolve() if args.root else Path(__file__).resolve().parent.parent
    if args.changed_files:
        changed = [
            line.strip()
            for line in Path(args.changed_files).read_text(encoding="utf-8").splitlines()
            if line.strip()
        ]
    else:
        changed = _changed_from_git(root, args.base, args.head)
    graph = ImportGraph(root)
    graph.build()
    selected, reason = affected_tests(graph, changed)
    packages = affected_packages(graph, changed)
    print(f"affected packages: {', '.join(packages) if packages else '(none)'}", file=sys.stderr)
    print(f"selection: {reason}; {len(selected)} test file(s)", file=sys.stderr)
    for path in selected:
        print(path.relative_to(root).as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
