"""Renderer angka yatim (§2.5 revisi 7).

Agen menulis pesan dengan penanda, mis. `{harga:bukti_41}`, dan
renderer berbasis kode mengisi angka dari Buku Bukti. Model tidak
pernah mengetik angka bermakna sendiri.

Angka bermakna = harga, jumlah tagihan, tanggal jatuh tempo, selisih,
persen. Kalau sebuah pesan memuat angka bermakna tanpa penanda
berbukti (angka yatim), pesan ditahan, agen diminta memperbaiki satu
kali, dan kalau masih yatim, angkanya dibuang dan diganti pernyataan
tidak bisa memastikan.
"""

from __future__ import annotations

import re

from puspa.evidence.evidence import EvidenceRecord

# Penanda: {label:bukti_id} — label bebas (harga, tagihan, ...).
MARKER_RE = re.compile(r"\{(?P<label>[a-z_]+):(?P<evidence_id>bukti-[0-9a-f]+)\}")

# Angka bermakna tanpa penanda: Rp + angka, tanggal (20 Nov / 20-11-2026),
# persen, selisih "Rp... lebih murah/mahal" — pola umum yang cukup.
ORPHAN_NUMBER_RE = re.compile(
    r"(Rp\s?\d[\d.,]*)|(\b\d{1,2}\s+(Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)\b)"
    r"|(\b\d+(\.\d+)?\s?%)",
    re.IGNORECASE,
)

UNCERTAIN_STATEMENT = "tidak bisa memastikan angkanya"


def format_value(evidence: EvidenceRecord) -> str:
    """Format nilai bukti untuk pesan (Rupiah tanpa desimal)."""
    if evidence.unit == "Rp":
        return f"Rp{int(evidence.value):,}".replace(",", ".")
    if evidence.unit == "%":
        return f"{evidence.value:g}%"
    return f"{evidence.value:g} {evidence.unit}".strip()


def render(text: str, evidence_by_id: dict[str, EvidenceRecord]) -> tuple[str, list[str]]:
    """Isi semua penanda dari Buku Bukti.

    Kembalikan (teks_terrender, daftar bukti_id yang tidak ditemukan).
    Penanda yang buktinya tidak ada dibiarkan apa adanya supaya
    `cari_angka_yatim` bisa menandainya sebagai yatim.
    """
    missing: list[str] = []

    def substitute(m: re.Match) -> str:
        evidence = evidence_by_id.get(m.group("evidence_id"))
        if evidence is None:
            missing.append(m.group("evidence_id"))
            return m.group(0)
        if evidence.verdict == "Cannot be determined":
            return UNCERTAIN_STATEMENT
        return format_value(evidence)

    return MARKER_RE.sub(substitute, text), missing


def without_markers(text: str) -> str:
    """Hapus penanda {label:bukti_id} dari teks (untuk deteksi yatim).

    Angka yang sudah dirender dari bukti TIDAK dihitung yatim — yang
    dihitung yatim hanya angka telanjang di draf agen.
    """
    return MARKER_RE.sub("", text)


def find_orphan_numbers(draft_text: str) -> list[str]:
    """Daftar angka bermakna yang masih telanjang (tanpa penanda).

    Dipanggil pada DRAF agen (sebelum render), supaya angka yang
    dirender dari bukti tidak salah ditandai yatim.
    """
    return [m.group(0) for m in ORPHAN_NUMBER_RE.finditer(without_markers(draft_text))]


def present(draft_text: str, evidence_by_id: dict[str, EvidenceRecord],
            fix=None) -> dict:
    """Sajikan pesan lewat renderer dengan aturan angka yatim.

    - Render penanda dari bukti.
    - Kalau ada angka yatim: minta agen memperbaiki 1x (`perbaiki`
      callable menerima teks -> teks baru; bila None, langsung buang).
    - Kalau setelah perbaikan masih yatim: buang angkanya, ganti
      pernyataan tidak bisa memastikan.
    Kembalikan {"teks": ..., "ditahan": bool, "dibuang": [...]}.
    """
    text, _missing = render(draft_text, evidence_by_id)
    orphans = find_orphan_numbers(draft_text)
    held = False
    if orphans:
        held = True
        if fix is not None:
            fixed_draft = fix(draft_text)
            text, _ = render(fixed_draft, evidence_by_id)
            orphans = find_orphan_numbers(fixed_draft)
    dropped: list[str] = []
    if orphans:
        for number in orphans:
            text = text.replace(number, UNCERTAIN_STATEMENT, 1)
            dropped.append(number)
    return {"text": text, "held": held, "dropped": dropped}
