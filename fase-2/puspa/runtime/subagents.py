"""Kolam sub-agen (§2.2 revisi 7).

- Sub-agen dibuat agen sesuai tugasnya (ephemeral: hidup selama tugas).
- Satu level saja: sub-agen tidak bisa membuat sub-agen.
- Batas paralel: maks 4 sub-agen aktif per agen, maks 6 di seluruh
  sistem; berikutnya antre.
- Token sub-agen harus himpunan bagian dari token agennya
  (`puspa.policy.tokens.is_subset`, diperiksa kode).
- Hentikan: mematikan semua sub-agen tugas itu dan mencabut token
  izinnya (`revoke_all_for_tugas` Fase 0) — target < 5 detik.
"""

from __future__ import annotations

import time
import uuid
from dataclasses import dataclass, field

from puspa.policy.tokens import is_subset, revoke_all_for_tugas

MAX_SUB_PER_AGEN = 4
MAX_SUB_SISTEM = 6
KELAS_IZIN = ("Pembaca", "Pelaksana", "Pemeriksa")


class SubagentError(Exception):
    """Spawn / batas sub-agen dilanggar."""


@dataclass
class Subagent:
    """Satu sub-agen aktif (catatan kolam; eksekusi di runtime)."""

    sub_agen_id: str
    agen_id: str
    tugas_id: str
    kelas_izin: str
    token_id: str = ""
    status: str = "aktif"  # aktif | antre | selesai | dihentikan


class SubagentPool:
    """Kolam sub-agen aktif seluruh sistem (in-memory, per proses)."""

    def __init__(self) -> None:
        self._aktif: dict[str, Subagent] = {}
        self._antre: list[Subagent] = []

    def spawn(self, *, agen_id: str, tugas_id: str, kelas_izin: str,
              token_agen: dict, token_subagen: dict,
              pembuat_adalah_subagen: bool = False) -> Subagent:
        """Buat sub-agen baru (atau antrekan bila kolam penuh).

        `token_agen` / `token_subagen` adalah payload dict yang sudah
        diverifikasi. Token sub-agen yang bukan subset token agen
        ditolak sebelum apa pun dibuat.
        """
        if pembuat_adalah_subagen:
            raise SubagentError("sub-agen tidak bisa membuat sub-agen (satu level saja)")
        if kelas_izin not in KELAS_IZIN:
            raise SubagentError(f"kelas_izin harus salah satu dari {KELAS_IZIN}")
        if not is_subset(token_subagen, token_agen):
            raise SubagentError("token sub-agen bukan himpunan bagian dari token agennya")
        sub = Subagent(
            sub_agen_id=f"sub-{uuid.uuid4().hex[:12]}",
            agen_id=agen_id, tugas_id=tugas_id, kelas_izin=kelas_izin,
        )
        per_agen = sum(1 for s in self._aktif.values() if s.agen_id == agen_id)
        if per_agen >= MAX_SUB_PER_AGEN or len(self._aktif) >= MAX_SUB_SISTEM:
            sub.status = "antre"
            self._antre.append(sub)
            return sub
        self._aktif[sub.sub_agen_id] = sub
        return sub

    def selesai(self, sub_agen_id: str) -> Subagent | None:
        """Tandai sub-agen selesai; majukan satu antrean bila ada."""
        sub = self._aktif.pop(sub_agen_id, None)
        if sub is None:
            for antre in self._antre:
                if antre.sub_agen_id == sub_agen_id:
                    self._antre.remove(antre)
                    antre.status = "selesai"
                    return antre
            return None
        sub.status = "selesai"
        self._majukan_antrean()
        return sub

    def _majukan_antrean(self) -> None:
        while self._antre and len(self._aktif) < MAX_SUB_SISTEM:
            calon = self._antre[0]
            per_agen = sum(1 for s in self._aktif.values() if s.agen_id == calon.agen_id)
            if per_agen >= MAX_SUB_PER_AGEN:
                break
            self._antre.pop(0)
            calon.status = "aktif"
            self._aktif[calon.sub_agen_id] = calon

    def aktif_per_agen(self, agen_id: str) -> list[Subagent]:
        return [s for s in self._aktif.values() if s.agen_id == agen_id]

    def jumlah_aktif(self) -> int:
        return len(self._aktif)

    def jumlah_antre(self) -> int:
        return len(self._antre)

    def hentikan_tugas(self, tugas_id: str) -> dict:
        """Hentikan semua sub-agen satu tugas + cabut tokennya.

        Kembalikan {"dihentikan": n, "token_dicabut": [...], "detik": x}.
        Target < 5 detik (diukur di tes).
        """
        mulai = time.monotonic()
        korban = [s for s in self._aktif.values() if s.tugas_id == tugas_id]
        korban += [s for s in self._antre if s.tugas_id == tugas_id]
        for sub in korban:
            sub.status = "dihentikan"
            self._aktif.pop(sub.sub_agen_id, None)
            if sub in self._antre:
                self._antre.remove(sub)
        dicabut = revoke_all_for_tugas(tugas_id)
        return {"dihentikan": len(korban), "token_dicabut": dicabut,
                "detik": round(time.monotonic() - mulai, 3)}
