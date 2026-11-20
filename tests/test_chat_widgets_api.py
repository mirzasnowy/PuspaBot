"""API tests for the P-14 interactive widgets in Chat (``puspa/pwa/chat_widgets_api.py``).

Covers the HTTP layer only (the widget maths live in
``tests/test_chat_widgets.py``): a logged-in user posts each
of the five widget kinds into their chat, computes a
calculator result, submits a form, steps the simulation
forward and pays the bill, and pulls the simulated price
comparison and the fresh simulation widget; a bad widget is
a 400, paying twice is a 400, and an anonymous caller gets
401s.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in P-14): the routes are mounted there with the app's
own ``need_user`` login resolver, so the paths, login gate,
status codes, and JSON shapes tested here are the contract
the live PWA serves.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.pwa.chat_widgets import simulation_widget


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


def _calculator_body():
    return {
        "text": "Hitung dulu:",
        "widget": {
            "kind": "calculator",
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
        },
    }


def _form_body():
    return {
        "text": "Isi dulu ya:",
        "widget": {
            "kind": "form",
            "title": "Data pengiriman",
            "fields": [
                {"id": "name", "label": "Nama", "field_kind": "text", "required": True},
                {"id": "qty", "label": "Jumlah", "field_kind": "number", "required": True},
                {
                    "id": "city",
                    "label": "Kota",
                    "field_kind": "select",
                    "options": ["Jakarta", "Karawang"],
                },
            ],
            "submit_label": "Kirim",
        },
    }


def _comparison_body():
    return {
        "text": "Bandingkan:",
        "widget": {
            "kind": "comparison",
            "title": "Harga HP",
            "columns": [
                {"id": "price", "label": "Harga (Rp)", "numeric": True},
                {"id": "note", "label": "Catatan", "numeric": False},
            ],
            "rows": [
                {
                    "id": "a",
                    "label": "Toko A",
                    "values": {"price": 5200000, "note": "Gratis ongkir"},
                },
                {"id": "b", "label": "Toko B", "values": {"price": 5100000, "note": "Stok 2"}},
            ],
        },
    }


def _diagram_body():
    return {
        "text": "Alurnya:",
        "widget": {
            "kind": "diagram",
            "title": "Cara beli",
            "nodes": [
                {"id": "pick", "label": "Pilih toko"},
                {"id": "pay", "label": "Bayar"},
            ],
            "edges": [{"from": "pick", "to": "pay"}],
        },
    }


def _simulation_body():
    return {"text": "Simulasi harga:", "widget": simulation_widget()}


def test_widget_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/chat/widgets" in paths
    assert "/api/chat/widgets/compute" in paths
    assert "/api/chat/widgets/submit" in paths
    assert "/api/chat/widgets/simulate" in paths
    assert "/api/chat/widgets/price-compare" in paths
    assert "/api/chat/widgets/simulation" in paths


def test_widget_routes_require_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.post("/api/chat/widgets", json=_calculator_body()).status_code == 401
        assert client.post("/api/chat/widgets/compute", json={}).status_code == 401
        assert client.post("/api/chat/widgets/submit", json={}).status_code == 401
        assert client.post("/api/chat/widgets/simulate", json={}).status_code == 401
        assert client.get("/api/chat/widgets/price-compare").status_code == 401
        assert client.get("/api/chat/widgets/simulation").status_code == 401


def test_post_all_five_widget_kinds_into_chat(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        for body, kind in [
            (_calculator_body(), "calculator"),
            (_form_body(), "form"),
            (_comparison_body(), "comparison"),
            (_diagram_body(), "diagram"),
            (_simulation_body(), "simulation"),
        ]:
            resp = client.post("/api/chat/widgets", json=body)
            assert resp.status_code == 200, resp.text
            message = resp.json()
            assert message["sender"] == "puspa"
            assert message["text"] == body["text"]
            assert message["widget"]["kind"] == kind
        page = client.get("/chat")
        assert page.status_code == 200


def test_post_widget_rejects_bad_widget(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        resp = client.post("/api/chat/widgets", json={"text": "x", "widget": {"kind": "nope"}})
        assert resp.status_code == 400
        resp = client.post(
            "/api/chat/widgets", json={"text": "   ", "widget": _calculator_body()["widget"]}
        )
        assert resp.status_code == 400
        body = _calculator_body()
        body["widget"]["formula"] = "price * tax"
        assert client.post("/api/chat/widgets", json=body).status_code == 400


def test_compute_calculator_endpoint(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        body = {"widget": _calculator_body()["widget"], "values": {"price": 50000, "qty": 3}}
        resp = client.post("/api/chat/widgets/compute", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json() == {"result": 150000, "formatted": "Rp150.000"}
        body["values"] = {"price": 50000}
        assert client.post("/api/chat/widgets/compute", json=body).status_code == 400
        body["values"] = {"price": 50000, "qty": 99}
        assert client.post("/api/chat/widgets/compute", json=body).status_code == 400
        body = {"widget": _form_body()["widget"], "values": {}}
        assert client.post("/api/chat/widgets/compute", json=body).status_code == 400


def test_submit_form_endpoint(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        body = {
            "widget": _form_body()["widget"],
            "answers": {"name": "Sari", "qty": 2, "city": "Karawang"},
        }
        resp = client.post("/api/chat/widgets/submit", json=body)
        assert resp.status_code == 200, resp.text
        message = resp.json()
        assert "Sari" in message["text"]
        assert "Karawang" in message["text"]
        assert message["widget"]["kind"] == "form"
        body["answers"] = {"qty": 2}
        assert client.post("/api/chat/widgets/submit", json=body).status_code == 400
        body = {"widget": _calculator_body()["widget"], "answers": {}}
        assert client.post("/api/chat/widgets/submit", json=body).status_code == 400


def test_simulate_endpoint_steps_days_and_pays_bill(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        body = {"widget": simulation_widget(), "action": {"type": "advance_day"}}
        resp = client.post("/api/chat/widgets/simulate", json=body)
        assert resp.status_code == 200, resp.text
        message = resp.json()
        assert message["widget"]["day"] == 16
        assert message["widget"]["prices"]["Tokopedia"] == 5248000
        assert "Hari simulasi 16" in message["text"]
        body = {"widget": message["widget"], "action": {"type": "pay_bill"}}
        resp = client.post("/api/chat/widgets/simulate", json=body)
        assert resp.status_code == 200, resp.text
        assert resp.json()["widget"]["bill_paid"] is True
        # Paying the same bill twice is a 400, not a silent no-op.
        body = {"widget": resp.json()["widget"], "action": {"type": "pay_bill"}}
        assert client.post("/api/chat/widgets/simulate", json=body).status_code == 400
        # Buying uses the current simulated price.
        body = {
            "widget": simulation_widget(),
            "action": {"type": "buy_from_store", "store": "Shopee"},
        }
        resp = client.post("/api/chat/widgets/simulate", json=body)
        assert resp.status_code == 200, resp.text
        assert "Rp5.100.000" in resp.json()["text"]
        body["action"] = {"type": "buy_from_store", "store": "Lazada"}
        assert client.post("/api/chat/widgets/simulate", json=body).status_code == 400


def test_price_compare_and_simulation_endpoints(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        resp = client.get("/api/chat/widgets/price-compare")
        assert resp.status_code == 200, resp.text
        message = resp.json()
        assert message["widget"]["kind"] == "comparison"
        cheapest = min(message["widget"]["rows"], key=lambda r: r["values"]["price"])
        assert cheapest["label"] == "Shopee"
        resp = client.get("/api/chat/widgets/simulation")
        assert resp.status_code == 200, resp.text
        message = resp.json()
        assert message["widget"]["kind"] == "simulation"
        assert message["widget"]["day"] == 15
        assert message["widget"]["prices"]["Blibli"] == 5300000
