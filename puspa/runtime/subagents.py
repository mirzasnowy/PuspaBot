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
from dataclasses import dataclass

from puspa.policy.tokens import is_subset, revoke_all_for_task

MAX_SUB_PER_AGENT = 4
MAX_SUBSYSTEM = 6
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
    role: str = ""  # peran hierarki ("" = tanpa peran khusus)


class SubagentPool:
    """Kolam sub-agen aktif seluruh sistem (in-memory, per proses)."""

    def __init__(self) -> None:
        self._active: dict[str, Subagent] = {}
        self._queued: list[Subagent] = []

    def spawn(
        self,
        *,
        agent_id: str,
        task_id: str,
        permission_class: str,
        agent_token: dict,
        subagent_token: dict,
        creator_is_subagent: bool = False,
        role: str = "",
    ) -> Subagent:
        """Buat sub-agen baru (atau antrekan bila kolam penuh).

        `token_agen` / `token_subagen` adalah payload dict yang sudah
        diverifikasi. Token sub-agen yang bukan subset token agen
        ditolak sebelum apa pun dibuat. `role` mencatat peran
        hierarki sub-agen (mis. "researcher"); "" berarti tanpa
        peran khusus dan tidak mengubah perilaku lama.
        """
        if creator_is_subagent:
            raise SubagentError("sub-agen tidak bisa membuat sub-agen (satu level saja)")
        if permission_class not in KLASS_PERMISSION:
            raise SubagentError(f"kelas_izin harus salah satu dari {KLASS_PERMISSION}")
        if not is_subset(subagent_token, agent_token):
            raise SubagentError("token sub-agen bukan himpunan bagian dari token agennya")
        sub = Subagent(
            subagent_id=f"sub-{uuid.uuid4().hex[:12]}",
            agent_id=agent_id,
            task_id=task_id,
            permission_class=permission_class,
            role=role,
        )
        per_agent = sum(1 for s in self._active.values() if s.agent_id == agent_id)
        if per_agent >= MAX_SUB_PER_AGENT or len(self._active) >= MAX_SUBSYSTEM:
            sub.status = "queued"
            self._queued.append(sub)
            return sub
        self._active[sub.subagent_id] = sub
        return sub

    def complete(self, subagent_id: str) -> Subagent | None:
        """Tandai sub-agen selesai; majukan satu antrean bila ada."""
        sub = self._active.pop(subagent_id, None)
        if sub is None:
            for queued in self._queued:
                if queued.subagent_id == subagent_id:
                    self._queued.remove(queued)
                    queued.status = "completed"
                    return queued
            return None
        sub.status = "completed"
        self._advance_queue()
        return sub

    def _advance_queue(self) -> None:
        while self._queued and len(self._active) < MAX_SUBSYSTEM:
            candidate = self._queued[0]
            per_agent = sum(1 for s in self._active.values() if s.agent_id == candidate.agent_id)
            if per_agent >= MAX_SUB_PER_AGENT:
                break
            self._queued.pop(0)
            candidate.status = "active"
            self._active[candidate.subagent_id] = candidate

    def active_per_agent(self, agent_id: str) -> list[Subagent]:
        return [s for s in self._active.values() if s.agent_id == agent_id]

    def active_by_role(self, agent_id: str, role: str) -> list[Subagent]:
        """Sub-agen aktif satu agen dengan peran hierarki tertentu."""
        return [s for s in self._active.values() if s.agent_id == agent_id and s.role == role]

    def count_active(self) -> int:
        return len(self._active)

    def count_queued(self) -> int:
        return len(self._queued)

    def stop_task(self, task_id: str) -> dict:
        """Hentikan semua sub-agen satu tugas + cabut tokennya.

        Kembalikan {"dihentikan": n, "token_dicabut": [...], "detik": x}.
        Target < 5 detik (diukur di tes).
        """
        start = time.monotonic()
        stopped_subagents = [s for s in self._active.values() if s.task_id == task_id]
        stopped_subagents += [s for s in self._queued if s.task_id == task_id]
        for sub in stopped_subagents:
            sub.status = "stopped"
            self._active.pop(sub.subagent_id, None)
            if sub in self._queued:
                self._queued.remove(sub)
        revoked = revoke_all_for_task(task_id)
        return {
            "stopped": len(stopped_subagents),
            "tokens_revoked": revoked,
            "seconds": round(time.monotonic() - start, 3),
        }

    def stop_all_for_agent(self, agent_id: str) -> dict:
        """Stop every live subagent of one agent (P-02 stop-all).

        Active and queued subagents of the agent are stopped
        and their task tokens revoked, one task at a time.
        Returns {"stopped": n, "tasks": [...], "tokens_revoked":
        [...], "seconds": x}, the same shape as
        :meth:`stop_task` but across the agent's tasks.
        """
        start = time.monotonic()
        stopped_subagents = [s for s in self._active.values() if s.agent_id == agent_id]
        stopped_subagents += [s for s in self._queued if s.agent_id == agent_id]
        task_ids = sorted({sub.task_id for sub in stopped_subagents})
        for sub in stopped_subagents:
            sub.status = "stopped"
            self._active.pop(sub.subagent_id, None)
            if sub in self._queued:
                self._queued.remove(sub)
        revoked: list[str] = []
        for task_id in task_ids:
            revoked.extend(revoke_all_for_task(task_id))
        return {
            "stopped": len(stopped_subagents),
            "tasks": task_ids,
            "tokens_revoked": revoked,
            "seconds": round(time.monotonic() - start, 3),
        }
