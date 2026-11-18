"""Coding harness: a real engineering loop for Puspa (Fase Otak).

Pattern sources (studied, then rewritten as Puspa's own code; see
``docs/brain-repo-list.md`` and ``docs/reuse-fase-otak-harness.md``):

- opencode / claude code: a coding agent loop that writes code,
  runs tests, reads the failure, and fixes it; per-tool permissions
  (allow / ask / deny) instead of one blanket allow per turn.
- SWE-bench: the task shape. A task carries fixed acceptance tests
  (FAIL_TO_PASS); the loop passes when those tests go green and the
  already-passing tests stay green. The harness never grades the
  agent's own words, only the test exit code.
- Voyager (self-verification part): generated code is verified by
  actually running it in the sandbox before it is trusted.
- ChatDev: readable handoffs between steps (here: each attempt
  records its code plus its test result, so the next attempt can
  read exactly what failed).

How it works:

1. The caller gives a :class:`CodingTask` (description plus fixed
   acceptance tests) and a ``generator`` callable. The generator
   produces the full program text for an attempt (implementation
   plus the task tests); in production it calls the model, in tests
   it is a stub. The harness never invents code by itself.
2. Each attempt runs through the executor and is graded by the
   ``verifier`` (default: the program exited 0). On failure the
   failure output is handed to the generator for the next attempt.
3. The loop stops on: all tests green (``passed``), the attempt
   limit (``attempt_limit``), the per-task cost ceiling
   (``budget_exhausted``), a denied tool (``denied``), a tool that
   needs user approval (``pending_approval``), or an unavailable
   sandbox (``sandbox_unavailable``).
4. Deploy is a separate step AFTER the loop passes, and it always
   goes through the approval gate (:class:`puspa.policy.Gate`,
   kind ``"deploy"``). :meth:`CodingHarness.request_deploy` asks
   the user; :meth:`CodingHarness.deploy` executes exactly once
   after approval. Nothing deploys without that approval.

Safety rules enforced here:

- Production runs use :class:`SandboxExecutor` only. Passing any
  other executor with ``production=True`` raises
  :class:`HarnessError` at construction time.
- A :class:`SandboxUnavailable` from the executor ends the run with
  status ``sandbox_unavailable``. The loop never retries on the
  host and never falls back to :class:`LocalTestExecutor`.
- Attempt and cost ceilings use the existing ceiling pattern
  (:class:`puspa.runtime.budget.BudgetTracker`,
  ``CEILING_RP_PER_TASK``). Exceeding either stops the loop and
  reports it; it never continues silently.
- Tool use is checked against the per-tool permission model
  (``puspa.policy.tokens.tool_permission``) through the gate before
  the first attempt. A "deny" tool raises :class:`GateDenied` and
  nothing runs; an "ask" tool stops the run as
  ``pending_approval`` until the user approves.

One harness, three mechanisms (see ``docs/brain-depth-triage.md``;
Aider + SWE-agent + openai/codex are one harness, not three):

- Surgical edits (Aider): with ``diff_mode=True`` the generator may
  return a unified diff instead of a full program. The diff is
  applied to the current program text with
  :mod:`puspa.harness.diff`; a diff whose context does not match is
  rejected whole (never applied partially) and the attempt fails
  with the reason recorded.
- Reproduce-first (SWE-agent): with ``reproduce_first=True`` the
  loop refuses to call a fix complete unless a failing reproduction
  was observed first. When the task carries ``starter_code`` that
  code plus the acceptance tests is run as the reproduction before
  any fix attempt; otherwise the first failing attempt is the
  reproduction, and a first attempt that passes straight away ends
  as ``reproduction_missing`` instead of ``passed``.
- Sandbox profiles (openai/codex): the ``profile`` parameter
  (``read_only`` / ``workspace_write`` / ``full``) caps every tool
  use through :mod:`puspa.harness.sandbox_profiles` and the gate.
  Under ``read_only`` any write or execution is refused with a
  clear error before anything runs. The profile never widens the
  token and the token never widens the profile.

Defaults keep the old behaviour exactly: ``profile`` is
``workspace_write``, ``diff_mode`` is off, ``reproduce_first`` is
off, and a generator that returns full program text works as
before.

No network calls are made from this module. Events go to ``sink``
(callable ``(event_type, content)``), the same shape as
``puspa.runtime.tasks``; the default sink keeps them in memory.
"""

from __future__ import annotations

import hashlib
from dataclasses import dataclass, field

from puspa.policy import Gate, GateDenied
from puspa.policy.tokens import tool_permission
from puspa.runtime.budget import CEILING_RP_PER_TASK, BudgetExhausted, BudgetTracker
from puspa.sandbox import SandboxUnavailable

from .diff import DiffApplyError, apply_diff, is_diff
from .executor import ExecutionResult, Executor, LocalTestExecutor, SandboxExecutor
from .reproduce import ReproduceFirst, build_reproduction
from .sandbox_profiles import PROFILE_WORKSPACE_WRITE, check_tool, get_profile

__all__ = [
    "AttemptRecord",
    "CodingHarness",
    "CodingTask",
    "HarnessError",
    "HarnessOutcome",
    "create_production_harness",
    "default_verdict",
]

# Tools the loop itself uses, checked against the per-tool model.
TOOL_WRITE_CODE = "write_code"
TOOL_RUN_TESTS = "run_tests"
TOOL_DEPLOY = "deploy"

# Outcome statuses (stored values are English, per naming rule).
STATUS_PASSED = "passed"
STATUS_ATTEMPT_LIMIT = "attempt_limit"
STATUS_BUDGET_EXHAUSTED = "budget_exhausted"
STATUS_SANDBOX_UNAVAILABLE = "sandbox_unavailable"
STATUS_PENDING_APPROVAL = "pending_approval"
STATUS_DENIED = "denied"
STATUS_REPRODUCTION_MISSING = "reproduction_missing"


class HarnessError(Exception):
    """The harness itself is misconfigured or misused (not a test
    failure and not a sandbox failure)."""


def default_verdict(result: ExecutionResult) -> tuple[bool, str]:
    """Grade one attempt from its execution result only.

    Passed means the program ran to completion (exit code 0). A
    failing assert, a raised exception, or a timeout all fail the
    attempt. The agent's words are never graded.
    """
    if result.timed_out:
        return False, "execution timed out"
    if not result.ok:
        return False, f"exit code {result.exit_code}"
    return True, ""


@dataclass
class CodingTask:
    """One coding task for the loop (SWE-bench task shape).

    ``test_code`` holds the fixed acceptance tests (FAIL_TO_PASS):
    the loop passes only when the generated program, which must
    include these tests, exits 0. ``starter_code`` is optional
    starting code the generator may build on.
    """

    task_id: str
    user_id: str
    agent_id: str
    description: str
    test_code: str
    starter_code: str = ""


@dataclass
class AttemptRecord:
    """One loop attempt: the code that was tried plus its result."""

    attempt: int
    code: str
    result: ExecutionResult
    passed: bool
    cost_rp: int
    reason: str = ""


@dataclass
class HarnessOutcome:
    """Final outcome of one harness run."""

    task_id: str
    user_id: str
    agent_id: str
    status: str
    attempts: list[AttemptRecord] = field(default_factory=list)
    final_code: str | None = None
    reason: str = ""
    test_only: bool = False
    # Sandbox profile the run used.
    profile: str = PROFILE_WORKSPACE_WRITE
    # Reproduce-first evidence: True once a failing reproduction was
    # observed (starter probe or a failing attempt), True for
    # reproduction_verified only when that reproduction later passed
    # on the fixed program.
    reproduction_failed: bool = False
    reproduction_verified: bool = False


class CodingHarness:
    """Run one coding task through the generate, test, fix loop."""

    def __init__(
        self,
        *,
        executor: Executor,
        generator,
        gate: Gate | None = None,
        token_payload: dict | None = None,
        sink=None,
        verifier=None,
        max_attempts: int = 5,
        cost_ceiling_rp: int = CEILING_RP_PER_TASK,
        cost_per_attempt_rp: int = 0,
        timeout_s: int = 30,
        production: bool = False,
        profile: str = PROFILE_WORKSPACE_WRITE,
        diff_mode: bool = False,
        reproduce_first: bool = False,
    ) -> None:
        if max_attempts < 1:
            raise HarnessError("max_attempts must be at least 1")
        if cost_ceiling_rp < 0 or cost_per_attempt_rp < 0:
            raise HarnessError("cost ceiling and cost per attempt must not be negative")
        # Unknown profile names are refused at construction time.
        get_profile(profile)
        if production and not isinstance(executor, SandboxExecutor):
            raise HarnessError(
                "production harness requires a SandboxExecutor; "
                f"got {type(executor).__name__}. LocalTestExecutor is "
                "test-only and must never run in production."
            )
        self.executor = executor
        # generator(task, attempts) -> str (full program text, or a
        # unified diff when diff_mode is on).
        self.generator = generator
        self.gate = gate
        self.token_payload = token_payload
        self.profile = profile
        self.diff_mode = diff_mode
        self.reproduce_first = reproduce_first
        self._event: list[dict] = []
        self.sink = sink or (lambda event_type, content: self._event.append({"type": event_type, "content": content}))
        # verifier(result) -> (passed: bool, reason: str).
        self.verifier = verifier or default_verdict
        self.max_attempts = max_attempts
        self.cost_per_attempt_rp = cost_per_attempt_rp
        self.timeout_s = timeout_s
        self.production = production
        self.test_only = isinstance(executor, LocalTestExecutor)
        self.budget = BudgetTracker(rupiah_max=cost_ceiling_rp, step_max=max_attempts)

    # -- events ---------------------------------------------------------
    def _record(self, event_type: str, content: dict) -> None:
        try:
            self.sink(event_type, content)
        except Exception:
            # Ledger writes are best-effort (Fase 0 decision no. 9):
            # a dead sink must not kill the run.
            pass

    def event_log(self) -> list[dict]:
        """Events recorded by the default sink (for tests)."""
        return list(self._event)

    # -- permissions ----------------------------------------------------
    def _check_tools(self, task: CodingTask) -> str | None:
        """Check the per-tool permissions for this run.

        The sandbox profile caps every tool first, then the gate
        enforces the token side; the effective level is the narrower
        of the two. Returns None when the loop may proceed, or the
        outcome status "pending_approval" when a tool needs user
        approval. Raises GateDenied for a denied tool (profile or
        token): nothing runs at all.
        """
        for tool in (TOOL_WRITE_CODE, TOOL_RUN_TESTS):
            level = check_tool(self.profile, self.gate, self.token_payload, tool)
            if level == "ask":
                self._record(
                    "approval_requested",
                    {"task_id": task.task_id, "tool": tool, "reason": "tool needs user approval"},
                )
                return STATUS_PENDING_APPROVAL
        return None

    # -- the loop ---------------------------------------------------------
    def run(self, task: CodingTask) -> HarnessOutcome:
        """Run the generate, test, fix loop until green or a limit."""
        outcome = HarnessOutcome(
            task_id=task.task_id,
            user_id=task.user_id,
            agent_id=task.agent_id,
            status=STATUS_ATTEMPT_LIMIT,
            test_only=self.test_only,
            profile=self.profile,
        )
        self._record("harness_started", {"task_id": task.task_id, "description": task.description})
        pending = self._check_tools(task)
        if pending is not None:
            outcome.status = pending
            outcome.reason = "a tool used by this run needs user approval"
            self._record("harness_pending_approval", {"task_id": task.task_id})
            return outcome

        # Reproduce-first, starter probe: when the task carries
        # starter code, run it plus the acceptance tests before any
        # fix attempt. The probe must FAIL; a starter that already
        # passes has no bug to reproduce, so the run stops as
        # reproduction_missing instead of ever claiming a fix.
        tracker: ReproduceFirst | None = None
        if self.reproduce_first:
            tracker = ReproduceFirst(build_reproduction(task.task_id, task.description, task.test_code))
            if task.starter_code:
                report = tracker.record_reproduction(
                    lambda program: self.executor.run(program, timeout_s=self.timeout_s),
                    task.starter_code,
                )
                outcome.reproduction_failed = bool(report["reproduced"])
                self._record(
                    "reproduction_checked",
                    {"task_id": task.task_id, "reproduced": report["reproduced"]},
                )
                if not report["reproduced"]:
                    outcome.status = STATUS_REPRODUCTION_MISSING
                    outcome.reason = (
                        "starter code already passes the acceptance tests; "
                        "no failing reproduction was observed before any fix"
                    )
                    self._record("harness_failed", {"task_id": task.task_id, "status": outcome.status})
                    return outcome

        # Current full program text. A diff attempt applies against
        # this; a full-program attempt replaces it.
        current_code = task.starter_code
        for attempt_no in range(1, self.max_attempts + 1):
            try:
                self.budget.record_cost(self.cost_per_attempt_rp)
                self.budget.record_step(1)
            except BudgetExhausted as exc:
                outcome.status = STATUS_BUDGET_EXHAUSTED
                outcome.reason = str(exc)
                self._record("budget_exhausted", {"task_id": task.task_id, "reason": str(exc)})
                self._record("harness_failed", {"task_id": task.task_id, "status": outcome.status})
                return outcome
            self._record("attempt_started", {"task_id": task.task_id, "attempt": attempt_no})
            generated = self.generator(task, list(outcome.attempts))
            code = generated
            if self.diff_mode and is_diff(generated):
                try:
                    code, _applied = apply_diff(current_code, generated)
                except DiffApplyError as exc:
                    # The diff is rejected whole: nothing was
                    # applied, and the attempt fails with the reason
                    # recorded for the next generator call.
                    record = AttemptRecord(
                        attempt=attempt_no,
                        code=current_code,
                        result=ExecutionResult(ok=False, stdout="", stderr=str(exc), exit_code=None),
                        passed=False,
                        cost_rp=self.cost_per_attempt_rp,
                        reason=f"diff did not apply: {exc}",
                    )
                    outcome.attempts.append(record)
                    self._record(
                        "attempt_finished",
                        {"task_id": task.task_id, "attempt": attempt_no, "passed": False, "reason": record.reason},
                    )
                    continue
            current_code = code
            try:
                result = self.executor.run(code, timeout_s=self.timeout_s)
            except SandboxUnavailable as exc:
                # Fail-closed: the sandbox cannot run. Stop here.
                # No retry, no host fallback, no local executor swap.
                outcome.status = STATUS_SANDBOX_UNAVAILABLE
                outcome.reason = str(exc)
                self._record("sandbox_unavailable", {"task_id": task.task_id, "reason": str(exc)})
                self._record("harness_failed", {"task_id": task.task_id, "status": outcome.status})
                return outcome
            passed, reason = self.verifier(result)
            record = AttemptRecord(
                attempt=attempt_no,
                code=code,
                result=result,
                passed=passed,
                cost_rp=self.cost_per_attempt_rp,
                reason=reason,
            )
            outcome.attempts.append(record)
            self._record(
                "attempt_finished",
                {"task_id": task.task_id, "attempt": attempt_no, "passed": passed, "reason": reason},
            )
            if passed:
                if self.reproduce_first and tracker is not None:
                    if tracker.reproduced:
                        # The starter probe already failed, and this
                        # passing attempt is the fix verification.
                        tracker.record_fix_result(
                            True,
                            (result.stdout or "") + (result.stderr or ""),
                        )
                        tracker.mark_fixed()
                        outcome.reproduction_failed = True
                        outcome.reproduction_verified = True
                    elif any(not attempt.passed for attempt in outcome.attempts[:-1]):
                        # No starter probe: an earlier failing attempt
                        # is the reproduction for this fix.
                        outcome.reproduction_failed = True
                        outcome.reproduction_verified = True
                    else:
                        outcome.status = STATUS_REPRODUCTION_MISSING
                        outcome.reason = (
                            "first attempt passed without a failing "
                            "reproduction; the fix is not proven"
                        )
                        self._record("harness_failed", {"task_id": task.task_id, "status": outcome.status})
                        return outcome
                outcome.status = STATUS_PASSED
                outcome.final_code = code
                outcome.reason = ""
                self._record("harness_passed", {"task_id": task.task_id, "attempts": attempt_no})
                return outcome
        outcome.status = STATUS_ATTEMPT_LIMIT
        outcome.reason = f"still failing after {self.max_attempts} attempts"
        self._record("attempt_limit_reached", {"task_id": task.task_id, "max_attempts": self.max_attempts})
        self._record("harness_failed", {"task_id": task.task_id, "status": outcome.status})
        return outcome

    # -- deploy (approval gate, after the loop passes) ------------------
    def request_deploy(self, outcome: HarnessOutcome, *, content: dict | None = None):
        """Ask the user for approval before deploying a passed result.

        Only a ``passed`` outcome can be deployed. Returns the
        :class:`ApprovalRequest` from the gate (kind ``"deploy"``).
        The caller approves it (``gate.approve``) and then calls
        :meth:`deploy`. Without a gate configured, this raises
        :class:`HarnessError`: deploy without an approval path is
        refused, not silently allowed.
        """
        if outcome.status != STATUS_PASSED or outcome.final_code is None:
            raise HarnessError("only a passed outcome can be deployed")
        if self.gate is None or self.token_payload is None:
            raise HarnessError("deploy needs a gate and a token payload; refusing without approval")
        # Raises GateDenied for a denied tool; "ask" is fine here
        # because requesting approval IS the ask flow.
        self.gate.authorize_tool(self.token_payload, TOOL_DEPLOY)
        deploy_content = content if content is not None else {
            "task_id": outcome.task_id,
            "code_sha256": hashlib.sha256(outcome.final_code.encode("utf-8")).hexdigest(),
        }
        request = self.gate.request_approval(
            user_id=outcome.user_id,
            agent_id=outcome.agent_id,
            token=self.token_payload,
            kind="deploy",
            content=deploy_content,
        )
        self._record("deploy_requested", {"task_id": outcome.task_id, "request_id": request.request_id})
        return request

    def deploy(self, request_id: str, *, executor):
        """Execute an approved deploy exactly once, through the gate.

        ``executor`` is a caller-supplied callable ``(content) ->
        result`` that performs the deploy step. The gate re-checks
        approval, content hash, and token validity before calling
        it, and runs it at most once per request.
        """
        if self.gate is None:
            raise HarnessError("deploy needs a gate; refusing without approval")
        return self.gate.execute(request_id, executor=executor)


def create_production_harness(
    *,
    generator,
    gate: Gate,
    token_payload: dict,
    sandbox=None,
    **kwargs,
) -> CodingHarness:
    """Build a production harness: sandbox executor only, always.

    ``LocalTestExecutor`` cannot reach this factory at all, so a
    production harness can never execute generated code on the host.
    """
    return CodingHarness(
        executor=SandboxExecutor(sandbox),
        generator=generator,
        gate=gate,
        token_payload=token_payload,
        production=True,
        **kwargs,
    )


def tool_permission_for(token_payload: dict | None, tool: str) -> str:
    """Per-tool level for the harness tools ("allow"/"ask"/"deny").

    No payload means no permission information, so the answer is
    "deny" (fail-closed). Thin wrapper over
    ``puspa.policy.tokens.tool_permission`` for harness callers.
    """
    if token_payload is None:
        return "deny"
    return tool_permission(token_payload, tool)
