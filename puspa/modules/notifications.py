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


def evaluasi_notif(manifes: dict, data: dict) -> list[dict]:
    """Kembalikan daftar notif yang harus dikirim:
    [{tabel, baris, pesan}]."""
    keluar = []
    for aturan in manifes.get("notif", []) or []:
        tabel = aturan.get("tabel")
        baris_list = data.get(tabel, []) if tabel else []
        for baris in baris_list:
            try:
                if evaluasi_ungkapan(aturan.get("kondisi", ""), dict(baris)):
                    keluar.append({"tabel": tabel, "baris": baris,
                                   "pesan": aturan.get("pesan", "")})
            except Exception:  # noqa: BLE001 — kondisi rusak = tidak memicu
                continue
    return keluar
