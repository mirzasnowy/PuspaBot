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

from puspa.evidence.evidence import record_evidence
from puspa.evidence.verifier import periksa
from puspa.graphs import service as graf_service
from puspa.pricing.tos import boleh_read

PROVIDER_MVP = ("PLN", "PDAM", "airtime", "paket data")

# Prosedur bawaan per provider (langkah sebagai data, tanpa nomor
# pelanggan — nomor disuntikkan dari memori saat jalan, bukan disimpan
# di prosedur).
STEP_PROSEDUR = {
    "PLN": [
        {"step": "open_situs_resmi", "situs": "layanan.pln.co.id",
         "catatan": "login akun pelanggan + captcha bila diminta"},
        {"step": "input_id_pelanggan_dari_memory"},
        {"step": "read_amount_bill_and_due_date"},
        {"step": "pemeriksa_read_ulang"},
    ],
    "PDAM": [
        {"step": "open_situs_pdam_daerah_dari_memory"},
        {"step": "input_number_pelanggan_dari_memory"},
        {"step": "read_amount_bill_and_due_date"},
        {"step": "pemeriksa_read_ulang"},
    ],
    "airtime": [
        {"step": "open_kanal_resmi_operator_dari_memory"},
        {"step": "check_balance_airtime_number_phone_dari_memory"},
        {"step": "pemeriksa_read_ulang"},
    ],
    "paket data": [
        {"step": "open_kanal_resmi_operator_dari_memory"},
        {"step": "check_sisa_kuota_and_masa_active_number_phone_dari_memory"},
        {"step": "pemeriksa_read_ulang"},
    ],
}

SKEMA_OUTPUT_BILL = {
    "amount": "Rp", "due_date": "date", "status": "text",
    "periode": "text", "evidence_id": "text",
}


class BillingError(Exception):
    """Wedge tagihan gagal / menolak."""


def _parse_amount(text: str) -> float | None:
    match = re.search(r"Rp\s*([\d.,]+)", text or "")
    if not match:
        return None
    try:
        return float(match.group(1).replace(".", "").replace(",", "."))
    except ValueError:
        return None


def _parse_due_date(text: str) -> str | None:
    match = re.search(r"Jatuh tempo:\s*(\d{4}-\d{2}-\d{2})", text or "")
    return match.group(1) if match else None


def _parse_status(text: str) -> str:
    match = re.search(r"Status:\s*([a-z_]+)", text or "")
    return match.group(1) if match else "unknown"


def pastikan_prosedur(*, user_id: str, provider: str, agent_id: str,
                      dsn=None) -> dict:
    """Ambil prosedur provider; kalau belum ada, temukan + tulis
    sebagai prosedur percobaan (§4.2), baru kembalikan."""
    if provider not in PROVIDER_MVP:
        raise BillingError(f"provider di luar MVP: {provider!r} (TIDAK ADA BPJS)")
    for prosedur in graf_service.items_prosedur(user_id, dsn=dsn):
        if prosedur["service"] == provider:
            return prosedur
    return graf_service.tulis_prosedur(
        user_id=user_id, service=provider, step=STEP_PROSEDUR[provider],
        agent_id=agent_id, output_schema=SKEMA_OUTPUT_BILL,
        required_checks=["pemeriksa_read_ulang"], dsn=dsn)


def check_bill(*, user_id: str, agent_id: str, provider: str,
                number_pelanggan: str, read_situs, reread,
                task_id: str, mode: str = "fixture", dsn=None) -> dict:
    """Cek satu tagihan. `baca_situs(url) -> teks` dan `baca_ulang(url)
    -> {"nilai", ...}` disuntikkan (Pembaca terkarantina / Pemeriksa).

    Kembalikan {"sukses", "jumlah", "jatuh_tempo", "status",
    "bukti_id", "vonis", "prosedur"} — atau {"sukses": False,
    "alasan", "pesan"} bila tidak bisa cek (gagal terlihat).
    """
    if provider not in PROVIDER_MVP:
        return {"success": False, "reason": "provider_di_luar_mvp",
                "message": f"Provider {provider!r} bukan provider MVP (PLN, PDAM, pulsa, paket data; tidak ada BPJS)."}
    if mode == "live" and not boleh_read(provider, mode="live"):
        return {"success": False, "reason": "syarat_service_pending_keputusan_nero",
                "message": f"{provider} belum diizinkan dibaca live — menunggu keputusan Nero (keputusan 15)."}
    prosedur = pastikan_prosedur(user_id=user_id, provider=provider,
                                 agent_id=agent_id, dsn=dsn)
    if prosedur["status"] == "broken":
        return {"success": False, "reason": "prosedur_broken",
                "message": "tidak bisa cek — prosedur tagihannya rusak, agen tidak memakai prosedur yang mungkin salah"}

    def _jalan(_prosedur):
        url = read_situs_url(provider, number_pelanggan, mode=mode)
        try:
            text = read_situs(url)
        except Exception as exc:  # noqa: BLE001
            return {"success": False, "reason": "situs_not_can_dibaca", "message": str(exc)[:200]}
        amount = _parse_amount(text)
        if amount is None:
            return {"success": False, "reason": "amount_not_ditemukan",
                    "message": "situs provider tidak menampilkan jumlah tagihan yang bisa di-parse"}
        if mode == "fixture":
            # Situs uji lokal (test double, di luar allowlist §2.6):
            # verifikasi deterministik — Pemeriksa tetap membaca ulang
            # lewat callable `baca_ulang` yang independen (tanpa menerima
            # halaman dari Pembaca), lalu nilainya dibandingkan. Jalur
            # live di bawah memakai Pemeriksa penuh (allowlist + baca
            # ulang + keputusan berbahasa).
            from puspa.evidence.evidence import EvidenceRecord, create_evidence_id

            evidence = EvidenceRecord(
                evidence_id=create_evidence_id(), klaim=f"tagihan {provider}",
                value=amount, unit="Rp",
                sumber_url=url if url.startswith("http") else "fixture://situs-uji",
                time_take=datetime.now(timezone.utc),
                text_excerpt=text[:200].replace("\n", " "),
                page_hash="", reader_id=f"pembaca-{provider.lower().replace(' ', '_')}")
            try:
                ulang = reread(url)
                value_ulang = float(ulang["value"])
                name_ulang = ulang.get("name", "")
            except Exception:
                evidence.vonis = "Cannot be determined"
            else:
                if name_ulang and name_ulang != evidence.klaim:
                    evidence.vonis = "Cannot be determined"
                elif value_ulang == evidence.value:
                    evidence.vonis = "Verified"
                else:
                    evidence.vonis = "Uncertain"
        else:
            evidence = record_evidence(
                task_id=task_id, klaim=f"tagihan {provider}",
                value=amount, unit="Rp", sumber_url=url,
                text_excerpt=text[:200].replace("\n", " "), halaman=text,
                reader_id=f"pembaca-{provider.lower().replace(' ', '_')}")
            evidence = periksa(evidence, reread=reread)
        if evidence.vonis == "Cannot be determined":
            return {"success": False, "reason": "not_can_dipastikan",
                    "message": "Pemeriksa tidak bisa memastikan angka tagihannya — angka tidak ditampilkan"}
        return {"success": True, "amount": amount,
                "due_date": _parse_due_date(text),
                "status": _parse_status(text),
                "evidence_id": evidence.evidence_id, "vonis": evidence.vonis}

    result = graf_service.jalankan_prosedur(
        prosedur, jalankan=_jalan, agent_id=agent_id, user_id=user_id,
        task_id=task_id, dsn=dsn)
    if not result["success"]:
        return {"success": False, "reason": result.get("result", {}).get("reason", "failed_runs"),
                "message": result.get("result", {}).get("message", "cek tagihan gagal")}
    jalan = result["result"]
    jalan["procedures"] = result["procedures"]
    return jalan


def read_situs_url(provider: str, number_pelanggan: str, mode: str = "fixture") -> str:
    """URL situs yang dibaca. Mode fixture: URL disuntikkan pemanggil
    lewat `baca_situs` (situs uji lokal) — fungsi ini hanya memberi
    URL resmi untuk mode live."""
    if mode == "fixture":
        # Pemanggil fixture memakai URL situs uji lokalnya sendiri;
        # nilai ini hanya penanda (tidak pernah di-fetch langsung).
        return f"fixture://{provider}/{number_pelanggan}"
    if provider == "PLN":
        return "https://layanan.pln.co.id/informasi-tagihan-listrik-pembelian-token-pln"
    return f"https://{provider.lower().replace(' ', '')}.co.id/"


def reminder_due_date(*, due_date: str | None, day_sebelum: int = 3,
                          today: date | None = None) -> dict:
    """Pengingat sebelum jatuh tempo (gratis, §4.2).

    Kembalikan {"perlu_ingatkan": bool, "hari_lagi": int|None}.
    Tanpa jatuh tempo (mis. pulsa) -> tidak ada pengingat.
    """
    if not due_date:
        return {"perlu_remind": False, "day_lagi": None}
    due = date.fromisoformat(due_date)
    today = today or datetime.now(timezone.utc).date()
    day_lagi = (due - today).days
    return {"perlu_remind": 0 <= day_lagi <= day_sebelum,
            "day_lagi": day_lagi}


def siapkan_pay(*, provider: str, amount, due_date: str | None,
                  number_pelanggan_masked: str, dsn=None) -> dict:
    """Siapkan bayar — MODE BAYAR AMAN (§4.2, §2.6).

    Hanya DRAF: nominal + tujuan + jatuh tempo untuk disetujui user di
    aplikasi bank/e-wallet miliknya sendiri. TIDAK ADA eksekusi bayar,
    TIDAK ADA pemindahan uang. Persetujuan terikat isi: draf memuat
    hash isi yang harus cocok saat user menyetujui (gerbang tunggal
    Fase 0 yang menegakkannya).
    """
    import hashlib
    import json

    content = {"provider": provider, "amount": float(amount),
           "due_date": due_date, "number_masked": number_pelanggan_masked}
    content_hash = hashlib.sha256(
        json.dumps(content, sort_keys=True).encode("utf-8")).hexdigest()
    return {"mode": "prepare_payment", "draft": content, "content_hash": content_hash,
            "message": "Draf siap — setujui di aplikasi bank/e-wallet kamu sendiri. "
                     "Puspa tidak memegang dan tidak memindahkan uang."}
