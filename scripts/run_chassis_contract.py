#!/usr/bin/env python3
"""Run the chassis contract lane locally (E-19, plan section 9.4).

Runs ONLY the wrapper-interface tests listed in
``puspa/chassis/contract.py`` against the currently installed nanobot
pin — the same set the ``chassis-contract`` CI workflow runs against a
candidate pin. Usage (from the repo root)::

    python scripts/run_chassis_contract.py
    python scripts/run_chassis_contract.py -x -q

The test database DSN comes from the environment (``PUSPA_TEST_DSN``),
exactly as the full suite reads it; this script never invents a
database name. A missing contract file fails before pytest starts, so
a renamed test file cannot silently shrink the lane.
"""

from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    # Plain `python scripts/...` has no pytest.ini pythonpath, so make
    # the repo root importable for the `puspa` package below.
    sys.path.insert(0, str(REPO_ROOT))
_NANOBOT_LOCAL = REPO_ROOT / "nanobot"
if _NANOBOT_LOCAL.is_dir() and str(_NANOBOT_LOCAL) not in sys.path:
    # Same path setup as tests/conftest.py: the local clone is the
    # read reference for the nanobot package.
    sys.path.insert(0, str(_NANOBOT_LOCAL))

from puspa.chassis.contract import (
    CHASSIS_CONTRACT_TESTS,
    missing_contract_files,
)

__all__ = ["contract_command"]


def contract_command(pytest_args: list[str]) -> list[str]:
    """Build the pytest command for the contract lane."""
    missing = missing_contract_files(REPO_ROOT)
    if missing:
        raise FileNotFoundError("chassis contract test files missing: " + ", ".join(missing))
    return [
        sys.executable,
        "-m",
        "pytest",
        *[str(REPO_ROOT / relative) for relative in CHASSIS_CONTRACT_TESTS],
        *pytest_args,
    ]


def main(argv: list[str] | None = None) -> int:
    """Entry point: run the contract lane and return pytest's exit code."""
    parser = argparse.ArgumentParser(description=__doc__)
    # parse_known_args: every unrecognised flag belongs to pytest
    # (``-q``, ``-x``, ``-k ...``), so it passes through untouched.
    _, pytest_args = parser.parse_known_args(argv)
    try:
        command = contract_command(pytest_args)
    except FileNotFoundError as exc:
        print(f"error: {exc}", file=sys.stderr)
        return 1
    print(f"chassis contract lane: {len(CHASSIS_CONTRACT_TESTS)} test files")
    for relative in CHASSIS_CONTRACT_TESTS:
        print(f"  {relative}")
    result = subprocess.run(command, check=False)
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
