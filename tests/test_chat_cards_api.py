"""API tests for the P-12 rich cards in Chat (``puspa/pwa/chat_cards_api.py``).

Covers the HTTP layer only (the card maths live in
``tests/test_chat_cards.py``): a logged-in user posts each of
the four card kinds into their chat and sees the message in
the thread, turns a stored B13 draft into a draft card, and
turns their G28 Insights report into a chart card; a bad card
is a 400, an unknown draft is a 404, and an anonymous caller
gets 401s, not 404s.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in P-12): the routes are mounted there with the app's
own ``need_user`` login resolver, so the paths, login gate,
status codes, and JSON shapes tested here are the contract
the live PWA serves.
"""

from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from puspa.billing.ledger import create_schema as create_usage_schema
from puspa.drafting.schema import create_schema as create_drafting_schema
from puspa.drafting.schema import reset_tables as reset_drafting_tables
from puspa.pwa.app import create_app


@pytest.fixture(autouse=True)
def _card_schemas(test_dsn):
    create_drafting_schema(test_dsn)
    reset_drafting_tables(test_dsn)
    create_usage_schema(test_dsn)
    yield
    reset_drafting_tables(test_dsn)


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


def _draft_card_body():
    return {
        "text": "Ini draft emailnya:",
        "card": {
            "kind": "draft",
            "title": "Kabar barang",
            "body": "Halo Sari, barang sudah sampai.",
            "channel": "email",
            "recipient": "sari@contoh.id",
            "subject": "Barang sampai",
        },
    }


def _options_card_body():
    return {
        "text": "Mau lanjut yang mana?",
        "card": {
            "kind": "options",
            "question": "Mau lanjut yang mana?",
            "options": [
                {"id": "a", "label": "Kirim email"},
                {"id": "b", "label": "Tunggu besok"},
            ],
        },
    }


def _chart_card_body():
    return {
        "text": "Biaya per hari:",
        "card": {
            "kind": "chart",
            "title": "Cost per day",
            "metric": "cost_idr",
            "points": [
                {"label": "2026-11-15", "value": 500},
                {"label": "2026-11-16", "value": 0},
            ],
        },
    }


def _map_card_body():
    return {
        "text": "Rute ke kantor:",
        "card": {
            "kind": "map",
            "title": "Rute ke kantor",
            "center": {"lat": -6.2, "lon": 106.8},
            "zoom": 15,
            "markers": [{"lat": -6.2, "lon": 106.8, "label": "Kantor"}],
            "route": [
                {"lat": -6.2, "lon": 106.8},
                {"lat": -6.21, "lon": 106.81},
            ],
        },
    }


def test_card_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/chat/cards" in paths
    assert "/api/chat/cards/draft/{draft_id}" in paths
    assert "/api/chat/cards/insights" in paths
    with _client(user_id, test_dsn) as client:
        resp = client.post("/api/chat/cards", json=_draft_card_body())
        assert resp.status_code == 200, resp.text
        assert resp.json()["card"]["kind"] == "draft"


def test_card_routes_require_login(test_dsn):
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.post("/api/chat/cards", json=_draft_card_body()).status_code == 401
        assert client.post("/api/chat/cards/draft/d-1").status_code == 401
        assert client.get("/api/chat/cards/insights").status_code == 401


def test_post_all_four_card_kinds_into_chat(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        for body, kind in [
            (_draft_card_body(), "draft"),
            (_options_card_body(), "options"),
            (_chart_card_body(), "chart"),
            (_map_card_body(), "map"),
        ]:
            resp = client.post("/api/chat/cards", json=body)
            assert resp.status_code == 200, resp.text
            message = resp.json()
            assert message["sender"] == "puspa"
            assert message["text"] == body["text"]
            assert message["card"]["kind"] == kind
        # All four messages are in the thread the Chat screen reads.
        page = client.get("/chat")
        assert page.status_code == 200
        for text in (
            "Ini draft emailnya:",
            "Mau lanjut yang mana?",
            "Biaya per hari:",
            "Rute ke kantor:",
        ):
            assert text in page.text


def test_post_card_refuses_bad_cards(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        assert (
            client.post(
                "/api/chat/cards",
                json={"text": "x", "card": {"kind": "calculator"}},
            ).status_code
            == 400
        )
        bad_map = _map_card_body()
        bad_map["card"]["center"] = {"lat": 91, "lon": 0}
        assert client.post("/api/chat/cards", json=bad_map).status_code == 400
        assert (
            client.post(
                "/api/chat/cards", json={"text": "  ", "card": _draft_card_body()["card"]}
            ).status_code
            == 400
        )
        assert client.post("/api/chat/cards", json={"text": "x"}).status_code == 400


def test_draft_card_from_stored_b13_draft(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        created = client.post(
            "/api/drafts",
            json={
                "kind": "email",
                "brief": "kabari Sari barang sampai",
                "recipient": "sari@contoh.id",
                "channel": "email",
            },
        )
        assert created.status_code == 200, created.text
        draft_id = created.json()["id"]
        resp = client.post(f"/api/chat/cards/draft/{draft_id}")
        assert resp.status_code == 200, resp.text
        message = resp.json()
        assert message["card"]["kind"] == "draft"
        assert message["card"]["draft_id"] == draft_id
        assert message["card"]["channel"] == "email"
        assert message["card"]["mailto"].startswith("mailto:sari@contoh.id?")
        assert message["card"]["body"] == created.json()["text"]
        # An unknown draft is a 404, not a 500.
        assert client.post("/api/chat/cards/draft/tidak-ada").status_code == 404


def test_chat_cards_render_in_lite_template(make_user, test_dsn, monkeypatch):
    """The card block in the Jinja chat template renders all four kinds.

    Hides the React build (same trick as tests/test_webui_lite.py)
    so the Chat screen falls back to the lite template: the card
    bodies must appear in the HTML, with no unrendered Jinja.
    """
    from pathlib import Path

    import puspa.pwa.app as pwa_app

    monkeypatch.setattr(pwa_app, "DIST_DIR", Path("/nonexistent-dist-for-lite-tests"))
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        for body in (
            _draft_card_body(),
            _options_card_body(),
            _chart_card_body(),
            _map_card_body(),
        ):
            assert client.post("/api/chat/cards", json=body).status_code == 200
        page = client.get("/chat")
        assert page.status_code == 200
        assert "Halo Sari, barang sudah sampai." in page.text  # draft body
        assert "Buka di Mail" in page.text  # draft mailto link
        assert "Kirim email" in page.text  # option label
        assert "2026-11-15: 500" in page.text  # chart point
        assert "Kantor" in page.text  # map marker label
        assert "Rute: 2 titik." in page.text  # map route
        assert "{{" not in page.text


def test_insights_card_from_g28_report(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        resp = client.get("/api/chat/cards/insights?days=7&metric=cost_idr")
        assert resp.status_code == 200, resp.text
        message = resp.json()
        assert message["card"]["kind"] == "chart"
        assert message["card"]["metric"] == "cost_idr"
        # A user with no recorded turns gets an honest all-zero
        # chart: 7 days, every value zero.
        assert len(message["card"]["points"]) == 7
        assert all(point["value"] == 0 for point in message["card"]["points"])
        turns = client.get("/api/chat/cards/insights?days=3&metric=turns")
        assert turns.status_code == 200
        assert len(turns.json()["card"]["points"]) == 3
        assert client.get("/api/chat/cards/insights?metric=rupiah").status_code == 400
        assert client.get("/api/chat/cards/insights?days=0").status_code == 400
