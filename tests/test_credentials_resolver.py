"""Tests for the shared credential resolver (G22).

One resolver for every surface: (provider, model) maps to
(api mode, key, base URL) through the ProviderProfile
registry. Run in-memory: ``pytest tests/test_credentials_resolver.py --noconftest``.
"""

import pytest

from puspa.credentials.resolver import (
    PROVIDER_REGISTRY,
    ApiKeyProviderProfile,
    LocalProviderProfile,
    OAuthProviderProfile,
    ProviderRegistry,
    ProviderShadowError,
    ResolvedCredential,
    UnknownProviderError,
    default_registry,
    key_fingerprint,
    resolve,
    resolve_for_complete,
)


def fake_source(keys):
    """A source callable returning keys from a dict."""

    def _source(provider, model):
        return keys.get(provider)

    return _source


def test_registry_rejects_shadow_registration():
    registry = ProviderRegistry()
    registry.register(ApiKeyProviderProfile("openai", base_url="https://api.openai.com/v1"))
    with pytest.raises(ProviderShadowError):
        registry.register(ApiKeyProviderProfile("openai", base_url="https://api.openai.com/v1"))


def test_registry_rejects_non_profile():
    registry = ProviderRegistry()
    with pytest.raises(TypeError):
        registry.register(object())


def test_default_registry_has_known_providers():
    registry = default_registry()
    for name in (
        "openrouter",
        "openai",
        "anthropic",
        "ollama",
        "github_copilot",
        "openai_compatible",
    ):
        assert name in registry
    assert registry.get("ollama").api_mode == "local"
    assert registry.get("github_copilot").api_mode == "oauth"


def test_shared_registry_is_seeded():
    assert "openrouter" in PROVIDER_REGISTRY
    assert "openai" in PROVIDER_REGISTRY


def test_resolve_maps_pair_to_mode_key_base_url():
    resolved = resolve("openai", "gpt-4o-mini", source=fake_source({"openai": "key-openai-1"}))
    assert isinstance(resolved, ResolvedCredential)
    assert resolved.provider == "openai"
    assert resolved.model == "gpt-4o-mini"
    assert resolved.api_mode == "api_key"
    assert resolved.base_url == "https://api.openai.com/v1"
    assert resolved.api_key == "key-openai-1"
    assert resolved.authorization_header() == {"Authorization": "Bearer key-openai-1"}


def test_resolve_openrouter_catalog_model():
    resolved = resolve(
        "openrouter", "qwen/qwen3-32b", source=fake_source({"openrouter": "key-or-1"})
    )
    assert resolved.provider == "openrouter"
    assert resolved.model == "qwen/qwen3-32b"
    assert resolved.base_url == "https://openrouter.ai/api/v1"


def test_resolve_unknown_provider():
    with pytest.raises(UnknownProviderError):
        resolve("no_such_provider", "some-model", source=fake_source({}))


def test_resolve_model_not_served_by_profile():
    registry = ProviderRegistry()
    registry.register(
        ApiKeyProviderProfile(
            "openai", base_url="https://api.openai.com/v1", models=("gpt-4o-mini",)
        )
    )
    with pytest.raises(ValueError):
        registry.resolve("openai", "gpt-5", source=fake_source({"openai": "k"}))


def test_resolve_missing_key_raises_key_error():
    with pytest.raises(KeyError):
        resolve("openai", "gpt-4o-mini", source=fake_source({}))


def test_resolve_empty_model_rejected():
    with pytest.raises(ValueError):
        resolve("openai", "  ", source=fake_source({"openai": "k"}))


def test_local_provider_needs_no_key():
    resolved = resolve("ollama", "llama3.1", source=fake_source({}))
    assert resolved.api_mode == "local"
    assert resolved.api_key is None
    assert resolved.authorization_header() == {}
    assert resolved.base_url == "http://localhost:11434/v1"


def test_openai_compatible_needs_explicit_base_url():
    with pytest.raises(ValueError):
        resolve("openai_compatible", "my-model", source=fake_source({"openai_compatible": "k"}))
    resolved = resolve(
        "openai_compatible",
        "my-model",
        source=fake_source({"openai_compatible": "k"}),
        base_url="https://example.invalid/v1",
    )
    assert resolved.base_url == "https://example.invalid/v1"


def test_resolved_credential_repr_never_shows_key():
    resolved = resolve(
        "openai", "gpt-4o-mini", source=fake_source({"openai": "super-secret-key-value"})
    )
    assert "super-secret-key-value" not in repr(resolved)
    assert resolved.key_id == key_fingerprint("super-secret-key-value")
    assert len(resolved.key_id) == 12


def test_oauth_profile_resolves_token_via_source():
    registry = ProviderRegistry()
    registry.register(
        OAuthProviderProfile(
            "github_copilot",
            base_url="https://api.githubcopilot.com",
            token_url="https://github.com/login/oauth/access_token",
        )
    )
    resolved = registry.resolve(
        "github_copilot", "gpt-4o", source=fake_source({"github_copilot": "token-abc"})
    )
    assert resolved.api_mode == "oauth"
    assert resolved.api_key == "token-abc"


def test_resolve_for_complete_explicit_model():
    resolved = resolve_for_complete(
        model="gpt-4o-mini", provider="openai", source=fake_source({"openai": "key-openai-1"})
    )
    assert (resolved.provider, resolved.model) == ("openai", "gpt-4o-mini")


def test_resolve_for_complete_role_uses_router_catalog():
    # "pembaca"/"reader" picks the cheapest catalog model
    # (llama-3.1-8b-instruct on openrouter).
    resolved = resolve_for_complete(role="reader", source=fake_source({"openrouter": "key-or-1"}))
    assert resolved.provider == "openrouter"
    assert resolved.model == "meta-llama/llama-3.1-8b-instruct"
    assert resolved.api_key == "key-or-1"


def test_resolve_for_complete_needs_role_or_model():
    with pytest.raises(ValueError):
        resolve_for_complete(source=fake_source({}))


def test_resolve_for_complete_unknown_role():
    with pytest.raises(ValueError):
        resolve_for_complete(role="nope", source=fake_source({}))


def test_local_profile_class_direct():
    profile = LocalProviderProfile()
    assert profile.supports_model("anything")
    assert profile.resolve_key(None) is None
    assert profile.base_url_for("m") == "http://localhost:11434/v1"
