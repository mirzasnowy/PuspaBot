"""Telegram adapter tests (G14).

No real token, no real network: every HTTP call goes through
``httpx.MockTransport``. Nothing here contacts api.telegram.org.
"""

from __future__ import annotations

import httpx
import pytest

from puspa.channels.errors import DeliveryError
from puspa.channels.telegram import TelegramAdapter


def _update(
    update_id=10,
    chat_id=111,
    user_id=222,
    text="hello world please",
    is_bot=False,
    chat_type="private",
):
    return {
        "update_id": update_id,
        "message": {
            "message_id": 5,
            "date": 1793123456,
            "chat": {"id": chat_id, "type": chat_type},
            "from": {"id": user_id, "is_bot": is_bot, "first_name": "Test"},
            "text": text,
        },
    }


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_send(request):
    return httpx.Response(
        200,
        json={"ok": True, "result": {"message_id": 7, "chat": {"id": 111}, "text": "hi"}},
    )


def test_parse_update_message():
    adapter = TelegramAdapter("fake-token")
    events = adapter.parse_update(_update())
    assert len(events) == 1
    event = events[0]
    assert event.platform == "telegram"
    assert event.chat_id == "111"
    assert event.sender_id == "222"
    assert event.text == "hello world please"
    assert event.message_id == "5"
    assert event.is_bot is False
    assert event.is_group is False
    assert event.dedup_key == "telegram:111:5"
    assert event.session_key == "telegram:111"


def test_parse_update_bot_sender_marked():
    adapter = TelegramAdapter("fake-token")
    events = adapter.parse_update(_update(is_bot=True))
    assert events[0].is_bot is True


def test_parse_update_own_id_marked_as_bot():
    adapter = TelegramAdapter("fake-token", bot_user_id=999)
    events = adapter.parse_update(_update(user_id=999))
    assert events[0].is_bot is True


def test_parse_update_group_marked():
    adapter = TelegramAdapter("fake-token")
    events = adapter.parse_update(_update(chat_type="group"))
    assert events[0].is_group is True


def test_parse_update_non_message_ignored():
    adapter = TelegramAdapter("fake-token")
    assert adapter.parse_update({"update_id": 1, "my_chat_member": {}}) == []
    assert adapter.parse_update({"update_id": 2}) == []


def test_send_text_success():
    seen = []

    def handler(request):
        seen.append(request)
        return _ok_send(request)

    adapter = TelegramAdapter("fake-token", http_client=_client(handler), sleep=lambda s: None)
    result = adapter.send_text("111", "hello")
    assert result["message_id"] == 7
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.telegram.org/botfake-token/sendMessage"


def test_send_text_retries_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"ok": False, "description": "boom"})
        return _ok_send(request)

    adapter = TelegramAdapter("fake-token", http_client=_client(handler), sleep=sleeps.append)
    result = adapter.send_text("111", "hello")
    assert result["message_id"] == 7
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_send_text_exhausted_raises():
    def handler(request):
        return httpx.Response(500, json={"ok": False, "description": "boom"})

    adapter = TelegramAdapter("fake-token", http_client=_client(handler), sleep=lambda s: None)
    with pytest.raises(DeliveryError, match="after 3 attempts"):
        adapter.send_text("111", "hello")


def test_poll_once_and_next_offset():
    def handler(request):
        assert "offset=42" in str(request.url)
        return httpx.Response(
            200,
            json={"ok": True, "result": [_update(update_id=42), _update(update_id=43)]},
        )

    adapter = TelegramAdapter("fake-token", http_client=_client(handler))
    events = adapter.poll_once(offset=42)
    assert len(events) == 2
    assert TelegramAdapter.next_offset(adapter._last_update_ids, 42) == 44
    assert TelegramAdapter.next_offset([], 42) == 42
    assert TelegramAdapter.next_offset([10], None) == 11
    assert TelegramAdapter.next_offset([10, 9], 5) == 11


def test_poll_failure_raises():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "description": "unauthorized"})

    adapter = TelegramAdapter("fake-token", http_client=_client(handler))
    with pytest.raises(DeliveryError):
        adapter.poll_once()


def test_verify_webhook_secret():
    adapter = TelegramAdapter("fake-token", webhook_secret="s3cr3t")
    good = {"headers": {"X-Telegram-Bot-Api-Secret-Token": "s3cr3t"}}
    bad = {"headers": {"X-Telegram-Bot-Api-Secret-Token": "nope"}}
    assert adapter.verify_webhook(good) is True
    assert adapter.verify_webhook(bad) is False
    assert adapter.verify_webhook({"headers": {}}) is False


def test_verify_webhook_without_secret_passes():
    adapter = TelegramAdapter("fake-token")
    assert adapter.verify_webhook({"headers": {}}) is True


def test_adapter_requires_token():
    with pytest.raises(ValueError):
        TelegramAdapter("")
