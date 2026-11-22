"""Slack adapter tests (AST-04).

No real token, no real network: every HTTP call goes through
``httpx.MockTransport``. Nothing here contacts slack.com.
"""

from __future__ import annotations

import hashlib
import hmac
import time
import uuid

import httpx
import pytest

from puspa.channels.authz import Allowlist, ChannelPairing
from puspa.channels.config import ChannelConfig, ChannelConfigError, load_config
from puspa.channels.errors import DeliveryError
from puspa.channels.events import MessageEvent
from puspa.channels.inbound import handle_inbound
from puspa.channels.ledger import InMemoryDeliveryLedger
from puspa.channels.run import GatewayRunner
from puspa.channels.slack import SlackAdapter


def _event(
    channel="D111",
    user="U222",
    text="hello world please",
    ts="1793123456.000100",
    bot_id=None,
    subtype=None,
):
    event = {
        "type": "message",
        "channel": channel,
        "user": user,
        "text": text,
        "ts": ts,
    }
    if bot_id is not None:
        event["bot_id"] = bot_id
    if subtype is not None:
        event["subtype"] = subtype
    return event


def _envelope(event):
    return {"token": "v", "team_id": "T1", "type": "event_callback", "event": event}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_send(request):
    return httpx.Response(200, json={"ok": True, "channel": "D111", "ts": "1793123457.000200"})


class FakeAgent:
    def __init__(self, reply="fake agent reply"):
        self.reply = reply
        self.calls = []

    def answer(self, event, *, user_id):
        self.calls.append((event, user_id))
        return self.reply


def _round_trip_client():
    def handler(request):
        if "chat.postMessage" in str(request.url):
            return _ok_send(request)
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})

    return _client(handler)


# -- parse ---------------------------------------------------------------


def test_parse_update_envelope():
    adapter = SlackAdapter("xoxb-fake")
    events = adapter.parse_update(_envelope(_event()))
    assert len(events) == 1
    event = events[0]
    assert event.platform == "slack"
    assert event.chat_id == "D111"
    assert event.sender_id == "U222"
    assert event.text == "hello world please"
    assert event.message_id == "1793123456.000100"
    assert event.is_bot is False
    assert event.is_group is False
    assert event.dedup_key == "slack:D111:1793123456.000100"
    assert event.session_key == "slack:D111"


def test_parse_update_bare_event():
    adapter = SlackAdapter("xoxb-fake")
    assert len(adapter.parse_update(_event())) == 1


def test_parse_update_channel_marked_as_group():
    adapter = SlackAdapter("xoxb-fake")
    events = adapter.parse_update(_envelope(_event(channel="C333")))
    assert events[0].is_group is True


def test_parse_update_bot_sender_marked():
    adapter = SlackAdapter("xoxb-fake")
    events = adapter.parse_update(_envelope(_event(bot_id="B1")))
    assert events[0].is_bot is True
    events = adapter.parse_update(_envelope(_event(subtype="bot_message", user="U999")))
    assert events[0].is_bot is True


def test_parse_update_own_id_marked_as_bot():
    adapter = SlackAdapter("xoxb-fake", bot_user_id="U999")
    events = adapter.parse_update(_envelope(_event(user="U999")))
    assert events[0].is_bot is True


def test_parse_update_bot_mention_marks_bot():
    adapter = SlackAdapter("xoxb-fake", bot_user_id="U999")
    events = adapter.parse_update(_envelope(_event(channel="C333", text="hi <@U999> help")))
    assert events[0].mentions_bot is True


def test_parse_update_non_message_ignored():
    adapter = SlackAdapter("xoxb-fake")
    assert adapter.parse_update({"type": "url_verification", "challenge": "abc"}) == []
    assert adapter.parse_update(_envelope(_event(subtype="message_changed"))) == []
    assert adapter.parse_update({"event": {"type": "reaction_added"}}) == []
    assert adapter.parse_update({}) == []


def test_verify_url_challenge():
    adapter = SlackAdapter("xoxb-fake")
    assert adapter.verify_url_challenge({"type": "url_verification", "challenge": "abc"}) == "abc"
    assert adapter.verify_url_challenge(_envelope(_event())) is None


# -- send -----------------------------------------------------------------


def test_send_text_success():
    seen = []

    def handler(request):
        seen.append(request)
        return _ok_send(request)

    adapter = SlackAdapter("xoxb-fake", http_client=_client(handler), sleep=lambda s: None)
    result = adapter.send_text("D111", "hello")
    assert result["ts"] == "1793123457.000200"
    assert len(seen) == 1
    assert str(seen[0].url) == "https://slack.com/api/chat.postMessage"
    assert seen[0].headers["Authorization"] == "Bearer xoxb-fake"


def test_send_text_retries_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(200, json={"ok": False, "error": "internal_error"})
        return _ok_send(request)

    adapter = SlackAdapter("xoxb-fake", http_client=_client(handler), sleep=sleeps.append)
    result = adapter.send_text("D111", "hello")
    assert result["ts"] == "1793123457.000200"
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_send_text_exhausted_raises():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "channel_not_found"})

    adapter = SlackAdapter("xoxb-fake", http_client=_client(handler), sleep=lambda s: None)
    with pytest.raises(DeliveryError, match="channel_not_found"):
        adapter.send_text("D999", "hello")


def test_send_text_invalid_token_says_so():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    adapter = SlackAdapter("xoxb-bad", http_client=_client(handler), sleep=lambda s: None)
    with pytest.raises(DeliveryError, match="invalid_auth"):
        adapter.send_text("D111", "hello")


# -- poll -----------------------------------------------------------------


def test_poll_once_uses_oldest_and_orders_oldest_first():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json={
                "ok": True,
                "messages": [_event(ts="1793123457.000100"), _event(ts="1793123456.000100")],
            },
        )

    adapter = SlackAdapter("xoxb-fake", http_client=_client(handler), channel_ids=["C333"])
    events = adapter.poll_once()
    assert [e.message_id for e in events] == ["1793123456.000100", "1793123457.000100"]
    assert "oldest" not in seen[0]
    events = adapter.poll_once()
    assert "oldest=1793123457.000100" in seen[1]
    assert events == []  # both messages are at or before the stored timestamp


def test_poll_once_needs_channel_ids():
    adapter = SlackAdapter("xoxb-fake", http_client=_client(lambda r: None))
    with pytest.raises(DeliveryError, match="at least one channel id"):
        adapter.poll_once()


def test_poll_failure_raises():
    def handler(request):
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    adapter = SlackAdapter("xoxb-bad", http_client=_client(handler), channel_ids=["C333"])
    with pytest.raises(DeliveryError, match="invalid_auth"):
        adapter.poll_once()


# -- token check + webhook -------------------------------------------------


def test_check_token_success_and_invalid():
    def ok_handler(request):
        return httpx.Response(
            200, json={"ok": True, "user_id": "U999", "team_id": "T1", "user": "puspa"}
        )

    adapter = SlackAdapter("xoxb-fake", http_client=_client(ok_handler))
    assert adapter.check_token()["user"] == "puspa"

    def bad_handler(request):
        return httpx.Response(200, json={"ok": False, "error": "invalid_auth"})

    adapter = SlackAdapter("xoxb-bad", http_client=_client(bad_handler))
    with pytest.raises(DeliveryError, match="invalid_auth"):
        adapter.check_token()


def _signed_request(secret, body, timestamp=None):
    timestamp = timestamp or str(int(time.time()))
    signature = (
        "v0="
        + hmac.new(secret.encode(), f"v0:{timestamp}:".encode() + body, hashlib.sha256).hexdigest()
    )
    return {
        "body": body,
        "headers": {"X-Slack-Signature": signature, "X-Slack-Request-Timestamp": timestamp},
    }


def test_verify_webhook_signature():
    adapter = SlackAdapter("xoxb-fake", signing_secret="s3cr3t")
    body = b'{"event": {}}'
    assert adapter.verify_webhook(_signed_request("s3cr3t", body)) is True
    assert adapter.verify_webhook(_signed_request("wrong", body)) is False
    assert adapter.verify_webhook({"body": body, "headers": {}}) is False
    stale = str(int(time.time()) - 301)
    assert adapter.verify_webhook(_signed_request("s3cr3t", body, timestamp=stale)) is False


def test_verify_webhook_without_secret_passes():
    adapter = SlackAdapter("xoxb-fake")
    assert adapter.verify_webhook({"body": b"{}", "headers": {}}) is True


def test_adapter_requires_token():
    with pytest.raises(ValueError):
        SlackAdapter("")


# -- round-trip through the pipeline + runner ------------------------------


def test_round_trip_message_through_pipeline():
    adapter = SlackAdapter("xoxb-fake", http_client=_round_trip_client())
    outcome = handle_inbound(
        MessageEvent(
            platform="slack",
            chat_id="D111",
            sender_id="U222",
            text="hello world please",
            message_id="1793123456.000100",
        ),
        adapter=adapter,
        agent=FakeAgent(),
        pairing=ChannelPairing(),
        allowlist=Allowlist.parse("slack:D111"),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "answered"
    assert outcome.reply == "fake agent reply"


def test_runner_wires_slack(tmp_path):
    profile = f"slack-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        slack_bot_token="xoxb-fake",
        slack_channel_ids=("D111",),
        allowlist=Allowlist.parse("slack:D111"),
        state_dir=str(tmp_path),
    )
    runner = GatewayRunner.open(
        profile=profile,
        config=config,
        agent=FakeAgent(),
        http_client=_round_trip_client(),
    )
    try:
        assert runner.adapter_for("slack").platform == "slack"
        assert runner.slack_adapter() is runner.adapter_for("slack")
        outcomes = runner.handle_webhook_payload("slack", _envelope(_event()))
        assert len(outcomes) == 1
        assert outcomes[0].outcome == "answered"
        assert outcomes[0].reply == "fake agent reply"
    finally:
        runner.close()


def test_runner_poll_slack_once(tmp_path):
    calls = {"gets": 0}

    def handler(request):
        if "conversations.history" in str(request.url):
            calls["gets"] += 1
            if calls["gets"] == 1:
                return httpx.Response(
                    200,
                    json={
                        "ok": True,
                        "messages": [
                            _event(channel="C333", ts="1793123457.000100"),
                            _event(channel="C333", ts="1793123456.000100"),
                        ],
                    },
                )
            return httpx.Response(200, json={"ok": True, "messages": []})
        if "chat.postMessage" in str(request.url):
            return _ok_send(request)
        return httpx.Response(200, json={"ok": False, "error": "unknown_method"})

    profile = f"slack-poll-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        slack_bot_token="xoxb-fake",
        slack_channel_ids=("C333",),
        allowlist=Allowlist.parse("slack:C333"),
        state_dir=str(tmp_path),
    )
    runner = GatewayRunner.open(
        profile=profile,
        config=config,
        agent=FakeAgent(),
        http_client=_client(handler),
    )
    try:
        # A channel message is a group message: without a room link the
        # pipeline drops it, so the poll proves delivery of the batch
        # (two events seen) rather than an answer.
        outcomes = runner.poll_slack_once()
        assert [o.outcome for o in outcomes] == ["dropped_group", "dropped_group"]
        assert runner.poll_slack_once() == []
    finally:
        runner.close()


def test_runner_requires_slack_token(tmp_path):
    runner = GatewayRunner.open(
        profile=f"no-slack-{uuid.uuid4().hex[:8]}",
        config=ChannelConfig(state_dir=str(tmp_path)),
        agent=FakeAgent(),
        http_client=_round_trip_client(),
    )
    try:
        with pytest.raises(ChannelConfigError):
            runner.slack_adapter()
    finally:
        runner.close()


def test_load_config_slack_env():
    config = load_config(
        {
            "SLACK_BOT_TOKEN": "xoxb-tok",
            "SLACK_CHANNEL_IDS": "C333, D111",
            "SLACK_SIGNING_SECRET": "s3cr3t",
        }
    )
    assert config.slack_enabled is True
    assert config.slack_channel_ids == ("C333", "D111")
    assert config.require_slack() == "xoxb-tok"
    assert load_config({}).slack_enabled is False
    with pytest.raises(ChannelConfigError):
        load_config({}).require_slack()
