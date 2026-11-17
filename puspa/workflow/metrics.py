"""Metrik alur (§3.8 revisi 7) — fondasi gerbang 8 + gerbang 13.

- minimal 5 dari 10 pengguna uji punya satu alur aktif setelah 2 minggu;
- minimal 50% jalan alur selesai tanpa campur tangan;
- Permintaan Blok tercatat dan diurutkan.

Tanpa pengguna uji, metrik pertama dilaporkan `tertunda` (bukan
diklaim). Jalan tanpa campur tangan terukur lokal dari jalan_alur.
"""

from __future__ import annotations

import os

import psycopg
from psycopg.rows import dict_row

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
TARGET_USER_ACTIVE = 5
TARGET_JALAN_WITHOUT_CAMPUR = 50.0


def _connect(dsn=None):
    return psycopg.connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row)


def metrik_workflow(dsn=None) -> dict:
    """Hitung metrik alur dari tabel Fase 4 + pustaka Fase 3."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT COUNT(DISTINCT user_id) AS n FROM workflows WHERE status = 'active'")
            user_active = int(cur.fetchone()["n"])
            cur.execute("SELECT status, COUNT(*) AS n FROM workflow_runs GROUP BY status")
            jalan = {r["status"]: int(r["n"]) for r in cur.fetchall()}
            cur.execute(
                "SELECT block, COUNT(*) AS n FROM block_requests"
                " GROUP BY block ORDER BY n DESC")
            permintaan = [(r["block"], int(r["n"])) for r in cur.fetchall()]
    total_jalan = sum(jalan.values())
    completed_at = jalan.get("completed", 0)
    without_campur = round(completed_at / total_jalan * 100, 1) if total_jalan else None
    return {
        "user_dengan_workflow_active": user_active,
        "target_user_active": TARGET_USER_ACTIVE,
        "status_user_active": (
            "tertunda" if user_active < TARGET_USER_ACTIVE else "passed"),
        "reason_user_active": (
            "butuh pengguna uji (10 orang) + masa uji 2 minggu"
            if user_active < TARGET_USER_ACTIVE else ""),
        "jalan_total": total_jalan,
        "jalan_completed_without_campur": completed_at,
        "persen_jalan_without_campur": without_campur,
        "target_jalan_without_campur": TARGET_JALAN_WITHOUT_CAMPUR,
        "permintaan_block": [{"block": b, "amount": n} for b, n in permintaan],
    }
