"""Loopback trust for the multi-tenant hosting server.

Pattern (DuskBot ``server/request-auth.ts``, rewritten as Puspa code):
loopback trust is granted only when BOTH the Host header and the
Origin header resolve to a loopback address, and a browser NEVER gets
it. The browser rule matters because a browser on the same machine
could otherwise be tricked into calling the local server on behalf of
a web page; the loopback interface alone is not a trust boundary.

How a browser is recognised here: real browsers always send a
``Sec-Fetch-Mode`` header on modern requests, and every major browser
sends a ``Mozilla/`` user agent. Either marker marks the request as a
browser request, which is denied loopback trust. This is a heuristic,
and it is documented as one: a non-browser client that spoofs a
browser user agent is denied too (fail-closed direction), while a
client that sends neither marker is treated as a non-browser client.
The trust boundary is therefore the loopback interface PLUS the
session/service rules in ``gateway.py``, never the user-agent string
alone. A browser cannot strip ``Sec-Fetch-Mode`` from its own
requests, so a real browser can never gain loopback trust.

Loopback hosts recognised: ``localhost`` (and ``*.localhost``),
``127.0.0.0/8``, and ``::1``. Ports, IPv6 brackets, and mixed case are
stripped/normalised before the check. A missing Origin is treated as
"no cross-origin claim": it passes the origin half of the check (a
``curl``-style non-browser client sends no Origin), but the request
must still be non-browser and loopback-hosted.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

__all__ = [
    "is_browser_request",
    "is_loopback_host",
    "is_loopback_origin",
    "loopback_trusted",
]


def _strip_port(host: str) -> str:
    """Strip a port (and IPv6 brackets) from a Host value."""
    host = host.strip()
    if host.startswith("["):
        # "[::1]:8080" or "[::1]"
        end = host.find("]")
        if end == -1:
            return host
        return host[1:end]
    if ":" in host and host.count(":") == 1:
        # "127.0.0.1:8080" -> "127.0.0.1". A bare IPv6 literal without
        # brackets contains more than one colon and is not a valid
        # Host value; it falls through and fails the loopback check.
        return host.rsplit(":", 1)[0]
    return host


def _is_loopback_name(name: str) -> bool:
    """True when a bare hostname/IP string is a loopback address."""
    name = name.strip().lower()
    if name.endswith("%"):
        return False
    if "%" in name:  # strip a zone id such as "%lo0"
        name = name.split("%", 1)[0]
    if name == "localhost" or name.endswith(".localhost"):
        return True
    try:
        address = ipaddress.ip_address(name)
    except ValueError:
        return False
    return address.is_loopback


def is_loopback_host(host: str | None) -> bool:
    """True when a Host header value is a loopback host (port ignored)."""
    if not host or not isinstance(host, str):
        return False
    return _is_loopback_name(_strip_port(host))


def is_loopback_origin(origin: str | None) -> bool:
    """True when an Origin header value has a loopback host.

    Only ``http``/``https``/``ws``/``wss`` origins are considered; any
    other scheme, an unparseable value, or a missing value returns
    False. Callers that accept a missing Origin (no cross-origin
    claim) handle ``None`` themselves; see ``loopback_trusted``.
    """
    if not origin or not isinstance(origin, str):
        return False
    try:
        parsed = urlparse(origin.strip())
    except ValueError:
        return False
    if parsed.scheme.lower() not in ("http", "https", "ws", "wss"):
        return False
    if not parsed.hostname:
        return False
    return _is_loopback_name(parsed.hostname)


def is_browser_request(headers) -> bool:
    """True when the headers look like a real browser request.

    ``headers`` is a mapping with case-insensitive lookup handled
    here (keys are lowercased). A request counts as a browser request
    when it carries ``Sec-Fetch-Mode`` (every modern browser sends
    it) or a ``Mozilla/`` user agent (every major browser sends it).
    """
    if not headers:
        return False
    lowered = {str(key).lower(): value for key, value in dict(headers).items()}
    if "sec-fetch-mode" in lowered:
        return True
    user_agent = str(lowered.get("user-agent", "")).lower()
    return "mozilla/" in user_agent


def loopback_trusted(host: str | None, origin: str | None, headers=None) -> bool:
    """True only when Host AND Origin are loopback and not a browser.

    A missing/empty Origin passes the origin half (no cross-origin
    claim); a present Origin must itself be loopback. Browsers never
    get trust, even from loopback.
    """
    if not is_loopback_host(host):
        return False
    if origin and not is_loopback_origin(origin):
        return False
    return not is_browser_request(headers)
