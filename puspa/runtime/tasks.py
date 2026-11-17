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

from puspa.runtime.budget import AnggaranHabis, BudgetTracker
from puspa.runtime.handoff import Handoff, HandoffContract

TAHAP = ("terima", "rencana", "jalankan", "periksa", "sajikan")


class TaskError(Exception):
    """Siklus tugas gagal / tidak sah."""


@dataclass
class Langkah:
    """Satu langkah rencana."""

    name: str
    masukan: dict
    expected_output: dict
    permission_class: str = "Pelaksana"
    anggaran: dict = field(default_factory=lambda: {"step_max": 15, "token_max": 20000, "rupiah_max": 2000})
    syarat_completed: str = ""
    action_logout: bool = False
    paralel_dengan: list[str] = field(default_factory=list)


@dataclass
class Task:
    """Satu tugas yang sedang berjalan."""

    task_id: str
    user_id: str
    agent_id: str
    purpose: str
    rencana: list[Langkah] = field(default_factory=list)
    status: str = "accepted"
    tahap: str = "terima"
    pertanyaan: str = ""
    result: dict = field(default_factory=dict)
    cost_rp: int = 0


def create_rencana(purpose: str, step: list[Langkah]) -> list[Langkah]:
    """Validasi rencana: langkah 1..25, tiap langkah ber-skema."""
    if not 1 <= len(step) <= 25:
        raise TaskError("rencana harus 1..25 langkah")
    for lg in step:
        if "type" not in lg.masukan or "type" not in lg.expected_output:
            raise TaskError(f"langkah {lg.name!r}: masukan/keluaran harus ber-skema ('jenis')")
        if lg.permission_class not in ("Pembaca", "Pelaksana", "Pemeriksa"):
            raise TaskError(f"langkah {lg.name!r}: kelas_izin tidak dikenal")
    return step


class TaskRunner:
    """Menjalankan satu tugas lewat 5 tahap, menulis ke Buku Kerja."""

    def __init__(self, *, sink=None, pelaksana=None, pemeriksa=None,
                 budget: BudgetTracker | None = None) -> None:
        # sink(jenis, isi): pencatat kejadian; default buang ke memori.
        self._event: list[dict] = []
        self.sink = sink or (lambda jenis, content: self._event.append({"type": jenis, "content": content}))
        # pelaksana(nama_langkah, masukan) -> dict hasil ber-skema.
        self.pelaksana = pelaksana or (lambda name, masukan: {"type": masukan.get("type", "result"), "value": None})
        # pemeriksa(hasil) -> (lolos: bool, alasan: str).
        self.pemeriksa = pemeriksa or (lambda result: (True, ""))
        self.budget = budget or BudgetTracker()

    def _record(self, jenis: str, content: dict) -> None:
        try:
            self.sink(jenis, content)
        except Exception:
            # Penulisan ledger best-effort (keputusan Fase 0 no. 9):
            # Postgres mati tidak boleh menjatuhkan turn.
            pass

    def jalankan(self, task: Task) -> Task:
        """Jalankan 5 tahap sampai selesai / gagal / menunggu."""
        self._record("task_created", {"task_id": task.task_id, "agent_id": task.agent_id, "purpose": task.purpose})
        # 1. Terima.
        task.tahap = "terima"
        if len(task.purpose.split()) < 3:
            task.pertanyaan = "Boleh dijelaskan sedikit lagi? Tujuanmu masih terlalu singkat."
            task.status = "pending_user"
            self._record("agent_message", {"task_id": task.task_id, "text": task.pertanyaan})
            return task
        # 2. Rencana.
        task.tahap = "rencana"
        if task.rencana:
            create_rencana(task.purpose, task.rencana)
        self._record("plan_created", {"task_id": task.task_id,
                                       "step": [lg.name for lg in task.rencana]})
        if any(lg.action_logout for lg in task.rencana):
            # Berhenti di titik persetujuan sebelum menjalankan apa pun.
            task.status = "pending_approval"
            self._record("approval_requested", {"task_id": task.task_id, "reason": "rencana memuat aksi keluar"})
            return task
        # 3. Jalankan.
        task.tahap = "jalankan"
        try:
            for lg in task.rencana:
                self.budget.record_step(1)
                kontrak = HandoffContract(
                    dari_id=task.agent_id, to_id=f"{task.agent_id}:pelaksana",
                    purpose=lg.name, masukan=lg.masukan,
                    expected_output=lg.expected_output,
                    anggaran=lg.anggaran, permission_class=lg.permission_class,
                    completion_definition=lg.syarat_completed or lg.name,
                )
                handoff = Handoff(kontrak)
                self._record("handoff_proposed", {"task_id": task.task_id, "handoff_id": handoff.id, "purpose": lg.name})
                handoff.terima()
                handoff.mulai()
                result = self.pelaksana(lg.name, lg.masukan)
                status_serah = handoff.serahkan(result)
                if status_serah == "rejected":
                    # serahkan() sudah menandai status 'ditolak' + alasan.
                    self._record("handoff_rejected", {"task_id": task.task_id, "handoff_id": handoff.id})
                    raise TaskError(f"langkah {lg.name!r} ditolak: keluaran tidak lolos skema")
                handoff.nyatakan_passed()
                self._record("handoff_handed_over", {"task_id": task.task_id, "handoff_id": handoff.id})
                task.result[lg.name] = result
        except AnggaranHabis as exc:
            task.status = "failed"
            self._record("budget_exhausted", {"task_id": task.task_id, "reason": str(exc)})
            self._record("task_failed", {"task_id": task.task_id, "reason": str(exc)})
            return task
        except TaskError as exc:
            task.status = "failed"
            self._record("task_failed", {"task_id": task.task_id, "reason": str(exc)})
            return task
        # 4. Periksa.
        task.tahap = "periksa"
        passed, reason = self.pemeriksa(task.result)
        self._record("verification_completed", {"task_id": task.task_id, "passed": passed, "reason": reason})
        if not passed:
            task.status = "failed"
            self._record("task_failed", {"task_id": task.task_id, "reason": reason or "pemeriksaan gagal"})
            return task
        # 5. Sajikan (juru bicara tunggal = agen pemilik tugas).
        task.tahap = "sajikan"
        task.status = "completed"
        task.cost_rp = self.budget.rupiah_terpakai
        self._record("task_completed", {"task_id": task.task_id, "agent_id": task.agent_id})
        return task

    def event_memory(self) -> list[dict]:
        """Kejadian yang dicatat sink default (untuk tes)."""
        return list(self._event)


def klaim_task_berikutnya(dsn: str, agent_id: str) -> dict | None:
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
            cur.execute("UPDATE tasks SET status = 'berjalan' WHERE id = %s", (row["id"],))
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
        sink = lambda jenis, content: ledger.append_event(
            task_id=task_id, agent_id=agent_id, jenis=jenis, content=content)
    sink("task_stopped", {"task_id": task_id, "deleted": True,
                              "user_id": user_id})
    cassette = delete_cassette_task(task_id, cassette_dir=cassette_dir, dsn=dsn)
    return {"task_id": task_id, "tombstone": True, "cassette_deleted": cassette["deleted"]}
