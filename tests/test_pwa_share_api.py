"""Tests for the Web Share Target (P-03, plan section 12.4).

The installed PWA appears in Android's share menu; shared text/URL
prefills a chat draft (nothing is sent until the user taps Send),
and shared files land in the per-user inbox and are announced in
chat.
"""

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.pwa.share_api import compose_shared_text, sanitize_filename


@pytest.fixture
def client(make_user, tmp_path):
    u = make_user()
    app = create_app(test_user_id=u["user_id"], share_inbox_dir=tmp_path / "inbox")
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = u["user_id"]
        c.inbox = tmp_path / "inbox"
        yield c


@pytest.mark.unit
def test_compose_shared_text_skips_empty_parts_in_order():
    assert compose_shared_text("Judul", "isi pesan", "https://example.com") == (
        "Judul\n\nisi pesan\n\nhttps://example.com"
    )
    assert compose_shared_text("", "", "https://example.com") == "https://example.com"
    assert compose_shared_text("", "isi pesan", "") == "isi pesan"
    assert compose_shared_text("", "", "") == ""


@pytest.mark.unit
def test_sanitize_filename_strips_directories_and_odd_characters():
    assert sanitize_filename("../../etc/passwd") == "passwd"
    assert sanitize_filename("foto liburan!.png") == "foto_liburan_.png"
    assert sanitize_filename("") == "shared-file"


def test_manifest_declares_the_share_target(client):
    manifest = client.get("/manifest.json").json()
    target = manifest["share_target"]
    assert target["action"] == "/share"
    assert target["method"] == "POST"
    assert target["enctype"] == "multipart/form-data"
    assert target["params"]["title"] == "title"
    assert target["params"]["text"] == "text"
    assert target["params"]["url"] == "url"
    assert target["params"]["files"][0]["name"] == "files"


def test_share_get_prefills_the_chat_draft_without_sending(client):
    resp = client.get(
        "/share",
        params={"title": "Beras murah", "text": "cek harga ini", "url": "https://toko.example/x"},
    )
    assert resp.status_code == 200
    assert "Beras murah" in resp.text
    assert "cek harga ini" in resp.text
    assert "https://toko.example/x" in resp.text
    # Prefilled, not sent: the chat history holds no such message yet.
    assert "Beras murah" not in client.get("/chat").text


def test_share_post_text_only_prefills_the_draft(client):
    resp = client.post(
        "/share", data={"text": "tolong rangkum artikel ini", "url": "https://example.com/a"}
    )
    assert resp.status_code == 200
    assert "tolong rangkum artikel ini" in resp.text


def test_empty_share_redirects_to_chat(client):
    resp = client.get("/share", follow_redirects=False)
    assert resp.status_code in (302, 307)
    assert resp.headers["location"] == "/chat"


def test_share_requires_login():
    app = create_app(test_user_id=None)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.get("/share", params={"text": "halo"}, follow_redirects=False)
        assert resp.status_code in (302, 307)
        assert "/masuk" in resp.headers["location"]


def test_share_post_file_is_stored_and_announced_in_chat(client):
    resp = client.post(
        "/share",
        files=[("files", ("foto.png", b"\x89PNG fake", "image/png"))],
        follow_redirects=False,
    )
    assert resp.status_code == 303
    assert resp.headers["location"] == "/chat"
    stored = list((client.inbox / client.user_id).iterdir())
    assert len(stored) == 1
    assert stored[0].read_bytes() == b"\x89PNG fake"
    page = client.get("/chat").text
    assert "foto.png" in page
    assert "Dibagikan dari luar Puspa" in page


def test_share_post_file_with_text_announces_both(client):
    resp = client.post(
        "/share",
        data={"text": "cek harga barang di foto ini"},
        files=[("files", ("struk.pdf", b"%PDF fake", "application/pdf"))],
        follow_redirects=False,
    )
    assert resp.status_code == 303
    page = client.get("/chat").text
    assert "struk.pdf" in page
    assert "cek harga barang di foto ini" in page


def test_share_post_rejects_too_many_files_and_video(client):
    many = [("files", (f"foto-{i}.png", b"fake", "image/png")) for i in range(6)]
    assert client.post("/share", files=many).status_code == 400
    video = [("files", ("klip.mp4", b"fake", "video/mp4"))]
    assert client.post("/share", files=video).status_code == 400
    # Rejected shares store nothing.
    user_dir = client.inbox / client.user_id
    assert not user_dir.exists() or not list(user_dir.iterdir())
