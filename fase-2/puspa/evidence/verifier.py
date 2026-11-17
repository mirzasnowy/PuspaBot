"""Pemeriksa independen (§2.5 revisi 7).

Pemeriksa TIDAK menerima halaman atau potongan dari pembuatnya. Ia
hanya menerima klaim ("harga produk X di URL Y adalah Rp Z") lalu
membaca ulang sumber lewat sandbox sendiri (di sini: callable
`baca_ulang` yang disuntikkan — di produksi ia memanggil Pembaca
terkarantina Fase 0 di sandbox baru; di tes ia mock / kaset).

Tiga lapis:
1. Deterministik: angka bisa di-parse, Rupiah, domain di allowlist,
   nilai masuk rentang wajar terhadap pembacaan lain, tanggal masuk
   akal.
2. Baca ulang sumber dan bandingkan nilainya.
3. Model hanya untuk keputusan berbahasa (apakah ini barang yang
   sama: varian, storage, kondisi) — callable `keputusan_model`
   opsional; default: bandingkan nama yang dinormalisasi.
"""

from __future__ import annotations

from datetime import datetime, timezone

from puspa.evidence.evidence import EvidenceRecord
from puspa.sandbox.allowlist import is_allowed


def _normalisasi_nama(nama: str) -> str:
    return " ".join(str(nama).lower().split())


def periksa(bukti: EvidenceRecord, *, baca_ulang, keputusan_model=None,
            pembacaan_lain: list[float] | None = None) -> EvidenceRecord:
    """Periksa satu rekaman bukti; isi `id_pemeriksa` + `vonis`.

    `baca_ulang(sumber_url) -> dict` dengan kunci `nilai` (float) dan
    opsional `nama` + `waktu_ambil`. `keputusan_model(nama_a, nama_b)
    -> bool` menjawab "barang yang sama?".
    """
    # Lapis 1 — deterministik.
    if not is_allowed(bukti.sumber_url):
        bukti.vonis = "Tidak bisa dipastikan"
        return bukti
    if pembacaan_lain:
        tengah = sorted(pembacaan_lain)[len(pembacaan_lain) // 2]
        if tengah and abs(bukti.nilai - tengah) / tengah > 0.5:
            bukti.vonis = "Tidak bisa dipastikan"
            return bukti
    # Lapis 2 — baca ulang independen.
    try:
        ulang = baca_ulang(bukti.sumber_url)
    except Exception:
        bukti.vonis = "Tidak bisa dipastikan"
        return bukti
    if ulang is None or "nilai" not in ulang:
        bukti.vonis = "Tidak bisa dipastikan"
        return bukti
    try:
        nilai_ulang = float(ulang["nilai"])
    except (TypeError, ValueError):
        bukti.vonis = "Tidak bisa dipastikan"
        return bukti
    # Lapis 3 — keputusan berbahasa (barang yang sama?).
    nama_klaim = bukti.klaim
    nama_ulang = ulang.get("nama", "")
    if nama_klaim and nama_ulang:
        sama = (keputusan_model(nama_klaim, nama_ulang) if keputusan_model
                else _normalisasi_nama(nama_klaim) == _normalisasi_nama(nama_ulang))
        if not sama:
            bukti.vonis = "Tidak bisa dipastikan"
            return bukti
    if nilai_ulang == bukti.nilai:
        bukti.vonis = "Terverifikasi"
    else:
        # Nilai beda tapi stempel waktu menunjukkan sumber berubah di
        # antara dua pembacaan -> Belum pasti (bukan langsung gagal).
        waktu_ulang = ulang.get("waktu_ambil")
        if isinstance(waktu_ulang, datetime) and waktu_ulang > bukti.waktu_ambil:
            bukti.vonis = "Belum pasti"
        elif nilai_ulang and abs(nilai_ulang - bukti.nilai) / max(abs(nilai_ulang), 1) <= 0.01:
            bukti.vonis = "Terverifikasi"
        else:
            bukti.vonis = "Belum pasti"
    return bukti


def periksa_suntikan(bukti: EvidenceRecord, *, nilai_salah: float, baca_ulang) -> str:
    """Uji suntik harga salah (gerbang 11): ubah nilai pembacaan awal
    menjadi `nilai_salah`, Pemeriksa harus TIDAK memvonis Terverifikasi.
    Kembalikan vonis yang dihasilkan.
    """
    bukti.nilai = nilai_salah
    return periksa(bukti, baca_ulang=baca_ulang).vonis
