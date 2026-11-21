"""Domain allowlist for the product's own readers (public).

Which network domains Puspa itself may fetch when it reads a price,
verifies a claim, or re-reads a source. This is product policy, not
sandbox machinery: the domains are the ones the product is built to
read (model APIs, Google connector APIs, the three marketplaces, the
bill providers), so the list lives in public code next to the other
permission policy in :mod:`puspa.policy`.

Matching rules (the behaviour the readers and the verifier rely on):

- an exact host or any of its subdomains matches
  (``www.tokopedia.com`` matches ``tokopedia.com``;
  ``tokopedia.com.evil.com`` does not),
- a label starting with ``pdam`` on an Indonesian host (``.id``) is
  allowed, because each region's water utility has its own site,
- localhost is allowed only through the explicit
  ``allow_test_localhost=True`` flag, never on the normal path.
"""

from __future__ import annotations

from urllib.parse import urlparse

__all__ = ["DEFAULT_ALLOWLIST", "Allowlist", "is_allowed"]

# --- default domain groups --------------------------------------------------

MODEL_API_DOMAINS = frozenset(
    {
        "api.openai.com",
        "api.anthropic.com",
        "generativelanguage.googleapis.com",
        "api.openrouter.ai",
        "openrouter.ai",
        "api.deepseek.com",
        "api.groq.com",
        "api.mistral.ai",
        "api.cohere.ai",
        "api.together.xyz",
        "api.fireworks.ai",
    }
)

CONNECTOR_API_DOMAINS = frozenset(
    {
        "gmail.googleapis.com",
        "drive.googleapis.com",
        "www.googleapis.com",
        "calendar.googleapis.com",
        "oauth2.googleapis.com",
        "accounts.google.com",
    }
)

MARKETPLACE_DOMAINS = frozenset(
    {
        "tokopedia.com",
        "shopee.co.id",
        "blibli.com",
    }
)

BILLING_DOMAINS = frozenset(
    {
        "pln.co.id",
        # Water utilities differ per region; the common ones are
        # listed, the rest are caught by the "pdam" label rule in
        # Allowlist.is_allowed.
        "pamjaya.co.id",
        "pdamkotabogor.go.id",
        "pdamtirtawening.co.id",
        "pdamkotabandung.co.id",
        "pdamsurabaya.co.id",
    }
)

DEFAULT_ALLOWLIST = frozenset(
    MODEL_API_DOMAINS | CONNECTOR_API_DOMAINS | MARKETPLACE_DOMAINS | BILLING_DOMAINS
)


def _host_of(value: str) -> str | None:
    """Take the host from a URL or a bare domain. None when there is none."""
    s = (value or "").strip().lower()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    try:
        host = urlparse(s).hostname
    except ValueError:
        return None
    return host


class Allowlist:
    """A list of domains that may be read. Matching covers subdomains."""

    def __init__(self, domains=None, *, allow_test_localhost: bool = False) -> None:
        self._domains = frozenset(
            d.lower().strip() for d in (DEFAULT_ALLOWLIST if domains is None else domains)
        )
        self.allow_test_localhost = allow_test_localhost

    @property
    def domains(self) -> frozenset[str]:
        return self._domains

    def is_allowed(self, url_or_host: str) -> bool:
        """True when the host (or the host of the URL) is on the list.

        Rules:
        - exact match or subdomain (``www.tokopedia.com`` matches
          ``tokopedia.com``; ``tokopedia.com.evil.com`` does NOT),
        - a label starting with ``pdam`` on an Indonesian host
          (``.id``) is allowed (regional water-utility sites),
        - localhost / 127.0.0.1 only when ``allow_test_localhost=True``.
        """
        host = _host_of(url_or_host)
        if host is None:
            return False
        if host in ("localhost", "127.0.0.1", "::1"):
            return self.allow_test_localhost
        if any(host == d or host.endswith("." + d) for d in self._domains):
            return True
        # Regional water utilities use "pdam<city>" names (for
        # example pdamkotamakassar.go.id) or "pdam-<name>.co.id".
        # Heuristic: a label starting with "pdam" plus an Indonesian
        # TLD (.id) is allowed; "pdam" on another TLD (for example
        # pdamphishing.com) stays rejected.
        return bool(
            host.endswith(".id") and any(label.startswith("pdam") for label in host.split("."))
        )


def is_allowed(url_or_host: str, allowlist: Allowlist | None = None) -> bool:
    """Shortcut: check against the default allowlist (or the given one)."""
    return (allowlist or Allowlist()).is_allowed(url_or_host)
