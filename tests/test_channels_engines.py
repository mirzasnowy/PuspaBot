"""External agent engine tests (AST-05): Dify, Coze, Bailian.

No real key, no real network: every adapter runs against an
``httpx.MockTransport`` fake server that asserts the request
shape (URL, auth header, body) and returns canned platform
responses. The fake servers are local by construction - the
tests never contact api.dify.ai, api.coze.com, or
dashscope-intl.aliyuncs.com.
"""

from __future__ import annotations

import json
import uuid

import httpx
import pytest

from puspa.channels import cli as channels_cli
from puspa.channels.agent import TaskRunnerAgent
from puspa.channels.authz import Allowlist
from puspa.channels.config import ChannelConfig, ChannelConfigError, load_config
from puspa.channels.engines import ENGINE_NAMES, EngineError, ExternalEngineAgent
from puspa.channels.engines_bailian import BailianEngine
from puspa.channels.engines_coze import CozeEngine
from puspa.channels.engines_dify import DifyEngine
from puspa.channels.events import MessageEvent
from puspa.channels.run import GatewayRunner
from puspa.cli import build_parser, main


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


# ---------------------------------------------------------------------------
# Dify
# ---------------------------------------------------------------------------


def _dify_client(calls, *, answer="dify says hi", conversation_id="dify-conv-1"):
    def handler(request):
        calls.append(request)
        assert str(request.url) == "https://api.dify.ai/v1/chat-messages"
        assert request.headers["Authorization"] == "Bearer app-fake-key"
        body = json.loads(request.content)
        assert body["response_mode"] == "blocking"
        assert body["user"] == "222"
        return httpx.Response(200, json={"answer": answer, "conversation_id": conversation_id})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_dify_send_returns_answer_and_keeps_conversation():
    calls: list = []
    engine = DifyEngine("app-fake-key", http_client=_dify_client(calls), sleep=lambda s: None)
    reply = engine.send("hello world please", conversation_id="telegram:111", user_id="222")
    assert reply == "dify says hi"
    assert json.loads(calls[0].content)["query"] == "hello world please"
    assert json.loads(calls[0].content)["conversation_id"] == ""
    reply2 = engine.send("and then?", conversation_id="telegram:111", user_id="222")
    assert reply2 == "dify says hi"
    # Second turn carries the conversation id the first reply returned.
    assert json.loads(calls[1].content)["conversation_id"] == "dify-conv-1"


def test_dify_rejected_key_fails_fast_without_retry():
    calls: list = []

    def handler(request):
        calls.append(request)
        return httpx.Response(401, json={"message": "Unauthorized"})

    engine = DifyEngine(
        "app-bad-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    with pytest.raises(EngineError, match="rejected the API key"):
        engine.send("hello", conversation_id="telegram:111", user_id="222")
    assert len(calls) == 1


def test_dify_server_error_retries_then_reports_unreachable():
    calls: list = []
    sleeps: list = []

    def handler(request):
        calls.append(request)
        return httpx.Response(500, json={"message": "boom"})

    engine = DifyEngine(
        "app-fake-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=sleeps.append,
    )
    with pytest.raises(EngineError, match="unreachable after 3 attempts"):
        engine.send("hello", conversation_id="telegram:111", user_id="222")
    assert len(calls) == 3
    assert sleeps == [1.0, 2.0]


def test_dify_transport_error_retries_then_reports_unreachable():
    calls: list = []

    def handler(request):
        calls.append(request)
        raise httpx.ConnectError("boom", request=request)

    engine = DifyEngine(
        "app-fake-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    with pytest.raises(EngineError, match="unreachable after 3 attempts"):
        engine.send("hello", conversation_id="telegram:111", user_id="222")
    assert len(calls) == 3


def test_dify_answer_missing_is_an_error():
    def handler(request):
        return httpx.Response(200, json={"conversation_id": "dify-conv-1"})

    engine = DifyEngine(
        "app-fake-key",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    with pytest.raises(EngineError, match="without an answer"):
        engine.send("hello", conversation_id="telegram:111", user_id="222")


def test_dify_requires_a_key():
    with pytest.raises(ValueError, match="requires an API key"):
        DifyEngine("")


# ---------------------------------------------------------------------------
# Coze
# ---------------------------------------------------------------------------


def _coze_client(calls, *, retrieve_statuses=("in_progress", "completed")):
    state = {"retrieves": 0}

    def handler(request):
        calls.append(request)
        assert request.headers["Authorization"] == "Bearer coze-fake-token"
        url = str(request.url)
        if url.endswith("/v3/chat"):
            body = json.loads(request.content)
            assert body["bot_id"] == "bot-123"
            assert body["user_id"] == "222"
            messages = body["additional_messages"]
            assert len(messages) == 1
            assert messages[0]["role"] == "user"
            assert messages[0]["content_type"] == "text"
            assert isinstance(messages[0]["content"], str) and messages[0]["content"]
            assert body["stream"] is False
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "id": "chat-1",
                        "conversation_id": "coze-conv-1",
                        "status": "in_progress",
                    },
                },
            )
        if "/v3/chat/retrieve" in url:
            status = retrieve_statuses[min(state["retrieves"], len(retrieve_statuses) - 1)]
            state["retrieves"] += 1
            return httpx.Response(
                200,
                json={"code": 0, "data": {"id": "chat-1", "status": status}},
            )
        if "/v3/chat/message/list" in url:
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": [
                        {"role": "user", "type": "question", "content": "hello world please"},
                        {"role": "assistant", "type": "answer", "content": "coze says hi"},
                    ],
                },
            )
        return httpx.Response(404, json={"code": 404, "msg": "not found"})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_coze_send_polls_then_reads_answer():
    calls: list = []
    engine = CozeEngine(
        "coze-fake-token", "bot-123", http_client=_coze_client(calls), sleep=lambda s: None
    )
    reply = engine.send("hello world please", conversation_id="telegram:111", user_id="222")
    assert reply == "coze says hi"
    urls = [str(call.url) for call in calls]
    assert urls[0].endswith("/v3/chat")
    assert any("/v3/chat/retrieve" in url for url in urls)
    assert any("/v3/chat/message/list" in url for url in urls)


def test_coze_second_turn_reuses_conversation():
    calls: list = []
    engine = CozeEngine(
        "coze-fake-token", "bot-123", http_client=_coze_client(calls), sleep=lambda s: None
    )
    engine.send("hello world please", conversation_id="telegram:111", user_id="222")
    engine.send("and then?", conversation_id="telegram:111", user_id="222")
    creates = [c for c in calls if str(c.url).endswith("/v3/chat")]
    assert json.loads(creates[0].content).get("conversation_id") is None
    assert json.loads(creates[1].content)["conversation_id"] == "coze-conv-1"


def test_coze_create_error_is_an_error():
    def handler(request):
        return httpx.Response(200, json={"code": 70001, "msg": "invalid bot id"})

    engine = CozeEngine(
        "coze-fake-token",
        "bot-123",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    with pytest.raises(EngineError, match="invalid bot id"):
        engine.send("hello", conversation_id="telegram:111", user_id="222")


def test_coze_failed_chat_is_an_error():
    calls: list = []
    engine = CozeEngine(
        "coze-fake-token",
        "bot-123",
        http_client=_coze_client(calls, retrieve_statuses=("failed",)),
        sleep=lambda s: None,
    )
    with pytest.raises(EngineError, match="status 'failed'"):
        engine.send("hello world please", conversation_id="telegram:111", user_id="222")


def test_coze_chat_that_never_completes_is_an_error():
    calls: list = []
    engine = CozeEngine(
        "coze-fake-token",
        "bot-123",
        http_client=_coze_client(calls, retrieve_statuses=("in_progress",)),
        sleep=lambda s: None,
        max_polls=2,
    )
    with pytest.raises(EngineError, match="did not complete after 2 polls"):
        engine.send("hello world please", conversation_id="telegram:111", user_id="222")


def test_coze_no_assistant_answer_is_an_error():
    def handler(request):
        url = str(request.url)
        if url.endswith("/v3/chat"):
            return httpx.Response(
                200,
                json={
                    "code": 0,
                    "data": {
                        "id": "chat-1",
                        "conversation_id": "coze-conv-1",
                        "status": "in_progress",
                    },
                },
            )
        if "/v3/chat/retrieve" in url:
            return httpx.Response(
                200, json={"code": 0, "data": {"id": "chat-1", "status": "completed"}}
            )
        return httpx.Response(
            200,
            json={
                "code": 0,
                "data": [{"role": "user", "type": "question", "content": "hello"}],
            },
        )

    engine = CozeEngine(
        "coze-fake-token",
        "bot-123",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    with pytest.raises(EngineError, match="no assistant answer"):
        engine.send("hello", conversation_id="telegram:111", user_id="222")


def test_coze_requires_token_and_bot():
    with pytest.raises(ValueError, match="requires an API token"):
        CozeEngine("", "bot-123")
    with pytest.raises(ValueError, match="requires a bot id"):
        CozeEngine("coze-fake-token", "")


# ---------------------------------------------------------------------------
# Bailian
# ---------------------------------------------------------------------------


def _bailian_client(calls, *, text="bailian says hi"):
    def handler(request):
        calls.append(request)
        assert str(request.url) == (
            "https://dashscope-intl.aliyuncs.com/api/v1/apps/app-fake-id/completion"
        )
        assert request.headers["Authorization"] == "Bearer bailian-fake-key"
        return httpx.Response(
            200,
            json={
                "output": {
                    "text": text,
                    "session_id": "telegram:111",
                    "finish_reason": "stop",
                },
                "request_id": "req-1",
            },
        )

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_bailian_send_uses_prompt_and_session_id():
    calls: list = []
    engine = BailianEngine(
        "bailian-fake-key",
        "app-fake-id",
        http_client=_bailian_client(calls),
        sleep=lambda s: None,
    )
    reply = engine.send("hello world please", conversation_id="telegram:111", user_id="222")
    assert reply == "bailian says hi"
    body = json.loads(calls[0].content)
    assert body["input"] == {"prompt": "hello world please", "session_id": "telegram:111"}


def test_bailian_send_with_history_uses_messages():
    calls: list = []
    engine = BailianEngine(
        "bailian-fake-key",
        "app-fake-id",
        http_client=_bailian_client(calls),
        sleep=lambda s: None,
    )
    history = (
        {"role": "user", "content": "what is 2+2?"},
        {"role": "assistant", "content": "4"},
    )
    reply = engine.send("and 3+3?", conversation_id="telegram:111", user_id="222", history=history)
    assert reply == "bailian says hi"
    body = json.loads(calls[0].content)
    assert body["input"] == {
        "messages": [
            {"role": "user", "content": "what is 2+2?"},
            {"role": "assistant", "content": "4"},
            {"role": "user", "content": "and 3+3?"},
        ]
    }


def test_bailian_rejected_key_is_an_error():
    def handler(request):
        return httpx.Response(401, json={"code": "InvalidApiKey", "message": "bad key"})

    engine = BailianEngine(
        "bailian-bad-key",
        "app-fake-id",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
        sleep=lambda s: None,
    )
    with pytest.raises(EngineError, match="rejected the API key"):
        engine.send("hello", conversation_id="telegram:111", user_id="222")


def test_bailian_requires_key_and_app():
    with pytest.raises(ValueError, match="requires an API key"):
        BailianEngine("", "app-fake-id")
    with pytest.raises(ValueError, match="requires an app id"):
        BailianEngine("bailian-fake-key", "")


# ---------------------------------------------------------------------------
# Agent wrapper (the ChannelAgent seam)
# ---------------------------------------------------------------------------


class _StubEngine:
    def __init__(self, *, reply="stub reply", error=None):
        self.platform = "dify"
        self.reply = reply
        self.error = error
        self.calls = []
        self.closed = False

    def send(self, text, *, conversation_id, user_id, history=()):
        self.calls.append((text, conversation_id, user_id))
        if self.error is not None:
            raise self.error
        return self.reply

    def close(self):
        self.closed = True


def test_agent_wrapper_passes_event_through_engine():
    stub = _StubEngine(reply="engine reply")
    agent = ExternalEngineAgent(stub)
    reply = agent.answer(_event(), user_id="u-1")
    assert reply == "engine reply"
    assert stub.calls == [("hello world please", "telegram:111", "u-1")]


def test_agent_wrapper_turns_engine_error_into_honest_notice():
    stub = _StubEngine(error=EngineError("dify engine is unreachable after 3 attempts"))
    agent = ExternalEngineAgent(stub)
    reply = agent.answer(_event(), user_id="u-1")
    assert "dify engine could not answer" in reply
    assert "unreachable" in reply


def test_agent_wrapper_empty_reply_is_a_notice():
    stub = _StubEngine(reply="   ")
    agent = ExternalEngineAgent(stub)
    assert "empty reply" in agent.answer(_event(), user_id="u-1")


# ---------------------------------------------------------------------------
# Config: the engine switch
# ---------------------------------------------------------------------------


def test_engine_names_cover_builtin_and_three_platforms():
    assert ENGINE_NAMES == ("builtin", "dify", "coze", "bailian")


def test_load_config_reads_engine_env(monkeypatch):
    monkeypatch.setenv("PUSPA_CHANNEL_ENGINE", "dify")
    monkeypatch.setenv("DIFY_API_KEY", "app-fake-key")
    monkeypatch.setenv("DIFY_BASE_URL", "http://localhost:9999/v1")
    config = load_config()
    assert config.engine == "dify"
    assert config.dify_api_key == "app-fake-key"
    assert config.dify_base_url == "http://localhost:9999/v1"


def test_load_config_defaults_to_builtin():
    config = load_config({})
    assert config.engine == "builtin"
    assert config.dify_base_url == "https://api.dify.ai/v1"
    assert config.coze_base_url == "https://api.coze.com"
    assert config.bailian_base_url == "https://dashscope-intl.aliyuncs.com"


def test_unknown_engine_fails_at_load():
    with pytest.raises(ChannelConfigError, match="unknown channel engine: 'nope'"):
        ChannelConfig(engine="nope")


def test_build_engine_builtin_is_none():
    assert ChannelConfig().build_engine() is None


def test_build_engine_missing_key_names_the_variable():
    with pytest.raises(ChannelConfigError, match="DIFY_API_KEY is not set"):
        ChannelConfig(engine="dify").build_engine()
    with pytest.raises(ChannelConfigError, match="COZE_API_TOKEN is not set"):
        ChannelConfig(engine="coze").build_engine()
    with pytest.raises(ChannelConfigError, match="COZE_BOT_ID is not set"):
        ChannelConfig(engine="coze", coze_api_token="tok").build_engine()
    with pytest.raises(ChannelConfigError, match="BAILIAN_API_KEY is not set"):
        ChannelConfig(engine="bailian").build_engine()
    with pytest.raises(ChannelConfigError, match="BAILIAN_APP_ID is not set"):
        ChannelConfig(engine="bailian", bailian_api_key="key").build_engine()


def test_build_engine_builds_each_platform():
    assert isinstance(ChannelConfig(engine="dify", dify_api_key="k").build_engine(), DifyEngine)
    assert isinstance(
        ChannelConfig(engine="coze", coze_api_token="t", coze_bot_id="b").build_engine(),
        CozeEngine,
    )
    assert isinstance(
        ChannelConfig(engine="bailian", bailian_api_key="k", bailian_app_id="a").build_engine(),
        BailianEngine,
    )


# ---------------------------------------------------------------------------
# Runner wiring: the configured engine answers the channel
# ---------------------------------------------------------------------------


def _combined_client(calls):
    def handler(request):
        url = str(request.url)
        if "sendMessage" in url:
            return httpx.Response(
                200, json={"ok": True, "result": {"message_id": 7, "chat": {"id": 111}}}
            )
        if "getUpdates" in url:
            return httpx.Response(200, json={"ok": True, "result": []})
        if "chat-messages" in url:
            calls.append(request)
            return httpx.Response(
                200, json={"answer": "dify says hi", "conversation_id": "dify-conv-1"}
            )
        return httpx.Response(404, json={"ok": False})

    return httpx.Client(transport=httpx.MockTransport(handler))


def test_runner_answers_through_configured_dify_engine(tmp_path):
    calls: list = []
    profile = f"test-engines-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        telegram_bot_token="fake-token",
        allowlist=Allowlist.parse("telegram:111"),
        state_dir=str(tmp_path),
        engine="dify",
        dify_api_key="app-fake-key",
    )
    runner = GatewayRunner.open(profile=profile, config=config, http_client=_combined_client(calls))
    try:
        assert isinstance(runner.agent, ExternalEngineAgent)
        assert runner.agent.platform == "dify"
        outcome = runner.handle_event(_event())
        assert outcome.outcome == "answered"
        assert outcome.reply == "dify says hi"
        assert len(calls) == 1
    finally:
        runner.close()


def test_runner_with_engine_but_no_key_fails_at_open(tmp_path):
    profile = f"test-engines-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        telegram_bot_token="fake-token",
        state_dir=str(tmp_path),
        engine="dify",
    )
    with pytest.raises(ChannelConfigError, match="DIFY_API_KEY is not set"):
        GatewayRunner.open(profile=profile, config=config, http_client=_telegram_client())


def test_runner_default_still_uses_builtin_agent(tmp_path):
    profile = f"test-engines-{uuid.uuid4().hex[:8]}"
    config = ChannelConfig(
        telegram_bot_token="fake-token",
        state_dir=str(tmp_path),
    )
    runner = GatewayRunner.open(profile=profile, config=config, http_client=_telegram_client())
    try:
        assert isinstance(runner.agent, TaskRunnerAgent)
    finally:
        runner.close()


# ---------------------------------------------------------------------------
# CLI: the engine switch
# ---------------------------------------------------------------------------


def test_cli_parses_channels_run_engine():
    parsed = build_parser().parse_args(["channels", "run", "--engine", "coze"])
    assert parsed.command == "channels"
    assert parsed.channels_command == "run"
    assert parsed.engine == "coze"


def test_cli_engines_lists_setup_state(monkeypatch, capsys):
    for var in (
        "DIFY_API_KEY",
        "COZE_API_TOKEN",
        "COZE_BOT_ID",
        "BAILIAN_API_KEY",
        "BAILIAN_APP_ID",
    ):
        monkeypatch.delenv(var, raising=False)
    assert main(["channels", "engines"]) == 0
    out = capsys.readouterr().out
    assert "builtin: ready" in out
    assert "dify: missing DIFY_API_KEY" in out
    assert "coze: missing COZE_API_TOKEN and/or COZE_BOT_ID" in out
    assert "bailian: missing BAILIAN_API_KEY and/or BAILIAN_APP_ID" in out


def test_cli_engines_shows_ready_when_configured(monkeypatch, capsys):
    monkeypatch.setenv("DIFY_API_KEY", "app-fake-key")
    monkeypatch.setenv("COZE_API_TOKEN", "tok")
    monkeypatch.setenv("COZE_BOT_ID", "bot-1")
    assert main(["channels", "engines"]) == 0
    out = capsys.readouterr().out
    assert "dify: ready" in out
    assert "coze: ready" in out


def test_cli_run_missing_engine_key_fails_clearly(monkeypatch, capsys):
    monkeypatch.delenv("DIFY_API_KEY", raising=False)
    assert main(["channels", "run", "--engine", "dify"]) == 2
    err = capsys.readouterr().err
    assert "DIFY_API_KEY is not set" in err


class _FakeRunner:
    def __init__(self, *, telegram_enabled=True):
        from types import SimpleNamespace

        self.config = SimpleNamespace(telegram_enabled=telegram_enabled, engine="builtin")
        self.polls = 0
        self.closed = False

    def poll_telegram_once(self, *, timeout=30):
        self.polls += 1
        raise KeyboardInterrupt

    def close(self):
        self.closed = True


def test_cli_run_polls_until_interrupt(monkeypatch, capsys):
    fake = _FakeRunner()
    monkeypatch.setattr(channels_cli, "_open_runner", lambda profile, **kwargs: fake)
    assert main(["channels", "run"]) == 0
    assert fake.polls == 1
    assert fake.closed is True
    out = capsys.readouterr().out
    assert "engine: builtin" in out
    assert "channels gateway stopped." in out


def test_cli_run_without_telegram_fails_clearly(monkeypatch, capsys):
    fake = _FakeRunner(telegram_enabled=False)
    monkeypatch.setattr(channels_cli, "_open_runner", lambda profile, **kwargs: fake)
    assert main(["channels", "run"]) == 2
    assert fake.closed is True
    assert "TELEGRAM_BOT_TOKEN" in capsys.readouterr().err
