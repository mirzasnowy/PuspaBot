"""Tests for the provider-pool wiring into the turn loop (V2-40).

The pool itself (``puspa/router/pool.py``) is tested in
``tests/test_provider_pool.py``; these tests prove the pool is
actually USED by a live path: the turn loop's routed adapter
(:func:`puspa.turns.turn_api.make_routed_model_call`) sends a
subscription-tier call through the pool when the routed provider
has at least two accounts, so a 429 on one key fails over to the
next account instead of killing the turn. Every live call goes
through an ``httpx.MockTransport`` that 429s the first key and
answers the second, so no test touches the network or a real key:
the only key values used are obviously fake and are only ever
sent to the mock transport, never to a real host.
"""

import httpx
import pytest

from puspa.accounts.vault import put as vault_put
from puspa.db import connect as _db_connect
from puspa.pwa.capability_routing import create_schema, save_route
from puspa.pwa.model_settings import save_byok_key
from puspa.router.complete import OPENROUTER_API_BASE
from puspa.router.fallback import (
    ChainEntry,
    NonRetryableError,
    ProviderError,
    ProviderFailure,
    RateLimited,
)
from puspa.router.pool import reset_provider_pools
from puspa.turns.messages import InternalMessage
from puspa.turns.turn_api import call_model, make_routed_model_call

FAKE_KEY_1 = "test-byok-key-one-not-real"
FAKE_KEY_2 = "test-byok-key-two-not-real"


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


@pytest.fixture(autouse=True)
def _fresh_pools():
    reset_provider_pools()
    yield
    reset_provider_pools()


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


class FailoverTransport(httpx.MockTransport):
    """A mock transport that fails the listed keys, answers the rest.

    Only requests to the routed provider (Groq here) can fail: the
    cheap tier calls OpenRouter with the test's key resolver, and
    that key must never be failed just because it happens to equal
    one of the pool keys. Without the URL scope the cheap tier
    would 429 too, and the test would depend on which key the
    resolver returns.
    """

    def __init__(self, *, fail_keys=(), fail_status=429, fail_message="Rate limit exceeded"):
        self.requests = []
        self.fail_keys = set(fail_keys)
        self.fail_status = fail_status
        self.fail_message = fail_message
        super().__init__(self._handle)

    def _handle(self, request):
        self.requests.append(request)
        key = request.headers.get("Authorization", "").removeprefix("Bearer ").strip()
        if "groq" in str(request.url) and key in self.fail_keys:
            return httpx.Response(
                self.fail_status,
                json={"error": {"message": self.fail_message, "type": "rate_limit_error"}},
            )
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


def _route_chat_to_groq(user, test_dsn, *, second_key=False):
    save_byok_key(user["user_id"], "groq", FAKE_KEY_1, dsn=test_dsn)
    if second_key:
        vault_put(user["user_id"], "byok:groq:2", FAKE_KEY_2, dsn=test_dsn)
    save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)


# -- the pool wiring ------------------------------------------------------


def test_routed_pool_fails_over_to_second_key_on_429(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn, second_key=True)
    transport = FailoverTransport(fail_keys={FAKE_KEY_1})
    model_call = make_routed_model_call(user_id=user["user_id"], dsn=test_dsn, transport=transport)
    reply = model_call(_entry("subscription"), [{"role": "user", "content": "hi"}], None)
    assert reply.text == "hello from the model"
    assert reply.provider == "groq"
    assert reply.model == "llama-3.3-70b-versatile"
    assert len(transport.requests) == 2
    assert transport.requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY_1}"
    assert transport.requests[1].headers["Authorization"] == f"Bearer {FAKE_KEY_2}"
    assert all(
        str(request.url) == "https://api.groq.com/openai/v1/chat/completions"
        for request in transport.requests
    )


def test_single_key_never_touches_the_pool(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn)
    transport = FailoverTransport(fail_keys={FAKE_KEY_1})
    model_call = make_routed_model_call(user_id=user["user_id"], dsn=test_dsn, transport=transport)
    with pytest.raises(RateLimited):
        model_call(_entry("subscription"), [{"role": "user", "content": "hi"}], None)
    assert len(transport.requests) == 1
    assert transport.requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY_1}"


def test_non_retryable_failure_does_not_try_the_second_key(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn, second_key=True)
    transport = FailoverTransport(
        fail_keys={FAKE_KEY_1}, fail_status=400, fail_message="Bad request"
    )
    model_call = make_routed_model_call(user_id=user["user_id"], dsn=test_dsn, transport=transport)
    with pytest.raises(NonRetryableError):
        model_call(_entry("subscription"), [{"role": "user", "content": "hi"}], None)
    assert len(transport.requests) == 1
    assert transport.requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY_1}"


def test_all_pool_accounts_failing_is_retryable_for_the_next_tier(user, test_dsn, vault_master_key):
    _route_chat_to_groq(user, test_dsn, second_key=True)
    transport = FailoverTransport(fail_keys={FAKE_KEY_1, FAKE_KEY_2})
    model_call = make_routed_model_call(user_id=user["user_id"], dsn=test_dsn, transport=transport)
    with pytest.raises(ProviderFailure) as exc_info:
        model_call(_entry("subscription"), [{"role": "user", "content": "hi"}], None)
    assert exc_info.value.retryable
    assert isinstance(exc_info.value, ProviderError)
    assert len(transport.requests) == 2


def test_chain_falls_through_to_the_next_tier_when_the_pool_is_exhausted(
    user, test_dsn, vault_master_key
):
    _route_chat_to_groq(user, test_dsn, second_key=True)
    transport = FailoverTransport(fail_keys={FAKE_KEY_1, FAKE_KEY_2})
    # The cheap tier has no vault key: without a resolver it would
    # fall back to the ambient BYOK path, which has a key on a dev
    # machine (so the test passed locally) and none in CI (so it
    # failed there with AllTiersFailed). The resolver is the
    # established seam for the cheap tier (see test_turn_routing);
    # it keeps this test independent of ambient credentials.
    model_call = make_routed_model_call(
        user_id=user["user_id"],
        dsn=test_dsn,
        transport=transport,
        key_resolver=lambda provider: "test-cheap-key-not-real",
    )
    chain = [
        _entry("subscription", provider="groq", model="llama-3.3-70b-versatile"),
        _entry("cheap"),
    ]
    outcome = call_model(
        [InternalMessage.user("hi")],
        chain=chain,
        model_call=model_call,
    )
    assert outcome.reply.provider == "openrouter"
    assert outcome.reply.model == "openai/gpt-4o-mini"
    assert outcome.tier_used.tier == "cheap"
    assert len(transport.requests) == 3
    assert transport.requests[0].headers["Authorization"] == f"Bearer {FAKE_KEY_1}"
    assert transport.requests[1].headers["Authorization"] == f"Bearer {FAKE_KEY_2}"
    assert str(transport.requests[2].url) == OPENROUTER_API_BASE + "/chat/completions"
