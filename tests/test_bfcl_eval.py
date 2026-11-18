"""Tests for the BFCL-style held-out task set and the AST checker.

All tests are in-memory / cassette replay only: no live model call,
no network, no Postgres table is touched by the test bodies
themselves. Held-out tasks carry inline cassettes, so they never
read a cassette file from disk either.
"""

import copy

import pytest

from puspa.cassette.cassette import TASK_REFERENCE
from puspa.evidence.agentbench_tasks import HELDOUT_TASK_IDS
from puspa.evidence.bfcl_checker import (
    ToolCall,
    ToolCallParseError,
    check_task,
    check_turn,
    compare_call,
    parse_tool_call,
)
from puspa.evidence.bfcl_tasks import (
    BFCL_TASK_IDS,
    FUNCTION_CATALOG,
    load_bfcl_tasks,
    score_bfcl,
    summarize_bfcl,
)
from puspa.evidence.eval_harness import (
    EvalHarness,
    load_reference_tasks,
    run_bfcl_eval,
    run_heldout_eval,
    run_reference_eval,
)


def _call(tool_name, **arguments):
    return ToolCall(name=tool_name, arguments=dict(arguments))


def _by_id(tasks):
    return {task.task_id: task for task in tasks}


# ---------------------------------------------------------------------------
# The checker: parsing a call from every accepted form
# ---------------------------------------------------------------------------

def test_parse_tool_call_from_source_string():
    call = parse_tool_call(
        'read_price(product="Laptop X", max_price=15000000)')
    assert call == _call("read_price", product="Laptop X",
                         max_price=15000000)


def test_parse_tool_call_from_dict_and_json_string_args():
    call = parse_tool_call({"name": "read_bill", "arguments": {
        "provider": "PLN", "customer_number": "512345678901"}})
    assert call == _call("read_bill", provider="PLN",
                         customer_number="512345678901")
    call = parse_tool_call({"name": "read_bill", "arguments":
                            '{"provider": "PLN", "customer_number": "1"}'})
    assert call == _call("read_bill", provider="PLN", customer_number="1")
    call = parse_tool_call({"function": {
        "name": "read_price",
        "arguments": {"product": "Mouse", "max_price": 200000}}})
    assert call == _call("read_price", product="Mouse", max_price=200000)


def test_parse_tool_call_from_source_with_nested_literals():
    call = parse_tool_call(
        'calendar_create_event(title="Team meeting", '
        'start_time="2026-11-20T10:00:00+07:00", '
        'attendees=["ana@example.com", "budi@example.com"])')
    assert call == _call(
        "calendar_create_event", title="Team meeting",
        start_time="2026-11-20T10:00:00+07:00",
        attendees=["ana@example.com", "budi@example.com"])


def test_parse_positional_args_with_spec():
    spec = FUNCTION_CATALOG["read_price"].parameters
    call = parse_tool_call('read_price("Laptop X", 15000000)', spec=spec)
    assert call == _call("read_price", product="Laptop X",
                         max_price=15000000)


def test_parse_positional_args_without_spec_is_an_error():
    with pytest.raises(ToolCallParseError) as excinfo:
        parse_tool_call('read_price("Laptop X", 15000000)')
    assert excinfo.value.kind == "unexpected_positional"


def test_parse_non_call_is_an_error():
    for raw in ["not a call", "1 + 1", "read_price", "", {"nope": True},
                42, None]:
        with pytest.raises(ToolCallParseError):
            parse_tool_call(raw)


def test_parse_non_literal_argument_is_an_error():
    with pytest.raises(ToolCallParseError):
        parse_tool_call("read_price(product=some_variable)")


# ---------------------------------------------------------------------------
# The checker: one call against its reference
# ---------------------------------------------------------------------------

def test_exact_call_passes_in_every_form():
    expected = _call("read_price", product="Laptop X", max_price=15000000)
    assert compare_call(expected, expected, turn=0) == []
    assert compare_call(
        parse_tool_call('read_price(product="Laptop X", max_price=15000000)'),
        expected, turn=0) == []
    assert compare_call(
        parse_tool_call({"name": "read_price", "arguments": {
            "product": "Laptop X", "max_price": 15000000}}),
        expected, turn=0) == []


def test_wrong_function_name():
    failures = compare_call(
        _call("read_bill", provider="PLN", customer_number="1"),
        _call("read_price", product="Laptop X"), turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_name"]
    assert failures[0].expected == "read_price"
    assert failures[0].actual == "read_bill"


def test_wrong_argument_value():
    failures = compare_call(
        _call("read_price", product="Laptop X", max_price=14000000),
        _call("read_price", product="Laptop X", max_price=15000000),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_value"]
    assert failures[0].path == "arguments.max_price"
    assert failures[0].expected == 15000000
    assert failures[0].actual == 14000000


def test_wrong_argument_value_string():
    failures = compare_call(
        _call("read_price", product="Laptop Y", max_price=15000000),
        _call("read_price", product="Laptop X", max_price=15000000),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_value"]
    assert failures[0].path == "arguments.product"


def test_wrong_argument_type_string_for_int():
    failures = compare_call(
        _call("read_price", product="Laptop X", max_price="15000000"),
        _call("read_price", product="Laptop X", max_price=15000000),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_type"]
    assert failures[0].path == "arguments.max_price"


def test_wrong_argument_type_float_for_int():
    failures = compare_call(
        _call("read_price", product="Laptop X", max_price=15000000.0),
        _call("read_price", product="Laptop X", max_price=15000000),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_type"]


def test_wrong_argument_type_int_for_float_threshold():
    failures = compare_call(
        _call("watch_prices", product_id="keyboard-k68",
              price_threshold=899999),
        _call("watch_prices", product_id="keyboard-k68",
              price_threshold=899999.5),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_type"]


def test_wrong_argument_type_digit_string_for_int_customer_number():
    # The reference keeps the customer number a string; an agent
    # that turns it into an integer is wrong, not clever.
    failures = compare_call(
        _call("read_bill", provider="PLN", customer_number=512345678901),
        _call("read_bill", provider="PLN",
              customer_number="512345678901"),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_type"]
    assert failures[0].path == "arguments.customer_number"


def test_wrong_argument_type_bool_is_not_int():
    failures = compare_call(
        _call("read_price", product="Laptop X", max_price=True),
        _call("read_price", product="Laptop X", max_price=1),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_type"]


def test_missing_argument():
    failures = compare_call(
        _call("read_price", product="Laptop X"),
        _call("read_price", product="Laptop X", max_price=15000000),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["missing_argument"]
    assert failures[0].path == "arguments.max_price"


def test_extra_argument():
    failures = compare_call(
        _call("read_price", product="Laptop X", max_price=15000000,
              store="tokopedia"),
        _call("read_price", product="Laptop X", max_price=15000000),
        turn=0, call_index=0)
    assert [f.kind for f in failures] == ["extra_argument"]
    assert failures[0].path == "arguments.store"


def test_empty_string_is_a_value_not_a_missing_argument():
    expected = _call("create_trigger", name="weekly bill reminder",
                     condition="every Monday 08:00", note="")
    assert compare_call(expected, expected, turn=0) == []
    failures = compare_call(
        _call("create_trigger", name="weekly bill reminder",
              condition="every Monday 08:00"),
        expected, turn=0, call_index=0)
    assert [f.kind for f in failures] == ["missing_argument"]
    assert failures[0].path == "arguments.note"
    failures = compare_call(
        _call("create_trigger", name="weekly bill reminder",
              condition="every Monday 08:00", note=None),
        expected, turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_type"]


def test_nested_list_argument_item_and_length():
    expected = _call("calendar_create_event", title="Team meeting",
                     start_time="2026-11-20T10:00:00+07:00",
                     attendees=["ana@example.com", "budi@example.com"])
    failures = compare_call(
        _call("calendar_create_event", title="Team meeting",
              start_time="2026-11-20T10:00:00+07:00",
              attendees=["ana@example.com", "cici@example.com"]),
        expected, turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_value"]
    assert failures[0].path == "arguments.attendees[1]"
    failures = compare_call(
        _call("calendar_create_event", title="Team meeting",
              start_time="2026-11-20T10:00:00+07:00",
              attendees=["ana@example.com"]),
        expected, turn=0, call_index=0)
    assert [f.kind for f in failures] == ["wrong_value"]
    assert failures[0].path == "arguments.attendees"


def test_multiple_failures_all_reported():
    failures = compare_call(
        _call("read_price", product="Laptop Y", max_price="15000000",
              store="tokopedia"),
        _call("read_price", product="Laptop X", max_price=15000000),
        turn=0, call_index=0)
    assert {f.kind for f in failures} == {
        "wrong_value", "wrong_type", "extra_argument"}


# ---------------------------------------------------------------------------
# The checker: one turn
# ---------------------------------------------------------------------------

def test_turn_passes_for_exact_calls():
    result = check_turn(
        [_call("read_bill", provider="PLN",
               customer_number="512345678901"),
         _call("read_bill", provider="PDAM",
               customer_number="0099887766")],
        [_call("read_bill", provider="PLN",
               customer_number="512345678901"),
         _call("read_bill", provider="PDAM",
               customer_number="0099887766")],
        turn=0)
    assert result.passed is True
    assert result.calls_checked == 2
    assert result.calls_correct == 2
    assert result.failures == []


def test_turn_wrong_order():
    result = check_turn(
        [_call("read_bill", provider="PDAM",
               customer_number="0099887766"),
         _call("read_bill", provider="PLN",
               customer_number="512345678901")],
        [_call("read_bill", provider="PLN",
               customer_number="512345678901"),
         _call("read_bill", provider="PDAM",
               customer_number="0099887766")],
        turn=0)
    assert result.passed is False
    assert result.calls_correct == 0
    assert "wrong_order" in {f.kind for f in result.failures}


def test_turn_missing_call():
    result = check_turn(
        [_call("read_bill", provider="PLN",
               customer_number="512345678901")],
        [_call("read_bill", provider="PLN",
               customer_number="512345678901"),
         _call("read_bill", provider="PDAM",
               customer_number="0099887766")],
        turn=0)
    assert result.passed is False
    assert result.calls_correct == 1
    assert [f.kind for f in result.failures] == ["missing_call"]
    assert result.failures[0].call_index == 1


def test_turn_extra_call():
    result = check_turn(
        [_call("read_bill", provider="PLN",
               customer_number="512345678901"),
         _call("read_bill", provider="PDAM",
               customer_number="0099887766"),
         _call("read_price", product="Laptop X")],
        [_call("read_bill", provider="PLN",
               customer_number="512345678901"),
         _call("read_bill", provider="PDAM",
               customer_number="0099887766")],
        turn=0)
    assert result.passed is False
    assert result.calls_correct == 2
    assert [f.kind for f in result.failures] == ["extra_call"]
    assert result.failures[0].call_index == 2


def test_turn_unparsable_call_fails_the_turn():
    result = check_turn(
        ["this is not a call"],
        [_call("read_price", product="Laptop X")],
        turn=0)
    assert result.passed is False
    assert result.calls_correct == 0
    assert [f.kind for f in result.failures] == ["unparsable_call"]


# ---------------------------------------------------------------------------
# The checker: a whole multi-turn task
# ---------------------------------------------------------------------------

def test_multiturn_scored_per_turn_and_aggregate():
    expected_turns = [
        [_call("read_price", product="Laptop X", max_price=15000000)],
        [_call("prepare_payment", provider="PLN", amount=250000)],
        [_call("create_trigger", name="weekly bill reminder",
               condition="every Monday 08:00", note="")],
    ]
    actual_turns = [
        [_call("read_price", product="Laptop X", max_price=15000000)],
        [_call("prepare_payment", provider="PLN", amount=240000)],
        [_call("create_trigger", name="weekly bill reminder",
               condition="every Monday 08:00", note="")],
    ]
    check = check_task("bfcl-demo", actual_turns, expected_turns)
    assert check.passed is False
    assert [turn.passed for turn in check.turns] == [True, False, True]
    assert check.turn_accuracy == pytest.approx(2 / 3)
    assert check.call_accuracy == pytest.approx(2 / 3)
    assert check.total_turns == 3
    assert check.total_calls == 3
    assert len(check.failures) == 1
    assert check.failures[0].kind == "wrong_value"
    assert check.failures[0].turn == 1


def test_multiturn_wrong_turn_order_fails():
    expected_turns = [
        [_call("read_price", product="Laptop Y", max_price=15000000)],
        [_call("read_price", product="Laptop X", max_price=15000000)],
    ]
    actual_turns = [
        [_call("read_price", product="Laptop X", max_price=15000000)],
        [_call("read_price", product="Laptop Y", max_price=15000000)],
    ]
    check = check_task("bfcl-demo", actual_turns, expected_turns)
    assert check.passed is False
    assert check.turn_accuracy == 0.0
    assert check.call_accuracy == 0.0


def test_multiturn_extra_turn_fails():
    expected_turns = [
        [_call("read_price", product="Laptop X", max_price=15000000)],
    ]
    actual_turns = [
        [_call("read_price", product="Laptop X", max_price=15000000)],
        [_call("read_price", product="Laptop Y", max_price=15000000)],
    ]
    check = check_task("bfcl-demo", actual_turns, expected_turns)
    assert check.passed is False
    assert check.turn_accuracy == 1.0
    assert "extra_call" in {f.kind for f in check.failures}


def test_multiturn_missing_turn_fails():
    expected_turns = [
        [_call("read_price", product="Laptop X", max_price=15000000)],
        [_call("read_price", product="Laptop Y", max_price=15000000)],
    ]
    check = check_task("bfcl-demo", [[_call(
        "read_price", product="Laptop X", max_price=15000000)]],
        expected_turns)
    assert check.passed is False
    assert check.turn_accuracy == pytest.approx(1 / 2)
    assert check.call_accuracy == pytest.approx(1 / 2)


# ---------------------------------------------------------------------------
# The set itself: real tasks, held out, schema-conformant
# ---------------------------------------------------------------------------

def test_bfcl_set_shape():
    tasks = load_bfcl_tasks()
    assert len(tasks) == 8
    assert len(BFCL_TASK_IDS) == 8
    total_turns = 0
    total_calls = 0
    for task in tasks:
        assert task.task_id.startswith("bfcl-")
        assert task.environment == "bfcl"
        assert len(task.prompt) > 40
        assert task.cassette is not None
        bfcl = task.cassette["bfcl_task"]
        assert bfcl.task_id == task.task_id
        assert len(bfcl.turns) >= 1
        total_turns += len(bfcl.turns)
        for turn in bfcl.turns:
            assert turn.expected_calls, f"{task.task_id} has an empty turn"
            assert len(turn.tool_outputs) == len(turn.expected_calls)
            total_calls += len(turn.expected_calls)
        recording = task.cassette["recording"]
        assert len(recording) == sum(
            len(turn.expected_calls) for turn in bfcl.turns)
    assert total_turns == 12
    assert total_calls == 16


def test_bfcl_tasks_cover_multi_turn_and_tricky_args():
    tasks = _by_id(load_bfcl_tasks())
    assert len(tasks["bfcl-watch-pay-remind"].cassette["bfcl_task"].turns) == 3
    assert len(tasks["bfcl-user-correction"].cassette["bfcl_task"].turns) == 2
    assert len(tasks["bfcl-draft-and-event"].cassette["bfcl_task"].turns) == 2
    # Empty-string argument, float threshold, digit string, omitted optional.
    watch = tasks["bfcl-watch-pay-remind"].cassette["bfcl_task"]
    assert watch.turns[2].expected_calls[0].arguments["note"] == ""
    tricky = tasks["bfcl-tricky-numbers"].cassette["bfcl_task"]
    assert tricky.turns[0].expected_calls[1].arguments[
        "price_threshold"] == 899999.5
    assert isinstance(tricky.turns[0].expected_calls[1].arguments[
        "price_threshold"], float)
    stringy = tasks["bfcl-string-not-number"].cassette["bfcl_task"]
    assert stringy.turns[0].expected_calls[0].arguments[
        "customer_number"] == "512345678901"
    optional = tasks["bfcl-optional-omitted"].cassette["bfcl_task"]
    assert "max_price" not in optional.turns[0].expected_calls[0].arguments
    assert "cc" not in optional.turns[0].expected_calls[1].arguments


def test_bfcl_reference_calls_conform_to_function_schemas():
    """Every reference call names a catalog function, passes its
    required arguments, passes no unknown argument, and matches
    the schema types (integer is not float, bool is not int)."""
    type_map = {"string": str, "integer": int, "number": (int, float),
                "array": list, "object": dict, "boolean": bool}
    for task in load_bfcl_tasks():
        bfcl = task.cassette["bfcl_task"]
        for turn in bfcl.turns:
            for call in turn.expected_calls:
                spec = FUNCTION_CATALOG[call.name]
                props = spec.parameters["properties"]
                for required in spec.required:
                    assert required in call.arguments, (
                        f"{task.task_id}: {call.name} misses {required}")
                for key, value in call.arguments.items():
                    assert key in props, (
                        f"{task.task_id}: {call.name} has unknown {key}")
                    expected_type = type_map[props[key]["type"]]
                    assert isinstance(value, expected_type), (
                        f"{task.task_id}: {call.name}.{key} has wrong type")
                    if props[key]["type"] == "integer":
                        assert type(value) is int
                    if props[key]["type"] == "boolean":
                        assert type(value) is bool


def test_bfcl_ids_disjoint_from_dev_and_agentbench():
    dev = load_reference_tasks()
    dev_ids = {task.task_id for task in dev}
    assert set(BFCL_TASK_IDS) & dev_ids == set()
    assert set(BFCL_TASK_IDS) & set(HELDOUT_TASK_IDS) == set()
    assert set(BFCL_TASK_IDS) & set(TASK_REFERENCE) == set()
    heldout_prompts = {task.prompt for task in load_bfcl_tasks()}
    dev_prompts = {task.prompt for task in dev}
    assert heldout_prompts & dev_prompts == set()


def test_bfcl_tools_are_real_puspa_tools():
    """Every tool in the set is a tool the repo already has: the
    cassette tools or watch_prices from the workflow runner."""
    known = {"read_price", "read_bill", "prepare_payment",
             "gmail_search", "gmail_read", "gmail_create_draft",
             "calendar_create_event", "create_trigger", "drive_search",
             "drive_read", "search_artikel", "read_artikel",
             "read_memory", "watch_prices"}
    used = {call.name for task in load_bfcl_tasks()
            for turn in task.cassette["bfcl_task"].turns
            for call in turn.expected_calls}
    assert used <= known
    assert "watch_prices" in used
    assert "prepare_payment" in used


# ---------------------------------------------------------------------------
# End to end through the harness, with measured accuracy
# ---------------------------------------------------------------------------

def test_reference_agent_scores_full_on_heldout_set():
    """Measured accuracy, reference agent: 8/8 tasks, 12/12 turns,
    16/16 calls. This locks the ceiling, not a model score: the
    reference agent replays the reference calls."""
    report = run_bfcl_eval()
    assert report.total == 8
    assert report.passed == 8
    assert report.score == 1.0
    assert "8/8" in report.summary()
    summary = summarize_bfcl(report)
    assert summary["bfcl"]["score"] == 1.0
    assert summary["mean_turn_accuracy"] == 1.0
    assert summary["mean_call_accuracy"] == 1.0
    for result in report.results:
        assert result.details["turn_accuracy"] == 1.0
        assert result.details["call_accuracy"] == 1.0
        assert result.details["failures"] == []


def test_source_string_agent_scores_full():
    """An agent that emits the same calls as Python source strings
    (the form a model transcript carries) scores the same 8/8."""
    def source_agent(task, turn_index, history):
        return [call.to_source()
                for call in task.turns[turn_index].expected_calls]

    report = run_bfcl_eval(agent=source_agent)
    assert report.total == 8
    assert report.passed == 8
    assert report.score == 1.0


def test_swap_order_agent_scores_half():
    """An agent that reverses the call order inside every turn
    passes only the single-call turns: 4/8 tasks, mean turn
    accuracy 0.5, mean call accuracy 0.5. Order is really scored."""
    def swap_agent(task, turn_index, history):
        return list(reversed(task.turns[turn_index].expected_calls))

    report = run_bfcl_eval(agent=swap_agent)
    assert report.total == 8
    assert report.passed == 4
    assert report.score == 0.5
    summary = summarize_bfcl(report)
    assert summary["mean_turn_accuracy"] == pytest.approx(0.5)
    assert summary["mean_call_accuracy"] == pytest.approx(0.5)
    passed_ids = {r.task_id for r in report.results if r.passed}
    assert passed_ids == {"bfcl-price-single", "bfcl-watch-pay-remind",
                          "bfcl-user-correction", "bfcl-draft-and-event"}


def test_stringify_numbers_agent_scores_low():
    """An agent that turns every number into a string passes only
    the two all-string tasks: 2/8 tasks, mean turn accuracy
    17/48, mean call accuracy 5/12. Type errors are really scored."""
    def stringify(value):
        if isinstance(value, bool):
            return value
        if isinstance(value, (int, float)):
            return str(value)
        if isinstance(value, list):
            return [stringify(item) for item in value]
        if isinstance(value, dict):
            return {key: stringify(item) for key, item in value.items()}
        return value

    def stringify_agent(task, turn_index, history):
        calls = []
        for call in task.turns[turn_index].expected_calls:
            calls.append(ToolCall(
                name=call.name,
                arguments={key: stringify(value)
                           for key, value in call.arguments.items()}))
        return calls

    report = run_bfcl_eval(agent=stringify_agent)
    assert report.total == 8
    assert report.passed == 2
    assert report.score == pytest.approx(2 / 8)
    summary = summarize_bfcl(report)
    assert summary["mean_turn_accuracy"] == pytest.approx(17 / 48)
    assert summary["mean_call_accuracy"] == pytest.approx(5 / 12)
    passed_ids = {r.task_id for r in report.results if r.passed}
    assert passed_ids == {"bfcl-bills-two-calls", "bfcl-optional-omitted"}


def test_drop_all_agent_scores_zero():
    def silent_agent(task, turn_index, history):
        return []

    report = run_bfcl_eval(agent=silent_agent)
    assert report.total == 8
    assert report.passed == 0
    assert report.score == 0.0
    summary = summarize_bfcl(report)
    assert summary["mean_turn_accuracy"] == 0.0
    assert summary["mean_call_accuracy"] == 0.0


def test_bfcl_reported_separately_from_dev_and_agentbench():
    dev_report = run_reference_eval()
    heldout_report = run_heldout_eval()
    bfcl_report = run_bfcl_eval()
    assert dev_report.total == 10
    assert heldout_report.total == 8
    assert bfcl_report.total == 8
    dev_ids = {r.task_id for r in dev_report.results}
    heldout_ids = {r.task_id for r in heldout_report.results}
    bfcl_ids = {r.task_id for r in bfcl_report.results}
    assert dev_ids & heldout_ids == set()
    assert dev_ids & bfcl_ids == set()
    assert heldout_ids & bfcl_ids == set()
    assert dev_report.score == 1.0
    assert heldout_report.score == 1.0
    assert bfcl_report.score == 1.0
    summary = summarize_bfcl(bfcl_report)
    assert set(summary) == {"bfcl", "mean_turn_accuracy",
                            "mean_call_accuracy", "per_task"}
    assert set(summary["per_task"]) == set(BFCL_TASK_IDS)


def test_inline_cassette_needs_no_cassette_files():
    """BFCL tasks run even when the cassette directory is empty."""
    from pathlib import Path

    from puspa.evidence.bfcl_tasks import (
        bfcl_solver as default_bfcl_solver,
    )

    harness = EvalHarness(cassette_dir=Path("/tmp/no-such-cassette-dir"),
                          solver=default_bfcl_solver, scorer=score_bfcl)
    report = harness.run(load_bfcl_tasks())
    assert report.total == 8
    assert report.passed == 8


def test_corrupted_run_scores_low_per_task():
    """An agent that gets one value wrong fails that task (score
    0.0, call accuracy 1/2) while the reference run passes it."""
    from puspa.evidence.bfcl_tasks import (
        bfcl_solver as default_bfcl_solver,
        make_bfcl_solver,
        reference_agent,
    )

    def one_value_wrong_agent(task, turn_index, history):
        calls = reference_agent(task, turn_index, history)
        if task.task_id == "bfcl-tricky-numbers":
            wrong = copy.deepcopy(calls[0])
            wrong.arguments["amount"] = 499998
            calls = [wrong] + calls[1:]
        return calls

    harness = EvalHarness(solver=make_bfcl_solver(one_value_wrong_agent),
                          scorer=score_bfcl)
    tasks = _by_id(load_bfcl_tasks())
    result = harness.run_task(tasks["bfcl-tricky-numbers"])
    assert result.passed is False
    assert result.score == 0.0
    assert result.details["call_accuracy"] == pytest.approx(1 / 2)
    kinds = {f["kind"] for f in result.details["failures"]}
    assert kinds == {"wrong_value"}
    clean = EvalHarness(solver=default_bfcl_solver, scorer=score_bfcl)
    assert clean.run_task(tasks["bfcl-tricky-numbers"]).passed is True
