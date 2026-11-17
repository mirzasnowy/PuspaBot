"""Aturan notif modul (§2.13 revisi 7).

Aturan kirim notif berdasarkan data modul atau pemicu alur. Satu
aturan: {"kondisi": "<field> <op> <nilai>", "pesan": "..."}.
Dievaluasi terhadap tiap baris tabel yang ditunjuk
({"tabel": ..., "kondisi": ..., "pesan": ...}); baris yang cocok
menghasilkan satu pesan notif. Evaluasi oleh kode (pemakaian ulang
evaluator kondisi alur) — bukan model.
"""

from __future__ import annotations

from puspa.workflow.runner import evaluasi_ungkapan


def evaluasi_notif(manifest: dict, data: dict) -> list[dict]:
    """Kembalikan daftar notif yang harus dikirim:
    [{tabel, baris, pesan}]."""
    logout = []
    for aturan in manifest.get("notif", []) or []:
        table = aturan.get("table")
        rows_list = data.get(table, []) if table else []
        for rows in rows_list:
            try:
                if evaluasi_ungkapan(aturan.get("condition", ""), dict(rows)):
                    logout.append({"table": table, "rows": rows,
                                   "message": aturan.get("message", "")})
            except Exception:  # noqa: BLE001 — kondisi rusak = tidak memicu
                continue
    return logout
