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
    "pulsa": "pulsa",
    "paket data": "paket data",
    "token listrik": "pulsa",
    "listrik": "PLN",
}

# Kemampuan yang sering diminta tapi BELUM ADA bloknya di MVP — tiap
# pola memetakan ke nama blok yang dicatat sebagai Permintaan Blok.
PERMINTAAN_POLa = [
    (r"\b(kirim|kirimkan)\b.*\b(email|wa|whatsapp|telegram)\b", "kirim_email"),
    (r"\bbayar(kan|lah)?\b.*\b(otomatis|langsung|tanpa.*setuju)", "bayar_otomatis"),
    (r"\b(telepon|telpon|hubungi|call)\b", "telepon"),
    (r"\b(download|unduh)\b", "unduh_file"),
    (r"\b(isi|isikan)\s+form\b", "isi_form"),
    (r"\bposting\b|\bpost(ing)?\b.*\b(ig|instagram|x|twitter|tiktok)\b", "posting_sosial"),
]

# Kata kerja yang menunjuk blok yang ADA.
POLA_BLOK = [
    (r"\bcek\b.*\btagihan\b|\bcek\b.*\b(pln|pdam|pulsa|paket data|listrik)\b",
     "cek_tagihan"),
    (r"\bbandingkan\b.*\bharga\b|\bcek\b.*\bharga\b.*\b(barang|hp|laptop|tv)\b",
     "bandingkan_harga"),
    (r"\bpantau\b.*\bharga\b|\bharga turun\b|\bturun di bawah\b",
     "pantau_harga"),
    (r"\bcari\b.*\bemail\b", "cari_email"),
    (r"\bbaca\b.*\bemail\b", "baca_email"),
    (r"\bdraf\b", "buat_draf"),
    (r"\bbuat\b.*\bevent\b|\bjadwalkan\b.*\b(meeting|rapat|acara)\b", "buat_event"),
    (r"\bsiapkan bayar\b|\bsiapkan pembayaran\b", "siapkan_bayar"),
    (r"\bingatkan\b|\bnotif\b|\bingatkan aku\b", "kirim_notif"),
    (r"\btanya\b|\bkonfirmasi\b.*\baku\b", "tanya_user"),
]


def _provider_disebut(teks: str) -> list[str]:
    """Provider MVP yang disebut di teks (terurut kemunculan)."""
    rendah = teks.lower()
    ketemu: list[str] = []
    for kata, provider in PROVIDER_KATA.items():
        if kata in rendah and provider not in ketemu:
            ketemu.append(provider)
    return ketemu


def _tanggal_disebut(teks: str) -> int | None:
    """'tiap tanggal 25' / 'tanggal 25' -> 25. None bila tidak disebut."""
    cocok = re.search(r"tanggal\s+(\d{1,2})", teks.lower())
    if cocok:
        nilai = int(cocok.group(1))
        return nilai if 1 <= nilai <= 31 else None
    return None


def _hari_disebut(teks: str) -> str | None:
    for hari in ("senin", "selasa", "rabu", "kamis", "jumat", "sabtu", "minggu"):
        if hari in teks.lower():
            return hari
    return None


def _hari_sebelum(teks: str) -> int:
    cocok = re.search(r"(\d+)\s+hari\s+sebelum", teks.lower())
    return int(cocok.group(1)) if cocok else 3


def _nama_barang(teks: str) -> str | None:
    cocok = re.search(r"harga\s+(?:barang\s+)?(.+?)(?:\s+di\b|\s*$)", teks.lower())
    if cocok:
        nama = cocok.group(1).strip(" .")
        return nama or None
    return None


def susun_draf(teks_user: str, *, user_id: str, dsn=None) -> dict:
    """Susun draf alur dari satu pesan chat. Kembalikan:
    {nama, pemicu, langkah, permintaan_blok, pesan, kartu}.
    `permintaan_blok`: kemampuan diminta yang belum ada bloknya
    (dicatat ke tabel permintaan_blok Fase 3 + dijelaskan ke user).
    `pesan`: kalimat yang ditampilkan ke user (gagal terlihat).
    """
    teks = (teks_user or "").strip()
    rendah = teks.lower()
    langkah: list[dict] = []
    pemicu: dict = {"jenis": "manual"}
    permintaan: list[str] = []

    tanggal = _tanggal_disebut(teks)
    hari = _hari_disebut(teks)
    if tanggal is not None:
        pemicu = {"jenis": "jadwal", "mode": "tanggal", "tanggal": tanggal,
                  "jam_mulai": "07:00"}
    elif hari is not None:
        pemicu = {"jenis": "jadwal", "mode": "hari", "hari": hari,
                  "jam_mulai": "07:00"}
    elif "tiap minggu" in rendah or "mingguan" in rendah or "seminggu" in rendah:
        pemicu = {"jenis": "jadwal", "mode": "hari", "hari": "senin",
                  "jam_mulai": "07:00"}

    # Pemicu jatuh tempo: "N hari sebelum jatuh tempo" / "ingatkan ... jatuh tempo".
    if "jatuh tempo" in rendah:
        provider = _provider_disebut(teks)
        pemicu = {"jenis": "jatuh_tempo",
                  "hari_sebelum": _hari_sebelum(teks),
                  "provider": provider[0] if provider else "PLN"}

    # Pemicu harga turun.
    if "harga turun" in rendah or "turun di bawah" in rendah:
        cocok = re.search(r"(?:di bawah|turun(?: ke)?)\s*rp?\s?([\d.]+)", rendah)
        ambang = int(cocok.group(1).replace(".", "")) if cocok else 0
        pemicu = {"jenis": "harga_turun", "produk_id": _nama_barang(teks) or "barang",
                  "ambang_harga": ambang}

    # Langkah: cek tagihan per provider yang disebut.
    provider = _provider_disebut(teks)
    if provider and ("cek" in rendah or "tagihan" in rendah or "jatuh tempo" in rendah):
        for p in provider:
            langkah.append({"blok": "cek_tagihan",
                            "masukan": {"provider": p, "nomor_pelanggan": ""},
                            "judul": f"Cek tagihan {p}"})
    # Rekap: untuk_tiap atas daftar provider (tugas acuan 9).
    if "semua tagihan" in rendah or "rekap" in rendah:
        if not langkah:
            for p in ("PLN", "PDAM", "pulsa", "paket data"):
                langkah.append({"blok": "cek_tagihan",
                                "masukan": {"provider": p, "nomor_pelanggan": ""},
                                "judul": f"Cek tagihan {p}"})
    # Bandingkan / pantau harga.
    if "bandingkan" in rendah and "harga" in rendah:
        langkah.append({"blok": "bandingkan_harga",
                        "masukan": {"nama_barang": _nama_barang(teks) or ""},
                        "judul": "Bandingkan harga"})
    if "pantau" in rendah and "harga" in rendah:
        langkah.append({"blok": "pantau_harga",
                        "masukan": {"produk_id": _nama_barang(teks) or "",
                                    "ambang_harga": 0},
                        "judul": "Pantau harga"})
    # Cari/baca email + draf.
    if re.search(r"\bcari\b.*\bemail\b", rendah):
        langkah.append({"blok": "cari_email",
                        "masukan": {"kata_kunci": ""}, "judul": "Cari email"})
    if re.search(r"\bbaca\b.*\bemail\b", rendah):
        langkah.append({"blok": "baca_email",
                        "masukan": {"email_id": ""}, "judul": "Baca email"})
    if "draf" in rendah:
        langkah.append({"blok": "buat_draf",
                        "masukan": {"penerima": "", "subjek": "", "isi": ""},
                        "judul": "Buat draf balasan"})
    if re.search(r"\bbuat\b.*\bevent\b|\bjadwalkan\b", rendah):
        langkah.append({"blok": "buat_event",
                        "masukan": {"judul": "", "waktu": "", "peserta": []},
                        "judul": "Buat event"})
    # Ingatkan (notif) — setelah cek tagihan.
    if "ingatkan" in rendah or "ingatin" in rendah:
        langkah.append({"blok": "kirim_notif",
                        "masukan": {"pesan": "Tagihanmu sudah dicek — lihat rinciannya."},
                        "judul": "Ingatkan aku"})
    # Siapkan bayar — selalu di akhir, setelah cek.
    if "siapkan bayar" in rendah or "siapkan pembayaran" in rendah:
        langkah.append({"blok": "siapkan_bayar",
                        "masukan": {"provider": provider[0] if provider else "PLN",
                                    "jumlah": 0, "tujuan": ""},
                        "judul": "Siapkan bayar (mode aman)"})

    # Kemampuan yang diminta tapi belum ada bloknya → Permintaan Blok.
    for pola, nama_blok in PERMINTAAN_POLa:
        if re.search(pola, rendah) and nama_blok not in permintaan:
            permintaan.append(nama_blok)
    if permintaan:
        from puspa.moat.library import catat_permintaan_blok

        for nama_blok in permintaan:
            catat_permintaan_blok(
                user_id=user_id, blok=nama_blok,
                alasan=f"diminta lewat chat saat menyusun alur: {teks[:120]!r}",
                dsn=dsn)

    if not langkah and not permintaan:
        return {
            "nama": "", "pemicu": pemicu, "langkah": [], "permintaan_blok": [],
            "pesan": ("Aku belum paham alur yang kamu maksud. Coba contoh: "
                      "'tiap tanggal 25 cek PLN dan PDAM, ingatkan aku, "
                      "siapkan bayar'."),
            "kartu": [],
        }

    nama = "Alur " + ("tagihan bulanan" if provider else "baru")
    pesan = ""
    if permintaan:
        daftar = ", ".join(permintaan)
        pesan = (f"Blok ini belum ada di Puspa, jadi tidak bisa kusertakan: {daftar}. "
                 "Aku sudah mencatatnya sebagai Permintaan Blok — blok baru berisi "
                 "kode tetap fase 6. Alur di bawah ini hanya memakai blok yang sudah ada.")
    return {
        "nama": nama,
        "pemicu": pemicu,
        "langkah": langkah,
        "permintaan_blok": permintaan,
        "pesan": pesan,
        "kartu": [kartu_langkah(l) for l in langkah],
    }


def kartu_langkah(langkah: dict) -> dict:
    """Satu langkah sebagai kartu untuk panel Alur (judul + blok +
    ringkasan masukan). Ini 'kanvas node' Puspa — kartu, bukan klon
    Langflow."""
    blok = langkah.get("blok", "?")
    definisi = BLOK.get(blok, {})
    return {
        "judul": langkah.get("judul") or blok,
        "blok": blok,
        "deskripsi": definisi.get("deskripsi", ""),
        "kelas_izin": definisi.get("kelas_izin", ""),
        "masukan": langkah.get("masukan", {}),
        "aksi_keluar": definisi.get("aksi_keluar", False),
    }
