"""Tests for the receipt-photo API.

Upload -> draft -> confirm-or-discard, with the photo kept as
evidence, identity documents refused, and nothing recorded
before confirmation.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.receipts.ocr import MappingOcrEngine

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"
WARUNG_TEXT = (FIXTURES / "warung.txt").read_text(encoding="utf-8")
WARUNG_PNG = (FIXTURES / "photo-warung.png").read_bytes()
KTP_TEXT = "KARTU TANDA PENDUDUK\nNIK: 3201234567890001\nNama: Budi Santoso"
KTP_PNG = (FIXTURES / "ktp.png").read_bytes()


@pytest.fixture
def client(make_user, tmp_path):
    u = make_user()
    engine = MappingOcrEngine({WARUNG_PNG: WARUNG_TEXT, KTP_PNG: KTP_TEXT})
    app = create_app(
        test_user_id=u["user_id"],
        ocr_engine=engine,
        share_inbox_dir=tmp_path / "inbox",
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = u["user_id"]
        c.inbox = tmp_path / "inbox"
        yield c


def _upload(c, photo=WARUNG_PNG, mime="image/png"):
    return c.post("/api/receipts/upload", files={"photo": ("struk.png", photo, mime)})


def test_upload_returns_a_confirmable_draft_with_evidence_ids(client):
    resp = _upload(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["pending_id"]
    assert body["photo_id"]
    assert body["engine"] == "stub-mapping"
    assert body["document_kind"] == "receipt"
    assert body["elapsed_seconds"] < 20.0
    draft = body["draft"]
    assert draft["merchant"] == "WARUNG KOPI SEDUH"
    assert draft["total"] == 51000
    assert draft["total_evidence_id"] == f"receipt-{body['photo_id']}-total"
    assert len(draft["lines"]) == 3
    for line in draft["lines"]:
        assert line["evidence_id"].startswith(f"receipt-{body['photo_id']}-line-")
    # The photo is kept as evidence, per user, under receipts/.
    kept = client.inbox / client.user_id / "receipts"
    assert kept.is_dir()
    assert list(kept.iterdir())
    # Nothing is recorded before confirmation.
    assert client.get("/api/receipts/expenses").json()["expenses"] == []


def test_upload_rejects_wrong_type_empty_and_non_image(client):
    assert _upload(client, photo=b"x", mime="application/pdf").status_code == 400
    assert _upload(client, photo=b"", mime="image/png").status_code == 400
    assert _upload(client, photo=b"plain text, not a photo", mime="image/png").status_code == 400


def test_upload_refuses_identity_document_and_stores_nothing(client):
    resp = _upload(client, photo=KTP_PNG)
    assert resp.status_code == 422
    body = resp.json()
    assert body["rejected"] is True
    assert body["document_kind"] == "id_document"
    assert "identity" in body["reason"].lower()
    kept = client.inbox / client.user_id / "receipts"
    assert not kept.exists() or not list(kept.iterdir())


def test_confirm_records_the_expense_with_evidence_and_posts_to_chat(client):
    pending_id = _upload(client).json()["pending_id"]
    resp = client.post("/api/receipts/confirm", json={"pending_id": pending_id})
    assert resp.status_code == 200
    expense = resp.json()["expense"]
    assert expense["merchant"] == "WARUNG KOPI SEDUH"
    assert expense["total"] == 51000
    assert expense["total_evidence_id"].startswith("receipt-")
    assert expense["edited_by_user"] is False
    assert all(line["evidence_id"] for line in expense["lines"])
    expenses = client.get("/api/receipts/expenses").json()["expenses"]
    assert len(expenses) == 1
    assert expenses[0]["expense_id"] == expense["expense_id"]
    page = client.get("/chat")
    assert "WARUNG KOPI SEDUH" in page.text
    assert "51.000" in page.text
    # Single-use: confirming again finds nothing.
    assert client.post("/api/receipts/confirm", json={"pending_id": pending_id}).status_code == 404


def test_confirm_applies_user_edits_and_marks_them(client):
    pending_id = _upload(client).json()["pending_id"]
    resp = client.post(
        "/api/receipts/confirm",
        json={"pending_id": pending_id, "merchant": "Warung Kopi Seduh (edited)", "total": 52000},
    )
    assert resp.status_code == 200
    expense = resp.json()["expense"]
    assert expense["merchant"] == "Warung Kopi Seduh (edited)"
    assert expense["total"] == 52000
    assert expense["edited_by_user"] is True
    # The edited total still carries the photo evidence id.
    assert expense["total_evidence_id"]


def test_confirm_rejects_empty_merchant_and_non_positive_total(client):
    pending_id = _upload(client).json()["pending_id"]
    assert (
        client.post(
            "/api/receipts/confirm", json={"pending_id": pending_id, "merchant": "  "}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/receipts/confirm", json={"pending_id": pending_id, "total": 0}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/api/receipts/confirm", json={"pending_id": pending_id, "total": "abc"}
        ).status_code
        == 400
    )


def test_discard_drops_the_draft_and_deletes_the_photo(client):
    pending_id = _upload(client).json()["pending_id"]
    kept = client.inbox / client.user_id / "receipts"
    assert list(kept.iterdir())
    resp = client.post("/api/receipts/discard", json={"pending_id": pending_id})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert list(kept.iterdir()) == []
    assert client.get("/api/receipts/expenses").json()["expenses"] == []
    assert client.post("/api/receipts/discard", json={"pending_id": pending_id}).status_code == 404


def test_pending_ids_are_scoped_to_their_user(make_user, tmp_path):
    first = make_user()
    second = make_user()
    engine = MappingOcrEngine({WARUNG_PNG: WARUNG_TEXT})
    app = create_app(
        test_user_id=first["user_id"],
        ocr_engine=engine,
        share_inbox_dir=tmp_path / "inbox",
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        pending_id = c.post(
            "/api/receipts/upload", files={"photo": ("struk.png", WARUNG_PNG, "image/png")}
        ).json()["pending_id"]
        # The same app with another logged-in user cannot see it.
        app.state.test_user_id = second["user_id"]
        assert c.post("/api/receipts/confirm", json={"pending_id": pending_id}).status_code == 404
        assert c.post("/api/receipts/discard", json={"pending_id": pending_id}).status_code == 404


def test_status_reports_engine_not_selected(client):
    body = client.get("/api/receipts/status").json()
    assert body["engine"] == "stub-mapping"
    assert body["engine_selected"] is False
    assert body["selection"] == "pending"
    assert body["required_cases"] == 20
    assert "20" in body["note"]


def test_routes_require_login(tmp_path):
    app = create_app(ocr_engine=MappingOcrEngine({}), share_inbox_dir=tmp_path / "inbox")
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/api/receipts/status").status_code == 401
        assert c.get("/api/receipts/expenses").status_code == 401
        assert c.post("/api/receipts/confirm", json={"pending_id": "x"}).status_code == 401
        assert c.post("/api/receipts/discard", json={"pending_id": "x"}).status_code == 401
