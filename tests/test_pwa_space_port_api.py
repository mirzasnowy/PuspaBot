"""API tests for the Space-port endpoints (``puspa/pwa/billing_api.py``,
``puspa/pwa/app.py`` trial status, ``puspa/pwa/onboarding_api.py``
agent-file).

Covers the HTTP layer only: the billing plan returns the real
tiers from ``puspa/billing/config.py`` (never invented prices),
trial status is JSON, and the onboarding agent file is really
written from the answers — the test reads the persona back via
GET /api/personas/{slug} and checks the answers appear in the
generated files.
"""

from fastapi.testclient import TestClient

from puspa.billing.config import default_config
from puspa.pwa.app import create_app
from puspa.pwa.onboarding_api import build_agent_files


def _client(user_id, test_dsn, tmp_path):
    # persona_home is a temp dir: no test ever touches the live home.
    app = create_app(test_user_id=user_id, dsn=test_dsn, persona_home=tmp_path)
    return TestClient(app)


def test_space_port_routes_exist(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn, persona_home=tmp_path)
    paths = {route.path for route in app.routes}
    for path in (
        "/api/billing/plan",
        "/api/trial/status",
        "/api/onboarding/agent-file",
    ):
        assert path in paths, path
    anon = create_app(test_user_id=None, dsn=test_dsn, persona_home=tmp_path)
    with TestClient(anon) as client:
        assert client.get("/api/billing/plan").status_code == 401
        assert client.get("/api/trial/status").status_code == 401
        assert client.post("/api/onboarding/agent-file", json={}).status_code == 401


def test_billing_plan_matches_config(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn, tmp_path) as client:
        resp = client.get("/api/billing/plan")
        assert resp.status_code == 200
        body = resp.json()
        # The endpoint mirrors the config value (the model itself —
        # credits vs subscription — is Nero's open decision, so the
        # test asserts the mirror, not a hardcoded model).
        assert body["billing_model"] == default_config().billing_model
        tiers = {tier["id"]: tier for tier in body["tiers"]}
        # The real config numbers (puspa/billing/config.py), not the
        # Space's demo numbers (Plus 100 / Pro 300 credits).
        assert tiers["free"]["price_idr_per_month"] == 0
        assert tiers["free"]["monthly_credits"] == 15
        assert tiers["plus"]["price_idr_per_month"] == 99000
        assert tiers["plus"]["monthly_credits"] == 60
        assert tiers["pro"]["price_idr_per_month"] == 199000
        assert tiers["pro"]["monthly_credits"] == 150
        assert tiers["team"]["price_idr_per_month"] == 499000
        assert tiers["team"]["monthly_credits"] == 400


def test_trial_status_json(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn, tmp_path) as client:
        resp = client.get("/api/trial/status")
        assert resp.status_code == 200
        body = resp.json()
        assert body["status"] in ("active", "grace", "expired", "none")
        assert isinstance(body["is_trial"], bool)


def test_build_agent_files_contains_answers():
    files = build_agent_files(
        agent_name="Mawar",
        user_name="Nero",
        style="rapi",
        purposes=["price_compare", "pay_bills"],
        needs="Skripsi, Kerja remote",
        data_sources=["chat", "memory"],
    )
    assert "Mawar" in files["soul"]
    assert "Name: Mawar" in files["identity"]
    assert "Nero" in files["user"]
    assert "Bandingkan harga" in files["soul"]
    assert "Bayar tagihan" in files["soul"]
    assert "Bahasa baku yang sopan" in files["soul"]
    assert "Skripsi, Kerja remote" in files["soul"]
    assert "Pesan chat" in files["user"]
    assert "Memori & fakta" in files["user"]


def test_agent_file_writes_persona(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn, tmp_path) as client:
        resp = client.post(
            "/api/onboarding/agent-file",
            json={
                "agent_name": "Mawar",
                "user_name": "Nero",
                "style": "santai",
                "purposes": ["research", "organize"],
                "needs": "Skripsi",
                "data_sources": ["chat", "memory", "connectors", "receipts"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["slug"] == "mawar"
        assert "Riset & belajar" in body["soul"]
        assert "Atur jadwal & tugas" in body["soul"]
        assert "Name: Mawar" in body["identity"]
        # The file is really on disk: read the persona back.
        read_back = client.get("/api/personas/mawar")
        assert read_back.status_code == 200
        assert "Riset & belajar" in read_back.json()["soul"]
        # Re-running with the same name updates instead of failing.
        again = client.post(
            "/api/onboarding/agent-file",
            json={
                "agent_name": "Mawar",
                "user_name": "Nero",
                "style": "rapi",
                "purposes": ["pay_bills"],
                "needs": "",
                "data_sources": ["chat"],
            },
        )
        assert again.status_code == 200
        assert "Bayar tagihan" in again.json()["soul"]
        assert "Bahasa baku yang sopan" in again.json()["soul"]


def test_agent_file_validation(make_user, test_dsn, tmp_path):
    user_id = make_user()["user_id"]
    with _client(user_id, test_dsn, tmp_path) as client:
        assert (
            client.post("/api/onboarding/agent-file", json={"agent_name": "  "}).status_code
            == 400
        )
        assert (
            client.post(
                "/api/onboarding/agent-file",
                json={"agent_name": "Mawar", "style": "formal"},
            ).status_code
            == 400
        )
