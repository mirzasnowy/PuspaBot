"""WhatsApp adapter tests (G14).

No real token, no real network: every HTTP call goes through
``httpx.MockTransport``. Nothing here contacts graph.facebook.com.
"""

from __future__ import annotations

import hashlib
import hmac

import httpx
import pytest

from puspa.channels.errors import DeliveryError, WebhookVerificationError
from puspa.channels.whatsapp import WhatsAppAdapter


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _webhook_body():
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "id": "123",
                "changes": [
                    {
                        "value": {
                            "messages": [
                                {
                                    "from": "628123456789",
                                    "id": "wamid.abc",
                                    "timestamp": "1793123456",
                                    "type": "text",
                                    "text": {"body": "hello world please"},
                                }
                            ],
                            "statuses": [{"id": "wamid.abc", "status": "delivered"}],
                        },
                        "field": "messages",
                    }
                ],
            }
        ],
    }


def test_parse_update_text_message():
    adapter = WhatsAppAdapter("fake-token", "555")
    events = adapter.parse_update(_webhook_body())
    assert len(events) == 1
    event = events[0]
    assert event.platform == "whatsapp"
    assert event.chat_id == "628123456789"
    assert event.sender_id == "628123456789"
    assert event.text == "hello world please"
    assert event.message_id == "wamid.abc"
    assert event.is_bot is False
    assert event.is_group is False
    assert event.dedup_key == "whatsapp:628123456789:wamid.abc"


def test_parse_update_status_only_ignored():
    adapter = WhatsAppAdapter("fake-token", "555")
    body = {"entry": [{"changes": [{"value": {"statuses": [{"id": "x"}]}, "field": "messages"}]}]}
    assert adapter.parse_update(body) == []


def test_parse_update_non_text_ignored():
    adapter = WhatsAppAdapter("fake-token", "555")
    body = {
        "entry": [
            {
                "changes": [
                    {
                        "value": {"messages": [{"from": "1", "id": "m1", "type": "image"}]},
                        "field": "messages",
                    }
                ]
            }
        ]
    }
    assert adapter.parse_update(body) == []


def test_parse_update_own_number_marked_as_bot():
    adapter = WhatsAppAdapter("fake-token", "555", bot_phone_number="628123456789")
    events = adapter.parse_update(_webhook_body())
    assert events[0].is_bot is True


def test_verify_webhook_handshake():
    adapter = WhatsAppAdapter("fake-token", "555", verify_token="tok123")
    query = {"hub.mode": "subscribe", "hub.verify_token": "tok123", "hub.challenge": "abc"}
    assert adapter.verify_webhook_handshake(query) == "abc"
    assert adapter.verify_webhook_handshake({**query, "hub.verify_token": "wrong"}) is None
    assert adapter.verify_webhook_handshake({**query, "hub.mode": "unsubscribe"}) is None


def test_verify_signature():
    adapter = WhatsAppAdapter("fake-token", "555", app_secret="secret")
    body = b'{"hello": "world"}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    assert adapter.verify_signature(body, good) is True
    assert adapter.verify_signature(body, "sha256=deadbeef") is False
    assert adapter.verify_signature(body, None) is False


def test_verify_signature_without_secret_passes():
    adapter = WhatsAppAdapter("fake-token", "555")
    assert adapter.verify_signature(b"anything", None) is True


def test_verify_webhook_post():
    adapter = WhatsAppAdapter("fake-token", "555", app_secret="secret")
    body = b'{"hello": "world"}'
    good = "sha256=" + hmac.new(b"secret", body, hashlib.sha256).hexdigest()
    request = {"body": body, "headers": {"X-Hub-Signature-256": good}}
    assert adapter.verify_webhook(request) is True
    bad = {"body": body, "headers": {"X-Hub-Signature-256": "sha256=deadbeef"}}
    with pytest.raises(WebhookVerificationError):
        adapter.verify_webhook(bad)
    missing = {"body": body, "headers": {}}
    assert adapter.verify_webhook(missing) is False


def test_send_text_success():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"messages": [{"id": "wamid.sent1"}]})

    adapter = WhatsAppAdapter(
        "fake-token", "555", http_client=_client(handler), sleep=lambda s: None
    )
    result = adapter.send_text("628123456789", "hello")
    assert result["messages"][0]["id"] == "wamid.sent1"
    assert len(seen) == 1
    assert str(seen[0].url) == "https://graph.facebook.com/v21.0/555/messages"
    assert seen[0].headers["Authorization"] == "Bearer fake-token"


def test_send_text_retries_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"error": {"message": "boom"}})
        return httpx.Response(200, json={"messages": [{"id": "wamid.sent1"}]})

    adapter = WhatsAppAdapter(
        "fake-token", "555", http_client=_client(handler), sleep=sleeps.append
    )
    result = adapter.send_text("628123456789", "hello")
    assert result["messages"][0]["id"] == "wamid.sent1"
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_send_text_exhausted_raises():
    def handler(request):
        return httpx.Response(500, json={"error": {"message": "boom"}})

    adapter = WhatsAppAdapter(
        "fake-token", "555", http_client=_client(handler), sleep=lambda s: None
    )
    with pytest.raises(DeliveryError, match="after 3 attempts"):
        adapter.send_text("628123456789", "hello")


def test_adapter_requires_token_and_number():
    with pytest.raises(ValueError):
        WhatsAppAdapter("", "555")
    with pytest.raises(ValueError):
        WhatsAppAdapter("fake-token", "")
