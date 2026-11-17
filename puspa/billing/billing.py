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
from puspa.evidence.verifier import verify
from puspa.graphs import service as graph_service
from puspa.pricing.tos import may_read

PROVIDER_MVP = ("PLN", "PDAM", "airtime", "paket data")

# Prosedur bawaan per provider (langkah sebagai data, tanpa nomor
# pelanggan — nomor disuntikkan dari memori saat jalan, bukan disimpan
# di prosedur).
PROCEDURE_STEPS = {
    "PLN": [
        {"step": "open_official_site", "site": "layanan.pln.co.id",
         "notes": "login akun pelanggan + captcha bila diminta"},
        {"step": "input_customer_id_from_memory"},
        {"step": "read_amount_bill_and_due_date"},
        {"step": "verifier_reread"},
    ],
    "PDAM": [
        {"step": "open_regional_pdam_site_from_memory"},
        {"step": "input_customer_number_from_memory"},
        {"step": "read_amount_bill_and_due_date"},
        {"step": "verifier_reread"},
    ],
    "airtime": [
        {"step": "open_official_operator_channel_from_memory"},
        {"step": "check_airtime_balance_phone_number_from_memory"},
        {"step": "verifier_reread"},
    ],
    "paket data": [
        {"step": "open_official_operator_channel_from_memory"},
        {"step": "check_remaining_quota_and_active_period_phone_number_from_memory"},
        {"step": "verifier_reread"},
    ],
}

BILL_OUTPUT_SCHEMA = {
    "amount": "Rp", "due_date": "date", "status": "text",
    "period": "text", "evidence_id": "text",
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


def ensure_procedure(*, user_id: str, provider: str, agent_id: str,
                      dsn=None) -> dict:
    """Ambil prosedur provider; kalau belum ada, temukan + tulis
    sebagai prosedur percobaan (§4.2), baru kembalikan."""
    if provider not in PROVIDER_MVP:
        raise BillingError(f"provider di luar MVP: {provider!r} (TIDAK ADA BPJS)")
    for procedure in graph_service.list_procedures(user_id, dsn=dsn):
        if procedure["service"] == provider:
            return procedure
    return graph_service.write_procedure(
        user_id=user_id, service=provider, step=PROCEDURE_STEPS[provider],
        agent_id=agent_id, output_schema=BILL_OUTPUT_SCHEMA,
        required_checks=["verifier_reread"], dsn=dsn)


def check_bill(*, user_id: str, agent_id: str, provider: str,
                customer_number: str, read_site, reread,
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
    if mode == "live" and not may_read(provider, mode="live"):
        return {"success": False, "reason": "syarat_service_pending_keputusan_nero",
                "message": f"{provider} belum diizinkan dibaca live — menunggu keputusan Nero (keputusan 15)."}
    procedure = ensure_procedure(user_id=user_id, provider=provider,
                                 agent_id=agent_id, dsn=dsn)
    if procedure["status"] == "broken":
        return {"success": False, "reason": "prosedur_broken",
                "message": "tidak bisa cek — prosedur tagihannya rusak, agen tidak memakai prosedur yang mungkin salah"}

    def _run(_prosedur):
        url = read_site_url(provider, customer_number, mode=mode)
        try:
            text = read_site(url)
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
                evidence_id=create_evidence_id(), claim=f"tagihan {provider}",
                value=amount, unit="Rp",
                source_url=url if url.startswith("http") else "fixture://situs-uji",
                time_take=datetime.now(timezone.utc),
                text_excerpt=text[:200].replace("\n", " "),
                page_hash="", reader_id=f"pembaca-{provider.lower().replace(' ', '_')}")
            try:
                reread_result = reread(url)
                reread_value = float(reread_result["value"])
                reread_name = reread_result.get("name", "")
            except Exception:
                evidence.verdict = "Cannot be determined"
            else:
                if reread_name and reread_name != evidence.claim:
                    evidence.verdict = "Cannot be determined"
                elif reread_value == evidence.value:
                    evidence.verdict = "Verified"
                else:
                    evidence.verdict = "Uncertain"
        else:
            evidence = record_evidence(
                task_id=task_id, claim=f"tagihan {provider}",
                value=amount, unit="Rp", source_url=url,
                text_excerpt=text[:200].replace("\n", " "), page=text,
                reader_id=f"pembaca-{provider.lower().replace(' ', '_')}")
            evidence = verify(evidence, reread=reread)
        if evidence.verdict == "Cannot be determined":
            return {"success": False, "reason": "not_can_dipastikan",
                    "message": "Pemeriksa tidak bisa memastikan angka tagihannya — angka tidak ditampilkan"}
        return {"success": True, "amount": amount,
                "due_date": _parse_due_date(text),
                "status": _parse_status(text),
                "evidence_id": evidence.evidence_id, "verdict": evidence.verdict}

    result = graph_service.run_procedure(
        procedure, run=_run, agent_id=agent_id, user_id=user_id,
        task_id=task_id, dsn=dsn)
    if not result["success"]:
        return {"success": False, "reason": result.get("result", {}).get("reason", "failed_runs"),
                "message": result.get("result", {}).get("message", "cek tagihan gagal")}
    jalan = result["result"]
    jalan["procedures"] = result["procedures"]
    return jalan


def read_site_url(provider: str, customer_number: str, mode: str = "fixture") -> str:
    """URL situs yang dibaca. Mode fixture: URL disuntikkan pemanggil
    lewat `baca_situs` (situs uji lokal) — fungsi ini hanya memberi
    URL resmi untuk mode live."""
    if mode == "fixture":
        # Pemanggil fixture memakai URL situs uji lokalnya sendiri;
        # nilai ini hanya penanda (tidak pernah di-fetch langsung).
        return f"fixture://{provider}/{customer_number}"
    if provider == "PLN":
        return "https://layanan.pln.co.id/informasi-tagihan-listrik-pembelian-token-pln"
    return f"https://{provider.lower().replace(' ', '')}.co.id/"


def reminder_due_date(*, due_date: str | None, days_before: int = 3,
                          today: date | None = None) -> dict:
    """Pengingat sebelum jatuh tempo (gratis, §4.2).

    Kembalikan {"perlu_ingatkan": bool, "hari_lagi": int|None}.
    Tanpa jatuh tempo (mis. pulsa) -> tidak ada pengingat.
    """
    if not due_date:
        return {"needs_reminder": False, "days_left": None}
    due = date.fromisoformat(due_date)
    today = today or datetime.now(timezone.utc).date()
    days_left = (due - today).days
    return {"needs_reminder": 0 <= days_left <= days_before,
            "days_left": days_left}


def prepare_pay(*, provider: str, amount, due_date: str | None,
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
