"""Templat bawaan (§2.12 revisi 7) — 3 templat.

Tugas acuan 8 dan 9 berjalan sebagai alur:
- Tugas 8 ("Buatkan pengingat untuk tagihan") = templat Tagihan bulanan
  dengan pemicu manual.
- Tugas 9 ("Ingatkan semua tagihan yang jatuh tempo minggu ini") =
  templat Rekap tagihan minggu ini.
"""

from __future__ import annotations

TEMPLATES = {
    "bill_monthly": {
        "name": "Tagihan bulanan",
        "description": "Tiap tanggal 25 cek PLN dan PDAM, ingatkan aku, siapkan bayar.",
        "triggers": {"type": "schedule", "mode": "date", "date": 25,
                   "start_time": "07:00"},
        "step": [
            {"block": "check_bill",
             "inputs": {"provider": "PLN", "customer_number": ""},
             "title": "Cek tagihan PLN"},
            {"block": "check_bill",
             "inputs": {"provider": "PDAM", "customer_number": ""},
             "title": "Cek tagihan PDAM"},
            {"block": "send_notification",
             "inputs": {"message": "Tagihan PLN dan PDAM sudah dicek — lihat rinciannya."},
             "title": "Ingatkan aku"},
            {"block": "prepare_payment",
             "inputs": {"provider": "PLN", "amount": 0, "purpose": "PLN"},
             "title": "Siapkan bayar (mode aman)"},
        ],
    },
    "watch_prices": {
        "name": "Pantau harga barang",
        "description": "Pantau harga barang di watchlist; beri tahu bila turun di bawah ambang.",
        "triggers": {"type": "price_drop", "product_id": "samsung-galaxy-s25",
                   "price_threshold": 10000000},
        "step": [
            {"block": "watch_prices",
             "inputs": {"product_id": "samsung-galaxy-s25",
                         "price_threshold": 10000000},
             "title": "Pantau harga"},
            {"block": "condition",
             "inputs": {"expression": "watch_price.below_threshold == True"},
             "then": [{"block": "send_notification",
                       "inputs": {"message": "Harga barang yang kamu pantau turun di bawah ambang."},
                       "title": "Beri tahu harga turun"}],
             "else": [],
             "title": "Kalau harga turun"},
        ],
    },
    "rekap_bill_weekly": {
        "name": "Rekap tagihan minggu ini",
        "description": "Tiap Senin ingatkan semua tagihan yang jatuh tempo minggu ini.",
        "triggers": {"type": "schedule", "mode": "day", "day": "senin",
                   "start_time": "07:00"},
        "step": [
            {"block": "check_bill",
             "inputs": {"provider": "PLN", "customer_number": ""},
             "title": "Cek tagihan PLN"},
            {"block": "check_bill",
             "inputs": {"provider": "PDAM", "customer_number": ""},
             "title": "Cek tagihan PDAM"},
            {"block": "check_bill",
             "inputs": {"provider": "airtime", "customer_number": ""},
             "title": "Cek tagihan pulsa"},
            {"block": "check_bill",
             "inputs": {"provider": "paket data", "customer_number": ""},
             "title": "Cek tagihan paket data"},
            {"block": "send_notification",
             "inputs": {"message": "Rekap tagihan minggu ini sudah siap — lihat rinciannya."},
             "title": "Ingatkan semua tagihan"},
        ],
    },
}

# Tugas acuan (§8) yang berjalan sebagai alur.
TASK_REFERENCE_WORKFLOW = {
    8: "bill_monthly",       # "Buatkan pengingat untuk tagihan."
    9: "rekap_bill_weekly",  # "Ingatkan semua tagihan yang jatuh tempo minggu ini."
}


def list_templates() -> list[dict]:
    """Tiga templat bawaan (nama + deskripsi + pemicu + langkah)."""
    return [dict(v, key=k) for k, v in TEMPLATES.items()]


def get_template(key: str) -> dict:
    if key not in TEMPLATES:
        raise KeyError(f"templat {key!r} tidak ada (hanya 3 templat bawaan)")
    return dict(TEMPLATES[key])


def create_from_template(key: str, *, user_id: str, agent_id: str,
                      trigger: dict | None = None, dsn=None) -> dict:
    """Buat alur draf dari templat (pemicu boleh ditimpa — misal tugas
    acuan 8 memakai pemicu manual)."""
    from puspa.workflow.runner import create_workflow

    template = get_template(key)
    return create_workflow(
        user_id=user_id, agent_id=agent_id, name=template["name"],
        trigger=trigger if trigger is not None else dict(template["triggers"]),
        step=[dict(l) for l in template["step"]], dsn=dsn)
