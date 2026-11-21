"""Interactive widgets for the Chat screen (P-14, plan section 12.10).

Chat can show five kinds of interactive widget, all as declarative
JSON payloads attached to a chat message under its ``widget`` key —
the same split as the B7 mockup doc and the P-12 rich cards: data
describes the widget, the renderer decides how to show it. The
difference from a P-12 card is state: a widget responds to the
user's input (typing, sliding, submitting, stepping a simulation)
instead of only being looked at.

- ``calculator`` — number/slider inputs plus a formula; the
  result updates live as the user changes an input. The formula
  is a small safe expression (``+ - * / % **``, parentheses,
  ``min``/``max``/``abs``/``round`` over the input ids) evaluated
  by an ``ast`` whitelist here and mirrored in
  ``apps/pwa/src/state/chatWidgets.ts`` — never ``eval``.
- ``form`` — text/number/select/checkbox fields with required
  flags; submitting validates the answers server-side and posts
  them as a chat message, like the options card sends a label.
- ``comparison`` — a table of rows against named columns
  (numeric columns are sortable and the cheapest row is
  highlighted); the bot builds one from the simulated prices
  with :func:`price_compare_widget`.
- ``diagram`` — nodes and edges, an acyclic flow the user can
  tap through: tapping a node shows its detail.
- ``simulation`` — the deterministic world simulator
  (``puspa.world.simulator.SimulatedWorld``) exposed as a
  widget: prices per store per day, the PLN bill, a day stepper
  and a pay-bill action. The widget is stateless: it carries
  its day and bill state, and every step re-derives the world
  from that state, so a forged widget whose prices do not match
  the simulator is refused instead of trusted.

Widgets ride on the in-memory chat store (``app.state.chat``):
:func:`append_widget_message` appends a ``"puspa"`` message with
its widget, the same list ``POST /api/chat`` appends to. No new
database table and no migration: the simulator is pure
in-memory maths and the widgets live in the chat store.

Honest limit, stated here and in ``docs/chat-widgets.md``:
the widgets are verified by the Python tests plus vitest/jsdom
rendering and interaction tests — this VM has no live browser,
so a tap or a slider drag on a real phone screen is not
verified here. The simulation widget shows only what the
deterministic simulator actually computes (fixed daily price
drifts, one PLN bill); it is not live market data.
"""

from __future__ import annotations

import ast
import math
import time

from ..world.simulator import (
    BILL_ID,
    EPISODE_START_DAY,
    PRODUCT_ID,
    STORES,
    SimulatedWorld,
)

__all__ = [
    "append_widget_message",
    "best_row",
    "build_widget",
    "calculator_widget",
    "comparison_widget",
    "compute_calculator",
    "diagram_widget",
    "evaluate_formula",
    "form_widget",
    "format_widget_value",
    "price_compare_widget",
    "simulate_step",
    "simulation_widget",
    "validate_form_submission",
    "validate_widget",
]

MAX_FORMULA_CHARS = 500
MAX_INPUTS = 10
MAX_FIELDS = 20
MAX_COLUMNS = 6
MAX_ROWS = 20
MAX_NODES = 40
MAX_EDGES = 80
MAX_SIMULATION_DAY = 30
SIMULATION_HISTORY_DAYS = 16

_FORM_FIELD_KINDS = ("text", "number", "select", "checkbox")
_INPUT_KINDS = ("number", "slider")
_VALUE_FORMATS = ("number", "rupiah", "percent")
_SIMULATION_ACTIONS = ("advance_day", "pay_bill", "buy_from_store")


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def _require_string(value, field: str) -> str:
    _require(bool(isinstance(value, str) and value.strip()), f"{field} must be a non-empty string")
    return value


def _optional_string(value, field: str) -> str | None:
    if value is None:
        return None
    _require(isinstance(value, str), f"{field} must be a string")
    return value


def _require_number(value, field: str) -> float:
    _require(
        isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value),
        f"{field} must be a finite number",
    )
    return float(value)


def _require_int(value, field: str) -> int:
    _require(isinstance(value, int) and not isinstance(value, bool), f"{field} must be an integer")
    return value


def _require_list(value, field: str) -> list:
    _require(isinstance(value, list), f"{field} must be a list")
    return value


def _require_dict(value, field: str) -> dict:
    _require(isinstance(value, dict), f"{field} must be an object")
    return value


def _require_bool(value, field: str) -> bool:
    _require(isinstance(value, bool), f"{field} must be a boolean")
    return value


def format_widget_value(value: float, format: str) -> str:
    """Render a computed number for display (``number`` / ``rupiah`` / ``percent``)."""
    _require(format in _VALUE_FORMATS, f"value format must be one of {_VALUE_FORMATS}")
    if format == "rupiah":
        return f"Rp{value:,.0f}".replace(",", ".")
    if format == "percent":
        return f"{value:.1f}%"
    if value == int(value):
        return str(int(value))
    return f"{value:.2f}"


# -- safe formula evaluation ---------------------------------------------


_ALLOWED_BINOPS = (ast.Add, ast.Sub, ast.Mult, ast.Div, ast.Mod, ast.Pow)
_ALLOWED_UNARYOPS = (ast.USub, ast.UAdd)
_ALLOWED_CALLS = ("min", "max", "abs", "round")


def _eval_node(node: ast.AST, values: dict[str, float]) -> float:
    """Evaluate one AST node against the input values.

    Only whitelisted node types are evaluated; anything else
    (attribute access, subscripts, imports, comprehensions) is
    refused, so a formula can never reach outside its inputs.
    """
    if isinstance(node, ast.Expression):
        return _eval_node(node.body, values)
    if isinstance(node, ast.Constant):
        if not isinstance(node.value, (int, float)) or isinstance(node.value, bool):
            raise ValueError("formula constants must be numbers")
        return float(node.value)
    if isinstance(node, ast.Name):
        _require(node.id in values, f"formula uses unknown input {node.id!r}")
        return values[node.id]
    if isinstance(node, ast.BinOp):
        _require(
            isinstance(node.op, _ALLOWED_BINOPS),
            f"formula operator {type(node.op).__name__} is not allowed",
        )
        left = _eval_node(node.left, values)
        right = _eval_node(node.right, values)
        if isinstance(node.op, ast.Add):
            return left + right
        if isinstance(node.op, ast.Sub):
            return left - right
        if isinstance(node.op, ast.Mult):
            return left * right
        if isinstance(node.op, ast.Div):
            _require(right != 0, "formula divides by zero")
            return left / right
        if isinstance(node.op, ast.Mod):
            _require(right != 0, "formula divides by zero")
            return left % right
        try:
            return left**right
        except OverflowError as exc:
            # A power too large to represent is not a finite
            # result; translate it to the formula error.
            raise ValueError("formula result must be finite") from exc
    if isinstance(node, ast.UnaryOp):
        _require(
            isinstance(node.op, _ALLOWED_UNARYOPS),
            f"formula operator {type(node.op).__name__} is not allowed",
        )
        operand = _eval_node(node.operand, values)
        return -operand if isinstance(node.op, ast.USub) else operand
    if isinstance(node, ast.Call):
        if not isinstance(node.func, ast.Name) or node.func.id not in _ALLOWED_CALLS:
            raise ValueError("formula calls must be min, max, abs, or round")
        _require(not node.keywords, "formula calls take no keyword arguments")
        _require(1 <= len(node.args) <= 2, "formula calls take 1 or 2 arguments")
        args = [_eval_node(arg, values) for arg in node.args]
        if node.func.id == "min":
            return min(args)
        if node.func.id == "max":
            return max(args)
        if node.func.id == "abs":
            _require(len(args) == 1, "abs takes exactly 1 argument")
            return abs(args[0])
        _require(len(args) <= 2, "round takes at most 2 arguments")
        return float(round(args[0], int(args[1]) if len(args) == 2 else 0))
    raise ValueError(f"formula node {type(node).__name__} is not allowed")


def evaluate_formula(formula: str, values: dict[str, float]) -> float:
    """Evaluate a calculator formula against the input values.

    The formula is parsed with :mod:`ast` and evaluated node by
    node through the whitelist in :func:`_eval_node` — it is
    data, never code, so a formula cannot call out to anything.
    """
    _require(
        bool(isinstance(formula, str) and formula.strip()), "formula must be a non-empty string"
    )
    _require(len(formula) <= MAX_FORMULA_CHARS, "formula exceeds the 500-character cap")
    try:
        tree = ast.parse(formula, mode="eval")
    except SyntaxError as exc:
        raise ValueError(f"formula is not a valid expression: {exc}") from exc
    result = _eval_node(tree, values)
    try:
        finite = math.isfinite(result)
    except OverflowError:
        # A huge integer power (e.g. 10 ** 9999) overflows the
        # float conversion: it is not a finite result either.
        finite = False
    _require(finite, "formula result must be finite")
    return result


# -- calculator ----------------------------------------------------------


def calculator_widget(
    *,
    title: str,
    inputs: list[dict],
    formula: str,
    result_label: str | None = None,
    value_format: str = "number",
) -> dict:
    """One calculator widget: inputs plus a formula over them.

    Each input is ``{"id", "label", "input_kind",
    "value", "min", "max", "step"}``; ``input_kind`` is
    ``"number"`` (a typed field) or ``"slider"`` (a range
    control). The formula may only name the input ids, and the
    default values must already satisfy it, so a widget that
    cannot compute is refused at build time instead of showing
    a broken result to the user.
    """
    title = _require_string(title, "calculator title")
    _require(
        bool(isinstance(inputs, list) and inputs), "calculator inputs must be a non-empty list"
    )
    _require(len(inputs) <= MAX_INPUTS, f"calculator holds at most {MAX_INPUTS} inputs")
    _require(value_format in _VALUE_FORMATS, f"value format must be one of {_VALUE_FORMATS}")
    cleaned = []
    defaults: dict[str, float] = {}
    seen: set[str] = set()
    for entry in inputs:
        _require(isinstance(entry, dict), "each calculator input must be an object")
        input_id = _require_string(entry.get("id"), "input id")
        _require(input_id not in seen, f"duplicate input id {input_id!r}")
        seen.add(input_id)
        label = _require_string(entry.get("label"), "input label")
        kind = entry.get("input_kind") or "number"
        _require(kind in _INPUT_KINDS, f"input kind must be one of {_INPUT_KINDS}")
        minimum = _require_number(entry.get("min"), "input min")
        maximum = _require_number(entry.get("max"), "input max")
        _require(minimum <= maximum, "input min must not exceed input max")
        step = entry.get("step")
        step = _require_number(step, "input step") if step is not None else 1.0
        _require(step > 0, "input step must be positive")
        value = _require_number(entry.get("value"), "input value")
        _require(minimum <= value <= maximum, "input value must be within min and max")
        cleaned.append(
            {
                "id": input_id,
                "label": label,
                "input_kind": kind,
                "value": value,
                "min": minimum,
                "max": maximum,
                "step": step,
            }
        )
        defaults[input_id] = value
    formula = _require_string(formula, "calculator formula")
    evaluate_formula(formula, defaults)
    return {
        "kind": "calculator",
        "title": title,
        "inputs": cleaned,
        "formula": formula,
        "result_label": result_label or "Result",
        "value_format": value_format,
    }


def compute_calculator(widget: dict, values: dict) -> float:
    """Compute a calculator widget's result for the given values.

    Every input id must be present, numeric, and within its
    declared range; an out-of-range value is refused instead of
    clamped silently, so the client and the server can never
    disagree about what a slider position means.
    """
    _require(
        isinstance(widget, dict) and widget.get("kind") == "calculator",
        "widget must be a calculator",
    )
    cleaned = calculator_widget(
        title=_require_string(widget.get("title"), "calculator title"),
        inputs=_require_list(widget.get("inputs"), "calculator inputs"),
        formula=_require_string(widget.get("formula"), "calculator formula"),
        result_label=widget.get("result_label"),
        value_format=widget.get("value_format") or "number",
    )
    _require(isinstance(values, dict), "values must be an object")
    supplied: dict[str, float] = {}
    for entry in cleaned["inputs"]:
        _require(entry["id"] in values, f"missing value for input {entry['id']!r}")
        value = _require_number(values[entry["id"]], f"value for {entry['id']}")
        _require(
            entry["min"] <= value <= entry["max"],
            f"value for {entry['id']!r} must be within min and max",
        )
        supplied[entry["id"]] = value
    return evaluate_formula(cleaned["formula"], supplied)


# -- form ----------------------------------------------------------------


def form_widget(
    *,
    title: str,
    fields: list[dict],
    submit_label: str | None = None,
) -> dict:
    """One form widget: labelled fields the user fills in.

    Each field is ``{"id", "label", "field_kind", "required",
    "options", "placeholder"}``; ``field_kind`` is ``"text"``,
    ``"number"``, ``"select"`` (needs 2..20 ``options``) or
    ``"checkbox"``. Field ids must be unique: the submission
    answers are keyed by id, and a duplicate would make two
    answers indistinguishable.
    """
    title = _require_string(title, "form title")
    _require(bool(isinstance(fields, list) and fields), "form fields must be a non-empty list")
    _require(len(fields) <= MAX_FIELDS, f"form holds at most {MAX_FIELDS} fields")
    cleaned = []
    seen: set[str] = set()
    for entry in fields:
        _require(isinstance(entry, dict), "each form field must be an object")
        field_id = _require_string(entry.get("id"), "field id")
        _require(field_id not in seen, f"duplicate field id {field_id!r}")
        seen.add(field_id)
        label = _require_string(entry.get("label"), "field label")
        kind = entry.get("field_kind") or "text"
        _require(kind in _FORM_FIELD_KINDS, f"field kind must be one of {_FORM_FIELD_KINDS}")
        required = entry.get("required", False)
        _require(isinstance(required, bool), "field required must be a boolean")
        cleaned_entry: dict = {
            "id": field_id,
            "label": label,
            "field_kind": kind,
            "required": required,
        }
        placeholder = _optional_string(entry.get("placeholder"), "field placeholder")
        if placeholder is not None:
            cleaned_entry["placeholder"] = placeholder
        if kind == "select":
            options = entry.get("options")
            if not isinstance(options, list) or not 2 <= len(options) <= 20:
                raise ValueError("select field needs 2 to 20 options")
            cleaned_options = [_require_string(o, "select option") for o in options]
            _require(
                len(set(cleaned_options)) == len(cleaned_options),
                "select options must be unique",
            )
            cleaned_entry["options"] = cleaned_options
        cleaned.append(cleaned_entry)
    return {
        "kind": "form",
        "title": title,
        "fields": cleaned,
        "submit_label": submit_label or "Submit",
    }


def validate_form_submission(widget: dict, answers: dict) -> dict:
    """Validate a form submission; return the cleaned answers.

    Required fields must be answered; a number answer must be a
    finite number, a select answer must be one of the field's
    options, and a checkbox answer must be a boolean. Unknown
    field ids are refused, so a submission can only answer the
    fields the widget actually shows.
    """
    _require(isinstance(widget, dict) and widget.get("kind") == "form", "widget must be a form")
    cleaned = form_widget(
        title=_require_string(widget.get("title"), "form title"),
        fields=_require_list(widget.get("fields"), "form fields"),
        submit_label=widget.get("submit_label"),
    )
    _require(isinstance(answers, dict), "answers must be an object")
    known = {field["id"] for field in cleaned["fields"]}
    for answer_id in answers:
        _require(answer_id in known, f"unknown field id {answer_id!r}")
    result: dict = {}
    for field in cleaned["fields"]:
        answer = answers.get(field["id"])
        if answer is None:
            _require(not field["required"], f"field {field['label']!r} is required")
            result[field["id"]] = None
            continue
        if field["field_kind"] == "text":
            _require(
                bool(isinstance(answer, str) and answer.strip()),
                f"field {field['label']!r} must be a non-empty string",
            )
            result[field["id"]] = answer
        elif field["field_kind"] == "number":
            result[field["id"]] = _require_number(answer, f"field {field['label']!r}")
        elif field["field_kind"] == "select":
            _require(
                isinstance(answer, str) and answer in field["options"],
                f"field {field['label']!r} must be one of its options",
            )
            result[field["id"]] = answer
        else:
            _require(
                isinstance(answer, bool),
                f"field {field['label']!r} must be a boolean",
            )
            result[field["id"]] = answer
    return result


# -- comparison ----------------------------------------------------------


def comparison_widget(
    *,
    title: str,
    columns: list[dict],
    rows: list[dict],
) -> dict:
    """One comparison widget: rows compared across named columns.

    Each column is ``{"id", "label", "numeric"}`` (numeric
    columns hold numbers, are sortable, and get a cheapest-row
    highlight); each row is ``{"id", "label", "values"}`` with
    one value per column. A row missing a column would render a
    blank cell, so it is refused instead of shown.
    """
    title = _require_string(title, "comparison title")
    _require(
        bool(isinstance(columns, list) and 2 <= len(columns) <= MAX_COLUMNS),
        f"comparison needs 2 to {MAX_COLUMNS} columns",
    )
    cleaned_columns = []
    seen: set[str] = set()
    numeric: dict[str, bool] = {}
    for entry in columns:
        _require(isinstance(entry, dict), "each comparison column must be an object")
        column_id = _require_string(entry.get("id"), "column id")
        _require(column_id not in seen, f"duplicate column id {column_id!r}")
        seen.add(column_id)
        label = _require_string(entry.get("label"), "column label")
        is_numeric = entry.get("numeric", False)
        _require(isinstance(is_numeric, bool), "column numeric must be a boolean")
        numeric[column_id] = is_numeric
        cleaned_columns.append({"id": column_id, "label": label, "numeric": is_numeric})
    _require(bool(isinstance(rows, list) and rows), "comparison rows must be a non-empty list")
    _require(len(rows) <= MAX_ROWS, f"comparison holds at most {MAX_ROWS} rows")
    cleaned_rows = []
    seen_rows: set[str] = set()
    for entry in rows:
        _require(isinstance(entry, dict), "each comparison row must be an object")
        row_id = _require_string(entry.get("id"), "row id")
        _require(row_id not in seen_rows, f"duplicate row id {row_id!r}")
        seen_rows.add(row_id)
        label = _require_string(entry.get("label"), "row label")
        values = _require_dict(entry.get("values"), "row values")
        cleaned_values: dict = {}
        for column_id in seen:
            _require(column_id in values, f"row {row_id!r} is missing column {column_id!r}")
            value = values[column_id]
            if numeric[column_id]:
                cleaned_values[column_id] = _require_number(
                    value, f"row {row_id!r} column {column_id!r}"
                )
            else:
                cleaned_values[column_id] = _require_string(
                    value, f"row {row_id!r} column {column_id!r}"
                )
        cleaned_rows.append({"id": row_id, "label": label, "values": cleaned_values})
    return {
        "kind": "comparison",
        "title": title,
        "columns": cleaned_columns,
        "rows": cleaned_rows,
    }


def best_row(widget: dict, column_id: str) -> dict:
    """The row with the smallest value in one numeric column.

    The client highlights this row as the cheapest / best; the
    server computes it so both sides agree. A text column has
    no ordering, so asking for its best row is refused.
    """
    _require(
        isinstance(widget, dict) and widget.get("kind") == "comparison",
        "widget must be a comparison",
    )
    cleaned = comparison_widget(
        title=_require_string(widget.get("title"), "comparison title"),
        columns=_require_list(widget.get("columns"), "comparison columns"),
        rows=_require_list(widget.get("rows"), "comparison rows"),
    )
    column = next((c for c in cleaned["columns"] if c["id"] == column_id), None)
    if column is None:
        raise ValueError(f"unknown column {column_id!r}")
    _require(column["numeric"], f"column {column_id!r} is not numeric")
    return min(cleaned["rows"], key=lambda row: row["values"][column_id])


# -- diagram -------------------------------------------------------------


def diagram_widget(
    *,
    title: str,
    nodes: list[dict],
    edges: list[dict],
) -> dict:
    """One diagram widget: nodes and edges, an acyclic flow.

    Each node is ``{"id", "label", "detail"}`` (detail shown
    when the user taps the node); each edge is ``{"from", "to"}``
    between existing nodes. Cycles are refused: a flow the user
    taps through must have a direction, so the widget is a DAG.
    """
    title = _require_string(title, "diagram title")
    _require(bool(isinstance(nodes, list) and nodes), "diagram nodes must be a non-empty list")
    _require(len(nodes) <= MAX_NODES, f"diagram holds at most {MAX_NODES} nodes")
    cleaned_nodes = []
    seen: set[str] = set()
    for entry in nodes:
        _require(isinstance(entry, dict), "each diagram node must be an object")
        node_id = _require_string(entry.get("id"), "node id")
        _require(node_id not in seen, f"duplicate node id {node_id!r}")
        seen.add(node_id)
        label = _require_string(entry.get("label"), "node label")
        cleaned_entry: dict = {"id": node_id, "label": label}
        detail = _optional_string(entry.get("detail"), "node detail")
        if detail is not None:
            cleaned_entry["detail"] = detail
        cleaned_nodes.append(cleaned_entry)
    _require(isinstance(edges, list), "diagram edges must be a list")
    _require(len(edges) <= MAX_EDGES, f"diagram holds at most {MAX_EDGES} edges")
    cleaned_edges = []
    children: dict[str, list[str]] = {node_id: [] for node_id in seen}
    indegree: dict[str, int] = {node_id: 0 for node_id in seen}
    for entry in edges:
        _require(isinstance(entry, dict), "each diagram edge must be an object")
        source = _require_string(entry.get("from"), "edge from")
        target = _require_string(entry.get("to"), "edge to")
        _require(source in seen, f"edge starts at unknown node {source!r}")
        _require(target in seen, f"edge ends at unknown node {target!r}")
        _require(source != target, "an edge cannot start and end at the same node")
        cleaned_edges.append({"from": source, "to": target})
        children[source].append(target)
        indegree[target] += 1
    # Kahn's algorithm: a cycle leaves nodes with indegree > 0.
    queue = [node_id for node_id, degree in indegree.items() if degree == 0]
    visited = 0
    while queue:
        current = queue.pop()
        visited += 1
        for child in children[current]:
            indegree[child] -= 1
            if indegree[child] == 0:
                queue.append(child)
    _require(visited == len(seen), "diagram edges must not form a cycle")
    return {
        "kind": "diagram",
        "title": title,
        "nodes": cleaned_nodes,
        "edges": cleaned_edges,
    }


# -- simulation ----------------------------------------------------------


def _world_at(day: int, bill_paid: bool) -> SimulatedWorld:
    world = SimulatedWorld(start_day=EPISODE_START_DAY)
    world.day = day
    world.bill_paid = bill_paid
    return world


def _simulation_state(world: SimulatedWorld) -> dict:
    """The widget state derived from one simulator world.

    ``prices`` are the current day's prices per store, ``bill``
    is the bill observation, and ``history`` is the price per
    store for every day from the episode start to the current
    day (at most 16 entries), so the widget can draw the price
    chart without asking the server again.
    """
    prices = {store: world.price(PRODUCT_ID, store) for store in STORES}
    history = []
    for day in range(EPISODE_START_DAY, world.day + 1):
        probe = _world_at(day, world.bill_paid)
        history.append(
            {"day": day, "prices": {store: probe.price(PRODUCT_ID, store) for store in STORES}}
        )
    return {
        "prices": prices,
        "bill": world.observe_bill(),
        "history": history[-SIMULATION_HISTORY_DAYS:],
    }


def simulation_widget(
    *,
    title: str | None = None,
    day: int = EPISODE_START_DAY,
    bill_paid: bool = False,
    product_id: str = PRODUCT_ID,
) -> dict:
    """One simulation widget from the deterministic world simulator.

    The widget carries the day and the bill state; its prices
    and bill are computed by :class:`SimulatedWorld`, never
    typed in by hand. The only product the simulator knows is
    the watched phone, so any other ``product_id`` is refused.
    """
    _require(product_id == PRODUCT_ID, f"simulator only knows product {PRODUCT_ID!r}")
    _require(
        isinstance(day, int) and not isinstance(day, bool),
        "simulation day must be an integer",
    )
    _require(
        EPISODE_START_DAY <= day <= MAX_SIMULATION_DAY,
        f"simulation day must be between {EPISODE_START_DAY} and {MAX_SIMULATION_DAY}",
    )
    _require(isinstance(bill_paid, bool), "bill_paid must be a boolean")
    world = _world_at(day, bill_paid)
    state = _simulation_state(world)
    return {
        "kind": "simulation",
        "title": title or "Price + bill simulation",
        "product_id": product_id,
        "stores": list(STORES),
        "start_day": EPISODE_START_DAY,
        "day": day,
        "bill_id": BILL_ID,
        "bill_paid": bill_paid,
        "prices": state["prices"],
        "bill": state["bill"],
        "history": state["history"],
    }


def _validate_simulation(widget: dict) -> dict:
    """Re-derive a simulation widget from the simulator.

    The day and bill state are the widget's own; everything
    else (prices, bill, history) must match what the simulator
    computes for that state, so a hand-forged widget claiming
    prices the simulator never produced is refused.
    """
    day = widget.get("day")
    bill_paid = widget.get("bill_paid")
    if not isinstance(day, int) or isinstance(day, bool):
        raise ValueError("simulation day must be an integer")
    _require(
        EPISODE_START_DAY <= day <= MAX_SIMULATION_DAY,
        f"simulation day must be between {EPISODE_START_DAY} and {MAX_SIMULATION_DAY}",
    )
    if not isinstance(bill_paid, bool):
        raise ValueError("bill_paid must be a boolean")
    product_id = widget.get("product_id")
    if not isinstance(product_id, str):
        raise ValueError(f"simulator only knows product {PRODUCT_ID!r}")
    rebuilt = simulation_widget(
        title=widget.get("title"),
        day=day,
        bill_paid=bill_paid,
        product_id=product_id,
    )
    _require(
        widget.get("stores") == rebuilt["stores"]
        and widget.get("start_day") == rebuilt["start_day"]
        and widget.get("bill_id") == rebuilt["bill_id"],
        "simulation widget header does not match the simulator",
    )
    _require(
        widget.get("prices") == rebuilt["prices"], "simulation prices do not match the simulator"
    )
    _require(widget.get("bill") == rebuilt["bill"], "simulation bill does not match the simulator")
    _require(
        widget.get("history") == rebuilt["history"],
        "simulation history does not match the simulator",
    )
    return rebuilt


def simulate_step(widget: dict, action: dict) -> tuple[dict, dict]:
    """Apply one user action to a simulation widget.

    The widget is stateless, so the step rebuilds the world at
    the widget's day and bill state, applies the action through
    :meth:`SimulatedWorld.act`, and returns the rebuilt widget
    plus the outcome. ``advance_day`` moves one day forward
    (never past day 30); ``pay_bill`` pays the PLN bill (refused
    when already paid); ``buy_from_store`` buys the watched
    phone at the current simulated price (the widget itself does
    not change — only the outcome carries the price).
    """
    _require(
        isinstance(widget, dict) and widget.get("kind") == "simulation",
        "widget must be a simulation",
    )
    cleaned = _validate_simulation(widget)
    _require(isinstance(action, dict), "simulation action must be an object")
    action_type = action.get("type")
    _require(action_type in _SIMULATION_ACTIONS, f"unknown simulation action {action_type!r}")
    world = _world_at(cleaned["day"], cleaned["bill_paid"])
    if action_type == "advance_day":
        _require(
            cleaned["day"] < MAX_SIMULATION_DAY,
            f"simulation cannot advance past day {MAX_SIMULATION_DAY}",
        )
        outcome = world.act({"type": "wait_day"})
        return simulation_widget(day=world.day, bill_paid=world.bill_paid), outcome
    if action_type == "pay_bill":
        outcome = world.act({"type": "pay_bill", "bill_id": BILL_ID})
        _require(bool(outcome.get("success")), "bill is already paid")
        return simulation_widget(day=world.day, bill_paid=world.bill_paid), outcome
    store = action.get("store")
    _require(store in STORES, f"unknown store {store!r}")
    outcome = world.act({"type": "buy_from_store", "product_id": PRODUCT_ID, "store": store})
    return cleaned, outcome


def price_compare_widget(*, product_id: str = PRODUCT_ID, day: int = EPISODE_START_DAY) -> dict:
    """A comparison widget built from the simulator's prices.

    One row per store with the simulated price that day — the
    bot's one-call way to show "where is the phone cheapest
    right now" without hand-typing prices that could disagree
    with the simulator. The prices are simulated, not live
    market data; the widget title says so.
    """
    _require(product_id == PRODUCT_ID, f"simulator only knows product {PRODUCT_ID!r}")
    _require(
        isinstance(day, int) and not isinstance(day, bool),
        "comparison day must be an integer",
    )
    _require(
        EPISODE_START_DAY <= day <= MAX_SIMULATION_DAY,
        f"comparison day must be between {EPISODE_START_DAY} and {MAX_SIMULATION_DAY}",
    )
    world = _world_at(day, bill_paid=False)
    return comparison_widget(
        title=f"Simulated phone prices — day {day} (simulator, not live prices)",
        columns=[
            {"id": "price", "label": "Price (Rp)", "numeric": True},
            {"id": "trend", "label": "Daily trend", "numeric": False},
        ],
        rows=[
            {
                "id": store,
                "label": store,
                "values": {
                    "price": world.price(PRODUCT_ID, store),
                    "trend": {
                        "Tokopedia": "Falls Rp2,000/day",
                        "Shopee": "Rises Rp3,000/day",
                        "Blibli": "No change",
                    }[store],
                },
            }
            for store in STORES
        ],
    )


# -- validation + chat seam ----------------------------------------------


def validate_widget(widget: dict) -> dict:
    """Validate one widget payload and return its cleaned copy.

    The ``kind`` field discriminates: an unknown kind is refused
    (400 at the API), and each kind is rebuilt through its own
    builder, so a widget that passes here is exactly the shape
    the renderer expects — no extra keys survive.
    """
    _require(isinstance(widget, dict), "widget must be a JSON object")
    kind = widget.get("kind")
    if kind == "calculator":
        return calculator_widget(
            title=_require_string(widget.get("title"), "calculator title"),
            inputs=_require_list(widget.get("inputs"), "calculator inputs"),
            formula=_require_string(widget.get("formula"), "calculator formula"),
            result_label=widget.get("result_label"),
            value_format=widget.get("value_format") or "number",
        )
    if kind == "form":
        return form_widget(
            title=_require_string(widget.get("title"), "form title"),
            fields=_require_list(widget.get("fields"), "form fields"),
            submit_label=widget.get("submit_label"),
        )
    if kind == "comparison":
        return comparison_widget(
            title=_require_string(widget.get("title"), "comparison title"),
            columns=_require_list(widget.get("columns"), "comparison columns"),
            rows=_require_list(widget.get("rows"), "comparison rows"),
        )
    if kind == "diagram":
        return diagram_widget(
            title=_require_string(widget.get("title"), "diagram title"),
            nodes=_require_list(widget.get("nodes"), "diagram nodes"),
            edges=_require_list(widget.get("edges"), "diagram edges"),
        )
    if kind == "simulation":
        return _validate_simulation(widget)
    raise ValueError(f"unknown widget kind {kind!r}")


def build_widget(kind: str, **fields) -> dict:
    """Build one widget by kind (``calculator`` / ``form`` / ``comparison`` / ``diagram`` / ``simulation``)."""
    return validate_widget({"kind": kind, **fields})


def append_widget_message(
    chat_state: dict,
    user_id: str,
    text: str,
    widget: dict,
    *,
    sender: str = "puspa",
    created_at: float | None = None,
) -> dict:
    """Append a message carrying a widget to the in-memory chat store.

    The message is the same shape ``POST /api/chat`` appends
    (``sender`` / ``text`` / ``created_at``) plus the validated
    ``widget`` payload. The agent's turn loop calls this to put a
    widget in front of the user; the HTTP layer in
    ``chat_widgets_api`` is the same seam over HTTP.
    """
    _require(isinstance(chat_state, dict), "chat_state must be a dict")
    _require_string(user_id, "user_id")
    _require_string(text, "message text")
    cleaned = validate_widget(widget)
    message = {
        "sender": sender,
        "text": text,
        "created_at": created_at if created_at is not None else time.time(),
        "widget": cleaned,
    }
    chat_state.setdefault(user_id, []).append(message)
    return message
