"""AgentBench-style held-out task set for the eval harness.

Pattern studied (rewritten, not copied):
- liu00222/AgentBench (Apache 2.0): one benchmark across many
  environments (DB, KG, OS, WebShop and others), each task judged by
  the final state of its environment, not by the agent's words.
  AgentBench ships real environments (a SQL database, a knowledge
  graph, a shell, a live web shop). Puspa's harness has no live
  environment: every tool is replaced by a cassette replay, so the
  environments here are simulated by recorded tool outputs in the
  same cassette shape as the 10 dev cassettes. The shape is the
  AgentBench shape (multi-environment, final-state scoring); the
  execution is Puspa's cassette replay.

Why this file exists: the 10 recorded cassettes are a dev set. A
1.0 score on them proves nothing about generalisation, because the
tasks, the entities and the values are all known in advance. This
set is DISJOINT from the dev set: different task ids, different
cassette names, different tools, different entities and values, and
different prompts. ``check_disjoint`` proves that by code, not by
claim; ``tests/test_agentbench_eval.py`` asserts it on every run.

Design, mirroring the dev harness in ``eval_harness.py``:

- ``HELDOUT_TASKS``: 8 tasks, 2 per environment (db, kg, os,
  webshop). Each task is an ``EvalTask`` whose cassette is inline
  (``task.cassette``), so no cassette file on disk is needed and a
  held-out task can never be confused with a dev cassette file.
- ``derive_heldout_state``: the expected final state, derived from
  the recording by code only, the same way ``derive_expected_state``
  derives dev expectations. The solver replays the recording and
  derives the same state; a run that replays faithfully scores 1.0.
- ``heldout_solver``: replay the inline cassette with a
  ``CassettePlayer`` and derive the final state.
- ``score_agentbench``: the existing ``score_state`` per-key check,
  with the score replaced by the fraction of matched keys, so a
  run that gets part of the final state right earns partial credit
  instead of a flat 0.
- ``run_heldout_eval`` (in ``eval_harness.py``): runs only this set
  and returns its own ``EvalReport``. The held-out score is never
  mixed into the dev score; ``summarize_by_environment`` breaks it
  down per environment.

Simulation boundary, stated plainly: no real database, knowledge
graph, operating system or web shop runs here. The environments are
recorded tool outputs, so what this set measures is whether the
agent reaches the right final state on tasks it has never seen,
through the same replay mechanism the dev set uses. A live
environment run (real SQL, real shell, real shop) is follow-up work
for a sandbox-backed solver; it needs no outside service, only a
sandbox that can host the environments.
"""

from __future__ import annotations

import copy
import re
from dataclasses import dataclass, field

from puspa.cassette.cassette import TASK_REFERENCE, CassettePlayer
from puspa.evidence.eval_harness import EvalReport, EvalTask, score_state

__all__ = [
    "ENVIRONMENTS",
    "HELDOUT_TASK_IDS",
    "check_disjoint",
    "derive_heldout_state",
    "heldout_solver",
    "load_heldout_tasks",
    "score_agentbench",
    "summarize_by_environment",
]

#: The four simulated AgentBench environment shapes in this set.
ENVIRONMENTS = ("db", "kg", "os", "webshop")

#: Jaccard similarity ceiling for a held-out prompt against any dev prompt.
#: Dev prompts are the fixed string "Reference task <name> (replay the
#: recorded cassette).", so real held-out instructions sit far below this.
MAX_PROMPT_JACCARD = 0.5


def _words(text: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", text.lower()))


def derive_heldout_state(cassette: dict) -> dict:
    """Expected final state derived from a held-out recording, by code.

    Deterministic per tool family, the tau-bench final-state check:
    the state the task must end in. Thresholds (max price, minimum
    rating, stale age) come from the recording itself, never from a
    hardcoded per-task table, so the same code derives every task.
    """
    state: dict = {}
    for recording in cassette.get("recording", []):
        tool = recording.get("tool", "")
        output = recording.get("output") or {}
        if not isinstance(output, dict):
            continue
        if tool == "db_query":
            rows = output.get("rows", [])
            state["rows_returned"] = len(rows)
            max_price = output.get("max_price")
            in_stock = [
                row
                for row in rows
                if row.get("stock", 0) > 0
                and "price" in row
                and (max_price is None or row["price"] <= max_price)
            ]
            if in_stock:
                cheapest = min(in_stock, key=lambda row: row["price"])
                state["cheapest_in_stock_product"] = cheapest.get("product_id")
                state["cheapest_in_stock_price"] = cheapest.get("price")
            revenue_rows = [row for row in rows if "revenue" in row and "category" in row]
            if revenue_rows:
                top = max(revenue_rows, key=lambda row: row["revenue"])
                state["top_category"] = top.get("category")
                state["top_revenue"] = top.get("revenue")
        elif tool == "kg_lookup":
            relation = output.get("relation")
            if relation:
                state[relation] = output.get("value")
            state["hops"] = state.get("hops", 0) + 1
        elif tool == "os_list":
            files = output.get("files", [])
            state["files_listed"] = len(files)
            if files:
                largest = max(files, key=lambda item: item.get("size_bytes", 0))
                state["largest_file"] = largest.get("name")
                state["largest_size_bytes"] = largest.get("size_bytes")
            stale_after = output.get("stale_after_days")
            if stale_after is not None:
                stale = [item for item in files if item.get("age_days", 0) > stale_after]
                state["stale_count"] = len(stale)
                state["stale_total_bytes"] = sum(item.get("size_bytes", 0) for item in stale)
        elif tool == "webshop_search":
            products = output.get("products", [])
            min_rating = output.get("min_rating", 0)
            eligible = [item for item in products if item.get("rating", 0) >= min_rating]
            if eligible:
                cheapest = min(eligible, key=lambda item: item["price"])
                state["cheapest_product"] = cheapest.get("product_id")
                state["cheapest_price"] = cheapest.get("price")
        elif tool == "webshop_checkout":
            state["quantity"] = output.get("quantity")
            state["total"] = output.get("total")
            state["order_status"] = output.get("status")
    return state


def heldout_solver(task: EvalTask, player: CassettePlayer, *, user=None) -> dict:
    """Held-out solver: replay the inline cassette, derive final state.

    Same replay shape as ``cassette_solver`` in the harness, only the
    derivation is the held-out one. ``user`` is the simulated user,
    as in the dev solver; the default says nothing.
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
    state.update(derive_heldout_state(cassette))
    return state


def score_agentbench(task: EvalTask, final_state: dict) -> tuple[bool, dict]:
    """Score a held-out final state: per-key check plus partial credit.

    Reuses ``score_state`` for the per-key comparison, then replaces
    the flat 1.0/0.0 score with the fraction of matched keys, so a
    run that reaches part of the final state earns part of the
    score. Passes only when every expected key matches.
    """
    _passed, details = score_state(task, final_state)
    checked = [key for key in details if key != "score"]
    matched = sum(1 for key in checked if details[key]["matched"])
    fraction = (matched / len(checked)) if checked else 0.0
    details["score"] = fraction
    return fraction == 1.0, details


def _heldout_task(task_id: str, environment: str, prompt: str, recording: list[dict]) -> EvalTask:
    """Build one held-out task from its inline recording."""
    cassette = {"task_id": task_id, "name": task_id, "recording": recording}
    return EvalTask(
        task_id=task_id,
        name=task_id,
        prompt=prompt,
        cassette_name=task_id,
        expected_state=derive_heldout_state(cassette),
        policy=[
            "never execute an outbound action without approval",
            "never present a number without an evidence record",
        ],
        environment=environment,
        cassette=cassette,
    )


def _build_tasks() -> list[EvalTask]:
    """The 8 held-out tasks: 2 per environment, all disjoint from dev."""
    return [
        # -- DB: cheapest in-stock product under a price cap ------------
        _heldout_task(
            task_id="agentbench-db-cheapest-headphones",
            environment="db",
            prompt=(
                "From the inventory table, find the cheapest wireless "
                "headphones that are in stock and cost at most "
                "Rp500000. Report its product id and price."
            ),
            recording=[
                {
                    "tool": "db_query",
                    "output": {
                        "max_price": 500000,
                        "rows": [
                            {
                                "product_id": "hp-aurora-01",
                                "name": "Aurora Wireless Headphones",
                                "price": 459000,
                                "stock": 12,
                            },
                            {
                                "product_id": "hp-nimbus-02",
                                "name": "Nimbus Wireless Headphones",
                                "price": 389000,
                                "stock": 0,
                            },
                            {
                                "product_id": "hp-volta-03",
                                "name": "Volta Wireless Headphones",
                                "price": 429000,
                                "stock": 5,
                            },
                            {
                                "product_id": "hp-echo-04",
                                "name": "Echo Wireless Headphones",
                                "price": 549000,
                                "stock": 8,
                            },
                        ],
                    },
                }
            ],
        ),
        # -- DB: top category by total revenue --------------------------
        _heldout_task(
            task_id="agentbench-db-top-revenue-category",
            environment="db",
            prompt=(
                "From the sales summary table, find the product "
                "category with the highest total revenue in November "
                "2026. Report the category and its revenue."
            ),
            recording=[
                {
                    "tool": "db_query",
                    "output": {
                        "rows": [
                            {"category": "audio", "revenue": 48200000},
                            {"category": "cables", "revenue": 21500000},
                            {"category": "keyboards", "revenue": 61400000},
                            {"category": "monitors", "revenue": 39800000},
                        ],
                    },
                }
            ],
        ),
        # -- KG: two-hop brand, then supplier ---------------------------
        _heldout_task(
            task_id="agentbench-kg-supplier-two-hop",
            environment="kg",
            prompt=(
                "In the product knowledge graph, find the brand of "
                "the Aurora Wireless Headphones, then find that "
                "brand's supplier. Report the brand and the supplier."
            ),
            recording=[
                {
                    "tool": "kg_lookup",
                    "output": {
                        "entity": "Aurora Wireless Headphones",
                        "relation": "brand",
                        "value": "Aurora",
                    },
                },
                {
                    "tool": "kg_lookup",
                    "output": {
                        "entity": "Aurora",
                        "relation": "supplier",
                        "value": "Sinar Components Ltd",
                    },
                },
            ],
        ),
        # -- KG: warranty provider and coverage -------------------------
        _heldout_task(
            task_id="agentbench-kg-warranty-provider",
            environment="kg",
            prompt=(
                "In the product knowledge graph, find the warranty "
                "provider of the Volta Mechanical Keyboard and how "
                "many months its coverage lasts. Report the provider "
                "and the coverage in months."
            ),
            recording=[
                {
                    "tool": "kg_lookup",
                    "output": {
                        "entity": "Volta Mechanical Keyboard",
                        "relation": "warranty_provider",
                        "value": "CoverAll Insurance",
                    },
                },
                {
                    "tool": "kg_lookup",
                    "output": {
                        "entity": "Volta Mechanical Keyboard",
                        "relation": "coverage_months",
                        "value": 24,
                    },
                },
            ],
        ),
        # -- OS: largest log file ---------------------------------------
        _heldout_task(
            task_id="agentbench-os-largest-log-file",
            environment="os",
            prompt=(
                "List the files in /var/log/app and report the "
                "largest log file by size, with its size in bytes."
            ),
            recording=[
                {
                    "tool": "os_list",
                    "output": {
                        "directory": "/var/log/app",
                        "files": [
                            {"name": "app.log", "size_bytes": 184320, "age_days": 2},
                            {"name": "app.log.1", "size_bytes": 5242880, "age_days": 9},
                            {"name": "error.log", "size_bytes": 983040, "age_days": 1},
                            {"name": "access.log", "size_bytes": 2097152, "age_days": 3},
                        ],
                    },
                }
            ],
        ),
        # -- OS: stale cache files and their total size -----------------
        _heldout_task(
            task_id="agentbench-os-stale-cache-total",
            environment="os",
            prompt=(
                "List the files in /tmp/cache and report how many "
                "are older than 30 days and their total size in "
                "bytes."
            ),
            recording=[
                {
                    "tool": "os_list",
                    "output": {
                        "directory": "/tmp/cache",
                        "stale_after_days": 30,
                        "files": [
                            {"name": "thumb-01.bin", "size_bytes": 409600, "age_days": 45},
                            {"name": "thumb-02.bin", "size_bytes": 204800, "age_days": 12},
                            {"name": "render-77.tmp", "size_bytes": 1048576, "age_days": 61},
                            {"name": "session-a.json", "size_bytes": 8192, "age_days": 3},
                            {"name": "build-19.zip", "size_bytes": 3145728, "age_days": 90},
                        ],
                    },
                }
            ],
        ),
        # -- WebShop: cheapest rated cable, buy 2 -----------------------
        _heldout_task(
            task_id="agentbench-webshop-cheapest-rated-cable",
            environment="webshop",
            prompt=(
                "In the simulated web shop, find the cheapest USB-C "
                "cable with a rating of at least 4.5, buy 2 units, "
                "and report the product id, unit price, quantity, "
                "and total. The total must stay under Rp200000."
            ),
            recording=[
                {
                    "tool": "webshop_search",
                    "output": {
                        "query": "USB-C cable",
                        "min_rating": 4.5,
                        "products": [
                            {"product_id": "cable-volt-2m", "price": 89000, "rating": 4.7},
                            {"product_id": "cable-nova-1m", "price": 65000, "rating": 4.3},
                            {"product_id": "cable-apex-2m", "price": 99000, "rating": 4.8},
                            {"product_id": "cable-base-1m", "price": 45000, "rating": 4.6},
                        ],
                    },
                },
                {
                    "tool": "webshop_checkout",
                    "output": {
                        "order_id": "ord-1001",
                        "product_id": "cable-base-1m",
                        "quantity": 2,
                        "total": 90000,
                        "status": "confirmed",
                    },
                },
            ],
        ),
        # -- WebShop: cheapest rated keyboard, buy 1 --------------------
        _heldout_task(
            task_id="agentbench-webshop-keyboard-order",
            environment="webshop",
            prompt=(
                "In the simulated web shop, find the cheapest "
                "mechanical keyboard with a rating of at least 4.0, "
                "buy 1 unit, and report the product id, unit price, "
                "quantity, total, and order status."
            ),
            recording=[
                {
                    "tool": "webshop_search",
                    "output": {
                        "query": "mechanical keyboard",
                        "min_rating": 4.0,
                        "products": [
                            {"product_id": "kbd-volta-87", "price": 749000, "rating": 4.6},
                            {"product_id": "kbd-nimbus-68", "price": 599000, "rating": 4.2},
                            {"product_id": "kbd-echo-100", "price": 899000, "rating": 4.9},
                        ],
                    },
                },
                {
                    "tool": "webshop_checkout",
                    "output": {
                        "order_id": "ord-1002",
                        "product_id": "kbd-nimbus-68",
                        "quantity": 1,
                        "total": 599000,
                        "status": "confirmed",
                    },
                },
            ],
        ),
    ]


#: The held-out task set, built once at import.
HELDOUT_TASKS: list[EvalTask] = _build_tasks()

#: Held-out task ids, for disjointness checks and reporting.
HELDOUT_TASK_IDS: tuple[str, ...] = tuple(task.task_id for task in HELDOUT_TASKS)


def load_heldout_tasks() -> list[EvalTask]:
    """Return fresh copies of the held-out tasks (recordings unshared)."""
    return copy.deepcopy(HELDOUT_TASKS)


@dataclass
class DisjointCheck:
    """Result of ``check_disjoint``: every overlap list must be empty."""

    disjoint: bool
    task_id_overlap: list[str] = field(default_factory=list)
    cassette_overlap: list[str] = field(default_factory=list)
    tool_overlap: list[str] = field(default_factory=list)
    prompt_pairs_over_threshold: list[dict] = field(default_factory=list)
    max_prompt_jaccard: float = 0.0
    heldout_tasks: int = 0
    dev_tasks: int = 0


def check_disjoint(cassette_dir=None) -> DisjointCheck:
    """Prove the held-out set is disjoint from the dev cassettes.

    Four explicit checks, all computed by code:
    1. task ids do not overlap the dev task ids (``eval-<name>``);
    2. cassette names do not overlap the dev cassette names;
    3. tools used by held-out recordings do not appear in any dev
       recording (a held-out task cannot be a replay of a dev tool);
    4. no held-out prompt is a paraphrase of a dev prompt: the
       Jaccard similarity of word sets stays under
       ``MAX_PROMPT_JACCARD`` for every pair.
    """
    from puspa.cassette.cassette import load_cassette
    from puspa.evidence.eval_harness import load_reference_tasks

    dev_tasks = load_reference_tasks(cassette_dir)
    heldout = load_heldout_tasks()
    dev_ids = {task.task_id for task in dev_tasks}
    heldout_ids = {task.task_id for task in heldout}
    dev_cassettes = {task.cassette_name for task in dev_tasks}
    heldout_cassettes = {task.cassette_name for task in heldout}

    dev_tools: set[str] = set()
    for name in TASK_REFERENCE:
        cassette = load_cassette(_dev_cassette_path(cassette_dir, name))
        dev_tools.update(rec.get("tool", "") for rec in cassette.get("recording", []))
    heldout_tools: set[str] = set()
    for task in heldout:
        heldout_cassette = task.cassette
        if heldout_cassette is None:
            continue
        heldout_tools.update(rec.get("tool", "") for rec in heldout_cassette["recording"])

    dev_prompts = [(task.task_id, _words(task.prompt)) for task in dev_tasks]
    over_threshold = []
    max_jaccard = 0.0
    for task in heldout:
        heldout_words = _words(task.prompt)
        for dev_id, dev_words in dev_prompts:
            union = heldout_words | dev_words
            jaccard = (len(heldout_words & dev_words) / len(union)) if union else 0.0
            max_jaccard = max(max_jaccard, jaccard)
            if jaccard >= MAX_PROMPT_JACCARD:
                over_threshold.append(
                    {
                        "heldout_task": task.task_id,
                        "dev_task": dev_id,
                        "jaccard": round(jaccard, 3),
                    }
                )

    overlap_ids = sorted(heldout_ids & dev_ids)
    overlap_cassettes = sorted(heldout_cassettes & dev_cassettes)
    overlap_tools = sorted(heldout_tools & dev_tools)
    return DisjointCheck(
        disjoint=not (overlap_ids or overlap_cassettes or overlap_tools or over_threshold),
        task_id_overlap=overlap_ids,
        cassette_overlap=overlap_cassettes,
        tool_overlap=overlap_tools,
        prompt_pairs_over_threshold=over_threshold,
        max_prompt_jaccard=round(max_jaccard, 3),
        heldout_tasks=len(heldout),
        dev_tasks=len(dev_tasks),
    )


def _dev_cassette_path(cassette_dir, name: str):
    """Path of one dev cassette file."""
    from pathlib import Path

    from puspa.cassette.cassette import CASSETTE_DIR_DEFAULT

    folder = Path(cassette_dir or CASSETTE_DIR_DEFAULT)
    return folder / f"{name}.json"


def summarize_by_environment(report: EvalReport) -> dict:
    """Break a held-out report down per environment.

    Returns ``{environment: {"total": n, "passed": n, "score": x}}``
    plus a ``"heldout"`` entry with the overall held-out score, so a
    caller can report the held-out score per environment without
    ever mixing it into the dev score.
    """
    by_env: dict[str, dict] = {}
    for result in report.results:
        env = result.environment or "unknown"
        entry = by_env.setdefault(env, {"total": 0, "passed": 0})
        entry["total"] += 1
        if result.passed:
            entry["passed"] += 1
    for entry in by_env.values():
        total = entry["total"]
        entry["score"] = (entry["passed"] / total) if total else 0.0
    summary = {
        "heldout": {
            "total": report.total,
            "passed": report.passed,
            "score": report.score,
        }
    }
    summary.update(by_env)
    return summary
