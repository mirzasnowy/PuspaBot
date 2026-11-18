"""Tests for the AgentBench held-out task set.

All tests are in-memory / cassette replay only: no live model call,
no network, no Postgres table is touched by the test bodies
themselves. Held-out tasks carry inline cassettes, so they never
read a cassette file from disk either.
"""

import copy
from dataclasses import replace
from pathlib import Path

import pytest

from puspa.cassette.cassette import TASK_REFERENCE
from puspa.evidence.agentbench_tasks import (
    ENVIRONMENTS,
    check_disjoint,
    derive_heldout_state,
    heldout_solver,
    load_heldout_tasks,
    score_agentbench,
    summarize_by_environment,
)
from puspa.evidence.eval_harness import (
    EvalHarness,
    load_reference_tasks,
    run_heldout_eval,
    run_reference_eval,
)


def _by_id(tasks):
    return {task.task_id: task for task in tasks}


# ---------------------------------------------------------------------------
# The set itself: real tasks, one per shape, two per environment
# ---------------------------------------------------------------------------


def test_heldout_set_has_two_tasks_per_environment():
    tasks = load_heldout_tasks()
    assert len(tasks) == 8
    assert set(ENVIRONMENTS) == {"db", "kg", "os", "webshop"}
    for env in ENVIRONMENTS:
        assert len([t for t in tasks if t.environment == env]) == 2


def test_heldout_tasks_are_real_tasks_not_empty_templates():
    for task in load_heldout_tasks():
        assert task.task_id.startswith("agentbench-")
        assert task.environment in ENVIRONMENTS
        assert len(task.prompt) > 40
        assert task.cassette is not None
        recording = task.cassette["recording"]
        assert len(recording) >= 1
        assert all(rec.get("tool") for rec in recording)
        assert task.expected_state, f"{task.task_id} has no expected state"
        assert task.cassette_name == task.task_id


def test_heldout_expected_states_match_hand_computed_values():
    """The derivation is real: hand-check every expected value."""
    tasks = _by_id(load_heldout_tasks())
    db_cheap = tasks["agentbench-db-cheapest-headphones"].expected_state
    assert db_cheap["cheapest_in_stock_product"] == "hp-volta-03"
    assert db_cheap["cheapest_in_stock_price"] == 429000
    assert db_cheap["rows_returned"] == 4
    db_top = tasks["agentbench-db-top-revenue-category"].expected_state
    assert db_top["top_category"] == "keyboards"
    assert db_top["top_revenue"] == 61400000
    kg_supplier = tasks["agentbench-kg-supplier-two-hop"].expected_state
    assert kg_supplier["brand"] == "Aurora"
    assert kg_supplier["supplier"] == "Sinar Components Ltd"
    assert kg_supplier["hops"] == 2
    kg_warranty = tasks["agentbench-kg-warranty-provider"].expected_state
    assert kg_warranty["warranty_provider"] == "CoverAll Insurance"
    assert kg_warranty["coverage_months"] == 24
    os_largest = tasks["agentbench-os-largest-log-file"].expected_state
    assert os_largest["largest_file"] == "app.log.1"
    assert os_largest["largest_size_bytes"] == 5242880
    assert os_largest["files_listed"] == 4
    os_stale = tasks["agentbench-os-stale-cache-total"].expected_state
    assert os_stale["stale_count"] == 3
    assert os_stale["stale_total_bytes"] == 409600 + 1048576 + 3145728
    ws_cable = tasks["agentbench-webshop-cheapest-rated-cable"].expected_state
    assert ws_cable["cheapest_product"] == "cable-base-1m"
    assert ws_cable["cheapest_price"] == 45000
    assert ws_cable["quantity"] == 2
    assert ws_cable["total"] == 90000
    assert ws_cable["order_status"] == "confirmed"
    ws_kbd = tasks["agentbench-webshop-keyboard-order"].expected_state
    assert ws_kbd["cheapest_product"] == "kbd-nimbus-68"
    assert ws_kbd["cheapest_price"] == 599000
    assert ws_kbd["total"] == 599000


# ---------------------------------------------------------------------------
# Disjointness from the dev cassettes, proved by code
# ---------------------------------------------------------------------------


def test_heldout_disjoint_from_dev_cassettes():
    check = check_disjoint()
    assert check.disjoint is True
    assert check.task_id_overlap == []
    assert check.cassette_overlap == []
    assert check.tool_overlap == []
    assert check.prompt_pairs_over_threshold == []
    assert check.max_prompt_jaccard < 0.5
    assert check.heldout_tasks == 8
    assert check.dev_tasks == 10


def test_heldout_ids_and_tools_do_not_overlap_dev_explicitly():
    heldout = load_heldout_tasks()
    dev = load_reference_tasks()
    assert {t.task_id for t in heldout} & {t.task_id for t in dev} == set()
    assert {t.cassette_name for t in heldout} & set(TASK_REFERENCE) == set()
    heldout_tools = {rec["tool"] for t in heldout for rec in t.cassette["recording"]}
    dev_tools = {
        "read_price",
        "read_bill",
        "prepare_payment",
        "gmail_search",
        "gmail_read",
        "gmail_create_draft",
        "calendar_create_event",
        "create_trigger",
        "drive_search",
        "drive_read",
        "search_artikel",
        "read_artikel",
        "read_memory",
    }
    assert heldout_tools & dev_tools == set()
    assert heldout_tools == {
        "db_query",
        "kg_lookup",
        "os_list",
        "webshop_search",
        "webshop_checkout",
    }


# ---------------------------------------------------------------------------
# End to end through the harness, per environment, scored separately
# ---------------------------------------------------------------------------


def test_heldout_runs_end_to_end_through_harness():
    report = run_heldout_eval()
    assert report.total == 8
    assert report.passed == 8
    assert report.score == 1.0
    assert "8/8" in report.summary()
    by_env = summarize_by_environment(report)
    for env in ENVIRONMENTS:
        assert by_env[env]["total"] == 2
        assert by_env[env]["passed"] == 2
        assert by_env[env]["score"] == 1.0


def test_heldout_scores_reported_separately_from_dev():
    dev_report = run_reference_eval()
    heldout_report = run_heldout_eval()
    assert dev_report.total == 10
    assert heldout_report.total == 8
    dev_ids = {r.task_id for r in dev_report.results}
    heldout_ids = {r.task_id for r in heldout_report.results}
    assert dev_ids & heldout_ids == set()
    assert dev_report.score == 1.0
    assert heldout_report.score == 1.0
    # Each report's score is computed from its own results only.
    assert dev_report.passed == len(dev_report.results)
    assert heldout_report.passed == len(heldout_report.results)
    by_env = summarize_by_environment(heldout_report)
    assert by_env["heldout"]["score"] == heldout_report.score
    assert set(by_env) == {"heldout", "db", "kg", "os", "webshop"}


def test_inline_cassette_needs_no_cassette_files():
    """Held-out tasks run even when the cassette directory is empty."""
    harness = EvalHarness(
        cassette_dir=Path("/tmp/no-such-cassette-dir"),
        solver=heldout_solver,
        scorer=score_agentbench,
    )
    report = harness.run(load_heldout_tasks())
    assert report.total == 8
    assert report.passed == 8


# ---------------------------------------------------------------------------
# Scoring discriminates: a correct run scores high, a corrupted one low
# ---------------------------------------------------------------------------


def _corrupt(task):
    """Tamper one answer-determining value in a copy of the cassette."""
    cassette = copy.deepcopy(task.cassette)
    recording = cassette["recording"]
    if task.environment == "db":
        rows = recording[0]["output"]["rows"]
        revenue_rows = [row for row in rows if "revenue" in row]
        if revenue_rows:
            top = max(revenue_rows, key=lambda row: row["revenue"])
            top["revenue"] += 1
        else:
            max_price = recording[0]["output"].get("max_price")
            in_stock = [
                row
                for row in rows
                if row.get("stock", 0) > 0
                and "price" in row
                and (max_price is None or row["price"] <= max_price)
            ]
            cheapest = min(in_stock, key=lambda row: row["price"])
            cheapest["price"] += 1
    elif task.environment == "kg":
        recording[-1]["output"]["value"] = "CORRUPTED"
    elif task.environment == "os":
        files = recording[0]["output"]["files"]
        largest = max(files, key=lambda item: item["size_bytes"])
        largest["size_bytes"] = 1
    elif task.environment == "webshop":
        checkout = next(rec for rec in recording if rec["tool"] == "webshop_checkout")
        checkout["output"]["total"] = 1
    else:  # pragma: no cover - every environment is handled above
        raise AssertionError(f"unknown environment {task.environment}")
    assert derive_heldout_state(cassette) != task.expected_state
    return replace(task, cassette=cassette)


@pytest.mark.parametrize(
    "task_id",
    [
        "agentbench-db-cheapest-headphones",
        "agentbench-db-top-revenue-category",
        "agentbench-kg-supplier-two-hop",
        "agentbench-kg-warranty-provider",
        "agentbench-os-largest-log-file",
        "agentbench-os-stale-cache-total",
        "agentbench-webshop-cheapest-rated-cable",
        "agentbench-webshop-keyboard-order",
    ],
)
def test_corrupted_run_scores_low(task_id):
    task = _by_id(load_heldout_tasks())[task_id]
    harness = EvalHarness(solver=heldout_solver, scorer=score_agentbench)
    corrupted = _corrupt(task)
    result = harness.run_task(corrupted)
    assert result.passed is False
    assert result.score < 1.0
    assert result.score >= 0.0
    failed = [
        key for key, check in result.details.items() if key != "score" and not check["matched"]
    ]
    assert failed, "corruption changed no scored key"


def test_partial_credit_for_partly_right_final_state():
    task = _by_id(load_heldout_tasks())["agentbench-webshop-keyboard-order"]
    state = dict(task.expected_state)
    state["total"] = 1  # one key wrong, the rest right
    passed, details = score_agentbench(task, state)
    assert passed is False
    assert 0.0 < details["score"] < 1.0
    assert details["total"]["matched"] is False
    assert details["cheapest_product"]["matched"] is True


def test_empty_final_state_scores_zero():
    task = _by_id(load_heldout_tasks())["agentbench-db-cheapest-headphones"]
    passed, details = score_agentbench(task, {})
    assert passed is False
    assert details["score"] == 0.0
