"""API tests for the B8 payment-preparation endpoints
(``puspa/pwa/payments_api.py``).

Covers the HTTP layer only (the preparation rules live in
``tests/test_payments_b8.py``): a logged-in user prepares a
payment, approves it, sees it as payable, marks it paid in
their own app, and gets a 403 on every execution attempt;
an anonymous caller gets 401, bad input gets 400, and
another user's preparation is 404.

These tests run against the real ``puspa.pwa.app.create_app``:
the routes are mounted there with the app's own ``need_user``
login resolver, so the paths, login gate, status codes, and
JSON shapes tested here are the contract the live PWA serves.
The payments schema is applied by the autouse fixture below.
"""

import pytest
from fastapi.testclient import TestClient

from puspa.payments.schema import create_schema, reset_tables
from puspa.pwa.app import create_app


@pytest.fixture(autouse=True)
def _payments_schema(test_dsn):
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


def _client(user_id, test_dsn):
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    return TestClient(app)


def _body(**overrides):
    body = {
        "recipient": {
            "name": "Toko Kopi Senja",
            "account_ref": "1234567890",
            "verified": True,
            "verified_by": "seller page on Tokopedia",
        },
        "amount_rupiah": 150_000,
        "rail": "bayar pake qris",
        "evidence": [
            {
                "kind": "seller_page",
                "source_url": "https://tokopedia.com/toko-kopi-senja",
                "recipient_name": "Toko Kopi Senja",
                "account_ref": "1234567890",
            }
        ],
    }
    body.update(overrides)
    return body


def test_payment_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/payments" in paths
    assert "/api/payments/payable" in paths
    assert "/api/payments/{preparation_id}" in paths
    assert "/api/payments/{preparation_id}/approve" in paths
    assert "/api/payments/{preparation_id}/reject" in paths
    assert "/api/payments/{preparation_id}/mark-paid" in paths
    assert "/api/payments/{preparation_id}/execute" in paths
    with _client(user_id, test_dsn) as client:
        assert client.get("/api/payments").json() == {"payments": []}
        assert client.get("/api/payments/payable").json() == {"payments": []}
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/payments").status_code == 401
        assert client.post("/api/payments", json=_body()).status_code == 401


def test_prepare_approve_payable_mark_paid(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        prepared = client.post("/api/payments", json=_body())
        assert prepared.status_code == 200
        payment = prepared.json()
        assert payment["status"] == "awaiting_approval"
        assert payment["payable"] is False
        assert payment["recipient"]["name"] == "Toko Kopi Senja"
        assert payment["amount_rupiah"] == 150_000
        assert payment["amount_display"] == "Rp150.000"
        assert payment["rail"]["rail_id"] == "qris"
        assert payment["pay_notice"].startswith("Puspa never pays")
        assert "Toko Kopi Senja" in payment["summary"]
        assert "Puspa never pays" in payment["summary"]
        preparation_id = payment["id"]

        # Not payable before approval.
        assert client.get("/api/payments/payable").json() == {"payments": []}

        approved = client.post(f"/api/payments/{preparation_id}/approve")
        assert approved.status_code == 200
        assert approved.json()["status"] == "approved"
        assert approved.json()["payable"] is True
        payable = client.get("/api/payments/payable").json()["payments"]
        assert [item["id"] for item in payable] == [preparation_id]

        marked = client.post(
            f"/api/payments/{preparation_id}/mark-paid",
            json={"assertion": "paid via BCA mobile"},
        )
        assert marked.status_code == 200
        assert marked.json()["status"] == "paid_by_user"
        assert marked.json()["paid_assertion"] == "paid via BCA mobile"
        assert client.get("/api/payments/payable").json() == {"payments": []}


def test_prepare_bad_input_is_400(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        # Unverified recipient.
        body = _body()
        body["recipient"]["verified"] = False
        assert client.post("/api/payments", json=body).status_code == 400
        # Non-integer amount.
        assert client.post("/api/payments", json=_body(amount_rupiah=150_000.5)).status_code == 400
        assert client.post("/api/payments", json=_body(amount_rupiah=0)).status_code == 400
        # Unnamed rail.
        assert client.post("/api/payments", json=_body(rail="nanti aja")).status_code == 400
        # No evidence.
        assert client.post("/api/payments", json=_body(evidence=[])).status_code == 400
        # Evidence that does not name the recipient.
        body = _body(evidence=[{"kind": "note", "text": "someone else entirely"}])
        assert client.post("/api/payments", json=body).status_code == 400
        assert client.get("/api/payments").json() == {"payments": []}


def test_execute_is_always_403(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        payment = client.post("/api/payments", json=_body()).json()
        preparation_id = payment["id"]
        client.post(f"/api/payments/{preparation_id}/approve")
        refused = client.post(f"/api/payments/{preparation_id}/execute")
        assert refused.status_code == 403
        assert "never pays" in refused.json()["error"]
        # Still approved and untouched: the refusal moved nothing.
        assert client.get(f"/api/payments/{preparation_id}").json()["status"] == "approved"
        # Unknown ids are refused the same way, never executed.
        assert (
            client.post("/api/payments/00000000-0000-0000-0000-000000000000/execute").status_code
            == 403
        )


def test_other_users_preparation_is_404(make_user, test_dsn):
    user_id = make_user()["user_id"]
    other_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        preparation_id = client.post("/api/payments", json=_body()).json()["id"]
    with _client(other_id, test_dsn) as client:
        assert client.get(f"/api/payments/{preparation_id}").status_code == 404
        assert client.get("/api/payments").json() == {"payments": []}


def test_reject_is_terminal(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        preparation_id = client.post("/api/payments", json=_body()).json()["id"]
        rejected = client.post(f"/api/payments/{preparation_id}/reject")
        assert rejected.status_code == 200
        assert rejected.json()["status"] == "rejected"
        assert client.post(f"/api/payments/{preparation_id}/approve").status_code == 400
        assert client.post(f"/api/payments/{preparation_id}/mark-paid", json={}).status_code == 400


def test_idempotent_prepare_over_http(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        first = client.post("/api/payments", json=_body(idempotency_key="order-123")).json()
        second = client.post("/api/payments", json=_body(idempotency_key="order-123")).json()
        assert second["id"] == first["id"]
        assert len(client.get("/api/payments").json()["payments"]) == 1


def test_api_creates_schema_when_table_missing(make_user, test_dsn):
    """The lazy schema (B7 mockup pattern): a server that only
    ran migration 0001_core.sql still serves payments — the first
    request creates the table instead of failing on it."""
    import psycopg

    from puspa.accounts.users import resolve_dsn

    user_id = make_user()["user_id"]
    with psycopg.connect(resolve_dsn(test_dsn)) as conn:
        # Simulate a server the payments migration never ran on:
        # drop the table AND forget the migration. (Numbered
        # migrations trust schema_migrations: re-applying a recorded
        # migration is a no-op by design, so dropping the table
        # alone would not make the lazy path do anything.)
        conn.execute("DROP TABLE IF EXISTS prepared_payments CASCADE")
        conn.execute("DELETE FROM schema_migrations WHERE version = '0013'")
        conn.commit()
    # A fresh app has never set the ready flag, so the first
    # request must create the table itself.
    with _client(user_id, test_dsn) as client:
        assert client.get("/api/payments").json() == {"payments": []}
        assert client.post("/api/payments", json=_body()).status_code == 200
        assert len(client.get("/api/payments").json()["payments"]) == 1
