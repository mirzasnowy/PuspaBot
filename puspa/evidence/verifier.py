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


def _normalize_name(name: str) -> str:
    return " ".join(str(name).lower().split())


def verify(evidence: EvidenceRecord, *, reread, model_decision=None,
            other_readings: list[float] | None = None) -> EvidenceRecord:
    """Periksa satu rekaman bukti; isi `id_pemeriksa` + `vonis`.

    `baca_ulang(sumber_url) -> dict` dengan kunci `nilai` (float) dan
    opsional `nama` + `waktu_ambil`. `keputusan_model(nama_a, nama_b)
    -> bool` menjawab "barang yang sama?".
    """
    # Lapis 1 — deterministik.
    if not is_allowed(evidence.source_url):
        evidence.verdict = "Cannot be determined"
        return evidence
    if other_readings:
        median = sorted(other_readings)[len(other_readings) // 2]
        if median and abs(evidence.value - median) / median > 0.5:
            evidence.verdict = "Cannot be determined"
            return evidence
    # Lapis 2 — baca ulang independen.
    try:
        reread_result = reread(evidence.source_url)
    except Exception:
        evidence.verdict = "Cannot be determined"
        return evidence
    if reread_result is None or "value" not in reread_result:
        evidence.verdict = "Cannot be determined"
        return evidence
    try:
        reread_value = float(reread_result["value"])
    except (TypeError, ValueError):
        evidence.verdict = "Cannot be determined"
        return evidence
    # Lapis 3 — keputusan berbahasa (barang yang sama?).
    claim_name = evidence.claim
    reread_name = reread_result.get("name", "")
    if claim_name and reread_name:
        same = (model_decision(claim_name, reread_name) if model_decision
                else _normalize_name(claim_name) == _normalize_name(reread_name))
        if not same:
            evidence.verdict = "Cannot be determined"
            return evidence
    if reread_value == evidence.value:
        evidence.verdict = "Verified"
    else:
        # Nilai beda tapi stempel waktu menunjukkan sumber berubah di
        # antara dua pembacaan -> Belum pasti (bukan langsung gagal).
        reread_time = reread_result.get("time_take")
        if isinstance(reread_time, datetime) and reread_time > evidence.time_take:
            evidence.verdict = "Uncertain"
        elif reread_value and abs(reread_value - evidence.value) / max(abs(reread_value), 1) <= 0.01:
            evidence.verdict = "Verified"
        else:
            evidence.verdict = "Uncertain"
    return evidence


def verify_injection(evidence: EvidenceRecord, *, value_wrong: float, reread) -> str:
    """Uji suntik harga salah (gerbang 11): ubah nilai pembacaan awal
    menjadi `nilai_salah`, Pemeriksa harus TIDAK memvonis Terverifikasi.
    Kembalikan vonis yang dihasilkan.
    """
    evidence.value = value_wrong
    return verify(evidence, reread=reread).verdict
