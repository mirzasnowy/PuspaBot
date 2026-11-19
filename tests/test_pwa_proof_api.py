"""API tests for the B4 proof-surface endpoints (``puspa/pwa/proof_api.py``).

Covers the HTTP layer only (the receipt maths live in
``tests/test_proof_surface.py``): a logged-in user gets their
receipts and audit trail, another user's event is 403, an
unknown event is 404, an anonymous caller gets 401, the export
downloads as JSON, and the minimal ``/proof`` page renders.

These tests run against the real ``puspa.pwa.app.create_app``
(wired in B4): the routes are mounted there with the app's own
``need_user`` login resolver, so the paths, login gate, status
codes, and JSON shapes tested here are the contract the live
PWA serves.
"""

from fastapi.testclient import TestClient

from puspa.evidence.outcomes import write_outcome
from puspa.pwa.app import create_app


def _client(user_id, test_dsn):
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    return TestClient(app)


def _seed_event(ledger, user, task_id="task-api-proof"):
    return ledger.append_event(
        task_id=task_id,
        agent_id=user["agent_id"],
        event_type="approval_requested",
        content={"action_kind": "pay", "summary": "payment of Rp50.000 requested"},
    )


def test_proof_routes_exist_on_create_app(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/proof/receipts/{event_id}" in paths
    assert "/api/proof/outcomes/{outcome_id}" in paths
    assert "/api/proof/audit-trail" in paths
    assert "/api/proof/audit-trail/export" in paths
    assert "/proof" in paths
    with _client(user_id, test_dsn) as client:
        assert client.get("/api/proof/audit-trail").status_code == 200
    anon = create_app(test_user_id=None, dsn=test_dsn)
    with TestClient(anon) as client:
        assert client.get("/api/proof/audit-trail").status_code == 401
        assert (
            client.get("/api/proof/receipts/00000000-0000-0000-0000-000000000000").status_code
            == 401
        )
        assert client.get("/proof", follow_redirects=False).status_code in (303, 307)


def test_receipt_endpoint_returns_receipt(make_user, ledger, test_dsn):
    user = make_user()
    event = _seed_event(ledger, user)
    with _client(user["user_id"], test_dsn) as client:
        resp = client.get(f"/api/proof/receipts/{event['id']}")
        assert resp.status_code == 200
        receipt = resp.json()
        assert receipt["event_id"] == event["id"]
        assert receipt["event_hash"] == event["hash"]
        assert receipt["action_kind"] == "money"
        assert receipt["verified"] is True
        assert receipt["chain_ok"] is True
        assert receipt["summary"].startswith("Approval requested")


def test_receipt_endpoint_forbidden_for_other_user(make_user, ledger, test_dsn):
    owner = make_user()
    outsider = make_user()
    event = _seed_event(ledger, owner)
    with _client(outsider["user_id"], test_dsn) as client:
        assert client.get(f"/api/proof/receipts/{event['id']}").status_code == 403


def test_receipt_endpoint_unknown_is_404(make_user, test_dsn):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn) as client:
        resp = client.get("/api/proof/receipts/00000000-0000-0000-0000-000000000000")
        assert resp.status_code == 404


def test_outcome_endpoint_returns_proof(make_user, ledger, test_dsn):
    user = make_user()
    record = write_outcome(
        ledger,
        task_id="task-api-outcome",
        agent_id=user["agent_id"],
        outcome_type="price_checked",
        user_id=user["user_id"],
        summary="checked the fair price",
        evidence=[{"kind": "evidence_record", "evidence_id": "bukti-api"}],
    )
    with _client(user["user_id"], test_dsn) as client:
        resp = client.get(f"/api/proof/outcomes/{record.outcome_id}")
        assert resp.status_code == 200
        body = resp.json()
        assert body["outcome_id"] == record.outcome_id
        assert body["proof"]["proven"] is True
        assert body["proof"]["receipt"]["event_hash"] == record.event_hash
    outsider = make_user()
    with _client(outsider["user_id"], test_dsn) as client:
        assert client.get(f"/api/proof/outcomes/{record.outcome_id}").status_code == 403
    with _client(user["user_id"], test_dsn) as client:
        assert (
            client.get("/api/proof/outcomes/00000000-0000-0000-0000-000000000000").status_code
            == 404
        )


def test_audit_trail_endpoint_paginates(make_user, ledger, test_dsn):
    user = make_user()
    for index in range(3):
        _seed_event(ledger, user, task_id=f"task-api-trail-{index}")
    with _client(user["user_id"], test_dsn) as client:
        first = client.get("/api/proof/audit-trail?limit=2&offset=0").json()
        assert first["pagination"]["total"] == 3
        assert len(first["events"]) == 2
        assert all(entry["receipt"]["verified"] for entry in first["events"])
        second = client.get("/api/proof/audit-trail?limit=2&offset=2").json()
        assert len(second["events"]) == 1
        bad = client.get("/api/proof/audit-trail?limit=0")
        assert bad.status_code == 400


def test_audit_trail_export_downloads_json(make_user, ledger, test_dsn):
    user = make_user()
    _seed_event(ledger, user)
    with _client(user["user_id"], test_dsn) as client:
        resp = client.get("/api/proof/audit-trail/export")
        assert resp.status_code == 200
        assert "attachment" in resp.headers["content-disposition"]
        assert "puspa-audit-trail.json" in resp.headers["content-disposition"]
        document = resp.json()
        assert document["format"] == "puspa-audit-trail"
        assert document["pagination"]["total"] == 1
        assert len(document["events"]) == 1


def test_proof_page_renders_receipts(make_user, ledger, test_dsn):
    user = make_user()
    event = _seed_event(ledger, user)
    with _client(user["user_id"], test_dsn) as client:
        resp = client.get("/proof")
        assert resp.status_code == 200
        assert event["hash"] in resp.text
        assert "Approval requested" in resp.text
        assert "audit trail" in resp.text.lower()
