"""Reproduce-first coding loop (SWE-agent pattern).

Pattern studied, then rewritten as Puspa's own code (see
``docs/brain-depth-triage.md``, Tier 1 SWE-agent, and the REUSE
fragment for this work):

- SWE-agent takes an issue and fixes it with a language model; the
  SWE-bench task shape behind it grades a fix only against tests
  that fail before the fix and pass after it (FAIL_TO_PASS). The
  reproduction discipline behind that shape is: capture the bug in
  a test BEFORE changing the code, require the same test to fail on
  the unpatched code and pass after the fix, and never call a fix
  complete without that pair of observations.
- The failure must also match the reported symptom: a test that
  fails for an unrelated reason (a missing fixture, a typo in the
  test itself) is not a reproduction.

This module is the Puspa version of that discipline, with no
dependency on any SWE-agent code:

- :func:`build_reproduction` turns a task description plus its
  acceptance test code into a :class:`Reproduction` record.
- :class:`ReproduceFirst` is a small state machine the harness loop
  drives: it records the reproduction run (which must fail), then
  records the verification run after the fix (which must pass).
  :meth:`ReproduceFirst.mark_fixed` refuses to certify a fix when
  no failing reproduction was ever observed, or when the
  reproduction still fails after the fix.
- :func:`generate_reproduction_stub` builds a starter reproduction
  test from a task description for tasks that arrive without test
  code: the stub names the expected behaviour, and the caller fills
  in the failing assertion. A stub is never treated as a passing
  reproduction on its own.

The module never runs code itself: the caller supplies a ``run``
callable ``(program_text) -> ExecutionResult`` (the harness
executor), so this state machine works with the sandbox executor
and with the test-only local executor alike.
"""

from __future__ import annotations

from dataclasses import dataclass, field

__all__ = [
    "ReproduceFirst",
    "ReproduceFirstError",
    "Reproduction",
    "build_reproduction",
    "generate_reproduction_stub",
]

# Statuses a reproduction can be in (stored values are English).
STATUS_PENDING = "pending"
STATUS_REPRODUCED = "reproduced"
STATUS_FIXED = "fixed"
STATUS_NOT_REPRODUCED = "not_reproduced"


class ReproduceFirstError(Exception):
    """The reproduce-first order was violated.

    Raised when a fix is certified without a failing reproduction
    first, or when the reproduction run does not actually fail.
    """


@dataclass
class Reproduction:
    """One reproduction test for one task.

    ``test_code`` is the acceptance test (FAIL_TO_PASS): it must
    fail on the unpatched program and pass on the fixed program.
    ``expected_failure`` is an optional substring the failure output
    must contain, so a failure for an unrelated reason does not
    count as a reproduction.
    """

    task_id: str
    description: str
    test_code: str
    expected_failure: str = ""
    status: str = STATUS_PENDING
    failure_output: str = ""
    fix_output: str = ""

    def matches_failure(self, output: str) -> bool:
        """True when the output shows the expected failure symptom."""
        if not self.expected_failure:
            return True
        return self.expected_failure in output


def build_reproduction(
    task_id: str,
    description: str,
    test_code: str,
    *,
    expected_failure: str = "",
) -> Reproduction:
    """Build a reproduction record from a task and its test code.

    Raises :class:`ReproduceFirstError` when there is no test code
    to reproduce with: reproduce-first cannot run on a task that
    carries no failing test.
    """
    if not test_code or not test_code.strip():
        raise ReproduceFirstError(
            f"task {task_id!r} carries no test code; reproduce-first "
            "needs a failing test before any fix"
        )
    return Reproduction(
        task_id=task_id,
        description=description,
        test_code=test_code,
        expected_failure=expected_failure,
    )


def generate_reproduction_stub(task_id: str, description: str, *, function_name: str = "") -> str:
    """Build a starter reproduction test from a task description.

    The stub is a starting point, not a reproduction: it asserts the
    behaviour the description asks for and the caller fills in the
    concrete failing call. Until the stub is run and observed to
    fail, the task has no reproduction.
    """
    target = function_name or "the function under test"
    lines = [
        f"# Reproduction test for {task_id}: {description}",
        "# Fill in the concrete failing call below, then run this",
        "# test against the unpatched code and confirm it fails",
        "# before writing any fix.",
        "",
        "def test_reproduction():",
        f"    # Expected behaviour: {description}",
        f"    # Exercise {target} with the failing input here.",
        "    raise AssertionError('reproduction stub: fill in the failing assertion')",
        "",
    ]
    return "\n".join(lines)


@dataclass
class ReproduceFirst:
    """State machine enforcing reproduce-before-fix for one task.

    Order enforced:

    1. :meth:`record_reproduction` runs the test against the
       unpatched program. It must FAIL, and when the reproduction
       carries an ``expected_failure`` the failure output must
       contain it. Otherwise the state stays ``pending`` and the
       run reports ``not_reproduced``.
    2. :meth:`record_fix` runs the same test against the fixed
       program. It must PASS. A fix that still fails the
       reproduction is recorded but does not certify anything.
    3. :meth:`mark_fixed` certifies the fix only when step 1
       observed a failure and step 2 observed a pass. Anything
       else raises :class:`ReproduceFirstError`.

    The reproduction test itself is frozen once it is reproduced:
    :meth:`record_fix` always runs the same ``test_code`` that was
    reproduced, so the agent cannot make the gate pass by weakening
    the test.
    """

    reproduction: Reproduction
    status: str = STATUS_PENDING
    reproduction_runs: int = 0
    fix_runs: int = 0
    history: list[dict] = field(default_factory=list)

    @property
    def reproduced(self) -> bool:
        """True once a failing reproduction has been observed."""
        return self.status in (STATUS_REPRODUCED, STATUS_FIXED)

    @property
    def fixed(self) -> bool:
        """True once the fix is certified (reproduced, then green)."""
        return self.status == STATUS_FIXED

    def record_reproduction(self, run, program_before: str) -> dict:
        """Run the reproduction test against the unpatched program.

        ``run`` is a callable ``(program_text) -> ExecutionResult``.
        Returns a report dict with ``reproduced`` (bool) and the
        failure output. A passing program or a failure that does not
        match ``expected_failure`` reports ``reproduced: False`` and
        leaves the state at ``pending``.
        """
        self.reproduction_runs += 1
        program = program_before + self.reproduction.test_code
        result = run(program)
        output = (result.stdout or "") + (result.stderr or "")
        reproduced = (not result.ok) and self.reproduction.matches_failure(output)
        self.reproduction.failure_output = output
        if reproduced:
            self.status = STATUS_REPRODUCED
        report = {
            "task_id": self.reproduction.task_id,
            "reproduced": reproduced,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "output": output,
        }
        self.history.append({"step": "reproduction", **report})
        return report

    def record_fix(self, run, program_after: str) -> dict:
        """Run the frozen reproduction test against the fixed program.

        Returns a report dict with ``passed`` (bool). Calling this
        before any reproduction is recorded raises
        :class:`ReproduceFirstError`: the fix cannot be verified
        against a reproduction that was never observed.
        """
        if not self.reproduced:
            raise ReproduceFirstError(
                "cannot verify a fix before a failing reproduction "
                "was observed; run record_reproduction first"
            )
        self.fix_runs += 1
        program = program_after + self.reproduction.test_code
        result = run(program)
        output = (result.stdout or "") + (result.stderr or "")
        self.reproduction.fix_output = output
        passed = bool(result.ok) and not result.timed_out
        report = {
            "task_id": self.reproduction.task_id,
            "passed": passed,
            "exit_code": result.exit_code,
            "timed_out": result.timed_out,
            "output": output,
        }
        self.history.append({"step": "fix_verification", **report})
        return report

    def record_fix_result(self, passed: bool, output: str = "") -> dict:
        """Record a fix verification that already ran elsewhere.

        The harness loop already executed the fixed program (the
        passing attempt); this records that observation instead of
        running the program a second time. Same rule as
        :meth:`record_fix`: a reproduction must exist first.
        """
        if not self.reproduced:
            raise ReproduceFirstError(
                "cannot verify a fix before a failing reproduction "
                "was observed; run record_reproduction first"
            )
        self.fix_runs += 1
        report = {
            "task_id": self.reproduction.task_id,
            "passed": bool(passed),
            "exit_code": None,
            "timed_out": False,
            "output": output,
        }
        self.history.append({"step": "fix_verification", **report})
        return report

    def mark_fixed(self) -> Reproduction:
        """Certify the fix, or refuse with ReproduceFirstError.

        Certification needs both observations: a failing
        reproduction recorded first, and a passing fix verification
        after it. The last fix verification in the history decides
        the pass side; an older pass does not rescue a later failing
        verification.
        """
        if not self.reproduced:
            raise ReproduceFirstError(
                "cannot mark fixed: no failing reproduction was "
                "observed before the fix"
            )
        fix_reports = [entry for entry in self.history if entry["step"] == "fix_verification"]
        if not fix_reports or not fix_reports[-1]["passed"]:
            raise ReproduceFirstError(
                "cannot mark fixed: the reproduction test does not "
                "pass on the fixed program yet"
            )
        self.status = STATUS_FIXED
        self.reproduction.status = STATUS_FIXED
        return self.reproduction
