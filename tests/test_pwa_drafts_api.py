"""API tests for the B13 drafting endpoints (``puspa/pwa/drafts_api.py``).

Covers the HTTP layer only (the drafting maths live in
``tests/test_drafting_b13.py``): a logged-in user creates a
draft in their voice, lists it, corrects the voice and sees
the next draft change, approves a draft, and sends it through
an injected test sender; sending without approval is 403, and
with no delivery channel wired in it is 501 — never a silent
send. An anonymous caller gets 401 everywhere.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in B13): the routes are mounted there with the app's
own ``need_user`` login resolver, so the paths, login gate,
status codes, and JSON shapes tested here are the contract
the live PWA serves. The drafting schema is applied by the
autouse fixture below (conftest does NOT apply it).
"""

import pytest
from fastapi.testclient import TestClient

from puspa.drafting.schema import create_schema, reset_tables
from puspa.pwa.app import create_app


@pytest.fixture(autouse=True)
def _drafting_schema(test_dsn):
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


def _client(user_id, test_dsn, sender=None):
    app = create_app(test_user_id=user_id, dsn=test_dsn, draft_sender=sender)
    return TestClient(app)


def test_draft_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/drafts" in paths
    assert "/api/drafts/{draft_id}" in paths
    assert "/api/drafts/{draft_id}/approve" in paths
    assert "/api/drafts/{draft_id}/send" in paths
    assert "/api/voice/profile" in paths
    assert "/api/voice/corrections" in paths
    with _client(user_id, test_dsn) as client:
        assert client.get("/api/drafts").status_code == 200
        assert client.get("/api/voice/profile").status_code == 200
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/drafts").status_code == 401
        assert client.get("/api/voice/profile").status_code == 401
        assert (
            client.post("/api/drafts", json={"kind": "message", "brief": "halo"}).status_code == 401
        )


def test_create_and_list_draft(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        resp = client.post(
            "/api/drafts",
            json={"kind": "message", "brief": "tolong kabari saya kalau barang sudah sampai"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["kind"] == "message"
        assert body["status"] == "draft"
        assert body["text"].startswith("Halo,")
        listed = client.get("/api/drafts").json()["drafts"]
        assert [d["id"] for d in listed] == [body["id"]]
        one = client.get(f"/api/drafts/{body['id']}").json()
        assert one["brief"] == "tolong kabari saya kalau barang sudah sampai"
        # Bad input is a 400, not a 500.
        assert client.post("/api/drafts", json={"kind": "letter", "brief": "x"}).status_code == 400
        assert (
            client.post("/api/drafts", json={"kind": "message", "brief": "  "}).status_code == 400
        )
        assert client.get("/api/drafts/00000000-0000-0000-0000-000000000000").status_code == 404


def test_voice_correction_changes_next_draft(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        first = client.post(
            "/api/drafts",
            json={"kind": "message", "brief": "Dear customer, pesanan Anda sudah dikirim"},
        ).json()
        assert "Dear customer," in first["text"]
        corrected = client.post("/api/voice/corrections", json={"correction": "never use Dear"})
        assert corrected.status_code == 200, corrected.text
        assert corrected.json()["fact"]["key"].startswith("voice.correction.")
        profile = client.get("/api/voice/profile").json()
        assert "never use 'Dear'" in profile["rules"]
        second = client.post(
            "/api/drafts",
            json={"kind": "message", "brief": "Dear customer, pesanan Anda sudah dikirim"},
        ).json()
        assert "Dear" not in second["text"]
        assert client.post("/api/voice/corrections", json={"correction": "   "}).status_code == 400


def test_approve_then_send_with_sender(make_user, test_dsn):
    user_id = make_user()["user_id"]
    delivered: list[dict] = []

    def sender(payload):
        delivered.append(payload)
        return {"delivered": True}

    with _client(user_id, test_dsn, sender=sender) as client:
        draft = client.post(
            "/api/drafts",
            json={
                "kind": "email",
                "brief": "mohon kirim invoice pesanan 42",
                "recipient": "toko@example.id",
            },
        ).json()
        # No approval yet: send is refused and nothing is delivered.
        assert client.post(f"/api/drafts/{draft['id']}/send", json={}).status_code == 403
        assert delivered == []
        approved = client.post(f"/api/drafts/{draft['id']}/approve").json()
        approval_id = approved["approval"]["id"]
        sent = client.post(f"/api/drafts/{draft['id']}/send", json={"approval_id": approval_id})
        assert sent.status_code == 200, sent.text
        assert sent.json()["draft"]["status"] == "sent"
        assert delivered[0]["recipient"] == "toko@example.id"
        # The approval is single-use: a second send is refused.
        assert (
            client.post(
                f"/api/drafts/{draft['id']}/send", json={"approval_id": approval_id}
            ).status_code
            == 403
        )


def test_send_without_channel_is_501_and_keeps_approval(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        draft = client.post(
            "/api/drafts",
            json={"kind": "message", "brief": "halo, barang sudah sampai"},
        ).json()
        approval_id = client.post(f"/api/drafts/{draft['id']}/approve").json()["approval"]["id"]
        # No sender wired into this app: 501, nothing sent.
        resp = client.post(f"/api/drafts/{draft['id']}/send", json={"approval_id": approval_id})
        assert resp.status_code == 501, resp.text
        assert client.get(f"/api/drafts/{draft['id']}").json()["status"] == "approved"


def test_draft_flagged_when_it_contradicts_a_decision(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        # Record the decision through the memory facts directly
        # (the same store the voice profile reads), then draft
        # against it.
        from puspa.memory.facts import PostgresFactStore, record_fact

        record_fact(
            user_id,
            "decision.discounts",
            "never offer discounts to new customers",
            source="user_confirmed",
            store=PostgresFactStore(test_dsn),
        )
        draft = client.post(
            "/api/drafts",
            json={
                "kind": "message",
                "brief": "tawarkan diskon 20% untuk pelanggan baru hari ini",
            },
        ).json()
        assert draft["decision_flags"] == ["never offer discounts to new customers"]
