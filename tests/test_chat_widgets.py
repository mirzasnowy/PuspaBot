"""Tests for the P-14 interactive widgets in Chat (``puspa.pwa.chat_widgets``).

Pure unit tests (``unit`` marker: no database, no network):
the five widget builders and their validation, the safe
formula evaluator, form submission validation, the cheapest
row, the diagram cycle check, the simulation steps against
the real deterministic simulator, and the in-memory chat seam.
The HTTP layer lives in ``tests/test_chat_widgets_api.py``.

The simulation prices asserted here are the simulator's own
constants (Tokopedia falls Rp2,000/day from Rp5,250,000,
Shopee rises Rp3,000/day from Rp5,100,000, Blibli never
moves; one PLN bill of Rp150,000 due day 20): the widget is
checked against the simulator, not against hand-typed
numbers. The honest limit (no live browser in this VM to
prove a tap or a slider drag on a real screen) is recorded
in ``docs/chat-widgets.md``.
"""

from __future__ import annotations

import pytest

from puspa.pwa.chat_widgets import (
    append_widget_message,
    best_row,
    build_widget,
    calculator_widget,
    comparison_widget,
    compute_calculator,
    diagram_widget,
    evaluate_formula,
    form_widget,
    format_widget_value,
    price_compare_widget,
    simulate_step,
    simulation_widget,
    validate_form_submission,
    validate_widget,
)

pytestmark = pytest.mark.unit


def _calculator(**overrides):
    payload = {
        "title": "Total belanja",
        "inputs": [
            {
                "id": "price",
                "label": "Harga",
                "input_kind": "number",
                "value": 50000,
                "min": 0,
                "max": 1000000,
                "step": 1000,
            },
            {
                "id": "qty",
                "label": "Jumlah",
                "input_kind": "slider",
                "value": 2,
                "min": 1,
                "max": 10,
                "step": 1,
            },
        ],
        "formula": "price * qty",
        "result_label": "Total",
        "value_format": "rupiah",
    }
    payload.update(overrides)
    return payload


def _form(**overrides):
    payload = {
        "title": "Data pengiriman",
        "fields": [
            {"id": "name", "label": "Nama", "field_kind": "text", "required": True},
            {"id": "qty", "label": "Jumlah", "field_kind": "number", "required": True},
            {
                "id": "city",
                "label": "Kota",
                "field_kind": "select",
                "required": False,
                "options": ["Jakarta", "Karawang", "Bandung"],
            },
            {"id": "gift", "label": "Bungkus kado", "field_kind": "checkbox", "required": False},
        ],
        "submit_label": "Kirim",
    }
    payload.update(overrides)
    return payload


def _comparison(**overrides):
    payload = {
        "title": "Harga HP",
        "columns": [
            {"id": "price", "label": "Harga (Rp)", "numeric": True},
            {"id": "note", "label": "Catatan", "numeric": False},
        ],
        "rows": [
            {"id": "a", "label": "Toko A", "values": {"price": 5200000, "note": "Gratis ongkir"}},
            {"id": "b", "label": "Toko B", "values": {"price": 5100000, "note": "Stok 2"}},
        ],
    }
    payload.update(overrides)
    return payload


def _diagram(**overrides):
    payload = {
        "title": "Cara beli",
        "nodes": [
            {"id": "pick", "label": "Pilih toko", "detail": "Bandingkan harga dulu."},
            {"id": "pay", "label": "Bayar", "detail": "Transfer atau COD."},
            {"id": "done", "label": "Selesai"},
        ],
        "edges": [{"from": "pick", "to": "pay"}, {"from": "pay", "to": "done"}],
    }
    payload.update(overrides)
    return payload


# -- formula evaluation --------------------------------------------------


def test_evaluate_formula_arithmetic_and_functions():
    values = {"a": 10, "b": 3}
    assert evaluate_formula("a + b * 2", values) == 16
    assert evaluate_formula("(a - b) / 2", values) == 3.5
    assert evaluate_formula("a % b", values) == 1
    assert evaluate_formula("a ** 2", values) == 100
    assert evaluate_formula("-a + abs(-b)", values) == -7
    assert evaluate_formula("min(a, b)", values) == 3
    assert evaluate_formula("max(a, b)", values) == 10
    assert evaluate_formula("round(a / b, 1)", values) == 3.3


def test_evaluate_formula_refuses_unknown_name():
    with pytest.raises(ValueError, match="unknown input"):
        evaluate_formula("a + missing", {"a": 1})


def test_evaluate_formula_refuses_division_by_zero():
    with pytest.raises(ValueError, match="divides by zero"):
        evaluate_formula("a / b", {"a": 1, "b": 0})


def test_evaluate_formula_refuses_code_not_data():
    for formula in ("__import__('os')", "a.b", "a[0]", "[a for a in b]", "a if b else a"):
        with pytest.raises(ValueError):
            evaluate_formula(formula, {"a": 1, "b": 2})


def test_evaluate_formula_refuses_bad_syntax_and_non_finite():
    with pytest.raises(ValueError, match="not a valid expression"):
        evaluate_formula("a +", {"a": 1})
    with pytest.raises(ValueError, match="finite"):
        evaluate_formula("a ** 9999", {"a": 10})


def test_format_widget_value():
    assert format_widget_value(5250000, "rupiah") == "Rp5.250.000"
    assert format_widget_value(12.5, "percent") == "12.5%"
    assert format_widget_value(42, "number") == "42"
    assert format_widget_value(3.333, "number") == "3.33"
    with pytest.raises(ValueError):
        format_widget_value(1, "roman")


# -- calculator ----------------------------------------------------------


def test_calculator_builds_and_computes_defaults():
    widget = calculator_widget(**_calculator())
    assert widget["kind"] == "calculator"
    assert compute_calculator(widget, {"price": 50000, "qty": 2}) == 100000


def test_calculator_slider_input_is_kept():
    widget = calculator_widget(**_calculator())
    slider = next(i for i in widget["inputs"] if i["id"] == "qty")
    assert slider["input_kind"] == "slider"
    assert slider["step"] == 1


def test_calculator_refuses_formula_over_unknown_input():
    with pytest.raises(ValueError, match="unknown input"):
        calculator_widget(**_calculator(formula="price * tax"))


def test_calculator_refuses_value_outside_range():
    inputs = _calculator()["inputs"]
    inputs[0]["value"] = 2000000
    with pytest.raises(ValueError, match="within min and max"):
        calculator_widget(**_calculator(inputs=inputs))


def test_calculator_refuses_duplicate_input_id():
    inputs = _calculator()["inputs"]
    inputs.append(dict(inputs[0]))
    with pytest.raises(ValueError, match="duplicate input id"):
        calculator_widget(**_calculator(inputs=inputs))


def test_compute_calculator_refuses_missing_and_out_of_range():
    widget = calculator_widget(**_calculator())
    with pytest.raises(ValueError, match="missing value"):
        compute_calculator(widget, {"price": 50000})
    with pytest.raises(ValueError, match="within min and max"):
        compute_calculator(widget, {"price": 50000, "qty": 99})


# -- form ----------------------------------------------------------------


def test_form_submission_validates_and_cleans():
    widget = form_widget(**_form())
    answers = validate_form_submission(
        widget,
        {"name": "Sari", "qty": 3, "city": "Karawang", "gift": True},
    )
    assert answers == {"name": "Sari", "qty": 3, "city": "Karawang", "gift": True}


def test_form_submission_allows_missing_optional_fields():
    widget = form_widget(**_form())
    answers = validate_form_submission(widget, {"name": "Sari", "qty": 1})
    assert answers["city"] is None
    assert answers["gift"] is None


def test_form_submission_refuses_missing_required():
    widget = form_widget(**_form())
    with pytest.raises(ValueError, match="required"):
        validate_form_submission(widget, {"qty": 1})


def test_form_submission_refuses_bad_select_and_types():
    widget = form_widget(**_form())
    with pytest.raises(ValueError, match="one of its options"):
        validate_form_submission(widget, {"name": "Sari", "qty": 1, "city": "Bogor"})
    with pytest.raises(ValueError, match="finite number"):
        validate_form_submission(widget, {"name": "Sari", "qty": "tiga"})
    with pytest.raises(ValueError, match="boolean"):
        validate_form_submission(widget, {"name": "Sari", "qty": 1, "gift": "yes"})
    with pytest.raises(ValueError, match="unknown field id"):
        validate_form_submission(widget, {"name": "Sari", "qty": 1, "nope": 1})


def test_form_refuses_select_without_options_and_duplicate_ids():
    fields = _form()["fields"]
    fields[2] = {"id": "city", "label": "Kota", "field_kind": "select"}
    with pytest.raises(ValueError, match="2 to 20 options"):
        form_widget(**_form(fields=fields))
    fields = _form()["fields"]
    fields.append(dict(fields[0]))
    with pytest.raises(ValueError, match="duplicate field id"):
        form_widget(**_form(fields=fields))


# -- comparison ----------------------------------------------------------


def test_comparison_best_row_is_cheapest():
    widget = comparison_widget(**_comparison())
    assert best_row(widget, "price")["label"] == "Toko B"
    assert best_row(widget, "price")["values"]["price"] == 5100000


def test_comparison_refuses_text_column_best_row():
    widget = comparison_widget(**_comparison())
    with pytest.raises(ValueError, match="not numeric"):
        best_row(widget, "note")


def test_comparison_refuses_row_missing_a_column():
    rows = _comparison()["rows"]
    rows[0]["values"] = {"price": 5200000}
    with pytest.raises(ValueError, match="missing column"):
        comparison_widget(**_comparison(rows=rows))


def test_comparison_refuses_text_in_numeric_column():
    rows = _comparison()["rows"]
    rows[0]["values"]["price"] = "mahal"
    with pytest.raises(ValueError, match="finite number"):
        comparison_widget(**_comparison(rows=rows))


# -- diagram -------------------------------------------------------------


def test_diagram_builds_with_edges():
    widget = diagram_widget(**_diagram())
    assert widget["kind"] == "diagram"
    assert len(widget["edges"]) == 2


def test_diagram_refuses_cycle_unknown_node_and_self_loop():
    with pytest.raises(ValueError, match="must not form a cycle"):
        diagram_widget(
            **_diagram(
                edges=[
                    {"from": "pick", "to": "pay"},
                    {"from": "pay", "to": "pick"},
                ]
            )
        )
    with pytest.raises(ValueError, match="unknown node"):
        diagram_widget(**_diagram(edges=[{"from": "pick", "to": "ghost"}]))
    with pytest.raises(ValueError, match="same node"):
        diagram_widget(**_diagram(edges=[{"from": "pick", "to": "pick"}]))


# -- simulation ----------------------------------------------------------


def test_simulation_widget_uses_simulator_prices():
    widget = simulation_widget()
    assert widget["day"] == 15
    assert widget["prices"] == {"Tokopedia": 5250000, "Shopee": 5100000, "Blibli": 5300000}
    assert widget["bill"]["status"] == "not_due"
    assert widget["bill"]["amount_due"] == 150000
    assert widget["history"] == [
        {"day": 15, "prices": {"Tokopedia": 5250000, "Shopee": 5100000, "Blibli": 5300000}}
    ]


def test_simulate_advance_day_moves_prices_by_the_drift():
    widget = simulation_widget()
    stepped, outcome = simulate_step(widget, {"type": "advance_day"})
    assert outcome["day"] == 16
    assert stepped["day"] == 16
    assert stepped["prices"] == {"Tokopedia": 5248000, "Shopee": 5103000, "Blibli": 5300000}
    assert len(stepped["history"]) == 2
    assert stepped["history"][1]["day"] == 16
    assert stepped["bill"]["status"] == "not_due"


def test_simulate_pay_bill_then_refuses_second_payment():
    widget = simulation_widget()
    paid, outcome = simulate_step(widget, {"type": "pay_bill"})
    assert outcome["success"] is True
    assert paid["bill_paid"] is True
    assert paid["bill"]["status"] == "paid"
    assert paid["bill"]["amount_due"] == 0
    with pytest.raises(ValueError, match="already paid"):
        simulate_step(paid, {"type": "pay_bill"})


def test_simulate_buy_uses_current_simulated_price():
    widget = simulation_widget()
    stepped, outcome = simulate_step(widget, {"type": "advance_day"})
    same, outcome = simulate_step(stepped, {"type": "buy_from_store", "store": "Shopee"})
    assert outcome["success"] is True
    assert outcome["price"] == 5103000
    assert same == stepped
    with pytest.raises(ValueError, match="unknown store"):
        simulate_step(stepped, {"type": "buy_from_store", "store": "Lazada"})


def test_simulate_refuses_advance_past_day_30_and_bad_action():
    widget = simulation_widget(day=30)
    with pytest.raises(ValueError, match="past day 30"):
        simulate_step(widget, {"type": "advance_day"})
    with pytest.raises(ValueError, match="unknown simulation action"):
        simulate_step(simulation_widget(), {"type": "teleport"})


def test_validate_widget_refuses_forged_simulation_prices():
    widget = simulation_widget()
    widget["prices"]["Shopee"] = 1000
    with pytest.raises(ValueError, match="do not match the simulator"):
        validate_widget(widget)


def test_price_compare_widget_comes_from_the_simulator():
    widget = price_compare_widget()
    assert widget["kind"] == "comparison"
    assert best_row(widget, "price")["label"] == "Shopee"
    assert best_row(widget, "price")["values"]["price"] == 5100000
    stepped = price_compare_widget(day=17)
    assert best_row(stepped, "price")["values"]["price"] == 5106000


# -- validation + chat seam ----------------------------------------------


def test_validate_widget_rejects_unknown_kind_and_extra_keys():
    with pytest.raises(ValueError, match="unknown widget kind"):
        validate_widget({"kind": "slot-machine"})
    widget = build_widget("calculator", **_calculator())
    assert widget["kind"] == "calculator"
    assert set(widget) == {"kind", "title", "inputs", "formula", "result_label", "value_format"}


def test_append_widget_message_puts_widget_in_chat_store():
    store = {}
    message = append_widget_message(
        store, "user-1", "Hitung dulu:", calculator_widget(**_calculator())
    )
    assert message["sender"] == "puspa"
    assert message["widget"]["kind"] == "calculator"
    assert store["user-1"][0] is message
