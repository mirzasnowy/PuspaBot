"""Tests for the agent avatar photo API (V2-26).

Upload -> serve -> delete, with validation (type, size, bytes
that do not decode as an image), per-user isolation, and the
default state (no photo) before anything is uploaded.
"""

from pathlib import Path

import pytest
from fastapi.testclient import TestClient
from PIL import Image

from puspa.pwa.app import create_app
from puspa.pwa.avatar_api import MAX_AVATAR_BYTES

FIXTURES = Path(__file__).parent / "fixtures" / "receipts"
WARUNG_PNG = (FIXTURES / "photo-warung.png").read_bytes()


@pytest.fixture
def client(make_user, tmp_path):
    u = make_user()
    app = create_app(
        test_user_id=u["user_id"],
        share_inbox_dir=tmp_path / "inbox",
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = u["user_id"]
        c.inbox = tmp_path / "inbox"
        yield c


@pytest.fixture
def other_client(make_user, tmp_path):
    u = make_user()
    app = create_app(
        test_user_id=u["user_id"],
        share_inbox_dir=tmp_path / "inbox",
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = u["user_id"]
        c.inbox = tmp_path / "inbox"
        yield c


def _upload(c, photo=WARUNG_PNG, mime="image/png", name="puspa.png"):
    return c.post("/api/agent/avatar/photo", files={"photo": (name, photo, mime)})


def test_no_photo_before_upload_and_photo_bytes_404(client):
    status = client.get("/api/agent/avatar").json()
    assert status["ok"] is True
    assert status["has_photo"] is False
    assert status["photo_url"] is None
    assert status["updated_at"] is None
    resp = client.get("/api/agent/avatar/photo")
    assert resp.status_code == 404
    assert resp.json()["has_photo"] is False


def test_upload_stores_one_photo_per_user_and_serves_it_back(client):
    resp = _upload(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["has_photo"] is True
    assert body["photo_url"] == "/api/agent/avatar/photo"
    assert body["mime_type"] == "image/png"
    assert body["updated_at"]
    # Stored under the user's own dir, one photo file only (the
    # avatar-source.json marker beside it records which avatar
    # is active and is not a photo).
    kept = client.inbox / client.user_id / "avatar"
    assert kept.is_dir()
    assert [p.name for p in kept.iterdir() if p.name.startswith("avatar.")] == [
        "avatar.png"
    ]
    # The status endpoint agrees, and the bytes round-trip.
    status = client.get("/api/agent/avatar").json()
    assert status["has_photo"] is True
    served = client.get("/api/agent/avatar/photo")
    assert served.status_code == 200
    assert served.headers["content-type"] == "image/png"
    assert served.content == WARUNG_PNG


def test_upload_replaces_the_previous_photo_and_old_extension(client):
    assert _upload(client).status_code == 200
    kept = client.inbox / client.user_id / "avatar"
    assert [p.name for p in kept.iterdir() if p.name.startswith("avatar.")] == [
        "avatar.png"
    ]
    # A jpeg upload replaces the png; no stale file lingers.
    import io

    buf = io.BytesIO()
    Image.new("RGB", (8, 8), "red").save(buf, format="JPEG")
    resp = _upload(client, photo=buf.getvalue(), mime="image/jpeg", name="puspa.jpg")
    assert resp.status_code == 200
    assert resp.json()["mime_type"] == "image/jpeg"
    assert [p.name for p in kept.iterdir() if p.name.startswith("avatar.")] == [
        "avatar.jpg"
    ]
    served = client.get("/api/agent/avatar/photo")
    assert served.headers["content-type"] == "image/jpeg"


def test_upload_rejects_wrong_type_empty_oversize_and_non_image(client):
    assert _upload(client, photo=b"x", mime="application/pdf").status_code == 400
    assert _upload(client, photo=b"", mime="image/png").status_code == 400
    assert _upload(client, photo=b"plain text, not a photo", mime="image/png").status_code == 400
    # HEIC is a real photo type but the PWA cannot display it.
    assert _upload(client, photo=WARUNG_PNG, mime="image/heic").status_code == 400
    big = b"\x89PNG\r\n\x1a\n" + b"\x00" * (MAX_AVATAR_BYTES + 1)
    assert _upload(client, photo=big, mime="image/png").status_code == 400
    # Nothing was stored by any rejected upload.
    kept = client.inbox / client.user_id / "avatar"
    assert not kept.exists() or not list(kept.iterdir())


def test_delete_removes_the_photo_and_is_idempotent(client):
    assert _upload(client).status_code == 200
    resp = client.delete("/api/agent/avatar/photo")
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "has_photo": False, "source": "default"}
    assert client.get("/api/agent/avatar").json()["source"] == "default"
    assert client.get("/api/agent/avatar").json()["has_photo"] is False
    assert client.get("/api/agent/avatar/photo").status_code == 404
    # Deleting again is still ok.
    assert client.delete("/api/agent/avatar/photo").status_code == 200


def test_photos_are_per_user(client, other_client):
    assert _upload(client).status_code == 200
    # The second user has no photo and cannot fetch the first one's.
    assert other_client.get("/api/agent/avatar").json()["has_photo"] is False
    assert other_client.get("/api/agent/avatar/photo").status_code == 404
    # Deleting as the second user does not touch the first one's photo.
    assert other_client.delete("/api/agent/avatar/photo").status_code == 200
    assert client.get("/api/agent/avatar").json()["has_photo"] is True


def test_routes_require_login(tmp_path):
    app = create_app(share_inbox_dir=tmp_path / "inbox")
    with TestClient(app, raise_server_exceptions=False) as c:
        assert _upload(c).status_code == 401
        assert c.get("/api/agent/avatar").status_code == 401
        assert c.get("/api/agent/avatar/photo").status_code == 401
        assert c.delete("/api/agent/avatar/photo").status_code == 401


def test_builtin_avatar_set_serves_the_shipped_file(client):
    from puspa.pwa.avatar_api import BUILTIN_AVATARS, _BUILTIN_DIR

    resp = client.post("/api/agent/avatar/builtin", json={"name": "sakura"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["has_photo"] is True
    assert body["source"] == "builtin:sakura"
    assert body["mime_type"] == "image/png"
    photo = client.get("/api/agent/avatar/photo")
    assert photo.status_code == 200
    assert photo.content == (_BUILTIN_DIR / BUILTIN_AVATARS["sakura"]).read_bytes()
    # Switching to another flower replaces the first one wholesale.
    resp = client.post("/api/agent/avatar/builtin", json={"name": "tulip"})
    assert resp.json()["source"] == "builtin:tulip"
    kept = client.inbox / client.user_id / "avatar"
    assert sorted(p.name for p in kept.iterdir()) == [
        "avatar-source.json",
        "avatar.png",
    ]
    # GET /api/agent/avatar agrees with the stored choice.
    assert client.get("/api/agent/avatar").json()["source"] == "builtin:tulip"


def test_builtin_avatar_unknown_name_is_rejected(client):
    assert client.post("/api/agent/avatar/builtin", json={"name": "robot"}).status_code == 400
    assert client.post("/api/agent/avatar/builtin", json={"name": "cat"}).status_code == 400
    assert client.post("/api/agent/avatar/builtin", json={}).status_code == 400
    assert client.post("/api/agent/avatar/builtin", json={"name": 7}).status_code == 400
    # Nothing was stored by the rejected picks.
    assert client.get("/api/agent/avatar").json()["has_photo"] is False


def test_builtin_then_upload_replaces_and_reports_upload(client):
    assert client.post("/api/agent/avatar/builtin", json={"name": "rose"}).status_code == 200
    assert _upload(client).status_code == 200
    body = client.get("/api/agent/avatar").json()
    assert body["source"] == "upload"
    assert body["mime_type"] == "image/png"


def test_upload_reports_upload_source_and_default_before_any_photo(client):
    assert client.get("/api/agent/avatar").json()["source"] == "default"
    assert _upload(client).status_code == 200
    assert client.get("/api/agent/avatar").json()["source"] == "upload"


def test_builtin_route_requires_login(tmp_path):
    app = create_app(share_inbox_dir=tmp_path / "inbox")
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.post("/api/agent/avatar/builtin", json={"name": "tulip"}).status_code == 401
