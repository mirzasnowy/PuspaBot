"""Per-capability model routing (COW-08).

Route each capability (chat, vision, image generation, speech,
embeddings) to a different model provider, and let the user swap
one capability's provider from the Settings screen with one click.
CowAgent routes every capability separately; PuspaBot had
bring-your-own-key and OpenRouter, but no per-capability routing.
The shape here (one routing table, one resolver per capability)
is adapted from that idea and rewritten as PuspaBot code, never
copied: CowAgent is MIT, and no CowAgent code is reproduced here.

The table (``capability_routing``, one row per (user,
capability), migration 0038) holds the route only, never a key.
Every key lives only in the existing per-user vault
(``puspa.accounts.vault``), under the same ``byok:<provider>``
entry the model settings use, and is read at call time. This
module never accepts, stores, logs, or returns a key anywhere
else: :func:`save_route` takes no key at all and refuses any
payload that carries one.

Providers
---------
A route names a provider plus a model id (and an optional base
URL override). The provider names are the BYOK names the model
settings already know (``openrouter``, ``openai``, ``deepseek``,
``groq``, ``together``, ``mistral``, ``xai``, ``gemini``), plus
two special ones:

- ``hosted`` — the Puspa default for that capability. No base
  URL, no key, no model override: the capability behaves exactly
  as it does today.
- ``ollama`` — a local OpenAI-compatible model (Ollama default
  ``http://localhost:11434/v1``, model default ``llama3.1``).

Not every provider can serve every capability, and saving a
route that cannot work is refused loudly at save time, naming
the providers that can (see :data:`CAPABILITY_PROVIDERS`).
The check is provider-level: whether a *model* can do the job
(a chat model that cannot see images, an embedding model with
the wrong dimension) is something only the provider knows, so
a wrong model fails loudly at call time instead of being
guessed at save time.

Defaults (no row stored)
------------------------
- ``chat`` — follows the user's existing model-settings
  selection (:mod:`puspa.pwa.model_settings`): ``hosted`` means
  the router's fallback chain as today; a BYOK or local
  selection means that endpoint is the primary tier. A stored
  chat route overrides the selection for chat only.
- ``vision`` — ``hosted`` (the router's vision chain as today).
- ``image`` — no usable default: image generation needs a
  provider and a model, so :func:`generate_image` raises a
  clear error until the user picks one.
- ``speech`` — no route of its own: the voice settings
  (:mod:`puspa.pwa.voice_settings`) stay the speech engines,
  and a stored speech route only sets the endpoint + model the
  OpenAI-compatible ASR/TTS engines call. Until one is stored,
  the voice settings behave exactly as today.
- ``embeddings`` — ``hosted`` (the local default embedder:
  FastEmbed when available, else the hash embedder).

Callers
-------
- Chat: :func:`resolve_chat_kwargs` returns the keyword
  arguments for :func:`puspa.router.complete.complete_messages`
  (``base_url`` / ``api_key`` / ``model`` / ``provider``), and
  :func:`puspa.turns.turn_api.make_routed_model_call` is the
  turn loop's adapter: the primary fallback tier goes to the
  routed chat provider, the cheap/free tiers stay the existing
  safety net.
- Vision: :func:`complete_vision_for_user` completes through
  the routed vision provider with role ``"vision"``.
- Image: :func:`generate_image` posts to the routed provider's
  ``/images/generations`` endpoint.
- Speech: :func:`puspa.pwa.voice_settings.resolve_asr` and
  :func:`resolve_tts` consult the stored speech route for the
  endpoint + model when the user picked the OpenAI-compatible
  engine.
- Embeddings: :func:`resolve_embedder` returns a remote
  OpenAI-compatible embedder when an embeddings route is
  stored, else the local default embedder. Honest limit: the
  episodic store is dimension-bound to 384 (see
  ``EMBEDDING_DIM``), so a remote embedder is for new uses,
  not a drop-in for the episodic index; a remote model whose
  dimension is unknown stays unknown until the first embed.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass
from urllib.parse import urlparse

import httpx

from puspa.accounts.ports import _connect, resolve_dsn
from puspa.migrations import apply_migrations
from puspa.pwa.model_settings import (
    DEFAULT_LOCAL_BASE_URL,
    DEFAULT_LOCAL_MODEL,
    KNOWN_BYOK_BASE_URLS,
    LOCAL_DUMMY_KEY,
)
from puspa.pwa.model_settings import (
    get_selection as _get_model_selection,
)
from puspa.pwa.model_settings import (
    has_byok_key as _has_byok_key,
)
from puspa.pwa.model_settings import (
    resolve_completion_kwargs as _resolve_model_selection_kwargs,
)

__all__ = [
    "CAPABILITIES",
    "CAPABILITY_PROVIDERS",
    "DEFAULT_PROVIDERS",
    "CapabilityRoute",
    "ImageResult",
    "complete_vision_for_user",
    "create_schema",
    "delete_routing_key",
    "generate_image",
    "get_route",
    "get_routing",
    "has_routing_key",
    "reset_route",
    "resolve_capability",
    "resolve_chat_kwargs",
    "resolve_embedder",
    "save_route",
    "save_routing_key",
    "validate_base_url",
]

#: The five routable capabilities, in console display order.
CAPABILITIES = ("chat", "vision", "image", "speech", "embeddings")

#: Providers that can serve each capability. The map is the
#: honest limit of what Puspa knows: a provider is listed only
#: when it is known to offer that kind of endpoint. ``deepseek``
#: has no vision model on its API, ``groq`` / ``deepseek`` /
#: ``mistral`` / ``xai`` offer no image endpoint, ``groq`` /
#: ``deepseek`` / ``xai`` offer no embeddings endpoint, and
#: ``hosted`` offers no image or speech endpoint of its own.
#: The map is data, not code: when a provider adds an endpoint,
#: one line here is the whole change.
CAPABILITY_PROVIDERS = {
    "chat": (
        "hosted",
        "ollama",
        "openrouter",
        "openai",
        "deepseek",
        "groq",
        "together",
        "mistral",
        "xai",
        "gemini",
    ),
    "vision": (
        "hosted",
        "ollama",
        "openrouter",
        "openai",
        "groq",
        "together",
        "mistral",
        "xai",
        "gemini",
    ),
    "image": ("openai", "openrouter", "together", "xai", "gemini"),
    "speech": ("openai", "openrouter", "groq", "deepseek", "together"),
    "embeddings": ("hosted", "ollama", "openai", "openrouter", "together", "mistral", "gemini"),
}

#: The default provider per capability when no row is stored.
#: ``chat`` has no entry here: its default is the user's model
#: selection (see :func:`get_route`).
DEFAULT_PROVIDERS = {
    "vision": "hosted",
    "image": None,
    "speech": None,
    "embeddings": "hosted",
}

#: Default model per (provider, capability) when the user names
#: a provider but no model. Only Ollama has one: the local
#: defaults the model settings already use.
DEFAULT_MODELS = {
    ("ollama", "chat"): DEFAULT_LOCAL_MODEL,
    ("ollama", "vision"): DEFAULT_LOCAL_MODEL,
    ("ollama", "embeddings"): "nomic-embed-text",
}

#: Default ASR model when a speech route names no model.
DEFAULT_SPEECH_ASR_MODEL = "whisper-1"

#: Default TTS model when a speech route names no model.
DEFAULT_SPEECH_TTS_MODEL = "tts-1"

#: Known OpenAI-compatible base URLs, by provider. The BYOK map
#: comes from the model settings (one source of truth); Ollama's
#: local default is added here.
KNOWN_BASE_URLS = {**KNOWN_BYOK_BASE_URLS, "ollama": DEFAULT_LOCAL_BASE_URL}

#: Keys a caller must never try to store through the routing
#: path. Any payload carrying one is refused outright, so a key
#: can only ever enter the vault through :func:`save_routing_key`.
_FORBIDDEN_ROUTE_KEYS = ("api_key", "apiKey", "key", "secret", "token", "password")


@dataclass(frozen=True)
class CapabilityRoute:
    """One capability's effective route.

    ``provider`` is one of :data:`CAPABILITY_PROVIDERS` for the
    capability (or None when the capability has no usable
    default yet, e.g. image before the user picks one).
    ``model`` is the model id to ask for (None means "the
    router's default for this capability", which only chat and
    vision have). ``base_url`` is the endpoint override;
    ``has_key`` reports whether the vault holds a BYOK key for
    the provider, without ever exposing the key. ``is_default``
    is True when no row is stored and this is the default.
    """

    capability: str
    provider: str | None = None
    model: str | None = None
    base_url: str | None = None
    has_key: bool = False
    is_default: bool = True

    def to_public_dict(self) -> dict:
        """The route as the Settings screen sees it.

        Contains no key material by construction: the row holds
        no key, and ``has_key`` is a boolean only.
        """
        return {
            "capability": self.capability,
            "provider": self.provider,
            "model": self.model,
            "base_url": self.base_url,
            "has_key": self.has_key,
            "is_default": self.is_default,
        }


def validate_base_url(base_url: str) -> str:
    """Validate a route base URL and return it stripped.

    Only http(s) URLs with a host are accepted, and userinfo is
    refused so credentials cannot hide in the URL. Raises
    ``ValueError`` naming the problem for anything else. Same
    rule as the model and voice settings.
    """
    if not isinstance(base_url, str) or not base_url.strip():
        raise ValueError("base URL must be a non-empty string")
    cleaned = base_url.strip()
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"base URL {cleaned!r} must use http or https, not {parsed.scheme or '(no scheme)'!r}"
        )
    if not parsed.hostname:
        raise ValueError(f"base URL {cleaned!r} has no host")
    if parsed.username or parsed.password:
        raise ValueError("base URL must not contain credentials (userinfo)")
    return cleaned


def create_schema(dsn=None) -> None:
    """Apply the ``capability_routing`` table (migration 0038)."""
    apply_migrations(resolve_dsn(dsn), target="0038")


def _check_capability(capability: str) -> str:
    """Return the capability lowercased, or raise for an unknown one."""
    if not isinstance(capability, str):
        raise ValueError(f"unknown capability: {capability!r} (known: {', '.join(CAPABILITIES)})")
    cleaned = capability.strip().lower()
    if cleaned not in CAPABILITIES:
        raise ValueError(f"unknown capability: {capability!r} (known: {', '.join(CAPABILITIES)})")
    return cleaned


def _check_provider(capability: str, provider: str) -> str:
    """Return the provider lowercased, or raise when it cannot serve the capability."""
    if not isinstance(provider, str):
        raise ValueError(f"unknown provider: {provider!r}")
    cleaned = provider.strip().lower()
    allowed = CAPABILITY_PROVIDERS[capability]
    if cleaned not in allowed:
        raise ValueError(
            f"provider {provider!r} cannot serve capability {capability!r} "
            f"(providers that can: {', '.join(allowed)})"
        )
    return cleaned


def _check_no_key(payload: dict) -> None:
    """Refuse any payload that tries to carry key material."""
    for forbidden in _FORBIDDEN_ROUTE_KEYS:
        if forbidden in payload:
            raise ValueError(
                f"refusing to store {forbidden!r} outside the vault: "
                "a provider key is saved only through save_routing_key, "
                "never through the route"
            )


def _default_route(capability: str, user_id=None, dsn=None) -> CapabilityRoute:
    """The route for a capability with no stored row."""
    if capability == "chat":
        # Chat follows the user's existing model selection, so
        # the two controls agree until the user routes chat
        # explicitly. Hosted means "the router's chain as today".
        selection = _get_model_selection(user_id, dsn=dsn)
        if selection.source == "hosted":
            return CapabilityRoute(capability="chat", provider="hosted", is_default=True)
        if selection.source == "local":
            return CapabilityRoute(
                capability="chat",
                provider="ollama",
                model=selection.model or DEFAULT_LOCAL_MODEL,
                base_url=selection.base_url or DEFAULT_LOCAL_BASE_URL,
                is_default=True,
            )
        # byok
        return CapabilityRoute(
            capability="chat",
            provider=selection.provider,
            model=selection.model,
            base_url=selection.base_url,
            has_key=_has_byok_key(user_id, selection.provider, dsn=dsn)
            if selection.provider
            else False,
            is_default=True,
        )
    provider = DEFAULT_PROVIDERS[capability]
    if provider is None:
        return CapabilityRoute(capability=capability, provider=None, is_default=True)
    return CapabilityRoute(
        capability=capability,
        provider=provider,
        model=DEFAULT_MODELS.get((provider, capability)),
        base_url=KNOWN_BASE_URLS.get(provider) if provider != "hosted" else None,
        is_default=True,
    )


def _row_to_route(capability: str, row, dsn=None, user_id=None) -> CapabilityRoute:
    """A stored row as a public route (with ``has_key`` filled in)."""
    provider, model, base_url = row
    return CapabilityRoute(
        capability=capability,
        provider=provider,
        model=model,
        base_url=base_url,
        has_key=_has_byok_key(user_id, provider, dsn=dsn)
        if provider not in ("hosted", "ollama")
        else False,
        is_default=False,
    )


def get_route(user_id, capability: str, dsn=None) -> CapabilityRoute:
    """Load one capability's effective route; the default when none is stored.

    Never raises for a missing row: a user who never opened the
    routing control gets the capability's default.
    """
    capability = _check_capability(capability)
    create_schema(dsn)

    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT provider, model, base_url FROM capability_routing"
            " WHERE user_id = %s AND capability = %s",
            (user_id, capability),
        )
        row = cur.fetchone()
    if row is None:
        return _default_route(capability, user_id=user_id, dsn=dsn)
    return _row_to_route(capability, row, dsn=dsn, user_id=user_id)


def get_routing(user_id, dsn=None) -> dict[str, CapabilityRoute]:
    """Load every capability's effective route for a user."""
    return {capability: get_route(user_id, capability, dsn=dsn) for capability in CAPABILITIES}


def save_route(
    user_id,
    capability: str,
    provider: str,
    model: str | None = None,
    base_url: str | None = None,
    dsn=None,
) -> CapabilityRoute:
    """Persist one capability's route (choice only, never a key).

    Validates the capability, then the provider against
    :data:`CAPABILITY_PROVIDERS`: a provider that cannot serve
    the capability is refused with ``ValueError`` naming the
    providers that can. A base URL is validated when given. A
    model is required for every provider except ``hosted`` (no
    model: the router's default) and ``ollama`` (default
    ``llama3.1`` for chat/vision, ``nomic-embed-text`` for
    embeddings). For ``hosted`` any model or base URL is dropped:
    hosted means "the default behaviour", nothing else is kept.
    Swapping one capability touches only that capability's row:
    the other four routes are never read, let alone changed.
    Returns the stored route (with ``has_key`` filled in).
    """
    capability = _check_capability(capability)
    provider = _check_provider(capability, provider)
    model = model.strip() if isinstance(model, str) else None
    model = model or None
    base_url = base_url.strip() if isinstance(base_url, str) else None
    base_url = base_url or None
    if base_url is not None:
        base_url = validate_base_url(base_url)
    if provider == "hosted":
        model = None
        base_url = None
    elif provider == "ollama":
        model = model or DEFAULT_MODELS.get(("ollama", capability))
        base_url = base_url or DEFAULT_LOCAL_BASE_URL
    elif model is None:
        raise ValueError(
            f"provider {provider!r} needs a model for capability {capability!r}: "
            "pick one in Settings (for example, the model id the provider lists)"
        )
    create_schema(dsn)

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO capability_routing (user_id, capability, provider, model, base_url)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (user_id, capability) DO UPDATE SET"
                " provider = EXCLUDED.provider, model = EXCLUDED.model,"
                " base_url = EXCLUDED.base_url, updated_at = now()",
                (user_id, capability, provider, model, base_url),
            )
        conn.commit()
    return get_route(user_id, capability, dsn=dsn)


def save_route_from_payload(user_id, payload: dict, dsn=None) -> CapabilityRoute:
    """Persist one route from a plain dict (the API layer's shape).

    The dict carries ``capability``, ``provider``, ``model``,
    ``base_url``. Any key field in it is refused with
    ``ValueError`` — keys go to the vault only.
    """
    if not isinstance(payload, dict):
        raise ValueError("route body must be a JSON object")
    _check_no_key(payload)
    return save_route(
        user_id,
        str(payload.get("capability", "")),
        str(payload.get("provider", "")),
        model=payload.get("model"),
        base_url=payload.get("base_url"),
        dsn=dsn,
    )


def reset_route(user_id, capability: str, dsn=None) -> CapabilityRoute:
    """Drop one capability's stored route; the default applies again.

    Idempotent: resetting a capability that was never routed
    returns its default. Other capabilities are untouched.
    """
    capability = _check_capability(capability)
    create_schema(dsn)

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "DELETE FROM capability_routing WHERE user_id = %s AND capability = %s",
                (user_id, capability),
            )
        conn.commit()
    return get_route(user_id, capability, dsn=dsn)


def save_routing_key(user_id, provider: str, key: str, dsn=None) -> None:
    """Store a provider key in the existing per-user vault, and only there.

    The vault entry is ``byok:<provider>`` — the same entry the
    model settings use, so one key serves every capability routed
    to that provider. An empty key is refused; the key value is
    never logged or returned.
    """
    from puspa.pwa.model_settings import save_byok_key

    save_byok_key(user_id, provider, key, dsn=dsn)


def has_routing_key(user_id, provider: str, dsn=None) -> bool:
    """Whether a BYOK key for ``provider`` exists in the vault."""
    return _has_byok_key(user_id, provider, dsn=dsn)


def delete_routing_key(user_id, provider: str, dsn=None) -> None:
    """Remove a provider key from the vault. Idempotent."""
    from puspa.pwa.model_settings import delete_byok_key

    delete_byok_key(user_id, provider, dsn=dsn)


def _resolve_key(user_id, provider: str, dsn=None) -> str | None:
    """The vault key for a provider, at call time only.

    ``hosted`` needs no key (None); ``ollama`` needs no real key
    (the dummy ``"ollama"`` the local convention uses). Any other
    provider raises ``KeyError`` when no vault key exists, naming
    the fix. The key value is returned only as a call argument
    to the provider — never logged, never written to a file.
    """
    if provider == "hosted":
        return None
    if provider == "ollama":
        return LOCAL_DUMMY_KEY
    from puspa.accounts.vault import get as vault_get

    try:
        return vault_get(user_id, f"byok:{provider.strip().lower()}", dsn=dsn)
    except Exception as exc:
        # Missing entry (KeyError) and an unusable vault
        # (VaultError when no master key is configured) both mean
        # the same thing to the caller: no usable key.
        raise KeyError(
            f"no key stored for provider {provider!r}: "
            "save one in Settings first (it lives in the vault only)"
        ) from exc


def resolve_capability(user_id, capability: str, dsn=None) -> dict:
    """Resolve one capability to the endpoint its caller must use.

    Returns ``{"provider", "model", "base_url", "api_key"}``: the
    provider name, the model id (None only for ``hosted``, where
    the router picks, and for image/speech with no route stored,
    where the caller must refuse first), the base URL (the
    provider's known endpoint, or the stored override), and the
    key read from the vault at call time (None for ``hosted``).
    Raises ``KeyError`` when a BYOK provider has no vault key,
    and ``ValueError`` when the capability has no usable route
    yet (image or speech with nothing stored).
    """
    route = get_route(user_id, capability, dsn=dsn)
    provider = route.provider
    if provider is None:
        raise ValueError(
            f"capability {capability!r} has no provider yet: pick one in Settings first"
        )
    if route.capability == "chat" and route.is_default:
        # The default chat route is the model selection, whose
        # resolver already knows how to turn it into call
        # arguments (hosted -> {}, byok/local -> endpoint+key).
        kwargs = _resolve_model_selection_kwargs(user_id, dsn=dsn)
        if not kwargs:
            return {"provider": "hosted", "model": None, "base_url": None, "api_key": None}
        return {
            "provider": kwargs.get("provider", "openrouter"),
            "model": kwargs.get("model"),
            "base_url": kwargs.get("base_url"),
            "api_key": kwargs.get("api_key"),
        }
    return {
        "provider": provider,
        "model": route.model,
        "base_url": route.base_url or KNOWN_BASE_URLS.get(provider),
        "api_key": _resolve_key(user_id, provider, dsn=dsn),
    }


def resolve_chat_kwargs(user_id, dsn=None) -> dict:
    """Resolve the chat capability to router completion arguments.

    The keyword arguments to pass to
    :func:`puspa.router.complete.complete_messages`: ``base_url``
    / ``api_key`` / ``model`` / ``provider`` for a routed chat
    provider, ``{}`` for ``hosted`` (the router behaves exactly
    as today). A stored chat route wins over the model selection;
    with no stored route the selection is the route.
    """
    resolved = resolve_capability(user_id, "chat", dsn=dsn)
    if resolved["provider"] == "hosted":
        return {}
    kwargs: dict = {"provider": resolved["provider"]}
    if resolved["base_url"] is not None:
        kwargs["base_url"] = resolved["base_url"]
    if resolved["api_key"] is not None:
        kwargs["api_key"] = resolved["api_key"]
    if resolved["model"] is not None:
        kwargs["model"] = resolved["model"]
    return kwargs


def complete_vision_for_user(
    messages: list[dict],
    *,
    user_id,
    dsn=None,
    **kwargs,
):
    """Complete a vision message list through the routed vision provider.

    Thin wrapper over :func:`puspa.router.complete.complete_messages`
    with role ``"vision"``: the stored vision route resolves to
    ``base_url`` / ``api_key`` / ``model`` / ``provider`` and
    those are passed through, so the request really goes to the
    selected endpoint. Explicit caller kwargs win over the
    resolved route. Returns the router's ``CompletionResult``;
    same errors as ``complete_messages`` (``KeyError`` for a
    missing BYOK key, ``CompletionError`` for any call failure,
    ``ValueError`` when vision has no provider yet).
    """
    from puspa.router.complete import complete_messages

    resolved = resolve_capability(user_id, "vision", dsn=dsn)
    if resolved["provider"] == "hosted":
        merged = dict(kwargs)
    else:
        merged = {
            "provider": resolved["provider"],
            "model": resolved["model"],
            "base_url": resolved["base_url"],
            "api_key": resolved["api_key"],
            **kwargs,
        }
    return complete_messages(messages, role="vision", user_id=user_id, dsn=dsn, **merged)


def _proxy_from_env() -> str | None:
    """Egress proxy for the image client, read explicitly from the env.

    Same rule as ``puspa.router.complete``: httpx's own env
    parsing is not used (the sandbox ``NO_PROXY`` breaks it), so
    the proxy is taken from ``HTTPS_PROXY`` / ``HTTP_PROXY``
    directly. None means direct egress.
    """
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or None
    )


@dataclass
class ImageResult:
    """One generated image: the URL or the base64 bytes, plus what made it."""

    url: str | None
    b64_json: str | None
    provider: str
    model: str
    revised_prompt: str | None = None


def generate_image(
    prompt: str,
    *,
    user_id,
    size: str = "1024x1024",
    dsn=None,
    transport=None,
    client: httpx.Client | None = None,
    timeout: float = 120.0,
) -> ImageResult:
    """Generate one image through the routed image provider.

    Posts to the routed provider's ``/images/generations``
    endpoint (OpenAI shape) with the vault key, and returns the
    image URL (or base64 bytes) the provider sent back. Raises
    ``ValueError`` when no image route is stored yet ("pick a
    provider in Settings first") or the prompt is empty, and
    ``KeyError`` when the provider has no vault key. An HTTP or
    transport failure raises ``RuntimeError`` naming the
    provider, the model, and the status, never a fake image.
    Tests inject a ``transport`` (``httpx.MockTransport``); the
    suite never touches a real image endpoint.
    """
    if not isinstance(prompt, str) or not prompt.strip():
        raise ValueError("prompt must be a non-empty string")
    resolved = resolve_capability(user_id, "image", dsn=dsn)
    provider = resolved["provider"]
    model = resolved["model"]
    if not model:
        raise ValueError(
            f"image is routed to provider {provider!r} with no model: pick a model in Settings"
        )
    base_url = (resolved["base_url"] or KNOWN_BASE_URLS.get(provider) or "").rstrip("/")
    if not base_url:
        raise ValueError(f"provider {provider!r} has no known image endpoint")
    key = resolved["api_key"]
    if not key:
        raise KeyError(
            f"no key stored for provider {provider!r}: "
            "save one in Settings first (it lives in the vault only)"
        )
    url = base_url + "/images/generations"
    own_client = client is None
    http = client
    if http is None:
        if transport is not None:
            http = httpx.Client(transport=transport, timeout=timeout)
        else:
            http = httpx.Client(
                proxy=_proxy_from_env(),
                trust_env=False,
                verify=ssl.create_default_context(),
                timeout=timeout,
            )
    try:
        try:
            response = http.post(
                url,
                json={"model": model, "prompt": prompt.strip(), "size": size},
                headers={"Authorization": f"Bearer {key}", "Content-Type": "application/json"},
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise RuntimeError(
                f"{provider}/{model} image request timed out after {timeout}s"
            ) from exc
        except httpx.HTTPError as exc:
            raise RuntimeError(
                f"{provider}/{model} image request failed: {type(exc).__name__}: {exc}"
            ) from exc
        if response.status_code < 200 or response.status_code >= 300:
            detail = ""
            try:
                detail = (response.json().get("error", {}) or {}).get(
                    "message", ""
                ) or response.text[:300]
            except ValueError:
                detail = response.text[:300]
            raise RuntimeError(
                f"{provider}/{model} rejected the image request"
                + (f": {detail}" if detail else ""),
            )
        try:
            payload = response.json()
        except ValueError as exc:
            raise RuntimeError(
                f"{provider}/{model} returned an image reply that is not valid JSON"
            ) from exc
        data = payload.get("data")
        if not isinstance(data, list) or not data or not isinstance(data[0], dict):
            raise RuntimeError(f"{provider}/{model} returned no image")
        first = data[0]
        return ImageResult(
            url=first.get("url"),
            b64_json=first.get("b64_json"),
            provider=provider,
            model=model,
            revised_prompt=first.get("revised_prompt"),
        )
    finally:
        if own_client:
            http.close()


def resolve_embedder(user_id, dsn=None, http_client=None):
    """Resolve the embeddings capability to an embedder instance.

    A stored embeddings route to a non-hosted provider returns
    an :class:`puspa.memory.embeddings.OpenAICompatibleEmbedding`
    against that provider's endpoint with the vault key (model
    required; ``KeyError`` when no key is stored). With no stored
    route, or a ``hosted`` route, the local default embedder
    (FastEmbed when available, else the hash embedder) is
    returned, exactly as today. Honest limit: the episodic store
    is dimension-bound to 384, so a remote embedder is for new
    uses, not a drop-in for the episodic index.
    """
    from puspa.memory.embeddings import get_default_embedder

    route = get_route(user_id, "embeddings", dsn=dsn)
    if route.provider in (None, "hosted"):
        return get_default_embedder()
    from puspa.memory.embeddings import OpenAICompatibleEmbedding

    resolved = resolve_capability(user_id, "embeddings", dsn=dsn)
    if not resolved["model"]:
        raise ValueError(
            f"embeddings are routed to provider {resolved['provider']!r} with no model: "
            "pick a model in Settings"
        )
    return OpenAICompatibleEmbedding(
        resolved["base_url"] or KNOWN_BASE_URLS.get(resolved["provider"]),
        resolved["api_key"],
        resolved["model"],
        provider=resolved["provider"],
        http_client=http_client,
    )
