"""Tes pustaka blok (§2.12): 15 blok tetap, skema, batas MVP."""

import pytest

from puspa.workflow.blocks import (
    BLOCKS,
    BlockError,
    block_definition,
    branch_depth,
    list_blocks,
    validate_inputs,
    validate_step,
    validate_workflow,
)

BLOCK_REQUIRED = (
    "check_bill",
    "compare_prices",
    "watch_prices",
    "search_email",
    "read_email",
    "create_draft",
    "create_event",
    "prepare_payment",
    "send_notification",
    "ask_user",
    "wait_for_approval",
    "condition",
    "for_each",
    "save_memory",
    "save_to_module_table",
)


def test_library_exactly_15_blocks():
    assert set(list_blocks()) == set(BLOCK_REQUIRED)
    assert len(BLOCKS) == 15


def test_each_block_has_schema_klass_and_permission():
    for name in BLOCK_REQUIRED:
        d = block_definition(name)
        assert d["inputs"] and d["output"], name
        assert d["permission_class"] in ("Pembaca", "Pelaksana", "Pemeriksa"), name
        assert set(d["permission"]) == {"connectors", "scope", "domain", "spending_limit"}, name


def test_block_read_email_reader_permission_class():
    assert block_definition("read_email")["permission_class"] == "Pembaca"
    assert block_definition("search_email")["permission"]["connectors"] == ["gmail"]
    assert block_definition("read_email")["permission"]["scope"] == ["gmail.readonly"]


def test_block_action_logout_only_create_event_and_prepare_payment():
    output = {n for n, d in BLOCKS.items() if d["outbound_action"]}
    assert output == {"create_event", "prepare_payment"}


def test_unknown_block_rejected():
    with pytest.raises(BlockError):
        block_definition("send_whatsapp")
    assert validate_step({"block": "send_whatsapp", "inputs": {}})


def test_check_input_with_fewer_fields():
    assert validate_inputs("check_bill", {"provider": "PLN"}) == ["customer_number"]
    assert validate_inputs("check_bill", {"provider": "PLN", "customer_number": "1"}) == []


def test_max_10_steps():
    step = [{"block": "send_notification", "inputs": {"message": "x"}}] * 11
    problems = validate_workflow(step)
    assert any("maks 10" in m for m in problems)


def test_max_2_branching_levels():
    depth = {"block": "send_notification", "inputs": {"message": "x"}}
    tiga = {
        "block": "condition",
        "inputs": {"expression": "a == b"},
        "then": [
            {
                "block": "condition",
                "inputs": {"expression": "a == b"},
                "then": [
                    {
                        "block": "condition",
                        "inputs": {"expression": "a == b"},
                        "then": [depth],
                        "else": [],
                    }
                ],
                "else": [],
            }
        ],
        "else": [],
    }
    assert branch_depth([tiga]) == 3
    assert any("maks 2" in m for m in validate_workflow([tiga]))


def test_branch_2_levels_passed():
    depth = {"block": "send_notification", "inputs": {"message": "x"}}
    dua = {
        "block": "condition",
        "inputs": {"expression": "a == b"},
        "then": [
            {"block": "condition", "inputs": {"expression": "a == b"}, "then": [depth], "else": []}
        ],
        "else": [],
    }
    assert branch_depth([dua]) == 2
    assert validate_workflow([dua]) == []


def test_for_each_max_20_items_checked_in_runner():
    step = {
        "block": "for_each",
        "inputs": {"items": list(range(21))},
        "nested_steps": [{"block": "send_notification", "inputs": {"message": "x"}}],
    }
    # Skema langkah lolos (batas item dicek saat jalan, bukan saat susun).
    assert validate_step(step) == []
