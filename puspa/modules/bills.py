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
        "triggers": {"type": "schedule", "mode": "date", "date": 25, "start_time": "07:00"},
        "step": [
            {
                "block": "check_bill",
                "inputs": {"provider": "PLN", "customer_number": ""},
                "title": "Cek tagihan PLN",
            },
            {
                "block": "check_bill",
                "inputs": {"provider": "PDAM", "customer_number": ""},
                "title": "Cek tagihan PDAM",
            },
            {
                "block": "save_to_module_table",
                "inputs": {"modules": "Tagihan", "table": "bill", "rows": []},
                "title": "Simpan ke tabel tagihan",
            },
            {
                "block": "send_notification",
                "inputs": {"message": "Tagihan PLN dan PDAM sudah dicek."},
                "title": "Ingatkan aku",
            },
        ],
    },
    "views": [
        {
            "type": "number",
            "title": "Total tagihan bulan ini",
            "table": "bill",
            "columns": "amount",
            "format": "Rupiah",
        },
        {
            "type": "table",
            "title": "Daftar tagihan",
            "table": "bill",
            "columns": ["provider", "amount", "due_date", "status"],
        },
        {
            "type": "line_chart",
            "title": "Jumlah tagihan per baris",
            "table": "bill",
            "columns": "amount",
        },
    ],
    "table": [
        {
            "name": "bill",
            "columns": [
                {
                    "name": "provider",
                    "type": "choice",
                    "choice": ["PLN", "PDAM", "airtime", "paket data"],
                },
                {"name": "amount", "type": "Rupiah"},
                {"name": "due_date", "type": "date"},
                {
                    "name": "status",
                    "type": "choice",
                    "choice": ["not_yet_pay", "already_pay", "active"],
                },
                {"name": "notes", "type": "text"},
            ],
        },
    ],
    "notifications": [
        {
            "table": "bill",
            "condition": "status == not_yet_pay",
            "message": "Ada tagihan yang belum dibayar — cek sebelum jatuh tempo.",
        },
    ],
    "example_data": {
        "bill": [
            {
                "provider": "PLN",
                "amount": 132500,
                "due_date": "2026-11-20",
                "status": "not_yet_pay",
                "notes": "November 2026",
            },
            {
                "provider": "PDAM",
                "amount": 87500,
                "due_date": "2026-11-25",
                "status": "not_yet_pay",
                "notes": "November 2026",
            },
        ],
    },
}


def create_module_bill(*, user_id: str, dsn=None) -> dict:
    """Buat modul Tagihan dari manifes di atas (draf)."""
    from puspa.modules.manifest import create_module

    return create_module(user_id=user_id, manifest=dict(MANIFEST_BILL), dsn=dsn)
