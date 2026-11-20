"""Penyusun alur lewat chat (§2.12 revisi 7).

User menulis bahasa biasa ("tiap tanggal 25 cek PLN dan PDAM,
ingatkan aku, siapkan bayar") → penyusun menyusun DRAF alur sebagai
kartu langkah. Penyusun ini DETERMINISTIK (pencocokan kata kunci +
pola), bukan model — jadi ia HANYA bisa memakai blok pustaka: blok
yang tidak ada di pustaka tidak mungkin tersusun. Kalau user meminta
kemampuan yang belum ada, penyusun bilang blok itu belum ada dan
mencatat Permintaan Blok (gagal terlihat + data peta jalan, §3.8).

Pola Langflow yang diadaptasi: kanvas node → flow. Di Puspa, kanvasnya
adalah kartu langkah di panel Alur (lihat panel.py), bukan klon UI
Langflow — node-nya blok bersertifikat, dan tiap alur lewat gerbang
manifes + uji kering sebelum aktif, yang tidak dimiliki Langflow.
"""

from __future__ import annotations

import re

from puspa.feature_flags import FLAG_WORKFLOW, require_enabled
from puspa.workflow.blocks import BLOCKS

# Provider MVP (§4.2) + kata yang menunjukkannya di chat.
PROVIDER_WORDS = {
    "pln": "PLN",
    "pdam": "PDAM",
    "airtime": "airtime",
    "paket data": "paket data",
    "token listrik": "airtime",
    "electricity": "PLN",
}

# Kemampuan yang sering diminta tapi BELUM ADA bloknya di MVP — tiap
# pola memetakan ke nama blok yang dicatat sebagai Permintaan Blok.
MISSING_BLOCK_PATTERNS = [
    (r"\b(kirim|kirimkan)\b.*\b(email|wa|whatsapp|telegram)\b", "send_email"),
    (r"\bbayar(kan|lah)?\b.*\b(otomatis|langsung|tanpa.*setuju)", "pay_automatic"),
    (r"\b(telepon|telpon|hubungi|call)\b", "phone"),
    (r"\b(download|unduh)\b", "download_file"),
    (r"\b(isi|isikan)\s+form\b", "content_form"),
    (r"\bposting\b|\bpost(ing)?\b.*\b(ig|instagram|x|twitter|tiktok)\b", "social_post"),
]

# Kata kerja yang menunjuk blok yang ADA.
BLOCK_PATTERNS = [
    (r"\bcek\b.*\btagihan\b|\bcek\b.*\b(pln|pdam|pulsa|paket data|listrik)\b", "check_bill"),
    (r"\bbandingkan\b.*\bharga\b|\bcek\b.*\bharga\b.*\b(barang|hp|laptop|tv)\b", "compare_prices"),
    (r"\bpantau\b.*\bharga\b|\bharga turun\b|\bturun di bawah\b", "watch_prices"),
    (r"\bcari\b.*\bemail\b", "search_email"),
    (r"\bbaca\b.*\bemail\b", "read_email"),
    (r"\bdraf\b", "create_draft"),
    (r"\bbuat\b.*\bevent\b|\bjadwalkan\b.*\b(meeting|rapat|acara)\b", "create_event"),
    (r"\bsiapkan bayar\b|\bsiapkan pembayaran\b", "prepare_payment"),
    (r"\bingatkan\b|\bnotif\b|\bingatkan aku\b", "send_notification"),
    (r"\btanya\b|\bkonfirmasi\b.*\baku\b", "ask_user"),
]


def _mentioned_providers(text: str) -> list[str]:
    """Provider MVP yang disebut di teks (terurut kemunculan)."""
    low = text.lower()
    found: list[str] = []
    for kata, provider in PROVIDER_WORDS.items():
        if kata in low and provider not in found:
            found.append(provider)
    return found


def _mentioned_date(text: str) -> int | None:
    """'tiap tanggal 25' / 'tanggal 25' -> 25. None bila tidak disebut."""
    match = re.search(r"tanggal\s+(\d{1,2})", text.lower())
    if match:
        value = int(match.group(1))
        return value if 1 <= value <= 31 else None
    return None


def _mentioned_day(text: str) -> str | None:
    for day in ("senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "week"):
        if day in text.lower():
            return day
    return None


def _days_before(text: str) -> int:
    match = re.search(r"(\d+)\s+hari\s+sebelum", text.lower())
    return int(match.group(1)) if match else 3


def _product_name(text: str) -> str | None:
    match = re.search(r"harga\s+(?:barang\s+)?(.+?)(?:\s+di\b|\s*$)", text.lower())
    if match:
        name = match.group(1).strip(" .")
        return name or None
    return None


def compose_draft(user_text: str, *, user_id: str, dsn=None) -> dict:
    """Susun draf alur dari satu pesan chat. Kembalikan:
    {nama, pemicu, langkah, permintaan_blok, pesan, kartu}.
    `permintaan_blok`: kemampuan diminta yang belum ada bloknya
    (dicatat ke tabel permintaan_blok Fase 3 + dijelaskan ke user).
    `pesan`: kalimat yang ditampilkan ke user (gagal terlihat).

    Gated on the ``workflow`` flag (E-21, plan 9.6): when the
    flag is off, no workflow draft can be composed.
    """
    require_enabled(FLAG_WORKFLOW)
    text = (user_text or "").strip()
    low = text.lower()
    step: list[dict] = []
    trigger: dict = {"type": "manual"}
    requests: list[str] = []

    date = _mentioned_date(text)
    day = _mentioned_day(text)
    if date is not None:
        trigger = {"type": "schedule", "mode": "date", "date": date, "start_time": "07:00"}
    elif day is not None:
        trigger = {"type": "schedule", "mode": "day", "day": day, "start_time": "07:00"}
    elif "tiap minggu" in low or "weekly" in low or "seminggu" in low:
        trigger = {"type": "schedule", "mode": "day", "day": "senin", "start_time": "07:00"}

    # Pemicu jatuh tempo: "N hari sebelum jatuh tempo" / "ingatkan ... jatuh tempo".
    if "jatuh tempo" in low:
        provider = _mentioned_providers(text)
        trigger = {
            "type": "due_date",
            "days_before": _days_before(text),
            "provider": provider[0] if provider else "PLN",
        }

    # Pemicu harga turun.
    if "harga turun" in low or "turun di bawah" in low:
        match = re.search(r"(?:di bawah|turun(?: ke)?)\s*rp?\s?([\d.]+)", low)
        threshold = int(match.group(1).replace(".", "")) if match else 0
        trigger = {
            "type": "price_drop",
            "product_id": _product_name(text) or "product",
            "price_threshold": threshold,
        }

    # Langkah: cek tagihan per provider yang disebut.
    provider = _mentioned_providers(text)
    if provider and (
        "cek" in low
        or "check" in low
        or "tagihan" in low
        or "bill" in low
        or "jatuh tempo" in low
        or "due" in low
    ):
        for p in provider:
            step.append(
                {
                    "block": "check_bill",
                    "inputs": {"provider": p, "customer_number": ""},
                    "title": f"Cek tagihan {p}",
                }
            )
    # Rekap: untuk_tiap atas daftar provider (tugas acuan 9).
    if ("semua tagihan" in low or "rekap" in low) and not step:
        for p in ("PLN", "PDAM", "airtime", "paket data"):
            step.append(
                {
                    "block": "check_bill",
                    "inputs": {"provider": p, "customer_number": ""},
                    "title": f"Cek tagihan {p}",
                }
            )
    # Bandingkan / pantau harga.
    if ("bandingkan" in low or "compare" in low) and ("harga" in low or "price" in low):
        step.append(
            {
                "block": "compare_prices",
                "inputs": {"name_product": _product_name(text) or ""},
                "title": "Bandingkan harga",
            }
        )
    if ("pantau" in low or "watch" in low) and ("harga" in low or "price" in low):
        step.append(
            {
                "block": "watch_prices",
                "inputs": {"product_id": _product_name(text) or "", "price_threshold": 0},
                "title": "Pantau harga",
            }
        )
    # Cari/baca email + draf.
    if re.search(r"\bcari\b.*\bemail\b", low):
        step.append({"block": "search_email", "inputs": {"keywords": ""}, "title": "Cari email"})
    if re.search(r"\bbaca\b.*\bemail\b", low):
        step.append({"block": "read_email", "inputs": {"email_id": ""}, "title": "Baca email"})
    if "draft" in low:
        step.append(
            {
                "block": "create_draft",
                "inputs": {"recipient": "", "subject": "", "content": ""},
                "title": "Buat draf balasan",
            }
        )
    if re.search(r"\bbuat\b.*\bevent\b|\bjadwalkan\b", low):
        step.append(
            {
                "block": "create_event",
                "inputs": {"title": "", "created_at": "", "attendees": []},
                "title": "Buat event",
            }
        )
    # Ingatkan (notif) — setelah cek tagihan.
    if "ingatkan" in low or "ingatin" in low or "remind" in low:
        step.append(
            {
                "block": "send_notification",
                "inputs": {"message": "Tagihanmu sudah dicek — lihat rinciannya."},
                "title": "Ingatkan aku",
            }
        )
    # Siapkan bayar — selalu di akhir, setelah cek.
    if "siapkan bayar" in low or "siapkan pembayaran" in low:
        step.append(
            {
                "block": "prepare_payment",
                "inputs": {
                    "provider": provider[0] if provider else "PLN",
                    "amount": 0,
                    "purpose": "",
                },
                "title": "Siapkan bayar (mode aman)",
            }
        )

    # Kemampuan yang diminta tapi belum ada bloknya → Permintaan Blok.
    for pattern, block_name in MISSING_BLOCK_PATTERNS:
        if re.search(pattern, low) and block_name not in requests:
            requests.append(block_name)
    if requests:
        from puspa.moat.library import record_block_request

        for block_name in requests:
            record_block_request(
                user_id=user_id,
                block=block_name,
                reason=f"diminta lewat chat saat menyusun alur: {text[:120]!r}",
                dsn=dsn,
            )

    if not step and not requests:
        return {
            "name": "",
            "triggers": trigger,
            "step": [],
            "block_requests": [],
            "message": (
                "Aku belum paham alur yang kamu maksud. Coba contoh: "
                "'tiap tanggal 25 cek PLN dan PDAM, ingatkan aku, "
                "siapkan bayar'."
            ),
            "card": [],
        }

    name = "Alur " + ("tagihan bulanan" if provider else "new")
    message = ""
    if requests:
        items = ", ".join(requests)
        message = (
            f"Blok ini belum ada di Puspa, jadi tidak bisa kusertakan: {items}. "
            "Aku sudah mencatatnya sebagai Permintaan Blok — blok baru berisi "
            "kode tetap fase 6. Alur di bawah ini hanya memakai blok yang sudah ada."
        )
    return {
        "name": name,
        "triggers": trigger,
        "step": step,
        "block_requests": requests,
        "message": message,
        "card": [card_step(step_entry) for step_entry in step],
    }


def card_step(step: dict) -> dict:
    """Satu langkah sebagai kartu untuk panel Alur (judul + blok +
    ringkasan masukan). Ini 'kanvas node' Puspa — kartu, bukan klon
    Langflow."""
    block = step.get("block", "?")
    definition = BLOCKS.get(block, {})
    return {
        "title": step.get("title") or block,
        "block": block,
        "description": definition.get("description", ""),
        "permission_class": definition.get("permission_class", ""),
        "inputs": step.get("inputs", {}),
        "outbound_action": definition.get("outbound_action", False),
    }
