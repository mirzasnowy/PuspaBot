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


def _normalisasi_name(name: str) -> str:
    return " ".join(str(name).lower().split())


def periksa(evidence: EvidenceRecord, *, reread, keputusan_model=None,
            pembacaan_lain: list[float] | None = None) -> EvidenceRecord:
    """Periksa satu rekaman bukti; isi `id_pemeriksa` + `vonis`.

    `baca_ulang(sumber_url) -> dict` dengan kunci `nilai` (float) dan
    opsional `nama` + `waktu_ambil`. `keputusan_model(nama_a, nama_b)
    -> bool` menjawab "barang yang sama?".
    """
    # Lapis 1 — deterministik.
    if not is_allowed(evidence.sumber_url):
        evidence.vonis = "Cannot be determined"
        return evidence
    if pembacaan_lain:
        tengah = sorted(pembacaan_lain)[len(pembacaan_lain) // 2]
        if tengah and abs(evidence.value - tengah) / tengah > 0.5:
            evidence.vonis = "Cannot be determined"
            return evidence
    # Lapis 2 — baca ulang independen.
    try:
        ulang = reread(evidence.sumber_url)
    except Exception:
        evidence.vonis = "Cannot be determined"
        return evidence
    if ulang is None or "value" not in ulang:
        evidence.vonis = "Cannot be determined"
        return evidence
    try:
        value_ulang = float(ulang["value"])
    except (TypeError, ValueError):
        evidence.vonis = "Cannot be determined"
        return evidence
    # Lapis 3 — keputusan berbahasa (barang yang sama?).
    name_klaim = evidence.klaim
    name_ulang = ulang.get("name", "")
    if name_klaim and name_ulang:
        same = (keputusan_model(name_klaim, name_ulang) if keputusan_model
                else _normalisasi_name(name_klaim) == _normalisasi_name(name_ulang))
        if not same:
            evidence.vonis = "Cannot be determined"
            return evidence
    if value_ulang == evidence.value:
        evidence.vonis = "Verified"
    else:
        # Nilai beda tapi stempel waktu menunjukkan sumber berubah di
        # antara dua pembacaan -> Belum pasti (bukan langsung gagal).
        time_ulang = ulang.get("time_take")
        if isinstance(time_ulang, datetime) and time_ulang > evidence.time_take:
            evidence.vonis = "Uncertain"
        elif value_ulang and abs(value_ulang - evidence.value) / max(abs(value_ulang), 1) <= 0.01:
            evidence.vonis = "Verified"
        else:
            evidence.vonis = "Uncertain"
    return evidence


def periksa_suntikan(evidence: EvidenceRecord, *, value_wrong: float, reread) -> str:
    """Uji suntik harga salah (gerbang 11): ubah nilai pembacaan awal
    menjadi `nilai_salah`, Pemeriksa harus TIDAK memvonis Terverifikasi.
    Kembalikan vonis yang dihasilkan.
    """
    evidence.value = value_wrong
    return periksa(evidence, reread=reread).vonis
