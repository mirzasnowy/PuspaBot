"""Boundary lint (G27, hermes-agent ``scripts/boundary_lint.py``).

Two rules, both enforced by scanning imports with ``ast``:

- **B1 — core never imports a specific builtin plugin.** Any
  file under ``puspa/`` (outside ``puspa/plugins/``) may import
  only the plugin-system modules listed in
  ``plugin_api.json``'s ``core_allowed_plugin_modules``. In
  particular ``puspa.plugins.builtin`` and any builtin plugin
  module are forbidden: builtin plugins are loaded by module
  path through the loader, never imported by core code.
- **B2 — plugins only use the published API.** Any file under
  ``puspa/plugins/`` may import only the standard library,
  ``puspa.plugins.*`` itself, the modules listed in
  ``plugin_api.json``'s ``allowed_imports``, and (only in
  ``adapters.py``) the nanobot modules in
  ``adapter_allowed_imports``. Anything else from ``puspa.*``
  or ``nanobot.*`` is a violation.
- **B3 — builtin plugins do not import each other.** A file
  under ``puspa/plugins/builtin/`` may not import another
  builtin plugin module; plugins share state only through the
  loader and the ``ctx`` API.

Run as ``python -m puspa.plugins.boundary [--root DIR]``; exit
code 1 when any violation is found. ``tests/test_plugin_boundary.py``
runs the same checks against the real tree and against a
synthetic tree with a planted violation.
"""

from __future__ import annotations

import ast
import json
import sys
from dataclasses import dataclass
from pathlib import Path

__all__ = ["Violation", "collect_imports", "lint_tree", "load_api_spec", "main"]

#: Plugin-system modules that are third-party dependencies of the
#: plugin system itself (declared in requirements.txt), allowed
#: in plugin-system files beyond the stdlib and the published API.
SYSTEM_THIRD_PARTY_IMPORTS = frozenset({"yaml"})


@dataclass(frozen=True)
class Violation:
    """One boundary violation."""

    file: str
    line: int
    rule: str
    message: str

    def __str__(self):
        return f"{self.file}:{self.line}: [{self.rule}] {self.message}"


def load_api_spec(root: Path) -> dict:
    """Load ``plugin_api.json`` from the repo root (or a test tree)."""
    api_path = root / "puspa" / "plugins" / "plugin_api.json"
    if not api_path.is_file():
        # A synthetic test tree may place the spec at its root.
        api_path = root / "plugin_api.json"
    return json.loads(api_path.read_text(encoding="utf-8"))


def _module_name_for(path: Path, root: Path) -> str:
    """Dotted module name of ``path`` relative to ``root``."""
    relative = path.relative_to(root).with_suffix("")
    parts = list(relative.parts)
    if parts and parts[-1] == "__init__":
        parts.pop()
    return ".".join(parts)


def _resolve_relative(module_name: str, level: int, imported: str | None) -> str:
    """Resolve a relative import to an absolute dotted name."""
    parts = module_name.split(".")
    # The containing package: drop ``level`` trailing components
    # (level 1 = current package, so drop the module itself).
    base = parts[: len(parts) - level] if level <= len(parts) else []
    if imported:
        return ".".join([*base, imported])
    return ".".join(base)


def collect_imports(path: Path, root: Path) -> list[tuple[str, int]]:
    """All imported dotted module names in one file, with line numbers.

    Returns ``(module, line)`` pairs. ``from x import y`` counts
    as importing ``x``; ``from x import y`` where ``y`` is itself
    a submodule is also recorded as ``x.y`` so that
    ``from puspa.plugins.builtin import echo`` is caught.
    Relative imports are resolved to absolute names.
    """
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"))
    except (OSError, SyntaxError):
        return []
    module_name = _module_name_for(path, root)
    found: list[tuple[str, int]] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                found.append((alias.name, node.lineno))
        elif isinstance(node, ast.ImportFrom):
            if node.level:
                base = _resolve_relative(module_name, node.level, node.module)
            else:
                base = node.module or ""
            if base:
                found.append((base, node.lineno))
            for alias in node.names:
                if alias.name == "*":
                    continue
                candidate = f"{base}.{alias.name}" if base else alias.name
                found.append((candidate, node.lineno))
    return found


def _is_allowed_plugin_import(imported: str, spec: dict, *, is_adapter: bool) -> bool:
    top = imported.split(".", maxsplit=1)[0]
    if top in sys.stdlib_module_names:
        return True
    if imported == "puspa.plugins" or imported.startswith("puspa.plugins."):
        return True
    allowed = spec.get("allowed_imports", [])
    if any(imported == entry or imported.startswith(entry + ".") for entry in allowed):
        return True
    if is_adapter:
        adapter_allowed = spec.get("adapter_allowed_imports", [])
        if any(imported == entry or imported.startswith(entry + ".") for entry in adapter_allowed):
            return True
    return top in SYSTEM_THIRD_PARTY_IMPORTS


def lint_tree(root: Path) -> list[Violation]:
    """Lint the whole tree under ``root``; return all violations."""
    root = Path(root)
    spec = load_api_spec(root)
    core_allowed = set(spec.get("core_allowed_plugin_modules", []))
    violations: list[Violation] = []
    puspa_dir = root / "puspa"
    if not puspa_dir.is_dir():
        return [Violation(str(root), 0, "B0", f"no puspa/ package under {root}")]
    for path in sorted(puspa_dir.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        relative = str(path.relative_to(root))
        module_name = _module_name_for(path, root)
        is_plugin_file = module_name.startswith("puspa.plugins.")
        is_adapter = module_name == "puspa.plugins.adapters"
        is_builtin = module_name.startswith("puspa.plugins.builtin.")
        for imported, line in collect_imports(path, root):
            # B3: a builtin plugin importing another builtin plugin.
            # (A builtin file importing its own module is not a
            # crossing; it never happens in practice.)
            if (
                is_plugin_file
                and is_builtin
                and imported.startswith("puspa.plugins.builtin.")
                and imported != module_name
            ):
                violations.append(
                    Violation(
                        relative,
                        line,
                        "B3",
                        f"builtin plugin imports another builtin plugin: {imported}",
                    )
                )
                continue
            # B2: a plugin file importing outside the published API.
            # (Builtin-plugin imports are allowed here: they start
            # with puspa.plugins., so _is_allowed_plugin_import
            # passes them; only a *different* builtin module is
            # B3, handled above.)
            if is_plugin_file:
                if not _is_allowed_plugin_import(imported, spec, is_adapter=is_adapter):
                    violations.append(
                        Violation(
                            relative,
                            line,
                            "B2",
                            f"plugin file imports outside the published API: {imported}",
                        )
                    )
                continue
            # B1: a core file importing a plugin module outside the
            # allowed list.
            if (
                (imported == "puspa.plugins" or imported.startswith("puspa.plugins."))
                and imported not in core_allowed
                and not any(
                    imported.startswith(entry + ".")
                    for entry in core_allowed
                    if entry != "puspa.plugins"
                )
            ):
                violations.append(
                    Violation(
                        relative,
                        line,
                        "B1",
                        f"core imports a plugin module outside the allowed list: {imported}",
                    )
                )
    return violations


def main(argv=None) -> int:
    """CLI entry point: ``python -m puspa.plugins.boundary``."""
    import argparse

    parser = argparse.ArgumentParser(description="Puspa plugin boundary lint (G27).")
    parser.add_argument(
        "--root",
        default=str(Path(__file__).resolve().parent.parent.parent),
        help="Repo root to lint (default: the Puspa repo).",
    )
    args = parser.parse_args(argv)
    violations = lint_tree(Path(args.root))
    for violation in violations:
        print(violation)
    if violations:
        print(f"boundary lint: {len(violations)} violation(s)")
        return 1
    print("boundary lint: clean")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
