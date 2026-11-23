"""Tests for per-capability model routing (COW-08).

Persistence: the route lives in Postgres (table
``capability_routing``), one row per (user, capability); the
key lives only in the existing per-user vault (``byok:<provider>``,
envelope-encrypted). Router wiring: every live call goes through
an ``httpx.MockTransport`` that records the request, so the tests
prove the request really went to the routed provider's base URL
with the routed model and key — the route is not just stored.

No test touches the network or a real key: the only key values
used are obviously fake (``"test-byok-key-not-real"``) and are
only ever sent to the mock transport, never to a real host.
"""

import httpx
from puspa.db import connect as _db_connect
import pytest

from puspa.memory.embeddings import EmbeddingUnavailable, OpenAICompatibleEmbedding
from puspa.pwa.capability_routing import (
    CAPABILITIES,
    CAPABILITY_PROVIDERS,
    complete_vision_for_user,
    create_schema,
    generate_image,
    get_route,
    get_routing,
    has_routing_key,
    reset_route,
    resolve_capability,
    resolve_chat_kwargs,
    resolve_embedder,
    save_route,
    save_route_from_payload,
    save_routing_key,
)
from puspa.pwa.model_settings import save_byok_key, save_selection
from puspa.pwa.voice_settings import resolve_asr, resolve_tts, save_settings, save_voice_key
from puspa.router.complete import OPENROUTER_API_BASE

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

    def __init__(self, handler=None):
        self.requests = []
        super().__init__(handler or self._handle)

    def _handle(self, request):
        self.requests.append(request)
        return httpx.Response(200, json=mock_chat_reply())


# -- defaults ------------------------------------------------------------


def test_defaults_for_a_fresh_user(user, test_dsn):
    routing = get_routing(user["user_id"], dsn=test_dsn)
    assert set(routing) == set(CAPABILITIES)
    assert routing["chat"].provider == "hosted"
    assert routing["chat"].is_default is True
    assert routing["vision"].provider == "hosted"
    assert routing["vision"].is_default is True
    assert routing["image"].provider is None
    assert routing["speech"].provider is None
    assert routing["embeddings"].provider == "hosted"
    for route in routing.values():
        assert route.has_key is False
        assert "api_key" not in route.to_public_dict()
        assert "key" not in route.to_public_dict()


def test_chat_default_follows_model_selection(user, test_dsn, vault_master_key):
    save_selection(
        user["user_id"],
        {"source": "byok", "provider": "openrouter", "model": "openai/gpt-4o-mini"},
        dsn=test_dsn,
    )
    route = get_route(user["user_id"], "chat", dsn=test_dsn)
    assert route.provider == "openrouter"
    assert route.model == "openai/gpt-4o-mini"
    assert route.is_default is True
    save_selection(user["user_id"], {"source": "local"}, dsn=test_dsn)
    route = get_route(user["user_id"], "chat", dsn=test_dsn)
    assert route.provider == "ollama"
    assert route.model == "llama3.1"


def test_unknown_capability_refused(user, test_dsn):
    with pytest.raises(ValueError, match="unknown capability"):
        get_route(user["user_id"], "translate", dsn=test_dsn)
    with pytest.raises(ValueError, match="unknown capability"):
        save_route(user["user_id"], "translate", "openai", "gpt-4o", dsn=test_dsn)


# -- saving, swapping, resetting -----------------------------------------


def test_save_and_read_one_route(user, test_dsn):
    saved = save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)
    assert saved.provider == "groq"
    assert saved.model == "llama-3.3-70b-versatile"
    assert saved.is_default is False
    again = get_route(user["user_id"], "chat", dsn=test_dsn)
    assert again.provider == "groq"
    assert again.model == "llama-3.3-70b-versatile"


def test_swapping_one_capability_leaves_the_others_alone(user, test_dsn):
    save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)
    save_route(user["user_id"], "vision", "openai", "gpt-4o", dsn=test_dsn)
    save_route(user["user_id"], "image", "openai", "dall-e-3", dsn=test_dsn)
    save_route(user["user_id"], "speech", "groq", "whisper-large-v3-turbo", dsn=test_dsn)
    save_route(user["user_id"], "embeddings", "openai", "text-embedding-3-small", dsn=test_dsn)
    before = get_routing(user["user_id"], dsn=test_dsn)
    save_route(user["user_id"], "chat", "deepseek", "deepseek-chat", dsn=test_dsn)
    after = get_routing(user["user_id"], dsn=test_dsn)
    assert after["chat"].provider == "deepseek"
    assert after["chat"].model == "deepseek-chat"
    for capability in ("vision", "image", "speech", "embeddings"):
        assert after[capability].to_public_dict() == before[capability].to_public_dict()


def test_reset_returns_the_default_and_leaves_the_others_alone(user, test_dsn):
    save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)
    save_route(user["user_id"], "vision", "openai", "gpt-4o", dsn=test_dsn)
    reset = reset_route(user["user_id"], "chat", dsn=test_dsn)
    assert reset.provider == "hosted"
    assert reset.is_default is True
    assert get_route(user["user_id"], "vision", dsn=test_dsn).provider == "openai"
    # Resetting again is idempotent.
    assert reset_route(user["user_id"], "chat", dsn=test_dsn).provider == "hosted"


def test_hosted_drops_model_and_base_url(user, test_dsn):
    saved = save_route(
        user["user_id"],
        "chat",
        "hosted",
        model="gpt-4o",
        base_url="https://example.com/v1",
        dsn=test_dsn,
    )
    assert saved.provider == "hosted"
    assert saved.model is None
    assert saved.base_url is None


def test_ollama_fills_local_defaults(user, test_dsn):
    saved = save_route(user["user_id"], "chat", "ollama", dsn=test_dsn)
    assert saved.provider == "ollama"
    assert saved.model == "llama3.1"
    assert saved.base_url == "http://localhost:11434/v1"


# -- validation ----------------------------------------------------------


@pytest.mark.parametrize(
    "capability,provider",
    [
        ("vision", "deepseek"),
        ("image", "groq"),
        ("image", "deepseek"),
        ("image", "mistral"),
        ("image", "hosted"),
        ("image", "ollama"),
        ("speech", "ollama"),
        ("speech", "mistral"),
        ("speech", "hosted"),
        ("embeddings", "groq"),
        ("embeddings", "deepseek"),
        ("embeddings", "xai"),
    ],
)
def test_provider_that_cannot_serve_is_refused(user, test_dsn, capability, provider):
    with pytest.raises(ValueError, match="cannot serve capability"):
        save_route(user["user_id"], capability, provider, "some-model", dsn=test_dsn)
    # The refusal changed nothing: the default still applies.
    assert get_route(user["user_id"], capability, dsn=test_dsn).is_default is True


def test_unknown_provider_refused(user, test_dsn):
    with pytest.raises(ValueError, match="cannot serve capability"):
        save_route(user["user_id"], "chat", "not-a-provider", "some-model", dsn=test_dsn)


def test_model_required_for_a_named_provider(user, test_dsn):
    with pytest.raises(ValueError, match="needs a model"):
        save_route(user["user_id"], "chat", "openai", dsn=test_dsn)
    with pytest.raises(ValueError, match="needs a model"):
        save_route(user["user_id"], "image", "openai", dsn=test_dsn)


def test_bad_base_url_refused(user, test_dsn):
    with pytest.raises(ValueError, match="must use http or https"):
        save_route(user["user_id"], "chat", "openai", "gpt-4o", base_url="ftp://x/v1", dsn=test_dsn)
    with pytest.raises(ValueError, match="must not contain credentials"):
        save_route(
            user["user_id"],
            "chat",
            "openai",
            "gpt-4o",
            base_url="https://user:pass@example.com/v1",
            dsn=test_dsn,
        )
    assert get_route(user["user_id"], "chat", dsn=test_dsn).is_default is True


def test_key_field_in_payload_refused(user, test_dsn):
    with pytest.raises(ValueError, match="outside the vault"):
        save_route_from_payload(
            user["user_id"],
            {"capability": "chat", "provider": "openai", "model": "gpt-4o", "api_key": FAKE_KEY},
            dsn=test_dsn,
        )
    assert get_route(user["user_id"], "chat", dsn=test_dsn).is_default is True


def test_every_capability_lists_its_providers():
    for capability in CAPABILITIES:
        assert CAPABILITY_PROVIDERS[capability], capability


# -- keys -----------------------------------------------------------------


def test_routing_key_round_trip_never_returned(user, test_dsn, vault_master_key):
    save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)
    assert has_routing_key(user["user_id"], "groq", dsn=test_dsn) is False
    save_routing_key(user["user_id"], "groq", FAKE_KEY, dsn=test_dsn)
    assert has_routing_key(user["user_id"], "groq", dsn=test_dsn) is True
    route = get_route(user["user_id"], "chat", dsn=test_dsn)
    assert route.has_key is True
    assert FAKE_KEY not in repr(route.to_public_dict())
    # One key serves every capability routed to that provider.
    save_route(user["user_id"], "speech", "groq", "whisper-large-v3-turbo", dsn=test_dsn)
    assert get_route(user["user_id"], "speech", dsn=test_dsn).has_key is True


def test_empty_key_refused(user, test_dsn, vault_master_key):
    with pytest.raises(ValueError, match="must be a non-empty string"):
        save_routing_key(user["user_id"], "groq", "   ", dsn=test_dsn)


# -- resolution -----------------------------------------------------------


def test_resolve_chat_hosted_is_empty_kwargs(user, test_dsn):
    assert resolve_chat_kwargs(user["user_id"], dsn=test_dsn) == {}
    resolved = resolve_capability(user["user_id"], "chat", dsn=test_dsn)
    assert resolved == {
        "provider": "hosted",
        "model": None,
        "base_url": None,
        "api_key": None,
    }


def test_resolve_chat_uses_the_routed_provider_and_vault_key(user, test_dsn, vault_master_key):
    save_byok_key(user["user_id"], "groq", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)
    kwargs = resolve_chat_kwargs(user["user_id"], dsn=test_dsn)
    assert kwargs["provider"] == "groq"
    assert kwargs["model"] == "llama-3.3-70b-versatile"
    assert kwargs["base_url"] == "https://api.groq.com/openai/v1"
    assert kwargs["api_key"] == FAKE_KEY


def test_resolve_raises_without_a_key(user, test_dsn):
    save_route(user["user_id"], "chat", "groq", "llama-3.3-70b-versatile", dsn=test_dsn)
    with pytest.raises(KeyError, match="no key stored for provider 'groq'"):
        resolve_capability(user["user_id"], "chat", dsn=test_dsn)


def test_resolve_image_without_a_route_refuses(user, test_dsn):
    with pytest.raises(ValueError, match="has no provider yet"):
        resolve_capability(user["user_id"], "image", dsn=test_dsn)


def test_vision_call_goes_to_the_routed_provider(user, test_dsn, vault_master_key):
    save_byok_key(user["user_id"], "openai", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "vision", "openai", "gpt-4o", dsn=test_dsn)
    transport = CapturingTransport()
    result = complete_vision_for_user(
        [{"role": "user", "content": "what is in this picture?"}],
        user_id=user["user_id"],
        dsn=test_dsn,
        transport=transport,
    )
    assert result.text == "hello from the model"
    assert result.provider == "openai"
    assert result.model == "gpt-4o"
    assert len(transport.requests) == 1
    request = transport.requests[0]
    assert str(request.url) == "https://api.openai.com/v1/chat/completions"
    assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
    import json

    body = json.loads(request.content)
    assert body["model"] == "gpt-4o"


def test_image_call_goes_to_the_routed_provider(user, test_dsn, vault_master_key):
    save_byok_key(user["user_id"], "openai", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "image", "openai", "dall-e-3", dsn=test_dsn)

    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/images/generations"
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        import json

        body = json.loads(request.content)
        assert body["model"] == "dall-e-3"
        assert body["prompt"] == "a red bicycle"
        return httpx.Response(
            200,
            json={"data": [{"url": "https://example.com/img.png", "revised_prompt": "a red bike"}]},
        )

    transport = httpx.MockTransport(handler)
    image = generate_image(
        "a red bicycle", user_id=user["user_id"], dsn=test_dsn, transport=transport
    )
    assert image.url == "https://example.com/img.png"
    assert image.provider == "openai"
    assert image.model == "dall-e-3"


def test_image_without_a_route_refuses(user, test_dsn):
    with pytest.raises(ValueError, match="has no provider yet"):
        generate_image("a red bicycle", user_id=user["user_id"], dsn=test_dsn)


def test_image_http_failure_is_loud(user, test_dsn, vault_master_key):
    save_byok_key(user["user_id"], "openai", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "image", "openai", "dall-e-3", dsn=test_dsn)
    transport = httpx.MockTransport(
        lambda request: httpx.Response(401, json={"error": {"message": "bad key"}})
    )
    with pytest.raises(RuntimeError, match="rejected the image request"):
        generate_image("a red bicycle", user_id=user["user_id"], dsn=test_dsn, transport=transport)


# -- embeddings -----------------------------------------------------------


def test_resolve_embedder_default_is_local(user, test_dsn):
    embedder = resolve_embedder(user["user_id"], dsn=test_dsn)
    assert embedder.dim == 384
    assert len(embedder.embed("hello world")) == 384


def test_resolve_embedder_routed_is_remote(user, test_dsn, vault_master_key):
    save_byok_key(user["user_id"], "openai", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "embeddings", "openai", "text-embedding-3-small", dsn=test_dsn)

    def handler(request):
        assert str(request.url) == "https://api.openai.com/v1/embeddings"
        assert request.headers["Authorization"] == f"Bearer {FAKE_KEY}"
        return httpx.Response(200, json={"data": [{"embedding": [0.25] * 1536, "index": 0}]})

    transport = httpx.MockTransport(handler)
    client = httpx.Client(transport=transport)
    embedder = resolve_embedder(user["user_id"], dsn=test_dsn, http_client=client)
    assert isinstance(embedder, OpenAICompatibleEmbedding)
    assert embedder.dim == 1536
    assert embedder.embedder_id == "remote:openai:text-embedding-3-small"
    vec = embedder.embed("hello")
    assert len(vec) == 1536
    assert abs(sum(v * v for v in vec) - 1.0) < 1e-6


def test_remote_embedder_dim_unknown_until_first_embed(user, test_dsn):
    def handler(request):
        return httpx.Response(200, json={"data": [{"embedding": [1.0, 2.0, 3.0], "index": 0}]})

    embedder = OpenAICompatibleEmbedding(
        "https://api.example.com/v1",
        FAKE_KEY,
        "some-unknown-model",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(EmbeddingUnavailable, match="no known dimension yet"):
        _ = embedder.dim
    assert len(embedder.embed("hello")) == 3
    assert embedder.dim == 3


def test_remote_embedder_dimension_mismatch_raises():
    def handler(request):
        return httpx.Response(200, json={"data": [{"embedding": [1.0, 2.0], "index": 0}]})

    embedder = OpenAICompatibleEmbedding(
        "https://api.example.com/v1",
        FAKE_KEY,
        "text-embedding-3-small",
        http_client=httpx.Client(transport=httpx.MockTransport(handler)),
    )
    with pytest.raises(EmbeddingUnavailable, match="returned dim 2, expected 1536"):
        embedder.embed("hello")


def test_remote_embedder_http_failure_is_loud():
    embedder = OpenAICompatibleEmbedding(
        "https://api.example.com/v1",
        FAKE_KEY,
        "text-embedding-3-small",
        http_client=httpx.Client(
            transport=httpx.MockTransport(lambda request: httpx.Response(500, json={}))
        ),
    )
    with pytest.raises(EmbeddingUnavailable, match="HTTP 500"):
        embedder.embed("hello")


# -- speech route drives the voice engines --------------------------------


def test_speech_route_overrides_asr_endpoint_and_model(user, test_dsn, vault_master_key):
    save_settings(
        user["user_id"],
        {"asr_provider": "openai-compatible", "tts_provider": "edge"},
        dsn=test_dsn,
    )
    save_voice_key(user["user_id"], "asr", "openai-compatible", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "speech", "groq", "whisper-large-v3-turbo", dsn=test_dsn)
    transcriber = resolve_asr(user["user_id"], dsn=test_dsn)
    assert transcriber.base_url == "https://api.groq.com/openai/v1"
    assert transcriber.model == "whisper-large-v3-turbo"


def test_speech_route_overrides_tts_endpoint_and_model(user, test_dsn, vault_master_key):
    save_settings(
        user["user_id"],
        {"asr_provider": "none", "tts_provider": "openai-compatible"},
        dsn=test_dsn,
    )
    save_voice_key(user["user_id"], "tts", "openai-compatible", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "speech", "deepseek", "deepseek-tts", dsn=test_dsn)
    engine = resolve_tts(user["user_id"], dsn=test_dsn)
    assert engine.base_url == "https://api.deepseek.com/v1"
    assert engine.model == "deepseek-tts"


def test_voice_engines_unchanged_without_a_speech_route(user, test_dsn, vault_master_key):
    save_settings(
        user["user_id"],
        {"asr_provider": "openai-compatible", "tts_provider": "edge"},
        dsn=test_dsn,
    )
    save_voice_key(user["user_id"], "asr", "openai-compatible", FAKE_KEY, dsn=test_dsn)
    transcriber = resolve_asr(user["user_id"], dsn=test_dsn)
    assert transcriber.base_url == "https://api.openai.com/v1"
    assert transcriber.model == "whisper-1"


# -- the OpenRouter default still works ------------------------------------


def test_chat_call_goes_to_openrouter_when_routed_there(user, test_dsn, vault_master_key):
    from puspa.pwa.capability_routing import resolve_chat_kwargs
    from puspa.router.complete import complete_detailed

    save_byok_key(user["user_id"], "openrouter", FAKE_KEY, dsn=test_dsn)
    save_route(user["user_id"], "chat", "openrouter", "openai/gpt-4o-mini", dsn=test_dsn)
    kwargs = resolve_chat_kwargs(user["user_id"], dsn=test_dsn)
    assert kwargs["provider"] == "openrouter"
    assert kwargs["base_url"] == OPENROUTER_API_BASE
    transport = CapturingTransport()
    result = complete_detailed(
        "hi", user_id=user["user_id"], dsn=test_dsn, transport=transport, **kwargs
    )
    assert result.text == "hello from the model"
    assert str(transport.requests[0].url) == OPENROUTER_API_BASE + "/chat/completions"
