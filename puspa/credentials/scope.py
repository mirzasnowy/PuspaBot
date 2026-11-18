"""Secret scope per profile (G22).

Pattern studied, then rewritten as Puspa's own code (see the
G22 section of the repo-comparison report): hermes-agent reads
secrets through a per-profile secret scope
(``agent/secret_scope.py``), never through a raw ``os.getenv``,
so one multi-profile process cannot leak credentials between
profiles. No hermes-agent code is copied.

How it works
------------
A :class:`CredentialScope` maps ``(profile_id, provider)`` to a
key *source* — a callable ``(provider, model) -> str | None``.
Reading a secret always goes through the scope for that
profile, so profile A can never see profile B's key, even
though both live in the same process:

- :meth:`CredentialScope.bind` registers a source for one
  profile and provider. Sources are explicit: a vault source
  (:func:`vault_source`, envelope-encrypted per user via
  ``puspa.accounts.vault``), a pool source (:func:`pool_source`,
  drawing from a :class:`puspa.credentials.pool.CredentialPool`),
  or a scoped env source (:func:`scoped_env_source`, which binds
  ONE named env var to ONE profile at bind time — the env var
  is never read for any other profile).
- :meth:`CredentialScope.get` returns the key for a profile, or
  raises ``KeyError`` naming the profile and provider (never
  falling back to another profile's key, and never falling back
  to a global env read).
- :func:`check_credential` is the shared interface with the
  G23 tool worker (same name and signature — do NOT rename it):
  ``check_credential(profile_id, provider, model=None) -> bool``
  is True when that profile has a usable credential for the
  provider (and that model, when given).

The module-level :func:`check_credential` delegates to a
default scope (see :func:`default_scope` /
:func:`set_default_scope`). Surfaces that own their scope
(G23's ``check_fn``, the gateway, the harness) should pass
their own scope explicitly instead of relying on the default.
"""

from __future__ import annotations

import os
from collections.abc import Callable

__all__ = [
    "CredentialScope",
    "check_credential",
    "default_scope",
    "pool_source",
    "scoped_env_source",
    "set_default_scope",
    "vault_source",
]


class CredentialScope:
    """Per-profile credential sources: ``(profile_id, provider)``.

    A source is a callable ``(provider, model) -> str | None``.
    Sources are stored per profile, so a lookup for one profile
    can only ever invoke that profile's sources.
    """

    def __init__(self):
        self._sources: dict[tuple[str, str], Callable[[str, str | None], str | None]] = {}

    def bind(self, profile_id, provider, source):
        """Bind ``source`` to one profile and provider.

        Re-binding the same pair replaces the source (an
        explicit re-bind by the profile owner, not a silent
        shadow: the caller owns the profile). ``source`` must
        be callable.
        """
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError("profile_id must be a non-empty string")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider must be a non-empty string")
        if not callable(source):
            raise ValueError("source must be callable")
        self._sources[(profile_id, provider)] = source
        return source

    def unbind(self, profile_id, provider) -> bool:
        """Remove a binding; True when one was present."""
        return self._sources.pop((profile_id, provider), None) is not None

    def providers(self, profile_id):
        """Providers bound for ``profile_id`` (sorted names)."""
        return sorted(provider for (owner, provider) in self._sources if owner == profile_id)

    def get(self, profile_id, provider, model=None) -> str:
        """Return the key for one profile and provider.

        Raises ``KeyError`` when the profile has no source for
        the provider, or when the source returns nothing. It
        never falls back to another profile.
        """
        if not isinstance(profile_id, str) or not profile_id:
            raise ValueError("profile_id must be a non-empty string")
        if not isinstance(provider, str) or not provider:
            raise ValueError("provider must be a non-empty string")
        source = self._sources.get((profile_id, provider))
        if source is None:
            raise KeyError(
                f"profile {profile_id!r} has no credential source for provider {provider!r}"
            )
        key = source(provider, model)
        if not key or not str(key).strip():
            raise KeyError(
                f"profile {profile_id!r} has no usable credential "
                f"for provider {provider!r}" + (f" and model {model!r}" if model else "")
            )
        return str(key)

    def check_credential(self, profile_id, provider, model=None) -> bool:
        """True when the profile has a usable credential.

        Shared interface with the G23 tool worker (same name and
        signature). Only a missing credential returns False; a
        source that raises anything other than ``KeyError`` /
        ``ValueError`` propagates, so a broken vault is loud
        instead of looking like "no credential".
        """
        try:
            key = self.get(profile_id, provider, model=model)
        except (KeyError, ValueError):
            return False
        return bool(key and key.strip())


def vault_source(profile_id, provider, *, dsn: str | None = None):
    """Build a vault-backed source for one profile and provider.

    Reads ``byok:<provider>`` from the envelope-encrypted vault
    (``puspa.accounts.vault``: master key -> HKDF(profile) ->
    Fernet), scoped to this profile's user id. The vault lookup
    happens per call, so a rotated key is picked up without
    re-binding. Raises ``KeyError`` from the vault when the
    entry is missing (``check_credential`` turns that into
    False); a missing master key raises ``VaultError`` loudly.
    """

    def _source(found_provider, model):
        import sys
        from pathlib import Path

        demo_dir = Path(__file__).resolve().parent.parent.parent / "demo"
        if str(demo_dir) not in sys.path:
            sys.path.insert(0, str(demo_dir))
        from demo.byok_provider import get_provider_key

        return get_provider_key(profile_id, found_provider, dsn=dsn)

    return _source


def pool_source(pool, provider, *, model=None):
    """Build a pool-backed source for one provider.

    Each call acquires the next usable key for
    ``(provider, model)`` from the pool (cooling-down keys are
    skipped by the pool). The model defaults to the ``model``
    bound here, and a per-call model wins when given.
    """

    def _source(found_provider, call_model):
        entry = pool.acquire(found_provider, call_model or model)
        return entry.key

    return _source


def scoped_env_source(env_var: str):
    """Build a source bound to ONE env var for ONE profile.

    The env var is read only when this profile's source is
    invoked, and only for the profile it was bound to — that is
    what makes it scoped instead of a raw global ``os.getenv``
    read. Bind it with :meth:`CredentialScope.bind` for exactly
    the profile that owns the key; other profiles never see it.
    """
    if not isinstance(env_var, str) or not env_var:
        raise ValueError("env_var must be a non-empty string")

    def _source(provider, model):
        value = os.environ.get(env_var)
        return value or None

    return _source


_DEFAULT_SCOPE: CredentialScope | None = None


def default_scope() -> CredentialScope:
    """The module-level default scope (created on first use)."""
    global _DEFAULT_SCOPE
    if _DEFAULT_SCOPE is None:
        _DEFAULT_SCOPE = CredentialScope()
    return _DEFAULT_SCOPE


def set_default_scope(scope: CredentialScope) -> CredentialScope:
    """Replace the default scope (tests / embedding hosts)."""
    global _DEFAULT_SCOPE
    if not isinstance(scope, CredentialScope):
        raise TypeError("scope must be a CredentialScope")
    _DEFAULT_SCOPE = scope
    return scope


def check_credential(profile_id, provider, model=None) -> bool:
    """True when ``profile_id`` has a usable credential.

    Shared interface with the G23 tool worker (same name and
    signature — do NOT rename it). Delegates to the default
    scope; surfaces with their own scope should call
    ``scope.check_credential(...)`` on it directly.
    """
    return default_scope().check_credential(profile_id, provider, model=model)
