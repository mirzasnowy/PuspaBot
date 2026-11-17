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
PENANDA_RE = re.compile(r"\{(?P<label>[a-z_]+):(?P<bukti_id>bukti-[0-9a-f]+)\}")

# Angka bermakna tanpa penanda: Rp + angka, tanggal (20 Nov / 20-11-2026),
# persen, selisih "Rp... lebih murah/mahal" — pola umum yang cukup.
ANGKA_YATIM_RE = re.compile(
    r"(Rp\s?\d[\d.,]*)|(\b\d{1,2}\s+(Jan|Feb|Mar|Apr|Mei|Jun|Jul|Agu|Sep|Okt|Nov|Des)\b)"
    r"|(\b\d+(\.\d+)?\s?%)",
    re.IGNORECASE,
)

PERNYATAAN_TIDAK_PASTI = "tidak bisa memastikan angkanya"


def format_nilai(bukti: EvidenceRecord) -> str:
    """Format nilai bukti untuk pesan (Rupiah tanpa desimal)."""
    if bukti.satuan == "Rp":
        return f"Rp{int(bukti.nilai):,}".replace(",", ".")
    if bukti.satuan == "%":
        return f"{bukti.nilai:g}%"
    return f"{bukti.nilai:g} {bukti.satuan}".strip()


def render(teks: str, bukti_by_id: dict[str, EvidenceRecord]) -> tuple[str, list[str]]:
    """Isi semua penanda dari Buku Bukti.

    Kembalikan (teks_terrender, daftar bukti_id yang tidak ditemukan).
    Penanda yang buktinya tidak ada dibiarkan apa adanya supaya
    `cari_angka_yatim` bisa menandainya sebagai yatim.
    """
    hilang: list[str] = []

    def ganti(m: re.Match) -> str:
        bukti = bukti_by_id.get(m.group("bukti_id"))
        if bukti is None:
            hilang.append(m.group("bukti_id"))
            return m.group(0)
        if bukti.vonis == "Tidak bisa dipastikan":
            return PERNYATAAN_TIDAK_PASTI
        return format_nilai(bukti)

    return PENANDA_RE.sub(ganti, teks), hilang


def tanpa_penanda(teks: str) -> str:
    """Hapus penanda {label:bukti_id} dari teks (untuk deteksi yatim).

    Angka yang sudah dirender dari bukti TIDAK dihitung yatim — yang
    dihitung yatim hanya angka telanjang di draf agen.
    """
    return PENANDA_RE.sub("", teks)


def cari_angka_yatim(teks_draf: str) -> list[str]:
    """Daftar angka bermakna yang masih telanjang (tanpa penanda).

    Dipanggil pada DRAF agen (sebelum render), supaya angka yang
    dirender dari bukti tidak salah ditandai yatim.
    """
    return [m.group(0) for m in ANGKA_YATIM_RE.finditer(tanpa_penanda(teks_draf))]


def sajikan(teks_draf: str, bukti_by_id: dict[str, EvidenceRecord],
            perbaiki=None) -> dict:
    """Sajikan pesan lewat renderer dengan aturan angka yatim.

    - Render penanda dari bukti.
    - Kalau ada angka yatim: minta agen memperbaiki 1x (`perbaiki`
      callable menerima teks -> teks baru; bila None, langsung buang).
    - Kalau setelah perbaikan masih yatim: buang angkanya, ganti
      pernyataan tidak bisa memastikan.
    Kembalikan {"teks": ..., "ditahan": bool, "dibuang": [...]}.
    """
    teks, _hilang = render(teks_draf, bukti_by_id)
    yatim = cari_angka_yatim(teks_draf)
    ditahan = False
    if yatim:
        ditahan = True
        if perbaiki is not None:
            draf_baru = perbaiki(teks_draf)
            teks, _ = render(draf_baru, bukti_by_id)
            yatim = cari_angka_yatim(draf_baru)
    dibuang: list[str] = []
    if yatim:
        for angka in yatim:
            teks = teks.replace(angka, PERNYATAAN_TIDAK_PASTI, 1)
            dibuang.append(angka)
    return {"teks": teks, "ditahan": ditahan, "dibuang": dibuang}
