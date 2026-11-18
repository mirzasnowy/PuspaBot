"""AST-based tool-call checker for BFCL-style evaluation tasks.

Pattern studied (rewritten, not copied):
- ShishirPatil/gorilla, BFCL (Apache 2.0): the Berkeley Function
  Calling Leaderboard judges an agent by the function calls it
  makes, not by its words. Each task ships reference calls per
  turn, and the checker compares the agent's calls against them:
  right function name, right arguments (value and type), right
  order, no missing calls, no extra calls. Accuracy is the share
  of tasks whose calls match exactly.

Puspa's checker, in Puspa's own code:

- ``ToolCall``: one function call (name plus arguments). It can be
  built from a dict, from an OpenAI-style function payload, or
  parsed from Python call source with ``ast``. The source form is
  parsed into a real syntax tree and the call is read off the
  tree, so the comparison is structural, never a string match.
- ``compare_call``: one actual call against one reference call.
  Name, argument values, argument types, missing arguments and
  extra arguments are all checked separately, each with its own
  failure kind, so a failure says exactly what went wrong.
- ``check_turn``: one turn of calls against the reference calls
  for that turn. Order is checked: the same calls in a different
  order fail with ``wrong_order`` instead of silently passing.
  Missing and extra calls are reported per position.
- ``check_task``: a whole multi-turn task. It reports per-turn
  results plus turn accuracy and call accuracy, and passes only
  when every turn matches exactly, the BFCL rule.

Type strictness is deliberate. A boolean is never an integer
(``True`` is not ``1``), an integer is never a float (``500000``
is not ``500000.0``), a string of digits is never a number
(``"512345678901"`` is not ``512345678901``), and an empty string
is a value, not a missing argument. Puspa tool schemas
distinguish these types, so the checker does too.
"""

from __future__ import annotations

import ast
import copy
import json
from dataclasses import dataclass, field

__all__ = [
    "CallFailure",
    "TaskCheck",
    "ToolCall",
    "ToolCallParseError",
    "TurnCheck",
    "check_task",
    "check_turn",
    "compare_call",
    "parse_tool_call",
]

#: Every failure kind the checker can report.
FAILURE_KINDS = (
    "wrong_name",
    "wrong_value",
    "wrong_type",
    "missing_argument",
    "extra_argument",
    "missing_call",
    "extra_call",
    "wrong_order",
    "unparsable_call",
    "unexpected_positional",
)


class ToolCallParseError(ValueError):
    """A raw agent call could not be read as a tool call.

    ``kind`` is one of ``unparsable_call`` (not a call at all, or a
    value that is not a literal) or ``unexpected_positional``
    (positional arguments with no function spec to map them to
    parameter names).
    """

    def __init__(self, message: str, *, kind: str = "unparsable_call") -> None:
        super().__init__(message)
        self.kind = kind


@dataclass(frozen=True)
class ToolCall:
    """One function call: a name plus keyword arguments."""

    name: str
    arguments: dict = field(default_factory=dict)

    def to_source(self) -> str:
        """Render the call as Python call source, for logs and tests."""
        parts = []
        for key, value in self.arguments.items():
            parts.append(f"{key}={value!r}")
        return f"{self.name}({', '.join(parts)})"

    def canonical(self) -> str:
        """A canonical string for multiset comparison (order check)."""
        return json.dumps(
            {"name": self.name, "arguments": self.arguments},
            sort_keys=True,
            ensure_ascii=False,
            default=str,
        )


def _literal(node: ast.AST):
    """Evaluate one AST value node as a literal, or raise."""
    try:
        return ast.literal_eval(node)
    except (ValueError, SyntaxError, MemoryError, RecursionError) as exc:
        raise ToolCallParseError(f"call argument is not a literal value: {ast.dump(node)}") from exc


def parse_tool_call(raw, spec: dict | None = None) -> ToolCall:
    """Read one raw agent call as a ``ToolCall``.

    Accepted forms:
    - a ``ToolCall`` already (returned unchanged);
    - a mapping with ``name`` and ``arguments`` (arguments may be a
      dict or a JSON string), or an OpenAI-style mapping with a
      ``function`` key holding such a mapping;
    - a string of Python call source, for example
      ``read_price(product="Laptop X", max_price=15000000)``,
      parsed with ``ast``. Keyword values must be literals.
      Positional arguments are mapped to parameter names in
      schema order when ``spec`` (a JSON-schema style dict with
      ``properties``) is given; without a spec they are an error.

    Raises ``ToolCallParseError`` when the input is not a call.
    """
    if isinstance(raw, ToolCall):
        return raw
    if isinstance(raw, dict):
        payload = raw.get("function", raw) if isinstance(raw.get("function"), dict) else raw
        if not isinstance(payload, dict) or "name" not in payload:
            raise ToolCallParseError(f"mapping is not a tool call: {raw!r}")
        arguments = payload.get("arguments", {})
        if isinstance(arguments, str):
            try:
                arguments = json.loads(arguments)
            except json.JSONDecodeError as exc:
                raise ToolCallParseError(f"arguments are not valid JSON: {arguments!r}") from exc
        if not isinstance(arguments, dict):
            raise ToolCallParseError(f"arguments are not an object: {arguments!r}")
        return ToolCall(name=str(payload["name"]), arguments=copy.deepcopy(arguments))
    if isinstance(raw, str):
        try:
            tree = ast.parse(raw.strip(), mode="eval")
        except SyntaxError as exc:
            raise ToolCallParseError(f"not a parseable call: {raw!r}") from exc
        if not isinstance(tree.body, ast.Call):
            raise ToolCallParseError(f"not a call expression: {raw!r}")
        call = tree.body
        if not isinstance(call.func, ast.Name):
            raise ToolCallParseError(f"call target is not a plain function name: {raw!r}")
        call_arguments: dict = {}
        if call.args:
            if spec is None:
                raise ToolCallParseError(
                    "positional arguments need a function spec to map "
                    f"them to parameter names: {raw!r}",
                    kind="unexpected_positional",
                )
            param_names = list((spec.get("properties") or {}).keys())
            if len(call.args) > len(param_names):
                raise ToolCallParseError(
                    f"more positional arguments than parameters: {raw!r}",
                    kind="unexpected_positional",
                )
            for name, node in zip(param_names, call.args):
                call_arguments[name] = _literal(node)
        for keyword in call.keywords:
            if keyword.arg is None:
                raise ToolCallParseError(f"** unpacking is not supported: {raw!r}")
            call_arguments[keyword.arg] = _literal(keyword.value)
        return ToolCall(name=call.func.id, arguments=call_arguments)
    raise ToolCallParseError(f"unsupported call form: {type(raw).__name__}")


@dataclass
class CallFailure:
    """One exact way an actual call differs from the reference call."""

    kind: str
    turn: int
    call_index: int | None
    path: str
    expected: object = None
    actual: object = None
    message: str = ""


@dataclass
class TurnCheck:
    """The check result for one turn of calls."""

    turn: int
    passed: bool
    failures: list[CallFailure] = field(default_factory=list)
    calls_checked: int = 0
    calls_correct: int = 0


@dataclass
class TaskCheck:
    """The check result for one whole multi-turn task."""

    task_id: str
    passed: bool
    turns: list[TurnCheck] = field(default_factory=list)
    turn_accuracy: float = 0.0
    call_accuracy: float = 0.0
    failures: list[CallFailure] = field(default_factory=list)

    @property
    def total_turns(self) -> int:
        return len(self.turns)

    @property
    def total_calls(self) -> int:
        return sum(turn.calls_checked for turn in self.turns)


def _values_equal(
    actual, expected, path: str, turn: int, call_index: int | None, failures: list[CallFailure]
) -> None:
    """Compare two argument values, strictly, recording failures.

    Appends one failure per mismatch (never raises). Recursion
    descends into dicts and lists with a dotted path such as
    ``arguments.attendees[1]`` so a nested failure is locatable.
    """
    # Bool first: bool is a subclass of int in Python, so
    # isinstance(True, int) is True and must not let True pass as 1.
    if isinstance(expected, bool) or isinstance(actual, bool):
        if not (isinstance(expected, bool) and isinstance(actual, bool)):
            failures.append(
                CallFailure(
                    kind="wrong_type",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=(
                        f"{path}: expected a boolean {expected!r}, "
                        f"got {actual!r} of type "
                        f"{type(actual).__name__}"
                    ),
                )
            )
        elif actual != expected:
            failures.append(
                CallFailure(
                    kind="wrong_value",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=f"{path}: expected {expected!r}, got {actual!r}",
                )
            )
        return
    if isinstance(expected, (int, float)) or isinstance(actual, (int, float)):
        if type(actual) is not type(expected):
            failures.append(
                CallFailure(
                    kind="wrong_type",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=(
                        f"{path}: expected {type(expected).__name__} "
                        f"{expected!r}, got {type(actual).__name__} "
                        f"{actual!r}"
                    ),
                )
            )
        elif actual != expected:
            failures.append(
                CallFailure(
                    kind="wrong_value",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=f"{path}: expected {expected!r}, got {actual!r}",
                )
            )
        return
    if isinstance(expected, str) or isinstance(actual, str):
        if not (isinstance(expected, str) and isinstance(actual, str)):
            failures.append(
                CallFailure(
                    kind="wrong_type",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=(
                        f"{path}: expected a string {expected!r}, got "
                        f"{actual!r} of type {type(actual).__name__}"
                    ),
                )
            )
        elif actual != expected:
            failures.append(
                CallFailure(
                    kind="wrong_value",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=f"{path}: expected {expected!r}, got {actual!r}",
                )
            )
        return
    if expected is None or actual is None:
        if expected is not actual:
            failures.append(
                CallFailure(
                    kind="wrong_value",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=f"{path}: expected {expected!r}, got {actual!r}",
                )
            )
        return
    if isinstance(expected, dict) or isinstance(actual, dict):
        if not (isinstance(expected, dict) and isinstance(actual, dict)):
            failures.append(
                CallFailure(
                    kind="wrong_type",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=(f"{path}: expected a dict, got {type(actual).__name__}"),
                )
            )
            return
        for key in expected:
            if key not in actual:
                failures.append(
                    CallFailure(
                        kind="missing_argument",
                        turn=turn,
                        call_index=call_index,
                        path=f"{path}.{key}",
                        expected=expected[key],
                        actual=None,
                        message=f"{path}.{key}: missing, expected {expected[key]!r}",
                    )
                )
        for key in actual:
            if key not in expected:
                failures.append(
                    CallFailure(
                        kind="extra_argument",
                        turn=turn,
                        call_index=call_index,
                        path=f"{path}.{key}",
                        expected=None,
                        actual=actual[key],
                        message=f"{path}.{key}: unexpected extra argument {actual[key]!r}",
                    )
                )
        for key in expected:
            if key in actual:
                _values_equal(
                    actual[key], expected[key], f"{path}.{key}", turn, call_index, failures
                )
        return
    if isinstance(expected, (list, tuple)) or isinstance(actual, (list, tuple)):
        if not (isinstance(expected, (list, tuple)) and isinstance(actual, (list, tuple))):
            failures.append(
                CallFailure(
                    kind="wrong_type",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=(f"{path}: expected a list, got {type(actual).__name__}"),
                )
            )
            return
        if len(actual) != len(expected):
            failures.append(
                CallFailure(
                    kind="wrong_value",
                    turn=turn,
                    call_index=call_index,
                    path=path,
                    expected=expected,
                    actual=actual,
                    message=(f"{path}: expected {len(expected)} items, got {len(actual)}"),
                )
            )
            return
        for index, (item_actual, item_expected) in enumerate(zip(actual, expected)):
            _values_equal(
                item_actual, item_expected, f"{path}[{index}]", turn, call_index, failures
            )
        return
    if actual != expected:
        failures.append(
            CallFailure(
                kind="wrong_value",
                turn=turn,
                call_index=call_index,
                path=path,
                expected=expected,
                actual=actual,
                message=f"{path}: expected {expected!r}, got {actual!r}",
            )
        )


def compare_call(
    actual: ToolCall, expected: ToolCall, *, turn: int, call_index: int | None = None
) -> list[CallFailure]:
    """Compare one actual call against one reference call.

    Returns the list of failures (empty when the calls match
    exactly). A wrong name short-circuits: arguments of a call to
    the wrong function are not compared. Argument comparison is
    the strict value comparison in ``_values_equal``.
    """
    failures: list[CallFailure] = []
    if actual.name != expected.name:
        failures.append(
            CallFailure(
                kind="wrong_name",
                turn=turn,
                call_index=call_index,
                path="name",
                expected=expected.name,
                actual=actual.name,
                message=(
                    f"call {call_index}: expected function {expected.name!r}, got {actual.name!r}"
                ),
            )
        )
        return failures
    _values_equal(actual.arguments, expected.arguments, "arguments", turn, call_index, failures)
    return failures


def check_turn(
    actual_calls, expected_calls: list[ToolCall], *, turn: int, specs: dict | None = None
) -> TurnCheck:
    """Check one turn of agent calls against the reference calls.

    ``actual_calls`` may mix ``ToolCall`` objects, dicts and Python
    call source strings; each is parsed before comparison, and a
    call that cannot be parsed fails the turn as
    ``unparsable_call``. Order matters: the same calls in a
    different order fail with ``wrong_order``. Fewer actual calls
    than reference calls fail with ``missing_call`` per missing
    position; more fail with ``extra_call`` per extra position.
    """
    specs = specs or {}
    parsed: list[ToolCall | None] = []
    failures: list[CallFailure] = []
    for index, raw in enumerate(actual_calls):
        try:
            spec = None
            if isinstance(raw, str):
                # A source string names its own function, so the
                # spec can only be found after parsing the name.
                # Parse once without a spec for the name, then
                # re-parse with the spec when positionals exist.
                try:
                    parsed.append(parse_tool_call(raw))
                    continue
                except ToolCallParseError as exc:
                    if exc.kind != "unexpected_positional":
                        raise
                    name_tree = ast.parse(raw.strip(), mode="eval")
                    if not isinstance(name_tree.body, ast.Call) or not isinstance(
                        name_tree.body.func, ast.Name
                    ):
                        raise
                    name = name_tree.body.func.id
                    spec = specs.get(name)
                    parsed.append(parse_tool_call(raw, spec=spec))
                    continue
            parsed.append(parse_tool_call(raw))
        except ToolCallParseError as exc:
            parsed.append(None)
            failures.append(
                CallFailure(
                    kind=exc.kind,
                    turn=turn,
                    call_index=index,
                    path=f"calls[{index}]",
                    expected=None,
                    actual=raw,
                    message=f"call {index}: could not be parsed ({exc})",
                )
            )
    calls_checked = len(expected_calls)
    calls_correct = 0
    overlap = min(len(parsed), len(expected_calls))
    if len(parsed) == len(expected_calls) and parsed and all(call is not None for call in parsed):
        # The all() above proves there is no None; filter to a
        # ToolCall-typed list so mypy knows too.
        actual_parsed: list[ToolCall] = [call for call in parsed if call is not None]
        canonical_actual = sorted(call.canonical() for call in actual_parsed)
        canonical_expected = sorted(call.canonical() for call in expected_calls)
        sequence_matches = all(
            call.canonical() == expected.canonical()
            for call, expected in zip(actual_parsed, expected_calls)
        )
        if not sequence_matches and canonical_actual == canonical_expected:
            failures.append(
                CallFailure(
                    kind="wrong_order",
                    turn=turn,
                    call_index=None,
                    path="calls",
                    expected=[c.to_source() for c in expected_calls],
                    actual=[c.to_source() for c in actual_parsed],
                    message="calls are the same set in a different order",
                )
            )
    for index in range(overlap):
        call = parsed[index]
        if call is None:
            continue
        call_failures = compare_call(call, expected_calls[index], turn=turn, call_index=index)
        if not call_failures:
            calls_correct += 1
        failures.extend(call_failures)
    if len(parsed) < len(expected_calls):
        for index in range(len(parsed), len(expected_calls)):
            failures.append(
                CallFailure(
                    kind="missing_call",
                    turn=turn,
                    call_index=index,
                    path=f"calls[{index}]",
                    expected=expected_calls[index].to_source(),
                    actual=None,
                    message=(
                        f"call {index}: missing, expected {expected_calls[index].to_source()}"
                    ),
                )
            )
    elif len(parsed) > len(expected_calls):
        for index in range(len(expected_calls), len(parsed)):
            actual = parsed[index]
            failures.append(
                CallFailure(
                    kind="extra_call",
                    turn=turn,
                    call_index=index,
                    path=f"calls[{index}]",
                    expected=None,
                    actual=actual.to_source() if actual is not None else None,
                    message=(
                        f"call {index}: extra call "
                        f"{actual.to_source() if actual is not None else None!r}"
                    ),
                )
            )
    passed = not failures
    return TurnCheck(
        turn=turn,
        passed=passed,
        failures=failures,
        calls_checked=calls_checked,
        calls_correct=calls_correct,
    )


def check_task(
    task_id: str,
    actual_turns: list,
    expected_turns: list[list[ToolCall]],
    *,
    specs: dict | None = None,
) -> TaskCheck:
    """Check a whole multi-turn task.

    ``actual_turns`` is one list of calls per turn (each call a
    ``ToolCall``, a dict, or Python call source). ``expected_turns``
    is the reference calls per turn. A turn with more or fewer
    turns than the reference fails with ``missing_call`` /
    ``extra_call`` for the whole turn. The task passes only when
    every turn passes, the BFCL rule. Turn accuracy is the share
    of turns fully correct; call accuracy is the share of
    reference calls matched exactly at their position.
    """
    turns: list[TurnCheck] = []
    failures: list[CallFailure] = []
    for index, expected in enumerate(expected_turns):
        actual = actual_turns[index] if index < len(actual_turns) else []
        result = check_turn(actual, expected, turn=index, specs=specs)
        turns.append(result)
        failures.extend(result.failures)
    if len(actual_turns) > len(expected_turns):
        for index in range(len(expected_turns), len(actual_turns)):
            for call_index, raw in enumerate(actual_turns[index]):
                try:
                    call = parse_tool_call(raw)
                    rendered = call.to_source()
                except ToolCallParseError:
                    rendered = repr(raw)
                failures.append(
                    CallFailure(
                        kind="extra_call",
                        turn=index,
                        call_index=call_index,
                        path=f"turns[{index}].calls[{call_index}]",
                        expected=None,
                        actual=rendered,
                        message=f"turn {index}: extra turn with call {rendered}",
                    )
                )
            turns.append(
                TurnCheck(
                    turn=index,
                    passed=False,
                    failures=[f for f in failures if f.turn == index],
                    calls_checked=0,
                    calls_correct=0,
                )
            )
    total_turns = len(expected_turns)
    passed_turns = sum(1 for turn in turns[:total_turns] if turn.passed)
    turn_accuracy = (passed_turns / total_turns) if total_turns else 0.0
    total_calls = sum(turn.calls_checked for turn in turns)
    correct_calls = sum(turn.calls_correct for turn in turns)
    call_accuracy = (correct_calls / total_calls) if total_calls else 0.0
    passed = len(actual_turns) == len(expected_turns) and all(
        turn.passed for turn in turns[:total_turns]
    )
    return TaskCheck(
        task_id=task_id,
        passed=passed,
        turns=turns,
        turn_accuracy=turn_accuracy,
        call_accuracy=call_accuracy,
        failures=failures,
    )
