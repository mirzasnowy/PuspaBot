"""Tests for the npm install wrapper (``package.json`` + ``bin/``).

The npm package only carries a Node shim; the agent itself is the
Python distribution ``puspa`` at the same version. These tests keep
the two in sync and prove the shim hands off to the real entry
point (or falls back to ``python -m puspa``) without needing a
published package.

The file runs on ubuntu, windows, and macOS in CI, so every fake
executable is built per OS: a fake is a small Node script
(``fake-python.cjs``) driven by a JSON rule list, reached through a
shell wrapper on POSIX and a ``.cmd`` wrapper on Windows. The rules
match on the logged ``name + args`` line; the first match wins.
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
WINDOWS = os.name == "nt"
pytestmark = pytest.mark.skipif(NODE is None, reason="node is not installed")

FAKE_RUNTIME = """
const fs = require("node:fs");
const name = process.env.FAKE_PY_NAME || "unknown";
const log = process.env.FAKE_PY_LOG;
const rules = JSON.parse(fs.readFileSync(process.env.FAKE_PY_RULES, "utf-8"));
const line = `${name} ${process.argv.slice(2).join(" ")}`;
fs.appendFileSync(log, line + "\\n");
for (const rule of rules) {
  if (rule.match === undefined || line.includes(rule.match)) {
    if (rule.output !== undefined) process.stdout.write(rule.output + "\\n");
    process.exit(rule.exit ?? 0);
  }
}
process.exit(1);
"""


def _package_json():
    return json.loads((BUILD_DIR / "package.json").read_text(encoding="utf-8"))


def _pyproject():
    with (BUILD_DIR / "pyproject.toml").open("rb") as fh:
        return tomllib.load(fh)


def _test_env(leading):
    """PATH with only the leading dirs, node, and the system dirs — so
    the code under test never reaches a real interpreter."""
    node_dir = str(Path(NODE).resolve().parent)
    parts = [str(p) for p in leading] + [node_dir]
    if WINDOWS:
        system_root = os.environ.get("SYSTEMROOT", r"C:\Windows")
        parts.append(os.path.join(system_root, "System32"))
    else:
        parts += ["/usr/bin", "/bin"]
    env = dict(os.environ)
    env["PATH"] = os.pathsep.join(parts)
    env.pop("PUSPA_LOCAL_WHEEL", None)
    return env


def _fake_command(tmp_path, name, log, rules):
    """Create a fake executable called ``name`` that appends its args
    to ``log`` and answers by ``rules`` (first match wins). The rules
    file is per fake; the log is shared by every fake in the test."""
    (tmp_path / "fake-python.cjs").write_text(FAKE_RUNTIME, encoding="utf-8")
    rules_path = tmp_path / f"{name}-rules.json"
    rules_path.write_text(json.dumps(rules), encoding="utf-8")
    if WINDOWS:
        launcher = tmp_path / f"{name}.cmd"
        launcher.write_text(
            "@echo off\n"
            f"set FAKE_PY_NAME={name}\n"
            f"set FAKE_PY_LOG={log}\n"
            f"set FAKE_PY_RULES={rules_path}\n"
            'node "%~dp0fake-python.cjs" %*\n',
            encoding="utf-8",
        )
    else:
        launcher = tmp_path / name
        launcher.write_text(
            "#!/bin/sh\n"
            f'FAKE_PY_NAME="{name}" FAKE_PY_LOG="{log}" '
            f'FAKE_PY_RULES="{rules_path}" '
            f'exec node "{tmp_path / "fake-python.cjs"}" "$@"\n',
            encoding="utf-8",
        )
        launcher.chmod(0o755)


def _user_site_rule(tmp_path):
    """The user-site query always succeeds and reports a site under
    tmp_path, so the --target step never touches a real home."""
    return {
        "match": "getusersitepackages",
        "exit": 0,
        "output": str(tmp_path / "usersite"),
    }


def test_package_json_name_and_bin():
    """The npm name is scoped (@puspaverse/puspa, the product scope)
    while the PyPI name stays plain `puspa`, so the names are checked
    separately. The wrapper version tracks the shim, the Python
    version tracks the PyPI release; both are 0.1.1 now (Nero's
    decision, 23 Nov 2026). The postinstall pin is what must match
    pyproject (see test_postinstall_installs_the_pyproject_version)."""
    pkg = _package_json()
    assert pkg["name"] in {"@puspaverse/puspa", "@mirzasnowy/puspa"}
    assert pkg["bin"] == {"puspa": "./bin/puspa.js"}


def test_shim_uses_the_installed_entry_point():
    """With the repo venv on PATH, the shim finds the pip console script."""
    # sys.prefix is the venv directory; sys.executable is a symlink to
    # the system python, so its resolved parent is NOT the venv bin.
    venv_bin = Path(sys.prefix) / ("Scripts" if WINDOWS else "bin")
    env = dict(os.environ)
    env["PATH"] = str(venv_bin) + os.pathsep + env["PATH"]
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
    real_bin = tmp_path / "realbin"
    real_bin.mkdir()
    if WINDOWS:
        # npm's Windows shim: a .cmd that calls this file.
        (bin_dir / "puspa.cmd").write_text(
            f'@node "{BUILD_DIR / "bin" / "puspa.js"}" %*\n', encoding="utf-8"
        )
        (real_bin / "puspa.cmd").write_text("@echo REAL-CONSOLE-SCRIPT %*\n", encoding="utf-8")
    else:
        (bin_dir / "puspa").symlink_to(BUILD_DIR / "bin" / "puspa.js")
        console_script = real_bin / "puspa"
        console_script.write_text('#!/bin/sh\necho "REAL-CONSOLE-SCRIPT $@"\n', encoding="utf-8")
        console_script.chmod(0o755)
    env = _test_env([bin_dir, real_bin])
    # Start the shim the way npm does. A bare "puspa" cannot be used on
    # Windows: CreateProcess resolves it against the PARENT process's
    # PATH (not `env`) and only tries .exe, so it would run the real
    # pip console script from the venv instead of npm's puspa.cmd.
    command = [str(bin_dir / "puspa.cmd")] if WINDOWS else ["puspa"]
    result = subprocess.run(
        [*command, "--help"],
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
    log = tmp_path / "calls.log"
    _fake_command(
        tmp_path,
        "python",
        log,
        [{"output": "FAKE-PYTHON-CALLED -m puspa --help", "exit": 0}],
    )
    result = subprocess.run(
        [NODE, str(BUILD_DIR / "bin" / "puspa.js"), "--help"],
        capture_output=True,
        check=False,
        text=True,
        env=_test_env([tmp_path]),
        timeout=60,
    )
    assert result.returncode == 0
    assert "FAKE-PYTHON-CALLED -m puspa --help" in result.stdout


def test_postinstall_installs_the_pyproject_version(tmp_path):
    """The postinstall target is puspa==<pyproject version>; the pin
    must match pyproject (both are 0.1.1 now)."""
    log = tmp_path / "calls.log"
    _fake_command(
        tmp_path,
        "python3",
        log,
        [_user_site_rule(tmp_path), {"exit": 0}],
    )
    _fake_command(tmp_path, "python", log, [{"exit": 1}])
    result = subprocess.run(
        [NODE, str(BUILD_DIR / "bin" / "postinstall.js")],
        capture_output=True,
        check=False,
        text=True,
        env=_test_env([tmp_path]),
        timeout=120,
    )
    assert result.returncode == 0
    assert f"puspa=={_pyproject()['project']['version']}" in log.read_text(encoding="utf-8")


def test_postinstall_retries_with_target_flag(tmp_path):
    """Plain pip install fails (PEP 668 on Debian/Ubuntu, and --user
    is refused the same way) — the postinstall retries with --target
    <user site> instead of giving up, and never reaches pipx once
    the target install succeeds."""
    log = tmp_path / "calls.log"
    _fake_command(
        tmp_path,
        "python3",
        log,
        [
            _user_site_rule(tmp_path),
            {"match": "--target", "exit": 0},
            {"match": "--user", "exit": 1},
            {"match": "-m pip", "exit": 1},
        ],
    )
    _fake_command(tmp_path, "python", log, [{"exit": 1}])
    _fake_command(tmp_path, "pipx", log, [{"exit": 0}])
    result = subprocess.run(
        [NODE, str(BUILD_DIR / "bin" / "postinstall.js")],
        capture_output=True,
        check=False,
        text=True,
        env=_test_env([tmp_path]),
        timeout=120,
    )
    assert result.returncode == 0
    lines = [line for line in log.read_text(encoding="utf-8").splitlines() if "-m pip" in line]
    assert len(lines) == 3
    assert "--user" not in lines[0] and "--target" not in lines[0]
    assert "--user" in lines[1]
    assert "--target" in lines[2]
    assert str(tmp_path / "usersite") in lines[2]
    assert "pipx install" not in log.read_text(encoding="utf-8")


def test_postinstall_falls_back_to_pipx(tmp_path):
    """Every pip attempt fails — the postinstall tries `pipx install`
    before printing the manual command."""
    log = tmp_path / "calls.log"
    _fake_command(
        tmp_path,
        "python3",
        log,
        [_user_site_rule(tmp_path), {"match": "-m pip", "exit": 1}],
    )
    _fake_command(
        tmp_path,
        "python",
        log,
        [_user_site_rule(tmp_path), {"match": "-m pip", "exit": 1}],
    )
    _fake_command(tmp_path, "pipx", log, [{"exit": 0}])
    result = subprocess.run(
        [NODE, str(BUILD_DIR / "bin" / "postinstall.js")],
        capture_output=True,
        check=False,
        text=True,
        env=_test_env([tmp_path]),
        timeout=120,
    )
    assert result.returncode == 0
    assert f"pipx install puspa=={_pyproject()['project']['version']}" in log.read_text(
        encoding="utf-8"
    )


def test_postinstall_tries_py_launcher(tmp_path):
    """`python3` and `python` are missing or fail every pip attempt —
    the postinstall tries the `py` Windows launcher next."""
    log = tmp_path / "calls.log"
    _fake_command(
        tmp_path,
        "python3",
        log,
        [_user_site_rule(tmp_path), {"match": "-m pip", "exit": 1}],
    )
    _fake_command(
        tmp_path,
        "python",
        log,
        [_user_site_rule(tmp_path), {"match": "-m pip", "exit": 1}],
    )
    _fake_command(
        tmp_path,
        "py",
        log,
        [_user_site_rule(tmp_path), {"match": "-m pip", "exit": 0}],
    )
    result = subprocess.run(
        [NODE, str(BUILD_DIR / "bin" / "postinstall.js")],
        capture_output=True,
        check=False,
        text=True,
        env=_test_env([tmp_path]),
        timeout=120,
    )
    assert result.returncode == 0
    assert (
        f"py -m pip install --quiet puspa=={_pyproject()['project']['version']}"
        in log.read_text(encoding="utf-8")
    )


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
