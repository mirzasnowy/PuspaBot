"""Modul bawaan Tagihan — ditulis sebagai manifes (§2.13 revisi 7).

Ini bukti format manifes cukup ekspresif: modul Tagihan (yang di
Fase 3 dibangun sebagai kode wedge) diekspresikan penuh sebagai
manifes deklaratif — alur cek tagihan + tampilan + data + notif.
Bandingkan Harga TETAP bawaan (butuh tampilan khusus) dan tidak
ditulis sebagai manifes.

Kalau suatu saat Tagihan tidak bisa diekspresikan dengan format ini,
itu TEMUAN (format diperbaiki, bukan dilewati) — di Fase 4 ini
manifes lolos kelima gerbang (diuji di test_tagihan_manifest.py).
"""

from __future__ import annotations

MANIFES_TAGIHAN = {
    "nama": "Tagihan",
    "alur": {
        "pemicu": {"jenis": "jadwal", "mode": "tanggal", "tanggal": 25,
                   "jam_mulai": "07:00"},
        "langkah": [
            {"blok": "cek_tagihan",
             "masukan": {"provider": "PLN", "nomor_pelanggan": ""},
             "judul": "Cek tagihan PLN"},
            {"blok": "cek_tagihan",
             "masukan": {"provider": "PDAM", "nomor_pelanggan": ""},
             "judul": "Cek tagihan PDAM"},
            {"blok": "simpan_ke_tabel_modul",
             "masukan": {"modul": "Tagihan", "tabel": "tagihan",
                         "baris": []},
             "judul": "Simpan ke tabel tagihan"},
            {"blok": "kirim_notif",
             "masukan": {"pesan": "Tagihan PLN dan PDAM sudah dicek."},
             "judul": "Ingatkan aku"},
        ],
    },
    "tampilan": [
        {"jenis": "angka", "judul": "Total tagihan bulan ini",
         "tabel": "tagihan", "kolom": "jumlah", "format": "Rupiah"},
        {"jenis": "tabel", "judul": "Daftar tagihan",
         "tabel": "tagihan",
         "kolom": ["provider", "jumlah", "jatuh_tempo", "status"]},
        {"jenis": "grafik_garis", "judul": "Jumlah tagihan per baris",
         "tabel": "tagihan", "kolom": "jumlah"},
    ],
    "tabel": [
        {"nama": "tagihan", "kolom": [
            {"nama": "provider", "tipe": "pilihan",
             "pilihan": ["PLN", "PDAM", "pulsa", "paket data"]},
            {"nama": "jumlah", "tipe": "Rupiah"},
            {"nama": "jatuh_tempo", "tipe": "tanggal"},
            {"nama": "status", "tipe": "pilihan",
             "pilihan": ["belum_bayar", "sudah_bayar", "aktif"]},
            {"nama": "catatan", "tipe": "teks"},
        ]},
    ],
    "notif": [
        {"tabel": "tagihan", "kondisi": "status == belum_bayar",
         "pesan": "Ada tagihan yang belum dibayar — cek sebelum jatuh tempo."},
    ],
    "contoh_data": {
        "tagihan": [
            {"provider": "PLN", "jumlah": 132500, "jatuh_tempo": "2026-11-20",
             "status": "belum_bayar", "catatan": "November 2026"},
            {"provider": "PDAM", "jumlah": 87500, "jatuh_tempo": "2026-11-25",
             "status": "belum_bayar", "catatan": "November 2026"},
        ],
    },
}


def buat_modul_tagihan(*, user_id: str, dsn=None) -> dict:
    """Buat modul Tagihan dari manifes di atas (draf)."""
    from puspa.modules.manifest import buat_modul

    return buat_modul(user_id=user_id, manifes=dict(MANIFES_TAGIHAN), dsn=dsn)
