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

_TINGKAT_RANK = {"rendah": 0, "sedang": 1, "tinggi": 2}


@dataclass(frozen=True)
class Finding:
    """Satu span teks yang terlihat seperti instruksi, bukan data."""

    span: str
    pola: str
    tingkat: str  # "rendah" | "sedang" | "tinggi"


# Pola (regex, case-insensitive). "pola" adalah nama yang dibaca manusia.
# Tingkat "tinggi": perintah langsung ke agen untuk bertindak / membocorkan
# rahasia / mengabaikan instruksi. "sedang": imperatif umum + URL.
# "rendah": kata pemicu urgensi / ajakan menghubungi pihak luar.
_POLAS: list[tuple[str, str, str]] = [
    # --- tinggi: pengambilalihan instruksi ---
    (r"abaikan\s+(semua\s+)?instruksi", "abaikan instruksi", "tinggi"),
    (r"lupakan\s+(semua\s+)?instruksi", "lupakan instruksi", "tinggi"),
    (r"ignore\s+(all\s+)?(previous|prior|above)\s+instructions?", "ignore previous instructions", "tinggi"),
    (r"disregard\s+(all\s+)?(previous|prior|above)\s+instructions?", "disregard instructions", "tinggi"),
    (r"override\s+(your|the)\s+instructions?", "override instructions", "tinggi"),
    (r"(reveal|show|print|tampilkan|ungkapkan)\s+(your|the)\s+(system\s+)?prompt", "reveal system prompt", "tinggi"),
    (r"system\s+prompt", "system prompt", "tinggi"),
    (r"jailbreak", "jailbreak", "tinggi"),
    (r"kamu\s+adalah\s+(sekarang\s+)?", "kamu adalah (peran baru)", "tinggi"),
    (r"you\s+are\s+now\s+", "you are now (peran baru)", "tinggi"),
    (r"mulai\s+sekarang[,.]?\s+kamu", "mulai sekarang kamu", "tinggi"),
    (r"from\s+now\s+on[,.]?\s+you", "from now on you", "tinggi"),
    # --- tinggi: perintah bertindak / membocorkan rahasia ---
    (r"kamu\s+harus\b", "kamu harus", "tinggi"),
    (r"anda\s+harus\b", "anda harus", "tinggi"),
    (r"kamu\s+wajib\b", "kamu wajib", "tinggi"),
    (r"you\s+must\b", "you must", "tinggi"),
    (r"jangan\s+(beri\s?tahu|bilang|ceritakan|sampaikan)\b[^.]{0,40}(user|pengguna|siapa\s*pun|siapapun|orang\s*lain)", "jangan beri tahu user", "tinggi"),
    (r"do\s+not\s+tell\s+the\s+user\b", "do not tell the user", "tinggi"),
    (r"(kirim|kirimkan|transfer|bayar)\s+[^.]{0,30}(uang|dana|saldo|password|kata\s+sandi|otp|token|api[_\s-]?key|kunci|data\s+pribadi)", "perintah kirim/bayar rahasia", "tinggi"),
    (r"(kirim|kirimkan|berikan|tunjukkan|ungkapkan)\s+[^.]{0,30}(password|kata\s+sandi|otp|token|api[_\s-]?key)", "minta kredensial", "tinggi"),
    (r"(send|transfer|pay)\s+[^.]{0,30}(money|password|otp|token|api[_\s-]?key|funds)", "send/transfer secret", "tinggi"),
    (r"bayar\s+sekarang\b", "bayar sekarang", "tinggi"),
    (r"transfer\s+sekarang\b", "transfer sekarang", "tinggi"),
    # --- sedang: imperatif umum + URL ---
    (r"\bkirim\b", "kirim", "sedang"),
    (r"\bkirimkan\b", "kirimkan", "sedang"),
    (r"\bbayar\b", "bayar", "sedang"),
    (r"\btransfer\b", "transfer", "sedang"),
    (r"\bklik\b", "klik", "sedang"),
    (r"\bclick\s+(here|this|the\s+link)\b", "click here", "sedang"),
    (r"\bunduh\b", "unduh", "sedang"),
    (r"\bdownload\b", "download", "sedang"),
    (r"\bbuka\s+(tautan|link|url)\b", "buka tautan", "sedang"),
    (r"\bverifikasi\s+akun\b", "verifikasi akun", "sedang"),
    (r"\bverify\s+your\s+account\b", "verify your account", "sedang"),
    (r"https?://[^\s<>\")\]]+", "URL", "sedang"),
    (r"\bwww\.[^\s<>\")\]]+", "URL www", "sedang"),
    (r"\b[\w.+-]+@[\w-]+\.[\w.]+\b", "alamat email", "sedang"),
    # --- rendah: urgensi / ajakan kontak luar ---
    (r"\bsegera\b", "segera", "rendah"),
    (r"\bsekarang\s+juga\b", "sekarang juga", "rendah"),
    (r"\bhubungi\b", "hubungi", "rendah"),
    (r"\bwa\.me/\S+", "tautan wa.me", "rendah"),
    (r"\bbit\.ly/\S+", "tautan pendek bit.ly", "rendah"),
]

_COMPILED: list[tuple[re.Pattern[str], str, str]] = [
    (re.compile(pola, re.IGNORECASE), nama, tingkat) for pola, nama, tingkat in _POLAS
]

_MAX_SPAN = 200


def _collect(text: str) -> list[tuple[int, int, str, str]]:
    """Semua kecocokan (start, end, nama pola, tingkat), belum didedup."""
    out: list[tuple[int, int, str, str]] = []
    for rx, nama, tingkat in _COMPILED:
        for m in rx.finditer(text):
            start, end = m.span()
            span = text[start:end].strip()
            if not span:
                continue
            out.append((start, end, nama, tingkat))
    return out


def _dedup(matches: list[tuple[int, int, str, str]]) -> list[tuple[int, int, str, str]]:
    """Buang kecocokan yang tertutup penuh oleh kecocokan lain.

    Diurutkan berdasarkan posisi, lalu panjang menurun, lalu tingkat menurun,
    sehingga span yang lebih spesifik / lebih berbahaya menang.
    """
    ordered = sorted(
        matches,
        key=lambda m: (m[0], -(m[1] - m[0]), -_TINGKAT_RANK[m[3]]),
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
    for start, end, nama, tingkat in _dedup(_collect(text)):
        span = text[start:end].strip()
        if len(span) > _MAX_SPAN:
            span = span[:_MAX_SPAN]
        findings.append(Finding(span=span, pola=nama, tingkat=tingkat))
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
    for start, end, _nama, _tingkat in kept:
        out.append(text[pos:start])
        out.append("[DATA: ")
        out.append(text[start:end])
        out.append("]")
        pos = end
    out.append(text[pos:])
    return "".join(out)
