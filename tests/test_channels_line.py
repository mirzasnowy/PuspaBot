"""LINE adapter tests (AST-04).

No real token, no real network: every HTTP call goes through
``httpx.MockTransport``. Nothing here contacts api.line.me.
"""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import uuid

import httpx
import pytest

from puspa.channels.authz import Allowlist, ChannelPairing
from puspa.channels.config import ChannelConfig, ChannelConfigError, load_config
from puspa.channels.errors import DeliveryError
from puspa.channels.events import MessageEvent
from puspa.channels.inbound import handle_inbound
from puspa.channels.ledger import InMemoryDeliveryLedger
from puspa.channels.line import LineAdapter
from puspa.channels.run import GatewayRunner


def _event(
    event_id="evt-1",
    user_id="U222",
    text="hello world please",
    source_type="user",
    source_id=None,
    message_type="text",
    reply_token="reply-1",
):
    source = {"type": source_type, "userId": user_id}
    if source_type == "group":
        source["groupId"] = source_id or "G333"
    if source_type == "room":
        source["roomId"] = source_id or "R444"
    return {
        "type": "message",
        "replyToken": reply_token,
        "source": source,
        "timestamp": 1793123456000,
        "mode": "active",
        "webhookEventId": event_id,
        "deliveryContext": {"isRedelivery": False},
        "message": {"id": "msg-1", "type": message_type, "text": text},
    }


def _webhook(events):
    return {"destination": "U999", "events": events}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


class FakeAgent:
    def __init__(self, reply="fake agent reply"):
        self.reply = reply
        self.calls = []

    def answer(self, event, *, user_id):
        self.calls.append((event, user_id))
        return self.reply


def _round_trip_client():
    def handler(request):
        if "message/push" in str(request.url):
            return httpx.Response(200, json={})
        return httpx.Response(404, json={"message": "not found"})

    return _client(handler)


# -- parse ---------------------------------------------------------------


def test_parse_update_user_message():
    adapter = LineAdapter("fake-token")
    events = adapter.parse_update(_webhook([_event()]))
    assert len(events) == 1
    event = events[0]
    assert event.platform == "line"
    assert event.chat_id == "U222"
    assert event.sender_id == "U222"
    assert event.text == "hello world please"
    assert event.message_id == "evt-1"
    assert event.is_bot is False
    assert event.is_group is False
    assert event.dedup_key == "line:U222:evt-1"
    assert event.session_key == "line:U222"
    assert event.raw["replyToken"] == "reply-1"


def test_parse_update_group_uses_group_id():
    adapter = LineAdapter("fake-token")
    events = adapter.parse_update(_webhook([_event(source_type="group")]))
    assert events[0].chat_id == "G333"
    assert events[0].sender_id == "U222"
    assert events[0].is_group is True


def test_parse_update_room_uses_room_id():
    adapter = LineAdapter("fake-token")
    events = adapter.parse_update(_webhook([_event(source_type="room")]))
    assert events[0].chat_id == "R444"
    assert events[0].is_group is True


def test_parse_update_own_id_marked_as_bot():
    adapter = LineAdapter("fake-token", bot_user_id="U999")
    events = adapter.parse_update(_webhook([_event(user_id="U999")]))
    assert events[0].is_bot is True


def test_parse_update_non_text_and_non_message_ignored():
    adapter = LineAdapter("fake-token")
    payload = _webhook(
        [
            _event(event_id="evt-1", message_type="image", text=""),
            {"type": "follow", "replyToken": "r", "source": {"type": "user", "userId": "U1"}},
            {
                "type": "message",
                "replyToken": "r2",
                "source": {"type": "user", "userId": "U1"},
                "message": {"id": "m", "type": "sticker"},
            },
        ]
    )
    assert adapter.parse_update(payload) == []
    assert adapter.parse_update({"destination": "U999", "events": []}) == []
    assert adapter.parse_update({}) == []


def test_parse_update_multiple_events():
    adapter = LineAdapter("fake-token")
    events = adapter.parse_update(_webhook([_event("evt-1"), _event("evt-2", user_id="U333")]))
    assert [e.message_id for e in events] == ["evt-1", "evt-2"]
    assert [e.sender_id for e in events] == ["U222", "U333"]


# -- send -----------------------------------------------------------------


def test_send_text_push_success():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    adapter = LineAdapter("fake-token", http_client=_client(handler), sleep=lambda s: None)
    result = adapter.send_text("U222", "hello")
    assert result == {}
    assert len(seen) == 1
    assert str(seen[0].url) == "https://api.line.me/v2/bot/message/push"
    assert seen[0].headers["Authorization"] == "Bearer fake-token"
    body = json.loads(seen[0].content)
    assert body == {"to": "U222", "messages": [{"type": "text", "text": "hello"}]}


def test_reply_text_success():
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={})

    adapter = LineAdapter("fake-token", http_client=_client(handler), sleep=lambda s: None)
    adapter.reply_text("reply-1", "hello")
    assert str(seen[0].url) == "https://api.line.me/v2/bot/message/reply"
    body = json.loads(seen[0].content)
    assert body["replyToken"] == "reply-1"
    assert body["messages"] == [{"type": "text", "text": "hello"}]


def test_reply_text_needs_token():
    adapter = LineAdapter("fake-token")
    with pytest.raises(ValueError, match="reply token"):
        adapter.reply_text("", "hello")


def test_send_text_retries_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"message": "boom"})
        return httpx.Response(200, json={})

    adapter = LineAdapter("fake-token", http_client=_client(handler), sleep=sleeps.append)
    assert adapter.send_text("U222", "hello") == {}
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_send_text_exhausted_raises():
    def handler(request):
        return httpx.Response(500, json={"message": "boom"})

    adapter = LineAdapter("fake-token", http_client=_client(handler), sleep=lambda s: None)
    with pytest.raises(DeliveryError, match="after 3 attempts"):
        adapter.send_text("U222", "hello")


def test_send_text_invalid_token_says_so():
    def handler(request):
        return httpx.Response(401, json={"message": "Authentication failed"})

    adapter = LineAdapter("bad-token", http_client=_client(handler), sleep=lambda s: None)
    with pytest.raises(DeliveryError, match="token is invalid"):
        adapter.send_text("U222", "hello")


# -- token check + webhook -------------------------------------------------


def test_check_token_success_and_invalid():
    def ok_handler(request):
        return httpx.Response(200, json={"type": "limited", "value": 500})

    adapter = LineAdapter("fake-token", http_client=_client(ok_handler))
    assert adapter.check_token()["value"] == 500

    def bad_handler(request):
        return httpx.Response(401, json={"message": "Authentication failed"})

    adapter = LineAdapter("bad-token", http_client=_client(bad_handler))
    with pytest.raises(DeliveryError, match="token is invalid"):
        adapter.check_token()


def _signed_request(secret, body):
    signature = base64.b64encode(hmac.new(secret.encode(), body, hashlib.sha256).digest()).decode()
    return {"body": body, "headers": {"X-Line-Signature": signature}}


def test_verify_webhook_signature():
    adapter = LineAdapter("fake-token", channel_secret="s3cr3t")
    body = b'{"events": []}'
    assert adapter.verify_webhook(_signed_request("s3cr3t", body)) is True
    assert adapter.verify_webhook(_signed_request("wrong", body)) is False
    assert adapter.verify_webhook({"body": body, "headers": {}}) is False


def test_verify_webhook_without_secret_passes():
    adapter = LineAdapter("fake-token")
    assert adapter.verify_webhook({"body": b"{}", "headers": {}}) is True


def test_adapter_requires_token():
    with pytest.raises(ValueError):
        LineAdapter("")


def test_poll_once_not_supported():
    adapter = LineAdapter("fake-token")
    with pytest.raises(NotImplementedError, match="does not poll"):
        adapter.poll_once()


# -- round-trip through the pipeline + runner ------------------------------


def test_round_trip_message_through_pipeline():
    adapter = LineAdapter("fake-token", http_client=_round_trip_client())
    outcome = handle_inbound(
        MessageEvent(
            platform="line",
            chat_id="U222",
            sender_id="U222",
            text="hello world please",
            message_id="evt-1",
        ),
        adapter=adapter,
        agent=FakeAgent(),
        pairing=ChannelPairing(),
        allowlist=Allowlist.parse("line:U222"),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "answered"
    assert outcome.reply == "fake agent reply"


def test_runner_wires_line(tmp_path):
    profile = f"line-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        line_channel_access_token="fake-token",
        allowlist=Allowlist.parse("line:U222"),
        state_dir=str(tmp_path),
    )
    runner = GatewayRunner.open(
        profile=profile,
        config=config,
        agent=FakeAgent(),
        http_client=_round_trip_client(),
    )
    try:
        assert runner.adapter_for("line").platform == "line"
        assert runner.line_adapter() is runner.adapter_for("line")
        outcomes = runner.handle_webhook_payload("line", _webhook([_event()]))
        assert len(outcomes) == 1
        assert outcomes[0].outcome == "answered"
        assert outcomes[0].reply == "fake agent reply"
    finally:
        runner.close()


def test_runner_requires_line_token(tmp_path):
    runner = GatewayRunner.open(
        profile=f"no-line-{uuid.uuid4().hex[:8]}",
        config=ChannelConfig(state_dir=str(tmp_path)),
        agent=FakeAgent(),
        http_client=_round_trip_client(),
    )
    try:
        with pytest.raises(ChannelConfigError):
            runner.line_adapter()
    finally:
        runner.close()


def test_load_config_line_env():
    config = load_config(
        {
            "LINE_CHANNEL_ACCESS_TOKEN": "tok",
            "LINE_CHANNEL_SECRET": "s3cr3t",
        }
    )
    assert config.line_enabled is True
    assert config.require_line() == "tok"
    assert load_config({}).line_enabled is False
    with pytest.raises(ChannelConfigError):
        load_config({}).require_line()
