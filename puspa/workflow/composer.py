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

from puspa.workflow.blocks import BLOK

# Provider MVP (§4.2) + kata yang menunjukkannya di chat.
PROVIDER_KATA = {
    "pln": "PLN",
    "pdam": "PDAM",
    "airtime": "airtime",
    "paket data": "paket data",
    "token listrik": "airtime",
    "electricity": "PLN",
}

# Kemampuan yang sering diminta tapi BELUM ADA bloknya di MVP — tiap
# pola memetakan ke nama blok yang dicatat sebagai Permintaan Blok.
PERMINTAAN_POLa = [
    (r"\b(kirim|kirimkan)\b.*\b(email|wa|whatsapp|telegram)\b", "send_email"),
    (r"\bbayar(kan|lah)?\b.*\b(otomatis|langsung|tanpa.*setuju)", "pay_automatic"),
    (r"\b(telepon|telpon|hubungi|call)\b", "phone"),
    (r"\b(download|unduh)\b", "unduh_file"),
    (r"\b(isi|isikan)\s+form\b", "content_form"),
    (r"\bposting\b|\bpost(ing)?\b.*\b(ig|instagram|x|twitter|tiktok)\b", "posting_sosial"),
]

# Kata kerja yang menunjuk blok yang ADA.
PATTERN_BLOCK = [
    (r"\bcek\b.*\btagihan\b|\bcek\b.*\b(pln|pdam|pulsa|paket data|listrik)\b",
     "check_bill"),
    (r"\bbandingkan\b.*\bharga\b|\bcek\b.*\bharga\b.*\b(barang|hp|laptop|tv)\b",
     "compare_prices"),
    (r"\bpantau\b.*\bharga\b|\bharga turun\b|\bturun di bawah\b",
     "watch_prices"),
    (r"\bcari\b.*\bemail\b", "search_email"),
    (r"\bbaca\b.*\bemail\b", "read_email"),
    (r"\bdraf\b", "create_draft"),
    (r"\bbuat\b.*\bevent\b|\bjadwalkan\b.*\b(meeting|rapat|acara)\b", "create_event"),
    (r"\bsiapkan bayar\b|\bsiapkan pembayaran\b", "prepare_payment"),
    (r"\bingatkan\b|\bnotif\b|\bingatkan aku\b", "send_notification"),
    (r"\btanya\b|\bkonfirmasi\b.*\baku\b", "ask_user"),
]


def _provider_disebut(text: str) -> list[str]:
    """Provider MVP yang disebut di teks (terurut kemunculan)."""
    low = text.lower()
    ketemu: list[str] = []
    for kata, provider in PROVIDER_KATA.items():
        if kata in low and provider not in ketemu:
            ketemu.append(provider)
    return ketemu


def _tanggal_disebut(text: str) -> int | None:
    """'tiap tanggal 25' / 'tanggal 25' -> 25. None bila tidak disebut."""
    match = re.search(r"tanggal\s+(\d{1,2})", text.lower())
    if match:
        value = int(match.group(1))
        return value if 1 <= value <= 31 else None
    return None


def _day_disebut(text: str) -> str | None:
    for day in ("senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "week"):
        if day in text.lower():
            return day
    return None


def _day_sebelum(text: str) -> int:
    match = re.search(r"(\d+)\s+hari\s+sebelum", text.lower())
    return int(match.group(1)) if match else 3


def _name_product(text: str) -> str | None:
    match = re.search(r"harga\s+(?:barang\s+)?(.+?)(?:\s+di\b|\s*$)", text.lower())
    if match:
        name = match.group(1).strip(" .")
        return name or None
    return None


def susun_draft(teks_user: str, *, user_id: str, dsn=None) -> dict:
    """Susun draf alur dari satu pesan chat. Kembalikan:
    {nama, pemicu, langkah, permintaan_blok, pesan, kartu}.
    `permintaan_blok`: kemampuan diminta yang belum ada bloknya
    (dicatat ke tabel permintaan_blok Fase 3 + dijelaskan ke user).
    `pesan`: kalimat yang ditampilkan ke user (gagal terlihat).
    """
    text = (teks_user or "").strip()
    low = text.lower()
    step: list[dict] = []
    trigger: dict = {"type": "manual"}
    permintaan: list[str] = []

    tanggal = _tanggal_disebut(text)
    day = _day_disebut(text)
    if tanggal is not None:
        trigger = {"type": "jadwal", "mode": "tanggal", "tanggal": tanggal,
                  "jam_mulai": "07:00"}
    elif day is not None:
        trigger = {"type": "jadwal", "mode": "day", "day": day,
                  "jam_mulai": "07:00"}
    elif "tiap minggu" in low or "weekly" in low or "seminggu" in low:
        trigger = {"type": "jadwal", "mode": "day", "day": "senin",
                  "jam_mulai": "07:00"}

    # Pemicu jatuh tempo: "N hari sebelum jatuh tempo" / "ingatkan ... jatuh tempo".
    if "jatuh tempo" in low:
        provider = _provider_disebut(text)
        trigger = {"type": "due_date",
                  "day_sebelum": _day_sebelum(text),
                  "provider": provider[0] if provider else "PLN"}

    # Pemicu harga turun.
    if "harga turun" in low or "turun di bawah" in low:
        match = re.search(r"(?:di bawah|turun(?: ke)?)\s*rp?\s?([\d.]+)", low)
        ambang = int(match.group(1).replace(".", "")) if match else 0
        trigger = {"type": "price_turun", "product_id": _name_product(text) or "product",
                  "price_threshold": ambang}

    # Langkah: cek tagihan per provider yang disebut.
    provider = _provider_disebut(text)
    if provider and ("check" in low or "bill" in low or "jatuh tempo" in low):
        for p in provider:
            step.append({"block": "check_bill",
                            "masukan": {"provider": p, "number_pelanggan": ""},
                            "title": f"Cek tagihan {p}"})
    # Rekap: untuk_tiap atas daftar provider (tugas acuan 9).
    if "semua tagihan" in low or "rekap" in low:
        if not step:
            for p in ("PLN", "PDAM", "airtime", "paket data"):
                step.append({"block": "check_bill",
                                "masukan": {"provider": p, "number_pelanggan": ""},
                                "title": f"Cek tagihan {p}"})
    # Bandingkan / pantau harga.
    if "compare" in low and "price" in low:
        step.append({"block": "compare_prices",
                        "masukan": {"name_product": _name_product(text) or ""},
                        "title": "Bandingkan harga"})
    if "watch" in low and "price" in low:
        step.append({"block": "watch_prices",
                        "masukan": {"product_id": _name_product(text) or "",
                                    "price_threshold": 0},
                        "title": "Pantau harga"})
    # Cari/baca email + draf.
    if re.search(r"\bcari\b.*\bemail\b", low):
        step.append({"block": "search_email",
                        "masukan": {"kata_key": ""}, "title": "Cari email"})
    if re.search(r"\bbaca\b.*\bemail\b", low):
        step.append({"block": "read_email",
                        "masukan": {"email_id": ""}, "title": "Baca email"})
    if "draft" in low:
        step.append({"block": "create_draft",
                        "masukan": {"penerima": "", "subjek": "", "content": ""},
                        "title": "Buat draf balasan"})
    if re.search(r"\bbuat\b.*\bevent\b|\bjadwalkan\b", low):
        step.append({"block": "create_event",
                        "masukan": {"title": "", "created_at": "", "peserta": []},
                        "title": "Buat event"})
    # Ingatkan (notif) — setelah cek tagihan.
    if "remind" in low or "ingatin" in low:
        step.append({"block": "send_notification",
                        "masukan": {"message": "Tagihanmu sudah dicek — lihat rinciannya."},
                        "title": "Ingatkan aku"})
    # Siapkan bayar — selalu di akhir, setelah cek.
    if "siapkan bayar" in low or "siapkan pembayaran" in low:
        step.append({"block": "prepare_payment",
                        "masukan": {"provider": provider[0] if provider else "PLN",
                                    "amount": 0, "purpose": ""},
                        "title": "Siapkan bayar (mode aman)"})

    # Kemampuan yang diminta tapi belum ada bloknya → Permintaan Blok.
    for pattern, name_block in PERMINTAAN_POLa:
        if re.search(pattern, low) and name_block not in permintaan:
            permintaan.append(name_block)
    if permintaan:
        from puspa.moat.library import record_permintaan_block

        for name_block in permintaan:
            record_permintaan_block(
                user_id=user_id, block=name_block,
                reason=f"diminta lewat chat saat menyusun alur: {text[:120]!r}",
                dsn=dsn)

    if not step and not permintaan:
        return {
            "name": "", "triggers": trigger, "step": [], "permintaan_block": [],
            "message": ("Aku belum paham alur yang kamu maksud. Coba contoh: "
                      "'tiap tanggal 25 cek PLN dan PDAM, ingatkan aku, "
                      "siapkan bayar'."),
            "card": [],
        }

    name = "Alur " + ("tagihan bulanan" if provider else "new")
    message = ""
    if permintaan:
        items = ", ".join(permintaan)
        message = (f"Blok ini belum ada di Puspa, jadi tidak bisa kusertakan: {items}. "
                 "Aku sudah mencatatnya sebagai Permintaan Blok — blok baru berisi "
                 "kode tetap fase 6. Alur di bawah ini hanya memakai blok yang sudah ada.")
    return {
        "name": name,
        "triggers": trigger,
        "step": step,
        "permintaan_block": permintaan,
        "message": message,
        "card": [card_step(l) for l in step],
    }


def card_step(step: dict) -> dict:
    """Satu langkah sebagai kartu untuk panel Alur (judul + blok +
    ringkasan masukan). Ini 'kanvas node' Puspa — kartu, bukan klon
    Langflow."""
    block = step.get("block", "?")
    definisi = BLOK.get(block, {})
    return {
        "title": step.get("title") or block,
        "block": block,
        "deskripsi": definisi.get("deskripsi", ""),
        "permission_class": definisi.get("permission_class", ""),
        "masukan": step.get("masukan", {}),
        "action_logout": definisi.get("action_logout", False),
    }
