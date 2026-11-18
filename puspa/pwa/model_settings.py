"""Own-model selection for the PWA Settings screen (G33).

A user picks one of three model sources, and the choice is
persisted in Postgres (table ``model_settings``, one row per
user) so it survives a restart. The choice is then wired into
the router's completion path: :func:`complete_with_selection`
resolves the stored selection to the ``base_url`` / ``api_key`` /
``model`` arguments of :func:`puspa.router.complete.complete_messages`
and calls it, so the selected endpoint is really the one that
receives the request — the selection is not just stored.

The three sources (decision locked with Nero, see
``docs/modes-decision.md``):

- ``hosted`` — the Puspa default. No base URL, no key, no model
  override: the router behaves exactly as it does today.
- ``byok`` — bring your own key. The key lives ONLY in the
  existing per-user vault (``puspa.accounts.vault``, entry
  ``byok:<provider>``, envelope-encrypted with the vault master
  key). This module never accepts, stores, logs, or returns a
  key anywhere else: :func:`save_selection` takes no key at all
  and refuses any payload that carries one, and the selection
  row holds no key column. A BYOK key is written only through
  :func:`save_byok_key` and read only through
  :func:`resolve_completion_kwargs` at call time.
- ``local`` — a local OpenAI-compatible model (Ollama default
  ``http://localhost:11434/v1``). The base URL must be a valid
  http(s) URL; a key is not needed, so a fixed dummy key
  (``"ollama"``) is sent, matching the existing BYOK factory's
  local-provider convention. Honest limit: a local model needs
  real hardware; for most users BYOK is the realistic path,
  local is for users with a GPU or patience for CPU.

Base URLs are validated by :func:`validate_base_url`: the scheme
must be http or https, a host must be present, and no userinfo
(``user:password@host``) is allowed, so a key can never be
smuggled into the stored URL.
"""

from __future__ import annotations

from dataclasses import dataclass
from urllib.parse import urlparse

__all__ = [
    "BYOK_KEY_PREFIX",
    "DEFAULT_LOCAL_BASE_URL",
    "DEFAULT_LOCAL_MODEL",
    "KNOWN_BYOK_BASE_URLS",
    "LOCAL_DUMMY_KEY",
    "SOURCES",
    "ModelSelection",
    "complete_with_selection",
    "create_schema",
    "delete_byok_key",
    "get_selection",
    "has_byok_key",
    "llm_complete_for_user",
    "resolve_completion_kwargs",
    "save_byok_key",
    "save_selection",
    "validate_base_url",
]

#: The three model sources the Settings picker offers.
SOURCES = ("hosted", "byok", "local")

#: Default base URL for a local model (Ollama's OpenAI-compatible endpoint).
DEFAULT_LOCAL_BASE_URL = "http://localhost:11434/v1"

#: Default model id asked of a local server when the user names none.
#: The user must have pulled it (``ollama pull llama3.1``); the
#: server, not Puspa, reports when it is missing.
DEFAULT_LOCAL_MODEL = "llama3.1"

#: Dummy key sent to a local server, which needs no real key.
LOCAL_DUMMY_KEY = "ollama"

#: Vault entry prefix for BYOK keys (same ``byok:<provider>``
#: convention as the BYOK factory in ``demo/byok_provider.py``).
BYOK_KEY_PREFIX = "byok:"

#: Known OpenAI-compatible base URLs for BYOK providers, so a
#: user who picks a provider does not have to type its endpoint.
#: A user-supplied base URL on the selection wins over this map.
KNOWN_BYOK_BASE_URLS = {
    "openrouter": "https://openrouter.ai/api/v1",
    "openai": "https://api.openai.com/v1",
    "deepseek": "https://api.deepseek.com/v1",
    "groq": "https://api.groq.com/openai/v1",
    "together": "https://api.together.xyz/v1",
    "mistral": "https://api.mistral.ai/v1",
    "xai": "https://api.x.ai/v1",
    "gemini": "https://generativelanguage.googleapis.com/v1beta/openai/",
}

#: Keys a caller must never try to store through the selection
#: path. Any payload carrying one is refused outright, so a key
#: can only ever enter the vault through :func:`save_byok_key`.
_FORBIDDEN_SELECTION_KEYS = ("api_key", "apiKey", "key", "secret", "token", "password")


@dataclass(frozen=True)
class ModelSelection:
    """One user's stored model choice.

    ``source`` is one of ``SOURCES``. ``base_url`` is the local
    server (or BYOK provider override) endpoint; ``model`` is the
    model id to ask for; ``provider`` is the BYOK provider name
    (whose key lives in the vault under ``byok:<provider>``).
    ``has_byok_key`` reports whether that vault entry exists,
    without ever exposing the key.
    """

    source: str = "hosted"
    base_url: str | None = None
    model: str | None = None
    provider: str | None = None
    has_byok_key: bool = False

    def to_public_dict(self) -> dict:
        """The selection as the Settings screen sees it.

        Contains no key material by construction: the row holds
        no key, and ``has_byok_key`` is a boolean only.
        """
        return {
            "source": self.source,
            "base_url": self.base_url,
            "model": self.model,
            "provider": self.provider,
            "has_byok_key": self.has_byok_key,
        }


def validate_base_url(base_url: str) -> str:
    """Validate a model base URL and return it stripped.

    Only http(s) URLs with a host are accepted, and userinfo is
    refused so credentials cannot hide in the URL. Raises
    ``ValueError`` naming the problem for anything else.
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
    """Create the ``model_settings`` table if it does not exist.

    One row per user; the row holds the choice only, never a key.
    Idempotent (``CREATE TABLE IF NOT EXISTS``).
    """
    from puspa.accounts.users import _connect

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "CREATE TABLE IF NOT EXISTS model_settings ("
                " user_id UUID PRIMARY KEY REFERENCES users(id) ON DELETE CASCADE,"
                " source TEXT NOT NULL,"
                " base_url TEXT,"
                " model TEXT,"
                " provider TEXT,"
                " updated_at TIMESTAMPTZ NOT NULL DEFAULT now()"
                ")"
            )
        conn.commit()


def _byok_vault_name(provider: str) -> str:
    return f"{BYOK_KEY_PREFIX}{provider.strip().lower()}"


def _check_no_key(payload: dict) -> None:
    """Refuse any payload that tries to carry key material."""
    for forbidden in _FORBIDDEN_SELECTION_KEYS:
        if forbidden in payload:
            raise ValueError(
                f"refusing to store {forbidden!r} outside the vault: "
                "a BYOK key is saved only through save_byok_key, "
                "never through the selection"
            )


def save_selection(user_id, selection: ModelSelection | dict, dsn=None) -> ModelSelection:
    """Persist a user's model selection (choice only, never a key).

    Accepts a :class:`ModelSelection` or a plain dict with the
    same fields (``source``, ``base_url``, ``model``,
    ``provider``). Validates the source and, for ``local`` and
    any explicit BYOK base URL, the base URL. A dict carrying
    any key field (``api_key``, ``key``, ...) is refused with
    ``ValueError`` — keys go to the vault only. Returns the
    stored selection (with ``has_byok_key`` filled in).
    """
    if isinstance(selection, dict):
        _check_no_key(selection)
        resolved = ModelSelection(
            source=str(selection.get("source", "hosted")),
            base_url=selection.get("base_url"),
            model=selection.get("model"),
            provider=selection.get("provider"),
        )
    elif isinstance(selection, ModelSelection):
        resolved = selection
    else:
        raise ValueError("selection must be a ModelSelection or a dict")
    resolved = _normalize(resolved)
    create_schema(dsn)
    from puspa.accounts.users import _connect

    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO model_settings (user_id, source, base_url, model, provider)"
                " VALUES (%s, %s, %s, %s, %s)"
                " ON CONFLICT (user_id) DO UPDATE SET"
                " source = EXCLUDED.source, base_url = EXCLUDED.base_url,"
                " model = EXCLUDED.model, provider = EXCLUDED.provider,"
                " updated_at = now()",
                (
                    user_id,
                    resolved.source,
                    resolved.base_url,
                    resolved.model,
                    resolved.provider,
                ),
            )
        conn.commit()
    return get_selection(user_id, dsn=dsn)


def _normalize(selection: ModelSelection) -> ModelSelection:
    """Validate one selection and fill in the per-source defaults."""
    source = selection.source.strip().lower() if isinstance(selection.source, str) else ""
    if source not in SOURCES:
        raise ValueError(f"unknown model source {selection.source!r} (known: {', '.join(SOURCES)})")
    base_url = selection.base_url.strip() if isinstance(selection.base_url, str) else None
    base_url = base_url or None
    model = selection.model.strip() if isinstance(selection.model, str) else None
    model = model or None
    provider = selection.provider.strip().lower() if isinstance(selection.provider, str) else None
    provider = provider or None
    if source == "hosted":
        # Hosted means "the current default behaviour": no URL,
        # no model, no provider override is kept.
        return ModelSelection(source="hosted")
    if source == "local":
        return ModelSelection(
            source="local",
            base_url=validate_base_url(base_url) if base_url else DEFAULT_LOCAL_BASE_URL,
            model=model or DEFAULT_LOCAL_MODEL,
        )
    # byok
    if provider is None:
        raise ValueError("BYOK selection needs a provider name (e.g. 'openrouter')")
    if base_url is not None:
        base_url = validate_base_url(base_url)
    return ModelSelection(source="byok", base_url=base_url, model=model, provider=provider)


def get_selection(user_id, dsn=None) -> ModelSelection:
    """Load a user's stored selection; ``hosted`` when none is stored.

    Never raises for a missing row: a user who never opened the
    picker gets the current default behaviour.
    """
    create_schema(dsn)
    from puspa.accounts.users import _connect

    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute(
            "SELECT source, base_url, model, provider FROM model_settings WHERE user_id = %s",
            (user_id,),
        )
        row = cur.fetchone()
    if row is None:
        return ModelSelection(source="hosted")
    selection = ModelSelection(source=row[0], base_url=row[1], model=row[2], provider=row[3])
    if selection.source == "byok" and selection.provider:
        return ModelSelection(
            source=selection.source,
            base_url=selection.base_url,
            model=selection.model,
            provider=selection.provider,
            has_byok_key=has_byok_key(user_id, selection.provider, dsn=dsn),
        )
    return selection


def save_byok_key(user_id, provider: str, key: str, dsn=None) -> None:
    """Store a BYOK key in the existing per-user vault, and only there.

    The vault entry is ``byok:<provider>`` (envelope-encrypted;
    only ciphertext reaches the database). An empty key is
    refused; the key value is never logged or returned.
    """
    from puspa.accounts.vault import put as vault_put

    if not isinstance(provider, str) or not provider.strip():
        raise ValueError("BYOK provider must be a non-empty string")
    if not isinstance(key, str) or not key.strip():
        raise ValueError("BYOK key must be a non-empty string")
    vault_put(user_id, _byok_vault_name(provider), key, dsn=dsn)


def has_byok_key(user_id, provider: str, dsn=None) -> bool:
    """Whether a BYOK key for ``provider`` exists in the vault."""
    from puspa.accounts.vault import get as vault_get

    try:
        vault_get(user_id, _byok_vault_name(provider), dsn=dsn)
    except (KeyError, Exception):
        return False
    return True


def delete_byok_key(user_id, provider: str, dsn=None) -> None:
    """Remove a BYOK key from the vault. Idempotent."""
    from puspa.accounts.vault import delete as vault_delete

    vault_delete(user_id, _byok_vault_name(provider), dsn=dsn)


def resolve_completion_kwargs(user_id, selection: ModelSelection | None = None, dsn=None) -> dict:
    """Resolve a stored selection to router completion arguments.

    Returns the keyword arguments to pass to
    :func:`puspa.router.complete.complete_messages` (or
    :func:`complete_detailed` / :func:`complete`):

    - ``hosted``: ``{}`` — the router behaves exactly as today.
    - ``byok``: ``base_url`` (the provider's known endpoint, or
      the user's override), ``api_key`` read from the vault at
      call time, ``model`` when the user named one. Raises
      ``KeyError`` when no vault key exists for the provider.
    - ``local``: ``base_url`` (the stored URL, Ollama default
      when unset), ``api_key`` = the dummy ``"ollama"`` key, and
      ``model`` (the stored model, ``llama3.1`` default), with
      ``provider`` = ``"ollama"`` so the result names the local
      provider instead of OpenRouter.

    The key is returned only as a call argument to the router —
    never logged, never written to a file, never put in the
    selection row.
    """
    if selection is None:
        selection = get_selection(user_id, dsn=dsn)
    if selection.source == "hosted":
        return {}
    if selection.source == "local":
        return {
            "base_url": selection.base_url or DEFAULT_LOCAL_BASE_URL,
            "api_key": LOCAL_DUMMY_KEY,
            "model": selection.model or DEFAULT_LOCAL_MODEL,
            "provider": "ollama",
        }
    if selection.source == "byok":
        from puspa.accounts.vault import get as vault_get

        provider = selection.provider or "openrouter"
        try:
            key = vault_get(user_id, _byok_vault_name(provider), dsn=dsn)
        except KeyError as exc:
            raise KeyError(
                f"no BYOK key stored for provider {provider!r}: "
                "save one in Settings first (it lives in the vault only)"
            ) from exc
        kwargs: dict = {"api_key": key, "provider": provider}
        base_url = selection.base_url or KNOWN_BYOK_BASE_URLS.get(provider)
        if base_url is not None:
            kwargs["base_url"] = base_url
        if selection.model:
            kwargs["model"] = selection.model
        return kwargs
    raise ValueError(f"unknown model source {selection.source!r}")


def complete_with_selection(
    prompt: str,
    *,
    user_id,
    role: str = "reader",
    selection: ModelSelection | None = None,
    dsn=None,
    **kwargs,
):
    """Complete one prompt using the user's stored model selection.

    Thin wrapper over :func:`puspa.router.complete.complete_detailed`:
    the selection resolves to ``base_url`` / ``api_key`` /
    ``model`` / ``provider`` (see :func:`resolve_completion_kwargs`)
    and those are passed through, so the request really goes to
    the selected endpoint with the selected key. Explicit caller
    kwargs win over the resolved selection. Returns the router's
    ``CompletionResult``; same errors as ``complete_detailed``
    (``KeyError`` for a missing BYOK key, ``CompletionError``
    for any call failure).
    """
    from puspa.router.complete import complete_detailed

    resolved = resolve_completion_kwargs(user_id, selection, dsn=dsn)
    merged = {**resolved, **kwargs}
    return complete_detailed(prompt, role=role, user_id=user_id, dsn=dsn, **merged)


def llm_complete_for_user(*, user_id, role: str = "reader", dsn=None, **kwargs):
    """Build the ``complete(prompt)`` callable for a user.

    The per-user form of ``default_llm_complete``: the returned
    callable resolves the user's stored selection on every call
    (so a Settings change takes effect without rebuilding it)
    and completes through the selected endpoint. Pass it as
    ``llm_complete`` to the retrieval seams, or resolve the
    string ``"default"`` per user through this function.
    """

    def _complete(prompt: str) -> str:
        return complete_with_selection(prompt, user_id=user_id, role=role, dsn=dsn, **kwargs).text

    return _complete
