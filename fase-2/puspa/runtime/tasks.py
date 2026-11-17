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

    nama: str
    masukan: dict
    keluaran_diharapkan: dict
    kelas_izin: str = "Pelaksana"
    anggaran: dict = field(default_factory=lambda: {"langkah_max": 15, "token_max": 20000, "rupiah_max": 2000})
    syarat_selesai: str = ""
    aksi_keluar: bool = False
    paralel_dengan: list[str] = field(default_factory=list)


@dataclass
class Task:
    """Satu tugas yang sedang berjalan."""

    tugas_id: str
    user_id: str
    agen_id: str
    tujuan: str
    rencana: list[Langkah] = field(default_factory=list)
    status: str = "diterima"
    tahap: str = "terima"
    pertanyaan: str = ""
    hasil: dict = field(default_factory=dict)
    biaya_rp: int = 0


def buat_rencana(tujuan: str, langkah: list[Langkah]) -> list[Langkah]:
    """Validasi rencana: langkah 1..25, tiap langkah ber-skema."""
    if not 1 <= len(langkah) <= 25:
        raise TaskError("rencana harus 1..25 langkah")
    for lg in langkah:
        if "jenis" not in lg.masukan or "jenis" not in lg.keluaran_diharapkan:
            raise TaskError(f"langkah {lg.nama!r}: masukan/keluaran harus ber-skema ('jenis')")
        if lg.kelas_izin not in ("Pembaca", "Pelaksana", "Pemeriksa"):
            raise TaskError(f"langkah {lg.nama!r}: kelas_izin tidak dikenal")
    return langkah


class TaskRunner:
    """Menjalankan satu tugas lewat 5 tahap, menulis ke Buku Kerja."""

    def __init__(self, *, sink=None, pelaksana=None, pemeriksa=None,
                 budget: BudgetTracker | None = None) -> None:
        # sink(jenis, isi): pencatat kejadian; default buang ke memori.
        self._kejadian: list[dict] = []
        self.sink = sink or (lambda jenis, isi: self._kejadian.append({"jenis": jenis, "isi": isi}))
        # pelaksana(nama_langkah, masukan) -> dict hasil ber-skema.
        self.pelaksana = pelaksana or (lambda nama, masukan: {"jenis": masukan.get("jenis", "hasil"), "nilai": None})
        # pemeriksa(hasil) -> (lolos: bool, alasan: str).
        self.pemeriksa = pemeriksa or (lambda hasil: (True, ""))
        self.budget = budget or BudgetTracker()

    def _catat(self, jenis: str, isi: dict) -> None:
        try:
            self.sink(jenis, isi)
        except Exception:
            # Penulisan ledger best-effort (keputusan Fase 0 no. 9):
            # Postgres mati tidak boleh menjatuhkan turn.
            pass

    def jalankan(self, task: Task) -> Task:
        """Jalankan 5 tahap sampai selesai / gagal / menunggu."""
        self._catat("tugas_dibuat", {"tugas_id": task.tugas_id, "agen_id": task.agen_id, "tujuan": task.tujuan})
        # 1. Terima.
        task.tahap = "terima"
        if len(task.tujuan.split()) < 3:
            task.pertanyaan = "Boleh dijelaskan sedikit lagi? Tujuanmu masih terlalu singkat."
            task.status = "menunggu_user"
            self._catat("pesan_agen", {"tugas_id": task.tugas_id, "teks": task.pertanyaan})
            return task
        # 2. Rencana.
        task.tahap = "rencana"
        if task.rencana:
            buat_rencana(task.tujuan, task.rencana)
        self._catat("rencana_dibuat", {"tugas_id": task.tugas_id,
                                       "langkah": [lg.nama for lg in task.rencana]})
        if any(lg.aksi_keluar for lg in task.rencana):
            # Berhenti di titik persetujuan sebelum menjalankan apa pun.
            task.status = "menunggu_persetujuan"
            self._catat("persetujuan_diminta", {"tugas_id": task.tugas_id, "alasan": "rencana memuat aksi keluar"})
            return task
        # 3. Jalankan.
        task.tahap = "jalankan"
        try:
            for lg in task.rencana:
                self.budget.catat_langkah(1)
                kontrak = HandoffContract(
                    dari_id=task.agen_id, ke_id=f"{task.agen_id}:pelaksana",
                    tujuan=lg.nama, masukan=lg.masukan,
                    keluaran_diharapkan=lg.keluaran_diharapkan,
                    anggaran=lg.anggaran, kelas_izin=lg.kelas_izin,
                    definisi_selesai=lg.syarat_selesai or lg.nama,
                )
                handoff = Handoff(kontrak)
                self._catat("handoff_diajukan", {"tugas_id": task.tugas_id, "handoff_id": handoff.id, "tujuan": lg.nama})
                handoff.terima()
                handoff.mulai()
                hasil = self.pelaksana(lg.nama, lg.masukan)
                status_serah = handoff.serahkan(hasil)
                if status_serah == "ditolak":
                    # serahkan() sudah menandai status 'ditolak' + alasan.
                    self._catat("handoff_ditolak", {"tugas_id": task.tugas_id, "handoff_id": handoff.id})
                    raise TaskError(f"langkah {lg.nama!r} ditolak: keluaran tidak lolos skema")
                handoff.nyatakan_lolos()
                self._catat("handoff_diserahkan", {"tugas_id": task.tugas_id, "handoff_id": handoff.id})
                task.hasil[lg.nama] = hasil
        except AnggaranHabis as exc:
            task.status = "gagal"
            self._catat("anggaran_habis", {"tugas_id": task.tugas_id, "alasan": str(exc)})
            self._catat("tugas_gagal", {"tugas_id": task.tugas_id, "alasan": str(exc)})
            return task
        except TaskError as exc:
            task.status = "gagal"
            self._catat("tugas_gagal", {"tugas_id": task.tugas_id, "alasan": str(exc)})
            return task
        # 4. Periksa.
        task.tahap = "periksa"
        lolos, alasan = self.pemeriksa(task.hasil)
        self._catat("pemeriksaan_selesai", {"tugas_id": task.tugas_id, "lolos": lolos, "alasan": alasan})
        if not lolos:
            task.status = "gagal"
            self._catat("tugas_gagal", {"tugas_id": task.tugas_id, "alasan": alasan or "pemeriksaan gagal"})
            return task
        # 5. Sajikan (juru bicara tunggal = agen pemilik tugas).
        task.tahap = "sajikan"
        task.status = "selesai"
        task.biaya_rp = self.budget.rupiah_terpakai
        self._catat("tugas_selesai", {"tugas_id": task.tugas_id, "agen_id": task.agen_id})
        return task

    def kejadian_memori(self) -> list[dict]:
        """Kejadian yang dicatat sink default (untuk tes)."""
        return list(self._kejadian)


def klaim_tugas_berikutnya(dsn: str, agen_id: str) -> dict | None:
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
                "SELECT * FROM tugas WHERE agen_id = %s AND status = 'diterima'"
                " ORDER BY dibuat ASC LIMIT 1 FOR UPDATE SKIP LOCKED",
                (agen_id,),
            )
            row = cur.fetchone()
            if row is None:
                return None
            cur.execute("UPDATE tugas SET status = 'berjalan' WHERE id = %s", (row["id"],))
        conn.commit()
    return dict(row)


def buat_tugas_id() -> str:
    return f"tugas-{uuid.uuid4().hex[:12]}"


def tombstone_tugas(tugas_id: str, user_id: str, agen_id: str, *, dsn=None,
                    sink=None, kaset_dir=None) -> dict:
    """Hapus tugas (C129): isi kejadian diganti tanda hapus TANPA
    UPDATE/DELETE di Buku Kerja — append `tugas_dihentikan` dengan
    isi {"dihapus": true}; proyeksi menyembunyikan tugas. Kaset +
    blob tugas ikut dihapus. Rantai hash tetap utuh.

    `sink(jenis, isi)` mencatat ke Buku Kerja; bila None, dipakai
    `Ledger.append_event` langsung.
    """
    from puspa.cassette.cassette import hapus_kaset_tugas

    if sink is None:
        from puspa.ledger import Ledger

        ledger = Ledger(dsn)
        sink = lambda jenis, isi: ledger.append_event(
            tugas_id=tugas_id, agen_id=agen_id, jenis=jenis, isi=isi)
    sink("tugas_dihentikan", {"tugas_id": tugas_id, "dihapus": True,
                              "user_id": user_id})
    kaset = hapus_kaset_tugas(tugas_id, kaset_dir=kaset_dir, dsn=dsn)
    return {"tugas_id": tugas_id, "tombstone": True, "kaset_dihapus": kaset["dihapus"]}
