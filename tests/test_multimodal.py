"""Tests for multimodal messages (COW-19).

A message can carry an image, a voice note, or a file:
attachments are stored with the message and retrievable,
images reach the model as image blocks through the turn
pipeline, the routed vision model describes an attached
image, the routed image provider generates one, and a voice
note is transcribed through the existing ASR seam and kept
as an audio attachment.

No test touches a real provider or a real key: every
provider call goes through an ``httpx.MockTransport`` and
the only key value is obviously fake
(``"test-byok-key-not-real"``), sent only to the mock.
"""

import base64

import httpx
import pytest
from fastapi.testclient import TestClient

from puspa.db import connect as _db_connect
from puspa.multimodal.attachments import AttachmentError, AttachmentStore
from puspa.multimodal.generation import generate_image_attachment
from puspa.multimodal.turns import (
    assistant_message_with_image,
    chat_message_to_internal,
    chat_messages_to_internal,
)
from puspa.multimodal.vision import describe_image
from puspa.pwa.app import create_app
from puspa.pwa.capability_routing import create_schema, save_route
from puspa.pwa.model_settings import save_byok_key
from puspa.turns.config import TurnConfig
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import to_chat_completions
from puspa.turns.turn_api import ModelReply
from puspa.voice.transcriber import StubTranscriber

FAKE_KEY = "test-byok-key-not-real"
TRANSCRIPT = "tolong cek harga beras"

# A 1x1 transparent PNG, the smallest real image file.
PNG_1X1 = base64.b64decode(
    "iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVR42mP8z8BQDwAEhQGAhKmMIQAAAABJRU5ErkJggg=="
)


def _mock_chat_reply(text="hello from the model"):
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [
            {"index": 0, "message": {"role": "assistant", "content": text}, "finish_reason": "stop"}
        ],
        "model": "test-model",
        "usage": {"prompt_tokens": 5, "completion_tokens": 3, "total_tokens": 8},
    }


class CapturingTransport(httpx.MockTransport):
    """A mock transport that records every request it receives."""

    def __init__(self, handler=None):
        self.requests = []
        super().__init__(handler or self._handle)

    def _handle(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=_mock_chat_reply("a red bicycle on a street"))


@pytest.fixture
def store(tmp_path):
    return AttachmentStore(tmp_path / "attachments")


# ---------------------------------------------------------------------------
# Attachments: validation and storage
# ---------------------------------------------------------------------------


def test_image_stored_inline_and_read_back(store):
    attachment = store.store_bytes("u1", PNG_1X1, "image/png", filename="cat.png")
    assert attachment.kind == "image"
    assert attachment.size_bytes == len(PNG_1X1)
    assert attachment.storage["type"] == "inline"
    assert store.read_bytes(attachment) == PNG_1X1
    assert store.get("u1", attachment.id) is attachment
    assert store.get("u2", attachment.id) is None  # another user's id finds nothing
    public = attachment.to_public_dict()
    assert public["download_url"] == f"/api/chat/attachments/{attachment.id}"
    assert "path" not in public and "data" not in str(public.values())


def test_large_image_goes_to_disk_not_memory(store, monkeypatch):
    import puspa.multimodal.attachments as attachments_mod

    monkeypatch.setattr(attachments_mod, "MAX_INLINE_BYTES", 4)
    attachment = store.store_bytes("u1", PNG_1X1, "image/png", filename="big.png")
    assert attachment.storage["type"] == "file"
    assert store.read_bytes(attachment) == PNG_1X1


def test_audio_and_file_round_trip(store):
    audio = store.store_bytes("u1", b"fake-recording", "audio/webm", filename="note.webm")
    assert audio.kind == "audio"
    assert store.read_bytes(audio) == b"fake-recording"
    text_file = store.store_bytes("u1", b"hello file", "text/plain", filename="notes.txt")
    assert text_file.kind == "file"
    assert store.read_bytes(text_file) == b"hello file"


def test_adopt_file_registers_a_kept_recording(store, tmp_path):
    kept = tmp_path / "rec.webm"
    kept.write_bytes(b"fake-recording")
    attachment = store.adopt_file(
        "u1", kept, "audio/webm", filename="voice-note", transcript=TRANSCRIPT
    )
    assert attachment.kind == "audio"
    assert attachment.transcript == TRANSCRIPT
    assert store.read_bytes(attachment) == b"fake-recording"


def test_url_attachment_registers_a_provider_hosted_image(store):
    attachment = store.store_url("u1", "https://example.com/img.png", "image/png")
    assert attachment.kind == "image"
    assert attachment.storage["type"] == "url"
    assert attachment.to_public_dict()["url"] == "https://example.com/img.png"
    with pytest.raises(AttachmentError):
        store.read_bytes(attachment)  # nothing local to serve; the route redirects


def test_refused_attachments(store):
    with pytest.raises(AttachmentError, match="unsupported attachment type"):
        store.store_bytes("u1", b"video", "video/mp4", filename="clip.mp4")
    with pytest.raises(AttachmentError, match="empty attachment"):
        store.store_bytes("u1", b"", "image/png")
    with pytest.raises(AttachmentError, match="over the"):
        store.store_bytes("u1", b"x" * (10 * 1024 * 1024 + 1), "image/png")
    with pytest.raises(AttachmentError, match="http"):
        store.store_url("u1", "ftp://example.com/img.png", "image/png")


def test_image_source_shapes(store):
    inline = store.store_bytes("u1", PNG_1X1, "image/png")
    source = store.image_source(inline)
    assert source == {"type": "base64", "media_type": "image/png", "data": source["data"]}
    url_attachment = store.store_url("u1", "https://example.com/img.png", "image/png")
    assert store.image_source(url_attachment) == {
        "type": "url",
        "url": "https://example.com/img.png",
    }
    audio = store.store_bytes("u1", b"fake-recording", "audio/webm")
    with pytest.raises(AttachmentError, match="not an image"):
        store.image_source(audio)


# ---------------------------------------------------------------------------
# Turn bridge: chat messages -> internal messages
# ---------------------------------------------------------------------------


def test_image_message_reaches_the_model_as_an_image_block(store):
    attachment = store.store_bytes("u1", PNG_1X1, "image/png", filename="cat.png")
    message = {
        "sender": "users",
        "text": "what is this?",
        "attachments": [attachment.to_public_dict()],
    }
    internal = chat_message_to_internal(message, store=store, user_id="u1")
    assert internal is not None and internal.role == "user"
    wire = to_chat_completions([internal])
    assert wire.dropped == []
    content = wire.value[0]["content"]
    assert {"type": "text", "text": "what is this?"} in content
    image_part = next(part for part in content if part["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_voice_note_becomes_its_transcript_and_file_is_named(store):
    audio = store.store_bytes(
        "u1", b"fake-recording", "audio/webm", transcript=TRANSCRIPT, filename="note.webm"
    )
    text_file = store.store_bytes("u1", b"beras 10kg", "text/plain", filename="notes.txt")
    message = {
        "sender": "users",
        "text": "",
        "attachments": [audio.to_public_dict(), text_file.to_public_dict()],
    }
    internal = chat_message_to_internal(message, store=store, user_id="u1")
    assert internal is not None
    assert f"[voice note: {TRANSCRIPT}]" in internal.text
    assert "[file: notes.txt (text/plain, 10 bytes)]" in internal.text
    assert "beras 10kg" in internal.text  # small text file is inlined


def test_unavailable_image_is_reported_not_silently_dropped(store):
    attachment = store.store_bytes("u1", PNG_1X1, "image/png")
    message = {
        "sender": "users",
        "text": "what is this?",
        "attachments": [attachment.to_public_dict()],
    }
    # No store behind the lookup: the image cannot cross.
    internal = chat_message_to_internal(message, user_id="u1")
    assert internal is not None
    assert "[image unavailable" in internal.text


def test_empty_message_converts_to_nothing(store):
    assert chat_message_to_internal({"sender": "users", "text": ""}) is None
    assert chat_message_to_internal({"sender": "users", "text": "hi"}) is not None
    history = chat_messages_to_internal(
        [
            {"sender": "users", "text": "hi"},
            {"sender": "users", "text": ""},
            {"sender": "puspa", "text": "hello"},
        ]
    )
    assert [message.role for message in history] == ["user", "assistant"]


def test_assistant_message_with_image_shape(store):
    attachment = store.store_url("u1", "https://example.com/img.png", "image/png")
    message = assistant_message_with_image("here is your picture", attachment)
    assert message["sender"] == "puspa"
    assert message["text"] == "here is your picture"
    assert message["attachments"][0]["id"] == attachment.id


def test_image_message_flows_through_the_turn_loop(store):
    attachment = store.store_bytes("u1", PNG_1X1, "image/png", filename="cat.png")
    history = chat_messages_to_internal(
        [
            {
                "sender": "users",
                "text": "what is this?",
                "attachments": [attachment.to_public_dict()],
            }
        ],
        store=store,
        user_id="u1",
    )
    seen: list = []

    def fake_model_call(entry, wire_messages, tools):
        seen.append(wire_messages)
        return ModelReply(text="a cat", input_tokens=10, output_tokens=5)

    loop = TurnLoop(model_call=fake_model_call, config=TurnConfig(call_timeout_seconds=30.0))
    result = loop.run_turn(history)
    assert result.status == "completed"
    assert result.text == "a cat"
    assert len(seen) == 1
    image_part = next(part for part in seen[0][0]["content"] if part["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


# ---------------------------------------------------------------------------
# Vision and generation through the routed providers (mock transport)
# ---------------------------------------------------------------------------


@pytest.fixture(autouse=True)
def _routing_table(test_dsn):
    create_schema(test_dsn)
    yield
    with _db_connect(test_dsn) as conn:
        conn.execute("TRUNCATE capability_routing")
        conn.commit()


def _route_vision(user, test_dsn):
    save_byok_key(user["user_id"], "openai", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "vision", "openai", "gpt-4o", dsn=test_dsn)


def _route_image(user, test_dsn):
    save_byok_key(user["user_id"], "openai", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "image", "openai", "dall-e-3", dsn=test_dsn)


def test_describe_image_sends_the_picture_to_the_routed_vision_model(
    make_user, test_dsn, vault_master_key, store
):
    user = make_user()
    _route_vision(user, test_dsn)
    attachment = store.store_bytes("u1", PNG_1X1, "image/png", filename="cat.png")
    transport = CapturingTransport()
    result = describe_image(
        "what is in this picture?",
        user_id=user["user_id"],
        attachment=attachment,
        store=store,
        dsn=test_dsn,
        transport=transport,
    )
    assert result.text == "a red bicycle on a street"
    assert result.provider == "openai"
    assert result.model == "gpt-4o"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    import json

    body = json.loads(request.content)
    assert body["model"] == "gpt-4o"
    content = body["messages"][0]["content"]
    assert {"type": "text", "text": "what is in this picture?"} in content
    image_part = next(part for part in content if part["type"] == "image_url")
    assert image_part["image_url"]["url"].startswith("data:image/png;base64,")


def test_describe_image_from_bytes_or_url(make_user, test_dsn, vault_master_key):
    user = make_user()
    _route_vision(user, test_dsn)
    transport = CapturingTransport()
    result = describe_image(
        "",
        user_id=user["user_id"],
        image_bytes=PNG_1X1,
        mime_type="image/png",
        dsn=test_dsn,
        transport=transport,
    )
    assert result.text == "a red bicycle on a street"
    import json

    body = json.loads(transport.requests[0].content)
    assert body["messages"][0]["content"][0]["text"] == "Describe what is in this image."
    result = describe_image(
        "what is this?",
        user_id=user["user_id"],
        image_url="https://example.com/cat.png",
        dsn=test_dsn,
        transport=transport,
    )
    assert result.text == "a red bicycle on a street"


def test_describe_image_without_a_key_refuses(make_user, test_dsn, store):
    user = make_user()
    # A vision route to a provider with no vault key is refused
    # before any call. (Vision has a hosted default, so "no
    # route" is not itself a refusal — the router's vision
    # chain answers instead.)
    save_route(user["user_id"], "vision", "openai", "gpt-4o", dsn=test_dsn)
    attachment = store.store_bytes("u1", PNG_1X1, "image/png")
    with pytest.raises(KeyError, match="no key stored for provider 'openai'"):
        describe_image(
            "what is this?",
            user_id=user["user_id"],
            attachment=attachment,
            store=store,
            dsn=test_dsn,
        )


def test_generate_image_stores_base64_bytes(make_user, test_dsn, vault_master_key, store):
    user = make_user()
    _route_image(user, test_dsn)

    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/images/generations"
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        return httpx.Response(
            200,
            json={
                "data": [
                    {
                        "b64_json": base64.b64encode(PNG_1X1).decode("ascii"),
                        "revised_prompt": "a red bicycle",
                    }
                ]
            },
        )

    transport = httpx.MockTransport(handler)
    attachment, result = generate_image_attachment(
        "a red bicycle",
        user_id=user["user_id"],
        store=store,
        dsn=test_dsn,
        transport=transport,
    )
    assert result.provider == "openai"
    assert result.model == "dall-e-3"
    assert result.revised_prompt == "a red bicycle"
    assert attachment.kind == "image"
    assert store.read_bytes(attachment) == PNG_1X1


def test_generate_image_stores_a_provider_url_without_downloading(
    make_user, test_dsn, vault_master_key, store
):
    user = make_user()
    _route_image(user, test_dsn)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(200, json={"data": [{"url": "https://example.com/img.png"}]})
    )
    attachment, result = generate_image_attachment(
        "a red bicycle",
        user_id=user["user_id"],
        store=store,
        dsn=test_dsn,
        transport=transport,
    )
    assert attachment.storage == {"type": "url", "url": "https://example.com/img.png"}
    assert result.url == "https://example.com/img.png"


def test_generate_image_without_a_route_refuses(make_user, test_dsn, store):
    user = make_user()
    with pytest.raises(ValueError, match="has no provider yet"):
        generate_image_attachment(
            "a red bicycle", user_id=user["user_id"], store=store, dsn=test_dsn
        )


# ---------------------------------------------------------------------------
# HTTP layer: send, download, describe, generate, voice note
# ---------------------------------------------------------------------------


@pytest.fixture
def api_client(make_user, tmp_path, test_dsn):
    user = make_user()
    vision_client = httpx.Client(transport=CapturingTransport())
    image_client = httpx.Client(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(
                200,
                json={
                    "data": [
                        {
                            "b64_json": base64.b64encode(PNG_1X1).decode("ascii"),
                            "revised_prompt": "a red bicycle",
                        }
                    ]
                },
            )
        )
    )
    app = create_app(
        test_user_id=user["user_id"],
        dsn=test_dsn,
        transcriber=StubTranscriber(TRANSCRIPT),
        share_inbox_dir=tmp_path / "inbox",
        vision_http_client=vision_client,
        image_http_client=image_client,
    )
    with TestClient(app, raise_server_exceptions=False) as client:
        client.user_id = user["user_id"]
        client.inbox = tmp_path / "inbox"
        yield client


def test_send_chat_with_an_image_and_download_it(api_client):
    resp = api_client.post(
        "/api/chat",
        data={"text": "what is this?"},
        files=[("files", ("cat.png", PNG_1X1, "image/png"))],
    )
    assert resp.status_code == 200
    message = resp.json()["message"]
    assert message["text"] == "what is this?"
    assert len(message["attachments"]) == 1
    attachment = message["attachments"][0]
    assert attachment["kind"] == "image"
    assert attachment["filename"] == "cat.png"
    download = api_client.get(attachment["download_url"])
    assert download.status_code == 200
    assert download.content == PNG_1X1
    assert download.headers["content-type"] == "image/png"
    page = api_client.get("/chat")
    assert attachment["download_url"] in page.text


def test_send_chat_refuses_video_and_empty_messages(api_client):
    resp = api_client.post(
        "/api/chat",
        data={"text": "clip"},
        files=[("files", ("clip.mp4", b"video", "video/mp4"))],
    )
    assert resp.status_code == 400
    assert api_client.post("/api/chat", data={"text": "   "}).status_code == 400
    assert api_client.post("/api/chat", data={"text": "hello"}).status_code == 200


def test_download_unknown_attachment_is_404(api_client):
    assert api_client.get("/api/chat/attachments/nope").status_code == 404


def test_describe_endpoint_uses_the_routed_vision_model(api_client, test_dsn, vault_master_key):
    _route_vision({"user_id": api_client.user_id}, test_dsn)
    sent = api_client.post(
        "/api/chat",
        data={"text": "what is this?"},
        files=[("files", ("cat.png", PNG_1X1, "image/png"))],
    )
    attachment_id = sent.json()["message"]["attachments"][0]["id"]
    resp = api_client.post(
        "/api/multimodal/describe",
        json={"attachment_id": attachment_id, "prompt": "what is in this picture?"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["description"] == "a red bicycle on a street"
    assert body["provider"] == "openai"
    assert body["model"] == "gpt-4o"


def test_describe_endpoint_refuses_a_non_image(api_client):
    sent = api_client.post(
        "/api/chat",
        data={"text": "notes"},
        files=[("files", ("notes.txt", b"hello", "text/plain"))],
    )
    attachment_id = sent.json()["message"]["attachments"][0]["id"]
    resp = api_client.post("/api/multimodal/describe", json={"attachment_id": attachment_id})
    assert resp.status_code == 400
    assert (
        api_client.post("/api/multimodal/describe", json={"attachment_id": "unknown"}).status_code
        == 404
    )


def test_generate_image_endpoint_posts_the_picture_to_chat(api_client, test_dsn, vault_master_key):
    _route_image({"user_id": api_client.user_id}, test_dsn)
    resp = api_client.post("/api/multimodal/generate-image", json={"prompt": "a red bicycle"})
    assert resp.status_code == 200
    body = resp.json()
    assert body["ok"] is True
    assert body["message"]["sender"] == "puspa"
    assert len(body["message"]["attachments"]) == 1
    attachment = body["attachment"]
    assert attachment["kind"] == "image"
    download = api_client.get(attachment["download_url"])
    assert download.status_code == 200
    assert download.content == PNG_1X1
    page = api_client.get("/chat")
    assert attachment["download_url"] in page.text


def test_generate_image_endpoint_refuses_without_a_route(api_client):
    resp = api_client.post("/api/multimodal/generate-image", json={"prompt": "a red bicycle"})
    assert resp.status_code == 400
    assert (
        api_client.post("/api/multimodal/generate-image", json={"prompt": "   "}).status_code == 400
    )


def test_voice_note_kept_audio_rides_on_the_confirmed_message(api_client):
    upload = api_client.post(
        "/api/voice/upload",
        files={"audio": ("rec.webm", b"fake-recording", "audio/webm")},
        data={"keep_audio": "true"},
    )
    assert upload.status_code == 200
    pending_id = upload.json()["pending_id"]
    resp = api_client.post("/api/voice/confirm", json={"pending_id": pending_id})
    assert resp.status_code == 200
    message = resp.json()["message"]
    assert message["text"] == TRANSCRIPT
    assert len(message["attachments"]) == 1
    attachment = message["attachments"][0]
    assert attachment["kind"] == "audio"
    assert attachment["transcript"] == TRANSCRIPT
    download = api_client.get(attachment["download_url"])
    assert download.status_code == 200
    assert download.content == b"fake-recording"


def test_voice_note_deleted_audio_sends_text_only(api_client):
    upload = api_client.post(
        "/api/voice/upload",
        files={"audio": ("rec.webm", b"fake-recording", "audio/webm")},
        data={"keep_audio": "false"},
    )
    pending_id = upload.json()["pending_id"]
    resp = api_client.post("/api/voice/confirm", json={"pending_id": pending_id})
    assert resp.status_code == 200
    assert resp.json()["message"]["attachments"] == []
