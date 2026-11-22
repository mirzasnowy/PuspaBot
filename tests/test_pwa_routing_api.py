"""API tests for the per-capability routing endpoints (COW-08).

Covers the HTTP layer only (the persistence / validation /
resolver rules live in tests/test_capability_routing.py): a
logged-in user can read the routing table, swap one
capability's provider with one click, reset one capability,
and save a provider key; a provider that cannot serve a
capability is a 400 naming the providers that can; a key sent
with a route is refused; and no response ever carries key
material.
"""

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.pwa.capability_routing import create_schema

FAKE_KEY = "test-byok-key-not-real"


@pytest.fixture
def client(make_user, test_dsn):
    u = make_user()
    create_schema(test_dsn)
    app = create_app(test_user_id=u["user_id"])
    with TestClient(app) as c:
        c.user_id = u["user_id"]
        yield c


def test_get_routing_defaults(client):
    resp = client.get("/api/routing")
    assert resp.status_code == 200
    body = resp.json()
    assert set(body["capabilities"]) == {"chat", "vision", "image", "speech", "embeddings"}
    assert body["capabilities"]["chat"]["provider"] == "hosted"
    assert body["capabilities"]["chat"]["is_default"] is True
    assert body["capabilities"]["image"]["provider"] is None
    assert "deepseek" not in body["providers"]["vision"]
    assert "groq" in body["providers"]["chat"]
    assert FAKE_KEY not in resp.text
    assert "api_key" not in resp.text


def test_swap_one_capability_leaves_the_others_alone(client):
    assert (
        client.post(
            "/api/routing/chat", json={"provider": "groq", "model": "llama-3.3-70b-versatile"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/routing/vision", json={"provider": "openai", "model": "gpt-4o"}
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/routing/image", json={"provider": "openai", "model": "dall-e-3"}
        ).status_code
        == 200
    )
    resp = client.post("/api/routing/chat", json={"provider": "deepseek", "model": "deepseek-chat"})
    assert resp.status_code == 200
    assert resp.json()["provider"] == "deepseek"
    assert resp.json()["is_default"] is False
    body = client.get("/api/routing").json()
    assert body["capabilities"]["chat"]["provider"] == "deepseek"
    assert body["capabilities"]["vision"]["provider"] == "openai"
    assert body["capabilities"]["vision"]["model"] == "gpt-4o"
    assert body["capabilities"]["image"]["provider"] == "openai"
    assert body["capabilities"]["image"]["model"] == "dall-e-3"


def test_swap_refuses_a_provider_that_cannot_serve(client):
    resp = client.post(
        "/api/routing/vision", json={"provider": "deepseek", "model": "deepseek-chat"}
    )
    assert resp.status_code == 400
    assert "cannot serve capability 'vision'" in resp.json()["error"]
    assert "openai" in resp.json()["error"]
    assert client.get("/api/routing").json()["capabilities"]["vision"]["is_default"] is True
    resp = client.post("/api/routing/image", json={"provider": "hosted"})
    assert resp.status_code == 400
    assert "cannot serve capability 'image'" in resp.json()["error"]


def test_swap_refuses_missing_model_and_key_field(client):
    resp = client.post("/api/routing/chat", json={"provider": "openai"})
    assert resp.status_code == 400
    assert "needs a model" in resp.json()["error"]
    resp = client.post(
        "/api/routing/chat",
        json={"provider": "openai", "model": "gpt-4o", "api_key": FAKE_KEY},
    )
    assert resp.status_code == 400
    assert "vault" in resp.json()["error"]
    assert client.get("/api/routing").json()["capabilities"]["chat"]["is_default"] is True


def test_swap_unknown_capability_is_404(client):
    assert client.post("/api/routing/translate", json={"provider": "openai"}).status_code == 404
    assert client.delete("/api/routing/translate").status_code == 404


def test_reset_one_capability(client):
    assert (
        client.post(
            "/api/routing/chat", json={"provider": "groq", "model": "llama-3.3-70b-versatile"}
        ).status_code
        == 200
    )
    resp = client.delete("/api/routing/chat")
    assert resp.status_code == 200
    assert resp.json()["provider"] == "hosted"
    assert resp.json()["is_default"] is True


def test_routing_key_round_trip_never_returned(client, vault_master_key):
    assert (
        client.post(
            "/api/routing/chat", json={"provider": "groq", "model": "llama-3.3-70b-versatile"}
        ).status_code
        == 200
    )
    assert client.get("/api/routing").json()["capabilities"]["chat"]["has_key"] is False
    saved = client.post("/api/routing/key", json={"provider": "groq", "key": FAKE_KEY})
    assert saved.status_code == 200
    assert FAKE_KEY not in saved.text
    body = client.get("/api/routing").json()
    assert body["capabilities"]["chat"]["has_key"] is True
    assert FAKE_KEY not in repr(body)
    assert client.delete("/api/routing/key?provider=groq").status_code == 200
    assert client.get("/api/routing").json()["capabilities"]["chat"]["has_key"] is False


def test_routing_requires_login():
    app = create_app(test_user_id=None)
    with TestClient(app) as c:
        assert c.get("/api/routing").status_code == 401
        assert (
            c.post("/api/routing/chat", json={"provider": "groq", "model": "x"}).status_code == 401
        )
        assert c.delete("/api/routing/chat").status_code == 401
        assert c.post("/api/routing/key", json={"provider": "groq", "key": "x"}).status_code == 401
