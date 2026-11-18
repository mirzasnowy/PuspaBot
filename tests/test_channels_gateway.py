"""Gateway pipeline tests (G14): authz, pairing, inbound, runner.

No real token, no real network: the runner is built with fake
tokens and an ``httpx.MockTransport`` client. Nothing here
contacts api.telegram.org or graph.facebook.com.
"""

from __future__ import annotations

import uuid

import httpx
import pytest

from puspa.channels.agent import TaskRunnerAgent
from puspa.channels.authz import Allowlist, ChannelPairing
from puspa.channels.config import ChannelConfig, ChannelConfigError, load_config
from puspa.channels.errors import PairingError, SingleWriterError, UnauthorizedChatError
from puspa.channels.events import MessageEvent
from puspa.channels.heartbeat import HeartbeatStore
from puspa.channels.inbound import handle_inbound
from puspa.channels.ledger import InMemoryDeliveryLedger
from puspa.channels.notify import notification_text
from puspa.channels.run import GatewayRunner
from puspa.channels.telegram import TelegramAdapter
from puspa.runtime.tasks import TaskRunner


class FakeClock:
    def __init__(self, now=1_000_000.0):
        self.now = now

    def __call__(self):
        return self.now


class FakeAgent:
    def __init__(self, reply="fake agent reply"):
        self.reply = reply
        self.calls = []

    def answer(self, event, *, user_id):
        self.calls.append((event, user_id))
        return self.reply


def _telegram_client():
    def handler(request):
        if "sendMessage" in str(request.url):
            return httpx.Response(
                200,
                json={"ok": True, "result": {"message_id": 7, "chat": {"id": 111}}},
            )
        if "getUpdates" in str(request.url):
            return httpx.Response(200, json={"ok": True, "result": []})
        return httpx.Response(404, json={"ok": False})

    return httpx.Client(transport=httpx.MockTransport(handler))


def _event(**overrides):
    event = {
        "platform": "telegram",
        "chat_id": "111",
        "sender_id": "222",
        "text": "hello world please",
        "message_id": "5",
    }
    event.update(overrides)
    return MessageEvent(**event)


def _runner(tmp_path, **overrides):
    profile = f"test-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        telegram_bot_token="fake-token",
        whatsapp_access_token="fake-wa-token",
        whatsapp_phone_number_id="555",
        allowlist=Allowlist.parse("telegram:111"),
        state_dir=str(tmp_path),
    )
    kwargs = {
        "profile": profile,
        "config": config,
        "agent": FakeAgent(),
        "http_client": _telegram_client(),
    }
    kwargs.update(overrides)
    return GatewayRunner.open(**kwargs)


# -- config / allowlist -----------------------------------------------------


def test_allowlist_parse_and_check():
    allowlist = Allowlist.parse("telegram:111, whatsapp:6281 ,telegram:222")
    assert allowlist.allows("telegram", "111") is True
    assert allowlist.allows("whatsapp", "6281") is True
    assert allowlist.allows("telegram", "999") is False
    assert Allowlist.parse(None).allows("telegram", "111") is False
    assert Allowlist.parse("").allows("telegram", "111") is False
    with pytest.raises(ValueError):
        Allowlist.parse("telegram")


def test_load_config_from_env():
    config = load_config(
        {
            "TELEGRAM_BOT_TOKEN": "tok",
            "PUSPA_CHANNEL_ALLOWLIST": "telegram:111",
            "PUSPA_BOT_NAME": "Puspa",
        }
    )
    assert config.telegram_enabled is True
    assert config.whatsapp_enabled is False
    assert config.allowlist.allows("telegram", "111") is True
    assert config.require_telegram() == "tok"
    with pytest.raises(ChannelConfigError):
        config.require_whatsapp()


# -- pairing ----------------------------------------------------------------


def test_pairing_issue_verify_binds_chat():
    clock = FakeClock()
    pairing = ChannelPairing(clock=clock)
    issued = pairing.issue("user-1")
    assert len(issued["code"]) == 6
    assert pairing.bound_user("telegram", "111") is None
    user_id = pairing.verify(issued["code"], platform="telegram", chat_id="111")
    assert user_id == "user-1"
    assert pairing.bound_user("telegram", "111") == "user-1"
    # Single use: the same code fails the second time.
    with pytest.raises(PairingError):
        pairing.verify(issued["code"], platform="telegram", chat_id="111")


def test_pairing_wrong_code_and_lockout():
    clock = FakeClock()
    pairing = ChannelPairing(clock=clock)
    for _ in range(5):
        with pytest.raises(PairingError, match="wrong pairing code"):
            pairing.verify("000000", platform="telegram", chat_id="111")
    assert pairing.is_locked("telegram", "111") is True
    with pytest.raises(PairingError, match="locked out"):
        pairing.verify("000000", platform="telegram", chat_id="111")
    clock.now += 601  # lockout is 10 minutes
    assert pairing.is_locked("telegram", "111") is False


def test_pairing_code_expires():
    clock = FakeClock()
    pairing = ChannelPairing(clock=clock)
    issued = pairing.issue("user-1")
    clock.now += 301  # TTL is 5 minutes
    with pytest.raises(PairingError, match="expired"):
        pairing.verify(issued["code"], platform="telegram", chat_id="111")


def test_pairing_authorize_allowlist_and_bound():
    pairing = ChannelPairing(clock=FakeClock())
    allowlist = Allowlist.parse("telegram:111")
    assert pairing.authorize("telegram", "111", allowlist=allowlist) is None
    issued = pairing.issue("user-1")
    pairing.verify(issued["code"], platform="telegram", chat_id="222")
    assert pairing.authorize("telegram", "222", allowlist=allowlist) == "user-1"
    with pytest.raises(UnauthorizedChatError):
        pairing.authorize("telegram", "333", allowlist=allowlist)


def test_pairing_unbind():
    pairing = ChannelPairing(clock=FakeClock())
    issued = pairing.issue("user-1")
    pairing.verify(issued["code"], platform="telegram", chat_id="111")
    assert pairing.unbind("telegram", "111") is True
    assert pairing.bound_user("telegram", "111") is None
    assert pairing.unbind("telegram", "111") is False


# -- inbound pipeline -------------------------------------------------------


def test_inbound_answered_via_allowlist():
    ledger = InMemoryDeliveryLedger()
    outcome = handle_inbound(
        _event(),
        adapter=TelegramAdapter("fake-token", http_client=_telegram_client()),
        agent=FakeAgent(),
        pairing=ChannelPairing(clock=FakeClock()),
        allowlist=Allowlist.parse("telegram:111"),
        ledger=ledger,
    )
    assert outcome.outcome == "answered"
    assert outcome.reply == "fake agent reply"
    assert ledger.seen("telegram:111:5") is True


def test_inbound_bot_message_dropped():
    outcome = handle_inbound(
        _event(is_bot=True),
        adapter=TelegramAdapter("fake-token", http_client=_telegram_client()),
        agent=FakeAgent(),
        pairing=ChannelPairing(clock=FakeClock()),
        allowlist=Allowlist.parse("telegram:111"),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "dropped_bot"


def test_inbound_group_message_dropped():
    outcome = handle_inbound(
        _event(is_group=True),
        adapter=TelegramAdapter("fake-token", http_client=_telegram_client()),
        agent=FakeAgent(),
        pairing=ChannelPairing(clock=FakeClock()),
        allowlist=Allowlist.parse("telegram:111"),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "dropped_group"


def test_inbound_duplicate_dropped_once():
    ledger = InMemoryDeliveryLedger()
    kwargs = {
        "adapter": TelegramAdapter("fake-token", http_client=_telegram_client()),
        "agent": FakeAgent(),
        "pairing": ChannelPairing(clock=FakeClock()),
        "allowlist": Allowlist.parse("telegram:111"),
        "ledger": ledger,
    }
    assert handle_inbound(_event(), **kwargs).outcome == "answered"
    assert handle_inbound(_event(), **kwargs).outcome == "dropped_duplicate"


def test_inbound_unauthorized_dropped_silently():
    agent = FakeAgent()
    outcome = handle_inbound(
        _event(),
        adapter=TelegramAdapter("fake-token", http_client=_telegram_client()),
        agent=agent,
        pairing=ChannelPairing(clock=FakeClock()),
        allowlist=Allowlist.parse("telegram:999"),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "dropped_unauthorized"
    assert agent.calls == []  # the agent was never called


def test_inbound_pairing_code_flow():
    clock = FakeClock()
    pairing = ChannelPairing(clock=clock)
    issued = pairing.issue("user-1")
    adapter = TelegramAdapter("fake-token", http_client=_telegram_client())
    outcome = handle_inbound(
        _event(text=issued["code"]),
        adapter=adapter,
        agent=FakeAgent(),
        pairing=pairing,
        allowlist=Allowlist(),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "paired"
    assert outcome.user_id == "user-1"
    # The chat is now authorised: the next message reaches the agent.
    agent = FakeAgent()
    outcome = handle_inbound(
        _event(message_id="6"),
        adapter=adapter,
        agent=agent,
        pairing=pairing,
        allowlist=Allowlist(),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "answered"
    assert agent.calls[0][1] == "user-1"


def test_inbound_wrong_pairing_code():
    adapter = TelegramAdapter("fake-token", http_client=_telegram_client())
    outcome = handle_inbound(
        _event(text="123456"),
        adapter=adapter,
        agent=FakeAgent(),
        pairing=ChannelPairing(clock=FakeClock()),
        allowlist=Allowlist(),
        ledger=InMemoryDeliveryLedger(),
    )
    assert outcome.outcome == "pairing_failed"


# -- agent seam ---------------------------------------------------------------


def test_task_runner_agent_answers_via_task_runner():
    def executor(step_name, inputs):
        return {"type": "answer", "text": f"echo: {inputs['text']}"}

    agent = TaskRunnerAgent(executor=executor)
    reply = agent.answer(_event(), user_id="user-1")
    assert reply == "echo: hello world please"


def test_task_runner_agent_short_purpose_returns_question():
    def executor(step_name, inputs):
        raise AssertionError("executor must not run for a too-short purpose")

    agent = TaskRunnerAgent(executor=executor)
    reply = agent.answer(_event(text="hi"), user_id="user-1")
    assert reply  # the runner's clarifying question is the reply


def test_task_runner_agent_failed_task_returns_honest_notice():
    def executor(step_name, inputs):
        raise RuntimeError("model down")

    agent = TaskRunnerAgent(executor=executor)
    reply = agent.answer(_event(), user_id="user-1")
    assert "could not handle" in reply


def test_task_runner_agent_without_executor_fails_closed():
    agent = TaskRunnerAgent()
    reply = agent.answer(_event(), user_id="user-1")
    assert "could not handle" in reply


def test_task_runner_agent_uses_injected_runner():
    events = []
    runner = TaskRunner(
        executor=lambda name, inputs: {"type": "answer", "text": "ok"},
        sink=lambda kind, content: events.append(kind),
    )
    agent = TaskRunnerAgent(runner=runner)
    assert agent.answer(_event(), user_id="user-1") == "ok"
    assert "task_created" in events


# -- runner: single writer, polling, heartbeat, notify ------------------------


def test_runner_single_writer(tmp_path):
    runner = _runner(tmp_path)
    try:
        with pytest.raises(SingleWriterError):
            GatewayRunner.open(profile=runner.profile)
    finally:
        runner.close()
    # After close, the profile can be opened again.
    reopened = GatewayRunner.open(
        profile=runner.profile,
        config=runner.config,
        agent=FakeAgent(),
        http_client=_telegram_client(),
    )
    reopened.close()


def test_runner_handle_event_and_webhook_payload(tmp_path):
    runner = _runner(tmp_path)
    try:
        outcome = runner.handle_event(_event())
        assert outcome.outcome == "answered"
        outcomes = runner.handle_webhook_payload(
            "telegram",
            {
                "update_id": 99,
                "message": {
                    "message_id": 8,
                    "date": 1793123456,
                    "chat": {"id": 111, "type": "private"},
                    "from": {"id": 222, "is_bot": False},
                    "text": "hello world please",
                },
            },
        )
        assert len(outcomes) == 1
        assert outcomes[0].outcome == "answered"
    finally:
        runner.close()


def test_runner_poll_restores_offset(tmp_path):
    store = HeartbeatStore(profile="poll-profile", state_dir=str(tmp_path))
    store.beat(last_offset=50)
    runner = GatewayRunner.open(
        profile="poll-profile",
        config=ChannelConfig(telegram_bot_token="fake-token", state_dir=str(tmp_path)),
        agent=FakeAgent(),
        http_client=_telegram_client(),
    )
    try:
        assert runner.telegram_offset == 50
        outcomes = runner.poll_telegram_once()
        assert outcomes == []  # the mock returns no updates
        assert runner.telegram_offset == 50
    finally:
        runner.close()


def test_runner_requires_token_for_adapter(tmp_path):
    runner = GatewayRunner.open(
        profile=f"no-token-{uuid.uuid4().hex[:8]}",
        config=ChannelConfig(state_dir=str(tmp_path)),
        agent=FakeAgent(),
        http_client=_telegram_client(),
    )
    try:
        with pytest.raises(ChannelConfigError):
            runner.telegram_adapter()
        with pytest.raises(ChannelConfigError):
            runner.adapter_for("signal")
    finally:
        runner.close()


def test_heartbeat_save_load_and_age(tmp_path):
    clock = FakeClock()
    store = HeartbeatStore(profile="hb", state_dir=str(tmp_path), clock=clock)
    assert store.load().last_offset is None
    assert store.age_seconds() is None
    beaten = store.beat(last_offset=12)
    assert beaten.last_offset == 12
    assert store.load().last_offset == 12
    clock.now += 30
    assert store.age_seconds() == 30


def test_heartbeat_corrupt_file_restores_empty(tmp_path):
    store = HeartbeatStore(profile="hb", state_dir=str(tmp_path))
    path = store.path
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{not json", encoding="utf-8")
    assert store.load().last_offset is None


def test_notification_text_is_bot_name_only():
    assert notification_text() == "Puspa"
    assert notification_text("Puspa") == "Puspa"


def test_runner_notify_sends_bot_name_only(tmp_path):
    runner = _runner(tmp_path)
    try:
        record = runner.notify("telegram", "111")
        assert record is not None
        assert record.status == "sent"
    finally:
        runner.close()
