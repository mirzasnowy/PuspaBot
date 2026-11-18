"""Tests for harness working modes (KiloCode pattern).

Modes change what the agent does per task phase, and each mode is
enforced through its sandbox profile and the Gate, not just a
label:

- Architect plans from a real repo map and never writes or runs.
- Code implements through unified diffs only; a full-program
  answer fails that attempt.
- Debug reproduces first, then fixes through diffs.
- Ask answers once without changing anything.
- Switching mode per phase works through ``with_mode``.
- An unknown mode is refused; with no mode the loop keeps its
  old behaviour exactly.

These tests use no Puspa database tables themselves (in-memory
stubs, a counting executor, and temp dirs only). Note: the shared
``tests/conftest.py`` autouse fixture still creates the schemas and
truncates tables in ``puspa_test`` for every test file in this
directory, including this one; that fixture cannot be opted out of
from here.
"""

import hashlib
import time

import pytest

from puspa.harness.diff import generate_diff
from puspa.harness.executor import ExecutionResult, Executor, LocalTestExecutor
from puspa.harness.loop import (
    STATUS_ANSWERED,
    STATUS_BUDGET_EXHAUSTED,
    STATUS_PASSED,
    STATUS_PLANNED,
    STATUS_REPRODUCTION_MISSING,
    CodingHarness,
    CodingTask,
    HarnessError,
)
from puspa.harness.modes import (
    MODE_ARCHITECT,
    MODE_ASK,
    MODE_CODE,
    MODE_DEBUG,
    ModeError,
    get_mode,
    list_modes,
)
from puspa.harness.sandbox_profiles import (
    PROFILE_FULL,
    PROFILE_READ_ONLY,
    PROFILE_WORKSPACE_WRITE,
    ProfileDenied,
    check_tool,
)
from puspa.policy import Gate, GateDenied

TEST_CODE = (
    "\nassert add(2, 3) == 5\n"
    "assert add(-1, 1) == 0\n"
    "assert add(0, 0) == 0\n"
    'print("ALL TESTS PASSED")\n'
)

BUGGY_IMPL = "def add(a, b):\n    return a - b\n"
FIXED_IMPL = "def add(a, b):\n    return a + b\n"


def _program(impl: str) -> str:
    return impl + TEST_CODE


def _task(**overrides) -> CodingTask:
    task = CodingTask(
        task_id="task-add",
        user_id="user-1",
        agent_id="agent-puspa",
        description="implement add(a, b) returning the sum",
        test_code=TEST_CODE,
    )
    for key, value in overrides.items():
        setattr(task, key, value)
    return task


def _harness_payload(**overrides) -> dict:
    now = int(time.time())
    payload = {
        "user_id": "user-1",
        "agent_id": "agent-puspa",
        "subagent_id": None,
        "tools": ["write_code", "run_tests", "deploy"],
        "domains": [],
        "connectors": {},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 2000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }
    payload.update(overrides)
    return payload


class _CountingExecutor(Executor):
    """Counts run() calls and never runs anything for real."""

    def __init__(self) -> None:
        self.calls = 0
        self.codes: list[str] = []

    def run(self, code: str, *, timeout_s: int = 30) -> ExecutionResult:
        self.calls += 1
        self.codes.append(code)
        return ExecutionResult(ok=True, stdout="", stderr="", exit_code=0)


def _repo_with_add(root) -> None:
    """A tiny real repo for the Architect repo map."""
    src = root / "src"
    src.mkdir(parents=True)
    (src / "add.py").write_text("def add(a, b):\n    return a + b\n", encoding="utf-8")
    (src / "main.py").write_text(
        "from src.add import add\n\n\ndef main():\n    print(add(2, 3))\n",
        encoding="utf-8",
    )


def _event_types(harness) -> list[str]:
    return [event["type"] for event in harness.event_log()]


# --- mode specs ------------------------------------------------------------


def test_mode_specs_cover_all_four_modes():
    assert list_modes() == ["architect", "code", "debug", "ask"]

    architect = get_mode(MODE_ARCHITECT)
    assert architect.profile == PROFILE_READ_ONLY
    assert architect.allows_write is False
    assert architect.allows_execute is False
    assert architect.diff_mode is False
    assert architect.reproduce_first is False
    assert architect.produces == "plan"

    code = get_mode(MODE_CODE)
    assert code.profile == PROFILE_WORKSPACE_WRITE
    assert code.diff_mode is True
    assert code.reproduce_first is False
    assert code.requires_diff is True
    assert code.allows_write is True
    assert code.allows_execute is True
    assert code.produces == "code"

    debug = get_mode(MODE_DEBUG)
    assert debug.profile == PROFILE_WORKSPACE_WRITE
    assert debug.diff_mode is True
    assert debug.reproduce_first is True
    assert debug.requires_diff is True
    assert debug.produces == "code"

    ask = get_mode(MODE_ASK)
    assert ask.profile == PROFILE_READ_ONLY
    assert ask.allows_write is False
    assert ask.allows_execute is False
    assert ask.produces == "answer"


def test_unknown_mode_is_refused():
    with pytest.raises(ModeError, match="unknown working mode 'wizard'"):
        get_mode("wizard")
    with pytest.raises(HarnessError, match="unknown working mode 'wizard'"):
        CodingHarness(
            executor=_CountingExecutor(),
            generator=lambda task, attempts: "",
            mode="wizard",
        )


def test_mode_overrides_explicit_flags_and_profile():
    """A mode wins over separately passed flags: the spec is the
    behaviour, the flags are ignored while a mode is set."""
    harness = CodingHarness(
        executor=_CountingExecutor(),
        generator=lambda task, attempts: "",
        mode=MODE_CODE,
        diff_mode=False,
        profile=PROFILE_READ_ONLY,
    )
    assert harness.profile == PROFILE_WORKSPACE_WRITE
    assert harness.diff_mode is True
    assert harness.reproduce_first is False

    architect = CodingHarness(
        executor=_CountingExecutor(),
        generator=lambda task, attempts: "",
        mode=MODE_ARCHITECT,
        profile=PROFILE_FULL,
    )
    assert architect.profile == PROFILE_READ_ONLY
    assert architect.diff_mode is False

    debug = CodingHarness(
        executor=_CountingExecutor(),
        generator=lambda task, attempts: "",
        mode=MODE_DEBUG,
    )
    assert debug.diff_mode is True
    assert debug.reproduce_first is True


# --- Architect ---------------------------------------------------------------


def test_architect_plans_from_real_repo_map_without_running(tmp_path):
    """Architect builds a plan from a real repo map, never calls
    the executor, and writes nothing."""
    repo = tmp_path / "repo"
    _repo_with_add(repo)
    before = sorted(str(path) for path in repo.rglob("*"))

    executor = _CountingExecutor()
    seen_maps = {}

    def generator(task, attempts, repo_map=""):
        seen_maps["map"] = repo_map
        assert "src/add.py" in repo_map
        assert "def add" in repo_map
        assert "src/main.py" in repo_map
        return "PLAN: change add in src/add.py, keep main.py as is"

    harness = CodingHarness(
        executor=executor,
        generator=generator,
        mode=MODE_ARCHITECT,
        workspace_root=repo,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_PLANNED
    assert outcome.mode == MODE_ARCHITECT
    assert outcome.profile == PROFILE_READ_ONLY
    assert outcome.plan == "PLAN: change add in src/add.py, keep main.py as is"
    assert outcome.repo_map_text is not None
    assert "src/add.py" in outcome.repo_map_text
    assert outcome.final_code is None
    assert outcome.attempts == []
    assert executor.calls == 0
    assert executor.codes == []
    assert sorted(str(path) for path in repo.rglob("*")) == before
    assert "mode_enforced" in _event_types(harness)
    assert "plan_ready" in _event_types(harness)


def test_architect_write_and_run_denied_even_with_permissive_token(tmp_path):
    """A token that allows everything still cannot widen the
    Architect profile: the Gate plus read_only refuse writes and
    execution before the plan starts."""
    executor = _CountingExecutor()
    harness = CodingHarness(
        executor=executor,
        generator=lambda task, attempts: "PLAN: do nothing yet",
        gate=Gate(),
        token_payload=_harness_payload(),
        mode=MODE_ARCHITECT,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_PLANNED
    assert outcome.plan == "PLAN: do nothing yet"
    assert executor.calls == 0

    # The enforcement itself, directly: under the Architect
    # profile both tools raise through the Gate.
    with pytest.raises(GateDenied, match="denies tool 'write_code'"):
        check_tool(PROFILE_READ_ONLY, Gate(), _harness_payload(), "write_code")
    with pytest.raises(GateDenied, match="denies tool 'run_tests'"):
        check_tool(PROFILE_READ_ONLY, Gate(), _harness_payload(), "run_tests")

    # And the executor refuses a write under that profile too.
    local = LocalTestExecutor(tmp_path / "work")
    with pytest.raises(ProfileDenied, match=r"read_only.*denies writes"):
        local.write_file("src/add.py", FIXED_IMPL, profile=PROFILE_READ_ONLY)


def test_architect_without_workspace_root_still_plans():
    """No root to map (an executor with no workdir): the plan is
    still produced, with no repo map attached."""
    executor = _CountingExecutor()
    harness = CodingHarness(
        executor=executor,
        generator=lambda task, attempts: "PLAN: explore first",
        mode=MODE_ARCHITECT,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_PLANNED
    assert outcome.plan == "PLAN: explore first"
    assert outcome.repo_map_text is None
    assert executor.calls == 0


# --- Ask ---------------------------------------------------------------------


def test_ask_answers_without_changing_anything(tmp_path):
    """Ask returns one answer, runs nothing, and leaves every file
    byte-identical."""
    target = tmp_path / "notes.txt"
    target.write_text("do not touch\n", encoding="utf-8")
    digest_before = hashlib.sha256(target.read_bytes()).hexdigest()

    executor = _CountingExecutor()
    harness = CodingHarness(
        executor=executor,
        generator=lambda task, attempts: "add returns a - b, so it is wrong",
        mode=MODE_ASK,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_ANSWERED
    assert outcome.mode == MODE_ASK
    assert outcome.profile == PROFILE_READ_ONLY
    assert outcome.answer == "add returns a - b, so it is wrong"
    assert outcome.final_code is None
    assert outcome.attempts == []
    assert executor.calls == 0
    assert hashlib.sha256(target.read_bytes()).hexdigest() == digest_before
    assert "mode_enforced" in _event_types(harness)
    assert "answer_ready" in _event_types(harness)


def test_ask_cannot_write_or_run_even_with_permissive_token():
    """Ask under a fully permissive token still answers without
    executing: the read_only profile denies both tools."""
    executor = _CountingExecutor()
    harness = CodingHarness(
        executor=executor,
        generator=lambda task, attempts: "the bug is the minus sign",
        gate=Gate(),
        token_payload=_harness_payload(),
        mode=MODE_ASK,
    )
    outcome = harness.run(_task())

    assert outcome.status == STATUS_ANSWERED
    assert outcome.answer == "the bug is the minus sign"
    assert executor.calls == 0


# --- Code --------------------------------------------------------------------


def test_code_mode_implements_through_diff(tmp_path):
    """Code mode applies one surgical diff and goes green."""
    diff_to_fix = generate_diff(BUGGY_IMPL, _program(FIXED_IMPL), filename="attempt.py")
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: diff_to_fix,
        max_attempts=3,
        mode=MODE_CODE,
    )
    outcome = harness.run(_task(starter_code=BUGGY_IMPL))

    assert outcome.status == STATUS_PASSED
    assert outcome.mode == MODE_CODE
    assert len(outcome.attempts) == 1
    assert outcome.final_code == _program(FIXED_IMPL)
    assert "ALL TESTS PASSED" in outcome.attempts[0].result.stdout


def test_code_mode_rejects_full_program_then_recovers_with_diff(tmp_path):
    """A full-program answer in Code mode fails that attempt with
    a clear reason and leaves the program untouched; the next
    attempt can still fix it with a diff."""
    diff_to_fix = generate_diff(BUGGY_IMPL, _program(FIXED_IMPL), filename="attempt.py")

    def generator(task, attempts):
        if not attempts:
            return _program(FIXED_IMPL)
        return diff_to_fix

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=3,
        mode=MODE_CODE,
    )
    outcome = harness.run(_task(starter_code=BUGGY_IMPL))

    assert outcome.status == STATUS_PASSED
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].passed is False
    assert "requires a unified diff" in outcome.attempts[0].reason
    assert outcome.attempts[0].code == BUGGY_IMPL
    assert outcome.attempts[1].passed is True
    assert outcome.final_code == _program(FIXED_IMPL)


# --- Debug -------------------------------------------------------------------


def test_debug_reproduces_before_fixing(tmp_path):
    """Debug mode: the starter probe fails (the reproduction),
    the first diff attempt fails again, the second diff fixes it;
    the fix is certified by the reproduction pair."""
    diff_to_buggy_program = generate_diff(BUGGY_IMPL, _program(BUGGY_IMPL), filename="attempt.py")
    diff_to_fix = generate_diff(_program(BUGGY_IMPL), _program(FIXED_IMPL), filename="attempt.py")

    def generator(task, attempts):
        if not attempts:
            return diff_to_buggy_program
        return diff_to_fix

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=3,
        mode=MODE_DEBUG,
    )
    outcome = harness.run(_task(starter_code=BUGGY_IMPL))

    assert outcome.status == STATUS_PASSED
    assert outcome.mode == MODE_DEBUG
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].passed is False
    assert outcome.attempts[1].passed is True
    assert outcome.reproduction_failed is True
    assert outcome.reproduction_verified is True
    assert "reproduction_checked" in _event_types(harness)


def test_debug_without_reproduction_is_not_a_pass(tmp_path):
    """Debug mode with starter code that already passes: no
    failing reproduction was ever observed, so the outcome is
    reproduction_missing, not passed."""
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: generate_diff(
            FIXED_IMPL, _program(FIXED_IMPL), filename="attempt.py"
        ),
        max_attempts=3,
        mode=MODE_DEBUG,
    )
    outcome = harness.run(_task(starter_code=FIXED_IMPL))

    assert outcome.status == STATUS_REPRODUCTION_MISSING
    assert outcome.final_code is None
    assert outcome.attempts == []
    assert outcome.reproduction_verified is False


# --- switching modes per phase ----------------------------------------------


class _PhaseGenerator:
    """One generator that behaves per phase; the test sets
    ``mode`` before each phase runs."""

    def __init__(self) -> None:
        self.mode = MODE_ARCHITECT

    def __call__(self, task, attempts, repo_map=""):
        if self.mode == MODE_ARCHITECT:
            assert "src/add.py" in repo_map
            return "PLAN: fix add with a diff"
        if self.mode == MODE_CODE:
            return generate_diff(task.starter_code, _program(FIXED_IMPL), filename="attempt.py")
        if self.mode == MODE_DEBUG:
            if not attempts:
                return generate_diff(task.starter_code, _program(BUGGY_IMPL), filename="attempt.py")
            return generate_diff(_program(BUGGY_IMPL), _program(FIXED_IMPL), filename="attempt.py")
        raise AssertionError(f"unexpected phase {self.mode!r}")


def test_mode_switch_per_phase_with_mode(tmp_path):
    """One task moves through phases on the same executor and
    generator: Architect plans, Code implements, Debug reproduces
    then fixes. ``with_mode`` switches the mode and its profile."""
    repo = tmp_path / "repo"
    _repo_with_add(repo)
    generator = _PhaseGenerator()
    base = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=3,
        mode=MODE_ARCHITECT,
        workspace_root=repo,
    )

    planned = base.run(_task())
    assert planned.status == STATUS_PLANNED
    assert planned.plan == "PLAN: fix add with a diff"

    generator.mode = MODE_CODE
    code_harness = base.with_mode(MODE_CODE)
    assert code_harness is not base
    assert code_harness.mode == MODE_CODE
    assert code_harness.profile == PROFILE_WORKSPACE_WRITE
    assert code_harness.executor is base.executor
    assert code_harness.generator is base.generator
    coded = code_harness.run(_task(starter_code=BUGGY_IMPL))
    assert coded.status == STATUS_PASSED
    assert coded.final_code == _program(FIXED_IMPL)

    generator.mode = MODE_DEBUG
    debug_harness = base.with_mode(MODE_DEBUG)
    assert debug_harness.profile == PROFILE_WORKSPACE_WRITE
    assert debug_harness.reproduce_first is True
    debugged = debug_harness.run(_task(starter_code=BUGGY_IMPL))
    assert debugged.status == STATUS_PASSED
    assert debugged.reproduction_failed is True
    assert debugged.reproduction_verified is True

    with pytest.raises(HarnessError, match="unknown working mode"):
        base.with_mode("wizard")


# --- default keeps the old behaviour -----------------------------------------


def test_default_without_mode_keeps_legacy_behavior(tmp_path):
    """No mode: full-program answers are still accepted, exactly
    as before modes existed."""
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: _program(FIXED_IMPL),
        max_attempts=2,
    )
    assert harness.mode is None
    assert harness.mode_spec is None
    assert harness.profile == PROFILE_WORKSPACE_WRITE
    assert harness.diff_mode is False
    assert harness.reproduce_first is False

    outcome = harness.run(_task())
    assert outcome.status == STATUS_PASSED
    assert outcome.mode is None
    assert outcome.plan is None
    assert outcome.answer is None


def test_default_diff_mode_still_accepts_full_program(tmp_path):
    """Legacy ``diff_mode=True`` without a mode still falls back
    to full-program text when the generator returns no diff; only
    Code/Debug modes enforce the diff."""

    def generator(task, attempts):
        if not attempts:
            return _program(BUGGY_IMPL)
        return _program(FIXED_IMPL)

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=3,
        diff_mode=True,
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_PASSED
    assert outcome.mode is None


def test_plan_and_answer_modes_respect_budget_ceiling():
    """A plan or answer call that would exceed the budget stops
    as budget_exhausted instead of calling the generator."""
    calls = {"n": 0}

    def generator(task, attempts):
        calls["n"] += 1
        return "PLAN: never reached"

    harness = CodingHarness(
        executor=_CountingExecutor(),
        generator=generator,
        max_attempts=1,
        cost_ceiling_rp=0,
        cost_per_attempt_rp=1,
        mode=MODE_ARCHITECT,
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_BUDGET_EXHAUSTED
    assert calls["n"] == 0
