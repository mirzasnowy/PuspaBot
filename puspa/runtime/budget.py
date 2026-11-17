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
CEILING_RP_PER_WORKFLOW_RUN = 5000
STEP_MAX_AGENT = 25
STEP_MAX_SUBAGENT = 15


class BudgetExhausted(Exception):
    """Plafon biaya / langkah terlampaui: tugas harus berhenti."""


class BudgetTracker:
    """Pelacak anggaran satu tugas (agen + sub-agennya)."""

    def __init__(self, rupiah_max: int = CEILING_RP_PER_TASK,
                 step_max: int = STEP_MAX_AGENT) -> None:
        self.rupiah_max = rupiah_max
        self.step_max = step_max
        self.rupiah_used = 0
        self.steps_used = 0
        self.tokens_used = 0

    @property
    def remaining_rupiah(self) -> int:
        return self.rupiah_max - self.rupiah_used

    @property
    def remaining_steps(self) -> int:
        return self.step_max - self.steps_used

    def record_step(self, amount: int = 1) -> None:
        if self.steps_used + amount > self.step_max:
            raise BudgetExhausted(
                f"langkah melewati batas {self.step_max} "
                f"({self.steps_used} + {amount})"
            )
        self.steps_used += amount

    def record_cost(self, rupiah: int, token: int = 0) -> None:
        if self.rupiah_used + rupiah > self.rupiah_max:
            raise BudgetExhausted(
                f"biaya melewati plafon Rp{self.rupiah_max} "
                f"(terpakai Rp{self.rupiah_used} + Rp{rupiah})"
            )
        self.rupiah_used += rupiah
        self.tokens_used += token

    def subagent_budget(self, step_max: int = STEP_MAX_SUBAGENT,
                         rupiah_max: int | None = None) -> dict:
        """Anggaran untuk satu sub-agen: tidak melebihi sisa agennya."""
        return {
            "step_max": min(step_max, self.remaining_steps),
            "token_max": 20000,
            "rupiah_max": self.remaining_rupiah if rupiah_max is None else min(rupiah_max, self.remaining_rupiah),
        }


def calibrate_from_ledger(event: list[dict]) -> dict:
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
        return {"amount_task": 0, "average_rp": None, "median_rp": None,
                "max_rp": None, "current_ceiling_idr": CEILING_RP_PER_TASK}
    median = value[len(value) // 2] if len(value) % 2 == 1 else (
        value[len(value) // 2 - 1] + value[len(value) // 2]) // 2
    return {"amount_task": len(value),
            "average_rp": sum(value) // len(value),
            "median_rp": median, "max_rp": value[-1],
            "current_ceiling_idr": CEILING_RP_PER_TASK}
