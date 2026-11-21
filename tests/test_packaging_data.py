"""Packaging regression tests (split Step 6, 21 Nov 2026).

The wheel once shipped zero data files, so ``import
puspa.i18n.server_lines`` failed from an installed package (the
default locale ``id`` was missing). These tests guard the fix:
every non-code file under ``puspa/`` must be covered by the
``[tool.setuptools.package-data]`` globs in ``pyproject.toml``, and
the ``py.typed`` marker must exist.
"""

from __future__ import annotations

import fnmatch
import tomllib
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
PACKAGE_DIR = REPO_ROOT / "puspa"
PYPROJECT = REPO_ROOT / "pyproject.toml"

# Code and bytecode are not data; everything else under puspa/ is.
IGNORED_SUFFIXES = {".py", ".pyc", ".pyo"}


def _package_data_globs() -> list[str]:
    with PYPROJECT.open("rb") as handle:
        config = tomllib.load(handle)
    package_data = config["tool"]["setuptools"]["package-data"]
    globs: list[str] = []
    for patterns in package_data.values():
        globs.extend(patterns)
    assert globs, "no [tool.setuptools.package-data] globs declared"
    return globs


def test_py_typed_marker_exists():
    assert (PACKAGE_DIR / "py.typed").is_file()


def test_every_data_file_is_covered_by_package_data():
    globs = _package_data_globs()
    uncovered = []
    for path in sorted(PACKAGE_DIR.rglob("*")):
        if not path.is_file() or path.suffix in IGNORED_SUFFIXES:
            continue
        name = path.name
        if not any(fnmatch.fnmatch(name, glob) for glob in globs):
            uncovered.append(str(path.relative_to(REPO_ROOT)))
    assert not uncovered, f"data files not covered by package-data: {uncovered}"


def test_package_data_globs_are_not_stale():
    """Each declared glob must match at least one file (no dead globs)."""
    globs = _package_data_globs()
    names = {path.name for path in PACKAGE_DIR.rglob("*") if path.is_file()}
    stale = [glob for glob in globs if not any(fnmatch.fnmatch(n, glob) for n in names)]
    assert not stale, f"package-data globs matching nothing: {stale}"


def test_manifest_lists_package_data():
    manifest = (REPO_ROOT / "MANIFEST.in").read_text(encoding="utf-8")
    assert "recursive-include puspa" in manifest
    assert "py.typed" in manifest
