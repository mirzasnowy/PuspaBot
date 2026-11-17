"""Penjaga prompt injection (definisi 2.6, lapis tambahan).

Pertahanan utama Puspa bersifat struktural (karantina di reader.py, token
izin, gerbang tunggal). Modul ini hanya menandai konten yang *terlihat*
seperti instruksi, supaya tidak pernah lolos sebagai perintah.

Aturan:
- ``scan(text)`` mengembalikan daftar Finding tanpa mengubah teks.
- ``mark(text)`` membungkus span mencurigakan sebagai ``[DATA: ...]``.
- Tidak ada akses jaringan, file, atau konektor di sini: fungsi murni.
"""

from __future__ import annotations

import re
from dataclasses import dataclass

__all__ = ["Finding", "scan", "mark"]

_LEVEL_RANK = {"low": 0, "in_progress": 1, "high": 2}


@dataclass(frozen=True)
class Finding:
    """Satu span teks yang terlihat seperti instruksi, bukan data."""

    span: str
    pattern: str
    level: str  # "rendah" | "sedang" | "tinggi"


# Pola (regex, case-insensitive). "pola" adalah nama yang dibaca manusia.
# Tingkat "tinggi": perintah langsung ke agen untuk bertindak / membocorkan
# rahasia / mengabaikan instruksi. "sedang": imperatif umum + URL.
# "rendah": kata pemicu urgensi / ajakan menghubungi pihak luar.
_PATTERNS: list[tuple[str, str, str]] = [
    # --- tinggi: pengambilalihan instruksi ---
    (r"abaikan\s+(semua\s+)?instruksi", "abaikan instruksi", "high"),
    (r"lupakan\s+(semua\s+)?instruksi", "lupakan instruksi", "high"),
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", "ignore previous instructions", "high"),
    (r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?", "disregard instructions", "high"),
    (r"override\s+(your|the)\s+instructions?", "override instructions", "high"),
    (r"(reveal|show|print|tampilkan|ungkapkan)\s+(your|the)\s+(system\s+)?prompt", "reveal system prompt", "high"),
    (r"system\s+prompt", "system prompt", "high"),
    (r"jailbreak", "jailbreak", "high"),
    (r"kamu\s+adalah\s+(sekarang\s+)?", "kamu adalah (peran baru)", "high"),
    (r"you\s+are\s+now\s+", "you are now (peran baru)", "high"),
    (r"mulai\s+sekarang[,.]?\s+kamu", "mulai sekarang kamu", "high"),
    (r"from\s+now\s+on[,.]?\s+you", "from now on you", "high"),
    # --- tinggi: perintah bertindak / membocorkan rahasia ---
    (r"kamu\s+harus\b", "kamu harus", "high"),
    (r"anda\s+harus\b", "anda harus", "high"),
    (r"kamu\s+wajib\b", "kamu wajib", "high"),
    (r"you\s+must\b", "you must", "high"),
    (r"jangan\s+(beri\s?tahu|bilang|ceritakan|sampaikan)\b[^.]{0,40}(user|pengguna|siapa\s*pun|siapapun|orang\s*lain)", "jangan beri tahu user", "high"),
    (r"do\s+not\s+tell\s+the\s+user\b", "do not tell the user", "high"),
    (r"(send|kirimkan|transfer|pay)\s+[^.]{0,30}(uang|dana|balance|password|kata\s+sandi|otp|token|api[_\s-]?key|key|data\s+pribadi)", "perintah kirim/bayar rahasia", "high"),
    (r"(kirim|kirimkan|berikan|tunjukkan|ungkapkan)\s+[^.]{0,30}(password|kata\s+sandi|otp|token|api[_\s-]?key)", "minta kredensial", "high"),
    (r"(send|transfer|pay)\s+[^.]{0,30}(money|password|otp|token|api[_\s-]?key|funds)", "send/transfer secret", "high"),
    (r"bayar\s+sekarang\b", "bayar sekarang", "high"),
    (r"transfer\s+sekarang\b", "transfer sekarang", "high"),
    # --- sedang: imperatif umum + URL ---
    (r"\bkirim\b", "send", "in_progress"),
    (r"\bkirimkan\b", "kirimkan", "in_progress"),
    (r"\bbayar\b", "pay", "in_progress"),
    (r"\btransfer\b", "transfer", "in_progress"),
    (r"\bklik\b", "klik", "in_progress"),
    (r"\bclick\s+(here|this|the\s+link)\b", "click here", "in_progress"),
    (r"\bunduh\b", "unduh", "in_progress"),
    (r"\bdownload\b", "download", "in_progress"),
    (r"\bbuka\s+(tautan|link|url)\b", "buka tautan", "in_progress"),
    (r"\bverifikasi\s+akun\b", "verifikasi akun", "in_progress"),
    (r"\bverify\s+your\s+account\b", "verify your account", "in_progress"),
    (r"https?://[^\s<>\")\]]+", "URL", "in_progress"),
    (r"\bwww\.[^\s<>\")\]]+", "URL www", "in_progress"),
    (r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "alamat email", "in_progress"),
    # --- rendah: urgensi / ajakan kontak luar ---
    (r"\bsegera\b", "segera", "low"),
    (r"\bsekarang\s+juga\b", "sekarang juga", "low"),
    (r"\bhubungi\b", "hubungi", "low"),
    (r"\bwa\.me/\S+", "tautan wa.me", "low"),
    (r"\bbit\.ly/\S+", "tautan pendek bit.ly", "low"),
]

_COMPILED: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(pattern, re.IGNORECASE), name, level) for pattern, name, level in _PATTERNS
]

_MAX_SPAN = 200


def _collect(text: str) -> list[tuple[int, int, str, str]]:
    """Semua kecocokan (start, end, nama pola, tingkat), belum dideduplikasi."""
    out: list[tuple[int, int, str, str]] = []
    for rx, name, level in _COMPILED:
        for m in rx.finditer(text):
            start, end = m.span()
            span = text[start:end].strip()
            if not span:
                continue
            out.append((start, end, name, level))
    return out


def _dedup(matches: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    """Buang kecocokan yang tertutup penuh oleh kecocokan lain.

    Diurutkan berdasarkan posisi, lalu panjang menurun, lalu tingkat menurun,
    sehingga span yang lebih spesifik / lebih berbahaya menang.
    """
    ordered = sorted(
        matches,
        key=lambda m: (m[0], -(m[1] - m[0]), -_LEVEL_RANK[m[3]]),
    )
    kept: list[tuple[int, int, str, str]] = []
    for m in ordered:
        if any(m[0] >= k[0] and m[1] <= k[1] for k in kept):
            continue
        kept.append(m)
    return sorted(kept, key=lambda m: m[0])


def scan(text: str) -> list[Finding]:
    """Cari pola instruksi dalam teks. Mengembalikan daftar Finding.

    Teks bersih mengembalikan list kosong. Fungsi ini tidak mengubah teks
    dan tidak pernah memutuskan sendiri apakah teks "aman": hasilnya hanya
    penanda untuk lapisan karantina di atasnya.
    """
    if not text:
        return []
    findings: list[Finding] = []
    for start, end, name, level in _dedup(_collect(text)):
        span = text[start:end].strip()
        if len(span) > _MAX_SPAN:
            span = span[:_MAX_SPAN]
        findings.append(Finding(span=span, pattern=name, level=level))
    return findings


def mark(text: str) -> str:
    """Bungkus setiap span mencurigakan sebagai ``[DATA: ...]``.

    Teks bersih dikembalikan apa adanya. Span yang tumpang tindih hanya
    dibungkus sekali (yang paling spesifik menang, lihat _dedup).
    """
    if not text:
        return text
    kept = _dedup(_collect(text))
    if not kept:
        return text
    out: list[str] = []
    pos = 0
    for start, end, _name, _level in kept:
        out.append(text[pos:start])
        out.append("[DATA: ")
        out.append(text[start:end])
        out.append("]")
        pos = end
    out.append(text[pos:])
    return "".join(out)
