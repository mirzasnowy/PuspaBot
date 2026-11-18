"""Aturan notif modul (§2.13 revisi 7).

Aturan kirim notif berdasarkan data modul atau pemicu alur. Satu
aturan: {"kondisi": "<field> <op> <nilai>", "pesan": "..."}.
Dievaluasi terhadap tiap baris tabel yang ditunjuk
({"tabel": ..., "kondisi": ..., "pesan": ...}); baris yang cocok
menghasilkan satu pesan notif. Evaluasi oleh kode (pemakaian ulang
evaluator kondisi alur) — bukan model.
"""

from __future__ import annotations

from puspa.workflow.runner import evaluate_expression


def evaluate_notifications(manifest: dict, data: dict) -> list[dict]:
    """Kembalikan daftar notif yang harus dikirim:
    [{tabel, baris, pesan}]."""
    output = []
    for rule in manifest.get("notifications", []) or []:
        table = rule.get("table")
        rows_list = data.get(table, []) if table else []
        for row in rows_list:
            try:
                if evaluate_expression(rule.get("condition", ""), dict(row)):
                    output.append({"table": table, "rows": row, "message": rule.get("message", "")})
            except Exception:
                continue
    return output
