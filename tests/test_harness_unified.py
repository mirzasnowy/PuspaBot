"""Tests for the unified harness: Aider + SWE-agent + openai/codex.

One harness, three mechanisms (see docs/brain-depth-triage.md):

- repo map (Aider): symbol extraction with signatures, token budget.
- unified diff (Aider): generate/apply, all hunks or none.
- reproduce-first (SWE-agent): no fix is certified without a failing
  reproduction first, then a passing verification of the same test.
- sandbox profiles (openai/codex): read_only / workspace_write /
  full, enforced through the gate and the executor, never widening
  the token.

These tests use no Puspa database tables themselves (in-memory
stubs, a counting executor, and temp dirs only). Note: the shared
``tests/conftest.py`` autouse fixture still creates the schemas and
truncates tables in ``puspa_test`` for every test file in this
directory, including this one; that fixture cannot be opted out of
from here.
"""

import pytest

from puspa.harness.diff import (
    DiffApplyError,
    apply_diff,
    generate_diff,
    is_diff,
    parse_diff,
)
from puspa.harness.executor import LocalTestExecutor, SandboxExecutor
from puspa.harness.loop import (
    STATUS_PASSED,
    STATUS_REPRODUCTION_MISSING,
    CodingHarness,
    CodingTask,
    HarnessError,
)
from puspa.harness.repo_map import build_repo_map, estimate_tokens, render_repo_map
from puspa.harness.reproduce import (
    ReproduceFirst,
    ReproduceFirstError,
    build_reproduction,
    generate_reproduction_stub,
)
from puspa.harness.sandbox_profiles import (
    PROFILE_FULL,
    PROFILE_READ_ONLY,
    PROFILE_WORKSPACE_WRITE,
    ProfileDenied,
    check_tool,
    check_write_path,
    effective_tool_level,
    get_profile,
    profile_allows_tool,
    requires_approval,
)
from puspa.policy import Gate, GateDenied
from puspa.sandbox import Sandbox

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


def _payload(**overrides) -> dict:
    import time

    now = int(time.time())
    payload = {
        "user_id": "user-1",
        "agent_id": "agent-puspa",
        "subagent_id": None,
        "tools": ["read_code", "write_code", "run_tests", "deploy"],
        "domains": [],
        "connectors": {},
        "budget": {"step_max": 25, "token_max": 100000, "rupiah_max": 2000},
        "exp": now + 3600,
        "iat": now,
        "parent": None,
    }
    payload.update(overrides)
    return payload


# --- repo map (Aider) ------------------------------------------------------


def test_repo_map_extracts_symbols_with_signatures(tmp_path):
    (tmp_path / "calc.py").write_text(
        "class Calculator:\n"
        "    def add(self, a: int, b: int = 0) -> int:\n"
        "        return a + b\n"
        "\n"
        "def greet(name: str) -> str:\n"
        '    return "hi " + name\n',
        encoding="utf-8",
    )
    (tmp_path / "plain.py").write_text("VALUE = 1\n", encoding="utf-8")
    repo_map = build_repo_map(tmp_path)
    assert repo_map.files_scanned == 2
    assert repo_map.files_skipped == []
    entries = {(entry.path, entry.kind, entry.name): entry for entry in repo_map.entries}
    assert ("calc.py", "class", "Calculator") in entries
    method = entries[("calc.py", "method", "Calculator.add")]
    assert method.signature == "(self, a: int, b: int = 0) -> int"
    func = entries[("calc.py", "function", "greet")]
    assert func.signature == "(name: str) -> str"
    # A file with no top-level symbols contributes nothing but is
    # still counted as scanned, not skipped.
    assert repo_map.entries_for("plain.py") == []


def test_repo_map_renders_compact_text_and_reports_budget(tmp_path):
    (tmp_path / "a.py").write_text("def alpha():\n    pass\n", encoding="utf-8")
    (tmp_path / "b.py").write_text("def beta():\n    pass\n", encoding="utf-8")
    repo_map = build_repo_map(tmp_path)
    text, report = render_repo_map(repo_map, max_tokens=1000)
    assert "a.py:" in text
    assert "def alpha():" in text
    assert report["truncated"] is False
    assert report["files_included"] == ["a.py", "b.py"]
    assert report["tokens"] == estimate_tokens(text)
    # A tiny budget drops whole files, never half a file.
    tiny_text, tiny_report = render_repo_map(repo_map, max_tokens=5)
    assert tiny_report["truncated"] is True
    assert tiny_report["files_dropped"] == ["b.py"]
    assert "def beta():" not in tiny_text


def test_repo_map_skips_unparseable_file(tmp_path):
    (tmp_path / "good.py").write_text("def ok():\n    pass\n", encoding="utf-8")
    (tmp_path / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    repo_map = build_repo_map(tmp_path)
    assert repo_map.files_scanned == 2
    assert repo_map.files_skipped == ["broken.py"]
    assert [entry.path for entry in repo_map.entries] == ["good.py"]
    text, _report = render_repo_map(repo_map)
    assert "broken.py" not in text


def test_repo_map_ignores_non_python_and_pycache(tmp_path):
    (tmp_path / "notes.txt").write_text("def not_code():\n", encoding="utf-8")
    cache = tmp_path / "__pycache__"
    cache.mkdir()
    (cache / "cached.py").write_text("def cached():\n    pass\n", encoding="utf-8")
    (tmp_path / "real.py").write_text("def real():\n    pass\n", encoding="utf-8")
    repo_map = build_repo_map(tmp_path)
    assert repo_map.files() == ["real.py"]


# --- unified diff (Aider) --------------------------------------------------


def test_diff_round_trip_generate_then_apply():
    original = _program(BUGGY_IMPL)
    fixed = _program(FIXED_IMPL)
    diff = generate_diff(original, fixed, filename="calc.py")
    assert "--- a/calc.py" in diff
    assert "-    return a - b" in diff
    assert "+    return a + b" in diff
    new_text, applied = apply_diff(original, diff)
    assert new_text == fixed
    assert applied == [1]
    assert generate_diff(original, original) == ""


def test_is_diff_detects_diff_and_program():
    assert is_diff(generate_diff("a\n", "b\n")) is True
    assert is_diff("def add(a, b):\n    return a + b\n") is False
    assert is_diff("") is False
    assert is_diff("@@ -1,1 +1,1 @@\n-a\n+b\n") is True


def test_diff_apply_rejects_bad_context_and_applies_nothing():
    original = _program(BUGGY_IMPL)
    # A diff written against different code: its context line
    # ("return a * b") never appears in the original.
    bad_diff = (
        "--- a/calc.py\n"
        "+++ b/calc.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a * b\n"
        "+    return a + b\n"
    )
    with pytest.raises(DiffApplyError) as excinfo:
        apply_diff(original, bad_diff)
    assert excinfo.value.failed_hunks == [1]
    assert "hunk 1" in excinfo.value.detail
    # The caller still holds the original: nothing was applied.
    assert original == _program(BUGGY_IMPL)


def test_diff_apply_multi_hunk_one_bad_applies_nothing():
    original = "line1\nline2\nline3\nline4\nline5\nline6\n"
    good_hunk = "@@ -1,2 +1,2 @@\n line1\n-line2\n+line2-fixed\n"
    bad_hunk = "@@ -5,2 +5,2 @@\n line5\n-lineX\n+line6-fixed\n"
    diff = "--- a/f.txt\n+++ b/f.txt\n" + good_hunk + bad_hunk
    with pytest.raises(DiffApplyError) as excinfo:
        apply_diff(original, diff)
    assert excinfo.value.failed_hunks == [2]
    # Even the good hunk was not applied: no partial apply, ever.
    assert "line2-fixed" not in original
    parsed = parse_diff(diff)
    assert [hunk.number for hunk in parsed.hunks] == [1, 2]


def test_diff_apply_insertion_and_malformed_diff():
    original = "a\nc\n"
    diff = "--- a/f.txt\n+++ b/f.txt\n@@ -1,1 +1,2 @@\n a\n+b\n"
    new_text, applied = apply_diff(original, diff)
    assert new_text == "a\nb\nc\n"
    assert applied == [1]
    with pytest.raises(DiffApplyError):
        parse_diff("@@ -1,1 +1,1 @@\n-a\n+b\n+c\n")


# --- reproduce-first (SWE-agent) -------------------------------------------


def test_reproduce_first_certifies_fix_after_failing_reproduction(tmp_path):
    executor = LocalTestExecutor(tmp_path / "work")
    tracker = ReproduceFirst(build_reproduction("task-add", "add sums", TEST_CODE))
    report = tracker.record_reproduction(executor.run, BUGGY_IMPL)
    assert report["reproduced"] is True
    assert tracker.reproduced is True
    assert tracker.fixed is False
    fix_report = tracker.record_fix(executor.run, FIXED_IMPL)
    assert fix_report["passed"] is True
    tracker.mark_fixed()
    assert tracker.fixed is True
    assert tracker.reproduction.status == "fixed"


def test_reproduce_first_refuses_fix_without_reproduction(tmp_path):
    executor = LocalTestExecutor(tmp_path / "work")
    tracker = ReproduceFirst(build_reproduction("task-add", "add sums", TEST_CODE))
    with pytest.raises(ReproduceFirstError):
        tracker.record_fix(executor.run, FIXED_IMPL)
    with pytest.raises(ReproduceFirstError):
        tracker.mark_fixed()
    assert tracker.fixed is False


def test_reproduce_first_reproduction_must_fail_and_fix_must_pass(tmp_path):
    executor = LocalTestExecutor(tmp_path / "work")
    # A program that already passes is not a reproduction.
    tracker = ReproduceFirst(build_reproduction("task-add", "add sums", TEST_CODE))
    report = tracker.record_reproduction(executor.run, FIXED_IMPL)
    assert report["reproduced"] is False
    assert tracker.reproduced is False
    # A fix that still fails the reproduction does not certify.
    tracker2 = ReproduceFirst(build_reproduction("task-add", "add sums", TEST_CODE))
    assert tracker2.record_reproduction(executor.run, BUGGY_IMPL)["reproduced"] is True
    assert tracker2.record_fix(executor.run, BUGGY_IMPL)["passed"] is False
    with pytest.raises(ReproduceFirstError):
        tracker2.mark_fixed()


def test_reproduce_first_wrong_symptom_is_not_a_reproduction(tmp_path):
    executor = LocalTestExecutor(tmp_path / "work")
    tracker = ReproduceFirst(
        build_reproduction("task-add", "add sums", TEST_CODE, expected_failure="ZeroDivisionError")
    )
    report = tracker.record_reproduction(executor.run, BUGGY_IMPL)
    # The test fails, but with AssertionError, not the expected
    # symptom, so this is not a reproduction of the reported bug.
    assert report["reproduced"] is False
    assert tracker.reproduced is False


def test_reproduce_first_needs_test_code_and_stub_is_not_proof():
    with pytest.raises(ReproduceFirstError):
        build_reproduction("task-add", "add sums", "")
    stub = generate_reproduction_stub("task-add", "add(a, b) returns the sum", function_name="add")
    assert "def test_reproduction():" in stub
    assert "task-add" in stub
    assert "fill in the failing assertion" in stub


# --- sandbox profiles (openai/codex) ---------------------------------------


def test_profiles_map_tools_as_documented():
    assert profile_allows_tool(PROFILE_READ_ONLY, "read_code") is True
    assert profile_allows_tool(PROFILE_READ_ONLY, "write_code") is False
    assert profile_allows_tool(PROFILE_READ_ONLY, "run_tests") is False
    assert profile_allows_tool(PROFILE_WORKSPACE_WRITE, "write_code") is True
    assert profile_allows_tool(PROFILE_WORKSPACE_WRITE, "run_tests") is True
    assert profile_allows_tool(PROFILE_FULL, "write_code") is True
    assert profile_allows_tool(PROFILE_FULL, "deploy") is False
    assert get_profile(PROFILE_WORKSPACE_WRITE).name == PROFILE_WORKSPACE_WRITE
    with pytest.raises(ProfileDenied):
        get_profile("danger-full-access")


def test_effective_level_is_the_narrower_of_profile_and_token():
    token = _payload()
    # Token allows, profile denies: read_only wins.
    assert effective_tool_level(PROFILE_READ_ONLY, token, "write_code") == "deny"
    # Profile allows, token denies: token wins, even under full.
    denied = _payload(tool_permissions={"write_code": "deny"})
    assert effective_tool_level(PROFILE_FULL, denied, "write_code") == "deny"
    # Both allow: allow. No payload at all: deny (fail-closed).
    assert effective_tool_level(PROFILE_WORKSPACE_WRITE, token, "write_code") == "allow"
    assert effective_tool_level(PROFILE_WORKSPACE_WRITE, None, "write_code") == "deny"
    # Ask on either side stays ask, never widened to allow.
    asked = _payload(tool_permissions={"deploy": "ask"})
    assert effective_tool_level(PROFILE_FULL, asked, "deploy") == "ask"


def test_check_tool_denies_write_under_read_only_through_gate():
    gate = Gate()
    token = _payload()
    assert check_tool(PROFILE_WORKSPACE_WRITE, gate, token, "write_code") == "allow"
    with pytest.raises(GateDenied, match=r"read_only.*denies tool 'write_code'"):
        check_tool(PROFILE_READ_ONLY, gate, token, "write_code")
    with pytest.raises(GateDenied, match=r"read_only.*denies tool 'run_tests'"):
        check_tool(PROFILE_READ_ONLY, gate, token, "run_tests")
    # Token deny still propagates under workspace_write.
    denied = _payload(tool_permissions={"write_code": "deny"})
    with pytest.raises(GateDenied):
        check_tool(PROFILE_WORKSPACE_WRITE, gate, denied, "write_code")
    # No gate at all: the profile alone still denies.
    with pytest.raises(GateDenied):
        check_tool(PROFILE_READ_ONLY, None, None, "write_code")


def test_check_write_path_refuses_escape_and_protected_paths(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    assert (
        check_write_path(PROFILE_WORKSPACE_WRITE, "src/add.py", workspace_root=root)
        == (root / "src/add.py").resolve()
    )
    with pytest.raises(ProfileDenied, match=r"read_only.*denies writes"):
        check_write_path(PROFILE_READ_ONLY, "src/add.py", workspace_root=root)
    with pytest.raises(ProfileDenied, match="outside the workspace"):
        check_write_path(PROFILE_WORKSPACE_WRITE, "../escape.py", workspace_root=root)
    with pytest.raises(ProfileDenied, match="outside the workspace"):
        check_write_path(PROFILE_FULL, "/etc/passwd", workspace_root=root)
    with pytest.raises(ProfileDenied, match=r"protected directory '.git'"):
        check_write_path(PROFILE_FULL, ".git/config", workspace_root=root)


def test_requires_approval_matrix(tmp_path):
    root = tmp_path / "work"
    root.mkdir()
    assert requires_approval(PROFILE_READ_ONLY, "write_code") is True
    assert requires_approval(PROFILE_READ_ONLY, "run_tests") is True
    assert requires_approval(PROFILE_WORKSPACE_WRITE, "write_code") is False
    assert requires_approval(PROFILE_WORKSPACE_WRITE, "deploy") is True
    assert requires_approval(PROFILE_FULL, "write_code") is False
    assert requires_approval(PROFILE_FULL, "deploy") is True
    # A workspace_write write outside the workspace needs approval.
    assert (
        requires_approval(
            PROFILE_WORKSPACE_WRITE,
            "write_code",
            path="../outside.py",
            workspace_root=root,
        )
        is True
    )


# --- executor write_file, profile-enforced ---------------------------------


def test_executor_write_file_enforces_profile(tmp_path):
    executor = LocalTestExecutor(tmp_path / "work")
    written = executor.write_file("src/add.py", FIXED_IMPL, profile=PROFILE_WORKSPACE_WRITE)
    assert written.read_text(encoding="utf-8") == FIXED_IMPL
    with pytest.raises(ProfileDenied, match=r"read_only.*denies writes"):
        executor.write_file("src/add.py", FIXED_IMPL, profile=PROFILE_READ_ONLY)
    with pytest.raises(ProfileDenied, match="outside the workspace"):
        executor.write_file("../escape.py", "x", profile=PROFILE_WORKSPACE_WRITE)
    with pytest.raises(ProfileDenied, match=r"protected directory '.git'"):
        executor.write_file(".git/config", "x", profile=PROFILE_FULL)


def test_sandbox_executor_write_file_always_refuses():
    executor = SandboxExecutor(Sandbox())
    with pytest.raises(NotImplementedError, match="no writable filesystem"):
        executor.write_file("add.py", FIXED_IMPL, profile=PROFILE_FULL)


# --- loop integration: one harness -----------------------------------------


def test_e2e_reproduce_first_diff_fix_to_green(tmp_path):
    """Attempt 1 fails (the reproduction), attempt 2 applies a diff
    that fixes it, the test goes green: the real harness, end to
    end, through the local test executor."""
    diff_to_fix = generate_diff(_program(BUGGY_IMPL), _program(FIXED_IMPL), filename="attempt.py")

    def generator(task, attempts):
        if not attempts:
            return _program(BUGGY_IMPL)
        if "diff did not apply" in attempts[-1].reason:
            return _program(FIXED_IMPL)
        return diff_to_fix

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=5,
        diff_mode=True,
        reproduce_first=True,
    )
    outcome = harness.run(_task(starter_code=BUGGY_IMPL))

    assert outcome.status == STATUS_PASSED
    assert len(outcome.attempts) == 2
    assert outcome.attempts[0].passed is False
    assert "AssertionError" in outcome.attempts[0].result.stderr
    assert outcome.attempts[1].passed is True
    assert "ALL TESTS PASSED" in outcome.attempts[1].result.stdout
    assert outcome.final_code == _program(FIXED_IMPL)
    assert outcome.reproduction_failed is True
    assert outcome.reproduction_verified is True
    assert outcome.profile == PROFILE_WORKSPACE_WRITE
    event_types = [event["type"] for event in harness.event_log()]
    assert "reproduction_checked" in event_types
    assert "harness_passed" in event_types


def test_e2e_reproduce_first_without_starter_first_failure_is_reproduction(tmp_path):
    """No starter code: the first failing attempt is the
    reproduction, the second attempt fixes it through a diff."""

    def generator(task, attempts):
        if not attempts:
            return _program(BUGGY_IMPL)
        return generate_diff(_program(BUGGY_IMPL), _program(FIXED_IMPL), filename="attempt.py")

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=5,
        diff_mode=True,
        reproduce_first=True,
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_PASSED
    assert len(outcome.attempts) == 2
    assert outcome.reproduction_failed is True
    assert outcome.reproduction_verified is True


def test_reproduce_first_first_attempt_pass_is_not_a_pass(tmp_path):
    """A first attempt that passes straight away proves nothing:
    no failing reproduction was ever observed, so the outcome is
    reproduction_missing, not passed."""
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: _program(FIXED_IMPL),
        max_attempts=3,
        reproduce_first=True,
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_REPRODUCTION_MISSING
    assert outcome.final_code is None
    assert outcome.reproduction_verified is False


def test_reproduce_first_starter_already_passing_stops_before_generator(tmp_path):
    """Starter code that already passes: the probe stops the run
    before the generator is ever asked for a fix."""
    calls = {"n": 0}

    def generator(task, attempts):
        calls["n"] += 1
        return _program(FIXED_IMPL)

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=3,
        reproduce_first=True,
    )
    outcome = harness.run(_task(starter_code=FIXED_IMPL))
    assert outcome.status == STATUS_REPRODUCTION_MISSING
    assert calls["n"] == 0
    assert outcome.attempts == []


def test_diff_that_does_not_apply_fails_the_attempt_not_the_run(tmp_path):
    """A diff with stale context fails that one attempt (reason
    recorded, code unchanged) and the next attempt can still fix
    the program with a full rewrite."""
    bad_diff = (
        "--- a/attempt.py\n"
        "+++ b/attempt.py\n"
        "@@ -1,2 +1,2 @@\n"
        " def add(a, b):\n"
        "-    return a * b\n"
        "+    return a + b\n"
    )

    def generator(task, attempts):
        if not attempts:
            return _program(BUGGY_IMPL)
        if len(attempts) == 1:
            return bad_diff
        return _program(FIXED_IMPL)

    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=generator,
        max_attempts=5,
        diff_mode=True,
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_PASSED
    assert len(outcome.attempts) == 3
    assert outcome.attempts[1].passed is False
    assert "diff did not apply" in outcome.attempts[1].reason
    # The failed diff left the program untouched: the recorded code
    # for that attempt is still the buggy program.
    assert outcome.attempts[1].code == _program(BUGGY_IMPL)
    assert outcome.attempts[2].passed is True


def test_read_only_profile_refuses_write_before_anything_runs(tmp_path):
    """Under read_only the harness refuses before the generator or
    the executor is touched, even when the token allows everything."""
    from puspa.harness.executor import ExecutionResult, Executor

    class _CountingExecutor(Executor):
        def __init__(self):
            self.calls = 0

        def run(self, code, *, timeout_s=30):
            self.calls += 1
            return ExecutionResult(ok=True, stdout="", stderr="", exit_code=0)

    executor = _CountingExecutor()
    calls = {"n": 0}

    def generator(task, attempts):
        calls["n"] += 1
        return _program(FIXED_IMPL)

    harness = CodingHarness(
        executor=executor,
        generator=generator,
        gate=Gate(),
        token_payload=_payload(),
        profile=PROFILE_READ_ONLY,
    )
    with pytest.raises(GateDenied, match=r"read_only.*denies tool 'write_code'"):
        harness.run(_task())
    assert calls["n"] == 0
    assert executor.calls == 0


def test_harness_rejects_unknown_profile_at_construction(tmp_path):
    with pytest.raises(ProfileDenied):
        CodingHarness(
            executor=LocalTestExecutor(tmp_path / "work"),
            generator=lambda task, attempts: "",
            profile="danger-full-access",
        )


def test_old_behaviour_unchanged_without_new_options(tmp_path):
    """Defaults (workspace_write, diff off, reproduce off) behave
    exactly like the old loop: full program text, pass on green."""
    harness = CodingHarness(
        executor=LocalTestExecutor(tmp_path / "work"),
        generator=lambda task, attempts: _program(FIXED_IMPL),
        max_attempts=3,
    )
    outcome = harness.run(_task())
    assert outcome.status == STATUS_PASSED
    assert outcome.profile == PROFILE_WORKSPACE_WRITE
    assert outcome.reproduction_failed is False


def test_production_factory_accepts_profile(tmp_path):
    harness = CodingHarness(
        executor=SandboxExecutor(Sandbox()),
        generator=lambda task, attempts: "",
        gate=Gate(),
        token_payload=_payload(),
        production=True,
        profile=PROFILE_READ_ONLY,
    )
    assert harness.production is True
    assert harness.profile == PROFILE_READ_ONLY
    with pytest.raises(HarnessError):
        CodingHarness(
            executor=LocalTestExecutor(tmp_path / "work"),
            generator=lambda task, attempts: "",
            production=True,
            profile=PROFILE_FULL,
        )
