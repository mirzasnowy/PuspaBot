"""Opt-in switch for anonymous usage telemetry (AST-09).

The pattern follows AstrBot's ``ASTRBOT_DISABLE_METRICS`` idea
(an environment flag plus a settings toggle) with the polarity
reversed: PuspaBot telemetry is OFF by default for self-host,
and only sends when it is switched on explicitly.

Precedence, highest first:

1. The environment flag ``PUSPA_TELEMETRY`` — an operator-level
   override. ``1`` / ``true`` / ``yes`` / ``on`` forces telemetry
   on; ``0`` / ``false`` / ``no`` / ``off`` forces it off, even
   when the Settings toggle is on. Unset means "no override".
2. The per-user Settings toggle (stored by
   :mod:`puspa.telemetry.settings`).
3. The default: off.

An unknown flag value raises ``ValueError`` instead of silently
meaning off: a typo in a privacy switch must fail loudly.

The destination is configured separately with
``PUSPA_TELEMETRY_ENDPOINT``. Puspa ships no telemetry server of
its own, so with no endpoint configured nothing is ever sent,
even when telemetry is on — an operator points the endpoint at
their own collector. The endpoint must be an http(s) URL with a
host and no credentials in it.
"""

from __future__ import annotations

import os
from urllib.parse import urlparse

__all__ = [
    "ENDPOINT_ENV_VAR",
    "ENV_VAR",
    "endpoint",
    "env_flag",
    "parse_endpoint",
    "parse_env_flag",
    "resolve_enabled",
]

#: Environment flag that overrides the Settings toggle.
ENV_VAR = "PUSPA_TELEMETRY"

#: Environment variable holding the collector endpoint.
ENDPOINT_ENV_VAR = "PUSPA_TELEMETRY_ENDPOINT"

#: Flag spellings that switch telemetry on.
_ON_VALUES = ("1", "true", "yes", "on")

#: Flag spellings that switch telemetry off.
_OFF_VALUES = ("0", "false", "no", "off")


def parse_env_flag(raw: str | None) -> bool | None:
    """Parse one ``PUSPA_TELEMETRY`` value.

    Returns ``True`` / ``False`` for a known spelling, ``None``
    when the flag is unset or blank (no override). Raises
    ``ValueError`` for anything else, so a typo never silently
    decides a privacy switch.
    """
    if raw is None:
        return None
    cleaned = raw.strip().lower()
    if not cleaned:
        return None
    if cleaned in _ON_VALUES:
        return True
    if cleaned in _OFF_VALUES:
        return False
    raise ValueError(
        f"{ENV_VAR} must be one of {', '.join(_ON_VALUES + _OFF_VALUES)} (or unset), not {raw!r}"
    )


def env_flag() -> bool | None:
    """The operator's environment override, or ``None`` when unset."""
    return parse_env_flag(os.environ.get(ENV_VAR))


def parse_endpoint(raw: str | None) -> str | None:
    """Validate one ``PUSPA_TELEMETRY_ENDPOINT`` value.

    Returns the stripped URL, or ``None`` when unset or blank
    (no collector configured: nothing is ever sent). Only
    http(s) URLs with a host are accepted, and userinfo is
    refused so credentials cannot hide in the URL. Raises
    ``ValueError`` for anything else.
    """
    if raw is None:
        return None
    cleaned = raw.strip()
    if not cleaned:
        return None
    parsed = urlparse(cleaned)
    if parsed.scheme not in ("http", "https"):
        raise ValueError(
            f"telemetry endpoint {cleaned!r} must use http or https,"
            f" not {parsed.scheme or '(no scheme)'!r}"
        )
    if not parsed.hostname:
        raise ValueError(f"telemetry endpoint {cleaned!r} has no host")
    if parsed.username or parsed.password:
        raise ValueError("telemetry endpoint must not contain credentials (userinfo)")
    return cleaned


def endpoint() -> str | None:
    """The configured collector endpoint, or ``None`` when unset."""
    return parse_endpoint(os.environ.get(ENDPOINT_ENV_VAR))


def resolve_enabled(*, env: bool | None = None, stored: bool = False) -> bool:
    """The effective telemetry switch.

    The environment flag wins when it is set (either way); the
    stored Settings toggle decides otherwise. The default is
    off: both unset means no telemetry.
    """
    if env is not None:
        return bool(env)
    return bool(stored)
