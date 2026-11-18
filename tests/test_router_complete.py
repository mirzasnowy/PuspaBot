"""Tests for the provider-layer completion function.

All tests here are offline: every HTTP call goes through an
``httpx.MockTransport``, and every key comes from an injected
``key_resolver`` returning a clearly fake key, so no test touches
the network, the vault, or the live credential. The one live call
(a smoke test against OpenRouter) is not in this file: it needs a
real key and is run by hand, once, outside the suite.

The fake key used below is obviously fake (``"test-key-not-real"``)
and is only ever sent to the mock transport, never to a real host.
"""

import json

import httpx
import pytest

import puspa.router.complete as complete_mod
from puspa.retrieval.hipporag import HippoRAGRetriever
from puspa.retrieval.knowledge_graph import Entity, KnowledgeGraph, Triple
from puspa.retrieval.reranker import retrieve_with_decomposition
from puspa.router.complete import (
    CompletionError,
    CompletionResult,
    complete,
    complete_detailed,
    default_llm_complete,
    make_call_fn,
    resolve_llm_complete,
    resolve_role,
)
from puspa.router.fallback import build_chain, call_with_fallback

FAKE_KEY = "test-key-not-real"

#: Cheapest model per role, from the router catalog rates
#: (input + output Rp per 1k tokens): reader/searcher ->
#: meta-llama/llama-3.1-8b-instruct (1 + 2); planner/presenter ->
#: qwen/qwen3-32b (2 + 6, cheapest of the benchmark-passing set).
CHEAPEST_READER = "meta-llama/llama-3.1-8b-instruct"
CHEAPEST_PLANNER = "qwen/qwen3-32b"


def fake_key_resolver(provider):
    """A key resolver that hands out the fake key for any provider."""
    return FAKE_KEY


def mock_reply(text="hello from the model", *, input_tokens=10,
               output_tokens=4, model=CHEAPEST_READER):
    """A minimal OpenRouter-style chat-completions reply body."""
    return {
        "id": "chatcmpl-test",
        "object": "chat.completion",
        "choices": [{
            "index": 0,
            "message": {"role": "assistant", "content": text},
            "finish_reason": "stop",
        }],
        "model": model,
        "usage": {"prompt_tokens": input_tokens,
                  "completion_tokens": output_tokens,
                  "total_tokens": input_tokens + output_tokens},
    }


class CapturingTransport(httpx.MockTransport):
    """A mock transport that records every request it receives."""

    def __init__(self, handler):
        super().__init__(handler)
        self.requests = []

    def handle_request(self, request):
        self.requests.append(request)
        return super().handle_request(request)


def transport_for(reply_body=None, *, status=200, raw=None):
    """A transport answering every request with one fixed reply."""
    if raw is not None:
        content = raw
    else:
        content = json.dumps(reply_body if reply_body is not None
                             else mock_reply()).encode()

    def handler(request):
        return httpx.Response(status, content=content,
                              headers={"Content-Type": "application/json"})

    return CapturingTransport(handler)


# ---------------------------------------------------------------------------
# Role resolution
# ---------------------------------------------------------------------------

def test_role_aliases_map_to_catalog_roles():
    assert resolve_role("reader") == "pembaca"
    assert resolve_role("searcher") == "pencari"
    assert resolve_role("planner") == "perencana"
    assert resolve_role("presenter") == "penyaji"
    assert resolve_role("checker") == "pemeriksa"
    assert resolve_role("vision") == "vision"
    # Catalog names pass through unchanged.
    assert resolve_role("pembaca") == "pembaca"
    assert resolve_role("pemeriksa") == "pemeriksa"


def test_unknown_role_is_rejected():
    with pytest.raises(ValueError, match="unknown role"):
        resolve_role("oracle")
    with pytest.raises(ValueError, match="unknown role"):
        resolve_role("")


# ---------------------------------------------------------------------------
# Cheapest-model selection, prompt sent, text returned
# ---------------------------------------------------------------------------

def test_reader_role_calls_the_cheapest_reader_model():
    transport = transport_for()
    text = complete("What is 2 + 2?", role="reader",
                    transport=transport, key_resolver=fake_key_resolver)
    assert text == "hello from the model"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert request.url.host == "openrouter.ai"
    assert request.url.path == "/api/v1/chat/completions"
    body = json.loads(request.content)
    assert body["model"] == CHEAPEST_READER
    assert body["messages"] == [{"role": "user", "content": "What is 2 + 2?"}]
    # Judge/decomposer prompts want deterministic JSON: temperature 0.
    assert body["temperature"] == 0.0
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"


def test_planner_role_calls_the_cheapest_benchmark_passing_model():
    transport = transport_for(
        mock_reply(model=CHEAPEST_PLANNER))
    result = complete_detailed("Plan this.", role="planner",
                               transport=transport,
                               key_resolver=fake_key_resolver)
    assert result.model == CHEAPEST_PLANNER
    assert result.provider == "openrouter"
    body = json.loads(transport.requests[0].content)
    assert body["model"] == CHEAPEST_PLANNER


def test_explicit_model_overrides_the_role_choice():
    transport = transport_for(
        mock_reply(model="openai/gpt-4o-mini"))
    result = complete_detailed("Hi.", role="reader",
                               model="openai/gpt-4o-mini",
                               transport=transport,
                               key_resolver=fake_key_resolver)
    assert result.model == "openai/gpt-4o-mini"
    body = json.loads(transport.requests[0].content)
    assert body["model"] == "openai/gpt-4o-mini"


def test_detailed_result_carries_usage_and_rupiah_cost():
    # llama-3.1-8b: Rp1 per 1k input, Rp2 per 1k output.
    # (1000 * 1 + 500 * 2) // 1000 = 2.
    transport = transport_for(
        mock_reply(input_tokens=1000, output_tokens=500))
    result = complete_detailed("Hi.", transport=transport,
                               key_resolver=fake_key_resolver)
    assert isinstance(result, CompletionResult)
    assert result.text == "hello from the model"
    assert result.input_tokens == 1000
    assert result.output_tokens == 500
    assert result.cost_rp == 2


def test_uncatalogued_model_has_unknown_cost_not_a_guessed_one():
    transport = transport_for(
        mock_reply(model="someone/unknown-model"))
    result = complete_detailed("Hi.", model="someone/unknown-model",
                               transport=transport,
                               key_resolver=fake_key_resolver)
    assert result.model == "someone/unknown-model"
    assert result.cost_rp is None


def test_explicit_api_key_is_used_and_prompt_must_not_be_empty():
    transport = transport_for()
    assert complete("Hi.", api_key="explicit-fake-key",
                    transport=transport) == "hello from the model"
    assert (transport.requests[0].headers["Authorization"]
            == "Bearer explicit-fake-key")
    with pytest.raises(ValueError):
        complete("", transport=transport,
                 key_resolver=fake_key_resolver)
    with pytest.raises(ValueError):
        complete("Hi.", max_tokens=0, transport=transport,
                 key_resolver=fake_key_resolver)


# ---------------------------------------------------------------------------
# Error propagation: never a fake answer
# ---------------------------------------------------------------------------

@pytest.mark.parametrize("status", [400, 401, 402, 404, 429, 500, 503])
def test_http_error_statuses_raise_completion_error(status):
    transport = transport_for(
        {"error": {"message": "provider says no", "code": status}},
        status=status)
    with pytest.raises(CompletionError) as exc_info:
        complete("Hi.", transport=transport,
                 key_resolver=fake_key_resolver)
    assert exc_info.value.status == status
    assert "provider says no" in str(exc_info.value)


def test_transport_failure_raises_completion_error():
    def handler(request):
        raise httpx.ConnectError("connection refused", request=request)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CompletionError, match="ConnectError"):
        complete("Hi.", transport=transport,
                 key_resolver=fake_key_resolver)


def test_timeout_raises_completion_error():
    def handler(request):
        raise httpx.ReadTimeout("timed out", request=request)

    transport = httpx.MockTransport(handler)
    with pytest.raises(CompletionError, match="timed out"):
        complete("Hi.", transport=transport,
                 key_resolver=fake_key_resolver)


@pytest.mark.parametrize("raw", [
    b"not json at all",
    json.dumps({"choices": []}).encode(),
    json.dumps({"choices": [{"message": {}}]}).encode(),
    json.dumps({"choices": [{"message": {"content": ""}}]}).encode(),
    json.dumps({"choices": [{"message": {"content": "   "}}]}).encode(),
    json.dumps({"choices": [{"message": {"content": 42}}]}).encode(),
    json.dumps({"no_choices_here": True}).encode(),
])
def test_malformed_or_empty_replies_raise_completion_error(raw):
    transport = transport_for(raw=raw)
    with pytest.raises(CompletionError):
        complete("Hi.", transport=transport,
                 key_resolver=fake_key_resolver)


# ---------------------------------------------------------------------------
# No key: a clear error, not a fake answer
# ---------------------------------------------------------------------------

def test_missing_key_raises_key_error_not_a_fake_answer():
    def no_key(provider):
        raise KeyError(
            f"no API key for provider {provider!r}: none stored")

    with pytest.raises(KeyError, match="no API key"):
        complete("Hi.", transport=transport_for(),
                 key_resolver=no_key)


def test_empty_key_from_resolver_is_rejected():
    with pytest.raises(KeyError, match="no API key"):
        complete("Hi.", transport=transport_for(),
                 key_resolver=lambda provider: "")


# ---------------------------------------------------------------------------
# The factory, the seam resolver, and the fallback adapter
# ---------------------------------------------------------------------------

def test_default_llm_complete_builds_a_prompt_callable():
    transport = transport_for()
    llm_complete = default_llm_complete(
        role="reader", transport=transport,
        key_resolver=fake_key_resolver)
    assert callable(llm_complete)
    assert llm_complete("Say hi.") == "hello from the model"
    body = json.loads(transport.requests[0].content)
    assert body["model"] == CHEAPEST_READER
    assert body["messages"][0]["content"] == "Say hi."


def test_default_llm_complete_makes_no_call_until_used():
    transport = transport_for()
    default_llm_complete(transport=transport,
                         key_resolver=fake_key_resolver)
    assert transport.requests == []


def test_resolve_llm_complete_passthrough_and_default():
    assert resolve_llm_complete(None) is None
    fn = lambda prompt: "x"  # noqa: E731 - a stub callable
    assert resolve_llm_complete(fn) is fn
    resolved = resolve_llm_complete("default")
    assert callable(resolved)
    with pytest.raises(ValueError, match="llm_complete must be"):
        resolve_llm_complete("live")
    with pytest.raises(ValueError, match="llm_complete must be"):
        resolve_llm_complete(42)


def test_make_call_fn_adapts_complete_to_the_fallback_contract():
    transport = transport_for()
    call_fn = make_call_fn("Say hi.", transport=transport,
                           key_resolver=fake_key_resolver)
    chain = build_chain("pembaca")
    result = call_with_fallback(chain, call_fn)
    assert result.value == "hello from the model"
    assert result.total_input_tokens == 10
    assert result.total_output_tokens == 4
    # The first tier tried is the chain's subscription tier; the
    # model it called is that tier's model, not the role cheapest.
    assert result.tier_used.model == chain[0].model
    body = json.loads(transport.requests[0].content)
    assert body["model"] == chain[0].model


def test_make_call_fn_falls_through_on_a_retryable_failure():
    seen = []

    def handler(request):
        seen.append(json.loads(request.content)["model"])
        if len(seen) == 1:
            return httpx.Response(
                503, content=json.dumps(
                    {"error": {"message": "overloaded"}}).encode())
        return httpx.Response(
            200, content=json.dumps(mock_reply()).encode())

    transport = CapturingTransport(handler)
    call_fn = make_call_fn("Say hi.", transport=transport,
                           key_resolver=fake_key_resolver)
    chain = build_chain("pembaca")
    result = call_with_fallback(chain, call_fn)
    assert result.value == "hello from the model"
    assert result.tier_used.tier == "cheap"
    assert seen == [chain[0].model, chain[1].model]


# ---------------------------------------------------------------------------
# Wiring: the "default" opt-in reaches the judge and the decomposer
# ---------------------------------------------------------------------------

def _small_graph():
    graph = KnowledgeGraph()
    for name in ("samsung galaxy s24", "toko gadget murah", "promo"):
        graph.nodes[name] = Entity(name=name, source_ids={"hand"})
    graph.edges[("samsung galaxy s24", "sells", "toko gadget murah")] = Triple(
        subject="samsung galaxy s24", relation="sells",
        object="toko gadget murah", source_ids={"hand"})
    graph.edges[("toko gadget murah", "offers", "promo")] = Triple(
        subject="toko gadget murah", relation="offers",
        object="promo", source_ids={"hand"})
    graph.passages = {
        "p1": "Samsung Galaxy S24 costs Rp12.999.000.",
        "p2": "Toko Gadget Murah sells Samsung Galaxy S24.",
    }
    graph.passage_entities = {
        "p1": {"samsung galaxy s24"},
        "p2": {"toko gadget murah", "samsung galaxy s24"},
    }
    graph.doc_passages = {"hand": ["p1", "p2"]}
    return graph


def test_hipporag_default_opt_in_uses_the_live_complete(monkeypatch):
    """llm_complete="default" resolves to the router factory.

    The factory itself is patched to a stub so the test stays
    offline; what is proven is the wiring: the retriever asks
    the router for the completion function and the judge's
    decisions come from it.
    """
    calls = []

    def stub_complete(prompt):
        calls.append(prompt)
        return json.dumps({"decisions": [
            {"index": 0, "keep": True, "reason": "the seller"},
            {"index": 1, "keep": True, "reason": "the deal"},
        ]})

    monkeypatch.setattr(
        complete_mod, "default_llm_complete",
        lambda **kwargs: stub_complete)
    retriever = HippoRAGRetriever(
        _small_graph(), llm_complete="default")
    retriever.retrieve("Is the Galaxy S24 deal trustworthy?", top_k=5)
    assert len(calls) == 1
    assert all(d.source == "llm"
               for d in retriever.last_filter_decisions)


def test_hipporag_without_opt_in_stays_deterministic():
    retriever = HippoRAGRetriever(_small_graph())
    retriever.retrieve("Is the Galaxy S24 deal trustworthy?", top_k=5)
    assert all(d.source == "deterministic"
               for d in retriever.last_filter_decisions)


def test_decomposition_default_opt_in_uses_the_live_complete(monkeypatch):
    calls = []

    def stub_complete(prompt):
        calls.append(prompt)
        return json.dumps({"subquestions": [
            "Is the cheap Galaxy S24 deal trustworthy?",
            "What is the Galaxy S24 official store price?",
        ]})

    monkeypatch.setattr(
        complete_mod, "default_llm_complete",
        lambda **kwargs: stub_complete)
    seen = []

    def fake_retrieve(sub_query, top_k):
        seen.append(sub_query)
        return [{"passage_id": "p-" + str(len(seen)), "score": 0.9}]

    merged = retrieve_with_decomposition(
        fake_retrieve,
        "cheap Galaxy S24 deal trustworthy and compare with official "
        "store price",
        top_k=2, llm_complete="default")
    assert len(calls) == 1
    assert seen == [
        "Is the cheap Galaxy S24 deal trustworthy?",
        "What is the Galaxy S24 official store price?",
    ]
    assert all(r["decomposition_source"] == "llm" for r in merged)


def test_decomposition_without_opt_in_stays_regex():
    seen = []

    def fake_retrieve(sub_query, top_k):
        seen.append(sub_query)
        return [{"passage_id": "p-" + str(len(seen)), "score": 0.9}]

    merged = retrieve_with_decomposition(
        fake_retrieve,
        "cheap Galaxy S24 deal trustworthy and compare with official "
        "store price",
        top_k=2)
    assert all(r["decomposition_source"] == "regex" for r in merged)


def test_router_package_exports_do_not_shadow_the_module():
    # The function is named `complete` and lives in the module
    # `puspa.router.complete`; the package must not re-export the
    # bare name, or `puspa.router.complete` would become the
    # function instead of the module.
    import puspa.router

    assert puspa.router.complete is complete_mod
    assert puspa.router.complete_detailed is complete_detailed
    assert puspa.router.default_llm_complete is default_llm_complete
    assert complete_mod.complete is complete
