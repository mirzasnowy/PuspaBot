"""Penjadwal (§2.14 revisi 7).

- Tabel pemicu di Postgres (jenis, jadwal, alur, pengguna,
  pemicu_berikutnya). Satu proses penjadwal memicu tugas lewat
  antrean yang sama (tabel `tugas` + SKIP LOCKED).
- Pemicu tercatat di Buku Kerja (`pemicu_terpicu`) dengan kunci
  idempotensi (`pemicu_jalan`), jadi satu pemicu tidak pernah
  menjalankan dua tugas.
- Pemicu terlewat: dijalankan sekali saat bangun + dilaporkan
  (`pemicu_terlewat`); pengingat yang sudah lewat jatuh tempo
  ditandai terlambat, tidak disamarkan.
- Detak penjadwal ke Kantor: mati > 2 menit -> tampil + beri tahu
  operator (`sehat()`).
- Job bawaan yang disambungkan di sini: backup harian Fase 1 (C126)
  dan konsolidasi saat sepi Fase 1 (C60) — keduanya dipanggil lewat
  pemicu, bukan cron terpisah.
"""

from __future__ import annotations

import os
import uuid
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import psycopg

DEFAULT_DSN = "dbname=puspa_test user=root host=/var/run/postgresql"
DETAK_MATI_DETIK = 120
JENIS_PEMICU = ("jadwal", "jatuh_tempo_tagihan", "harga_turun", "manual",
                "backup_harian", "konsolidasi")


class SchedulerError(Exception):
    """Penjadwal gagal / pemicu tidak sah."""


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Pemicu:
    """Satu pemicu terjadwal."""

    id: str
    user_id: str
    jenis: str
    jadwal: str = ""
    alur: str = ""
    pemicu_berikutnya: datetime = field(default_factory=_sekarang)
    aktif: bool = True


def _connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def buat_pemicu(user_id: str, jenis: str, pemicu_berikutnya: datetime,
                jadwal: str = "", alur: str = "", dsn=None) -> Pemicu:
    """Buat pemicu baru (idempotent per id yang dibuat di sini)."""
    if jenis not in JENIS_PEMICU:
        raise SchedulerError(f"jenis pemicu tidak dikenal: {jenis!r}")
    pemicu = Pemicu(id=f"pemicu-{uuid.uuid4().hex[:12]}", user_id=str(user_id),
                    jenis=jenis, jadwal=jadwal, alur=alur,
                    pemicu_berikutnya=pemicu_berikutnya)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO pemicu (id, user_id, jenis, jadwal, alur, pemicu_berikutnya, aktif)"
                " VALUES (%s, %s, %s, %s, %s, %s, TRUE)",
                (pemicu.id, pemicu.user_id, pemicu.jenis, pemicu.jadwal,
                 pemicu.alur, pemicu.pemicu_berikutnya),
            )
        conn.commit()
    return pemicu


def daftar_pemicu_jatuh_tempo(dsn=None, sekarang: datetime | None = None) -> list[Pemicu]:
    """Pemicu aktif yang sudah jatuh tempo, diklaim SKIP LOCKED."""
    sekarang = sekarang or _sekarang()
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT id, user_id, jenis, jadwal, alur, pemicu_berikutnya, aktif"
                " FROM pemicu WHERE aktif AND pemicu_berikutnya <= %s"
                " ORDER BY pemicu_berikutnya ASC FOR UPDATE SKIP LOCKED",
                (sekarang,),
            )
            rows = cur.fetchall()
        conn.commit()
    return [Pemicu(id=r["id"], user_id=str(r["user_id"]), jenis=r["jenis"],
                   jadwal=r["jadwal"] or "", alur=r["alur"] or "",
                   pemicu_berikutnya=r["pemicu_berikutnya"], aktif=r["aktif"])
            for r in rows]


def jalan_pemicu(pemicu: Pemicu, *, sink=None, terlewat: bool = False,
                 dsn=None, sekarang: datetime | None = None) -> dict | None:
    """Jalankan satu pemicu (sekali, idempotent).

    Kembalikan dict jalan (dengan `tugas_id`) atau None bila kunci
    idempotensi sudah pernah dipakai (pemicu yang sama tidak pernah
    menjalankan dua tugas). Mencatat `pemicu_terpicu` (atau
    `pemicu_terlewat`) ke Buku Kerja lewat `sink(jenis, isi)`.
    """
    sekarang = sekarang or _sekarang()
    kunci = f"{pemicu.id}:{pemicu.pemicu_berikutnya.isoformat()}"
    tugas_id = f"tugas-{uuid.uuid4().hex[:12]}"
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM pemicu_jalan WHERE kunci_idempotensi = %s", (kunci,))
            if cur.fetchone() is not None:
                return None
            cur.execute(
                "INSERT INTO pemicu_jalan (kunci_idempotensi, pemicu_id, tugas_id, terlewat)"
                " VALUES (%s, %s, %s, %s)",
                (kunci, pemicu.id, tugas_id, terlewat),
            )
            # Jadwal berikutnya: geser 1 hari (jadwal harian; jadwal
            # cron penuh adalah peningkatan Fase 4 — dicatat di STATUS).
            cur.execute(
                "UPDATE pemicu SET pemicu_berikutnya = pemicu_berikutnya + INTERVAL '1 day'"
                " WHERE id = %s", (pemicu.id,),
            )
        conn.commit()
    if sink is not None:
        try:
            sink("pemicu_terlewat" if terlewat else "pemicu_terpicu",
                 {"pemicu_id": pemicu.id, "tugas_id": tugas_id,
                  "jenis": pemicu.jenis, "terlewat": terlewat})
        except Exception:
            pass
    return {"pemicu_id": pemicu.id, "tugas_id": tugas_id, "terlewat": terlewat}


def jalankan_yang_terlewat(*, sink=None, dsn=None,
                           sekarang: datetime | None = None) -> list[dict]:
    """Saat bangun: jalankan sekali tiap pemicu terlewat + laporkan.

    Pemicu dianggap terlewat bila `pemicu_berikutnya` sudah lewat
    lebih dari 1 jam dari sekarang (bukan sekadar baru jatuh tempo —
    yang baru jatuh tempo ditangani `tick` biasa).
    """
    sekarang = sekarang or _sekarang()
    batas = sekarang - timedelta(hours=1)
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT id, user_id, jenis, jadwal, alur, pemicu_berikutnya, aktif"
                " FROM pemicu WHERE aktif AND pemicu_berikutnya <= %s"
                " ORDER BY pemicu_berikutnya ASC",
                (batas,),
            )
            rows = cur.fetchall()
    hasil = []
    for r in rows:
        pemicu = Pemicu(id=r["id"], user_id=str(r["user_id"]), jenis=r["jenis"],
                        jadwal=r["jadwal"] or "", alur=r["alur"] or "",
                        pemicu_berikutnya=r["pemicu_berikutnya"], aktif=r["aktif"])
        jalan = jalan_pemicu(pemicu, sink=sink, terlewat=True, dsn=dsn, sekarang=sekarang)
        if jalan is not None:
            hasil.append(jalan)
    return hasil


def detak(dsn=None, sekarang: datetime | None = None) -> None:
    """Kirim detak penjadwal (ditulis ke `detak_sistem`)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO detak_sistem (komponen, waktu) VALUES ('penjadwal', %s)"
                " ON CONFLICT (komponen) DO UPDATE SET waktu = EXCLUDED.waktu",
                (sekarang or _sekarang(),),
            )
        conn.commit()


def sehat(dsn=None, sekarang: datetime | None = None) -> bool:
    """True bila detak penjadwal terakhir < 2 menit lalu."""
    sekarang = sekarang or _sekarang()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT waktu FROM detak_sistem WHERE komponen = 'penjadwal'")
            row = cur.fetchone()
    if row is None:
        return False
    return (sekarang - row[0]).total_seconds() < DETAK_MATI_DETIK


def tick(*, sink=None, dsn=None, sekarang: datetime | None = None) -> list[dict]:
    """Satu putaran penjadwal: detak + jalankan pemicu jatuh tempo."""
    detak(dsn=dsn, sekarang=sekarang)
    hasil = []
    for pemicu in daftar_pemicu_jatuh_tempo(dsn=dsn, sekarang=sekarang):
        jalan = jalan_pemicu(pemicu, sink=sink, dsn=dsn, sekarang=sekarang)
        if jalan is not None:
            hasil.append(jalan)
    return hasil


def jadwalkan_backup_harian(user_id: str, *, jam: int = 3, dsn=None) -> Pemicu:
    """Daftarkan job backup harian Fase 1 (C126) sebagai pemicu.

    Jalan pemicu ini memanggil `puspa.backup.create_backup`; penjadwal
    yang menjalankan job memakai `jalankan_job_terjadwal` di bawah.
    """
    besok = (_sekarang() + timedelta(days=1)).replace(hour=jam, minute=0, second=0, microsecond=0)
    return buat_pemicu(user_id, "backup_harian", besok, jadwal=f"harian {jam:02d}:00", dsn=dsn)


def jalankan_job_terjadwal(jalan: dict, *, dsn=None) -> dict:
    """Eksekusi job sistem untuk satu jalan pemicu (backup/konsolidasi).

    Kembalikan {"job": ..., "hasil": ...}. Job yang tidak dikenal
    dikembalikan sebagai tugas biasa (alur pengguna) tanpa eksekusi
    sistem — pemanggil (runtime tugas) yang menanganinya.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT jenis, user_id FROM pemicu WHERE id = %s", (jalan["pemicu_id"],))
            row = cur.fetchone()
    if row is None:
        return {"job": "tidak_dikenal", "hasil": None}
    jenis, user_id = row
    if jenis == "backup_harian":
        from puspa.backup.backup import create_backup

        hasil = create_backup(dsn=dsn)
        return {"job": "backup_harian", "hasil": str(hasil)}
    if jenis == "konsolidasi":
        from puspa.memory.consolidate import consolidate_idle

        hasil = consolidate_idle(str(user_id), dsn=dsn)
        return {"job": "konsolidasi", "hasil": hasil}
    return {"job": "tugas_biasa", "hasil": jalan["tugas_id"]}
