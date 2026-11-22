"""Discord adapter tests (AST-04).

No real token, no real network: every HTTP call goes through
``httpx.MockTransport``. Nothing here contacts discord.com.
"""

from __future__ import annotations

import uuid

import httpx
import pytest
from cryptography.hazmat.primitives.asymmetric.ed25519 import Ed25519PrivateKey

from puspa.channels.authz import Allowlist, ChannelPairing
from puspa.channels.config import ChannelConfig, ChannelConfigError, load_config
from puspa.channels.discord import DiscordAdapter
from puspa.channels.errors import DeliveryError
from puspa.channels.events import MessageEvent
from puspa.channels.inbound import handle_inbound
from puspa.channels.ledger import InMemoryDeliveryLedger
from puspa.channels.run import GatewayRunner


def _message(
    message_id="1001",
    channel_id="2001",
    user_id="3001",
    content="hello world please",
    is_bot=False,
    guild_id=None,
):
    message = {
        "id": message_id,
        "channel_id": channel_id,
        "author": {"id": user_id, "bot": is_bot, "username": "Test"},
        "content": content,
        "timestamp": "2026-11-22T09:00:00.000Z",
    }
    if guild_id is not None:
        message["guild_id"] = guild_id
    return message


def _dispatch(message):
    return {"t": "MESSAGE_CREATE", "d": message}


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def _ok_send(request):
    return httpx.Response(
        200,
        json={"id": "1002", "channel_id": "2001", "content": "hi"},
    )


class FakeAgent:
    def __init__(self, reply="fake agent reply"):
        self.reply = reply
        self.calls = []

    def answer(self, event, *, user_id):
        self.calls.append((event, user_id))
        return self.reply


def _round_trip_client():
    def handler(request):
        if request.url.path.endswith("/messages") and request.method == "POST":
            return _ok_send(request)
        return httpx.Response(404, json={"message": "not found"})

    return _client(handler)


# -- parse ---------------------------------------------------------------


def test_parse_update_dispatch():
    adapter = DiscordAdapter("fake-token")
    events = adapter.parse_update(_dispatch(_message()))
    assert len(events) == 1
    event = events[0]
    assert event.platform == "discord"
    assert event.chat_id == "2001"
    assert event.sender_id == "3001"
    assert event.text == "hello world please"
    assert event.message_id == "1001"
    assert event.is_bot is False
    assert event.is_group is False
    assert event.dedup_key == "discord:2001:1001"
    assert event.session_key == "discord:2001"


def test_parse_update_bare_message():
    adapter = DiscordAdapter("fake-token")
    assert len(adapter.parse_update(_message())) == 1


def test_parse_update_bot_sender_marked():
    adapter = DiscordAdapter("fake-token")
    events = adapter.parse_update(_dispatch(_message(is_bot=True)))
    assert events[0].is_bot is True


def test_parse_update_own_id_marked_as_bot():
    adapter = DiscordAdapter("fake-token", bot_user_id="9999")
    events = adapter.parse_update(_dispatch(_message(user_id="9999")))
    assert events[0].is_bot is True


def test_parse_update_guild_marked_as_group():
    adapter = DiscordAdapter("fake-token")
    events = adapter.parse_update(_dispatch(_message(guild_id="4001")))
    assert events[0].is_group is True


def test_parse_update_mention_marks_bot():
    adapter = DiscordAdapter("fake-token", bot_user_id="9999")
    message = _message(guild_id="4001")
    message["mentions"] = [{"id": "9999"}]
    events = adapter.parse_update(_dispatch(message))
    assert events[0].mentions_bot is True


def test_parse_update_non_message_ignored():
    adapter = DiscordAdapter("fake-token")
    assert adapter.parse_update({"t": "MESSAGE_UPDATE", "d": _message()}) == []
    assert adapter.parse_update({"t": "READY", "d": {}}) == []
    assert adapter.parse_update({}) == []


# -- send -----------------------------------------------------------------


def test_send_text_success():
    seen = []

    def handler(request):
        seen.append(request)
        return _ok_send(request)

    adapter = DiscordAdapter("fake-token", http_client=_client(handler), sleep=lambda s: None)
    result = adapter.send_text("2001", "hello")
    assert result["id"] == "1002"
    assert len(seen) == 1
    assert str(seen[0].url) == "https://discord.com/api/v10/channels/2001/messages"
    assert seen[0].headers["Authorization"] == "Bot fake-token"


def test_send_text_retries_then_succeeds():
    calls = {"n": 0}
    sleeps = []

    def handler(request):
        calls["n"] += 1
        if calls["n"] < 3:
            return httpx.Response(500, json={"message": "boom"})
        return _ok_send(request)

    adapter = DiscordAdapter("fake-token", http_client=_client(handler), sleep=sleeps.append)
    result = adapter.send_text("2001", "hello")
    assert result["id"] == "1002"
    assert calls["n"] == 3
    assert sleeps == [1.0, 2.0]


def test_send_text_exhausted_raises():
    def handler(request):
        return httpx.Response(500, json={"message": "boom"})

    adapter = DiscordAdapter("fake-token", http_client=_client(handler), sleep=lambda s: None)
    with pytest.raises(DeliveryError, match="after 3 attempts"):
        adapter.send_text("2001", "hello")


def test_send_text_invalid_token_says_so():
    def handler(request):
        return httpx.Response(401, json={"message": "401: Unauthorized"})

    adapter = DiscordAdapter("bad-token", http_client=_client(handler), sleep=lambda s: None)
    with pytest.raises(DeliveryError, match="token is invalid"):
        adapter.send_text("2001", "hello")


# -- poll -----------------------------------------------------------------


def test_poll_once_uses_after_and_orders_oldest_first():
    seen = []

    def handler(request):
        seen.append(str(request.url))
        return httpx.Response(
            200,
            json=[_message("1003"), _message("1002")],
        )

    adapter = DiscordAdapter("fake-token", http_client=_client(handler), channel_ids=["2001"])
    events = adapter.poll_once()
    assert [e.message_id for e in events] == ["1002", "1003"]
    assert "limit=50" in seen[0]
    assert "after" not in seen[0]
    # The next batch pages past the newest message seen.
    events = adapter.poll_once()
    assert "after=1003" in seen[1]
    assert events[0].message_id == "1002"


def test_poll_once_needs_channel_ids():
    adapter = DiscordAdapter("fake-token", http_client=_client(lambda r: None))
    with pytest.raises(DeliveryError, match="at least one channel id"):
        adapter.poll_once()


def test_poll_failure_raises():
    def handler(request):
        return httpx.Response(401, json={"message": "401: Unauthorized"})

    adapter = DiscordAdapter("bad-token", http_client=_client(handler), channel_ids=["2001"])
    with pytest.raises(DeliveryError, match="token is invalid"):
        adapter.poll_once()


# -- token check + webhook -------------------------------------------------


def test_check_token_success_and_invalid():
    def ok_handler(request):
        return httpx.Response(200, json={"id": "9999", "username": "Puspa"})

    adapter = DiscordAdapter("fake-token", http_client=_client(ok_handler))
    assert adapter.check_token()["username"] == "Puspa"

    def bad_handler(request):
        return httpx.Response(401, json={"message": "401: Unauthorized"})

    adapter = DiscordAdapter("bad-token", http_client=_client(bad_handler))
    with pytest.raises(DeliveryError, match="token is invalid"):
        adapter.check_token()


def test_verify_webhook_ed25519():
    private = Ed25519PrivateKey.generate()
    public_hex = private.public_key().public_bytes_raw().hex()
    adapter = DiscordAdapter("fake-token", public_key=public_hex)
    body = b'{"type": 1}'
    timestamp = "1793123456"
    signature = private.sign(timestamp.encode() + body).hex()
    good = {
        "body": body,
        "headers": {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": timestamp},
    }
    assert adapter.verify_webhook(good) is True
    bad = {
        "body": b'{"type": 2}',
        "headers": {"X-Signature-Ed25519": signature, "X-Signature-Timestamp": timestamp},
    }
    assert adapter.verify_webhook(bad) is False
    assert adapter.verify_webhook({"body": body, "headers": {}}) is False


def test_verify_webhook_without_public_key_passes():
    adapter = DiscordAdapter("fake-token")
    assert adapter.verify_webhook({"body": b"{}", "headers": {}}) is True


def test_adapter_requires_token():
    with pytest.raises(ValueError):
        DiscordAdapter("")


# -- round-trip through the pipeline + runner ------------------------------


def test_round_trip_message_through_pipeline():
    adapter = DiscordAdapter("fake-token", http_client=_round_trip_client())
    outcome = handle_inbound(
        MessageEvent(
            platform="discord",
            chat_id="2001",
            sender_id="3001",
            text="hello world please",
            message_id="1001",
        ),
        adapter=adapter,
        agent=FakeAgent(),
        pairing=ChannelPairing(),
        allowlist=Allowlist.parse("discord:2001"),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "answered"
    assert outcome.reply == "fake agent reply"


def test_runner_wires_discord(tmp_path):
    profile = f"discord-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        discord_bot_token="fake-token",
        discord_channel_ids=("2001",),
        allowlist=Allowlist.parse("discord:2001"),
        state_dir=str(tmp_path),
    )
    runner = GatewayRunner.open(
        profile=profile,
        config=config,
        agent=FakeAgent(),
        http_client=_round_trip_client(),
    )
    try:
        assert runner.adapter_for("discord").platform == "discord"
        assert runner.discord_adapter() is runner.adapter_for("discord")
        outcomes = runner.handle_webhook_payload("discord", _dispatch(_message()))
        assert len(outcomes) == 1
        assert outcomes[0].outcome == "answered"
        assert outcomes[0].reply == "fake agent reply"
    finally:
        runner.close()


def test_runner_poll_discord_once(tmp_path):
    calls = {"gets": 0}

    def handler(request):
        if request.method == "GET" and "/messages" in str(request.url):
            calls["gets"] += 1
            if calls["gets"] == 1:
                return httpx.Response(200, json=[_message("1002"), _message("1001")])
            return httpx.Response(200, json=[])
        if request.method == "POST":
            return _ok_send(request)
        return httpx.Response(404, json={"message": "not found"})

    profile = f"discord-poll-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        discord_bot_token="fake-token",
        discord_channel_ids=("2001",),
        allowlist=Allowlist.parse("discord:2001"),
        state_dir=str(tmp_path),
    )
    runner = GatewayRunner.open(
        profile=profile,
        config=config,
        agent=FakeAgent(),
        http_client=_client(handler),
    )
    try:
        outcomes = runner.poll_discord_once()
        assert [o.outcome for o in outcomes] == ["answered", "answered"]
        assert runner.poll_discord_once() == []
    finally:
        runner.close()


def test_runner_requires_discord_token(tmp_path):
    runner = GatewayRunner.open(
        profile=f"no-discord-{uuid.uuid4().hex[:8]}",
        config=ChannelConfig(state_dir=str(tmp_path)),
        agent=FakeAgent(),
        http_client=_round_trip_client(),
    )
    try:
        with pytest.raises(ChannelConfigError):
            runner.discord_adapter()
    finally:
        runner.close()


def test_load_config_discord_env():
    config = load_config(
        {
            "DISCORD_BOT_TOKEN": "tok",
            "DISCORD_CHANNEL_IDS": "2001, 2002",
            "DISCORD_BOT_USER_ID": "9999",
        }
    )
    assert config.discord_enabled is True
    assert config.discord_channel_ids == ("2001", "2002")
    assert config.require_discord() == "tok"
    assert load_config({}).discord_enabled is False
    with pytest.raises(ChannelConfigError):
        load_config({}).require_discord()
