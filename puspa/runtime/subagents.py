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

from puspa.policy.tokens import is_subset, revoke_all_for_task

MAX_SUB_PER_AGENT = 4
MAX_SUB_SISTEM = 6
KLASS_PERMISSION = ("Pembaca", "Pelaksana", "Pemeriksa")


class SubagentError(Exception):
    """Spawn / batas sub-agen dilanggar."""


@dataclass
class Subagent:
    """Satu sub-agen aktif (catatan kolam; eksekusi di runtime)."""

    subagent_id: str
    agent_id: str
    task_id: str
    permission_class: str
    token_id: str = ""
    status: str = "active"  # aktif | antre | selesai | dihentikan


class SubagentPool:
    """Kolam sub-agen aktif seluruh sistem (in-memory, per proses)."""

    def __init__(self) -> None:
        self._active: dict[str, Subagent] = {}
        self._antre: list[Subagent] = []

    def spawn(self, *, agent_id: str, task_id: str, permission_class: str,
              token_agent: dict, token_subagen: dict,
              pembuat_adalah_subagen: bool = False) -> Subagent:
        """Buat sub-agen baru (atau antrekan bila kolam penuh).

        `token_agen` / `token_subagen` adalah payload dict yang sudah
        diverifikasi. Token sub-agen yang bukan subset token agen
        ditolak sebelum apa pun dibuat.
        """
        if pembuat_adalah_subagen:
            raise SubagentError("sub-agen tidak bisa membuat sub-agen (satu level saja)")
        if permission_class not in KLASS_PERMISSION:
            raise SubagentError(f"kelas_izin harus salah satu dari {KLASS_PERMISSION}")
        if not is_subset(token_subagen, token_agent):
            raise SubagentError("token sub-agen bukan himpunan bagian dari token agennya")
        sub = Subagent(
            subagent_id=f"sub-{uuid.uuid4().hex[:12]}",
            agent_id=agent_id, task_id=task_id, permission_class=permission_class,
        )
        per_agent = sum(1 for s in self._active.values() if s.agent_id == agent_id)
        if per_agent >= MAX_SUB_PER_AGENT or len(self._active) >= MAX_SUB_SISTEM:
            sub.status = "antre"
            self._antre.append(sub)
            return sub
        self._active[sub.subagent_id] = sub
        return sub

    def completed_at(self, subagent_id: str) -> Subagent | None:
        """Tandai sub-agen selesai; majukan satu antrean bila ada."""
        sub = self._active.pop(subagent_id, None)
        if sub is None:
            for antre in self._antre:
                if antre.subagent_id == subagent_id:
                    self._antre.remove(antre)
                    antre.status = "completed"
                    return antre
            return None
        sub.status = "completed"
        self._majukan_antrean()
        return sub

    def _majukan_antrean(self) -> None:
        while self._antre and len(self._active) < MAX_SUB_SISTEM:
            calon = self._antre[0]
            per_agent = sum(1 for s in self._active.values() if s.agent_id == calon.agent_id)
            if per_agent >= MAX_SUB_PER_AGENT:
                break
            self._antre.pop(0)
            calon.status = "active"
            self._active[calon.subagent_id] = calon

    def active_per_agent(self, agent_id: str) -> list[Subagent]:
        return [s for s in self._active.values() if s.agent_id == agent_id]

    def amount_active(self) -> int:
        return len(self._active)

    def amount_antre(self) -> int:
        return len(self._antre)

    def hentikan_task(self, task_id: str) -> dict:
        """Hentikan semua sub-agen satu tugas + cabut tokennya.

        Kembalikan {"dihentikan": n, "token_dicabut": [...], "detik": x}.
        Target < 5 detik (diukur di tes).
        """
        mulai = time.monotonic()
        korban = [s for s in self._active.values() if s.task_id == task_id]
        korban += [s for s in self._antre if s.task_id == task_id]
        for sub in korban:
            sub.status = "dihentikan"
            self._active.pop(sub.subagent_id, None)
            if sub in self._antre:
                self._antre.remove(sub)
        revoked = revoke_all_for_task(task_id)
        return {"dihentikan": len(korban), "token_dicabut": revoked,
                "detik": round(time.monotonic() - mulai, 3)}
