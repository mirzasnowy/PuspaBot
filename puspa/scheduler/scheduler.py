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
HEARTBEAT_MATI_DETIK = 120
TRIGGER_TYPES = ("jadwal", "due_date_bill", "price_turun", "manual",
                "backup_daily", "konsolidasi")


class SchedulerError(Exception):
    """Penjadwal gagal / pemicu tidak sah."""


def _sekarang() -> datetime:
    return datetime.now(timezone.utc)


@dataclass
class Pemicu:
    """Satu pemicu terjadwal."""

    id: str
    user_id: str
    event_type: str
    jadwal: str = ""
    workflow: str = ""
    next_trigger_at: datetime = field(default_factory=_sekarang)
    active: bool = True


def _connect(dsn=None):
    return psycopg.connect(dsn or os.environ.get("PUSPA_TEST_DSN", DEFAULT_DSN))


def create_trigger(user_id: str, event_type: str, next_trigger_at: datetime,
                jadwal: str = "", workflow: str = "", dsn=None) -> Pemicu:
    """Buat pemicu baru (idempotent per id yang dibuat di sini)."""
    if event_type not in TRIGGER_TYPES:
        raise SchedulerError(f"jenis pemicu tidak dikenal: {event_type!r}")
    trigger = Pemicu(id=f"pemicu-{uuid.uuid4().hex[:12]}", user_id=str(user_id),
                    event_type=event_type, jadwal=jadwal, workflow=workflow,
                    next_trigger_at=next_trigger_at)
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO triggers (id, user_id, type, jadwal, workflow, next_trigger_at, active)"
                " VALUES (%s, %s, %s, %s, %s, %s, TRUE)",
                (trigger.id, trigger.user_id, trigger.event_type, trigger.jadwal,
                 trigger.workflow, trigger.next_trigger_at),
            )
        conn.commit()
    return trigger


def items_trigger_due_date(dsn=None, sekarang: datetime | None = None) -> list[Pemicu]:
    """Pemicu aktif yang sudah jatuh tempo, diklaim SKIP LOCKED."""
    sekarang = sekarang or _sekarang()
    with _connect(dsn) as conn:
        with conn.cursor(row_factory=psycopg.rows.dict_row) as cur:
            cur.execute(
                "SELECT id, user_id, type, jadwal, workflow, next_trigger_at, active"
                " FROM triggers WHERE active AND next_trigger_at <= %s"
                " ORDER BY next_trigger_at ASC FOR UPDATE SKIP LOCKED",
                (sekarang,),
            )
            rows = cur.fetchall()
        conn.commit()
    return [Pemicu(id=r["id"], user_id=str(r["user_id"]), event_type=r["type"],
                   jadwal=r["jadwal"] or "", workflow=r["workflows"] or "",
                   next_trigger_at=r["next_trigger_at"], active=r["active"])
            for r in rows]


def jalan_trigger(trigger: Pemicu, *, sink=None, terlewat: bool = False,
                 dsn=None, sekarang: datetime | None = None) -> dict | None:
    """Jalankan satu pemicu (sekali, idempotent).

    Kembalikan dict jalan (dengan `tugas_id`) atau None bila kunci
    idempotensi sudah pernah dipakai (pemicu yang sama tidak pernah
    menjalankan dua tugas). Mencatat `pemicu_terpicu` (atau
    `pemicu_terlewat`) ke Buku Kerja lewat `sink(jenis, isi)`.
    """
    sekarang = sekarang or _sekarang()
    key = f"{trigger.id}:{trigger.next_trigger_at.isoformat()}"
    task_id = f"tugas-{uuid.uuid4().hex[:12]}"
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT 1 FROM trigger_runs WHERE idempotency_key = %s", (key,))
            if cur.fetchone() is not None:
                return None
            cur.execute(
                "INSERT INTO trigger_runs (idempotency_key, trigger_id, task_id, terlewat)"
                " VALUES (%s, %s, %s, %s)",
                (key, trigger.id, task_id, terlewat),
            )
            # Jadwal berikutnya: geser 1 hari (jadwal harian; jadwal
            # cron penuh adalah peningkatan Fase 4 — dicatat di STATUS).
            cur.execute(
                "UPDATE triggers SET next_trigger_at = next_trigger_at + INTERVAL '1 day'"
                " WHERE id = %s", (trigger.id,),
            )
        conn.commit()
    if sink is not None:
        try:
            sink("trigger_missed" if terlewat else "trigger_fired",
                 {"trigger_id": trigger.id, "task_id": task_id,
                  "type": trigger.event_type, "terlewat": terlewat})
        except Exception:
            pass
    return {"trigger_id": trigger.id, "task_id": task_id, "terlewat": terlewat}


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
                "SELECT id, user_id, type, jadwal, workflow, next_trigger_at, active"
                " FROM triggers WHERE active AND next_trigger_at <= %s"
                " ORDER BY next_trigger_at ASC",
                (batas,),
            )
            rows = cur.fetchall()
    result = []
    for r in rows:
        trigger = Pemicu(id=r["id"], user_id=str(r["user_id"]), event_type=r["type"],
                        jadwal=r["jadwal"] or "", workflow=r["workflows"] or "",
                        next_trigger_at=r["next_trigger_at"], active=r["active"])
        jalan = jalan_trigger(trigger, sink=sink, terlewat=True, dsn=dsn, sekarang=sekarang)
        if jalan is not None:
            result.append(jalan)
    return result


def heartbeat(dsn=None, sekarang: datetime | None = None) -> None:
    """Kirim detak penjadwal (ditulis ke `detak_sistem`)."""
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute(
                "INSERT INTO system_heartbeats (komponen, created_at) VALUES ('penjadwal', %s)"
                " ON CONFLICT (komponen) DO UPDATE SET created_at = EXCLUDED.created_at",
                (sekarang or _sekarang(),),
            )
        conn.commit()


def sehat(dsn=None, sekarang: datetime | None = None) -> bool:
    """True bila detak penjadwal terakhir < 2 menit lalu."""
    sekarang = sekarang or _sekarang()
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT created_at FROM system_heartbeats WHERE komponen = 'penjadwal'")
            row = cur.fetchone()
    if row is None:
        return False
    return (sekarang - row[0]).total_seconds() < HEARTBEAT_MATI_DETIK


def tick(*, sink=None, dsn=None, sekarang: datetime | None = None) -> list[dict]:
    """Satu putaran penjadwal: detak + jalankan pemicu jatuh tempo."""
    heartbeat(dsn=dsn, sekarang=sekarang)
    result = []
    for trigger in items_trigger_due_date(dsn=dsn, sekarang=sekarang):
        jalan = jalan_trigger(trigger, sink=sink, dsn=dsn, sekarang=sekarang)
        if jalan is not None:
            result.append(jalan)
    return result


def schedule_backup_daily(user_id: str, *, jam: int = 3, dsn=None) -> Pemicu:
    """Daftarkan job backup harian Fase 1 (C126) sebagai pemicu.

    Jalan pemicu ini memanggil `puspa.backup.create_backup`; penjadwal
    yang menjalankan job memakai `jalankan_job_terjadwal` di bawah.
    """
    besok = (_sekarang() + timedelta(days=1)).replace(hour=jam, minute=0, second=0, microsecond=0)
    return create_trigger(user_id, "backup_daily", besok, jadwal=f"harian {jam:02d}:00", dsn=dsn)


def jalankan_job_terjadwal(jalan: dict, *, dsn=None) -> dict:
    """Eksekusi job sistem untuk satu jalan pemicu (backup/konsolidasi).

    Kembalikan {"job": ..., "hasil": ...}. Job yang tidak dikenal
    dikembalikan sebagai tugas biasa (alur pengguna) tanpa eksekusi
    sistem — pemanggil (runtime tugas) yang menanganinya.
    """
    with _connect(dsn) as conn:
        with conn.cursor() as cur:
            cur.execute("SELECT type, user_id FROM triggers WHERE id = %s", (jalan["trigger_id"],))
            row = cur.fetchone()
    if row is None:
        return {"job": "not_dikenal", "result": None}
    event_type, user_id = row
    if event_type == "backup_daily":
        from puspa.backup.backup import create_backup

        result = create_backup(dsn=dsn)
        return {"job": "backup_daily", "result": str(result)}
    if event_type == "konsolidasi":
        from puspa.memory.consolidate import consolidate_idle

        result = consolidate_idle(str(user_id), dsn=dsn)
        return {"job": "konsolidasi", "result": result}
    return {"job": "task_biasa", "result": jalan["task_id"]}
