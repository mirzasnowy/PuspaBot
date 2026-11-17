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

MANIFEST_BILL = {
    "name": "Tagihan",
    "workflows": {
        "triggers": {"type": "schedule", "mode": "date", "date": 25,
                   "start_time": "07:00"},
        "step": [
            {"block": "check_bill",
             "masukan": {"provider": "PLN", "number_pelanggan": ""},
             "title": "Cek tagihan PLN"},
            {"block": "check_bill",
             "masukan": {"provider": "PDAM", "number_pelanggan": ""},
             "title": "Cek tagihan PDAM"},
            {"block": "save_to_module_table",
             "masukan": {"modules": "Tagihan", "table": "bill",
                         "rows": []},
             "title": "Simpan ke tabel tagihan"},
            {"block": "send_notification",
             "masukan": {"message": "Tagihan PLN dan PDAM sudah dicek."},
             "title": "Ingatkan aku"},
        ],
    },
    "tampilan": [
        {"type": "number", "title": "Total tagihan bulan ini",
         "table": "bill", "kolom": "amount", "format": "Rupiah"},
        {"type": "table", "title": "Daftar tagihan",
         "table": "bill",
         "kolom": ["provider", "amount", "due_date", "status"]},
        {"type": "grafik_garis", "title": "Jumlah tagihan per baris",
         "table": "bill", "kolom": "amount"},
    ],
    "table": [
        {"name": "bill", "kolom": [
            {"name": "provider", "tipe": "choice",
             "choice": ["PLN", "PDAM", "airtime", "paket data"]},
            {"name": "amount", "tipe": "Rupiah"},
            {"name": "due_date", "tipe": "date"},
            {"name": "status", "tipe": "choice",
             "choice": ["not_yet_pay", "already_pay", "active"]},
            {"name": "catatan", "tipe": "text"},
        ]},
    ],
    "notif": [
        {"table": "bill", "condition": "status == not_yet_pay",
         "message": "Ada tagihan yang belum dibayar — cek sebelum jatuh tempo."},
    ],
    "example_data": {
        "bill": [
            {"provider": "PLN", "amount": 132500, "due_date": "2026-11-20",
             "status": "not_yet_pay", "catatan": "November 2026"},
            {"provider": "PDAM", "amount": 87500, "due_date": "2026-11-25",
             "status": "not_yet_pay", "catatan": "November 2026"},
        ],
    },
}


def create_module_bill(*, user_id: str, dsn=None) -> dict:
    """Buat modul Tagihan dari manifes di atas (draf)."""
    from puspa.modules.manifest import create_module

    return create_module(user_id=user_id, manifest=dict(MANIFEST_BILL), dsn=dsn)
