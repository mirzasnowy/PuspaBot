"""Tests for the voice-message API (P-03, plan section 12.4).

Upload -> transcript -> confirm-or-discard, with the audio deleted
by default and nothing sent before confirmation.
"""

import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.voice.transcriber import StubTranscriber

TRANSCRIPT = "tolong cek harga beras"


@pytest.fixture
def client(make_user, tmp_path):
    u = make_user()
    app = create_app(
        test_user_id=u["user_id"],
        transcriber=StubTranscriber(TRANSCRIPT),
        share_inbox_dir=tmp_path / "inbox",
    )
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = u["user_id"]
        c.inbox = tmp_path / "inbox"
        yield c


def _upload(c, audio=b"fake-recording", mime="audio/webm", keep=False):
    return c.post(
        "/api/voice/upload",
        files={"audio": ("rec.webm", audio, mime)},
        data={"keep_audio": "true" if keep else "false"},
    )


def test_upload_transcribes_and_deletes_audio_by_default(client):
    resp = _upload(client)
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["transcript"] == TRANSCRIPT
    assert body["engine"] == "stub"
    assert body["audio_deleted"] is True
    assert body["audio_path"] is None
    assert body["pending_id"]


def test_upload_keeps_audio_only_when_asked(client):
    resp = _upload(client, keep=True)
    assert resp.status_code == 200
    body = resp.json()
    assert body["audio_deleted"] is False
    assert body["audio_path"] is not None
    kept = client.inbox / client.user_id / "voice"
    assert kept.is_dir()
    assert list(kept.iterdir())


def test_confirm_sends_the_transcript_as_a_chat_message(client):
    pending_id = _upload(client).json()["pending_id"]
    resp = client.post("/api/voice/confirm", json={"pending_id": pending_id})
    assert resp.status_code == 200
    assert resp.json()["ok"] is True
    assert resp.json()["message"]["text"] == TRANSCRIPT
    page = client.get("/chat")
    assert TRANSCRIPT in page.text
    # Single-use: confirming again finds nothing.
    assert client.post("/api/voice/confirm", json={"pending_id": pending_id}).status_code == 404


def test_confirm_sends_the_edited_text_not_the_raw_transcript(client):
    pending_id = _upload(client).json()["pending_id"]
    resp = client.post(
        "/api/voice/confirm", json={"pending_id": pending_id, "text": "cek harga gula"}
    )
    assert resp.status_code == 200
    assert resp.json()["message"]["text"] == "cek harga gula"
    page = client.get("/chat")
    assert "cek harga gula" in page.text


def test_discard_drops_the_pending_transcript_without_sending(client):
    pending_id = _upload(client).json()["pending_id"]
    assert client.post("/api/voice/discard", json={"pending_id": pending_id}).status_code == 200
    assert client.post("/api/voice/confirm", json={"pending_id": pending_id}).status_code == 404
    assert TRANSCRIPT not in client.get("/chat").text


def test_confirm_and_discard_reject_unknown_pending_ids(client):
    assert client.post("/api/voice/confirm", json={"pending_id": "nope"}).status_code == 404
    assert client.post("/api/voice/discard", json={"pending_id": "nope"}).status_code == 404


def test_upload_rejects_wrong_type_and_empty_audio(client):
    assert _upload(client, mime="video/mp4").status_code == 400
    assert _upload(client, audio=b"").status_code == 400


def test_voice_routes_require_login():
    app = create_app(test_user_id=None, transcriber=StubTranscriber(TRANSCRIPT))
    with TestClient(app, raise_server_exceptions=False) as c:
        assert c.get("/api/voice/status").status_code == 401
        assert _upload(c).status_code == 401


def test_voice_status_is_honest_about_engine_selection(client):
    body = client.get("/api/voice/status").json()
    assert body["engine"] == "stub"
    assert body["engine_selected"] is False
    assert body["selection"] == "pending"
    assert body["required_recordings"] == 50
