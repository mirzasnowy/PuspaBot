"""Tests for the routed turn-loop adapter (COW-08).

The turn loop's primary model tier goes to the user's routed
chat provider; the cheap/free tiers stay the existing OpenRouter
safety net. Every live call goes through an ``httpx.MockTransport``
that records the request, so the tests prove the request really
went to the routed provider's base URL with the routed model and
the vault key. No test touches the network or a real key: the only
key value used is obviously fake (``"test-byok-key-not-real"``)
and is only ever sent to the mock transport, never to a real host.
"""

import json

import httpx
import pytest

from puspa.db import connect as _db_connect
from puspa.pwa.capability_routing import create_schema, save_route
from puspa.pwa.model_settings import save_byok_key
from puspa.router.complete import OPENROUTER_API_BASE
from puspa.router.fallback import ChainEntry
from puspa.turns.loop import TurnLoop
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import make_routed_model_call

FAKE_KEY = "test-byok-key-not-real"


@pytest.fixture
def user(make_user):
    return make_user()


@pytest.fixture(autouse=True)
def _routing_table(test_dsn):
    create_schema(test_dsn)
    yield
    with _db_connect(test_dsn) as conn:
        conn.execute("TRUNCATE capability_routing")
        conn.commit()


def mock_chat_reply(text="hello from the model"):
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

    def __init__(self):
        self.requests = []
        super().__init__(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=mock_chat_reply())


def _entry(tier, provider="openrouter", model="openai/gpt-4o-mini"):
    return ChainEntry(
        tier=tier,
        provider=provider,
        model=model,
        family="openai",
        rp_per_1k_input=10,
        rp_per_1k_output=40,
        vision=True,
    )


def _route_chat_to_groq(user, test_dsn):
    save_byok_key(user["user_id"], "groq", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)


# -- the adapter ----------------------------------------------------------


def test_primary_tier_goes_to_the_routed_provider(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn)
    transport = CapturingTransport()
    model_call = make_routed_model_call(user_id=user["user_id"], dsn=test_dsn, transport=transport)
    reply = model_call(_entry("subscription"), [{"role": "user", "content": "hi"}], None)
    assert reply.text == "hello from the model"
    assert reply.provider == "groq"
    assert reply.model == "llama-3.3-70b-versatile"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    assert json.loads(request.content)["model"] == "llama-3.3-70b-versatile"


def test_fallback_tiers_keep_the_openrouter_safety_net(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn)
    transport = CapturingTransport()
    model_call = make_routed_model_call(
        user_id=user["user_id"],
        dsn=test_dsn,
        transport=transport,
        key_resolver=lambda provider: FAKE_KEY,
    )
    reply = model_call(_entry("cheap"), [{"role": "user", "content": "hi"}], None)
    assert reply.provider == "openrouter"
    assert reply.model == "openai/gpt-4o-mini"
    assert len(transport.requests) == 1
    assert str(transport.requests[0].url) == OPENROUTER_API_BASE + "/chat/completions"
    assert transport.requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY}"


def test_hosted_chat_uses_the_chain_entry(user, test_dsn):
    transport = CapturingTransport()
    model_call = make_routed_model_call(
        user_id=user["user_id"],
        dsn=test_dsn,
        transport=transport,
        key_resolver=lambda provider: FAKE_KEY,
    )
    reply = model_call(_entry("subscription"), [{"role": "user", "content": "hi"}], None)
    assert reply.provider == "openrouter"
    assert reply.model == "openai/gpt-4o-mini"
    assert str(transport.requests[0].url) == OPENROUTER_API_BASE + "/chat/completions"


def test_routed_provider_without_a_key_fails_loudly(user, test_dsn):
    save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)
    transport = CapturingTransport()
    model_call = make_routed_model_call(user_id=user["user_id"], dsn=test_dsn, transport=transport)
    with pytest.raises(KeyError, match="no key stored for provider 'groq'"):
        model_call(_entry("subscription"), [{"role": "user", "content": "hi"}], None)
    assert transport.requests == []


# -- the loop -------------------------------------------------------------


def test_loop_turn_uses_the_routed_chat_provider(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn)
    transport = CapturingTransport()
    loop = TurnLoop(dsn=test_dsn, model_transport=transport)
    result = loop.run_turn(
        [InternalMessage.user("hello")],
        user_id=user["user_id"],
    )
    assert result.status == "completed"
    assert result.text == "hello from the model"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
    assert json.loads(request.content)["model"] == "llama-3.3-70b-versatile"


def test_explicit_model_call_wins_over_routing(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn)
    transport = CapturingTransport()
    from puspa.turns.turn_api import ModelReply

    def fake_model_call(entry, wire_messages, tools):
        return ModelReply(text="from the fake", provider="fake", model="fake")

    loop = TurnLoop(dsn=test_dsn, model_transport=transport, model_call=fake_model_call)
    result = loop.run_turn(
        [InternalMessage.user("hello")],
        user_id=user["user_id"],
    )
    assert result.status == "completed"
    assert result.text == "from the fake"
    assert transport.requests == []


def test_loop_turn_without_a_user_never_touches_routing(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn)
    transport = CapturingTransport()
    loop = TurnLoop(dsn=test_dsn, model_transport=transport)
    result = loop.run_turn([InternalMessage.user("hello")])
    assert all("groq" not in str(request.url) for request in transport.requests)
    if transport.requests:
        assert all(
            str(request.url).startswith(OPENROUTER_API_BASE) for request in transport.requests
        )
    assert result.status in ("completed", "failed")
