"""The chassis contract: the wrapper-interface tests (E-19, plan 9.4).

The contract lane runs ONLY these test files against a candidate
nanobot pin, before the full suite. They are the tests that exercise
the wrapper interface in ``puspa/chassis/`` — the only place Puspa
touches nanobot — so they are the first thing that must pass when the
pin moves:

- ``PuspaTool`` wrapping (tool execution checks, gate, audit).
- The backstop registry (``PuspaRegistry.prepare_call`` / visibility).
- The ledger hook (``PuspaHook`` cycle and task records).
- The skill loader (``PuspaSkillsLoader`` signature checks).

The list lives here, in one place, so the CI workflow, the local
runner (``scripts/run_chassis_contract.py``), and the tests below all
read the same list instead of each keeping its own copy. A file joins
this list only when at least one of its tests imports a chassis
wrapper surface; the test ``test_contract_files_cover_wrapper_surfaces``
in ``tests/test_nanobot_pin.py`` enforces that.
"""

from __future__ import annotations

from pathlib import Path

__all__ = ["CHASSIS_CONTRACT_TESTS", "contract_test_files", "missing_contract_files"]

#: Test files that make up the chassis contract, relative to the repo
#: root. Each file exercises at least one wrapper surface.
CHASSIS_CONTRACT_TESTS: tuple[str, ...] = (
    # PuspaTool wrapping (execution checks, gate, audit, compression).
    "tests/test_tool_result_compression.py",
    # PuspaTool + PuspaRegistry + assemble (plugin wiring through the
    # chassis composition root).
    "tests/test_plugin_wiring.py",
    # PuspaHook ledger hook (guardrails cycle and task records).
    "tests/test_guardrails.py",
    # PuspaSkillsLoader (signature checks, revocation).
    "tests/test_skill_revocation.py",
    # Skill hooks + MCP wrapper in chassis.tools.
    "tests/test_skills_hooks.py",
    # assemble / build_registry / build_hook_factory (composition root).
    "tests/test_factory_rails.py",
    # End-to-end wrapper wiring: registry backstop, ledger hook
    # writes, skill loader, sandbox tool, assemble.
    "tests/test_integration.py",
)


def contract_test_files(repo_root: str | Path) -> list[Path]:
    """Absolute paths of the contract test files under ``repo_root``."""
    root = Path(repo_root)
    return [root / relative for relative in CHASSIS_CONTRACT_TESTS]


def missing_contract_files(repo_root: str | Path) -> list[str]:
    """Relative paths of contract test files that do not exist."""
    return [
        relative
        for relative, path in zip(CHASSIS_CONTRACT_TESTS, contract_test_files(repo_root))
        if not path.is_file()
    ]
