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

PLAFON_RP_PER_TUGAS = 2000
PLAFON_RP_PER_JALAN_ALUR = 5000
LANGKAH_MAX_AGEN = 25
LANGKAH_MAX_SUBAGEN = 15


class AnggaranHabis(Exception):
    """Plafon biaya / langkah terlampaui: tugas harus berhenti."""


class BudgetTracker:
    """Pelacak anggaran satu tugas (agen + sub-agennya)."""

    def __init__(self, rupiah_max: int = PLAFON_RP_PER_TUGAS,
                 langkah_max: int = LANGKAH_MAX_AGEN) -> None:
        self.rupiah_max = rupiah_max
        self.langkah_max = langkah_max
        self.rupiah_terpakai = 0
        self.langkah_terpakai = 0
        self.token_terpakai = 0

    @property
    def sisa_rupiah(self) -> int:
        return self.rupiah_max - self.rupiah_terpakai

    @property
    def sisa_langkah(self) -> int:
        return self.langkah_max - self.langkah_terpakai

    def catat_langkah(self, jumlah: int = 1) -> None:
        if self.langkah_terpakai + jumlah > self.langkah_max:
            raise AnggaranHabis(
                f"langkah melewati batas {self.langkah_max} "
                f"({self.langkah_terpakai} + {jumlah})"
            )
        self.langkah_terpakai += jumlah

    def catat_biaya(self, rupiah: int, token: int = 0) -> None:
        if self.rupiah_terpakai + rupiah > self.rupiah_max:
            raise AnggaranHabis(
                f"biaya melewati plafon Rp{self.rupiah_max} "
                f"(terpakai Rp{self.rupiah_terpakai} + Rp{rupiah})"
            )
        self.rupiah_terpakai += rupiah
        self.token_terpakai += token

    def anggaran_subagen(self, langkah_max: int = LANGKAH_MAX_SUBAGEN,
                         rupiah_max: int | None = None) -> dict:
        """Anggaran untuk satu sub-agen: tidak melebihi sisa agennya."""
        return {
            "langkah_max": min(langkah_max, self.sisa_langkah),
            "token_max": 20000,
            "rupiah_max": self.sisa_rupiah if rupiah_max is None else min(rupiah_max, self.sisa_rupiah),
        }


def kalibrasi_dari_ledger(kejadian: list[dict]) -> dict:
    """Kalibrasi plafon dari Buku Kerja: biaya per tugas dari kejadian.

    `kejadian` = daftar kejadian ledger (dict dengan `isi` yang boleh
    memuat `biaya_rp`). Kembalikan rata-rata, median, maks, dan jumlah
    tugas. Kalau tidak ada data biaya, kembalikan angka kosong dengan
    jujur (bukan plafon karangan).
    """
    per_tugas: dict[str, int] = {}
    for ev in kejadian:
        isi = ev.get("isi") or {}
        biaya = isi.get("biaya_rp")
        if isinstance(biaya, (int, float)):
            per_tugas[ev.get("tugas_id", "?")] = per_tugas.get(ev.get("tugas_id", "?"), 0) + int(biaya)
    nilai = sorted(per_tugas.values())
    if not nilai:
        return {"jumlah_tugas": 0, "rata_rata_rp": None, "median_rp": None,
                "maks_rp": None, "plafon_saat_ini_rp": PLAFON_RP_PER_TUGAS}
    tengah = nilai[len(nilai) // 2] if len(nilai) % 2 == 1 else (
        nilai[len(nilai) // 2 - 1] + nilai[len(nilai) // 2]) // 2
    return {"jumlah_tugas": len(nilai),
            "rata_rata_rp": sum(nilai) // len(nilai),
            "median_rp": tengah, "maks_rp": nilai[-1],
            "plafon_saat_ini_rp": PLAFON_RP_PER_TUGAS}
