"""OAuth login flows (PKCE, device flow, anonymous) (G22).

Pattern studied, then rewritten as Puspa's own code (see the
G22 section of the repo-comparison report): hermes-agent ships
OAuth in ``hermes_cli/auth*.py`` (PKCE, device flow, anonymous
auth). No hermes-agent code is copied. The flows here are the
standard OAuth 2.0 / PKCE shapes (RFC 6749, RFC 7636, RFC 8628)
plus Puspa's existing Google login (``puspa/auth/google_oauth.py``,
authorization code + PKCE S256), generalised to any provider.

Every flow is a testable pure function over an injected
transport — no live credential and no live call is made here:

- PKCE: :func:`generate_code_verifier`,
  :func:`code_challenge_s256`,
  :func:`build_authorization_url` (returns the URL plus the
  state / verifier / challenge), :func:`exchange_code` (code
  -> token set, over an injected ``http_post``).
- Device flow: :func:`start_device_flow` and
  :func:`poll_device_token`. Polling takes an injectable
  ``sleep`` callable, so tests never wait on a real interval.
- Anonymous auth: :func:`anonymous_auth` — the documented
  fallback when a provider allows unauthenticated use. It
  carries NO credential: its ``authorization_header`` is empty
  and its token value is None, so it can never be mistaken for
  a real login.

``http_post`` is a callable ``(url, data_dict) -> response``
where the response is either a dict (parsed JSON body) or an
object with ``status_code`` and a ``json()`` method (an httpx
response). An ``"error"`` field in the body, or a non-2xx
status, raises :class:`OAuthError` naming the provider and the
error code — never the token value. ``TokenSet.__repr__``
shows only the provider and expiry, never the tokens.
"""

from __future__ import annotations

import base64
import hashlib
import secrets
import time
from dataclasses import dataclass
from urllib.parse import urlencode

__all__ = [
    "AnonymousAuth",
    "DeviceCode",
    "DeviceFlowError",
    "OAuthError",
    "TokenSet",
    "anonymous_auth",
    "build_authorization_url",
    "code_challenge_s256",
    "exchange_code",
    "exchange_device_code",
    "generate_code_verifier",
    "generate_state",
    "poll_device_token",
    "refresh_access_token",
    "start_device_flow",
]


class OAuthError(Exception):
    """An OAuth flow failed (bad code, denied, expired, ...)."""


class DeviceFlowError(OAuthError):
    """A device flow failed (denied, expired, or timed out)."""


def _b64url(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def generate_code_verifier(*, length: int = 64) -> str:
    """Random PKCE code verifier (43-128 URL-safe chars).

    RFC 7636 requires 43-128 characters from the unreserved set;
    ``token_urlsafe`` gives exactly that alphabet.
    """
    if length < 43 or length > 128:
        raise ValueError("code verifier length must be 43-128")
    # token_urlsafe(n) yields ~4/3*n chars; ask for enough bytes
    # and trim to the requested length.
    return secrets.token_urlsafe(96)[:length]


def code_challenge_s256(verifier: str) -> str:
    """S256 code challenge for a verifier (RFC 7636)."""
    if not isinstance(verifier, str) or not verifier:
        raise ValueError("verifier must be a non-empty string")
    digest = hashlib.sha256(verifier.encode()).digest()
    return _b64url(digest)


def generate_state(*, length: int = 32) -> str:
    """Random ``state`` value (CSRF protection for the redirect)."""
    if length < 16:
        raise ValueError("state must be at least 16 characters")
    return secrets.token_urlsafe(48)[:length]


@dataclass(frozen=True)
class AuthorizationRequest:
    """An authorization URL plus the values to verify on return.

    Keep ``verifier`` and ``state``: the callback must present
    the same ``state``, and the code exchange must present the
    same ``verifier``. Neither value is a credential on its own,
    but neither belongs in a log either.
    """

    url: str
    state: str
    code_verifier: str
    code_challenge: str

    def __repr__(self) -> str:  # pragma: no cover - formatting only
        return (
            f"AuthorizationRequest(url={self.url!r}, "
            f"state={self.state!r}, code_verifier='[hidden]', "
            f"code_challenge={self.code_challenge!r})"
        )


def build_authorization_url(
    auth_url,
    *,
    client_id,
    redirect_uri,
    scope=(),
    state=None,
    code_verifier=None,
    extra_params=None,
) -> AuthorizationRequest:
    """Build the authorization URL for the code + PKCE flow.

    Returns an :class:`AuthorizationRequest` with the URL, a
    fresh ``state`` (or the given one), and a fresh verifier /
    S256 challenge (or the given verifier). ``scope`` is a list
    of scope strings, joined with spaces.
    """
    if not auth_url or not client_id or not redirect_uri:
        raise ValueError("auth_url, client_id, and redirect_uri are required")
    state = state if state is not None else generate_state()
    verifier = code_verifier if code_verifier is not None else generate_code_verifier()
    challenge = code_challenge_s256(verifier)
    params = {
        "response_type": "code",
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
    }
    if scope:
        params["scope"] = " ".join(scope)
    if extra_params:
        params.update(extra_params)
    return AuthorizationRequest(
        url=f"{auth_url}?{urlencode(params)}",
        state=state,
        code_verifier=verifier,
        code_challenge=challenge,
    )


@dataclass
class TokenSet:
    """An OAuth token set: access + refresh token + expiry.

    ``expires_at`` is a monotonic timestamp (seconds, from
    ``time.monotonic``) when known, or None for a token with no
    expiry. ``__repr__`` never shows either token value.
    """

    provider: str
    access_token: str
    refresh_token: str | None = None
    expires_at: float | None = None
    scope: tuple = ()

    def __repr__(self) -> str:  # pragma: no cover - formatting only
        return (
            f"TokenSet(provider={self.provider!r}, "
            f"access_token='[hidden]', "
            f"refresh_token={'[hidden]' if self.refresh_token else None}, "
            f"expires_at={self.expires_at!r})"
        )

    def is_expired(self, *, now: float | None = None, leeway_seconds: float = 0.0) -> bool:
        """True when the token is expired (or expires in leeway)."""
        if self.expires_at is None:
            return False
        now = time.monotonic() if now is None else now
        return now + leeway_seconds >= self.expires_at

    def authorization_header(self) -> dict:
        """Bearer header for this token set."""
        return {"Authorization": f"Bearer {self.access_token}"}


def _post(http_post, url, data, *, provider):
    """Call the injected transport and return the parsed body.

    Accepts a dict body or an httpx-like response object.
    Raises :class:`OAuthError` on an ``"error"`` field or a
    non-2xx status, naming provider + error code only.
    """
    response = http_post(url, data)
    if isinstance(response, dict):
        body, status = response, 200
    else:
        status = getattr(response, "status_code", 200)
        try:
            body = response.json()
        except Exception as exc:
            raise OAuthError(
                f"{provider}: token endpoint returned an unreadable reply (HTTP {status})"
            ) from exc
    if not isinstance(body, dict):
        raise OAuthError(f"{provider}: token endpoint returned a reply that is not a JSON object")
    error = body.get("error")
    if error or not (200 <= status < 300):
        raise OAuthError(f"{provider}: token endpoint failed ({error or f'HTTP {status}'})")
    return body


def _token_set_from_body(provider, body) -> TokenSet:
    access_token = body.get("access_token")
    if not access_token or not str(access_token).strip():
        raise OAuthError(f"{provider}: token endpoint returned no access_token")
    expires_in = body.get("expires_in")
    expires_at = None
    if expires_in is not None:
        try:
            expires_at = time.monotonic() + float(expires_in)
        except (TypeError, ValueError) as exc:
            raise OAuthError(f"{provider}: token endpoint returned an unusable expires_in") from exc
    scope = body.get("scope")
    return TokenSet(
        provider=provider,
        access_token=str(access_token),
        refresh_token=(str(body["refresh_token"]) if body.get("refresh_token") else None),
        expires_at=expires_at,
        scope=tuple(str(scope).split()) if scope else (),
    )


def exchange_code(
    token_url,
    code,
    *,
    provider,
    client_id,
    redirect_uri,
    code_verifier,
    http_post,
    client_secret=None,
) -> TokenSet:
    """Exchange an authorization code for a token set (PKCE).

    Standard authorization-code grant (RFC 6749 section 4.1)
    with the PKCE verifier (RFC 7636). ``http_post`` is the
    injected transport; no live call is made here.
    """
    if not code:
        raise ValueError("code must be a non-empty string")
    data = {
        "grant_type": "authorization_code",
        "code": code,
        "client_id": client_id,
        "redirect_uri": redirect_uri,
        "code_verifier": code_verifier,
    }
    if client_secret is not None:
        data["client_secret"] = client_secret
    body = _post(http_post, token_url, data, provider=provider)
    return _token_set_from_body(provider, body)


def refresh_access_token(
    token_url, refresh_token, *, provider, client_id, http_post, client_secret=None
) -> TokenSet:
    """Refresh an access token (RFC 6749 section 6).

    Returns the new token set; when the endpoint returns a new
    refresh token it replaces the old one, otherwise the old
    refresh token is kept (the caller holds it).
    """
    if not refresh_token:
        raise ValueError("refresh_token must be a non-empty string")
    data = {
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
        "client_id": client_id,
    }
    if client_secret is not None:
        data["client_secret"] = client_secret
    body = _post(http_post, token_url, data, provider=provider)
    token_set = _token_set_from_body(provider, body)
    if token_set.refresh_token is None:
        token_set.refresh_token = refresh_token
    return token_set


@dataclass(frozen=True)
class DeviceCode:
    """A device-flow code: show the user code + verification URI."""

    provider: str
    device_code: str
    user_code: str
    verification_uri: str
    expires_in: int
    interval: int

    def __repr__(self) -> str:  # pragma: no cover - formatting only
        return (
            f"DeviceCode(provider={self.provider!r}, "
            f"device_code='[hidden]', user_code={self.user_code!r}, "
            f"verification_uri={self.verification_uri!r}, "
            f"expires_in={self.expires_in}, interval={self.interval})"
        )


def start_device_flow(device_code_url, *, provider, client_id, scope=(), http_post) -> DeviceCode:
    """Start a device flow (RFC 8628): get user code + URI."""
    if not device_code_url or not client_id:
        raise ValueError("device_code_url and client_id are required")
    data = {"client_id": client_id}
    if scope:
        data["scope"] = " ".join(scope)
    body = _post(http_post, device_code_url, data, provider=provider)
    device_code = body.get("device_code")
    user_code = body.get("user_code")
    verification_uri = body.get("verification_uri") or body.get("verification_url")
    if not device_code or not user_code or not verification_uri:
        raise OAuthError(f"{provider}: device endpoint returned an incomplete device code")
    try:
        expires_in = int(body.get("expires_in") or 0)
        interval = int(body.get("interval") or 5)
    except (TypeError, ValueError) as exc:
        raise OAuthError(f"{provider}: device endpoint returned unusable expiry/interval") from exc
    return DeviceCode(
        provider=provider,
        device_code=str(device_code),
        user_code=str(user_code),
        verification_uri=str(verification_uri),
        expires_in=expires_in,
        interval=max(1, interval),
    )


def exchange_device_code(token_url, device_code, *, provider, client_id, http_post) -> TokenSet:
    """One device-code token poll (RFC 8628 section 3.4).

    Returns the token set on success. Raises
    :class:`OAuthError` with ``authorization_pending`` while the
    user has not finished, and :class:`DeviceFlowError` on
    ``access_denied`` / ``expired_token`` (terminal).
    """
    data = {
        "grant_type": "urn:ietf:params:oauth:grant-type:device_code",
        "device_code": device_code,
        "client_id": client_id,
    }
    try:
        body = _post(http_post, token_url, data, provider=provider)
    except OAuthError as exc:
        message = str(exc)
        if "expired_token" in message:
            raise DeviceFlowError(f"{provider}: device code expired; start a new flow") from exc
        if "access_denied" in message:
            raise DeviceFlowError(f"{provider}: user denied the device login") from exc
        raise
    return _token_set_from_body(provider, body)


def poll_device_token(
    token_url, device_code, *, provider, client_id, http_post, max_attempts: int = 60, sleep=None
) -> TokenSet:
    """Poll the device flow until the user finishes (or it ends).

    ``sleep`` is an injectable ``(seconds) -> None`` (default
    ``time.sleep``); tests inject a no-op and count calls.
    ``max_attempts`` bounds the loop: running out raises
    :class:`DeviceFlowError` (timeout), and a ``slow_down``
    error from the endpoint lengthens the interval for the
    next poll. The device code's own expiry is the caller's
    responsibility: keep ``max_attempts * interval`` below
    ``expires_in``.
    """
    if max_attempts < 1:
        raise ValueError("max_attempts must be at least 1")
    sleep_fn = sleep if sleep is not None else time.sleep
    interval = 5
    if isinstance(device_code, DeviceCode):
        interval = device_code.interval
        code_value = device_code.device_code
    else:
        code_value = str(device_code)
    last_error = None
    for _attempt in range(max_attempts):
        try:
            return exchange_device_code(
                token_url, code_value, provider=provider, client_id=client_id, http_post=http_post
            )
        except DeviceFlowError:
            raise
        except OAuthError as exc:
            message = str(exc)
            if "slow_down" in message:
                interval += 5
                last_error = exc
            elif "authorization_pending" in message:
                last_error = exc
            else:
                raise
        sleep_fn(interval)
    raise DeviceFlowError(
        f"{provider}: device login timed out after {max_attempts} "
        f"polls ({last_error or 'no reply'})"
    )


@dataclass(frozen=True)
class AnonymousAuth:
    """Anonymous auth: the documented no-credential fallback.

    Some providers allow unauthenticated use (public models,
    local endpoints). This object represents that explicitly:
    it carries NO token (``access_token`` is None) and sends NO
    Authorization header, so a caller can tell "anonymous" from
    "logged in" without guessing, and no fake credential is ever
    invented.
    """

    provider: str
    note: str = (
        "anonymous access: no credential is sent; only "
        "providers that allow unauthenticated use accept it"
    )

    def authorization_header(self) -> dict:
        """No header: anonymous calls send no credential."""
        return {}

    @property
    def access_token(self) -> None:
        return None


def anonymous_auth(provider) -> AnonymousAuth:
    """Build the anonymous-auth fallback for ``provider``."""
    if not isinstance(provider, str) or not provider:
        raise ValueError("provider must be a non-empty string")
    return AnonymousAuth(provider=provider)
