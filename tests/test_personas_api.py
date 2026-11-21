"""Tests for the switchable-personas HTTP layer
(``puspa/pwa/personas_api.py``) against the real
``puspa.pwa.app.create_app``.

The persona home is a temporary directory injected through the
``persona_home`` seam, so no test ever touches the live home.
Covers the full flow from the PWA: list, create two personas,
switch the active one, read, edit, delete, and the login +
validation errors.
"""

from __future__ import annotations

from fastapi.testclient import TestClient

from puspa.pwa.app import create_app


def _client(user_id, tmp_path):
    app = create_app(test_user_id=user_id, persona_home=tmp_path)
    return TestClient(app)


def test_routes_exist_and_require_login(make_user, tmp_path):
    user_id = make_user()["user_id"]
    client = _client(user_id, tmp_path)
    paths = {route.path for route in client.app.routes}
    assert "/api/personas" in paths
    assert "/api/personas/{slug}" in paths
    assert "/api/personas/{slug}/activate" in paths

    anon = _client(None, tmp_path)
    assert anon.get("/api/personas").status_code == 401
    assert anon.post("/api/personas", json={"name": "Kerja"}).status_code == 401
    assert anon.get("/api/personas/kerja").status_code == 401
    assert anon.patch("/api/personas/kerja", json={"soul": "x"}).status_code == 401
    assert anon.delete("/api/personas/kerja").status_code == 401
    assert anon.post("/api/personas/kerja/activate").status_code == 401


def test_full_flow_create_switch_read_edit_delete(make_user, tmp_path):
    user_id = make_user()["user_id"]
    client = _client(user_id, tmp_path)

    # Only the default persona exists at first, and it is active.
    resp = client.get("/api/personas")
    assert resp.status_code == 200
    assert resp.json() == {
        "personas": [
            {
                "slug": "default",
                "name": "Puspa",
                "style": None,
                "is_active": True,
                "is_default": True,
            }
        ],
        "active": "default",
    }

    # Create two personas: one for work, one for personal use.
    kerja = client.post(
        "/api/personas",
        json={"name": "Kerja", "soul": "Fokus kerja.", "style": "rapi"},
    )
    assert kerja.status_code == 200
    assert kerja.json()["slug"] == "kerja"
    assert kerja.json()["name"] == "Kerja"
    assert kerja.json()["style"] == "rapi"
    personal = client.post(
        "/api/personas",
        json={"name": "Personal", "soul": "Santai aja, kayak teman."},
    )
    assert personal.status_code == 200
    assert personal.json()["slug"] == "personal"

    listed = client.get("/api/personas").json()
    assert [item["slug"] for item in listed["personas"]] == [
        "default",
        "kerja",
        "personal",
    ]
    assert listed["active"] == "default"

    # Switch to the work persona: the old one stays stored.
    switched = client.post("/api/personas/kerja/activate")
    assert switched.status_code == 200
    assert switched.json()["active"] == "kerja"
    assert client.get("/api/personas").json()["active"] == "kerja"

    read = client.get("/api/personas/kerja")
    assert read.status_code == 200
    assert read.json()["soul"] == "Fokus kerja."
    assert read.json()["is_active"] is True
    assert client.get("/api/personas/personal").json()["soul"] == ("Santai aja, kayak teman.")

    # Edit only the prompt; name and style survive.
    edited = client.patch("/api/personas/kerja", json={"soul": "Fokus kerja, tanpa basa-basi."})
    assert edited.status_code == 200
    assert edited.json()["soul"] == "Fokus kerja, tanpa basa-basi."
    assert edited.json()["name"] == "Kerja"
    assert edited.json()["style"] == "rapi"

    # Delete the personal persona; the active work one is untouched.
    deleted = client.delete("/api/personas/personal")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": "personal", "active": "kerja"}
    assert client.get("/api/personas/personal").status_code == 404
    assert [item["slug"] for item in client.get("/api/personas").json()["personas"]] == [
        "default",
        "kerja",
    ]


def test_delete_active_persona_falls_back_to_default(make_user, tmp_path):
    user_id = make_user()["user_id"]
    client = _client(user_id, tmp_path)
    client.post("/api/personas", json={"name": "Kerja", "soul": "Fokus kerja."})
    client.post("/api/personas/kerja/activate")
    assert client.get("/api/personas").json()["active"] == "kerja"
    deleted = client.delete("/api/personas/kerja")
    assert deleted.status_code == 200
    assert deleted.json() == {"deleted": "kerja", "active": "default"}


def test_create_validation_errors(make_user, tmp_path):
    user_id = make_user()["user_id"]
    client = _client(user_id, tmp_path)
    assert client.post("/api/personas", json={}).status_code == 400
    assert client.post("/api/personas", json={"name": "  "}).status_code == 400
    assert client.post("/api/personas", json={"name": 12}).status_code == 400
    assert client.post("/api/personas", json={"name": "Kerja", "style": "galak"}).status_code == 400
    assert client.post("/api/personas", json={"name": "Kerja"}).status_code == 200
    # Same name twice: the slug already exists.
    assert client.post("/api/personas", json={"name": "kerja"}).status_code == 409
    assert client.post("/api/personas", json={"name": "Kerja"}).status_code == 409
    assert client.post("/api/personas", json={"name": "Default"}).status_code == 400


def test_unknown_slug_and_default_delete_errors(make_user, tmp_path):
    user_id = make_user()["user_id"]
    client = _client(user_id, tmp_path)
    assert client.get("/api/personas/tidak-ada").status_code == 404
    assert client.patch("/api/personas/tidak-ada", json={"soul": "x"}).status_code == 404
    assert client.delete("/api/personas/tidak-ada").status_code == 404
    assert client.post("/api/personas/tidak-ada/activate").status_code == 404
    # The default persona cannot be deleted, but it can be activated.
    assert client.delete("/api/personas/default").status_code == 400
    assert client.post("/api/personas/default/activate").status_code == 200
    assert client.get("/api/personas").json()["active"] == "default"


def test_personas_screen_serves(make_user, tmp_path):
    user_id = make_user()["user_id"]
    client = _client(user_id, tmp_path)
    resp = client.get("/personas")
    assert resp.status_code == 200
    assert "Puspa" in resp.text
