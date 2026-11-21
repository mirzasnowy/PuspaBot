"""Evaluation harness (revision 7 gate 11, section 2.11).

Pattern studied (rewritten, not copied):
- UKGovernmentBEIS/inspect_ai (MIT): task, solver and scorer shape,
  multi-turn tool use, sandboxed execution, scoring from evidence
  rather than the agent's words.
- sierra-research/tau-bench (MIT): a simulated user plus a domain
  policy the agent must not break, judged by the final database state
  after the task, not by the transcript.
- EleutherAI/lm-evaluation-harness (MIT): versioned task configs, a
  pinned prompt and a scorer per task, so regression runs are
  reproducible.

Puspa's harness, in Puspa's own code:

- ``EvalTask``: one versioned task (id, prompt, cassette name,
  expected final state, policy). The 10 reference tasks come from the
  recorded cassettes in ``cassettes/`` via ``load_reference_tasks``.
- ``Solver``: a callable ``(task, player) -> dict`` that runs the task
  with tools replaced by a ``CassettePlayer`` (no network, no live
  calls, no cost). The default solver replays every recording in
  order and builds the final state from the tool outputs.
- ``Scorer``: a callable ``(task, final_state) -> (passed, details)``
  that compares the final state against the expected state, the
  tau-bench way. The default scorer is ``score_state``.
- ``run_price_injection_eval``: the gate 11 price-injection test.
  The cassette price is tampered with, the solver replays it, and the
  existing ``puspa/evidence/verifier.py`` must refuse to verify the
  tampered value against a reread of the true value.

Numbers in a report are computed by code from scored states, never
typed by a model. Where a report needs an evidence-backed number it
goes through ``puspa/evidence/renderer.py`` (``render_report``).

Sandbox note: cassette replay needs no sandbox because no code and
no network runs. ``sandbox_available`` only reports whether code
execution is available through the bound local runner
(:mod:`puspa.localexec`), for solvers that choose to execute code;
a solver that needs execution and finds it unavailable must fail
closed, never run code outside the runner.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from pathlib import Path

from puspa.cassette.cassette import (
    CASSETTE_DIR_DEFAULT,
    TASK_REFERENCE,
    CassettePlayer,
    inject_price_wrong,
    load_cassette,
)
from puspa.evidence.renderer import render as render_markers

__all__ = [
    "EvalHarness",
    "EvalReport",
    "EvalResult",
    "EvalTask",
    "cassette_solver",
    "derive_expected_state",
    "load_reference_tasks",
    "run_bfcl_eval",
    "run_heldout_eval",
    "run_price_injection_eval",
    "run_reference_eval",
    "sandbox_available",
    "score_state",
]

#: Harness version, pinned on every report so runs are comparable.
HARNESS_VERSION = "1.0.0"


@dataclass
class EvalTask:
    """One versioned evaluation task (inspect_ai task shape)."""

    task_id: str
    name: str
    prompt: str
    cassette_name: str
    expected_state: dict = field(default_factory=dict)
    policy: list[str] = field(default_factory=list)
    version: str = HARNESS_VERSION
    environment: str = ""
    cassette: dict | None = None


@dataclass
class EvalResult:
    """One scored task run."""

    task_id: str
    name: str
    passed: bool
    score: float
    final_state: dict = field(default_factory=dict)
    details: dict = field(default_factory=dict)
    environment: str = ""


@dataclass
class EvalReport:
    """A full harness run over a task set."""

    harness_version: str
    results: list[EvalResult] = field(default_factory=list)

    @property
    def total(self) -> int:
        return len(self.results)

    @property
    def passed(self) -> int:
        return sum(1 for result in self.results if result.passed)

    @property
    def score(self) -> float:
        return (self.passed / self.total) if self.total else 0.0

    def summary(self) -> str:
        return (
            f"eval harness v{self.harness_version}: "
            f"{self.passed}/{self.total} passed "
            f"({self.score:.0%})"
        )


def derive_expected_state(cassette: dict) -> dict:
    """Expected final state derived from a cassette, by code only.

    Deterministic per tool family: cheapest price across price reads,
    bill value, draft/event/trigger/file/summary counts. This is the
    tau-bench final-state check: the state the task must end in.
    """
    state: dict = {}
    prices = []
    for recording in cassette.get("recording", []):
        tool = recording.get("tool", "")
        output = recording.get("output") or {}
        if not isinstance(output, dict):
            continue
        if tool == "read_price" and "value" in output:
            prices.append(float(output["value"]))
        if tool == "read_bill" and "value" in output:
            state.setdefault("bill_value", float(output["value"]))
            state.setdefault("bill_provider", output.get("penyedia"))
        if tool == "gmail_create_draft":
            state["draft_created"] = 1
        if tool == "calendar_create_event":
            state["event_created"] = 1
        if tool == "create_trigger":
            state["trigger_created"] = 1
        if tool == "drive_search":
            state["files_found"] = output.get("amount", 1)
        if tool == "search_artikel":
            state["articles_found"] = output.get("amount", 0)
        if tool == "read_artikel":
            state["articles_summarized"] = output.get("value", 0)
        if tool == "read_memory":
            state["preference_text"] = output.get("text", "")
        if tool == "prepare_payment":
            state["payment_status"] = output.get("status", "")
            state["payment_value"] = output.get("value")
    if prices:
        state["cheapest_price"] = min(prices)
        state["prices_seen"] = len(prices)
    return state


def cassette_solver(task: EvalTask, player: CassettePlayer, *, user=None) -> dict:
    """Default solver: replay the cassette, build the final state.

    Plays every recording in order (the same order the reference run
    recorded them), collects tool outputs, and derives the final
    state with the same code as ``derive_expected_state``. ``user`` is
    the tau-bench simulated user: a callable returning the next user
    message for a turn; the default simulated user says nothing, so
    the solver never waits on a live user.
    """
    cassette = player.cassette
    state: dict = {"tools_used": []}
    for recording in cassette.get("recording", []):
        tool = recording.get("tool", "")
        output = player.play(tool)
        state["tools_used"].append(tool)
        state.setdefault("tool_outputs", {})[tool] = output
        if callable(user):
            state.setdefault("user_turns", []).append(user(tool, output))
    derived = derive_expected_state(cassette)
    state.update(derived)
    return state


def score_state(task: EvalTask, final_state: dict) -> tuple[bool, dict]:
    """Score a final state against the expected state (tau-bench).

    Every expected key must match exactly. Returns
    ``(passed, details)`` where details names each checked key and
    whether it matched, so a failure is diagnosable, not just a 0.
    """
    details: dict = {}
    passed = True
    for key, expected in task.expected_state.items():
        actual = final_state.get(key)
        matched = actual == expected
        details[key] = {"expected": expected, "actual": actual, "matched": matched}
        if not matched:
            passed = False
    details["score"] = 1.0 if passed else 0.0
    return passed, details


def load_reference_tasks(cassette_dir: Path | str | None = None) -> list[EvalTask]:
    """The 10 reference tasks, each pinned to its recorded cassette."""
    folder = Path(cassette_dir or CASSETTE_DIR_DEFAULT)
    tasks = []
    for name in TASK_REFERENCE:
        cassette = load_cassette(folder / f"{name}.json")
        tasks.append(
            EvalTask(
                task_id=f"eval-{name}",
                name=name,
                prompt=f"Reference task {name} (replay the recorded cassette).",
                cassette_name=name,
                expected_state=derive_expected_state(cassette),
                policy=[
                    "never execute an outbound action without approval",
                    "never present a number without an evidence record",
                ],
            )
        )
    return tasks


class EvalHarness:
    """Run tasks with a solver, score them with a scorer."""

    def __init__(
        self, *, solver=None, scorer=None, sink=None, cassette_dir: Path | str | None = None
    ) -> None:
        self.solver = solver or cassette_solver
        self.scorer = scorer or score_state
        self.cassette_dir = Path(cassette_dir or CASSETTE_DIR_DEFAULT)
        self._events: list[dict] = []
        self.sink = sink or (
            lambda event_type, content: self._events.append(
                {"type": event_type, "content": content}
            )
        )

    @property
    def events(self) -> list[dict]:
        return list(self._events)

    def run_task(self, task: EvalTask) -> EvalResult:
        """Run one task: replay, score the final state, record it.

        A task with an inline cassette (``task.cassette``) replays it
        directly and never touches the cassette directory; that is
        how the AgentBench held-out tasks run. The score comes from
        the scorer's details when it reports one (the held-out scorer
        reports partial credit), otherwise 1.0 on pass and 0.0 on fail.
        """
        if task.cassette is not None:
            cassette = task.cassette
        else:
            cassette = load_cassette(self.cassette_dir / f"{task.cassette_name}.json")
        player = CassettePlayer(cassette)
        final_state = self.solver(task, player)
        passed, details = self.scorer(task, final_state)
        result = EvalResult(
            task_id=task.task_id,
            name=task.name,
            passed=passed,
            score=float(details.get("score", 1.0 if passed else 0.0)),
            final_state=final_state,
            details=details,
            environment=task.environment,
        )
        with contextlib.suppress(Exception):
            self.sink(
                "verification_completed",
                {
                    "task_id": task.task_id,
                    "passed": passed,
                    "harness_version": HARNESS_VERSION,
                },
            )
        return result

    def run(self, tasks: list[EvalTask]) -> EvalReport:
        """Run a task set and return the scored report."""
        return EvalReport(
            harness_version=HARNESS_VERSION,
            results=[self.run_task(task) for task in tasks],
        )


def run_reference_eval(cassette_dir: Path | str | None = None, **kwargs) -> EvalReport:
    """Run the 10 reference tasks with the default solver and scorer."""
    harness = EvalHarness(cassette_dir=cassette_dir, **kwargs)
    return harness.run(load_reference_tasks(cassette_dir))


def run_heldout_eval(cassette_dir: Path | str | None = None, **kwargs) -> EvalReport:
    """Run the AgentBench held-out tasks and return their own report.

    The held-out set is disjoint from the 10 dev cassettes (see
    ``puspa/evidence/agentbench_tasks.py``) and is scored separately:
    this report's score is the held-out score only, never mixed with
    the dev score from ``run_reference_eval``. The import is lazy
    because ``agentbench_tasks`` imports this module.
    """
    from puspa.evidence.agentbench_tasks import (
        heldout_solver,
        load_heldout_tasks,
        score_agentbench,
    )

    harness = EvalHarness(
        cassette_dir=cassette_dir, solver=heldout_solver, scorer=score_agentbench, **kwargs
    )
    return harness.run(load_heldout_tasks())


def run_bfcl_eval(agent=None, **kwargs) -> EvalReport:
    """Run the BFCL-style held-out tasks and return their own report.

    The BFCL set is disjoint from the 10 dev cassettes and from the
    AgentBench held-out set (see ``puspa/evidence/bfcl_tasks.py``)
    and is scored separately: this report's score is the BFCL
    score only, never mixed with the dev score from
    ``run_reference_eval`` or the AgentBench score from
    ``run_heldout_eval``. ``agent`` is a callable
    ``(bfcl_task, turn_index, history) -> list of calls``; the
    default is the reference agent, which emits the reference
    calls. The import is lazy because ``bfcl_tasks`` imports this
    module.
    """
    from puspa.evidence.bfcl_tasks import (
        load_bfcl_tasks,
        make_bfcl_solver,
        score_bfcl,
    )

    harness = EvalHarness(solver=make_bfcl_solver(agent), scorer=score_bfcl, **kwargs)
    return harness.run(load_bfcl_tasks())


def run_price_injection_eval(
    cassette_dir: Path | str | None = None,
    *,
    cassette_name: str = "compare_price_laptop",
    tool: str = "read_price",
    wrong_value: float = 1000,
) -> EvalResult:
    """Gate 11 price-injection test, end to end through the harness.

    The cassette price is tampered with (``inject_price_wrong``), the
    solver replays the tampered cassette, and the existing verifier
    must refuse to verify the tampered value against a reread of the
    true value. The test PASSES when the verifier does NOT say
    Verified: catching the injection is the expected outcome.
    """
    from puspa.evidence.evidence import record_evidence
    from puspa.evidence.verifier import verify

    folder = Path(cassette_dir or CASSETTE_DIR_DEFAULT)
    cassette = load_cassette(folder / f"{cassette_name}.json")
    original = derive_expected_state(cassette)
    true_value = original.get("cheapest_price")
    tampered = inject_price_wrong(cassette, tool=tool, value_new=wrong_value)
    player = CassettePlayer(tampered)
    task = EvalTask(
        task_id=f"eval-injection-{cassette_name}",
        name=f"price_injection_{cassette_name}",
        prompt="Price injection: a tampered cassette price must not verify.",
        cassette_name=cassette_name,
        expected_state={},
    )
    final_state = cassette_solver(task, player)
    tampered_value = final_state.get("cheapest_price")
    evidence = record_evidence(
        task_id=task.task_id,
        claim="Laptop X",
        value=tampered_value,
        unit="Rp",
        source_url="https://tokopedia.com/produk/laptop-x",
        text_excerpt="Laptop X",
        reader_id="eval-harness",
    )
    verify(evidence, reread=lambda url: {"value": true_value, "name": "Laptop X"})
    caught = evidence.verdict != "Verified"
    return EvalResult(
        task_id=task.task_id,
        name=task.name,
        passed=caught,
        score=1.0 if caught else 0.0,
        final_state=final_state,
        details={
            "tampered_value": tampered_value,
            "true_value": true_value,
            "verdict": evidence.verdict,
            "caught": caught,
        },
    )


def sandbox_available() -> bool:
    """True when code execution is available through the local runner.

    The local runner is a subprocess on the user's own machine, so
    it is available as a mechanism; the user may still deny any
    single run at the permission prompt, which the executor then
    reports as unavailable instead of running the code. Reports
    only; the harness itself never executes code, so a False here
    never falls back to execution outside the runner.
    """
    try:
        from puspa.localexec import LocalExecutor

        LocalExecutor(Path.cwd())
        return True
    except Exception:
        return False


def render_report(report: EvalReport, evidence_by_id: dict | None = None) -> str:
    """Render a report summary, filling evidence markers from the book.

    Counts are computed by code in ``EvalReport.summary``; markers in
    a caller-supplied template (``{label:bukti_id}``) are filled by
    the existing renderer, so no number is typed by a model.
    """
    text, _missing = render_markers(report.summary(), evidence_by_id or {})
    return text
