"""Templat bawaan (§2.12 revisi 7) — 3 templat.

Tugas acuan 8 dan 9 berjalan sebagai alur:
- Tugas 8 ("Buatkan pengingat untuk tagihan") = templat Tagihan bulanan
  dengan pemicu manual.
- Tugas 9 ("Ingatkan semua tagihan yang jatuh tempo minggu ini") =
  templat Rekap tagihan minggu ini.
"""

from __future__ import annotations

TEMPLAT = {
    "tagihan_bulanan": {
        "nama": "Tagihan bulanan",
        "deskripsi": "Tiap tanggal 25 cek PLN dan PDAM, ingatkan aku, siapkan bayar.",
        "pemicu": {"jenis": "jadwal", "mode": "tanggal", "tanggal": 25,
                   "jam_mulai": "07:00"},
        "langkah": [
            {"blok": "cek_tagihan",
             "masukan": {"provider": "PLN", "nomor_pelanggan": ""},
             "judul": "Cek tagihan PLN"},
            {"blok": "cek_tagihan",
             "masukan": {"provider": "PDAM", "nomor_pelanggan": ""},
             "judul": "Cek tagihan PDAM"},
            {"blok": "kirim_notif",
             "masukan": {"pesan": "Tagihan PLN dan PDAM sudah dicek — lihat rinciannya."},
             "judul": "Ingatkan aku"},
            {"blok": "siapkan_bayar",
             "masukan": {"provider": "PLN", "jumlah": 0, "tujuan": "PLN"},
             "judul": "Siapkan bayar (mode aman)"},
        ],
    },
    "pantau_harga": {
        "nama": "Pantau harga barang",
        "deskripsi": "Pantau harga barang di watchlist; beri tahu bila turun di bawah ambang.",
        "pemicu": {"jenis": "harga_turun", "produk_id": "samsung-galaxy-s25",
                   "ambang_harga": 10000000},
        "langkah": [
            {"blok": "pantau_harga",
             "masukan": {"produk_id": "samsung-galaxy-s25",
                         "ambang_harga": 10000000},
             "judul": "Pantau harga"},
            {"blok": "kondisi",
             "masukan": {"ungkapan": "pantau_harga.di_bawah_ambang == True"},
             "maka": [{"blok": "kirim_notif",
                       "masukan": {"pesan": "Harga barang yang kamu pantau turun di bawah ambang."},
                       "judul": "Beri tahu harga turun"}],
             "kalau_tidak": [],
             "judul": "Kalau harga turun"},
        ],
    },
    "rekap_tagihan_mingguan": {
        "nama": "Rekap tagihan minggu ini",
        "deskripsi": "Tiap Senin ingatkan semua tagihan yang jatuh tempo minggu ini.",
        "pemicu": {"jenis": "jadwal", "mode": "hari", "hari": "senin",
                   "jam_mulai": "07:00"},
        "langkah": [
            {"blok": "cek_tagihan",
             "masukan": {"provider": "PLN", "nomor_pelanggan": ""},
             "judul": "Cek tagihan PLN"},
            {"blok": "cek_tagihan",
             "masukan": {"provider": "PDAM", "nomor_pelanggan": ""},
             "judul": "Cek tagihan PDAM"},
            {"blok": "cek_tagihan",
             "masukan": {"provider": "pulsa", "nomor_pelanggan": ""},
             "judul": "Cek tagihan pulsa"},
            {"blok": "cek_tagihan",
             "masukan": {"provider": "paket data", "nomor_pelanggan": ""},
             "judul": "Cek tagihan paket data"},
            {"blok": "kirim_notif",
             "masukan": {"pesan": "Rekap tagihan minggu ini sudah siap — lihat rinciannya."},
             "judul": "Ingatkan semua tagihan"},
        ],
    },
}

# Tugas acuan (§8) yang berjalan sebagai alur.
TUGAS_ACUAN_ALUR = {
    8: "tagihan_bulanan",       # "Buatkan pengingat untuk tagihan."
    9: "rekap_tagihan_mingguan",  # "Ingatkan semua tagihan yang jatuh tempo minggu ini."
}


def daftar_templat() -> list[dict]:
    """Tiga templat bawaan (nama + deskripsi + pemicu + langkah)."""
    return [dict(v, kunci=k) for k, v in TEMPLAT.items()]


def ambil_templat(kunci: str) -> dict:
    if kunci not in TEMPLAT:
        raise KeyError(f"templat {kunci!r} tidak ada (hanya 3 templat bawaan)")
    return dict(TEMPLAT[kunci])


def buat_dari_templat(kunci: str, *, user_id: str, agen_id: str,
                      pemicu: dict | None = None, dsn=None) -> dict:
    """Buat alur draf dari templat (pemicu boleh ditimpa — misal tugas
    acuan 8 memakai pemicu manual)."""
    from puspa.workflow.runner import buat_alur

    templat = ambil_templat(kunci)
    return buat_alur(
        user_id=user_id, agen_id=agen_id, nama=templat["nama"],
        pemicu=pemicu if pemicu is not None else dict(templat["pemicu"]),
        langkah=[dict(l) for l in templat["langkah"]], dsn=dsn)
