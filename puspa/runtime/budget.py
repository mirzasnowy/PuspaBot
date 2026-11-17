"""Anggaran per tugas (§2.4 revisi 7).

- Maks 25 langkah per tugas untuk agen, maks 15 langkah per sub-agen.
- Anggaran menyempit ke bawah: total anggaran sub-agen tidak boleh
  melebihi sisa anggaran agennya.
- Plafon biaya per tugas Rp2.000 (angka awal, dikalibrasi dari Buku
  Kerja di Fase 2 — lihat `kalibrasi_dari_ledger`). Kalau terlampaui,
  tugas berhenti dan melapor, bukan lanjut diam-diam.

Biaya token dihitung dari tarif per 1.000 token di `puspa.router`
(tarif konfigurasi, bukan angka karangan — lihat `router.py`).
"""

from __future__ import annotations

CEILING_RP_PER_TASK = 2000
CEILING_RP_PER_JALAN_WORKFLOW = 5000
STEP_MAX_AGENT = 25
STEP_MAX_SUBAGEN = 15


class AnggaranHabis(Exception):
    """Plafon biaya / langkah terlampaui: tugas harus berhenti."""


class BudgetTracker:
    """Pelacak anggaran satu tugas (agen + sub-agennya)."""

    def __init__(self, rupiah_max: int = CEILING_RP_PER_TASK,
                 step_max: int = STEP_MAX_AGENT) -> None:
        self.rupiah_max = rupiah_max
        self.step_max = step_max
        self.rupiah_terpakai = 0
        self.step_terpakai = 0
        self.token_terpakai = 0

    @property
    def sisa_rupiah(self) -> int:
        return self.rupiah_max - self.rupiah_terpakai

    @property
    def sisa_step(self) -> int:
        return self.step_max - self.step_terpakai

    def record_step(self, amount: int = 1) -> None:
        if self.step_terpakai + amount > self.step_max:
            raise AnggaranHabis(
                f"langkah melewati batas {self.step_max} "
                f"({self.step_terpakai} + {amount})"
            )
        self.step_terpakai += amount

    def record_cost(self, rupiah: int, token: int = 0) -> None:
        if self.rupiah_terpakai + rupiah > self.rupiah_max:
            raise AnggaranHabis(
                f"biaya melewati plafon Rp{self.rupiah_max} "
                f"(terpakai Rp{self.rupiah_terpakai} + Rp{rupiah})"
            )
        self.rupiah_terpakai += rupiah
        self.token_terpakai += token

    def anggaran_subagen(self, step_max: int = STEP_MAX_SUBAGEN,
                         rupiah_max: int | None = None) -> dict:
        """Anggaran untuk satu sub-agen: tidak melebihi sisa agennya."""
        return {
            "step_max": min(step_max, self.sisa_step),
            "token_max": 20000,
            "rupiah_max": self.sisa_rupiah if rupiah_max is None else min(rupiah_max, self.sisa_rupiah),
        }


def kalibrasi_dari_ledger(event: list[dict]) -> dict:
    """Kalibrasi plafon dari Buku Kerja: biaya per tugas dari kejadian.

    `kejadian` = daftar kejadian ledger (dict dengan `isi` yang boleh
    memuat `biaya_rp`). Kembalikan rata-rata, median, maks, dan jumlah
    tugas. Kalau tidak ada data biaya, kembalikan angka kosong dengan
    jujur (bukan plafon karangan).
    """
    per_task: dict[str, int] = {}
    for ev in event:
        content = ev.get("content") or {}
        cost = content.get("cost_rp")
        if isinstance(cost, (int, float)):
            per_task[ev.get("task_id", "?")] = per_task.get(ev.get("task_id", "?"), 0) + int(cost)
    value = sorted(per_task.values())
    if not value:
        return {"amount_task": 0, "rata_rata_rp": None, "median_rp": None,
                "maks_rp": None, "current_ceiling_idr": CEILING_RP_PER_TASK}
    tengah = value[len(value) // 2] if len(value) % 2 == 1 else (
        value[len(value) // 2 - 1] + value[len(value) // 2]) // 2
    return {"amount_task": len(value),
            "rata_rata_rp": sum(value) // len(value),
            "median_rp": tengah, "maks_rp": value[-1],
            "current_ceiling_idr": CEILING_RP_PER_TASK}
