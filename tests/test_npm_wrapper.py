"""Tests for the npm install wrapper (``package.json`` + ``bin/``).

The npm package only carries a Node shim; the agent itself is the
Python distribution ``puspa`` at the same version. These tests keep
the two in sync and prove the shim hands off to the real entry
point (or falls back to ``python -m puspa``) without needing a
published package.
"""

import json
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

import pytest

BUILD_DIR = Path(__file__).resolve().parent.parent
NODE = shutil.which("node")
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")


def _package_json():
    return json.loads((BUILD_DIR / "package.json").read_text(encoding="utf-8"))


def _pyproject():
    with (BUILD_DIR / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def test_package_json_version_matches_pyproject():
    pkg = _package_json()
    project = _pyproject()["project"]
    assert pkg["name"] == project["name"] == "puspa"
    assert pkg["version"] == project["version"]
    assert pkg["bin"] == {"puspa": "./bin/puspa.js"}


def test_shim_uses_the_installed_entry_point():
    """With the repo venv on PATH, the shim finds the pip console script."""
    # sys.prefix is the venv directory; sys.executable is a symlink to
    # the system python, so its resolved parent is NOT the venv bin.
    venv_bin = str(Path(sys.prefix) / "bin")
    env = dict(os.environ)
    env["PATH"] = venv_bin + os.pathsep + env["PATH"]
    result = subprocess.run(
        [NODE, str(BUILD_DIR / "bin" / "puspa.js"), "--help"],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0
    assert "PuspaBot" in result.stdout


def test_shim_skips_itself_when_its_own_bin_dir_is_first_on_path(tmp_path):
    """npx/npm put the package bin dir first on PATH, so a bare `puspa`
    lookup finds this shim itself. The shim must skip itself and run
    the pip console script found later on PATH — never recurse (seen
    21 Nov 2026: `npx puspa --help` grew a 268-deep node chain)."""
    bin_dir = tmp_path / "node_modules" / ".bin"
    bin_dir.mkdir(parents=True)
    (bin_dir / "puspa").symlink_to(BUILD_DIR / "bin" / "puspa.js")
    real_bin = tmp_path / "realbin"
    real_bin.mkdir()
    console_script = real_bin / "puspa"
    console_script.write_text('#!/bin/sh\necho "REAL-CONSOLE-SCRIPT $@"\n', encoding="utf-8")
    console_script.chmod(0o755)
    env = dict(os.environ)
    # Only the two fake dirs plus the system dirs node needs; nothing
    # else on PATH, so the only `puspa` entries are the shim and the fake.
    # Node's own directory comes from shutil.which: CI installs node via
    # setup-node into a tool-cache dir, not /usr/bin (hardcoding /usr/bin
    # made this test fail in CI with exit 127, command not found).
    node_dir = str(Path(NODE).resolve().parent)
    env["PATH"] = os.pathsep.join([str(bin_dir), str(real_bin), node_dir, "/usr/bin", "/bin"])
    result = subprocess.run(
        ["puspa", "--help"],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=20,
    )
    assert result.returncode == 0
    assert "REAL-CONSOLE-SCRIPT --help" in result.stdout


def test_shim_falls_back_to_python_module(tmp_path):
    """With no `puspa` on PATH, the shim runs `python -m puspa`."""
    fake_python = tmp_path / "python"
    fake_python.write_text('#!/bin/sh\necho "FAKE-PYTHON-CALLED $@"\n', encoding="utf-8")
    fake_python.chmod(0o755)
    env = dict(os.environ)
    env["PATH"] = str(tmp_path)
    result = subprocess.run(
        [NODE, str(BUILD_DIR / "bin" / "puspa.js"), "--help"],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=60,
    )
    assert result.returncode == 0
    assert "FAKE-PYTHON-CALLED -m puspa --help" in result.stdout


def test_postinstall_never_fails_the_npm_install():
    """A pip failure prints the manual command and still exits 0."""
    env = dict(os.environ)
    env["PUSPA_LOCAL_WHEEL"] = "/tmp/does-not-exist-puspa-0.1.0.whl"
    result = subprocess.run(
        [NODE, str(BUILD_DIR / "bin" / "postinstall.js")],
        capture_output=True,
        check=False,
        text=True,
        env=env,
        timeout=120,
    )
    assert result.returncode == 0
    assert "Install it by hand" in result.stderr
