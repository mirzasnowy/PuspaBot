"""Wedge 2 — autopilot tagihan PPOB (§4.2 revisi 7).

Input: nomor pelanggan PLN, PDAM, atau nomor HP (pulsa/paket data).
Nomor tersimpan di memori (Fase 1: fakta profil), TIDAK PERNAH masuk
graf (§3.3 — prosedur menolak langkah ber-data pribadi).

Proses: agen menjalankan prosedur dari Graf Alur Layanan; Pembaca
membaca situs/API resmi provider (jumlah, jatuh tempo, status),
Pemeriksa membaca ulang. Kalau belum ada prosedur, agen menemukannya,
menulis sebagai prosedur percobaan, baru menjalankannya.

Output: tagihan + jatuh tempo + status (angka dari bukti), pengingat
sebelum jatuh tempo, tombol siapkan bayar (mode bayar aman: agen
menyiapkan, user menyetujui di aplikasi bank/e-wallet sendiri;
persetujuan terikat isi, §2.6 — di sini hanya DRAF, tidak ada
eksekusi bayar).

Provider MVP: PLN, PDAM (hanya PDAM milik Nero + pengguna uji),
pulsa, paket data. TIDAK ADA BPJS (proses pemerintah, aturan keras).

Mode baca: `fixture` (situs uji lokal — dipakai sampai keputusan 5
memberi nomor Nero) atau `live` (situs resmi; gerbang ToS: PLN/PDAM/
pulsa/paket data `layanan_resmi_terbatas`, baca hanya atas permintaan
user; captcha/login -> "tidak bisa cek", tanpa upaya menembus).
"""

from __future__ import annotations

import re
from datetime import date, datetime, timedelta, timezone

from puspa.evidence.evidence import catat_bukti
from puspa.evidence.verifier import periksa
from puspa.graphs import service as graf_layanan
from puspa.pricing.tos import boleh_baca

PROVIDER_MVP = ("PLN", "PDAM", "pulsa", "paket data")

# Prosedur bawaan per provider (langkah sebagai data, tanpa nomor
# pelanggan — nomor disuntikkan dari memori saat jalan, bukan disimpan
# di prosedur).
LANGKAH_PROSEDUR = {
    "PLN": [
        {"langkah": "buka_situs_resmi", "situs": "layanan.pln.co.id",
         "catatan": "login akun pelanggan + captcha bila diminta"},
        {"langkah": "masukkan_id_pelanggan_dari_memori"},
        {"langkah": "baca_jumlah_tagihan_dan_jatuh_tempo"},
        {"langkah": "pemeriksa_baca_ulang"},
    ],
    "PDAM": [
        {"langkah": "buka_situs_pdam_daerah_dari_memori"},
        {"langkah": "masukkan_nomor_pelanggan_dari_memori"},
        {"langkah": "baca_jumlah_tagihan_dan_jatuh_tempo"},
        {"langkah": "pemeriksa_baca_ulang"},
    ],
    "pulsa": [
        {"langkah": "buka_kanal_resmi_operator_dari_memori"},
        {"langkah": "cek_saldo_pulsa_nomor_hp_dari_memori"},
        {"langkah": "pemeriksa_baca_ulang"},
    ],
    "paket data": [
        {"langkah": "buka_kanal_resmi_operator_dari_memori"},
        {"langkah": "cek_sisa_kuota_dan_masa_aktif_nomor_hp_dari_memori"},
        {"langkah": "pemeriksa_baca_ulang"},
    ],
}

SKEMA_KELUARAN_TAGIHAN = {
    "jumlah": "Rp", "jatuh_tempo": "tanggal", "status": "teks",
    "periode": "teks", "bukti_id": "teks",
}


class BillingError(Exception):
    """Wedge tagihan gagal / menolak."""


def _parse_jumlah(teks: str) -> float | None:
    cocok = re.search(r"Rp\s*([\d.,]+)", teks or "")
    if not cocok:
        return None
    try:
        return float(cocok.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _parse_jatuh_tempo(teks: str) -> str | None:
    cocok = re.search(r"Jatuh tempo:\s*(\d{4}-\d{2}-\d{2})", teks or "")
    return cocok.group(1) if cocok else None


def _parse_status(teks: str) -> str:
    cocok = re.search(r"Status:\s*([a-z_]+)", teks or "")
    return cocok.group(1) if cocok else "tidak_diketahui"


def pastikan_prosedur(*, user_id: str, provider: str, agen_id: str,
                      dsn=None) -> dict:
    """Ambil prosedur provider; kalau belum ada, temukan + tulis
    sebagai prosedur percobaan (§4.2), baru kembalikan."""
    if provider not in PROVIDER_MVP:
        raise BillingError(f"provider di luar MVP: {provider!r} (TIDAK ADA BPJS)")
    for prosedur in graf_layanan.daftar_prosedur(user_id, dsn=dsn):
        if prosedur["layanan"] == provider:
            return prosedur
    return graf_layanan.tulis_prosedur(
        user_id=user_id, layanan=provider, langkah=LANGKAH_PROSEDUR[provider],
        agen_id=agen_id, skema_keluaran=SKEMA_KELUARAN_TAGIHAN,
        pemeriksaan_wajib=["pemeriksa_baca_ulang"], dsn=dsn)


def cek_tagihan(*, user_id: str, agen_id: str, provider: str,
                nomor_pelanggan: str, baca_situs, baca_ulang,
                tugas_id: str, mode: str = "fixture", dsn=None) -> dict:
    """Cek satu tagihan. `baca_situs(url) -> teks` dan `baca_ulang(url)
    -> {"nilai", ...}` disuntikkan (Pembaca terkarantina / Pemeriksa).

    Kembalikan {"sukses", "jumlah", "jatuh_tempo", "status",
    "bukti_id", "vonis", "prosedur"} — atau {"sukses": False,
    "alasan", "pesan"} bila tidak bisa cek (gagal terlihat).
    """
    if provider not in PROVIDER_MVP:
        return {"sukses": False, "alasan": "provider_di_luar_mvp",
                "pesan": f"Provider {provider!r} bukan provider MVP (PLN, PDAM, pulsa, paket data; tidak ada BPJS)."}
    if mode == "live" and not boleh_baca(provider, mode="live"):
        return {"sukses": False, "alasan": "syarat_layanan_menunggu_keputusan_nero",
                "pesan": f"{provider} belum diizinkan dibaca live — menunggu keputusan Nero (keputusan 15)."}
    prosedur = pastikan_prosedur(user_id=user_id, provider=provider,
                                 agen_id=agen_id, dsn=dsn)
    if prosedur["status"] == "rusak":
        return {"sukses": False, "alasan": "prosedur_rusak",
                "pesan": "tidak bisa cek — prosedur tagihannya rusak, agen tidak memakai prosedur yang mungkin salah"}

    def _jalan(_prosedur):
        url = baca_situs_url(provider, nomor_pelanggan, mode=mode)
        try:
            teks = baca_situs(url)
        except Exception as exc:  # noqa: BLE001
            return {"sukses": False, "alasan": "situs_tidak_bisa_dibaca", "pesan": str(exc)[:200]}
        jumlah = _parse_jumlah(teks)
        if jumlah is None:
            return {"sukses": False, "alasan": "jumlah_tidak_ditemukan",
                    "pesan": "situs provider tidak menampilkan jumlah tagihan yang bisa di-parse"}
        if mode == "fixture":
            # Situs uji lokal (test double, di luar allowlist §2.6):
            # verifikasi deterministik — Pemeriksa tetap membaca ulang
            # lewat callable `baca_ulang` yang independen (tanpa menerima
            # halaman dari Pembaca), lalu nilainya dibandingkan. Jalur
            # live di bawah memakai Pemeriksa penuh (allowlist + baca
            # ulang + keputusan berbahasa).
            from puspa.evidence.evidence import EvidenceRecord, buat_bukti_id

            bukti = EvidenceRecord(
                bukti_id=buat_bukti_id(), klaim=f"tagihan {provider}",
                nilai=jumlah, satuan="Rp",
                sumber_url=url if url.startswith("http") else "fixture://situs-uji",
                waktu_ambil=datetime.now(timezone.utc),
                potongan_teks=teks[:200].replace("\n", " "),
                hash_halaman="", id_pembaca=f"pembaca-{provider.lower().replace(' ', '_')}")
            try:
                ulang = baca_ulang(url)
                nilai_ulang = float(ulang["nilai"])
                nama_ulang = ulang.get("nama", "")
            except Exception:
                bukti.vonis = "Tidak bisa dipastikan"
            else:
                if nama_ulang and nama_ulang != bukti.klaim:
                    bukti.vonis = "Tidak bisa dipastikan"
                elif nilai_ulang == bukti.nilai:
                    bukti.vonis = "Terverifikasi"
                else:
                    bukti.vonis = "Belum pasti"
        else:
            bukti = catat_bukti(
                tugas_id=tugas_id, klaim=f"tagihan {provider}",
                nilai=jumlah, satuan="Rp", sumber_url=url,
                potongan_teks=teks[:200].replace("\n", " "), halaman=teks,
                id_pembaca=f"pembaca-{provider.lower().replace(' ', '_')}")
            bukti = periksa(bukti, baca_ulang=baca_ulang)
        if bukti.vonis == "Tidak bisa dipastikan":
            return {"sukses": False, "alasan": "tidak_bisa_dipastikan",
                    "pesan": "Pemeriksa tidak bisa memastikan angka tagihannya — angka tidak ditampilkan"}
        return {"sukses": True, "jumlah": jumlah,
                "jatuh_tempo": _parse_jatuh_tempo(teks),
                "status": _parse_status(teks),
                "bukti_id": bukti.bukti_id, "vonis": bukti.vonis}

    hasil = graf_layanan.jalankan_prosedur(
        prosedur, jalankan=_jalan, agen_id=agen_id, user_id=user_id,
        tugas_id=tugas_id, dsn=dsn)
    if not hasil["sukses"]:
        return {"sukses": False, "alasan": hasil.get("hasil", {}).get("alasan", "jalan_gagal"),
                "pesan": hasil.get("hasil", {}).get("pesan", "cek tagihan gagal")}
    jalan = hasil["hasil"]
    jalan["prosedur"] = hasil["prosedur"]
    return jalan


def baca_situs_url(provider: str, nomor_pelanggan: str, mode: str = "fixture") -> str:
    """URL situs yang dibaca. Mode fixture: URL disuntikkan pemanggil
    lewat `baca_situs` (situs uji lokal) — fungsi ini hanya memberi
    URL resmi untuk mode live."""
    if mode == "fixture":
        # Pemanggil fixture memakai URL situs uji lokalnya sendiri;
        # nilai ini hanya penanda (tidak pernah di-fetch langsung).
        return f"fixture://{provider}/{nomor_pelanggan}"
    if provider == "PLN":
        return "https://layanan.pln.co.id/informasi-tagihan-listrik-pembelian-token-pln"
    return f"https://{provider.lower().replace(' ', '')}.co.id/"


def pengingat_jatuh_tempo(*, jatuh_tempo: str | None, hari_sebelum: int = 3,
                          hari_ini: date | None = None) -> dict:
    """Pengingat sebelum jatuh tempo (gratis, §4.2).

    Kembalikan {"perlu_ingatkan": bool, "hari_lagi": int|None}.
    Tanpa jatuh tempo (mis. pulsa) -> tidak ada pengingat.
    """
    if not jatuh_tempo:
        return {"perlu_ingatkan": False, "hari_lagi": None}
    tempo = date.fromisoformat(jatuh_tempo)
    hari_ini = hari_ini or datetime.now(timezone.utc).date()
    hari_lagi = (tempo - hari_ini).days
    return {"perlu_ingatkan": 0 <= hari_lagi <= hari_sebelum,
            "hari_lagi": hari_lagi}


def siapkan_bayar(*, provider: str, jumlah, jatuh_tempo: str | None,
                  nomor_pelanggan_masked: str, dsn=None) -> dict:
    """Siapkan bayar — MODE BAYAR AMAN (§4.2, §2.6).

    Hanya DRAF: nominal + tujuan + jatuh tempo untuk disetujui user di
    aplikasi bank/e-wallet miliknya sendiri. TIDAK ADA eksekusi bayar,
    TIDAK ADA pemindahan uang. Persetujuan terikat isi: draf memuat
    hash isi yang harus cocok saat user menyetujui (gerbang tunggal
    Fase 0 yang menegakkannya).
    """
    import hashlib
    import json

    isi = {"provider": provider, "jumlah": float(jumlah),
           "jatuh_tempo": jatuh_tempo, "nomor_masked": nomor_pelanggan_masked}
    isi_hash = hashlib.sha256(
        json.dumps(isi, sort_keys=True).encode("utf-8")).hexdigest()
    return {"mode": "siapkan_bayar", "draf": isi, "isi_hash": isi_hash,
            "pesan": "Draf siap — setujui di aplikasi bank/e-wallet kamu sendiri. "
                     "Puspa tidak memegang dan tidak memindahkan uang."}
