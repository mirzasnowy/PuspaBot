"""Request authorization: the single front door for hosting requests.

This module ties the pieces together into one decision function,
``authorize_request``. Google login stays in front: a valid Google
session (``puspa.accounts.user_sessions.validate_session``) or a
valid pairing session (``pairing.py``) authorises any request, from
anywhere. The loopback/service rules below only govern requests
WITHOUT a session.

Decision order (first match wins):

1. Google session valid -> allow (``via="google_session"``).
2. Pairing session valid -> allow (``via="pairing_session"``).
3. Loopback-trusted request (Host + Origin loopback, never a
   browser; see ``loopback.py``):
   - mode ``"single_user"`` (one user, local machine): allow
     (``via="loopback"``).
   - mode ``"service"`` (shared server, the default): allow ONLY
     when (method, path) is on the explicit service allowlist
     (``via="service_allow"``); anything else is denied.
4. Anything else -> deny (``via="denied"``).

The Google session validator is an injected callable
(``session_validator(token) -> user_id``, raising on an invalid
token) because this module must not import Postgres code directly
and must stay testable without a database. The wiring is one line —
see ``docs/hosting-auth.md`` — and the PWA already has the same
validator in ``puspa/pwa/app.py``. When no validator is wired, the
Google-session step is skipped (never assumed valid) and pairing
sessions + loopback rules still apply. A validator that raises is
treated as "no valid session", never as a crash: authorization is
fail-closed.
"""

from __future__ import annotations

from dataclasses import dataclass

from .loopback import loopback_trusted
from .service_allow import DEFAULT_SERVICE_ALLOW, ServiceAllowlist

__all__ = [
    "SERVICE_MODE",
    "SINGLE_USER_MODE",
    "RequestDecision",
    "authorize_request",
]

SERVICE_MODE = "service"
SINGLE_USER_MODE = "single_user"


@dataclass(frozen=True)
class RequestDecision:
    """One authorization verdict."""

    allowed: bool
    reason: str
    via: str  # "google_session" | "pairing_session" | "loopback" | "service_allow" | "denied"
    user_id: str | None = None


def _try_session_validator(session_validator, token):
    """Return the user_id for a valid token, else None. Never raises."""
    if session_validator is None or not token:
        return None
    try:
        user_id = session_validator(token)
    except Exception:
        return None
    if not isinstance(user_id, str) or not user_id.strip():
        return None
    return user_id


def authorize_request(
    method: str,
    path: str,
    headers=None,
    *,
    host: str | None,
    origin: str | None = None,
    session_token: str | None = None,
    pairing_token: str | None = None,
    mode: str = SERVICE_MODE,
    service_allowlist: ServiceAllowlist | None = None,
    session_validator=None,
    pairing_store=None,
) -> RequestDecision:
    """Decide one request. Pure function over its arguments; never
    raises and never touches the network or the database itself.

    ``headers`` is used only for the browser check in
    ``loopback_trusted``. ``session_validator`` is
    ``puspa.accounts.user_sessions.validate_session`` (or a wrapper
    with the same ``(token) -> user_id`` contract). ``pairing_store``
    is a ``PairingStore``; its ``validate_session`` decides pairing
    tokens. ``mode`` is ``"service"`` (shared server, default) or
    ``"single_user"`` (local machine, loopback trusted outright).
    """
    if mode not in (SERVICE_MODE, SINGLE_USER_MODE):
        return RequestDecision(
            allowed=False, reason=f"unknown hosting mode: {mode!r}", via="denied"
        )

    user_id = _try_session_validator(session_validator, session_token)
    if user_id is not None:
        return RequestDecision(
            allowed=True, reason="valid Google session", via="google_session", user_id=user_id
        )

    if pairing_store is not None and pairing_token:
        try:
            record = pairing_store.validate_session(pairing_token)
        except Exception:
            record = None
        if record is not None:
            return RequestDecision(
                allowed=True,
                reason="valid pairing session",
                via="pairing_session",
                user_id=record["user_id"],
            )

    if loopback_trusted(host, origin, headers):
        if mode == SINGLE_USER_MODE:
            return RequestDecision(
                allowed=True, reason="loopback request in single-user mode", via="loopback"
            )
        allowlist = service_allowlist or ServiceAllowlist(DEFAULT_SERVICE_ALLOW)
        if allowlist.allowed(method, path):
            return RequestDecision(
                allowed=True,
                reason="loopback request to an explicitly allowed service path",
                via="service_allow",
            )
        return RequestDecision(
            allowed=False,
            reason="loopback request without a session is restricted to the service allowlist",
            via="denied",
        )

    return RequestDecision(
        allowed=False, reason="no valid session and not a trusted loopback request", via="denied"
    )
