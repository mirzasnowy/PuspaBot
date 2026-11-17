"""Pembaca terkarantina (definisi 2.6).

Konten tak tepercaya (halaman marketplace, situs provider, isi email, isi
file) hanya dibaca lewat kelas ini. Aturan karantina:

- Pembaca TIDAK punya alat tulis, TIDAK punya konektor, dan TIDAK mengambil
  jaringan sendiri. Fase 0: ia menerima teks yang sudah diambil (pengambilan
  jaringan ada di ``puspa.sandbox``).
- Keluarannya JSON ber-skema ketat: hanya ``source``, ``judul``, ``angka``,
  dan ``potongan``. Tidak ada teks mentah lain yang lolos.
- Semua bidang teks bebas ditandai data (``[DATA: ...]``) dan dipangkas
  maks 200 karakter, sehingga tidak pernah ditempatkan di posisi instruksi
  dalam prompt agen atau Pelaksana.
- ``source`` harus berasal dari domain yang diizinkan (``allowed_domains``);
  sumber di luar daftar ditolak sebelum teks dibaca.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

__all__ = ["QuarantinedReader", "MAX_CHARS"]

MAX_CHARS = 200

# Angka + satuan yang dikenali. Satuan kosong ("") berarti angka polos.
# "Rp" / "IDR" adalah prefix (Rp125.500); satuan lain umumnya suffix (87 kWh).
_NUMBER_RE = re.compile(
    r"(?:(?P<awalan>rp|idr|rupiah)\s*)?"
    r"(?P<value>\d{1,3}(?:[.,]\d{3})+(?:[.,]\d+)?|\d+(?:[.,]\d+)?)"
    r"(?:\s*(?P<satuan>rp|idr|rupiah|%|persen|kg|gram|g|mg|liter|l|ml|cm|mm|m|km|"
    r"jam|menit|detik|hari|minggu|bulan|tahun|buah|pcs|unit|orang|gb|mb|kb|tb|"
    r"kwh|wh|va|mah|watt|volt|ampere|a|hz|mhz|ghz))?\b",
    re.IGNORECASE,
)


def _mark_data(text: str) -> str:
    """Tandai teks bebas sebagai data dan pangkas ke maks 200 karakter."""
    clean = " ".join(text.split())
    if len(clean) > MAX_CHARS:
        clean = clean[:MAX_CHARS]
    return f"[DATA: {clean}]"


def _domain_of(source: str) -> str | None:
    """Ambil host dari source (URL atau domain polos). None bila tak ada host."""
    s = source.strip()
    if not s:
        return None
    if "://" not in s:
        s = "https://" + s
    try:
        host = urlparse(s).hostname
    except ValueError:
        return None
    return host.lower() if host else None


def _domain_allowed(host: str, allowed_domains: frozenset[str]) -> bool:
    """True bila host sama dengan atau subdomain dari salah satu domain izin."""
    return any(host == d or host.endswith("." + d) for d in allowed_domains)


class QuarantinedReader:
    """Membaca teks tak tepercaya menjadi dict ber-skema ketat."""

    def __init__(self, allowed_domains) -> None:
        self.allowed_domains = frozenset(d.lower().strip() for d in allowed_domains)

    def read_text(self, source: str, text: str) -> dict:
        """Baca ``text`` yang sudah diambil dari ``source``.

        Mengembalikan dict dengan kunci persis:
        ``source``, ``judul`` (maks 200 karakter, ditandai data),
        ``angka`` (list ``{nilai, satuan, konteks}``, konteks maks 200 karakter),
        ``potongan`` (maks 200 karakter, ditandai data).

        Raise ``ValueError`` bila host ``source`` di luar ``allowed_domains``.
        Tidak pernah mengembalikan teks mentah sebagai instruksi.
        """
        host = _domain_of(source)
        if host is not None and not _domain_allowed(host, self.allowed_domains):
            raise ValueError(f"source di luar allowlist pembaca: {source!r}")

        clean = " ".join((text or "").split())
        lines = [ln.strip() for ln in (text or "").splitlines() if ln.strip()]

        title = lines[0] if lines else ""
        excerpt = clean

        number: list[dict] = []
        seen: set[tuple[str, str]] = set()
        for m in _NUMBER_RE.finditer(clean):
            value = m.group("value")
            unit = ((m.group("prefix") or "") or (m.group("unit") or "")).lower()
            key = (value, unit)
            if key in seen:
                continue
            seen.add(key)
            start = max(0, m.start() - 100)
            end = min(len(clean), m.end() + 100)
            context = clean[start:end]
            if len(context) > MAX_CHARS:
                context = context[:MAX_CHARS]
            number.append(
                {
                    "value": value,
                    "unit": unit,
                    "context": _mark_data(context),
                }
            )

        return {
            "source": source,
            "title": _mark_data(title),
            "number": number,
            "excerpt": _mark_data(excerpt),
        }
