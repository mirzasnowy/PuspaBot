"""API tests for the G33 model-settings endpoints in puspa/pwa/app.py.

Covers the HTTP layer only (the persistence / validation /
router-wiring rules live in tests/test_pwa_model_settings.py):
a logged-in user can read and save a selection, a bad base URL
is a 400, a key sent with the selection is refused, and a BYOK
key saved through its own endpoint lands in the vault and is
never returned in any response.
"""

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.pwa.model_settings import create_schema


@pytest.fixture
def client(make_user, test_dsn):
    u = make_user()
    create_schema(test_dsn)
    app = create_app(test_user_id=u["user_id"])
    with TestClient(app) as c:
        c.user_id = u["user_id"]
        yield c


def test_get_model_settings_default_is_hosted(client):
    resp = client.get("/api/model-settings")
    assert resp.status_code == 200
    body = resp.json()
    assert body["source"] == "hosted"
    assert body["has_byok_key"] is False
    assert "api_key" not in body and "key" not in body


def test_save_and_read_local_selection(client):
    resp = client.post(
        "/api/model-settings",
        json={"source": "local", "base_url": "http://localhost:11434/v1", "model": "llama3.1"},
    )
    assert resp.status_code == 200
    assert resp.json()["source"] == "local"
    again = client.get("/api/model-settings")
    assert again.json()["base_url"] == "http://localhost:11434/v1"
    assert again.json()["model"] == "llama3.1"


def test_save_rejects_non_http_base_url(client):
    resp = client.post("/api/model-settings", json={"source": "local", "base_url": "ftp://x/v1"})
    assert resp.status_code == 400
    assert client.get("/api/model-settings").json()["source"] == "hosted"


def test_save_refuses_key_in_selection_body(client):
    resp = client.post(
        "/api/model-settings",
        json={"source": "byok", "provider": "openrouter", "api_key": "sk-should-not-land"},
    )
    assert resp.status_code == 400
    assert "vault" in resp.json()["error"]


def test_byok_key_round_trip_never_returned(client, vault_master_key):
    assert (
        client.post(
            "/api/model-settings", json={"source": "byok", "provider": "openrouter"}
        ).status_code
        == 200
    )
    assert client.get("/api/model-settings").json()["has_byok_key"] is False
    saved = client.post(
        "/api/model-settings/byok-key",
        json={"provider": "openrouter", "key": "test-byok-key-not-real"},
    )
    assert saved.status_code == 200
    assert "test-byok-key-not-real" not in saved.text
    body = client.get("/api/model-settings").json()
    assert body["has_byok_key"] is True
    assert "test-byok-key-not-real" not in repr(body)
    assert client.delete("/api/model-settings/byok-key?provider=openrouter").status_code == 200
    assert client.get("/api/model-settings").json()["has_byok_key"] is False


def test_model_settings_require_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as c:
        assert c.get("/api/model-settings").status_code == 401
        assert c.post("/api/model-settings", json={"source": "hosted"}).status_code == 401
