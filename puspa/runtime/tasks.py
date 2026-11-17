"""Siklus tugas 5 tahap (§2.4 revisi 7).

Terima -> Rencana -> Jalankan -> Periksa -> Sajikan.

- Terima: agen menyatakan ulang tujuan dalam satu kalimat. Kalau
  ambigu (tujuan kurang dari 3 kata), ia bertanya maks 1 pertanyaan
  sebelum bekerja dan status menjadi `menunggu_user`.
- Rencana: graf langkah; tiap langkah punya masukan, keluaran
  ber-skema, kelas izin sub-agen, anggaran, syarat selesai. Tugas di
  bawah 3 langkah jalan tanpa menampilkan rencana. Tugas yang memuat
  aksi keluar menampilkan rencana dulu dan berhenti di titik
  persetujuan.
- Jalankan: langkah yang tidak saling bergantung jalan paralel
  (dalam batas §2.2); hasil tiap langkah masuk lewat kontrak handoff.
  Di sini eksekusi langkah adalah callable yang disuntikkan pemanggil
  (`pelaksana`), supaya runner bisa diuji tanpa model dan tanpa
  jaringan; runtime produksi menyuntikkan pelaksana yang memanggil
  model + tool lewat chassis Fase 0.
- Periksa: hook Pemeriksa untuk klaim angka (disuntikkan; default
  lolos bila tidak ada klaim angka).
- Sajikan: agen pemilik tugas (juru bicara tunggal) menggabungkan
  hasil, merender pesan dari bukti, dan menutup tugas.

Semua tahap menulis kejadian ke Buku Kerja lewat `sink` (callable
`(jenis, isi)` — di produksi `Ledger.append_event`, di tes sink
in-memory). Antrean tugas memakai Postgres SKIP LOCKED
(`klaim_tugas_berikutnya`, §2.3) — tanpa broker tambahan.
"""

from __future__ import annotations

import uuid
from dataclasses import dataclass, field

from puspa.runtime.budget import BudgetExhausted, BudgetTracker
from puspa.runtime.handoff import Handoff, HandoffContract

STAGES = ("accept", "plan", "run", "verify", "present")


class TaskError(Exception):
    """Siklus tugas gagal / tidak sah."""


@dataclass
class Step:
    """Satu langkah rencana."""

    name: str
    inputs: dict
    expected_output: dict
    permission_class: str = "Pelaksana"
    budget: dict = field(default_factory=lambda: {"step_max": 15, "token_max": 20000, "rupiah_max": 2000})
    completion_condition: str = ""
    outbound_action: bool = False
    parallel_with: list[str] = field(default_factory=list)


@dataclass
class Task:
    """Satu tugas yang sedang berjalan."""

    task_id: str
    user_id: str
    agent_id: str
    purpose: str
    plan: list[Step] = field(default_factory=list)
    status: str = "accepted"
    stage: str = "accept"
    question: str = ""
    result: dict = field(default_factory=dict)
    cost_rp: int = 0


def create_plan(purpose: str, steps: list[Step]) -> list[Step]:
    """Validasi rencana: langkah 1..25, tiap langkah ber-skema."""
    if not 1 <= len(steps) <= 25:
        raise TaskError("rencana harus 1..25 langkah")
    for step in steps:
        if "type" not in step.inputs or "type" not in step.expected_output:
            raise TaskError(f"langkah {step.name!r}: masukan/keluaran harus ber-skema ('jenis')")
        if step.permission_class not in ("Pembaca", "Pelaksana", "Pemeriksa"):
            raise TaskError(f"langkah {step.name!r}: kelas_izin tidak dikenal")
    return steps


class TaskRunner:
    """Menjalankan satu tugas lewat 5 tahap, menulis ke Buku Kerja."""

    def __init__(self, *, sink=None, executor=None, verifier=None,
                 budget: BudgetTracker | None = None) -> None:
        # sink(jenis, isi): pencatat kejadian; default buang ke memori.
        self._event: list[dict] = []
        self.sink = sink or (lambda event_type, content: self._event.append({"type": event_type, "content": content}))
        # pelaksana(nama_langkah, masukan) -> dict hasil ber-skema.
        self.executor = executor or (lambda name, inputs: {"type": inputs.get("type", "result"), "value": None})
        # pemeriksa(hasil) -> (lolos: bool, alasan: str).
        self.verifier = verifier or (lambda result: (True, ""))
        self.budget = budget or BudgetTracker()

    def _record(self, event_type: str, content: dict) -> None:
        try:
            self.sink(event_type, content)
        except Exception:
            # Penulisan ledger best-effort (keputusan Fase 0 no. 9):
            # Postgres mati tidak boleh menjatuhkan turn.
            pass

    def run(self, task: Task) -> Task:
        """Jalankan 5 tahap sampai selesai / gagal / menunggu."""
        self._record("task_created", {"task_id": task.task_id, "agent_id": task.agent_id, "purpose": task.purpose})
        # 1. Terima.
        task.stage = "accept"
        if len(task.purpose.split()) < 3:
            task.question = "Boleh dijelaskan sedikit lagi? Tujuanmu masih terlalu singkat."
            task.status = "pending_user"
            self._record("agent_message", {"task_id": task.task_id, "text": task.question})
            return task
        # 2. Rencana.
        task.stage = "plan"
        if task.plan:
            create_plan(task.purpose, task.plan)
        self._record("plan_created", {"task_id": task.task_id,
                                       "step": [step.name for step in task.plan]})
        if any(step.outbound_action for step in task.plan):
            # Berhenti di titik persetujuan sebelum menjalankan apa pun.
            task.status = "pending_approval"
            self._record("approval_requested", {"task_id": task.task_id, "reason": "rencana memuat aksi keluar"})
            return task
        # 3. Jalankan.
        task.stage = "run"
        try:
            for step in task.plan:
                self.budget.record_step(1)
                contract = HandoffContract(
                    from_id=task.agent_id, to_id=f"{task.agent_id}:pelaksana",
                    purpose=step.name, inputs=step.inputs,
                    expected_output=step.expected_output,
                    budget=step.budget, permission_class=step.permission_class,
                    completion_definition=step.completion_condition or step.name,
                )
                handoff = Handoff(contract)
                self._record("handoff_proposed", {"task_id": task.task_id, "handoff_id": handoff.id, "purpose": step.name})
                handoff.accept()
                handoff.start()
                result = self.executor(step.name, step.inputs)
                hand_over_status = handoff.hand_over(result)
                if hand_over_status == "rejected":
                    # serahkan() sudah menandai status 'ditolak' + alasan.
                    self._record("handoff_rejected", {"task_id": task.task_id, "handoff_id": handoff.id})
                    raise TaskError(f"langkah {step.name!r} ditolak: keluaran tidak lolos skema")
                handoff.mark_passed()
                self._record("handoff_handed_over", {"task_id": task.task_id, "handoff_id": handoff.id})
                task.result[step.name] = result
        except BudgetExhausted as exc:
            task.status = "failed"
            self._record("budget_exhausted", {"task_id": task.task_id, "reason": str(exc)})
            self._record("task_failed", {"task_id": task.task_id, "reason": str(exc)})
            return task
        except TaskError as exc:
            task.status = "failed"
            self._record("task_failed", {"task_id": task.task_id, "reason": str(exc)})
            return task
        # 4. Periksa.
        task.stage = "verify"
        passed, reason = self.verifier(task.result)
        self._record("verification_completed", {"task_id": task.task_id, "passed": passed, "reason": reason})
        if not passed:
            task.status = "failed"
            self._record("task_failed", {"task_id": task.task_id, "reason": reason or "pemeriksaan gagal"})
            return task
        # 5. Sajikan (juru bicara tunggal = agen pemilik tugas).
        task.stage = "present"
        task.status = "completed"
        task.cost_rp = self.budget.rupiah_used
        self._record("task_completed", {"task_id": task.task_id, "agent_id": task.agent_id})
        return task

    def event_memory(self) -> list[dict]:
        """Kejadian yang dicatat sink default (untuk tes)."""
        return list(self._event)


def claim_next_task(dsn: str, agent_id: str) -> dict | None:
    """Ambil satu tugas `diterima` lewat SELECT FOR UPDATE SKIP LOCKED.

    Kembalikan baris tugas (dict) atau None bila antrean kosong.
    Tanpa broker tambahan (§2.3).
    """
    import os

    import psycopg

    dsn = dsn or os.environ.get("PUSPA_TEST_DSN", "dbname=puspa_test user=root host=/var/run/postgresql")
    with psycopg.connect(dsn, row_factory=psycopg.rows.dict_row) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "SELECT * FROM tasks WHERE agent_id = %s AND status = 'accepted'"
                " ORDER BY created_at ASC LIMIT 1 FOR UPDATE SKIP LOCKED",
                (agent_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("UPDATE tasks SET status = 'running' WHERE id = %s", (row["id"],))
        conn.commit()
    return dict(row)


def create_task_id() -> str:
    return f"tugas-{uuid.uuid4().hex[:12]}"


def tombstone_task(task_id: str, user_id: str, agent_id: str, *, dsn=None,
                    sink=None, cassette_dir=None) -> dict:
    """Hapus tugas (C129): isi kejadian diganti tanda hapus TANPA
    UPDATE/DELETE di Buku Kerja — append `tugas_dihentikan` dengan
    isi {"dihapus": true}; proyeksi menyembunyikan tugas. Kaset +
    blob tugas ikut dihapus. Rantai hash tetap utuh.

    `sink(jenis, isi)` mencatat ke Buku Kerja; bila None, dipakai
    `Ledger.append_event` langsung.
    """
    from puspa.cassette.cassette import delete_cassette_task

    if sink is None:
        from puspa.ledger import Ledger

        ledger = Ledger(dsn)
        sink = lambda event_type, content: ledger.append_event(
            task_id=task_id, agent_id=agent_id, event_type=event_type, content=content)
    sink("task_stopped", {"task_id": task_id, "deleted": True,
                              "user_id": user_id})
    cassette = delete_cassette_task(task_id, cassette_dir=cassette_dir, dsn=dsn)
    return {"task_id": task_id, "tombstone": True, "cassette_deleted": cassette["deleted"]}
