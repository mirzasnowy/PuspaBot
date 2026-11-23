"""Metrik alur (§3.8 revisi 7) — fondasi gerbang 8 + gerbang 13.

- minimal 5 dari 10 pengguna uji punya satu alur aktif setelah 2 minggu;
- minimal 50% jalan alur selesai tanpa campur tangan;
- Permintaan Blok tercatat dan diurutkan.

Tanpa pengguna uji, metrik pertama dilaporkan `tertunda` (bukan
diklaim). Jalan tanpa campur tangan terukur lokal dari jalan_alur.
"""

from __future__ import annotations

import os

from puspa.db import connect as _db_connect
from puspa.db import dict_row

DEFAULT_DSN = None
TARGET_USER_ACTIVE = 5
TARGET_RUNS_WITHOUT_INTERVENTION = 50.0


def _connect(dsn=None):
    return _db_connect(
        dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN), row_factory=dict_row
    )


def workflow_metrics(dsn=None) -> dict:
    """Hitung metrik alur dari tabel Fase 4 + pustaka Fase 3."""
    with _connect(dsn) as conn, conn.cursor() as cur:
        cur.execute("SELECT COUNT(DISTINCT user_id) AS n FROM workflows WHERE status = 'active'")
        user_active = int(cur.fetchone()["n"])
        cur.execute("SELECT status, COUNT(*) AS n FROM workflow_runs GROUP BY status")
        runs = {r["status"]: int(r["n"]) for r in cur.fetchall()}
        cur.execute(
            "SELECT block, COUNT(*) AS n FROM block_requests GROUP BY block ORDER BY n DESC"
        )
        requests = [(r["block"], int(r["n"])) for r in cur.fetchall()]
    total_runs = sum(runs.values())
    completed_count = runs.get("completed", 0)
    without_intervention = round(completed_count / total_runs * 100, 1) if total_runs else None
    return {
        "users_with_active_workflow": user_active,
        "target_user_active": TARGET_USER_ACTIVE,
        "active_user_status": ("pending" if user_active < TARGET_USER_ACTIVE else "passed"),
        "active_user_reason": (
            "butuh pengguna uji (10 orang) + masa uji 2 minggu"
            if user_active < TARGET_USER_ACTIVE
            else ""
        ),
        "total_runs": total_runs,
        "completed_runs_without_intervention": completed_count,
        "percent_runs_without_intervention": without_intervention,
        "target_runs_without_intervention": TARGET_RUNS_WITHOUT_INTERVENTION,
        "block_requests": [{"block": b, "amount": n} for b, n in requests],
    }
