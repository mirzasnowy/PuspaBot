"""Shared runtime credential resolver (G22).

One resolver used by every surface (PWA, gateway, cron, harness):
no provider-selection logic is duplicated per surface. A
``(provider, model)`` pair maps to ``(api mode, key, base URL)``
through a :class:`ProviderProfile` registry.

Pattern studied, then rewritten as Puspa's own code (see the
G22 section of the repo-comparison report): hermes-agent keeps
one runtime resolver with a ``ProviderProfile`` registry plus
ABC (``providers/base.py``) and ``PROVIDER_REGISTRY`` plus
credential resolution in ``hermes_cli/auth.py``. No hermes-agent
code is copied.

Where it sits
-------------
Today ``puspa.router.complete.complete()`` resolves its key
itself: it picks a model from the router catalog
(``_resolve_choice``) and then calls ``resolve_api_key``, which
delegates to the BYOK path (``demo.byok_provider.resolve_key``:
vault ``byok:<provider>`` first, then the provider env var).
That works, but every new surface that needs a key repeats the
same choice. This module is the single replacement for that
logic:

- :func:`resolve_for_complete` is the drop-in call site for
  ``complete()``: give it a role (or an explicit model) and it
  returns a :class:`ResolvedCredential` carrying provider, model,
  api mode, key, and base URL. ``complete.py`` is NOT changed by
  this item; the wiring (calling this function from
  ``complete_detailed`` instead of ``_resolve_choice`` plus
  ``resolve_api_key``) is a follow-up for the coordinator.
- :class:`ProviderRegistry` / :data:`PROVIDER_REGISTRY` hold the
  profiles. Registration is explicit and shadowing is rejected
  (first registration wins), the same rule as the memory
  provider registry in ``puspa.memory.provider``.
- Key sources are injected as callables
  (``source(provider, model) -> str | None``). The default source
  is the existing BYOK path, so no key lookup is duplicated
  here; a profile-scoped source comes from
  ``puspa.credentials.scope`` instead.

A resolved credential never leaks its key: ``__repr__`` shows
only a fingerprint, and :meth:`ResolvedCredential.authorization_header`
is the only place the raw value is handed to an HTTP call.
"""

from __future__ import annotations

import hashlib
from abc import ABC, abstractmethod
from dataclasses import dataclass

__all__ = [
    "PROVIDER_REGISTRY",
    "ApiKeyProviderProfile",
    "CredentialSource",
    "LocalProviderProfile",
    "OAuthProviderProfile",
    "ProviderProfile",
    "ProviderRegistry",
    "ProviderShadowError",
    "ResolvedCredential",
    "UnknownProviderError",
    "byok_source",
    "default_registry",
    "resolve",
    "resolve_for_complete",
]

#: API modes a profile can declare.
API_MODES = ("api_key", "oauth", "local", "anonymous")

#: Default base URLs for the known API-key providers.
DEFAULT_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "anthropic": "https://api.anthropic.com/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta",
    "groq": "https://api.groq.com/openai/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "mistral": "https://api.mistral.ai/v1",
    "together": "https://api.together.xyz/v1",
    "xai": "https://api.x.ai/v1",
    "ollama": "http://localhost:11434/v1",
    "openai_compatible": "",
}

#: Environment variable holding the API key, per provider.
ENV_KEY_NAMES = {
    "openrouter": "OPENROUTER_API_KEY",
    "openai": "OPENAI_API_KEY",
    "anthropic": "ANTHROPIC_API_KEY",
    "gemini": "GEMINI_API_KEY",
    "groq": "GROQ_API_KEY",
    "deepseek": "DEEPSEEK_API_KEY",
    "mistral": "MISTRAL_API_KEY",
    "together": "TOGETHER_API_KEY",
    "xai": "XAI_API_KEY",
}


class ProviderShadowError(Exception):
    """A provider name was registered twice; shadowing is rejected."""


class UnknownProviderError(Exception):
    """The requested provider is not in the registry."""


def key_fingerprint(key: str) -> str:
    """Short, non-reversible fingerprint of a key (first 12 hex chars).

    Safe to show in logs and status reports: it identifies a key
    inside a pool without ever revealing it.
    """
    return hashlib.sha256(str(key).encode()).hexdigest()[:12]


class ProviderProfile(ABC):
    """Abstract provider profile: one provider, one auth mode.

    ``name`` is the registry name (``"openrouter"``, ``"openai"``,
    ...). ``api_mode`` is one of :data:`API_MODES`. ``base_url``
    is the chat-completions base URL (``""`` when the caller must
    supply one, e.g. ``openai_compatible``). ``env_key_name`` is
    the env var that holds the key, or None when the provider
    takes no key. ``models`` lists the model ids this profile
    serves; an empty tuple means "any model id" (the profile does
    not restrict models).
    """

    name: str = "base"
    api_mode: str = "api_key"
    base_url: str = ""
    env_key_name: str | None = None
    models: tuple = ()

    @abstractmethod
    def supports_model(self, model: str) -> bool:
        """True when this profile serves ``model``."""

    @abstractmethod
    def resolve_key(self, source, *, user_id=None) -> str | None:
        """Resolve the key for this provider through ``source``.

        ``source`` is a ``(provider, model) -> str | None``
        callable (or None, meaning "no source"). Returns None
        when the provider needs no key (local / anonymous).
        Raises ``KeyError`` when a key is required but none is
        found.
        """

    def base_url_for(self, model: str, *, base_url: str | None = None) -> str:
        """Base URL for ``model``: explicit override wins."""
        if base_url:
            return str(base_url).rstrip("/")
        if not self.base_url:
            raise ValueError(
                f"provider {self.name!r} needs an explicit base_url "
                "(no default base URL is known for it)"
            )
        return self.base_url.rstrip("/")


class ApiKeyProviderProfile(ProviderProfile):
    """An API-key provider (OpenRouter, OpenAI, Anthropic, ...)."""

    api_mode = "api_key"

    def __init__(self, name, *, base_url="", env_key_name=None, models=()):
        self.name = name
        self.base_url = base_url
        self.env_key_name = env_key_name
        self.models = tuple(models)

    def supports_model(self, model: str) -> bool:
        return not self.models or model in self.models

    def resolve_key(self, source, *, user_id=None) -> str | None:
        key = source(self.name, None) if source is not None else None
        if not key or not str(key).strip():
            raise KeyError(
                f"no API key for provider {self.name!r}: store it in "
                f"the vault as 'byok:{self.name}' or set "
                f"{self.env_key_name or '(no env var known)'}"
            )
        return str(key)


class OAuthProviderProfile(ProviderProfile):
    """An OAuth (subscription) provider: key comes from a token set.

    The access token is resolved through the same ``source``
    callable, which for OAuth providers is expected to return a
    fresh access token (see ``puspa.router.oauth.TokenRefresher``
    for refresh, and ``puspa.credentials.auth`` for the login
    flows that first obtain a token set).
    """

    api_mode = "oauth"

    def __init__(self, name, *, base_url="", token_url=None, client_id=None, models=()):
        self.name = name
        self.base_url = base_url
        self.token_url = token_url
        self.client_id = client_id
        self.env_key_name = None
        self.models = tuple(models)

    def supports_model(self, model: str) -> bool:
        return not self.models or model in self.models

    def resolve_key(self, source, *, user_id=None) -> str | None:
        key = source(self.name, None) if source is not None else None
        if not key or not str(key).strip():
            raise KeyError(
                f"no OAuth access token for provider {self.name!r}: "
                "log in first (device flow / authorization code), "
                "then retry"
            )
        return str(key)


class LocalProviderProfile(ProviderProfile):
    """A local provider (Ollama): no key, fixed local base URL."""

    api_mode = "local"

    def __init__(self, name="ollama", *, base_url="http://localhost:11434/v1", models=()):
        self.name = name
        self.base_url = base_url
        self.env_key_name = None
        self.models = tuple(models)

    def supports_model(self, model: str) -> bool:
        return not self.models or model in self.models

    def resolve_key(self, source, *, user_id=None) -> str | None:
        return None


@dataclass(frozen=True)
class ResolvedCredential:
    """One resolved ``(provider, model)``: mode, key, base URL.

    ``api_key`` is None for local / anonymous providers. The
    repr never shows the key, only its fingerprint.
    """

    provider: str
    model: str
    api_mode: str
    base_url: str
    api_key: str | None = None

    def __repr__(self) -> str:  # pragma: no cover - formatting only
        fingerprint = key_fingerprint(self.api_key) if self.api_key else None
        return (
            f"ResolvedCredential(provider={self.provider!r}, "
            f"model={self.model!r}, api_mode={self.api_mode!r}, "
            f"base_url={self.base_url!r}, "
            f"api_key_fingerprint={fingerprint!r})"
        )

    @property
    def key_id(self) -> str | None:
        """Fingerprint of the key, or None when there is no key."""
        return key_fingerprint(self.api_key) if self.api_key else None

    def authorization_header(self) -> dict:
        """Bearer header for this credential, or {} when keyless."""
        if not self.api_key:
            return {}
        return {"Authorization": f"Bearer {self.api_key}"}


from typing import Protocol, runtime_checkable


@runtime_checkable
class CredentialSource(Protocol):
    """A key source: ``(provider, model) -> key string or None``."""

    def __call__(self, provider: str, model: str | None) -> str | None: ...


def byok_source(*, user_id=None, dsn: str | None = None):
    """Build the default key source: the existing BYOK path.

    Delegates to ``demo.byok_provider.resolve_key`` (vault
    ``byok:<provider>`` first when ``user_id`` is given, then
    the provider env var; OpenRouter returns a fresh surrogate).
    Returns None instead of raising when no key exists, so the
    profile can raise its own ``KeyError`` naming the provider.
    """

    def _source(provider, model):
        try:
            import sys
            from pathlib import Path

            demo_dir = Path(__file__).resolve().parent.parent.parent / "demo"
            if str(demo_dir) not in sys.path:
                sys.path.insert(0, str(demo_dir))
            from demo.byok_provider import resolve_key

            return resolve_key(provider, None, user_id=user_id, dsn=dsn)
        except KeyError:
            return None

    return _source


class ProviderRegistry:
    """Explicit registry of provider profiles.

    First registration of a name wins; a second one raises
    :class:`ProviderShadowError` instead of silently replacing
    the profile an earlier source chose.
    """

    def __init__(self):
        self._profiles = {}

    def register(self, profile):
        """Register a profile instance under its ``name``."""
        if not isinstance(profile, ProviderProfile):
            raise TypeError("profile must be a ProviderProfile instance")
        if not isinstance(profile.name, str) or not profile.name:
            raise TypeError("profile must define a non-empty name")
        if profile.name in self._profiles:
            raise ProviderShadowError(
                f"provider {profile.name!r} is already registered; shadowing is rejected"
            )
        self._profiles[profile.name] = profile
        return profile

    def get(self, name):
        """Return the profile for ``name`` (KeyError if absent)."""
        return self._profiles[name]

    def names(self):
        """Registered provider names, in registration order."""
        return list(self._profiles)

    def __contains__(self, name):
        return name in self._profiles

    def __len__(self):
        return len(self._profiles)

    def resolve(
        self,
        provider,
        model,
        *,
        source=None,
        user_id=None,
        dsn: str | None = None,
        base_url: str | None = None,
    ):
        """Resolve ``(provider, model)`` to a credential.

        ``source`` defaults to the BYOK path
        (:func:`byok_source`). Raises :class:`UnknownProviderError`
        for an unregistered provider, ``ValueError`` when the
        profile does not serve ``model``, and ``KeyError`` when a
        required key cannot be found.
        """
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider must be a non-empty string")
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        model = model.strip()
        if provider not in self._profiles:
            raise UnknownProviderError(
                f"unknown provider {provider!r}; registered: {', '.join(self.names()) or '(none)'}"
            )
        profile = self._profiles[provider]
        if not profile.supports_model(model):
            raise ValueError(f"provider {provider!r} does not serve model {model!r}")
        key_source = source if source is not None else byok_source(user_id=user_id, dsn=dsn)
        key = profile.resolve_key(key_source, user_id=user_id)
        return ResolvedCredential(
            provider=provider,
            model=model,
            api_mode=profile.api_mode,
            base_url=profile.base_url_for(model, base_url=base_url),
            api_key=key,
        )


def default_registry() -> ProviderRegistry:
    """Registry seeded with the providers Puspa knows today.

    OpenRouter carries the router catalog's models
    (``puspa.router.router.CATALOG``); the other API-key
    providers accept any model id. The three OAuth providers
    the BYOK factory already knows (xai, github_copilot,
    openai_codex) are registered as OAuth profiles, and Ollama
    as the local profile.
    """
    registry = ProviderRegistry()
    try:
        from ..router.router import CATALOG

        openrouter_models = tuple(row[1] for row in CATALOG if row[0] == "openrouter")
    except ImportError:
        openrouter_models = ()
    registry.register(
        ApiKeyProviderProfile(
            "openrouter",
            base_url=DEFAULT_BASE_URLS["openrouter"],
            env_key_name=ENV_KEY_NAMES["openrouter"],
            models=openrouter_models,
        )
    )
    for name in ("openai", "anthropic", "gemini", "groq", "deepseek", "mistral", "together"):
        registry.register(
            ApiKeyProviderProfile(
                name,
                base_url=DEFAULT_BASE_URLS[name],
                env_key_name=ENV_KEY_NAMES.get(name),
            )
        )
    registry.register(
        ApiKeyProviderProfile(
            "xai",
            base_url=DEFAULT_BASE_URLS["xai"],
            env_key_name=ENV_KEY_NAMES["xai"],
        )
    )
    registry.register(
        OAuthProviderProfile(
            "github_copilot",
            base_url="https://api.githubcopilot.com",
            token_url="https://github.com/login/oauth/access_token",
        )
    )
    registry.register(
        OAuthProviderProfile(
            "openai_codex",
            base_url="https://chatgpt.com/backend-api/codex",
        )
    )
    registry.register(
        ApiKeyProviderProfile(
            "openai_compatible",
            base_url="",
            env_key_name=None,
        )
    )
    registry.register(LocalProviderProfile())
    return registry


#: The one shared registry every surface resolves through.
PROVIDER_REGISTRY = default_registry()


def resolve(
    provider,
    model,
    *,
    source=None,
    user_id=None,
    dsn: str | None = None,
    base_url: str | None = None,
    registry=None,
) -> ResolvedCredential:
    """Resolve ``(provider, model)`` through the shared registry."""
    registry = registry if registry is not None else PROVIDER_REGISTRY
    return registry.resolve(
        provider, model, source=source, user_id=user_id, dsn=dsn, base_url=base_url
    )


def resolve_for_complete(
    role: str | None = None,
    *,
    model: str | None = None,
    provider: str | None = None,
    source=None,
    user_id=None,
    dsn: str | None = None,
    base_url: str | None = None,
    registry=None,
) -> ResolvedCredential:
    """Resolve the credential a ``complete()`` call needs.

    This is the documented replacement call site for
    ``puspa.router.complete.complete_detailed``: today that
    function picks a model with ``_resolve_choice(role, model)``
    and then resolves the key with ``resolve_api_key``. The
    replacement is a single call here, which returns provider,
    model, api mode, key, and base URL together:

    - explicit ``model`` (with optional ``provider``; defaults
      to ``"openrouter"``) wins;
    - otherwise ``role`` picks the router catalog's cheapest
      model for the role (``puspa.router.router.choose``), and
      its provider is used.

    ``complete.py`` is intentionally NOT changed by this item;
    the swap is a follow-up for the coordinator.
    """
    if model is not None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        return resolve(
            provider or "openrouter",
            model.strip(),
            source=source,
            user_id=user_id,
            dsn=dsn,
            base_url=base_url,
            registry=registry,
        )
    if role is None:
        raise ValueError("resolve_for_complete needs a role or a model")
    from ..router.complete import resolve_role
    from ..router.router import choose

    choice = choose(resolve_role(role))
    if choice is None:
        raise ValueError(f"no model in the router catalog for role {role!r}")
    return resolve(
        choice.provider,
        choice.model,
        source=source,
        user_id=user_id,
        dsn=dsn,
        base_url=base_url,
        registry=registry,
    )
