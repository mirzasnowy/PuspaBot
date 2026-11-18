"""Live completion through the router's cheapest model for a role.

This is the first live caller of the router: it picks the cheapest
model for a role (``puspa.router.router.choose``), calls it over an
OpenRouter-compatible chat-completions endpoint with httpx, and
returns the model's text. It is the completion function the
retrieval seams plug into: ``LLMTripleJudge`` (HippoRAG 2 triple
filtering) and ``LLMQueryDecomposer`` (query decomposition) both
take a ``complete(prompt)`` callable, and
:func:`default_llm_complete` builds exactly that callable from
this module, bound to a role.

Failure is always loud, never a fake answer. A missing key raises
``KeyError`` with a clear message naming what is missing; an HTTP
or transport failure, a malformed reply, or a reply with no text
raises :class:`CompletionError`. Nothing here invents text, falls
back to a canned answer, or guesses a key.

Keys: an explicit ``api_key`` wins. Otherwise the key comes from
the existing BYOK path (``demo.byok_provider.resolve_key``): for
OpenRouter that is a fresh surrogate (``hsurr:...``) from the
connected credential, swapped for the real key by the runtime
only on requests to ``openrouter.ai``; for other providers it is
the vault entry or the provider env var. The key value is never
printed, logged, or written to a file by this module. Tests inject
a ``key_resolver`` and a fake httpx transport instead, so the
suite never touches the network or the vault.

Proxy note: the sandbox egress proxy breaks httpx's own
environment parsing (bracketed IPv6 entries in ``NO_PROXY`` make
``httpx.Client()`` raise ``InvalidURL`` at construction). The
client built here therefore takes its proxy explicitly from the
``HTTPS_PROXY`` / ``HTTP_PROXY`` env vars and sets
``trust_env=False``, so ``NO_PROXY`` is never parsed. A caller
that passes its own ``client`` or ``transport`` owns that choice
instead.

TLS note: the egress proxy terminates TLS with the sandbox's own
CA ("Hatch Sandbox Egress CA"), which lives in the system trust
store (curl trusts it) but not in httpx's bundled certifi store.
The client built here therefore verifies against the system
default trust store (``ssl.create_default_context()``) instead
of certifi. Verification stays on: this only changes *which*
trust store is used, it never disables it.
"""

from __future__ import annotations

import os
import ssl
from dataclasses import dataclass

import httpx

from .fallback import CallResult
from .router import CATALOG, ChoiceModel, choose, compute_cost

__all__ = [
    "CompletionError",
    "CompletionResult",
    "OPENROUTER_API_BASE",
    "ROLE_ALIASES",
    "complete",
    "complete_detailed",
    "default_llm_complete",
    "make_call_fn",
    "resolve_api_key",
    "resolve_llm_complete",
    "resolve_role",
]

#: OpenRouter chat-completions base URL. The runtime swaps the
#: BYOK surrogate for the real key only on this host.
OPENROUTER_API_BASE = "https://openrouter.ai/api/v1"

#: English role names accepted by ``complete`` (and by
#: ``default_llm_complete``), mapped to the router catalog's role
#: names. The catalog's own names pass through unchanged.
ROLE_ALIASES = {
    "reader": "pembaca",
    "searcher": "pencari",
    "planner": "perencana",
    "presenter": "penyaji",
    "checker": "pemeriksa",
    "vision": "vision",
}

#: Roles the router catalog knows, for the error message.
_KNOWN_ROLES = tuple(sorted(set(ROLE_ALIASES) | set(ROLE_ALIASES.values())))


class CompletionError(RuntimeError):
    """A live completion failed: transport, HTTP status, or bad reply.

    ``status`` is the provider HTTP status when known (None for a
    transport error or an unparsable reply). The message always
    names the provider, the model, and what went wrong, so the
    caller can tell a rate limit from a bad request from a dead
    network without guessing.
    """

    def __init__(self, message: str, *, status: int | None = None) -> None:
        super().__init__(message)
        self.message = message
        self.status = status

    def __str__(self) -> str:
        if self.status is not None:
            return f"{self.message} (HTTP {self.status})"
        return self.message


@dataclass
class CompletionResult:
    """One completed call: the text plus what it cost.

    ``cost_rp`` is None when the model is not in the router
    catalog, because then its rates are unknown and no cost is
    invented. Token counts are 0 when the provider reports no
    usage.
    """

    text: str
    provider: str
    model: str
    input_tokens: int = 0
    output_tokens: int = 0
    cost_rp: int | None = None


def resolve_role(role: str) -> str:
    """Map an English role alias (or a catalog role) to a catalog role.

    Raises ``ValueError`` naming the known roles for anything else,
    so a typo fails here instead of silently picking a model for
    the wrong job.
    """
    if not isinstance(role, str) or not role:
        raise ValueError(f"unknown role: {role!r} (known: {', '.join(_KNOWN_ROLES)})")
    mapped = ROLE_ALIASES.get(role, role)
    if mapped not in ("perencana", "penyaji", "pembaca", "pencari",
                      "pemeriksa", "vision"):
        raise ValueError(f"unknown role: {role!r} (known: {', '.join(_KNOWN_ROLES)})")
    return mapped


def _catalog_choice(provider: str, model: str) -> ChoiceModel | None:
    """The catalog entry for a model, or None when it is not listed."""
    for row in CATALOG:
        if row[0] == provider and row[1] == model:
            return ChoiceModel(provider=row[0], model=row[1], family=row[2],
                               rp_per_1k_input=row[3],
                               rp_per_1k_output=row[4], vision=row[5])
    return None


def _resolve_choice(role: str, model: str | None) -> ChoiceModel:
    """The model to call: the role's cheapest, or an explicit model.

    An explicit model keeps the catalog rates when it is listed
    there; when it is not listed, the rates are 0 and the cost
    stays unknown (``cost_rp=None`` on the result) instead of a
    guessed rate.
    """
    if model is not None:
        if not isinstance(model, str) or not model.strip():
            raise ValueError("model must be a non-empty string")
        model = model.strip()
        catalogued = _catalog_choice("openrouter", model)
        if catalogued is not None:
            return catalogued
        return ChoiceModel(provider="openrouter", model=model, family="",
                           rp_per_1k_input=0, rp_per_1k_output=0,
                           vision=False)
    choice = choose(resolve_role(role))
    if choice is None:
        raise ValueError(f"no model in the router catalog for role {role!r}")
    return choice


def resolve_api_key(provider: str, *, api_key: str | None = None,
                    user_id=None, dsn: str | None = None,
                    key_resolver=None) -> str:
    """Find the API key for one provider, without ever printing it.

    Order: an injected ``key_resolver(provider)`` (tests), an
    explicit ``api_key``, then the existing BYOK path
    (``demo.byok_provider.resolve_key``: a fresh OpenRouter
    surrogate from the connected credential, else the vault entry
    or the provider env var). Raises ``KeyError`` with a clear
    message when no key can be found; a key is never invented.
    """
    if key_resolver is not None:
        key = key_resolver(provider)
        if not key or not str(key).strip():
            raise KeyError(
                f"no API key for provider {provider!r}: the injected "
                "key resolver returned nothing")
        return str(key)
    if api_key:
        return str(api_key)
    import sys
    from pathlib import Path

    demo_dir = Path(__file__).resolve().parent.parent.parent.parent / "demo"
    if str(demo_dir) not in sys.path:
        sys.path.insert(0, str(demo_dir))
    from demo.byok_provider import resolve_key  # noqa: E402

    return resolve_key(provider, None, user_id=user_id, dsn=dsn)


def _proxy_from_env() -> str | None:
    """Egress proxy for the client, read explicitly from the env.

    httpx's own env parsing is not used (see the module docstring:
    the sandbox ``NO_PROXY`` breaks it), so the proxy is taken
    from ``HTTPS_PROXY`` / ``HTTP_PROXY`` directly. None means
    direct egress.
    """
    return (
        os.environ.get("HTTPS_PROXY")
        or os.environ.get("https_proxy")
        or os.environ.get("HTTP_PROXY")
        or os.environ.get("http_proxy")
        or None
    )


def _build_client(*, transport=None, timeout: float) -> httpx.Client:
    """Build the HTTP client for one completion call.

    A caller-supplied transport (tests use ``httpx.MockTransport``)
    is honoured. Otherwise the client gets the egress proxy
    explicitly and ``trust_env=False`` so httpx never parses the
    sandbox ``NO_PROXY``, and verifies TLS against the system
    default trust store (see the module docstring: the egress
    proxy's CA lives there, not in httpx's certifi bundle).
    """
    if transport is not None:
        return httpx.Client(transport=transport, timeout=timeout)
    return httpx.Client(proxy=_proxy_from_env(), trust_env=False,
                        verify=ssl.create_default_context(),
                        timeout=timeout)


def _parse_reply(payload: dict, *, provider: str, model: str) -> tuple[str, int, int]:
    """Pull the text and token usage out of a chat-completions reply.

    Strict: the reply must be a JSON object with a non-empty
    ``choices`` list whose first choice carries a non-empty
    ``message.content`` string. Anything else is a
    ``CompletionError``, never an empty string passed off as an
    answer.
    """
    if not isinstance(payload, dict):
        raise CompletionError(
            f"{provider}/{model} returned a reply that is not a JSON object")
    choices = payload.get("choices")
    if not isinstance(choices, list) or not choices:
        raise CompletionError(
            f"{provider}/{model} returned no choices")
    first = choices[0]
    if not isinstance(first, dict):
        raise CompletionError(
            f"{provider}/{model} returned a choice that is not an object")
    message = first.get("message")
    if not isinstance(message, dict):
        raise CompletionError(
            f"{provider}/{model} returned a choice with no message")
    content = message.get("content")
    if not isinstance(content, str) or content.strip() == "":
        raise CompletionError(
            f"{provider}/{model} returned an empty completion")
    usage = payload.get("usage") or {}
    if not isinstance(usage, dict):
        usage = {}
    input_tokens = usage.get("prompt_tokens") or 0
    output_tokens = usage.get("completion_tokens") or 0
    try:
        input_tokens = int(input_tokens)
        output_tokens = int(output_tokens)
    except (TypeError, ValueError) as exc:
        raise CompletionError(
            f"{provider}/{model} returned unusable token counts") from exc
    return content, max(0, input_tokens), max(0, output_tokens)


def complete_detailed(prompt: str, *, role: str = "reader",
                      model: str | None = None, api_key: str | None = None,
                      user_id=None, dsn: str | None = None,
                      temperature: float = 0.0, max_tokens: int = 1024,
                      timeout: float = 60.0, base_url: str | None = None,
                      transport=None, client: httpx.Client | None = None,
                      key_resolver=None) -> CompletionResult:
    """Complete one prompt through the router's cheapest model.

    ``role`` picks the model (see ``ROLE_ALIASES``); ``model``
    overrides the choice with one explicit model id. ``prompt``
    is sent as a single user message. ``temperature`` defaults
    to 0: the judge and decomposer prompts ask for JSON, and a
    deterministic reply is what they need. ``transport`` takes
    an httpx transport (tests pass ``httpx.MockTransport``);
    ``client`` takes a caller-owned ``httpx.Client`` (not
    closed here). ``key_resolver`` takes a ``provider -> key``
    callable (tests inject a fake key); without it the key comes
    from the BYOK path.

    Raises ``KeyError`` when no key can be found and
    ``CompletionError`` on any transport, HTTP, or reply failure.
    """
    if not isinstance(prompt, str) or prompt == "":
        raise ValueError("prompt must be a non-empty string")
    if max_tokens < 1:
        raise ValueError("max_tokens must be at least 1")
    if timeout <= 0:
        raise ValueError("timeout must be positive")
    choice = _resolve_choice(role, model)
    key = resolve_api_key(choice.provider, api_key=api_key,
                          user_id=user_id, dsn=dsn,
                          key_resolver=key_resolver)
    url = (base_url or OPENROUTER_API_BASE).rstrip("/") + "/chat/completions"
    body = {
        "model": choice.model,
        "messages": [{"role": "user", "content": prompt}],
        "temperature": temperature,
        "max_tokens": max_tokens,
    }
    own_client = client is None
    http = client if client is not None else _build_client(
        transport=transport, timeout=timeout)
    try:
        try:
            response = http.post(
                url, json=body,
                headers={"Authorization": f"Bearer {key}",
                         "Content-Type": "application/json"},
                timeout=timeout,
            )
        except httpx.TimeoutException as exc:
            raise CompletionError(
                f"{choice.provider}/{choice.model} timed out after "
                f"{timeout}s") from exc
        except httpx.HTTPError as exc:
            raise CompletionError(
                f"{choice.provider}/{choice.model} request failed: "
                f"{type(exc).__name__}: {exc}") from exc
        if response.status_code < 200 or response.status_code >= 300:
            detail = ""
            try:
                detail = (response.json().get("error", {}) or {}).get(
                    "message", "") or response.text[:300]
            except ValueError:
                detail = response.text[:300]
            raise CompletionError(
                f"{choice.provider}/{choice.model} rejected the request"
                + (f": {detail}" if detail else ""),
                status=response.status_code)
        try:
            payload = response.json()
        except ValueError as exc:
            raise CompletionError(
                f"{choice.provider}/{choice.model} returned a reply "
                "that is not valid JSON") from exc
        text, input_tokens, output_tokens = _parse_reply(
            payload, provider=choice.provider, model=choice.model)
        catalogued = _catalog_choice(choice.provider, choice.model)
        cost = (compute_cost(catalogued, input_tokens, output_tokens)
                if catalogued is not None else None)
        return CompletionResult(
            text=text, provider=choice.provider, model=choice.model,
            input_tokens=input_tokens, output_tokens=output_tokens,
            cost_rp=cost)
    finally:
        if own_client:
            http.close()


def complete(prompt: str, *, role: str = "reader",
             model: str | None = None, api_key: str | None = None,
             user_id=None, dsn: str | None = None,
             temperature: float = 0.0, max_tokens: int = 1024,
             timeout: float = 60.0, base_url: str | None = None,
             transport=None, client: httpx.Client | None = None,
             key_resolver=None) -> str:
    """Complete one prompt and return the model's text.

    Thin wrapper over :func:`complete_detailed`: same arguments,
    same errors (``KeyError`` for a missing key,
    ``CompletionError`` for any call failure), just the text.
    This is the ``complete(prompt)`` callable the retrieval seams
    take.
    """
    return complete_detailed(
        prompt, role=role, model=model, api_key=api_key, user_id=user_id,
        dsn=dsn, temperature=temperature, max_tokens=max_tokens,
        timeout=timeout, base_url=base_url, transport=transport,
        client=client, key_resolver=key_resolver).text


def default_llm_complete(*, role: str = "reader", **kwargs):
    """Build the ``complete(prompt)`` callable for a role.

    The explicit opt-in seam for the retrieval modules: pass the
    returned callable as ``llm_complete`` to ``HippoRAGRetriever``
    or ``retrieve_with_decomposition`` (or pass the string
    ``"default"`` there and let them resolve it). Building the
    callable makes no network call and needs no key; the key is
    resolved (and a missing key reported) on the first call.
    Extra keyword arguments are passed through to :func:`complete`
    (``model=``, ``api_key=``, ``transport=``, ``key_resolver=``,
    ...), so tests can bind a fake transport here once.
    """
    def _complete(prompt: str) -> str:
        return complete(prompt, role=role, **kwargs)

    return _complete


def resolve_llm_complete(llm_complete, *, role: str = "reader", **kwargs):
    """Resolve an ``llm_complete`` seam value to a callable or None.

    ``None`` stays None (the caller runs its deterministic path).
    A callable passes through unchanged. The string ``"default"``
    resolves to :func:`default_llm_complete` for the role: the
    explicit opt-in to the router's live completion. Anything
    else raises ``ValueError`` instead of being silently treated
    as no LLM.
    """
    if llm_complete is None:
        return None
    if callable(llm_complete):
        return llm_complete
    if isinstance(llm_complete, str) and llm_complete == "default":
        return default_llm_complete(role=role, **kwargs)
    raise ValueError(
        "llm_complete must be a callable, the string \"default\", "
        f"or None, not {llm_complete!r}")


def make_call_fn(prompt: str, **kwargs):
    """Build a ``call_fn(entry)`` for ``fallback.call_with_fallback``.

    The returned function calls :func:`complete_detailed` with
    the chain entry's model and wraps the result in a
    ``CallResult`` (text plus token counts), so the fallback
    executor can charge the call and fall through to the next
    tier on a retryable failure. ``prompt`` and the keyword
    arguments (``api_key=``, ``transport=``, ``key_resolver=``,
    ...) are bound at build time; only the model comes from the
    entry.
    """
    if not isinstance(prompt, str) or prompt == "":
        raise ValueError("prompt must be a non-empty string")

    def call_fn(entry) -> CallResult:
        result = complete_detailed(prompt, model=entry.model, **kwargs)
        return CallResult(value=result.text,
                          input_tokens=result.input_tokens,
                          output_tokens=result.output_tokens)

    return call_fn
