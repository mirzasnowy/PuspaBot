"""Turn phase: API error handling (G21).

Two rules live here, and only here:

1. Auth errors refresh credentials FIRST. When a tier fails
   with an auth error (401, or 403 with an auth signal), the
   per-tier call is wrapped by :func:`wrap_with_auth_refresh`:
   the shared OAuth refresher (``puspa.router.oauth`` — the one
   the router already owns, never a second refresher written
   here) is asked for a fresh token, the same tier is retried
   once, and only if that still fails auth does the failure
   reach the fallback executor, which moves to the next tier
   (``auth_error`` is retryable in ``puspa.router.fallback``).
   The refresh-then-retry only helps when the per-tier call
   resolves its credentials on every call — the built-in
   OpenRouter adapter does (``resolve_api_key`` per call), so a
   refreshed token is picked up by the retry; an adapter with a
   fixed key baked in at build time cannot benefit, and that
   limit is stated in the retry note.
2. Every other failure is classified, never swallowed:
   :func:`describe_failure` turns a provider failure into the
   one-line human-readable reason the turn result carries.

The refresher is injected (tests pass a fake); the default is
the router's shared refresher from ``puspa.router.router``.
"""

from __future__ import annotations

from puspa.router.fallback import AuthError, ProviderFailure, classify_failure

__all__ = [
    "describe_failure",
    "is_auth_failure",
    "wrap_with_auth_refresh",
]


def is_auth_failure(status: int | None, message: str = "") -> bool:
    """True when a failure is an authentication failure."""
    return classify_failure(status, message) == "auth_error"


def describe_failure(failure: BaseException) -> str:
    """One human-readable line for a provider failure.

    Names the provider when the failure carries one, the status
    when known, and the kind — so a turn that ends in failure
    can say "the first provider rate-limited the request and the
    second returned a server error", not just "it failed".
    """
    if isinstance(failure, ProviderFailure):
        provider = getattr(failure, "provider", None)
        where = f"{provider} " if provider else ""
        status = f" (HTTP {failure.status})" if failure.status is not None else ""
        return f"{where}{failure.message}{status} [{failure.kind}]"
    return f"{type(failure).__name__}: {failure}"


def wrap_with_auth_refresh(
    call_fn, refresher=None, *, provider_of=None, provider: str | None = None
):
    """Wrap a per-tier call with refresh-first auth handling.

    ``call_fn(entry)`` is the tier call. ``provider_of`` maps a
    chain entry to its provider name for the refresh (or pass a
    fixed ``provider`` when every tier shares one). On an auth
    failure the wrapper asks ``refresher.get_valid_token(provider)``
    for a fresh token and retries the same tier once. Outcomes:

    - refresh + retry succeed: the retry's value is returned;
      the caller never knows auth failed.
    - refresh succeeds but the retry still fails auth: an
      ``AuthError`` is raised, and the fallback executor moves
      to the next tier with the audit trail intact.
    - refresh fails (dead refresh token, no registration): an
      ``AuthError`` naming the refresh failure is raised — the
      chain moves on instead of stalling on a dead credential.
    - no refresher was given: the auth failure is raised as an
      ``AuthError`` immediately (the chain still moves on).

    Non-auth failures pass through untouched. The wrapper never
    logs, prints, or stores a token value: the refresher returns
    the token to the credential-resolving call, this wrapper only
    records that a refresh happened (``refreshed`` on the raised
    error when one was attempted).
    """

    def wrapped(entry):
        entry_provider = (
            provider_of(entry)
            if provider_of is not None
            else (provider if provider is not None else getattr(entry, "provider", ""))
        )
        try:
            return call_fn(entry)
        except BaseException as exc:
            failure = _as_provider_failure(exc)
            if failure is None or failure.kind != "auth_error":
                raise
            refreshed = False
            if refresher is not None:
                try:
                    refresher.get_valid_token(entry_provider)
                    refreshed = True
                except Exception as refresh_exc:
                    error = AuthError(
                        f"authentication failed for {entry_provider} and the "
                        f"credential refresh failed: {refresh_exc}",
                        status=failure.status,
                    )
                    error.refreshed = False
                    raise error from refresh_exc
            try:
                return call_fn(entry)
            except BaseException as retry_exc:
                retry_failure = _as_provider_failure(retry_exc)
                if retry_failure is not None and retry_failure.kind == "auth_error":
                    error = AuthError(
                        f"authentication failed for {entry_provider} even after "
                        "a credential refresh",
                        status=retry_failure.status,
                    )
                    error.refreshed = refreshed
                    raise error from retry_exc
                raise

    return wrapped


def _as_provider_failure(exc: BaseException) -> ProviderFailure | None:
    """Normalise an exception to a ``ProviderFailure``, or None.

    A ``ProviderFailure`` passes through. Any other exception
    with a ``status`` / ``status_code`` attribute is classified
    from it (the same rule as the fallback executor). Anything
    else is not a provider failure at all — the wrapper must not
    treat a programming error as an auth problem.
    """
    if isinstance(exc, ProviderFailure):
        return exc
    status = getattr(exc, "status", None)
    if status is None:
        status = getattr(exc, "status_code", None)
    if isinstance(status, int):
        kind = classify_failure(status, str(exc))
        failure = ProviderFailure(str(exc), status=status)
        failure.kind = kind
        return failure
    return None
