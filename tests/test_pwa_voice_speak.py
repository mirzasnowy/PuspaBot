"""API tests for the V2-30 voice endpoints (speak + settings + per-user ASR).

The speak chain is exercised end to end through the HTTP layer:
Edge TTS via a fake WebSocket peer, BYOK ElevenLabs via an httpx
mock transport, browser fallback as JSON, and a missing key as a
loud 400. Uploads go through the user's own ASR endpoint when
one is configured (mock transport), else the stub. No test
touches the network or a real key.
"""

import httpx
import pytest
from fastapi.testclient import TestClient

from puspa.pwa.app import create_app
from puspa.pwa.voice_settings import create_schema, save_settings, save_voice_key

FAKE_KEY = "test-voice-key-not-real"


class FakeWS:
    """A fake Edge TTS peer: replays queued frames, records sends."""

    def __init__(self, frames):
        self.frames = list(frames)
        self.sent: list[str] = []

    async def __aenter__(self):
        return self

    async def __aexit__(self, *args):
        return False

    async def send(self, message):
        self.sent.append(message)

    async def recv(self):
        if not self.frames:
            raise ConnectionError("peer closed")
        return self.frames.pop(0)


def _audio_frame(payload: bytes) -> bytes:
    return b"Path:audio\r\nContent-Type:audio/mpeg\r\n\r\n" + payload


def _edge_connect_ok():
    def connect(url, headers):
        return FakeWS(
            [
                "X-Timestamp:0\r\nPath:turn.start\r\n\r\n",
                _audio_frame(b"EDGE-MP3"),
                "X-Timestamp:0\r\nPath:turn.end\r\n\r\n",
            ]
        )

    return connect


def _edge_connect_broken():
    def connect(url, headers):
        raise ConnectionError("endpoint blocked")

    return connect


@pytest.fixture
def client(make_user, test_dsn):
    u = make_user()
    create_schema(test_dsn)
    app = create_app(test_user_id=u["user_id"], voice_edge_connect=_edge_connect_ok())
    with TestClient(app, raise_server_exceptions=False) as c:
        c.user_id = u["user_id"]
        yield c


def test_speak_default_edge_returns_audio(client):
    resp = client.post("/api/voice/speak", json={"text": "Halo, ini balasan."})
    assert resp.status_code == 200
    assert resp.content == b"EDGE-MP3"
    assert resp.headers["content-type"] == "audio/mpeg"
    assert resp.headers["x-voice-engine"] == "edge"


def test_speak_edge_failure_falls_back_to_browser(client, make_user, test_dsn):
    u = make_user()
    app = create_app(test_user_id=u["user_id"], voice_edge_connect=_edge_connect_broken())
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/voice/speak", json={"text": "Halo"})
    assert resp.status_code == 502
    assert resp.json()["fallback"] == "browser"


def test_speak_browser_provider_never_calls_a_server_engine(client):
    assert client.post("/api/voice/settings", json={"tts_provider": "browser"}).status_code == 200
    resp = client.post("/api/voice/speak", json={"text": "Halo"})
    assert resp.status_code == 200
    assert resp.json() == {"ok": True, "fallback": "browser", "engine": "browser"}


def test_speak_elevenlabs_with_vault_key(client, make_user, test_dsn, vault_master_key):
    def handler(request: httpx.Request) -> httpx.Response:
        assert request.headers.get("xi-api-key") == FAKE_KEY
        return httpx.Response(200, content=b"ELEVEN-MP3")

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    u = make_user()
    save_settings(
        u["user_id"],
        {"tts_provider": "elevenlabs", "tts_voice": "voice-abc"},
        dsn=test_dsn,
    )
    save_voice_key(u["user_id"], "tts", "elevenlabs", FAKE_KEY, dsn=test_dsn)
    app = create_app(test_user_id=u["user_id"], voice_http_client=http_client)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post("/api/voice/speak", json={"text": "Halo"})
    assert resp.status_code == 200
    assert resp.content == b"ELEVEN-MP3"
    assert resp.headers["x-voice-engine"] == "elevenlabs"


def test_speak_byok_without_key_fails_loudly_with_browser_fallback(client):
    assert (
        client.post(
            "/api/voice/settings",
            json={"tts_provider": "elevenlabs", "tts_voice": "voice-abc"},
        ).status_code
        == 200
    )
    resp = client.post("/api/voice/speak", json={"text": "Halo"})
    assert resp.status_code == 400
    assert resp.json()["fallback"] == "browser"
    assert "no TTS key stored" in resp.json()["error"]


def test_speak_rejects_empty_and_overlong_text(client):
    assert client.post("/api/voice/speak", json={"text": "  "}).status_code == 400
    assert client.post("/api/voice/speak", json={"text": "x" * 2001}).status_code == 400


def test_voice_settings_api_round_trip(client, vault_master_key):
    resp = client.get("/api/voice/settings")
    assert resp.status_code == 200
    assert resp.json()["tts_provider"] == "edge"
    assert resp.json()["tts_voice"] == "id-ID-GadisNeural"
    assert "key" not in str(resp.json()).lower() or resp.json()["has_tts_key"] is False
    saved = client.post(
        "/api/voice/settings",
        json={"tts_provider": "edge", "tts_voice": "id-ID-ArdiNeural"},
    )
    assert saved.status_code == 200
    assert saved.json()["tts_voice"] == "id-ID-ArdiNeural"
    assert client.get("/api/voice/settings").json()["tts_voice"] == "id-ID-ArdiNeural"


def test_voice_settings_api_refuses_key_in_body(client):
    resp = client.post("/api/voice/settings", json={"tts_provider": "edge", "api_key": FAKE_KEY})
    assert resp.status_code == 400
    assert "vault" in resp.json()["error"]


def test_voice_key_api_stores_in_vault_only(client, vault_master_key):
    resp = client.post(
        "/api/voice/key",
        json={"kind": "tts", "provider": "elevenlabs", "key": FAKE_KEY},
    )
    assert resp.status_code == 200
    settings = client.get("/api/voice/settings").json()
    assert settings["has_tts_key"] is False  # elevenlabs not the chosen provider yet
    assert FAKE_KEY not in str(settings)
    assert (
        client.post(
            "/api/voice/settings",
            json={"tts_provider": "elevenlabs", "tts_voice": "voice-abc"},
        ).status_code
        == 200
    )
    assert client.get("/api/voice/settings").json()["has_tts_key"] is True
    assert client.delete("/api/voice/key?kind=tts&provider=elevenlabs").status_code == 200
    assert client.get("/api/voice/settings").json()["has_tts_key"] is False


def test_upload_uses_the_users_own_asr_endpoint(make_user, test_dsn, vault_master_key):
    def handler(request: httpx.Request) -> httpx.Response:
        assert str(request.url).endswith("/audio/transcriptions")
        assert request.headers.get("authorization") == f"Bearer {FAKE_KEY}"
        return httpx.Response(200, json={"text": "tolong cek harga beras", "language": "id"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    u = make_user()
    create_schema(test_dsn)
    save_settings(
        u["user_id"],
        {"asr_provider": "openai-compatible", "asr_base_url": "https://api.openai.com/v1"},
        dsn=test_dsn,
    )
    save_voice_key(u["user_id"], "asr", "openai-compatible", FAKE_KEY, dsn=test_dsn)
    app = create_app(test_user_id=u["user_id"], voice_http_client=http_client)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post(
            "/api/voice/upload",
            files={"audio": ("rec.webm", b"fake-recording", "audio/webm")},
            data={"keep_audio": "false"},
        )
    assert resp.status_code == 200
    body = resp.json()
    assert body["transcript"] == "tolong cek harga beras"
    assert body["engine"] == "openai-compatible"
    assert body["audio_deleted"] is True


def test_upload_asr_endpoint_failure_is_a_loud_502(make_user, test_dsn, vault_master_key):
    def handler(request):
        return httpx.Response(401, json={"error": "bad key"})

    http_client = httpx.Client(transport=httpx.MockTransport(handler))
    u = make_user()
    create_schema(test_dsn)
    save_settings(
        u["user_id"],
        {"asr_provider": "openai-compatible", "asr_base_url": "https://api.openai.com/v1"},
        dsn=test_dsn,
    )
    save_voice_key(u["user_id"], "asr", "openai-compatible", FAKE_KEY, dsn=test_dsn)
    app = create_app(test_user_id=u["user_id"], voice_http_client=http_client)
    with TestClient(app, raise_server_exceptions=False) as c:
        resp = c.post(
            "/api/voice/upload",
            files={"audio": ("rec.webm", b"fake-recording", "audio/webm")},
            data={"keep_audio": "false"},
        )
    assert resp.status_code == 502
    assert "refused the key" in resp.json()["error"]


def test_voice_status_reports_the_users_own_asr_choice(client, vault_master_key):
    body = client.get("/api/voice/status").json()
    assert body["engine"] == "stub"
    assert body["engine_selected"] is False
    assert body["selection"] == "pending"
    assert body["tts_provider"] == "edge"
    assert (
        client.post(
            "/api/voice/settings",
            json={"asr_provider": "openai-compatible", "asr_base_url": "https://api.openai.com/v1"},
        ).status_code
        == 200
    )
    assert (
        client.post(
            "/api/voice/key",
            json={"kind": "asr", "provider": "openai-compatible", "key": FAKE_KEY},
        ).status_code
        == 200
    )
    body = client.get("/api/voice/status").json()
    assert body["engine"] == "openai-compatible"
    assert body["engine_selected"] is True
    assert body["selection"] == "done"
    assert body["has_asr_key"] is True
