"""Allowlist jaringan sandbox (definisi 2.6).

Fase 0: container sandbox dijalankan dengan ``--network none`` (tidak ada
jaringan sama sekali — lebih ketat dari allowlist, dan itu disengaja:
fail-closed). Allowlist di sini adalah daftar domain yang *boleh* dibuka
ketika jalur jaringan sandbox diaktifkan nanti, dan sudah dipakai sekarang
untuk memvalidasi URL sebelum diteruskan ke sandbox / pembaca.

Cakupan default (CONTRACTS.md):
- API model (OpenAI, Anthropic, Google Gemini, OpenRouter, DeepSeek, Groq,
  Mistral, Cohere, Together, Fireworks),
- API konektor Google (Gmail, Drive, Calendar, OAuth),
- marketplace (tokopedia.com, shopee.co.id, blibli.com),
- provider tagihan (pln.co.id + situs PDAM).

Localhost hanya untuk test double lewat flag eksplisit
``allow_test_localhost=True``; tidak pernah di jalur prod.
"""

from __future__ import annotations

from urllib.parse import urlparse

__all__ = ["DEFAULT_ALLOWLIST", "Allowlist", "is_allowed"]

# --- kelompok domain default -------------------------------------------------

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
        # Situs PDAM berbeda-beda per daerah; yang umum dicantumkan, sisanya
        # ditangkap aturan label "pdam" di Allowlist.is_allowed.
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
    """Ambil host dari URL atau domain polos. None bila tak ada host."""
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
    """Daftar domain yang boleh diakses. Pencocokan mencakup subdomain."""

    def __init__(self, domains=None, *, allow_test_localhost: bool = False) -> None:
        self._domains = frozenset(
            d.lower().strip() for d in (DEFAULT_ALLOWLIST if domains is None else domains)
        )
        self.allow_test_localhost = allow_test_localhost

    @property
    def domains(self) -> frozenset[str]:
        return self._domains

    def is_allowed(self, url_or_host: str) -> bool:
        """True bila host (atau host dari URL) ada di allowlist.

        Aturan:
        - cocok persis atau subdomain (``www.tokopedia.com`` cocok
          ``tokopedia.com``; ``tokopedia.com.evil.com`` TIDAK cocok),
        - label berawalan ``pdam`` pada host TLD Indonesia (``.id``)
          diizinkan (situs PDAM per daerah),
        - localhost / 127.0.0.1 hanya bila ``allow_test_localhost=True``.
        """
        host = _host_of(url_or_host)
        if host is None:
            return False
        if host in ("localhost", "127.0.0.1", "::1"):
            return self.allow_test_localhost
        if any(host == d or host.endswith("." + d) for d in self._domains):
            return True
        # Situs PDAM per daerah memakai pola nama "pdam<kota>" (mis.
        # pdamkotamakassar.go.id) atau "pdam-<nama>.co.id". Heuristik:
        # label berawalan "pdam" + TLD Indonesia (.id) diizinkan; "pdam"
        # di TLD lain (mis. pdamphishing.com) tetap ditolak.
        if host.endswith(".id") and any(
            label.startswith("pdam") for label in host.split(".")
        ):
            return True
        return False


def is_allowed(url_or_host: str, allowlist: Allowlist | None = None) -> bool:
    """Jalan pintas: cek terhadap allowlist default (atau yang diberikan)."""
    return (allowlist or Allowlist()).is_allowed(url_or_host)
