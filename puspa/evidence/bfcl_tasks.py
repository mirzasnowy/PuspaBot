"""BFCL-style held-out task set for the eval harness.

Pattern studied (rewritten, not copied):
- ShishirPatil/gorilla, BFCL (Apache 2.0): the Berkeley Function
  Calling Leaderboard judges an agent by the function calls it
  makes. A task gives the agent a prompt, a list of available
  functions with JSON schemas, and a multi-turn conversation;
  each turn has reference calls, and accuracy is the share of
  tasks whose calls match the reference exactly (name, arguments
  with value and type, order, no missing and no extra calls).

Puspa's set, in Puspa's own code and data:

- ``FUNCTION_CATALOG``: the Puspa tools an agent may call in
  these tasks, each with a JSON schema. The names are the real
  tool names from this repo: ``read_price``, ``read_bill`` and
  ``prepare_payment`` from the dev cassettes, ``watch_prices``
  from ``puspa/workflow/runner.py``, ``gmail_search``,
  ``gmail_read``, ``gmail_create_draft``,
  ``calendar_create_event``, ``create_trigger`` and
  ``drive_search`` from the dev cassettes, ``read_memory`` from
  the memory cassette. No BFCL task data is copied: every prompt,
  entity, value and reference call here was written for Puspa.
- ``BFCL_TASKS``: 8 held-out tasks, disjoint from the 10 dev
  cassettes and from the AgentBench set by task id, prompt and
  values. They cover single-turn single-call, single-turn
  multi-call with order, multi-turn with a user correction,
  optional arguments omitted, an empty-string argument, a
  digit-string that must stay a string, and a float threshold
  that must stay a float.
- ``reference_agent``: the agent that emits the reference calls.
  It exists so the harness path is exercised end to end and the
  ceiling is measured, not assumed.
- ``make_bfcl_solver`` / ``bfcl_solver``: run a task with an
  agent (default the reference agent) through the cassette
  player, which supplies the simulated tool outputs per turn.
  The solver records the agent's calls; it never scores them.
- ``score_bfcl``: the harness scorer. It runs the AST checker
  from ``bfcl_checker.py`` over the recorded calls and reports
  per-turn results, turn accuracy and call accuracy. The task
  score is turn accuracy (partial credit, the same shape as the
  AgentBench scorer); the task passes only when every turn
  matches exactly, the BFCL rule.
- ``run_bfcl_eval`` (in ``eval_harness.py``): runs only this set
  and returns its own ``EvalReport``. The BFCL score is never
  mixed into the dev score or the AgentBench held-out score.

Simulation boundary, stated plainly: no live model is called
here. The reference agent replays the reference calls, so a
1.0 on this set proves the checker and the harness wiring work
and that the reference calls are self-consistent; it does not
prove a live model would score 1.0. What this set measures
today is the checker: faulty agents (wrong name, wrong value,
wrong type, missing or extra argument, wrong order, missing or
extra call) must score below 1.0, and the tests lock those
numbers. Scoring a live model through this set is follow-up
work for a model-backed solver; it needs no outside service,
only a model client wired as the agent.
"""

from __future__ import annotations

import copy
from dataclasses import dataclass, field

from puspa.cassette.cassette import CassetteError, CassettePlayer
from puspa.evidence.bfcl_checker import (
    ToolCall,
    check_task,
    parse_tool_call,
)
from puspa.evidence.eval_harness import EvalReport, EvalTask

__all__ = [
    "BFCL_TASKS",
    "BFCL_TASK_IDS",
    "FUNCTION_CATALOG",
    "BFCLTask",
    "BFCLTurn",
    "FunctionSpec",
    "bfcl_solver",
    "load_bfcl_tasks",
    "make_bfcl_solver",
    "reference_agent",
    "score_bfcl",
    "summarize_bfcl",
]

#: The environment label for every task in this set.
ENVIRONMENT = "bfcl"


@dataclass(frozen=True)
class FunctionSpec:
    """One available function: name, description, JSON schema."""

    name: str
    description: str
    parameters: dict
    required: tuple[str, ...] = ()

    def to_schema(self) -> dict:
        """The function as a model-facing tool definition."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": copy.deepcopy(self.parameters),
            "required": list(self.required),
        }


#: The Puspa tools a BFCL-style agent may call, with JSON schemas.
#: Names are the real tool names from this repo (see the module
#: docstring). ``amount`` is an integer number of rupiah;
#: ``price_threshold`` is a number and may be a float;
#: ``customer_number`` is a string even when it looks numeric.
FUNCTION_CATALOG: dict[str, FunctionSpec] = {
    "read_price": FunctionSpec(
        name="read_price",
        description="Read the current price of a product from a marketplace.",
        parameters={
            "type": "object",
            "properties": {
                "product": {"type": "string"},
                "max_price": {"type": "integer"},
            },
        },
        required=("product",),
    ),
    "read_bill": FunctionSpec(
        name="read_bill",
        description="Read the current bill for a provider and customer number.",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "customer_number": {"type": "string"},
            },
        },
        required=("provider", "customer_number"),
    ),
    "prepare_payment": FunctionSpec(
        name="prepare_payment",
        description="Prepare a payment draft for a provider bill. Does not send money.",
        parameters={
            "type": "object",
            "properties": {
                "provider": {"type": "string"},
                "amount": {"type": "integer"},
                "purpose": {"type": "string"},
            },
        },
        required=("provider", "amount"),
    ),
    "watch_prices": FunctionSpec(
        name="watch_prices",
        description="Watch a product and notify when its price drops below a threshold.",
        parameters={
            "type": "object",
            "properties": {
                "product_id": {"type": "string"},
                "price_threshold": {"type": "number"},
            },
        },
        required=("product_id", "price_threshold"),
    ),
    "create_trigger": FunctionSpec(
        name="create_trigger",
        description="Create a recurring reminder trigger.",
        parameters={
            "type": "object",
            "properties": {
                "name": {"type": "string"},
                "condition": {"type": "string"},
                "note": {"type": "string"},
            },
        },
        required=("name", "condition"),
    ),
    "gmail_search": FunctionSpec(
        name="gmail_search",
        description="Search the mailbox for messages matching a query.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
                "max_results": {"type": "integer"},
            },
        },
        required=("query",),
    ),
    "gmail_read": FunctionSpec(
        name="gmail_read",
        description="Read one message by its id.",
        parameters={
            "type": "object",
            "properties": {
                "message_id": {"type": "string"},
            },
        },
        required=("message_id",),
    ),
    "gmail_create_draft": FunctionSpec(
        name="gmail_create_draft",
        description="Create an email draft. Does not send it.",
        parameters={
            "type": "object",
            "properties": {
                "recipient": {"type": "string"},
                "subject": {"type": "string"},
                "content": {"type": "string"},
                "cc": {"type": "string"},
            },
        },
        required=("recipient", "subject", "content"),
    ),
    "calendar_create_event": FunctionSpec(
        name="calendar_create_event",
        description="Create a calendar event.",
        parameters={
            "type": "object",
            "properties": {
                "title": {"type": "string"},
                "start_time": {"type": "string"},
                "attendees": {"type": "array", "items": {"type": "string"}},
            },
        },
        required=("title", "start_time"),
    ),
    "drive_search": FunctionSpec(
        name="drive_search",
        description="Search Drive files by query.",
        parameters={
            "type": "object",
            "properties": {
                "query": {"type": "string"},
            },
        },
        required=("query",),
    ),
    "read_memory": FunctionSpec(
        name="read_memory",
        description="Read a stored preference or fact by topic.",
        parameters={
            "type": "object",
            "properties": {
                "topic": {"type": "string"},
            },
        },
        required=("topic",),
    ),
}


@dataclass
class BFCLTurn:
    """One turn: a user message, the reference calls, their outputs.

    ``tool_outputs`` are the simulated outputs the cassette
    returns for the reference calls, in order. They are what the
    agent sees before the next turn.
    """

    user_message: str
    expected_calls: list[ToolCall] = field(default_factory=list)
    tool_outputs: list[dict] = field(default_factory=list)


@dataclass
class BFCLTask:
    """One BFCL-style task: prompt, functions, turns."""

    task_id: str
    name: str
    prompt: str
    functions: list[FunctionSpec] = field(default_factory=list)
    turns: list[BFCLTurn] = field(default_factory=list)
    environment: str = ENVIRONMENT


def _call(tool_name: str, **arguments) -> ToolCall:
    """Build a reference call without repeating the class name."""
    return ToolCall(name=tool_name, arguments=dict(arguments))


def _bfcl_task(task_id: str, prompt: str, functions: list[str], turns: list[BFCLTurn]) -> EvalTask:
    """Build one held-out task as an ``EvalTask`` with an inline cassette.

    The inline cassette records one entry per reference call, in
    turn order, so the cassette player can supply the simulated
    tool outputs and the solver never touches a cassette file.
    The ``BFCLTask`` itself rides in the cassette under
    ``bfcl_task`` so the solver and scorer can find it.
    """
    bfcl = BFCLTask(
        task_id=task_id,
        name=task_id,
        prompt=prompt,
        functions=[FUNCTION_CATALOG[name] for name in functions],
        turns=turns,
    )
    recording = []
    for turn in turns:
        for call, output in zip(turn.expected_calls, turn.tool_outputs):
            recording.append({"tool": call.name, "output": output})
    cassette = {"task_id": task_id, "name": task_id, "recording": recording, "bfcl_task": bfcl}
    return EvalTask(
        task_id=task_id,
        name=task_id,
        prompt=prompt,
        cassette_name=task_id,
        expected_state={},
        policy=[
            "never execute an outbound action without approval",
            "never present a number without an evidence record",
        ],
        environment=ENVIRONMENT,
        cassette=cassette,
    )


def _build_tasks() -> list[EvalTask]:
    """The 8 held-out BFCL-style tasks, all written for Puspa."""
    return [
        # -- Single turn, single call --------------------------------
        _bfcl_task(
            task_id="bfcl-price-single",
            prompt=(
                "What is the current price of Laptop X? Only report "
                "it if it costs at most Rp15000000."
            ),
            functions=["read_price"],
            turns=[
                BFCLTurn(
                    user_message=(
                        "What is the current price of Laptop X? "
                        "Only report it if it costs at most "
                        "Rp15000000."
                    ),
                    expected_calls=[_call("read_price", product="Laptop X", max_price=15000000)],
                    tool_outputs=[
                        {"product": "Laptop X", "value": 14250000, "source": "tokopedia.com"}
                    ],
                )
            ],
        ),
        # -- Single turn, two calls, order matters -------------------
        _bfcl_task(
            task_id="bfcl-bills-two-calls",
            prompt=(
                "Check my PLN electricity bill and my PDAM water "
                "bill for this month, in that order."
            ),
            functions=["read_bill"],
            turns=[
                BFCLTurn(
                    user_message=(
                        "Check my PLN electricity bill and my "
                        "PDAM water bill for this month, in that "
                        "order."
                    ),
                    expected_calls=[
                        _call("read_bill", provider="PLN", customer_number="512345678901"),
                        _call("read_bill", provider="PDAM", customer_number="0099887766"),
                    ],
                    tool_outputs=[
                        {"provider": "PLN", "value": 287500, "due_date": "2026-11-20"},
                        {"provider": "PDAM", "value": 142000, "due_date": "2026-11-25"},
                    ],
                )
            ],
        ),
        # -- Three turns: watch, pay, remind (empty-string note) -----
        _bfcl_task(
            task_id="bfcl-watch-pay-remind",
            prompt=("Watch the price of laptop-x-14 and tell me when it drops below Rp12000000."),
            functions=["watch_prices", "prepare_payment", "create_trigger"],
            turns=[
                BFCLTurn(
                    user_message=(
                        "Watch the price of laptop-x-14 and tell me when it drops below Rp12000000."
                    ),
                    expected_calls=[
                        _call("watch_prices", product_id="laptop-x-14", price_threshold=12000000)
                    ],
                    tool_outputs=[
                        {"product_id": "laptop-x-14", "watching": True, "current_price": 13499000}
                    ],
                ),
                BFCLTurn(
                    user_message=(
                        "The price dropped. Now pay my PLN bill "
                        "of Rp250000 for the electricity token."
                    ),
                    expected_calls=[
                        _call(
                            "prepare_payment",
                            provider="PLN",
                            amount=250000,
                            purpose="electricity token",
                        )
                    ],
                    tool_outputs=[{"payment_draft_id": "pay-2201", "status": "draft"}],
                ),
                BFCLTurn(
                    user_message="Also remind me about bills every week.",
                    expected_calls=[
                        _call(
                            "create_trigger",
                            name="weekly bill reminder",
                            condition="every Monday 08:00",
                            note="",
                        )
                    ],
                    tool_outputs=[{"trigger_id": "trg-77", "status": "active"}],
                ),
            ],
        ),
        # -- Tricky numbers: odd int amount, float threshold ---------
        _bfcl_task(
            task_id="bfcl-tricky-numbers",
            prompt=(
                "Pay my PLN bill of Rp499999 and watch keyboard-k68 "
                "until it costs less than Rp899999.50."
            ),
            functions=["prepare_payment", "watch_prices"],
            turns=[
                BFCLTurn(
                    user_message=(
                        "Pay my PLN bill of Rp499999 and watch "
                        "keyboard-k68 until it costs less than "
                        "Rp899999.50."
                    ),
                    expected_calls=[
                        _call("prepare_payment", provider="PLN", amount=499999),
                        _call("watch_prices", product_id="keyboard-k68", price_threshold=899999.5),
                    ],
                    tool_outputs=[
                        {"payment_draft_id": "pay-2202", "status": "draft"},
                        {"product_id": "keyboard-k68", "watching": True, "current_price": 949000},
                    ],
                )
            ],
        ),
        # -- Digit string must stay a string -------------------------
        _bfcl_task(
            task_id="bfcl-string-not-number",
            prompt=(
                "My PLN customer number is 512345678901. Check that "
                "bill, then search my email for the November PLN "
                "invoice."
            ),
            functions=["read_bill", "gmail_search"],
            turns=[
                BFCLTurn(
                    user_message=(
                        "My PLN customer number is 512345678901. "
                        "Check that bill, then search my email for "
                        "the November PLN invoice."
                    ),
                    expected_calls=[
                        _call("read_bill", provider="PLN", customer_number="512345678901"),
                        _call("gmail_search", query="PLN invoice November", max_results=5),
                    ],
                    tool_outputs=[
                        {"provider": "PLN", "value": 312000, "due_date": "2026-11-20"},
                        {"emails": ["inv-pln-nov"], "count": 1},
                    ],
                )
            ],
        ),
        # -- Optional arguments omitted entirely ---------------------
        _bfcl_task(
            task_id="bfcl-optional-omitted",
            prompt=(
                "Find the price of a wireless mouse with no price "
                "limit, then draft an email to ana@example.com "
                "about it."
            ),
            functions=["read_price", "gmail_create_draft"],
            turns=[
                BFCLTurn(
                    user_message=(
                        "Find the price of a wireless mouse with no "
                        "price limit, then draft an email to "
                        "ana@example.com about it."
                    ),
                    expected_calls=[
                        _call("read_price", product="Wireless Mouse"),
                        _call(
                            "gmail_create_draft",
                            recipient="ana@example.com",
                            subject="Wireless mouse price",
                            content="The wireless mouse costs Rp189000.",
                        ),
                    ],
                    tool_outputs=[
                        {"product": "Wireless Mouse", "value": 189000, "source": "shopee.co.id"},
                        {"draft_id": "draft-331", "status": "created"},
                    ],
                )
            ],
        ),
        # -- Multi-turn with a user correction -----------------------
        _bfcl_task(
            task_id="bfcl-user-correction",
            prompt=(
                "What is the price of Laptop Y? I need the price before deciding which one to buy."
            ),
            functions=["read_price"],
            turns=[
                BFCLTurn(
                    user_message=(
                        "What is the price of Laptop Y? I need "
                        "the price before deciding which one to "
                        "buy."
                    ),
                    expected_calls=[_call("read_price", product="Laptop Y", max_price=15000000)],
                    tool_outputs=[
                        {"product": "Laptop Y", "value": 15800000, "source": "tokopedia.com"}
                    ],
                ),
                BFCLTurn(
                    user_message="Not that one, I meant Laptop X.",
                    expected_calls=[_call("read_price", product="Laptop X", max_price=15000000)],
                    tool_outputs=[
                        {"product": "Laptop X", "value": 14250000, "source": "tokopedia.com"}
                    ],
                ),
            ],
        ),
        # -- Search, then schedule with a list argument --------------
        _bfcl_task(
            task_id="bfcl-draft-and-event",
            prompt=("Search my email for meeting invitations, at most 3 results."),
            functions=["gmail_search", "calendar_create_event"],
            turns=[
                BFCLTurn(
                    user_message=("Search my email for meeting invitations, at most 3 results."),
                    expected_calls=[
                        _call("gmail_search", query="meeting invitation", max_results=3)
                    ],
                    tool_outputs=[{"emails": ["msg-101", "msg-102", "msg-103"], "count": 3}],
                ),
                BFCLTurn(
                    user_message=(
                        "Schedule the team meeting for 20 "
                        "November 2026 at 10:00 WIB with Ana "
                        "and Budi."
                    ),
                    expected_calls=[
                        _call(
                            "calendar_create_event",
                            title="Team meeting",
                            start_time="2026-11-20T10:00:00+07:00",
                            attendees=["ana@example.com", "budi@example.com"],
                        )
                    ],
                    tool_outputs=[{"event_id": "evt-55", "status": "created"}],
                ),
            ],
        ),
    ]


#: The held-out task set, built once at import.
BFCL_TASKS: list[EvalTask] = _build_tasks()

#: Held-out task ids, for disjointness checks and reporting.
BFCL_TASK_IDS: tuple[str, ...] = tuple(task.task_id for task in BFCL_TASKS)


def load_bfcl_tasks() -> list[EvalTask]:
    """Return fresh copies of the held-out tasks (recordings unshared)."""
    return copy.deepcopy(BFCL_TASKS)


def reference_agent(task: BFCLTask, turn_index: int, history: list[dict]) -> list[ToolCall]:
    """The agent that emits the reference calls for a turn.

    This is the ceiling agent: it proves the harness path works
    end to end and that the reference calls are self-consistent.
    A live model agent would take the same arguments (the task,
    the turn index, and the history of user messages, calls and
    tool outputs so far) and return its own calls.
    """
    return list(task.turns[turn_index].expected_calls)


def make_bfcl_solver(agent=None):
    """Build a harness solver that runs a BFCL task with an agent.

    ``agent`` is a callable ``(bfcl_task, turn_index, history) ->
    list of calls``; the default is ``reference_agent``. Each
    turn, the agent's calls are played through the cassette
    player, which returns the simulated tool outputs for that
    tool (a call to a tool with no recording left yields None
    instead of crashing the run). The solver records the agent's
    calls per turn and never scores them; scoring is the
    scorer's job.
    """
    agent = agent or reference_agent

    def bfcl_solver(task: EvalTask, player: CassettePlayer, *, user=None) -> dict:
        if task.cassette is None:
            raise ValueError(f"task {task.task_id} has no cassette")
        bfcl: BFCLTask = task.cassette["bfcl_task"]
        history: list[dict] = []
        agent_turns: list[list[ToolCall]] = []
        tool_outputs: list[list] = []
        for index, turn in enumerate(bfcl.turns):
            raw_calls = agent(bfcl, index, history)
            calls = [
                parse_tool_call(raw) if not isinstance(raw, ToolCall) else raw for raw in raw_calls
            ]
            agent_turns.append(calls)
            outputs = []
            for call in calls:
                try:
                    outputs.append(player.play(call.name))
                except CassetteError:
                    outputs.append(None)
            tool_outputs.append(outputs)
            history.append({"user": turn.user_message, "calls": calls, "outputs": outputs})
        return {
            "tools_used": [call.name for turn in agent_turns for call in turn],
            "agent_calls": agent_turns,
            "tool_outputs": tool_outputs,
            "turns": len(bfcl.turns),
        }

    return bfcl_solver


#: The default solver: the reference agent through the harness.
bfcl_solver = make_bfcl_solver()


def score_bfcl(task: EvalTask, final_state: dict) -> tuple[bool, dict]:
    """Score a BFCL run: AST-check the recorded calls per turn.

    The task score is turn accuracy (partial credit, the same
    shape as the AgentBench scorer); the task passes only when
    every turn matches exactly, the BFCL rule. Details carry the
    per-turn breakdown and every failure, so a failure is
    diagnosable, not just a 0.
    """
    if task.cassette is None:
        raise ValueError(f"task {task.task_id} has no cassette")
    bfcl: BFCLTask = task.cassette["bfcl_task"]
    specs = {spec.name: spec.parameters for spec in bfcl.functions}
    agent_turns = final_state.get("agent_calls", [])
    expected_turns = [turn.expected_calls for turn in bfcl.turns]
    check = check_task(task.task_id, agent_turns, expected_turns, specs=specs)
    details = {
        "score": check.turn_accuracy,
        "passed": check.passed,
        "turn_accuracy": check.turn_accuracy,
        "call_accuracy": check.call_accuracy,
        "total_turns": check.total_turns,
        "total_calls": check.total_calls,
        "turns": [
            {
                "turn": turn.turn,
                "passed": turn.passed,
                "calls_checked": turn.calls_checked,
                "calls_correct": turn.calls_correct,
                "failures": [
                    {
                        "kind": failure.kind,
                        "path": failure.path,
                        "expected": failure.expected,
                        "actual": failure.actual,
                        "message": failure.message,
                    }
                    for failure in turn.failures
                ],
            }
            for turn in check.turns
        ],
        "failures": [
            {
                "kind": failure.kind,
                "turn": failure.turn,
                "call_index": failure.call_index,
                "path": failure.path,
                "expected": failure.expected,
                "actual": failure.actual,
                "message": failure.message,
            }
            for failure in check.failures
        ],
    }
    return check.passed, details


def summarize_bfcl(report: EvalReport) -> dict:
    """Aggregate a BFCL report into one accuracy summary.

    Returns the task accuracy (the report score: share of tasks
    fully correct), the mean turn accuracy and the mean call
    accuracy across tasks, plus per-task scores. The BFCL score
    is reported on its own, never mixed into the dev score or
    the AgentBench held-out score.
    """
    per_task = {}
    turn_total = 0.0
    call_total = 0.0
    for result in report.results:
        per_task[result.task_id] = {
            "passed": result.passed,
            "score": result.score,
            "turn_accuracy": result.details.get("turn_accuracy", 0.0),
            "call_accuracy": result.details.get("call_accuracy", 0.0),
        }
        turn_total += result.details.get("turn_accuracy", 0.0)
        call_total += result.details.get("call_accuracy", 0.0)
    total = report.total
    return {
        "bfcl": {
            "total": total,
            "passed": report.passed,
            "score": report.score,
        },
        "mean_turn_accuracy": (turn_total / total) if total else 0.0,
        "mean_call_accuracy": (call_total / total) if total else 0.0,
        "per_task": per_task,
    }
