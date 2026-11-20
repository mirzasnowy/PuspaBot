"""API + store tests for the B7 mockup editor (``puspa/pwa/mockup_api.py``).

Covers the HTTP layer against the real ``puspa.pwa.app.create_app``:
template listing, prompt-to-mockup generation, storing a mockup,
applying numbered ops, word-change patches, undo/redo, asset upload
(data URI only), the critique endpoint, SVG/HTML rendering (with
the strict CSP header), PNG/PDF export, deletion, cross-user
isolation, and the 401 gate for anonymous callers. The mockup
schema is applied by the autouse fixture below (conftest does NOT
apply it).
"""

import pytest
from fastapi.testclient import TestClient

from puspa.mockup.store import MockupStore, create_schema, reset_tables
from puspa.mockup.templates import fill_template
from puspa.pwa.app import create_app

TINY_PNG = (
    "data:image/png;base64,iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJ"
    "AAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


@pytest.fixture(autouse=True)
def _mockup_schema(test_dsn):
    create_schema(test_dsn)
    reset_tables(test_dsn)
    yield
    reset_tables(test_dsn)


def _client(user_id, test_dsn):
    return TestClient(create_app(test_user_id=user_id, dsn=test_dsn))


def _create(client, template="promo_banner", fields=None):
    response = client.post(
        "/api/mockups",
        json={"template": template, "fields": fields or {"shop": "Warung Sari"}},
    )
    assert response.status_code == 200, response.text
    return response.json()


def test_routes_exist_and_require_login(make_user, test_dsn):
    user_id = make_user()["user_id"]
    app = create_app(test_user_id=user_id, dsn=test_dsn)
    paths = {route.path for route in app.routes}
    assert "/api/mockups/templates" in paths
    assert "/api/mockups/generate" in paths
    assert "/api/mockups" in paths
    assert "/api/mockups/{mockup_id}" in paths
    assert "/api/mockups/{mockup_id}/ops" in paths
    assert "/api/mockups/{mockup_id}/patch" in paths
    assert "/api/mockups/{mockup_id}/undo" in paths
    assert "/api/mockups/{mockup_id}/redo" in paths
    assert "/api/mockups/{mockup_id}/render.svg" in paths
    assert "/api/mockups/{mockup_id}/render.html" in paths
    assert "/api/mockups/{mockup_id}/export.png" in paths
    assert "/api/mockups/{mockup_id}/export.pdf" in paths
    anon = TestClient(create_app(test_user_id=None, dsn=test_dsn))
    with anon:
        assert anon.get("/api/mockups").status_code == 401
        assert anon.get("/api/mockups/templates").status_code == 401
        assert anon.post("/api/mockups/generate", json={"prompt": "promo"}).status_code == 401


def test_templates_endpoint_lists_five(make_user, test_dsn):
    client = _client(make_user()["user_id"], test_dsn)
    with client:
        body = client.get("/api/mockups/templates").json()
        assert [t["id"] for t in body["templates"]] == [
            "hajatan_invitation",
            "promo_banner",
            "warung_menu",
            "chat_catalog",
            "lebaran_card",
        ]
        assert "shop" in body["templates"][1]["fields"]


def test_generate_then_store_then_list(make_user, test_dsn):
    user_id = make_user()["user_id"]
    client = _client(user_id, test_dsn)
    with client:
        generated = client.post(
            "/api/mockups/generate", json={"prompt": "poster promo diskon toko Kopi Senja"}
        )
        assert generated.status_code == 200
        assert len(generated.json()["variants"]) == 3
        doc = generated.json()["variants"][0]["doc"]
        stored = client.post("/api/mockups", json={"doc": doc})
        assert stored.status_code == 200
        listed = client.get("/api/mockups").json()["mockups"]
        assert len(listed) == 1
        assert listed[0]["id"] == doc["id"]
        assert listed[0]["template"] == "promo_banner"


def test_create_rejects_unknown_template_and_script_doc(make_user, test_dsn):
    client = _client(make_user()["user_id"], test_dsn)
    with client:
        assert client.post("/api/mockups", json={"template": "cash_recap"}).status_code == 400
        doc = fill_template("promo_banner", {})
        doc["nodes"][0]["text"] = "<script>alert(1)</script>"
        assert client.post("/api/mockups", json={"doc": doc}).status_code == 400


def test_op_patch_undo_redo_round_trip(make_user, test_dsn):
    user_id = make_user()["user_id"]
    client = _client(user_id, test_dsn)
    with client:
        doc = _create(client)
        mockup_id = doc["id"]
        moved = client.post(
            f"/api/mockups/{mockup_id}/ops",
            json={"op": {"type": "move_node", "node_id": "price", "payload": {"x": 120, "y": 420}}},
        )
        assert moved.status_code == 200, moved.text
        assert moved.json()["version"] == 1
        assert moved.json()["ops"][0]["seq"] == 1
        patched = client.post(
            f"/api/mockups/{mockup_id}/patch", json={"instruction": "besarkan harga"}
        )
        assert patched.status_code == 200, patched.text
        assert patched.json()["version"] == 2
        undone = client.post(f"/api/mockups/{mockup_id}/undo")
        assert undone.status_code == 200
        assert undone.json()["version"] == 1
        redone = client.post(f"/api/mockups/{mockup_id}/redo")
        assert redone.status_code == 200
        assert redone.json()["version"] == 2
        bad = client.post(
            f"/api/mockups/{mockup_id}/ops",
            json={"op": {"type": "move_node", "node_id": "nope", "payload": {"x": 1, "y": 1}}},
        )
        assert bad.status_code == 400
        assert (
            client.post(
                f"/api/mockups/{mockup_id}/patch", json={"instruction": "sulap"}
            ).status_code
            == 400
        )


def test_asset_upload_is_an_op_and_survives_later_ops(make_user, test_dsn):
    """P-06: uploading an asset writes a numbered add_asset op.

    Before P-06 the endpoint mutated the stored doc without an op,
    so the version stayed put and the cached undo session kept the
    pre-upload doc; the next op then overwrote the stored doc and
    the asset was silently lost.
    """
    user_id = make_user()["user_id"]
    client = _client(user_id, test_dsn)
    with client:
        doc = _create(client)
        mockup_id = doc["id"]
        moved = client.post(
            f"/api/mockups/{mockup_id}/ops",
            json={"op": {"type": "move_node", "node_id": "price", "payload": {"x": 120, "y": 420}}},
        )
        assert moved.status_code == 200, moved.text
        uploaded = client.post(
            f"/api/mockups/{mockup_id}/assets",
            json={"asset_id": "logo", "data_uri": TINY_PNG, "name": "logo.png"},
        )
        assert uploaded.status_code == 200, uploaded.text
        assert uploaded.json()["version"] == 2
        assert uploaded.json()["ops"][-1]["type"] == "add_asset"
        assert uploaded.json()["ops"][-1]["seq"] == 2
        assert "logo" in uploaded.json()["assets"]
        moved2 = client.post(
            f"/api/mockups/{mockup_id}/ops",
            json={"op": {"type": "set_text", "node_id": "price", "payload": {"text": "Rp20.000"}}},
        )
        assert moved2.status_code == 200, moved2.text
        fetched = client.get(f"/api/mockups/{mockup_id}").json()
        assert "logo" in fetched["assets"]
        assert fetched["version"] == 3


def test_asset_upload_only_accepts_image_data_uris(make_user, test_dsn):
    user_id = make_user()["user_id"]
    client = _client(user_id, test_dsn)
    with client:
        doc = _create(client)
        mockup_id = doc["id"]
        ok = client.post(
            f"/api/mockups/{mockup_id}/assets",
            json={"asset_id": "logo", "data_uri": TINY_PNG, "name": "logo.png"},
        )
        assert ok.status_code == 200, ok.text
        assert ok.json()["assets"]["logo"]["data_uri"] == TINY_PNG
        bad = client.post(
            f"/api/mockups/{mockup_id}/assets",
            json={"asset_id": "evil", "data_uri": "https://example.com/x.png"},
        )
        assert bad.status_code == 400
        linked = client.post(
            f"/api/mockups/{mockup_id}/ops",
            json={
                "op": {"type": "set_image", "node_id": "headline", "payload": {"asset_id": "logo"}}
            },
        )
        assert linked.status_code == 200


def test_render_export_and_critique_endpoints(make_user, test_dsn):
    user_id = make_user()["user_id"]
    client = _client(user_id, test_dsn)
    with client:
        doc = _create(client)
        mockup_id = doc["id"]
        svg = client.get(f"/api/mockups/{mockup_id}/render.svg")
        assert svg.status_code == 200
        assert svg.headers["content-type"].startswith("image/svg+xml")
        assert svg.text.startswith("<svg")
        page = client.get(f"/api/mockups/{mockup_id}/render.html")
        assert page.status_code == 200
        assert "sandbox" in page.headers["content-security-policy"]
        assert "<script" not in page.text
        png = client.get(f"/api/mockups/{mockup_id}/export.png")
        assert png.status_code == 200
        assert png.content[:8] == b"\x89PNG\r\n\x1a\n"
        pdf = client.get(f"/api/mockups/{mockup_id}/export.pdf")
        assert pdf.status_code == 200
        assert pdf.content.startswith(b"%PDF-1.4")
        critique = client.get(f"/api/mockups/{mockup_id}/critique").json()
        assert isinstance(critique["issues"], list)
        assert client.get("/api/mockups/nope/render.svg").status_code == 404


def test_cross_user_isolation_and_delete(make_user, test_dsn):
    first = make_user()["user_id"]
    second = make_user()["user_id"]
    client = _client(first, test_dsn)
    with client:
        doc = _create(client)
        mockup_id = doc["id"]
    other = _client(second, test_dsn)
    with other:
        assert other.get(f"/api/mockups/{mockup_id}").status_code == 404
        assert other.get("/api/mockups").json()["mockups"] == []
    with client:
        assert client.delete(f"/api/mockups/{mockup_id}").status_code == 200
        assert client.get(f"/api/mockups/{mockup_id}").status_code == 404


def test_store_round_trip_directly(make_user, test_dsn):
    user_id = make_user()["user_id"]
    store = MockupStore(dsn=test_dsn)
    doc = fill_template("lebaran_card", {"sender": "Keluarga Budi"})
    store.create(user_id, doc)
    assert store.get(user_id, doc["id"])["template"] == "lebaran_card"
    updated = store.apply(
        user_id,
        doc["id"],
        {"type": "set_text", "node_id": "sender", "payload": {"text": "Keluarga Ani"}},
    )
    assert updated["version"] == 1
    assert updated["ops"][0]["seq"] == 1
    with pytest.raises(KeyError):
        store.get("someone-else", doc["id"])
